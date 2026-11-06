"""Global safety gate shared by every Telegram-heavy operation.

Per the "Important reuse rule": no engine module is allowed to implement
its own FloodWait handling or concurrency limits — they all go through
this one governor.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Governor state
#   - TransferGovernor
#       - Setup, background pause and playback policy
#       - Status and download limits
#       - File slots and download flood handling
#       - Global range slots and playback lanes
#       - Lane learning and range outcomes
#       - RPC permits
#       - Upload permits and ramping
#       - Public RPC slot
#       - Range and transfer permits
#       - Status
# ------------------------------------------------------------------
from __future__ import annotations

import threading
import time
from contextlib import asynccontextmanager
import asyncio
import statistics
from dataclasses import dataclass, asdict

from .database import Database
from .. import config




# ==============================================================================
# SECTION: Governor state
# Plain state snapshot type.
# ==============================================================================

@dataclass
class GovernorState:
    """Mutable counters and limits owned by TransferGovernor."""
    max_rpc: int = 1
    active_rpc: int = 0
    max_ranges: int = 2
    active_ranges: int = 0
    max_playback_ranges: int = 4
    active_playback_ranges: int = 0
    max_thumbnail_workers: int = 1
    flood_wait_until: float = 0
    cooldown_until: float = 0
    bytes_per_second: float = 0
    last_error: str = ""
    total_rpc_requests: int = 0
    total_rpc_success: int = 0
    total_rpc_floodwaits: int = 0
    last_rpc_latency_ms: float = 0
    last_rpc_at: float = 0
    playback_active: bool = False
    playback_streams: int = 0
    playback_range_waiting: int = 0
    playback_range_reservations: int = 0
    playback_range_requests: int = 0
    playback_range_deadline_misses: int = 0
    last_playback_range_wait_ms: float = 0
    total_playback_range_wait_ms: float = 0




# ==============================================================================
# SECTION: TransferGovernor
# Global Telegram safety gate. The groups below are natural split points.
# ==============================================================================

class TransferGovernor:
    """Global safety gate shared by every Telegram-heavy operation."""


    # ------------------------------------------------------------------
    # SUBSECTION: Setup, background pause and playback policy
    # Construction, background pause and playback enter/exit.
    # ------------------------------------------------------------------
    def __init__(self, db: Database):
        """Create state, locks and semaphores for every lane."""
        self.db = db
        self.state = GovernorState()
        self.lock = threading.RLock()
        self.rpc_sem = threading.BoundedSemaphore(self.state.max_rpc)
        self._rpc_cond = threading.Condition(threading.RLock())
        self._active_rpc_slots = 0
        self._rpc_schedule_lock = threading.Lock()
        self._rpc_next_at = 0.0
        # Foreground playback RPCs have their own cancellation-aware lane. A
        # metadata/entity RPC occupying the ordinary lane must never make a P0
        # seek wait for the shared permit. The lane is bounded and paced, not
        # single-flight, so multiple media ranges can progress concurrently.
        self._playback_rpc_lock = threading.Condition(threading.RLock())
        self._active_playback_rpc_slots = 0
        self._playback_rpc_max = max(1, int(getattr(config, "PLAYBACK_FOREGROUND_RPC_CONCURRENCY", 4)))
        self._playback_rpc_next_at = 0.0
        self._playback_rpc_interval = float(getattr(config, "PLAYBACK_FOREGROUND_RPC_INTERVAL_SECONDS", 0.02))
        self._rpc_interval = 0.12
        self._rpc_interval_min = 0.06
        # V29.4.130: raised from 2.0 s so repeated FloodWaits can slow the lane
        # below one request every 2 s when Telegram requires it.
        self._rpc_interval_max = 4.0
        self._rpc_latency_samples: list[float] = []
        self._range_cond = threading.Condition(threading.Lock())
        self._active_range_slots = 0
        self._active_playback_range_slots = 0
        # Telegram app-config values cap concurrent FILE operations in the
        # background lanes; they are not per-part-request limits for the current
        # playback file. Foreground byte-range concurrency is governed separately
        # by the throughput/QoS controller.
        self._global_range_cond = threading.Condition(threading.RLock())
        self._global_range_active = 0
        self._global_range_active_by_class = {"small": 0, "large": 0}
        # `help.getAppConfig` reports concurrently active FILE operations. Keep
        # those as file-level limits, never use them to cap byte-part requests.
        self._telegram_small_file_limit = 3
        self._telegram_large_file_limit = 2
        # Background byte-range requests have an independent budget. Playback
        # uses its own throughput-tuned lane and does not consume this pool.
        self._telegram_small_range_limit = max(1, int(getattr(config, "TELEGRAM_BACKGROUND_RANGE_CONCURRENCY", 8)))
        self._telegram_large_range_limit = self._telegram_small_range_limit
        self._telegram_global_range_limit = self._telegram_small_range_limit
        self.state.max_ranges = self._telegram_global_range_limit
        self._active_file_operations = {"small": 0, "large": 0}
        # File-operation quotas are per Telegram DC, not global across the
        # account. The aggregate counters above remain for existing diagnostics.
        self._active_file_operations_by_dc: dict[tuple[int, str], int] = {}
        self._download_lane_pause_until = {"range": 0.0, "playback": 0.0, "fingerprint": 0.0}
        self._download_lane_reduction = {"range": 0, "playback": 0, "fingerprint": 0}
        self._download_lane_reduced_until = {"range": 0.0, "playback": 0.0, "fingerprint": 0.0}
        self._download_lane_floodwaits = {"range": 0, "playback": 0, "fingerprint": 0}
        self._download_lane_last_wait_seconds = {"range": 0.0, "playback": 0.0, "fingerprint": 0.0}
        # OSHASH reads have a small independent permit pool, so snapshots cannot
        # occupy all background range capacity while metadata matching waits.
        self._fingerprint_sem = asyncio.Semaphore(max(1, int(getattr(config, "TELEGRAM_FINGERPRINT_CONCURRENCY", 4))))
        self._download_channel_floodwaits: dict[int, int] = {}
        self._download_file_reference_refreshes = 0
        self._download_file_reference_refreshes_by_channel: dict[int, int] = {}
        # Dedicated upload-part lane. It is independent of the general RPC and
        # byte-range lanes: a part FloodWait must not stall catalogue calls or
        # playback, and those lanes must not pause an upload. X is parts per
        # queue and Y is the number of logical parallel upload queues. Requests
        # use the existing authenticated Telethon client; no login/session is
        # created here.
        self._upload_cond = threading.Condition(threading.RLock())
        self._upload_x = 4
        self._upload_y = 1
        self._upload_active_parts = 0
        self._upload_pause_until = 0.0
        self._upload_last_flood_monotonic = 0.0
        self._upload_last_ramp_monotonic = time.monotonic()
        self._upload_floodwait_count = 0
        self._upload_last_wait_seconds = 0.0
        self._upload_last_part_success_monotonic = 0.0
        self._upload_step_levels = ((4, 1), (6, 1), (6, 2), (8, 2), (8, 3), (8, 4))
        self._global_playback_waiters = 0
        self._telegram_app_config_refreshed_at = 0.0
        self._playback_range_limit = max(1, int(getattr(config, "PLAYBACK_FOREGROUND_RANGE_CONCURRENCY", 6)))
        # V29.4.121: account-aware playback lanes (set by the playback engine).
        self._playback_lane_target = self._playback_range_limit
        self._playback_lane_emergency = max(self._playback_range_limit, int(getattr(config, "PLAYBACK_FOREGROUND_EMERGENCY_CONCURRENCY", 10)))
        self._playback_range_waiting = 0
        self._background_paused = False
        self._range_schedule_lock = threading.Lock()
        self._range_next_at = 0.0
        self._range_interval = 0.08
        self._range_interval_min = 0.02
        # V29.4.130: raised from 1.0 s (see _rpc_interval_max).
        self._range_interval_max = 4.0
        self._range_samples: list[float] = []
        self._range_success_count = 0
        # V29.4.130: learned pacing floor per background lane. After a
        # FloodWait, the lane may speed back up only to 1.5x the spacing that
        # triggered it (NoBuf measured that 250 ms between file requests still
        # flooded and settled on 300 ms; telegram-drive holds a job in a
        # cool-down state). The floor is kept for FLOOD_FLOOR_HOLD_SECONDS and
        # then relaxes by half every FLOOD_FLOOR_HALF_LIFE_SECONDS, so a short
        # burst of floods does not slow the app forever.
        self._lane_floor = {"rpc": 0.0, "range": 0.0}
        self._lane_flood_at = {"rpc": 0.0, "range": 0.0}
        # A burst of FloodWaits (several requests in flight when the limit
        # hits) is one event: only the first one teaches the floor.
        self._lane_learn_block_until = {"rpc": 0.0, "range": 0.0}
        self.flood_floor_hold_seconds = 600.0
        self.flood_floor_half_life_seconds = 600.0
        self.recent_floodwaits: list[dict] = []
        # Cross-event-loop transfer gate: Media Manager runs on its own loop,
        # while Local Upload runs on the application loop. A threading semaphore
        # acquired via asyncio.to_thread() is safe across both. Three concurrent
        # transfer workflows are allowed globally; Telegram-heavy calls inside
        # each workflow still use Telethon's request handling and FloodWait logic.
        self.transfer_sem = threading.BoundedSemaphore(3)
        self._transfer_capacity = 3
        self._active_transfer_slots = 0
        # Playback traffic has priority over byte-range and bulk-transfer lanes.
        # Lightweight RPC work such as metadata/catalogue lookups may continue
        # during playback through the shared RPC rate limiter; this avoids making
        # a capable machine appear idle while protecting the actual media stream.
        self._playback_cond = threading.Condition(threading.RLock())
        self._playback_blocks_background = True


    def set_background_paused(self, paused: bool):
        """Pause/resume new background Telegram transfer units globally."""
        with self._playback_cond:
            self._background_paused = bool(paused)
            self._playback_cond.notify_all()
        with self._range_cond:
            self._range_cond.notify_all()

    async def wait_for_background_resume_async(self):
        while True:
            with self._playback_cond:
                paused = self._background_paused
            if not paused:
                return
            await asyncio.sleep(0.1)

    def set_playback_policy(self, blocks_background: bool):
        with self._playback_cond:
            self._playback_blocks_background = bool(blocks_background)
            self._playback_cond.notify_all()

    def playback_enter(self):
        """Mark interactive video playback active across all Telegram traffic."""
        with self._playback_cond:
            self.state.playback_streams += 1
            self.state.playback_active = self.state.playback_streams > 0
            self._playback_cond.notify_all()

    def playback_exit(self):
        """Release one playback stream and wake waiting background traffic."""
        with self._playback_cond:
            self.state.playback_streams = max(0, self.state.playback_streams - 1)
            self.state.playback_active = self.state.playback_streams > 0
            self._playback_cond.notify_all()

    def wait_for_playback_clear(self):
        """Block a non-playback Telegram operation until playback is idle."""
        with self._playback_cond:
            while self.state.playback_active and self._playback_blocks_background:
                self._playback_cond.wait(timeout=0.25)

    async def wait_for_playback_clear_async(self):
        await asyncio.to_thread(self.wait_for_playback_clear)


    # ------------------------------------------------------------------
    # SUBSECTION: Status and download limits
    # Status snapshot and download lane limits.
    # ------------------------------------------------------------------
    def snapshot(self):
        """Return a JSON-friendly copy of governor state and effective limits."""
        with self.lock:
            d = asdict(self.state)
            d["range_interval_seconds"] = round(self._range_interval, 3)
            d["range_throughput_bps"] = round(self.state.bytes_per_second, 1)
            d["flood_wait_remaining"] = max(
                0, round(self.state.flood_wait_until - time.time(), 1)
            )
            d["cooldown_remaining"] = max(
                0, round(self.state.cooldown_until - time.time(), 1)
            )
            d["traffic_mode"] = "PLAYBACK_PRIORITY" if self.state.playback_active and self._playback_blocks_background else ("BALANCED" if self.state.playback_active else "NORMAL")
            d["playback_gate"] = self._playback_blocks_background
            d["playback_range_reserved"] = self.state.playback_range_reservations > 0
            d["playback_range_waiting"] = self.state.playback_range_waiting
            d["playback_range_deadline_seconds"] = float(getattr(config, "TRANSFER_GOVERNOR_PLAYBACK_DEADLINE_SECONDS", 3.0))
            d["rpc_interval_seconds"] = round(self._rpc_interval, 3)
            d["rpc_floor_seconds"] = round(self.lane_floor("rpc"), 3)
            d["range_floor_seconds"] = round(self.lane_floor("range"), 3)
            d["recent_floodwaits"] = list(self.recent_floodwaits[-10:])
            d["rpc_active"] = self._active_rpc_slots
            d["playback_rpc_active"] = self._active_playback_rpc_slots
            d["transfer_active"] = int(self._active_transfer_slots)
            d["transfer_capacity"] = int(self._transfer_capacity)
            d["playback_rpc_max"] = self._playback_rpc_max
            d["playback_range_limit"] = self._playback_range_limit
            d["playback_range_active"] = self._active_playback_range_slots
            with self._global_range_cond:
                d["telegram_range_active_global"] = self._global_range_active
                d["telegram_range_limit_global"] = self._telegram_global_range_limit
                d["telegram_range_active_small"] = self._global_range_active_by_class["small"]
                d["telegram_range_active_large"] = self._global_range_active_by_class["large"]
                d["telegram_range_limit_small"] = self._telegram_small_range_limit
                d["telegram_range_limit_large"] = self._telegram_large_range_limit
                d["telegram_file_limit_small"] = self._telegram_small_file_limit
                d["telegram_file_limit_large"] = self._telegram_large_file_limit
                d["telegram_file_active_small"] = self._active_file_operations["small"]
                d["telegram_file_active_large"] = self._active_file_operations["large"]
                d["telegram_file_active_by_dc"] = {
                    f"{dc}:{kind}": int(value)
                    for (dc, kind), value in self._active_file_operations_by_dc.items() if value
                }
                self._restore_download_lanes_locked()
                d["download_flood_wait_remaining"] = max(
                    0.0, round(max(self._download_lane_pause_until.values()) - time.time(), 1)
                )
                d["download_flood_wait_message"] = (
                    f"Telegram asked to slow down — resuming in {int(max(0, max(self._download_lane_pause_until.values()) - time.time()) + 0.999)}s"
                    if max(self._download_lane_pause_until.values()) > time.time() else ""
                )
                d["download_range_floodwaits"] = int(self._download_lane_floodwaits["range"])
                d["download_playback_floodwaits"] = int(self._download_lane_floodwaits["playback"])
                d["download_range_concurrency_reduction"] = int(self._download_lane_reduction["range"])
                d["download_playback_concurrency_reduction"] = int(self._download_lane_reduction["playback"])
                d["download_fingerprint_floodwaits"] = int(self._download_lane_floodwaits["fingerprint"])
                d["download_fingerprint_flood_wait_remaining"] = max(0.0, round(self._download_lane_pause_until["fingerprint"] - time.time(), 1))
                d["file_reference_refreshes"] = int(self._download_file_reference_refreshes)
                d["download_floodwaits_by_channel"] = {str(k): int(v) for k, v in self._download_channel_floodwaits.items()}
                d["file_reference_refreshes_by_channel"] = {str(k): int(v) for k, v in self._download_file_reference_refreshes_by_channel.items()}
                d["telegram_playback_waiters_global"] = self._global_playback_waiters
            d["rpc_queueing"] = max(0, int(self.state.active_rpc) - self._active_rpc_slots)
            with self._upload_cond:
                self._maybe_ramp_upload_locked()
                ux, uy = self._effective_upload_xy_locked()
                d["upload_x"] = int(ux)
                d["upload_y"] = int(uy)
                d["upload_configured_x"] = int(self._upload_x)
                d["upload_configured_y"] = int(self._upload_y)
                d["upload_active_parts"] = int(self._upload_active_parts)
                d["upload_max_parts"] = int(ux * uy)
                d["upload_floodwaits"] = int(self._upload_floodwait_count)
                d["upload_last_wait_seconds"] = float(self._upload_last_wait_seconds)
                d["upload_flood_wait_remaining"] = max(0, round(self._upload_pause_until - time.time(), 1))
                d["upload_lane_mode"] = "PLAYBACK_LIMITED" if self.state.playback_active else "ADAPTIVE"
            return d

    def set_telegram_download_limits(self, small: int | None, large: int | None, *, refreshed_at: float | None = None):
        """Apply Telegram app-config limits to FILE operations, not part requests.

        The API's small/large queue values describe how many files can be active
        at once. Byte ranges inside a current playback file have their own
        throughput-driven pool and must not collapse to (for example) two
        512-KiB requests merely because `large_queue_max_active_operations_count`
        is two.
        """
        def clamp(value, fallback):
            try:
                return max(1, min(64, int(value)))
            except (TypeError, ValueError, OverflowError):
                return int(fallback)
        with self._global_range_cond:
            self._telegram_small_file_limit = clamp(small, self._telegram_small_file_limit)
            self._telegram_large_file_limit = clamp(large, self._telegram_large_file_limit)
            # Range concurrency is independent of Telegram's per-file queue
            # counts. A fixed safe ceiling bounds scan/preview part requests;
            # playback has a separate adaptive permit lane.
            background_limit = max(1, min(16, int(getattr(config, "TELEGRAM_BACKGROUND_RANGE_CONCURRENCY", 8))))
            self._telegram_small_range_limit = background_limit
            self._telegram_large_range_limit = background_limit
            self._telegram_global_range_limit = background_limit
            self.state.max_ranges = background_limit
            if refreshed_at is not None:
                self._telegram_app_config_refreshed_at = float(refreshed_at)
            self._global_range_cond.notify_all()
        with self._range_cond:
            self._range_cond.notify_all()

    def _restore_download_lanes_locked(self):
        now = time.time()
        for lane in ("range", "playback", "fingerprint"):
            if self._download_lane_reduced_until[lane] and now >= self._download_lane_reduced_until[lane]:
                self._download_lane_reduction[lane] = 0
                self._download_lane_reduced_until[lane] = 0.0
            if self._download_lane_pause_until[lane] and now >= self._download_lane_pause_until[lane]:
                self._download_lane_pause_until[lane] = 0.0


    # ------------------------------------------------------------------
    # SUBSECTION: File slots and download flood handling
    # Per-file operation slots and download FloodWait bookkeeping.
    # ------------------------------------------------------------------
    async def _acquire_file_operation_slot(self, *, file_size: int | None,
                                           dc_id: int | None = None,
                                           allow_during_playback: bool = False):
        """Acquire Telegram's app-config file-operation quota for one DC."""
        size = max(0, int(file_size or 0))
        kind = "small" if size < 20 * 1024 * 1024 else "large"
        dc = max(0, int(dc_id or 0))
        key = (dc, kind)
        while True:
            await self.wait_for_background_resume_async()
            if not allow_during_playback:
                await self.wait_for_playback_clear_async()
            with self._global_range_cond:
                limit = self._telegram_small_file_limit if kind == "small" else self._telegram_large_file_limit
                active_dc = self._active_file_operations_by_dc.get(key, 0)
                with self._playback_cond:
                    playback_active = bool(self.state.playback_active and self._playback_blocks_background)
                if active_dc < limit and (allow_during_playback or not playback_active):
                    self._active_file_operations_by_dc[key] = active_dc + 1
                    self._active_file_operations[kind] += 1
                    return key
            await asyncio.sleep(0.05)

    def _release_file_operation_slot(self, key):
        with self._global_range_cond:
            dc, kind = key if isinstance(key, tuple) else (0, str(key))
            self._active_file_operations[kind] = max(0, self._active_file_operations[kind] - 1)
            active = max(0, self._active_file_operations_by_dc.get((dc, kind), 0) - 1)
            if active:
                self._active_file_operations_by_dc[(dc, kind)] = active
            else:
                self._active_file_operations_by_dc.pop((dc, kind), None)
            self._global_range_cond.notify_all()

    @asynccontextmanager
    async def telegram_file_slot(self, *, file_size: int | None = None,
                                  dc_id: int | None = None,
                                  allow_during_playback: bool = False):
        """Bound active file-byte operations separately for every Telegram DC."""
        key = await self._acquire_file_operation_slot(
            file_size=file_size, dc_id=dc_id, allow_during_playback=allow_during_playback
        )
        try:
            yield
        finally:
            self._release_file_operation_slot(key)

    def note_download_floodwait(self, seconds: float, *, lane: str = "range",
                                request: str | None = None, channel_id: int = 0,
                                message_id: int = 0):
        """Pause/back off only the download lane that received a FloodWait."""
        lane_value = str(lane or "range").lower()
        lane = lane_value if lane_value in {"range", "playback", "fingerprint"} else "range"
        seconds = max(1.0, min(float(seconds or 1.0), 86400.0))
        now = time.time()
        with self._global_range_cond:
            self._restore_download_lanes_locked()
            self._download_lane_pause_until[lane] = max(self._download_lane_pause_until[lane], now + seconds)
            self._download_lane_reduced_until[lane] = max(self._download_lane_reduced_until[lane], now + 300.0)
            max_reduction = (max(0, self._telegram_global_range_limit - 1) if lane == "range" else
                             max(0, self._playback_range_limit - 1) if lane == "playback" else 0)
            self._download_lane_reduction[lane] = min(max_reduction, self._download_lane_reduction[lane] + 1)
            self._download_lane_floodwaits[lane] += 1
            self._download_lane_last_wait_seconds[lane] = seconds
            cid = int(channel_id or 0)
            if cid:
                self._download_channel_floodwaits[cid] = self._download_channel_floodwaits.get(cid, 0) + 1
            self._global_range_cond.notify_all()
        source = f"{request}, {lane} lane" if request else f"{lane} lane"
        location = f", channel {int(channel_id)} message {int(message_id)}" if channel_id else ""
        self.db.log(
            f"Telegram asked to slow down — resuming in {seconds:.0f}s ({source}{location}); only this download lane paused",
            "WARN",
        )

    async def wait_download_lane_async(self, lane: str = "range"):
        lane_value = str(lane or "range").lower()
        lane = lane_value if lane_value in {"range", "playback", "fingerprint"} else "range"
        while True:
            with self._global_range_cond:
                self._restore_download_lanes_locked()
                until = float(self._download_lane_pause_until[lane])
            remaining = until - time.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.5))

    def note_file_reference_refresh(self, *, channel_id: int = 0, message_id: int = 0):
        with self._global_range_cond:
            self._download_file_reference_refreshes += 1
            cid = int(channel_id or 0)
            if cid:
                self._download_file_reference_refreshes_by_channel[cid] = self._download_file_reference_refreshes_by_channel.get(cid, 0) + 1
        try:
            self.db.log(
                f"Refreshed expired Telegram file reference for channel {int(channel_id or 0)} message {int(message_id or 0)}",
                "INFO",
            )
        except Exception:
            pass

    def note_channel_scan_floodwait(self, channel_id: int):
        """Count a history/metadata API FloodWait against its channel without pausing byte lanes."""
        cid = int(channel_id or 0)
        if not cid:
            return
        with self._global_range_cond:
            self._download_channel_floodwaits[cid] = self._download_channel_floodwaits.get(cid, 0) + 1

    def channel_download_floodwaits(self, channel_id: int) -> int:
        with self._global_range_cond:
            return int(self._download_channel_floodwaits.get(int(channel_id or 0), 0))


    # ------------------------------------------------------------------
    # SUBSECTION: Global range slots and playback lanes
    # Range slots, playback waiters, lane configuration and emergency mode.
    # ------------------------------------------------------------------
    async def _acquire_global_range_slot(self, *, file_size: int | None, playback: bool,
                                         allow_during_playback: bool = False) -> str:
        if playback:
            # The app-config queues count concurrent FILE operations, not byte
            # parts. The current media file gets its independently tuned 6–10
            # range requests from _acquire_playback_range_permit_async().
            return "playback"
        size = max(0, int(file_size or 0))
        size_class = "small" if 0 < size < 20 * 1024 * 1024 else "large"
        while True:
            with self._global_range_cond:
                # These are request-part permits, independent of app-config
                # file operation counts (those are enforced by telegram_file_slot).
                self._restore_download_lanes_locked()
                class_limit = max(1, self._telegram_global_range_limit - self._download_lane_reduction["range"])
                playback_waiting = self._global_playback_waiters > 0
                with self._playback_cond:
                    playback_active = bool(self.state.playback_active and self._playback_blocks_background)
                background_blocked = (not playback and not allow_during_playback
                                      and (playback_active or playback_waiting))
                if (self._global_range_active < self._telegram_global_range_limit
                        and self._global_range_active_by_class[size_class] < class_limit
                        and (playback or not playback_waiting)
                        and not background_blocked):
                    self._global_range_active += 1
                    self._global_range_active_by_class[size_class] += 1
                    return size_class
            await asyncio.sleep(0.02)

    def _release_global_range_slot(self, size_class: str):
        if size_class == "playback":
            return
        with self._global_range_cond:
            self._global_range_active = max(0, self._global_range_active - 1)
            self._global_range_active_by_class[size_class] = max(
                0, self._global_range_active_by_class.get(size_class, 0) - 1
            )
            self._global_range_cond.notify_all()

    def _register_global_playback_waiter(self):
        with self._global_range_cond:
            self._global_playback_waiters += 1
            self._global_range_cond.notify_all()

    def _release_global_playback_waiter(self):
        with self._global_range_cond:
            self._global_playback_waiters = max(0, self._global_playback_waiters - 1)
            self._global_range_cond.notify_all()

    def configure_playback_lanes(self, normal: int, emergency: int | None = None):
        """Set foreground media lanes for the signed-in account (V29.4.121).

        Telegram Web K keeps up to 28 MB of file parts in flight per DC for
        Premium and 4.5 MB for free accounts (apiFileManager download deltas).
        The fixed 4-lane (2 MB) budget was far below that.
        """
        normal = max(1, int(normal))
        emergency = max(normal, int(emergency or normal))
        with self.lock:
            self._playback_lane_target = normal
            self._playback_lane_emergency = emergency
            if time.time() >= self.state.flood_wait_until:
                self._playback_range_limit = normal
                self._playback_rpc_max = normal
            self.state.max_playback_ranges = self._playback_range_limit
        with self._range_cond:
            self._range_cond.notify_all()

    def set_playback_emergency(self, emergency: bool):
        """Raise/release the dedicated playback lane during browser starvation."""
        emergency = bool(emergency)
        with self.lock:
            flood_active = time.time() < self.state.flood_wait_until
            if flood_active:
                range_limit = 2
                rpc_limit = 2
            elif emergency:
                range_limit = max(1, int(getattr(self, "_playback_lane_emergency", getattr(config, "PLAYBACK_FOREGROUND_EMERGENCY_CONCURRENCY", 10))))
                rpc_limit = range_limit
            else:
                range_limit = max(1, int(getattr(self, "_playback_lane_target", getattr(config, "PLAYBACK_FOREGROUND_RANGE_CONCURRENCY", 6))))
                rpc_limit = range_limit
            self._playback_range_limit = range_limit
            self._playback_rpc_max = rpc_limit
            self.state.max_playback_ranges = range_limit
        with self._range_cond:
            self._range_cond.notify_all()
        with self._playback_rpc_lock:
            self._playback_rpc_lock.notify_all()


    # ------------------------------------------------------------------
    # SUBSECTION: Lane learning and range outcomes
    # Learned lane floors and notes on flood, network and range results.
    # ------------------------------------------------------------------
    def lane_floor(self, lane: str) -> float:
        """Current learned minimum spacing for a background lane (seconds)."""
        floor = float(self._lane_floor.get(lane, 0.0) or 0.0)
        if floor <= 0:
            return 0.0
        age = time.monotonic() - float(self._lane_flood_at.get(lane, 0.0) or 0.0)
        extra = age - self.flood_floor_hold_seconds
        if extra > 0:
            floor *= 0.5 ** (extra / max(1.0, self.flood_floor_half_life_seconds))
        return floor

    def _learn_floor(self, lane: str, interval_before: float, interval_max: float, seconds: float) -> bool:
        """Learn a lane's floor from the spacing that flooded; False within a burst."""
        now = time.monotonic()
        if now < self._lane_learn_block_until[lane]:
            return False                 # same burst: already learned and slowed
        self._lane_learn_block_until[lane] = now + float(seconds)
        current = self.lane_floor(lane)
        self._lane_floor[lane] = min(interval_max, max(current, float(interval_before) * 1.5))
        self._lane_flood_at[lane] = now
        return True

    @staticmethod
    def flood_lane_for(request_name: str | None, playback: bool = False) -> str:
        if playback:
            return "playback"
        name = str(request_name or "")
        if name in {"GetFileRequest", "GetCdnFileRequest", "GetFile"}:
            return "range"
        if name in {"SaveFilePartRequest", "SaveBigFilePartRequest"}:
            # Upload parts have their own Telegram limits; they must not teach
            # the API lane a floor that slows every other request.
            return "upload"
        return "rpc"

    def note_floodwait(self, seconds: float, kind: str | None = None, request: str | None = None):
        """Record a Telegram FloodWait.

        ``kind`` is the lane whose pace caused it: "rpc" (API calls), "range"
        (file byte ranges), "playback" or "upload". The two background lanes
        learn a pacing floor from it (V29.4.130); other and unknown kinds keep
        the old behaviour of slowing both lanes for a while. ``request`` is
        only used to say in the log what flooded.
        """
        with self.lock:
            self.state.total_rpc_floodwaits += 1
            self.state.flood_wait_until = max(
                self.state.flood_wait_until, time.time() + seconds
            )
            self.state.max_rpc = 1
            self.state.max_ranges = 1
            self._playback_range_limit = max(1, min(self._playback_range_limit, 2))
            self._playback_rpc_max = max(1, min(self._playback_rpc_max, 2))
            self.state.max_playback_ranges = self._playback_range_limit
            self.state.last_error = f"FloodWait {seconds:.0f}s" + (f" ({request})" if request else "")
            self.recent_floodwaits.append({
                "at": time.time(), "seconds": float(seconds),
                "lane": kind or "unknown", "request": str(request or ""),
            })
            if len(self.recent_floodwaits) > 50:
                self.recent_floodwaits = self.recent_floodwaits[-50:]
        # A FloodWait attributed to one background lane ("rpc" or "range")
        # teaches and slows only that lane, so the other lane's spacing is
        # never inflated by it (and never learned from an inflated value).
        # Unattributed, playback and upload FloodWaits keep the old
        # behaviour: both lanes slow down temporarily, nothing is learned.
        slow_range = kind not in ("rpc",)
        slow_rpc = kind not in ("range",)
        with self._range_schedule_lock:
            if kind == "range" and not self._learn_floor("range", self._range_interval, self._range_interval_max, seconds):
                slow_range = False
            if slow_range:
                self._range_interval = min(self._range_interval_max, max(self._range_interval * 2.0, 0.50))
        with self._rpc_schedule_lock:
            if kind == "rpc" and not self._learn_floor("rpc", self._rpc_interval, self._rpc_interval_max, seconds):
                slow_rpc = False
            if slow_rpc:
                self._rpc_interval = min(self._rpc_interval_max, max(self._rpc_interval * 2.0, 0.50))
            self._rpc_next_at = max(self._rpc_next_at, time.monotonic() + min(seconds, 60.0))
            self._range_next_at = max(self._range_next_at, time.monotonic() + min(seconds, 60.0))
        self.db.log(
            f"Telegram FloodWait: {seconds:.0f}s"
            + (f" ({request}{', ' + kind if kind else ''})" if request else (f" ({kind})" if kind else "")),
            "WARN",
        )

    def note_network_error(self, message: str):
        with self.lock:
            self.state.cooldown_until = max(
                self.state.cooldown_until, time.time() + 5
            )
            self.state.max_rpc = 1
            self.state.max_ranges = 1
            self._playback_range_limit = max(1, min(self._playback_range_limit, 2))
            self._playback_rpc_max = max(1, min(self._playback_rpc_max, 2))
            self.state.max_playback_ranges = self._playback_range_limit
            self.state.last_error = message[:300]
        with self._range_schedule_lock:
            self._range_interval = min(self._range_interval_max, max(self._range_interval * 1.5, 0.15))
        with self._rpc_schedule_lock:
            self._rpc_interval = min(self._rpc_interval_max, max(self._rpc_interval * 1.5, 0.20))
        self.db.log(f"Network cooldown: {message}", "WARN")

    def note_range_success(self, amount: int, elapsed: float):
        if amount <= 0 or elapsed <= 0:
            return
        bps = float(amount) / float(elapsed)
        with self.lock:
            self.state.bytes_per_second = (0.75 * self.state.bytes_per_second + 0.25 * bps) if self.state.bytes_per_second else bps
            flood_active = time.time() < self.state.flood_wait_until
        with self._range_schedule_lock:
            self._range_samples.append(bps)
            if len(self._range_samples) > 12:
                self._range_samples = self._range_samples[-12:]
            self._range_success_count += 1
            if not flood_active and len(self._range_samples) >= 6 and self._range_success_count % 6 == 0:
                recent = statistics.fmean(self._range_samples[-6:])
                if self.state.bytes_per_second and recent >= self.state.bytes_per_second * 0.80:
                    self._range_interval = max(self._range_interval_min, self.lane_floor("range"),
                                               self._range_interval * 0.90)

        with self.lock:
            if time.time() >= self.state.flood_wait_until and self.state.max_ranges == 1:
                # Recover the background range slot gradually once Telegram has
                # stopped rate-limiting us.
                self.state.max_ranges = 2
            if time.time() >= self.state.flood_wait_until:
                target_ranges = max(1, int(getattr(self, "_playback_lane_target", getattr(config, "PLAYBACK_FOREGROUND_RANGE_CONCURRENCY", 6))))
                target_rpc = target_ranges
                if self._playback_range_limit < target_ranges:
                    self._playback_range_limit += 1
                if self._playback_rpc_max < target_rpc:
                    self._playback_rpc_max += 1
                self.state.max_playback_ranges = self._playback_range_limit

    def note_range_failure(self):
        with self._range_schedule_lock:
            self._range_interval = min(self._range_interval_max, max(self._range_interval * 1.20, 0.10))



    # ------------------------------------------------------------------
    # SUBSECTION: RPC permits
    # General and playback RPC permits.
    # ------------------------------------------------------------------
    def _acquire_rpc_permit(self):
        with self._rpc_cond:
            while True:
                with self.lock:
                    limit = max(1, int(self.state.max_rpc))
                if self._active_rpc_slots < limit:
                    self._active_rpc_slots += 1
                    with self.lock:
                        self.state.active_rpc = self._active_rpc_slots
                    return
                self._rpc_cond.wait(timeout=0.25)

    def _release_rpc_permit(self):
        with self._rpc_cond:
            self._active_rpc_slots = max(0, self._active_rpc_slots - 1)
            with self.lock:
                self.state.active_rpc = self._active_rpc_slots
            self._rpc_cond.notify_all()

    async def _acquire_playback_rpc_permit_async(self, poll_seconds: float = 0.02):
        while True:
            with self._playback_rpc_lock:
                if self._active_playback_rpc_slots < self._playback_rpc_max:
                    self._active_playback_rpc_slots += 1
                    return
            await asyncio.sleep(poll_seconds)

    def _release_playback_rpc_permit(self):
        with self._playback_rpc_lock:
            self._active_playback_rpc_slots = max(0, self._active_playback_rpc_slots - 1)
            self._playback_rpc_lock.notify_all()

    @asynccontextmanager
    async def _playback_rpc_slot(self):
        # Do not make foreground media wait on the background RPC semaphore.
        # FloodWait/network cooldown still applies because it is Telegram-wide.
        while True:
            with self.lock:
                wait_until = max(self.state.flood_wait_until, self.state.cooldown_until)
            remaining = wait_until - time.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 0.5))
        await self._acquire_playback_rpc_permit_async()
        try:
            with self._rpc_schedule_lock:
                now = time.monotonic()
                wait = max(0.0, self._playback_rpc_next_at - now)
                self._playback_rpc_next_at = max(now, self._playback_rpc_next_at) + self._playback_rpc_interval
            if wait > 0:
                await asyncio.sleep(wait)
            with self.lock:
                self.state.total_rpc_requests += 1
                self.state.last_rpc_at = time.time()
            yield
        finally:
            self._release_playback_rpc_permit()


    # ------------------------------------------------------------------
    # SUBSECTION: Upload permits and ramping
    # Adaptive upload concurrency and upload outcome notes.
    # ------------------------------------------------------------------
    def _maybe_ramp_upload_locked(self) -> None:
        """Increase upload parallelism one safe step after 30s without a wait."""
        now = time.monotonic()
        if time.time() < self._upload_pause_until:
            return
        # Do not increase limits merely because the application has been idle.
        # Ramp only while an upload is actively exercising the current limit.
        if self._upload_active_parts <= 0:
            return
        if self._upload_last_flood_monotonic and now - self._upload_last_flood_monotonic < 30.0:
            return
        if now - self._upload_last_ramp_monotonic < 30.0:
            return
        levels = self._upload_step_levels
        current = (self._upload_x, self._upload_y)
        try:
            index = levels.index(current)
        except ValueError:
            index = -2 if current == (8, 1) else -1
        if current == (8, 1):
            self._upload_x, self._upload_y = (8, 2)
            self._upload_last_ramp_monotonic = now
            self._upload_cond.notify_all()
        elif index + 1 < len(levels):
            self._upload_x, self._upload_y = levels[index + 1]
            self._upload_last_ramp_monotonic = now
            self._upload_cond.notify_all()

    def _effective_upload_xy_locked(self) -> tuple[int, int]:
        self._maybe_ramp_upload_locked()
        if self.state.playback_active:
            return 2, 1
        return max(2, int(self._upload_x)), max(1, int(self._upload_y))

    def _acquire_upload_permit(self) -> None:
        """Blocking upload permit used from asyncio.to_thread by all clients."""
        with self._upload_cond:
            while True:
                self._maybe_ramp_upload_locked()
                now_wall = time.time()
                remaining = self._upload_pause_until - now_wall
                if remaining > 0:
                    self._upload_cond.wait(timeout=min(remaining, 0.5))
                    continue
                x, y = self._effective_upload_xy_locked()
                if self._upload_active_parts < x * y:
                    if self._upload_active_parts == 0 and self._upload_last_part_success_monotonic <= 0:
                        self._upload_last_ramp_monotonic = time.monotonic()
                    self._upload_active_parts += 1
                    return
                self._upload_cond.wait(timeout=0.1)

    def _release_upload_permit(self) -> None:
        with self._upload_cond:
            self._upload_active_parts = max(0, self._upload_active_parts - 1)
            self._upload_cond.notify_all()

    @asynccontextmanager
    async def telegram_upload_slot(self):
        """Dedicated upload part lane; playback reduces, but never blocks, uploads."""
        await asyncio.to_thread(self._acquire_upload_permit)
        try:
            yield
        finally:
            self._release_upload_permit()

    def note_upload_connection_failure(self, available_queues: int, error: str = "") -> None:
        """Keep diagnostics/concurrency honest if an extra upload TCP sender cannot open."""
        now = time.monotonic()
        available_queues = max(1, min(4, int(available_queues or 1)))
        with self._upload_cond:
            self._upload_y = min(self._upload_y, available_queues)
            self._upload_last_flood_monotonic = now
            self._upload_last_ramp_monotonic = now
            self._upload_cond.notify_all()
        try:
            self.db.log(
                f"Telegram parallel upload connection unavailable; using {available_queues} queue(s)"
                + (f": {str(error)[:300]}" if error else ""),
                "WARN",
            )
        except Exception:
            pass

    def note_upload_part_success(self, elapsed: float = 0.0) -> None:
        """Record part completion without teaching the shared API lane a pace."""
        try:
            with self._upload_cond:
                self._upload_last_part_success_monotonic = time.monotonic()
                self._upload_cond.notify_all()
        except Exception:
            pass

    def note_upload_floodwait(self, seconds: float, request: str | None = None) -> None:
        """Pause only uploads and back off Y before X after a Telegram wait."""
        seconds = max(1.0, float(seconds or 1.0))
        now_wall = time.time()
        now_mono = time.monotonic()
        with self._upload_cond:
            already_paused = self._upload_pause_until > now_wall
            self._upload_pause_until = max(self._upload_pause_until, now_wall + seconds)
            self._upload_floodwait_count += 1
            self._upload_last_wait_seconds = seconds
            self._upload_last_flood_monotonic = now_mono
            self._upload_last_ramp_monotonic = now_mono
            # Multiple parallel parts may receive the same wait. Back off only
            # once during an already-active pause, but extend the pause if needed.
            if not already_paused:
                if self._upload_y > 1:
                    self._upload_y -= 1
                elif self._upload_x > 6:
                    self._upload_x = 6
                elif self._upload_x > 4:
                    self._upload_x = 4
                elif self._upload_x > 2:
                    self._upload_x = 2
            self._upload_cond.notify_all()
        try:
            self.db.log(
                f"Telegram upload FloodWait: {seconds:.0f}s"
                + (f" ({request})" if request else "")
                + "; upload lane paused; API and playback lanes unchanged",
                "WARN",
            )
        except Exception:
            pass


    # ------------------------------------------------------------------
    # SUBSECTION: Public RPC slot
    # RPC slot context manager and success notes.
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def telegram_rpc_slot(self, playback: bool = False):
        """Global API request gate shared across every Telegram client.

        Background calls wait behind playback when configured. Playback calls
        still use the same single adaptive RPC lane, so multiple playback
        connections cannot create independent request bursts.
        """
        # Interactive playback uses a dedicated foreground lane. It is never
        # queued behind metadata/entity/background RPCs. The lane is bounded
        # and observes Telegram-wide FloodWait/network cooldown.
        if playback:
            async with self._playback_rpc_slot():
                yield
            return

        # Ordinary RPCs yield while interactive playback is active.
        await self.wait_for_playback_clear_async()

        # A server-directed FloodWait or transient network cooldown blocks new
        # requests before they reach Telegram.
        while True:
            now = time.time()
            with self.lock:
                wait_until = max(self.state.flood_wait_until, self.state.cooldown_until)
            remaining = wait_until - now
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 1.0))
            if not playback:
                await self.wait_for_playback_clear_async()

        await asyncio.to_thread(self._acquire_rpc_permit)
        try:
            with self._rpc_schedule_lock:
                now_m = time.monotonic()
                wait = max(0.0, self._rpc_next_at - now_m)
                self._rpc_next_at = max(now_m, self._rpc_next_at) + self._rpc_interval
            if wait > 0:
                await asyncio.sleep(wait)
            with self.lock:
                self.state.total_rpc_requests += 1
                self.state.last_rpc_at = time.time()
            yield
        finally:
            self._release_rpc_permit()

    def note_rpc_success(self, elapsed: float):
        latency_ms = max(0.0, float(elapsed) * 1000.0)
        with self.lock:
            self.state.total_rpc_success += 1
            self.state.last_rpc_latency_ms = round(latency_ms, 1)
            flood_active = time.time() < self.state.flood_wait_until
        if not flood_active:
            with self._rpc_schedule_lock:
                self._rpc_latency_samples.append(latency_ms)
                if len(self._rpc_latency_samples) > 20:
                    self._rpc_latency_samples = self._rpc_latency_samples[-20:]
                # Recover very slowly. Avoid chasing short-term throughput
                # spikes; safety is more important than maximum request rate.
                if len(self._rpc_latency_samples) >= 8 and len(self._rpc_latency_samples) % 4 == 0:
                    self._rpc_interval = max(self._rpc_interval_min, self.lane_floor("rpc"),
                                             self._rpc_interval * 0.95)


    # ------------------------------------------------------------------
    # SUBSECTION: Range and transfer permits
    # Range, transfer and playback range permits plus public slot wrappers.
    # ------------------------------------------------------------------
    def _acquire_range_permit(self, *, playback: bool = False):
        while True:
            with self._playback_cond:
                background_paused = self._background_paused
                playback_waiting = self._playback_range_waiting > 0
            if not playback and (background_paused or playback_waiting):
                time.sleep(0.05)
                continue
            with self._range_cond:
                limit = max(1, int(self.state.max_ranges))
                if not playback:
                    # Permanently reserve one Telegram byte-range lane for
                    # interactive playback. This removes the race where a
                    # background snapshot starts before the user opens a video.
                    limit = max(1, limit - 1)
                if self._active_range_slots < limit:
                    self._active_range_slots += 1
                    with self.lock:
                        self.state.active_ranges = self._active_range_slots
                    return
                self._range_cond.wait(timeout=0.25)

    def _release_range_permit(self):
        with self._range_cond:
            self._active_range_slots = max(0, self._active_range_slots - 1)
            with self.lock:
                self.state.active_ranges = self._active_range_slots
            self._range_cond.notify_all()

    async def _acquire_range_permit_async(self, *, playback: bool, poll_seconds: float = 0.05):
        """Cancellation-aware asynchronous range admission.

        The previous implementation delegated the blocking Condition wait to
        ``asyncio.to_thread``. Cancelling the coroutine did not stop that thread,
        so a seek/preemption could leave a Telegram range slot waiter alive for
        minutes. Playback and background admission now poll the existing governor
        state without creating another scheduler or semaphore.
        """
        while True:
            with self._playback_cond:
                background_paused = self._background_paused
                playback_waiting = self._playback_range_waiting > 0
            if not playback and (background_paused or playback_waiting):
                await asyncio.sleep(poll_seconds)
                continue
            with self._range_cond:
                limit = max(1, int(self.state.max_ranges))
                if not playback:
                    limit = max(1, limit - 1)
                if self._active_range_slots < limit:
                    self._active_range_slots += 1
                    with self.lock:
                        self.state.active_ranges = self._active_range_slots
                    return
            await asyncio.sleep(poll_seconds)

    async def _acquire_transfer_permit_async(self, poll_seconds: float = 0.05):
        """Cancellation-aware acquisition of the existing transfer semaphore."""
        while True:
            if self.transfer_sem.acquire(blocking=False):
                return
            await asyncio.sleep(poll_seconds)

    async def _acquire_playback_range_permit_async(self, poll_seconds: float = 0.02):
        """Acquire one of the dedicated foreground media range lanes.

        Playback ranges intentionally do not consume the ordinary Telegram
        range pool. This is the key distinction between foreground media and
        metadata/cache work: a slow background range can no longer reduce
        active video concurrency to one or two lanes.
        """
        while True:
            with self._global_range_cond:
                self._restore_download_lanes_locked()
                reduction = int(self._download_lane_reduction["playback"])
            with self.lock:
                limit = max(1, int(self._playback_range_limit) - reduction)
            with self._range_cond:
                if self._active_playback_range_slots < limit:
                    self._active_playback_range_slots += 1
                    self.state.active_playback_ranges = self._active_playback_range_slots
                    return
            await asyncio.sleep(poll_seconds)

    def _release_playback_range_permit(self):
        with self._range_cond:
            self._active_playback_range_slots = max(0, self._active_playback_range_slots - 1)
            with self.lock:
                self.state.active_playback_ranges = self._active_playback_range_slots
            self._range_cond.notify_all()

    @asynccontextmanager
    async def telegram_playback_range_slot(
        self, *, deadline_seconds: float | None = None,
        file_size: int | None = None, request_bytes: int | None = None,
    ):
        """Admit foreground media ranges through the same global Telegram budget.

        Playback announces its wait before acquiring either its lane or the
        shared budget, so newly arriving background scans/preview requests yield
        admission to the foreground stream. Existing in-flight Telegram calls
        are not cancelled, but no new background call is admitted ahead of a
        waiting playback request.
        """
        deadline = float(
            getattr(config, "TRANSFER_GOVERNOR_PLAYBACK_DEADLINE_SECONDS", 3.0)
            if deadline_seconds is None else max(0.0, deadline_seconds)
        )
        request_started = time.monotonic()
        acquired = False
        reservation_released = False
        global_size_class = None
        playback_waiter_registered = False
        with self.lock:
            self.state.playback_range_requests += 1
        with self._playback_cond:
            self._playback_range_waiting += 1
            self.state.playback_range_waiting = self._playback_range_waiting
            self.state.playback_range_reservations += 1
        self._register_global_playback_waiter()
        playback_waiter_registered = True
        try:
            while True:
                now = time.time()
                with self.lock:
                    wait_until = max(self.state.flood_wait_until, self.state.cooldown_until)
                remaining = wait_until - now
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.5))

            await self._acquire_playback_range_permit_async()
            acquired = True
            global_size_class = await self._acquire_global_range_slot(
                file_size=file_size, playback=True
            )
            self._release_global_playback_waiter()
            playback_waiter_registered = False
            admission_wait_ms = max(0.0, (time.monotonic() - request_started) * 1000.0)
            with self.lock:
                self.state.last_playback_range_wait_ms = round(admission_wait_ms, 1)
                self.state.total_playback_range_wait_ms += admission_wait_ms
                if admission_wait_ms > deadline * 1000.0:
                    self.state.playback_range_deadline_misses += 1
            with self._playback_cond:
                self._playback_range_waiting = max(0, self._playback_range_waiting - 1)
                self.state.playback_range_waiting = self._playback_range_waiting
                self.state.playback_range_reservations = max(0, self.state.playback_range_reservations - 1)
                reservation_released = True
                self._playback_cond.notify_all()
            with self._range_schedule_lock:
                now = time.monotonic()
                next_at = getattr(self, "_playback_range_next_at", 0.0)
                interval = float(getattr(config, "PLAYBACK_FOREGROUND_RANGE_INTERVAL_SECONDS", 0.02))
                wait = max(0.0, next_at - now)
                self._playback_range_next_at = max(now, next_at) + interval
            if wait > 0:
                await asyncio.sleep(wait)
            yield
        finally:
            if playback_waiter_registered:
                self._release_global_playback_waiter()
            with self._playback_cond:
                if self._playback_range_waiting > 0:
                    self._playback_range_waiting = max(0, self._playback_range_waiting - 1)
                self.state.playback_range_waiting = self._playback_range_waiting
                if not reservation_released:
                    self.state.playback_range_reservations = max(0, self.state.playback_range_reservations - 1)
                self._playback_cond.notify_all()
            if global_size_class is not None:
                self._release_global_range_slot(global_size_class)
            if acquired:
                self._release_playback_range_permit()

    @asynccontextmanager
    async def telegram_fingerprint_slot(self):
        """Priority permit for small OSHASH reads, separate from snapshot ranges.

        Playback and global background pause always win. FloodWaits reported to
        the fingerprint lane are isolated from the regular range/download lane.
        """
        await self.wait_for_background_resume_async()
        await self.wait_for_playback_clear_async()
        while True:
            await self.wait_download_lane_async("fingerprint")
            await self._fingerprint_sem.acquire()
            with self._playback_cond:
                playback_active = bool(self.state.playback_active and self._playback_blocks_background)
            if playback_active:
                self._fingerprint_sem.release()
                await self.wait_for_playback_clear_async()
                continue
            try:
                yield
            finally:
                self._fingerprint_sem.release()
            return

    @asynccontextmanager
    async def telegram_range_slot(self, *, file_size: int | None = None, request_bytes: int | None = None,
                                  allow_during_playback: bool = False):
        """Admit scans/previews to the shared byte budget.

        Only UI live-seek previews opt into ``allow_during_playback``. Scan and
        maintenance ranges wait until playback exits; previews may compete for
        the remaining budget but yield admission whenever a playback request is
        queued.
        """
        await self.wait_for_background_resume_async()
        if not allow_during_playback:
            await self.wait_for_playback_clear_async()
        global_size_class = None
        counted_active = False
        try:
            # Do not hold a background-lane permit while waiting for playback
            # priority; otherwise two paused scan requests could occupy the lane
            # and prevent a live seek preview from entering it.
            global_size_class = await self._acquire_global_range_slot(
                file_size=file_size, playback=False, allow_during_playback=allow_during_playback
            )
            with self._range_cond:
                self._active_range_slots += 1
                self.state.active_ranges = self._active_range_slots
                counted_active = True
                self._range_cond.notify_all()
            with self._range_schedule_lock:
                now = time.monotonic()
                wait = max(0.0, self._range_next_at - now)
                self._range_next_at = max(now, self._range_next_at) + self._range_interval
            if wait > 0:
                await asyncio.sleep(wait)
            yield
        finally:
            if counted_active:
                with self._range_cond:
                    self._active_range_slots = max(0, self._active_range_slots - 1)
                    self.state.active_ranges = self._active_range_slots
                    self._range_cond.notify_all()
            if global_size_class is not None:
                self._release_global_range_slot(global_size_class)

    @asynccontextmanager
    async def telegram_transfer_slot(self):
        """Reserve a Telegram transfer lane, never starting a new unit over playback."""
        await self.wait_for_background_resume_async()
        await self.wait_for_playback_clear_async()
        await self._acquire_transfer_permit_async()
        try:
            # Playback may have started while waiting for the semaphore. Do not
            # let this transfer enter its first network unit until playback clears.
            await self.wait_for_playback_clear_async()
            with self.lock:
                self._active_transfer_slots += 1
            yield
        finally:
            with self.lock:
                self._active_transfer_slots = max(0, self._active_transfer_slots - 1)
            self.transfer_sem.release()


    # ------------------------------------------------------------------
    # SUBSECTION: Status
    # Public status payload.
    # ------------------------------------------------------------------
    def status(self):
        return self.snapshot()
