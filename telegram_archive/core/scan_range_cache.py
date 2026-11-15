"""Short-lived byte-range reuse for one Metadata & Assets pass.

Ranges are bounded in RAM. Only OSHASH head/tail intervals can be pinned to a
temporary directory when a scan is active, so a delayed snapshot phase can reuse
them after RAM eviction. The temporary files are deleted when the scan ends.
"""
from __future__ import annotations

import threading
import time
import os
import shutil
import tempfile
from pathlib import Path
from collections import OrderedDict

_LOCK = threading.RLock()
_FILES: OrderedDict[tuple[int, int, int], dict] = OrderedDict()
_INFLIGHT: dict[tuple[int, int, int], list[tuple[int, int, threading.Event]]] = {}
# Fingerprint reads can lead the snapshot worker by hours on very large
# channels. Pin only the two tiny OSHASH ranges to a temporary scan directory
# so their bytes remain reusable after the bounded RAM cache evicts them.
_PINNED_RANGES: dict[tuple[int, int, int], list[tuple[int, int, str]]] = {}
_SCAN_TOKENS: set[str] = set()
_SCAN_ROOT: Path | None = None
_MAX_TOTAL_BYTES = 96 * 1024 * 1024
_MAX_FILE_BYTES = 128 * 1024 * 1024  # effectively capped by _MAX_TOTAL_BYTES
_TTL_SECONDS = 15 * 60
_TOTAL_BYTES = 0


def key_for_message(message, size: int | None = None, explicit=None):
    """Return a stable numeric key for a Telegram message, or None."""
    try:
        if explicit and len(explicit) >= 2:
            cid, mid = int(explicit[0]), int(explicit[1])
        else:
            cid = int(getattr(message, "chat_id", 0) or 0)
            mid = int(getattr(message, "id", 0) or 0)
            if not cid:
                peer = getattr(message, "peer_id", None)
                cid = int(getattr(peer, "channel_id", 0) or getattr(peer, "chat_id", 0) or getattr(peer, "user_id", 0) or 0)
        amount = int(size if size is not None else getattr(getattr(message, "file", None), "size", 0) or 0)
        return (cid, mid, amount) if cid and mid and amount > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _expire(now: float) -> None:
    global _TOTAL_BYTES
    for key in list(_FILES.keys()):
        record = _FILES[key]
        if now - float(record.get("touched", now)) > _TTL_SECONDS:
            _TOTAL_BYTES -= sum(len(data) for _, data in record["ranges"])
            _FILES.pop(key, None)


def _merge(ranges: list[tuple[int, bytes]], start: int, data: bytes) -> list[tuple[int, bytes]]:
    if not data:
        return ranges
    items = sorted([*ranges, (int(start), bytes(data))], key=lambda item: item[0])
    merged: list[tuple[int, bytes]] = []
    for begin, chunk in items:
        if not merged:
            merged.append((begin, chunk))
            continue
        prev_start, prev_data = merged[-1]
        prev_end = prev_start + len(prev_data)
        end = begin + len(chunk)
        if begin > prev_end:
            merged.append((begin, chunk))
            continue
        union_end = max(prev_end, end)
        buf = bytearray(union_end - prev_start)
        buf[:len(prev_data)] = prev_data
        offset = begin - prev_start
        # Either source has identical bytes from the same immutable Telegram file.
        buf[offset:offset + len(chunk)] = chunk
        merged[-1] = (prev_start, bytes(buf))
    return merged


