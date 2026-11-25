import json, math, random, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import photo_face_clustering as pf


def unit(v):
    n = math.sqrt(sum(x * x for x in v)); return [x / n for x in v]


def vec(rng): return unit([rng.gauss(0, 1) for _ in range(128)])


def near(b, rng, eps=0.03): return unit([x + rng.gauss(0, eps) for x in b])


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    db.init_schema() if hasattr(db, "init_schema") else None
    return db


def test_a_clear_match_is_only_a_suggestion_and_nothing_is_named(tmp_path):
    rng = random.Random(5)
    db = make(tmp_path)
    now = "2025-01-01T00:00:00"
    a, b, other = vec(rng), vec(rng), vec(rng)
    with db.lock:
        for i, name in enumerate(["Ava", "Mia", "Cleo"], start=1):
            db.conn.execute("INSERT INTO performers(name) VALUES(?)", (name,))
        for pid, v in ((1, a), (2, b)):
            db.conn.execute("INSERT INTO performer_face_embeddings VALUES(?,?,?,?)", (pid, "k", json.dumps(near(v, rng)), now))
        photo = 0
        for gid, base in ((1, a), (2, other)):
            db.conn.execute("INSERT INTO photo_face_groups(id,label,created_at,updated_at) VALUES(?,?,?,?)", (gid, f"g{gid}", now, now))
            for _ in range(3):
                photo += 1
                db.conn.execute("INSERT INTO photo_face_analysis(photo_id,status,face_count,updated_at) VALUES(?,?,?,?)", (photo, "COMPLETE", 1, now))
                db.conn.execute("INSERT INTO photo_face_detections(photo_id,face_index,group_id,embedding_json,created_at,updated_at) VALUES(?,?,?,?,?,?)", (photo, 0, gid, json.dumps(near(base, rng)), now, now))
        db.conn.commit()
    out = pf._likely_people(db)
    rows = {r["id"]: r["performer_id"] for r in db.conn.execute("SELECT id,performer_id FROM photo_face_groups")}
    assert out["suggested"] == 1                 # Ava's face group is recognised as a likely match ...
    assert rows == {1: None, 2: None}            # ... but no group is named: only the user's confirmation does that
    assert db.conn.execute("SELECT COUNT(*) FROM photo_face_suggestion_feedback").fetchone()[0] == 0   # and nothing is recorded as confirmed


def test_move_detections_makes_one_group_and_can_name_it(tmp_path):
    rng = random.Random(9)
    db = make(tmp_path)
    now = "2025-01-01T00:00:00"
    with db.lock:
        db.conn.execute("INSERT INTO performers(name) VALUES('Ava')")
        db.conn.execute("INSERT INTO photo_face_groups(id,label,created_at,updated_at) VALUES(1,'g1',?,?)", (now, now))
        for i in range(1, 6):
            db.conn.execute("INSERT INTO photo_face_detections(photo_id,face_index,group_id,embedding_json,created_at,updated_at) VALUES(?,?,?,?,?,?)", (i, 0, 1, json.dumps(vec(rng)), now, now))
        db.conn.commit()
    out = pf._move_detections(db, [2, 3, 4], performer_id=1)
    assert out["moved"] == 3
    rows = db.conn.execute("SELECT group_id,COUNT(*) n FROM photo_face_detections GROUP BY group_id").fetchall()
    assert sorted(r["n"] for r in rows) == [2, 3]
    named = db.conn.execute("SELECT performer_id FROM photo_face_groups WHERE id=?", (out["group_id"],)).fetchone()
    assert named["performer_id"] == 1
    import pytest
    with pytest.raises(ValueError):
        pf._move_detections(db, [], None)
    with pytest.raises(ValueError):
        pf._move_detections(db, [1], performer_id=99)


def test_model_download_falls_back_to_the_mirror(tmp_path, monkeypatch):
    calls = []

    def fake(url, dest, digest):
        calls.append(url)
        if __import__("urllib.parse").parse.urlsplit(url).hostname == "github.com":
            raise OSError("blocked")
        dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(b"x" * 2048)
    monkeypatch.setattr(pf, "_download_checked", fake)
    monkeypatch.setattr(pf, "_cache_root", lambda: tmp_path)
    yunet, sface = pf._ensure_models()
    assert calls == [pf._YUNET_URL, pf._YUNET_URL_FALLBACK, pf._SFACE_URL, pf._SFACE_URL_FALLBACK]
    assert yunet.exists() and sface.exists()
