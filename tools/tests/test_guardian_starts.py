"""The watchdog guardian is started by the app as a plain script (watchdogs.py: python .../watchdog_guardian.py --manifest ...).
A package-relative import at its top made it crash on every start (ImportError: attempted relative import with no known parent package)."""
import subprocess
import sys
from pathlib import Path

GUARDIAN = Path(__file__).resolve().parents[2] / "telegram_archive" / "core" / "watchdog_guardian.py"


def test_the_guardian_starts_as_a_script_the_way_the_app_launches_it():
    r = subprocess.run([sys.executable, str(GUARDIAN), "--help"], capture_output=True, text=True, timeout=30, cwd=str(GUARDIAN.parent.parent.parent))
    assert r.returncode == 0, r.stderr
    assert "ImportError" not in r.stderr and "usage:" in r.stdout.lower()
    assert "--manifest" in r.stdout and "--parent-pid" in r.stdout


def test_the_script_has_no_top_level_relative_import_outside_a_fallback():
    import ast
    tree = ast.parse(GUARDIAN.read_text(encoding="utf-8"))
    bare = [n.lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.level > 0]
    assert bare == [], f"relative import(s) at the top level of a file that runs as a script: lines {bare}"


def test_guardian_is_off_in_the_desktop_app_and_can_be_forced():
    import subprocess, sys, os
    root = str(Path(__file__).resolve().parents[2])
    def enabled(**env):
        e = {k: v for k, v in os.environ.items() if not k.startswith("ARCHIVE_X_")}
        e.update(env)
        out = subprocess.run([sys.executable, "-c", "import telegram_archive.config as c; print(c.WATCHDOG_GUARDIAN_ENABLED)"], cwd=root, env=e, capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr[-400:]
        return out.stdout.strip().splitlines()[-1] == "True"
    assert enabled() is True
    assert enabled(ARCHIVE_X_DESKTOP="1") is False
    assert enabled(ARCHIVE_X_DESKTOP="1", ARCHIVE_X_WATCHDOG_GUARDIAN="1") is True
    assert enabled(ARCHIVE_X_WATCHDOG_GUARDIAN="0") is False
