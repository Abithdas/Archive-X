"""Strong references for fire-and-forget asyncio tasks.

The event loop keeps only weak references to tasks. A task created with
``create_task(...)`` whose result is discarded can be garbage-collected
before it finishes (job requeues, slot releases, waiter notifications).
``retain`` keeps the task alive until it is done and logs unexpected errors
instead of leaving "Task exception was never retrieved" noise.
"""
from __future__ import annotations

import asyncio
import logging

_TASKS: set[asyncio.Task] = set()
_log = logging.getLogger("archive_x.background")


def _done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _log.warning("Background task %s failed: %s: %s", task.get_name(), type(exc).__name__, exc)


def retain(task: asyncio.Task) -> asyncio.Task:
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task


def pending_count() -> int:
    return len(_TASKS)
