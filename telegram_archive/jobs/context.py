"""What every engine module (video/, telegram/, playback/) receives from
the job worker. This is the seam between "job plumbing" and "actual work".

v15: engines are now `async def run(ctx)` and run on the SAME asyncio
loop as the Telegram client. (v14 ran jobs on a separate thread, which
would have made every Telethon call from an engine hang or crash —
Telethon clients are bound to the loop they were created on.)
Blocking work (FFmpeg/FFprobe, hashing, image processing) must go
through `await ctx.blocking(func, *args)` so it runs in a worker thread
and never freezes the web dashboard.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json
import time
from typing import Any, Callable, Optional

from .. import config
from ..core.database import Database
from ..core.governor import TransferGovernor
from ..telegram.service import TelegramService


# Shown while background work yields to video playback. Starts with "Waiting"
# so the watchdog treats it as a legitimate wait rather than a stall.
PLAYBACK_WAIT_OPERATION = "Waiting — paused while a video is playing or open (resumes automatically)"


# Jobs whose `detail` column holds the settings the job is restarted from (which channels, which options), not a progress line.
_DETAIL_IS_SETTINGS = {"METADATA_CHANNEL_PIPELINE", "METADATA_ASSETS"}


def _would_overwrite_settings(row: dict, detail: Any) -> bool:
    """True when `detail` is a plain progress message and the job's stored detail is its settings.

    The metadata-match stage runs inside the channel pipeline's job and ends with report(detail="fingerprints 0/21 ok ..."), which replaced the
    saved settings with that sentence. Recovering such a job (or resuming it after a restart) then found no channels: "Select at least one
    source channel". Progress text must never take the place of the settings."""
    if str(row.get("job_type") or "").upper() not in _DETAIL_IS_SETTINGS or not isinstance(detail, str):
        return False
    if not isinstance(_load_object(row.get("detail")), dict):
        return False
    return not isinstance(_load_object(detail), dict)


def _load_object(value):
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return None


class JobCancelled(Exception):
    """Raised by ctx.checkpoint() when the operator stops a job."""


class JobSkipped(Exception):
    """Raised when the operator skips the current queue item."""


