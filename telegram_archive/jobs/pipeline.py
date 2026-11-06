"""Per-job execution pipelines.

The application deliberately uses a hybrid isolation model:
- asyncio remains the control plane so Telethon stays on its owning loop;
- every job gets its own bounded ThreadPoolExecutor for ordinary blocking work;
- native/crash-prone media work is delegated to child processes by the media layer;
- the optional embedded runtime adapter can host future WASM/WASI-safe workloads
  without becoming a startup dependency.

A pipeline is never allowed to grow an unbounded queue.  This prevents one busy
module from silently accumulating thousands of blocking calls and starving the
rest of the application.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import threading
import time
from typing import Any, Awaitable, Callable, Optional


class Pipeline:
    """Tracks and supervises the blocking stages of one job."""
    def __init__(self, job_id: str, *, max_workers: int = 2,
                 queue_factor: int = 2, heartbeat_seconds: float = 15.0):
        self.job_id = job_id
        self.max_workers = max(1, int(max_workers))
        self.capacity = max(self.max_workers, self.max_workers * max(1, int(queue_factor)))
        self.executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix=f"pipeline-{job_id[:8]}",
        )
        self._slots = asyncio.Semaphore(self.capacity)
        self.started = time.monotonic()
        self.last_heartbeat = self.started
        self.last_activity = self.started
        self.heartbeat_seconds = heartbeat_seconds
        self._closed = False
        self._active = 0
        self._queued = 0
        self._active_since = 0.0
        self._last_operation = ""
        self._lock = threading.Lock()
        self._active_futures: set[concurrent.futures.Future] = set()

    def heartbeat(self, *, activity: bool = False) -> None:
        now = time.monotonic()
        self.last_heartbeat = now
        if activity:
            self.last_activity = now

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = self._active
            queued = self._queued
        now = time.monotonic()
        age = now - self.last_heartbeat
        activity_age = now - self.last_activity
        return {
            "job_id": self.job_id,
            "pid": os.getpid(),
            "uptime": round(time.monotonic() - self.started, 1),
            "last_heartbeat": round(age, 1),
            "last_activity": round(max(0.0, activity_age), 1),
            "active_blocking": active,
            "queued_blocking": queued,
            "active_blocking_age": round(max(0.0, time.monotonic() - self._active_since), 2) if active and self._active_since else 0.0,
            "last_operation": self._last_operation,
            "max_workers": self.max_workers,
            "queue_capacity": self.capacity,
            "healthy": age <= self.heartbeat_seconds and not self._closed,
            "closed": self._closed,
        }

    async def blocking(self, func: Callable[..., Any], *args: Any,
                       timeout: Optional[float] = None, **kwargs: Any) -> Any:
        """Run ordinary blocking work without using the application's default executor.

        Capacity is bounded.  If a module produces work faster than it can be
        processed, its own pipeline back-pressures rather than affecting other
        jobs.  Crash-prone native work must use a subprocess boundary instead.
        """
        if self._closed:
            raise RuntimeError("Pipeline is closed")
        await self._slots.acquire()
        self.heartbeat(activity=True)
        with self._lock:
            self._queued += 1
        try:
            loop = asyncio.get_running_loop()
            with self._lock:
                self._queued = max(0, self._queued - 1)
                self._active += 1
                self._active_since = time.monotonic()
                self._last_operation = getattr(func, "__name__", type(func).__name__)[:120]
            concurrent_future = self.executor.submit(func, *args, **kwargs)
            future = asyncio.wrap_future(concurrent_future, loop=loop)
            with self._lock:
                self._active_futures.add(concurrent_future)
            if timeout is None:
                result = await future
            else:
                result = await asyncio.wait_for(future, timeout=timeout)
            self.heartbeat(activity=True)
            return result
        finally:
            with self._lock:
                self._active = max(0, self._active - 1)
                self._queued = max(0, self._queued)
                self._active_futures.discard(concurrent_future)
                if self._active == 0:
                    self._active_since = 0.0
            self._slots.release()
            self.heartbeat(activity=True)

    async def wait_idle(self, timeout: float = 5.0) -> bool:
        """Wait for executor work to actually finish before lifecycle cleanup.

        Cancelling the asyncio wrapper around run_in_executor does not terminate
        the Python worker thread. Keep the pipeline alive until its underlying
        futures finish, bounded by a shutdown grace period. This prevents a
        cancelled job from immediately releasing its lifecycle resources while
        its old blocking function is still executing.
        """
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                active = sum(1 for future in self._active_futures if not future.done())
                self._active = active
            if active <= 0:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(0.1, remaining))

    async def run(self, handler: Callable[[], Awaitable[Any]]) -> Any:
        self.heartbeat(activity=True)
        try:
            return await handler()
        finally:
            self.heartbeat(activity=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Do not wait for a stuck native operation here. Native/crash-prone
        # operations are already process-isolated by their caller.
        self.executor.shutdown(wait=False, cancel_futures=True)


class PipelineRegistry:
    """Looks up the Pipeline for each running job."""
    def __init__(self):
        self._items: dict[str, Pipeline] = {}
        self._lock = threading.RLock()

    def create(self, job_id: str, *, max_workers: int = 2) -> Pipeline:
        with self._lock:
            old = self._items.pop(job_id, None)
            if old:
                old.close()
            pipe = Pipeline(job_id, max_workers=max_workers)
            self._items[job_id] = pipe
            return pipe

    def get(self, job_id: str) -> Optional[Pipeline]:
        with self._lock:
            return self._items.get(job_id)

    def remove(self, job_id: str) -> None:
        with self._lock:
            pipe = self._items.pop(job_id, None)
        if pipe:
            pipe.close()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [p.snapshot() for p in self._items.values()]