def store(key, offset: int, data: bytes) -> None:
    global _TOTAL_BYTES
    if not key or not data:
        return
    key = tuple(int(x) for x in key)
    now = time.monotonic()
    with _LOCK:
        _expire(now)
        record = _FILES.pop(key, {"ranges": [], "touched": now})
        before = sum(len(blob) for _, blob in record["ranges"])
        ranges = _merge(record["ranges"], int(offset), bytes(data))
        after = sum(len(blob) for _, blob in ranges)
        _TOTAL_BYTES += after - before
        record["ranges"] = ranges
        record["touched"] = now
        _FILES[key] = record
        if after > _MAX_FILE_BYTES:
            # Keep newest/most useful intervals only; don't grow without bound.
            keep = []
            budget = _MAX_FILE_BYTES
            for begin, blob in reversed(ranges):
                if budget <= 0:
                    break
                if len(blob) > budget:
                    begin += len(blob) - budget
                    blob = blob[-budget:]
                keep.append((begin, blob))
                budget -= len(blob)
            keep.reverse()
            _TOTAL_BYTES += sum(len(b) for _, b in keep) - after
            record["ranges"] = keep
        # Over the limit: drop the least recently used OTHER videos first. This one was just touched, so it goes last; and when it
        # is the only one left, drop only its oldest ranges. (It used to be popped whole, together with the bytes just stored, so a
        # video whose snapshot windows passed 96 MB failed its last frames with "did not cover".)
        while _TOTAL_BYTES > _MAX_TOTAL_BYTES and len(_FILES) > 1:
            old_key, old_record = _FILES.popitem(last=False)
            _TOTAL_BYTES -= sum(len(blob) for _, blob in old_record["ranges"])
        if _TOTAL_BYTES > _MAX_TOTAL_BYTES and _FILES:
            mine = _FILES[key]
            ranges = list(mine["ranges"])
            newest = int(offset)
            # keep the range that holds the bytes just stored; drop the others, oldest position first, until it fits
            ranges.sort(key=lambda item: (item[0] <= newest < item[0] + len(item[1]), item[0]))
            while ranges and _TOTAL_BYTES > _MAX_TOTAL_BYTES and not (ranges[0][0] <= newest < ranges[0][0] + len(ranges[0][1])):
                _TOTAL_BYTES -= len(ranges.pop(0)[1])
            mine["ranges"] = sorted(ranges, key=lambda item: item[0])
        _TOTAL_BYTES = max(0, _TOTAL_BYTES)


def read(key, offset: int, length: int) -> bytes | None:
    """Read an exact interval from bounded RAM ranges or pinned scan ranges."""
    if not key or length <= 0:
        return b"" if length == 0 else None
    key = tuple(int(x) for x in key)
    start, end = int(offset), int(offset) + int(length)
    now = time.monotonic()
    with _LOCK:
        _expire(now)
        record = _FILES.get(key)
        memory_ranges = list(record["ranges"]) if record else []
        pinned_ranges = list(_PINNED_RANGES.get(key, []))
        if record:
            record["touched"] = now
            _FILES.move_to_end(key)
        # The exact bytes are from one immutable Telegram document, so any
        # covering overlap is interchangeable. Prefer the in-memory copy.
        cursor = start
        parts = []
        while cursor < end:
            choices = []
            for begin, blob in memory_ranges:
                finish = begin + len(blob)
                if begin <= cursor < finish:
                    choices.append((finish, "memory", begin, blob))
            for begin, finish, path in pinned_ranges:
                if begin <= cursor < finish:
                    choices.append((finish, "disk", begin, path))
            if not choices:
                return None
            finish, kind, begin, value = max(choices, key=lambda item: (item[0], item[1] == "memory"))
            take_end = min(end, finish)
            if kind == "memory":
                parts.append(value[cursor - begin:take_end - begin])
            else:
                try:
                    with open(value, "rb") as fh:
                        fh.seek(cursor - begin)
                        chunk = fh.read(take_end - cursor)
                except OSError:
                    _PINNED_RANGES[key] = [entry for entry in _PINNED_RANGES.get(key, []) if entry[2] != value]
                    return None
                if len(chunk) != take_end - cursor:
                    return None
                parts.append(chunk)
            cursor = take_end
        return b"".join(parts)


def begin_scan() -> str:
    """Open a temporary disk-backed range session for one Metadata & Assets run."""
    global _SCAN_ROOT
    import uuid
    token = uuid.uuid4().hex
    with _LOCK:
        if _SCAN_ROOT is None:
            _SCAN_ROOT = Path(tempfile.mkdtemp(prefix="archive-x-scan-ranges-"))
        _SCAN_TOKENS.add(token)
    return token


def end_scan(token: str | None) -> None:
    """Delete temporary fingerprint range pins after the scan has finished."""
    global _SCAN_ROOT
    root_to_remove = None
    with _LOCK:
        if token:
            _SCAN_TOKENS.discard(str(token))
        if not _SCAN_TOKENS and _SCAN_ROOT is not None:
            root_to_remove = _SCAN_ROOT
            _SCAN_ROOT = None
            _PINNED_RANGES.clear()
    if root_to_remove is not None:
        shutil.rmtree(root_to_remove, ignore_errors=True)


