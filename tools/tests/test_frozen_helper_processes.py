"""The installed backend is one frozen program with no separate Python. When the app starts a helper from it, "-c" and "-m" are
ignored: the helper started the whole app, which stopped at the one-instance lock ("Archive X is already running ... Close the
running Archive X first"). So PyAV looked missing on every install and every native media job fell back to FFmpeg. In a frozen
build the helpers are started with their own flags, and packaging/backend/entry.py answers them."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axfrozen_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, str(root))

ENTRY = root / "packaging" / "backend" / "entry.py"


def _run(*args):
    env = {**os.environ, "PYTHONPATH": str(root)}
    return subprocess.run([sys.executable, str(ENTRY), *args], capture_output=True, text=True, timeout=60, env=env)


def test_a_frozen_build_starts_the_worker_with_its_own_flag(monkeypatch):
    from telegram_archive.core import internal_media
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = internal_media._worker_cmd("probe", {"path": "a.mp4"})
    assert cmd[0] == sys.executable and cmd[1] == "--ax-native-worker" and cmd[2] == "probe" and "-m" not in cmd
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert internal_media._worker_cmd("probe", {})[1:3] == ["-m", "telegram_archive.core.native_media_worker"]


def test_a_frozen_build_checks_pyav_with_its_own_flag(monkeypatch):
    from telegram_archive.core import pyav_support
    seen = []

    class Done:
        returncode = 0
        stdout = json.dumps({"ok": True, "version": "9.9", "architecture": "64-bit"})
        stderr = ""

    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda cmd, **kw: (seen.append(cmd), Done())[1])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pyav_support._diagnose_uncached()["ok"] is True
    assert seen[-1] == [sys.executable, "--ax-pyav-diagnostic"]
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    pyav_support._diagnose_uncached()
    assert seen[-1][1] == "-c"


def test_the_entry_point_answers_the_pyav_flag_without_starting_the_app():
    r = _run("--ax-pyav-diagnostic")
    assert "already running" not in (r.stdout + r.stderr)
    line = next(x for x in r.stdout.splitlines() if x.startswith("{"))
    assert json.loads(line)["ok"] is True and r.returncode == 0


def test_the_entry_point_runs_a_worker_job_and_a_clean_finish_is_quiet(tmp_path):
    clip = tmp_path / "t.mp4"
    made = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=5", "-y", str(clip)],
                          capture_output=True)
    if made.returncode != 0:
        import pytest
        pytest.skip("no ffmpeg to make a test clip")
    r = _run("--ax-native-worker", "probe", json.dumps({"path": str(clip)}))
    kinds = [json.loads(x)["type"] for x in r.stdout.splitlines() if x.startswith("{")]
    assert kinds == ["result"] and r.returncode == 0 and "SystemExit" not in r.stderr
    assert "already running" not in (r.stdout + r.stderr)
