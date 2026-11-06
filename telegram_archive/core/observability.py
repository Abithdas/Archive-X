"""Lightweight structured observability for Archive X.

Inspired by OpenTelemetry log correlation, Sentry breadcrumbs, and APM-style
operation/dependency correlation, but implemented without an external runtime
dependency. The module keeps a bounded in-memory diagnostic ledger and exposes
helpers used by the application's JSONL diagnostics stream.
"""
from __future__ import annotations

from collections import Counter, deque
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import inspect
import os
import threading
import time
from datetime import datetime, timezone
import uuid
from typing import Any, Iterator, Mapping

from .. import config

_request_id: ContextVar[str] = ContextVar("archive_x_request_id", default="-")
_trace_id: ContextVar[str] = ContextVar("archive_x_trace_id", default="-")
_span_id: ContextVar[str] = ContextVar("archive_x_span_id", default="-")
_operation_id: ContextVar[str] = ContextVar("archive_x_operation_id", default="-")
_job_id: ContextVar[str] = ContextVar("archive_x_job_id", default="-")
_component: ContextVar[str] = ContextVar("archive_x_component", default="-")
_launch_id: ContextVar[str] = ContextVar("archive_x_launch_id", default="-")

_MAX_RECENT = int(getattr(config, "DIAGNOSTICS_RECENT_EVENT_LIMIT", 600))
_MAX_ERRORS = 100
_LOCK = threading.RLock()
_RECENT: deque[dict[str, Any]] = deque(maxlen=_MAX_RECENT)
_ERROR_RECENT: deque[dict[str, Any]] = deque(maxlen=_MAX_ERRORS)
_LEVELS: Counter[str] = Counter()
_EVENTS: Counter[str] = Counter()
_COMPONENTS: Counter[str] = Counter()
_ERROR_FINGERPRINTS: Counter[str] = Counter()
_DUPLICATES: Counter[str] = Counter()
_LAST_EVENT: dict[str, tuple[float, int]] = {}
_HTTP_TOTAL = 0
_HTTP_ERRORS = 0
_HTTP_BY_METHOD: Counter[str] = Counter()
_HTTP_BY_STATUS: Counter[str] = Counter()
_HTTP_BY_PATH: Counter[str] = Counter()
_HTTP_LATENCY_MS: deque[float] = deque(maxlen=1000)
_HTTP_SAMPLE_CAPACITY = 1000
_STARTED = time.monotonic()
_HTTP_STARTED_AT = datetime.now(timezone.utc)


def _new_id(nbytes: int = 16) -> str:
    return uuid.uuid4().hex[: nbytes * 2]


def parse_traceparent(value: str | None) -> tuple[str, str] | None:
    raw = str(value or "").strip()
    parts = raw.split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    trace_id, span_id = parts[1], parts[2]
    if len(trace_id) != 32 or len(span_id) != 16:
        return None
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    try:
        int(trace_id, 16); int(span_id, 16)
    except ValueError:
        return None
    return trace_id, span_id


def set_context(*, request_id: str | None = None, trace_id: str | None = None,
                span_id: str | None = None, operation_id: str | None = None,
                job_id: str | None = None, component: str | None = None,
                launch_id: str | None = None) -> dict[str, Any]:
    values = {
        "request_id": str(request_id or _request_id.get() or "-"),
        "trace_id": str(trace_id or _trace_id.get() or "-"),
        "span_id": str(span_id or _span_id.get() or "-"),
        "operation_id": str(operation_id or _operation_id.get() or "-"),
        "job_id": str(job_id or _job_id.get() or "-"),
        "component": str(component or _component.get() or "-"),
        "launch_id": str(launch_id or _launch_id.get() or os.getenv("ARCHIVE_X_LAUNCH_ID") or "-"),
    }
    _request_id.set(values["request_id"]); _trace_id.set(values["trace_id"])
    _span_id.set(values["span_id"]); _operation_id.set(values["operation_id"])
    _job_id.set(values["job_id"]); _component.set(values["component"]); _launch_id.set(values["launch_id"])
    return values


