"""Make the backups the Rust tests read: real `.axbackup` files written by Python's `create_backup`.

    python tools/parity/gen_backup_fixtures.py        # rewrite crates/ax-backup/tests/fixtures/

The salt, nonce and timestamps differ on every run, so the files are committed, not re-checked; run this only when the format changes.
The data folder has a small SQLite database, a settings file, a vault-sealed credentials file (secret `fixture-secret-for-backup-interop-tests-0123456789`), a SQLite WAL
sidecar and a cache folder (the last two must be left out of the backup). Four backups are made: full, configuration only, encrypted
(password `correct horse`), and a plain one with a media flag set by hand to check that the restore refuses it.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates" / "ax-backup" / "tests" / "fixtures"
SECRET = "fixture-secret-for-backup-interop-tests-0123456789"
PASSWORD = "correct horse"


def main():
    base = Path(tempfile.mkdtemp(prefix="axbk-"))
    data = base / "data"
    (data / "database").mkdir(parents=True)
    os.environ["ARCHIVE_X_DATA_DIR"] = str(data)
    os.environ["ARCHIVE_X_ENCRYPTION_KEY"] = SECRET
    from telegram_archive import config
    from telegram_archive.core import encrypted_files
    from telegram_archive.core.backup import manager as bm

    conn = sqlite3.connect(data / "database" / "archive.db")
    conn.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY, body TEXT)")
    conn.executemany("INSERT INTO notes(body) VALUES (?)", [("alpha",), ("beta é 日本",)])
    conn.commit()
    conn.close()
    (data / "database" / "archive.db-wal").write_bytes(b"stale wal frames")
    (data / "settings.json").write_text('{"theme": "dark"}\n')
    (data / "PERSISTENT_DATA_MANIFEST.json").write_text('{"manifest_version": 1}\n')
    (data / "state").mkdir(exist_ok=True)
    (data / "state" / "last.json").write_text('{"a": 1}')
    (data / "assets").mkdir(exist_ok=True)
    (data / "assets" / "pic.bin").write_bytes(bytes(range(256)) * 40)
    (data / "logs").mkdir(exist_ok=True)
    (data / "logs" / "app.log").write_text("line one\nline two\n")
    (data / "auth").mkdir(exist_ok=True)
    encrypted_files.write_encrypted(data / "auth" / "credentials.json", b'{"api_id": 1}', "archive-x-credentials-v1")
    (data / "cache").mkdir(exist_ok=True)
    (data / "cache" / "big.bin").write_bytes(b"x" * 1000)
    (data / "assets" / "cache").mkdir(exist_ok=True)
    (data / "assets" / "cache" / "skipped.bin").write_bytes(b"y")

    class Db:
        def setting(self, key, default=None):
            return default

        def set_setting(self, key, value):
            pass

    repo = base / "repo"
    made = {}
    for name, kind, password in (("full", "full_application", None), ("config", "configuration", None), ("encrypted", "full_application", PASSWORD)):
        res = bm.create_backup(Db(), backup_type=kind, password=password, repository_path=str(repo))
        made[name] = {"file": Path(res["path"]).name, "password": password, "type": kind, "files": {f["path"]: f["sha256"] for f in res["manifest"]["files"]}}
        # keep the names distinct when two are made within one second
        os.utime(res["path"], None)
        import time
        time.sleep(1.1)
    # a plain backup flagged as holding media: the staged restore must refuse it
    src = repo / made["full"]["file"]
    media = repo / "Archive_X_Backup_media.axbackup"
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(media, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            payload = zin.read(item.filename)
            if item.filename == "BACKUP_MANIFEST.json":
                m = json.loads(payload)
                m["media_included"] = True
                payload = (json.dumps(m, indent=2) + "\n").encode()
            zout.writestr(item, payload)
    made["media"] = {"file": media.name, "password": None, "type": "full_application", "files": made["full"]["files"]}
    # a truncated file and a plain file that is not a backup
    (repo / "Archive_X_Backup_broken.axbackup").write_bytes(src.read_bytes()[:200])
    (repo / "Archive_X_Backup_text.axbackup").write_text("not a backup")
    # what Python says about each file (and about the encrypted one with a wrong or no password)
    def say(path, password):
        try:
            info = bm.inspect_backup(path, password)
            return {"ok": True, "encrypted": info["encrypted"], "type": info["manifest"].get("backup_type"), "files": len(info["manifest"]["files"])}
        except Exception as exc:
            return {"error": str(exc)}
    inspected = {f.name: say(f, PASSWORD if "encrypted" in str(f) or f.name == made["encrypted"]["file"] else None) for f in sorted(repo.glob("*.axbackup"))}
    extra = {
        "encrypted without a password": say(repo / made["encrypted"]["file"], None),
        "encrypted with a wrong password": say(repo / made["encrypted"]["file"], "wrong"),
    }
    # list_backups (the repository setting is the folder)
    class ListDb(Db):
        def setting(self, key, default=None):
            return str(repo) if key == "backup_repository" else default
    listed = {r["name"]: {k: r.get(k) for k in ("type", "encrypted", "verified", "files", "error")} for r in bm.list_backups(ListDb())}
    try:
        bm.stage_restore(Db(), str(media))
        refused = None
    except Exception as exc:
        refused = str(exc)
    # retention on a UTC calendar: which files of a spread of times Python keeps
    import time as _time
    os.environ["TZ"] = "UTC"
    _time.tzset()
    day = 86400
    t0 = 1798761600  # 2027-01-01 00:00 UTC
    spreads = {
        "daily run": [t0 + i * day for i in range(40)],
        "across the iso year": [t0 - 10 * day + i * day for i in range(25)],
        "sparse": [t0 + i * 9 * day + (i % 3) * 3600 for i in range(15)],
        "same moment": [t0] * 5,
        "hourly": [t0 + i * 3600 for i in range(60)],
    }
    retention = []
    for name, times in spreads.items():
        for daily, weekly, monthly in ((7, 4, 3), (0, 0, 0), (1, 0, 0), (0, 3, 0), (0, 0, 2), (3, 2, 1), (100, 0, 0)):
            rdir = Path(tempfile.mkdtemp(prefix="axbk-ret-"))
            for i, t in enumerate(times):
                f = rdir / f"b{i:03d}.axbackup"
                f.write_bytes(b"x")
                os.utime(f, (t, t))
            settings = {"backup_repository": str(rdir), "backup_retention_daily": daily, "backup_retention_weekly": weekly, "backup_retention_monthly": monthly}

            class RDb:
                def setting(self, key, default=None):
                    return settings.get(key, default)
            result = bm.apply_retention(RDb())
            kept = sorted(int(p.stem[1:]) for p in rdir.glob("*.axbackup"))
            retention.append({"case": name, "times": times, "daily": daily, "weekly": weekly, "monthly": monthly, "kept": kept, "result": result})
            shutil.rmtree(rdir, ignore_errors=True)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    for f in repo.glob("*.axbackup"):
        shutil.copy(f, OUT / f.name)
    (OUT / "expected.json").write_text(json.dumps({"secret": SECRET, "password": PASSWORD, "backups": made, "inspected": inspected, "extra": extra, "listed": listed, "media_refused": refused, "retention": retention}, indent=1, ensure_ascii=False) + "\n")
    shutil.rmtree(base, ignore_errors=True)
    print("wrote", OUT.relative_to(ROOT), sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
