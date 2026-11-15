"""Leave a trace when the process dies from inside native code.

A crash in a native library (the video decoder, an image library) ends the process at once and nothing reaches the normal log: a user's log
simply stops in the middle of decoding a snapshot frame, and the next start reports an unclean shutdown. `faulthandler` writes the Python stack of
every thread to a file when that happens, so the next log bundle shows where it was.
"""
from __future__ import annotations

import faulthandler
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 256 * 1024
_FILE = None   # kept open for as long as the process runs: faulthandler writes to it from the crash handler


def install(log_dir: Path) -> Path | None:
    """Enable the handler, writing to `<log_dir>/native-crash.log`. Returns the file, or None if it could not be set up."""
    global _FILE
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "native-crash.log"
        if path.exists() and path.stat().st_size > MAX_BYTES:
            path.write_text("", encoding="utf-8")   # old crashes are history: keep the file small
        _FILE = open(path, "a", encoding="utf-8", buffering=1)
        _FILE.write(f"=== started {datetime.now(timezone.utc).isoformat(timespec='seconds')} pid {os.getpid()} (a traceback below this line means the process crashed)\n")
        faulthandler.enable(file=_FILE, all_threads=True)
        return path
    except Exception:
        return None
