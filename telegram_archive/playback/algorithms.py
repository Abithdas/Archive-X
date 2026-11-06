"""Dependency-free playback algorithms and control primitives."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import deque
from dataclasses import dataclass
from statistics import median
from typing import Iterable
import math


class IntervalSet:
    """Sorted disjoint inclusive intervals with logarithmic lookup.

    The old implementation linearly scanned every range. Playback caches can
    accumulate hundreds of small holes, so lookups now use bisect while merge
    operations still keep adjacent intervals compact.
    """
    __slots__ = ("_items", "_starts")

    def __init__(self, intervals: Iterable[tuple[int, int]] | None = None):
        self._items: list[tuple[int, int]] = []
        self._starts: list[int] = []
        if intervals:
            for start, end in intervals:
                self.add(start, end)

    def _rebuild_starts(self) -> None:
        self._starts = [a for a, _ in self._items]

    def add(self, start: int, end: int) -> None:
        start, end = int(start), int(end)
        if end < start:
            return
        if not self._items:
            self._items.append((start, end)); self._starts.append(start); return
        i = bisect_left(self._starts, start)
        if i > 0 and self._items[i - 1][1] + 1 >= start:
            i -= 1
        j = i
        while j < len(self._items) and self._items[j][0] <= end + 1:
            a, b = self._items[j]
            start, end = min(start, a), max(end, b)
            j += 1
        self._items[i:j] = [(start, end)]
        self._rebuild_starts()

    def contains(self, start: int, end: int) -> bool:
        start, end = int(start), int(end)
        if end < start:
            return True
        if not self._items:
            return False
        i = bisect_right(self._starts, start) - 1
        if i < 0:
            return False
        a, b = self._items[i]
        return a <= start and end <= b

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear(); self._starts.clear()

    def snapshot(self) -> list[tuple[int, int]]:
        return list(self._items)


class RobustRateEstimator:
    """Outlier-resistant estimate of a rate from recent samples."""
    __slots__ = ("window", "alpha", "samples", "ewma")

    def __init__(self, window: int = 8, alpha: float = 0.35):
        self.window = max(3, int(window))
        self.alpha = min(0.95, max(0.05, float(alpha)))
        self.samples: deque[float] = deque(maxlen=self.window)
        self.ewma = 0.0

    def add(self, value: float) -> float:
        value = max(0.0, float(value))
        if value <= 0:
            return self.value
        self.samples.append(value)
        med = float(median(self.samples))
        self.ewma = med if self.ewma <= 0 else self.alpha * med + (1.0 - self.alpha) * self.ewma
        return self.ewma

    @property
    def value(self) -> float:
        return float(self.ewma or (median(self.samples) if self.samples else 0.0))

    def __len__(self) -> int:
        return len(self.samples)


@dataclass
class BufferDecision:
    """Output of one adaptive buffer decision."""
    target_seconds: float
    foreground_seconds: float
    readahead_seconds: float
    reason: str
    preview_level: str = "full"
    preview_allowed: bool = True


class AdaptiveBufferController:
    """Compute playback/readahead targets from observed network and media rate."""
    __slots__ = ("min_buffer", "max_buffer", "foreground_min", "foreground_max", "seek_target",
                 "low_water", "high_water", "history", "throughput", "last_decision")

    def __init__(self, *, min_buffer: float = 6.0, max_buffer: float = 36.0,
                 foreground_min: float = 6.0, foreground_max: float = 18.0,
                 seek_target: float = 2.0, low_water: float = 4.0, high_water: float = 14.0):
        self.min_buffer = max(2.0, float(min_buffer))
        self.max_buffer = max(self.min_buffer, float(max_buffer))
        self.foreground_min = max(2.0, float(foreground_min))
        self.foreground_max = max(self.foreground_min, float(foreground_max))
        self.seek_target = max(2.0, float(seek_target))
        self.low_water=max(0.0,float(low_water)); self.high_water=max(self.low_water,float(high_water))
        self.history: deque[tuple[float, float]] = deque(maxlen=24)
        self.throughput = RobustRateEstimator(window=8, alpha=0.35)
        self.last_decision = BufferDecision(self.min_buffer, self.foreground_min, 6.0, "initial")

    def observe(self, *, bytes_downloaded: int, elapsed: float, media_bps: float,
                current_buffer_seconds: float = 0.0, seeking: bool = False,
                active_requests: int = 0) -> BufferDecision:
        """Add a throughput sample and return the new buffer decision."""
        if bytes_downloaded > 0 and elapsed > 0:
            self.throughput.add(float(bytes_downloaded) / float(elapsed))
        net_bps = self.throughput.value
        media_bps = max(1.0, float(media_bps or 0.0))
        ratio = max(0.25, net_bps / media_bps) if net_bps else 1.0
        # More headroom when the line is close to the media bitrate; less when it
        # is extremely fast. Active seeks get a smaller speculative window.
        # Prefer a larger safety buffer when sustained throughput is close to
        # the media bitrate. Never compensate for a bandwidth deficit by adding
        # more parallel playback requests; the extra requests only increase
        # contention.
        if ratio < 1.0:
            target = 34.0
        elif ratio < 1.2:
            target = 30.0
        elif ratio < 1.5:
            target = 24.0
        elif ratio < 2.5:
            target = 16.0
        elif ratio < 4.0:
            target = 12.0
        else:
            target = 8.0
        if seeking:
            # A committed seek should resume after a small local buffer near the
            # target, not wait for the ordinary full-playback safety target.
            target = min(target, self.seek_target)
        # A second request is useful for sequential read-ahead, but it must not
        # inflate the target indefinitely under congestion.
        if active_requests > 1 and ratio >= 1.5 and not seeking:
            target = min(self.max_buffer, target + min(4.0, active_requests * 1.0))
        target = max(self.min_buffer, min(self.max_buffer, target))
        if current_buffer_seconds >= target * 1.75:
            target = max(self.min_buffer, target * 0.75)
        foreground = max(self.foreground_min, min(self.foreground_max, min(12.0, target)))
        read_ahead = max(4.0, target - foreground + 5.0)

        # Preview is expendable. Keep it fully enabled with a healthy buffer,
        # throttle it when the buffer is marginal, and disable server-side
        # preview work when playback is close to starvation.
        if current_buffer_seconds < self.low_water:
            preview_level, preview_allowed = "off", False
        elif current_buffer_seconds < self.high_water:
            preview_level, preview_allowed = "reduced", True
        else:
            preview_level, preview_allowed = "full", True
        reason = (f"ratio={ratio:.2f};net={net_bps:.0f};media={media_bps:.0f};"
                  f"buffer={current_buffer_seconds:.1f};preview={preview_level}")
        self.history.append((time_value(), target))
        self.last_decision = BufferDecision(round(target, 2), round(foreground, 2),
                                            round(read_ahead, 2), reason,
                                            preview_level, preview_allowed)
        return self.last_decision

    def decision(self, media_bps: float, *, current_buffer_seconds: float = 0.0,
                 seeking: bool = False, active_requests: int = 0) -> BufferDecision:
        return self.observe(bytes_downloaded=0, elapsed=0.0, media_bps=media_bps,
                            current_buffer_seconds=current_buffer_seconds,
                            seeking=seeking, active_requests=active_requests)


def time_value() -> float:
    # Kept local to avoid importing time for the common algorithm path in tests.
    import time
    return time.monotonic()


def aging_priority(priority: int, enqueued_at: float, now: float, quantum: float = 15.0) -> int:
    waited = max(0.0, float(now) - float(enqueued_at))
    return max(0, int(priority) - int(waited // max(1.0, float(quantum))))
