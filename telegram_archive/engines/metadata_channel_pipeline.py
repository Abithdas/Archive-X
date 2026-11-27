"""Channel-wide Metadata & Assets preparation pipeline.

The primary Process Channel workflow is channel-phased rather than video-phased:

1. incrementally inventory new Telegram messages (full reconciliation is explicit) and persist video identities;
2. match the collected names/details through the canonical metadata pipeline;
3. resolve the unique performers and related studio/site/channel entities;
4. collect performer data first, then studio/site/channel data;
5. generate/check playback snapshots for the channel;
6. only then advance to the next selected channel.

Every phase is checkpointed. Re-running an incomplete phase is safe because the
underlying Metadata & Assets engines are reuse-first; valid metadata, entity
data, and complete 36-frame snapshots are retained rather than rebuilt.

The Metadata & Assets Manager is one user-facing workflow. A single job carries
the selected channels and selected stages; legacy single-operation APIs may still
map into the same canonical engines for compatibility, but they are not a second
operator workflow.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Plan, progress and logging
#   - Phase state
#   - Selection and scope
#   - Inventory discovery
#   - Running stage engines
#   - Caption reconciliation
#   - Per-channel pipeline
#   - Readiness verification
#   - Public entry points
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
from ..core import content_mode
import asyncio
import json
import hashlib
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from . import entity_data, metadata_assets
from ..jobs.context import JobCancelled
from ..core.resource_registry import mark_media_entity_complete

PHASES = ("inventory", "metadata", "performers", "channel_data", "snapshots")
ADULT_PHASES = ("metadata", "performers", "channel_data")   # these ask StashDB / ThePornDB
PHASE_LABELS = {
    "inventory": "Collect filenames",
    "metadata": "Match metadata · PornDB / StashDB",
    "performers": "Collect performer data",
    "channel_data": "Collect channel / studio / site data",
    "snapshots": "Playback snapshots",
    "caption_reconciliation": "Uploaded caption reconciliation",
}




# ==============================================================================
# SECTION: Plan, progress and logging
# Stage dependencies, detail persistence and progress.
# ==============================================================================

def dependency_plan(db, channels: list[str], stages: list[str]) -> dict:
    """Return persisted-data prerequisites for the selected stage set.

    Downstream stages may be selected without their prerequisites.  The plan
    distinguishes data that already exists locally from stages that must be
    added to the requested run.  It is shared by the API preflight and the
    channel pipeline so the dependency contract has one canonical definition.
    """
    phase_order = PHASES
    dependencies = {
        "inventory": (),
        "metadata": ("inventory",),
        "performers": ("inventory", "metadata"),
        "channel_data": ("inventory", "metadata"),
        "snapshots": ("inventory",),
    }
    selected = set(stages)
    missing_by_channel = []
    required = set()
    with db.lock:
        for channel_id in channels:
            total = int(db.conn.execute(
                "SELECT COUNT(*) FROM media WHERE source_channel=?", (str(channel_id),)
            ).fetchone()[0] or 0)
            metadata_ready = False
            if total:
                synced = int(db.conn.execute(
                    "SELECT COUNT(*) FROM media WHERE source_channel=? AND metadata_synced_at IS NOT NULL",
                    (str(channel_id),),
                ).fetchone()[0] or 0)
                metadata_ready = synced >= total
            available = {"inventory": total > 0, "metadata": metadata_ready}
            missing = set()
            reasons = []
            for stage in stages:
                for dep in dependencies.get(stage, ()):
                    if dep in selected or available.get(dep, False):
                        continue
                    missing.add(dep)
                    label = "Inventory" if dep == "inventory" else "Metadata Match"
                    if label not in reasons:
                        reasons.append(label)
            if missing:
                required.update(missing)
                missing_by_channel.append({
                    "channel_id": str(channel_id),
                    "missing": [p for p in phase_order if p in missing],
                    "reasons": reasons,
                    "inventory_available": available["inventory"],
                    "metadata_available": available["metadata"],
                    "video_count": total,
                })
    ordered_required = [p for p in phase_order if p in required]
    return {
        "ok": True,
        "ready": not bool(missing_by_channel),
        "required_stages": ordered_required,
        "missing_by_channel": missing_by_channel,
        "selected_stages": list(stages),
    }



def _detail(ctx) -> dict[str, Any]:
    raw = ctx.job.get("detail") or "{}"
    try:
        value = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _save(ctx, payload: dict[str, Any]) -> None:
    ctx.db.update_job(ctx.job_id, detail=json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def _log_pipeline_event(ctx, event: str, level: str = "INFO", *, channel_id: str = "", phase: str = "", current_item: str = "", message: str = "", details: dict[str, Any] | None = None) -> None:
    payload = {"job_type": "METADATA_CHANNEL_PIPELINE", "channel_id": str(channel_id or ""), "phase": str(phase or ""), "current_item": str(current_item or "")[:300]}
    try:
        raw_detail = json.loads(ctx.job.get("detail") or "{}")
        if raw_detail.get("parent_transfer_job_id"):
            payload["parent_transfer_job_id"] = str(raw_detail.get("parent_transfer_job_id"))
        if raw_detail.get("parent_job_id"):
            payload["parent_job_id"] = str(raw_detail.get("parent_job_id"))
        if raw_detail.get("parent_automation_item"):
            payload["parent_automation_item"] = str(raw_detail.get("parent_automation_item"))
    except Exception:
        _swallowed_note("metadata_channel_pipeline._log_pipeline_event")
        pass
    if details:
        payload.update(details)
    ctx.db.log(message or event, level, event=event, job_id=ctx.job_id, operation=PHASE_LABELS.get(phase, phase) if phase else None, details=payload)
    # Database events are normally buffered. Flush this pipeline event before
    # the next checkpoint/engine operation, which may begin an explicit SQLite
    # transaction. This keeps structured logging from becoming a transaction
    # boundary hazard.
    flush = getattr(ctx.db, "flush_event_buffer", None)
    if callable(flush):
        flush()


def _eta_label(seconds: float | int | None) -> str:
    if seconds is None:
        return "Estimating"
    value = max(0, int(math.ceil(float(seconds))))
    if value < 60:
        return f"{value}s"
    if value < 3600:
        return f"{value // 60}m {value % 60:02d}s"
    return f"{value // 3600}h {(value % 3600) // 60:02d}m"


def _set_phase_progress(entry: dict[str, Any], phase: str, *, completed: int = 0, total: int = 0, current_item: str = "", progress: float | None = None) -> None:
    progress_map = entry.setdefault("phase_progress", {})
    previous = progress_map.get(phase) if isinstance(progress_map.get(phase), dict) else {}
    total_i = max(0, int(total or 0))
    completed_i = max(0, min(total_i if total_i else max(0, int(completed or 0)), int(completed or 0)))
    if progress is None:
        progress = (100.0 * completed_i / total_i) if total_i else 0.0
    progress = round(max(0.0, min(100.0, float(progress))), 1)
    now = time.time()
    # Keep a stable start clock while an active phase is being updated, but
    # reset it when a previously completed/failed phase is deliberately rerun.
    started_at = float(previous.get("started_at") or 0.0)
    if not started_at or (not bool(previous.get("active", False)) and progress < 100.0):
        started_at = now
    active = progress < 100.0
    remaining = max(0, total_i - completed_i)
    elapsed = max(0.0, now - started_at)
    rate = completed_i / elapsed if elapsed > 0.5 else 0.0
    eta_seconds = (remaining / rate) if active and remaining > 0 and rate > 0 else (0.0 if not active else None)
    progress_map[phase] = {
        "completed": completed_i,
        "total": total_i,
        "remaining": remaining,
        "progress": progress,
        "current_item": str(current_item or "")[:300],
        "started_at": started_at,
        "elapsed_seconds": round(elapsed, 1),
        "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
        "eta_text": _eta_label(eta_seconds),
        "active": active,
    }




# ==============================================================================
# SECTION: Phase state
# Local snapshots, work snapshots and phase invalidation.
# ==============================================================================

async def _prepare_local_snapshot_assets(
    ctx,
    videos: list[dict[str, Any]],
    entry: dict[str, Any],
    payload: dict[str, Any],
    *,
    selected_stages: list[str],
    total: int,
) -> list[dict[str, Any]]:
    """Prepare snapshots per local file, keeping one bad source from blocking its peers."""
    from .thumbnail_cache import (
        ensure_local_playback_assets,
        local_playback_representative_path,
        local_playback_snapshot_frames_path,
    )
    from ..core.resource_registry import mark_local_media_prepared

    failures = []
    ready_count = 0

    for video in videos:
        await ctx.checkpoint()
        path = Path(video["path"])
        filename = str(video.get("filename") or path.name)
        video.pop("snapshot_error", None)

        try:
            representative = await ensure_local_playback_assets(
                ctx.db, path, video["oshash"],
                metadata=video.get("metadata") or {}, ctx=ctx,
            )
            frames_dir = local_playback_snapshot_frames_path(path)
            frames_ready = (
                frames_dir.is_dir()
                and sum(1 for _ in frames_dir.glob("frame_*.jpg")) >= 36
            )
            representative_path = local_playback_representative_path(path)
            if not representative_path.is_file() and representative and Path(representative).is_file():
                representative_path = Path(representative)
            if not frames_ready:
                raise RuntimeError("Snapshot extraction did not produce 36 frames")
            if not representative_path.is_file():
                raise RuntimeError("Local representative snapshot is missing")

            await asyncio.to_thread(
                mark_local_media_prepared,
                ctx.db,
                int(video["media_id"]),
                representative_path=str(representative_path),
                frames_ready=True,
                metadata_complete=("metadata" in selected_stages),
            )
            video["snapshot_state"] = "READY"
            ready_count += 1
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            failure = {
                "media_id": int(video.get("media_id") or 0),
                "filename": filename,
                "path": str(path),
                "error": reason[:1000],
            }
            video["snapshot_state"] = "FAILED"
            video["snapshot_error"] = failure["error"]
            failures.append(failure)
            _log_pipeline_event(
                ctx,
                "metadata_assets.snapshot_item_failed",
                "WARN",
                channel_id=str(video.get("channel_id") or ""),
                phase="snapshots",
                current_item=filename,
                message=f"Snapshots failed for {filename}; keeping the source and continuing to the next file",
                details={"media_id": failure["media_id"], "error_type": type(exc).__name__,
                         "error": failure["error"]},
            )

        processed = ready_count + len(failures)
        _set_phase_progress(
            entry, "snapshots", completed=processed, total=total, current_item=filename,
        )
        entry["snapshot_failures"] = failures
        entry.setdefault("counts", {})["snapshot_ready"] = ready_count
        entry["counts"]["snapshot_failed"] = len(failures)
        payload["current_file"] = filename
        payload["snapshot_failures"] = failures
        _save(ctx, payload)

    return failures


def _int(value: Any, default: int = 0) -> int:
    """A whole number from whatever a saved job holds (a number, digits as text, nothing, or junk)."""
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            return int(float(value))
        except (TypeError, ValueError, OverflowError):
            return default


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _stage_progress(payload: dict[str, Any]) -> dict[str, Any]:
    """Progress by whole stages, for the one bar a job has in the lists.

    Each stage reports items of its own (caption sync 3/30, then a visual check 1/10), and the number of items is often only known once the stage
    starts, so a bar made of items steps back and rescales at every stage. A stage is the unit that does not change: the total is the number of
    (channel, stage) pairs, a finished stage counts whole, the running one counts by its own items, and the percentage never goes down
    (the highest value reached is kept in the checkpoint)."""
    channels = payload.get("channel_queue") if isinstance(payload.get("channel_queue"), list) else []
    selected = _selected_stages(payload)
    total = 0
    done = 0
    partial = 0.0
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        for phase in selected:
            total += 1
            state = str(_dict(entry.get("phases")).get(phase) or "PENDING").upper()
            if state in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"}:
                done += 1
            elif state == "PROCESSING":
                pp = _dict(_dict(entry.get("phase_progress")).get(phase))
                items = max(0, _int(pp.get("total")))
                if items:
                    partial += max(0.0, min(1.0, _int(pp.get("completed")) / items))
    caption = payload.get("caption_reconciliation") if isinstance(payload.get("caption_reconciliation"), dict) else {}
    caption_total = max(0, _int(caption.get("total")))
    if caption_total:
        total += 1
        if _int(caption.get("completed")) >= caption_total:
            done += 1
        else:
            partial += max(0.0, min(1.0, _int(caption.get("completed")) / caption_total))
    percent = round(100.0 * (done + partial) / total, 1) if total else 0.0
    peak = payload.get("_stage_progress_peak")
    if isinstance(peak, (int, float)) and not isinstance(peak, bool):
        percent = max(percent, float(peak))
    payload["_stage_progress_peak"] = percent
    return {"stages_total": total, "stages_done": done, "stage_progress": percent}


def stage_view(job_type: str, detail: Any) -> dict[str, Any] | None:
    """The one bar a scan job shows in the lists, by stages (see `_stage_progress`): stages done, stages in all, the percentage, and the item counts of the stage
    that is running now. None for any other job, and for a scan that has not counted its stages yet (its own counters are shown then)."""
    if job_type != "METADATA_CHANNEL_PIPELINE" or not isinstance(detail, dict):
        return None
    snap = detail.get("progress")
    if not isinstance(snap, dict) or _int(snap.get("stages_total")) <= 0:
        return None
    return {"completed": _int(snap.get("stages_done")), "total": _int(snap.get("stages_total")), "progress": float(snap.get("stage_progress") or 0.0)}


def _work_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a UI-safe hierarchical progress snapshot from the checkpoint."""
    if not isinstance(payload, dict):
        payload = {}
    channels = payload.get("channel_queue") if isinstance(payload.get("channel_queue"), list) else []
    selected = _selected_stages(payload)
    total_work = completed_work = 0.0
    channel_rows = []
    phase_etas = []
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        phase_rows = []
        channel_total = channel_done = 0.0
        for phase in PHASES:
            if phase not in selected:
                continue
            pp = _dict(_dict(entry.get("phase_progress")).get(phase))
            total = max(0, _int(pp.get("total")))
            done = max(0, min(total if total else max(0, _int(pp.get("completed"))), _int(pp.get("completed"))))
            state = str(_dict(entry.get("phases")).get(phase) or "PENDING").upper()
            if state in {"COMPLETED", "COMPLETED_WITH_ERRORS"} and total:
                done = total
            if not total:
                total = 1
                done = 1 if state in {"COMPLETED", "COMPLETED_WITH_ERRORS"} else 0
            remaining = max(0, total - done)
            eta_seconds = pp.get("eta_seconds")
            if state == "PROCESSING" and eta_seconds is not None:
                try:
                    phase_etas.append(max(0.0, float(eta_seconds)))
                except (TypeError, ValueError):
                    pass
            channel_total += total
            channel_done += done
            total_work += total
            completed_work += done
            phase_rows.append({
                "phase": phase, "state": state, "completed": done, "total": total,
                "remaining": remaining, "progress": round(done * 100.0 / total, 1),
                "current_item": str(pp.get("current_item") or ""),
                "eta_seconds": eta_seconds, "eta_text": str(pp.get("eta_text") or "Estimating"),
            })
        channel_rows.append({
            "channel_id": str(entry.get("channel_id") or ""),
            "state": str(entry.get("state") or "PENDING").upper(),
            "total_videos": _int(_dict(entry.get("counts")).get("videos")) or len(entry.get("videos") if isinstance(entry.get("videos"), list) else []),
            "failed": _int(_dict(entry.get("counts")).get("failed")),
            "published_ready": _int(_dict(entry.get("readiness")).get("published_ready")) or _int(_dict(entry.get("counts")).get("published_ready")),
            "needs_review": _int(_dict(entry.get("readiness")).get("needs_review")) or _int(_dict(entry.get("counts")).get("needs_review")),
            "needs_snapshots": _int(_dict(entry.get("readiness")).get("snapshot_pending")),
            "progress": round(channel_done * 100.0 / channel_total, 1) if channel_total else 0.0,
            "completed_work": int(channel_done), "total_work": int(channel_total),
            "remaining_work": max(0, int(channel_total - channel_done)),
            "phases": phase_rows,
            "current_file": next((r["current_item"] for r in reversed(phase_rows) if r["current_item"]), ""),
            "error": str(entry.get("error") or ""),
        })
    caption_state = payload.get("caption_reconciliation") if isinstance(payload.get("caption_reconciliation"), dict) else {}
    caption_total = max(0, _int(caption_state.get("total")))
    caption_completed = min(caption_total, max(0, _int(caption_state.get("completed"))))
    if caption_total:
        total_work += caption_total
        completed_work += caption_completed

    current_index = _int(payload.get("current_channel_index"))
    current = channel_rows[current_index] if 0 <= current_index < len(channel_rows) else None
    current_phase = str(payload.get("current_phase") or "")
    if current and not current_phase:
        current_phase = next((p["phase"] for p in current["phases"] if p["state"] == "PROCESSING"), "")
    clock = payload.get("progress_started_at")
    try:
        clock = float(clock)
    except (TypeError, ValueError):
        clock = 0.0
    if clock <= 0:
        clock = payload["progress_started_at"] = time.time()
    elapsed = max(0.0, time.time() - clock)
    remaining_work = max(0, int(total_work - completed_work))
    rate = completed_work / elapsed if elapsed > 1.0 else 0.0
    global_eta = remaining_work / rate if remaining_work and rate > 0 else (0.0 if remaining_work == 0 else None)
    # While two independent phases run concurrently, elapsed estimates should
    # not add their remaining seconds together; use the slowest active phase.
    active_eta = max(phase_etas) if phase_etas else None
    if active_eta is not None:
        global_eta = max(global_eta or 0.0, active_eta)
    return {
        "channel_count": len(channel_rows),
        "completed_channels": sum(1 for row in channel_rows if row["state"] in {"COMPLETED", "INCOMPLETE"}),
        "incomplete_channels": sum(1 for row in channel_rows if row["state"] == "INCOMPLETE"),
        "current_channel_index": current_index,
        "current_channel_id": current.get("channel_id") if current else "",
        "current_phase": current_phase,
        "current_file": (str(payload.get("current_file") or "") if current_phase == "caption_reconciliation" else (current.get("current_file", "") if current else "")),
        "caption_reconciliation": dict(caption_state) if caption_state else {},
        **_stage_progress(payload),
        "overall_progress": round(completed_work * 100.0 / total_work, 1) if total_work else 0.0,
        "completed_work": int(completed_work), "total_work": int(total_work),
        "remaining_work": remaining_work,
        "elapsed_seconds": round(elapsed, 1),
        "estimated_seconds_remaining": round(global_eta, 1) if global_eta is not None else None,
        "estimated_time_left": _eta_label(global_eta),
        "published_ready": sum(int(row.get("published_ready") or 0) for row in channel_rows),
        "needs_review": sum(int(row.get("needs_review") or 0) for row in channel_rows),
        "needs_snapshots": sum(int(row.get("needs_snapshots") or 0) for row in channel_rows),
        "channels": channel_rows,
    }


