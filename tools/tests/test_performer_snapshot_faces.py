"""Gallery pictures taken from the snapshots of a performer's own videos, and using a gallery picture as the profile picture."""
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

import test_snapshot_identity as base
from telegram_archive import config
from telegram_archive.engines import performer_gallery as pg
from telegram_archive.engines import performer_snapshot_faces as psf
from telegram_archive.engines import snapshot_identity as si

H = {"Origin": "https://testserver"}


@pytest.fixture(autouse=True)
def _folders(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(base.preview_prefetch, "preview_frames_path", lambda ch, mid: tmp_path / "frames" / str(ch) / f"{mid}.frames")
    yield


def face(v, w=120, conf=0.95):
    return {"bbox": [200.0, 80.0, float(w), float(w) * 1.2], "embedding": v.tolist(), "confidence": conf}


class Reader:
    def __init__(self, plan):
        self.plan, self.calls = plan, 0

    def __call__(self, path):
        self.calls += 1
        return self.plan(path)


def frame_no(path):
    return int(path[-6:-4])


def map_video(db, vid, *pids):
    with db.lock:
        for p in pids:
            db.conn.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES (?,?)", (vid, p))
        db.conn.commit()


def reference(db, pid, v):
    base.add_reference(db, pid, v)


def snaps(db, pid):
    with db.lock:
        return db.conn.execute("SELECT * FROM performer_gallery WHERE performer_id=? AND source='snapshot' AND status='ACTIVE'", (pid,)).fetchall()


def test_faces_matching_the_performers_references_become_gallery_pictures(tmp_path):
    db = base.private_db(tmp_path)
    p, other = base.performer(db, "Gallery From Video"), base.performer(db, "Another")
    ref = base.vec(1)
    reference(db, p, ref)
    reference(db, other, base.vec(2))
    vid = base.make_video(db, tmp_path, 1)
    map_video(db, vid, p)
    out = psf.grow_gallery(db, p, reader=Reader(lambda path: [face(base.vec(frame_no(path), ref, 0.05))]))
    rows = snaps(db, p)
    assert out["added"] == len(rows) == psf.PER_VIDEO
    for r in rows:
        assert (pg.gallery_dir() / r["file_path"]).is_file() and r["faces"] == 1 and json.loads(r["embedding_json"]) and r["width"] > 0
    shown = pg.list_pictures(db, p)
    assert {i["source"] for i in shown["items"]} == {"snapshot"} and shown["references"] == psf.PER_VIDEO
    ids, matrix = si._references(db)
    assert ids.count(p) == 1 + psf.PER_VIDEO and matrix.shape[0] == len(ids)        # they are now matching references


def test_strangers_small_and_unsure_faces_are_not_kept(tmp_path):
    db = base.private_db(tmp_path)
    p = base.performer(db, "Picky")
    ref = base.vec(5)
    reference(db, p, ref)
    reference(db, base.performer(db, "Someone"), base.vec(6))
    for seed, plan in ((10, lambda path: [face(base.vec(300 + frame_no(path)))]),
                       (11, lambda path: [face(base.vec(frame_no(path), ref, 0.05), w=30)]),
                       (12, lambda path: [face(base.vec(frame_no(path), ref, 0.05), conf=0.5)])):
        vid = base.make_video(db, tmp_path, seed)
        map_video(db, vid, p)
        psf.grow_gallery(db, p, reader=Reader(plan))
    assert snaps(db, p) == []


def test_a_look_alike_that_matches_another_performer_better_is_not_kept(tmp_path):
    db = base.private_db(tmp_path)
    a, b = base.performer(db, "Twin A"), base.performer(db, "Twin B")
    shared = base.vec(8)
    reference(db, a, shared)
    reference(db, b, base.vec(9, shared, 0.01))
    vid = base.make_video(db, tmp_path, 20)
    map_video(db, vid, a)
    psf.grow_gallery(db, a, reader=Reader(lambda path: [face(shared)]))
    assert snaps(db, a) == []


def test_without_references_only_a_single_performer_video_with_a_dominant_face_starts_them(tmp_path):
    db = base.private_db(tmp_path)
    solo, duo1, duo2 = base.performer(db, "Solo"), base.performer(db, "Duo One"), base.performer(db, "Duo Two")
    who = base.vec(30)
    plan = lambda path: [face(base.vec(frame_no(path), who, 0.05))] if frame_no(path) % 2 == 0 else [face(base.vec(500 + frame_no(path)))]
    v1 = base.make_video(db, tmp_path, 31)
    map_video(db, v1, solo)
    psf.grow_gallery(db, solo, reader=Reader(plan))
    assert len(snaps(db, solo)) >= 1
    v2 = base.make_video(db, tmp_path, 32)
    map_video(db, v2, duo1, duo2)
    psf.grow_gallery(db, duo1, reader=Reader(plan))
    assert snaps(db, duo1) == []


def test_a_face_in_only_a_few_snapshots_does_not_start_anyone(tmp_path):
    db = base.private_db(tmp_path)
    p = base.performer(db, "Fleeting")
    who = base.vec(40)
    vid = base.make_video(db, tmp_path, 41)
    map_video(db, vid, p)
    psf.grow_gallery(db, p, reader=Reader(lambda path: [face(base.vec(frame_no(path), who, 0.05))] if frame_no(path) <= 3 else []))
    assert snaps(db, p) == []


def test_a_removed_picture_stays_removed_and_videos_are_not_searched_twice(tmp_path):
    db = base.private_db(tmp_path)
    p = base.performer(db, "Removal")
    ref = base.vec(50)
    reference(db, p, ref)
    reference(db, base.performer(db, "Bystander"), base.vec(51))
    vid = base.make_video(db, tmp_path, 52)
    map_video(db, vid, p)
    reader = Reader(lambda path: [face(base.vec(frame_no(path), ref, 0.05))])
    psf.grow_gallery(db, p, reader=reader)
    first_calls = reader.calls
    assert first_calls == 36
    psf.grow_gallery(db, p, reader=reader)
    assert reader.calls == first_calls                                  # searched once
    for r in snaps(db, p):
        assert pg.remove_picture(db, p, int(r["id"]))
    with db.lock:
        db.conn.execute("DELETE FROM performer_video_faces_done")
        db.conn.commit()
    psf.grow_gallery(db, p, reader=reader)
    assert snaps(db, p) == []                                           # what was removed is not taken again


def test_a_video_searched_before_they_had_references_is_searched_again(tmp_path):
    db = base.private_db(tmp_path)
    p, q = base.performer(db, "Late Start"), base.performer(db, "Not On It")
    mapped = base.make_video(db, tmp_path, 60)
    map_video(db, mapped, p, q)                                         # two performers and no references: nothing can start
    who = base.vec(61)
    reader = Reader(lambda path: [face(base.vec(frame_no(path), who, 0.05))])
    psf.grow_gallery(db, p, reader=reader)
    assert snaps(db, p) == []
    reference(db, p, who)
    reference(db, q, base.vec(62))
    psf.grow_gallery(db, p, reader=reader)
    assert len(snaps(db, p)) >= 1


def test_the_cap_and_the_per_run_budget_hold(tmp_path, monkeypatch):
    db = base.private_db(tmp_path)
    monkeypatch.setattr(psf, "MAX_SNAPSHOT_PICTURES", 1)
    ref = base.vec(70)
    people = [base.performer(db, f"Budget {i}") for i in range(3)]
    reference(db, base.performer(db, "Budget Other"), base.vec(71))
    for i, p in enumerate(people):
        reference(db, p, ref if i == 0 else base.vec(80 + i))
    vid = base.make_video(db, tmp_path, 72)
    map_video(db, vid, people[0])
    psf.grow_gallery(db, people[0], reader=Reader(lambda path: [face(base.vec(frame_no(path), ref, 0.05))]))
    assert len(snaps(db, people[0])) == 1
    for i in (1, 2):
        map_video(db, base.make_video(db, tmp_path, 73 + i), people[i])
    out = psf.grow_all(db, reader=Reader(lambda path: []), max_videos=1)
    assert out["videos"] == 1


def test_snapshot_pictures_do_not_use_up_the_providers_quota(tmp_path):
    db = base.private_db(tmp_path)
    p = base.performer(db, "Quota")
    with db.lock:
        for i in range(3):
            db.conn.execute("INSERT INTO performer_gallery(performer_id,url_hash,url,provider,file_path,status,source,created_at,updated_at) VALUES (?,?,?,?,?,'ACTIVE','snapshot','t','t')", (p, f"s{i}", "", "snapshot", f"{p}/x{i}.jpg"))
        db.conn.commit()
    import test_performer_gallery as g
    out = g.sync(db, p, g.Net({"https://x.test/a.jpg": g.jpeg(noise=3)}), ["https://x.test/a.jpg"], max_pictures=1)
    assert out["added"] == 1


def test_use_as_profile_picture_stores_it_like_a_manual_upload(tmp_path):
    import test_performer_gallery as g
    db = base.private_db(tmp_path)
    p = base.performer(db, "Profile Pic")
    url = "https://x.test/p.jpg"
    data = g.jpeg(noise=9)
    g.sync(db, p, g.Net({url: data}), [url])
    pic = pg.list_pictures(db, p)["items"][0]["id"]
    assert pg.use_as_profile_picture(db, p, pic) is True
    with db.lock:
        row = db.conn.execute("SELECT image,image_source,image_locked FROM performers WHERE id=?", (p,)).fetchone()
        cached = db.conn.execute("SELECT mime,data FROM performer_image_cache WHERE performer_id=?", (p,)).fetchone()
    assert row["image"].startswith("manual-upload:") and row["image_source"] == "manual" and row["image_locked"] == 1
    assert cached["data"] == data and cached["mime"] == "image/jpeg"
    assert pg.use_as_profile_picture(db, p, 99999) is False


@pytest.fixture()
def client(tmp_path):
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    db = base.private_db(tmp_path)
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver")
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, db


def test_routes_use_as_profile_and_from_videos_without_models(client, monkeypatch):
    import test_performer_gallery as g
    c, db = client
    monkeypatch.setattr(si, "make_face_reader", lambda: (_ for _ in ()).throw(RuntimeError("no models")))
    p = base.performer(db, "Route Profile")
    url = "https://x.test/r.jpg"
    g.sync(db, p, g.Net({url: g.jpeg(noise=4)}), [url])
    pic = pg.list_pictures(db, p)["items"][0]["id"]
    assert c.post(f"/api/performers/{p}/gallery/{pic}/use-as-profile", headers=H).status_code == 200
    assert c.post(f"/api/performers/{p}/gallery/9999/use-as-profile", headers=H).status_code == 404
    assert c.post(f"/api/performers/999999/gallery/{pic}/use-as-profile", headers=H).status_code == 404
    out = c.post(f"/api/performers/{p}/gallery/from-videos", headers=H).json()
    assert out["ok"] and out["faces_checked"] is False and out["added"] == 0
