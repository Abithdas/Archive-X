"""Shared, Telegram-compliant upload.getFile transport for all media byte reads.

All callers use precise 1-KiB-aligned ranges split at absolute 1-MiB boundaries.
The helper reuses the existing authenticated client and governor; it does not
create logins or sessions. Playback can request a reserved priority lane.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Request validation and error classification
#   - Download primitives and file-reference refresh
#   - Public entry point
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import asyncio
import inspect
import re
import time
from typing import Awaitable, Callable, Any

from . import telethon_lazy   # the Telegram library is loaded when a range is first fetched, not at start-up

RequestCall = Callable[[Any, int, int], Awaitable[Any]]
RefreshCall = Callable[[], Awaitable[Any]]
_ONE_MIB = 1024 * 1024
_ONE_KIB = 1024




# ==============================================================================
# SECTION: Request validation and error classification
# Checks upload.getFile alignment rules and interprets Telegram errors.
# ==============================================================================

def _assert_precise_request(offset: int, limit: int) -> None:
    """Debug assertion for every upload.getFile request this helper emits."""
    assert int(offset) >= 0 and int(offset) % _ONE_KIB == 0, "upload.getFile offset is not 1-KiB aligned"
    assert 0 < int(limit) <= _ONE_MIB, "upload.getFile limit must be 1 KiB..1 MiB"
    assert int(limit) % _ONE_KIB == 0, "upload.getFile limit is not a multiple of 1 KiB"
    assert int(offset) // _ONE_MIB == (int(offset) + int(limit) - 1) // _ONE_MIB, "upload.getFile request crosses a 1-MiB boundary"


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__} {exc}".upper()


def _flood_wait_seconds(exc: BaseException) -> int:
    try:
        seconds = int(getattr(exc, "seconds", 0) or 0)
        if seconds > 0:
            return seconds
    except (TypeError, ValueError, OverflowError):
        pass
    match = re.search(r"FLOOD(?:_PREMIUM)?_WAIT[_ ](\d+)|A\s+WAIT\s+OF\s+(\d+)\s+SECONDS?", _error_text(exc))
    return int(next(group for group in match.groups() if group)) if match else 0


def _file_reference_error(exc: BaseException) -> bool:
    text = _error_text(exc)
    return "FILE_REFERENCE_EXPIRED" in text or "FILE_REFERENCE_INVALID" in text or "FILEREFERENCEEXPIRED" in text or "FILEREFERENCEINVALID" in text


def _file_migrate_dc(exc: BaseException) -> int:
    match = re.search(r"FILE_MIGRATE[_ ](\d+)", _error_text(exc))
    return int(match.group(1)) if match else 0


def _channel_id(message) -> int:
    for value in (
        getattr(message, "_archive_x_channel_id", None),
        getattr(message, "chat_id", None),
        getattr(getattr(message, "peer_id", None), "channel_id", None),
        getattr(getattr(message, "peer_id", None), "chat_id", None),
    ):
        try:
            if value not in (None, "", 0):
                return int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    return 0


def _channel_key(message, size: int):
    try:
        from .scan_range_cache import key_for_message
        return key_for_message(message, size)
    except Exception:
        return None




# ==============================================================================
# SECTION: Download primitives and file-reference refresh
# Bounded iter_download calls and stale file-reference recovery.
# ==============================================================================

async def _bounded_iter_download(client, message, offset: int, expected: int, request_size: int, size: int) -> bytes:
    """Read at most the bounded requested interval (Telethon limit counts chunks)."""
    if expected <= 0:
        return b""
    media = getattr(message, "media", None)
    if media is None:
        raise TypeError("Selected Telegram media has no iterable media object")
    chunks: list[bytes] = []
    got = 0
    async for part in client.iter_download(
        media,
        offset=int(offset),
        limit=1,
        request_size=int(request_size),
        chunk_size=int(request_size),
        file_size=int(size) if size > 0 else None,
    ):
        raw = bytes(part or b"")
        if raw:
            take = min(len(raw), expected - got)
            chunks.append(raw[:take])
            got += take
        if got >= expected:
            break
    data = b"".join(chunks)
    if len(data) != expected:
        raise IOError(f"bounded iter_download short read {len(data)}/{expected} at offset {offset}")
    return data



def _propagate_fresh_file_reference(original, fresh) -> None:
    """Update the original message's mutable document references for later reads."""
    fresh_doc = getattr(fresh, "document", None)
    if fresh_doc is None:
        return
    candidates = [getattr(original, "document", None), getattr(getattr(original, "media", None), "document", None)]
    seen = set()
    for old_doc in candidates:
        if old_doc is None or id(old_doc) in seen:
            continue
        seen.add(id(old_doc))
        for name in ("file_reference", "access_hash", "dc_id"):
            try:
                value = getattr(fresh_doc, name)
                setattr(old_doc, name, value)
            except Exception:
                _swallowed_note("aligned_telegram_range._propagate_fresh_file_reference")
                pass
    try:
        migrated = int(getattr(fresh, "_archive_x_download_dc_id", 0) or 0)
        if migrated:
            setattr(original, "_archive_x_download_dc_id", migrated)
    except Exception:
        _swallowed_note("aligned_telegram_range._propagate_fresh_file_reference")
        pass

