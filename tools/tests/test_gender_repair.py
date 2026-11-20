import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.metadata.gender_repair import missing_gender_count, restore_missing_genders


def test_blank_genders_come_back_from_the_video_provider_details_and_nothing_else_changes(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for n, g in (("Ava Stone", ""), ("Mia Rivera", None), ("Zoe Ray", "male"), ("Nina Nolan", ""), ("Unknown Performer", "")):
            db.conn.execute("INSERT INTO performers(name,gender) VALUES(?,?)", (n, g))
        ids = {r["name"]: r["id"] for r in db.conn.execute("SELECT id,name FROM performers").fetchall()}
        payload = json.dumps({"performer_details": [{"name": "ava stone", "gender": "FEMALE"}, {"name": "Mia Rivera", "gender": "transgender_female"}, {"name": "Zoe Ray", "gender": "female"}]})
        for i, who in enumerate(("Ava Stone", "Mia Rivera", "Zoe Ray"), start=1):
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,provider_payload,status,created_at,updated_at) VALUES('-1',?,?,?,'ACTIVE','t','t')", (i, f"v{i}.mp4", payload))
            vid = db.conn.execute("SELECT id FROM media WHERE source_message_id=?", (i,)).fetchone()["id"]
            db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(?,?)", (vid, ids[who]))
        db.conn.commit()
    assert missing_gender_count(db) == 3          # Ava, Mia, Nina (Unknown Performer is not counted)
    r = restore_missing_genders(db)
    assert r == {"checked": 3, "restored": 2, "still_blank": 1}
    with db.lock:
        g = {x["name"]: x["gender"] for x in db.conn.execute("SELECT name,gender FROM performers").fetchall()}
    assert g["Ava Stone"] == "female" and g["Mia Rivera"] == "trans"
    assert g["Zoe Ray"] == "male", "a performer who already has a gender is never changed"
    assert not g["Nina Nolan"]
    assert missing_gender_count(db) == 1
