"""A real performer or studio replaces 'Unknown Performer' / 'Unknown Studio' on a video, however the link was added."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core import placeholders
from telegram_archive.core.database import Database


def make(tmp_path):
    db = Database(tmp_path / "p.db")
    with db.lock:
        for n in ("Unknown Performer", "Mia Rivera"):
            db.conn.execute("INSERT INTO performers(name) VALUES(?)", (n,))
        for n in ("Unknown Studio", "Acme"):
            db.conn.execute("INSERT INTO studios(name) VALUES(?)", (n,))
        db.conn.commit()
    ids = {n: db.conn.execute("SELECT id FROM performers WHERE name=?", (n,)).fetchone()[0] for n in ("Unknown Performer", "Mia Rivera")}
    sids = {n: db.conn.execute("SELECT id FROM studios WHERE name=?", (n,)).fetchone()[0] for n in ("Unknown Studio", "Acme")}
    return db, ids, sids


def links(db, table, col, video=1):
    return sorted(r[0] for r in db.conn.execute(f"SELECT {col} FROM {table} WHERE video_id=?", (video,)))


def test_adding_a_real_performer_takes_the_placeholder_away(tmp_path):
    db, p, _ = make(tmp_path)
    with db.lock:
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(1,?)", (p["Unknown Performer"],))
        db.conn.execute("INSERT INTO local_video_performers(video_id,performer_id,created_at) VALUES(1,?,'x')", (p["Unknown Performer"],))
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(1,?)", (p["Mia Rivera"],))
        db.conn.commit()
    assert links(db, "video_performers", "performer_id") == [p["Mia Rivera"]]
    assert links(db, "local_video_performers", "performer_id") == []          # the other list that names performers is cleared too


def test_other_videos_keep_their_placeholder(tmp_path):
    db, p, _ = make(tmp_path)
    with db.lock:
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(2,?)", (p["Unknown Performer"],))
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(1,?)", (p["Mia Rivera"],))
        db.conn.commit()
    assert links(db, "video_performers", "performer_id", video=2) == [p["Unknown Performer"]]


def test_choosing_the_placeholder_on_purpose_still_works(tmp_path):
    db, p, _ = make(tmp_path)
    with db.lock:
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(1,?)", (p["Unknown Performer"],))
        db.conn.commit()
    assert links(db, "video_performers", "performer_id") == [p["Unknown Performer"]]


def test_a_real_studio_replaces_unknown_studio(tmp_path):
    db, _, s = make(tmp_path)
    with db.lock:
        db.conn.execute("INSERT INTO video_studios(video_id,studio_id) VALUES(1,?)", (s["Unknown Studio"],))
        db.conn.execute("INSERT INTO video_studios(video_id,studio_id) VALUES(1,?)", (s["Acme"],))
        db.conn.commit()
    assert links(db, "video_studios", "studio_id") == [s["Acme"]]


def test_what_is_already_in_the_database_is_cleaned_when_it_opens(tmp_path):
    db, p, s = make(tmp_path)
    with db.lock:
        for t in ("ax_placeholder_video_performers_insert", "ax_placeholder_local_video_performers_insert", "ax_placeholder_video_studios_insert"):
            db.conn.execute(f"DROP TRIGGER {t}")
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(1,?),(1,?),(2,?)", (p["Unknown Performer"], p["Mia Rivera"], p["Unknown Performer"]))
        db.conn.execute("INSERT INTO video_studios(video_id,studio_id) VALUES(1,?),(1,?)", (s["Unknown Studio"], s["Acme"]))
        db.conn.commit()
        placeholders.install(db.conn)
    assert links(db, "video_performers", "performer_id") == [p["Mia Rivera"]]
    assert links(db, "video_performers", "performer_id", video=2) == [p["Unknown Performer"]]   # a video with only the placeholder keeps it
    assert links(db, "video_studios", "studio_id") == [s["Acme"]]
