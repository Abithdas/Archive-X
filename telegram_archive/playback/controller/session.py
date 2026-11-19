"""PlaybackSession: state of one playback session."""
from __future__ import annotations
from dataclasses import dataclass, field
import time

@dataclass
class PlaybackSession:
    """Per-media playback state and seek-generation boundary."""
    media_id: str
    generation: int = 0
    position: float = 0.0
    playing: bool = False
    active_requests: int = 0
    pending_seek_position: float | None = None
    pending_seek_at: float = 0.0
    # A marker is valid only for the immediate browser Range after a committed
    # seek. If Chromium never issues that request (navigation, abort, or failed
    # media element), a later unrelated Range must not consume stale intent.
    pending_seek_ttl: float = 5.0
    last_started_at: float = field(default_factory=time.monotonic)

    def mark_seek(self, position: float) -> None:
        """Mark the next browser media Range as an explicit user seek.

        Chromium can issue several non-contiguous Range requests during normal
        MP4 probing/buffering. Those requests are not seeks and must share the
        current generation. The browser explicitly marks a committed seek here;
        the first subsequent browser Range consumes the marker and creates the
        new generation.
        """
        self.pending_seek_position = max(0.0, float(position))
        self.pending_seek_at = time.monotonic()

    def consume_pending_seek(self) -> float | None:
        position = self.pending_seek_position
        marked_at = self.pending_seek_at
        self.pending_seek_position = None
        self.pending_seek_at = 0.0
        if position is None:
            return None
        # Browser Range creation normally follows currentTime synchronously, but
        # it can be suppressed by an aborted seek or source teardown. Expire the
        # one-shot marker instead of turning the next request into a false seek.
        if marked_at <= 0.0 or time.monotonic() - marked_at > max(0.1, float(self.pending_seek_ttl)):
            return None
        return position

    def new_seek(self, position: float) -> int:
        self.generation += 1
        self.position = max(0.0, float(position))
        self.last_started_at = time.monotonic()
        return self.generation

    def begin_request(self, position: float | None = None) -> int:
        if position is not None:
            self.position = max(0.0, float(position))
        self.active_requests += 1
        return self.generation

    def end_request(self) -> None:
        self.active_requests = max(0, self.active_requests - 1)

    def is_current(self, generation: int) -> bool:
        return int(generation) == self.generation

    def snapshot(self) -> dict:
        return {"media_id": self.media_id, "generation": self.generation, "position": round(self.position, 3), "playing": self.playing, "active_requests": self.active_requests, "pending_seek_position": None if self.pending_seek_position is None else round(self.pending_seek_position, 3)}
