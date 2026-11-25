"""The Telethon client every Telegram request goes through (see client_policy.py for the policy it applies).

It is its own module because it subclasses the Telegram library, which takes about 0.4 s to import: client_policy loads it when the first client
is made, so starting the application does not pay for it.
"""
from __future__ import annotations

import asyncio
import time

from telethon import TelegramClient
from telethon.errors import FloodWaitError

from ..core.swallowed import note as _swallowed_note
from .client_policy import (
    CONNECTION_RETRIES, CONNECTION_RETRY_DELAY_SECONDS, RECONNECT_ATTEMPT_TIMEOUT_SECONDS, RECONNECT_BUDGET_SECONDS,
    _is_authorization_error, _is_idempotent_request, _note_flood, _notify_reconnect_listeners, _upload_floodwait_seconds,
)


class GovernedTelegramClient(TelegramClient):
    """Telethon client whose every API request passes through the governor.

    The governor is deliberately duck-typed so this module does not create an
    import cycle with core.governor. Playback clients can be marked
    ``_archive_x_playback_priority`` to bypass the playback gate for media
    ranges, while ordinary metadata/catalogue RPCs continue through the shared
    rate limiter during playback. Bulk transfers and byte ranges retain their
    explicit playback gates in the governor.
    """

    def __init__(self, *args, governor=None, playback_priority=False, **kwargs):
        # Telethon's defaults (5 reconnect attempts, 1 s apart) give up after a
        # few seconds of Wi-Fi roaming, router hiccup or PC wake-from-sleep, and
        # then leave the client permanently disconnected. Keep reconnecting for
        # longer; Archive X's own recovery below takes over if that also fails.
        kwargs.setdefault("connection_retries", CONNECTION_RETRIES)
        kwargs.setdefault("retry_delay", CONNECTION_RETRY_DELAY_SECONDS)
        kwargs.setdefault("auto_reconnect", True)
        super().__init__(*args, **kwargs)
        self._telegram_archive_governor = governor
        self._archive_x_playback_priority = bool(playback_priority)
        self._archive_x_ever_connected = False
        self._archive_x_closed = False
        self._archive_x_reconnect_lock = None
        self._archive_x_reconnects = 0

    async def connect(self, *args, **kwargs):
        result = await super().connect(*args, **kwargs)
        try:
            if self.is_connected():
                self._archive_x_ever_connected = True
                self._archive_x_closed = False
        except Exception:
            _swallowed_note("client_policy.GovernedTelegramClient.connect")
            pass
        return result

    def disconnect(self, *args, **kwargs):
        # An explicit disconnect (shutdown, logout, pool trim) must not be
        # undone by the automatic recovery in __call__.
        updates_error = getattr(self, "_updates_error", None)
        # Telethon's update loop also calls disconnect() itself, e.g. after a
        # session-storage error. Only an authorization failure (or a caller's
        # own disconnect) is final; anything else stays recoverable.
        if updates_error is None or _is_authorization_error(updates_error):
            self._archive_x_closed = True
        return super().disconnect(*args, **kwargs)

    def archive_x_can_recover(self) -> bool:
        return bool(self._archive_x_ever_connected and not self._archive_x_closed)

    async def archive_x_reconnect(self, *, reason: str = "", budget_seconds: float = RECONNECT_BUDGET_SECONDS) -> bool:
        """Re-open the same client after Telethon gave up reconnecting.

        Reconnecting the *same* object keeps every engine/job/player that holds
        a reference to it working; building a new client would strand them on a
        dead connection. Concurrent callers share one attempt.
        """
        if not self.archive_x_can_recover():
            return False
        if self._archive_x_reconnect_lock is None:
            self._archive_x_reconnect_lock = asyncio.Lock()
        async with self._archive_x_reconnect_lock:
            if self.is_connected():
                return True
            deadline = time.monotonic() + max(5.0, float(budget_seconds))
            delay = 1.0
            attempt = 0
            last_error = None
            while self.archive_x_can_recover():
                attempt += 1
                try:
                    await asyncio.wait_for(super().connect(), timeout=RECONNECT_ATTEMPT_TIMEOUT_SECONDS)
                    if self.is_connected():
                        self._archive_x_reconnects += 1
                        _notify_reconnect_listeners(self, True, reason, attempt, None)
                        return True
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    last_error = exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(delay, remaining))
                delay = min(15.0, delay * 2)
            _notify_reconnect_listeners(self, False, reason, attempt, last_error)
            return False

    async def __call__(self, request, *args, **kwargs):
        if self.archive_x_can_recover() and not self.is_connected():
            await self.archive_x_reconnect(reason=f"{type(request).__name__} on a dropped connection")
        try:
            return await self._archive_x_governed_call(request, *args, **kwargs)
        except ConnectionError as exc:
            # Retry once after recovering the connection when that cannot
            # duplicate work: the request never left (sender was disconnected)
            # or it is a read/idempotent upload-part request.
            never_sent = "while disconnected" in str(exc)
            if not self.archive_x_can_recover() or not (never_sent or _is_idempotent_request(request)):
                raise
            if not self.is_connected() and not await self.archive_x_reconnect(
                reason=f"{type(request).__name__}: {type(exc).__name__}"
            ):
                raise
            return await self._archive_x_governed_call(request, *args, **kwargs)

    async def _archive_x_governed_call(self, request, *args, **kwargs):
        """Send an RPC through governor slots, with flood and reconnect handling."""
        governor = getattr(self, "_telegram_archive_governor", None)
        if governor is None:
            return await super().__call__(request, *args, **kwargs)

        # Authentication/control-plane RPCs (send code, sign-in, 2FA, and the
        # no-login API probe) must not wait behind the data-plane transfer
        # governor. Login is what establishes the session that the rest of the
        # application depends on, so applying the global playback/transfer
        # gate here can make a valid Telegram connection look like a dead login.
        # The service enables this flag only around the bounded authentication
        # calls below. Normal media/catalogue traffic remains governed.
        if getattr(self, "_archive_x_auth_priority", False):
            started = time.monotonic()
            try:
                result = await super().__call__(request, *args, **kwargs)
                governor.note_rpc_success(time.monotonic() - started)
                return result
            except FloodWaitError as exc:
                seconds = max(1, int(getattr(exc, "seconds", 1) or 1))
                governor.note_floodwait(seconds)
                raise
            except Exception as exc:
                try:
                    governor.note_network_error(f"Telegram auth RPC: {type(exc).__name__}: {exc}")
                except Exception:
                    _swallowed_note("client_policy.GovernedTelegramClient._archive_x_governed_call")
                    pass
                raise

        request_name = type(request).__name__
        if request_name in {"GetFileRequest", "GetCdnFileRequest"}:
            # All media-byte requests go through core.aligned_telegram_range.
            # Don't double-throttle byte reads in the generic RPC lane: its
            # shared helper owns the per-DC admission and lane-local FloodWait.
            return await super().__call__(request, *args, **kwargs)
        if request_name in {"SaveFilePartRequest", "SaveBigFilePartRequest"}:
            # Upload parts have their own adaptive lane. Do not pass them
            # through telegram_rpc_slot(): that would add generic API pacing,
            # block behind browser playback, and let an upload FloodWait slow
            # unrelated API/range traffic. Retry the exact rejected part only.
            while True:
                try:
                    async with governor.telegram_upload_slot():
                        started = time.monotonic()
                        result = await asyncio.wait_for(
                            super().__call__(request, *args, **kwargs), timeout=75.0
                        )
                    try:
                        governor.note_upload_part_success(time.monotonic() - started)
                    except Exception:
                        _swallowed_note("client_policy.GovernedTelegramClient._archive_x_governed_call")
                        pass
                    return result
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    seconds = _upload_floodwait_seconds(exc)
                    if seconds > 0:
                        try:
                            exc._archive_x_upload_floodwait_noted = True
                        except Exception:
                            _swallowed_note("client_policy.GovernedTelegramClient._archive_x_governed_call")
                            pass
                        try:
                            governor.note_upload_floodwait(seconds, request=request_name)
                        except Exception:
                            _swallowed_note("client_policy.GovernedTelegramClient._archive_x_governed_call")
                            pass
                        callback = getattr(self, "_archive_x_upload_wait_callback", None)
                        if callable(callback):
                            try:
                                value = callback(seconds, request_name)
                                if hasattr(value, "__await__"):
                                    await value
                            except Exception:
                                _swallowed_note("client_policy.GovernedTelegramClient._archive_x_governed_call")
                                pass
                        # The slot has been released. Other upload parts wait on
                        # the upload-lane pause; RPC and playback lanes keep going.
                        await asyncio.sleep(seconds)
                        continue
                    # A 75s stuck part times out here. The upload worker catches
                    # it and retries this part with the same file id.
                    raise

        # FloodWait is server-directed.  The governor records it and the
        # request is retried after the server's requested delay.  Because the
        # server rejected the request, retrying it does not duplicate an
        # accepted send/upload operation.
        flood_retries = 0
        while True:
            try:
                async with governor.telegram_rpc_slot(
                    playback=bool(getattr(self, "_archive_x_playback_priority", False))
                ):
                    started = time.monotonic()
                    result = await super().__call__(request, *args, **kwargs)
                    governor.note_rpc_success(time.monotonic() - started)
                    return result
            except FloodWaitError as exc:
                seconds = max(1, int(getattr(exc, "seconds", 1) or 1))
                request_name = type(request).__name__
                _note_flood(governor, seconds, request_name,
                            bool(getattr(self, "_archive_x_playback_priority", False)))
                flood_retries += 1
                # One controlled retry is enough.  Further FloodWaits remain
                # visible to the caller rather than creating an endless loop.
                if flood_retries > 1:
                    # Callers that also report FloodWaits (range readers)
                    # must not count this one a second time.
                    try:
                        exc._archive_x_flood_noted = True
                    except Exception:
                        _swallowed_note("client_policy.GovernedTelegramClient._archive_x_governed_call")
                        pass
                    raise
                await asyncio.sleep(seconds)
