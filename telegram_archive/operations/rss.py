"""RSS feed parsing for Content Automation."""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
try:
    from defusedxml.ElementTree import fromstring as _safe_fromstring
except ImportError:
    def _safe_fromstring(text):
        parser = ET.XMLParser()
        parser.entity = {}
        parser.feed(text if isinstance(text, str) else text.decode("utf-8", "replace"))
        return parser.close()
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import aiohttp


@dataclass(frozen=True)
class RSSItem:
    """One parsed RSS entry."""
    key: str
    guid: str
    title: str
    link: str
    torrent_url: str
    published_at: str
    source_url: str
    enclosure_size: int = 0


def _local(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _clean(value: Optional[str]) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _attr(el: ET.Element, name: str) -> str:
    return _clean(el.attrib.get(name, ""))


def _child_text(el: ET.Element, names: set[str]) -> str:
    for child in el:
        if _local(child.tag) in names:
            text = _clean("".join(child.itertext()))
            if text:
                return text
    return ""


def _parse_size_bytes(value: str) -> int:
    """Parse common RSS/torrent-feed size strings into bytes."""
    raw = _clean(value).replace(",", "")
    if not raw:
        return 0
    match = re.fullmatch(
        r"(\d+(?:\.\d+)?)\s*(bytes?|b|k|kb|kib|m|mb|mib|g|gb|gib|t|tb|tib)?",
        raw, re.I,
    )
    if not match:
        return 0
    amount = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    multipliers = {
        "byte": 1, "bytes": 1, "b": 1,
        "k": 1024, "kb": 1000**1, "kib": 1024**1,
        "m": 1024**2, "mb": 1000**2, "mib": 1024**2,
        "g": 1024**3, "gb": 1000**3, "gib": 1024**3,
        "t": 1024**4, "tb": 1000**4, "tib": 1024**4,
    }
    return int(amount * multipliers[unit])


def _declared_size_elements(el: ET.Element) -> int:
    """Prefer media payload-size elements over RSS enclosure length."""
    names = {"size", "contentlength", "content-length", "contentsize", "filesize"}
    for node in el.iter():
        if node is el or _local(node.tag) == "enclosure":
            continue
        if _local(node.tag) not in names:
            continue
        size = _parse_size_bytes("".join(node.itertext()))
        if size > 0:
            return size
        for attr in ("value", "size", "length", "filesize", "content-length"):
            size = _parse_size_bytes(node.attrib.get(attr, ""))
            if size > 0:
                return size
    return 0


def _enclosure_info(el: ET.Element) -> tuple[str, int, bool]:
    """Return enclosure URL, declared payload size, and whether it is a torrent.

    Some RSS providers expose a torrent enclosure through a URL such as
    ``/download?id=123`` instead of ending the URL with ``.torrent``.  The
    MIME type on the enclosure is the authoritative hint in that case.
    """
    declared = _declared_size_elements(el)
    for child in el:
        if _local(child.tag) != "enclosure":
            continue
        url = _clean(child.attrib.get("url", ""))
        raw_size = (child.attrib.get("length") or child.attrib.get("size") or
                    child.attrib.get("filesize") or child.attrib.get("content-length") or "")
        ctype = _clean(child.attrib.get("type", "")).lower()
        is_magnet = url.lower().startswith("magnet:")
        enclosure_is_torrent = (
            is_magnet
            or Path(urlparse(url).path).suffix.lower() == ".torrent"
            or "torrent" in ctype
            or ctype in {"application/x-bittorrent", "application/bittorrent", "application/x-torrent"}
        )
        torrent_metadata_file = (
            Path(urlparse(url).path).suffix.lower() == ".torrent"
            or "torrent" in ctype
            or ctype in {"application/x-bittorrent", "application/bittorrent", "application/x-torrent"}
        )
        if declared:
            return url, declared, enclosure_is_torrent
        parsed = _parse_size_bytes(raw_size)
        # A .torrent enclosure's length is the torrent metadata size, not the
        # media payload. Tiny declarations are also treated as unknown.
        if torrent_metadata_file or parsed < 1024 * 1024:
            parsed = 0
        return url, parsed, enclosure_is_torrent
    return "", declared, False


def _pick_torrent(link: str, enclosure: str, enclosure_is_torrent: bool, extra_links: list[str], extra_torrent_links: list[str]) -> str:
    if enclosure and (enclosure_is_torrent or enclosure.lower().startswith("magnet:")):
        return enclosure
    # An explicit rel="enclosure"/rel="torrent" or torrent MIME type is
    # sufficient even when the URL path is an opaque download endpoint such as
    # /download?id=123 and therefore does not contain .torrent.
    for value in extra_torrent_links:
        if value:
            return value
    for value in [*extra_links, link]:
        lower = value.lower()
        if value and (lower.startswith("magnet:") or ".torrent" in urlparse(value).path.lower()):
            return value
    return ""


def parse_feed(payload: bytes | str, source_url: str) -> list[RSSItem]:
    root = _safe_fromstring(payload)
    nodes = [el for el in root.iter() if _local(el.tag) in {"item", "entry"}]
    out: list[RSSItem] = []
    for node in nodes:
        guid = _child_text(node, {"guid", "id"})
        title = _child_text(node, {"title"})
        link = _child_text(node, {"link", "url"})
        extra_links = []
        extra_torrent_links = []
        for child in node:
            if _local(child.tag) == "link":
                candidate = _attr(child, "href") or _attr(child, "url") or _clean("".join(child.itertext()))
                if candidate:
                    if not link:
                        link = candidate
                    rel = _attr(child, "rel").lower()
                    ctype = _attr(child, "type").lower()
                    if rel in {"enclosure", "torrent"} or "torrent" in ctype:
                        extra_torrent_links.append(candidate)
                    extra_links.append(candidate)
        if link and not link.startswith(("http://", "https://", "magnet:")):
            link = urljoin(source_url, link)
        enclosure, enclosure_size, enclosure_is_torrent = _enclosure_info(node)
        torrent_url = _pick_torrent(link, enclosure, enclosure_is_torrent, extra_links, extra_torrent_links)
        published_at = _child_text(node, {"pubdate", "published", "updated", "date", "dc:date"})
        identity = guid or torrent_url or link or f"{title}|{published_at}"
        key = hashlib.sha256((source_url + "\n" + identity).encode("utf-8", "ignore")).hexdigest()
        if not title and not (torrent_url or link):
            continue
        out.append(RSSItem(key, guid, title or identity, link, torrent_url, published_at, source_url, enclosure_size))
    return out


async def fetch_feed(source_url: str, *, timeout: float = 30.0) -> list[RSSItem]:
    url = str(source_url or "").strip()
    if not url:
        raise ValueError("RSS feed URL is required")
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("RSS feed URL must use http:// or https://")
    from ..core.http_client import aiohttp_session
    session = await aiohttp_session(timeout=timeout, headers={"User-Agent": "Telegram-Archive-Operations/18.40"})
    if session is None:
        client_timeout = aiohttp.ClientTimeout(total=float(timeout))
        async with aiohttp.ClientSession(timeout=client_timeout, headers={"User-Agent": "Telegram-Archive-Operations/18.40"}) as fallback:
            async with fallback.get(url) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"RSS HTTP {resp.status}")
                payload = await resp.read()
    else:
        async with session.get(url) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"RSS HTTP {resp.status}")
            payload = await resp.read()
    return parse_feed(payload, url)
