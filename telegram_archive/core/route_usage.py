"""Which API routes are really used.

About a third of the API (106 of 325 routes) has no caller in the web app or in the Rust code. Before any of them is deleted, this counts the
calls each route gets in real use, so the decision is made on what happened rather than on a text search. It records the route's path template
and a count (never a request, a user, or any data), keeps the totals across launches in `route-usage.json` in the log folder, and the log
bundle includes it. Remove this module again when the clean-up it serves is finished (REWRITE-AND-CLEANUP-PLAN.md, step C3).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

FLUSH_EVERY_SECONDS = 60.0


class RouteUsage:
    def __init__(self, path: Path | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._counts: dict[str, dict[str, Any]] = {}
        self._dirty = False
        self._last_flush = time.monotonic()
        self._first_seen = time.time()
        if path is not None:
            try:
                saved = json.loads(path.read_text(encoding="utf-8"))
                self._counts = {k: dict(v) for k, v in (saved.get("routes") or {}).items() if isinstance(v, dict)}
                self._first_seen = float(saved.get("counting_since") or self._first_seen)
            except Exception:
                pass

    def hit(self, method: str, template: str) -> None:
        key = f"{method} {template}"
        now = time.time()
        with self._lock:
            row = self._counts.setdefault(key, {"count": 0, "first": now})
            row["count"] = int(row.get("count", 0)) + 1
            row["last"] = now
            self._dirty = True
            due = time.monotonic() - self._last_flush >= FLUSH_EVERY_SECONDS
        if due:
            self.flush()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"counting_since": self._first_seen, "routes": {k: dict(v) for k, v in sorted(self._counts.items())}}

    def flush(self) -> None:
        if self.path is None:
            return
        with self._lock:
            if not self._dirty:
                return
            data = {"counting_since": self._first_seen, "routes": self._counts}
            self._dirty = False
            self._last_flush = time.monotonic()
            text = json.dumps(data, indent=1, sort_keys=True)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass


class RouteUsageMiddleware:
    """Counts a route after it has answered (the route is known by then). WebSockets count as one call per connection."""

    def __init__(self, app, usage: RouteUsage):
        self.app = app
        self.usage = usage

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None)
            if template:
                self.usage.hit("WS" if scope.get("type") == "websocket" else str(scope.get("method") or "GET"), str(template))
