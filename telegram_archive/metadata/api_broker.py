"""Provider-aware metadata API broker.

All StashDB/ThePornDB requests pass through this module before reaching the
shared HTTP client.  It provides bounded concurrency, pacing, adaptive
cooldown/backoff and short-lived in-flight deduplication.  Persistent metadata
caching remains in :mod:`metadata.providers`; this broker protects the API
boundary itself when several Archive X workers miss the cache at once.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping, Any


@dataclass
class _Policy:
    """Rate limit settings for one provider."""
    min_interval: float
    max_concurrency: int
    max_backoff: float = 120.0


@dataclass
class _State:
    """Current rate limit state for one provider."""
    next_allowed: float = 0.0
    cooldown_until: float = 0.0
    consecutive_limits: int = 0
    requests: int = 0
    deduplicated: int = 0
    throttled: int = 0
    retries: int = 0
    errors: int = 0
    last_latency_ms: float | None = None
    last_error: str = ""
    last_request_at: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    semaphore: threading.BoundedSemaphore | None = None


_DEFAULT_POLICIES = {
    # Conservative defaults. They are deliberately independent so one provider
    # being slow/rate-limited never stalls the other provider.
    "stashdb": _Policy(
        min_interval=float(os.getenv("ARCHIVE_X_STASHDB_MIN_INTERVAL", "0.50")),
        max_concurrency=max(1, int(os.getenv("ARCHIVE_X_STASHDB_CONCURRENCY", "2"))),
    ),
    "tpdb": _Policy(
        min_interval=float(os.getenv("ARCHIVE_X_TPDB_MIN_INTERVAL", "0.75")),
        max_concurrency=max(1, int(os.getenv("ARCHIVE_X_TPDB_CONCURRENCY", "2"))),
    ),
}

_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.Lock()
_RESULT_CACHE: dict[str, tuple[float, Any]] = {}
_RESULT_LOCK = threading.Lock()
_RESULT_TTL = max(0.0, float(os.getenv("ARCHIVE_X_METADATA_SINGLEFLIGHT_TTL", "5")))


def _state(provider: str) -> tuple[_Policy, _State]:
    provider = provider.casefold()
    policy = _DEFAULT_POLICIES.get(provider, _Policy(0.5, 2))
    with _STATES_LOCK:
        state = _STATES.get(provider)
        if state is None:
            state = _State(semaphore=threading.BoundedSemaphore(policy.max_concurrency))
            _STATES[provider] = state
    return policy, state


def provider_for_url(url: str) -> str:
    from urllib.parse import urlsplit
    try:
        host = (urlsplit(str(url or "")).hostname or "").casefold()
    except ValueError:
        return "other"
    # The host itself or one of its subdomains: "stashdb.org.evil.example" or a path that merely mentions it does not count.
    for domain, name in (("stashdb.org", "stashdb"), ("theporndb.net", "tpdb")):
        if host == domain or host.endswith("." + domain):
            return name
    return "other"


def _request_key(provider: str, url: str, data: bytes | None, headers: Mapping[str, str] | None) -> str:
    # Do not include authentication material in the key. Authorization/APIKey
    # headers only distinguish credentials, not the resource being requested.
    safe_headers = {}
    secret_fingerprint = ""
    for key, value in (headers or {}).items():
        key_l = str(key).casefold()
        if key_l in {"authorization", "apikey", "api-key"}:
            secret_fingerprint += f"{key_l}:{hashlib.pbkdf2_hmac("sha256", str(value).encode(), b"archive-x-key-fingerprint", 1000).hex()}\n"
        else:
            safe_headers[key_l] = str(value)
    payload = data or b""
    raw = json.dumps({"provider": provider, "url": str(url), "data": payload.decode("utf-8", "replace"),
                      "headers": safe_headers, "credential": secret_fingerprint}, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _http_status(exc: Exception) -> int | None:
    """Resolve an HTTP response status from common client exception shapes."""
    for candidate in (
        getattr(exc, "status_code", None), getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
        getattr(getattr(exc, "response", None), "status", None),
    ):
        try:
            value = int(candidate)
            if 100 <= value <= 599:
                return value
        except (TypeError, ValueError, OverflowError):
            pass
    import re
    match = re.search(r"\b(429|5\d\d)\b", str(exc or ""))
    return int(match.group(1)) if match else None


def _retry_after(exc: Exception) -> float | None:
    """Read Retry-After from response headers or exception text."""
    import re
    candidates = []
    response = getattr(exc, "response", None)
    for headers in (getattr(response, "headers", None), getattr(exc, "headers", None), getattr(exc, "hdrs", None)):
        if headers is not None:
            try:
                value = headers.get("Retry-After") or headers.get("retry-after")
                if value is not None:
                    candidates.append(str(value))
            except Exception:
                _swallowed_note("api_broker._retry_after")
                pass
    candidates.append(str(exc or ""))
    patterns = (
        r"retry-after\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)",
        r"retry_after\s*[=:]\s*([0-9]+(?:\.[0-9]+)?)",
        r"^\s*([0-9]+(?:\.[0-9]+)?)\s*$",
    )
    for text in candidates:
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    return max(0.0, min(3600.0, float(match.group(1))))
                except (TypeError, ValueError, OverflowError):
                    continue
    return None


def _is_retryable(exc: Exception) -> bool:
    status = _http_status(exc)
    if status in {408, 429} or (status is not None and 500 <= status <= 599):
        return True
    low = str(exc).casefold()
    return any(token in low for token in (
        "408", "429", "timed out", "timeout", "temporarily unavailable",
        "connection reset", "connection aborted", "connection refused", "unreachable",
    ))

def _wait_for_slot(policy: _Policy, state: _State) -> None:
    while True:
        with state.lock:
            now = time.monotonic()
            target = max(state.next_allowed, state.cooldown_until)
            wait = max(0.0, target - now)
            if wait <= 0:
                state.next_allowed = now + max(0.0, policy.min_interval)
                state.requests += 1
                state.last_request_at = now
                return
            state.throttled += 1
        time.sleep(min(wait, 1.0))


def _mark_failure(policy: _Policy, state: _State, exc: Exception, retry_after: float | None = None) -> None:
    with state.lock:
        state.errors += 1
        state.last_error = str(exc)[:500]
        status = _http_status(exc)
        limited = status == 429 or (status is not None and 500 <= status <= 599)
        if not limited:
            import re
            error_text = str(exc).casefold()
            limited = bool(re.search(r"\b429\b|\b5\d\d\b", error_text))
        if limited:
            state.consecutive_limits += 1
            base = retry_after if retry_after is not None else min(policy.max_backoff, 5.0 * (2 ** min(state.consecutive_limits - 1, 5)))
            state.cooldown_until = max(state.cooldown_until, time.monotonic() + min(policy.max_backoff, base))
        else:
            state.consecutive_limits = 0


def _mark_success(policy: _Policy, state: _State, latency_ms: float) -> None:
    with state.lock:
        state.consecutive_limits = 0
        state.cooldown_until = min(state.cooldown_until, time.monotonic())
        state.last_latency_ms = latency_ms
        state.last_error = ""


def request_json(provider: str, url: str, *, data: bytes | None, headers: Mapping[str, str],
                 timeout: float, request_fn: Callable[..., dict]) -> dict:
    """Run one provider request through the shared broker."""
    provider = provider.casefold()
    if provider not in _DEFAULT_POLICIES:
        return request_fn(url, data=data, headers=dict(headers), timeout=timeout)

    policy, state = _state(provider)
    key = _request_key(provider, url, data, headers)

    if _RESULT_TTL:
        with _RESULT_LOCK:
            cached = _RESULT_CACHE.get(key)
            if cached and time.monotonic() - cached[0] < _RESULT_TTL:
                with state.lock:
                    state.deduplicated += 1
                return cached[1]
            if cached:
                _RESULT_CACHE.pop(key, None)

    assert state.semaphore is not None
    state.semaphore.acquire()
    try:
        # Re-check after waiting for concurrency: another worker may have
        # completed the same request while this worker was queued.
        if _RESULT_TTL:
            with _RESULT_LOCK:
                cached = _RESULT_CACHE.get(key)
                if cached and time.monotonic() - cached[0] < _RESULT_TTL:
                    with state.lock:
                        state.deduplicated += 1
                    return cached[1]

        max_retries = 2
        for attempt in range(max_retries + 1):
            _wait_for_slot(policy, state)
            # A duplicate may have completed while this worker was waiting for
            # the provider slot. Re-check immediately before hitting the API.
            if _RESULT_TTL:
                with _RESULT_LOCK:
                    cached = _RESULT_CACHE.get(key)
                    if cached and time.monotonic() - cached[0] < _RESULT_TTL:
                        with state.lock:
                            state.deduplicated += 1
                        return cached[1]
            started = time.perf_counter()
            try:
                result = request_fn(url, data=data, headers=dict(headers), timeout=timeout)
                _mark_success(policy, state, (time.perf_counter() - started) * 1000.0)
                if _RESULT_TTL:
                    with _RESULT_LOCK:
                        _RESULT_CACHE[key] = (time.monotonic(), result)
                return result
            except Exception as exc:
                _mark_failure(policy, state, exc, _retry_after(exc))
                if attempt >= max_retries or not _is_retryable(exc):
                    raise
                with state.lock:
                    state.retries += 1
                    cooldown = max(0.0, state.cooldown_until - time.monotonic())
                time.sleep(min(policy.max_backoff, max(0.25, cooldown)))
        raise RuntimeError("metadata provider request exhausted retries")
    finally:
        state.semaphore.release()


def snapshot() -> dict[str, dict[str, Any]]:
    now = time.monotonic()
    out: dict[str, dict[str, Any]] = {}
    for provider, policy in _DEFAULT_POLICIES.items():
        _p, state = _state(provider)
        with state.lock:
            out[provider] = {
                "min_interval": policy.min_interval,
                "max_concurrency": policy.max_concurrency,
                "requests": state.requests,
                "deduplicated": state.deduplicated,
                "throttled": state.throttled,
                "retries": state.retries,
                "errors": state.errors,
                "last_latency_ms": state.last_latency_ms,
                "last_error": state.last_error,
                "cooldown_seconds": max(0.0, state.cooldown_until - now),
                "active_slots": policy.max_concurrency - getattr(state.semaphore, "_value", policy.max_concurrency),
            }
    return out
