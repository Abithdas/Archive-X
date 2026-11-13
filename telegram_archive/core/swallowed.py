"""Records errors that best-effort code deliberately ignores, so they stop being invisible.

Call `note("where")` inside an `except` block that would otherwise just `pass`. The first time a cause is seen it is
logged as a warning; after that it is only counted. `snapshot()` is shown in diagnostics, so a stalled transfer can be
traced to the thing that kept failing quietly.
"""
from __future__ import annotations

import logging
import sys
import threading
import time

_log = logging.getLogger("archive_x.swallowed")
_lock = threading.Lock()
_seen: dict[tuple[str, str], dict] = {}
MAX_CAUSES = 300


def note(where: str, exc: BaseException | None = None) -> None:
    exc = exc or sys.exc_info()[1]
    if exc is None:
        return
    kind = type(exc).__name__
    text = str(exc)[:200]
    key = (where, kind)
    now = time.time()
    with _lock:
        row = _seen.get(key)
        first = row is None
        if first:
            if len(_seen) >= MAX_CAUSES:
                _seen.pop(min(_seen, key=lambda k: _seen[k]["last"]), None)
            row = _seen[key] = {"where": where, "error": kind, "message": text, "count": 0, "first": now, "last": now}
        row["count"] += 1
        row["last"] = now
        row["message"] = text
    if first:
        _log.warning("ignored error in %s: %s: %s", where, kind, text)


def snapshot(limit: int = 50) -> list[dict]:
    """The most frequent ignored errors first."""
    with _lock:
        rows = [dict(r) for r in _seen.values()]
    rows.sort(key=lambda r: (-r["count"], -r["last"]))
    return rows[:limit]


def reset() -> None:
    with _lock:
        _seen.clear()
