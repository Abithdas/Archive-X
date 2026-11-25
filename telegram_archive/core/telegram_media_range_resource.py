"""Canonical Telegram Media Manager range resource.

Owns the Media Manager-specific 1 MiB direct-MTProto range path, including
file-reference/DC recovery, bounded in-flight requests, cache reuse, and
sequential chunk access. The Media Manager imports this class as a compatibility
facade so callers retain the established import path.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - File and range slots
#       - Chunk fetch and cache
#       - Precise range fetch
#       - Synchronous public API
# ------------------------------------------------------------------
from __future__ import annotations
from .swallowed import note as _swallowed_note
from .background_tasks import retain as _retain_task

import asyncio
import threading
import time
from collections import OrderedDict
from contextlib import asynccontextmanager

from . import telethon_lazy   # the Telegram library is loaded when a range is first fetched, not at start-up

from ..engines.media_utils import flood_wait_seconds, is_flood_wait, TelegramRangeFloodError
from .aligned_telegram_range import fetch_aligned_range

def _cover(pieces: list[tuple[int, bytes]], start: int, end: int) -> bytes | None:
    """[start, end) assembled from (offset, bytes) pieces, or None when they leave a gap."""
    out = bytearray()
    cursor = start
    for offset, data in sorted(pieces, key=lambda p: p[0]):
        if cursor >= end:
            break
        if offset <= cursor < offset + len(data):
            take = min(end, offset + len(data))
            out += data[cursor - offset:take - offset]
            cursor = take
    return bytes(out) if cursor >= end else None


class DirectTelegramRangeSource:
    """Direct Telegram byte-range reader using ONE authorized session.

    The fast path never opens localhost HTTP and never clones MemorySession
    clients. Exact Telegram 1 MiB ranges are fetched concurrently on the
    already-authenticated Telethon connection and cached in RAM.
    """
    CHUNK = 1024 * 1024
    CACHE_BYTES = 32 * 1024 * 1024
    # Telegram file-range requests are rate-limited much more aggressively
    # than normal message API calls. Keep thumbnail extraction deliberately
    # serialized and pace requests so a 36-frame sheet does not create a burst.
    MAX_CONCURRENT_RPC = 2
    RANGE_REQUEST_INTERVAL = 0.08  # fallback pacing; shared governor is preferred
    # Fast thumbnail mode: use the nearest sync/keyframe instead of decoding
    # an entire long-GOP chain. This keeps the 36-shot sheet representative
    # while cutting Telegram bytes and local decode work dramatically.
    KEYFRAME_ONLY = False

    def __init__(self, client, *args, loop=None, range_concurrency=None, range_request_interval=None):
        """Accept both historical (client, message, loop) and newer
        (client, entity, message, loop) call forms. The media-manager worker
        must never fail solely because an older caller omitted entity/loop."""
        self.client = client
        self.entity = None
        self.message = None
        if len(args) == 1:
            self.message = args[0]
        elif len(args) == 2:
            # Legacy: (message, loop)
            self.message, maybe_loop = args
            if loop is None: loop = maybe_loop
        elif len(args) >= 3:
            self.entity, self.message, maybe_loop = args[:3]
            if loop is None: loop = maybe_loop
        else:
            raise TypeError("DirectTelegramRangeSource requires a Telegram message")
        self.loop = loop or self._resolve_loop()
        self.size = int(getattr(self.message.file, "size", 0) or 0)
        self.mime = getattr(self.message.file, "mime_type", None) or "video/mp4"
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._inflight = {}
        self._request_futures = set()
        self._lock = threading.RLock()
        self.governor = getattr(client, "_telegram_archive_governor", None)
        rpc_limit = self.MAX_CONCURRENT_RPC if range_concurrency is None else max(1, min(4, int(range_concurrency)))
        self._rpc_sem = asyncio.Semaphore(rpc_limit)
        self._range_request_interval = (
            self.RANGE_REQUEST_INTERVAL if range_request_interval is None
            else max(0.0, float(range_request_interval))
        )
        self.bytes_received = 0
        self.rpc_requests = 0
        # V29.4.130: optional progress hook, called with each chunk's size.
        self.on_bytes = None
        # Telegram may rate-limit a burst of file-range RPCs.  Share one
        # cooldown across all worker threads so they do not independently
        # wake up and hammer the same file again.
        self._flood_lock = threading.Lock()
        self._flood_until = 0.0
        self._next_rpc_at = 0.0
        self._rate_lock = threading.Lock()
        self._closed = False
        # The app-config Telegram quota counts simultaneous whole-file reads,
        # not every byte-range request. Snapshot extraction holds one permit
        # from its first read until close(); individual seeks still acquire the
        # shared range lane independently.
        self._file_slot_context = None
        self._file_slot_held = False
        self._file_slot_lock = asyncio.Lock()
        try:
            self._file_slot_dc_id = int(getattr(getattr(self.message, "document", None), "dc_id", 0) or 0)
        except Exception:
            self._file_slot_dc_id = 0


    # ------------------------------------------------------------------
    # SUBSECTION: File and range slots
    # Governor slots held while reading.
    # ------------------------------------------------------------------
    async def _ensure_file_slot_async(self):
        if self._closed or self.governor is None:
            return
        slot_factory = getattr(self.governor, "telegram_file_slot", None)
        if not callable(slot_factory):
            return
        async with self._file_slot_lock:
            if self._closed or self._file_slot_held:
                return
            context = slot_factory(file_size=self.size, dc_id=self._file_slot_dc_id,
                                   allow_during_playback=False)
            await context.__aenter__()
            if self._closed:
                try:
                    await context.__aexit__(None, None, None)
                finally:
                    raise asyncio.CancelledError()
            self._file_slot_context = context
            self._file_slot_held = True

    async def _release_file_slot_async(self):
        async with self._file_slot_lock:
            context = self._file_slot_context
            self._file_slot_context = None
            self._file_slot_held = False
            if context is not None:
                await context.__aexit__(None, None, None)

    @asynccontextmanager
    async def _range_slot(self, request_bytes=None):
        if self.governor is not None:
            async with self.governor.telegram_range_slot(file_size=self.size, request_bytes=request_bytes):
                yield
            return
        async with self._rpc_sem:
            with self._rate_lock:
                now = time.monotonic()
                delay = float(self._next_rpc_at) - now
                self._next_rpc_at = max(now, self._next_rpc_at) + self._range_request_interval
            if delay > 0:
                await asyncio.sleep(delay)
            yield

    async def _refresh_message(self):
        try:
            entity = self.entity or getattr(self.message, "chat_id", None) or getattr(self.message, "peer_id", None)
            fresh = await self.client.get_messages(entity, ids=int(self.message.id))
            if fresh:
                self.message = fresh
                return fresh
        except Exception:
            _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._refresh_message")
            pass
        return None


    # ------------------------------------------------------------------
    # SUBSECTION: Chunk fetch and cache
    # Chunk fetching, caching and future waits.
    # ------------------------------------------------------------------
    async def _fetch_chunk_async(self, index):
        """Fetch one aligned chunk, retrying within governor slots."""
        if self._closed:
            raise asyncio.CancelledError()
        offset = int(index) * self.CHUNK
        if offset >= self.size:
            return b""
        amount = min(self.CHUNK, self.size - offset)
        last_error = None
        for attempt in range(1, 4):
            try:
                await self._ensure_file_slot_async()
                with self._flood_lock:
                    flood_until = float(self._flood_until)
                remaining = flood_until - time.monotonic()
                if remaining > 0:
                    await asyncio.sleep(min(remaining, 60.0))
                async def fetch():
                    started = time.monotonic()
                    self.rpc_requests += 1
                    data = await fetch_aligned_range(
                        self.client, self.message, offset, amount, fallback=True,
                        telethon_functions=telethon_lazy.functions(), telethon_types=telethon_lazy.types(),
                        refresh_message=self._refresh_message, cache_policy="coalesce",
                        file_slot_held=True, message_scoped_floodwait=True,
                        admission_managed=self.governor is None,
                    )
                    if len(data) != amount:
                        raise RuntimeError(f"Telegram returned {len(data)}/{amount} bytes")
                    elapsed = max(0.001, time.monotonic() - started)
                    self.bytes_received += len(data)
                    hook = self.on_bytes
                    if hook is not None:
                        try:
                            hook(len(data))
                        except Exception:
                            _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_chunk_async.fetch")
                            pass
                    if self.governor is not None:
                        try:
                            self.governor.note_range_success(len(data), elapsed)
                        except Exception:
                            _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_chunk_async.fetch")
                            pass
                    return data
                # When a governor exists, let fetch_aligned_range acquire and
                # release the shared range permit around each actual RPC. If a
                # single message is FloodWaited, that wait must not retain the
                # shared permit and stall reads for unrelated videos.
                if self.governor is None:
                    async with self._range_slot(request_bytes=amount):
                        data = await fetch()
                else:
                    data = await fetch()
                return data
            except Exception as exc:
                if getattr(exc, "_archive_x_download_flood_noted", False) or getattr(exc, "_archive_x_file_reference_retry_attempted", False):
                    raise
                last_error = exc
                name = type(exc).__name__.upper()
                text = str(exc).upper()
                if "FILE_REFERENCE" in name or "FILE_REFERENCE" in text or "FILE_MIGRATE" in name or "FILE_MIGRATE" in text:
                    try:
                        fresh = await self._refresh_message()
                        if fresh is not None:
                            self.message = fresh
                    except Exception:
                        _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_chunk_async")
                        pass
                wait = flood_wait_seconds(exc)
                if wait:
                    wait = min(max(int(wait), 1), 60)
                    with self._flood_lock:
                        self._flood_until = max(self._flood_until, time.monotonic() + wait)
                    if self.governor is not None and not getattr(exc, "_archive_x_flood_noted", False):
                        try:
                            self.governor.note_floodwait(wait, kind="range", request="GetFile")
                        except Exception:
                            _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_chunk_async")
                            pass
                    if attempt < 3:
                        await asyncio.sleep(wait)
                        continue
                    raise TelegramRangeFloodError(
                        f"Telegram FLOOD_WAIT ({wait}s) while reading video ranges; aborting thumbnail generation"
                    ) from exc
                if self.governor is not None:
                    try:
                        self.governor.note_range_failure()
                    except Exception:
                        _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_chunk_async")
                        pass
                if attempt < 3:
                    await asyncio.sleep(0.08 * attempt)
        raise last_error or IOError("Telegram range fetch failed")

    def _start_fetch(self, index):
        index = int(index)
        with self._lock:
            if index in self._cache:
                return None
            fut = self._inflight.get(index)
            if fut is not None:
                return fut
            fut = asyncio.run_coroutine_threadsafe(self._fetch_chunk_async(index), self.loop)
            self._inflight[index] = fut
            return fut

    def _cache_put(self, index, data):
        with self._lock:
            old = self._cache.pop(index, None)
            if old is not None:
                self._cache_bytes -= len(old)
            self._cache[index] = data
            self._cache_bytes += len(data)
            while self._cache_bytes > self.CACHE_BYTES and self._cache:
                _, old = self._cache.popitem(last=False)
                self._cache_bytes -= len(old)

    def _wait_future(self, index, future):
        try:
            data = future.result(timeout=120)
            self._cache_put(index, data)
            return data
        finally:
            with self._lock:
                self._inflight.pop(int(index), None)

    def _scan_cache_key(self):
        from .scan_range_cache import key_for_message
        return key_for_message(self.message, self.size, getattr(self, "archive_x_cache_key", None))


    # ------------------------------------------------------------------
    # SUBSECTION: Precise range fetch
    # Exact byte range reads that bypass chunk alignment.
    # ------------------------------------------------------------------
    def _submit_precise_fetch(self, offset: int, request_limit: int):
        """Track direct precise requests so watchdog close can cancel their RPCs."""
        if self._closed:
            raise asyncio.CancelledError()
        future = asyncio.run_coroutine_threadsafe(self._fetch_precise_async(offset, request_limit), self.loop)
        with self._lock:
            if self._closed:
                future.cancel()
                raise asyncio.CancelledError()
            self._request_futures.add(future)
        return future

    def _wait_precise_fetch(self, future, timeout: float) -> bytes:
        try:
            return future.result(timeout=timeout)
        finally:
            with self._lock:
                self._request_futures.discard(future)

    async def _fetch_precise_async(self, offset: int, request_limit: int) -> bytes:
        """Fetch an exact range through the shared aligned transport."""
        if self._closed:
            raise asyncio.CancelledError()
        expected = min(int(request_limit), max(0, self.size - int(offset)))
        if expected <= 0:
            return b""
        await self._ensure_file_slot_async()
        async def fetch():
            started = time.monotonic()
            self.rpc_requests += 1
            data = await fetch_aligned_range(
                self.client, self.message, int(offset), expected, fallback=True,
                telethon_functions=telethon_lazy.functions(), telethon_types=telethon_lazy.types(),
                refresh_message=self._refresh_message, cache_policy="caller",
                file_slot_held=True, message_scoped_floodwait=True,
                admission_managed=self.governor is None,
            )
            if len(data) != expected:
                raise RuntimeError(f"Telegram returned {len(data)}/{expected} bytes at offset {offset}")
            elapsed = max(0.001, time.monotonic() - started)
            self.bytes_received += len(data)
            hook = self.on_bytes
            if hook is not None:
                try:
                    hook(len(data))
                except Exception:
                    _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_precise_async.fetch")
                    pass
            if self.governor is not None:
                try:
                    self.governor.note_range_success(len(data), elapsed)
                except Exception:
                    _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource._fetch_precise_async.fetch")
                    pass
            return data
        if self.governor is None:
            async with self._range_slot(request_bytes=request_limit):
                return await fetch()
        return await fetch()
    def get_precise_range_sync(self, start, length):
        """Read only requested bytes, with 1 KiB alignment and 1 MiB boundaries.

        Missing scan-cache holes are downloaded only once, letting OSHASH, moov
        parsing, permanent startup segments and keyframe samples reuse overlap.
        This method is used by scan paths; it never writes media bytes to disk.
        """
        from . import scan_range_cache
        start = max(0, int(start))
        length = max(0, int(length))
        if not length or start >= self.size:
            return b""
        end = min(self.size, start + length)
        cache_key = self._scan_cache_key()
        if cache_key:
            found = scan_range_cache.read(cache_key, start, end - start)
            if found is not None:
                return found
        aligned_start = (start // 1024) * 1024
        aligned_end = ((end + 1023) // 1024) * 1024
        reservation_tokens = []
        if cache_key:
            wait_started = time.monotonic()
            while True:
                reservations, wait_event = scan_range_cache.claim_missing(
                    cache_key, aligned_start, max(0, min(self.size, aligned_end) - aligned_start)
                )
                if reservations is not None:
                    reservation_tokens = reservations
                    break
                if wait_event is None:
                    break
                # A concurrent fingerprint/index/snapshot read already owns an
                # overlapping range. Wait for its bytes to become visible and
                # then re-evaluate gaps rather than duplicating the Telegram RPC.
                remaining = max(0.1, 90.0 - (time.monotonic() - wait_started))
                if not wait_event.wait(min(remaining, 30.0)):
                    if time.monotonic() - wait_started >= 90.0:
                        raise TimeoutError("Timed out waiting for overlapping scan byte-range fetch")
                    continue
                if time.monotonic() - wait_started >= 90.0:
                    raise TimeoutError("Timed out waiting for overlapping scan byte-range fetch")
        else:
            reservation_tokens = [(aligned_start, min(self.size, aligned_end), None)]
        fetched: list[tuple[int, bytes]] = []   # what THIS call downloaded, kept in case the shared cache drops it
        try:
            for gap_start, gap_end, _event in reservation_tokens:
                # Reservation gaps come from existing aligned ranges. Round out
                # defensively, retaining 1 KiB offsets and 1 MiB absolute limits.
                cursor = max(0, (int(gap_start) // 1024) * 1024)
                target_end = min(self.size, ((int(gap_end) + 1023) // 1024) * 1024)
                while cursor < target_end:
                    boundary = ((cursor // (1024 * 1024)) + 1) * (1024 * 1024)
                    request_end = min(target_end, boundary)
                    request_limit = request_end - cursor
                    if request_limit <= 0:
                        break
                    if request_limit % 1024:
                        request_limit = ((request_limit + 1023) // 1024) * 1024
                    request_limit = min(1024 * 1024, request_limit)
                    actual = min(request_limit, self.size - cursor)
                    if actual <= 0:
                        break
                    data = self._wait_precise_fetch(
                        self._submit_precise_fetch(cursor, request_limit), timeout=90
                    )
                    if len(data) < actual:
                        raise RuntimeError(f"Short Telegram range at {cursor}: {len(data)}/{actual}")
                    if cache_key:
                        scan_range_cache.store(cache_key, cursor, data[:actual])
                        fetched.append((cursor, data[:actual]))
                    cursor += actual
                    # A final EOF response may be shorter than the aligned request.
                    if actual < request_limit:
                        break
        finally:
            if cache_key and reservation_tokens:
                scan_range_cache.release_reservations(cache_key, reservation_tokens)
        if cache_key:
            result = scan_range_cache.read(cache_key, start, end - start)
            if result is not None:
                return result
            # The shared cache holds 96 MB for every video being scanned. While this call was downloading, other videos' bytes
            # pushed this one out (the "did not cover" errors that failed snapshots on a busy PC). Use the pieces we hold, and
            # download again only what is missing, instead of failing the frame.
            result = _cover(fetched, start, end)
            if result is not None:
                return result
            return self._fetch_precise_no_cache(start, end)
        # Cache-less callers (such as synthetic adapters) still get a precise
        # one-off read, split only at absolute 1 MiB boundaries.
        return self._fetch_precise_no_cache(start, end)

    def _fetch_precise_no_cache(self, start: int, end: int) -> bytes:
        pieces = []
        cursor = (int(start) // 1024) * 1024
        desired_end = int(end)
        prefix = int(start) - cursor
        while cursor < desired_end:
            boundary = ((cursor // (1024 * 1024)) + 1) * (1024 * 1024)
            limit = min(((desired_end - cursor + 1023) // 1024) * 1024, boundary - cursor)
            data = self._wait_precise_fetch(self._submit_precise_fetch(cursor, limit), timeout=90)
            pieces.append(data)
            cursor += len(data)
            if not data:
                break
        return b"".join(pieces)[prefix:prefix + desired_end - int(start)]


    # ------------------------------------------------------------------
    # SUBSECTION: Synchronous public API
    # Blocking range reads and close.
    # ------------------------------------------------------------------
    def get_range_sync(self, start, length):
        # Scan/index/snapshot consumers benefit from precise reads rather than
        # downloading a complete 1 MiB block for every small keyframe sample.
        return self.get_precise_range_sync(start, length)

    def _get_chunk_sync(self, index):
        """Fetch one whole CHUNK by index, blocking the calling thread.

        Used by RangeHandler's sequential (no-Range-header) streaming loop,
        which walks the file chunk-by-chunk instead of asking for an
        arbitrary byte range. Shares the same cache/inflight bookkeeping as
        get_range_sync so a chunk already pulled in for one path is reused
        by the other instead of being fetched twice.
        """
        index = int(index)
        with self._lock:
            data = self._cache.get(index)
            if data is not None:
                self._cache.move_to_end(index)
                return data
            fut = self._start_fetch(index)
        if fut is None:
            # Another thread finished fetching it between the checks above.
            with self._lock:
                return self._cache.get(index, b"")
        return self._wait_future(index, fut)

    def close(self):
        # Cancel any Telegram range coroutines still waiting on the application
        # event loop before the surrounding asyncio.run()/worker loop closes.
        # This prevents the noisy "Event loop is closed" errors seen after a
        # thumbnail timeout.
        with self._lock:
            self._closed = True
            futures = list(self._inflight.values())
            futures.extend(self._request_futures)
            self._inflight.clear()
            self._request_futures.clear()
        for future in futures:
            try:
                future.cancel()
            except Exception:
                _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource.close")
                pass
        if self._file_slot_context is not None and self.loop is not None and not self.loop.is_closed():
            async def release_slot():
                try:
                    await self._release_file_slot_async()
                except Exception:
                    _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource.close.release_slot")
                    pass
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is self.loop:
                _retain_task(self.loop.create_task(release_slot()))
            else:
                try:
                    asyncio.run_coroutine_threadsafe(release_slot(), self.loop).result(timeout=10)
                except Exception:
                    _swallowed_note("telegram_media_range_resource.DirectTelegramRangeSource.close")
                    pass


