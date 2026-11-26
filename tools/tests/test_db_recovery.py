"""A damaged database file must not stop the app from starting for good: keep what can be read, start a new file, keep the damaged one."""
from __future__ import annotations

import sqlite3

from telegram_archive.core import db_recovery
from telegram_archive.core.database import Database

ROWS = 3000


def _filled(path):
    db = Database(path)
    with db.lock:
        db.conn.executemany(
            "INSERT INTO media(filename,source_channel,source_message_id,status,created_at,updated_at) VALUES(?,?,?,?,datetime('now'),datetime('now'))",
            [(f"video {i} " + "x" * 200 + ".mp4", "-1001", i, "ACTIVE") for i in range(1, ROWS + 1)])
        db.conn.commit()
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.conn.close()


def _damage(path, page_from_end=6):
    size = path.stat().st_size
    page = 4096
    with open(path, "r+b") as f:
        f.seek(size - page * page_from_end)
        f.write(b"\xff" * page)


def test_the_error_that_stopped_the_app_is_recognised():
    assert db_recovery.is_corruption(sqlite3.DatabaseError("database disk image is malformed"))
    assert db_recovery.is_corruption(sqlite3.DatabaseError("file is not a database"))
    assert not db_recovery.is_corruption(sqlite3.OperationalError("database is locked"))
    assert not db_recovery.is_corruption(ValueError("malformed"))


def test_rows_on_the_readable_pages_come_back_and_the_damaged_file_is_kept(tmp_path):
    path = tmp_path / "archive.db"
    _filled(path)
    _damage(path)
    moved = db_recovery.quarantine(path)
    assert not path.exists() and moved.exists() and moved.parent.name.startswith("damaged-")
    fresh = Database(path)
    result = db_recovery.salvage(moved, fresh)
    copied = fresh.conn.execute("SELECT count(*) FROM media").fetchone()[0]
    assert copied == result["copied"]["media"]
    assert 0.5 * ROWS < copied <= ROWS, copied          # most rows survive; only the damaged page's rows are lost
    assert fresh.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    fresh.conn.close()


def test_a_file_that_is_not_a_database_gives_a_working_empty_one(tmp_path):
    path = tmp_path / "archive.db"
    path.write_bytes(b"this is not a database" * 400)
    moved = db_recovery.quarantine(path)
    fresh = Database(path)
    result = db_recovery.salvage(moved, fresh)
    assert result["rows_copied"] == 0 and result["tables_unreadable"]
    assert fresh.conn.execute("SELECT count(*) FROM media").fetchone()[0] == 0
    fresh.conn.close()


def test_a_database_that_fails_to_open_does_not_stay_locked(tmp_path):
    path = tmp_path / "archive.db"
    path.write_bytes(b"this is not a database" * 400)
    try:
        Database(path)
    except sqlite3.DatabaseError as exc:
        assert db_recovery.is_corruption(exc)
    else:
        raise AssertionError("opening a file that is not a database should fail")
    path.unlink()          # Windows refuses to remove a file that is still open
