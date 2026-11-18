"""The Metadata & Assets start routes: what they accept, what they refuse and what job they save."""
import json

import pytest

import metadata_scan_harness as h
from fastapi.testclient import TestClient

H = {"Origin": "https://testserver"}


@pytest.fixture(scope="module")
def client():
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    from telegram_archive.jobs.manager import JobManager
    db = h.fresh_db()
    ensure_web_credentials(db)
    real = JobManager.submit
    JobManager.submit = lambda self, jid, *a, **k: None          # queue the job, do not run it
    c = TestClient(create_app(db=db), base_url="https://testserver")
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    yield c, db
    JobManager.submit = real


def start(client, body):
    c, _ = client
    return c.post("/api/metadata-assets/process-channel", json=body, headers=H)


def saved(client, r):
    return json.loads(client[1].get_job(r.json()["id"])["detail"])


def stored_channel(db, monkeypatch=None, n=3):
    ch = h.new_channel()
    with db.lock:
        for i in range(1, n + 1):
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,'SCANNED')", (ch, i, f"v{i}.mp4", f"v{i}"))
        db.conn.commit()
    return ch


def test_a_valid_request_saves_a_job_with_clean_settings(client):
    ch = h.new_channel()
    r = start(client, {"channel_ids": [ch, ch, " "], "stages": ["Inventory", "metadata_match", "inventory"]})
    assert r.status_code == 200, r.text
    d = saved(client, r)
    assert d["channel_ids"] == [ch] and d["stages"] == ["inventory", "metadata"] and d["limit"] == 50 and d["unbounded"] is False


def test_the_request_needs_a_channel_and_a_stage(client):
    assert start(client, {"stages": ["inventory"]}).status_code == 400
    assert start(client, {"channel_ids": [], "stages": ["inventory"]}).status_code == 400
    assert start(client, {"channel_ids": [h.new_channel()]}).status_code == 400
    assert start(client, {"channel_ids": [h.new_channel()], "stages": ["bogus", ""]}).status_code == 400


def test_a_channel_id_given_as_one_string_is_one_channel_not_its_digits(client):
    ch = h.new_channel()
    r = start(client, {"channel_ids": ch, "stages": ["inventory"]})
    assert r.status_code == 200 and saved(client, r)["channel_ids"] == [ch]


def test_a_channel_id_that_is_not_a_number_is_refused(client):
    r = start(client, {"channel_ids": ["not-a-channel"], "stages": ["inventory"]})
    assert r.status_code == 400 and "number" in r.text.lower()


def test_garbage_in_the_optional_settings_does_not_crash_the_route(client):
    for extra in ({"auto_added_prerequisites": ["x"]}, {"auto_added_prerequisites": "x"}, {"auto_added_prerequisites": {"inventory": "x"}},
                  {"limit": "abc"}, {"limit": None}, {"limit": -4}, {"limit": 10**12}, {"stages": "metadata"}):
        body = {"channel_ids": [h.new_channel()], "stages": ["inventory"], **extra}
        r = start(client, body)
        assert r.status_code in (200, 400), (extra, r.status_code, r.text[:200])


@pytest.mark.parametrize("raw,expected", [("abc", 50), (None, 50), (-4, 50), (0, 50), (10**12, 5000), ("7", 7)])
def test_limit_values_reach_the_job_in_range(client, raw, expected):
    body = {"channel_ids": [h.new_channel()], "stages": ["inventory"]}
    if raw is not None:
        body["limit"] = raw
    r = start(client, body)
    assert r.status_code == 200 and saved(client, r)["limit"] == expected


def test_stages_that_need_an_earlier_stage_are_refused_with_the_missing_ones_named(client):
    ch = h.new_channel()
    r = start(client, {"channel_ids": [ch], "stages": ["performers"]})
    assert r.status_code == 409
    d = r.json()["detail"]
    assert d["code"] == "METADATA_ASSETS_PREREQUISITES_REQUIRED" and d["required_stages"] == ["inventory", "metadata"]
    assert start(client, {"channel_ids": [ch], "stages": ["inventory", "metadata", "performers"]}).status_code == 200


def test_snapshots_and_metadata_only_need_stored_videos(client):
    _, db = client
    stored = stored_channel(db)
    assert start(client, {"channel_ids": [stored], "stages": ["snapshots"]}).status_code == 200
    stored2 = stored_channel(db)
    assert start(client, {"channel_ids": [stored2], "stages": ["metadata"]}).status_code == 200
    empty = h.new_channel()
    assert start(client, {"channel_ids": [empty], "stages": ["snapshots"]}).status_code == 409


def test_performers_alone_work_once_every_video_has_metadata(client):
    _, db = client
    ch = stored_channel(db)
    assert start(client, {"channel_ids": [ch], "stages": ["performers"]}).status_code == 409           # videos stored, no metadata yet
    with db.lock:
        db.conn.execute("UPDATE media SET metadata_synced_at=datetime('now') WHERE source_channel=?", (ch,))
        db.conn.commit()
    assert start(client, {"channel_ids": [ch], "stages": ["performers"]}).status_code == 200


def test_one_video_without_metadata_is_enough_to_ask_for_the_metadata_stage_first(client):
    _, db = client
    ch = stored_channel(db, n=4)
    with db.lock:
        db.conn.execute("UPDATE media SET metadata_synced_at=datetime('now') WHERE source_channel=? AND source_message_id<>3", (ch,))
        db.conn.commit()
    r = start(client, {"channel_ids": [ch], "stages": ["channel_data"]})
    assert r.status_code == 409 and r.json()["detail"]["required_stages"] == ["metadata"]


def test_a_second_request_for_a_channel_already_being_scanned_joins_the_first(client):
    ch = h.new_channel()
    first = start(client, {"channel_ids": [ch], "stages": ["inventory"]})
    second = start(client, {"channel_ids": [ch], "stages": ["inventory", "snapshots"]})
    assert first.status_code == 200 and second.json().get("already_running") is True and second.json()["id"] == first.json()["id"]


def test_the_full_history_option_always_includes_the_inventory(client):
    r = start(client, {"channel_ids": [h.new_channel()], "stages": ["metadata"], "full_inventory": True})
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        assert saved(client, r)["stages"][0] == "inventory" and saved(client, r)["full_inventory"] is True


def test_the_preflight_names_what_is_missing(client):
    c, _ = client
    ch = h.new_channel()
    r = c.post("/api/metadata-assets/preflight", json={"channel_ids": [ch], "stages": ["performers"]}, headers=H)
    assert r.status_code == 200 and r.json()["ready"] is False and r.json()["required_stages"] == ["inventory", "metadata"]


def test_the_pending_route_needs_numeric_channels_and_reports_nothing_to_do(client):
    c, db = client
    assert c.post("/api/metadata-assets/process-pending", json={"channel_ids": ["abc", ""]}, headers=H).status_code == 400
    ch = h.new_channel()
    r = c.post("/api/metadata-assets/process-pending", json={"channel_ids": [ch]}, headers=H)
    assert r.status_code == 200 and not r.json().get("id")


def test_the_routes_need_a_login(client):
    from telegram_archive.app import create_app
    anon = TestClient(create_app(db=client[1]), base_url="https://testserver")
    assert anon.post("/api/metadata-assets/process-channel", json={"channel_ids": ["-1"], "stages": ["inventory"]}, headers=H).status_code in (401, 403)
