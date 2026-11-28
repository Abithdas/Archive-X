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


def parse_torrent_url(base: str, url: str) -> tuple[str, int | None, list[str]]:
    """The inverse of `torrent_url`: (info-hash, file number or None, trackers). Raises ValueError for anything that is not such an address of this server."""
    if not is_torrent_url(base, url):
        raise ValueError("That is not a torrent link of the streaming server.")
    parts = urllib.parse.urlsplit(str(url))
    h, idx = parts.path.strip("/").split("/")[:2]
    file_idx = int(idx) if idx.lstrip("-").isdigit() and int(idx) >= 0 else None
    return info_hash_of(h), file_idx, [t for t in urllib.parse.parse_qs(parts.query).get("tr", []) if t][:MAX_TRACKERS]


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


# ---- starting it ---------------------------------------------------------------------------------------------------------------------------------
DOWNLOAD_PAGE = "https://github.com/Stremio/stremio-service/releases/latest"
START_WAIT = 25.0          # a freshly started server needs a few seconds before it answers


def installed_program(env: dict | None = None, exists=None) -> str:
    """Where Stremio's streaming service (or Stremio itself, which runs the same server) is installed on this PC, or "" when it cannot be found. Windows only: the places
    its installers use. The path is only ever one of these fixed names under a folder taken from the system's own settings."""
    import os
    env = os.environ if env is None else env
    exists = exists or os.path.isfile
    roots = [env.get(k) for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    local = env.get("LOCALAPPDATA")
    names = [(r, rel) for r in roots[:2] if r for rel in ("StremioService/stremio-service.exe",)]
    if local:
        names += [(local, "Programs/StremioService/stremio-service.exe"), (local, "Programs/LNV/Stremio-4/stremio.exe"), (local, "Programs/Stremio/stremio.exe")]
    for root, rel in names:
        path = os.path.join(root, *rel.split("/"))
        if exists(path):
            return path
    return ""


def start(base: str, *, program: str | None = None, launch=None, probe=None, wait: float = START_WAIT, sleep=None) -> dict:
    """Start the streaming server that is installed on this PC and wait until it answers. `{up, started, found, program?, reason}`: never raises."""
    import subprocess
    import time
    probe = probe or reachable
    sleep = sleep or time.sleep
    if probe(base):
        return {"up": True, "started": False, "found": True, "reason": ""}
    managed = False
    path = installed_program() if program is None else program
    if not path and program is None:
        from . import stremio_runtime as rt
        if rt.ready():                     # the streaming server Archive X set up itself (Settings, Addons, Set it up)
            managed, path = True, "managed"
    if not path:
        return {"up": False, "started": False, "found": False, "reason": "The streaming server is not set up on this PC. Use Set it up, or install Stremio's streaming service."}
    host = urllib.parse.urlsplit(base).hostname or ""
    if not ac.is_local_host(host) or host not in ("127.0.0.1", "localhost", "::1"):
        return {"up": False, "started": False, "found": True, "reason": "The saved address is another computer: start the streaming server there."}

    def spawn() -> None:
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen([path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    try:
        if managed:
            from . import stremio_runtime as rt
            rt.start_process()
        else:
            (launch or spawn)()
    except OSError as exc:
        return {"up": False, "started": False, "found": True, "program": path, "reason": f"It could not be started: {exc.strerror or exc}"}
    waited = 0.0
    while waited < wait:
        sleep(0.5)
        waited += 0.5
        if probe(base):
            return {"up": True, "started": True, "found": True, "program": path, "reason": ""}
    return {"up": False, "started": True, "found": True, "program": path, "reason": "It was started but is not answering yet. Give it a moment and check again."}
