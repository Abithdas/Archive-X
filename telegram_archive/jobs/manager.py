"""Background task manager — the ONE place all background work runs.

Each job is one visible task (Active Jobs shows only QUEUED/RUNNING/PAUSED
jobs). Internal stages update that job's counters and current operation;
they never create extra jobs. Different kinds of jobs run side by side; a
second job of the same kind is refused while one is active.

Modules:
  METADATA_SYNC  Metadata match
  UPLOAD_LOCAL   Upload Local Files
  MEDIA_MANAGER  Telegram Media Manager (unified mover/organizer/duplicates/router)
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Module helpers
#   - JobManager
#       - Construction and queueing
#       - Lifecycle
#       - Introspection and lane helpers
#       - Queries and submission
#       - Operator controls
#       - Cancellable resource acquisition
#       - Worker loops
#       - Job execution
# ------------------------------------------------------------------
from __future__ import annotations
from ..core.background_tasks import retain as _retain_task

import asyncio
import json
import time
import uuid
from datetime import datetime
from typing import Awaitable, Callable, Optional

from ..core.database import Database
from .. import config
from ..core.governor import TransferGovernor
from ..telegram.service import TelegramService
from .context import JobCancelled, JobContext, JobSkipped
from .pipeline import PipelineRegistry
from .priorities import job_priority
from .isolated_worker import ISOLATED_JOB_ENGINES, start_process as start_isolated_process
from ..core.watchdogs import WatchdogManager
from ..core.observability import operation_context
from ..core.background_priority import BackgroundPriorityGate
from .recovery import RecoveryCoordinator, INTERRUPTED, RECOVERING
from .orchestrator import JobOrchestrator

from ..engines import uploader, metadata_sync, asset_prefetch, entity_data, suggestion_scan
from ..engines import transfer_manager, metadata_assets, preview_prefetch, metadata_channel_pipeline
from ..media_manager.runner import RUNNER as media_manager
from ..operations import automation as content_automation
from ..core.backup import engine as backup_engine


ENGINES: dict[str, Callable[[JobContext], Awaitable[None]]] = {
    "METADATA_SYNC": metadata_assets.run,
    "SUGGESTION_SCAN": metadata_assets.run,
    "ENTITY_IMAGE_SYNC": metadata_assets.run,
    "THUMBNAIL_INDEXER": metadata_assets.run,
    "METADATA_ASSETS": metadata_assets.run,
    "METADATA_CHANNEL_PIPELINE": metadata_channel_pipeline.run,
    "UPLOAD_LOCAL": transfer_manager.run,
    "MEDIA_MANAGER": transfer_manager.run,
    "TELEGRAM_TRANSFER": transfer_manager.run,
    "ASSET_PREFETCH": asset_prefetch.run,
    "PREVIEW_PREFETCH": preview_prefetch.run,
    "CONTENT_AUTOMATION": content_automation.run,
    "BACKUP_CREATE": backup_engine.run,
}
JOB_NAMES = {
    "METADATA_SYNC": "Metadata & Assets Manager · Metadata Match",
    "SUGGESTION_SCAN": "Metadata & Assets Manager · Suggestions",
    "ENTITY_IMAGE_SYNC": "Metadata & Assets Manager · Performer / Channel data",
    "THUMBNAIL_INDEXER": "Metadata & Assets Manager · Snapshots",
    "METADATA_ASSETS": "Metadata & Assets Manager",
    "METADATA_CHANNEL_PIPELINE": "Metadata & Assets Manager",
    "UPLOAD_LOCAL":  "Telegram Transfer Manager · Local",
    "MEDIA_MANAGER": "Telegram Transfer Manager · Telegram",
    "TELEGRAM_TRANSFER": "Telegram Transfer Manager",
    "ASSET_PREFETCH": "Library Asset Prefetch",
    "PREVIEW_PREFETCH": "Metadata & Assets Manager · Snapshots",
    "CONTENT_AUTOMATION": "Content Automation",
    "BACKUP_CREATE": "Backup & Restore · Backup",
}
# Restart policy (owned by start() + RecoveryCoordinator): every recognised
# persisted job is marked INTERRUPTED after an unclean restart and waits for an
# explicit operator Resume/Cancel. Telegram transfers therefore never restart
# silently when Archive X boots.

# Maintenance caches are disposable work. Do not leave them paused across a
# restart; the idle maintenance watchers will recreate a small bounded slice.
RESTART_DISCARD_MAINTENANCE = {"ASSET_PREFETCH", "PREVIEW_PREFETCH"}
MAINTENANCE_JOB_TYPES = RESTART_DISCARD_MAINTENANCE



# ==============================================================================
# SECTION: Module helpers
# Maintenance suppression, resource categories and lane conflicts.
# ==============================================================================

def _maintenance_suppression_key(job_type: str) -> str:
    return f"maintenance.operator_suppressed.{str(job_type or '').upper()}"

def _maintenance_suppressed(db, job_type: str) -> bool:
    value = db.setting(_maintenance_suppression_key(job_type), {})
    return bool(isinstance(value, dict) and value.get("suppressed") is True)

def _set_maintenance_suppressed(db, job_type: str, *, suppressed: bool, job_id: str | None = None, reason: str = "") -> None:
    payload = {"suppressed": bool(suppressed), "job_id": job_id or "", "reason": reason, "timestamp": config.now_iso()}
    db.set_setting(_maintenance_suppression_key(job_type), payload)

# Image synchronization is strictly operator-triggered. Any job created by an
# old build, an automatic watcher, or another internal caller without the
# explicit manual marker is cancelled rather than being allowed to touch the
# provider APIs in the background.
MANUAL_ONLY_JOBS = {"ENTITY_IMAGE_SYNC"}

def _job_is_manual(job: dict) -> bool:
    import json
    try:
        detail = json.loads(job.get("detail") or "{}")
    except Exception:
        detail = {}
    return bool(detail.get("manual"))


def job_resource_category(job: dict) -> str:
    """The single background-resource category a job holds for its whole run.

    The JobManager is the only owner of a job's background slot. Engines must
    not acquire a second slot from inside the job: with a one-slot machine
    that self-deadlocks (the job waits for the slot it already holds).
    """
    job_type = str(job.get("job_type") or "")
    if job_type in {"PREVIEW_PREFETCH", "THUMBNAIL_INDEXER"}:
        return "preview"
    if job_type in {"METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC"}:
        ops = set(metadata_assets.unified_detail(job_type, job.get("detail"))["operations"]) if job_type == "METADATA_ASSETS" else set()
        if "thumbnails" in ops:
            return "preview"
        return "metadata"
    return "generic"


from .context import PLAYBACK_WAIT_OPERATION  # noqa: E402  (shared UI wording)

TRANSFER_FAMILY = {"TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER"}
METADATA_ASSET_FAMILY = {"METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC", "THUMBNAIL_INDEXER"}
COALESCIBLE_JOB_TYPES = {"ASSET_PREFETCH", "PREVIEW_PREFETCH", "SUGGESTION_SCAN", "METADATA_SYNC", "THUMBNAIL_INDEXER"}


def execution_lanes_conflict(job_a: dict, job_b: dict) -> bool:
    """Return whether two jobs require the same canonical execution lane.

    Channel selection is intentionally not part of this decision: Metadata &
    Assets and Telegram transfer are independent workflows even when they
    operate on different channels. Shared Telegram/resource governors provide
    the global safety limits; execution lanes prevent logical job conflicts.
    """
    return bool(set(_execution_keys(job_a)).intersection(_execution_keys(job_b)))


def _execution_keys(job: dict) -> list[str]:
    """Return bounded execution lanes for one job.

    The UI still exposes unified managers, but independent pipelines no longer
    block one another unnecessarily. Locks are acquired in deterministic order
    to avoid deadlocks when a combined Metadata & Assets job uses multiple lanes.
    """
    job_type = str(job.get("job_type") or "")
    if job_type in {"UPLOAD_LOCAL", "MEDIA_MANAGER", "TELEGRAM_TRANSFER"}:
        if job_type == "UPLOAD_LOCAL":
            return ["TRANSFER_LOCAL"]
        if job_type == "MEDIA_MANAGER":
            # The legacy Media Manager is independently runnable beside the
            # canonical Telegram Transfer Manager. Shared upload permits and
            # per-video locks still arbitrate actual work across both lanes.
            return ["MEDIA_MANAGER"]
        try:
            detail = json.loads(job.get("detail") or "{}")
        except Exception:
            detail = {}
        workflow = str(detail.get("workflow") or "").strip().upper()
        if workflow == "CONTENT_AUTOMATION":
            # The parent Content Automation job owns CONTENT_AUTOMATION; its
            # supervised Telegram send runs in a separate bounded child lane.
            # It still uses the global transfer/upload governor with every lane.
            return ["CONTENT_AUTOMATION_DELIVERY"]
        source = str(detail.get("source") or detail.get("source_type") or "telegram").strip().casefold()
        return ["TRANSFER_LOCAL" if source in {"local", "local_files", "upload_local"} else "TRANSFER_TELEGRAM"]
    if job_type == "METADATA_CHANNEL_PIPELINE":
        # One unified Metadata & Assets job owns only the lanes represented by
        # its selected stages. Inventory itself is Telegram/index work and does
        # not reserve a provider lane. Legacy standalone jobs still conflict
        # with the corresponding selected stage lane.
        try:
            detail = json.loads(job.get("detail") or "{}")
        except Exception:
            detail = {}
        stages = detail.get("stages") if isinstance(detail.get("stages"), list) else ["inventory", "metadata", "performers", "channel_data", "snapshots"]
        lanes = set()
        if "metadata" in stages: lanes.add("METADATA")
        if "performers" in stages or "channel_data" in stages: lanes.add("ENTITY_DATA")
        if "snapshots" in stages: lanes.add("THUMBNAILS")
        return sorted(lanes or {"METADATA_ASSETS"})
    if job_type in METADATA_ASSET_FAMILY:
        aliases = {
            "METADATA_SYNC": {"METADATA"},
            "SUGGESTION_SCAN": {"SUGGESTIONS"},
            "ENTITY_IMAGE_SYNC": {"ENTITY_DATA"},
            "THUMBNAIL_INDEXER": {"THUMBNAILS"},
        }
        if job_type in aliases:
            return sorted(aliases[job_type])
        ops = set(metadata_assets.unified_detail(job_type, job.get("detail"))["operations"])
        lanes = {"metadata": "METADATA", "suggestions": "SUGGESTIONS", "entity_data": "ENTITY_DATA", "thumbnails": "THUMBNAILS"}
        out = {lanes[op] for op in ops if op in lanes}
        return sorted(out or {"METADATA_ASSETS"})
    return [job_type]




# ==============================================================================
# SECTION: JobManager
# The one place all background work runs.
# ==============================================================================

class JobManager:

    # ------------------------------------------------------------------
    # SUBSECTION: Construction and queueing
    # Setup, enqueue, requeue and maintenance suppression.
    # ------------------------------------------------------------------
    """Queues, admits, runs and controls all background jobs."""
    def __init__(self, db: Database, telegram: TelegramService, governor: TransferGovernor, resources=None):
        self.db = db
        self.telegram = telegram
        self.governor = governor
        self.resources = resources
        self.queue: Optional[asyncio.PriorityQueue] = None
        self._queue_sequence = 0
        self._queued_ids: set[str] = set()
        self.worker_id = f"worker-{uuid.uuid4().hex[:10]}"
        self._execution_context: dict[str, dict] = {}
        # Active parent-child supervision is in-memory control-plane state. The
        # durable child job and activity journal remain authoritative; the map
        # exists only while a live parent is actively waiting for that child.
        self._supervised_children: dict[str, dict] = {}
        self.max_queued_runtime_items = 256
        self.task: Optional[asyncio.Task] = None
        self._lifecycle_watchdog: Optional[asyncio.Task] = None
        self._backup_scheduler: Optional[asyncio.Task] = None
        self.running: dict[str, asyncio.Task] = {}
        self.paused = False
        self.stopping = False
        self._cancelled: set[str] = set()
        self._cancel_requested_at: dict[str, float] = {}
        self._cancel_escalated: set[str] = set()
        self._paused_jobs: set[str] = set()
        self._skip_current: set[str] = set()
        self._pending_before_start: list[str] = []
        self._type_locks: dict[str, asyncio.Lock] = {}
        self._lane_drain = False
        self.pipelines = PipelineRegistry()
        self.isolated_processes: dict[str, object] = {}
        self.watchdogs = WatchdogManager(self)
        # One shared automatic resource-priority lane. Feature engines may
        # request an operation stage from the same gate; they must not create
        # their own schedulers or competing priority queues.
        self.background_priority = BackgroundPriorityGate()
        self.recovery = RecoveryCoordinator(db)
        self.orchestrator = JobOrchestrator(db, self)

    def _enqueue_job(self, jid: str, *, reset_admission: bool = True) -> None:
        """Queue one job through the durable orchestrator admission index.

        The database remains authoritative. The in-memory queue is bounded and
        deduplicated so repeated recovery/wait signals cannot create thousands
        of asyncio queue entries. Waiting jobs are reinserted only when their
        admission window is eligible.
        """
        queued_ids=getattr(self, "_queued_ids", set())
        if jid in queued_ids:
            return
        if self.queue is None:
            if jid not in self._pending_before_start:
                self._pending_before_start.append(jid)
            return
        row = self.db.get_job(jid) or {"job_type": ""}
        if len(queued_ids) >= getattr(self, "max_queued_runtime_items", 256):
            self.db.set_job_admission(jid, "WAITING", reason="ORCHESTRATOR_BACKPRESSURE", priority=job_priority(row), operation="Waiting for orchestrator capacity")
            return
        priority = self.orchestrator.effective_priority(row) if hasattr(self, "orchestrator") else job_priority(row)
        if reset_admission:
            try:
                self.db.set_job_admission(jid, "READY", reason="", next_run_at=None, priority=priority, operation="Queued for admission")
            except Exception:
                pass
        self._queue_sequence += 1
        queued_ids.add(jid)
        self.queue.put_nowait((priority, self._queue_sequence, jid))

    async def _requeue_after(self, jid: str, delay: float) -> None:
        await asyncio.sleep(max(0.05, float(delay)))
        if self.stopping:
            return
        job = self.db.get_job(jid)
        if job and job.get("status") == "QUEUED":
            self._enqueue_job(jid, reset_admission=False)

    def maintenance_suppressed(self, job_type: str) -> bool:
        return _maintenance_suppressed(self.db, job_type)

    def suppress_maintenance(self, job_type: str, *, job_id: str | None = None, reason: str = "") -> None:
        _set_maintenance_suppressed(self.db, job_type, suppressed=True, job_id=job_id, reason=reason)

    def clear_maintenance_suppression(self, job_type: str, *, reason: str = "") -> None:
        _set_maintenance_suppressed(self.db, job_type, suppressed=False, reason=reason)


    # ------------------------------------------------------------------
    # SUBSECTION: Lifecycle
    # Start, stop and metadata job coalescing.
    # ------------------------------------------------------------------
    def start(self):
        """Recover interrupted jobs and start the worker and watcher tasks."""
        self.queue = asyncio.PriorityQueue()
        scan = self.recovery.scan_startup()
        if scan.interrupted:
            self.db.log(f"Recovery coordinator found {len(scan.interrupted)} interrupted job(s); operator recovery is available", "WARN", event="recovery.interrupted_jobs_found", details={"count": len(scan.interrupted)})
        for jid in self._pending_before_start:
            self._enqueue_job(jid)
        self._pending_before_start.clear()
        self._coalesce_metadata_jobs()
        for job in self.db.active_jobs():
            jid, jtype = job["id"], job["job_type"]
            if job.get("status") in {"PAUSED", INTERRUPTED, RECOVERING}:
                # Preserve operator pause and explicit recovery states. The
                # startup scanner already classified runtime work when the
                # previous process did not shut down cleanly.
                continue
            if jtype in MAINTENANCE_JOB_TYPES:
                # Maintenance jobs are disposable; startup recovery handles any
                # stale persisted execution. Suppressed maintenance remains
                # terminal until an explicit retry or a new qualifying event.
                if self.maintenance_suppressed(jtype):
                    if job.get("status") not in {"CANCELLED", "FAILED", "COMPLETED", "COMPLETED_WITH_ERRORS"}:
                        self.db.update_job(jid, status="CANCELLED", current_operation="Automatic maintenance suppressed by operator")
                    continue

            if jtype in MANUAL_ONLY_JOBS and not _job_is_manual(job):
                self.db.update_job(jid, status="CANCELLED",
                                   detail="Automatic entity data sync is disabled — start it manually from Operations")
                self.db.log(f"{JOB_NAMES.get(jtype, jtype)} auto-run blocked; explicit manual start is required")
                continue
            if jtype not in ENGINES:
                self.db.update_job(jid, status="CANCELLED", detail="Module was removed")
            elif jtype in RESTART_DISCARD_MAINTENANCE:
                # Cache builders are safe to abandon because their source of
                # truth is the database/library. Preview snapshots are user-started
                # only; interrupted maintenance must not silently resume on boot.
                self.db.update_job(
                    jid,
                    status="CANCELLED",
                    current_operation="Maintenance slice discarded after restart; start it explicitly if still needed",
                )
                self.db.log(f"{JOB_NAMES.get(jtype, jtype)} discarded after restart; explicit retry is available")
            else:
                # User-owned/persistent work never auto-resumes on boot. Every
                # recognized interrupted job is paused and requires explicit
                # Resume from Operations.
                # Startup interruption is distinct from an operator pause.
                # The recovery coordinator owns the durable state transition.
                self.recovery.mark_interrupted(job, "Application restart occurred before a clean shutdown marker")
                if jtype == "CONTENT_AUTOMATION":
                    try:
                        content_automation.set_runtime(self.db, operator_started=False, paused=False)
                    except Exception as exc:
                        self.db.log(f"Content Automation runtime reset failed after restart: {type(exc).__name__}: {exc}", "WARN")
                self.db.log(f"{JOB_NAMES.get(jtype, jtype)} is waiting for explicit Resume or Cancel after restart")
        self.task = asyncio.create_task(self._worker(), name="job-worker")
        self._lifecycle_watchdog = asyncio.create_task(self._watch_job_lifecycle(), name="job-lifecycle-watchdog")
        self._backup_scheduler = asyncio.create_task(self._backup_schedule_loop(), name="backup-scheduler")
        self.watchdogs.start()
        # qBittorrent is an external application and may survive Telegram
        # Archive restarts. Pause only Archive X-owned torrents so no
        # previous automation item can resume downloading before the operator
        # explicitly starts Content Automation. This never starts qBittorrent.
        try:
            _retain_task(asyncio.create_task(content_automation.pause_qbittorrent_automation_torrents(self.db),
                                name="pause-qbit-automation-on-boot"))
        except Exception:
            pass

    async def stop(self):
        """Drain lanes, cancel running work and mark a clean shutdown."""
        self.stopping = True
        self.begin_lane_drain()
        if media_manager.running:
            media_manager.stop()
        tasks = [t for t in self.running.values() if not t.done()]
        if self.task:
            tasks.append(self.task)
        if self._lifecycle_watchdog:
            tasks.append(self._lifecycle_watchdog)
            self._lifecycle_watchdog = None
        if self._backup_scheduler:
            tasks.append(self._backup_scheduler)
            self._backup_scheduler = None
        self.watchdogs.stop()
        # Preserve explicit operator Stop across application shutdown. Jobs
        # that were merely running when the application died remain eligible
        # for INTERRUPTED recovery; only CANCEL_REQUESTED is terminalized here.
        try:
            for job in self.db.active_jobs():
                if str(job.get("status") or "").upper() == "CANCEL_REQUESTED":
                    self.db.update_job(
                        job["id"], status="CANCELLED",
                        current_operation="Stopped by operator",
                    )
                    self.recovery.record(
                        job["id"], "OPERATOR_CANCELLED", status="CANCELLED",
                        operation="Stop", detail="Operator Stop persisted during application shutdown",
                    )
        except Exception as exc:
            self.db.log(f"Failed to persist operator cancellations during shutdown: {type(exc).__name__}: {exc}", "WARN", event="job.operator_cancel_persist_failed")
        for t in tasks:
            t.cancel()
        for process in list(self.isolated_processes.values()):
            try:
                if process.is_alive():
                    process.terminate()
            except Exception:
                pass
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.isolated_processes.clear()
        # Only write the clean marker after all Archive X-owned background
        # workers have been stopped and their resources released.
        self.recovery.mark_clean_shutdown()

    def _coalesce_metadata_jobs(self):
        """Old builds queued one METADATA_SYNC job per channel (dozens in a
        burst). Merge every waiting one into a single job."""
        import json
        waiting = self.db.active_jobs("METADATA_SYNC")
        if len(waiting) < 2:
            return
        keep = waiting[0]
        merged = merge_metadata_detail(keep, waiting[1:])
        self.db.update_job(keep["id"], detail=merged)
        for j in waiting[1:]:
            self.db.update_job(j["id"], status="CANCELLED", detail=json.dumps({"merged_into": keep["id"]}))
        self.db.log(f"Metadata match: {len(waiting)} waiting jobs merged into one")


    # ------------------------------------------------------------------
    # SUBSECTION: Introspection and lane helpers
    # Recovery state, workflows, lanes and supervised children.
    # ------------------------------------------------------------------
    def recovery_snapshot(self) -> dict:
        return self.recovery.recovery_snapshot()

    # ---- operator controls ----
    def execution_lanes_conflict(self, job_a: dict, job_b: dict) -> bool:
        return execution_lanes_conflict(job_a, job_b)

    def execution_lane_keys(self, job: dict) -> list[str]:
        return sorted(set(_execution_keys(job)))

    def execution_lane_capacity(self, lane: str) -> int:
        base=int(getattr(config, "EXECUTION_LANE_CAPACITIES", {}).get(lane, 1))
        if self._lane_drain: return 0
        resources=self.resources
        try:
            snap=resources.snapshot() if resources is not None else {}
            pressure=str(snap.get("pressure_state") or snap.get("pressure") or "NORMAL").upper()
            if pressure in {"CRITICAL","CONSTRAINED"}: return max(1, base//2)
            if pressure in {"PRESSURED"} and base>1: return max(1, base-1)
        except Exception: pass
        return base

    def execution_lane_capacities(self, lanes=None) -> dict:
        keys=lanes or getattr(config, "EXECUTION_LANE_CAPACITIES", {}).keys()
        return {k:self.execution_lane_capacity(k) for k in keys}

    def begin_lane_drain(self):
        self._lane_drain=True

    def job_resource_category(self, job: dict) -> str:
        return job_resource_category(job)

    def register_supervised_child(self, parent_job_id: str, child_job_id: str, *, label: str = "child") -> dict:
        """Register a live parent -> child supervision relationship.

        It is safe to register the same relationship after a retry/recovery. A
        previous progress watermark is retained when the same parent resumes
        waiting on the same still-running child.
        """
        parent_id = str(parent_job_id or "").strip()
        child_id = str(child_job_id or "").strip()
        if not parent_id or not child_id or parent_id == child_id:
            raise ValueError("parent and child job IDs must be non-empty and different")
        now = time.monotonic()
        previous = self._supervised_children.get(child_id) or {}
        if str(previous.get("parent_job_id") or "") != parent_id:
            previous = {}
        record = dict(previous)
        record.update({
            "parent_job_id": parent_id,
            "child_job_id": child_id,
            "label": str(label or "child")[:120],
            "registered_monotonic": float(previous.get("registered_monotonic") or now),
            "last_observed_monotonic": now,
        })
        record.setdefault("last_progress_monotonic", now)
        record.setdefault("last_activity_id", None)
        record.setdefault("last_signature", None)
        self._supervised_children[child_id] = record
        return dict(record)

    def unregister_supervised_child(self, parent_job_id: str, child_job_id: str) -> None:
        parent_id = str(parent_job_id or "").strip()
        child_id = str(child_job_id or "").strip()
        current = self._supervised_children.get(child_id)
        if current and str(current.get("parent_job_id") or "") == parent_id:
            self._supervised_children.pop(child_id, None)

    def supervised_child(self, child_job_id: str) -> dict | None:
        current = self._supervised_children.get(str(child_job_id or "").strip())
        return dict(current) if current else None

    def observe_supervised_child(self, child_job_id: str, row: dict, activity_id: int | None = None) -> dict:
        """Update the progress watermark from durable child state/activity.

        Merely having a live task is liveness, not progress. Meaningful progress
        is a child operation/item/counter/status change or a new durable activity
        event. Explicit queue/resource waits are separately marked as waiting.
        """
        child_id = str(child_job_id or "").strip()
        record = self._supervised_children.get(child_id)
        if record is None:
            return {"registered": False, "no_progress_seconds": 0.0, "waiting": False}
        now = time.monotonic()
        row = row or {}
        signature = (
            str(row.get("status") or "").upper(),
            str(row.get("admission_state") or "").upper(),
            str(row.get("current_operation") or row.get("operation") or ""),
            str(row.get("current_item") or ""),
            row.get("progress"), row.get("completed"), row.get("total"),
        )
        try:
            current_activity_id = int(activity_id) if activity_id is not None else None
        except (TypeError, ValueError):
            current_activity_id = None
        previous_signature = record.get("last_signature")
        previous_activity_id = record.get("last_activity_id")
        progress_changed = previous_signature is not None and signature != previous_signature
        if current_activity_id is not None and previous_activity_id is not None and current_activity_id > int(previous_activity_id):
            progress_changed = True
        status = str(row.get("status") or "").upper()
        admission = str(row.get("admission_state") or "").upper()
        operation = str(row.get("current_operation") or row.get("operation") or "").strip().casefold()
        # Do not treat every operation containing the word "waiting" as
        # indefinitely healthy: e.g. "waiting for provider response" may be a
        # stuck request and must hit the supervised no-progress deadline.
        # Only known queue/resource/playback/FloodWait/backoff states bypass it.
        legitimate_wait_phrases = (
            "paused while a video is playing", "waiting for playback",
            "waiting for resource capacity", "waiting for resources",
            "waiting for shared cpu/disk capacity", "waiting for shared cpu/disk worker capacity",
            "waiting for reserved metadata worker capacity", "waiting for independent worker capacity",
            "waiting for worker category capacity", "waiting for execution lane",
            "waiting for orchestrator capacity", "waiting for telegram transfer capacity",
            "waiting for telegram rate-limit cooldown", "waiting for background priority",
            "waiting for download engine", "waiting for qbit", "retry scheduled",
            "retry budget preserved", "telegram floodwait", "flood_wait",
            "flood wait", "rate limit cooldown",
        )
        waiting = status in {"QUEUED", "PAUSED", "INTERRUPTED"} or admission in {"WAITING", "BLOCKED", "DELAYED"} or any(
            token in operation for token in legitimate_wait_phrases
        )
        if previous_signature is None or progress_changed or waiting:
            record["last_progress_monotonic"] = now
            if waiting:
                record["last_progress_reason"] = "legitimate_wait"
            elif progress_changed:
                record["last_progress_reason"] = "child_state_or_activity_changed"
            else:
                record["last_progress_reason"] = "supervision_started"
        record["last_signature"] = signature
        if current_activity_id is not None:
            record["last_activity_id"] = current_activity_id
        record["last_observed_monotonic"] = now
        self._supervised_children[child_id] = record
        return {
            "registered": True,
            "parent_job_id": str(record.get("parent_job_id") or ""),
            "child_job_id": child_id,
            "label": str(record.get("label") or "child"),
            "progress_changed": bool(progress_changed),
            "waiting": bool(waiting),
            "last_progress_reason": str(record.get("last_progress_reason") or ""),
            "no_progress_seconds": max(0.0, now - float(record.get("last_progress_monotonic") or now)),
            "signature": signature,
            "last_activity_id": record.get("last_activity_id"),
        }

    def orchestration_snapshot(self) -> dict:
        snapshot = self.orchestrator.snapshot()
        snapshot["queue_depth"] = self.queue.qsize() if self.queue is not None else 0
        snapshot["running"] = len([t for t in self.running.values() if not t.done()])
        snapshot["execution_lanes"] = self.db.execution_lane_snapshot(self.execution_lane_capacities())
        snapshot["lane_drain"] = self._lane_drain
        return snapshot


    # ------------------------------------------------------------------
    # SUBSECTION: Queries and submission
    # Active checks, duplicate coalescing and submit.
    # ------------------------------------------------------------------
    def is_active(self, job_type: str) -> bool:
        """Whether a requested job conflicts with an active execution lane."""
        if job_type not in TRANSFER_FAMILY and job_type not in METADATA_ASSET_FAMILY:
            return bool(self.db.active_jobs(job_type))
        requested = set(_execution_keys({"job_type": job_type, "detail": "{}"}))
        for active in self.db.active_jobs():
            if active.get("job_type") not in TRANSFER_FAMILY and active.get("job_type") not in METADATA_ASSET_FAMILY:
                continue
            if execution_lanes_conflict({"job_type": job_type, "detail": "{}"}, active):
                return True
        return False

    def transfer_active(self) -> bool:
        return any(self.db.active_jobs(t) for t in TRANSFER_FAMILY)

    def metadata_assets_active(self) -> bool:
        return any(self.db.active_jobs(t) for t in METADATA_ASSET_FAMILY)

    def _coalesce_duplicate_job(self, job_id: str) -> bool:
        job = self.db.get_job(job_id)
        if not job or job.get("job_type") not in COALESCIBLE_JOB_TYPES or job.get("status") != "QUEUED":
            return False
        detail = str(job.get("detail") or "")
        for other in self.db.active_jobs(job.get("job_type")):
            if other.get("id") == job_id or other.get("status") not in {"QUEUED", "RUNNING"}:
                continue
            if str(other.get("source_channel") or "") == str(job.get("source_channel") or "") and str(other.get("destination_channel") or "") == str(job.get("destination_channel") or "") and str(other.get("detail") or "") == detail:
                self.db.update_job(job_id, status="CANCELLED", current_operation=f"Coalesced into existing job {other.get('id')}", detail=f"Coalesced into {other.get('id')}")
                self.db.append_job_journal(job_id, "JOB_COALESCED", status="CANCELLED", operation="Duplicate work coalesced", payload={"target_job_id":other.get("id")})
                return True
        return False

    def submit(self, job_id: str):
        if self._coalesce_duplicate_job(job_id):
            return
        if self.queue is None:
            if job_id not in self._pending_before_start:
                self._pending_before_start.append(job_id)
        else:
            self._enqueue_job(job_id)


    # ------------------------------------------------------------------
    # SUBSECTION: Operator controls
    # Pause, resume, skip, recover and cancel.
    # ------------------------------------------------------------------
    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def pause_job(self, job_id: str) -> str:
        """Request a cooperative pause and return the durable state reached."""
        job = self.db.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        status = str(job.get("status") or "").upper()
        if status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}:
            raise ValueError(f"Job is already {status.lower().replace('_', ' ')}")
        if status in {INTERRUPTED, RECOVERING}:
            raise ValueError("Interrupted/recovering jobs cannot be paused. Use Recover & Resume or Stop.")
        if status == "CANCEL_REQUESTED":
            raise ValueError("Job is stopping and cannot be paused")
        if status not in {"QUEUED", "RUNNING", "PAUSING", "PAUSED"}:
            raise ValueError(f"Job cannot be paused from {status or 'unknown'}")

        self._paused_jobs.add(job_id)
        if job.get("job_type") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER"}:
            try:
                if media_manager.job_id == job_id or media_manager.last_job_id == job_id:
                    media_manager.pause()
            except Exception:
                pass
        if status == "QUEUED":
            self.db.update_job(job_id, status="PAUSED", current_operation="Paused before start")
            return "PAUSED"
        if status == "RUNNING":
            # Pause is cooperative: finish the current safe operation, then
            # JobContext enters PAUSED at the next checkpoint.
            self.db.update_job(job_id, status="PAUSING", current_operation="Pause requested — finishing current safe operation")
            return "PAUSING"
        # Make a paused state visible in memory as well as SQLite (important
        # after a dashboard refresh or for an isolated worker reading the row).
        if status == "PAUSED":
            return "PAUSED"
        return "PAUSING"

    async def recover_job(self, job_id: str):
        """Hard-recover one genuinely stalled job without restarting Archive X.

        Normal Refresh is read-only. Recovery is intentionally explicit and is
        only allowed for watchdog-stalled/failed jobs or PAUSING jobs that still
        have a live worker. The current coroutine is cancelled, its resources
        are released by the normal finally path, and the persisted job is
        re-queued from its saved state.
        """
        job = self.db.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        status = str(job.get("status") or "")
        if status not in {"RUNNING", "PAUSING", "QUEUED", INTERRUPTED, RECOVERING}:
            if status == "PAUSED":
                self.resume_job(job_id)
                return {"ok": True, "action": "resumed", "status": "QUEUED"}
            raise ValueError(f"Job is not recoverable from {status or 'unknown'}")

        # Interactive Media Manager owns a separate thread. Never start a second
        # runner while the first one is still alive; stop it and wait briefly for
        # its event loop to exit first.
        if job.get("job_type") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER"}:
            from ..media_manager.runner import RUNNER as media_manager
            if media_manager.job_id == job_id and media_manager.running:
                media_manager.stop()
                for _ in range(40):
                    if not media_manager.running:
                        break
                    await asyncio.sleep(0.25)
                if media_manager.running:
                    raise ValueError("Telegram Transfer Manager is still stopping; retry Recover after a few seconds")

        if status == INTERRUPTED:
            self.recovery.begin_recovery(job_id)
            self.db.update_job(job_id, status="QUEUED", current_operation="Recovery queued from persisted checkpoint")
            self.recovery.record(job_id, "RECOVERY_QUEUED", status="QUEUED", checkpoint=self.recovery._checkpoint_for(self.db.get_job(job_id) or {}))
        elif status == RECOVERING:
            self.db.update_job(job_id, status="QUEUED", current_operation="Recovery retry queued")
        task = self.running.get(job_id)
        if task is not None and not task.done():
            self._cancelled.add(job_id)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.running.pop(job_id, None)
        self._cancelled.discard(job_id)
        self._paused_jobs.discard(job_id)
        self._skip_current.discard(job_id)

        detail = job.get("detail") or "{}"
        try:
            payload = json.loads(detail) if isinstance(detail, str) else dict(detail)
        except Exception:
            payload = {}
        if job.get("job_type") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER"}:
            payload["resume_after_restart"] = True
            detail = json.dumps(payload, ensure_ascii=False)
        self.db.update_job(job_id, status="QUEUED", current_operation="Recovered — resuming from saved state", detail=detail)
        self._enqueue_job(job_id)
        self.db.log(f"{JOB_NAMES.get(job.get('job_type'), job.get('job_type'))} manually recovered and re-queued", "WARN", event="job.recovered", job_id=job_id)
        return {"ok": True, "action": "recovered", "status": "QUEUED"}

    def resume_job(self, job_id: str) -> str:
        job = self.db.get_job(job_id)
        if not job:
            raise ValueError("Job not found")
        status = str(job.get("status") or "").upper()
        if status not in {"PAUSED", "PAUSING"}:
            raise ValueError(f"Job cannot be resumed from {status or 'unknown'}")
        self._paused_jobs.discard(job_id)
        if job.get("job_type") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER"}:
            try:
                if media_manager.job_id == job_id or media_manager.last_job_id == job_id:
                    media_manager.resume()
            except Exception:
                pass
        live_task = job_id in self.running and not self.running[job_id].done()
        if live_task:
            self.db.update_job(job_id, status="RUNNING", current_operation="Resuming")
            return "RUNNING"
        # Restart-resume: preserve an explicit marker so the Telegram Transfer
        # workflow restores its saved manifest/config instead of asking the
        # operator to configure a 7,000-item channel again.
        detail = job.get("detail") or "{}"
        try:
            payload = json.loads(detail) if isinstance(detail, str) else dict(detail)
        except Exception:
            payload = {}
        interrupted = "interrupted by restart" in str(job.get("current_operation") or "").casefold()
        if interrupted and job.get("job_type") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER"}:
            payload["resume_after_restart"] = True
            detail = json.dumps(payload, ensure_ascii=False)
        self.db.update_job(job_id, status="QUEUED", current_operation="Resuming", detail=detail)
        if self.queue is None:
            if job_id not in self._pending_before_start:
                self._pending_before_start.append(job_id)
        else:
            self._enqueue_job(job_id)
        return "QUEUED"

    def skip_current(self, job_id: str):
        self._skip_current.add(job_id)

    def skip_requested(self, job_id: str) -> bool:
        return job_id in self._skip_current

    def pause_requested(self, job_id: str) -> bool:
        return job_id in self._paused_jobs

    def clear_skip(self, job_id: str):
        self._skip_current.discard(job_id)

    def cancel_playback_conflicting_maintenance(self) -> list[str]:
        """Stop disposable maintenance that must never compete with interactive playback."""
        stopped = []
        for jid, task in list(self.running.items()):
            job = self.db.get_job(jid)
            if not job or str(job.get("job_type") or "").upper() not in {"PREVIEW_PREFETCH", "ASSET_PREFETCH"}:
                continue
            if str(job.get("status") or "").upper() not in {"RUNNING", "QUEUED", "PAUSING", "PAUSED", "CANCEL_REQUESTED"}:
                continue
            self.cancel(jid)
            stopped.append(str(jid))
        return stopped

    def cancel(self, job_id: str) -> str:
        """Request Stop; workers finish cooperatively or are escalated by the lifecycle monitor."""
        job = self.db.get_job(job_id)
        if not job:
            self._cancelled.discard(job_id)
            self._paused_jobs.discard(job_id)
            self._skip_current.discard(job_id)
            self._cancel_requested_at.pop(job_id, None)
            self._cancel_escalated.discard(job_id)
            raise ValueError("Job not found")
        status = str(job.get("status") or "").upper()
        terminal = {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}
        if status in terminal:
            # Stale controls must never poison the in-memory cancel set.
            self._cancelled.discard(job_id)
            self._paused_jobs.discard(job_id)
            self._skip_current.discard(job_id)
            self._cancel_requested_at.pop(job_id, None)
            self._cancel_escalated.discard(job_id)
            return status
        if status not in {"QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", INTERRUPTED, RECOVERING}:
            raise ValueError(f"Job cannot be stopped from {status or 'unknown'}")

        self._cancelled.add(job_id)
        self._paused_jobs.discard(job_id)
        self._skip_current.discard(job_id)
        if status in {INTERRUPTED, RECOVERING}:
            self.db.update_job(job_id, status="CANCELLED", current_operation="Stopped; pending recovery discarded")
            self._cancelled.discard(job_id)
            return "CANCELLED"

        if job.get("job_type") in MAINTENANCE_JOB_TYPES:
            self.suppress_maintenance(job["job_type"], job_id=job_id, reason="Operator stopped automatic maintenance; explicit retry required")
            self.db.log(
                f"{JOB_NAMES.get(job.get('job_type'), job.get('job_type'))} operator suppression enabled",
                "INFO", event="job.maintenance_suppressed", job_id=job_id,
                details={"job_type": job.get('job_type')},
            )
            if job.get("job_type") in {"PREVIEW_PREFETCH", "ASSET_PREFETCH"}:
                live = self.running.get(job_id)
                if live is not None and not live.done():
                    live.cancel()
                self.db.update_job(job_id, status="CANCELLED", current_operation="Stopped — disposable maintenance cancelled")
                self._cancel_requested_at.pop(job_id, None)
                self._cancel_escalated.discard(job_id)
                self._cancelled.discard(job_id)
                return "CANCELLED"

        # The interactive Telegram runner has its own thread/event loop. Stop it
        # as well as publishing CANCEL_REQUESTED in the shared job record.
        if job.get("job_type") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER"}:
            try:
                if media_manager.job_id == job_id or media_manager.last_job_id == job_id:
                    media_manager.stop()
            except Exception:
                pass

        live_task = job_id in self.running and not self.running[job_id].done()
        if not live_task:
            self.db.update_job(job_id, status="CANCELLED", current_operation="Cancelled before resume")
            self._cancel_requested_at.pop(job_id, None)
            self._cancel_escalated.discard(job_id)
            self._cancelled.discard(job_id)
            return "CANCELLED"
        if status != "CANCEL_REQUESTED":
            self.db.update_job(job_id, status="CANCEL_REQUESTED", current_operation="Stop requested — finishing current operation")
        self._cancel_requested_at.setdefault(job_id, time.monotonic())
        return "CANCEL_REQUESTED"

    def watchdog_stop_unrecovered(self, job_id: str, *, reason: str) -> bool:
        """Force-close an active job after its watchdog grace period expires.

        Normal cancellation is requested first so engines can run their cleanup
        path. If still active, owned isolated workers are terminated and the
        owning asyncio task is cancelled. The durable row is made terminal now
        so the UI cannot display a zombie RUNNING/CANCEL_REQUESTED job.
        """
        jid = str(job_id)
        job = self.db.get_job(jid) or {}
        status = str(job.get("status") or "").upper()
        if status not in {"RUNNING", "QUEUED", "PAUSING", "PAUSED", "CANCEL_REQUESTED", INTERRUPTED, RECOVERING}:
            return False
        try:
            self.cancel(jid)
        except Exception:
            pass

        process = self.isolated_processes.get(jid)
        if process is not None:
            try:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1.0)
                    if process.is_alive() and hasattr(process, "kill"):
                        process.kill()
                        process.join(timeout=1.0)
            except Exception as exc:
                try:
                    self.db.log(f"Watchdog could not terminate isolated worker for {jid}: {type(exc).__name__}: {exc}",
                                "WARN", event="watchdog.worker_terminate_failed", job_id=jid)
                except Exception:
                    pass

        task = self.running.get(jid)
        if task is not None and not task.done():
            task.cancel()
        self.db.update_job(
            jid, status="CANCELLED", worker_state="STOPPED",
            current_operation="Stopped by watchdog after unrecovered-job recovery grace expired",
            error=(str(reason or "watchdog_unrecovered_stall")[:1000]),
        )
        try:
            self.db.log(
                f"{JOB_NAMES.get(job.get('job_type'), job.get('job_type') or 'Job')} force-stopped: {reason}",
                "ERROR", event="watchdog.unrecovered_force_stop", job_id=jid,
                details={"job_type": job.get("job_type"), "previous_status": status, "reason": str(reason)[:1000]},
            )
        except Exception:
            pass
        return True

    def cancel_requested(self, job_id: str) -> bool:
        return self.stopping or job_id in self._cancelled

    def pipeline_for(self, job_id: str):
        return self.pipelines.get(job_id)

    def pipeline_snapshot(self):
        return self.pipelines.snapshot()


    # ------------------------------------------------------------------
    # SUBSECTION: Cancellable resource acquisition
    # Waits for resources while honouring cancel requests.
    # ------------------------------------------------------------------
    async def _acquire_cancellable(self, awaitable, job_id: str, *, operation: str,
                                   heartbeat_reason: str = "resource_wait"):
        """Acquire a resource/gate without trapping Stop behind an unrelated wait.

        Gate waits may remain pending while playback owns the machine. Keep the
        watchdog heartbeat in the event loop, and publish the durable waiting
        state off-loop: admission updates use SQLite's shared writer lock and
        must never freeze every coroutine while waiting for a transfer lane.
        The wait state is written once, not once per second.
        """
        task = asyncio.create_task(awaitable)
        state_task = None
        announced = False
        wait_reason = {
            "resource_wait": "RESOURCE_CAPACITY",
            "service_resource_wait": "SERVICE_RESOURCE_CAPACITY",
            "transfer_gate_wait": "TELEGRAM_TRANSFER_CAPACITY",
            "operation_lane_wait": "EXECUTION_LANE_BUSY",
        }.get(heartbeat_reason, heartbeat_reason.upper())

        async def settle_control_task(control_task, reason):
            """Wait for slow synchronous control-plane I/O without blocking heartbeats."""
            while not control_task.done():
                if self.watchdogs is not None:
                    self.watchdogs.heartbeat(
                        job_id, progress=False, activity=True, reason=reason,
                        metadata={"operation": operation},
                    )
                await asyncio.sleep(0.2)
            return control_task.result()

        def publish_waiting_state():
            # The DB write can wait behind a long transaction. Run it on a
            # worker thread and re-check status there so a delayed write cannot
            # overwrite a cancellation/terminal state from a previous attempt.
            try:
                current = self.db.get_job(job_id) if hasattr(self.db, "get_job") else None
                if current and str(current.get("status") or "").upper() not in {"RUNNING", "PAUSING", "CANCEL_REQUESTED"}:
                    return
            except Exception:
                pass
            if hasattr(self, "orchestrator"):
                self.orchestrator.waiting(job_id, wait_reason, operation=operation)
            else:
                self.db.update_job(job_id, status="RUNNING", current_operation=operation)

        try:
            while not task.done():
                if self.cancel_requested(job_id):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    if state_task is not None and not state_task.done():
                        state_task.cancel()
                    raise JobCancelled()
                if self.watchdogs is not None:
                    self.watchdogs.heartbeat(
                        job_id,
                        progress=False,
                        activity=True,
                        reason=heartbeat_reason,
                        metadata={"operation": operation},
                    )
                if not announced:
                    # Previously this synchronous SQLite admission write was
                    # repeated every second on the application loop. Under DB
                    # writer contention it starved the loop, causing the
                    # independent watchdog to kill an otherwise healthy waiter.
                    state_task = asyncio.create_task(asyncio.to_thread(publish_waiting_state))
                    announced = True
                await asyncio.sleep(0.2)
            result = await task
            if state_task is not None:
                await settle_control_task(state_task, f"{heartbeat_reason}_state_persist")
            if self.cancel_requested(job_id):
                raise JobCancelled()
            if hasattr(self, "orchestrator"):
                ready_task = asyncio.create_task(asyncio.to_thread(self.orchestrator.ready, job_id))
                await settle_control_task(ready_task, f"{heartbeat_reason}_state_ready")
            return result
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if state_task is not None and not state_task.done():
                state_task.cancel()
            raise

    async def _acquire_background_cancellable(self, job_id: str, *, category: str = "generic",
                                              operation: str = "Waiting for interactive resources"):
        if self.resources is None:
            return
        await self._acquire_cancellable(
            self.resources.acquire_background(category),
            job_id,
            operation=operation,
            heartbeat_reason="resource_wait",
        )

    async def _acquire_service_cancellable(self, job_id: str, *,
                                           operation: str = "Waiting for interactive resources",
                                           block_playback: bool = True):
        if self.resources is None:
            return
        await self._acquire_cancellable(
            self.resources.acquire_service(block_playback=block_playback),
            job_id,
            operation=operation,
            heartbeat_reason="service_resource_wait",
        )

    async def _acquire_transfer_gate_cancellable(self, job_id: str, transfer_gate, *,
                                                 operation: str = "Waiting for Telegram transfer capacity"):
        await self._acquire_cancellable(
            transfer_gate.__aenter__(),
            job_id,
            operation=operation,
            heartbeat_reason="transfer_gate_wait",
        )

    async def wait_if_paused(self, job_id: str):
        marked = False
        while (self.paused or job_id in self._paused_jobs) and not self.cancel_requested(job_id):
            if not marked:
                current = self.db.get_job(job_id) or {}
                current_status = str(current.get("status") or "").upper()
                # A concurrent Stop wins over Pause; never overwrite its
                # CANCEL_REQUESTED state while the checkpoint wakes up.
                if current_status == "CANCEL_REQUESTED" or self.cancel_requested(job_id):
                    break
                if current_status not in {"PAUSED", "PAUSING"}:
                    self.db.update_job(job_id, status="PAUSED", current_operation="Paused")
                marked = True
            await asyncio.sleep(0.5)
        if marked and not self.cancel_requested(job_id):
            current = self.db.get_job(job_id) or {}
            if str(current.get("status") or "").upper() in {"PAUSED", "PAUSING"}:
                self.db.update_job(job_id, status="RUNNING", current_operation="Resuming")


    # ------------------------------------------------------------------
    # SUBSECTION: Worker loops
    # Backup schedule, lifecycle watch and the worker.
    # ------------------------------------------------------------------
    async def _backup_schedule_loop(self):
        """Run one verified application backup at the configured local time."""
        from ..core.backup.manager import PENDING_BACKUP_PASSWORDS
        last_date = ""
        while not self.stopping:
            try:
                await asyncio.sleep(20)
                if not bool(self.db.setting("backup_enabled", False)) or self.is_active("BACKUP_CREATE"):
                    continue
                target = str(self.db.setting("backup_schedule_time", "02:00") or "02:00")
                now = datetime.now()
                if now.strftime("%H:%M") != target or last_date == now.date().isoformat():
                    continue
                jid = self.db.create_job("BACKUP_CREATE", "", "", False, detail=json.dumps({
                    "backup_type":"full_application", "repository":None,
                    "automatic_background": True,
                }))
                self.submit(jid)
                last_date = now.date().isoformat()
                self.db.log("Scheduled Archive X backup queued", "INFO")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.db.log(f"Backup scheduler error: {type(exc).__name__}: {exc}", "ERROR")

    async def _watch_job_lifecycle(self):
        """Reconcile jobs whose interactive runner has already exited.

        Telegram Media Manager owns a separate thread/event loop. A successful
        CLI completion can therefore happen a moment before the asyncio job
        coroutine returns. The watchdog closes that small lifecycle gap so the
        dashboard cannot remain RUNNING after the visible Media Manager says
        Finished, and a Stop request cannot remain CANCEL_REQUESTED after the
        runner has actually exited.
        """
        from ..media_manager.runner import RUNNER as mm_runner
        last_error = ""
        while not self.stopping:
            try:
                await asyncio.sleep(0.25)
                # Reconcile explicit Stop requests even if a handler misses its
                # normal checkpoint. Most jobs stop cooperatively; after a
                # generous grace period, cancel only the owned asyncio task (or
                # its isolated child process) and persist a terminal state.
                cancel_grace = max(5.0, float(getattr(config, "JOB_OPERATOR_CANCEL_GRACE_SECONDS", 20.0)))
                now_mono = time.monotonic()
                for cancel_job in self.db.active_jobs():
                    cancel_id = str(cancel_job.get("id") or "")
                    if str(cancel_job.get("status") or "").upper() != "CANCEL_REQUESTED" or not cancel_id:
                        continue
                    requested_at = self._cancel_requested_at.setdefault(cancel_id, now_mono)
                    task = self.running.get(cancel_id)
                    if task is None or task.done():
                        try:
                            latest = self.db.get_job(cancel_id) or {}
                            if str(latest.get("status") or "").upper() == "CANCEL_REQUESTED":
                                self.db.update_job(cancel_id, status="CANCELLED", worker_state="STOPPED", current_operation="Stopped")
                                self.db.log("Job stop reconciled after worker exit", "INFO", event="job.cancelled", job_id=cancel_id)
                        except Exception:
                            pass
                        self._cancel_requested_at.pop(cancel_id, None)
                        self._cancel_escalated.discard(cancel_id)
                        self._cancelled.discard(cancel_id)
                        self._paused_jobs.discard(cancel_id)
                        self._skip_current.discard(cancel_id)
                        continue
                    if now_mono - requested_at < cancel_grace or cancel_id in self._cancel_escalated:
                        continue
                    self._cancel_escalated.add(cancel_id)
                    process = self.isolated_processes.get(cancel_id)
                    if process is not None:
                        try:
                            if process.is_alive():
                                process.terminate()
                                process.join(timeout=1.0)
                                if process.is_alive() and hasattr(process, "kill"):
                                    process.kill()
                                    process.join(timeout=1.0)
                        except Exception as exc:
                            try:
                                self.db.log(f"Could not terminate stopped job worker {cancel_id}: {type(exc).__name__}: {exc}", "WARN", event="job.cancel_escalation_failed", job_id=cancel_id)
                            except Exception:
                                pass
                    self.db.log(
                        f"Job did not stop cooperatively within {cancel_grace:.0f}s; escalating cancellation",
                        "WARN", event="job.cancel_escalated", job_id=cancel_id,
                        details={"job_type": cancel_job.get("job_type"), "grace_seconds": cancel_grace},
                    )
                    if not task.done():
                        task.cancel()
                # Durable execution leases are independent from the in-memory
                # watchdog. Refresh them from the live worker map and classify
                # expired leases as interrupted work for recovery.
                for jid, execution in list(self._execution_context.items()):
                    try:
                        if jid in self.running and not self.running[jid].done():
                            self.orchestrator.heartbeat_execution(jid, execution["execution_id"], "RUNNING")
                    except Exception:
                        pass
                for expired_id in self.orchestrator.recover_expired_leases():
                    task = self.running.get(expired_id)
                    if task is not None and not task.done():
                        task.cancel()
                for job in self.db.active_jobs():
                    if job.get("job_type") not in {"MEDIA_MANAGER", "TELEGRAM_TRANSFER"}:
                        continue
                    jid = str(job.get("id") or "")
                    if not jid or jid != str(mm_runner.last_job_id or ""):
                        continue
                    if mm_runner.running:
                        continue
                    status = str(job.get("status") or "")
                    if status not in {"RUNNING", "CANCEL_REQUESTED", "PAUSING"} and jid not in self.running:
                        continue
                    if status == "CANCEL_REQUESTED" or self.cancel_requested(jid):
                        self.db.update_job(jid, status="CANCELLED", current_operation="Stopped")
                        self._cancelled.discard(jid)
                        continue
                    if mm_runner.error:
                        self.db.update_job(jid, status="FAILED", error=str(mm_runner.error)[:1000], current_operation="Media Manager failed")
                    elif mm_runner.completed is not None or mm_runner.seq:
                        self.db.update_job(jid, status="COMPLETED", progress=100, current_operation="Completed")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # The normal job coroutine remains authoritative; the watchdog
                # is only a reconciliation path. Log each distinct failure once
                # instead of silently retrying every 0.5 s forever.
                error = f"{type(exc).__name__}: {exc}"
                if error != last_error:
                    last_error = error
                    self.db.log(f"Job lifecycle reconciliation failed: {error}", "WARN")
                continue

    async def _worker(self):
        """Main queue loop: admit each job or requeue it, then spawn it."""
        while True:
            _priority, _seq, jid = await self.queue.get()
            try:
                self._queued_ids.discard(jid)
                current = self.db.get_job(jid)
                # A queued item can be paused before the worker sees it. Do not
                # start it behind the operator's back; Resume will enqueue it
                # again explicitly.
                if not current or current.get("status") != "QUEUED":
                    continue
                admitted, reason = self.orchestrator.prepare(jid)
                if not admitted:
                    # Capacity/dependency admissions are transient. Requeue
                    # these jobs so a slot becoming available always wakes the
                    # durable queue; previously several WAITING reasons were
                    # persisted but never put back on the in-memory queue.
                    transient_reasons = {
                        "lane_busy", "lane_capacity", "lane_draining", "delayed",
                        "resource", "dependency",
                    }
                    if reason in transient_reasons and not self.stopping:
                        current_wait = self.db.get_job(jid) or {}
                        delay = 0.5 if reason in {"resource", "lane_capacity", "lane_busy"} else 1.0
                        if reason == "delayed" or str(current_wait.get("admission_state") or "").upper() == "DELAYED":
                            if current_wait.get("next_run_at"):
                                try:
                                    from datetime import datetime, timezone
                                    target = datetime.fromisoformat(str(current_wait["next_run_at"]).replace("Z", "+00:00"))
                                    if target.tzinfo is None:
                                        target = target.replace(tzinfo=timezone.utc)
                                    delay = max(0.1, min(5.0, (target - datetime.now(timezone.utc)).total_seconds()))
                                except (TypeError, ValueError):
                                    delay = 1.0
                        _retain_task(asyncio.create_task(self._requeue_after(jid, delay), name=f"job-requeue-{jid}"))
                    continue
                if jid not in self.running:
                    task = asyncio.create_task(self._run_job(jid), name=f"job-{jid}")
                    self.running[jid] = task
                    task.add_done_callback(lambda t, j=jid: self.running.pop(j, None))
            finally:
                self.queue.task_done()


    # ------------------------------------------------------------------
    # SUBSECTION: Job execution
    # Runs a job and computes its outcome.
    # ------------------------------------------------------------------
    async def _run_job(self, jid: str):
        """Acquire execution lanes for a job and run it under them."""
        job = self.db.get_job(jid)
        if not job or job["status"] in ("COMPLETED", "COMPLETED_WITH_ERRORS", "CANCELLED", "FAILED"):
            return
        # Pause may win the small race between queue admission and task startup.
        # In that case leave the job durably PAUSED instead of letting the
        # runner overwrite it to RUNNING before its first engine checkpoint.
        initial_status = str(job.get("status") or "").upper()
        if jid in self._paused_jobs and initial_status in {"QUEUED", "PAUSING", "PAUSED"}:
            if initial_status != "PAUSED":
                self.db.update_job(jid, status="PAUSED", current_operation="Paused before start")
            return
        name = JOB_NAMES.get(job["job_type"], job["job_type"])
        if job.get("job_type") in MANUAL_ONLY_JOBS and not _job_is_manual(job):
            self.db.update_job(jid, status="CANCELLED", current_operation="Automatic entity data sync blocked — manual start required")
            self.db.log(f"{JOB_NAMES.get(job.get('job_type'), job.get('job_type'))} blocked: manual start required", event="job.blocked", job_id=jid)
            return
        if jid in self._cancelled:
            self.db.update_job(jid, status="CANCELLED", detail="Cancelled before start")
            self._cancelled.discard(jid)
            self._paused_jobs.discard(jid)
            self._skip_current.discard(jid)
            return
        handler = ENGINES.get(job["job_type"])
        if handler is None:
            self.db.update_job(jid, status="CANCELLED", detail="Module was removed")
            return
        lock_keys = sorted(set(_execution_keys(job)))
        locks = []
        acquired_locks = []
        try:
            current = self.db.get_job(jid) or {}
            if self.cancel_requested(jid) or current.get("status") == "CANCELLED":
                self.db.update_job(jid, status="CANCELLED", current_operation="Cancelled")
                self._cancelled.discard(jid)
                self._paused_jobs.discard(jid)
                self._skip_current.discard(jid)
                return
            execution = self.orchestrator.start_execution(jid)
            lane_keys = self.execution_lane_keys(job)
            if not self.db.acquire_execution_lane_slots(jid, execution["execution_id"], lane_keys, capacities=self.execution_lane_capacities(lane_keys), lease_id=execution.get("lease_id"), lease_seconds=getattr(config, "EXECUTION_LANE_LEASE_SECONDS",45)):
                self.orchestrator.finish_execution(jid, execution["execution_id"], status="FAILED", failure_class="RESOURCE")
                self.db.set_job_admission(jid, "WAITING", reason="EXECUTION_LANE_CAPACITY", operation="Waiting for execution lane capacity")
                if not self.stopping:
                    _retain_task(asyncio.create_task(self._requeue_after(jid, 0.5), name=f"job-lane-requeue-{jid}"))
                return
            execution["lane_keys"] = lane_keys
            self._execution_context[jid] = execution
            await self._run_locked(jid, self.db.get_job(jid) or job, name, handler)
        finally:
            execution = self._execution_context.pop(jid, None)
            if execution is not None:
                try:
                    latest = self.db.get_job(jid) or {}
                    final_status = latest.get("status") or "FAILED"
                    # QUEUED/DELAYED belongs to the next job attempt. The
                    # execution that just ended must retain its terminal outcome.
                    execution_status = final_status
                    if final_status in {"QUEUED", "DELAYED"}:
                        execution_status = "FAILED" if latest.get("failure_class") else "INTERRUPTED"
                    self.orchestrator.finish_execution(jid, execution["execution_id"], status=execution_status, failure_class=latest.get("failure_class") or "")
                except Exception:
                    pass
                try:
                    self.db.release_execution_lane_slots(execution_id=execution.get("execution_id"))
                except Exception:
                    pass

    def _completion_outcome(self, jid: str) -> tuple[str, dict, float, str]:
        """V29.4.133: status, outcome, final percentage and summary of a
        finished job, from what it verified -- never a forced 100%."""
        status, outcome = self._metadata_pipeline_completion_status(jid)
        job = self.db.get_job(jid) or {}
        progress, summary = 100.0, ""
        if str(job.get("job_type") or "") == "METADATA_CHANNEL_PIPELINE":
            try:
                detail = json.loads(job.get("detail") or "{}")
                p = (detail.get("progress") or {}).get("overall_progress")
                if isinstance(p, (int, float)):
                    progress = float(p)
            except Exception:
                pass
        else:
            from ..core import job_results
            result = job_results.read(self.db, jid) or {}
            if result.get("status") == "COMPLETED_WITH_ERRORS":
                status = "COMPLETED_WITH_ERRORS"
            if isinstance(result.get("progress"), (int, float)):
                progress = float(result["progress"])
            summary = str(result.get("summary") or "")
            if summary:
                outcome = dict(outcome, summary=summary)
        return status, outcome, round(max(0.0, min(100.0, progress)), 1), summary

    def _metadata_pipeline_completion_status(self, jid: str) -> tuple[str, dict]:
        """Derive the terminal outcome from the Metadata & Assets checkpoint."""
        job = self.db.get_job(jid) or {}
        if str(job.get("job_type") or "") != "METADATA_CHANNEL_PIPELINE":
            return "COMPLETED", {}
        try:
            detail = json.loads(job.get("detail") or "{}") if isinstance(job.get("detail"), str) else dict(job.get("detail") or {})
        except Exception:
            detail = {}
        channels = detail.get("channel_queue") if isinstance(detail.get("channel_queue"), list) else []
        failed_channels = []
        failed_phases = []
        for row in channels:
            if not isinstance(row, dict):
                continue
            cid = str(row.get("channel_id") or "")
            # V29.4.132: INCOMPLETE = the phase ran but its results are missing.
            if str(row.get("state") or "").upper() in {"FAILED", "INCOMPLETE", "COMPLETED_WITH_ERRORS"}:
                failed_channels.append(cid)
            phases = row.get("phases") if isinstance(row.get("phases"), dict) else {}
            failed_phases.extend(
                f"{cid}:{phase}" for phase, state in phases.items()
                if str(state or "").upper() in {"FAILED", "INCOMPLETE", "COMPLETED_WITH_ERRORS"}
            )
        outcome = {
            "failed_channels": failed_channels,
            "failed_phases": failed_phases,
            "channel_count": len(channels),
        }
        return ("COMPLETED_WITH_ERRORS" if failed_channels or failed_phases else "COMPLETED"), outcome

    async def _run_locked(self, jid, job, name, handler):
        operation_id = f"job-{jid}"
        with operation_context(operation_id=operation_id, job_id=jid, component=f"job.{str(job.get('job_type') or 'DEFAULT').lower()}"):
            return await self._run_locked_impl(jid, job, name, handler)

    async def _run_locked_impl(self, jid, job, name, handler):
        """Run a job handler, either in-process or isolated, and record the outcome."""
        # A pause can arrive while the job is acquiring its execution lane. Wait
        # before entering the engine so expensive startup work does not continue
        # after the operator has asked to pause it.
        initial = self.db.get_job(jid) or {}
        if jid in self._cancelled or str(initial.get("status") or "").upper() == "CANCEL_REQUESTED":
            if str(initial.get("status") or "").upper() not in {"CANCELLED", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"}:
                self.db.update_job(jid, status="CANCELLED", current_operation="Stopped before operation start")
            self._cancelled.discard(jid)
            self._paused_jobs.discard(jid)
            self._skip_current.discard(jid)
            self._cancel_requested_at.pop(jid, None)
            self._cancel_escalated.discard(jid)
            return
        if self.paused or jid in self._paused_jobs:
            if str(initial.get("status") or "").upper() in {"QUEUED", "RUNNING", "PAUSING"}:
                self.db.update_job(jid, status="PAUSED", current_operation="Paused before operation start")
            await self.wait_if_paused(jid)
            latest = self.db.get_job(jid) or {}
            latest_status = str(latest.get("status") or "").upper()
            if jid in self._cancelled or latest_status == "CANCEL_REQUESTED":
                if latest_status not in {"CANCELLED", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"}:
                    self.db.update_job(jid, status="CANCELLED", current_operation="Stopped while paused")
                self._cancelled.discard(jid)
                self._paused_jobs.discard(jid)
                self._skip_current.discard(jid)
                self._cancel_requested_at.pop(jid, None)
                self._cancel_escalated.discard(jid)
                return
            if self.stopping:
                # Application shutdown is not an operator Stop. Preserve the
                # paused/checkpoint state so startup recovery can classify it.
                return
        current_attempt = int((self.db.get_job(jid) or {}).get("attempt") or 0)
        self.db.update_job(jid, status="RUNNING", current_operation="Starting", admission_state="READY", admission_reason="", next_run_at=None, worker_state="EXECUTING")
        self.recovery.record(jid, "JOB_STARTED", status="RUNNING", checkpoint=self.recovery._checkpoint_for(job), operation="Starting")
        self.db.log(f"{name} started", event="job.started", job_id=jid,
                     details={"job_type": job.get("job_type"), "source": job.get("source_channel"),
                              "destination": job.get("destination_channel"), "destructive": bool(job.get("destructive"))})
        # Every job owns a private bounded pipeline. Blocking/native-adjacent
        # work is routed through that pipeline so one module cannot consume
        # the application's shared executor and starve unrelated modules.
        # The pipeline is disposed even when the job is cancelled or fails.
        pipeline = self.pipelines.create(jid, max_workers=2)
        # The manager owns the job's watchdog clock: start it fresh for this
        # run. JobContext (and sub-contexts) only attach to the existing entry.
        self.watchdogs.register_job(jid, str(job.get("job_type") or "DEFAULT"), replace=True)
        ctx = JobContext(jid, job, self.db, self.telegram, self.governor, self)
        background_slot = False
        background_category = "generic"
        service_slot = False
        transfer_gate = None
        try:
            is_isolated = job.get("job_type") in ISOLATED_JOB_ENGINES
            if self.resources is not None:
                is_service = job.get("job_type") == "CONTENT_AUTOMATION"
                if is_service:
                    # Torrent/download service stays alive during playback; the
                    # content-automation Telegram delivery stage still enters
                    # the hard transfer gate separately.
                    wait_op = PLAYBACK_WAIT_OPERATION if self.resources.playback_active else "Starting"
                    await self._acquire_service_cancellable(
                        jid,
                        operation=("Starting download service" if self.resources.playback_active else wait_op),
                        block_playback=False,
                    )
                    service_slot = True
                else:
                    # Take an aggregate Telegram transfer permit before occupying
                    # a shared CPU/disk worker. A transfer queued behind the global
                    # upload limit must not monopolize the worker needed by the
                    # Metadata & Assets scan (and vice versa).
                    if self.governor is not None and job.get("job_type") in TRANSFER_FAMILY:
                        transfer_gate = self.governor.telegram_transfer_slot()
                        await self._acquire_transfer_gate_cancellable(
                            jid, transfer_gate,
                            operation="Waiting for shared Telegram transfer capacity",
                        )
                    background_category = job_resource_category(job)
                    playback_wait = (
                        self.resources.playback_active
                        and self.resources.playback_blocks_category(background_category)
                    )
                    wait_op = PLAYBACK_WAIT_OPERATION if playback_wait else (
                        "Waiting for reserved metadata worker capacity"
                        if background_category == "metadata"
                        else "Waiting for shared CPU/disk worker capacity"
                    )
                    await self._acquire_background_cancellable(jid, category=background_category, operation=wait_op)
                    background_slot = True
                self.db.update_job(jid, current_operation=("Starting isolated background worker" if is_isolated else "Starting"))
            if self.resources is None and self.governor is not None and job.get("job_type") in TRANSFER_FAMILY:
                transfer_gate = self.governor.telegram_transfer_slot()
                await self._acquire_transfer_gate_cancellable(
                    jid,
                    transfer_gate,
                    operation="Waiting for Telegram transfer capacity",
                )
            if is_isolated:
                process, result_queue = start_isolated_process(jid, job, self.db.path)
                self.isolated_processes[jid] = process
                self.db.update_job(jid, current_operation="Running in isolated background worker")
                try:
                    while process.is_alive():
                        # The child process is the actual unit of execution.
                        # Keep the existing watchdog and durable execution lease
                        # clocks aligned while the parent waits for it.
                        if self.watchdogs is not None:
                            self.watchdogs.heartbeat(jid, progress=False, activity=True, reason="isolated_worker_alive", metadata={"pid": getattr(process, "pid", None)})
                        if self.cancel_requested(jid):
                            self.watchdogs.heartbeat(jid, progress=False, activity=True, reason="isolated_worker_terminating", metadata={"pid": getattr(process, "pid", None)})
                            process.terminate()
                            break
                        await asyncio.sleep(0.1)
                    process.join(timeout=1.0)
                    result = None
                    try:
                        result = result_queue.get_nowait()
                    except Exception:
                        result = None
                    if self.cancel_requested(jid):
                        self.db.update_job(jid, status="CANCELLED", current_operation="Stopped")
                        self.db.log(f"{name} stopped", event="job.cancelled", job_id=jid)
                    elif result and result.get("ok"):
                        final_status, outcome, final_progress, summary = self._completion_outcome(jid)
                        operation = "Completed with errors" if final_status == "COMPLETED_WITH_ERRORS" else "Completed"
                        if summary:
                            operation = f"{operation} · {summary}"
                        level = "WARN" if final_status == "COMPLETED_WITH_ERRORS" else "SUCCESS"
                        event = "job.completed_with_errors" if final_status == "COMPLETED_WITH_ERRORS" else "job.completed"
                        self.db.update_job(jid, status=final_status, progress=final_progress, current_operation=operation)
                        self.db.log(f"{name} {operation.lower()}", level, event=event, job_id=jid, details=outcome)
                    elif result and result.get("skipped"):
                        self.db.update_job(jid, status="RUNNING", current_operation="Skipped current queue item")
                        self.db.log(f"{name} skipped current queue item", event="job.skipped_current", job_id=jid)
                    elif result and result.get("cancelled"):
                        self.db.update_job(jid, status="CANCELLED", current_operation="Stopped")
                        self.db.log(f"{name} stopped", event="job.cancelled", job_id=jid)
                    else:
                        error = (result or {}).get("error") or f"isolated worker exited with code {process.exitcode}"
                        self.db.update_job(jid, status="FAILED", error=str(error)[:1000], current_operation="Isolated worker failed")
                        self.db.log_exception(
                            f"{name} failed in isolated worker: {error}",
                            RuntimeError(str(error)),
                            event="job.failed", job_id=jid,
                            operation=(self.db.get_job(jid) or {}).get("current_operation"),
                            details={"job_type": job.get("job_type"), "worker_pid": process.pid, "exitcode": process.exitcode},
                        )
                finally:
                    self.isolated_processes.pop(jid, None)
                    try:
                        result_queue.close()
                        result_queue.join_thread()
                    except Exception:
                        pass
            else:
                # Automatic jobs enter the same application-wide priority lane.
                # Explicit operator jobs keep their existing execution semantics;
                # their feature-level operation stages use the same gate only
                # when the selected operation itself is resource-heavy.
                idem_key = self.recovery.idempotency_key(jid, str(job.get("job_type") or "JOB"), str(job.get("current_item") or ""))
                self.recovery.begin_idempotent(idem_key, jid, str(job.get("current_operation") or job.get("job_type") or "JOB"))
                stage = self.background_priority.job_stage(job)
                if stage:
                    self.db.update_job(jid, current_operation=f"Waiting for background priority · {stage}")
                    async with self.background_priority.slot(stage):
                        await pipeline.run(lambda: handler(ctx))
                else:
                    await pipeline.run(lambda: handler(ctx))
                self.recovery.complete_idempotent(idem_key, {"job_id": jid, "status": "completed"})
                # Cancellation can arrive just after an interactive worker has
                # finished but before control returns to this coroutine. Never
                # overwrite that cancellation with COMPLETED.
                if self.cancel_requested(jid):
                    self.db.update_job(jid, status="CANCELLED", current_operation="Stopped")
                    self.db.log(f"{name} stopped", event="job.cancelled", job_id=jid)
                else:
                    final_status, outcome, final_progress, summary = self._completion_outcome(jid)
                    operation = "Completed with errors" if final_status == "COMPLETED_WITH_ERRORS" else "Completed"
                    if summary:
                        operation = f"{operation} · {summary}"
                    level = "WARN" if final_status == "COMPLETED_WITH_ERRORS" else "SUCCESS"
                    event = "job.completed_with_errors" if final_status == "COMPLETED_WITH_ERRORS" else "job.completed"
                    journal_event = "JOB_COMPLETED_WITH_ERRORS" if final_status == "COMPLETED_WITH_ERRORS" else "JOB_COMPLETED"
                    self.db.update_job(jid, status=final_status, progress=final_progress, current_operation=operation)
                    self.recovery.record(jid, journal_event, status=final_status, checkpoint=self.recovery._checkpoint_for(self.db.get_job(jid) or {}), operation=operation, detail=json.dumps(outcome, ensure_ascii=False, separators=(",", ":")))
                    self.db.log(f"{name} {operation.lower()}", level, event=event, job_id=jid, details=outcome)
        except JobSkipped:
            self.db.update_job(jid, status="RUNNING", current_operation="Skipped current item")
            self.db.log(f"{name} skipped current queue item", event="job.skipped_current", job_id=jid)
        except JobCancelled:
            self.db.update_job(jid, status="CANCELLED", current_operation="Stopped")
            self.db.log(f"{name} stopped", event="job.cancelled", job_id=jid)
        except asyncio.CancelledError:
            raise   # app shutting down: stays RUNNING and resumes next start
        except Exception as e:
            retry, failure_class, delay = self.orchestrator.schedule_failure(jid, e)
            if retry:
                self.recovery.record(jid, "JOB_RETRY_SCHEDULED", status="QUEUED", checkpoint=self.recovery._checkpoint_for(self.db.get_job(jid) or {}), operation=(self.db.get_job(jid) or {}).get("current_operation"), detail=f"{failure_class}; retry in {delay:.1f}s")
                self.db.log_exception(f"{name} retry scheduled after {failure_class}: {e}", e, level="WARN", event="job.retry_scheduled", job_id=jid, operation=(self.db.get_job(jid) or {}).get("current_operation"), details={"job_type":job.get("job_type"),"retry_count":(self.db.get_job(jid) or {}).get("retry_count"),"failure_class":failure_class,"delay":delay})
                self._enqueue_job(jid, reset_admission=False)
            else:
                self.db.update_job(jid, status="FAILED", error=str(e)[:1000], failure_class=failure_class, worker_state="EXITED")
                self.recovery.record(jid, "JOB_FAILED", status="FAILED", checkpoint=self.recovery._checkpoint_for(self.db.get_job(jid) or {}), operation=(self.db.get_job(jid) or {}).get("current_operation"), detail=str(e)[:1000])
                pipe_state = pipeline.snapshot()
                self.db.log_exception(f"{name} failed: {e}", e, event="job.failed", job_id=jid, operation=(self.db.get_job(jid) or {}).get("current_operation"), details={"job_type": job.get("job_type"), "pipeline": pipe_state, "failure_class":failure_class})
        finally:
            # Cancellation of an asyncio wrapper cannot forcibly terminate a
            # Python worker thread. Give the job-owned pipeline a bounded drain
            # window before releasing execution/resource ownership, preventing a
            # cancelled blocking operation from racing a later retry.
            try:
                drained = await pipeline.wait_idle(timeout=float(getattr(config, "JOB_PIPELINE_CANCEL_DRAIN_SECONDS", 5.0)))
                if not drained:
                    self.db.update_job(jid, worker_state="STOPPING", current_operation="Waiting for cancelled blocking operation to exit")
                    self.db.log(f"{name} cancellation drain exceeded grace period", "WARN", event="job.blocking_operation_drain_timeout", job_id=jid)
            except Exception:
                pass
            if transfer_gate is not None:
                try:
                    await transfer_gate.__aexit__(None, None, None)
                except Exception:
                    pass
            if background_slot and self.resources is not None:
                await self.resources.release_background(background_category)
            if service_slot and self.resources is not None:
                await self.resources.release_service()
            self.pipelines.remove(jid)
            self.watchdogs.unregister_job(jid)
            self._cancelled.discard(jid)
            self._paused_jobs.discard(jid)
            self._skip_current.discard(jid)
            latest_status = str((self.db.get_job(jid) or {}).get("status") or "").upper()
            if latest_status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}:
                self._cancel_requested_at.pop(jid, None)
                self._cancel_escalated.discard(jid)

    def running_job(self):
        jobs = self.db.active_jobs()
        return next((j for j in jobs if j["status"] in ("RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED")), None)


def merge_metadata_detail(keep: dict, others) -> str:
    """Union of channels / largest limit / any force, for coalesced jobs."""
    import json

    def parse(j):
        try:
            d = json.loads(j.get("detail") or "{}")
        except Exception:
            d = {}
        chans = [str(x) for x in (d.get("source_channels") or []) if str(x)]
        if j.get("source_channel") and str(j["source_channel"]) not in chans:
            chans.append(str(j["source_channel"]))
        return d, chans

    base, chans = parse(keep)
    unbounded = bool(base.get("unbounded", False))
    limit = int(base.get("limit", 50) or 50)
    force = bool(base.get("force"))
    for o in others:
        d, c = parse(o)
        chans += [x for x in c if x not in chans]
        ol = int(d.get("limit", 50) or 50)
        unbounded = unbounded or bool(d.get("unbounded", False))
        if not unbounded:
            limit = max(limit, ol)
        force = force or bool(d.get("force"))
    return json.dumps({"limit": 0 if unbounded else limit, "unbounded": unbounded, "force": force, "source_channels": chans})
