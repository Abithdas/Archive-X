"""Identifying a video from its own snapshots: a studio from its watermark, a performer from the faces, both only as suggestions."""
import json

import cv2
import numpy as np
import pytest

import metadata_scan_harness as h
from telegram_archive.core.database import Database
from telegram_archive.engines import preview_prefetch
from telegram_archive.engines import snapshot_identity as si
from telegram_archive.engines.entity_identity import upsert_performer, upsert_studio

W, H = 640, 360


def private_db(tmp_path):
    """Every test its own database: studios and videos of other tests would otherwise count as look-alikes."""
    return Database(tmp_path / "archive.db")


@pytest.fixture(autouse=True)
def _frames_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(preview_prefetch, "preview_frames_path", lambda ch, mid: tmp_path / "frames" / str(ch) / f"{mid}.frames")
    yield


def picture(rng, text="", corner="br", alpha=0.5, scale=1.0):
    base = cv2.GaussianBlur(rng.integers(0, 255, (H // 8, W // 8), dtype=np.uint8), (3, 3), 0)
    img = cv2.resize(base, (W, H), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    if text:
        layer = np.zeros((H, W), np.float32)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
        x = W - tw - 20 if corner[1] == "r" else 20
        y = H - 20 if corner[0] == "b" else th + 20
        cv2.putText(layer, text, (x, y), cv2.FONT_HERSHEY_DUPLEX, scale, 255, 2)
        img = img * (1 - alpha * (layer > 0)) + alpha * layer
    return np.clip(img, 0, 255).astype(np.uint8)


def make_video(db, tmp_path, seed, text="", n=36, **kw):
    """A media row whose 36 snapshots are on disk: different moving pictures, the same overlay."""
    channel, mid = h.new_channel(), seed + 1
    with db.lock:
        cur = db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,status,created_at,updated_at) VALUES (?,?,?,'ACTIVE','t','t')",
                              (str(channel), mid, f"v{seed}.mp4"))
        vid = int(cur.lastrowid)
        db.conn.commit()
    folder = preview_prefetch.preview_frames_path(channel, mid)
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    for i in range(n):
        cv2.imwrite(str(folder / f"frame_{i + 1:02d}.jpg"), picture(rng, text, **kw))
    return vid


def studio(db, name):
    sid = upsert_studio(db, name, provider="stashdb", provider_id=name)
    db.conn.commit()
    return sid


def performer(db, name):
    pid = upsert_performer(db, name, provider="stashdb", provider_id=name)
    db.conn.commit()
    return pid


def link_studio(db, vid, sid):
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id) VALUES (?,?)", (vid, sid))
        db.conn.commit()


def vec(seed, base=None, noise=0.0):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=128).astype(np.float32) if base is None else base + rng.normal(size=128).astype(np.float32) * noise
    return (v / np.linalg.norm(v)).astype(np.float32)


# ---------------------------------------------------------------- watermark ---------------------------------------------------------------
def test_a_static_overlay_is_found_and_plain_footage_has_none(tmp_path):
    db = private_db(tmp_path)
    marked = si.marks_of(db, make_video(db, tmp_path, 1, "BRAZZ"))
    plain = si.marks_of(db, make_video(db, tmp_path, 2, ""))
    assert "br" in marked and not plain


def test_too_few_snapshots_give_no_mark(tmp_path):
    db = private_db(tmp_path)
    assert not si.marks_of(db, make_video(db, tmp_path, 3, "BRAZZ", n=5))


def test_studio_is_suggested_from_its_learned_watermark(tmp_path):
    db = private_db(tmp_path)
    a, b = studio(db, "Studio A"), studio(db, "Studio B")
    for i in range(4):
        link_studio(db, make_video(db, tmp_path, 10 + i, "BRAZZ"), a)
    for i in range(4):
        link_studio(db, make_video(db, tmp_path, 20 + i, "NUBILE FILMS", scale=0.7), b)
    unknown = make_video(db, tmp_path, 30, "BRAZZ")
    plain = make_video(db, tmp_path, 31, "")
    out = si.run(db, extract=None, use_faces=False)
    assert out["studios_learned"] == 2
    sug = si.suggestions(db, unknown)
    assert [(s["kind"], s["id"]) for s in sug] == [("studio", a)] and sug[0]["name"] == "Studio A"
    assert si.suggestions(db, plain) == []


