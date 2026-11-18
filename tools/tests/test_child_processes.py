"""The helper-process registry the Jobs page lists (FFmpeg, PyAV worker)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axcp_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def test_tracked_process_is_listed_while_it_runs_and_gone_after():
    from telegram_archive.core import child_processes as cp
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        with cp.tracked(proc, "FFmpeg · CONVERT", kind="ffmpeg", job_id="j1") as h:
            h.set_progress(0.4)
            rows = cp.snapshot()
            assert len(rows) == 1
            assert rows[0]["label"] == "FFmpeg · CONVERT" and rows[0]["alive"] is True
            assert rows[0]["progress"] == 40.0 and rows[0]["pid"] == proc.pid and rows[0]["job_id"] == "j1"
        assert cp.snapshot() == []
    finally:
        proc.kill()
        proc.wait()
