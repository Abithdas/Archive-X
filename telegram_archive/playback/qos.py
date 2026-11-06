"""Central playback QoS policy shared by the playback scheduler and status layer."""
from __future__ import annotations
from dataclasses import dataclass
import time

P0_EXACT = 0
P1_NEXT = 1
P2_CURRENT_CACHE = 2
P4_BACKGROUND = 4
P5_BULK = 5
P6_MAINTENANCE = 6

@dataclass
class PlaybackContext:
    """Inputs to one QoS decision."""
    video_id: tuple[int, int] | None = None
    generation: int = 0
    current_segment: int = 0
    target_segment: int | None = None
    buffered_seconds: float = 0.0
    cache_buffered_seconds: float = 0.0
    browser_buffered_seconds: float | None = None
    browser_buffer_observed_at: float = 0.0
    target_buffer_seconds: float = 8.0
    last_progress: float = 0.0
    state: str = "IDLE"
    starvation_events: int = 0

class PlaybackQoSController:
    """Urgency policy above SegmentTransferScheduler; it is not a scheduler."""
    def __init__(self):
        self.context = PlaybackContext(last_progress=time.monotonic())
        self.last_admission_wait = 0.0
        self.stale_cancelled = 0
        self.first_byte_samples: list[float] = []
        self.seek_samples: list[float] = []
        self.cache_hits = 0
        self.cache_misses = 0
        self._last_state = "IDLE"

    def begin(self, video_id, generation: int, segment: int, *, seeking: bool = False):
        self.context.video_id = (int(video_id[0]), int(video_id[1]))
        self.context.generation = int(generation)
        self.context.current_segment = int(segment)
        self.context.target_segment = int(segment) if seeking else None
        self.context.browser_buffered_seconds = None
        self.context.browser_buffer_observed_at = 0.0
        self.context.cache_buffered_seconds = 0.0
        self.context.buffered_seconds = 0.0
        self.context.state = "SEEKING" if seeking else "PLAYING"
        self.context.last_progress = time.monotonic()

    def end(self) -> None:
        """Return playback QoS state to idle after the interactive session ends."""
        self.context = PlaybackContext(last_progress=time.monotonic())
        self._last_state = "IDLE"

    def observe_buffer(self, buffered_seconds: float, target_buffer_seconds: float):
        self.context.cache_buffered_seconds = max(0.0, float(buffered_seconds))
        self.context.target_buffer_seconds = max(1.0, float(target_buffer_seconds))
        self.context.buffered_seconds = self._effective_buffer_seconds()
        now = time.monotonic()
        if self.context.state in {"PLAYING", "SEEKING", "STARVING", "STALLED"}:
            if self.context.buffered_seconds <= 0.05:
                if self._last_state not in {"STARVING", "STALLED"}:
                    self.context.starvation_events += 1
                self.context.state = "STALLED"
            elif self.context.buffered_seconds < min(4.0, self.context.target_buffer_seconds * .35):
                self.context.state = "STARVING"
            elif self.context.state != "SEEKING":
                self.context.state = "PLAYING"
        self.context.last_progress = now
        self._last_state = self.context.state

    def observe_browser_buffer(self, buffered_seconds: float, *, target_buffer_seconds: float | None = None):
        """Record the browser's actual forward playable buffer.

        Server-side cache continuity is not equivalent to what Chromium can
        currently play. Browser telemetry is therefore kept separately and is
        authoritative for urgency while it remains fresh.
        """
        self.context.browser_buffered_seconds = max(0.0, float(buffered_seconds))
        self.context.browser_buffer_observed_at = time.monotonic()
        if target_buffer_seconds is not None:
            self.context.target_buffer_seconds = max(1.0, float(target_buffer_seconds))
        self.context.buffered_seconds = self._effective_buffer_seconds()
        now = time.monotonic()
        if self.context.state in {"PLAYING", "SEEKING", "STARVING", "STALLED"}:
            if self.context.buffered_seconds <= 0.05:
                if self._last_state not in {"STARVING", "STALLED"}:
                    self.context.starvation_events += 1
                self.context.state = "STALLED"
            elif self.context.buffered_seconds < min(4.0, self.context.target_buffer_seconds * .35):
                self.context.state = "STARVING"
            elif self.context.state != "SEEKING":
                self.context.state = "PLAYING"
        self.context.last_progress = now
        self._last_state = self.context.state

    def _effective_buffer_seconds(self) -> float:
        browser = self.context.browser_buffered_seconds
        if browser is not None and (time.monotonic() - self.context.browser_buffer_observed_at) <= 6.0:
            return max(0.0, float(browser))
        return max(0.0, float(self.context.cache_buffered_seconds))

    # V29.4.121: account-aware playback lanes, set by the engine. Telegram
    # Web K allows ~28 MB in flight for Premium and ~4.5 MB for free
    # accounts; with 512 KiB parts the engine uses 16 / 8 lanes.
    playback_lanes: int = 4

    def foreground_worker_target(self) -> int:
        """Return the reserved scheduler worker count for active playback."""
        lanes = max(1, int(getattr(self, "playback_lanes", 4) or 4))
        if lanes > 4:
            return lanes
        return 6 if self.context.state in {"STALLED", "STARVING"} else 4

    def readahead_lane_cap(self, lanes: int) -> int:
        """How many playhead read-ahead ranges may download at once (V29.4.129).

        Read-ahead shares the user's Telegram bandwidth with the range the
        browser is waiting for. With every lane on read-ahead, the browser's
        range crawled (diagnostics: 13 s for one 512 KiB range). While the
        player has little playable buffer -- start-up, a seek, a stall -- only
        two read-ahead ranges run; with a healthy buffer up to half the lanes.
        """
        lanes = max(1, int(lanes))
        low = self.context.state in {"STALLED", "STARVING", "SEEKING"} or self.context.buffered_seconds < 6.0
        return 2 if low else max(2, lanes // 2)

    def foreground_range_concurrency(self) -> int:
        """Return dedicated Telegram range capacity for foreground media."""
        return self.foreground_worker_target()

    def readahead_budget(self, active: bool = True) -> int:
        """Return the current playhead read-ahead concurrency budget.

        This is also exposed by ``status()`` for diagnostics. Keep it aligned
        with ``readahead_lane_cap`` so status collection cannot fail merely
        because the policy's reporting API was omitted. No video means there
        is no active playhead to read ahead from.
        """
        if not active or self.context.video_id is None or self.context.state == "IDLE":
            return 0
        return self.readahead_lane_cap(self.foreground_range_concurrency())

    def priority(self, video_id, segment: int, requested: int, kind: str = "playback") -> int:
        vkey = (int(video_id[0]), int(video_id[1]))
        seg = int(segment)
        requested = int(requested)
        if kind == "preview":
            # Keep live-frame requests below the exact/current playback range,
            # but let them use a spare foreground lane so active browser playback
            # does not starve a hover preview behind the P2/background gate.
            if vkey == self.context.video_id:
                return P1_NEXT
            return max(P4_BACKGROUND, requested)
        if kind == "preload":
            # Card-click and next-video prefixes share one bounded low-priority lane.
            return P2_CURRENT_CACHE if vkey == self.context.video_id else max(P4_BACKGROUND, requested)
        if kind == "seek-buffer":
            target = self.context.target_segment
            if vkey == self.context.video_id and target is not None and abs(seg - int(target)) <= 8:
                return P1_NEXT
            return max(P4_BACKGROUND, requested)
        if kind == "seek" or (self.context.target_segment == seg and self.context.video_id == vkey) or (vkey == self.context.video_id and seg == self.context.current_segment):
            return P0_EXACT
        if vkey == self.context.video_id:
            # A range the browser explicitly requested is foreground demand even
            # when its byte offset is far from the playhead (MP4 indexes/moov can
            # live at EOF). Demoting it to P2 deadlocks: the scheduler correctly
            # holds P2 work while the browser waits for its HTTP range. Only
            # speculative work may be downgraded by distance.
            if kind in {"playback", "seek"} and requested <= P1_NEXT:
                return requested
            distance = seg - self.context.current_segment
            if distance in (1, 2):
                return P1_NEXT
            if kind == "readahead" and distance > 0 and requested <= P1_NEXT:
                # Playhead read-ahead of the playing video runs on the
                # foreground lanes (the scheduler keeps one lane free for P0).
                return P1_NEXT
            return min(P2_CURRENT_CACHE, max(requested, P2_CURRENT_CACHE))
        return max(P4_BACKGROUND, requested)

    def network_reserve(self, active: bool) -> int:
        if not active:
            return 1
        if self.context.state == "STALLED":
            return 6
        if self.context.state == "STARVING":
            return 5
        if self.context.state == "SEEKING":
            return 4
        return 3

    def background_worker_budget(self, active: bool = True) -> int:
        """Bound optional cache tasks while reserving foreground play/seek capacity."""
        if not active:
            return 4
        if self.context.state in {"STALLED", "STARVING", "SEEKING"}:
            return 0
        target = max(1.0, self.context.target_buffer_seconds)
        if self.context.buffered_seconds < target:
            return 1
        # One lane can fill the current video while another warms the next card.
        # The scheduler separately keeps a worker reserved for foreground ranges.
        return 2

    def background_prefetch_allowed(self, active: bool = True) -> bool:
        if not active:
            return True
        if self.context.state != "PLAYING":
            return False
        return self.context.buffered_seconds >= max(4.0, self.context.target_buffer_seconds * 0.75)

    def status(self) -> dict:
        return {
            "priority_levels": {"P0":"exact_target","P1":"next_playback","P2":"current_video_cache","P3":"reserved_unused","P4":"background","P5":"bulk","P6":"maintenance"},
            "video_id": list(self.context.video_id) if self.context.video_id else None,
            "generation": self.context.generation,
            "current_segment": self.context.current_segment,
            "target_segment": self.context.target_segment,
            "buffered_seconds": round(self.context.buffered_seconds, 2),
            "cache_buffered_seconds": round(self.context.cache_buffered_seconds, 2),
            "browser_buffered_seconds": None if self.context.browser_buffered_seconds is None else round(self.context.browser_buffered_seconds, 2),
            "browser_buffer_age_seconds": None if not self.context.browser_buffer_observed_at else round(max(0.0, time.monotonic() - self.context.browser_buffer_observed_at), 2),
            "target_buffer_seconds": round(self.context.target_buffer_seconds, 2),
            "last_progress_age_seconds": round(max(0.0, time.monotonic() - self.context.last_progress), 2),
            "state": self.context.state,
            "starvation_events": self.context.starvation_events,
            "stale_requests_cancelled": self.stale_cancelled,
            "cache_hit_rate": round(self.cache_hits / max(1, self.cache_hits + self.cache_misses), 4),
            "network_reserve": self.network_reserve(True),
            "foreground_worker_target": self.foreground_worker_target(),
            "foreground_range_concurrency": self.foreground_range_concurrency(),
            "background_worker_budget": self.background_worker_budget(True),
            "readahead_budget": self.readahead_budget(True),
            "background_prefetch_allowed": self.background_prefetch_allowed(True),
            "last_admission_wait_seconds": round(self.last_admission_wait, 4),
            "first_byte_latency_p50": self._percentile(self.first_byte_samples, .50),
            "first_byte_latency_p95": self._percentile(self.first_byte_samples, .95),
            "seek_latency_p50": self._percentile(self.seek_samples, .50),
            "seek_latency_p95": self._percentile(self.seek_samples, .95),
        }

    @staticmethod
    def _percentile(values, p):
        if not values:
            return None
        values = sorted(values[-200:])
        return round(values[min(len(values)-1, int((len(values)-1)*p))], 4)
