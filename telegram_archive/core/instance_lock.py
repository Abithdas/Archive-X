"""One Archive X process per data folder.

The Windows launcher checks for a running instance, but the Python process
had no guard of its own. A second process (double launch race, run.py,
scheduled task) ran the full startup, including job recovery that marked
the live instance's jobs INTERRUPTED and a second Telethon client on the same
session file, before Uvicorn failed to bind the port.

The lock is an OS file lock held for the life of the process and released
automatically if the process dies, so a crash never leaves a stale lock.
On Windows the locked byte sits far beyond EOF, so backup/data-move code
can still read the (empty) file.
"""
from __future__ import annotations

import os
from pathlib import Path

_HANDLE = None
_LOCK_OFFSET = 1 << 30


class AlreadyRunning(RuntimeError):
    """Another Archive X instance holds the data folder lock."""
    pass


def acquire(data_dir: Path) -> Path:
    global _HANDLE
    if _HANDLE is not None:
        return Path(_HANDLE.name)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / ".instance.lock"
    handle = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(f"Archive X is already running with data folder {data_dir}") from exc
    _HANDLE = handle
    return path
