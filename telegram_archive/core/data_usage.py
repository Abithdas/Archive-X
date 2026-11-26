"""How many files and bytes the data folder holds, for Settings, without walking the whole folder on every visit.

The folder grows with the library (a thumbnail and a metadata file for every video), and walking it with `Path.rglob` and a `stat` for each file took
1.2 s for 250,000 files in isolation and 3.4 s inside the running app. Two changes: a direct directory scan (about twice as fast, with the same
totals: files are counted as before, a link to a file included, and a link to a folder is not followed), and the answer is kept. A kept answer is
returned at once; when it is more than `REFRESH_AFTER` seconds old it is measured again in the background, so only the very first visit waits.
"""
from __future__ import annotations

import os
import threading
import time

REFRESH_AFTER = 60.0
_CACHE: dict[str, tuple[float, int, int]] = {}      # root -> (when measured, files, bytes)
_BUSY: set[str] = set()
_LOCK = threading.Lock()


def measure(root) -> tuple[int, int]:
    """(number of files, bytes) under `root`, by a direct scan."""
    count = total = 0
    stack = [str(root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file():
                            count += 1
                            total += entry.stat().st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return count, total


def _refresh(key: str) -> tuple[int, int]:
    files, size = measure(key)
    with _LOCK:
        _CACHE[key] = (time.monotonic(), files, size)
        _BUSY.discard(key)
    return files, size


def usage(root) -> tuple[int, int]:
    """The files and bytes under `root`: the kept answer at once, measured again in the background when it is old; the first time, measured now."""
    key = str(root)
    with _LOCK:
        kept = _CACHE.get(key)
        stale = kept is not None and time.monotonic() - kept[0] >= REFRESH_AFTER and key not in _BUSY
        if stale:
            _BUSY.add(key)
    if kept is None:
        return _refresh(key)
    if stale:
        threading.Thread(target=_refresh, args=(key,), name="data-usage-refresh", daemon=True).start()
    return kept[1], kept[2]


def clear() -> None:
    with _LOCK:
        _CACHE.clear()
        _BUSY.clear()
