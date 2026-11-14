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