def _phase_state(entry: dict[str, Any], phase: str) -> str:
    phases = entry.setdefault("phases", {})
    value = str(phases.get(phase) or "PENDING").upper()
    return value if value in {"PENDING", "PROCESSING", "COMPLETED", "FAILED"} else "PENDING"


def _invalidate_completed_entity_phases(entry: dict[str, Any], phases: tuple[str, ...] = ("performers", "channel_data")) -> list[str]:
    """Invalidate downstream entity work after a video's cast/studio links change.

    This is intentionally independent of the current job's selected stages: a
    completed-but-unselected phase must be marked stale so a later job does not
    incorrectly skip fetching details for newly linked entities.
    """
    invalidated: list[str] = []
    phase_states = entry.setdefault("phases", {})
    progress_map = entry.setdefault("phase_progress", {})
    for phase in phases:
        if _phase_state(entry, phase) != "COMPLETED":
            continue
        phase_states[phase] = "PENDING"
        previous = progress_map.get(phase) if isinstance(progress_map.get(phase), dict) else {}
        total = max(0, int(previous.get("total") or 0))
        _set_phase_progress(entry, phase, completed=0, total=total, current_item="", progress=0.0)
        invalidated.append(phase)
    return invalidated


def _relationship_signature(db, media_ids: list[int]) -> tuple[tuple[int, str, int], ...]:
    """Capture link identities to detect provider-driven relationship changes."""
    ids = sorted({int(value) for value in media_ids if int(value or 0) > 0})
    if not ids or not hasattr(db, "lock") or not hasattr(db, "conn"):
        # Lightweight context/test DB doubles may not expose SQL internals.
        # Treat those as unavailable signature sources; production Database
        # instances always provide both attributes.
        return ()
    rows: list[tuple[int, str, int]] = []
    with db.lock:
        for start in range(0, len(ids), 400):
            batch = ids[start:start + 400]
            marks = ",".join("?" for _ in batch)
            queries = (
                (f"SELECT video_id, 'performer' AS kind, performer_id AS entity_id FROM video_performers WHERE video_id IN ({marks})", batch),
                (f"SELECT video_id, 'local_performer' AS kind, performer_id AS entity_id FROM local_video_performers WHERE video_id IN ({marks})", batch),
                (f"SELECT video_id, 'studio' AS kind, studio_id AS entity_id FROM video_studios WHERE video_id IN ({marks})", batch),
            )
            for query, args in queries:
                try:
                    selected_rows = db.conn.execute(query, tuple(args)).fetchall()
                except sqlite3.OperationalError as exc:
                    # Older/test databases may not have every optional local
                    # relationship table; do not hide lock, I/O or other SQL
                    # errors that could make invalidation decisions unreliable.
                    if "no such table" in str(exc).casefold():
                        continue
                    raise
                rows.extend((int(row[0]), str(row[1]), int(row[2])) for row in selected_rows)
    return tuple(sorted(set(rows)))




# ==============================================================================
# SECTION: Selection and scope
# Selected stages, channels and limits.
# ==============================================================================

def _selected_stages(payload: dict[str, Any]) -> list[str]:
    aliases = {
        "inventory": "inventory", "metadata": "metadata", "metadata_match": "metadata",
        "performers": "performers", "performer_data": "performers",
        "channel_data": "channel_data", "related_entities": "channel_data",
        "snapshots": "snapshots", "playback_snapshots": "snapshots",
    }
    raw = payload.get("stages")
    if not isinstance(raw, list):
        return list(PHASES)
    out = []
    for value in raw:
        canonical = aliases.get(str(value or "").strip().casefold())
        if canonical and canonical not in out:
            out.append(canonical)
    return out


def _auto_added_channels(payload: dict[str, Any], phase: str) -> set[str]:
    raw = payload.get("auto_added_prerequisites")
    if not isinstance(raw, dict):
        return set()
    values = raw.get(phase)
    if not isinstance(values, list):
        return set()
    return {str(v).strip() for v in values if str(v).strip()}


def _selected_limit(payload: dict[str, Any]) -> int:
    try:
        limit = max(0, min(5000, int(payload.get("limit", 50))))
    except (TypeError, ValueError):
        limit = 50
    if bool(payload.get("unbounded")):
        return 0
    return limit or 50


def _saved_channel_name(ctx, channel_id: str) -> str:
    """Return the saved channel title for user-facing job progress."""
    try:
        with ctx.db.lock:
            row = ctx.db.conn.execute(
                "SELECT title FROM channels WHERE telegram_id=? LIMIT 1",
                (str(channel_id),),
            ).fetchone()
        title = str(row["title"] or "").strip() if row else ""
        return title or "Unknown channel"
    except Exception:
        return "Unknown channel"




# ==============================================================================
# SECTION: Inventory discovery
# Channel inventory scanning.
# ==============================================================================

def _report_inventory_scan_progress(ctx, channel_name: str, scan_stats: dict[str, Any], update: dict[str, Any]) -> None:
    """Surface history-walk progress without treating message counts as job totals."""
    if isinstance(update, dict):
        scan_stats.update(update)
    messages = max(0, int(scan_stats.get("messages") or 0))
    videos = max(0, int(scan_stats.get("videos") or 0))
    phase = str(scan_stats.get("phase") or "history")
    phase_label = {
        "native": "video messages",
        "document": "document messages",
        "complete": "finalizing",
        "limit": "limit reached",
        "partial": "saving partial results",
    }.get(phase, phase)
    ctx.report(
        operation=f"Channel inventory · {phase_label}",
        current_item=f"{channel_name} · {messages:,} messages scanned · {videos:,} videos found",
    )


