"""Each content mode lists only its own tasks (Operations, Transfers, the activity indicators); the other mode's tasks are only counted."""
import json

import pytest

from telegram_archive.core import job_mode
from telegram_archive.core.database import Database
from test_screen_home import H, _client

SFW, NSFW, UNKNOWN = "-1009000000001", "-1009000000002", "-1009000000099"


@pytest.fixture
def world(tmp_path):
    db = Database(tmp_path / "j.db")
    with db.lock:
        for cid, title, rating in ((SFW, "Films", "sfw"), (NSFW, "Adult", "nsfw")):
            db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES(?,?,'source',1,?,'video','2026-01-01')", (cid, title, rating))
        db.conn.commit()
    return db, _client(db)


def job(db, kind, source="", dest="", detail=None, status="RUNNING"):
    jid = db.create_job(kind, source, dest, False, detail=json.dumps(detail or {}))
    with db.lock:
        db.conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, jid))
        db.conn.commit()
    return jid


def listed(c, url="/api/status"):
    return {j["id"] for j in c.get(url).json()["jobs"]}


def mode(c, which):
    assert c.post("/api/content-mode", json={"mode": which}, headers=H).json()["mode"] == which


def test_a_job_belongs_to_the_mode_of_its_channels(world):
    db, _ = world
    jobs = [
        {"id": "a", "source_channel": SFW, "destination_channel": "", "detail": "{}"},
        {"id": "b", "source_channel": NSFW, "destination_channel": "", "detail": "{}"},
        {"id": "c", "source_channel": "", "destination_channel": "", "detail": json.dumps({"source_channels": [int(SFW)], "destination_channel": SFW})},
        {"id": "d", "source_channel": f"{SFW},{NSFW}", "destination_channel": "", "detail": "{}"},       # one adult channel makes it an adult job
        {"id": "e", "source_channel": "", "destination_channel": "", "detail": "{}"},                      # names no channel: both
        {"id": "f", "source_channel": UNKNOWN, "destination_channel": "", "detail": "{}"},                 # unknown counts as NSFW, like everywhere else
        {"id": "g", "source_channel": "all indexed photos", "destination_channel": "", "detail": "not json"},
        {"id": "h", "source_channel": SFW, "destination_channel": "", "detail": json.dumps({"destination_channel": NSFW})},    # SFW source, NSFW destination
    ]
    assert job_mode.modes_of(db, jobs) == {"a": "sfw", "b": "nsfw", "c": "sfw", "d": "nsfw", "e": "", "f": "nsfw", "g": "", "h": "nsfw"}


def test_status_lists_only_the_open_modes_tasks_and_counts_the_other(world):
    db, c = world
    mine = job(db, "TELEGRAM_TRANSFER", SFW)
    theirs = job(db, "TELEGRAM_TRANSFER", NSFW, detail={"destination_channel": NSFW})
    queued = job(db, "TELEGRAM_TRANSFER", NSFW, status="QUEUED")
    shared = job(db, "CONTENT_AUTOMATION")
    mode(c, "sfw")
    body = c.get("/api/status").json()
    assert {j["id"] for j in body["jobs"]} == {mine, shared}
    assert body["other_mode_jobs"] == {"count": 2, "running": 1}
    assert body["current_job"]["id"] in {mine, shared}                    # never the other mode's task
    assert theirs not in json.dumps(body["watchdogs"]) and theirs not in json.dumps(body["jobs"])
    mode(c, "nsfw")
    body = c.get("/api/status").json()
    assert {j["id"] for j in body["jobs"]} == {theirs, queued, shared}
    assert body["other_mode_jobs"] == {"count": 1, "running": 1}


def test_the_transfer_lanes_and_the_operations_diagnostics_follow_the_mode_too(world):
    db, c = world
    mine = job(db, "TELEGRAM_TRANSFER", SFW)
    theirs = job(db, "MEDIA_MANAGER", NSFW)
    mode(c, "sfw")
    lanes = c.get("/api/telegram-transfer/lanes").json()
    assert {j["id"] for j in lanes["jobs"]} == {mine} and lanes["other_mode_jobs"] == {"count": 1, "running": 1}
    assert {j["id"] for j in c.get("/api/diagnostics/summary").json()["active_jobs"]} == {mine}
    mode(c, "nsfw")                                                       # the answer kept for a few seconds is not the other mode's
    lanes = c.get("/api/telegram-transfer/lanes").json()
    assert {j["id"] for j in lanes["jobs"]} == {theirs} and lanes["other_mode_jobs"] == {"count": 1, "running": 1}
    assert {j["id"] for j in c.get("/api/diagnostics/summary").json()["active_jobs"]} == {theirs}


def test_with_nothing_running_in_the_other_mode_the_count_is_zero(world):
    db, c = world
    job(db, "TELEGRAM_TRANSFER", SFW)
    mode(c, "sfw")
    assert c.get("/api/status").json()["other_mode_jobs"] == {"count": 0, "running": 0}
    assert c.get("/api/telegram-transfer/lanes").json()["other_mode_jobs"] == {"count": 0, "running": 0}
