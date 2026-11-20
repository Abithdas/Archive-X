"""A native crash must leave a Python stack trace in native-crash.log (a user's log just stopped mid-decode, with nothing to say why)."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_a_native_crash_leaves_a_traceback(tmp_path):
    code = (
        "import sys, faulthandler; sys.path.insert(0, sys.argv[1]);"
        "from pathlib import Path;"
        "from telegram_archive.core import crash_log;"
        "assert crash_log.install(Path(sys.argv[2])) is not None;"
        "faulthandler._sigsegv()"
    )
    done = subprocess.run([sys.executable, "-c", code, str(ROOT), str(tmp_path)], capture_output=True, text=True, timeout=60)
    assert done.returncode != 0                      # the process really died
    text = (tmp_path / "native-crash.log").read_text(encoding="utf-8")
    assert "=== started" in text and "Fatal Python error" in text and "most recent call first" in text.lower()
