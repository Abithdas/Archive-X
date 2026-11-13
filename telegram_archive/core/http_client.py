"""Small persistent outbound HTTP client pool for API/image/RSS traffic.

The application performs many small HTTPS requests to the same metadata and
artwork hosts. Creating a fresh urllib/aiohttp client for every request forces
repeated DNS/TCP/TLS setup.  This module keeps a bounded per-thread requests
Session for synchronous worker traffic and a bounded per-event-loop aiohttp
Session for asynchronous callers.
"""
from __future__ import annotations

from .swallowed import note as _swallowed_note
import asyncio
import threading
import weakref
from typing import Any, Mapping, Optional

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    REQUESTS_AVAILABLE = True
except Exception:  # pragma: no cover
    requests = None
    HTTPAdapter = None
    Retry = None
    REQUESTS_AVAILABLE = False

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except Exception:  # pragma: no cover
    aiohttp = None
    AIOHTTP_AVAILABLE = False

USER_AGENT = "Telegram-Archive/18.40"
_MAX_POOL = 8
_thread_local = threading.local()
_async_sessions: weakref.WeakKeyDictionary[Any, Any] = weakref.WeakKeyDictionary()
_async_session_lock = threading.Lock()


def _requests_session():
    if not REQUESTS_AVAILABLE:
        return None
    session = getattr(_thread_local, "session", None)
    if session is not None:
        return session
    session = requests.Session()
    retries = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.25,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=_MAX_POOL, pool_maxsize=_MAX_POOL, max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT})
    _thread_local.session = session
    return session


def request_bytes(url: str, *, method: str = "GET", data: bytes | None = None,
                  headers: Optional[Mapping[str, str]] = None, timeout: float = 20.0,
                  max_bytes: int | None = None) -> tuple[bytes, str | None, int]:
    """Perform one synchronous request using a persistent connection pool."""
    extra = dict(headers or {})
    if REQUESTS_AVAILABLE:
        session = _requests_session()
        response = session.request(method.upper(), str(url), data=data, headers=extra, timeout=timeout, stream=True)
        try:
            try:
                response.raise_for_status()
            except Exception as exc:
                try:
                    error_body = str(getattr(response, "text", "") or "")[:1200]
                except Exception:
                    error_body = ""
                status_code = int(getattr(response, "status_code", 0) or 0)
                reason = str(getattr(response, "reason", "") or "")
                raise RuntimeError(
                    f"HTTP {status_code} {reason}; response_body={error_body or '<empty>'}"
                ) from exc
            chunks: list[bytes] = []
            total = 0
            limit = None if max_bytes is None else int(max_bytes)
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if limit is not None and total > limit:
                    raise ValueError("HTTP response exceeds configured size limit")
                chunks.append(bytes(chunk))
            return b"".join(chunks), response.headers.get("content-type"), int(response.status_code)
        finally:
            response.close()
    # Fallback for minimal environments without requests.
    import urllib.request
    req = urllib.request.Request(str(url), data=data, headers={"User-Agent": USER_AGENT, **extra}, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(None if max_bytes is None else int(max_bytes) + 1)
            if max_bytes is not None and len(body) > int(max_bytes):
                raise ValueError("HTTP response exceeds configured size limit")
            return body, resp.headers.get("Content-Type"), int(getattr(resp, "status", 200))
    except Exception as exc:
        # urllib's HTTPError is also a readable response. Capture its body before
        # returning control to metadata callers so provider 422s are diagnosable.
        status = getattr(exc, "code", None)
        if status is not None:
            try:
                body = exc.read(1200).decode("utf-8", "replace")
            except Exception:
                body = ""
            reason = str(getattr(exc, "reason", "") or "")
            raise RuntimeError(f"HTTP {status} {reason}; response_body={body or '<empty>'}") from exc
        raise


def request_json(url: str, *, method: str = "GET", data: bytes | None = None,
                 headers: Optional[Mapping[str, str]] = None, timeout: float = 20.0) -> dict:
    body, _mime, status = request_bytes(url, method=method, data=data, headers=headers, timeout=timeout)
    import json
    try:
        value = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Invalid JSON response (HTTP {status})") from exc
    if not isinstance(value, dict):
        raise RuntimeError("Expected a JSON object response")
    return value


async def aiohttp_session(*, timeout: float = 30.0, headers: Optional[Mapping[str, str]] = None):
    """Return a persistent aiohttp session for the current event loop."""
    if not AIOHTTP_AVAILABLE:
        return None
    loop = asyncio.get_running_loop()
    key = loop
    with _async_session_lock:
        session = _async_sessions.get(key)
        if session is not None and not session.closed:
            return session
        connector = aiohttp.TCPConnector(limit=_MAX_POOL, limit_per_host=_MAX_POOL, ttl_dns_cache=300, enable_cleanup_closed=True)
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=float(timeout)),
            headers={"User-Agent": USER_AGENT, **dict(headers or {})},
            connector=connector,
        )
        _async_sessions[key] = session
        return session


async def close_async_sessions():
    with _async_session_lock:
        sessions = list(_async_sessions.values())
        _async_sessions.clear()
    for session in sessions:
        try:
            await session.close()
        except Exception:
            _swallowed_note("http_client.close_async_sessions")
            pass
