"""Stremio's streaming server: what turns a torrent stream (an `infoHash`) into a video link this app can play.

Stremio's own app, and every Stremio client that plays torrents, hands the torrent to a small local program, the streaming server (`stremio-service`, which
listens on port 11470). It finds the peers, downloads the pieces the player needs first, and serves the video file over HTTP with range requests. Archive X
has no torrent code of its own: when the person has that server running (on this PC or elsewhere on their network) and its address is saved in Settings,
Addons, a torrent stream becomes `{server}/{infoHash}/{fileIdx}?tr=...` and plays like any direct stream.

The address is the person's own choice and may only name this PC or their network. Archive X talks to that one address and nothing else for torrents: the link
is built here from a checked info-hash, a file number and tracker URLs (as plain query values), never from text an add-on supplies.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import addon_client as ac

DEFAULT = "http://127.0.0.1:11470"
SETTING = "streaming_server_url"
CHECK_TIMEOUT = 1.5        # asking whether the server is there
OPEN_TIMEOUT = 90          # a torrent can take a minute to find peers before its first byte
MAX_TRACKERS = 12

_PATH = re.compile(r"/[0-9a-f]{40}/(-1|\d{1,5})")


def clean_url(text: str) -> str:
    """The server's address as it will be used (`scheme://host:port`), or ValueError saying what is wrong. Only this PC or the person's network may be named."""
    raw = str(text or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    parts = urllib.parse.urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        raise ValueError("The address must start with http:// or https://.")
    host = (parts.hostname or "").lower()
    if not host or parts.username or parts.password:
        raise ValueError("That is not a server address.")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError("Give only the server's address and port, for example http://127.0.0.1:11470.")
    if not ac.is_local_host(host):
        raise ValueError("The streaming server has to be on this PC or your own network.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 11470)
    except ValueError as exc:
        raise ValueError("That port is not valid.") from exc
    shown = f"[{host}]" if ":" in host else host
    return f"{parts.scheme}://{shown}:{port}"


def configured(db) -> str:
    """The saved address, or the usual one when none is saved (or the saved one is no longer acceptable)."""
    saved = str(db.setting(SETTING) or "").strip()
    if not saved:
        return DEFAULT
    try:
        return clean_url(saved)
    except ValueError:
        return DEFAULT


def reachable(base: str) -> bool:
    """Is a Stremio streaming server answering there? It serves its settings as JSON at /settings."""
    try:
        req = urllib.request.Request(base + "/settings", headers={"User-Agent": "ArchiveX", "Accept": "application/json"})
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=CHECK_TIMEOUT) as resp:
            body = resp.read(200_000)
        return isinstance(json.loads(body.decode("utf-8", "replace")), dict)
    except (OSError, ValueError, urllib.error.URLError):
        return False


def info_hash_of(value) -> str:
    """The 40-letter lower-case hex form of a torrent's info-hash (a 32-letter base32 one is converted), or "" when it is neither."""
    text = str(value or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", text):
        return text.lower()
    if re.fullmatch(r"[A-Za-z2-7]{32}", text):
        try:
            return base64.b32decode(text.upper()).hex()
        except (binascii.Error, ValueError):
            return ""
    return ""


def trackers_of(sources) -> list[str]:
    """The tracker addresses among a stream's `sources` ("tracker:udp://..."; "dht:" entries are not trackers), only well-formed ones."""
    out: list[str] = []
    for item in sources if isinstance(sources, list) else []:
        text = str(item or "")
        text = text[len("tracker:"):] if text.startswith("tracker:") else text
        parts = urllib.parse.urlsplit(text)
        if parts.scheme in ("udp", "http", "https", "wss", "ws") and parts.hostname and len(text) < 300 and text not in out:
            out.append(text)
        if len(out) >= MAX_TRACKERS:
            break
    return out


def torrent_url(base: str, info_hash: str, file_idx, trackers: list[str]) -> str:
    """`{server}/{hash}/{file}?tr=...`: the file number is -1 when the add-on gave none (the server then picks the video)."""
    h = info_hash_of(info_hash)
    if not h:
        raise ValueError("That is not a torrent hash.")
    try:
        idx = int(file_idx)
        idx = idx if 0 <= idx < 100_000 else -1
    except (TypeError, ValueError):
        idx = -1
    query = urllib.parse.urlencode([("tr", t) for t in trackers[:MAX_TRACKERS]])
    return f"{clean_url(base)}/{h}/{idx}" + (f"?{query}" if query else "")


def is_torrent_url(base: str, url: str) -> bool:
    """Is this the kind of address `torrent_url` builds for this server? Nothing else is ever opened through the streaming server."""
    try:
        parts = urllib.parse.urlsplit(str(url))
        want = urllib.parse.urlsplit(clean_url(base))
    except ValueError:
        return False
    return (parts.scheme, parts.netloc) == (want.scheme, want.netloc) and bool(_PATH.fullmatch(parts.path)) and not parts.fragment and not parts.username


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def open_torrent(base: str, url: str, byte_range: str = ""):
    """The open answer for a torrent link on the person's streaming server, asked for the bytes the player wants. No redirect is followed."""
    if not is_torrent_url(base, url):
        raise ValueError("That is not a torrent link of the streaming server.")
    headers = {"User-Agent": "ArchiveX"}
    if byte_range and byte_range.startswith("bytes=") and len(byte_range) < 64:
        headers["Range"] = byte_range
    return urllib.request.build_opener(_NoRedirect).open(urllib.request.Request(url, headers=headers), timeout=OPEN_TIMEOUT)
