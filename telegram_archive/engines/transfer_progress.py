"""Shared rolling throughput/ETA calculations for Telegram uploads and downloads."""
from __future__ import annotations

from collections import deque
import time

MIB = 1024 * 1024


def format_eta(seconds: int | float | None) -> str:
    """Format a known ETA; None means there is not enough signal yet."""
    if seconds is None:
        return "--:--"
    value = max(0, int(seconds))
    if value >= 3600:
        return f"{value // 3600:d}:{(value % 3600) // 60:02d}:{value % 60:02d}"
    return f"{value // 60:02d}:{value % 60:02d}"


class RollingTransferProgress:
    """Rolling ~10-second byte rate that ignores old activity before long waits.

    Speed/ETA deliberately remain unknown until at least two seconds of new
    samples are available, which avoids wildly optimistic ETAs at transfer start
    or immediately after a FloodWait.
    """

    def __init__(self, *, window_seconds: float = 10.0, minimum_seconds: float = 2.0):
        self.window_seconds = max(2.0, float(window_seconds))
        self.minimum_seconds = max(1.0, float(minimum_seconds))
        self.started_at = time.monotonic()
        self.finished_at: float | None = None
        self._samples: deque[tuple[float, int]] = deque()
        self._last_bytes = -1
        self._total = 0

    def update(self, current: int, total: int, *, now: float | None = None) -> dict:
        """Add a progress sample and return rolling speed and ETA."""
        now = time.monotonic() if now is None else float(now)
        total = max(0, int(total or 0))
        current = max(0, int(current or 0))
        if total:
            current = min(current, total)
        if self._last_bytes >= 0 and current < self._last_bytes:
            self._samples.clear()
        if current != self._last_bytes or not self._samples:
            self._samples.append((now, current))
        self._last_bytes = current
        self._total = total
        cutoff = now - self.window_seconds
        # Do not average across a long stall/FloodWait. The first update after
        # a quiet window becomes a fresh baseline; speed/ETA return only after
        # new bytes have accumulated for the configured minimum interval.
        if len(self._samples) > 1 and now - self._samples[-2][0] > self.window_seconds:
            latest = self._samples[-1]
            self._samples.clear()
            self._samples.append(latest)
        while len(self._samples) > 1 and self._samples[1][0] < cutoff:
            self._samples.popleft()

        rate_bps = None
        if len(self._samples) >= 2:
            elapsed = now - self._samples[0][0]
            byte_delta = current - self._samples[0][1]
            if elapsed >= self.minimum_seconds and byte_delta > 0:
                rate_bps = byte_delta / elapsed
        remaining = max(0, total - current)
        eta = int(remaining / rate_bps) if rate_bps and rate_bps > 0 else None
        average = current / max(0.001, now - self.started_at)
        return {
            "percent": (100.0 * current / total) if total else 0.0,
            "current": current,
            "total": total,
            "remaining": remaining,
            "rate_mib_per_second": (rate_bps / MIB) if rate_bps else None,
            "rate_bytes_per_second": rate_bps,
            "eta_seconds": eta,
            "eta_label": format_eta(eta),
            "average_mib_per_second": average / MIB,
            "elapsed_seconds": max(0.0, now - self.started_at),
        }

    def finish(self, current: int | None = None, total: int | None = None) -> dict:
        now = time.monotonic()
        self.finished_at = now
        final_current = self._last_bytes if current is None else int(current)
        final_total = self._total if total is None else int(total)
        value = self.update(final_current, final_total, now=now)
        value["elapsed_seconds"] = max(0.001, now - self.started_at)
        value["average_mib_per_second"] = max(0, final_current) / value["elapsed_seconds"] / MIB
        return value


def format_size(num_bytes: int | float) -> str:
    value = max(0.0, float(num_bytes or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} bytes"
        value /= 1024.0
    return f"{value:.2f} TB"
