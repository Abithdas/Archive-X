import asyncio, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import missing_images, performer_image_sync, studio_image_sync


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for n in ("Has Pic", "No Pic A", "No Pic B", "Unknown Performer", "Unlinked"):
            db.conn.execute("INSERT INTO performers(name) VALUES(?)", (n,))
        db.conn.execute("INSERT INTO studios(name) VALUES('Studio One')")
        ids = {r["name"]: r["id"] for r in db.conn.execute("SELECT id,name FROM performers").fetchall()}
        sid = db.conn.execute("SELECT id FROM studios").fetchone()["id"]
        db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,status,created_at,updated_at) VALUES('-1',1,'a.mp4','ACTIVE','t','t')")
        vid = db.conn.execute("SELECT id FROM media").fetchone()["id"]
        for n in ("Has Pic", "No Pic A", "No Pic B", "Unknown Performer"):
            db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(?,?)", (vid, ids[n]))
        db.conn.execute("INSERT INTO video_studios(video_id,studio_id) VALUES(?,?)", (vid, sid))
        db.conn.execute("INSERT INTO performer_image_cache(performer_id,url,mime,data,updated_at) VALUES(?, 'u','image/png',x'89504e47','t')", (ids["Has Pic"],))
        db.conn.commit()
    return db, ids, sid


def test_only_linked_performers_and_studios_without_a_stored_picture_count(tmp_path):
    db, ids, sid = _db(tmp_path)
    assert missing_images.counts(db) == {"performers": 2, "studios": 1}       # not "Has Pic", "Unknown Performer" or the unlinked one


def test_a_try_is_remembered_so_a_miss_is_not_asked_again_this_week_and_a_find_removes_it_from_the_list(tmp_path, monkeypatch):
    db, ids, sid = _db(tmp_path)
    asked = []
    async def perf(db_, pid, force_api=True, **k):
        asked.append(("p", pid))
        if pid == ids["No Pic A"]:
            with db_.lock:
                db_.conn.execute("INSERT OR REPLACE INTO performer_image_cache(performer_id,url,mime,data,updated_at) VALUES(?, 'u','image/png',x'89504e47','t')", (pid,))
                db_.conn.commit()
            return {"ok": True}
        return {"ok": False, "outcome": "no_provider_artwork"}
    async def stud(db_, sid_, force_api=False, **k):
        asked.append(("s", sid_)); raise RuntimeError("provider artwork rejected")
    monkeypatch.setattr(performer_image_sync, "refresh_performer_image", perf)
    monkeypatch.setattr(studio_image_sync, "refresh_studio_image", stud)
    r = asyncio.run(missing_images.fetch_missing(db, pause=0))
    assert r == {"tried": 3, "found": 1, "not_found": 2}                       # a failing lookup (the studio raised) does not stop the others
    assert missing_images.counts(db) == {"performers": 1, "studios": 1}
    asked.clear()
    r2 = asyncio.run(missing_images.fetch_missing(db, pause=0))
    assert r2["tried"] == 0 and asked == []                                  # nothing is retried within the week
    with db.lock:
        db.conn.execute("UPDATE entity_image_attempts SET last_attempt=last_attempt-?", (8 * 86400,))
        db.conn.commit()
    asyncio.run(missing_images.fetch_missing(db, pause=0))
    assert {a[0] for a in asked} == {"p", "s"}                               # a week later they are tried again


def test_the_limit_applies_per_kind(tmp_path, monkeypatch):
    db, ids, sid = _db(tmp_path)
    async def nothing(*a, **k): return {"ok": False}
    monkeypatch.setattr(performer_image_sync, "refresh_performer_image", nothing)
    monkeypatch.setattr(studio_image_sync, "refresh_studio_image", nothing)
    assert asyncio.run(missing_images.fetch_missing(db, limit=1, pause=0))["tried"] == 2    # one performer, one studio
