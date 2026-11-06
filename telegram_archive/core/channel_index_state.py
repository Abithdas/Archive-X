"""Shared channel-index freshness state.

The channel video catalogue is persistent application data.  This module keeps
freshness/dirty metadata beside the existing index so callers can decide
whether a Telegram history scan is actually necessary.
"""
from __future__ import annotations

import time
from typing import Any

INDEX_STATE_PREFIX = "video_index_state_v2_"
INDEX_STATE_VERSION = 1
DEFAULT_FRESHNESS_SECONDS = 300


def state_key(channel_id: int | str) -> str:
    return f"{INDEX_STATE_PREFIX}{int(channel_id)}"


def load_state(db, channel_id: int | str) -> dict:
    raw = db.setting(state_key(channel_id), {})
    if isinstance(raw, str):
        try:
            import json
            raw = json.loads(raw)
        except Exception:
            raw = {}
    return dict(raw) if isinstance(raw, dict) else {}


def save_state(db, channel_id: int | str, *, record_count: int,
               highest_message_id: int, complete: bool = True,
               dirty: bool = False, indexed_at: float | None = None) -> dict:
    state = {
        "version": INDEX_STATE_VERSION,
        "index_version": 2,
        "record_count": max(0, int(record_count)),
        "highest_message_id": max(0, int(highest_message_id)),
        "complete": bool(complete),
        "dirty": bool(dirty),
        "indexed_at": float(indexed_at if indexed_at is not None else time.time()),
    }
    db.set_setting(state_key(channel_id), state)
    return state


def freshness(state: dict, *, now: float | None = None,
               max_age: int = DEFAULT_FRESHNESS_SECONDS) -> dict:
    now = float(now if now is not None else time.time())
    indexed_at = float(state.get("indexed_at") or 0)
    age = max(0, now - indexed_at) if indexed_at else None
    known = bool(state.get("version") == INDEX_STATE_VERSION and state.get("index_version") == 2 and indexed_at)
    fresh = bool(known and state.get("complete") and not state.get("dirty") and age is not None and age <= max(0, int(max_age)))
    return {"known": known, "fresh": fresh, "age_seconds": int(age) if age is not None else None,
            "max_age_seconds": int(max_age), "dirty": bool(state.get("dirty")),
            "complete": bool(state.get("complete"))}


def status(state: dict, *, now: float | None = None,
           max_age: int = DEFAULT_FRESHNESS_SECONDS) -> dict:
    info = freshness(state, now=now, max_age=max_age)
    return {**info,
            "record_count": int(state.get("record_count") or 0),
            "highest_message_id": int(state.get("highest_message_id") or 0),
            "indexed_at": float(state.get("indexed_at") or 0)}
