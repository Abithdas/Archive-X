"""OSHASH — the OpenSubtitles "moviehash", the fingerprint Stash and
StashDB/ThePornDB use for exact-file lookups.

hash = file_size + sum(uint64 little-endian words of the first 64 KiB)
                 + sum(uint64 little-endian words of the last 64 KiB)   (mod 2^64)
formatted as 16 lowercase hex digits.

Why it matters here: it needs only ~128 KB from Telegram per video (two
range reads), versus downloading the whole file for MD5 or decoding 25
frames for a perceptual hash. That makes identifying 20,000 videos cheap.
"""
from __future__ import annotations

import struct

CHUNK = 64 * 1024
_MASK = 0xFFFFFFFFFFFFFFFF


def _sum_words(buf: bytes) -> int:
    if len(buf) % 8:
        buf = buf + b"\0" * (8 - len(buf) % 8)
    total = 0
    for (v,) in struct.iter_unpack("<Q", buf):
        total = (total + v) & _MASK
    return total


def expected_lengths(size: int) -> tuple[int, int]:
    """Bytes OSHASH needs from the start and from the end of a file."""
    n = max(0, min(CHUNK, int(size)))
    return n, n


def oshash_from_parts(size: int, head: bytes, tail: bytes) -> str:
    """OSHASH from the first and last 64 KiB.

    Files of 128 KiB or more use exactly 64 KiB from each end (the standard
    value StashDB/ThePornDB store). Smaller files use what exists (head and
    tail overlap, as in the reference OpenSubtitles implementation) instead of
    failing. The lengths are validated: a short read is a read error, not a
    reason to compute a wrong hash.
    """
    size = int(size)
    if size <= 0:
        raise ValueError("empty file")
    want_head, want_tail = expected_lengths(size)
    if len(head) != want_head or len(tail) != want_tail:
        raise ValueError(f"incomplete read: got {len(head)}+{len(tail)} bytes, "
                         f"need {want_head}+{want_tail}")
    return f"{(size + _sum_words(head) + _sum_words(tail)) & _MASK:016x}"


def oshash_from_source(source) -> str:
    """OSHASH using two exact 64 KiB ranges with bounded re-reads.

    Telegram can occasionally return a transient short range. Retry the whole
    fingerprint read a few times so metadata matching is not lost merely
    because one range response was truncated.
    """
    size = int(source.size)
    want_head, want_tail = expected_lengths(size)
    last_error = None
    # V29.4.130: ask the reader for 64 KiB Telegram requests while hashing.
    # The hash needs only 64 KiB at each end; reading them as 512 KiB blocks
    # downloaded ~1 MiB per video instead of ~128-192 KiB.
    small_blocks = _small_blocks(source)
    try:
        for attempt in range(3):
            head = source.get_range_sync(0, want_head)
            tail = source.get_range_sync(max(0, size - want_tail), want_tail)
            try:
                return oshash_from_parts(size, head, tail)
            except ValueError as exc:
                last_error = exc
                if attempt < 2:
                    import time
                    time.sleep(0.25 * (attempt + 1))
        raise last_error or ValueError("incomplete Telegram range read")
    finally:
        small_blocks()


def _small_blocks(source):
    """Switch a range reader to 64 KiB blocks; returns the undo function."""
    switch = getattr(source, "use_block_size", None)
    if not callable(switch):
        return lambda: None
    try:
        return switch(CHUNK) or (lambda: None)
    except Exception:
        return lambda: None


def oshash_file(path: str) -> str:
    """Reference implementation over a local file (used for testing)."""
    import os
    size = os.path.getsize(path)
    want_head, want_tail = expected_lengths(size)
    with open(path, "rb") as f:
        head = f.read(want_head)
        f.seek(size - want_tail)
        tail = f.read(want_tail)
    return oshash_from_parts(size, head, tail)


