"""Application watchdogs and liveness tracking.

The watchdog layer is deliberately diagnostic-first.  It distinguishes:
- heartbeat/liveness: the component is still servicing its control loop;
- progress: useful work is advancing;
- dependency health: an external service/process is responsive.

Recovery is conservative: watchdogs may request a safe stop or mark an
orphaned isolated process failed, but they never auto-resume persisted jobs.
This avoids the false-positive restart loops common in naive watchdogs.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Policy and registry
#   - Event loop stall guard
#   - WatchdogManager
#       - Lifecycle and job registration
#       - Job observation
#       - Loop beat and snapshot
#       - Guardian process
#       - Independent enforcement and recovery
#       - Evaluation loop
# ------------------------------------------------------------------
from __future__ import annotations
from .swallowed import note as _swallowed_note
from .background_tasks import retain as _retain_task

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


HEALTHY = "HEALTHY"
DEGRADED = "DEGRADED"
SUSPECTED = "SUSPECTED"
STALLED = "STALLED"
WAITING = "WAITING"
FAILED = "FAILED"
RECOVERING = "RECOVERING"
UNKNOWN = "UNKNOWN"




# ==============================================================================
# SECTION: Policy and registry
# Watchdog policy, entries and the liveness registry.
# ==============================================================================

@dataclass(frozen=True)
class WatchdogPolicy:
    """Timing thresholds for one watchdog."""
    heartbeat_warn: float = 15.0
    heartbeat_timeout: float = 45.0
    progress_warn: float = 90.0
    progress_timeout: float = 180.0
    suspect_after: float = 120.0
    recovery_timeout: float = 600.0
    startup_grace: float = 15.0


# All running job types share the same configurable minimum recovery grace.
# Older releases gave most jobs only five seconds while transfer/automation jobs
# got a minute; that asymmetry could cancel healthy metadata, snapshot, image,
# or backup work while a child/native operation was briefly quiet.
DEFAULT_UNRECOVERED_GRACE_SECONDS = 60.0
# Legacy import alias retained for integrations/tests from earlier releases.
ONE_MINUTE_UNRECOVERED_GRACE_SECONDS = DEFAULT_UNRECOVERED_GRACE_SECONDS


def _unrecovered_grace_seconds(job: dict[str, Any] | None = None) -> float:
    """Return the shared recovery grace for every job family.

    ``job`` remains accepted for source compatibility with older callers, but
    the grace is deliberately not narrowed based on the job type. The shipped
    default is 60 seconds; deployments may raise it for slower hosts.
    """
    from .. import config
    return max(1.0, float(getattr(config, "WATCHDOG_UNRECOVERED_GRACE_SECONDS", DEFAULT_UNRECOVERED_GRACE_SECONDS)))


DEFAULT_POLICIES: dict[str, WatchdogPolicy] = {
    "DEFAULT": WatchdogPolicy(),
    # Backups can spend substantial time copying/verifying large asset trees.
    # Keep the heartbeat cadence strict but allow long, well-observed progress.
    "BACKUP_CREATE": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300,
                                    progress_timeout=1800, suspect_after=300, recovery_timeout=3600,
                                    startup_grace=20),
    "TELEGRAM_TRANSFER": WatchdogPolicy(heartbeat_warn=20, heartbeat_timeout=60, progress_warn=120, progress_timeout=300, recovery_timeout=900),
    "MEDIA_MANAGER": WatchdogPolicy(heartbeat_warn=20, heartbeat_timeout=60, progress_warn=120, progress_timeout=300, recovery_timeout=900),
    "UPLOAD_LOCAL": WatchdogPolicy(heartbeat_warn=20, heartbeat_timeout=90, progress_warn=180, progress_timeout=600, recovery_timeout=1200),
    "CONTENT_AUTOMATION": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=900, recovery_timeout=1800),
    "METADATA_ASSETS": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=900, recovery_timeout=1800),
    "METADATA_SYNC": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=900, recovery_timeout=1800),
    "SUGGESTION_SCAN": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=900, recovery_timeout=1800),
    "ENTITY_IMAGE_SYNC": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=900, recovery_timeout=1800),
    "THUMBNAIL_INDEXER": WatchdogPolicy(heartbeat_warn=20, heartbeat_timeout=90, progress_warn=180, progress_timeout=900, recovery_timeout=1500),
    "METADATA_CHANNEL_PIPELINE": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=1800, recovery_timeout=2400),
    "ASSET_PREFETCH": WatchdogPolicy(heartbeat_warn=20, heartbeat_timeout=90, progress_warn=180, progress_timeout=600, recovery_timeout=1200),
    "PREVIEW_PREFETCH": WatchdogPolicy(heartbeat_warn=30, heartbeat_timeout=120, progress_warn=300, progress_timeout=1200, recovery_timeout=1800),
}


@dataclass
class WatchdogEntry:
    """Liveness record for one watched item."""
    name: str
    kind: str
    policy: WatchdogPolicy
    started_at: float = field(default_factory=time.monotonic)
    last_heartbeat: float = field(default_factory=time.monotonic)
    last_progress: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    heartbeat_count: int = 0
    progress_count: int = 0
    consecutive_failures: int = 0
    state: str = UNKNOWN
    last_reason: str = "starting"
    last_action: str = ""
    last_action_at: float = 0.0
    recovery_generation: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


# Keep the public vocabulary simple; STARTING is represented as UNKNOWN with a
# startup grace period so clients don't need another state.


class WatchdogRegistry:
    """Thread-safe registry of liveness/progress clocks."""

    def __init__(self):
        self._items: dict[str, WatchdogEntry] = {}
        self._lock = threading.RLock()

    def register(self, name: str, *, kind: str = "component", policy: Optional[WatchdogPolicy] = None,
                 metadata: Optional[dict[str, Any]] = None) -> WatchdogEntry:
        with self._lock:
            entry = WatchdogEntry(name=name, kind=kind, policy=policy or DEFAULT_POLICIES["DEFAULT"],
                                  metadata=dict(metadata or {}))
            self._items[name] = entry
            return entry

    def unregister(self, name: str) -> None:
        with self._lock:
            self._items.pop(name, None)

    def heartbeat(self, name: str, *, progress: bool = False, activity: bool = False, reason: str = "heartbeat",
                  metadata: Optional[dict[str, Any]] = None) -> None:
        now = time.monotonic()
        with self._lock:
            item = self._items.get(name)
            if item is None:
                item = self.register(name, kind="component")
            item.last_heartbeat = now
            item.heartbeat_count += 1
            item.last_reason = reason
            item.consecutive_failures = 0
            if progress:
                item.last_progress = now
                item.last_activity = now
                item.progress_count += 1
            elif activity:
                item.last_activity = now
            if metadata:
                item.metadata.update(metadata)
            recovery_verified = bool(metadata and metadata.get("recovery_verified"))
            if item.state in {DEGRADED, STALLED, UNKNOWN} or (item.state == RECOVERING and recovery_verified):
                item.state = HEALTHY

    def progress(self, name: str, *, reason: str = "progress", metadata: Optional[dict[str, Any]] = None) -> None:
        self.heartbeat(name, progress=True, activity=True, reason=reason, metadata=metadata)

    def fail(self, name: str, reason: str, *, metadata: Optional[dict[str, Any]] = None) -> None:
        with self._lock:
            item = self._items.get(name)
            if item is None:
                item = self.register(name, kind="component")
            item.consecutive_failures += 1
            item.last_reason = str(reason)[:300]
            item.state = FAILED
            if metadata:
                item.metadata.update(metadata)

    def mark_action(self, name: str, action: str) -> None:
        with self._lock:
            item = self._items.get(name)
            if item:
                item.last_action = str(action)[:200]
                item.last_action_at = time.monotonic()
                item.recovery_generation += 1
                item.state = RECOVERING

    def get(self, name: str) -> Optional[WatchdogEntry]:
        with self._lock:
            return self._items.get(name)

    def evaluate(self, name: str, *, now: Optional[float] = None, active_blocking_age: Optional[float] = None,
                 waiting: bool = False) -> dict[str, Any]:
        """Classify an item's health from its heartbeat and progress ages."""
        now = time.monotonic() if now is None else now
        with self._lock:
            item = self._items.get(name)
            if item is None:
                return {"name": name, "state": UNKNOWN, "reason": "not_registered"}
            age_hb = max(0.0, now - item.last_heartbeat)
            age_prog = max(0.0, now - item.last_progress)
            age_activity = max(0.0, now - item.last_activity)
            since_start = max(0.0, now - item.started_at)
            state = item.state
            reason = item.last_reason
            p = item.policy
            if state in {FAILED, RECOVERING}:
                return {
                    "name": item.name, "kind": item.kind, "state": state, "reason": reason,
                    "heartbeat_age": round(age_hb, 2), "progress_age": round(age_prog, 2),
                    "activity_age": round(age_activity, 2), "heartbeat_count": item.heartbeat_count,
                    "progress_count": item.progress_count, "consecutive_failures": item.consecutive_failures,
                    "last_action": item.last_action,
                    "last_action_age": round(max(0.0, now - item.last_action_at), 2) if item.last_action_at else None,
                    "recovery_generation": item.recovery_generation, "metadata": dict(item.metadata),
                }
            if since_start < p.startup_grace:
                state = UNKNOWN
                reason = "startup_grace"
            elif waiting:
                state = WAITING if age_hb <= p.heartbeat_timeout else STALLED
                reason = "waiting" if state == WAITING else "heartbeat_timeout_while_waiting"
            elif active_blocking_age is not None and 0.0 <= active_blocking_age < p.recovery_timeout:
                # A worker currently executing a bounded blocking call is real
                # liveness evidence, even if the event loop cannot emit ordinary
                # progress events during native decode/hash/IO. Its age remains
                # visible in diagnostics and the per-job recovery_timeout is the
                # hard ceiling; this is not an indefinite keep-alive.
                state = HEALTHY
                reason = "blocking_worker_alive_within_deadline"
            elif age_hb >= p.heartbeat_timeout:
                state = STALLED
                reason = "heartbeat_timeout"
            elif age_activity >= p.suspect_after or age_prog >= p.progress_timeout:
                if age_prog >= p.progress_timeout or age_activity >= p.recovery_timeout:
                    state = STALLED
                    reason = "progress_timeout" if age_prog >= p.progress_timeout else "activity_timeout"
                else:
                    state = SUSPECTED
                    reason = "no_activity"
            elif age_hb >= p.heartbeat_warn or age_activity >= p.progress_warn:
                state = DEGRADED
                reason = "heartbeat_lag" if age_hb >= p.heartbeat_warn else "activity_lag"
            else:
                state = HEALTHY
                reason = "ok"
            item.state = state
            item.last_reason = reason
            return {
                "name": item.name,
                "kind": item.kind,
                "state": state,
                "reason": reason,
                "heartbeat_age": round(age_hb, 2),
                "progress_age": round(age_prog, 2),
                "activity_age": round(age_activity, 2),
                "heartbeat_count": item.heartbeat_count,
                "progress_count": item.progress_count,
                "consecutive_failures": item.consecutive_failures,
                "last_action": item.last_action,
                "last_action_age": round(max(0.0, now - item.last_action_at), 2) if item.last_action_at else None,
                "recovery_generation": item.recovery_generation,
                "metadata": dict(item.metadata),
            }

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            names = list(self._items)
        return [self.evaluate(name) for name in names]




