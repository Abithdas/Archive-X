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


def test_auto_match_names_only_clear_matches(tmp_path):
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
    out = pf._auto_match_people(db)
    rows = {r["id"]: r["performer_id"] for r in db.conn.execute("SELECT id,performer_id FROM photo_face_groups")}
    assert out["matched"] == 1
    assert rows[1] == 1          # Ava's face group is named
    assert rows[2] is None       # an unrelated face is left unnamed
