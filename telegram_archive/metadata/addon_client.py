"""Stremio catalogue add-ons that the person adds themselves (Settings, Addons): a manifest address, and the lists it offers.

An add-on is plain HTTPS and JSON (`/manifest.json`, `/catalog/{type}/{id}.json`). Because the address is typed by a person and then fetched by this PC, it is
checked before every use: HTTPS only, the default port, a name that resolves only to public addresses (never this PC, a router or a LAN device), at most
a few redirects and each one checked the same way, a size cap and a time limit. Catalogues support Discover; title pages may request streams, whose direct URLs are checked before playback. On this PC or network (a switch in Settings, Addons) http, any port and private addresses are allowed.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

MAX_BYTES = 6 * 1024 * 1024
TIMEOUT = 15
USABLE_TYPES = ("movie", "series", "anime")


LOCAL = False   # the person allowed add-ons on this PC or its network (Settings, Addons): then http, any port and private addresses are fine


def set_allow_local(on: bool) -> None:
    global LOCAL
    LOCAL = bool(on)


def _resolve(host: str):
    return socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)


def check_public(url: str) -> str:
    """The address as it will be used, or ValueError saying what is wrong with it. `stremio://` links are the same address over HTTPS."""
    text = str(url or "").strip()
    if text.lower().startswith("stremio://"):
        text = "https://" + text[len("stremio://"):]
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("https", "http") or (parts.scheme == "http" and not LOCAL):
        raise ValueError("The address must start with https:// (or stremio://)." + ("" if LOCAL else " To use an add-on on this PC or network, switch that on in Settings, Addons."))
    host = (parts.hostname or "").strip().lower()
    if not host or parts.username or parts.password:
        raise ValueError("That is not an add-on address.")
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    if LOCAL:                                                     # the owner's own choice: this PC, a media server or an add-on on the network
        return clean
    if parts.port not in (None, 443):
        raise ValueError("Add-ons on other ports are not supported.")
    try:                                                         # a number given as the address is judged as it is, with no name lookup
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise ValueError("That address points inside this PC or its network, which is not allowed.")
        return clean
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")) or "." not in host:
        raise ValueError("That address points inside this PC or its network, which is not allowed.")
    try:
        found = {ipaddress.ip_address(info[4][0]) for info in _resolve(host)}
    except (OSError, ValueError) as exc:
        if urllib.request.getproxies().get("https"):             # behind a proxy the proxy looks the name up, and this PC may not be able to
            return clean
        raise ValueError("That address could not be found.") from exc
    if not found or not all(ip.is_global for ip in found):
        raise ValueError("That address points inside this PC or its network, which is not allowed.")
    return clean


def base_of(url: str) -> str:
    """The add-on's own address: the manifest address without `/manifest.json` (configuration kept in the path stays)."""
    clean = check_public(url)
    parts = urllib.parse.urlsplit(clean)
    path = parts.path[: -len("/manifest.json")] if parts.path.endswith("/manifest.json") else parts.path
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