# ==============================================================================
# SECTION: Event loop stall guard
# Detects and records asyncio event loop stalls.
# ==============================================================================

class EventLoopGuard:
    """Independent thread that detects a stalled asyncio event loop.

    An asyncio task cannot reliably detect that the event loop itself is frozen;
    the small daemon thread provides the independent clock needed for that case.
    """

    def __init__(self, *, check_interval: float = 2.0, stall_after: float = 8.0):
        self.check_interval = max(0.2, float(check_interval))
        self.stall_after = max(self.check_interval * 2, float(stall_after))
        self._last_tick = time.monotonic()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._stalled = False
        self._stall_count = 0
        self._loop_thread_id: Optional[int] = None
        self._stall_started: Optional[float] = None
        self._stall_dumped = False

    def beat(self) -> None:
        with self._lock:
            self._last_tick = time.monotonic()
            self._loop_thread_id = threading.get_ident()
            if self._stalled and self._stall_started is not None:
                self._record_stall_end(time.monotonic() - self._stall_started)
            self._stalled = False
            self._stall_started = None
            self._stall_dumped = False

    # V29.4.223: when the event loop freezes, record what it is doing so the
    # slow code can be found from the user's logs instead of guessed.
    def _stall_log_path(self):
        try:
            from .. import config
            return config.LOG_DIR / "event-loop-stalls.log"
        except Exception:
            return None

    def _write_stall(self, text: str) -> None:
        path = self._stall_log_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_file() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix(".old.log"))
            with path.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except Exception:
            _swallowed_note("watchdogs.EventLoopGuard._write_stall")
            pass

    def _dump_loop_stack(self, age: float) -> None:
        import traceback
        frame = sys._current_frames().get(getattr(self, "_loop_thread_id", None) or -1)
        stack = "".join(traceback.format_stack(frame)) if frame is not None else "(event-loop thread stack unavailable)\n"
        stamp = datetime.now(timezone.utc).isoformat()
        self._write_stall(f"=== {stamp} event loop frozen for {age:.1f}s; code running now:\n{stack[-6000:]}")

    def _record_stall_end(self, total: float) -> None:
        stamp = datetime.now(timezone.utc).isoformat()
        self._write_stall(f"--- {stamp} event loop recovered after {total:.1f}s")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="archive-x-eventloop-watchdog")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=2.0)
        self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self.check_interval):
            with self._lock:
                age = time.monotonic() - self._last_tick
                if age >= self.stall_after:
                    if not self._stalled:
                        self._stall_count += 1
                        self._stall_started = self._last_tick
                    self._stalled = True
                    if not self._stall_dumped:
                        self._stall_dumped = True
                        try:
                            self._dump_loop_stack(age)
                        except Exception:
                            _swallowed_note("watchdogs.EventLoopGuard._run")
                            pass

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            age = max(0.0, time.monotonic() - self._last_tick)
            return {
                "state": STALLED if self._stalled else HEALTHY,
                "last_tick_age": round(age, 2),
                "stall_count": self._stall_count,
                "stall_after": self.stall_after,
            }


# ==============================================================================
# SECTION: WatchdogManager
# Coordinates job liveness, the guardian process and recovery.
# ==============================================================================

