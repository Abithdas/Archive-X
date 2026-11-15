"""Process-isolated background job execution.

This is the reusable process boundary for background modules that do not need
Telegram's live asyncio client. The parent FastAPI/Telethon process owns the
control plane; the child opens its own SQLite connection and executes an
allow-listed engine. This follows the application's existing native-worker
isolation pattern while keeping the main event loop free from provider/cache
work.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import importlib
import multiprocessing as mp
import traceback
from typing import Any

from ..core.database import Database
from .context import JobCancelled, JobContext, JobSkipped


# Only engines proven safe to run without the parent's Telegram client belong
# here. Add future API/cache-only engines explicitly rather than broadening this
# set to all jobs.
ISOLATED_JOB_ENGINES: dict[str, str] = {
    "ENTITY_IMAGE_SYNC": "telegram_archive.engines.entity_data:run",
}


class _IsolatedManager:
    """Small JobManager-compatible control plane used inside the child process."""

    resources = None

    def __init__(self, db: Database, job_id: str):
        self.db = db
        self.job_id = job_id

    def pipeline_for(self, _job_id: str):
        return None

    def _job_status(self) -> str:
        row = self.db.get_job(self.job_id) or {}
        return str(row.get("status") or "")

    def cancel_requested(self, job_id: str) -> bool:
        if job_id != self.job_id:
            return False
        return self._job_status() in {"CANCEL_REQUESTED", "CANCELLED"}

    def pause_requested(self, job_id: str) -> bool:
        if job_id != self.job_id:
            return False
        return self._job_status() in {"PAUSING", "PAUSED"}

    def skip_requested(self, _job_id: str) -> bool:
        # The currently isolated engines do not implement skip-at-item semantics.
        return False

    def clear_skip(self, _job_id: str) -> None:
        return None

    async def wait_if_paused(self, job_id: str):
        marked = False
        while self.pause_requested(job_id) and not self.cancel_requested(job_id):
            if not marked:
                self.db.update_job(job_id, status="PAUSED", current_operation="Paused")
                marked = True
            await asyncio.sleep(0.5)
        if marked and not self.cancel_requested(job_id):
            self.db.update_job(job_id, status="RUNNING", current_operation="Resuming")

    def skip_current(self, _job_id: str) -> None:
        return None


def _resolve_engine(spec: str):
    module_name, func_name = spec.split(":", 1)
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


def _child_entry(job_id: str, job: dict[str, Any], db_path: str, result_queue) -> None:
    """Spawn target. Keep this top-level so Windows multiprocessing spawn works."""
    db = Database(path=db_path)
    try:
        spec = ISOLATED_JOB_ENGINES.get(str(job.get("job_type") or ""))
        if not spec:
            raise RuntimeError(f"Job type is not enabled for process isolation: {job.get('job_type')}")
        engine = _resolve_engine(spec)
        manager = _IsolatedManager(db, job_id)
        # Image-sync engines are provider/SQLite work and do not require the
        # parent's live Telethon session. JobContext keeps the interface the
        # same, while the child intentionally receives telegram=None.
        ctx = JobContext(job_id, job, db, telegram=None, governor=None, manager=manager)
        asyncio.run(engine(ctx))
        result_queue.put({"ok": True})
    except JobCancelled:
        result_queue.put({"ok": False, "cancelled": True, "error": "Cancelled"})
    except JobSkipped:
        result_queue.put({"ok": False, "skipped": True, "error": "Skipped"})
    except BaseException as exc:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-12000:]
        try:
            db.log(f"Isolated worker failed for {job.get('job_type')}: {type(exc).__name__}: {exc}", "ERROR")
        except Exception:
            _swallowed_note("isolated_worker._child_entry")
            pass
        result_queue.put({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": tb,
        })
    finally:
        try:
            db.conn.close()
        except Exception:
            _swallowed_note("isolated_worker._child_entry")
            pass


def start_process(job_id: str, job: dict[str, Any], db_path) -> tuple[mp.Process, Any]:
    """Start one allow-listed isolated job and return its process + result queue."""
    if str(job.get("job_type") or "") not in ISOLATED_JOB_ENGINES:
        raise ValueError(f"Job type is not process-isolated: {job.get('job_type')}")
    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=_child_entry,
        name=f"telegram-archive-{str(job.get('job_type')).lower()[:24]}-{job_id[:8]}",
        args=(job_id, dict(job), str(db_path), result_queue),
    )
    process.daemon = True
    process.start()
    return process, result_queue
