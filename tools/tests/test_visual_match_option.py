"""The pHash (visual) scan of Metadata & Assets is optional: `visual_match`, on by default, saved in the job and honoured by every step that runs it."""
import json

import pytest
from fastapi.testclient import TestClient

import metadata_scan_harness as h
from test_metadata_scan_matching import Telegram, ctx_for, go, stored, wire
from telegram_archive.engines import metadata_channel_pipeline as pipe
from telegram_archive.engines import metadata_sync
from telegram_archive.metadata import pipeline as meta_pipeline

HDR = {"Origin": "https://testserver"}


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
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=HDR)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=HDR).status_code == 200
    yield c, db
    JobManager.submit = real


@pytest.fixture
def keyed():
    db = h.fresh_db()
    old = {k: db.setting(k) for k in ("stashdb_api_key", "tpdb_api_token", "tpdb_token")}
    db.set_setting("stashdb_api_key", "test-key")
    yield db
    for k, v in old.items():
        db.set_setting(k, v if v is not None else "")


def saved(client, r):
    return json.loads(client[1].get_job(r.json()["id"])["detail"])


# ---------------------------------------------------------------- the routes save the choice ----------------------------------------------------
def test_a_scan_started_without_the_option_keeps_the_visual_match_on(client):
    r = client[0].post("/api/metadata-assets/process-channel", json={"channel_ids": [h.new_channel()], "stages": ["inventory"]}, headers=HDR)
    assert r.status_code == 200, r.text
    assert saved(client, r)["visual_match"] is True


@pytest.mark.parametrize("sent,expected", [(False, False), (True, True), (0, False), (1, True)])
def test_the_channel_scan_saves_what_was_chosen(client, sent, expected):
    r = client[0].post("/api/metadata-assets/process-channel", json={"channel_ids": [h.new_channel()], "stages": ["inventory"], "visual_match": sent}, headers=HDR)
    assert saved(client, r)["visual_match"] is expected


def test_the_pending_scan_saves_it_too(client):
    r = client[0].post("/api/metadata-assets/process-pending", json={"channel_ids": [h.new_channel()], "stages": ["snapshots"], "visual_match": False}, headers=HDR)
    assert r.status_code == 200 and saved(client, r)["visual_match"] is False


def test_the_metadata_phase_hands_the_choice_to_the_matching_step():
    for chosen in (True, False):
        _, detail = pipe._phase_detail("metadata", "-1001", [{"media_id": 1}], {"visual_match": chosen})
        assert detail["visual_match"] is chosen
    assert pipe._phase_detail("metadata", "-1001", [{"media_id": 1}], {})[1]["visual_match"] is True      # an older job without the key: as before


# ---------------------------------------------------------------- the matching step honours it ---------------------------------------------------
class Spy:
    """A fake identify_video that records whether the visual scan was asked for."""

    def __init__(self):
        self.calls = []

    async def __call__(self, db, *, channel_id, message_id, filename, oshash=None, force=False, visual_scan=False, **kw):
        self.calls.append({"id": message_id, "visual_scan": visual_scan})
        return {"action": "applied"}


@pytest.mark.parametrize("extra,expected", [({}, True), ({"visual_match": True}, True), ({"visual_match": False}, False)])
def test_the_metadata_match_runs_the_visual_scan_only_when_asked(keyed, monkeypatch, extra, expected):
    ch = stored(keyed, 3)
    spy = Spy()
    wire(monkeypatch, spy)
    go(ctx_for(keyed, ch, Telegram(), extra=extra))
    assert spy.calls and all(c["visual_scan"] is expected for c in spy.calls)


# ---------------------------------------------------------------- the pass after the snapshots honours it ---------------------------------------------
def _with_frames(db, channel, ids):
    """Give each video its 36 stored frames, so the visual pass after the snapshots has something to work on."""
    from telegram_archive.engines.preview_prefetch import preview_frames_path
    for message in ids:
        folder = preview_frames_path(int(channel), int(message))
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(1, 37):
            (folder / f"frame_{i:02d}.jpg").write_bytes(b"\xff\xd8\xff\xd9")


@pytest.mark.parametrize("extra,expected_calls", [({}, True), ({"visual_match": True}, True), ({"visual_match": False}, False)])
def test_the_visual_fallback_pass_after_the_snapshots_is_optional(monkeypatch, extra, expected_calls):
    db = h.fresh_db()
    channel = h.new_channel()
    with db.lock:
        for i in range(1, 4):
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status,oshash) VALUES(?,?,?,?,'SCANNED','0123456789abcdef')", (channel, i, f"v{i}.mp4", f"v{i}"))
        db.conn.commit()
    _with_frames(db, channel, (1, 2, 3))
    spy = Spy()
    monkeypatch.setattr(meta_pipeline, "identify_video", spy)
    h.job(db, monkeypatch, stages=["inventory", "metadata", "snapshots"], channels=[channel], engines=h.Engines(db), detail=extra)
    visual_calls = [c for c in spy.calls if c["visual_scan"]]
    assert bool(visual_calls) is expected_calls, spy.calls


def test_the_metadata_module_still_exposes_its_entry_point():
    assert callable(metadata_sync.run)
