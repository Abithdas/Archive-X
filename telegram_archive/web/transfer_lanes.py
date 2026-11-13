"""Allowlisted, latest-per-job telemetry for the read-only live lane cards."""
from datetime import datetime, timezone
import math

TELEMETRY_MAX_AGE_SECONDS = 30


def waiting_reason(status: str, state: str, operation: str, age: float) -> str:
    """One plain sentence for why a running lane is not moving bytes, or '' when it is."""
    if status in {"PAUSED", "PAUSING"}:
        return "Paused"
    if status not in {"RUNNING", "ACTIVE", "IN_PROGRESS"}:
        return ""
    if state == "paused":
        return operation[:200] or "Waiting on Telegram"
    if state == "stale":
        secs = int(age) if math.isfinite(age) else 0
        return f"No progress update for {secs} s" if secs else "No progress update yet"
    return ""


def transfer_lane_metrics(job, telemetry, *, now=None):
    """Shape the one-row telemetry snapshot; never infer file metrics from jobs/bandwidth."""
    now = now or datetime.now(timezone.utc)
    status = str(job.get("status") or "").upper()
    try:
        stamp = datetime.fromisoformat(str((telemetry or {}).get("updated_at") or "").replace("Z", "+00:00"))
        stamp = stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
        age = (now - stamp).total_seconds()
    except (TypeError, ValueError, OverflowError):
        age = float("inf")

    state = str((telemetry or {}).get("transfer_state") or "")
    if status in {"PAUSED", "PAUSING"}:
        state = "paused"
    elif not 0 <= age <= TELEMETRY_MAX_AGE_SECONDS:
        state = "stale" if telemetry else "warming_up"
    elif state not in {"active", "complete", "paused"}:
        state = "warming_up"

    result = {"state": state}
    reason = waiting_reason(status, state, str(job.get("current_operation") or ""), age)
    if reason:
        result["waiting_reason"] = reason
    if not isinstance(telemetry, dict):
        return result
    stage = str(telemetry.get("stage") or "")
    if stage:
        result["stage"] = stage[:200]
    item = str(telemetry.get("current_item") or "")
    if item:
        result["current_item"] = item[:300]
    filename = str(telemetry.get("file_name") or "")
    if filename:
        result["filename"] = filename[:300]

    direction = str(telemetry.get("direction") or "").lower()
    job_item = str(job.get("current_item") or "")
    if direction == "upload" and filename and job_item and filename.casefold() not in job_item.casefold():
        # A completed file's final sample must not appear against the next
        # upload item while it is still being prepared.
        if state not in {"stale", "paused"}:
            result["state"] = "warming_up"
        return result
    if direction in {"upload", "download"}:
        try:
            total = int(telemetry.get("bytes_total") or 0)
            done = int(telemetry.get("bytes_done") or 0)
            rate_raw = telemetry.get("rate_bytes_per_second")
            eta_raw = telemetry.get("eta_seconds")
            rate = float(rate_raw) if rate_raw is not None else None
            eta = float(eta_raw) if eta_raw is not None else None
            if total > 0 and done >= 0 and (rate is None or math.isfinite(rate) and rate >= 0):
                fresh = 0 <= age <= TELEMETRY_MAX_AGE_SECONDS
                live = fresh and state == "active"
                result.update({
                    "bytes_done": min(done, total),
                    "bytes_total": total,
                    "direction": direction,
                    "rate_bytes_per_second": rate if live else None,
                    "eta_seconds": eta if live and eta is not None and math.isfinite(eta) and eta >= 0 else None,
                    "scope": "file",
                })
        except (TypeError, ValueError, OverflowError):
            pass
    return result