async def _discover_and_store_inventory(ctx, channel_id: str, limit: int, *, full_scan: bool = False) -> list[dict[str, Any]]:
    """Refresh Telegram inventory incrementally and persist the filename catalogue.

    Normal scans read messages newer than a durable high-water mark. A full scan
    is reserved for first discovery or explicit reconciliation. Previously seen
    messages above an incomplete high-water mark are seeded into the index so a
    bounded scan can continue past them without repeatedly processing the same
    newest messages.
    """
    client = getattr(getattr(ctx, "telegram", None), "client", None)
    if client is None:
        raise RuntimeError("Telegram is not connected; cannot collect the channel filename inventory")
    channel_name = _saved_channel_name(ctx, channel_id)

    # A previously verified move can leave a source-deletion retry if Telegram
    # temporarily refused the delete. Retry a small bounded batch during normal
    # inventory work; identity is rechecked before any message is deleted.
    try:
        from ..core.telegram_registry import retry_pending_move_deletions
        cleanup = await retry_pending_move_deletions(ctx.db, client, limit=10)
        if cleanup.get("deleted") or cleanup.get("deferred") or cleanup.get("needs_review"):
            ctx.db.log(f"Verified-move source cleanup retry: {cleanup}", "INFO" if not cleanup.get("deferred") else "WARN")
    except Exception as cleanup_exc:
        ctx.db.log(f"Verified-move source cleanup retry deferred: {type(cleanup_exc).__name__}: {cleanup_exc}", "WARN")

    from ..playback.stream_server import build_channel_index

    channel_entity = await ctx.cancellable(
        lambda: client.get_entity(int(channel_id)), timeout=30.0
    )
    with ctx.db.lock:
        existing_max = int(ctx.db.conn.execute(
            "SELECT COALESCE(MAX(CAST(source_message_id AS INTEGER)),0) FROM media WHERE source_channel=?",
            (channel_id,),
        ).fetchone()[0] or 0)
        existing_rows = ctx.db.conn.execute(
            "SELECT CAST(source_message_id AS INTEGER) FROM media WHERE source_channel=?",
            (channel_id,),
        ).fetchall()
    existing_media_ids = {int(row[0] or 0) for row in existing_rows if int(row[0] or 0) > 0}
    flood_before = 0
    try:
        flood_before = int(ctx.governor.channel_download_floodwaits(int(channel_id)))
    except Exception:
        _swallowed_note("metadata_channel_pipeline._discover_and_store_inventory")
        pass
    highwater_key = f"inventory_highwater_v1_{int(channel_id)}"
    saved_highwater = await asyncio.to_thread(ctx.db.setting, highwater_key, None)
    try:
        highwater = max(0, int(saved_highwater)) if saved_highwater is not None else existing_max
    except (TypeError, ValueError):
        highwater = existing_max
    base_min_id = 0 if full_scan else highwater
    if saved_highwater is None and not full_scan:
        # Establish the pre-scan boundary before any new rows are inserted. If a
        # bounded scan stops early, the next run keeps discovering the remainder.
        await asyncio.to_thread(ctx.db.set_setting, highwater_key, highwater)

    initial_records: dict[str, dict[str, Any]] = {}
    if not full_scan and base_min_id > 0:
        with ctx.db.lock:
            known = ctx.db.conn.execute(
                "SELECT source_message_id,filename,title,size,duration FROM media "
                "WHERE source_channel=? AND CAST(source_message_id AS INTEGER)>?",
                (channel_id, base_min_id),
            ).fetchall()
        for row in known:
            mid = int(row["source_message_id"] or 0)
            if mid > 0:
                initial_records[str(mid)] = {
                    "id": mid,
                    "name": str(row["filename"] or row["title"] or f"Telegram video {mid}"),
                    "size": int(row["size"] or 0),
                    "duration": float(row["duration"] or 0),
                }

    # A split-upload Library card is keyed by the `.001` message but represents
    # all registered volumes. Keep that logical row in the scan result, and
    # require every physical message to be seen before a full scan can preserve
    # it. The physical `.001` document is not itself a playable video record.
    with ctx.db.lock:
        split_tables = {str(row[0]) for row in ctx.db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('split_file_groups','split_file_parts')"
        ).fetchall()}
        split_groups = ctx.db.conn.execute(
            "SELECT group_id,primary_message_id FROM split_file_groups "
            "WHERE destination_channel=? AND status='VERIFIED' AND primary_message_id IS NOT NULL",
            (str(channel_id),),
        ).fetchall() if split_tables == {"split_file_groups", "split_file_parts"} else []
    split_updated_at = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    if split_groups:
        from ..core.split_files import load_split_group, SplitFileError
        for saved_group in split_groups:
            primary_id = int(saved_group["primary_message_id"] or 0)
            if primary_id <= 0:
                continue
            try:
                split_group = await asyncio.to_thread(load_split_group, ctx.db, str(channel_id), primary_id)
                if not split_group or str(split_group.get("status") or "").upper() != "VERIFIED":
                    raise SplitFileError("split group is no longer verified")
            except SplitFileError as exc:
                with ctx.db.lock:
                    ctx.db.conn.execute(
                        "UPDATE split_file_groups SET status='INCOMPLETE',updated_at=? WHERE group_id=? AND status='VERIFIED'",
                        (split_updated_at, str(saved_group["group_id"])),
                    )
                    ctx.db.conn.commit()
                ctx.db.log(f"Split-file group {saved_group['group_id']} is no longer complete during inventory: {exc}", "WARN")
                continue
            initial_records[str(primary_id)] = {
                "id": primary_id,
                "name": str(split_group.get("logical_filename") or f"Telegram video {primary_id}"),
                "size": int(split_group.get("total_size") or 0),
                "duration": float(split_group.get("duration") or 0),
                "width": int(split_group.get("width") or 0),
                "height": int(split_group.get("height") or 0),
                "mime": str(split_group.get("mime_type") or "application/octet-stream"),
                "caption": str(split_group.get("caption") or ""),
                "is_split_group": True,
                "thumbnail_message_id": int(split_group.get("thumbnail_message_id") or 0) or None,
                "_split_group_group_id": str(split_group.get("group_id") or ""),
                "_split_group_part_ids": [int(part["message_id"]) for part in split_group.get("parts", [])],
                "_split_group_seen_part_ids": [],
            }

    scan_stats: dict[str, Any] = {}
    mode_key = "full" if full_scan else f"after_{base_min_id}"
    checkpoint_key = f"inventory_scan_checkpoint_v2_{int(channel_id)}_{mode_key}"
    checkpoint = await asyncio.to_thread(ctx.db.setting, checkpoint_key, {})
    if not isinstance(checkpoint, dict) or checkpoint.get("version") != 2 or int(checkpoint.get("base_min_id") or 0) != base_min_id:
        checkpoint = None

    async def save_checkpoint(value):
        await asyncio.to_thread(ctx.db.set_setting, checkpoint_key, value)
        # History walks checkpoint every bounded batch; use that same boundary
        # to pause cleanly if the optional daily scan window has ended.
        await _scan_window_wait(ctx)

    def report_scan_progress(value):
        _report_inventory_scan_progress(ctx, channel_name, scan_stats, value)

    records = await ctx.cancellable(
        lambda: build_channel_index(
            client,
            channel_entity,
            min_id=base_min_id,
            initial_records=initial_records,
            # An explicit full reconciliation must enumerate *all* history,
            # regardless of the downstream processing limit. Otherwise a
            # bounded scan could incorrectly treat unseen older messages as
            # deleted. The limit is applied later to pending processing.
            max_videos=(None if full_scan else (limit or None)),
            progress=report_scan_progress,
            checkpoint=checkpoint,
            checkpoint_callback=save_checkpoint,
        )
    )
    enumeration_complete = bool(scan_stats.get("enumeration_complete", scan_stats.get("complete")))
    if enumeration_complete:
        await asyncio.to_thread(ctx.db.set_setting, checkpoint_key, {})
    elif scan_stats.get("limit_hit"):
        # A requested item limit is not a full inventory. Do not advance the
        # high-water mark or mark unvisited history as deleted.
        await asyncio.to_thread(ctx.db.set_setting, checkpoint_key, {})
    if checkpoint:
        ctx.db.log(
            f"Collect filenames {channel_id}: resumed inventory cursor; "
            f"{int(scan_stats.get('messages') or 0):,} messages scanned in this checkpoint chain",
            "INFO",
        )

    ordered = sorted(records.values(), key=lambda item: int(item.get("id") or 0), reverse=True)
    if limit and not full_scan:
        ordered = ordered[:limit + len(initial_records)] if initial_records else ordered[:limit]

    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    # Reserve discovered logical cards before media rows are inserted. The
    # resource view hides PREPARING groups until their physical parts, metadata,
    # and registries have all been stored and the group is finalized VERIFIED.
    # These blocks write thousands of rows (and commit per message): run them off the event loop, which otherwise freezes for minutes.
    def _store_split_groups():
        for record in ordered:
            parts = record.get("_split_group_parts") if record.get("is_split_group") else None
            if not parts:
                continue
            from ..core.split_files import store_split_group
            channel_key = str(channel_id)
            primary_id = int(record.get("id") or 0)
            group_id = str(record.get("_split_group_group_id") or "").strip()
            if not group_id:
                import hashlib
                group_id = "scan-" + hashlib.sha256(f"{channel_key}:{primary_id}:{record.get('name') or ''}".encode("utf-8")).hexdigest()[:24]
            record["_split_group_group_id"] = group_id
            store_split_group(
                ctx.db, group_id=group_id, destination_channel=channel_key,
                logical_filename=str(record.get("name") or ""), primary_message_id=primary_id,
                thumbnail_message_id=int(record.get("thumbnail_message_id") or 0) or None,
                total_size=int(record.get("size") or 0), parts=list(parts),
                metadata={"mime_type": str(record.get("mime") or "application/octet-stream"),
                          "duration": float(record.get("duration") or 0),
                          "width": int(record.get("width") or 0), "height": int(record.get("height") or 0),
                          "fps": float(record.get("fps") or 0), "bitrate": int(record.get("bitrate") or 0),
                          "codec": str(record.get("codec") or ""), "audio_codec": str(record.get("audio_codec") or ""),
                          "caption": str(record.get("caption") or "")},
                identity_key=f"telegram-split:{channel_key}:{primary_id}", status="PREPARING",
            )
    await asyncio.to_thread(_store_split_groups)
    def _store_media_rows():
        # Records are written in short chunks: one lock hold and one commit per ~50 ms of work, not one per record. A commit per record cost
        # a disk flush for every video (slow on a big library or a hard disk), and the chunk limit keeps the database lock free often enough
        # that the event loop and everything else never wait long (a 95 s wait for a whole batch once got the job killed by the watchdog).
        position = 0
        while position < len(ordered):
            deadline = time.monotonic() + 0.05
            with ctx.db.lock:
                while position < len(ordered):
                    record = ordered[position]
                    position += 1
                    message_id = int(record.get("id") or 0)
                    if not message_id:
                        continue
                    filename = str(record.get("name") or f"Telegram video {message_id}")
                    size = int(record.get("size") or 0)
                    duration = float(record.get("duration") or 0)
                    caption_update = "excluded.caption" if full_scan else "CASE WHEN excluded.caption<>'' THEN excluded.caption ELSE media.caption END"
                    ctx.db.conn.execute(
                        f"""INSERT INTO media(
                            source_channel,source_message_id,filename,title,size,duration,
                            width,height,fps,streamable,status,caption,created_at,updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source_channel,source_message_id) DO UPDATE SET
                            filename=excluded.filename,
                            title=CASE WHEN media.title IS NULL OR TRIM(media.title)='' OR media.title=media.filename
                                       THEN excluded.title ELSE media.title END,
                            size=CASE WHEN excluded.size>0 THEN excluded.size ELSE media.size END,
                            duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE media.duration END,
                            width=CASE WHEN excluded.width>0 THEN excluded.width ELSE media.width END,
                            height=CASE WHEN excluded.height>0 THEN excluded.height ELSE media.height END,
                            caption={caption_update},
                            status=CASE WHEN media.status='SOURCE_DELETED' THEN excluded.status ELSE media.status END,
                            streamable=1,updated_at=excluded.updated_at""",
                        (channel_id, message_id, filename, filename, size, duration,
                         int(record.get("width") or 0), int(record.get("height") or 0), 0.0, 1,
                         "SCANNED", str(record.get("caption") or ""), now, now),
                    )
                    from ..core.telegram_registry import register_known_message
                    media_row = ctx.db.conn.execute(
                        "SELECT id,thumbnail_message_id FROM media WHERE source_channel=? AND source_message_id=?",
                        (channel_id, message_id),
                    ).fetchone()
                    if record.get("is_split_group") and record.get("thumbnail_message_id") and media_row:
                        ctx.db.conn.execute(
                            "UPDATE media SET thumbnail_message_id=? WHERE id=?",
                            (int(record["thumbnail_message_id"]), int(media_row["id"])),
                        )
                    if not record.get("is_split_group"):
                        register_known_message(
                            ctx.db, channel_id=channel_id, message_id=message_id, record=record,
                            media_id=int(media_row["id"]) if media_row else None,
                            thumbnail_message_id=int(media_row["thumbnail_message_id"] or 0) if media_row else None,
                        )
                    if time.monotonic() >= deadline:
                        break
                ctx.db.conn.commit()
            time.sleep(0.002)   # Python's lock is not fair: without this pause this thread takes it straight back and a waiting thread can starve
        with ctx.db.lock:
            # Only a complete scan of all Telegram history can prove that older
            # messages disappeared. Incremental passes never tombstone unseen rows.
            present = {int(item.get("id") or 0) for item in ordered}
            if scan_stats.get("full_history") and not (present - {0}):
                known_live = int(ctx.db.conn.execute(
                    "SELECT COUNT(*) FROM media WHERE source_channel=? AND COALESCE(status,'')<>'SOURCE_DELETED'", (channel_id,)).fetchone()[0] or 0)
                if known_live:
                    # Telegram can answer a history walk with nothing (access lost, a search hiccup) and still call it complete.
                    # Treating that as "every video was deleted" would remove the whole channel from the Library.
                    _INVENTORY_WARNINGS[(str(getattr(ctx, "job_id", "")), str(channel_id))] = (
                        f"Telegram returned no videos for the whole history while {known_live:,} are stored; nothing was removed")
                    ctx.db.log(
                        f"Collect filenames {channel_id}: Telegram returned no videos for a full history scan, but {known_live:,} are known. "
                        "Nothing was removed from the Library; check that this account can still read the channel and scan again.",
                        "WARN")
                    scan_stats["full_history"] = False
            if scan_stats.get("full_history"):
                if split_tables == {"split_file_groups", "split_file_parts"}:
                    missing_split_primary_ids = [int(row["primary_message_id"] or 0) for row in split_groups
                                                 if int(row["primary_message_id"] or 0) not in present]
                    if missing_split_primary_ids:
                        marks = ",".join("?" for _ in missing_split_primary_ids)
                        ctx.db.conn.execute(
                            f"UPDATE split_file_groups SET status='INCOMPLETE',updated_at=? "
                            f"WHERE destination_channel=? AND status='VERIFIED' AND primary_message_id IN ({marks})",
                            (now, str(channel_id), *missing_split_primary_ids),
                        )
                stale = [
                    int(r["source_message_id"]) for r in ctx.db.conn.execute(
                        "SELECT source_message_id FROM media WHERE source_channel=? AND COALESCE(status,'')<>'SOURCE_DELETED'",
                        (channel_id,),
                    ).fetchall()
                    if int(r["source_message_id"]) not in present
                ]
                for mid in stale:
                    ctx.db.conn.execute(
                        "UPDATE media SET status='SOURCE_DELETED', updated_at=? WHERE source_channel=? AND source_message_id=?",
                        (now, channel_id, mid),
                    )
                    ctx.db.conn.execute(
                        "UPDATE telegram_document_locations SET location_state='DELETED_OUTSIDE_ARCHIVE_X',last_seen_at=? "
                        "WHERE channel_id=? AND message_id=? AND location_state NOT IN ('MOVED_BY_ARCHIVE_X','MOVED_PENDING_DELETE')",
                        (now, channel_id, mid),
                    )
                ctx.db.conn.commit()
                if stale:
                    ctx.db.log(
                        f"Collect filenames {channel_id}: {len(stale)} video(s) no longer in the Telegram channel were removed from the Library",
                        "INFO",
                    )
            ids = [int(item.get("id") or 0) for item in ordered if int(item.get("id") or 0) > 0]
            if ids:
                from ..core.sql_batches import batches, marks as _marks
                rows = []
                for batch in batches(ids):
                    rows.extend(ctx.db.conn.execute(
                        f"SELECT id,source_channel,source_message_id,filename,title FROM media WHERE source_channel=? AND source_message_id IN ({_marks(len(batch))})",
                        [channel_id, *batch],
                    ).fetchall())
                rows.sort(key=lambda r: int(r["source_message_id"] or 0), reverse=True)
                for row in rows:
                    ctx.db.conn.execute(
                        "INSERT INTO resource_state(media_id,catalogue_state,updated_at) VALUES(?,?,?) "
                        "ON CONFLICT(media_id) DO UPDATE SET catalogue_state='COMPLETE', updated_at=excluded.updated_at",
                        (int(row["id"]), "COMPLETE", now),
                    )
                ctx.db.conn.commit()
            else:
                rows = []
            # Register every Telegram volume (not only .001), then make the logical
            # card visible to downstream snapshot processing. A partial failure
            # leaves the PREPARING group hidden and is retried on the next scan.
            from ..core.split_files import finalize_split_group
            from ..core.telegram_registry import register_known_message
            for record in ordered:
                parts = record.get("_split_group_parts") if record.get("is_split_group") else None
                if not parts:
                    continue
                primary_id = int(record.get("id") or 0)
                with ctx.db.lock:
                    media_row = ctx.db.conn.execute(
                        "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                        (channel_id, primary_id),
                    ).fetchone()
                primary_media_id = int(media_row["id"]) if media_row else None
                for part in parts:
                    part_record = dict(part.get("record") or {})
                    part_record.setdefault("id", int(part["message_id"]))
                    part_record.setdefault("name", str(part.get("telegram_filename") or ""))
                    part_record.setdefault("size", int(part.get("size_bytes") or 0))
                    register_known_message(
                        ctx.db, channel_id=channel_id, message_id=int(part["message_id"]),
                        record=part_record, media_id=primary_media_id,
                        thumbnail_message_id=(int(record["thumbnail_message_id"])
                                               if int(part.get("part_index") or 0) == 1 and record.get("thumbnail_message_id") else None),
                    )
                finalize_split_group(ctx.db, str(record.get("_split_group_group_id") or ""), status="VERIFIED")
            maximum = int(ctx.db.conn.execute(
                "SELECT COALESCE(MAX(CAST(source_message_id AS INTEGER)),0) FROM media WHERE source_channel=? AND COALESCE(status,'')<>'SOURCE_DELETED'",
                (channel_id,),
            ).fetchone()[0] or 0)
            if enumeration_complete:
                ctx.db.set_setting(highwater_key, maximum)
            from ..core.telegram_registry import record_channel_scan
            new_video_ids = {int(item.get("id") or 0) for item in ordered if int(item.get("id") or 0) > 0} - existing_media_ids
            try:
                flood_after = int(ctx.governor.channel_download_floodwaits(int(channel_id)))
            except Exception:
                flood_after = flood_before
            record_channel_scan(ctx.db, channel_id, high_water_mark=maximum,
                                status="COMPLETE" if enumeration_complete else "PARTIAL",
                                detail={"messages": int(scan_stats.get("messages") or 0),
                                        "known_records": len(ordered), "new_videos_found": len(new_video_ids),
                                        "floodwaits": max(0, flood_after - flood_before),
                                        "full_history": bool(scan_stats.get("full_history")),
                                        "limit_hit": bool(scan_stats.get("limit_hit"))})
        return rows
    rows = await asyncio.to_thread(_store_media_rows)

    ctx.db.log(
        f"Channel inventory {'full reconciliation' if full_scan else 'incremental refresh'} {channel_id}: "
        f"{len(ordered)} known/new video record(s), enumeration_complete={enumeration_complete}, "
        f"full_history={bool(scan_stats.get('full_history'))}",
        "INFO",
    )
    videos = []
    for row in rows:
        videos.append({
            "media_id": int(row["id"]),
            "channel_id": int(row["source_channel"]),
            "message_id": int(row["source_message_id"]),
            "filename": str(row["filename"] or row["title"] or f"Video {row['source_message_id']}"),
        })
    return videos

