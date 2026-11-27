"""Asking the person's Stremio add-ons for the torrents of one title or episode, outside a web request (the Collection does it in the background).

It is the same question the title page asks (`/stream/{type}/{id}.json` of each add-on that answers for streams), kept here as plain functions so the background worker needs
no web code. Only add-ons the person saved are asked, and what comes back is classified exactly as for the page: a torrent is its checked info-hash, file number and trackers.
"""
from __future__ import annotations

import re
import urllib.parse as _up

from . import addon_client as ac
from . import screen_ids as si


def saved_addons(db) -> list[dict]:
    raw = db.setting("catalog_addons")
    out = []
    for a in raw if isinstance(raw, list) else []:
        if isinstance(a, dict) and isinstance(a.get("url"), str) and re.fullmatch(r"[0-9a-f]{10}", str(a.get("id") or "")):
            out.append({"id": a["id"], "url": a["url"], "name": str(a.get("name") or "")[:60]})
    return out


def asks_for(manifest: dict, resource: str, kind: str, ident: str) -> bool:
    for r in manifest.get("resources") or []:
        name = r if isinstance(r, str) else (r or {}).get("name")
        if name != resource:
            continue
        types = (manifest.get("types") or []) if isinstance(r, str) else ((r or {}).get("types") or manifest.get("types") or [])
        prefixes = (manifest.get("idPrefixes") or []) if isinstance(r, str) else ((r or {}).get("idPrefixes") or manifest.get("idPrefixes") or [])
        if types and kind not in types:
            return False
        return not prefixes or any(ident.startswith(str(x)) for x in prefixes)
    return False


def torrents_for(db, kind: str, ident: str, season: int | None = None, episode: int | None = None, *, get_json=None) -> list[dict]:
    """The torrent streams (kind "torrent", with infoHash, fileIdx, trackers, seeders, size, height…) the saved add-ons offer for a movie or one episode. [] when none."""
    if kind not in ("movie", "series") or not re.fullmatch(r"tt\d{5,10}", ident):
        return []
    sid = ident if kind == "movie" else (f"{ident}:{int(season)}:{int(episode)}" if season is not None and episode is not None else "")
    if not sid:
        return []
    ac.set_allow_local(str(db.setting("addons_allow_local") or "") in {"1", "true", "True"})
    get = get_json or ac.get_json
    out: list[dict] = []
    for a in saved_addons(db):
        try:
            manifest = get(a["url"] + "/manifest.json")
            if not isinstance(manifest, dict) or not asks_for(manifest, "stream", kind, ident):
                continue
            name = str(manifest.get("name") or a["name"] or "Add-on")[:40]
            got = get(f"{a['url']}/stream/{kind}/{_up.quote(sid, safe='')}.json")
            found, _skipped = ac.classify_streams((got or {}).get("streams") if isinstance(got, dict) else [], name)
        except Exception:
            continue
        for stream in found:
            if stream.get("kind") != "torrent":
                continue
            tags = si.quality_of(stream.get("name") or "")
            stream.update({"height": tags["height"], "quality": tags["resolution"], "source": tags["source"], "hdr": tags["hdr"], "languages": tags["languages"]})
            out.append(stream)
    return out
