"""Durable job interruption/recovery coordination for Archive X.

This module is intentionally a thin control-plane layer over the existing
Database and JobManager. It does not execute job work or create a second queue.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any

from .. import config

INTERRUPTED = "INTERRUPTED"
RECOVERING = "RECOVERING"


@dataclass(frozen=True)
class RecoveryScan:
    """Result of the startup recovery scan."""
    clean_shutdown: bool
    interrupted: list[dict]
    invalidated_locks: int


class RecoveryCoordinator:
    """Owns the durable interruption/recovery protocol for the existing jobs."""

    def __init__(self, db):
        self.db = db
        self.launch_id = os.getenv("ARCHIVE_X_LAUNCH_ID") or uuid.uuid4().hex[:16]
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self.db.lock:
            self.db.conn.executescript("""
            -- Resource leases are owned by Database; RecoveryCoordinator only
            -- invokes the canonical recovery/invalidation methods.
            CREATE TABLE IF NOT EXISTS idempotency_records (
                key TEXT PRIMARY KEY,
                job_id TEXT,
                operation TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_idempotency_job ON idempotency_records(job_id, updated_at);
            """)
            self.db.conn.commit()

    def record(self, job_id: str, event_type: str, *, status: str | None = None, checkpoint: Any = None, operation: str | None = None, detail: str | None = None, payload: Any = None, error_type: str | None = None, retry_count: int | None = None) -> None:
        self.db.append_job_journal(job_id,event_type,status=status,checkpoint=checkpoint,operation=operation,detail=detail,payload=payload,error_type=error_type,retry_count=retry_count,launch_id=self.launch_id)

    def checkpoint(self, job: dict, checkpoint: Any = None, *, operation: str | None = None,
                   detail: str | None = None) -> None:
        self.record(job["id"], "CHECKPOINT", status=job.get("status"),
                    checkpoint=checkpoint, operation=operation or job.get("current_operation"), detail=detail)

    def _checkpoint_for(self, job: dict):
        checkpoint = self.db.get_job_checkpoint(job["id"])
        if checkpoint and checkpoint.get("payload") is not None:
            return checkpoint["payload"]
        return job.get("detail")

    def mark_interrupted(self, job: dict, reason: str = "Application restarted before clean shutdown") -> None:
        jid = job["id"]
        checkpoint = self._checkpoint_for(job)
        # A new application launch owns recovery authority. Invalidate any
        # execution/resource/lane ownership left by the previous launch before
        # marking the job interrupted, otherwise a later Recover can be rejected
        # by the stale execution_id.
        try:
            self.db.interrupt_stale_job_execution(jid, reason=reason)
        except Exception as exc:
            self.db.log(f"Failed to invalidate stale execution for {jid}: {exc}", "WARN", event="recovery.stale_execution_invalidation_failed", job_id=jid)
        self.record(jid, "INTERRUPTED", status=INTERRUPTED,
                    checkpoint=checkpoint, operation=job.get("current_operation"), detail=reason)
        self.db.update_job(jid, status=INTERRUPTED,
                           current_operation="Interrupted — recovery pending",
                           error=(job.get("error") or "")[:1000])
        self.db.log(f"Job {jid} marked INTERRUPTED during startup recovery scan", "WARN",
                    event="recovery.job_interrupted", job_id=jid,
                    details={"job_type": job.get("job_type"), "reason": reason})

    def begin_recovery(self, job_id: str) -> dict | None:
        job = self.db.get_job(job_id)
        if not job or job.get("status") != INTERRUPTED:
            return job
        self.record(job_id, "RECOVERY_STARTED", status=RECOVERING,
                    checkpoint=self._checkpoint_for(job), operation=job.get("current_operation"))
        self.db.update_job(job_id, status=RECOVERING, current_operation="Recovering persisted state")
        self.db.log(f"Job {job_id} entered RECOVERING", "INFO", event="recovery.started", job_id=job_id)
        return self.db.get_job(job_id)

    def idempotency_key(self, job_id: str, operation: str, item: str = "") -> str:
        raw = f"{job_id}:{operation}:{item}"
        import hashlib
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:48]

    def begin_idempotent(self, key: str, job_id: str, operation: str) -> bool:
        """Claim an operation exactly once until it is explicitly completed."""
        now = config.now_iso()
        with self.db.lock:
            row = self.db.conn.execute("SELECT status FROM idempotency_records WHERE key=?", (key,)).fetchone()
            if row and str(row["status"]).upper() in {"COMPLETED", "COMPLETED_WITH_ERRORS"}:
                return False
            self.db.conn.execute(
                "INSERT INTO idempotency_records(key,job_id,operation,status,result,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET job_id=excluded.job_id,operation=excluded.operation,status=excluded.status,updated_at=excluded.updated_at",
                (key, job_id, operation, "STARTED", "", now, now),
            )
            self.db.conn.commit()
        self.record(job_id, "IDEMPOTENCY_STARTED", status=self.db.get_job(job_id).get("status") if self.db.get_job(job_id) else None, operation=operation, detail=key)
        return True

    def complete_idempotent(self, key: str, result: Any = None) -> None:
        now=config.now_iso(); payload=None if result is None else json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        with self.db.lock:
            self.db.conn.execute("UPDATE idempotency_records SET status='COMPLETED',result=?,updated_at=? WHERE key=?", (payload, now, key))
            self.db.conn.commit()

    def recovery_snapshot(self) -> dict[str, Any]:
        with self.db.lock:
            interrupted=self.db.conn.execute("SELECT COUNT(*) n FROM jobs WHERE status='INTERRUPTED'").fetchone()["n"]
            recovering=self.db.conn.execute("SELECT COUNT(*) n FROM jobs WHERE status='RECOVERING'").fetchone()["n"]
            leases=self.db.conn.execute("SELECT COUNT(*) n FROM resource_leases WHERE state IN ('RESERVED','ACTIVE','RELEASING')").fetchone()["n"]
            idem=self.db.conn.execute("SELECT COUNT(*) n FROM idempotency_records WHERE status IN ('STARTED','FAILED')").fetchone()["n"]
            recent=self.db.conn.execute("SELECT event_id,job_id,event_type,from_status,status,operation,detail,payload,error_type,retry_count,launch_id,created_at FROM job_journal ORDER BY id DESC LIMIT 25").fetchall()
        return {"launch_id":self.launch_id,"interrupted":int(interrupted),"recovering":int(recovering),"resource_leases":int(leases),"pending_idempotency":int(idem),"events":[dict(x) for x in recent]}

    def cleanup_stale_temp_files(self, roots: list[str | os.PathLike], older_than_seconds: int = 86400) -> int:
        import time as _time
        removed=0; cutoff=_time.time()-max(3600,int(older_than_seconds))
        for raw in roots:
            base=os.path.abspath(os.fspath(raw))
            if not os.path.isdir(base): continue
            for name in os.listdir(base):
                if not (name.startswith(("tg_upload_","tg_upload_recover_","archive_x_recovery_"))): continue
                path=os.path.join(base,name)
                try:
                    if os.path.getmtime(path) >= cutoff: continue
                    import shutil; shutil.rmtree(path, ignore_errors=True); removed += 1
                except OSError: pass
        if removed:
            self.db.log(f"Recovery cleaned {removed} stale temporary work directories", "INFO", event="recovery.temp_cleanup", details={"removed":removed})
        return removed

    def invalidate_old_leases(self) -> int:
        count = self.db.invalidate_stale_resource_leases(self.launch_id)
        expired = self.db.expire_resource_leases()
        count += len(expired)
        # Execution-lane slots belong to the previous process launch as well;
        # startup reconciliation must not wait for their normal lease timeout.
        count += int(self.db.release_execution_lane_slots(state="EXPIRED") or 0)
        if count:
            self.db.log(f"Recovery invalidated {count} stale resource lease(s)", "WARN",
                        event="recovery.resource_locks_invalidated",
                        details={"count": count, "launch_id": self.launch_id})
        return count

    def scan_startup(self) -> RecoveryScan:
        """Check for a clean shutdown, invalidate leases and mark interrupted jobs."""
        marker = self.db.setting("recovery.clean_shutdown", {})
        clean = bool(isinstance(marker, dict) and marker.get("clean") is True)
        invalidated = self.invalidate_old_leases()
        import tempfile
        self.cleanup_stale_temp_files([tempfile.gettempdir(), os.path.join(config.DATA_DIR, "tmp")])
        interrupted = []
        if not clean:
            for job in self.db.active_jobs():
                status = str(job.get("status") or "").upper()
                # Automatic maintenance is disposable work. A crash may cause
                # the idle watcher to create a fresh bounded slice, so never
                # surface an old maintenance execution as operator recovery.
                # An explicit operator cancellation is terminal and must also
                # survive an unclean restart.
                if job.get("job_type") in {"ASSET_PREFETCH", "PREVIEW_PREFETCH"}:
                    if status == "CANCEL_REQUESTED":
                        self.db.update_job(
                            job["id"], status="CANCELLED",
                            current_operation="Stopped by operator — maintenance suppressed",
                        )
                        self.db.log(
                            f"Maintenance job {job['id']} preserved as CANCELLED after restart",
                            "INFO", event="recovery.operator_cancel_preserved", job_id=job["id"],
                            details={"job_type": job.get("job_type")},
                        )
                    elif status in {"QUEUED", "RUNNING", "PAUSING", RECOVERING}:
                        self.db.update_job(
                            job["id"], status="CANCELLED",
                            current_operation="Maintenance slice discarded after restart",
                        )
                        self.db.log(
                            f"Maintenance job {job['id']} discarded after unclean restart",
                            "INFO", event="recovery.maintenance_discarded", job_id=job["id"],
                            details={"job_type": job.get("job_type")},
                        )
                    continue
                if status in {"QUEUED", "RUNNING", "PAUSING", "CANCEL_REQUESTED", RECOVERING}:
                    self.mark_interrupted(job)
                    interrupted.append(job)
        self.db.set_setting("recovery.clean_shutdown", {"clean": False, "launch_id": self.launch_id, "timestamp": config.now_iso()})
        self.db.log(
            f"Recovery startup scan complete: clean_shutdown={clean}, interrupted={len(interrupted)}, stale_locks={invalidated}",
            "INFO", event="recovery.startup_scan",
            details={"clean_shutdown": clean, "interrupted": len(interrupted), "stale_locks": invalidated, "launch_id": self.launch_id},
        )
        return RecoveryScan(clean, interrupted, invalidated)

    def mark_clean_shutdown(self) -> None:
        self.db.set_setting("recovery.clean_shutdown", {"clean": True, "launch_id": self.launch_id, "timestamp": config.now_iso()})
        self.db.log("Archive X clean shutdown marker written", "INFO", event="recovery.clean_shutdown",
                    details={"launch_id": self.launch_id})