async def _refresh_message(client, message, refresh_call: RefreshCall | None):
    if refresh_call is not None:
        value = refresh_call()
        if inspect.isawaitable(value):
            value = await value
        return value or None
    mid = int(getattr(message, "id", 0) or 0)
    entity = getattr(message, "chat_id", None) or getattr(message, "peer_id", None) or _channel_id(message)
    if not mid or entity is None:
        return None
    fresh = await client.get_messages(entity, ids=mid)
    return fresh or None


async def _dispatch_file_request(client, request, message, request_call: RequestCall | None):
    """Send one request on its document DC using the already-authorized sender pool."""
    if request_call is not None:
        return await request_call(request, int(request.offset), int(request.limit))

    document = getattr(message, "document", None)
    target_dc = int(getattr(message, "_archive_x_download_dc_id", 0) or (getattr(document, "dc_id", 0) if document is not None else 0) or 0)
    home_dc = int(getattr(getattr(client, "session", None), "dc_id", 0) or 0)

    async def send_on_dc(dc_id: int):
        borrower = getattr(client, "_borrow_exported_sender", None)
        returner = getattr(client, "_return_exported_sender", None)
        caller = getattr(client, "_call", None)
        if dc_id and home_dc and dc_id != home_dc and callable(borrower) and callable(returner) and callable(caller):
            sender = await borrower(dc_id)
            try:
                return await caller(sender, request)
            finally:
                await returner(sender)
        return await client(request)

    try:
        return await send_on_dc(target_dc)
    except Exception as exc:
        migrated_dc = _file_migrate_dc(exc)
        if not migrated_dc or migrated_dc == target_dc:
            raise
        document = getattr(message, "document", None)
        if document is not None:
            try:
                document.dc_id = int(migrated_dc)
            except Exception:
                _swallowed_note("aligned_telegram_range._dispatch_file_request")
                pass
        # Remember the migrated DC on the message/document for later ranges.
        setattr(message, "_archive_x_download_dc_id", int(migrated_dc)) if hasattr(message, "__dict__") else None
        return await send_on_dc(int(migrated_dc))




# ==============================================================================
# SECTION: Public entry point
# fetch_aligned_range: the shared range fetch used by media byte readers.
# ==============================================================================

