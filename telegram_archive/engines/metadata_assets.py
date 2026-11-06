"""Unified Metadata & Assets Manager.

Provides one job-level orchestration seam for:
  * API metadata matching
  * metadata/suggestion scanning
  * performer/studio API artwork
  * local playback snapshot generation

This module is the single owner of the Metadata & Assets job payload shape.
Legacy job types (METADATA_SYNC, SUGGESTION_SCAN, ENTITY_IMAGE_SYNC,
THUMBNAIL_INDEXER) are converted to the unified shape by
``unified_detail()``; the JobManager does not interpret the payload itself.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from . import metadata_sync, suggestion_scan, entity_data
from . import preview_prefetch
from ..jobs.context import JobContext

DEFAULT_BATCH_LIMIT = 50
MAX_BATCH_LIMIT = 5000

OP_NAMES = {
    "metadata": "Metadata match",
    "suggestions": "Suggestions",
    "entity_data": "Performer / Channel data",
    "thumbnails": "Playback Snapshots",
}

# Every accepted spelling of an operation -> its canonical name. The canonical
# name is also the payload section key that holds that operation's settings.
OP_ALIASES = {
    "metadata_sync": "metadata", "metadata": "metadata",
    "suggestion_scan": "suggestions", "suggestions": "suggestions",
    "entity_data": "entity_data", "entity_images": "entity_data",
    "thumbnail_indexer": "thumbnails", "thumbnails": "thumbnails",
}

# Legacy single-operation job types and the canonical operation they run.
LEGACY_JOB_OPERATION = {
    "METADATA_SYNC": "metadata",
    "SUGGESTION_SCAN": "suggestions",
    "ENTITY_IMAGE_SYNC": "entity_data",
    "THUMBNAIL_INDEXER": "thumbnails",
}


def _loads(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def unified_detail(job_type: str, raw_detail: Any) -> dict[str, Any]:
    """Return the canonical unified payload for any Metadata & Assets job.

    Canonical shape: ``{"operations": [<canonical op>...], <op>: {...settings}}``.
    Section keys written under an alias (for example ``entity_data``) are moved
    to the canonical key so the selected scope/entity IDs are never dropped.
    """
    detail = _loads(raw_detail)
    legacy_op = LEGACY_JOB_OPERATION.get(str(job_type or ""))
    if legacy_op and "operations" not in detail:
        # Legacy jobs store that one operation's settings at the top level.
        return {"operations": [legacy_op], legacy_op: detail}
    operations: list[str] = []
    for value in detail.get("operations") or []:
        op = OP_ALIASES.get(str(value or "").strip().casefold())
        if op and op not in operations:
            operations.append(op)
    out: dict[str, Any] = {k: v for k, v in detail.items() if k not in OP_ALIASES}
    out["operations"] = operations
    for op in dict.fromkeys(OP_ALIASES.values()):
        # The canonical section wins; otherwise accept the first alias found.
        candidates = [op] + [alias for alias, target in OP_ALIASES.items() if target == op and alias != op]
        section = next((detail[k] for k in candidates if isinstance(detail.get(k), dict)), None)
        if section is not None:
            out[op] = dict(section)
    return out


def _is_automatic(payload: dict[str, Any], section: dict[str, Any]) -> bool:
    return bool(payload.get("automatic_background") or section.get("automatic_background"))


def _subctx(ctx, job_type: str, detail: dict[str, Any]) -> JobContext:
    job = dict(ctx.job)
    job["job_type"] = job_type
    job["detail"] = json.dumps(detail, ensure_ascii=False)
    sub = JobContext(ctx.job_id, job, ctx.db, ctx.telegram, ctx.governor, ctx._manager)
    sub.pipeline = ctx.pipeline
    sub.scan_window_enforced = bool(getattr(ctx, "scan_window_enforced", False))
    return sub


def bounded_limit(detail: dict[str, Any], *, default: int = DEFAULT_BATCH_LIMIT) -> int:
    """Return a safe operator batch limit. Full-library work is explicit."""
    try:
        value = max(0, min(MAX_BATCH_LIMIT, int(detail.get("limit", default) or 0)))
    except (TypeError, ValueError):
        value = default
    if bool(detail.get("unbounded", False)):
        return 0
    return value or default


def batch_label(detail: dict[str, Any], limit: int) -> str:
    return "entire matching set" if bool(detail.get("unbounded", False)) else f"first {limit}"


async def _run_thumbnails(ctx, detail: dict[str, Any]):
    """Run the playback snapshot stage for the selected source channels."""
    channels = [int(x) for x in (detail.get("channels") or []) if str(x).strip()]
    if not channels:
        raise RuntimeError("Select at least one source channel for playback snapshots")
    message_ids = [int(x) for x in (detail.get("message_ids") or []) if str(x).strip()]
    limit = bounded_limit(detail)
    scope = str(detail.get("scope") or "missing").strip().casefold()
    if scope not in {"missing", "all"}:
        scope = "missing"
    sub = _subctx(ctx, "PREVIEW_PREFETCH", {
        "channels": channels,
        "message_ids": message_ids,
        "limit": limit,
        "unbounded": bool(detail.get("unbounded", False)),
        "scope": scope,
        "operator_channel_scan": bool(detail.get("operator_channel_scan", False)),
        "thumbnail_channels": channels,
        "thumbnail_message_ids": message_ids,
        "thumbnail_limit": limit,
        "thumbnail_scope": scope,
    })
    await preview_prefetch.run(sub)

    # A targeted channel-pipeline snapshot request is only allowed to complete
    # when each exact target video has both the canonical representative snapshot
    # and its complete 36-frame timeline assets. Broad channel-phase scans omit
    # message_ids and rely on the reuse-first channel-wide snapshot engine.
    if message_ids:
        from .thumbnail_cache import playback_snapshot_state_sync
        channel_id = channels[0]
        failures = []
        for message_id in message_ids:
            state = await asyncio.to_thread(
                playback_snapshot_state_sync, ctx.db, channel_id, message_id
            )
            if not bool(state.get("representative")) or not bool(state.get("timeline_ready")):
                failures.append((
                    int(message_id),
                    int(state.get("frames") or 0),
                    bool(state.get("frames_ready")),
                    bool(state.get("representative")),
                ))
        if failures:
            detail_text = "; ".join(
                f"{message_id}: representative={int(representative)}, frames={frames}/{preview_prefetch.FRAME_COUNT}, frames_ready={int(frames_ready)}"
                for message_id, frames, frames_ready, representative in failures
            )
            raise RuntimeError(
                "Playback snapshot stage did not produce complete local assets: "
                + detail_text
            )


async def _run_operation(ctx, op: str, section: dict[str, Any]):
    if op == "metadata":
        await metadata_sync.run(_subctx(ctx, "METADATA_SYNC", section))
    elif op == "suggestions":
        await suggestion_scan.run(_subctx(ctx, "SUGGESTION_SCAN", section))
    elif op == "entity_data":
        await entity_data.run(_subctx(ctx, "ENTITY_IMAGE_SYNC", dict(section, manual=True)))
    elif op == "thumbnails":
        await _run_thumbnails(ctx, section)


async def _run_gated(ctx, op: str, section: dict[str, Any]):
    """Run one automatic operation inside the shared background-priority lane.

    The wait for the lane is cancellable and keeps the job watchdog fed, so an
    automatic job queued behind other maintenance is reported as waiting
    rather than stalled, and Stop is honoured while it waits.
    """
    manager = getattr(ctx, "_manager", None)
    slot = manager.background_priority.operation_slot(op)
    await manager._acquire_cancellable(
        slot.__aenter__(), ctx.job_id,
        operation=f"Waiting for background priority · {OP_NAMES[op]}",
        heartbeat_reason="background_priority_wait",
    )
    try:
        await _run_operation(ctx, op, section)
    except BaseException as exc:
        if not await slot.__aexit__(type(exc), exc, exc.__traceback__):
            raise
    else:
        await slot.__aexit__(None, None, None)


async def run(ctx):
    ctx.scan_window_enforced = True
    payload = unified_detail(str(ctx.job.get("job_type") or ""), ctx.job.get("detail"))
    operations = payload["operations"]
    if not operations:
        raise RuntimeError("Select at least one Metadata & Assets operation")

    total_ops = len(operations)
    manager = getattr(ctx, "_manager", None)
    gate_available = getattr(manager, "background_priority", None) is not None and hasattr(manager, "_acquire_cancellable")
    for index, op in enumerate(operations, start=1):
        await ctx.checkpoint()
        section = dict(payload.get(op) or {})
        ctx.report(
            progress=100.0 * (index - 1) / total_ops,
            current_item="",
            operation=f"{OP_NAMES[op]} · {batch_label(section, bounded_limit(section))} ({index}/{total_ops})",
            force=True,
        )
        # The shared background-priority lane serialises *automatic* work only
        # (see core/background_priority.py). Operator-started work must not
        # queue behind automatic maintenance such as the Playback snapshots maintenance job.
        if gate_available and _is_automatic(payload, section):
            await _run_gated(ctx, op, section)
        else:
            await _run_operation(ctx, op, section)
    ctx.report(completed=total_ops, total=total_ops, progress=100.0, operation="Finished", force=True)
    ctx.db.log(
        "Metadata & Assets Manager completed: " + ", ".join(OP_NAMES[x] for x in operations),
        "SUCCESS",
    )