def context_snapshot() -> dict[str, str]:
    return {
        "request_id": _request_id.get(),
        "trace_id": _trace_id.get(),
        "span_id": _span_id.get(),
        "operation_id": _operation_id.get(),
        "job_id": _job_id.get(),
        "component": _component.get(),
        "launch_id": _launch_id.get() if _launch_id.get() != "-" else (os.getenv("ARCHIVE_X_LAUNCH_ID") or "-"),
    }


@contextmanager
def operation_context(*, operation_id: str | None = None, job_id: str | None = None,
                     component: str | None = None) -> Iterator[str]:
    token_o = _operation_id.set(operation_id or _new_id(12))
    token_j = _job_id.set(str(job_id or _job_id.get() or "-"))
    token_c = _component.set(str(component or _component.get() or "-"))
    try:
        yield _operation_id.get()
    finally:
        _operation_id.reset(token_o); _job_id.reset(token_j); _component.reset(token_c)


@contextmanager
def span(name: str, *, operation_id: str | None = None,
         component: str | None = None) -> Iterator[str]:
    parent = _span_id.get()
    token = _span_id.set(_new_id(8))
    span_value = _span_id.get()
    started = time.perf_counter()
    try:
        yield span_value
    finally:
        elapsed = (time.perf_counter() - started) * 1000.0
        _span_id.reset(token)
        # Avoid importing diagnostics at module load to prevent circular import.
        try:
            from .diagnostics import record_event
            record_event(
                event="operation.span.completed",
                level="DEBUG",
                message=str(name),
                operation=operation_id or _operation_id.get(),
                details={"duration_ms": round(elapsed, 3), "parent_span_id": parent, "span_name": str(name),
                         "component": component or _component.get()},
            )
        except Exception:
            pass



def _normalize_http_path(path: str) -> str:
    raw = str(path or "/").split("?", 1)[0].split("#", 1)[0]
    parts = [p for p in raw.split("/") if p]
    normalized = []
    for part in parts:
        if part.isdigit() or (len(part) >= 16 and all(c in "0123456789abcdefABCDEF-" for c in part)):
            normalized.append(":id")
        else:
            normalized.append(part[:80])
    return "/" + "/".join(normalized) if normalized else "/"


def record_http_request(*, method: str, path: str, status: int, duration_ms: float) -> None:
    """Record bounded HTTP telemetry without storing query strings or unbounded route cardinality."""
    global _HTTP_TOTAL, _HTTP_ERRORS
    with _LOCK:
        _HTTP_TOTAL += 1
        if int(status) >= 500:
            _HTTP_ERRORS += 1
        _HTTP_BY_METHOD[str(method or "GET").upper()] += 1
        _HTTP_BY_STATUS[str(int(status))] += 1
        _HTTP_BY_PATH[_normalize_http_path(path)] += 1
        _HTTP_LATENCY_MS.append(max(0.0, float(duration_ms)))


def http_metrics_snapshot() -> dict[str, Any]:
    with _LOCK:
        values = sorted(_HTTP_LATENCY_MS)
        def pct(p: float) -> float:
            if not values:
                return 0.0
            idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * p))))
            return round(values[idx], 2)
        return {
            "schema": 2,
            "requests_total": _HTTP_TOTAL,
            "server_errors": _HTTP_ERRORS,
            "error_rate_percent": round((_HTTP_ERRORS / _HTTP_TOTAL) * 100.0, 3) if _HTTP_TOTAL else 0.0,
            "latency_ms": {"p50": pct(0.50), "p95": pct(0.95), "max": round(values[-1], 2) if values else 0.0},
            "by_method": dict(_HTTP_BY_METHOD),
            "by_status": dict(_HTTP_BY_STATUS),
            "top_paths": dict(_HTTP_BY_PATH.most_common(20)),
            "sample_size": len(values),
            "sample_capacity": _HTTP_SAMPLE_CAPACITY,
            "sample_fill_percent": round((len(values) / _HTTP_SAMPLE_CAPACITY) * 100.0, 1),
            "window_started_at": _HTTP_STARTED_AT.isoformat(),
            "uptime_seconds": round(time.monotonic() - _STARTED, 2),
        }


