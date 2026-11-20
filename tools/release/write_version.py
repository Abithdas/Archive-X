"""Write the version of a build into telegram_archive/_version.py (and, for a release, into the desktop shell's tauri.conf.json).

usage: python tools/release/write_version.py <version or "dev"> [commit]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def write(version: str, commit: str = "", root: Path = ROOT) -> None:
    if not re.fullmatch(r"dev|\d+\.\d+\.\d+([-+.][0-9A-Za-z.\-]+)?", version):
        raise SystemExit(f"not a version number: {version!r}")
    (root / "telegram_archive" / "_version.py").write_text(
        '"""The version of this build, written by tools/release/write_version.py."""\n'
        f"VERSION = {version!r}\nCOMMIT = {commit[:12]!r}\n", encoding="utf-8")
    conf = root / "apps" / "desktop" / "src-tauri" / "tauri.conf.json"
    if version != "dev" and conf.is_file():
        data = json.loads(conf.read_text(encoding="utf-8"))
        data["version"] = version
        conf.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    write(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
