"""A snapshot frame is read from Telegram in 1 MiB pieces that go through a shared 96 MB cache and are read back from it. On a busy
PC another video's pieces pushed these out in between, and the frame failed with "Shared scan byte cache did not cover" (and the
whole video then had no snapshots). The read must use what it downloaded, or download again, never fail."""
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axrange_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)

MIB = 1024 * 1024
FILE = bytes((i * 7 + i // 251) % 256 for i in range(5 * MIB + 333))


class _Future:
    def __init__(self, data):
        self.data = data


def _source(monkeypatch, calls):
    from telegram_archive.core import scan_range_cache
    from telegram_archive.core.telegram_media_range_resource import DirectTelegramRangeSource

    src = object.__new__(DirectTelegramRangeSource)
    src.size = len(FILE)
    key = (-100, 7, len(FILE))
    src._scan_cache_key = lambda: key
    src._submit_precise_fetch = lambda offset, limit: _Future(FILE[offset:offset + min(limit, len(FILE) - offset)])
    src._wait_precise_fetch = lambda fut, timeout: (calls.append(1), fut.data)[1]
    scan_range_cache._FILES.clear()
    scan_range_cache._PINNED_RANGES.clear()
    return src, scan_range_cache, key


def test_normal_read_is_exact_and_cached(monkeypatch):
    calls = []
    src, cache, key = _source(monkeypatch, calls)
    assert src.get_precise_range_sync(1_500_000, 2_097_152) == FILE[1_500_000:3_597_152]
    first = len(calls)
    assert src.get_precise_range_sync(1_500_000, 2_097_152) == FILE[1_500_000:3_597_152]
    assert len(calls) == first                      # second read came from the cache


def test_a_cache_that_forgets_everything_still_gives_the_bytes(monkeypatch):
    calls = []
    src, cache, key = _source(monkeypatch, calls)
    monkeypatch.setattr(cache, "store", lambda *a, **k: None)   # the cache drops every piece at once
    assert src.get_precise_range_sync(371_000, 2_097_152) == FILE[371_000:371_000 + 2_097_152]
    assert src.get_precise_range_sync(4 * MIB + 5, 10 * MIB) == FILE[4 * MIB + 5:]   # a read that runs to the end of the file


def test_a_cache_evicted_between_store_and_read_does_not_fail(monkeypatch):
    calls = []
    src, cache, key = _source(monkeypatch, calls)
    real_read = cache.read
    state = {"first": True}

    def read(k, offset, length):
        if state["first"]:
            state["first"] = False
            return real_read(k, offset, length)           # the look before downloading finds nothing
        cache._FILES.clear()                              # another video's bytes evicted ours before we read them back
        return real_read(k, offset, length)

    monkeypatch.setattr(cache, "read", read)
    assert src.get_precise_range_sync(100, 3 * MIB) == FILE[100:100 + 3 * MIB]


def test_cover_joins_pieces_and_reports_gaps():
    from telegram_archive.core.telegram_media_range_resource import _cover
    assert _cover([(0, b"abcd"), (4, b"efgh")], 2, 7) == b"cdefg"
    assert _cover([(0, b"abcd"), (6, b"gh")], 2, 7) is None
    assert _cover([], 0, 1) is None
