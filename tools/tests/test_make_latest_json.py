"""desktop/scripts/make_latest_json.py writes what the Tauri updater expects."""
import datetime
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "desktop" / "scripts"))
from make_latest_json import build  # noqa: E402

NOW = datetime.datetime(2026, 11, 14, 6, 0, 0, tzinfo=datetime.timezone.utc)


def test_shape_matches_the_updater_format():
    d = build("30.1.0", "Abithdas/Archive-X", "desktop-v30.1.0", "Archive-X_30.1.0_x64-setup.exe", "  c2lnbmF0dXJl\n", "Faster lists", NOW)
    assert d["version"] == "30.1.0" and d["notes"] == "Faster lists" and d["pub_date"] == "2026-11-14T06:00:00Z"
    p = d["platforms"]["windows-x86_64"]
    assert p["signature"] == "c2lnbmF0dXJl"
    assert p["url"] == "https://github.com/Abithdas/Archive-X/releases/download/desktop-v30.1.0/Archive-X_30.1.0_x64-setup.exe"


@pytest.mark.parametrize("version", ["30", "v30.1.0", "30.1", "", "30.1.0 beta"])
def test_rejects_bad_versions(version):
    with pytest.raises(ValueError):
        build(version, "a/b", "t", "x.exe", "sig", now=NOW)


def test_rejects_empty_signature_and_spaced_or_pathed_names():
    with pytest.raises(ValueError):
        build("1.0.0", "a/b", "t", "x.exe", "  \n", now=NOW)
    with pytest.raises(ValueError):
        build("1.0.0", "a/b", "t", "Archive X_1.0.0_x64-setup.exe", "sig", now=NOW)
    with pytest.raises(ValueError):
        build("1.0.0", "a/b", "t", "dir/x.exe", "sig", now=NOW)


def test_command_line_writes_the_file(tmp_path):
    sig = tmp_path / "a.sig"
    sig.write_text("abc123\n")
    out = tmp_path / "latest.json"
    r = subprocess.run([sys.executable, str(ROOT / "desktop/scripts/make_latest_json.py"), "--version", "2.0.0", "--repo", "o/r", "--tag", "desktop-v2.0.0",
                        "--installer", "A_2.0.0_x64-setup.exe", "--signature", str(sig), "--out", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert json.loads(out.read_text())["platforms"]["windows-x86_64"]["signature"] == "abc123"
    bad = subprocess.run([sys.executable, str(ROOT / "desktop/scripts/make_latest_json.py"), "--version", "x", "--repo", "o/r", "--tag", "t",
                          "--installer", "A.exe", "--signature", str(sig), "--out", str(out)], capture_output=True, text=True)
    assert bad.returncode == 1 and "not a version" in bad.stderr