async def fetch_aligned_range(
    client,
    message,
    offset: int,
    amount: int,
    *,
    request_call: RequestCall | None = None,
    fallback: bool = True,
    allow_direct: bool = True,
    telethon_functions=None,
    telethon_types=None,
    priority: str = "background",
    cache_policy: str = "caller",
    refresh_message: RefreshCall | None = None,
    admission_managed: bool = False,
    file_slot_held: bool = False,
    message_scoped_floodwait: bool = False,
    status_callback=None,
) -> bytes:
    """Fetch an arbitrary byte interval using compliant ``precise=True`` ranges.

    All callers share the scan range cache. Source wrappers may reserve/cache
    their own blocks with ``cache_policy='caller'``. ``cache_policy='coalesce'``
    lets the helper reserve missing ranges itself, so playback and background
    reads never download the same in-flight bytes twice.
    """
    start = max(0, int(offset))
    length = max(0, int(amount))
    file_obj = getattr(message, "file", None)
    size = max(0, int(getattr(file_obj, "size", 0) or 0))
    if size:
        length = min(length, max(0, size - start))
    if length <= 0:
        return b""
    end = start + length
    aligned_start = (start // _ONE_KIB) * _ONE_KIB
    aligned_end = ((end + _ONE_KIB - 1) // _ONE_KIB) * _ONE_KIB
    document = getattr(message, "document", None)
    api_functions = telethon_functions if telethon_functions is not None else telethon_lazy.functions()
    api_types = telethon_types if telethon_types is not None else telethon_lazy.types()
    if allow_direct and document is None:
        raise TypeError("Selected Telegram video has no document backing")
    if allow_direct and (api_functions is None or api_types is None):
        raise RuntimeError("Telethon is required for Telegram media range access")

    cache_key = _channel_key(message, size) if cache_policy != "none" else None
    cache_module = None
    if cache_key:
        try:
            from . import scan_range_cache as cache_module
            cached = cache_module.read(cache_key, start, length)
            if cached is not None and len(cached) == length:
                return cached
        except Exception:
            cache_module = None
            cache_key = None

    governor = getattr(client, "_telegram_archive_governor", None)
    priority_value = str(priority or "").lower()
    lane = priority_value if priority_value in {"playback", "fingerprint"} else "range"

    async def wait_lane():
        wait_func = getattr(governor, "wait_download_lane_async", None) if governor is not None else None
        if callable(wait_func):
            await wait_func(lane)

    async def with_download_slots(current_message, request_bytes: int, operation):
        """Use the shared range lane and per-DC cap exactly once for each byte request."""
        current_doc = getattr(current_message, "document", None)
        current_dc = int(
            getattr(current_message, "_archive_x_download_dc_id", 0)
            or getattr(current_doc, "dc_id", 0)
            or getattr(getattr(client, "session", None), "dc_id", 0)
            or 0
        ) if current_doc is not None else 0
        file_slot = getattr(governor, "telegram_file_slot", None) if (lane not in {"playback", "fingerprint"} and not file_slot_held and governor is not None) else None
        range_slot = getattr(governor, "telegram_range_slot", None) if (lane == "range" and governor is not None and not admission_managed) else None

        async def per_dc_operation():
            if callable(file_slot):
                async with file_slot(file_size=size, dc_id=current_dc, allow_during_playback=True):
                    return await operation()
            return await operation()

        if callable(range_slot):
            async with range_slot(file_size=size, request_bytes=max(1024, int(request_bytes)), allow_during_playback=True):
                return await per_dc_operation()
        return await per_dc_operation()

    async def send_one(request, pos: int, limit: int, current_message):
        """One guarded request, with range and per-DC admission held only while active."""
        await wait_lane()
        return await with_download_slots(
            current_message, limit,
            lambda: _dispatch_file_request(client, request, current_message, request_call),
        )

    async def fetch_span(span_start: int, span_end: int, *, current_message):
        """Fetch a reserved missing span and store exact response bytes if asked."""
        nonlocal message, document
        cursor = max(0, (int(span_start) // _ONE_KIB) * _ONE_KIB)
        stop = ((int(span_end) + _ONE_KIB - 1) // _ONE_KIB) * _ONE_KIB
        out = bytearray()
        reference_refreshed = False
        while cursor < stop:
            boundary = ((cursor // _ONE_MIB) + 1) * _ONE_MIB
            rounded_remaining = ((stop - cursor + _ONE_KIB - 1) // _ONE_KIB) * _ONE_KIB
            limit = min(boundary - cursor, rounded_remaining, _ONE_MIB)
            limit = (limit // _ONE_KIB) * _ONE_KIB
            if limit <= 0:
                raise AssertionError(f"Unable to form a precise Telegram range at {cursor}")
            _assert_precise_request(cursor, limit)
            expected = min(limit, max(0, size - cursor)) if size else limit
            if expected <= 0:
                break

            flood_retried = 0
            while True:
                try:
                    data = None
                    if allow_direct:
                        document = getattr(current_message, "document", None)
                        request = api_functions.upload.GetFileRequest(
                            location=api_types.InputDocumentFileLocation(
                                id=int(document.id),
                                access_hash=int(document.access_hash),
                                file_reference=bytes(document.file_reference or b""),
                                thumb_size="",
                            ),
                            offset=int(cursor),
                            limit=int(limit),
                            precise=True,
                            cdn_supported=True,
                        )
                        _assert_precise_request(int(request.offset), int(request.limit))
                        result = await send_one(request, cursor, limit, current_message)
                        raw = getattr(result, "bytes", None)
                        if raw is not None:
                            data = bytes(raw)[:expected]
                        # fileCdnRedirect has no .bytes; Telethon's bounded
                        # iter_download fallback follows the CDN token/hash path.
                    if data is None and fallback:
                        await wait_lane()
                        data = await with_download_slots(
                            current_message, limit,
                            lambda: _bounded_iter_download(client, current_message, cursor, expected, limit, size),
                        )
                    elif data is None:
                        return b""
                    if len(data) != expected:
                        raise IOError(f"Telegram aligned range short read {len(data)}/{expected} at offset {cursor}")
                    out.extend(data)
                    if cache_key and cache_module is not None and cache_policy == "coalesce":
                        cache_module.store(cache_key, cursor, data)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    wait_seconds = _flood_wait_seconds(exc)
                    if wait_seconds:
                        try:
                            exc._archive_x_download_flood_noted = True
                            if message_scoped_floodwait:
                                # The response throttled this file/message, not
                                # every reader in the shared background lane.
                                exc._archive_x_flood_noted = True
                        except Exception:
                            _swallowed_note("aligned_telegram_range.fetch_aligned_range.fetch_span")
                            pass
                        max_flood_retries = 2 if message_scoped_floodwait else 1
                        if int(flood_retried) < max_flood_retries:
                            if callable(status_callback):
                                try:
                                    cb = status_callback(f"Telegram asked to slow down — resuming in {wait_seconds}s", wait_seconds)
                                    if inspect.isawaitable(cb):
                                        await cb
                                except Exception:
                                    _swallowed_note("aligned_telegram_range.fetch_aligned_range.fetch_span")
                                    pass
                            if message_scoped_floodwait:
                                await asyncio.sleep(wait_seconds)
                            elif governor is not None and callable(getattr(governor, "note_download_floodwait", None)):
                                governor.note_download_floodwait(
                                    wait_seconds, lane=lane, request="GetFile",
                                    channel_id=_channel_id(current_message), message_id=int(getattr(current_message, "id", 0) or 0),
                                )
                            else:
                                await asyncio.sleep(wait_seconds)
                            flood_retried = int(flood_retried) + 1
                            await wait_lane()
                            continue
                        raise
                    if _file_reference_error(exc) and reference_refreshed:
                        try:
                            exc._archive_x_file_reference_retry_attempted = True
                        except Exception:
                            _swallowed_note("aligned_telegram_range.fetch_aligned_range.fetch_span")
                            pass
                        raise
                    if _file_reference_error(exc) and not reference_refreshed:
                        reference_refreshed = True
                        fresh = None
                        try:
                            fresh = await _refresh_message(client, current_message, refresh_message)
                        except Exception:
                            fresh = None
                        if fresh is not None:
                            _propagate_fresh_file_reference(current_message, fresh)
                            current_message = fresh
                            message = fresh
                            document = getattr(current_message, "document", None)
                            try:
                                if governor is not None and callable(getattr(governor, "note_file_reference_refresh", None)):
                                    governor.note_file_reference_refresh(channel_id=_channel_id(current_message), message_id=int(getattr(current_message, "id", 0) or 0))
                            except Exception:
                                _swallowed_note("aligned_telegram_range.fetch_aligned_range.fetch_span")
                                pass
                            # Rebuild the InputDocumentFileLocation from the fresh message.
                            continue
                        try:
                            exc._archive_x_file_reference_retry_attempted = True
                        except Exception:
                            _swallowed_note("aligned_telegram_range.fetch_aligned_range.fetch_span")
                            pass
                    raise
            cursor += expected
            if expected < limit:
                break
        return bytes(out)

    if cache_key and cache_module is not None and cache_policy == "coalesce":
        # A cache read for this whole range failed above. Claim every current
        # hole atomically, wait when another caller owns an overlap, and fetch
        # only reserved holes. The slot is acquired inside send_one, after the
        # claim, so waiting here can never hold scarce per-DC capacity.
        claim_start = aligned_start
        claim_end = min(size, aligned_end) if size else aligned_end
        wait_started = time.monotonic()
        while True:
            reservations, wait_event = cache_module.claim_missing(cache_key, claim_start, max(0, claim_end - claim_start))
            if reservations is not None:
                break
            if wait_event is not None:
                remaining = 90.0 - (time.monotonic() - wait_started)
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for overlapping Telegram byte-range download")
                await asyncio.to_thread(wait_event.wait, min(remaining, 5.0))
                cached = cache_module.read(cache_key, start, length)
                if cached is not None and len(cached) == length:
                    return cached
                continue
            reservations = []
            break
        fetched_parts: list[tuple[int, bytes]] = []
        try:
            for gap_start, gap_end, _event in reservations:
                data = await fetch_span(gap_start, gap_end, current_message=message)
                fetched_parts.append((gap_start, data))
        finally:
            cache_module.release_reservations(cache_key, reservations)
        cached = cache_module.read(cache_key, start, length)
        if cached is not None and len(cached) == length:
            return cached
        # Cache under eviction pressure — the bytes were fetched from
        # Telegram and stored, but the bounded cache evicted them before
        # the read-back.  Use the fetched data directly to avoid the
        # IOError that previously blocked all playback for large files.
        for fp_start, fp_data in fetched_parts:
            fp_end = fp_start + len(fp_data)
            if fp_start <= start and fp_end >= start + length:
                local = start - fp_start
                return fp_data[local:local + length]
        # Multiple disjoint gaps or partial coverage — fall through to
        # the direct (non-coalesce) fetch below which always works.

    aligned_data = await fetch_span(aligned_start, min(size, aligned_end) if size else aligned_end, current_message=message)
    local_start = start - aligned_start
    output = aligned_data[local_start:local_start + length]
    if len(output) != length:
        raise IOError(f"Telegram assembled range short read {len(output)}/{length} at offset {start}")
    return output
