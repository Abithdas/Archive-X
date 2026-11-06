"""HTTP performance and security middleware for Archive X V6."""
from __future__ import annotations

import gzip
import hashlib
import zlib
import threading
import time
from typing import Callable


_TEXT_TYPES = ("application/json", "application/javascript", "text/", "application/xml")
_SKIP_PREFIXES = (
    "/stream", "/api/play/", "/api/thumb/", "/static/", "/ca.crt", "/api/log/download",
)


def _is_safe_cache_path(path: str) -> bool:
    if any(path.startswith(p) for p in _SKIP_PREFIXES):
        return False
    return path.startswith("/api/") or path in {"/", "/login", "/health"}


class SecurityHeadersMiddleware:
    """ASGI middleware that adds security headers to responses."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message):
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {k.lower() for k, _ in headers}
                additions = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
                    (b"cross-origin-opener-policy", b"same-origin"),
                    (b"cross-origin-resource-policy", b"same-origin"),
                    (b"content-security-policy", (
                        "default-src 'self'; base-uri 'self'; object-src 'none'; "
                        "frame-ancestors 'self'; img-src 'self' data: blob: https:; "
                        "media-src 'self' blob:; font-src 'self' data:; "
                        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                        "connect-src 'self' ws: wss: https:"
                    ).encode("ascii")),
                ]
                if scope.get("scheme") == "https":
                    additions.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                for key, value in additions:
                    if key not in existing:
                        headers.append((key, value))
                message = dict(message)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)


class APIRateLimitMiddleware:
    """IP-based rate limiter for unauthenticated endpoints (login, setup, health)."""

    _UNAUTHENTICATED_PATHS = {"/login", "/api/auth/setup", "/api/login-hint", "/health"}

    def __init__(self, app, *, max_requests: int = 30, window: int = 60):
        self.app = app
        self.max_requests = max_requests
        self.window = window
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = {}

    def _client_ip(self, scope) -> str:
        for k, v in scope.get("headers") or []:
            if k.lower() == b"x-forwarded-for":
                return v.decode("latin1", "ignore").split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path not in self._UNAUTHENTICATED_PATHS:
            await self.app(scope, receive, send)
            return
        ip = self._client_ip(scope)
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._buckets.get(ip, []) if now - t < self.window]
            if len(recent) >= self.max_requests:
                self._buckets[ip] = recent
                from starlette.responses import JSONResponse
                resp = JSONResponse({"detail": "Too many requests"}, status_code=429)
                await resp(scope, receive, send)
                return
            recent.append(now)
            self._buckets[ip] = recent
            if len(self._buckets) > 10000:
                cutoff = now - self.window
                self._buckets = {k: v for k, v in self._buckets.items() if v and v[-1] > cutoff}
        await self.app(scope, receive, send)


class BrowserMutationGuardMiddleware:
    """Reject cross-origin state-changing API requests carrying the session cookie."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if method in {"POST", "PUT", "PATCH", "DELETE"} and (
            path.startswith("/api/") or path in {"/login", "/logout"}
        ):
            headers = {
                k.decode("latin1").lower(): v.decode("latin1", "ignore")
                for k, v in scope.get("headers") or []
            }
            cookie = headers.get("cookie", "")
            origin = headers.get("origin", "")
            # Any browser-originated cross-site mutation is rejected, with or without a
            # cookie: /api/auth/setup and /login are reachable before a session exists,
            # and request.json() accepts a text/plain "simple" cross-site POST.
            if origin:
                host = headers.get("host", "")
                # Compare the origin's authority with the Host header; scheme is intentionally ignored
                # so HTTP localhost development and HTTPS deployment both work.
                try:
                    from urllib.parse import urlsplit
                    netloc = urlsplit(origin).netloc
                except Exception:
                    netloc = ""
                if not netloc or netloc.lower() != host.lower():
                    from starlette.responses import JSONResponse
                    resp = JSONResponse({"detail": "Cross-origin mutation rejected"}, status_code=403)
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)