def test_a_mark_two_studios_share_is_never_guessed(tmp_path):
    db = private_db(tmp_path)
    a, b = studio(db, "Twin A"), studio(db, "Twin B")
    for i in range(4):
        link_studio(db, make_video(db, tmp_path, 40 + i, "SAME"), a)
        link_studio(db, make_video(db, tmp_path, 50 + i, "SAME"), b)
    unknown = make_video(db, tmp_path, 60, "SAME")
    si.run(db, extract=None, use_faces=False)
    assert si.suggestions(db, unknown) == []


def test_a_studio_whose_videos_disagree_gets_no_mark(tmp_path):
    db = private_db(tmp_path)
    a = studio(db, "Mixed")
    for i, text in enumerate(("ONE", "TWO TWO", "X", "LONGER NAME HERE")):
        link_studio(db, make_video(db, tmp_path, 70 + i, text, scale=0.5 + i * 0.4), a)
    assert si.run(db, extract=None, use_faces=False)["studios_learned"] == 0


def test_a_video_that_already_has_a_studio_is_not_second_guessed(tmp_path):
    db = private_db(tmp_path)
    a = studio(db, "Known A")
    for i in range(4):
        link_studio(db, make_video(db, tmp_path, 80 + i, "BRAZZ"), a)
    other = studio(db, "Known B")
    named = make_video(db, tmp_path, 90, "BRAZZ")
    link_studio(db, named, other)
    si.run(db, extract=None, use_faces=False)
    assert si.suggestions(db, named) == []


# ---------------------------------------------------------------- faces -------------------------------------------------------------------
class Faces:
    """A face reader that returns the embeddings it was told to for each frame of a video (by frame file name)."""

    def __init__(self, plan):
        self.plan, self.calls = plan, 0

    def __call__(self, path):
        self.calls += 1
        return [v.tolist() for v in self.plan(path)]


def add_reference(db, pid, v):
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO performer_face_embeddings(performer_id,image_key,embedding_json,updated_at) VALUES (?,?,?,'t')",
                        (pid, "k", json.dumps(v.tolist())))
        db.conn.commit()


def test_a_performer_seen_in_several_frames_is_suggested(tmp_path):
    db = private_db(tmp_path)
    p, other = performer(db, "Face Match"), performer(db, "Someone Else")
    ref = vec(1)
    add_reference(db, p, ref)
    add_reference(db, other, vec(2))
    vid = make_video(db, tmp_path, 100)
    extract = Faces(lambda path: [vec(int(path[-6:-4]), ref, 0.03)] if int(path[-6:-4]) % 3 == 0 else [])
    out = si.run(db, extract=extract)
    sug = si.suggestions(db, vid)
    assert [(s["kind"], s["id"]) for s in sug] == [("performer", p)] and sug[0]["evidence"]["frames"] == 12
    assert out["performer_suggestions"] >= 1


def test_one_lucky_frame_or_a_stranger_suggests_nothing(tmp_path):
    db = private_db(tmp_path)
    p = performer(db, "Not Here")
    add_reference(db, p, vec(5))
    few = make_video(db, tmp_path, 110)
    stranger = make_video(db, tmp_path, 111)
    plan_few = lambda path: [vec(7, vec(5), 0.03)] if path.endswith("frame_04.jpg") else []
    si.identify_video(db, few, extract=Faces(plan_few))
    si.identify_video(db, stranger, extract=Faces(lambda path: [vec(900 + int(path[-6:-4]))]))
    assert si.suggestions(db, few) == [] and si.suggestions(db, stranger) == []


def test_two_look_alike_performers_are_not_guessed(tmp_path):
    db = private_db(tmp_path)
    a, b = performer(db, "Twin One"), performer(db, "Twin Two")
    base = vec(8)
    add_reference(db, a, base)
    add_reference(db, b, vec(9, base, 0.01))
    vid = make_video(db, tmp_path, 120)
    si.identify_video(db, vid, extract=Faces(lambda path: [base]))
    assert si.suggestions(db, vid) == []


