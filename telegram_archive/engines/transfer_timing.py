"""Where the time of a channel-to-channel transfer goes, step by step.

One transferred video is a chain of Telegram calls (refresh the source message, copy the video with its cover, post the contact-sheet picture, read
the destination back, delete the source, confirm the deletion). Each video gets an `ItemClock` that records how long each step took, and the
`TIMER` keeps the totals for the job: the average per video, the share of each step, and how much time was spent waiting because Telegram asked the
application to slow down (a flood wait). That tells whether a transfer is slow because of the application's own round trips (which can be removed)
or because of Telegram's limits (which cannot).
"""
from __future__ import annotations

import time

ORDER = ("refresh", "prepare", "cover", "copy", "picture", "verify", "bookkeeping", "delete", "confirm")
LABELS = {
    "refresh": "refresh source", "prepare": "prepare/caption", "cover": "cover", "copy": "copy video", "picture": "post picture",
    "verify": "verify", "bookkeeping": "records", "delete": "delete source", "confirm": "confirm delete",
}


class ItemClock:
    """The steps of one video. `mark(step)` adds the time since the last mark to that step; `pause`/`resume` leave out the time the video waited
    for others (in a batch, between its posting and its verification)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self.steps: dict[str, float] = {}
        self._last: float | None = clock()
        self.flood_seconds = 0.0

    def mark(self, step: str) -> None:
        now = self._clock()
        if self._last is not None:
            self.steps[step] = self.steps.get(step, 0.0) + max(0.0, now - self._last)
        self._last = now

    def add(self, step: str, seconds: float) -> None:
        self.steps[step] = self.steps.get(step, 0.0) + max(0.0, float(seconds))

    def pause(self) -> None:
        self._last = None

    def resume(self) -> None:
        self._last = self._clock()

    @property
    def total(self) -> float:
        return sum(self.steps.values())

    def text(self) -> str:
        parts = [f"{LABELS.get(k, k)} {self.steps[k]:.1f}" for k in ORDER if self.steps.get(k, 0.0) >= 0.05]
        parts += [f"{k} {v:.1f}" for k, v in self.steps.items() if k not in ORDER and v >= 0.05]
        return f"{self.total:.1f} s: " + " · ".join(parts) if parts else f"{self.total:.1f} s"


class TransferTimer:
    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.items = 0
        self.failed = 0
        self.totals: dict[str, float] = {}
        self.flood_count = 0
        self.flood_seconds = 0.0
        self.batches = 0
        self.batch_items = 0

    def flood(self, seconds: float) -> None:
        """Telegram asked to wait `seconds` before the next call."""
        self.flood_count += 1
        self.flood_seconds += max(0.0, float(seconds))

    def batch(self, size: int) -> None:
        self.batches += 1
        self.batch_items += int(size)

    def finish(self, clock: ItemClock, ok: bool = True) -> None:
        if ok:
            self.items += 1
            for step, seconds in clock.steps.items():
                self.totals[step] = self.totals.get(step, 0.0) + seconds
        else:
            self.failed += 1

    def summary(self) -> dict:
        n = max(1, self.items)
        total = sum(self.totals.values())
        return {
            "videos": self.items, "failed": self.failed,
            "average_seconds": round(total / n, 2) if self.items else 0.0,
            "steps": {k: {"average_seconds": round(self.totals[k] / n, 2), "share_percent": round(self.totals[k] * 100 / total, 1) if total else 0.0}
                      for k in sorted(self.totals, key=lambda k: -self.totals[k])},
            "flood_waits": self.flood_count, "flood_wait_seconds": round(self.flood_seconds, 1),
            "batches": self.batches, "average_batch": round(self.batch_items / self.batches, 1) if self.batches else 0.0,
        }

    def text(self) -> str:
        s = self.summary()
        if not s["videos"]:
            return "no video finished yet"
        steps = " · ".join(f"{LABELS.get(k, k)} {v['average_seconds']:.1f} s ({v['share_percent']:.0f}%)" for k, v in s["steps"].items() if v["average_seconds"] >= 0.05)
        line = f"{s['videos']} video(s), {s['average_seconds']:.1f} s each on average: {steps}"
        if s["flood_waits"]:
            line += f"; Telegram asked to slow down {s['flood_waits']} time(s), {s['flood_wait_seconds']:.0f} s in all"
        return line


TIMER = TransferTimer()