def _local_inventory(ctx, channel_id: str, limit: int, *, pending_only: bool = False) -> list[dict[str, Any]]:
    """Load known catalogue rows without Telegram access, optionally only not-ready videos."""
    if pending_only:
        from ..core.resource_registry import ensure_resource_tables
        ensure_resource_tables(ctx.db)
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
        # A row with a real Telegram channel/message identity is already
        # catalogued even if an older build failed to persist its checkpoint.
        # Repair that checkpoint locally; this does not read Telegram history.
        with ctx.db.lock:
            ctx.db.conn.execute(
                "UPDATE resource_state SET catalogue_state='COMPLETE',updated_at=? "
                "WHERE COALESCE(catalogue_state,'PENDING')<>'COMPLETE' AND media_id IN ("
                "SELECT id FROM media WHERE source_channel=? AND CAST(source_message_id AS INTEGER)>0 "
                "AND COALESCE(status,'')<>'SOURCE_DELETED')",
                (now, channel_id),
            )
            ctx.db.conn.commit()
        sql = ("SELECT m.id,m.source_channel,m.source_message_id,m.filename,m.title "
               "FROM archive_x_catalogued_media m WHERE m.source_channel=? "
               "AND NOT EXISTS (SELECT 1 FROM archive_x_published_media pm WHERE pm.id=m.id) "
               "ORDER BY COALESCE(NULLIF(m.upload_date,''),m.created_at) DESC, CAST(m.source_message_id AS INTEGER) DESC")
    else:
        sql = ("SELECT id,source_channel,source_message_id,filename,title FROM media "
               "WHERE source_channel=? AND COALESCE(status,'')<>'SOURCE_DELETED' "
               "ORDER BY CAST(source_message_id AS INTEGER) DESC")
    params: list[Any] = [channel_id]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    with ctx.db.lock:
        rows = ctx.db.conn.execute(sql, params).fetchall()
    return [{
        "media_id": int(row["id"]),
        "channel_id": int(row["source_channel"]),
        "message_id": int(row["source_message_id"]),
        "filename": str(row["filename"] or row["title"] or f"Video {row['source_message_id']}"),
    } for row in rows]


# (job id, channel id) -> why the Telegram scan did not finish (V29.4.133).
_INVENTORY_WARNINGS: dict[tuple[str, str], str] = {}


async def _inventory(ctx, channel_id: str, limit: int, *, full_scan: bool = False) -> list[dict[str, Any]]:
    """Load a channel's video inventory, waiting out scan windows and FloodWaits."""
    attempts = 0
    last_exc = None
    while True:
        try:
            await _scan_window_wait(ctx)
            return await _discover_and_store_inventory(ctx, channel_id, limit, full_scan=full_scan)
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            last_exc = exc
            import re
            message = str(exc)
            match = re.search(r"FLOOD(?:_PREMIUM)?_WAIT[_ ]?([0-9]+)|FLOOD_WAIT_PREMIUM[_ ]?([0-9]+)|FloodWaitError[^0-9]*([0-9]+)", message, re.I)
            seconds = getattr(exc, "seconds", None) or (next((int(x) for x in match.groups() if x), None) if match else None)
            if seconds is None or attempts >= 5:
                break
            attempts += 1
            delay = min(3600, max(1, int(seconds)))
            try:
                ctx.governor.note_channel_scan_floodwait(int(channel_id))
            except Exception:
                _swallowed_note("metadata_channel_pipeline._inventory")
                pass
            ctx.db.log(f"Telegram flood wait during channel inventory; pausing {delay}s before retry {attempts}/5", "WARN")
            ctx.report(
                operation=f"Channel inventory · Telegram rate-limit wait ({delay}s, retry {attempts}/5)",
                current_item=_saved_channel_name(ctx, channel_id),
                force=True,
            )
            until = time.monotonic() + delay
            while time.monotonic() < until:
                await ctx.checkpoint()
                await _scan_window_wait(ctx)
                await asyncio.sleep(min(5.0, max(0.1, until-time.monotonic())))
    if last_exc is None:
        raise RuntimeError("Channel inventory ended without a result")
    fallback = _local_inventory(ctx, channel_id, limit)
    # V29.4.133: the Telegram scan did not finish; the step is not
    # reported as done just because older rows could be reused.
    _INVENTORY_WARNINGS[(str(getattr(ctx, "job_id", "")), str(channel_id))] = (f"Telegram scan did not finish ({type(last_exc).__name__}: {str(last_exc)[:160]}); using {len(fallback)} videos found earlier")
    if fallback:
        ctx.db.log(
            f"Metadata channel inventory: Telegram refresh failed for {channel_id}; reusing {len(fallback)} local catalogue rows: {type(last_exc).__name__}: {last_exc}",
            "WARN",
        )
        return fallback
    raise last_exc




# ==============================================================================
# SECTION: Running stage engines
# Sub-job context, scan windows and engine execution.
# ==============================================================================

def _subctx(ctx, job_type: str, detail: dict[str, Any]):
    job = dict(ctx.job)
    job["job_type"] = job_type
    job["detail"] = json.dumps(detail, ensure_ascii=False)
    sub = type(ctx)(ctx.job_id, job, ctx.db, ctx.telegram, ctx.governor, ctx._manager)
    sub.pipeline = ctx.pipeline
    sub.scan_window_enforced = bool(getattr(ctx, "scan_window_enforced", False))
    return sub


async def _scan_window_wait(ctx):
    from ..core.scan_schedule import wait_for_metadata_scan_window
    await wait_for_metadata_scan_window(ctx)


async def _run_engine(ctx, operation: str, detail: dict[str, Any], *, report_override=None) -> None:
    """Run one canonical stage while retaining the parent watchdog/Stop path."""
    job_type = {
        "metadata": "METADATA_SYNC",
        "entity_data": "ENTITY_IMAGE_SYNC",
        "thumbnails": "THUMBNAIL_INDEXER",
    }[operation]
    subctx = _subctx(ctx, job_type, detail)
    subctx.scan_window_enforced = bool(getattr(ctx, "scan_window_enforced", False))
    if report_override is not None:
        # Parallel background phases must not overwrite the visible parent-job
        # counters/current operation. Their own progress is persisted in the
        # channel phase checkpoint through this adapter instead.
        subctx.report = report_override
    attempts = 0
    while True:
        await _scan_window_wait(ctx)
        try:
            await ctx.cancellable(lambda: metadata_assets._run_operation(subctx, operation, detail))
            return
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            import re
            message = str(exc)
            match = re.search(r"FLOOD(?:_PREMIUM)?_WAIT[_ ]?([0-9]+)|FLOOD_WAIT_PREMIUM[_ ]?([0-9]+)|FloodWaitError[^0-9]*([0-9]+)", message, re.I)
            seconds = getattr(exc, "seconds", None) or (next((int(x) for x in match.groups() if x), None) if match else None)
            if seconds is None or attempts >= 5:
                raise
            attempts += 1
            delay = min(3600, max(1, int(seconds)))
            ctx.db.log(f"Telegram flood wait during {operation}; pausing {delay}s before retry {attempts}/5", "WARN")
            until = time.monotonic() + delay
            while time.monotonic() < until:
                await ctx.checkpoint()
                await _scan_window_wait(ctx)
                await asyncio.sleep(min(5.0, max(0.1, until - time.monotonic())))


def _start_entity_repair(ctx, phase: str) -> None:
    """After the performer or studio phase: fill in blank genders and fetch the pictures still missing, in the background.

    The phase refreshes only entities whose data is due, so a performer whose refresh was recent but found no picture was never tried again
    by a scan. This pass covers every performer or studio without a picture (retry rules in engines/missing_images.py) and does not hold the
    scan back.
    """
    try:
        from . import missing_images
        kind = "performers" if phase == "performers" else "studios"
        asyncio.get_running_loop().create_task(missing_images.auto_repair(ctx.db, kind, reason="metadata scan", force=True))
    except Exception:
        pass


def _entity_ids(ctx, channel_id: str, media_ids: list[int]) -> tuple[list[int], list[int]]:
    """Return unique performer and studio IDs linked to the channel inventory."""
    with ctx.db.lock:
        if media_ids:
            from ..core.sql_batches import batches, marks as _marks
            performers_found: set[int] = set()
            studios_found: set[int] = set()
            for batch in batches(media_ids):
                params = tuple(batch)
                performers_found.update(int(r[0]) for r in ctx.db.conn.execute(
                    f"SELECT DISTINCT performer_id FROM video_performers WHERE video_id IN ({_marks(len(batch))})", params).fetchall())
                studios_found.update(int(r[0]) for r in ctx.db.conn.execute(
                    f"SELECT DISTINCT studio_id FROM video_studios WHERE video_id IN ({_marks(len(batch))})", params).fetchall())
            return sorted(performers_found), sorted(studios_found)
        performers = [int(r[0]) for r in ctx.db.conn.execute(
            "SELECT DISTINCT vp.performer_id FROM video_performers vp JOIN media m ON m.id=vp.video_id WHERE m.source_channel=? ORDER BY vp.performer_id",
            (channel_id,),
        ).fetchall()]
        studios = [int(r[0]) for r in ctx.db.conn.execute(
            "SELECT DISTINCT vs.studio_id FROM video_studios vs JOIN media m ON m.id=vs.video_id WHERE m.source_channel=? ORDER BY vs.studio_id",
            (channel_id,),
        ).fetchall()]
    return performers, studios


def _phase_detail(phase: str, channel_id: str, videos: list[dict[str, Any]], payload: dict[str, Any], performer_ids: list[int] | None = None, studio_ids: list[int] | None = None) -> tuple[str, dict[str, Any]]:
    if phase == "metadata":
        return "metadata", {
            "limit": len(videos) if not payload.get("unbounded") else 0,
            "unbounded": bool(payload.get("unbounded")),
            "force": bool(payload.get("force_metadata", False)),
            "visual_match": bool(payload.get("visual_match", True)),
            "source_channels": [channel_id],
            "reconcile_filename_entities": False,
        }
    if phase == "performers":
        return "entity_data", {
            "scope": "performers",
            "entity_ids": {"performer": list(performer_ids or [])},
            "limit": 0,
            "unbounded": True,
            "force_recheck": bool(payload.get("force_entity", False)),
            "manual": True,
        }
    if phase == "channel_data":
        return "entity_data", {
            "scope": "studios",
            "entity_ids": {"studio": list(studio_ids or [])},
            "limit": 0,
            "unbounded": True,
            "force_recheck": bool(payload.get("force_entity", False)),
            "manual": True,
        }
    if phase == "snapshots":
        return "thumbnails", {
            "channels": [int(channel_id)],
            "message_ids": [] if payload.get("unbounded") else [int(v["message_id"]) for v in videos],
            "limit": len(videos) if not payload.get("unbounded") else 0,
            "unbounded": bool(payload.get("unbounded")),
            "scope": "missing",
            # Operator-initiated channel scans must inspect real local files,
            # bypass background retry cooldowns, and include rows whose duration
            # has not yet been hydrated from Telegram.
            "operator_channel_scan": True,
        }
    raise ValueError(f"Unsupported channel phase: {phase}")




# ==============================================================================
# SECTION: Caption reconciliation
# Detects and syncs changed captions.
# ==============================================================================

def _caption_signatures(db, channel_id: str, videos: list[dict[str, Any]]) -> dict[int, str]:
    """Hash the exact rendered caption inputs for existing Telegram source videos."""
    try:
        channel_number = int(channel_id)
    except (TypeError, ValueError):
        return {}
    if channel_number == 0:
        return {}
    from ..engines.telegram_sender import _video_fields, render_caption, settings as sender_settings
    template = str(sender_settings(db).get("template") or "")
    result: dict[int, str] = {}
    for video in videos:
        media_id = int(video.get("media_id") or 0)
        message_id = int(video.get("message_id") or 0)
        if media_id <= 0 or message_id <= 0:
            continue
        try:
            fields = _video_fields(db, channel_id, message_id, None)
            caption = render_caption(template, fields, 1024)
            result[media_id] = hashlib.sha256(caption.encode("utf-8")).hexdigest()
        except Exception:
            # Failure to render a comparison must not stop metadata processing.
            _swallowed_note("metadata_channel_pipeline._caption_signatures")
            continue
    return result


async def _sync_changed_captions(ctx, channel_id: str, changed_ids: list[int], videos: list[dict[str, Any]]) -> None:
    """Update only changed Telegram captions and retry durable transient failures."""
    try:
        channel_number = int(channel_id)
    except (TypeError, ValueError):
        return  # synthetic local-media records have no source Telegram caption
    if channel_number == 0:
        return
    from ..engines.caption_sync import sync_catalogue_caption, _ensure_table
    await asyncio.to_thread(_ensure_table, ctx.db)
    ids = set(int(x) for x in changed_ids if int(x) > 0)
    # A prior disconnected/flood-limited caption update stays retryable. Do not
    # retry permission-denied or permanently unavailable messages on every scan.
    with ctx.db.lock:
        pending = ctx.db.conn.execute(
            "SELECT cs.media_id FROM telegram_caption_sync cs JOIN media m ON m.id=cs.media_id "
            "WHERE m.source_channel=? AND cs.status IN ('pending','failed','partial') "
            "AND julianday(cs.last_attempt_at) < julianday('now','-15 minutes')",
            (str(channel_id),),
        ).fetchall()
    ids.update(int(row["media_id"]) for row in pending if int(row["media_id"] or 0) > 0)
    if not ids:
        return
    name_by_id = {int(v.get("media_id") or 0): str(v.get("filename") or v.get("message_id") or "") for v in videos}
    ctx.db.log(f"Caption sync: {len(ids)} changed/retryable caption(s) queued for channel {channel_id}", "INFO")
    updated = failed = 0
    for index, media_id in enumerate(sorted(ids), 1):
        await ctx.checkpoint()
        try:
            outcome = await sync_catalogue_caption(ctx.db, ctx.telegram, media_id)
            if outcome.get("ok"):
                updated += int(outcome.get("synced_count") or 0)
            else:
                failed += 1
            ctx.report(completed=index, total=len(ids), current_item=name_by_id.get(media_id, str(media_id)),
                       operation=f"Caption sync {index}/{len(ids)}", force=(index == 1 or index == len(ids)))
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            failed += 1
            ctx.db.log(f"Caption sync failed for media_id={media_id}: {type(exc).__name__}: {exc}", "WARN")
    ctx.db.log(f"Caption sync completed for channel {channel_id}: {updated} caption(s) updated/verified, {failed} item(s) need retry or review", "INFO" if not failed else "WARN")


def _verified_delivery_caption_media_ids(db) -> list[int]:
    """Return catalogue IDs selected by the former standalone caption action."""
    if not hasattr(db, "lock") or not hasattr(db, "conn"):
        return []
    with db.lock:
        tables = {str(row[0]) for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
            "('transfer_locations','archive_x_deliveries','automation_delivery_fingerprints')"
        ).fetchall()}
        clauses = []
        if "transfer_locations" in tables:
            clauses.append(
                "EXISTS (SELECT 1 FROM transfer_locations t WHERE t.status='VERIFIED' "
                "AND t.destination_channel=m.source_channel AND t.destination_message_id=m.source_message_id)"
            )
        if "archive_x_deliveries" in tables:
            clauses.append(
                "EXISTS (SELECT 1 FROM archive_x_deliveries d WHERE d.status='VERIFIED' "
                "AND d.destination_channel=m.source_channel AND d.destination_video_message_id=m.source_message_id)"
            )
        if "automation_delivery_fingerprints" in tables:
            clauses.append(
                "EXISTS (SELECT 1 FROM automation_delivery_fingerprints f "
                "WHERE f.destination_channel=m.source_channel AND f.destination_video_id=m.source_message_id)"
            )
        if not clauses:
            return []
        rows = db.conn.execute(
            "SELECT m.id FROM media m WHERE m.source_message_id>0 "
            "AND COALESCE(m.status,'')<>'SOURCE_DELETED' AND (" + " OR ".join(clauses) + ") "
            "ORDER BY COALESCE(m.updated_at,m.created_at,''),m.id"
        ).fetchall()
    return list(dict.fromkeys(int(row["id"] or 0) for row in rows if int(row["id"] or 0) > 0))


