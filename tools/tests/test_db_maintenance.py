"""The database file is kept from growing and fragmenting: new files are incremental, old ones are switched, free space is given back in slices."""
from __future__ import annotations

import os
import shutil
import sqlite3

import pytest
from fastapi.testclient import TestClient

from telegram_archive.app import create_app
from telegram_archive.core import db_maintenance as dm
from telegram_archive.core.auth import ensure_web_credentials
from telegram_archive.core.database import Database

NOW = "2026-11-26T00:00:00"
H = {"Origin": "https://testserver"}


def _fill(db, n=6000, blob=2000, channel="-1005"):
    with db.lock:
        db.conn.executemany(
            "INSERT INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) VALUES(?,?,'image/jpeg',?,'playback-snapshot',?)",
            [(channel, i, os.urandom(blob), NOW) for i in range(1, n + 1)])
        db.conn.commit()


def _delete_most(db):
    with db.lock:
        db.conn.execute("DELETE FROM thumbnail_cache WHERE video_message_id % 4 <> 0")
        db.conn.commit()
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _legacy(db):
    """Make the file what an existing installation has: auto_vacuum off."""
    with db.lock:
        db.conn.commit()
        db.conn.execute("PRAGMA auto_vacuum=NONE")
        db.conn.execute("VACUUM")
    assert dm.status(db)["auto_vacuum"] == 0


def _size(db):
    return os.path.getsize(db.path)


def test_a_new_database_is_made_incremental(tmp_path):
    db = Database(tmp_path / "t.db")
    assert dm.status(db)["incremental"] is True


def test_deleted_data_leaves_the_file_big_until_space_is_given_back_in_slices(tmp_path):
    db = Database(tmp_path / "t.db")
    _fill(db)
    big = _size(db)
    _delete_most(db)
    assert _size(db) == big                                    # deleting alone returns nothing to the disk
    assert dm.status(db)["free_bytes"] > 5_000_000
    result = dm.give_back_space(db, budget_seconds=20)
    with db.lock:
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert result["released_bytes"] > 5_000_000 and _size(db) < big * 0.6
    assert dm.status(db)["free_bytes"] < 1_000_000
    assert db.conn.execute("SELECT COUNT(*) FROM thumbnail_cache").fetchone()[0] == 1500     # nothing else was touched


def test_nothing_is_done_when_the_file_is_not_incremental_or_there_is_little_to_give_back(tmp_path):
    db = Database(tmp_path / "t.db")
    _fill(db, 200)
    assert dm.give_back_space(db)["released_bytes"] == 0       # nothing free
    _legacy(db)
    _fill(db, 6000, channel="-1006")
    _delete_most(db)
    assert dm.status(db)["free_bytes"] > 5_000_000
    assert dm.give_back_space(db)["released_bytes"] == 0       # free pages, but the file cannot give them back until it is switched


def test_compact_switches_an_old_file_and_keeps_every_row(tmp_path):
    db = Database(tmp_path / "t.db")
    _legacy(db)                      # an existing installation's file: auto_vacuum off, then used and emptied like a real library
    _fill(db)
    _delete_most(db)
    assert dm.status(db)["incremental"] is False and dm.status(db)["free_bytes"] > 5_000_000
    before = _size(db)
    rows = db.conn.execute("SELECT COUNT(*) FROM thumbnail_cache").fetchone()[0]
    result = dm.compact(db)
    assert result["ok"] and result["incremental"] is True
    assert result["after_bytes"] < before and result["saved_bytes"] == before - result["after_bytes"] > 0
    assert db.conn.execute("SELECT COUNT(*) FROM thumbnail_cache").fetchone()[0] == rows
    assert db.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert dm.status(db)["incremental"] is True and dm.status(db)["last_compact"]


def test_compact_is_refused_without_room_or_while_a_job_runs(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    _fill(db, 500)
    monkeypatch.setattr(dm.shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(10**12, 10**12 - 1000, 1000))
    with pytest.raises(dm.MaintenanceRefused, match="free disk space"):
        dm.compact(db)
    monkeypatch.undo()
    monkeypatch.setattr(db, "active_jobs", lambda: [{"status": "RUNNING", "job_type": "X"}])
    with pytest.raises(dm.MaintenanceRefused, match="job is running"):
        dm.compact(db)
    assert dm.run_once(db) == {"skipped": True}                # the quiet pass also waits for the jobs


def test_the_start_switches_only_a_small_database(tmp_path, monkeypatch):
    db = Database(tmp_path / "t.db")
    _fill(db, 300)
    _legacy(db)
    monkeypatch.setattr(dm, "AUTO_CONVERT_BELOW_BYTES", 1000)   # "large": left for the Settings button
    assert dm.convert_small_database(db) is False and dm.status(db)["incremental"] is False
    monkeypatch.setattr(dm, "AUTO_CONVERT_BELOW_BYTES", 10**9)
    assert dm.convert_small_database(db) is True and dm.status(db)["incremental"] is True
    assert dm.convert_small_database(db) is False               # once


def test_statistics_are_refreshed_once_a_week_not_every_pass(tmp_path):
    db = Database(tmp_path / "t.db")
    t0 = 1_800_000_000.0
    assert dm.run_once(db, now=t0).get("optimized") is True
    assert dm.run_once(db, now=t0 + 3600).get("optimized") is None
    assert dm.run_once(db, now=t0 + dm.OPTIMIZE_EVERY_SECONDS + 1).get("optimized") is True
    assert dm.status(db)["last_optimize"]


def _client(tmp_path):
    db = Database(tmp_path / "api.db")
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    pw = "correct-horse-battery"
    assert c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H).status_code in (200, 201, 204)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return db, c


def test_settings_shows_the_state_and_compacts_on_request(tmp_path):
    db, c = _client(tmp_path)
    _fill(db)
    _delete_most(db)
    state = c.get("/api/settings/database").json()
    assert state["ok"] and state["free_bytes"] > 5_000_000 and {"file_bytes", "incremental", "can_compact", "last_compact"} <= set(state)
    done = c.post("/api/settings/database/compact", json={}, headers=H)
    assert done.status_code == 200 and done.json()["saved_bytes"] > 0
    assert c.get("/api/settings/database").json()["free_bytes"] < 1_000_000


def test_settings_refuses_to_compact_while_a_job_runs(tmp_path, monkeypatch):
    db, c = _client(tmp_path)
    monkeypatch.setattr(db, "active_jobs", lambda: [{"status": "RUNNING", "job_type": "X"}])
    refused = c.post("/api/settings/database/compact", json={}, headers=H)
    assert refused.status_code == 409 and "job is running" in refused.json()["detail"]


def test_a_second_connection_can_read_while_space_is_given_back(tmp_path):
    db = Database(tmp_path / "t.db")
    _fill(db)
    _delete_most(db)
    reader = sqlite3.connect(f"file:{db.path}?mode=ro", uri=True)
    assert dm.give_back_space(db, budget_seconds=20)["released_bytes"] > 0
    assert reader.execute("SELECT COUNT(*) FROM thumbnail_cache").fetchone()[0] == 1500
    reader.close()
