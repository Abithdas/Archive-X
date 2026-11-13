"""Canonical durable Job Orchestrator.

The orchestrator decides whether a persisted job is eligible to execute. It
owns dependency evaluation, resource/network admission, retry scheduling,
fairness and execution-lease policy. JobManager remains the worker/execution
owner; resource managers and TransferGovernor remain enforcement mechanisms.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from .priorities import job_priority
from .. import config

ADMISSION_READY = "READY"
ADMISSION_WAITING = "WAITING"
ADMISSION_BLOCKED = "BLOCKED"
ADMISSION_DELAYED = "DELAYED"

REASON_DEPENDENCY = "DEPENDENCY"
REASON_RESOURCE_CAPACITY = "RESOURCE_CAPACITY"
REASON_SERVICE_RESOURCE_CAPACITY = "SERVICE_RESOURCE_CAPACITY"
REASON_TELEGRAM_TRANSFER_CAPACITY = "TELEGRAM_TRANSFER_CAPACITY"
REASON_EXECUTION_LANE_BUSY = "EXECUTION_LANE_BUSY"
REASON_RETRY_DELAY = "RETRY_DELAY"
REASON_OPERATOR_PAUSED = "OPERATOR_PAUSED"
REASON_DUPLICATE_EXECUTION = "EXECUTION_ALREADY_ACTIVE"
REASON_DEADLINE = "DEADLINE_EXPIRED"

TRANSIENT_FAILURES = {"TIMEOUT", "NETWORK", "THROTTLED", "RESOURCE"}
PERMANENT_FAILURES = {"INVALID_INPUT", "NOT_FOUND", "PERMISSION", "BUG", "CANCELLED", "UNKNOWN"}

class JobOrchestrator:
    """Admission control: priority, dependencies, resources and execution records."""
    LEASE_SECONDS = getattr(config, "JOB_ORCHESTRATOR_LEASE_SECONDS", 45)

    def __init__(self, db, manager):
        self.db = db
        self.manager = manager
        self._fairness = {}
        self._last_execution_heartbeat: dict[str, float] = {}

    @staticmethod
    def _future(iso: str | None) -> bool:
        if not iso:
            return False
        try:
            value = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value > datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _expired(iso: str | None) -> bool:
        if not iso:
            return False
        try:
            value = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return False

    def effective_priority(self, job: dict) -> int:
        base = job_priority(job)
        created = job.get("created_at")
        try:
            value = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
            if value.tzinfo is None: value = value.replace(tzinfo=timezone.utc)
            age_minutes = max(0.0, (datetime.now(timezone.utc) - value).total_seconds() / 60.0)
            # Preserve the established bounded priority-aging semantics. Lower numeric
            # values are more urgent in the existing priority queue. Lane congestion
            # can improve urgency by at most five points without exceeding base.
            effective=max(0, int(base - min(20.0, age_minutes / 5.0)))
            try:
                lanes=self.manager.execution_lane_keys(job)
                snap=self.db.execution_lane_snapshot(self.manager.execution_lane_capacities(lanes))
                congested=sum(1 for lane in lanes if int((snap.get("lanes") or {}).get(lane,{}).get("available",0))<=0)
                effective=max(0, effective - min(5, congested))
            except Exception:
                _swallowed_note("orchestrator.JobOrchestrator.effective_priority")
                pass
            return min(base, effective)
        except (TypeError, ValueError):
            return base

    def _requirements(self, job: dict) -> dict:
        try:
            value = job.get("resource_requirements") or "{}"
            return value if isinstance(value, dict) else json.loads(value)
        except Exception:
            return {}

    def _dependency_state(self, job_id: str):
        deps = self.db.job_dependencies(job_id)
        failed = [d for d in deps if str(d.get("dependency_status") or "").upper() in {"FAILED", "CANCELLED", "COMPLETED_WITH_ERRORS"} and str(d.get("dependency_type") or "SUCCESS").upper() == "SUCCESS"]
        pending = [d for d in deps if str(d.get("dependency_status") or "").upper() not in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}]
        return deps, failed, pending

    def _resource_decision(self, job: dict):
        resources = getattr(self.manager, "resources", None)
        governor = getattr(self.manager, "governor", None)
        req = self._requirements(job)
        if resources is not None:
            category = self.manager.job_resource_category(job)
            if resources.background_paused:
                return False, ADMISSION_BLOCKED, "OPERATOR_PAUSED"
            snap = resources.snapshot()
            if resources.playback_active and resources.playback_blocks_category(category) and not req.get("playback_compatible", False):
                return False, ADMISSION_WAITING, "RESOURCE_CAPACITY"
            active = int((snap.get("category_active") or {}).get(category, 0))
            limit = int((snap.get("effective_limits") or {}).get(category, snap.get("background_capacity", 1)))
            if active >= max(1, limit):
                return False, ADMISSION_WAITING, "RESOURCE_CATEGORY_CAPACITY"
            # Mirror InteractiveResourceManager's class reservation here. Do
            # not acquire durable execution-lane leases for jobs that cannot
            # yet obtain a resource slot: otherwise a waiter can reserve its
            # logical lane and prevent another job from progressing.
            if int(snap.get("background_active", 0) or 0) >= int(snap.get("background_capacity", 1) or 1):
                return False, ADMISSION_WAITING, "SHARED_BACKGROUND_CAPACITY"
            metadata_class = category == "metadata"
            class_active = int(snap.get("metadata_active" if metadata_class else "non_metadata_active", 0) or 0)
            class_limit = int(snap.get("metadata_capacity" if metadata_class else "non_metadata_capacity", snap.get("background_capacity", 1)) or 1)
            if class_active >= max(1, class_limit):
                return False, ADMISSION_WAITING, "METADATA_WORKER_CAPACITY" if metadata_class else "OTHER_WORKER_CAPACITY"
        if governor is not None and job.get("job_type") in {"TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER"}:
            gs = governor.snapshot()
            if int(gs.get("flood_wait_remaining", 0) or 0) > 0:
                return False, ADMISSION_DELAYED, "TELEGRAM_COOLDOWN"
            if int(gs.get("cooldown_remaining", 0) or 0) > 0:
                return False, ADMISSION_DELAYED, "TELEGRAM_COOLDOWN"
            if int(gs.get("transfer_active", 0) or 0) >= int(gs.get("transfer_capacity", 3) or 3):
                return False, ADMISSION_WAITING, "TELEGRAM_TRANSFER_CAPACITY"
        return True, ADMISSION_READY, ""

    def prepare(self, job_id: str) -> tuple[bool, str]:
        """Decide whether a queued job may start; returns (allowed, reason)."""
        job = self.db.get_job(job_id)
        if not job or job.get("status") != "QUEUED":
            return False, "not_queued"
        if job_id in self.manager.running and not self.manager.running[job_id].done():
            self.db.set_job_admission(job_id, ADMISSION_BLOCKED, reason=REASON_DUPLICATE_EXECUTION, priority=job_priority(job), operation="Admission blocked")
            return False, "duplicate_execution"
        if self._future(job.get("next_run_at")):
            self.db.set_job_admission(job_id, ADMISSION_DELAYED, reason=REASON_RETRY_DELAY, priority=job_priority(job), operation="Admission delayed")
            return False, "delayed"
        if self._expired(job.get("deadline_at")):
            self.db.set_job_admission(job_id, ADMISSION_BLOCKED, reason=REASON_DEADLINE, priority=job_priority(job), operation="Admission blocked")
            return False, "deadline"
        if job_id in self.manager._cancelled:
            return False, "cancelled"
        if job_id in self.manager._paused_jobs or self.manager.paused:
            self.db.set_job_admission(job_id, ADMISSION_BLOCKED, reason=REASON_OPERATOR_PAUSED, priority=job_priority(job), operation="Admission blocked")
            return False, "paused"
        deps, failed, pending = self._dependency_state(job_id)
        if failed:
            self.db.set_job_admission(job_id, ADMISSION_BLOCKED, reason="DEPENDENCY_FAILED", priority=job_priority(job), operation="Admission blocked", payload={"dependencies": failed})
            return False, "dependency_failed"
        if pending:
            self.db.set_job_admission(job_id, ADMISSION_WAITING, reason=REASON_DEPENDENCY, priority=job_priority(job), operation="Waiting for dependencies", payload={"dependencies": pending})
            return False, "dependency"
        lanes = self.manager.execution_lane_keys(job) if hasattr(self.manager, "execution_lane_keys") else []
        if getattr(self.manager, "_lane_drain", False):
            self.db.set_job_admission(job_id, ADMISSION_WAITING, reason="EXECUTION_LANE_DRAINING", priority=self.effective_priority(job), operation="Waiting for execution lane drain")
            return False, "lane_draining"
        if lanes and hasattr(self.manager, "execution_lane_capacities"):
            snap=self.db.execution_lane_snapshot(self.manager.execution_lane_capacities(lanes))
            if any(int((snap.get("lanes") or {}).get(l,{}).get("available",0))<=0 for l in lanes):
                self.db.set_job_admission(job_id, ADMISSION_WAITING, reason="EXECUTION_LANE_CAPACITY", priority=self.effective_priority(job), operation="Waiting for execution lane capacity")
                return False, "lane_capacity"
        ok, state, reason = self._resource_decision(job)
        if not ok:
            retry_after = 2.0 if state == ADMISSION_DELAYED else None
            next_run = (datetime.now(timezone.utc) + timedelta(seconds=retry_after)).isoformat() if retry_after else None
            wait_operations = {
                "TELEGRAM_TRANSFER_CAPACITY": "Waiting for Telegram transfer capacity",
                "TELEGRAM_COOLDOWN": "Waiting for Telegram rate-limit cooldown",
                "SHARED_BACKGROUND_CAPACITY": "Waiting for shared CPU/disk capacity",
                "METADATA_WORKER_CAPACITY": "Waiting for reserved metadata worker capacity",
                "OTHER_WORKER_CAPACITY": "Waiting for independent worker capacity",
                "RESOURCE_CATEGORY_CAPACITY": "Waiting for worker category capacity",
                "RESOURCE_CAPACITY": "Waiting for playback/resource capacity",
            }
            operation = wait_operations.get(reason, "Waiting for resources")
            self.db.set_job_admission(job_id, state, reason=reason, next_run_at=next_run, priority=job_priority(job), operation=operation)
            return False, "resource"
        self.db.set_job_admission(job_id, ADMISSION_READY, reason="", next_run_at=None, priority=job_priority(job), operation="Admission ready")
        return True, "ready"

    def waiting(self, job_id: str, reason: str, *, operation: str = "Waiting", retry_after: float | None = None) -> None:
        next_run = None
        if retry_after is not None:
            next_run = (datetime.now(timezone.utc) + timedelta(seconds=max(0.1, float(retry_after)))).isoformat()
        self.db.set_job_admission(job_id, ADMISSION_WAITING if retry_after is None else ADMISSION_DELAYED, reason=reason, next_run_at=next_run, operation=operation)

    def ready(self, job_id: str) -> None:
        self.db.set_job_admission(job_id, ADMISSION_READY, reason="", next_run_at=None, operation="Admission ready")

    def start_execution(self, job_id: str):
        worker_id = getattr(self.manager, "worker_id", "archive-x-worker")
        # Database binds any RESERVED resource leases to this execution lease
        # and reuses the same lease_id, making resource and worker ownership
        # one durable recovery contract.
        return self.db.acquire_job_execution(job_id, worker_id=worker_id, lease_seconds=self.LEASE_SECONDS)

    def heartbeat_execution(self, job_id: str, execution_id: str, worker_state="RUNNING"):
        # Keep durable lease renewal comfortably below the 45s lease while
        # avoiding a SQLite write on every lifecycle tick. The in-memory
        # watchdog remains high-frequency; this path is durable ownership only.
        now = time.monotonic()
        key = str(execution_id)
        interval = max(0.25, float(getattr(config, "WATCHDOG_EXECUTION_LEASE_HEARTBEAT_SECONDS", 2.0)))
        last = self._last_execution_heartbeat.get(key, 0.0)
        if last and now - last < interval:
            return True
        result = self.db.heartbeat_job_execution(job_id, execution_id, worker_state=worker_state, lease_seconds=self.LEASE_SECONDS)
        self._last_execution_heartbeat[key] = now
        return result

    def finish_execution(self, job_id: str, execution_id: str, *, status="COMPLETED", failure_class=""):
        self.db.finish_job_execution(job_id, execution_id, status=status, failure_class=failure_class)
        self._last_execution_heartbeat.pop(str(execution_id), None)

    def classify_failure(self, exc: BaseException) -> str:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        if "floodwait" in name or "flood wait" in text:
            return "THROTTLED"
        if isinstance(exc, (TimeoutError, asyncio_timeout_error())) or "timeout" in name or "timed out" in text:
            return "TIMEOUT"
        if isinstance(exc, (ConnectionError, OSError)) or any(x in text for x in ("connection reset", "network", "temporarily unavailable")):
            return "NETWORK"
        if "resource" in text or "capacity" in text:
            return "RESOURCE"
        if isinstance(exc, (ValueError, KeyError, TypeError)):
            return "INVALID_INPUT"
        return "UNKNOWN"

    def schedule_failure(self, job_id: str, exc: BaseException):
        job = self.db.get_job(job_id) or {}
        failure = self.classify_failure(exc)
        retries = int(job.get("retry_count") or 0)
        maximum = int(job.get("max_retries") or 0)
        if failure in TRANSIENT_FAILURES and retries < maximum:
            retries += 1
            delay = min(float(getattr(config, "JOB_ORCHESTRATOR_RETRY_MAX_DELAY_SECONDS", 300)), 2.0 ** min(retries, 8))
            delay *= 1.0 + ((hash(job_id) % 20) / 100.0)
            next_run = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
            self.db.set_job_retry_policy(job_id, retry_count=retries, max_retries=maximum, failure_class=failure, next_run_at=next_run)
            self.db.set_job_admission(job_id, ADMISSION_DELAYED, reason=f"RETRY_{failure}", next_run_at=next_run, operation=f"Retry scheduled in {delay:.1f}s", payload={"attempt": job.get("attempt", 0), "retry_count": retries, "failure_class": failure})
            self.db.transition_job(job_id, "QUEUED", error=str(exc)[:1000], current_operation=f"Retry scheduled ({failure})")
            return True, failure, delay
        self.db.set_job_retry_policy(job_id, retry_count=retries, max_retries=maximum, failure_class=failure, next_run_at=None)
        return False, failure, None

    def recover_expired_leases(self):
        expired = self.db.expired_job_executions()
        recovered = []
        # Execution and resource lease deadlines are intentionally coupled;
        # release resource ownership immediately when execution recovery starts.
        self.db.expire_resource_leases()
        self.db.expire_execution_lane_slots()
        for job in expired:
            jid = job["id"]
            if job.get("status") == "RUNNING":
                try:
                    self.db.transition_job(jid, "INTERRUPTED", current_operation="Worker lease expired; recovery required")
                    self.db.append_job_journal(jid, "EXECUTION_LEASE_EXPIRED", status="INTERRUPTED", operation="Execution lease expired", payload={"execution_id":job.get("execution_id"),"worker_id":job.get("worker_id")})
                    recovered.append(jid)
                except Exception:
                    _swallowed_note("orchestrator.JobOrchestrator.recover_expired_leases")
                    pass
        return recovered

    def snapshot(self) -> dict[str, Any]:
        snap = self.db.job_orchestration_snapshot()
        snap["expired_leases"] = len(self.db.expired_job_executions())
        snap["resource_leases"] = self.db.resource_lease_snapshot()
        return snap


def asyncio_timeout_error():
    try:
        import asyncio
        return asyncio.TimeoutError
    except Exception:
        return TimeoutError
