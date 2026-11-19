"""Stremio catalogue add-ons that the person adds themselves (Settings, Addons): a manifest address, and the lists it offers.

An add-on is plain HTTPS and JSON (`/manifest.json`, `/catalog/{type}/{id}.json`). Because the address is typed by a person and then fetched by this PC, it is
checked before every use: HTTPS only, the default port, a name that resolves only to public addresses (never this PC, a router or a LAN device), at most
a few redirects and each one checked the same way, a size cap and a time limit. Only catalogues are read; an add-on's streams are never asked for. On this PC or network (a switch in Settings, Addons) http, any port and private addresses are allowed.
"""
from __future__ import annotations

import ipaddress
import json
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


PROVIDES = {"catalog": "Lists", "meta": "Details", "stream": "Streams", "subtitles": "Subtitles", "addon_catalog": "Add-on lists"}


def parse_manifest(manifest) -> dict:
    """What an add-on offers. Any Stremio add-on is accepted; Discover uses its lists of movies, series or anime that need nothing but their own address.
    `provides` names every kind of thing it serves, and `note` says what Archive X does not use (it never asks an add-on for streams)."""
    if not isinstance(manifest, dict) or not (manifest.get("resources") or manifest.get("catalogs")):
        raise ValueError("That address did not answer with a Stremio add-on.")
    resources = [r if isinstance(r, str) else (r or {}).get("name") for r in (manifest.get("resources") or [])]
    lists = []
    for c in manifest.get("catalogs") or []:
        if not isinstance(c, dict) or c.get("type") not in USABLE_TYPES or not c.get("id"):
            continue
        extra = [e for e in (c.get("extra") or []) if isinstance(e, dict)]
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
    if "stream" in resources:
        notes.append("Archive X never asks an add-on for streams.")
    if "subtitles" in resources:
        notes.append("Subtitles from add-ons are not used yet.")
    return {"name": str(manifest.get("name") or "Add-on")[:60], "description": str(manifest.get("description") or "")[:240], "version": str(manifest.get("version") or "")[:20],
            "lists": lists[:40], "provides": provides, "note": " ".join(notes)}
