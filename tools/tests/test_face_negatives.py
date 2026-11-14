"""Taking a face out of a named person's group is remembered as 'not this person' (the Rust face learning reads it)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import photo_face_clustering as pfc


def _setup(tmp_path):
    db = Database(tmp_path / "t.db")
    now = "2026-01-01T00:00:00Z"
    with db.lock:
        db.conn.execute("INSERT INTO performers(name) VALUES('Ava'),('Mia')")
        ava, mia = [r["id"] for r in db.conn.execute("SELECT id FROM performers ORDER BY id").fetchall()]
        db.conn.execute("INSERT INTO photo_face_groups(label,performer_id,created_at,updated_at) VALUES('g1',?,?,?)", (ava, now, now))
        gid = db.conn.execute("SELECT id FROM photo_face_groups").fetchone()["id"]
        for i in range(1, 4):
            db.conn.execute("INSERT INTO photo_face_detections(photo_id,face_index,group_id,created_at,updated_at) VALUES(?,?,?,?,?)", (i, 0, gid, now, now))
        db.conn.commit()
        ids = [r["id"] for r in db.conn.execute("SELECT id FROM photo_face_detections ORDER BY id").fetchall()]
    return db, ava, mia, ids


def _negatives(db):
    with db.lock:
        return sorted((r["detection_id"], r["performer_id"]) for r in db.conn.execute("SELECT detection_id,performer_id FROM photo_face_negatives").fetchall())


def test_removed_faces_become_not_this_person_examples(tmp_path):
    db, ava, mia, ids = _setup(tmp_path)
    pfc._move_detections(db, [ids[0]], None)
    assert _negatives(db) == [(ids[0], ava)]


def test_moving_to_another_person_also_counts_but_moving_to_the_same_person_does_not(tmp_path):
    db, ava, mia, ids = _setup(tmp_path)
    pfc._move_detections(db, [ids[1]], mia)
    assert _negatives(db) == [(ids[1], ava)]
    pfc._move_detections(db, [ids[2]], ava)          # out of Ava's group and straight back to Ava: no complaint about Ava
    assert (ids[2], ava) not in _negatives(db)


def test_a_face_given_back_to_a_person_stops_being_a_negative(tmp_path):
    db, ava, mia, ids = _setup(tmp_path)
    pfc._move_detections(db, [ids[0]], None)
    assert _negatives(db) == [(ids[0], ava)]
    pfc._move_detections(db, [ids[0]], ava)
    assert _negatives(db) == []


def test_different_person_and_single_split_are_remembered_too(tmp_path):
    db, ava, mia, ids = _setup(tmp_path)
    pfc._split_face_detection(db, ids[0])
    assert _negatives(db) == [(ids[0], ava)]