class WatchdogManager:
    """Archive X job/process watchdog coordinator."""


    # ------------------------------------------------------------------
    # SUBSECTION: Lifecycle and job registration
    # Start, stop, job registration and heartbeats.
    # ------------------------------------------------------------------
    def __init__(self, job_manager):
        """Create the registry, loop guard and guardian bookkeeping."""
        self.manager = job_manager
        self.registry = WatchdogRegistry()
        self.loop_guard = EventLoopGuard()
        self._task = None
        self._stopping = False
        self._last_recovery: dict[str, float] = {}
        self._last_alert: dict[str, float] = {}
        # Continuous time in DEGRADED/SUSPECTED/STALLED/RECOVERING. A healthy
        # tick resets this clock; an unrecovered active job is stopped after the
        # short user-requested grace period rather than left hanging indefinitely.
        self._unhealthy_since: dict[str, float] = {}
        self._independent_unhealthy_since: dict[str, float] = {}
        self._independent_stop_requested: set[str] = set()
        self._independent_lock = threading.RLock()
        self._active_jobs_cache: dict[str, dict[str, Any]] = {}
        self._active_jobs_lock = threading.RLock()
        # Last observed child signature/activity id used to refresh a parent
        # progress clock only when the child actually changes or reports work.
        self._parent_child_observations: dict[str, tuple[Any, ...]] = {}
        self._loop = None
        self._loop_task = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()
        self._restart_pending = False
        self._last_task_recovery_monotonic = 0.0
        self._last_loop_heartbeat_monotonic = time.monotonic()
        self._last_loop_heartbeat_wall = time.time()
        self._last_supervisor_heartbeat_monotonic = time.monotonic()
        self._last_supervisor_heartbeat_wall = time.time()
        self._last_supervisor_success_monotonic = time.monotonic()
        self._last_supervisor_success_wall = time.time()
        self._supervisor_error_count = 0
        self._last_supervisor_error = ""
        self._independent_monitor_error_count = 0
        self._independent_monitor_last_error = ""
        self._started_wall = time.time()
        self._started_monotonic = time.monotonic()
        self._guardian_process: Optional[subprocess.Popen] = None
        self._guardian_status = "NOT_STARTED"
        self._guardian_started_monotonic = 0.0
        self._manifest_path: Optional[Path] = None
        self._control_path: Optional[Path] = None
        self._guardian_heartbeat_path: Optional[Path] = None
        self._guardian_incident_path: Optional[Path] = None
        self._guardian_restart_history_path: Optional[Path] = None
        self._guardian_session_id = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
        self._last_guardian_restart_monotonic = 0.0

    def start(self) -> None:
        import asyncio
        self._stopping = False
        self._loop = asyncio.get_running_loop()
        self._started_wall = time.time()
        self._started_monotonic = time.monotonic()
        self._last_loop_heartbeat_monotonic = time.monotonic()
        self._last_loop_heartbeat_wall = self._started_wall
        self._last_supervisor_heartbeat_monotonic = time.monotonic()
        self._last_supervisor_heartbeat_wall = self._started_wall
        self._last_supervisor_success_monotonic = time.monotonic()
        self._last_supervisor_success_wall = self._started_wall
        self._supervisor_error_count = 0
        self._last_supervisor_error = ""
        self._independent_monitor_error_count = 0
        self._independent_monitor_last_error = ""
        self.loop_guard.start()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="watchdog-supervisor")
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop_heartbeat_loop(), name="watchdog-loop-heartbeat")
        from .. import config
        guardian_enabled = bool(getattr(config, "WATCHDOG_GUARDIAN_ENABLED", True))
        if guardian_enabled:
            self._configure_guardian()
            self._publish_guardian_manifest()
            self._start_guardian_process()
        else:
            self._guardian_status = "DISABLED"
        # Keep the in-process independent monitor active even when the optional
        # external guardian is disabled: it still enforces the job grace and can
        # recover a watchdog task while the event loop is responsive.
        self._monitor_stop.clear()
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=self._independent_monitor_loop, daemon=True,
                name="archive-x-independent-watchdog-monitor",
            )
            self._monitor_thread.start()

    def stop(self) -> None:
        self._stopping = True
        self._write_guardian_control("stop")
        self._monitor_stop.set()
        monitor = self._monitor_thread
        if monitor is not None and monitor.is_alive() and monitor is not threading.current_thread():
            monitor.join(timeout=2.0)
        self._monitor_thread = None
        task = self._task
        if task is not None and not task.done():
            task.cancel()
        self._task = None
        loop_task = self._loop_task
        if loop_task is not None and not loop_task.done():
            loop_task.cancel()
        self._loop_task = None
        self.loop_guard.stop()
        guardian = self._guardian_process
        if guardian is not None and guardian.poll() is None:
            try:
                guardian.wait(timeout=1.25)
            except subprocess.TimeoutExpired:
                try:
                    guardian.terminate()
                    guardian.wait(timeout=1.0)
                except Exception:
                    try:
                        guardian.kill()
                    except Exception:
                        _swallowed_note("watchdogs.WatchdogManager.stop")
                        pass

    def register_job(self, job_id: str, job_type: str, *, replace: bool = False) -> None:
        """Register a job clock. Idempotent unless ``replace`` is requested.

        Sub-contexts of a running job (for example each Metadata & Assets
        operation) must not reset the job's liveness/progress clocks or swap
        its watchdog policy mid-run; only the JobManager starts a fresh clock.
        """
        if not replace and self.registry.get(job_id) is not None:
            return
        normalized_type = str(job_type or "DEFAULT").strip().upper() or "DEFAULT"
        policy = DEFAULT_POLICIES.get(normalized_type, DEFAULT_POLICIES["DEFAULT"])
        self.registry.register(job_id, kind="job", policy=policy, metadata={"job_type": normalized_type})

    def unregister_job(self, job_id: str) -> None:
        self.registry.unregister(job_id)
        self._last_recovery.pop(job_id, None)
        self._last_alert.pop(job_id, None)
        self._unhealthy_since.pop(job_id, None)
        with self._independent_lock:
            self._independent_unhealthy_since.pop(job_id, None)
            self._independent_stop_requested.discard(job_id)

    def heartbeat(self, job_id: str, *, progress: bool = False, activity: bool = False, reason: str = "heartbeat",
                  metadata: Optional[dict[str, Any]] = None) -> None:
        self.registry.heartbeat(job_id, progress=progress, activity=activity, reason=reason, metadata=metadata)


    # ------------------------------------------------------------------
    # SUBSECTION: Job observation
    # Works out progress, blocking age and liveness per job.
    # ------------------------------------------------------------------
    def _pipeline_observation(self, job_id: str) -> dict[str, Any]:
        """Return the bounded worker-pipeline snapshot for one running job."""
        try:
            return dict(self._pipeline_for(str(job_id)) or {})
        except Exception:
            return {}

    def _active_blocking_age(self, job_id: str) -> float | None:
        """Age of a currently executing blocking call, or ``None`` if idle.

        Zero is a valid age immediately after dispatch. Checking the active
        worker count avoids confusing an idle pipeline's zero age with a live
        operation.
        """
        observation = self._pipeline_observation(job_id)
        try:
            active = int(observation.get("active_blocking") or 0)
            age = max(0.0, float(observation.get("active_blocking_age") or 0.0))
        except (TypeError, ValueError):
            return None
        return age if active > 0 else None

    @staticmethod
    def _job_detail(job: dict[str, Any]) -> dict[str, Any]:
        raw_detail = job.get("detail") or "{}"
        try:
            parsed = json.loads(raw_detail) if isinstance(raw_detail, str) else dict(raw_detail)
        except Exception:
            parsed = {}
        return parsed if isinstance(parsed, dict) else {}

    def _parent_job_id(self, job: dict[str, Any]) -> str:
        """Resolve a parent from durable detail or the live supervision registry.

        Transfer Manager historically uses ``parent_transfer_job_id`` for its
        Metadata & Assets child jobs, while newer orchestrators use
        ``parent_job_id``. Prefer durable row metadata and fall back to the
        JobManager's in-memory link for child relationships registered at run
        time. This keeps the global watchdog aligned with both generations.
        """
        detail = self._job_detail(job)
        parent_id = str(detail.get("parent_job_id") or detail.get("parent_transfer_job_id") or "").strip()
        if parent_id:
            return parent_id
        job_id = str(job.get("id") or job.get("job_id") or "").strip()
        lookup = getattr(self.manager, "supervised_child", None)
        if job_id and callable(lookup):
            try:
                record = lookup(job_id) or {}
                parent_id = str(record.get("parent_job_id") or "").strip() if isinstance(record, dict) else ""
            except Exception:
                parent_id = ""
        return parent_id

    def _execution_is_live(self, job_id: str, job: dict[str, Any]) -> bool:
        """Require a live task/process and matching, unexpired execution lease.

        A persisted RUNNING row alone must never keep its parent alive: stale
        rows with orphaned workers are precisely what the watchdog must detect.
        """
        if str(job.get("status") or "").upper() not in {"RUNNING", "PAUSING"}:
            return False
        running = getattr(self.manager, "running", {}) or {}
        task = running.get(str(job_id)) if hasattr(running, "get") else None
        try:
            task_alive = task is not None and not task.done()
        except Exception:
            task_alive = False
        isolated = getattr(self.manager, "isolated_processes", {}) or {}
        process = isolated.get(str(job_id)) if hasattr(isolated, "get") else None
        try:
            process_alive = process is not None and bool(process.is_alive())
        except Exception:
            process_alive = False
        if not (task_alive or process_alive):
            return False

        row_execution_id = str(job.get("execution_id") or "").strip()
        contexts = getattr(self.manager, "_execution_context", {}) or {}
        execution = contexts.get(str(job_id)) if hasattr(contexts, "get") else None
        execution = execution if isinstance(execution, dict) else {}
        if not row_execution_id or str(execution.get("execution_id") or "").strip() != row_execution_id:
            return False
        lease_expires = str(job.get("lease_expires_at") or execution.get("lease_expires_at") or "").strip()
        if lease_expires:
            try:
                expiry = datetime.fromisoformat(lease_expires.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= datetime.now(timezone.utc):
                    return False
            except (TypeError, ValueError):
                # Match the existing parent supervisor's compatibility rule:
                # a malformed legacy lease is ignored only when the live task
                # and the current in-memory execution ID agree.
                pass
        return True

    def _pulse_live_parent_jobs(self, active: dict[str, dict[str, Any]], *, check_activity: bool) -> None:
        """Supervise every declared active parent/child job pair.

        Parent heartbeat is activity-only unless a child state/counter/activity
        changes or a bounded blocking operation is still inside its hard limit.
        This prevents healthy child work from making the parent look dead while
        ensuring a quiet/hung child cannot create an infinite keep-alive.
        """
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        seen_children: set[str] = set()
        for child_id, child in active.items():
            parent_id = self._parent_job_id(child)
            if not parent_id or parent_id == child_id or parent_id not in active:
                continue
            if not self._execution_is_live(child_id, child):
                continue
            parent = active[parent_id]
            if not self._execution_is_live(parent_id, parent):
                continue

            entry = self.registry.get(child_id)
            policy = entry.policy if entry is not None else DEFAULT_POLICIES.get(
                str(child.get("job_type") or "DEFAULT").upper(), DEFAULT_POLICIES["DEFAULT"]
            )
            blocking_age = self._active_blocking_age(child_id)
            bounded_blocking = blocking_age is not None and blocking_age < policy.recovery_timeout
            signature = (
                str(child.get("status") or "").upper(),
                str(child.get("current_operation") or ""),
                str(child.get("current_item") or ""),
                child.get("progress"), child.get("completed"), child.get("total"),
                str(child.get("admission_state") or "").upper(),
            )
            activity_id = None
            if check_activity:
                try:
                    activity_id = self.manager.db.latest_job_activity_id(child_id)
                except Exception:
                    activity_id = None
            previous = self._parent_child_observations.get(child_id)
            changed = previous is None or previous[:len(signature)] != signature
            if check_activity and activity_id is not None:
                previous_activity = previous[len(signature)] if previous and len(previous) > len(signature) else None
                try:
                    changed = changed or (previous_activity is not None and int(activity_id) > int(previous_activity))
                except (TypeError, ValueError):
                    pass
            retained_activity_id = activity_id if check_activity else (
                previous[len(signature)] if previous and len(previous) > len(signature) else None
            )
            self._parent_child_observations[child_id] = (*signature, retained_activity_id)
            seen_children.add(child_id)
            children_by_parent.setdefault(parent_id, []).append({
                "id": child_id,
                "operation": str(child.get("current_operation") or "")[:160],
                "progress": child.get("progress"),
                "blocking_age": blocking_age,
                "blocking_live": bool(bounded_blocking),
                "changed": bool(changed),
            })

        # Clean old per-child signatures after they leave the active set.
        self._parent_child_observations = {
            child_id: value for child_id, value in self._parent_child_observations.items()
            if child_id in seen_children
        }
        for parent_id, children in children_by_parent.items():
            parent_entry = self.registry.get(parent_id)
            if parent_entry is None:
                continue
            active_children = [child["id"] for child in children]
            progress = any(child["changed"] or child["blocking_live"] for child in children)
            self.registry.heartbeat(
                parent_id, progress=progress, activity=True,
                reason="live_child_progress" if progress else "live_child_execution",
                metadata={
                    "recovery_verified": True,
                    "active_child_count": len(active_children),
                    "active_child_job_ids": active_children[:20],
                    "active_child_operations": [child["operation"] for child in children[:5]],
                    "child_supervision_source": "watchdog_supervisor",
                    "bounded_child_blocking_age_seconds": max(
                        [float(child["blocking_age"] or 0.0) for child in children if child["blocking_live"]] or [0.0]
                    ),
                },
            )

    def _evaluate_job(self, job_id: str, job: dict[str, Any] | None = None, *, now: float | None = None) -> dict[str, Any]:
        return self.registry.evaluate(
            str(job_id), now=now, active_blocking_age=self._active_blocking_age(str(job_id)),
            waiting=self._waiting_operation(job or {}),
        )


    # ------------------------------------------------------------------
    # SUBSECTION: Loop beat and snapshot
    # Event loop beat and snapshot output.
    # ------------------------------------------------------------------
    def beat_loop(self) -> None:
        self.loop_guard.beat()
        self._last_loop_heartbeat_monotonic = time.monotonic()
        self._last_loop_heartbeat_wall = time.time()

    def snapshot(self) -> dict[str, Any]:
        """Return watchdog, guardian and per-job health for status output."""
        raw_jobs = self.registry.snapshot()
        with self._active_jobs_lock:
            active_rows = {jid: dict(row) for jid, row in self._active_jobs_cache.items()}
        now_mono = time.monotonic()
        jobs = [
            self._evaluate_job(str(item.get("name") or ""), active_rows.get(str(item.get("name") or ""), {}), now=now_mono)
            if item.get("kind") == "job" and str(item.get("name") or "") in active_rows
            else item
            for item in raw_jobs
        ]
        counts: dict[str, int] = {state: 0 for state in (HEALTHY, WAITING, DEGRADED, SUSPECTED, STALLED, FAILED, RECOVERING, UNKNOWN)}
        for item in jobs:
            counts[item["state"]] = counts.get(item["state"], 0) + 1
        from .. import config
        now_mono = time.monotonic()
        guardian_age = None
        if self._guardian_heartbeat_path is not None:
            try:
                guardian_age = max(0.0, time.time() - float(json.loads(self._guardian_heartbeat_path.read_text(encoding="utf-8")).get("heartbeat_unix") or 0.0))
            except Exception:
                guardian_age = None
        guardian_process_alive = bool(self._guardian_process is not None and self._guardian_process.poll() is None)
        effective_job_grace = _unrecovered_grace_seconds({})
        guardian = {
            "state": self._guardian_health_state(guardian_process_alive, guardian_age, now_mono),
            "process_alive": guardian_process_alive,
            "pid": self._guardian_process.pid if guardian_process_alive else None,
            "heartbeat_age": round(guardian_age, 2) if guardian_age is not None else None,
            "loop_heartbeat_age": round(max(0.0, time.time() - self._last_loop_heartbeat_wall), 2),
            "watchdog_task_heartbeat_age": round(max(0.0, now_mono - self._last_supervisor_heartbeat_monotonic), 2),
            "watchdog_success_age": round(max(0.0, now_mono - self._last_supervisor_success_monotonic), 2),
            "watchdog_error_count": int(self._supervisor_error_count),
            "watchdog_last_error": self._last_supervisor_error[:240],
            "independent_monitor_error_count": int(self._independent_monitor_error_count),
            "independent_monitor_last_error": self._independent_monitor_last_error[:240],
            "enforcement": "independent_process",
            "restart_limit": int(getattr(config, "WATCHDOG_GUARDIAN_RESTART_LIMIT", 2)),
            "restart_window_seconds": float(getattr(config, "WATCHDOG_GUARDIAN_RESTART_WINDOW_SECONDS", 600.0)),
            "event_loop_stale_after_seconds": float(getattr(config, "WATCHDOG_EVENT_LOOP_STALE_SECONDS", 6.0)),
            "watchdog_task_recovery_after_seconds": float(getattr(config, "WATCHDOG_TASK_RECOVERY_SECONDS", 5.0)),
            "watchdog_task_restart_after_seconds": float(getattr(config, "WATCHDOG_TASK_ENFORCER_SECONDS", 12.0)),
            "publisher_stale_after_seconds": float(getattr(config, "WATCHDOG_PUBLISHER_STALE_SECONDS", 8.0)),
            "job_unhealthy_grace_seconds": effective_job_grace,
            "job_unhealthy_grace_seconds_by_type": {
                kind: effective_job_grace for kind in sorted({
                    "DEFAULT", "BACKUP_CREATE", "CONTENT_AUTOMATION", "ENTITY_IMAGE_SYNC",
                    "METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "METADATA_SYNC",
                    "PREVIEW_PREFETCH", "ASSET_PREFETCH", "SUGGESTION_SCAN", "THUMBNAIL_INDEXER",
                    "TELEGRAM_TRANSFER", "MEDIA_MANAGER", "UPLOAD_LOCAL",
                })
            },
        }
        overall = FAILED if counts[FAILED] else (STALLED if counts[STALLED] else (SUSPECTED if counts[SUSPECTED] else (DEGRADED if counts[DEGRADED] else HEALTHY)))
        if guardian["state"] == "DEGRADED" and overall == HEALTHY:
            overall = DEGRADED
        return {
            "overall": overall,
            "counts": counts,
            "jobs": jobs,
            "event_loop": self.loop_guard.snapshot(),
            "guardian": guardian,
            "interval_seconds": float(getattr(config, "WATCHDOG_SUPERVISOR_INTERVAL_SECONDS", 1.0)),
        }


    # ------------------------------------------------------------------
    # SUBSECTION: Guardian process
    # Starts, configures and monitors the out-of-process guardian.
    # ------------------------------------------------------------------
    def _guardian_health_state(self, process_alive: bool, heartbeat_age: float | None, now_mono: float) -> str:
        from .. import config
        if not bool(getattr(config, "WATCHDOG_GUARDIAN_ENABLED", True)):
            return "DISABLED"
        if self._guardian_status == "NOT_STARTED":
            return "STARTING"
        if not process_alive:
            return "DEGRADED"
        if heartbeat_age is None:
            startup_age = max(0.0, now_mono - self._guardian_started_monotonic) if self._guardian_started_monotonic else 0.0
            if startup_age > float(getattr(config, "WATCHDOG_PUBLISHER_STALE_SECONDS", 8.0)):
                return "DEGRADED"
            return "STARTING"
        if heartbeat_age is not None and heartbeat_age > float(getattr(config, "WATCHDOG_PUBLISHER_STALE_SECONDS", 8.0)):
            return "DEGRADED"
        if self._guardian_status in {"DEGRADED", "UNAVAILABLE"}:
            return "DEGRADED"
        loop_age = max(0.0, now_mono - self._last_loop_heartbeat_monotonic)
        task_age = max(0.0, now_mono - self._last_supervisor_success_monotonic)
        if loop_age > float(getattr(config, "WATCHDOG_EVENT_LOOP_STALE_SECONDS", 6.0)) or task_age > float(getattr(config, "WATCHDOG_TASK_ENFORCER_SECONDS", 12.0)):
            return "DEGRADED"
        return "HEALTHY"

    def _configure_guardian(self) -> None:
        from .. import config
        config.RUNTIME_STATE_DIR.mkdir(parents=True, exist_ok=True)
        base = config.RUNTIME_STATE_DIR / f"watchdog-{self._guardian_session_id}"
        self._manifest_path = base.with_suffix(".json")
        self._control_path = base.with_name(base.name + "-control.json")
        self._guardian_heartbeat_path = base.with_name(base.name + "-guardian-heartbeat.json")
        self._guardian_incident_path = config.LOG_DIR / "watchdog-guardian-incidents.jsonl"
        self._guardian_restart_history_path = config.RUNTIME_STATE_DIR / "watchdog-guardian-restarts.json"
        self._guardian_status = "STARTING"
        for path in (self._control_path, self._guardian_heartbeat_path):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._configure_guardian")
                pass
        self._report_previous_guardian_restart()

    def _report_previous_guardian_restart(self) -> None:
        """V29.4.223: say in the normal log why the guardian restarted the app."""
        from .. import config
        path = self._guardian_incident_path
        marker = config.RUNTIME_STATE_DIR / "watchdog-guardian-incident-reported.txt"
        try:
            if path is None or not path.is_file():
                return
            lines = [x for x in path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()][-20:]
            last = None
            for raw in reversed(lines):
                try:
                    item = json.loads(raw)
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._report_previous_guardian_restart")
                    continue
                if str(item.get("event") or "").startswith("watchdog.guardian_restart") or item.get("event") == "watchdog.guardian_enforcement":
                    last = item
                    break
            if not last:
                return
            stamp = str(last.get("timestamp") or "")
            try:
                if marker.is_file() and marker.read_text(encoding="utf-8").strip() == stamp:
                    return
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._report_previous_guardian_restart")
                pass
            reason = str(last.get("reason") or "unknown").replace("_", " ")
            self.manager.db.log(
                f"Archive X was restarted by the watchdog guardian at {stamp}: {reason} "
                f"(the app had stopped responding; event-loop age {last.get('loop_heartbeat_age_seconds')}s)",
                "WARN", event="watchdog.guardian_restart_reported",
            )
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(stamp, encoding="utf-8")
        except Exception:
            _swallowed_note("watchdogs.WatchdogManager._report_previous_guardian_restart")
            pass

    def _start_guardian_process(self) -> bool:
        """Launch the out-of-process guardian; returns True when it starts."""
        from .. import config
        if self._stopping or self._manifest_path is None or self._control_path is None:
            return False
        if self._guardian_process is not None and self._guardian_process.poll() is None:
            return True
        now = time.monotonic()
        min_retry = float(getattr(config, "WATCHDOG_GUARDIAN_POLL_SECONDS", 0.5)) * 4
        if self._last_guardian_restart_monotonic and now - self._last_guardian_restart_monotonic < min_retry:
            return False
        # V29.4.221 restart backoff: a guardian that keeps exiting must never be
        # relaunched in a tight loop. After 3 launches within 120 s, pause
        # relaunching for 10 minutes and log the guardian's own error output once.
        launches = [t for t in (getattr(self, "_guardian_launch_times", None) or []) if now - t < 120.0]
        suppressed_until = float(getattr(self, "_guardian_suppressed_until", 0.0) or 0.0)
        if now < suppressed_until:
            return False
        if len(launches) >= 3:
            self._guardian_suppressed_until = now + 600.0
            self._guardian_launch_times = []
            self._guardian_status = "DEGRADED"
            tail = ""
            try:
                log_path = config.LOG_DIR / "watchdog-guardian-process.log"
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                tail = " | ".join(x.strip() for x in lines[-5:] if x.strip())[:600]
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._start_guardian_process")
                pass
            try:
                self.manager.db.log(
                    "Independent watchdog guardian exited repeatedly; relaunch paused for 10 minutes"
                    + (f". Guardian output: {tail}" if tail else ""),
                    "ERROR", event="watchdog.guardian_restart_backoff",
                )
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._start_guardian_process")
                pass
            return False
        launches.append(now)
        self._guardian_launch_times = launches
        self._last_guardian_restart_monotonic = now
        command = [sys.executable, str(config.APP_DIR / "run.py"), *sys.argv[1:]]
        import base64
        import json as _json
        encoded_command = base64.urlsafe_b64encode(_json.dumps(command).encode("utf-8")).decode("ascii")
        guardian_script = Path(__file__).with_name("watchdog_guardian.py").resolve()
        args = [
            sys.executable, str(guardian_script),
            "--manifest", str(self._manifest_path), "--control", str(self._control_path),
            "--parent-pid", str(os.getpid()),
            "--guardian-heartbeat", str(self._guardian_heartbeat_path),
            "--incident", str(self._guardian_incident_path),
            "--restart-history", str(self._guardian_restart_history_path),
            "--app-root", str(config.APP_DIR), "--restart-command", encoded_command,
            "--poll-seconds", str(getattr(config, "WATCHDOG_GUARDIAN_POLL_SECONDS", 0.5)),
            "--startup-grace-seconds", str(getattr(config, "WATCHDOG_GUARDIAN_STARTUP_HANG_GRACE_SECONDS", 90.0)),
            "--loop-stale-seconds", str(getattr(config, "WATCHDOG_GUARDIAN_LOOP_HANG_SECONDS", 180.0)),
            "--watchdog-stale-seconds", str(getattr(config, "WATCHDOG_GUARDIAN_TASK_HANG_SECONDS", 240.0)),
            "--publisher-stale-seconds", str(getattr(config, "WATCHDOG_GUARDIAN_PUBLISHER_HANG_SECONDS", 240.0)),
            "--restart-limit", str(getattr(config, "WATCHDOG_GUARDIAN_RESTART_LIMIT", 2)),
            "--restart-window-seconds", str(getattr(config, "WATCHDOG_GUARDIAN_RESTART_WINDOW_SECONDS", 600.0)),
        ]
        try:
            guardian_log_path = config.LOG_DIR / "watchdog-guardian-process.log"
            guardian_log_path.parent.mkdir(parents=True, exist_ok=True)
            guardian_log = guardian_log_path.open("ab", buffering=0)
            kwargs: dict[str, Any] = {
                "cwd": str(config.APP_DIR), "stdin": subprocess.DEVNULL,
                "stdout": guardian_log, "stderr": guardian_log,
                "close_fds": True,
            }
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            else:
                kwargs["start_new_session"] = True
            try:
                self._guardian_process = subprocess.Popen(args, **kwargs)
            finally:
                try: guardian_log.close()
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._start_guardian_process")
                    pass
            self._guardian_started_monotonic = time.monotonic()
            # Popen success does not prove the standard-library guardian loaded
            # correctly. Give it a short bootstrap interval, verify it is alive,
            # and record the child PID in the startup event. The monitor loop will
            # relaunch it if it exits later.
            time.sleep(0.12)
            if self._guardian_process.poll() is not None:
                code = self._guardian_process.returncode
                self._guardian_status = "DEGRADED"
                try:
                    self.manager.db.log(
                        f"Independent watchdog guardian exited during startup (code {code}); supervisor will retry",
                        "ERROR", event="watchdog.guardian_start_exited",
                    )
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._start_guardian_process")
                    pass
                return False
            self._guardian_status = "RUNNING"
            try:
                self.manager.db.log(
                    f"Independent watchdog guardian started (pid {self._guardian_process.pid})",
                    "INFO", event="watchdog.guardian_started",
                )
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._start_guardian_process")
                pass
            return True
        except Exception as exc:
            self._guardian_status = "UNAVAILABLE"
            try:
                self.manager.db.log(
                    f"Independent watchdog guardian could not start: {type(exc).__name__}: {exc}",
                    "ERROR", event="watchdog.guardian_start_failed",
                )
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._start_guardian_process")
                pass
            return False

    def _write_guardian_control(self, action: str) -> None:
        if self._control_path is None:
            return
        try:
            self._write_atomic_json(self._control_path, {"action": action, "timestamp_unix": time.time()})
        except Exception:
            _swallowed_note("watchdogs.WatchdogManager._write_guardian_control")
            pass

    @staticmethod
    def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
        """Atomically publish watchdog state, retrying short Windows file locks.

        Security scanners and other Windows processes can briefly deny replacement
        of the destination. A single failure must not stop this monitor cycle or
        leave the guardian without a fresh manifest. Keep the staged JSON intact
        while retrying the replace, and use a unique temporary file per write.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        temp_ready = False
        delays = (0.04, 0.08, 0.16, 0.32, 0.64)
        try:
            for attempt in range(len(delays) + 1):
                try:
                    if not temp_ready:
                        temporary.write_text(serialized, encoding="utf-8")
                        temp_ready = True
                    os.replace(temporary, path)
                    return
                except PermissionError:
                    if attempt >= len(delays):
                        raise
                    time.sleep(delays[attempt])
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _publish_guardian_manifest(self) -> None:
        """Write the liveness manifest that the guardian reads."""
        if self._manifest_path is None:
            return
        now_mono = time.monotonic()
        now_wall = time.time()
        with self._active_jobs_lock:
            active = [dict(job) for job in self._active_jobs_cache.values()]
        live_jobs = []
        for job in active:
            jid = str(job.get("id") or "")
            entry = self.registry.get(jid)
            state = self._evaluate_job(jid, job, now=now_mono) if entry is not None else {"state": UNKNOWN, "reason": "not_registered"}
            with self._independent_lock:
                unhealthy_since = self._independent_unhealthy_since.get(jid)
            live = {
                "id": jid, "status": str(job.get("status") or "UNKNOWN").upper(),
                "job_type": str(job.get("job_type") or "DEFAULT"),
                "watchdog_state": str(state.get("state") or UNKNOWN),
                "watchdog_reason": str(state.get("reason") or ""),
                "heartbeat_age": state.get("heartbeat_age"),
                "progress_age": state.get("progress_age"),
                "unhealthy_elapsed_seconds": round(max(0.0, now_mono - unhealthy_since), 2) if unhealthy_since is not None else 0.0,
                "active_blocking_age_seconds": self._active_blocking_age(jid),
                "parent_job_id": self._parent_job_id(job) or None,
                "active_child_count": int((entry.metadata or {}).get("active_child_count") or 0) if entry is not None else 0,
            }
            process = getattr(self.manager, "isolated_processes", {}).get(jid)
            try:
                live["isolated_process_pid"] = int(process.pid) if process is not None and process.is_alive() else None
            except Exception:
                live["isolated_process_pid"] = None
            live_jobs.append(live)
        payload = {
            "schema": 1, "parent_pid": os.getpid(), "guardian_session": self._guardian_session_id,
            "started_at_unix": self._started_wall, "started_at_monotonic": self._started_monotonic,
            "publisher_heartbeat_unix": now_wall, "publisher_heartbeat_monotonic": now_mono,
            "loop_heartbeat_unix": self._last_loop_heartbeat_wall,
            "loop_heartbeat_monotonic": self._last_loop_heartbeat_monotonic,
            "watchdog_heartbeat_unix": self._last_supervisor_heartbeat_wall,
            "watchdog_heartbeat_monotonic": self._last_supervisor_heartbeat_monotonic,
            "watchdog_success_unix": self._last_supervisor_success_wall,
            "watchdog_success_monotonic": self._last_supervisor_success_monotonic,
            "watchdog_error_count": self._supervisor_error_count,
            "watchdog_last_error": self._last_supervisor_error[:240],
            "active_jobs": live_jobs,
            "guardian_pid": self._guardian_process.pid if self._guardian_process and self._guardian_process.poll() is None else None,
        }
        self._write_atomic_json(self._manifest_path, payload)

    def _independent_monitor_loop(self) -> None:
        """Thread loop that keeps enforcing even if the asyncio loop stalls."""
        from .. import config
        interval = max(0.2, float(getattr(config, "WATCHDOG_GUARDIAN_PUBLISH_SECONDS", 0.5)))
        task_timeout = max(1.0, float(getattr(config, "WATCHDOG_TASK_RECOVERY_SECONDS", 5.0)))
        loop_timeout = max(1.0, float(getattr(config, "WATCHDOG_EVENT_LOOP_STALE_SECONDS", 6.0)))
        while not self._monitor_stop.wait(interval):
            now_mono = time.monotonic()
            try:
                self._enforce_jobs_independently(now_mono)
                loop_age = max(0.0, now_mono - self._last_loop_heartbeat_monotonic)
                task_age = max(0.0, now_mono - self._last_supervisor_success_monotonic)
                # If the asyncio loop is responsive but its watchdog task has stopped
                # servicing checks, recreate that task from this independent thread.
                recovery_cooldown_elapsed = (
                    not self._last_task_recovery_monotonic
                    or now_mono - self._last_task_recovery_monotonic >= task_timeout
                )
                if loop_age < min(2.5, loop_timeout / 2.0) and task_age >= task_timeout and recovery_cooldown_elapsed:
                    self._request_watchdog_task_recovery()
                self._publish_guardian_manifest()
                if bool(getattr(config, "WATCHDOG_GUARDIAN_ENABLED", True)):
                    process_alive = self._guardian_process is not None and self._guardian_process.poll() is None
                    heartbeat_age = None
                    if self._guardian_heartbeat_path is not None:
                        try:
                            heartbeat_age = time.time() - float(json.loads(self._guardian_heartbeat_path.read_text(encoding="utf-8")).get("heartbeat_unix") or 0.0)
                        except Exception:
                            heartbeat_age = None
                    heartbeat_stale = (
                        heartbeat_age is not None and heartbeat_age > float(getattr(config, "WATCHDOG_PUBLISHER_STALE_SECONDS", 8.0))
                    ) or (
                        heartbeat_age is None and self._guardian_started_monotonic
                        and now_mono - self._guardian_started_monotonic > float(getattr(config, "WATCHDOG_PUBLISHER_STALE_SECONDS", 8.0))
                    )
                    if not process_alive or heartbeat_stale:
                        self._guardian_status = "DEGRADED"
                        self._restart_guardian_process()
                    elif self._guardian_status != "UNAVAILABLE":
                        self._guardian_status = "RUNNING"
                else:
                    self._guardian_status = "DISABLED"
            except Exception as exc:
                # Monitoring errors are reflected separately from watchdog task
                # errors. Repeated failures stop manifest publication so the
                # out-of-process guardian enforces its publisher heartbeat deadline.
                self._independent_monitor_error_count += 1
                self._independent_monitor_last_error = f"{type(exc).__name__}: {exc}"[:240]
                self._guardian_status = "DEGRADED"
                continue

    def _restart_guardian_process(self) -> None:
        process = self._guardian_process
        replacing_live_process = process is not None and process.poll() is None
        if replacing_live_process:
            try:
                process.terminate()
                process.wait(timeout=1.0)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._restart_guardian_process")
                    pass
        self._guardian_process = None
        for path in (self._control_path, self._guardian_heartbeat_path):
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._restart_guardian_process")
                    pass
        # A live-but-unresponsive guardian can be replaced immediately. A dead
        # guardian/start failure still respects the retry cooldown in start().
        if replacing_live_process:
            self._last_guardian_restart_monotonic = 0.0
        self._start_guardian_process()


    # ------------------------------------------------------------------
    # SUBSECTION: Independent enforcement and recovery
    # Stops stuck jobs and recovers watchdog tasks.
    # ------------------------------------------------------------------
    def _enforce_jobs_independently(self, now: float) -> None:
        """Evaluate active jobs from the monitor thread and request stops."""
        from .. import config
        unhealthy = {DEGRADED, SUSPECTED, STALLED, RECOVERING}
        with self._active_jobs_lock:
            active = {jid: dict(job) for jid, job in self._active_jobs_cache.items()}
        # Parent-child links must remain supervised even if the asyncio watchdog
        # coroutine is delayed. Only jobs with matching live execution leases
        # may refresh their parent's clocks.
        self._pulse_live_parent_jobs(active, check_activity=False)
        for jid, job in active.items():
            status = str(job.get("status") or "").upper()
            if status not in {"RUNNING", "PAUSING", "CANCEL_REQUESTED"}:
                with self._independent_lock:
                    self._independent_unhealthy_since.pop(jid, None)
                    self._independent_stop_requested.discard(jid)
                continue
            entry = self.registry.get(jid)
            blocking_age = self._active_blocking_age(jid)
            if (entry is not None and blocking_age is not None
                    and blocking_age < entry.policy.recovery_timeout):
                # This thread remains independent of the asyncio loop. A live
                # bounded worker is sufficient heartbeat evidence during a slow
                # native call, but not progress evidence beyond its hard limit.
                self.registry.heartbeat(
                    jid, progress=False, activity=False,
                    reason="independent_blocking_worker_alive",
                    metadata={
                        "recovery_verified": True,
                        "active_blocking_age": round(blocking_age, 2),
                        "active_blocking_operation": str(self._pipeline_observation(jid).get("last_operation") or "")[:120],
                    },
                )
                if status in {"RUNNING", "PAUSING"} and entry.state != FAILED:
                    # The active worker is within its explicit hard deadline;
                    # remove any false unhealthy timer instead of counting a
                    # missing event-loop tick against a healthy native call.
                    with self._independent_lock:
                        self._independent_unhealthy_since.pop(jid, None)
                        self._independent_stop_requested.discard(jid)
                    continue
            result = self.registry.evaluate(
                jid, now=now, active_blocking_age=blocking_age,
                waiting=self._waiting_operation(job),
            )
            state = str(result.get("state") or UNKNOWN)
            with self._independent_lock:
                if state not in unhealthy:
                    self._independent_unhealthy_since.pop(jid, None)
                    self._independent_stop_requested.discard(jid)
                    continue
                started = self._independent_unhealthy_since.setdefault(jid, now)
                entry = self.registry.get(jid)
                if state == RECOVERING and entry is not None and entry.last_progress > started:
                    self._independent_unhealthy_since[jid] = now
                    continue
                elapsed = max(0.0, now - started)
                grace = _unrecovered_grace_seconds(job)
                if elapsed < grace or jid in self._independent_stop_requested:
                    continue
                self._independent_stop_requested.add(jid)
            reason = f"Independent watchdog observed {state} for {elapsed:.1f}s without recovery ({result.get('reason') or state})"
            loop = self._loop
            if loop is None or loop.is_closed():
                # The out-of-process guard will restart the application once its
                # loop pulse expires; never write to the DB from an unrelated process.
                continue
            try:
                loop.call_soon_threadsafe(self._independent_stop_job_on_loop, jid, reason)
            except RuntimeError:
                pass

    def _independent_stop_job_on_loop(self, jid: str, reason: str) -> None:
        stopped = False
        try:
            stopped = self.manager.watchdog_stop_unrecovered(jid, reason=reason)
            if stopped:
                try:
                    self.manager.db.log("Independent watchdog stopped an unrecovered job", "ERROR",
                                        event="watchdog.independent_force_stop", job_id=jid,
                                        details={"reason": reason})
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._independent_stop_job_on_loop")
                    pass
        except Exception as exc:
            try:
                self.manager.db.log(f"Independent watchdog stop failed: {type(exc).__name__}: {exc}", "ERROR",
                                    event="watchdog.independent_force_stop_failed", job_id=jid)
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._independent_stop_job_on_loop")
                pass
        finally:
            if not stopped:
                with self._independent_lock:
                    self._independent_stop_requested.discard(jid)

    def _request_watchdog_task_recovery(self) -> None:
        with self._independent_lock:
            if self._restart_pending or self._stopping:
                return
            self._restart_pending = True
            self._last_task_recovery_monotonic = time.monotonic()
        loop = self._loop
        if loop is None or loop.is_closed():
            with self._independent_lock:
                self._restart_pending = False
            return
        try:
            loop.call_soon_threadsafe(self._begin_watchdog_task_recovery)
        except RuntimeError:
            with self._independent_lock:
                self._restart_pending = False

    def _begin_watchdog_task_recovery(self) -> None:
        import asyncio
        if self._stopping:
            with self._independent_lock:
                self._restart_pending = False
            return
        _retain_task(asyncio.create_task(self._recover_watchdog_task(), name="watchdog-task-recovery"))

    async def _recover_watchdog_task(self) -> None:
        import asyncio
        old = self._task
        try:
            if old is not None and not old.done():
                old.cancel()
                await asyncio.gather(old, return_exceptions=True)
            if not self._stopping:
                self._last_supervisor_heartbeat_monotonic = time.monotonic()
                self._last_supervisor_heartbeat_wall = time.time()
                self._task = asyncio.create_task(self._run(), name="watchdog-supervisor-recovered")
                try:
                    self.manager.db.log("Watchdog task restarted by independent monitor", "ERROR",
                                        event="watchdog.task_restarted")
                except Exception:
                    _swallowed_note("watchdogs.WatchdogManager._recover_watchdog_task")
                    pass
        finally:
            with self._independent_lock:
                self._restart_pending = False


    # ------------------------------------------------------------------
    # SUBSECTION: Evaluation loop
    # Heartbeat loop, waiting states, grace periods and the tick.
    # ------------------------------------------------------------------
    async def _loop_heartbeat_loop(self) -> None:
        import asyncio
        from .. import config
        interval = max(0.1, min(0.5, float(getattr(config, "WATCHDOG_GUARDIAN_PUBLISH_SECONDS", 0.5))))
        while not self._stopping:
            self.beat_loop()
            await asyncio.sleep(interval)

    def _waiting_operation(self, job: dict[str, Any]) -> bool:
        status = str(job.get("status") or "").upper()
        op = str(job.get("current_operation") or "").lower()
        if status in {"PAUSED", "PAUSING", "CANCEL_REQUESTED"} or op.startswith("waiting") or "waiting for" in op:
            return True
        # Content Automation often keeps a healthy worker alive while polling
        # qBittorrent for metadata/download state. A long dependency wait is not
        # a stuck worker and must not be labelled DEGRADED from heartbeat lag.
        job_type = str(job.get("job_type") or "").upper()
        waiting_fields = ("admission_state", "dependency_state", "worker_state", "current_state")
        for field_name in waiting_fields:
            state = str(job.get(field_name) or "").strip().lower()
            if state in {"waiting", "queued", "idle", "polling", "metadata_wait", "waiting_dependency"}:
                return True
        if job_type == "CONTENT_AUTOMATION" and "qbittorrent" in op:
            wait_tokens = ("wait", "queue", "poll", "metadata", "checking", "idle", "paused", "stalled")
            if any(token in op for token in wait_tokens):
                return True
        return False

    def _idle_for_operator(self, job_id: str, job: dict[str, Any]) -> bool:
        """True when a persisted job is waiting for an operator and has no live worker."""
        status = str(job.get("status") or "")
        if status not in {"PAUSED", "INTERRUPTED", "RECOVERING"}:
            return False
        process = getattr(self.manager, "isolated_processes", {}).get(job_id)
        if process is not None:
            try:
                if process.is_alive():
                    return False
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._idle_for_operator")
                pass
        runner = self._runner_snapshot()
        if runner and str(runner.get("job_id") or "") == job_id and runner.get("running"):
            return False
        return True

    def _pipeline_for(self, job_id: str) -> Optional[dict[str, Any]]:
        for pipe in self.manager.pipeline_snapshot():
            if pipe.get("job_id") == job_id:
                return pipe
        return None

    def _runner_snapshot(self) -> Optional[dict[str, Any]]:
        try:
            from ..media_manager.runner import RUNNER
            return RUNNER.snapshot()
        except Exception:
            return None

    def _enforce_unrecovered_grace(self, jid: str, job: dict, state: str, now: float) -> bool:
        """Stop jobs only after the shared unhealthy-recovery grace expires.

        WAITING, startup UNKNOWN and operator-paused jobs are deliberately not
        treated as stuck. Any healthy/recovered tick resets the grace clock.
        All job types share the same grace; a bounded, currently executing
        blocking operation is exempt until its per-job hard deadline expires.
        """
        job_status = str(job.get("status") or "").upper()
        active_statuses = {"RUNNING", "PAUSING", "CANCEL_REQUESTED"}
        unhealthy_states = {DEGRADED, SUSPECTED, STALLED, RECOVERING}
        if job_status not in active_statuses or state not in unhealthy_states:
            self._unhealthy_since.pop(jid, None)
            return False
        # Re-read both durable status and live watchdog state before destructive
        # action. This closes the race where a heartbeat arrives on the same tick
        # the five-second timer expires.
        db_get_job = getattr(getattr(self.manager, "db", None), "get_job", None)
        latest_job = (db_get_job(jid) or job) if callable(db_get_job) else job
        if str(latest_job.get("status") or "").upper() not in active_statuses:
            self._unhealthy_since.pop(jid, None)
            return False
        entry = self.registry.get(jid)
        blocking_age = self._active_blocking_age(jid)
        if (str(latest_job.get("status") or "").upper() in {"RUNNING", "PAUSING"}
                and entry is not None and blocking_age is not None
                and blocking_age < entry.policy.recovery_timeout):
            # Do not force-stop a worker that is still executing its bounded
            # operation. In particular, the previous implementation re-read
            # the registry without active_blocking_age here and could cancel a
            # healthy decoder after the generic five-second grace elapsed.
            self.registry.heartbeat(
                jid, progress=False, activity=False,
                reason="blocking_worker_within_hard_deadline",
                metadata={"recovery_verified": True,
                          "active_blocking_age": round(blocking_age, 2),
                          "recovery_deadline_seconds": entry.policy.recovery_timeout},
            )
            self._unhealthy_since.pop(jid, None)
            return False
        latest = (self.registry.evaluate(
            jid, now=now, active_blocking_age=blocking_age,
            waiting=self._waiting_operation(latest_job),
        ) if entry is not None else {"state": state, "reason": state})
        if latest.get("state") not in unhealthy_states:
            self._unhealthy_since.pop(jid, None)
            return False
        state = str(latest.get("state") or state)
        started = self._unhealthy_since.setdefault(jid, now)
        # RECOVERING is intentionally sticky in the registry until recovery is
        # evaluated again. A useful progress heartbeat since this unhealthy
        # interval began is real evidence that the job is moving again; grant a
        # fresh grace window instead of stopping it on an outdated recovery flag.
        entry = self.registry.get(jid)
        if state == RECOVERING and entry is not None and entry.last_progress > started:
            self._unhealthy_since[jid] = now
            return False
        grace = _unrecovered_grace_seconds(latest_job)
        elapsed = max(0.0, now - started)
        if elapsed < grace:
            return False
        # Set the guard before calling manager code; a failing cancellation must
        # not create a per-tick cancellation storm. Terminal DB state removes it.
        self._unhealthy_since[jid] = now + 10_000_000_000.0
        reason = str(latest.get("reason") or state)
        try:
            self.manager.watchdog_stop_unrecovered(jid,
                reason=f"Watchdog remained {state} for {elapsed:.1f}s without recovery ({reason})")
        except Exception as exc:
            try:
                self.manager.db.log(f"Watchdog force-stop failed for {jid}: {type(exc).__name__}: {exc}", "ERROR",
                                     event="watchdog.force_stop_failed", job_id=jid,
                                     details={"state": state, "elapsed_seconds": round(elapsed, 2)})
            except Exception:
                _swallowed_note("watchdogs.WatchdogManager._enforce_unrecovered_grace")
                pass
        return True

    async def _run(self):
        import asyncio
        while not self._stopping:
            try:
                self._last_supervisor_heartbeat_monotonic = time.monotonic()
                self._last_supervisor_heartbeat_wall = time.time()
                await self._tick()
                self._last_supervisor_heartbeat_monotonic = time.monotonic()
                self._last_supervisor_heartbeat_wall = time.time()
                self._last_supervisor_success_monotonic = self._last_supervisor_heartbeat_monotonic
                self._last_supervisor_success_wall = self._last_supervisor_heartbeat_wall
                self._last_supervisor_error = ""
                from .. import config
                await asyncio.sleep(max(0.25, float(getattr(config, "WATCHDOG_SUPERVISOR_INTERVAL_SECONDS", 1.0))))
            except asyncio.CancelledError:
                raise
            except Exception:
                # Watchdog failures must never take down the application.
                self._last_supervisor_heartbeat_monotonic = time.monotonic()
                self._last_supervisor_heartbeat_wall = time.time()
                self._supervisor_error_count += 1
                self._last_supervisor_error = "Watchdog cycle raised an exception; see application diagnostics"
                from .. import config
                await asyncio.sleep(max(0.5, float(getattr(config, "WATCHDOG_SUPERVISOR_INTERVAL_SECONDS", 1.0))))

    async def _tick(self):
        """One evaluation pass over active jobs on the event loop."""
        now = time.monotonic()
        active = {str(j.get("id")): j for j in self.manager.db.active_jobs()}
        with self._active_jobs_lock:
            self._active_jobs_cache = {jid: dict(job) for jid, job in active.items()}

        # Dependency-level health: Telegram can be administratively offline or
        # waiting for login, which is not an application failure. Only flag a
        # dependency stall when work currently depends on that connection.
        telegram_name = "dependency:telegram"
        tg = getattr(self.manager, "telegram", None)
        transfer_needed = any(str(j.get("job_type") or "") in {"TELEGRAM_TRANSFER", "MEDIA_MANAGER", "UPLOAD_LOCAL"}
                              and str(j.get("status") or "") in {"RUNNING", "QUEUED", "PAUSING", "CANCEL_REQUESTED"}
                              for j in active.values())
        if self.registry.get(telegram_name) is None:
            self.registry.register(telegram_name, kind="dependency",
                                   policy=WatchdogPolicy(heartbeat_warn=10, heartbeat_timeout=30, progress_warn=30, progress_timeout=60, recovery_timeout=120, startup_grace=5),
                                   metadata={"dependency": "telegram"})
        if tg is not None and getattr(tg, "connected", False):
            client = getattr(tg, "client", None)
            connected = True
            try:
                connected = bool(client.is_connected()) if client is not None and hasattr(client, "is_connected") else bool(client)
            except Exception:
                connected = False
            if connected:
                self.registry.heartbeat(telegram_name, reason="telegram_connection_check")
            elif transfer_needed:
                self.registry.fail(telegram_name, "telegram_client_disconnected", metadata={"status": getattr(tg, "status_text", "")})
        elif transfer_needed:
            self.registry.fail(telegram_name, "telegram_service_not_connected", metadata={"status": getattr(tg, "status_text", "") if tg else "unavailable"})
        else:
            self.registry.heartbeat(telegram_name, reason="telegram_not_required")

        # Register jobs created before the watchdog supervisor started.
        for jid, job in active.items():
            if self.registry.get(jid) is None:
                self.register_job(jid, str(job.get("job_type") or "DEFAULT"))

        # JobContext normally emits fine-grained heartbeats itself. The global
        # link pass fills the gaps for declared child jobs started by an engine
        # that is waiting on its child, including non-Content-Automation parents.
        self._pulse_live_parent_jobs(active, check_activity=True)

        # Remove stale registry entries after a job reaches a terminal DB state.
        for item in list(self.registry.snapshot()):
            name = str(item.get("name") or "")
            if name and name not in active:
                self.unregister_job(name)

        runner = self._runner_snapshot()
        for jid, job in active.items():
            entry = self.registry.get(jid)
            if entry is None:
                continue
            pipe = self._pipeline_for(jid)
            active_blocking_age = self._active_blocking_age(jid)
            waiting = self._waiting_operation(job)
            # Native work runs in a bounded worker thread. While that worker is
            # actively executing a known blocking operation, the asyncio job
            # task cannot emit ordinary heartbeats. Refresh the job watchdog
            # from the independent supervisor so a healthy FFmpeg/PyAV operation
            # is not reported as stalled merely because it is decoding.
            if active_blocking_age is not None:
                entry = self.registry.get(jid)
                if entry is not None and active_blocking_age < entry.policy.recovery_timeout:
                    self.registry.heartbeat(
                        jid, progress=False, activity=False, reason="blocking_worker_heartbeat",
                        metadata={"recovery_verified": True,
                                  "active_blocking_age": round(active_blocking_age, 2),
                                  "active_blocking_operation": str((pipe or {}).get("last_operation") or "")[:120]},
                    )
            if self._idle_for_operator(jid, job):
                # PAUSED with nothing executing (e.g. "Interrupted by restart —
                # choose Resume or Cancel"). No worker exists to heartbeat, so
                # without this every such job was reported as stalled every 30 s.
                self._unhealthy_since.pop(jid, None)
                self.registry.heartbeat(jid, progress=True, reason="paused_awaiting_operator")
                self.registry.evaluate(jid, now=now, waiting=True)
                continue
            result = self.registry.evaluate(jid, now=now, active_blocking_age=active_blocking_age, waiting=waiting)

            # A job with a live dedicated transfer runner inherits that runner's
            # heartbeat. This is important because its real work happens in a
            # separate thread/event loop.
            if runner and str(runner.get("job_id") or "") == jid and runner.get("running"):
                age = float(runner.get("last_heartbeat_age") or 0.0)
                if age < entry.policy.heartbeat_timeout:
                    self.registry.heartbeat(jid, progress=False, reason="transfer_runner_heartbeat",
                                            metadata={"runner_heartbeat_age": age, "runner_seq": runner.get("seq", 0)})
                    result = self.registry.evaluate(jid, now=now, active_blocking_age=active_blocking_age, waiting=waiting)
                elif result["state"] != FAILED:
                    result["state"] = STALLED
                    result["reason"] = "transfer_runner_heartbeat_timeout"

            # Isolated process: a missing child while the job is still active is
            # unambiguous; fail it rather than leaving a phantom RUNNING job.
            process = self.manager.isolated_processes.get(jid)
            if process is not None:
                try:
                    alive = bool(process.is_alive())
                except Exception:
                    alive = False
                if not alive and str(job.get("status") or "") in {"RUNNING", "CANCEL_REQUESTED", "PAUSING"}:
                    self.registry.fail(jid, "isolated_worker_exited")
                    self.manager.db.update_job(jid, status="FAILED", error="Watchdog: isolated worker exited unexpectedly",
                                                current_operation="Watchdog detected stopped worker")
                    try:
                        self.manager.db.log("Watchdog detected an unexpected isolated worker exit", "ERROR",
                                            event="watchdog.failed", job_id=jid,
                                            details={"job_type": job.get("job_type")})
                    except Exception:
                        _swallowed_note("watchdogs.WatchdogManager._tick")
                        pass
                    continue

            result = self.registry.evaluate(jid, now=now, active_blocking_age=active_blocking_age, waiting=waiting)
            state = result["state"]
            key = jid
            if state in {DEGRADED, SUSPECTED, STALLED}:
                last_alert = self._last_alert.get(key, 0.0)
                if now - last_alert >= 30.0:
                    self._last_alert[key] = now
                    try:
                        self.manager.db.log(
                            f"Watchdog {state.lower()}: {job.get('job_type')} {jid} — {result['reason']}",
                            "WARN", event="watchdog.alert", job_id=jid,
                            details={"watchdog": result, "pipeline": pipe or {}},
                        )
                    except Exception:
                        _swallowed_note("watchdogs.WatchdogManager._tick")
                        pass

                # Safe recovery: only automatically stop transfer runners that
                # have demonstrably stopped beating. Persisted jobs are never
                # auto-resumed by the watchdog.
                if state == STALLED and runner and str(runner.get("job_id") or "") == jid and runner.get("running"):
                    hb_age = float(runner.get("last_heartbeat_age") or 0.0)
                    if hb_age >= entry.policy.recovery_timeout:
                        last = self._last_recovery.get(key, 0.0)
                        if now - last >= entry.policy.recovery_timeout:
                            self._last_recovery[key] = now
                            self.registry.mark_action(jid, "safe_stop_transfer_runner")
                            try:
                                self.manager.cancel(jid)
                                self.manager.db.log(
                                    "Watchdog safely stopped an unresponsive Telegram transfer runner",
                                    "ERROR", event="watchdog.recovery", job_id=jid,
                                    details={"heartbeat_age": hb_age, "reason": result["reason"]},
                                )
                            except Exception:
                                _swallowed_note("watchdogs.WatchdogManager._tick")
                                pass

            # Run this outside the alert-only branch so a recovery to HEALTHY
            # immediately resets the timer, and RECOVERING itself gets its grace.
            self._enforce_unrecovered_grace(jid, job, state, now)
