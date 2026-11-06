"""Process-topology diagnostics for Archive X lifecycle ownership.

Archive X currently owns process-local Telegram, JobManager, playback and
watcher state. This module provides a single, side-effect-free description of
that ownership boundary so launchers, diagnostics and tests do not invent
separate worker assumptions.
"""
from __future__ import annotations

import multiprocessing
import os
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class ProcessTopology:
    """Describes which process roles are active."""
    pid: int
    process_name: str
    requested_workers: int | None
    ownership_mode: str
    multi_process_requested: bool
    singleton_services: tuple[str, ...]

    def snapshot(self) -> dict:
        data = asdict(self)
        data["singleton_services"] = list(self.singleton_services)
        return data


def _requested_workers() -> int | None:
    for key in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        raw = os.getenv(key)
        if raw is None or not str(raw).strip():
            continue
        try:
            value = int(str(raw).strip())
        except ValueError:
            continue
        if value > 0:
            return value
    return None


def snapshot() -> ProcessTopology:
    workers = _requested_workers()
    return ProcessTopology(
        pid=os.getpid(),
        process_name=multiprocessing.current_process().name,
        requested_workers=workers,
        ownership_mode="single-process-owned",
        multi_process_requested=bool(workers and workers > 1),
        singleton_services=(
            "TelegramService",
            "JobManager",
            "ContentAutomation",
            "playback-engine",
            "runtime-watchers",
        ),
    )


def startup_message() -> str:
    topo = snapshot()
    if topo.multi_process_requested:
        return (
            f"Multi-process launch requested ({topo.requested_workers} workers), "
            "but Archive X lifecycle services are process-owned. "
            "Keep a single application worker until singleton ownership "
            "coordination is explicitly enabled."
        )
    return "Archive X lifecycle ownership: single-process-owned"