async def _reconcile_verified_delivery_captions(ctx, payload: dict[str, Any], media_ids: list[int]) -> dict[str, int | str]:
    """Reconcile captions for existing verified deliveries inside the parent job.

    This is the former standalone action moved into the full-history channel
    workflow. It edits linked Telegram message captions only; it never re-uploads
    media, and each catalogue item's result is persisted by caption_sync.
    """
    total = len(media_ids)
    state: dict[str, Any] = payload.setdefault("caption_reconciliation", {})
    state.update({
        "state": "PROCESSING" if total else "NO_WORK",
        "total": total,
        "completed": 0,
        "updated": 0,
        "unchanged": 0,
        "failed": 0,
    })
    payload["current_phase"] = "caption_reconciliation"
    payload["current_file"] = ""
    _save(ctx, payload)
    _log_pipeline_event(
        ctx, "metadata_assets.caption_reconciliation_started",
        message=(f"Uploaded caption reconciliation started for {total} known delivered video(s)"
                 if total else "Uploaded caption reconciliation skipped: no registered verified deliveries need checking"),
        phase="caption_reconciliation",
        details={"total": total, "workflow": "full_inventory"},
    )
    if not total:
        state["state"] = "NO_WORK"
        _save(ctx, payload)
        return {"total": 0, "completed": 0, "updated": 0, "unchanged": 0, "failed": 0}

    from ..engines.caption_sync import sync_catalogue_caption
    for index, media_id in enumerate(media_ids, 1):
        await ctx.checkpoint()
        payload["current_phase"] = "caption_reconciliation"
        payload["current_file"] = f"Catalogue video {media_id}"
        try:
            outcome = await sync_catalogue_caption(ctx.db, ctx.telegram, int(media_id))
            state["updated"] = int(state.get("updated") or 0) + max(0, int(outcome.get("synced_count") or 0))
            state["unchanged"] = int(state.get("unchanged") or 0) + sum(
                1 for item in (outcome.get("targets") or [])
                if isinstance(item, dict) and str(item.get("status") or "").casefold() == "unchanged"
            )
            if not outcome.get("ok"):
                state["failed"] = int(state.get("failed") or 0) + 1
        except (JobCancelled, asyncio.CancelledError):
            state["state"] = "INTERRUPTED"
            state["completed"] = index - 1
            _save(ctx, payload)
            raise
        except Exception as exc:
            state["failed"] = int(state.get("failed") or 0) + 1
            ctx.db.log(
                f"Uploaded caption reconciliation failed for media_id={media_id}: {type(exc).__name__}: {exc}",
                "WARN",
            )
        state["completed"] = index
        force = index == 1 or index % 10 == 0 or index == total
        ctx.report(
            completed=index, total=total, current_item=f"Catalogue video {media_id}",
            operation=f"Uploaded caption reconciliation {index}/{total}", force=force,
        )
        if force:
            _save(ctx, payload)
        if index < total:
            # Preserve the former bulk action's pacing so a global reconciliation
            # cannot flood Telegram with caption edits.
            await asyncio.sleep(0.5)

    state["state"] = "COMPLETED_WITH_ERRORS" if int(state.get("failed") or 0) else "COMPLETED"
    state["completed"] = total
    payload["current_file"] = ""
    _save(ctx, payload)
    _log_pipeline_event(
        ctx,
        "metadata_assets.caption_reconciliation_completed_with_errors" if int(state.get("failed") or 0) else "metadata_assets.caption_reconciliation_completed",
        "WARN" if int(state.get("failed") or 0) else "SUCCESS",
        message=(f"Uploaded caption reconciliation completed: {int(state.get('updated') or 0)} Telegram caption(s) updated, "
                 f"{int(state.get('unchanged') or 0)} unchanged, {int(state.get('failed') or 0)} catalogue item(s) need retry/review"),
        phase="caption_reconciliation",
        details={"total": total, "completed": total, "updated": int(state.get("updated") or 0),
                 "unchanged": int(state.get("unchanged") or 0), "failed": int(state.get("failed") or 0),
                 "workflow": "full_inventory"},
    )
    return {key: state[key] for key in ("total", "completed", "updated", "unchanged", "failed")}




# ==============================================================================
# SECTION: Per-channel pipeline
# Runs all phases for one channel.
# ==============================================================================

