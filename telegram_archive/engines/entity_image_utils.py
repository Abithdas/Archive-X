"""Shared helpers for performer/studio artwork synchronization."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse


def provider_rank(source: str) -> int:
    source = str(source or "").strip().casefold()
    if source == "stashdb":
        return 2
    if source in {"theporndb", "tpdb"}:
        return 1
    return 0


def http_image_url(value: Any) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() in {"http", "https"} and parsed.netloc:
            return url
    except Exception:
        pass
    return ""


def normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def same_name(a: str, b: str) -> bool:
    left = normalize_name(a)
    return bool(left and left == normalize_name(b))


def entity_image_cache_valid(db, entity_type: str, entity_id: int, url: str) -> bool:
    """Return whether the canonical local artwork cache contains this exact URL.

    The table and key are selected only from the two supported entity kinds.
    A URL in the entity row alone is not considered a usable local image.
    """
    kind = str(entity_type or "").strip().casefold()
    if kind == "performer":
        table, key = "performer_image_cache", "performer_id"
    elif kind == "studio":
        table, key = "studio_image_cache", "studio_id"
    else:
        return False
    if not str(url or "").strip():
        return False
    try:
        with db.lock:
            row = db.conn.execute(
                f"SELECT 1 FROM {table} WHERE {key}=? AND url=? "
                "AND data IS NOT NULL AND length(data)>0 LIMIT 1",
                (int(entity_id), str(url)),
            ).fetchone()
        return bool(row)
    except Exception:
        return False