def reset_http_metrics() -> dict[str, Any]:
    """Reset only the bounded HTTP telemetry window/counters for a fresh operator baseline."""
    global _HTTP_TOTAL, _HTTP_ERRORS, _HTTP_STARTED_AT
    with _LOCK:
        _HTTP_TOTAL = 0
        _HTTP_ERRORS = 0
        _HTTP_BY_METHOD.clear()
        _HTTP_BY_STATUS.clear()
        _HTTP_BY_PATH.clear()
        _HTTP_LATENCY_MS.clear()
        _HTTP_STARTED_AT = datetime.now(timezone.utc)
    return http_metrics_snapshot()


def error_fingerprint(*, event: str, error_type: str, message: str = "",
                      traceback_text: str = "") -> str:
    lines = [x.strip() for x in str(traceback_text or "").splitlines() if x.strip()]
    tail = "\n".join(lines[-8:])
    seed = f"{event}|{error_type}|{tail}|{str(message)[:240]}".encode("utf-8", "replace")
    return hashlib.sha256(seed).hexdigest()[:16]


def _caller() -> dict[str, Any]:
    try:
        for frame in inspect.stack()[2:9]:
            name = str(frame.filename)
            if "observability.py" not in name and "diagnostics.py" not in name:
                return {"file": os.path.basename(name), "line": int(frame.lineno), "function": str(frame.function)}
    except Exception:
        pass
    return {}


def _classify(level: str, event: str, message: str, error: Any = None) -> tuple[str, str]:
    """Return stable outcome + severity semantics without changing legacy levels."""
    lv = str(level or "INFO").upper()
    name = str(event or "application").lower()
    msg = str(message or "").lower()
    if lv in {"CRITICAL", "FATAL"}:
        return "FATAL", "CRITICAL"
    if lv == "ERROR":
        return "FAILURE", "ERROR"
    if lv == "WARN":
        if any(x in name or x in msg for x in ("timeout", "retry", "reconnect", "floodwait", "waiting", "degraded")):
            return "TRANSIENT", "WARN"
        return "DEGRADED", "WARN"
    if lv == "SUCCESS":
        return "SUCCESS", "INFO"
    if any(x in name or x in msg for x in ("cancelled", "canceled", "no_match", "not_authorized", "login_required")):
        return "EXPECTED", "INFO"
    return "SUCCESS" if name.endswith((".completed", ".success", ".ready")) else "INFO", lv


def _event_key(event: Mapping[str, Any]) -> str:
    err = event.get("error") or {}
    fp = str(err.get("fingerprint") or "")
    if fp:
        return f"error:{fp}"
    return "event:" + "|".join([
        str(event.get("event") or "application"),
        str(event.get("level") or "INFO"),
        str(event.get("message") or "")[:240],
        str(event.get("job_id") or (event.get("context") or {}).get("job_id") or "-"),
    ])


def ingest(item: Mapping[str, Any]) -> dict[str, Any]:
    """Add a structured event to the bounded ledger with classification and repetition control."""
    event = dict(item)
    event.setdefault("context", context_snapshot())
    event.setdefault("caller", _caller())
    event.setdefault("mono_age_ms", round((time.monotonic() - _STARTED) * 1000.0, 1))
    level = str(event.get("level") or "INFO").upper()
    name = str(event.get("event") or "application")
    outcome, severity = _classify(level, name, str(event.get("message") or ""), event.get("error"))
    event["schema_version"] = 2
    event["severity"] = severity
    event["outcome"] = outcome
    event["event_key"] = _event_key(event)
    component = str((event.get("details") or {}).get("component") or context_snapshot().get("component") or "-")
    now = time.monotonic()
    key = event["event_key"]
    with _LOCK:
        previous = _LAST_EVENT.get(key)
        duplicate = bool(previous and now - previous[0] <= float(getattr(config, "DIAGNOSTICS_DEDUP_WINDOW_SECONDS", 60.0)))
        if duplicate:
            count = int(previous[1]) + 1
            _LAST_EVENT[key] = (now, count)
            _DUPLICATES[key] += 1
            event["deduplicated"] = True
            event["repeat_count"] = count
            event["_persist"] = (count % 10 == 0)
        else:
            _LAST_EVENT[key] = (now, 1)
            event["deduplicated"] = False
            event["repeat_count"] = 1
            event["_persist"] = True
            _RECENT.append(event)
        _LEVELS[level] += 1
        _EVENTS[name] += 1
        _COMPONENTS[component] += 1
        if event.get("error"):
            err = event.get("error") or {}
            fp = str(err.get("fingerprint") or "")
            if fp:
                _ERROR_FINGERPRINTS[fp] += 1
                _ERROR_RECENT.append({
                    "ts": event.get("ts"), "level": level, "event": name,
                    "fingerprint": fp, "error_type": err.get("type"),
                    "message": err.get("message"), "outcome": outcome,
                    "job_id": (event.get("job_id") or event.get("context", {}).get("job_id")),
                    "request_id": event.get("context", {}).get("request_id"),
                    "trace_id": event.get("context", {}).get("trace_id"),
                    "launch_id": event.get("launch_id") or event.get("context", {}).get("launch_id"),
                })
    return event


