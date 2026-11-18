"""Direct links to one item: refuse what the current SFW / NSFW mode hides.

The lists are filtered in the database (core/content_mode.py); this covers an address typed or kept from the other mode, such as
``/stream/<channel>/<video>`` or ``/api/performers/12``. An item that does not exist at all is left for its route to answer; only an
item that exists but belongs to the other mode is answered with 404, the same as if it were not there.
"""
from __future__ import annotations

import re
import threading
import time

from .content_mode import FILTER_ACTIVE, get_mode

_VIDEO = re.compile(r"^/(?:stream|stream-cached|stream-compat|hls|api/thumb|api/play/[a-z-]+)/(-?\d+)/(\d+)(?:[/.]|$)")
_PHOTO = re.compile(r"^/api/photos/(-?\d+)/(\d+)(?:/|$)")
_CHANNEL = re.compile(r"^/api/channels/(-?\d+)(?:/|$)")
_ENTITY = re.compile(r"^/api/(performers|studios)/(\d+)(?:/|$)")

_TTL = 3.0
_cache: dict[tuple, tuple[float, bool]] = {}
_lock = threading.Lock()


def _exists(db, sql: str, args: tuple, filtered: bool) -> bool:
    token = FILTER_ACTIVE.set(filtered)
    try:
        with db.reading() as conn:
            return conn.execute(sql, args).fetchone() is not None
    finally:
        FILTER_ACTIVE.reset(token)


def target(path: str) -> tuple[str, tuple] | None:
    """(SQL that finds the item, its arguments) for an address that names one item, else None."""
    m = _VIDEO.match(path)
    if m:
        return "SELECT 1 FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1", (m.group(1), int(m.group(2)))
    m = _PHOTO.match(path)
    if m:
        return "SELECT 1 FROM photo_library_items WHERE channel_id=? AND message_id=? LIMIT 1", (m.group(1), int(m.group(2)))
    m = _CHANNEL.match(path)
    if m:
        return "SELECT 1 FROM channels WHERE telegram_id=? LIMIT 1", (m.group(1),)
    m = _ENTITY.match(path)
    if m:
        return f"SELECT 1 FROM {m.group(1)} WHERE id=? LIMIT 1", (int(m.group(2)),)
    return None


def hidden(db, path: str) -> bool:
    """True when the address names an item that exists but is not shown in the current mode."""
    found = target(path)
    if not found:
        return False
    sql, args = found
    key = (get_mode(db), sql, args)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _TTL:
            return hit[1]
    result = _exists(db, sql, args, False) and not _exists(db, sql, args, True)
    with _lock:
        if len(_cache) > 4096:
            _cache.clear()
        _cache[key] = (now, result)
    return result
