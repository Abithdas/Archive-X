"""How long each web route takes to start answering, so what is slow is found by measuring and not by guessing.

A plain ASGI wrapper (no per-request threads, and a streamed video is not counted for its whole length: the clock stops at the first byte of the
response). Routes are counted by their pattern (`/api/performers/{performer_id}`), not by the address, so ids do not make thousands of rows.
`summary()` lists the routes by their total time and is part of the diagnostics snapshot.
"""
from __future__ import annotations

import threading
import time

_ROUTES: dict[str, list] = {}      # "GET /api/x/{id}" -> [count, total ms, longest ms, calls over 500 ms]
_LOCK = threading.Lock()
_MAX_ROUTES = 600
SLOW_MS = 500.0


class RouteTimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        done = False

        async def timed_send(message):
            nonlocal done
            if not done and message.get("type") == "http.response.start":
                done = True
                _record(scope, (time.perf_counter() - started) * 1000.0)
            await send(message)

        await self.app(scope, receive, timed_send)


def _record(scope, ms: float) -> None:
    try:
        route = scope.get("route")
        path = getattr(route, "path", None)
        if not path:
            return
        key = f"{scope.get('method', 'GET')} {path}"
        with _LOCK:
            row = _ROUTES.get(key)
            if row is None:
                if len(_ROUTES) >= _MAX_ROUTES:
                    return
                row = _ROUTES[key] = [0, 0.0, 0.0, 0]
            row[0] += 1
            row[1] += ms
            row[2] = max(row[2], ms)
            if ms >= SLOW_MS:
                row[3] += 1
    except Exception:
        pass


def summary(limit: int = 15) -> list[dict]:
    with _LOCK:
        rows = sorted(_ROUTES.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]
    return [{"route": k, "calls": r[0], "total_ms": round(r[1]), "average_ms": round(r[1] / r[0], 1), "longest_ms": round(r[2]), "over_500ms": r[3]}
            for k, r in rows]


def reset() -> None:
    with _LOCK:
        _ROUTES.clear()