def _incident_analysis(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Build bounded incident summaries from existing evidence; no new telemetry store."""
    errors = []
    for e in events:
        if str(e.get("outcome") or "").upper() in {"FAILURE", "FATAL", "DEGRADED", "TRANSIENT"} or str(e.get("level") or "").upper() in {"ERROR", "CRITICAL"}:
            errors.append(e)
    errors.sort(key=lambda x: str(x.get("ts") or ""))
    incidents = []
    seen = set()
    window = float(getattr(config, "DIAGNOSTICS_INCIDENT_WINDOW_SECONDS", 15.0))
    for e in errors:
        key = str((e.get("error") or {}).get("fingerprint") or e.get("event") or "unknown")
        if key in seen:
            continue
        seen.add(key)
        fp_events = [x for x in errors if str((x.get("error") or {}).get("fingerprint") or x.get("event") or "unknown") == key]
        first = fp_events[0]
        incidents.append({
            "fingerprint": key,
            "event": first.get("event"),
            "severity": first.get("severity") or first.get("level"),
            "outcome": first.get("outcome"),
            "first_seen": first.get("ts"),
            "last_seen": fp_events[-1].get("ts"),
            "occurrences": len(fp_events),
            "message": first.get("message"),
            "launch_id": first.get("launch_id") or (first.get("context") or {}).get("launch_id"),
        })
    first_failure = errors[0] if errors else None
    return {
        "first_failure": ({"ts": first_failure.get("ts"), "event": first_failure.get("event"), "message": first_failure.get("message"), "severity": first_failure.get("severity"), "outcome": first_failure.get("outcome")} if first_failure else None),
        "incident_window_seconds": window,
        "incidents": incidents[:20],
        "active_incident_count": len(incidents),
    }


def diagnostic_health() -> dict[str, Any]:
    with _LOCK:
        fatal = sum(1 for e in _RECENT if str(e.get("outcome")) == "FATAL")
        failures = sum(1 for e in _RECENT if str(e.get("outcome")) == "FAILURE")
        degraded = sum(1 for e in _RECENT if str(e.get("outcome")) in {"DEGRADED", "TRANSIENT"})
        if fatal:
            state = "CRITICAL"
        elif failures:
            state = "DEGRADED"
        elif degraded:
            state = "ATTENTION"
        else:
            state = "HEALTHY"
        return {"state": state, "recent_failures": failures, "recent_degraded": degraded, "recent_fatal": fatal}

def snapshot(*, recent_limit: int = 80, errors_limit: int = 40) -> dict[str, Any]:
    with _LOCK:
        return {
            "schema": 1,
            "uptime_seconds": round(time.monotonic() - _STARTED, 2),
            "recent": list(_RECENT)[-max(1, min(int(recent_limit), _MAX_RECENT)):],
            "recent_errors": list(_ERROR_RECENT)[-max(1, min(int(errors_limit), _MAX_ERRORS)):],
            "levels": dict(_LEVELS),
            "events": dict(_EVENTS.most_common(80)),
            "components": dict(_COMPONENTS.most_common(50)),
            "error_fingerprints": dict(_ERROR_FINGERPRINTS.most_common(50)),
            "duplicate_events": sum(_DUPLICATES.values()),
            "diagnostic_health": diagnostic_health(),
            "incident_analysis": _incident_analysis(list(_RECENT)),
            "http": http_metrics_snapshot(),
        }


def recent(limit: int = 100) -> list[dict[str, Any]]:
    with _LOCK:
        return list(_RECENT)[-max(1, min(int(limit), _MAX_RECENT)):]
