"""Where the intro, recap and credits are in a movie or episode, for the player's Skip button.

The times come from two community databases the way dx.bi gets them: TheIntroDB (movies and series, by IMDb or TMDB number) and AniSkip (anime, by MyAnimeList
number, found from the Kitsu number). Only a title's public identifiers leave the app, never a file name, and only in SFW mode (the routes enforce that).
The answer for a title is kept for a few hours, an empty one included, so a page that is opened again does not ask again.
"""
from __future__ import annotations

import re
import threading
import time

from . import screen_identify as si

KEEP_SECONDS = 6 * 3600
_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
_TYPES = ("intro", "recap", "credits", "preview")
_ANISKIP = {"op": "intro", "mixed-op": "intro", "ed": "credits", "mixed-ed": "credits", "recap": "recap"}
IDENT = re.compile(r"^(tt\d+|tmdb[:-]\d+|kitsu:\d+)$")


def _segment(kind: str, start, end) -> dict | None:
    """One clean segment, or None: a start before the video, an end not after the start, or a blip shorter than a second."""
    try:
        s = float(start)
        e = None if end is None else float(end)
    except (TypeError, ValueError):
        return None
    if s < 0 or (e is not None and e <= s + 1):
        return None
    return {"type": kind, "start": round(s, 2), "end": None if e is None else round(e, 2)}


def _from_theintrodb(doc) -> list[dict]:
    out: list[dict] = []
    if not isinstance(doc, dict):
        return out
    for kind in _TYPES:
        for seg in doc.get(kind) or []:
            if not isinstance(seg, dict):
                continue
            start = (seg.get("start_ms") or 0) / 1000
            end = seg.get("end_ms")
            one = _segment(kind, start, None if end is None else end / 1000)
            if one:
                out.append(one)
    return out


def _from_aniskip(doc) -> list[dict]:
    out: list[dict] = []
    for r in (doc.get("results") if isinstance(doc, dict) else None) or []:
        kind = _ANISKIP.get(str(r.get("skipType") or "")) if isinstance(r, dict) else None
        iv = r.get("interval") if isinstance(r, dict) else None
        one = _segment(kind, iv.get("startTime"), iv.get("endTime")) if kind and isinstance(iv, dict) else None
        if one:
            out.append(one)
    return out


def _mal_id(kitsu: str) -> int | None:
    doc = si.http_fetch(f"https://kitsu.app/api/edge/anime/{kitsu}/mappings", {"filter[externalSite]": "myanimelist/anime"}, {})
    try:
        return int(doc["data"][0]["attributes"]["externalId"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def lookup(ident: str, kind: str, season: int | None = None, episode: int | None = None) -> list[dict]:
    """The segments for a title: `ident` is an IMDb (tt…), TMDB (tmdb:… or tmdb-…) or Kitsu (kitsu:…) number, `kind` 'movie' or 'series'.

    A series needs its season and episode; a title nobody has submitted times for answers an empty list. A provider that is down also answers empty (and is not
    remembered), because the player must never wait on, or break because of, a skip button.
    """
    if not IDENT.match(ident or ""):
        return []
    if kind == "series" and not (isinstance(season, int) and isinstance(episode, int) and season >= 0 and episode >= 1):
        return []
    key = f"{ident}|{kind}|{season}|{episode}"
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < KEEP_SECONDS:
            return [dict(x) for x in hit[1]]
    try:
        if ident.startswith("kitsu:"):
            mal = _mal_id(ident[6:])
            doc = si.http_fetch(f"https://api.aniskip.com/v2/skip-times/{mal}/{int(episode or 1)}?types[]=op&types[]=ed&types[]=recap&types[]=mixed-op&types[]=mixed-ed&episodeLength=0", {}, {}) if mal else {}
            found = _from_aniskip(doc)
        else:
            params: dict = {"imdb_id": ident} if ident.startswith("tt") else {"tmdb_id": re.sub(r"^tmdb[:-]", "", ident)}
            if kind == "series":
                params.update({"season": season, "episode": episode})
            found = _from_theintrodb(si.http_fetch("https://api.theintrodb.org/v3/media", params, {}))
    except Exception:   # a provider that is down: nothing to skip this time, and ask again next time
        return []
    found.sort(key=lambda s: s["start"])
    with _lock:
        if len(_cache) > 500:
            _cache.clear()
        _cache[key] = (now, found)
    return [dict(x) for x in found]
