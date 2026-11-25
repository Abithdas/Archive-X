"""DirectTelegramRangeSource — reads arbitrary byte ranges of a Telegram file.

Ported verbatim from telegram_unstreamable_mover.py (the most complete version
with TelegramRangeFloodError and transient-error retry logic).

Canonical engine-side synchronous Telegram range transport.
Active consumers include metadata/suggestion/page-enrichment paths and the web
range-serving path. The historical v17 ``telegram/files.py`` module is no longer
part of the current package; the asynchronous playback/media-resource transport
lives in the current playback stack instead.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Shared scan cache
#       - Async fetch
#       - Synchronous public API
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import asyncio
import io
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextlib import asynccontextmanager
from typing import Optional

from .. import config
from ..engines.media_utils import flood_wait_seconds, is_flood_wait, TelegramRangeFloodError
from .aligned_telegram_range import fetch_aligned_range

from . import telethon_lazy   # the Telegram library is loaded when a range is first fetched, not at start-up


def _scan_range_cache():
    from . import scan_range_cache
    return scan_range_cache


class DirectTelegramRangeSource:
    """Synchronous byte-range reader backed by Telethon.

    Ported verbatim from telegram_unstreamable_mover.py:DirectTelegramRangeSource.
    The class is used in ThreadPoolExecutor workers (thumbnail generation), so
    every Telegram call goes through a sync wrapper that runs on the event loop.
    """

    CHUNK_SIZE = 512 * 1024          # precise=True permits any 1 KiB multiple ≤ 1 MiB
    MAX_RETRIES = 6
    FLOOD_WAIT_CAP = 300             # seconds
    TRANSIENT_RETRY_DELAY = 10
    MAX_TRANSIENT_RETRIES = 8

    def __init__(self, client, message, loop: asyncio.AbstractEventLoop | None = None, *, fingerprint_lane: bool = False):
        self.client = client
        self.message = message
        self.loop = loop or self._resolve_loop()
        self.fingerprint_lane = bool(fingerprint_lane)
        self._size: Optional[int] = None
        self._cache: dict[int, bytes] = {}
        self.bytes_received = 0
        self.rpc_requests = 0
        self._lock = threading.Lock()
        self.governor = getattr(client, "_telegram_archive_governor", None)
        self._local_rpc_sem = asyncio.Semaphore(2)
        self._local_rate_lock = asyncio.Lock()
        self._local_next_rpc_at = 0.0
        self._local_interval = 0.08
        self._fingerprint_deadline = (time.monotonic() + float(getattr(config, "FINGERPRINT_TIMEOUT_SECONDS", 30.0))
                                      if self.fingerprint_lane else None)

    def close(self) -> None:
        """Release what this reader holds. Callers (metadata matching) close every source they open; the missing method made each close fail
        and the failure was swallowed (22 times in one user's log)."""
        with self._lock:
            self._cache.clear()

    def _resolve_loop(self):
        """Resolve the authenticated Telegram event loop even from worker threads."""
        try:
            running = asyncio.get_running_loop()
            return running
        except RuntimeError:
            pass
        for attr in ("loop", "_loop"):
            candidate = getattr(self.client, attr, None)
            if candidate is not None and not candidate.is_closed():
                return candidate
        raise RuntimeError(
            "Telegram event loop is unavailable; pass the application's loop explicitly "
            "when constructing a range source from a worker thread"
        )

    def use_block_size(self, block_size: int):
        """Read in smaller Telegram blocks for a while (V29.4.130).

        Telegram precise=True accepts any 1 KiB multiple up to 1 MiB; the
        request need not divide 1 MiB. Returns a function that restores the
        normal block size. The cache is
        cleared on both switches because its entries are keyed by the
        block-aligned offset of the block size in use.
        """
        block_size = int(block_size)
        if block_size <= 0 or block_size % 1024 or block_size > 1024 * 1024:
            raise ValueError(f"invalid precise Telegram block size {block_size}; expected a 1 KiB multiple up to 1 MiB")
        previous = self.CHUNK_SIZE
        with self._lock:
            self.CHUNK_SIZE = block_size
            self._cache.clear()

        def restore():
            with self._lock:
                self.CHUNK_SIZE = previous
                self._cache.clear()
        return restore

    @property
    def size(self) -> int:
        if self._size is None:
            try:
                self._size = int(self.message.file.size)
            except Exception:
                self._size = 0
        return self._size


    # ------------------------------------------------------------------
    # SUBSECTION: Shared scan cache
    # Fills and reads the shared scan block cache.
    # ------------------------------------------------------------------
    def _shared_scan_cache_key(self):
        try:
            return _scan_range_cache().key_for_message(self.message, self.size)
        except Exception:
            return None

    def _is_transient_telegram_error(self, exc) -> bool:
        if is_flood_wait(exc):
            return False
        name = type(exc).__name__
        text = str(exc or "")
        transient = (
            "ConnectionError", "TimeoutError", "ServerError",
            "BadMessageError", "AuthKeyError", "SecurityError",
            "RPCError", "NetworkError",
        )
        for t in transient:
            if t in name or t in text:
                return True
        return False

    def _fetch_exact_sync(self, offset: int, request_limit: int) -> tuple[bytes, float]:
        """Fetch one precise aligned subrange through the shared governor."""
        async def do_fetch():
            async with self._range_slot(request_bytes=request_limit):
                started = time.monotonic()
                data = await self._fetch_async(offset, request_limit=request_limit)
                return data, max(0.001, time.monotonic() - started)
        self.rpc_requests += 1
        future = asyncio.run_coroutine_threadsafe(do_fetch(), self.loop)
        timeout = 60.0
        if self.fingerprint_lane:
            remaining = float(self._fingerprint_deadline or time.monotonic()) - time.monotonic()
            if remaining <= 0:
                future.cancel()
                raise TimeoutError("OSHASH fingerprint exceeded its 30 second budget")
            timeout = min(timeout, remaining)
        try:
            data, elapsed = future.result(timeout=timeout)
        except FutureTimeoutError as exc:
            future.cancel()
            if self.fingerprint_lane:
                raise TimeoutError("OSHASH fingerprint Telegram range timed out within the 30 second budget") from exc
            raise
        self.bytes_received += len(data)
        if self.governor is not None:
            try:
                self.governor.note_range_success(len(data), elapsed)
            except Exception:
                _swallowed_note("telegram_range_transport.DirectTelegramRangeSource._fetch_exact_sync")
                pass
        return data, elapsed

    def _fill_shared_block_sync(self, cache_key, offset: int, length: int) -> bytes:
        """Fill only cache holes, sharing in-flight ranges with the snapshot reader."""
        cache = _scan_range_cache()
        started = time.monotonic()
        while True:
            cached = cache.read(cache_key, offset, length)
            if cached is not None:
                return cached
            reservations, wait_event = cache.claim_missing(cache_key, offset, length)
            if reservations is None:
                if wait_event is None:
                    continue
                remaining = max(0.1, 90.0 - (time.monotonic() - started))
                if not wait_event.wait(min(remaining, 30.0)):
                    if time.monotonic() - started >= 90.0:
                        raise TimeoutError("Timed out waiting for overlapping scan byte-range fetch")
                continue
            if not reservations:
                cached = cache.read(cache_key, offset, length)
                if cached is not None:
                    return cached
                continue
            try:
                for gap_start, gap_end, _event in reservations:
                    cursor = max(0, (int(gap_start) // 1024) * 1024)
                    target_end = min(self.size, (int(gap_end) + 1023) // 1024 * 1024)
                    while cursor < target_end:
                        boundary = ((cursor // (1024 * 1024)) + 1) * (1024 * 1024)
                        limit = min(target_end - cursor, boundary - cursor, 1024 * 1024)
                        if limit % 1024:
                            limit = min(1024 * 1024, ((limit + 1023) // 1024) * 1024, boundary - cursor)
                        expected = min(limit, max(0, self.size - cursor))
                        if expected <= 0:
                            break
                        data, _elapsed = self._fetch_exact_sync(cursor, limit)
                        if len(data) != expected:
                            raise RuntimeError(f"Short Telegram range at {cursor}: {len(data)}/{expected} bytes")
                        cache.store(cache_key, cursor, data)
                        cursor += len(data)
                        if len(data) < limit:
                            break
            finally:
                cache.release_reservations(cache_key, reservations)

    def _fetch_chunk_sync(self, offset: int, length: int) -> bytes:
        """Fetch [offset, offset+length) with FloodWait and transient-error retry."""
        aligned_offset = (offset // self.CHUNK_SIZE) * self.CHUNK_SIZE
        cache_key = self._shared_scan_cache_key()
        shared_len = min(self.CHUNK_SIZE, max(0, self.size - aligned_offset))
        with self._lock:
            if aligned_offset in self._cache:
                block = self._cache[aligned_offset]
                local_off = offset - aligned_offset
                if len(block) >= local_off + length:
                    return block[local_off: local_off + length]

        flood_retries = 0
        transient_retries = 0
        while True:
            try:
                if cache_key and shared_len > 0:
                    data = self._fill_shared_block_sync(cache_key, aligned_offset, shared_len)
                    with self._lock:
                        self._cache[aligned_offset] = data
                    local_off = offset - aligned_offset
                    return data[local_off: local_off + length]

                async def do_fetch():
                    async with self._range_slot(request_bytes=self.CHUNK_SIZE):
                        started = time.monotonic()
                        data = await self._fetch_async(aligned_offset, request_limit=self.CHUNK_SIZE)
                        return data, max(0.001, time.monotonic() - started)
                self.rpc_requests += 1
                data, elapsed = asyncio.run_coroutine_threadsafe(
                    do_fetch(), self.loop
                ).result(timeout=60)
                self.bytes_received += len(data)
                if self.governor is not None:
                    try:
                        self.governor.note_range_success(len(data), elapsed)
                    except Exception:
                        _swallowed_note("telegram_range_transport.DirectTelegramRangeSource._fetch_chunk_sync")
                        pass
                with self._lock:
                    self._cache[aligned_offset] = data
                local_off = offset - aligned_offset
                return data[local_off: local_off + length]
            except Exception as exc:
                if getattr(exc, "_archive_x_download_flood_noted", False) or getattr(exc, "_archive_x_file_reference_retry_attempted", False):
                    raise
                if self.governor is not None:
                    try:
                        if is_flood_wait(exc):
                            if not getattr(exc, "_archive_x_flood_noted", False):
                                self.governor.note_floodwait(flood_wait_seconds(exc), kind="range", request="GetFile")
                        else:
                            self.governor.note_range_failure()
                    except Exception:
                        _swallowed_note("telegram_range_transport.DirectTelegramRangeSource._fetch_chunk_sync")
                        pass
                if is_flood_wait(exc):
                    wait = min(flood_wait_seconds(exc) + 2, self.FLOOD_WAIT_CAP)
                    flood_retries += 1
                    if flood_retries > self.MAX_RETRIES:
                        raise TelegramRangeFloodError(
                            f"Telegram kept rate-limiting range reads after {self.MAX_RETRIES} retries"
                        ) from exc
                    time.sleep(wait)
                    continue
                if self._is_transient_telegram_error(exc):
                    transient_retries += 1
                    if transient_retries > self.MAX_TRANSIENT_RETRIES:
                        raise
                    time.sleep(self.TRANSIENT_RETRY_DELAY * min(transient_retries, 3))
                    continue
                raise



    # ------------------------------------------------------------------
    # SUBSECTION: Async fetch
    # Governor slot, message refresh and async fetch.
    # ------------------------------------------------------------------
    @asynccontextmanager
    async def _range_slot(self, request_bytes=None):
        if self.governor is not None:
            if self.fingerprint_lane and callable(getattr(self.governor, "telegram_fingerprint_slot", None)):
                async with self.governor.telegram_fingerprint_slot():
                    yield
            else:
                async with self.governor.telegram_range_slot(file_size=self.size, request_bytes=request_bytes):
                    yield
            return
        async with self._local_rpc_sem:
            async with self._local_rate_lock:
                now = time.monotonic()
                wait = max(0.0, self._local_next_rpc_at - now)
                self._local_next_rpc_at = max(now, self._local_next_rpc_at) + self._local_interval
            if wait > 0:
                await asyncio.sleep(wait)
            yield

    async def _refresh_message(self):
        """Resolve one fresh message/file_reference after Telegram invalidates it."""
        try:
            entity = self.entity or getattr(self.message, "chat_id", None) or self.message.peer_id
            fresh = await self.client.get_messages(entity, ids=int(self.message.id))
            if fresh:
                self.message = fresh
                return fresh
        except Exception:
            _swallowed_note("telegram_range_transport.DirectTelegramRangeSource._refresh_message")
            pass
        return None

    async def _fetch_async(self, offset: int, request_limit: int | None = None) -> bytes:
        """Use the shared aligned upload.getFile transport and bounded fallback."""
        limit = max(1024, int(request_limit or self.CHUNK_SIZE))
        if limit > 1024 * 1024 or int(offset) % 1024 or limit % 1024:
            raise ValueError("Telegram precise range input must be 1 KiB aligned and <=1 MiB; the shared helper splits boundary crossings")
        return await fetch_aligned_range(
            self.client, self.message, int(offset), limit, fallback=True,
            telethon_functions=telethon_lazy.functions(), telethon_types=telethon_lazy.types(),
            refresh_message=self._refresh_message,
            # OSHASH must not wait for a snapshot-owned cache reservation for
            # the same bytes: its two tiny reads are independently scheduled.
            cache_policy="none" if self.fingerprint_lane else "coalesce", admission_managed=True,
            priority="fingerprint" if self.fingerprint_lane else "range",
            file_slot_held=self.fingerprint_lane,
            message_scoped_floodwait=self.fingerprint_lane,
        )


    # ------------------------------------------------------------------
    # SUBSECTION: Synchronous public API
    # Blocking read helpers used by parsers.
    # ------------------------------------------------------------------
    def get_range_sync(self, offset: int, length: int) -> bytes:
        """Exactly `length` bytes from `offset` (fewer only at end of file).

        One Telegram request returns at most one aligned 512 KiB block, so a
        range that crosses a block boundary (e.g. the last 64 KiB of a file)
        needs several requests. The old code returned only the first part,
        which broke OSHASH ("head and tail must each be exactly 64 KiB")."""
        offset, length = max(0, int(offset)), max(0, int(length))
        if self.fingerprint_lane and time.monotonic() >= float(self._fingerprint_deadline or 0.0):
            raise TimeoutError("OSHASH fingerprint exceeded its 30 second budget")
        size = self.size
        if size:
            length = min(length, max(0, size - offset))
        if self.fingerprint_lane:
            # Fingerprinting is an exact first/last 64 KiB operation, not a
            # request for a shared 64 KiB cache block. Align only to Telegram's
            # required 1 KiB boundary and slice the alignment padding locally.
            if length <= 0:
                return b""
            aligned_start = (offset // 1024) * 1024
            prefix = offset - aligned_start
            aligned_end = ((offset + length + 1023) // 1024) * 1024
            cursor = aligned_start
            pieces = []
            while cursor < aligned_end and (not size or cursor < size):
                boundary = ((cursor // (1024 * 1024)) + 1) * (1024 * 1024)
                request_limit = min(aligned_end - cursor, boundary - cursor, 1024 * 1024)
                request_limit = (request_limit // 1024) * 1024
                if request_limit <= 0:
                    break
                data, _elapsed = self._fetch_exact_sync(cursor, request_limit)
                if not data:
                    break
                pieces.append(data)
                cursor += len(data)
                if len(data) < request_limit:
                    break
            result = b"".join(pieces)[prefix:prefix + length]
            # Publish these exact head/tail bytes into the ordinary scan cache
            # without taking a cache reservation or waiting behind snapshot
            # work. Metadata sync may then pin the already-read OSHASH intervals
            # for the later index/snapshot phases instead of downloading them again.
            try:
                key = self._shared_scan_cache_key()
                if key and result:
                    _scan_range_cache().store(key, offset, result)
            except Exception:
                _swallowed_note("telegram_range_transport.DirectTelegramRangeSource.get_range_sync")
                pass
            return result
        parts, got, empty = [], 0, 0
        while got < length:
            piece = self._fetch_chunk_sync(offset + got, length - got)
            if not piece:
                # A truncated/short Telegram answer: forget the cached block
                # and ask again a few times before giving up (the caller then
                # sees a short result and can fall back safely).
                empty += 1
                if empty > 3:
                    break
                aligned = ((offset + got) // self.CHUNK_SIZE) * self.CHUNK_SIZE
                with self._lock:
                    self._cache.pop(aligned, None)
                continue
            empty = 0
            parts.append(piece)
            got += len(piece)
        return b"".join(parts)