def test_a_video_with_performers_is_left_alone_and_faces_are_not_even_read(tmp_path):
    db = private_db(tmp_path)
    p = performer(db, "Already Linked")
    add_reference(db, p, vec(11))
    vid = make_video(db, tmp_path, 130)
    with db.lock:
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES (?,?)", (vid, p))
        db.conn.commit()
    extract = Faces(lambda path: [vec(11)])
    si.identify_video(db, vid, extract=extract)
    assert extract.calls == 0 and si.suggestions(db, vid) == []


def test_without_face_models_the_run_still_does_the_studios(tmp_path, monkeypatch):
    db = private_db(tmp_path)
    monkeypatch.setattr(si, "make_extractor", lambda: (_ for _ in ()).throw(RuntimeError("no models")))
    a = studio(db, "Offline A")
    for i in range(4):
        link_studio(db, make_video(db, tmp_path, 140 + i, "BRAZZ"), a)
    unknown = make_video(db, tmp_path, 150, "BRAZZ")
    out = si.run(db)
    assert out["faces"] is False and [s["id"] for s in si.suggestions(db, unknown)] == [a]


# ---------------------------------------------------------------- learning and memory -----------------------------------------------------
def test_rejected_stays_rejected_and_confirmed_faces_become_references(tmp_path):
    db = private_db(tmp_path)
    p = performer(db, "Learner")
    ref = vec(21)
    add_reference(db, p, ref)
    vid = make_video(db, tmp_path, 160)
    extract = Faces(lambda path: [vec(int(path[-6:-4]), ref, 0.03)])
    si.identify_video(db, vid, extract=extract)
    assert len(si.suggestions(db, vid)) == 1
    si.reject(db, vid, "performer", p)
    si.identify_video(db, vid, extract=extract)
    assert si.suggestions(db, vid) == []

    vid2 = make_video(db, tmp_path, 161)
    si.identify_video(db, vid2, extract=extract)
    assert len(si.suggestions(db, vid2)) == 1
    assert si.settle(db, vid2, [p], None)["face_references_added"] == si.FACE_KEEP
    ids, matrix = si._references(db)
    assert ids.count(p) == 1 + si.FACE_KEEP and matrix.shape[0] == len(ids)
    assert si.suggestions(db, vid2) == []


def test_settling_marks_the_ones_not_taken_as_rejected(tmp_path):
    db = private_db(tmp_path)
    a, b = studio(db, "Settle A"), studio(db, "Settle B")
    for i in range(4):
        link_studio(db, make_video(db, tmp_path, 170 + i, "BRAZZ"), a)
        link_studio(db, make_video(db, tmp_path, 180 + i, "NUBILE FILMS", scale=0.7), b)
    unknown = make_video(db, tmp_path, 190, "BRAZZ")
    si.run(db, extract=None, use_faces=False)
    si.settle(db, unknown, [], b)           # the person chose the other studio
    with db.lock:
        status = db.conn.execute("SELECT status FROM video_snapshot_suggestions WHERE video_id=? AND kind='studio' AND entity_id=?", (unknown, a)).fetchone()[0]
    assert status == "REJECTED"


def test_a_video_is_not_read_again_until_something_new_is_known(tmp_path):
    db = private_db(tmp_path)
    p = performer(db, "Once")
    add_reference(db, p, vec(31))
    vid = make_video(db, tmp_path, 200)
    extract = Faces(lambda path: [])
    si.run(db, extract=extract)
    first = extract.calls
    assert first == 36
    si.run(db, extract=extract)
    assert extract.calls == first                      # nothing changed: not read again
    add_reference(db, performer(db, "New Face"), vec(32))
    si.run(db, extract=extract)
    assert extract.calls == first + 36                 # a new reference exists: worth another look


def test_the_limit_bounds_how_many_videos_one_run_looks_at(tmp_path):
    db = private_db(tmp_path)
    add_reference(db, performer(db, "Bounded"), vec(41))
    for i in range(5):
        make_video(db, tmp_path, 210 + i)
    out = si.run(db, limit=2, extract=Faces(lambda path: []))
    assert out["videos"] == 2
