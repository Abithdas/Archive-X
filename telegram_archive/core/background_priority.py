"""Application-wide priority gate for automatic background maintenance.

Channel-selection work advances from cheap to expensive stages.  The gate is
intentionally small: it does not own job persistence or operator controls; it
only serializes automatic resource-heavy stages so they cannot pile up.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

STAGE_ORDER = {
    # Lower numbers are cheaper/earlier automatic stages. Interactive work,
    # authentication, and explicit Telegram transfers are not placed behind
    # this gate; their existing resource/transfer governors remain authoritative.
    "catalogue": 10,
    "snapshot": 20,
    "metadata": 30,
    "fingerprint": 40,
    "provider": 50,
    "delivery": 60,
    "artwork": 70,
    "maintenance": 80,
}

OPERATION_STAGE = {
    "metadata": "metadata",
    "suggestions": "metadata",
    "thumbnails": "snapshot",
    "entity_data": "artwork",
    "entity_images": "artwork",
}

AUTOMATIC_JOB_STAGE = {
    "PREVIEW_PREFETCH": "snapshot",
    "ASSET_PREFETCH": "artwork",
    "BACKUP_CREATE": "maintenance",
}


class BackgroundPriorityGate:
    """Single-lane automatic background gate ordered by increasing cost."""

    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._active: tuple[int, str] | None = None
        self._waiting: list[tuple[int, int, str]] = []
        self._seq = 0

    @property
    def active(self) -> str | None:
        return self._active[1] if self._active else None

    def status(self) -> dict:
        return {
            "active_stage": self.active,
            "queued_stages": [x[2] for x in sorted(self._waiting)],
            "stage_order": dict(STAGE_ORDER),
        }

    @staticmethod
    def operation_stage(operation: str) -> str | None:
        return OPERATION_STAGE.get(str(operation or "").strip().casefold())

    @staticmethod
    def job_stage(job: dict) -> str | None:
        """Return the gate stage for an automatic job, never for an explicit job."""
        job_type = str(job.get("job_type") or "").strip().upper()
        if job_type in {"PREVIEW_PREFETCH", "ASSET_PREFETCH"}:
            return AUTOMATIC_JOB_STAGE.get(job_type)
        try:
            detail = job.get("detail") or {}
            if isinstance(detail, str):
                import json
                detail = json.loads(detail) if detail else {}
            if not isinstance(detail, dict) or not bool(detail.get("automatic_background")):
                return None
        except Exception:
            return None
        return AUTOMATIC_JOB_STAGE.get(job_type)

    @asynccontextmanager
    async def operation_slot(self, operation: str):
        stage = self.operation_stage(operation)
        if not stage:
            yield
            return
        async with self.slot(stage):
            yield

    @asynccontextmanager
    async def slot(self, stage: str):
        stage = str(stage or "metadata").strip().lower()
        priority = STAGE_ORDER.get(stage, 100)
        async with self._condition:
            self._seq += 1
            token = (priority, self._seq, stage)
            self._waiting.append(token)
            try:
                while self._active is not None or min(self._waiting) != token:
                    await self._condition.wait()
                self._waiting.remove(token)
                self._active = (priority, stage)
            except BaseException:
                if token in self._waiting:
                    self._waiting.remove(token)
                self._condition.notify_all()
                raise
        try:
            yield
        finally:
            async with self._condition:
                self._active = None
                self._condition.notify_all()