class _Guard(urllib.request.HTTPRedirectHandler):
    max_redirections = 3

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(urllib.parse.urljoin(req.full_url, newurl))      # a redirect may not lead anywhere the first address could not
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_json(url: str):
    """One JSON answer from a checked address. A 404 is an empty answer; anything else wrong raises."""
    check_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": "ArchiveX", "Accept": "application/json"})
    try:
        with urllib.request.build_opener(_Guard).open(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise
    if len(raw) > MAX_BYTES:
        raise ValueError("The answer was too large.")
    return json.loads(raw.decode("utf-8")) if raw else {}


# ---- streams: what an add-on's stream answer may do here -------------------------------------------------------------------------------------------
# A stream is a link opened elsewhere (a YouTube id, an external page: the browser goes there, nothing is played here), or a direct video link. A direct
# link is played inside the app only from the person's own sources (this PC or network, with the switch in Settings, Addons) or from a public HTTPS
# host whose DNS answers contain only public IP addresses; other direct links are listed and not played. A torrent (`infoHash`) is played only through the
# person's own Stremio streaming server (streaming_server.py), when one is set up; Archive X has no torrent code of its own.
def _is_public_host(host: str) -> bool:
    """A host reachable only at public IP addresses, as required for an external direct stream."""
    host = (host or "").strip().lower().strip("[]").rstrip(".")
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")) or "." not in host:
        return False
    try:
        found = {ipaddress.ip_address(info[4][0]) for info in _resolve(host)}
    except (OSError, ValueError):
        return False
    return bool(found) and all(ip.is_global for ip in found)


def is_local_host(host: str) -> bool:
    """A name or number that is inside this PC or its network (a media server at home)."""
    host = (host or "").strip().lower().strip("[]")
    if not host:
        return False
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        pass
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")) or "." not in host:
        return True
    try:
        found = {ipaddress.ip_address(info[4][0]) for info in _resolve(host)}
    except (OSError, ValueError):
        return False
    return bool(found) and not any(ip.is_global for ip in found)


def stream_origin(url: str) -> str:
    """Where a direct stream may be played: "own" (this PC or network, only while allowed), "public" (a public HTTPS host), or "" (blocked)."""
    parts = urllib.parse.urlsplit(str(url or ""))
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host or parts.username or parts.password:
        return ""
    try:
        port = parts.port
    except ValueError:
        return ""
    if LOCAL and is_local_host(host):
        return "own"
    if parts.scheme == "https" and port in (None, 443) and _is_public_host(host):
        return "public"
    return ""


class _StreamGuard(urllib.request.HTTPRedirectHandler):
    max_redirections = 4

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        if not stream_origin(target):
            raise urllib.error.URLError("the stream was sent somewhere it may not be played from")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_stream(url: str, byte_range: str = ""):
    """The open answer for a stream the app may play (`stream_origin`), asked for the bytes the player wants; redirects are held to the same rule."""
    if not stream_origin(url):
        raise ValueError("That stream may not be played here.")
    headers = {"User-Agent": "ArchiveX"}
    if byte_range and byte_range.startswith("bytes=") and len(byte_range) < 64:
        headers["Range"] = byte_range
    return urllib.request.build_opener(_StreamGuard).open(urllib.request.Request(url, headers=headers), timeout=TIMEOUT)


def subtitle_text(url: str) -> str:
    """The text of a subtitle file an add-on listed (SRT or WebVTT), from a host a stream could be played from; at most 3 MB, decoded as UTF-8 or else Latin-1."""
    with open_stream(url) as resp:
        raw = resp.read(3 * 1024 * 1024 + 1)
    if len(raw) > 3 * 1024 * 1024:
        raise ValueError("That subtitle file is too large.")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


_SIZE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(TB|GB|MB|KB)\b", re.I)
_UNIT = {"KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}


def stream_facts(st: dict) -> dict:
    """What a stream's own text says about it, as Torrentio and its relatives write it ("👤 12 💾 1.4 GB ⚙️ SomeSite"): seeds, size, where it is from, and whether a
    debrid service already has it ("[RD+]"). Only the keys that are found are returned."""
    text = " ".join(str(st.get(k) or "") for k in ("name", "title", "description"))
    out: dict = {}
    m = re.search(r"👤\s*(\d{1,7})", text)
    if m:
        out["seeders"] = int(m.group(1))
    m = _SIZE.search(text)
    if m:
        out["size"] = int(float(m.group(1).replace(",", ".")) * _UNIT[m.group(2).upper()])
    m = re.search(r"⚙️?\s*([^\n👤💾]{1,40})", text)
    if m and m.group(1).strip():
        out["provider"] = m.group(1).strip()
    if re.search(r"\[[A-Za-z]{1,3}\+\]", text):
        out["cached"] = True
    return out


def classify_streams(streams, addon: str) -> tuple[list[dict], int]:
    """An add-on's streams as the title page shows them: links that open elsewhere, direct streams that play here, direct streams that do not. Anything else is counted and left out."""
    from . import streaming_server       # (it uses this module's address checks, so it is read when needed)
    out: list[dict] = []
    skipped = 0
    for st in streams if isinstance(streams, list) else []:
        if not isinstance(st, dict):
            continue
        made = len(out)
        name = " · ".join(x for x in (str(st.get("name") or "").strip(), str(st.get("title") or st.get("description") or "").strip()) if x).replace("\n", " ")[:220] or addon
        yt = str(st.get("ytId") or "")
        ext = str(st.get("externalUrl") or "")
        url = str(st.get("url") or "")
        if re.fullmatch(r"[\w-]{6,16}", yt):
            out.append({"addon": addon, "name": name, "kind": "link", "href": f"https://www.youtube.com/watch?v={yt}"})
        elif ext.startswith("https://") and not urllib.parse.urlsplit(ext).username:
            out.append({"addon": addon, "name": name, "kind": "link", "href": ext})
        elif url.startswith(("http://", "https://")):
            origin = stream_origin(url)
            hints = st.get("behaviorHints") if isinstance(st.get("behaviorHints"), dict) else {}
            binge = str(hints.get("bingeGroup") or "")[:80]     # streams with the same label are "the same kind": the next episode starts with the one that matches
            out.append({"addon": addon, "name": name, "kind": "play" if origin else "blocked", "url": url if origin else "", "host": (urllib.parse.urlsplit(url).hostname or "")[:80],
                        **({"bingeGroup": binge} if binge else {})})
        elif streaming_server.info_hash_of(st.get("infoHash")):
            hints = st.get("behaviorHints") if isinstance(st.get("behaviorHints"), dict) else {}
            binge = str(hints.get("bingeGroup") or "")[:80]
            out.append({"addon": addon, "name": name, "kind": "torrent", "infoHash": streaming_server.info_hash_of(st.get("infoHash")), "fileIdx": st.get("fileIdx"),
                        "trackers": streaming_server.trackers_of(st.get("sources")), **({"bingeGroup": binge} if binge else {})})
        else:
            skipped += 1
        if len(out) > made:
            out[-1].update(stream_facts(st))
    return out[:60], skipped


PROVIDES = {"catalog": "Lists", "meta": "Details", "stream": "Streams", "subtitles": "Subtitles", "addon_catalog": "Add-on lists"}


def parse_manifest(manifest) -> dict:
    """What an add-on offers. Any Stremio add-on is accepted; Discover uses its lists of movies, series or anime that need nothing but their own address.
    `provides` names every kind of thing it serves, and `note` explains stream and subtitle handling."""
    if not isinstance(manifest, dict) or not (manifest.get("resources") or manifest.get("catalogs")):
        raise ValueError("That address did not answer with a Stremio add-on.")
    resources = [r if isinstance(r, str) else (r or {}).get("name") for r in (manifest.get("resources") or [])]
    lists = []
    searches = []
    for c in manifest.get("catalogs") or []:
        if not isinstance(c, dict) or c.get("type") not in USABLE_TYPES or not c.get("id"):
            continue
        extra = [e for e in (c.get("extra") or []) if isinstance(e, dict)]
        if any(e.get("name") == "search" for e in extra) and not any(e.get("isRequired") and e.get("name") != "search" for e in extra):
            searches.append({"type": c["type"], "id": str(c["id"])})     # a list that can be searched: the Discover search box asks it too
        if any(e.get("isRequired") for e in extra):          # search-only lists and the like: they need something typed
            continue
        names = {e.get("name") for e in extra}
        lists.append({"type": c["type"], "id": str(c["id"]), "name": str(c.get("name") or c["id"])[:60], "genre": "genre" in names, "skip": "skip" in names})
    if "catalog" not in resources and lists:
        resources.append("catalog")
    provides = [PROVIDES[r] for r in dict.fromkeys(resources) if r in PROVIDES]
    notes = []
    if not lists:
        notes.append("It has no lists to browse, so nothing from it shows in Discover.")
    if searches:
        notes.append("What you type in Discover's search box is also searched in " + ("its lists." if len(searches) > 1 else "its list."))
    if "stream" in resources:
        notes.append("Title pages ask for streams. Direct streams from your own sources or public HTTPS hosts play here, and torrents play through Stremio's streaming server (set it up below).")
    if "subtitles" in resources:
        notes.append("Its subtitles are offered in the video player's subtitle menu for titles that have an IMDb number.")
    return {"name": str(manifest.get("name") or "Add-on")[:60], "description": str(manifest.get("description") or "")[:240], "version": str(manifest.get("version") or "")[:20],
            "lists": lists[:40], "searches": searches[:6], "provides": provides, "note": " ".join(notes),
            "configurable": bool((manifest.get("behaviorHints") or {}).get("configurable")) if isinstance(manifest.get("behaviorHints"), dict) else False}