async def _run_channel(ctx, payload: dict[str, Any], entry: dict[str, Any], channel_index: int, channel_count: int) -> tuple[int, int]:
    """Run every selected stage for one channel and verify the result."""
    await _scan_window_wait(ctx)
    channel_id = str(entry["channel_id"])
    limit = _selected_limit(payload)
    videos = sorted(
        (v for v in entry.get("videos", []) if isinstance(v, dict) and int(v.get("media_id") or 0) > 0),
        key=lambda v: int(v.get("message_id") or v.get("id") or 0), reverse=True,
    )
    entry["videos"] = videos
    entry.setdefault("phases", {})
    selected = _selected_stages(payload)
    pending_only = bool(payload.get("pending_only"))
    if channel_id in content_mode.sfw_channels(ctx.db):
        # SFW content is named by the movie and show identification (TMDB), never by PornDB / StashDB: leave those phases out.
        selected = [phase for phase in selected if phase not in ADULT_PHASES]
    if pending_only:
        # This workflow is explicitly local-only: a pending item pass never scans Telegram history.
        selected = [phase for phase in selected if phase != "inventory"]

    # Inventory is only refreshed when explicitly selected by the operator; unchecked phases never auto-run.
    # When preflight added Inventory only as a prerequisite, reuse any existing
    # local catalogue rows first and refresh Telegram only when those rows are
    # actually absent.
    inventory_selected = "inventory" in selected
    inventory_auto_added = channel_id in _auto_added_channels(payload, "inventory")
    if not videos and inventory_auto_added:
        videos = _local_inventory(ctx, channel_id, limit, pending_only=not bool(payload.get("force_metadata") or payload.get("force_entity")))
        entry["videos"] = videos
    inventory_needed_for_auto_dependency = not inventory_auto_added or not videos
    if inventory_selected and inventory_needed_for_auto_dependency and _phase_state(entry, "inventory") != "COMPLETED":
        entry["state"] = "PROCESSING"
        entry["phases"]["inventory"] = "PROCESSING"
        _set_phase_progress(entry, "inventory", completed=0, total=1, current_item=channel_id)
        payload["current_phase"] = "inventory"
        payload["current_file"] = ""
        payload["progress"] = _work_snapshot(payload)
        _save(ctx, payload)
        ctx.report(completed=0, total=len(selected), current_item=_saved_channel_name(ctx, channel_id),
                   operation=f"Channel {channel_index + 1}/{channel_count} · {PHASE_LABELS['inventory']}", force=True)
        try:
            entry.pop("inventory_warning", None)
            _INVENTORY_WARNINGS.pop((str(getattr(ctx, "job_id", "")), channel_id), None)
            if bool(payload.get("full_inventory")):
                videos = await _inventory(ctx, channel_id, limit, full_scan=True)
            else:
                videos = await _inventory(ctx, channel_id, limit)
            warning = _INVENTORY_WARNINGS.pop((str(getattr(ctx, "job_id", "")), channel_id), None)
            if warning:
                entry["inventory_warning"] = warning
            entry["videos"] = videos
            entry["counts"] = {"videos": len(videos)}
            if entry.get("inventory_warning"):
                _set_phase_progress(entry, "inventory", completed=0, total=1, current_item="")
                entry["phases"]["inventory"] = "INCOMPLETE"
            else:
                _set_phase_progress(entry, "inventory", completed=1, total=1, current_item="", progress=100.0)
                entry["phases"]["inventory"] = "COMPLETED"
            payload["progress"] = _work_snapshot(payload)
            _save(ctx, payload)
            # Inventory and downstream processing have separate scopes. The
            # inventory result is persisted, then process only newly discovered
            # or still-incomplete items unless the operator explicitly forces
            # a recheck. Previously-ready videos are not probed/matched again.
            if set(selected) - {"inventory"}:
                force_all = bool(payload.get("force_metadata") or payload.get("force_entity"))
                pending_limit = 0 if payload.get("unbounded") else limit
                # Real Archive X database contexts always expose ``conn``. Keep
                # lightweight legacy test/adaptor contexts compatible by using
                # the just-persisted inventory result when no DB query interface
                # is available; runtime database errors are not swallowed.
                if hasattr(ctx.db, "conn"):
                    pending = _local_inventory(ctx, channel_id, pending_limit, pending_only=not force_all)
                else:
                    pending = list(videos)
                pending_ids = {int(v.get("media_id") or 0) for v in pending}
                merged = []
                seen_ids = set()
                # Newly inventoried rows are processed only when not already
                # ready. Explicit force flags intentionally include ready rows.
                for video in videos:
                    media_id = int(video.get("media_id") or 0)
                    if media_id > 0 and (force_all or media_id in pending_ids) and media_id not in seen_ids:
                        merged.append(video); seen_ids.add(media_id)
                for video in pending:
                    media_id = int(video.get("media_id") or 0)
                    if media_id > 0 and media_id not in seen_ids:
                        merged.append(video); seen_ids.add(media_id)
                if limit and not payload.get("unbounded"):
                    merged = merged[:limit]
                videos = merged
                entry["videos"] = videos
                entry.setdefault("counts", {})["videos"] = len(videos)
                _save(ctx, payload)
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            entry["phases"]["inventory"] = "FAILED"
            entry["state"] = "FAILED"
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
            _save(ctx, payload)
            _log_pipeline_event(ctx, "metadata_assets.phase_failed", "ERROR", channel_id=channel_id, phase="inventory",
                                message=f"Metadata & Assets phase failed: {PHASE_LABELS['inventory']}: {type(exc).__name__}: {exc}",
                                details={"error_type": type(exc).__name__, "error": str(exc)[:1000]})
            return 0, 1

    # An explicit Snapshots-only run is also a local integrity audit. Do not
    # derive its candidates from archive_x_published_media: a READY row can have
    # missing/corrupt frame files while its persisted resource state still says
    # READY. Load the saved channel catalogue and let the snapshot engine inspect
    # the actual on-disk assets before deciding which items need work.
    snapshot_integrity_scan = len(selected) == 1 and selected[0] == "snapshots"
    if snapshot_integrity_scan:
        videos = _local_inventory(ctx, channel_id, limit, pending_only=False)
        entry["videos"] = videos
    elif not videos and "inventory" not in selected:
        videos = _local_inventory(ctx, channel_id, limit, pending_only=pending_only or not bool(payload.get("force_metadata") or payload.get("force_entity")))
        entry["videos"] = videos

    if inventory_auto_added and videos and _phase_state(entry, "inventory") != "COMPLETED":
        entry["phases"]["inventory"] = "COMPLETED"
        entry.setdefault("counts", {})["videos"] = len(videos)
        _save(ctx, payload)

    if not videos:
        entry["state"] = "COMPLETED"
        entry["counts"] = {"videos": 0, "ready": 0, "failed": 0}
        _save(ctx, payload)
        return 0, 0

    # A prerequisite stage added by preflight is scoped to only the channels
    # that were missing that dependency.  If persisted data is already present
    # for another selected channel, mark the prerequisite satisfied without
    # refreshing Telegram/provider data there.
    for auto_phase in ("metadata",):
        if auto_phase in selected and channel_id in _auto_added_channels(payload, auto_phase) and _phase_state(entry, auto_phase) != "COMPLETED":
            with ctx.db.lock:
                total = int(ctx.db.conn.execute("SELECT COUNT(*) FROM media WHERE source_channel=?", (channel_id,)).fetchone()[0] or 0)
                synced = int(ctx.db.conn.execute("SELECT COUNT(*) FROM media WHERE source_channel=? AND metadata_synced_at IS NOT NULL", (channel_id,)).fetchone()[0] or 0)
            if total and synced >= total:
                entry["phases"][auto_phase] = "COMPLETED"
                _save(ctx, payload)

    caption_sync_enabled = bool(set(selected) & {"metadata", "performers", "channel_data"})
    caption_signatures_before = (
        await asyncio.to_thread(_caption_signatures, ctx.db, channel_id, videos)
        if caption_sync_enabled else {}
    )

    # Filename-linked entities may be resolved before performer/studio data
    # stages only when this run is not doing scene metadata matching. If the
    # metadata phase is selected, provider OSHASH and filename-clue matching must
    # get first refusal; the filename entity fallback runs after a provider no-match.
    if "metadata" not in set(selected) and (set(selected) & {"performers", "channel_data"}):
        from ..metadata.filename_entities import link_known_entities_from_filenames
        filename_links = await asyncio.to_thread(
            link_known_entities_from_filenames, ctx.db,
            [int(v["media_id"]) for v in videos if int(v.get("media_id") or 0) > 0],
        )
        if any(filename_links.get(k, 0) for k in ("performer_links_added", "studio_links_added", "unknown_links_replaced", "ambiguous_entities_skipped")):
            ctx.db.log(
                "Filename entity matching: "
                f"{filename_links['performer_links_added']} performer link(s), "
                f"{filename_links['studio_links_added']} studio link(s), "
                f"{filename_links['unknown_links_replaced']} Unknown placeholder(s) replaced, "
                f"{filename_links['ambiguous_entities_skipped']} ambiguous alias(es) skipped",
                "INFO" if not filename_links["ambiguous_entities_skipped"] else "WARN",
                event="metadata.filename_entity_match", job_id=ctx.job_id,
                details={"channel_id": channel_id, "phase": "pre_entity_scan", **filename_links},
            )
        relationships_changed = bool(
            filename_links.get("performer_links_added")
            or filename_links.get("studio_links_added")
            or filename_links.get("unknown_links_replaced")
        )
        if relationships_changed:
            invalidated = _invalidate_completed_entity_phases(entry)
            if invalidated:
                _save(ctx, payload)
                _log_pipeline_event(
                    ctx, "metadata_assets.entity_phase_invalidated", channel_id=channel_id, phase="metadata",
                    message="Re-queued entity detail phases after filename relationship changes",
                    details={"invalidated_phases": invalidated, "reason": "filename_entity_reconciliation"},
                )

    snapshot_task = None
    if "snapshots" in selected and videos and _phase_state(entry, "snapshots") != "COMPLETED":
        snapshot_operation, snapshot_detail = _phase_detail("snapshots", channel_id, videos, payload)
        entry["phases"]["snapshots"] = "PROCESSING"
        _set_phase_progress(entry, "snapshots", completed=0, total=len(videos), current_item="")
        payload["progress"] = _work_snapshot(payload)
        _save(ctx, payload)
        _log_pipeline_event(
            ctx, "metadata_assets.phase_started", channel_id=channel_id, phase="snapshots",
            message=f"Metadata & Assets phase started in parallel: {PHASE_LABELS['snapshots']}",
            details={"channel_index": channel_index, "channel_count": channel_count,
                     "total_items": len(videos), "parallel_with": ["metadata", "performers", "channel_data"]},
        )
        snapshot_report_state = {"last_save": 0.0}

        def _snapshot_report(progress=None, detail=None, current_item=None, *, completed=None,
                             total=None, operation=None, force=False):
            nonlocal snapshot_report_state
            try:
                total_i = max(0, int(total if total is not None else len(videos)))
                if completed is None and progress is not None and total_i:
                    completed_i = int(total_i * max(0.0, min(100.0, float(progress))) / 100.0)
                else:
                    completed_i = int(completed or 0)
                current = str(current_item or operation or "")
                _set_phase_progress(entry, "snapshots", completed=completed_i, total=total_i,
                                    current_item=current, progress=progress)
                now = time.monotonic()
                if force or now - float(snapshot_report_state["last_save"]) >= 0.5:
                    payload["progress"] = _work_snapshot(payload)
                    _save(ctx, payload)
                    snapshot_report_state["last_save"] = now
                if getattr(ctx, "watchdog", None) is not None:
                    ctx.watchdog.heartbeat(
                        ctx.job_id, progress=(progress is not None or completed is not None or total is not None),
                        activity=True, reason="metadata_assets_parallel_snapshots",
                        metadata={"phase": "snapshots", "current_item": current[:180]},
                    )
                if getattr(ctx, "pipeline", None) is not None:
                    ctx.pipeline.heartbeat()
            except Exception:
                # Dashboard progress must never fail actual snapshot work.
                _swallowed_note("metadata_channel_pipeline._run_channel._snapshot_report")
                pass

        async def _run_parallel_snapshot_phase():
            await _run_engine(ctx, snapshot_operation, snapshot_detail, report_override=_snapshot_report)

        snapshot_task = asyncio.create_task(_run_parallel_snapshot_phase(), name=f"metadata-snapshots-{channel_id}")

    for phase_index, phase in enumerate(PHASES):
        await ctx.checkpoint()
        if phase == "inventory":
            continue        # handled above (it may be INCOMPLETE; never run here)
        if phase == "snapshots" and snapshot_task is not None:
            continue        # awaited after provider phases; its Telegram work runs concurrently
        if phase not in selected or _phase_state(entry, phase) == "COMPLETED":
            continue

        # Resolve entities only after metadata matching has completed. This is
        # a read-only lookup and is deduplicated at channel scope.
        performers, studios = await asyncio.to_thread(
            _entity_ids,
            ctx,
            channel_id,
            [int(v["media_id"]) for v in videos],
        ) if phase in {"performers", "channel_data"} else ([], [])
        if phase in {"performers", "channel_data"}:
            entity_type = "performer" if phase == "performers" else "studio"
            linked_ids = performers if phase == "performers" else studios
            # A query over every linked performer: on a worker thread (it held the event loop for 13 s).
            refreshable_ids = await asyncio.to_thread(
                entity_data.refreshable_entity_ids,
                ctx.db,
                entity_type,
                linked_ids,
                force_recheck=bool(payload.get("force_entity", False)),
            )
            if phase == "performers":
                performers = refreshable_ids
            else:
                studios = refreshable_ids
            if not refreshable_ids:
                entry["phases"][phase] = "COMPLETED"
                entry.setdefault("counts", {})[phase] = 0
                _set_phase_progress(entry, phase, completed=0, total=0, current_item="", progress=100.0)
                payload["current_phase"] = phase
                payload["current_file"] = ""
                payload["progress"] = _work_snapshot(payload)
                _save(ctx, payload)
                _log_pipeline_event(
                    ctx,
                    "metadata_assets.phase_skipped",
                    channel_id=channel_id,
                    phase=phase,
                    message=f"Skipped {PHASE_LABELS[phase]}; linked entities are already fresh or in retry cooldown",
                    details={
                        "linked_entities": len(linked_ids),
                        "reason": "no_refresh_due",
                        "force_recheck": bool(payload.get("force_entity", False)),
                    },
                )
                _start_entity_repair(ctx, phase)
                continue

        operation, detail = _phase_detail(phase, channel_id, videos, payload, performers, studios)
        entry["phases"][phase] = "PROCESSING"
        phase_total = len(videos) if phase in {"inventory", "metadata", "snapshots"} else (len(performers) if phase == "performers" else len(studios))
        _set_phase_progress(entry, phase, completed=0, total=phase_total, current_item="")
        payload["current_phase"] = phase
        payload["current_file"] = ""
        payload["progress"] = _work_snapshot(payload)
        _save(ctx, payload)
        _log_pipeline_event(ctx, "metadata_assets.phase_started", channel_id=channel_id, phase=phase,
                            message=f"Metadata & Assets phase started: {PHASE_LABELS[phase]}",
                            details={"channel_index": channel_index, "channel_count": channel_count, "total_items": phase_total})
        ctx.report(completed=phase_index, total=len(PHASES), current_item=channel_id,
                   operation=f"Channel {channel_index + 1}/{channel_count} · {PHASE_LABELS[phase]}", force=True)
        monitor_stop = asyncio.Event()
        async def _monitor_phase() -> None:
            nonlocal phase_total
            while not monitor_stop.is_set():
                try:
                    await asyncio.wait_for(monitor_stop.wait(), timeout=0.75)
                    break
                except asyncio.TimeoutError:
                    pass
                try:
                    live = ctx.db.get_job(ctx.job_id) or {}
                    total = int(live.get("total") or phase_total or 0)
                    done = int(live.get("completed") or 0)
                    current_item = str(live.get("current_item") or "")
                    if total and total != phase_total and phase in {"metadata", "performers", "channel_data", "snapshots"}:
                        phase_total = total
                    _set_phase_progress(entry, phase, completed=done, total=phase_total, current_item=current_item)
                    payload["current_phase"] = phase
                    payload["current_file"] = current_item
                    payload["progress"] = _work_snapshot(payload)
                    _save(ctx, payload)
                except Exception:
                    _swallowed_note("metadata_channel_pipeline._run_channel._monitor_phase")
                    continue
        monitor_task = asyncio.create_task(_monitor_phase())
        try:
            metadata_relationships_before = (
                await asyncio.to_thread(
                    _relationship_signature, ctx.db,
                    [int(v["media_id"]) for v in videos if int(v.get("media_id") or 0) > 0],
                )
                if phase == "metadata" else None
            )
            # Entity phases with no linked entities are valid no-op phases.
            if phase not in {"performers", "channel_data"} or (phase == "performers" and performers) or (phase == "channel_data" and studios):
                await _run_engine(ctx, operation, detail)
            # After the metadata phase, apply local filename entity matching only
            # to rows that still have no scene candidate. A provider hash match or
            # pending provider suggestion must not be overlaid by filename guesses.
            if phase == "metadata":
                with ctx.db.lock:
                    fallback_ids = []
                    for video in videos:
                        media_id = int(video.get("media_id") or 0)
                        if media_id <= 0:
                            continue
                        identity_row = ctx.db.conn.execute(
                            "SELECT metadata_source, identification_state FROM media WHERE id=? LIMIT 1",
                            (media_id,),
                        ).fetchone()
                        if not identity_row:
                            continue
                        source = str(identity_row["metadata_source"] or "").strip().lower()
                        state = str(identity_row["identification_state"] or "").strip().upper()
                        if source == "none" and state not in {"MATCHED", "VERIFIED", "PARTIAL"}:
                            fallback_ids.append(media_id)
                if fallback_ids:
                    from ..metadata.filename_entities import link_known_entities_from_filenames
                    filename_links = await asyncio.to_thread(
                        link_known_entities_from_filenames, ctx.db, fallback_ids,
                    )
                    if any(filename_links.get(k, 0) for k in ("performer_links_added", "studio_links_added", "unknown_links_replaced", "ambiguous_entities_skipped")):
                        ctx.db.log(
                            "Filename entity fallback after provider no-match: "
                            f"{filename_links['performer_links_added']} performer link(s), "
                            f"{filename_links['studio_links_added']} studio link(s), "
                            f"{filename_links['unknown_links_replaced']} Unknown placeholder(s) replaced, "
                            f"{filename_links['ambiguous_entities_skipped']} ambiguous alias(es) skipped",
                            "INFO" if not filename_links["ambiguous_entities_skipped"] else "WARN",
                            event="metadata.filename_entity_match", job_id=ctx.job_id,
                            details={"channel_id": channel_id, "phase": "post_metadata", **filename_links},
                        )
            if phase == "metadata" and metadata_relationships_before is not None:
                metadata_relationships_after = await asyncio.to_thread(
                    _relationship_signature, ctx.db,
                    [int(v["media_id"]) for v in videos if int(v.get("media_id") or 0) > 0],
                )
                if metadata_relationships_after != metadata_relationships_before:
                    invalidated = _invalidate_completed_entity_phases(entry)
                    if invalidated:
                        _log_pipeline_event(
                            ctx, "metadata_assets.entity_phase_invalidated", channel_id=channel_id, phase="metadata",
                            message="Re-queued entity detail phases after metadata matching changed relationships",
                            details={"invalidated_phases": invalidated, "reason": "metadata_relationship_change"},
                        )
            monitor_stop.set()
            await asyncio.gather(monitor_task, return_exceptions=True)
            if phase in {"metadata", "performers", "channel_data"} and caption_sync_enabled:
                try:
                    after_signatures = await asyncio.to_thread(_caption_signatures, ctx.db, channel_id, videos)
                    changed_caption_ids = [media_id for media_id, signature in after_signatures.items()
                                           if caption_signatures_before.get(media_id) != signature]
                    await _sync_changed_captions(ctx, channel_id, changed_caption_ids, videos)
                    caption_signatures_before = after_signatures
                except (JobCancelled, asyncio.CancelledError):
                    raise
                except Exception as caption_exc:
                    ctx.db.log(f"Caption synchronization deferred for channel {channel_id}: {type(caption_exc).__name__}: {caption_exc}", "WARN")
            _set_phase_progress(entry, phase, completed=phase_total, total=phase_total, current_item="", progress=100.0)
            entry["phases"][phase] = "COMPLETED"
            if phase == "performers":
                entry.setdefault("counts", {})["performers"] = len(performers)
            elif phase == "channel_data":
                entry.setdefault("counts", {})["channel_data"] = len(studios)
            _save(ctx, payload)
            _log_pipeline_event(ctx, "metadata_assets.phase_completed", channel_id=channel_id, phase=phase,
                                message=f"Metadata & Assets phase completed: {PHASE_LABELS[phase]}",
                                details={"completed": phase_total, "total": phase_total})
            if phase in {"performers", "channel_data"}:
                _start_entity_repair(ctx, phase)
        except (JobCancelled, asyncio.CancelledError):
            monitor_stop.set()
            await asyncio.gather(monitor_task, return_exceptions=True)
            if snapshot_task is not None:
                if not snapshot_task.done():
                    snapshot_task.cancel()
                await asyncio.gather(snapshot_task, return_exceptions=True)
            raise
        except Exception as exc:
            monitor_stop.set()
            await asyncio.gather(monitor_task, return_exceptions=True)
            if snapshot_task is not None:
                if not snapshot_task.done():
                    snapshot_task.cancel()
                await asyncio.gather(snapshot_task, return_exceptions=True)
            entry["phases"][phase] = "FAILED"
            entry["state"] = "FAILED"
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
            _save(ctx, payload)
            _log_pipeline_event(ctx, "metadata_assets.phase_failed", "ERROR", channel_id=channel_id, phase=phase,
                                current_item=str(payload.get("current_file") or ""),
                                message=f"Metadata & Assets phase failed: {PHASE_LABELS[phase]}: {type(exc).__name__}: {exc}",
                                details={"error_type": type(exc).__name__, "error": str(exc)[:1000]})
            return 0, 1

    if snapshot_task is not None:
        try:
            await snapshot_task
            _set_phase_progress(entry, "snapshots", completed=len(videos), total=len(videos), current_item="", progress=100.0)
            entry["phases"]["snapshots"] = "COMPLETED"
            _save(ctx, payload)
            _log_pipeline_event(
                ctx, "metadata_assets.phase_completed", channel_id=channel_id, phase="snapshots",
                message=f"Metadata & Assets phase completed: {PHASE_LABELS['snapshots']}",
                details={"completed": len(videos), "total": len(videos), "parallel": True},
            )
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            entry["phases"]["snapshots"] = "FAILED"
            entry["state"] = "FAILED"
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
            _save(ctx, payload)
            _log_pipeline_event(ctx, "metadata_assets.phase_failed", "ERROR", channel_id=channel_id, phase="snapshots",
                                message=f"Metadata & Assets phase failed: {PHASE_LABELS['snapshots']}: {type(exc).__name__}: {exc}",
                                details={"error_type": type(exc).__name__, "error": str(exc)[:1000], "parallel": True})
            return 0, 1

    # Snapshot generation runs in parallel with the metadata phase. For a newly
    # inventoried video, the first metadata pass may finish before its 36 local
    # frames exist; give those unresolved rows one visual-fingerprint pass now
    # that the snapshots are guaranteed to be available. Only rows lacking a
    # saved video PHASH are selected, making this a one-time scan repair rather
    # than a repeated provider retry loop.
    if snapshot_task is not None and "metadata" in selected and videos and bool(payload.get("visual_match", True)):   # the visual scan is optional
        try:
            from ..engines.preview_prefetch import preview_frames_path
            from ..metadata.pipeline import identify_video
            selected_media_ids = {int(v.get("media_id") or 0) for v in videos if int(v.get("media_id") or 0) > 0}
            def _missing_visual_hash_rows():
                with ctx.db.lock:
                    raw_rows = ctx.db.conn.execute(
                        """SELECT id,source_channel,source_message_id,filename,oshash,identification_state,video_phash
                           FROM media WHERE source_channel=? AND (video_phash IS NULL OR TRIM(video_phash)='')
                           AND UPPER(COALESCE(identification_state,'')) NOT IN ('MATCHED','VERIFIED')
                           ORDER BY CAST(source_message_id AS INTEGER) DESC""",
                        (channel_id,),
                    ).fetchall()
                return [dict(row) for row in raw_rows if int(row["id"]) in selected_media_ids]
            visual_rows = await asyncio.to_thread(_missing_visual_hash_rows)
            visual_rows = [row for row in visual_rows if row.get("source_message_id") and row.get("filename")]
            checked = 0
            for visual_index, row in enumerate(visual_rows, 1):
                await ctx.checkpoint()
                try:
                    frames_dir = preview_frames_path(int(row["source_channel"]), int(row["source_message_id"]))
                except (TypeError, ValueError):
                    continue
                if not frames_dir.is_dir() or not all((frames_dir / f"frame_{frame_index:02d}.jpg").is_file() for frame_index in range(1, 37)):
                    continue
                await ctx.cancellable(lambda row=row: identify_video(
                    ctx.db, channel_id=str(row["source_channel"]),
                    message_id=int(row["source_message_id"]), filename=str(row["filename"]),
                    oshash=str(row.get("oshash") or "") or None,
                    reconcile_filename_entities=False, visual_scan=True,
                ), activity=True)
                checked += 1
                if visual_index == 1 or visual_index % 10 == 0 or visual_index == len(visual_rows):
                    ctx.report(
                        completed=visual_index, total=max(1, len(visual_rows)),
                        current_item=str(row.get("filename") or row["id"]),
                        operation=f"Visual PHASH fallback · {visual_index}/{len(visual_rows)}",
                        force=(visual_index == 1 or visual_index == len(visual_rows)),
                    )
            if checked:
                _log_pipeline_event(
                    ctx, "metadata_assets.visual_phash_backfill", channel_id=channel_id, phase="metadata",
                    message=f"Ran visual fingerprint fallback after snapshots were generated for {checked} unresolved video(s)",
                    details={"checked": checked, "candidates": len(visual_rows)},
                )
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            ctx.db.log(f"Post-snapshot visual matching deferred for channel {channel_id}: {type(exc).__name__}: {exc}", "WARN")

    # Entity resolution is a per-video publication prerequisite. Missing
    # performer/studio identities are represented by explicit Unknown entities;
    # they are valid published classifications and can be replaced manually later.
    # Channels themselves are only source/grouping metadata and never gate
    # publication.
    media_ids=[int(v["media_id"]) for v in videos]
    # A snapshots-only integrity audit must not change entity readiness as a
    # side effect of merely loading all channel catalogue rows for filesystem
    # inspection. Other workflow modes retain the normal entity-completion step.
    if not snapshot_integrity_scan:
        mark_media_entity_complete(ctx.db, media_ids)
    from ..core.resource_registry import media_readiness_stats
    readiness = await asyncio.to_thread(media_readiness_stats, ctx.db, media_ids=media_ids)   # a whole channel's videos: not on the event loop
    entry["readiness"] = readiness
    entry.setdefault("counts", {}).update({
        "published_ready": readiness["published_ready"],
        "needs_review": readiness["needs_review"],
    })
    waiting = _readiness_reasons(readiness)
    ctx.db.log(
        f"Library readiness {channel_id}: {readiness['published_ready']} of {readiness['total']} videos in the Library"
        + (f"; {'; '.join(waiting)}" if waiting else ""),
        "INFO" if not waiting else "WARN",
    )

    # V29.4.132: a phase is complete only when its result is really there.
    # Engines count per-video failures without raising, so "the engine
    # returned" used to be reported as 100% even when nothing was produced.
    unverified = _verify_phases(entry, selected, readiness, len(videos))
    entry["state"] = "INCOMPLETE" if unverified else "COMPLETED"
    counts = entry.setdefault("counts", {})
    counts["selected_stages"] = len(selected)
    counts["ready"] = int(readiness.get("published_ready") or 0)
    counts["not_in_library"] = int(readiness.get("not_in_library") or 0)
    counts["failed"] = 0
    entry["error"] = ("Not finished: " + "; ".join(unverified)) if unverified else ""
    _save(ctx, payload)
    _log_pipeline_event(ctx, "metadata_assets.channel_completed", channel_id=channel_id,
                        message=f"Metadata & Assets channel completed: {channel_id}",
                        details={"channel_index": channel_index, "channel_count": channel_count, "video_count": len(videos), "selected_stages": selected})
    ctx.report(completed=len(PHASES), total=len(PHASES), current_item=channel_id,
               operation=(f"Channel {channel_index + 1}/{channel_count} · "
                          + ("INCOMPLETE" if unverified else "COMPLETE")
                          + f" · {readiness['published_ready']} of {len(videos)} videos in the Library"), force=True)
    return len(videos), 0




