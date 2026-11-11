"""Desktop mode: Archive X running as the backend of the standalone desktop app.

The desktop shell (Tauri, see desktop/) starts this process as a sidecar and loads its UI in a native
window. In desktop mode the server is local-only and reachable only by that shell:

* Binds to 127.0.0.1 on a free port (no LAN exposure, no firewall prompt, no certificate: plain HTTP on
  loopback is a secure context for WebView2).
* Every request must carry the per-launch secret the shell generated (header or cookie). Other programs on
  the machine that find the port cannot use the API.
* The shell opens ``/desktop/session?token=<secret>`` once; that creates the normal Archive X web session
  (no web login screen in local mode) and a guard cookie, then redirects to ``/``.
* ``POST /desktop/shutdown`` (secret header) performs the normal graceful shutdown.
* If the shell dies, the backend notices (parent process watchdog) and shuts down instead of lingering.

Everything is opt-in through environment variables set by the shell, so the source-run (browser) mode and the
LAN mode are unchanged:

    ARCHIVE_X_DESKTOP=1            enable desktop mode
    ARCHIVE_X_LAUNCH_SECRET=<hex>  per-launch secret (generated here if absent, for development)
    ARCHIVE_X_DESKTOP_PORT=<n>     port, 0/absent = pick a free one
    ARCHIVE_X_PARENT_PID=<pid>     process to watch; exit when it disappears
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
from typing import Any

from fastapi import Request

from .. import config

HEADER = b"x-archive-x-desktop"
COOKIE = "ax_desktop"
READY_PREFIX = "ARCHIVE_X_READY "
# Reachable without the secret: liveness probe only (it reveals nothing but the version).
_OPEN_PATHS = {"/health"}


def landing() -> str:
    """Path the desktop window opens after the session hand-off: "/" by default.
    Set with ARCHIVE_X_DESKTOP_LANDING; anything that is not a plain absolute path falls back to "/"."""
    value = os.getenv("ARCHIVE_X_DESKTOP_LANDING", "/").strip() or "/"
    ok = value.startswith("/") and not value.startswith("//") and "\\" not in value and ":" not in value and len(value) < 200
    return value if ok else "/"


def _handoff_page() -> str:
    import html as _html
    target = landing()
    return (
        '<!doctype html><meta charset="utf-8"><title>Archive X</title>'
        f'<meta http-equiv="refresh" content="0;url={_html.escape(target, quote=True)}">'
        f'<style>html{{background:#232833}}</style><script>location.replace({json.dumps(target)})</script>'
    )


def enabled() -> bool:
    return os.getenv("ARCHIVE_X_DESKTOP", "0").strip().lower() in {"1", "true", "yes", "on"}


def frozen() -> bool:
    """True when running from a PyInstaller/Nuitka bundle (the install folder is read-only)."""
    return bool(getattr(sys, "frozen", False))


_SECRET: str | None = None


def launch_secret() -> str:
    global _SECRET
    if _SECRET is None:
        _SECRET = (os.getenv("ARCHIVE_X_LAUNCH_SECRET") or "").strip() or secrets.token_hex(32)
    return _SECRET


def _cookie_value() -> str:
    # The cookie never contains the secret itself.
    return hmac.new(launch_secret().encode(), b"archive-x-desktop-cookie", hashlib.sha256).hexdigest()


def secret_matches(candidate: str | None) -> bool:
    return bool(candidate) and hmac.compare_digest(str(candidate), launch_secret())


def pick_socket(port: int = 0) -> socket.socket:
    """Bind a loopback listening socket now, so the chosen port cannot be taken before uvicorn starts."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(port)))
    sock.set_inheritable(True)
    return sock


def requested_port() -> int:
    try:
        return max(0, int(os.getenv("ARCHIVE_X_DESKTOP_PORT", "0") or 0))
    except ValueError:
        return 0


def prepare_credentials(db) -> None:
    """Local mode has no web login: complete first-run setup with a random, never-disclosed password."""
    from ..core import auth
    if not db.setting(auth.SETUP_COMPLETE_FLAG):
        auth.set_web_login(db, auth.DEFAULT_WEB_USERNAME, secrets.token_urlsafe(32))
        db.set_setting(auth.SETUP_COMPLETE_FLAG, config.now_iso())
        db.set_setting(auth.SETUP_REQUIRED_FLAG, "")
        db.log("Desktop mode: first-run web login skipped (local-only session)")


