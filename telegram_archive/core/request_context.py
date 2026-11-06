"""HTTP request correlation context shared by diagnostics and structured logs."""
from __future__ import annotations

from contextvars import ContextVar
import uuid

from . import observability

_request_id: ContextVar[str] = ContextVar("archive_x_request_id", default="-")


def new_request_id(value: str | None = None) -> str:
    raw = str(value or "").strip()
    if 8 <= len(raw) <= 96 and all(ch.isalnum() or ch in "._:-" for ch in raw):
        rid = raw
    else:
        rid = uuid.uuid4().hex[:16]
    _request_id.set(rid)
    observability.set_context(request_id=rid)
    return rid


def set_trace_context(trace_id: str, span_id: str) -> None:
    observability.set_context(trace_id=trace_id, span_id=span_id)


def request_id() -> str:
    return _request_id.get()


def correlation_snapshot() -> dict[str, str]:
    return observability.context_snapshot()
