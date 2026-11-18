"""The performer gallery routes, and the People suggestions that now draw on the gallery."""
import json

import pytest
from fastapi.testclient import TestClient

import metadata_scan_harness as h
from telegram_archive import config
from telegram_archive.engines import performer_gallery as pg
import test_performer_gallery as base

H = {"Origin": "https://testserver"}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    old = config.CACHE_DIR
    config.CACHE_DIR = tmp_path_factory.mktemp("cache")
    db = h.fresh_db(); db.set_setting("content_mode", "nsfw")   # the performers here are adult; the app opens in SFW
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver")
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    yield c, db
    config.CACHE_DIR = old


def stocked(db, n=2):
    pid = base.performer(db)
    files = {f"https://x.test/{pid}-{i}.jpg": base.jpeg((i * 60 % 255, 90, 160), noise=i + pid) for i in range(1, n + 1)}
    base.sync(db, pid, base.Net(files), list(files))
    return pid, files


def test_the_gallery_lists_the_kept_pictures(client):
    c, db = client
    pid, files = stocked(db)
    r = c.get(f"/api/performers/{pid}/gallery")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and len(body["items"]) == 2 and body["syncing"] is False and body["removed"] == 0 and body["last_result"] == "ok"
    assert body["items"][0]["url"].startswith(f"/api/performers/{pid}/gallery/")


def test_an_empty_gallery_and_an_unknown_performer(client):
    c, db = client
    assert c.get(f"/api/performers/{base.performer(db)}/gallery").json()["items"] == []
    assert c.get("/api/performers/999999999/gallery").status_code == 404


def test_a_picture_is_served_as_an_image_and_only_through_its_own_performer(client):
    c, db = client
    pid, files = stocked(db, 1)
    other = base.performer(db)
    item = c.get(f"/api/performers/{pid}/gallery").json()["items"][0]
    r = c.get(item["url"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/") and r.content == list(files.values())[0]
    assert c.get(f"/api/performers/{other}/gallery/{item['id']}/image").status_code == 404
    assert c.get(f"/api/performers/{pid}/gallery/99999999/image").status_code == 404


def test_removing_a_picture_takes_it_out_for_good(client):
    c, db = client
    pid, files = stocked(db, 2)
    item = c.get(f"/api/performers/{pid}/gallery").json()["items"][0]
    assert c.delete(f"/api/performers/{pid}/gallery/{item['id']}", headers=H).json() == {"ok": True}
    after = c.get(f"/api/performers/{pid}/gallery").json()
    assert len(after["items"]) == 1 and after["removed"] == 1
    assert c.get(item["url"]).status_code == 404
    assert c.delete(f"/api/performers/{pid}/gallery/{item['id']}", headers=H).status_code == 404


def test_refresh_starts_one_background_download(client, monkeypatch):
    c, db = client
    pid = base.performer(db)
    started = []
    monkeypatch.setattr(pg, "start_background_sync", lambda db_, performer_id: (started.append(performer_id), len(started) == 1)[1])
    first = c.post(f"/api/performers/{pid}/gallery/refresh", headers=H).json()
    second = c.post(f"/api/performers/{pid}/gallery/refresh", headers=H).json()
    assert first == {"ok": True, "started": True, "syncing": True} and second["started"] is False and started == [pid, pid]


def test_refresh_refuses_the_unknown_performer_and_a_missing_one(client):
    c, db = client
    from telegram_archive.core import resource_registry as rr
    rr.ensure_unknown_entity_links(db, [])
    db.conn.commit()
    unknown = db.conn.execute("SELECT id FROM performers WHERE name='Unknown Performer'").fetchone()
    if unknown:
        assert c.post(f"/api/performers/{unknown[0]}/gallery/refresh", headers=H).status_code == 400
    assert c.post("/api/performers/999999999/gallery/refresh", headers=H).status_code == 404


def test_the_gallery_needs_a_login():
    from telegram_archive.app import create_app
    anon = TestClient(create_app(db=h.fresh_db()), base_url="https://testserver")
    assert anon.get("/api/performers/1/gallery").status_code in (401, 403)
    assert anon.delete("/api/performers/1/gallery/1", headers=H).status_code in (401, 403)


def test_the_people_suggestions_use_the_gallery_pictures(client):
    """An unnamed group is suggested for a performer whose profile picture is unlike it but whose gallery holds the same face."""
    c, db = client
    base.clean_face_tables(db)
    ava = base.performer(db, "Ava Gallery Match")
    target = base.unit(321)
    base.vec_row(db, ava, 1)
    gid = base.make_group(db, range(3), base=target, noise=0.02)
    # a photo that belongs to the group's channel so the groups list returns it
    with db.lock:   # the test photos' channel is a library channel (adult), so the NSFW mode shows them
        db.conn.execute("INSERT OR IGNORE INTO channels(telegram_id,title,updated_at) SELECT DISTINCT channel_id,'Test channel','2026-01-01' FROM photo_library_items")
        db.conn.commit()
    before = c.get("/api/photos/faces/groups").json()
    listed = {g.get("id"): g for g in before.get("groups", [])}
    assert gid in listed and not listed[gid]["profile_image_suggestions"]
    base.sync(db, ava, base.Net({"https://x.test/ava.jpg": base.jpeg(noise=3)}), ["https://x.test/ava.jpg"])
    pg.index_faces(db, lambda path: [base.face(0, 0, 90, 90, target)])
    after = {g.get("id"): g for g in c.get("/api/photos/faces/groups").json().get("groups", [])}
    sug = after[gid]["profile_image_suggestions"]
    assert sug and sug[0]["id"] == ava and sug[0]["source"] == "profile_image" and "gallery" in sug[0]["reason"]