class DesktopGuardMiddleware:
    """Reject every HTTP and WebSocket request that does not carry the per-launch secret."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        kind = scope.get("type")
        if kind not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        if path in _OPEN_PATHS and kind == "http":
            await self.app(scope, receive, send)
            return
        if secret_matches(headers.get(HEADER, b"").decode("latin1", "ignore")):
            await self.app(scope, receive, send)
            return
        cookie = headers.get(b"cookie", b"").decode("latin1", "ignore")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and hmac.compare_digest(value, _cookie_value()):
                await self.app(scope, receive, send)
                return
        if path == "/desktop/session" and kind == "http":
            await self.app(scope, receive, send)   # validates the ?token= itself
            return
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 4403})
            return
        body = b'{"detail":"Archive X desktop session required"}'
        await send({"type": "http.response.start", "status": 403,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def install(app, db, sessions) -> None:
    """Add the guard middleware and the /desktop/* endpoints to the FastAPI app (desktop mode only)."""
    from fastapi.responses import HTMLResponse, JSONResponse
    from .session_cookie import set_session_cookie
    from . import auth

    @app.get("/desktop/session", include_in_schema=False)
    async def desktop_session(request: Request, token: str = ""):
        if not secret_matches(token):
            return JSONResponse({"detail": "Invalid desktop token"}, status_code=403)
        username = str(db.setting("web_username") or auth.DEFAULT_WEB_USERNAME)
        # Not a 302: the shell opens this URL from its own origin (tauri://localhost), so the WebView treats a
        # redirect chain as cross-site and withholds the SameSite=Strict cookies from the redirected request.
        # A page that navigates itself starts a fresh same-site navigation, and the cookies are sent.
        response = HTMLResponse(_handoff_page(), headers={"Cache-Control": "no-store"})
        set_session_cookie(response, request, sessions.create(username))
        response.set_cookie(COOKIE, _cookie_value(), path="/", httponly=True, samesite="strict",
                            secure=False, max_age=config.SESSION_TTL_SECONDS)
        return response

    @app.get("/desktop/info", include_in_schema=False)
    async def desktop_info():
        # Reachable only with the guard cookie/secret. Lets the web UI know it runs inside the desktop shell
        # (no web login exists there, so it hides "Sign out").
        return JSONResponse({"desktop": True, "version": config.APP_VERSION}, headers={"Cache-Control": "no-store"})

    @app.post("/desktop/shutdown", include_in_schema=False)
    async def desktop_shutdown(request: Request):
        if not secret_matches(request.headers.get("x-archive-x-desktop")):
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
        stop = getattr(request.app.state, "request_shutdown", None)
        if not callable(stop):
            return JSONResponse({"detail": "No shutdown controller"}, status_code=503)
        import asyncio
        asyncio.get_running_loop().call_later(0.3, stop)
        return JSONResponse({"ok": True, "shutdown": "scheduled"}, status_code=202)

    app.add_middleware(DesktopGuardMiddleware)   # added last = outermost: runs before everything else


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        handle = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def watch_parent(stop_callback, *, interval: float = 2.0) -> threading.Thread | None:
    """Stop the backend if the desktop shell disappears (crash, force quit)."""
    try:
        pid = int(os.getenv("ARCHIVE_X_PARENT_PID", "0") or 0)
    except ValueError:
        pid = 0
    if pid <= 0:
        return None

    def run():
        while True:
            time.sleep(interval)
            if not _pid_alive(pid):
                try:
                    stop_callback()
                finally:
                    # If the graceful path hangs, do not linger as an orphan.
                    time.sleep(20)
                    os._exit(0)

    thread = threading.Thread(target=run, name="archive-x-parent-watchdog", daemon=True)
    thread.start()
    return thread


def announce_ready(port: int) -> None:
    """One machine-readable line on stdout for the desktop shell: it learns the port from this."""
    payload: dict[str, Any] = {"port": int(port), "pid": os.getpid(), "version": config.APP_VERSION}
    print(READY_PREFIX + json.dumps(payload), flush=True)
