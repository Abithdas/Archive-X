"""Python must read the backups the Rust crate makes.

    AX_BACKUP_INTEROP_OUT=/tmp/out cargo test -p ax-backup --test interop backups_made
    python tools/parity/check_rust_backup.py /tmp/out

`inspect_backup` and `stage_restore` of the Python original are run on each file; the files the staged restore unpacks must match the manifest.
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
EXPECTED = json.loads((ROOT / "crates" / "ax-backup" / "tests" / "fixtures" / "expected.json").read_text(encoding="utf-8"))


def main(folder: str) -> int:
    base = Path(tempfile.mkdtemp(prefix="axbk-check-"))
    data = base / "data"
    data.mkdir()
    os.environ["ARCHIVE_X_DATA_DIR"] = str(data)
    os.environ["ARCHIVE_X_ENCRYPTION_KEY"] = EXPECTED["secret"]
    from telegram_archive.core.backup import manager as bm

    class Db:
        def setting(self, key, default=None):
            return default

        def set_setting(self, key, value):
            pass

    files = sorted(Path(folder).glob("*.axbackup"))
    if len(files) < 3:
        print("expected the three backups made by the Rust test in", folder)
        return 1
    failed = 0
    for f in files:
        password = EXPECTED["password"] if "encrypted" in f.name else None
        try:
            info = bm.inspect_backup(f, password)
            staged = bm.stage_restore(Db(), str(f), password)
            stage = Path(staged["staging_path"])
            for item in info["manifest"]["files"]:
                got = hashlib.sha256((stage / item["path"]).read_bytes()).hexdigest()
                assert got == item["sha256"], item["path"]
            assert info["encrypted"] == bool(password)
            print("ok", f.name, info["manifest"]["backup_type"], len(info["manifest"]["files"]), "files")
        except Exception as exc:
            failed += 1
            print("FAILED", f.name, type(exc).__name__, exc)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