class JobContext:
    """Handle given to a running job: progress, cancellation, checkpoints and state."""
    def __init__(self, job_id: str, job: dict, db: Database, telegram: TelegramService,
                 governor: TransferGovernor, manager: "Any"):
        self.job_id = job_id
        self.job = job
        self.db = db
        self.telegram = telegram
        self.governor = governor
        self._manager = manager
        self._pending: dict[str, Any] = {}
        self._operation: Optional[str] = None
        self._telemetry_stage = str(job.get("current_operation") or "")
        self._telemetry_item = str(job.get("current_item") or "")
        self._last_write = 0.0
        self._pending_detail: Optional[str] = None
        self.pipeline = getattr(manager, "pipeline_for", lambda _job_id: None)(job_id)
        self.watchdog = getattr(manager, "watchdogs", None)
        if self.watchdog is not None:
            self.watchdog.register_job(job_id, str(job.get("job_type") or "DEFAULT"))

    def report(self, progress: Optional[float] = None, detail: Optional[str] = None,
               current_item: Optional[str] = None, *, completed: Optional[int] = None,
               total: Optional[int] = None, operation: Optional[str] = None, force: bool = False):
        """Update this job's ONE row in Active Jobs. Internal stages only change
        the current operation/counters; writes are throttled so tight loops
        cannot flood the database or the dashboard."""
        # Nothing here reads the database: this is called from tight loops, and each read took the database lock the web server also waits on.
        # The stored row is read once, when the throttle lets a write through (below).
        fields: dict[str, Any] = {}
        if completed is not None:
            fields["completed"] = int(completed)
        if total is not None:
            fields["total"] = int(total)
        if progress is None and total:
            progress = min(100.0, 100.0 * float(completed or 0) / float(total))
        if progress is not None:
            fields["progress"] = round(float(progress), 1)
        if detail is not None:
            self._pending_detail = detail
        if current_item is not None:
            fields["current_item"] = str(current_item)[:300]
        if operation is not None:
            fields["current_operation"] = str(operation)[:200]
            self._telemetry_stage = str(operation)[:200]
            if operation != PLAYBACK_WAIT_OPERATION:
                self._operation = str(operation)
        if current_item is not None:
            self._telemetry_item = str(current_item)[:300]
        self._pending.update(fields)
        if self.pipeline is not None:
            self.pipeline.heartbeat()
        if self.watchdog is not None:
            is_progress = progress is not None or completed is not None or total is not None or current_item is not None
            self.watchdog.heartbeat(self.job_id, progress=is_progress, activity=True, reason="job_report", metadata={"operation": fields.get("current_operation", "")})
        now = time.monotonic()
        if not force and now - self._last_write < float(getattr(config, "JOB_PROGRESS_PERSIST_INTERVAL_SECONDS", 0.5)):
            return
        self._last_write = now
        self._publish_stage_telemetry()   # a database write: once per persist interval, not once per report
        pending, self._pending = self._pending, {}
        detail_now, self._pending_detail = self._pending_detail, None
        if pending or detail_now is not None:
            current_row = self.db.get_job(self.job_id) or {}
            current_status = str(current_row.get("status") or "")
            # Preserve terminal states written by the job lifecycle reconciler. A late progress/completion report from an interactive
            # runner must never resurrect COMPLETED/CANCELLED/FAILED back to RUNNING.
            terminal = {"COMPLETED", "COMPLETED_WITH_ERRORS", "CANCELLED", "FAILED"}
            pending["status"] = current_status if current_status in ("PAUSING", "CANCEL_REQUESTED", *terminal) else "RUNNING"
            if detail_now is not None and not _would_overwrite_settings(current_row, detail_now):
                pending["detail"] = detail_now
            checkpoint = dict(pending)
            self.db.save_job_checkpoint(
                self.job_id, checkpoint,
                stage=pending.get("current_operation"),
                item_id=pending.get("current_item"),
                item_index=pending.get("completed"),
                total_items=pending.get("total"),
                current_operation=pending.get("current_operation"),
                update_fields=pending,
            )
            recovery = getattr(self._manager, "recovery", None)
            if recovery is not None:
                recovery.checkpoint(self.job, checkpoint=checkpoint, operation=pending.get("current_operation"))

    def _publish_stage_telemetry(self) -> None:
        if str(self.job.get("job_type") or "").upper() not in {
            "METADATA_CHANNEL_PIPELINE", "METADATA_ASSETS",
        }:
            return
        telemetry = {}
        if self._telemetry_stage:
            telemetry["stage"] = self._telemetry_stage
            telemetry["current_item"] = self._telemetry_item
        if telemetry:
            try:
                self.db.set_job_transfer_telemetry(self.job_id, telemetry)
            except Exception:
                # Telemetry is advisory and must not fail the actual scan.
                _swallowed_note("context.JobContext._publish_stage_telemetry")
                pass

    def activity(self, message: str, *, operation: Optional[str] = None,
                 current_item: Optional[str] = None, level: str = "INFO",
                 event: str = "job.activity", details: Optional[dict[str, Any]] = None,
                 progress: bool = True, force: bool = False) -> None:
        """Publish a durable, job-linked process breadcrumb and refresh liveness.

        This is deliberately distinct from ``report(detail=...)``: job ``detail``
        stores restartable workflow configuration/checkpoints and must not be
        overwritten with a progress message. Activity events persist separately
        and are rendered in the live job detail timeline.
        """
        item_text = str(current_item) if current_item is not None else None
        operation_text = str(operation) if operation is not None else None
        if item_text is not None or operation_text is not None:
            self.report(current_item=item_text, operation=operation_text, force=force)
        if self.watchdog is not None:
            self.watchdog.heartbeat(
                self.job_id, progress=bool(progress), activity=True,
                reason=str(event or "job.activity")[:160],
                metadata={"operation": operation_text or self._operation or "", **(details or {})},
            )
        if self.pipeline is not None:
            self.pipeline.heartbeat(activity=True)
        try:
            self.db.log(str(message), str(level or "INFO").upper(), event=str(event or "job.activity"),
                        job_id=self.job_id, operation=operation_text,
                        details=dict(details or {}))
        except Exception:
            # Progress telemetry must never turn an otherwise successful stage
            # into a failed transfer. The job's durable checkpoint remains the
            # source of truth if activity logging is temporarily unavailable.
            _swallowed_note("context.JobContext.activity")
            pass

    def skip_requested(self) -> bool:
        return bool(self._manager.skip_requested(self.job_id))

    def cancel_requested(self) -> bool:
        return bool(self._manager.cancel_requested(self.job_id))

    def pause_requested(self) -> bool:
        return bool(self._manager.pause_requested(self.job_id))

    async def cancellable(self, awaitable_factory: Callable[[], Any], *, timeout: float | None = None,
                          activity: bool = False):
        """Run one long async operation while making Cancel responsive.

        ``activity`` marks the wait loop as an actively supervised operation.
        Use it only where the awaited operation has its own bounded stall/error
        detection (for example local snapshot extraction), because the wrapper's
        polling alone does not prove the underlying native work is advancing.
        Pause and Skip are intentionally handled at safe checkpoints so a video
        message and its paired thumbnail are not left half-created. Cancel may
        stop the in-flight operation immediately.
        """
        task = asyncio.create_task(awaitable_factory())
        started = time.monotonic()
        last_control_report = 0.0
        try:
            while True:
                if task.done():
                    return await task
                self._publish_stage_telemetry()
                if self.watchdog is not None:
                    self.watchdog.heartbeat(
                        self.job_id, progress=False, activity=activity,
                        reason="cancellable_wait",
                        metadata={"operation": self._operation or "cancellable operation"},
                    )
                if self.pipeline is not None:
                    self.pipeline.heartbeat(activity=activity)
                if timeout is not None and time.monotonic() - started >= float(timeout):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    if self.db is not None:
                        self.report(operation=f"Operation timed out after {float(timeout):.0f}s", force=True)
                    raise TimeoutError(f"job operation timed out after {float(timeout):.0f}s")
                if self._manager.cancel_requested(self.job_id):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise JobCancelled()
                now = time.monotonic()
                if now - last_control_report >= 1.0:
                    if self.pause_requested():
                        self.report(current_item=self.job.get("current_item") or "",
                                    operation="Pause requested — finishing current operation", force=True)
                        last_control_report = now
                    elif self.skip_requested():
                        self.report(current_item=self.job.get("current_item") or "",
                                    operation="Skip requested — finishing current operation", force=True)
                        last_control_report = now
                await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def checkpoint(self):
        """Call between units of work: waits out a pause, raises if stopped."""
        if self.pipeline is not None:
            self.pipeline.heartbeat()
        if self.watchdog is not None:
            self.watchdog.heartbeat(self.job_id, progress=False, reason="checkpoint")
        # Check cancellation before entering any resource/playback gate so a
        # Stop request cannot get trapped behind an unrelated playback session.
        if self._manager.cancel_requested(self.job_id):
            raise JobCancelled()
        await self._manager.wait_if_paused(self.job_id)
        if self._manager.cancel_requested(self.job_id):
            raise JobCancelled()
        resources = getattr(self._manager, "resources", None)
        # Every background/service pipeline yields at safe checkpoints while
        # interactive playback is active. Make the wait cancellable: the old
        # implementation could remain blocked here until playback ended, which
        # made a manual Stop look ineffective.
        if resources is not None:
            category = "generic"
            try:
                from .manager import job_resource_category
                category = job_resource_category(self.job)
            except Exception:
                _swallowed_note("context.JobContext.checkpoint")
                pass
            gate = asyncio.create_task(resources.cooperative_background_checkpoint(category=category))
            announced = False
            try:
                while not gate.done():
                    playback_waits = True
                    try:
                        playback_waits = resources.playback_blocks_category(category)
                    except Exception:
                        _swallowed_note("context.JobContext.checkpoint")
                        pass
                    if not announced and getattr(resources, "playback_active", False) and playback_waits:
                        self.report(operation=PLAYBACK_WAIT_OPERATION, force=True)
                        announced = True
                    if self._manager.cancel_requested(self.job_id):
                        gate.cancel()
                        await asyncio.gather(gate, return_exceptions=True)
                        raise JobCancelled()
                    if self.watchdog is not None:
                        self.watchdog.heartbeat(self.job_id, progress=False, reason="resource_wait")
                    await asyncio.sleep(0.1)
                await gate
                if announced and self._operation:
                    self.report(operation=self._operation, force=True)
            except asyncio.CancelledError:
                if not gate.done():
                    gate.cancel()
                    await asyncio.gather(gate, return_exceptions=True)
                raise
        if self._manager.skip_requested(self.job_id):
            self._manager.clear_skip(self.job_id)
            raise JobSkipped()
        if self._manager.cancel_requested(self.job_id):
            raise JobCancelled()

    async def blocking(self, func: Callable, *args, timeout: float | None = None, **kwargs):
        """Run blocking code on this job's private bounded pipeline executor.

        This prevents one module from exhausting the application's default
        asyncio thread pool and slowing unrelated modules.
        """
        resources = getattr(self._manager, "resources", None)
        # Every background/service pipeline yields at safe checkpoints while
        # interactive playback is active. An already-running native operation may
        # finish its current unit, but the next unit waits for playback to clear.
        if resources is not None:
            if self.watchdog is not None:
                self.watchdog.heartbeat(self.job_id, progress=False, activity=True, reason="blocking_resource_wait")
            category = "generic"
            try:
                from .manager import job_resource_category
                category = job_resource_category(self.job)
            except Exception:
                _swallowed_note("context.JobContext.blocking")
                pass
            await resources.cooperative_background_checkpoint(category=category)
        operation = (self.pipeline.blocking(func, *args, timeout=timeout, **kwargs)
                     if self.pipeline is not None
                     else asyncio.to_thread(func, *args, **kwargs))
        task = asyncio.ensure_future(operation)
        started = time.monotonic()
        # Native/media operations can legitimately take longer than the
        # watchdog heartbeat interval. Keep liveness explicit while the bounded
        # operation runs so a slow Telegram/FFmpeg request is not mistaken for
        # a dead job. Cancellation still propagates immediately.
        try:
            while not task.done():
                self._publish_stage_telemetry()
                if timeout is not None and time.monotonic() - started >= float(timeout):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    if self.db is not None:
                        self.report(operation=f"Operation timed out after {float(timeout):.0f}s", force=True)
                    raise TimeoutError(f"job blocking operation timed out after {float(timeout):.0f}s")
                if self._manager.cancel_requested(self.job_id):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise JobCancelled()
                if self.watchdog is not None:
                    self.watchdog.heartbeat(self.job_id, progress=False, reason="blocking_operation")
                if self.pipeline is not None:
                    self.pipeline.heartbeat()
                # Wake immediately when the work finishes while still respecting
                # a bounded operation timeout.
                wait_timeout = 1.0
                if timeout is not None:
                    wait_timeout = min(wait_timeout, max(0.01, float(timeout) - (time.monotonic() - started)))
                await asyncio.wait({task}, timeout=wait_timeout)
            return await task
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    # Per-job resume state (e.g. last processed message id) — survives
    # restarts, so a job cut off by a PC shutdown continues where it was.
    def load_state(self, default: Any = None) -> Any:
        checkpoint = self.db.get_job_checkpoint(self.job_id)
        if checkpoint is not None and checkpoint.get("payload") is not None:
            return checkpoint["payload"]
        # One-way compatibility fallback for jobs created before the generic
        # checkpoint store existed. New writes never use the settings table.
        return self.db.setting(f"job_state:{self.job_id}", default)

    def save_state(self, state: Any):
        job = self.db.get_job(self.job_id) or {}
        self.db.save_job_checkpoint(
            self.job_id, state,
            stage=job.get("current_operation"),
            current_operation=job.get("current_operation"),
        )
        recovery = getattr(self._manager, "recovery", None)
        if recovery is not None:
            recovery.record(self.job_id, "CHECKPOINT", status=job.get("status"), checkpoint=state, operation=job.get("current_operation"))
