#!/usr/bin/env python3
"""Write latest.json, the file the desktop app's updater reads (Tauri updater format).

    make_latest_json.py --version 30.1.0 --repo Abithdas/Archive-X --tag desktop-v30.1.0 \
        --installer Archive-X_30.1.0_x64-setup.exe --signature Archive-X_30.1.0_x64-setup.exe.sig --out latest.json

The installer must already be uploaded under exactly that file name (GitHub turns spaces in asset names into dots, so the workflow
renames it first). The signature is the text of the .sig file made while building with the signing key.
"""
import argparse
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path

VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def build(version: str, repo: str, tag: str, installer: str, signature: str, notes: str = "", now: datetime.datetime | None = None) -> dict:
    if not VERSION.match(version):
        raise ValueError(f"not a version like 30.1.0: {version!r}")
    signature = signature.strip()
    if not signature:
        raise ValueError("the signature is empty")
    name = Path(installer).name
    if name != installer or " " in name:
        raise ValueError(f"installer must be a plain file name without spaces (GitHub renames them): {installer!r}")
    when = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(datetime.timezone.utc)
    return {
        "version": version,
        "notes": notes,
        "pub_date": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": {
            "windows-x86_64": {
                "signature": signature,
                "url": f"https://github.com/{repo}/releases/download/{urllib.parse.quote(tag)}/{urllib.parse.quote(name)}",
            }
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--repo", required=True, help="owner/name that holds the release")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--installer", required=True, help="file name of the uploaded installer")
    ap.add_argument("--signature", required=True, help="path of the .sig file")
    ap.add_argument("--notes", default="")
    ap.add_argument("--out", default="latest.json")
    a = ap.parse_args()
    try:
        data = build(a.version, a.repo, a.tag, a.installer, Path(a.signature).read_text(encoding="utf-8"), a.notes)
    except (ValueError, OSError) as e:
        print(f"make_latest_json: {e}", file=sys.stderr)
        return 1
    Path(a.out).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {a.out}: {data['version']} -> {data['platforms']['windows-x86_64']['url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