class ResponseOptimizationMiddleware:
    """Small-response compression + ETag support without touching video streams.

    Bodies up to ``max_body`` are buffered for optional compression/ETag handling.
    Once a response exceeds that limit, the middleware switches to pass-through
    streaming. It must never truncate the buffered prefix while retaining the
    origin response's full Content-Length (large composed HTML pages can exceed
    the limit even when they are ordinary document responses).
    """
    def __init__(self, app, *, max_body=2 * 1024 * 1024, min_compress=1024):
        self.app = app
        self.max_body = max_body
        self.min_compress = min_compress

    async def __call__(self, scope, receive, send):
        """Buffer eligible GET/HEAD responses so they can be compressed and tagged."""
        if scope.get("type") != "http" or scope.get("method", "GET").upper() not in {"GET", "HEAD"}:
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not _is_safe_cache_path(path):
            await self.app(scope, receive, send)
            return

        start = {}
        body_parts = []
        total = 0
        overflow = False
        accepted_gzip = False
        for k, v in scope.get("headers") or []:
            if k.lower() == b"accept-encoding" and b"gzip" in v.lower():
                accepted_gzip = True
                break

        async def _send(message):
            nonlocal total, overflow
            message_type = message.get("type")

            # After the size threshold has been crossed, the original response
            # has already started. Forward every subsequent ASGI message verbatim.
            if overflow:
                await send(message)
                return

            if message_type == "http.response.start":
                start.update(message)
                return

            if message_type == "http.response.body":
                chunk = message.get("body", b"") or b""
                if total + len(chunk) > self.max_body:
                    # The complete response is too large for safe buffering.
                    # Switch to transparent streaming: send the untouched start
                    # headers, flush buffered chunks, then forward this chunk and
                    # all later messages. This keeps Content-Length and bytes in
                    # sync and avoids truncating large HTML/JSON responses.
                    overflow = True
                    await send(start)
                    for part in body_parts:
                        await send({"type": "http.response.body", "body": part, "more_body": True})
                    body_parts.clear()
                    await send(message)
                    return

                total += len(chunk)
                body_parts.append(chunk)
                if not message.get("more_body", False):
                    await self._finish(scope, start, body_parts, False, accepted_gzip, send)
                return

            await send(message)

        await self.app(scope, receive, _send)

    async def _finish(self, scope, start, parts, overflow, accepted_gzip, send):
        """Send a buffered response, applying gzip and ETag handling."""
        headers = list(start.get("headers") or [])
        h = {k.lower(): v for k, v in headers}
        body = b"".join(parts)
        if b"content-encoding" in h or int(start.get("status", 200)) == 304:
            # Already encoded/validated by the handler (e.g. the precompressed app shell).
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return
        ctype = (h.get(b"content-type", b"") or b"").decode("latin1", "ignore").lower()
        should_opt = not overflow and any(ctype.startswith(t) for t in _TEXT_TYPES)
        if not should_opt:
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})
            return

        etag = '"' + format(zlib.adler32(body) & 0xffffffff, '08x') + '"'
        request_headers = {k.lower(): v for k, v in scope.get("headers") or []}
        inm = request_headers.get(b"if-none-match", b"").decode("latin1", "ignore")
        no_store = b"no-store" in h.get(b"cache-control", b"").lower()
        if not no_store and inm and inm.strip() == etag:
            out = [(k, v) for k, v in headers if k.lower() not in {b"content-length", b"content-encoding"}]
            out.append((b"etag", etag.encode("ascii")))
            out.append((b"cache-control", b"private, no-cache"))
            await send({"type": "http.response.start", "status": 304, "headers": out})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        out = [(k, v) for k, v in headers if k.lower() not in {b"content-length", b"content-encoding", b"etag", b"cache-control"}]
        compressed = body
        if accepted_gzip and len(body) >= self.min_compress:
            compressed = gzip.compress(body, compresslevel=5, mtime=0)
            out.append((b"content-encoding", b"gzip"))
            out.append((b"vary", b"Accept-Encoding"))
        if not no_store:
            out.append((b"etag", etag.encode("ascii")))
        out.append((b"cache-control", h[b"cache-control"] if no_store else b"private, no-cache"))
        out.append((b"content-length", str(len(compressed)).encode("ascii")))
        start_msg = {"type": "http.response.start", "status": start.get("status", 200), "headers": out}
        await send(start_msg)
        await send({"type": "http.response.body", "body": compressed, "more_body": False})
