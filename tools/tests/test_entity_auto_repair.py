"""Genders and pictures are filled in by the app itself (after a metadata scan, and when the Performers or Studios page opens)."""
import asyncio
import contextlib
import json
import sqlite3
import threading
import time

from telegram_archive.engines import missing_images
from telegram_archive.metadata.gender_repair import restore_missing_genders


class _Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.logs = []
        self.conn.executescript(
            """
            CREATE TABLE performers (id INTEGER PRIMARY KEY, name TEXT, gender TEXT, image_locked INT DEFAULT 0);
            CREATE TABLE video_performers (performer_id INT, video_id INT);
            CREATE TABLE media (id INTEGER PRIMARY KEY, provider_payload TEXT);
            CREATE TABLE performer_image_cache (performer_id INT, data BLOB);
            CREATE TABLE entity_metadata (entity_type TEXT, entity_id INT, provider TEXT, payload TEXT);
            """
        )

    @contextlib.contextmanager
    def reading(self):
        yield self.conn

    def log(self, message, level="INFO", **kw):
        self.logs.append((level, message))


def test_gender_is_restored_from_the_stored_provider_record_when_the_videos_have_none():
    db = _Db()
    db.conn.execute("INSERT INTO performers(id,name,gender) VALUES (1,'Anna Test',''),(2,'Bea Test',NULL),(3,'Cy Test','male')")
    db.conn.execute("INSERT INTO entity_metadata VALUES ('performer',1,'stashdb',?)", (json.dumps({"gender": "FEMALE"}),))
    db.conn.execute("INSERT INTO entity_metadata VALUES ('performer',2,'stashdb',?)", (json.dumps({"gender": "TRANSGENDER_FEMALE"}),))
    r = restore_missing_genders(db)
    assert (r["checked"], r["restored"], r["still_blank"]) == (2, 2, 0)
    genders = dict(db.conn.execute("SELECT id,gender FROM performers").fetchall())
    assert genders == {1: "female", 2: "trans", 3: "male"}


def test_a_failed_fetch_is_retried_in_hours_and_a_missing_picture_in_a_week():
    db = _Db()
    for i in (1, 2, 3, 4):
        db.conn.execute("INSERT INTO performers(id,name) VALUES (?,?)", (i, f"P{i}"))
        db.conn.execute("INSERT INTO video_performers VALUES (?,1)", (i,))
    missing_images._ensure(db)
    now = time.time()
    for pid, age, outcome in [(1, 7 * 3600, "download_failed"), (2, 1 * 3600, "download_failed"), (3, 2 * 86400, "no_provider_artwork"), (4, 8 * 86400, "no_provider_artwork")]:
        db.conn.execute("INSERT INTO entity_image_attempts(kind,entity_id,last_attempt,found,outcome) VALUES('performer',?,?,0,?)", (pid, now - age, outcome))
    # 1: failed 7 h ago -> due again; 2: failed 1 h ago -> wait; 3: no picture 2 days ago -> wait; 4: 8 days ago -> due again
    assert missing_images.missing_ids(db, "performer", include_recent_failures=False) == [1, 4]
    assert missing_images.missing_ids(db, "performer") == [1, 2, 3, 4]   # the count still includes all of them


def test_the_old_attempts_table_gets_its_outcome_column():
    db = _Db()
    db.conn.execute("CREATE TABLE entity_image_attempts (kind TEXT NOT NULL, entity_id INTEGER NOT NULL, last_attempt REAL NOT NULL, found INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind, entity_id))")
    missing_images._ensure(db)
    assert "outcome" in {r[1] for r in db.conn.execute("PRAGMA table_info(entity_image_attempts)")}


def test_opening_the_page_repeatedly_runs_the_repair_once_per_ten_minutes(monkeypatch):
    db = _Db()
    calls = []

    async def fake_run_once(db_, **kw):
        calls.append(kw)
        return {"tried": 0}

    monkeypatch.setattr(missing_images, "run_once", fake_run_once)
    monkeypatch.setattr(missing_images, "_last_auto_repair", {})
    first = asyncio.run(missing_images.auto_repair(db, "studios", reason="page opened"))
    second = asyncio.run(missing_images.auto_repair(db, "studios", reason="page opened"))
    forced = asyncio.run(missing_images.auto_repair(db, "studios", reason="metadata scan", force=True))
    assert "pictures" in first and second == {"skipped": "cooldown"} and "pictures" in forced
    assert [c["kinds"] for c in calls] == [("studio",), ("studio",)]
    # performers also get their genders restored
    perf = asyncio.run(missing_images.auto_repair(db, "performers", reason="page opened"))
    assert perf["gender"] == {"checked": 0, "restored": 0, "still_blank": 0}
