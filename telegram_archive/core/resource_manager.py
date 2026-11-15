"""Interactive resource coordinator.

Playback and the web UI are interactive workloads. Background jobs are admitted
through this coordinator so a low-spec machine can keep interactive work
responsive without shrinking the playback buffer or seek/read-ahead settings.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Snapshot and restored controls
#   - InteractiveResourceManager
#       - Configuration and background pause
#       - Category limits and status
#       - Playback tracking
#       - Background admission
#       - Policy and snapshot
# ------------------------------------------------------------------
from __future__ import annotations
from .swallowed import note as _swallowed_note
from .background_tasks import retain as _retain_task

import asyncio
import os
import time


# ==============================================================================
# SECTION: Snapshot and restored controls
# Snapshot type and restoring persisted controls.
# ==============================================================================


_RESOURCE_CONTROL_MODES = {"automatic", "playback", "balanced", "background", "manual"}
_RESOURCE_CONTROL_CATEGORIES = ("conversion", "thumbnail", "preview", "metadata", "upload")

def restore_persisted_resource_controls(db, resources) -> str:
    """Restore the last non-expiring resource policy saved by the operator.

    Temporary overrides are deliberately persisted as Automatic by the API, so
    this restores only policies that were selected with "Until changed".
    """
    try:
        saved_mode = db.get_setting("ax_resource_control_mode", "automatic")
    except Exception:
        saved_mode = "automatic"
    mode = str(saved_mode or "automatic").strip().lower()
    if mode not in _RESOURCE_CONTROL_MODES:
        try:
            db.log(f"Invalid saved resource control mode {mode!r}; using Automatic", "WARN")
        except Exception:
            _swallowed_note("resource_manager.restore_persisted_resource_controls")
            pass
        mode = "automatic"

    try:
        saved_limits = db.get_setting("ax_resource_manual_limits", {})
    except Exception:
        saved_limits = {}
    clean_limits = {}
    if isinstance(saved_limits, dict):
        for key in _RESOURCE_CONTROL_CATEGORIES:
            try:
                value = int(saved_limits[key])
                if 1 <= value <= 8:
                    clean_limits[key] = value
            except (KeyError, TypeError, ValueError, OverflowError):
                continue

    try:
        saved_gate = db.get_setting("ax_resource_playback_gate", True)
    except Exception:
        saved_gate = True
    if isinstance(saved_gate, bool):
        playback_gate = saved_gate
    elif isinstance(saved_gate, (int, float)):
        playback_gate = bool(saved_gate)
    elif isinstance(saved_gate, str):
        playback_gate = saved_gate.strip().lower() in {"1", "true", "yes", "on"}
    else:
        playback_gate = True

    label = f"Restored saved {mode} resource policy" if mode != "automatic" else ""
    resources.configure(
        mode=mode,
        limits=clean_limits,
        playback_gate=playback_gate,
        expires_seconds=0,
        label=label,
    )
    return mode


# ==============================================================================
# SECTION: InteractiveResourceManager
# Coordinates background work around interactive playback.
# ==============================================================================

class InteractiveResourceManager:
    """Priority gate: playback/UI remain responsive; background work yields.

    This is intentionally a gate, not a playback limiter. Playback never waits
    for this manager and its buffer/read-ahead values are unchanged.
    """


    # ------------------------------------------------------------------
    # SUBSECTION: Configuration and background pause
    # Setup, pause/resume and effective mode.
    # ------------------------------------------------------------------
    def __init__(self, governor=None, hardware_profile=None):
        cpu = max(1, int(os.cpu_count() or 1))
        self.hardware_profile = hardware_profile or {}
        suggested = int(self.hardware_profile.get("background_capacity") or 0)
        self.background_capacity = max(1, min(6, suggested or (cpu // 2 if cpu > 2 else 1)))
        self.media_workers = max(1, min(4, int(self.hardware_profile.get("media_workers") or 1)))
        self._category_active = {"conversion": 0, "thumbnail": 0, "preview": 0, "metadata": 0, "upload": 0}
        self.playback_streams = 0
        self.interactive_playback = False
        self._interactive_last_activity = 0.0
        self._interactive_expiry_task = None
        self.background_active = 0
        self.background_waiting = 0
        self._category_waiting: dict[str, int] = {}
        # Keep one slot available to each scheduling class on machines with at
        # least two background slots. This prevents long-running transfer,
        # thumbnail or generic jobs from occupying every slot and starving the
        # metadata/channel-scan lane. A one-slot machine remains serialized by
        # design; software cannot provide true parallelism without capacity.
        self._metadata_active = 0
        self._non_metadata_active = 0
        self.service_active = 0
        self._last_playback_activity = 0.0
        self._condition = asyncio.Condition()
        self.governor = governor
        self.control_mode = "automatic"
        self.manual_limits = {}
        self.manual_playback_gate = True
        self.override_expires_at = 0.0
        self.override_label = ""
        self.background_paused = False
        self.background_pause_reason = ""

    def configure(self, *, mode=None, limits=None, playback_gate=None, expires_seconds=None, label=""):
        """Apply operator resource policy. Safety limits remain enforced by callers/governor."""
        if mode is not None:
            mode = str(mode).strip().lower()
            if mode not in {"automatic", "playback", "balanced", "background", "manual"}:
                raise ValueError("Invalid resource control mode")
            self.control_mode = mode
        if limits is not None:
            clean = {}
            for key in ("conversion", "thumbnail", "preview", "metadata", "upload"):
                if key in limits and limits[key] is not None:
                    value = int(limits[key])
                    if value < 1 or value > 8:
                        raise ValueError(f"{key} limit must be between 1 and 8")
                    clean[key] = value
            self.manual_limits = clean
        if playback_gate is not None:
            self.manual_playback_gate = bool(playback_gate)
        if expires_seconds is not None:
            seconds = max(0, int(expires_seconds))
            self.override_expires_at = time.time() + seconds if seconds else 0.0
        self.override_label = str(label or "")[:120]
        if self.governor is not None:
            self.governor.set_playback_policy(self.playback_blocks_background())

    def pause_background(self, reason: str = "Paused by operator") -> None:
        self.background_paused = True
        self.background_pause_reason = str(reason or "Paused by operator")[:160]
        if self.governor is not None:
            self.governor.set_background_paused(True)
            self.governor.set_playback_policy(self.playback_blocks_background())
        # Wake waiters so they can observe the pause state and update their
        # cancellable wait/checkpoint instead of remaining on a stale condition.
        try:
            loop = asyncio.get_running_loop()
            _retain_task(loop.create_task(self._notify_waiters()))
        except RuntimeError:
            pass

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    def resume_background(self) -> None:
        self.background_paused = False
        self.background_pause_reason = ""
        if self.governor is not None:
            self.governor.set_background_paused(False)
        try:
            loop = asyncio.get_running_loop()
            _retain_task(loop.create_task(self._notify_waiters()))
        except RuntimeError:
            pass

    def _effective_mode(self):
        if self.override_expires_at and time.time() >= self.override_expires_at:
            self.override_expires_at = 0.0
            self.override_label = ""
            self.control_mode = "automatic"
            self.manual_playback_gate = True
        return self.control_mode

    # Local/API maintenance is safe to continue during playback because it does
    # not consume Telegram byte-range transfer lanes. Bulk transfers and generic
    # service work remain gated elsewhere. This lets capable PCs use spare CPU/API
    # capacity without allowing background media transfers to steal playback
    # bandwidth.
    # During active playback, only CPU/local conversion work is explicitly
    # compatible. Metadata, provider artwork, thumbnail/snapshot generation,
    # and preview maintenance are allowed to finish their current bounded unit
    # but yield before their next unit. This keeps background Telegram/API work
    # from competing with interactive playback.
    PLAYBACK_COMPATIBLE_CATEGORIES = frozenset({
        "conversion",
    })


    # ------------------------------------------------------------------
    # SUBSECTION: Category limits and status
    # Whether playback blocks work, category limits and status.
    # ------------------------------------------------------------------
    def playback_blocks_background(self):
        mode = self._effective_mode()
        if mode in {"automatic", "playback"}:
            return True
        if mode == "manual":
            return bool(self.manual_playback_gate)
        return False

    def playback_blocks_category(self, category: str = "generic") -> bool:
        category = str(category or "generic").lower()
        if category in self.PLAYBACK_COMPATIBLE_CATEGORIES:
            return False
        return self.playback_blocks_background()

    def _configured_category_limit(self, category: str) -> int:
        category = str(category or "generic").lower()
        if self._effective_mode() != "manual":
            return self._category_limit_auto(category)
        return int(self.manual_limits.get(category, self._category_limit_auto(category)))

    def _category_limit_auto(self, category: str) -> int:
        category = str(category or "generic").lower()
        if category == "thumbnail":
            return self.media_workers
        if category == "preview":
            return 1
        if category == "conversion":
            return max(1, min(self.media_workers, 2))
        if category == "metadata":
            return max(1, min(self.background_capacity, 3))
        if category == "upload":
            return max(1, self.background_capacity)
        return self.background_capacity

    def status(self) -> dict:
        mode = self._effective_mode()
        remaining = max(0, int(self.override_expires_at - time.time())) if self.override_expires_at else 0
        return {
            "mode": mode,
            "mode_label": {"automatic":"Automatic","playback":"Playback Priority","balanced":"Balanced","background":"Background Priority","manual":"Manual"}.get(mode, mode),
            "playback_gate": self.playback_blocks_background(),
            "playback_category_gates": {
                category: self.playback_blocks_category(category)
                for category in ("metadata", "thumbnail", "preview", "conversion", "upload", "generic")
            },
            "manual_limits": dict(self.manual_limits),
            "effective_limits": {k: self._configured_category_limit(k) for k in ("conversion","thumbnail","preview","metadata","upload")},
            "override_seconds_remaining": remaining,
            "override_label": self.override_label,
            "background_paused": bool(self.background_paused),
            "background_pause_reason": self.background_pause_reason,
        }


    # ------------------------------------------------------------------
    # SUBSECTION: Playback tracking
    # Playback enter/exit, interactive heartbeat and activity notes.
    # ------------------------------------------------------------------
    @property
    def playback_active(self) -> bool:
        return self.playback_streams > 0 or self.interactive_playback

    async def playback_enter(self) -> None:
        async with self._condition:
            was_active = self.playback_active
            self.playback_streams += 1
            self._last_playback_activity = time.monotonic()
            if self.governor is not None and not was_active:
                self.governor.playback_enter()
            self._condition.notify_all()

    async def playback_exit(self) -> None:
        async with self._condition:
            self.playback_streams = max(0, self.playback_streams - 1)
            if self.governor is not None and not self.playback_active:
                self.governor.playback_exit()
            self._condition.notify_all()

    async def interactive_playback_enter(self) -> None:
        async with self._condition:
            was_active = self.playback_active
            self.interactive_playback = True
            self._interactive_last_activity = time.monotonic()
            self._last_playback_activity = self._interactive_last_activity
            if self.governor is not None and not was_active:
                self.governor.playback_enter()
            if self._interactive_expiry_task is None or self._interactive_expiry_task.done():
                self._interactive_expiry_task = asyncio.create_task(self._interactive_expiry_watch(), name="interactive-playback-expiry")
            self._condition.notify_all()

    async def interactive_playback_heartbeat(self) -> None:
        async with self._condition:
            if self.interactive_playback:
                self._interactive_last_activity = time.monotonic()
                self._last_playback_activity = self._interactive_last_activity
                self._condition.notify_all()

    async def interactive_playback_exit(self) -> None:
        async with self._condition:
            self.interactive_playback = False
            self._interactive_last_activity = 0.0
            task = self._interactive_expiry_task
            self._interactive_expiry_task = None
            if task is not None and not task.done():
                task.cancel()
            if self.governor is not None and not self.playback_active:
                self.governor.playback_exit()
            self._condition.notify_all()

    async def _interactive_expiry_watch(self) -> None:
        try:
            while True:
                await asyncio.sleep(8.0)
                async with self._condition:
                    if not self.interactive_playback:
                        return
                    if time.monotonic() - self._interactive_last_activity <= 8.0:
                        continue
                    self.interactive_playback = False
                    self._interactive_last_activity = 0.0
                    if self.governor is not None and not self.playback_streams:
                        self.governor.playback_exit()
                    self._condition.notify_all()
                    return
        except asyncio.CancelledError:
            return

    async def reconcile_playback(self, *, expected_streams: int, reason: str = "reconcile") -> int:
        """Reconcile leaked playback leases against authoritative activity.

        Playback admission is request-scoped. A browser HTTP range stream and an
        externally launched player are the only legitimate owners of this lease.
        If both report zero activity while the coordinator still holds leases,
        release the stale leases and wake background jobs. This protects jobs
        from an abandoned/cancelled streaming request permanently blocking the
        background resource gate.
        """
        expected = max(0, int(expected_streams))
        async with self._condition:
            current = max(0, int(self.playback_streams))
            # An interactive browser session is a legitimate owner even when
            # Chromium temporarily has no active HTTP Range request between two
            # requests. Never reclaim the last lease while the UI still reports
            # interactive playback as active.
            if self.interactive_playback and expected > 0:
                expected = max(expected, 1)
            if self.interactive_playback and current <= 1:
                return 0
            if current <= expected:
                return 0
            stale = current - expected
            self.playback_streams = expected
            if self.governor is not None:
                for _ in range(stale):
                    self.governor.playback_exit()
            self._condition.notify_all()
            return stale

    def note_playback_activity(self) -> None:
        self._last_playback_activity = time.monotonic()


    # ------------------------------------------------------------------
    # SUBSECTION: Background admission
    # Checkpoints, service and background acquire/release.
    # ------------------------------------------------------------------
    async def cooperative_background_checkpoint(self, *, category: str = "generic", stale_after: float = 5.0, quantum: float = 0.08) -> None:
        """Yield background work completely while interactive playback is active.

        Playback is the interactive workload. Existing background jobs may already
        be inside a bounded transfer, but at every safe checkpoint they wait for
        playback to leave the active state before starting the next unit of work.
        This prevents asset/metadata/image jobs from competing with playback on
        slow machines while preserving resumability and bounded concurrency.
        """
        category = str(category or "generic").lower()
        if not self.playback_active or not self.playback_blocks_category(category):
            if self.background_paused:
                async with self._condition:
                    while self.background_paused:
                        await self._condition.wait()
            await asyncio.sleep(0)
            return
        async with self._condition:
            while self.background_paused or (self.playback_streams > 0 and self.playback_blocks_category(category)):
                await self._condition.wait()
        await asyncio.sleep(0)

    async def wait_for_interactive_clear(self) -> None:
        """Legacy hard gate retained for callers that explicitly want to wait."""
        async with self._condition:
            while self.playback_active:
                await self._condition.wait()

    async def acquire_service(self, *, block_playback: bool = True) -> None:
        """Reserve the service slot.

        Content Automation may keep its torrent/download service active during
        playback; its Telegram delivery phase still uses the hard transfer gate.
        Other service callers retain the conservative playback wait by default.
        """
        async with self._condition:
            while self.background_paused or (block_playback and self.playback_active):
                await self._condition.wait()
            self.service_active += 1
            self._condition.notify_all()

    async def release_service(self) -> None:
        async with self._condition:
            self.service_active = max(0, self.service_active - 1)
            self._condition.notify_all()

    def _category_limit(self, category: str) -> int:
        return self._configured_category_limit(category)

    def _reserved_class_capacity(self) -> int:
        """Capacity available to either scheduling class while preserving one slot for the other."""
        capacity = max(1, int(self.background_capacity))
        return max(1, capacity - 1) if capacity >= 2 else 1

    async def acquire_background(self, category: str = "generic") -> None:
        """Reserve a hardware-aware background slot.

        Playback remains the highest priority. Playback-compatible categories
        may continue during playback within their independent limits; transfer-
        heavy/generic categories remain gated.
        """
        category = str(category or "generic").lower()
        is_metadata = category == "metadata"
        async with self._condition:
            self.background_waiting += 1
            self._category_waiting[category] = self._category_waiting.get(category, 0) + 1
            try:
                while True:
                    class_active = self._metadata_active if is_metadata else self._non_metadata_active
                    class_capacity = self._reserved_class_capacity()
                    blocked = (
                        self.background_paused
                        or (self.playback_blocks_category(category) and self.playback_active)
                        or self.background_active >= self.background_capacity
                        or self._category_active.get(category, 0) >= self._configured_category_limit(category)
                        or class_active >= class_capacity
                    )
                    if not blocked:
                        break
                    await self._condition.wait()
                self.background_active += 1
                self._category_active[category] = self._category_active.get(category, 0) + 1
                if is_metadata:
                    self._metadata_active += 1
                else:
                    self._non_metadata_active += 1
            finally:
                self.background_waiting = max(0, self.background_waiting - 1)
                self._category_waiting[category] = max(0, self._category_waiting.get(category, 0) - 1)
                if self._category_waiting[category] <= 0:
                    self._category_waiting.pop(category, None)

    async def release_background(self, category: str = "generic") -> None:
        category = str(category or "generic").lower()
        async with self._condition:
            self.background_active = max(0, self.background_active - 1)
            if category in self._category_active:
                self._category_active[category] = max(0, self._category_active[category] - 1)
            if category == "metadata":
                self._metadata_active = max(0, self._metadata_active - 1)
            else:
                self._non_metadata_active = max(0, self._non_metadata_active - 1)
            self._condition.notify_all()


    # ------------------------------------------------------------------
    # SUBSECTION: Policy and snapshot
    # Media policy and snapshot output.
    # ------------------------------------------------------------------
    def media_policy(self) -> dict:
        """Work out media worker limits from configuration and playback state."""
        # Snapshot generation is a bounded two-level workload. On capable,
        # idle machines, allow two videos at once with at most two decoders per
        # video. The total decoder fan-out stays within the hardware-adaptive
        # media-worker recommendation. Smaller machines and active playback
        # remain serial so snapshot work cannot crowd out interactive playback.
        snapshot_workers = max(1, min(4, int(self.media_workers or 1)))
        can_parallelize_snapshots = (
            snapshot_workers >= 3
            and self.background_capacity >= 2
            and not self.playback_active
            and not self.background_paused
        )
        snapshot_video_concurrency = 2 if can_parallelize_snapshots else 1
        playback_priority = bool(self.playback_active or self.background_paused)
        snapshot_frame_workers = (
            1 if playback_priority
            else max(1, min(2, snapshot_workers // snapshot_video_concurrency))
        )
        # Per-video network fan-out is also bounded. During playback/background
        # pause, snapshots yield to one range request; otherwise two videos get
        # at most two range reads each, or a single video can use up to four.
        snapshot_range_concurrency = (
            1 if playback_priority
            else (
                max(1, min(2, snapshot_workers // snapshot_video_concurrency))
                if snapshot_video_concurrency > 1
                else max(1, min(4, snapshot_workers))
            )
        )
        return {
            "thumbnail_workers": self._configured_category_limit("thumbnail"),
            "preview_workers": self._configured_category_limit("preview"),
            "snapshot_video_concurrency": snapshot_video_concurrency,
            "snapshot_frame_workers": snapshot_frame_workers,
            "snapshot_range_concurrency": snapshot_range_concurrency,
            "conversion_workers": self._configured_category_limit("conversion"),
            "metadata_workers": self._configured_category_limit("metadata"),
            "upload_workers": self._configured_category_limit("upload"),
            "hardware_acceleration": (self.hardware_profile.get("hardware_acceleration") or {}),
        }

    def snapshot(self) -> dict:
        return {
            "playback_active": self.playback_active,
            "playback_streams": self.playback_streams,
            "interactive_playback": bool(self.interactive_playback),
            "background_active": self.background_active,
            "background_waiting": self.background_waiting,
            "background_waiting_by_category": dict(self._category_waiting),
            "metadata_active": self._metadata_active,
            "non_metadata_active": self._non_metadata_active,
            "metadata_capacity": self._reserved_class_capacity(),
            "non_metadata_capacity": self._reserved_class_capacity(),
            "metadata_reserved_capacity": 1 if self.background_capacity >= 2 else 0,
            "service_active": self.service_active,
            "background_capacity": self.background_capacity,
            "media_workers": self.media_workers,
            "category_active": dict(self._category_active),
            "media_policy": self.media_policy(),
            "playback_last_activity_age": max(0.0, time.monotonic() - self._last_playback_activity) if self._last_playback_activity else None,
            "cpu_count": max(1, int(os.cpu_count() or 1)),
            "hardware": self.hardware_profile,
            "control": self.status(),
        }