def pin_ranges(key, ranges: list[tuple[int, int]]) -> int:
    """Pin existing cached intervals to temporary scan storage; return bytes pinned.

    ``ranges`` contains ``(offset, length)`` items. No Telegram operation is
    performed; unavailable intervals are ignored and remain ordinary RAM cache
    entries. This is intended for OSHASH head/tail blocks shared with the
    slower snapshot phase.
    """
    if not key:
        return 0
    key = tuple(int(x) for x in key)
    pinned = 0
    for offset, length in ranges or []:
        offset, length = max(0, int(offset)), max(0, int(length))
        if length <= 0:
            continue
        payload = read(key, offset, length)
        if payload is None or len(payload) != length:
            continue
        with _LOCK:
            root = _SCAN_ROOT
            if root is None:
                continue
            target_dir = root / str(key[0]) / f"{key[1]}_{key[2]}"
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{offset}_{length}.bin"
            temp = target.with_suffix(f".tmp-{threading.get_ident()}")
            try:
                with open(temp, "wb") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp, target)
            finally:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
            rows = _PINNED_RANGES.setdefault(key, [])
            rows[:] = [row for row in rows if not (row[0] == offset and row[1] == offset + length)]
            rows.append((offset, offset + length, str(target)))
            pinned += length
    return pinned

def gaps(key, offset: int, length: int) -> list[tuple[int, int]]:
    """Return uncovered [start,end) spans within the requested range."""
    if length <= 0:
        return []
    start, end = int(offset), int(offset) + int(length)
    cursor = start
    missing = []
    now = time.monotonic()
    with _LOCK:
        _expire(now)
        normalized_key = tuple(int(x) for x in key) if key else None
        record = _FILES.get(normalized_key) if normalized_key else None
        ranges = [(begin, begin + len(blob)) for begin, blob in (list(record["ranges"]) if record else [])]
        ranges.extend((begin, finish) for begin, finish, _path in _PINNED_RANGES.get(normalized_key, []))
        if record:
            record["touched"] = now
            _FILES.move_to_end(tuple(int(x) for x in key))
    for begin, finish in sorted(ranges):
        if finish <= cursor or begin >= end:
            continue
        if begin > cursor:
            missing.append((cursor, min(begin, end)))
        cursor = max(cursor, finish)
        if cursor >= end:
            break
    if cursor < end:
        missing.append((cursor, end))
    return [(a, b) for a, b in missing if b > a]


def claim_missing(key, offset: int, length: int):
    """Atomically reserve missing byte intervals for one file.

    Returns ``(reservations, event)``. If another task is already fetching any
    overlapping part, reservations is None and the caller waits on event before
    retrying. Otherwise each returned token is ``(start, end, event)`` and must
    be released with :func:`release_reservations` in a finally block.
    """
    if not key or length <= 0:
        return [], None
    key = tuple(int(x) for x in key)
    start, end = int(offset), int(offset) + int(length)
    now = time.monotonic()
    with _LOCK:
        _expire(now)
        record = _FILES.get(key)
        ranges = [(begin, begin + len(blob)) for begin, blob in (list(record["ranges"]) if record else [])]
        ranges.extend((begin, finish) for begin, finish, _path in _PINNED_RANGES.get(key, []))
        if record:
            record["touched"] = now
            _FILES.move_to_end(key)
        for flight_start, flight_end, event in _INFLIGHT.get(key, []):
            if flight_start < end and flight_end > start:
                return None, event
        cursor = start
        missing = []
        for begin, finish in sorted(ranges):
            if finish <= cursor or begin >= end:
                continue
            if begin > cursor:
                missing.append((cursor, min(begin, end)))
            cursor = max(cursor, finish)
            if cursor >= end:
                break
        if cursor < end:
            missing.append((cursor, end))
        reservations = []
        for a, b in missing:
            if b > a:
                reservations.append((a, b, threading.Event()))
        if reservations:
            _INFLIGHT.setdefault(key, []).extend(reservations)
        return reservations, None


def release_reservations(key, reservations) -> None:
    if not key or not reservations:
        return
    key = tuple(int(x) for x in key)
    events = []
    with _LOCK:
        current = _INFLIGHT.get(key, [])
        remove_ids = {id(token[2]) for token in reservations}
        keep = []
        for token in current:
            if id(token[2]) in remove_ids:
                events.append(token[2])
            else:
                keep.append(token)
        if keep:
            _INFLIGHT[key] = keep
        else:
            _INFLIGHT.pop(key, None)
    for event in events:
        event.set()


def stats() -> dict:
    with _LOCK:
        return {"files": len(_FILES), "bytes": _TOTAL_BYTES, "inflight_ranges": sum(len(v) for v in _INFLIGHT.values()), "ttl_seconds": _TTL_SECONDS}
