"""Lightweight playback performance tracing and latest-wins preview scheduling.

The module deliberately avoids external dependencies. It keeps bounded, in-memory
telemetry so performance diagnostics cannot become a source of memory growth.
"""
from __future__ import annotations

import asyncio
import heapq
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Hashable, Any


@dataclass(slots=True)
class TraceEvent:
    """One recorded playback trace event."""
    ts: float
    kind: str
    key: str = ""
    value: float = 0.0
    detail: str = ""


class PlaybackTrace:
    """Bounded event recorder for repeatable playback-performance traces."""
    def __init__(self, max_events: int = 2048):
        self.events: deque[TraceEvent] = deque(maxlen=max(128, int(max_events)))
        self.counts: dict[str, int] = {}
        self._started = time.monotonic()

    def record(self, kind: str, *, key: str = "", value: float = 0.0, detail: str = "") -> None:
        kind = str(kind)
        self.events.append(TraceEvent(time.monotonic(), kind, str(key), float(value), str(detail)))
        self.counts[kind] = self.counts.get(kind, 0) + 1

    def snapshot(self, limit: int = 256) -> dict[str, Any]:
        rows = list(self.events)[-max(1, int(limit)):]
        now = time.monotonic()
        return {
            "event_count": len(self.events),
            "counts": dict(self.counts),
            "uptime_seconds": round(now - self._started, 3),
            "events": [
                {"age_ms": round((now - e.ts) * 1000.0, 1), "kind": e.kind,
                 "key": e.key, "value": e.value, "detail": e.detail}
                for e in rows
            ],
        }


@dataclass(order=True, slots=True)
class _PreviewItem:
    """Queue entry ordered by preview priority."""
    priority: int
    sequence: int
    media_key: Hashable = field(compare=False)
    generation: int = field(compare=False)
    work: Callable[[], Awaitable[bytes]] = field(compare=False)
    future: asyncio.Future = field(compare=False)


class PreviewScheduler:
    """Bounded, latest-wins scheduler for interactive exact-frame previews.

    A new scrub position supersedes queued work for the same media. Running
    FFmpeg work is not forcibly killed here; its result is discarded if its
    generation is obsolete. This keeps process cleanup deterministic.
    """
    def __init__(self, workers: int = 1, max_pending: int = 16):
        self.workers = max(1, min(4, int(workers)))
        self.max_pending = max(4, int(max_pending))
        self._queue: list[_PreviewItem] = []
        self._latest: dict[Hashable, int] = {}
        self._sequence = 0
        self._active = 0
        self._condition = asyncio.Condition()
        self._tasks: list[asyncio.Task] = []
        self._active_items: dict[Hashable, tuple[int, asyncio.Task]] = {}
        self._started = False
        self.stats = {"submitted": 0, "completed": 0, "cancelled": 0,
                      "superseded": 0, "active_superseded": 0, "queue_rejected": 0}

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._tasks = [asyncio.create_task(self._worker(), name=f"preview-scheduler-{i}")
                       for i in range(self.workers)]

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        async with self._condition:
            for item in self._queue:
                if not item.future.done():
                    item.future.cancel()
            self._queue.clear()
            self._condition.notify_all()
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def submit(self, media_key: Hashable, generation: int, work: Callable[[], Awaitable[bytes]], *, priority: int = 0) -> bytes:
        await self.start()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        generation = int(generation)
        async with self._condition:
            self.stats["submitted"] += 1
            self._latest[media_key] = generation
            active=self._active_items.get(media_key)
            if active is not None and active[0] < generation and not active[1].done():
                active[1].cancel(); self.stats["active_superseded"] += 1
            kept = []
            for item in self._queue:
                if item.media_key == media_key and item.generation < generation:
                    if not item.future.done():
                        item.future.cancel()
                    self.stats["superseded"] += 1
                else:
                    kept.append(item)
            self._queue = kept
            heapq.heapify(self._queue)
            if len(self._queue) >= self.max_pending:
                self.stats["queue_rejected"] += 1
                future.set_exception(RuntimeError("preview scheduler queue full"))
                return await future
            self._sequence += 1
            heapq.heappush(self._queue, _PreviewItem(int(priority), self._sequence, media_key,
                                                     generation, work, future))
            self._condition.notify()
        return await asyncio.shield(future)

    async def _worker(self) -> None:
        """Run queued preview jobs, cancelling work that is no longer wanted."""
        while True:
            async with self._condition:
                while self._started and not self._queue:
                    await self._condition.wait()
                if not self._started and not self._queue:
                    return
                item = heapq.heappop(self._queue)
                self._active += 1
            try:
                latest = self._latest.get(item.media_key, item.generation)
                if item.generation < latest:
                    self.stats["superseded"] += 1
                    if not item.future.done():
                        item.future.cancel()
                    continue
                work_task=asyncio.create_task(item.work(), name=f"preview-work-{self._sequence}")
                self._active_items[item.media_key]=(item.generation,work_task)
                try:
                    result=await work_task
                except asyncio.CancelledError:
                    # V29.4.126: a newer scrub position superseded this work.
                    # Only the work was cancelled -- the worker must keep
                    # running, otherwise every later preview waits forever.
                    # Superseded = submit() saw a newer generation for this
                    # video. Decided from our own state, so a real shutdown
                    # cancel is never mistaken for it (Python 3.10 has no
                    # Task.cancelling()).
                    superseded = self._latest.get(item.media_key, item.generation) > item.generation
                    current = asyncio.current_task()
                    outer_cancel = bool(current is not None and getattr(current, "cancelling", lambda: 0)())
                    if work_task.cancelled() and superseded and not outer_cancel:
                        self.stats["superseded"] += 1
                        if not item.future.done():
                            item.future.cancel()
                        continue
                    raise
                finally:
                    if self._active_items.get(item.media_key,(None,None))[1] is work_task:
                        self._active_items.pop(item.media_key,None)
                latest = self._latest.get(item.media_key, item.generation)
                if item.generation < latest:
                    self.stats["superseded"] += 1
                    if not item.future.done():
                        item.future.cancel()
                elif not item.future.done():
                    item.future.set_result(result)
                    self.stats["completed"] += 1
            except asyncio.CancelledError:
                if not item.future.done():
                    item.future.cancel()
                self.stats["cancelled"] += 1
                raise
            except Exception as exc:
                if not item.future.done():
                    item.future.set_exception(exc)
            finally:
                async with self._condition:
                    self._active = max(0, self._active - 1)
                    self._condition.notify_all()

    def status(self) -> dict[str, int]:
        return {**self.stats, "queued": len(self._queue), "active": self._active,
                "workers": self.workers, "max_pending": self.max_pending}
