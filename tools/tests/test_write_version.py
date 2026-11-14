import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "release"))
import write_version


def _tree(tmp_path):
    (tmp_path / "telegram_archive").mkdir()
    (tmp_path / "desktop" / "src-tauri").mkdir(parents=True)
    (tmp_path / "desktop" / "src-tauri" / "tauri.conf.json").write_text(json.dumps({"version": "0.0.0", "productName": "x"}))
    return tmp_path


def test_a_release_number_goes_into_the_backend_and_the_desktop_shell(tmp_path):
    root = _tree(tmp_path)
    write_version.write("30.2.3", "0123456789abcdef", root)
    ns: dict = {}
    exec((root / "telegram_archive" / "_version.py").read_text(), ns)
    assert (ns["VERSION"], ns["COMMIT"]) == ("30.2.3", "0123456789ab")
    assert json.loads((root / "desktop" / "src-tauri" / "tauri.conf.json").read_text()) == {"version": "30.2.3", "productName": "x"}


def test_a_plain_build_says_dev_and_leaves_the_shell_alone(tmp_path):
    root = _tree(tmp_path)
    write_version.write("dev", "abc", root)
    assert json.loads((root / "desktop" / "src-tauri" / "tauri.conf.json").read_text())["version"] == "0.0.0"


def test_nonsense_is_refused(tmp_path):
    root = _tree(tmp_path)
    for bad in ("", "v1", "30.2", "1.0.0; rm -rf /"):
        try:
            write_version.write(bad, "", root)
        except SystemExit:
            continue
        raise AssertionError(bad)


def test_the_backend_reads_its_version_from_that_one_file():
    out = subprocess.run([sys.executable, "-c", "import telegram_archive.config as c; print(c.APP_VERSION, bool(c.APP_COMMIT is not None))"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.split()[0] == "dev"
