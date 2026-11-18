"""Registry of the helper processes the backend starts (FFmpeg, the PyAV worker).

The Jobs page lists these so a long conversion is visibly *a process doing work* and not a frozen job. Registering is
cheap and never raises; an entry disappears when its process ends.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_lock = threading.Lock()
_entries: dict[int, dict] = {}


@contextmanager
def tracked(proc, label: str, *, kind: str = "ffmpeg", job_id: str | None = None):
    """Track ``proc`` while the ``with`` block runs. Yields an object whose ``.progress`` (0..1) can be set."""
    entry = {"pid": int(getattr(proc, "pid", 0) or 0), "label": str(label), "kind": kind, "job_id": job_id or "",
             "started": time.monotonic(), "progress": None, "proc": proc}
    key = id(entry)
    with _lock:
        _entries[key] = entry
    try:
        yield _Handle(entry)
    finally:
        with _lock:
            _entries.pop(key, None)


class _Handle:
    def __init__(self, entry: dict):
        self._e = entry

    def set_progress(self, fraction: float) -> None:
        try:
            self._e["progress"] = max(0.0, min(1.0, float(fraction)))
        except (TypeError, ValueError):
            pass


def snapshot() -> list[dict]:
    now = time.monotonic()
    with _lock:
        items = list(_entries.values())
    out = []
    for e in items:
        proc = e.get("proc")
        try:
            alive = proc is not None and proc.poll() is None
        except Exception:
            alive = False
        p = e.get("progress")
        out.append({"pid": e["pid"], "label": e["label"], "kind": e["kind"], "job_id": e["job_id"],
                    "running_seconds": round(now - e["started"], 1), "alive": alive,
                    "progress": None if p is None else round(p * 100, 1)})
    out.sort(key=lambda r: -r["running_seconds"])
    return out
