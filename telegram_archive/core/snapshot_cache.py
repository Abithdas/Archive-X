"""A short-lived cache for a read-only view that several pages ask for again and again.

The Operations page asks for the resource and diagnostics views every 5 seconds from every open window, and each answer takes the database lock
(and the first of them writes to it). One answer is built, kept for a few seconds and shared; callers that arrive while it is being built wait
for that answer instead of each building their own.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class SnapshotCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._at = 0.0
        self._value: Any = None
        self._has = False

    def get(self, build: Callable[[], Any]) -> Any:
        with self._lock:
            now = time.monotonic()
            if self._has and now - self._at < self.ttl:
                return self._value
            value = build()          # an error is not cached: the next caller tries again
            self._value, self._at, self._has = value, time.monotonic(), True
            return value

    def clear(self) -> None:
        with self._lock:
            self._has = False
