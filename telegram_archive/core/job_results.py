"""Verified job outcomes (V29.4.133).

A job used to end "Completed" at 100% whenever its engine returned, whatever
it produced: a snapshot run that built 50 of 4,300 videos, or one whose every
build failed, looked finished. Engines now record what they verified here and
the job manager uses it for the final status, percentage and summary.

Stored under a ``job_state:`` settings key, which the change tracker ignores,
so recording a result never triggers a page refresh by itself.
"""
from __future__ import annotations

from .swallowed import note as _swallowed_note
import json

_RANK = {"COMPLETED": 0, "COMPLETED_WITH_ERRORS": 1}
# The channel pipeline verifies its own phases; engines it runs as sub-steps
# share its job id and must not overwrite that.
_SELF_VERIFYING = {"METADATA_CHANNEL_PIPELINE"}


def _key(job_id) -> str:
    return f"job_state:result:{job_id}"


def record(db, job_id, *, status: str = "COMPLETED", progress: float | None = None, summary: str = "") -> None:
    """Merge one engine's verified outcome into the job's result.

    Several operations can run in one job; the worst status, the lowest
    progress and every summary are kept.
    """
    try:
        job = db.get_job(str(job_id)) or {}
        if str(job.get("job_type") or "") in _SELF_VERIFYING:
            return
        old = read(db, job_id) or {}
        status = status if status in _RANK else "COMPLETED"
        if _RANK.get(old.get("status"), 0) > _RANK[status]:
            status = old["status"]
        progs = [p for p in (old.get("progress"), progress) if isinstance(p, (int, float))]
        summaries = [s for s in (old.get("summary"), summary) if s]
        db.set_setting(_key(job_id), {
            "status": status,
            "progress": round(max(0.0, min(100.0, min(progs))), 1) if progs else None,
            "summary": " · ".join(dict.fromkeys(summaries)),
        })
    except Exception:
        _swallowed_note("job_results.record")
        pass   # bookkeeping must never fail the job itself


def read(db, job_id) -> dict | None:
    try:
        raw = db.setting(_key(job_id))
        if not raw:
            return None
        return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        return None