# ==============================================================================
# SECTION: Readiness verification
# Explains and verifies phase completion.
# ==============================================================================

def _readiness_reasons(readiness: dict[str, Any]) -> list[str]:
    """Why videos are not (yet) in the Library, in plain words (V29.4.129+)."""
    waiting = []
    if readiness.get("snapshot_pending"):
        waiting.append(f"{readiness['snapshot_pending']} need Snapshots")
    if readiness.get("card_image_pending"):
        waiting.append(f"{readiness['card_image_pending']} need a card image (run Snapshots again)")
    if readiness.get("metadata_pending"):
        waiting.append(f"{readiness['metadata_pending']} need metadata")
    if readiness.get("entity_pending"):
        waiting.append(f"{readiness['entity_pending']} need performer/studio data")
    if readiness.get("catalogue_pending"):
        waiting.append(f"{readiness['catalogue_pending']} need Collect filenames")
    if readiness.get("not_streamable"):
        waiting.append(f"{readiness['not_streamable']} not streamable (run Collect filenames again)")
    return waiting


def _verify_phases(entry: dict[str, Any], selected: list[str], readiness: dict[str, Any], total: int) -> list[str]:
    """Replace "the engine finished" with what the database shows was done.

    Only phases whose result can be checked per video are verified: metadata
    (every video has finished metadata) and snapshots (every video has
    complete local snapshots, and its card image). A phase with
    missing results is marked INCOMPLETE with its real count, so progress
    stays below 100% and a resumed job runs that phase again.
    """
    unverified = []
    if str((entry.get("phases") or {}).get("inventory") or "").upper() == "INCOMPLETE":
        unverified.append(f"{PHASE_LABELS['inventory']}: {entry.get('inventory_warning') or 'Telegram scan did not finish'}")
    checks = {
        "metadata": int(readiness.get("metadata_pending") or 0),
        "snapshots": int(readiness.get("snapshot_pending") or 0) + int(readiness.get("card_image_pending") or 0),
    }
    for phase, missing in checks.items():
        if phase not in selected or str((entry.get("phases") or {}).get(phase) or "").upper() != "COMPLETED":
            continue
        missing = max(0, min(int(total), missing))
        if not missing:
            continue
        _set_phase_progress(entry, phase, completed=int(total) - missing, total=int(total), current_item="")
        entry["phases"][phase] = "INCOMPLETE"
        unverified.append(f"{PHASE_LABELS[phase]}: {missing} of {int(total)} videos missing")
    return unverified





# ==============================================================================
# SECTION: Public entry points
# Local media runs, progress and the job entry point.
# ==============================================================================

