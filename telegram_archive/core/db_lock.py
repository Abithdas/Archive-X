"""The database's one lock, with a record of who holds it and for how long.

Every database call takes this lock, so when one thread keeps it for tens of seconds the web server's event loop (which also needs it) freezes
and the whole app feels stuck. A plain lock cannot say who is holding it; this one can, so the freeze log names the code responsible instead
of only the code that was waiting.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
import weakref

_ALL: "weakref.WeakSet[TrackedRLock]" = weakref.WeakSet()

# Holds at least this long are counted by the code that held them, so the slow ones can be found and fixed (see slow_summary).
SLOW_HOLD_SECONDS = 0.25
_MAX_SITES = 200
_SLOW: dict[str, list] = {}          # "file:line function" of the holder -> [times, total seconds, longest seconds]
_SLOW_LOCK = threading.Lock()
_THIS_FILE = __file__.replace(".pyc", ".py")


def _note_slow(seconds: float) -> None:
    """Called when the lock is released after a long hold: the stack here is the code that held it."""
    try:
        site = "?"
        for frame in reversed(traceback.extract_stack(limit=8)):
            if frame.filename.replace(".pyc", ".py") != _THIS_FILE:
                site = f"{frame.filename.replace(chr(92), '/').rsplit('/telegram_archive/', 1)[-1]}:{frame.lineno} {frame.name}"
                break
        with _SLOW_LOCK:
            row = _SLOW.get(site)
            if row is None:
                if len(_SLOW) >= _MAX_SITES:
                    return
                row = _SLOW[site] = [0, 0.0, 0.0]
            row[0] += 1
            row[1] += seconds
            row[2] = max(row[2], seconds)
    except Exception:
        pass


def slow_summary(limit: int = 12) -> list[dict]:
    """The code that held the database lock for 250 ms or more, longest total first: where the app can be made to stop waiting."""
    with _SLOW_LOCK:
        rows = sorted(_SLOW.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]
    return [{"where": site, "times": r[0], "total_seconds": round(r[1], 2), "longest_seconds": round(r[2], 2)} for site, r in rows]


class TrackedRLock:
    """A re-entrant lock that remembers its owner and when the owner first took it. Same use as threading.RLock."""

    __slots__ = ("_lock", "_owner", "_since", "_depth", "__weakref__")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner: int | None = None
        self._since = 0.0
        self._depth = 0
        _ALL.add(self)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        got = self._lock.acquire(blocking, timeout)
        if got:
            if self._depth == 0:
                self._owner = threading.get_ident()
                self._since = time.monotonic()
            self._depth += 1
        return got

    def release(self) -> None:
        # Only the owner may release (RLock raises otherwise, before this point is reached).
        if self._depth == 1:
            held = time.monotonic() - self._since
            self._owner = None
            self._depth = 0
            if held >= SLOW_HOLD_SECONDS:
                _note_slow(held)
        elif self._depth > 1:
            self._depth -= 1
        self._lock.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()

    def held_for(self) -> tuple[int | None, float]:
        """(thread id of the holder, seconds it has held the lock); (None, 0) when free."""
        owner, since = self._owner, self._since
        return (owner, time.monotonic() - since) if owner is not None else (None, 0.0)


def long_holds(min_seconds: float = 2.0) -> str:
    """A text for the freeze log: each database lock held longer than `min_seconds`, who holds it and what that thread is doing."""
    frames = sys._current_frames()
    names = {t.ident: t.name for t in threading.enumerate()}
    out: list[str] = []
    for lock in list(_ALL):
        owner, seconds = lock.held_for()
        if owner is None or seconds < min_seconds:
            continue
        frame = frames.get(owner)
        stack = "".join(traceback.format_stack(frame)) if frame is not None else "(stack unavailable)\n"
        out.append(f"database lock held for {seconds:.1f}s by thread {names.get(owner, owner)}; it is running:\n{stack[-4000:]}")
    return "\n".join(out)
