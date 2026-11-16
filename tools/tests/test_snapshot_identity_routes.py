"""The snapshot-suggestion routes: listing, a one-video run, "not them", and Quick edit confirming what the person accepted."""
import pytest
from fastapi.testclient import TestClient

import test_snapshot_identity as base
from telegram_archive.engines import snapshot_identity as si

H = {"Origin": "https://testserver"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    monkeypatch.setattr(base.preview_prefetch, "preview_frames_path", lambda ch, mid: tmp_path / "frames" / str(ch) / f"{mid}.frames")
    monkeypatch.setattr(si, "make_extractor", lambda: (_ for _ in ()).throw(RuntimeError("no models")))
    db = base.private_db(tmp_path)
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver")
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    yield c, db, tmp_path


def ident(db, vid):
    with db.lock:
        r = db.conn.execute("SELECT source_channel,source_message_id FROM media WHERE id=?", (vid,)).fetchone()
    return {"channel_id": r[0], "message_id": r[1]}


def test_listing_and_not_them(client):
    c, db, tmp = client
    vid = base.make_video(db, tmp, 1)
    p = base.performer(db, "Route Person")
    si._store(db, vid, "performer", p, 0.5, {"frames": 4, "faces": [[0.1] * 128]})
    q = ident(db, vid)
    got = c.get("/api/media/snapshot-suggestions", params=q, headers=H).json()["suggestions"]
    assert [(s["kind"], s["id"], s["name"]) for s in got] == [("performer", p, "Route Person")] and "faces" not in got[0]["evidence"]
    assert c.post("/api/media/snapshot-suggestions/reject", json={**q, "kind": "performer", "id": p}, headers=H).status_code == 200
    assert c.get("/api/media/snapshot-suggestions", params=q, headers=H).json()["suggestions"] == []


def test_unknown_video_and_bad_input(client):
    c, db, tmp = client
    assert c.get("/api/media/snapshot-suggestions", params={"channel_id": "-1", "message_id": 999}, headers=H).status_code == 404
    vid = base.make_video(db, tmp, 2)
    assert c.post("/api/media/snapshot-suggestions/reject", json={**ident(db, vid), "kind": "tag", "id": 1}, headers=H).status_code == 400


def test_running_one_video_finds_the_studio_without_the_face_models(client):
    c, db, tmp = client
    a = base.studio(db, "Run Studio")
    for i in range(4):
        base.link_studio(db, base.make_video(db, tmp, 10 + i, "BRAZZ"), a)
    vid = base.make_video(db, tmp, 20, "BRAZZ")
    out = c.post("/api/media/snapshot-suggestions/run", json=ident(db, vid), headers=H).json()
    assert out["faces_checked"] is False and out["frames"] == 36
    assert [(s["kind"], s["id"]) for s in out["suggestions"]] == [("studio", a)]


def test_quick_edit_confirms_what_was_taken_and_a_title_change_leaves_them_pending(client):
    c, db, tmp = client
    vid = base.make_video(db, tmp, 30)
    p = base.performer(db, "Take Me")
    q = ident(db, vid)
    si._store(db, vid, "performer", p, 0.6, {"frames": 5, "faces": [[0.2] * 128, [0.3] * 128]})
    assert c.post("/api/media/quick-edit", json={**q, "title": "Only a title"}, headers=H).status_code == 200
    assert len(c.get("/api/media/snapshot-suggestions", params=q, headers=H).json()["suggestions"]) == 1
    assert c.post("/api/media/quick-edit", json={**q, "performers": [{"id": p, "name": "Take Me"}]}, headers=H).status_code == 200
    assert c.get("/api/media/snapshot-suggestions", params=q, headers=H).json()["suggestions"] == []
    with db.lock:
        status = db.conn.execute("SELECT status FROM video_snapshot_suggestions WHERE video_id=?", (vid,)).fetchone()[0]
        refs = db.conn.execute("SELECT COUNT(*) FROM video_face_references WHERE performer_id=?", (p,)).fetchone()[0]
    assert status == "CONFIRMED" and refs == 2