async def run_local_media(ctx, media_items: list[dict[str, Any]], *, force_metadata: bool = False, force_entity: bool = False,
                           parent_job_id: str = "", parent_automation_item: str = "", visual_match: bool = True) -> dict[str, Any]:
    """Run the same five Metadata & Assets phases for local media.

    Content Automation uses this canonical pipeline after a torrent file is
    complete.  It deliberately stores a normal ``media`` row with a stable
    synthetic source identity, so the existing metadata/entity engines and
    reuse-first database remain authoritative even though the source has no
    Telegram message yet.
    """
    items = [dict(x) for x in (media_items or []) if isinstance(x, dict)]
    if not items:
        raise RuntimeError("Metadata & Assets local preparation has no media items")
    selected_stages = _selected_stages(_detail(ctx)) or list(PHASES)
    source_namespace = str(_detail(ctx).get("source_namespace") or "content_automation").strip() or "content_automation"
    payload = {
        "workflow": "local_media",
        "source_namespace": source_namespace,
        "parent_job_id": str(parent_job_id or ""),
        "parent_automation_item": str(parent_automation_item or ""),
        "channel_ids": [source_namespace],
        "stages": selected_stages,
        "current_channel_index": 0,
        "current_phase": "pending",
        "current_file": "",
        "channel_queue": [{"channel_id": source_namespace, "state": "PENDING", "videos": [], "phases": {}}],
        "force_metadata": bool(force_metadata),
        "force_entity": bool(force_entity),
        "local_media": items,
    }
    entry = payload["channel_queue"][0]
    # Inventory is authoritative for the local source. It never calls a provider.
    videos = []
    for index, item in enumerate(items, 1):
        await ctx.checkpoint()
        path = Path(str(item.get("path") or "")).expanduser()
        if not path.is_file():
            raise RuntimeError(f"Local media file does not exist: {path}")
        filename = str(item.get("filename") or path.name)
        oshash = str(item.get("oshash") or "").strip()
        if not oshash:
            from ..metadata.fingerprint import oshash_file
            oshash = await ctx.blocking(oshash_file, str(path))
        # Stable positive SQLite INTEGER derived from the content identity.
        digest = hashlib.sha256((source_namespace + ":" + oshash).encode("utf-8")).digest()
        source_message_id = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
        source_message_id = source_message_id or 1
        # Content Automation has already probed this exact source during
        # validation (or immediately before queuing this child). Reuse that
        # probe only when path, size and mtime still match; a recovered child
        # falls back to probing if the source has changed or the signature is absent.
        meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else None
        signature = item.get("metadata_signature") if isinstance(item.get("metadata_signature"), dict) else {}
        try:
            stat = path.stat()
            current_signature = {
                "path": str(path.resolve(strict=False)),
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        except OSError:
            current_signature = {}
        if not meta or {k: signature.get(k) for k in ("path", "size", "mtime_ns")} != current_signature:
            from ..core import internal_media
            try:
                meta = await ctx.blocking(internal_media.probe, path)
                meta = dict(meta or {})
            except Exception:
                meta = {}
            if meta:
                _log_pipeline_event(
                    ctx, "metadata_assets.local_probe_completed", channel_id=source_namespace, phase="inventory",
                    current_item=filename,
                    message=f"Probed technical metadata for local media: {filename}",
                    details={"source_path": current_signature.get("path", str(path)), "metadata": meta},
                )
        else:
            _log_pipeline_event(
                ctx, "metadata_assets.local_probe_reused", channel_id=source_namespace, phase="inventory",
                current_item=filename,
                message=f"Reused validation metadata; no second file probe: {filename}",
                details={"source_path": current_signature["path"], "file_size_bytes": current_signature["size"],
                         "mtime_ns": current_signature["mtime_ns"], "metadata": meta},
            )
        with ctx.db.lock:
            now = __import__("telegram_archive.config", fromlist=["now_iso"]).now_iso()
            ctx.db.conn.execute(
                """INSERT INTO media(source_channel,source_message_id,filename,title,size,duration,width,height,fps,codec,audio_codec,orientation,resolution_category,streamable,oshash,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_channel,source_message_id) DO UPDATE SET
                   filename=excluded.filename,title=COALESCE(media.title,excluded.title),size=excluded.size,duration=excluded.duration,width=excluded.width,height=excluded.height,
                   fps=excluded.fps,codec=excluded.codec,audio_codec=excluded.audio_codec,orientation=excluded.orientation,resolution_category=excluded.resolution_category,
                   streamable=excluded.streamable,oshash=excluded.oshash,updated_at=excluded.updated_at""",
                (source_namespace, source_message_id, filename, filename, int(path.stat().st_size), float(meta.get("duration") or 0),
                 int(meta.get("width") or 0), int(meta.get("height") or 0), float(meta.get("fps") or 0), str(meta.get("video_codec") or meta.get("codec") or ""),
                 str(meta.get("audio_codec") or ""), str(meta.get("orientation") or ""),
                 ("4K" if int(meta.get("height") or 0)>=2160 else "1440p" if int(meta.get("height") or 0)>=1440 else "1080p" if int(meta.get("height") or 0)>=1080 else "720p" if int(meta.get("height") or 0)>=720 else f"{int(meta.get('height') or 0)}p" if int(meta.get("height") or 0) else ""),
                 1, oshash, "SCANNED", now, now),
            )
            row = ctx.db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", (source_namespace, source_message_id)).fetchone()
            media_id = int(row["id"])
            ctx.db.conn.execute("INSERT OR IGNORE INTO resource_state(media_id,catalogue_state,updated_at) VALUES(?,?,?)", (media_id, "COMPLETE", now))
            ctx.db.conn.commit()
        videos.append({"media_id": media_id, "channel_id": source_namespace, "message_id": source_message_id, "filename": filename, "path": str(path), "oshash": oshash, "metadata": meta,
                       "identity_preflight": item.get("identity_preflight") if isinstance(item.get("identity_preflight"), dict) else None})
        ctx.report(completed=index, total=len(items), current_item=filename, operation="Metadata & Assets · Inventory")
    entry["videos"] = videos
    entry["counts"] = {"videos": len(videos)}
    for phase in PHASES:
        if phase not in selected_stages:
            entry["phases"][phase] = "NOT_SELECTED"
            continue
        await ctx.checkpoint()
        entry["phases"][phase] = "PROCESSING"
        total = len(videos)
        _set_phase_progress(entry, phase, completed=0, total=total, current_item="")
        payload["current_phase"] = phase
        payload["progress"] = _work_snapshot(payload)
        _save(ctx, payload)
        _log_pipeline_event(ctx, "metadata_assets.phase_started", channel_id=source_namespace, phase=phase, message=f"Metadata & Assets local phase started: {PHASE_LABELS[phase]}", details={"total_items": total, "workflow": "local_media"})
        phase_result = "COMPLETED"
        try:
            if phase == "inventory":
                pass
            elif phase == "metadata":
                from ..metadata.pipeline import identify_video, metadata_result_is_current, get_identification_state
                done = 0
                stats = {"reused": 0, "matched": 0, "suggested": 0, "no_match": 0, "failed": 0}
                for video in videos:
                    await ctx.checkpoint()
                    media_channel = str(video.get("channel_id") or source_namespace)
                    message_id = int(video["message_id"])
                    is_current, reason = (False, "forced") if force_metadata else await asyncio.to_thread(
                        metadata_result_is_current, ctx.db, media_channel, message_id,
                    )
                    preflight = video.get("identity_preflight") if isinstance(video.get("identity_preflight"), dict) else {}
                    preflight_has_candidates = bool(preflight.get("matches") or preflight.get("best"))
                    if is_current and preflight_has_candidates:
                        current_identity_state = get_identification_state(ctx.db, media_channel, message_id)
                        # A recent no-match/partial lookup should not suppress a
                        # candidate discovered while the torrent was downloading.
                        # Already matched/verified catalogue rows remain protected.
                        if current_identity_state not in ("MATCHED", "VERIFIED"):
                            is_current = False
                            reason = "pre-upload filename identity candidates available"
                    if is_current:
                        result = {"action": "skipped", "reason": reason}
                        stats["reused"] += 1
                        ctx.activity(
                            f"Reused saved metadata result for {video['filename']}",
                            operation="Metadata & Assets · metadata cache hit", current_item=video["filename"],
                            event="metadata_assets.metadata_reused",
                            details={"media_id": int(video.get("media_id") or 0), "channel_id": media_channel,
                                     "message_id": message_id, "reason": reason}, progress=True,
                        )
                    else:
                        result = await ctx.cancellable(lambda v=video, channel=media_channel: identify_video(
                            ctx.db, channel_id=channel, message_id=int(v["message_id"]),
                            filename=v["filename"], oshash=v["oshash"], force=bool(force_metadata),
                            preflight_result=v.get("identity_preflight"), visual_scan=bool(visual_match),
                        ))
                        action = str(result.get("action") or "no_match")
                        # identify_video reports successful catalogue updates as "applied"
                        # and provider failures as "provider_error"; normalize these into
                        # the local pipeline's stable counters instead of counting matches
                        # as no-matches in the job summary.
                        if action == "applied":
                            stats["matched"] += 1
                        elif action in {"failed", "provider_error"}:
                            stats["failed"] += 1
                        elif action in {"suggested", "no_match"}:
                            stats[action] += 1
                        elif action != "skipped":
                            stats["no_match"] += 1
                        ctx.activity(
                            f"Metadata identification {action.replace('_', ' ')} for {video['filename']}",
                            operation="Metadata & Assets · scene identification", current_item=video["filename"],
                            event="metadata_assets.metadata_item_completed",
                            details={"media_id": int(video.get("media_id") or 0), "channel_id": media_channel,
                                     "message_id": message_id, "action": action,
                                     "confidence": result.get("confidence"), "source": result.get("source"),
                                     "suggestion_count": int(result.get("suggestion_count") or 0)}, progress=True,
                        )
                    done += 1
                    _set_phase_progress(entry, phase, completed=done, total=total, current_item=video["filename"])
                    payload["current_file"] = video["filename"]
                    _save(ctx, payload)
                entry.setdefault("counts", {})["metadata"] = stats
            elif phase in {"performers", "channel_data"}:
                performer_ids, studio_ids = _entity_ids(ctx, source_namespace, [int(v["media_id"]) for v in videos])
                scope = "performers" if phase == "performers" else "studios"
                ids = performer_ids if phase == "performers" else studio_ids
                if ids:
                    operation = "entity_data"
                    detail = {"scope": scope, "entity_ids": {"performer": ids} if phase == "performers" else {"studio": ids}, "limit": 0, "unbounded": True, "force_recheck": bool(force_entity), "manual": True}
                    await _run_engine(ctx, operation, detail)
                _set_phase_progress(entry, phase, completed=len(ids), total=len(ids), current_item="", progress=100.0)
                entry.setdefault("counts", {})[phase] = len(ids)
            elif phase == "snapshots":
                failures = await _prepare_local_snapshot_assets(
                    ctx,
                    videos,
                    entry,
                    payload,
                    selected_stages=selected_stages,
                    total=total,
                )
                if failures:
                    phase_result = "COMPLETED_WITH_ERRORS"
            _set_phase_progress(entry, phase, completed=total if phase in {"inventory","metadata","snapshots"} else int((entry.get("counts") or {}).get(phase, 0)), total=total if phase in {"inventory","metadata","snapshots"} else int((entry.get("counts") or {}).get(phase, 0)), current_item="", progress=100.0)
            entry["phases"][phase] = phase_result
            _save(ctx, payload)
            _log_pipeline_event(
                ctx,
                "metadata_assets.phase_completed_with_errors" if phase_result == "COMPLETED_WITH_ERRORS" else "metadata_assets.phase_completed",
                "WARN" if phase_result == "COMPLETED_WITH_ERRORS" else "INFO",
                channel_id=source_namespace,
                phase=phase,
                message=f"Metadata & Assets local phase {phase_result.lower().replace('_', ' ')}: {PHASE_LABELS[phase]}",
                details={"workflow": "local_media", "snapshot_failures": len(entry.get("snapshot_failures") or [])},
            )
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            entry["phases"][phase] = "FAILED"
            entry["state"] = "FAILED"
            entry["error"] = f"{type(exc).__name__}: {exc}"[:500]
            _save(ctx, payload)
            _log_pipeline_event(ctx, "metadata_assets.phase_failed", "ERROR", channel_id=source_namespace, phase=phase, message=f"Metadata & Assets local phase failed: {PHASE_LABELS[phase]}: {type(exc).__name__}: {exc}", details={"workflow": "local_media", "error": str(exc)[:1000]})
            raise
    # A local video whose metadata step ran has finished its entity step too: whoever was not found is Unknown Performer / Unknown Studio (the same as for
    # a Telegram channel), which a later performer or studio stage replaces. This used to wait for all three stages to be ticked, and a folder that was scanned with
    # fewer never showed its videos.
    if "metadata" in set(selected_stages):
        mark_media_entity_complete(ctx.db, [int(v["media_id"]) for v in videos])
    snapshot_failures = entry.get("snapshot_failures") if isinstance(entry.get("snapshot_failures"), list) else []
    ready_count = max(0, len(videos) - len(snapshot_failures))
    entry["state"] = "COMPLETED_WITH_ERRORS" if snapshot_failures else "COMPLETED"
    entry["counts"]["ready"] = ready_count
    payload["current_phase"] = "complete"
    payload["current_file"] = ""
    payload["overall_ready"] = ready_count
    payload["overall_failed"] = len(snapshot_failures)
    payload["result_status"] = "COMPLETED_WITH_ERRORS" if snapshot_failures else "COMPLETED"
    payload["progress"] = _work_snapshot(payload)
    _save(ctx, payload)
    from ..core.resource_registry import media_readiness_stats
    readiness = await asyncio.to_thread(media_readiness_stats, ctx.db, media_ids=[int(v["media_id"]) for v in videos])
    payload["readiness"] = readiness
    _save(ctx, payload)
    _log_pipeline_event(
        ctx,
        "metadata_assets.pipeline_completed_with_errors" if snapshot_failures else "metadata_assets.pipeline_completed",
        "WARN" if snapshot_failures else "SUCCESS",
        message=(f"Metadata & Assets local preparation completed with {len(snapshot_failures)} snapshot failure(s)"
                 if snapshot_failures else f"Metadata & Assets local preparation completed: {len(videos)} media item(s)"),
        details={"workflow": "local_media", "videos_processed": len(videos), "videos_ready": ready_count,
                 "snapshot_failures": len(snapshot_failures), "published_ready": readiness["published_ready"],
                 "needs_review": readiness["needs_review"]},
    )
    return {
        "media_ids": [int(v["media_id"]) for v in videos],
        "videos": videos,
        "snapshot_failures": snapshot_failures,
        "progress": _work_snapshot(payload),
    }

def progress_snapshot(detail: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(detail, str):
        try:
            payload = json.loads(detail or "{}")
        except Exception:
            payload = {}
    elif isinstance(detail, dict):
        payload = dict(detail)
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}                      # valid JSON that is not an object ("[]", "123", "null")
    try:
        return _work_snapshot(payload)
    except Exception:
        # A saved job in a shape no version wrote must not take the progress page down with it.
        _swallowed_note("metadata_channel_pipeline.progress_snapshot")
        return _work_snapshot({})


async def _run_metadata_assets(ctx):
    """Job body: route local media runs or process each selected channel."""
    payload = _detail(ctx)
    if str(payload.get("workflow") or "").strip().casefold() == "local_media":
        media_items = payload.get("media_items")
        if not isinstance(media_items, list) or not media_items:
            raise RuntimeError("Metadata & Assets local preparation has no media items")
        return await run_local_media(
            ctx,
            media_items,
            force_metadata=bool(payload.get("force_metadata")),
            force_entity=bool(payload.get("force_entity")),
            visual_match=bool(payload.get("visual_match", True)),
            parent_job_id=str(payload.get("parent_job_id") or ""),
            parent_automation_item=str(payload.get("parent_automation_item") or ""),
        )
    raw_channels = payload.get("channel_ids")
    if not isinstance(raw_channels, list) or not raw_channels:
        raw_channels = [payload.get("channel_id")]
    channels = []
    for value in raw_channels:
        channel = str(value or "").strip()
        if channel and channel not in channels:
            channels.append(channel)
    if not channels:
        raise RuntimeError(
            "Select at least one source channel (this job's saved settings are missing: an earlier version overwrote them with a progress line, "
            "so it cannot be resumed; start a new Metadata & Assets run)")

    payload["channel_ids"] = channels
    payload.setdefault("current_channel_index", 0)
    payload["stages"] = _selected_stages(payload)
    if not payload["stages"]:
        # Without this a job whose saved stages are empty or misspelt "completed" having done nothing at all.
        raise RuntimeError("This Metadata & Assets job has no valid stage selected (Collect filenames, Match metadata, Performer data, "
                           "Studio/site data or Playback snapshots); start a new run and tick at least one")
    queue = payload.get("channel_queue")
    if not isinstance(queue, list) or len(queue) != len(channels):
        queue = [{"channel_id": channel, "state": "PENDING", "videos": [], "phases": {}}
                 for channel in channels]
    else:
        normalized = []
        for i, channel in enumerate(channels):
            entry = queue[i] if isinstance(queue[i], dict) else {}
            entry["channel_id"] = channel
            entry.setdefault("state", "PENDING")
            if not isinstance(entry.get("videos"), list):
                entry["videos"] = []
            if not isinstance(entry.get("phases"), dict):
                entry["phases"] = {}
            normalized.append(entry)
        queue = normalized
    payload["channel_queue"] = queue
    reconcile_captions = bool(payload.get("reconcile_uploaded_captions", payload.get("full_inventory", False)))
    caption_media_ids: list[int] = []
    if reconcile_captions:
        try:
            caption_media_ids = await asyncio.to_thread(_verified_delivery_caption_media_ids, ctx.db)
            payload["caption_reconciliation"] = {
                "state": "PENDING" if caption_media_ids else "NO_WORK",
                "total": len(caption_media_ids), "completed": 0,
                "updated": 0, "unchanged": 0, "failed": 0,
            }
        except Exception as exc:
            # This first query only reserves progress weight. Retry it after the
            # selected history scans because inventory may materialize additional
            # local rows that now match a verified-delivery ledger.
            payload["caption_reconciliation"] = {
                "state": "PENDING", "total": 0, "completed": 0,
                "updated": 0, "unchanged": 0, "failed": 0,
                "initial_query_warning": f"{type(exc).__name__}: {exc}"[:1000],
            }
            ctx.db.log(f"Initial delivered-caption count was unavailable; it will be retried after inventory: {type(exc).__name__}: {exc}", "WARN")
    else:
        payload.pop("caption_reconciliation", None)
    _save(ctx, payload)

    overall_ready = 0
    overall_failed = 0
    channel_count = len(channels)
    for channel_index, channel in enumerate(channels):
        await ctx.checkpoint()
        payload["current_channel_index"] = channel_index
        payload["channel_id"] = channel
        entry = queue[channel_index]
        entry["channel_id"] = channel
        _save(ctx, payload)
        ready, failed = await _run_channel(ctx, payload, entry, channel_index, channel_count)
        overall_ready += ready
        overall_failed += failed

    payload["current_channel_index"] = channel_count
    caption_result = {"total": 0, "completed": 0, "updated": 0, "unchanged": 0, "failed": 0}
    if reconcile_captions:
        try:
            # Re-select after inventory. That includes any catalogue rows the
            # just-completed full-history scan has materialized in the database.
            caption_media_ids = await asyncio.to_thread(_verified_delivery_caption_media_ids, ctx.db)
            payload["caption_reconciliation"] = {
                "state": "PENDING" if caption_media_ids else "NO_WORK",
                "total": len(caption_media_ids), "completed": 0,
                "updated": 0, "unchanged": 0, "failed": 0,
            }
            _save(ctx, payload)
            caption_result = await _reconcile_verified_delivery_captions(ctx, payload, caption_media_ids)
        except (JobCancelled, asyncio.CancelledError):
            raise
        except Exception as exc:
            payload["caption_reconciliation"] = {
                "state": "FAILED", "total": len(caption_media_ids), "completed": 0,
                "updated": 0, "unchanged": 0, "failed": 1,
                "error": f"{type(exc).__name__}: {exc}"[:1000],
            }
            caption_result = {"total": len(caption_media_ids), "completed": 0, "updated": 0, "unchanged": 0, "failed": 1}
            _save(ctx, payload)
            ctx.db.log(f"Uploaded caption reconciliation could not start: {type(exc).__name__}: {exc}", "WARN")
    payload["current_phase"] = "complete"
    payload["current_file"] = ""
    payload["overall_ready"] = overall_ready
    payload["overall_failed"] = overall_failed
    incomplete = sum(1 for e in queue if str(e.get("state") or "").upper() == "INCOMPLETE")
    in_library = sum(int((e.get("counts") or {}).get("ready") or 0) for e in queue)
    missing = sum(int((e.get("counts") or {}).get("not_in_library") or 0) for e in queue)
    caption_failed = int(caption_result.get("failed") or 0)
    if isinstance(payload.get("caption_reconciliation"), dict) and payload["caption_reconciliation"].get("state") == "FAILED" and caption_failed == 0:
        caption_failed = 1
    has_errors = bool(overall_failed or incomplete or caption_failed)
    payload["result_status"] = "COMPLETED_WITH_ERRORS" if has_errors else "COMPLETED"
    payload["progress"] = _work_snapshot(payload)
    payload["overall_in_library"] = in_library
    payload["overall_not_in_library"] = missing
    _save(ctx, payload)
    # V29.4.132: the final figure is the verified one, never a forced 100%.
    ctx.report(completed=channel_count, total=channel_count, progress=float(payload["progress"].get("overall_progress") or 0.0),
               operation=(f"Finished · {channel_count} channel(s) · {in_library} of {in_library + missing} videos in the Library"
                          + (f" · {incomplete} channel(s) not finished" if incomplete else "")
                          + (f" · {overall_failed} channel failures" if overall_failed else "")
                          + (f" · {int(caption_result.get('updated') or 0)} uploaded caption(s) updated" if reconcile_captions else "")
                          + (f" · {caption_failed} caption item(s) need retry/review" if caption_failed else "")), force=True)
    _log_pipeline_event(
        ctx,
        "metadata_assets.pipeline_completed_with_errors" if has_errors else "metadata_assets.pipeline_completed",
        "WARN" if has_errors else "SUCCESS",
        message=(f"Metadata & Assets pipeline completed{' with errors' if has_errors else ''}: {channel_count} channels; "
                 f"{overall_ready} videos processed; {in_library} in the Library, {missing} not yet; "
                 f"{incomplete} channel(s) not finished; {overall_failed} channel failures; "
                 f"uploaded captions {int(caption_result.get('updated') or 0)} updated, "
                 f"{int(caption_result.get('unchanged') or 0)} unchanged, {caption_failed} failed"),
        details={"channel_count": channel_count, "videos_processed": overall_ready, "in_library": in_library, "not_in_library": missing,
                 "incomplete_channels": incomplete, "channel_failures": overall_failed,
                 "caption_reconciliation": caption_result, "caption_failures": caption_failed, "result_status": payload["result_status"]},
    )


async def run(ctx):
    """Run Metadata & Assets with a short-lived shared Telegram range cache."""
    ctx.scan_window_enforced = True
    payload = _detail(ctx)
    if str(payload.get("workflow") or "").strip().casefold() == "local_media":
        return await _run_metadata_assets(ctx)
    from ..core import scan_range_cache
    token = scan_range_cache.begin_scan()
    try:
        return await _run_metadata_assets(ctx)
    finally:
        # Never leave temporary fingerprint ranges behind after completion,
        # cancellation, or an exception; the permanent MP4/index/start caches
        # are managed separately by playback_index_cache.
        await asyncio.to_thread(scan_range_cache.end_scan, token)
