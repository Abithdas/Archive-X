"""Videos with no performer or studio are filed under Unknown Performer / Unknown Studio in BOTH content modes, each mode listing only its own videos."""
import pytest
from fastapi.testclient import TestClient

from telegram_archive.app import create_app
from telegram_archive.core import resource_registry as rr
from telegram_archive.core.auth import ensure_web_credentials
from telegram_archive.core.database import Database

H = {"Origin": "https://testserver"}
NOW = "2026-01-01T00:00:00"


@pytest.fixture()
def world(tmp_path):
    db = Database(tmp_path / "archive.db")
    ensure_web_credentials(db)
    with db.lock:
        for tid, title, rating in (("-100", "Adult channel", "nsfw"), ("-200", "Family channel", "sfw")):
            db.conn.execute("INSERT INTO channels(telegram_id,title,updated_at,in_library,content_rating) VALUES (?,?,'t',1,?)", (tid, title, rating))
        n = 0
        for ch in ("-100", "-200"):
            for i in range(3):
                n += 1
                db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,status,created_at,updated_at,duration,streamable) VALUES (?,?,?,'ACTIVE','t','t',600,1)", (ch, n, f"{ch}_{i}.mp4"))
        db.conn.execute("INSERT INTO performers(name,content_rating) VALUES ('Adult Star','nsfw'),('Family Actor','sfw')")
        db.conn.execute("INSERT INTO studios(name,content_rating) VALUES ('Adult Studio','nsfw'),('Family Studio','sfw')")
        db.conn.commit()
        ids = {r[0]: r[1] for r in db.conn.execute("SELECT filename,id FROM media")}
        pid = {r[0]: r[1] for r in db.conn.execute("SELECT name,id FROM performers")}
        sid = {r[0]: r[1] for r in db.conn.execute("SELECT name,id FROM studios")}
        db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES (?,?),(?,?)", (ids["-100_0.mp4"], pid["Adult Star"], ids["-200_0.mp4"], pid["Family Actor"]))
        db.conn.execute("INSERT INTO video_studios(video_id,studio_id) VALUES (?,?),(?,?)", (ids["-100_0.mp4"], sid["Adult Studio"], ids["-200_0.mp4"], sid["Family Studio"]))
        db.conn.commit()
    rr.ensure_unknown_entity_links(db)
    with db.lock:
        for ch, m in [(r[0], r[1]) for r in db.conn.execute("SELECT source_channel,source_message_id FROM media")]:
            for sql in (
                f"INSERT OR REPLACE INTO resource_state(media_id,catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,updated_at) SELECT id,'COMPLETE','COMPLETE','COMPLETE','READY','COMPLETE','COMPLETE','{NOW}' FROM media WHERE source_channel='{ch}' AND source_message_id={m}",
                f"INSERT OR REPLACE INTO timeline_preview_cache(source_channel,video_message_id,path,status,frame_count,updated_at) VALUES('{ch}',{m},'p','READY',36,'{NOW}')",
                f"INSERT OR REPLACE INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) VALUES('{ch}',{m},'image/jpeg',x'ffff','playback-snapshot','{NOW}')",
                f"INSERT OR REPLACE INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,created_at,updated_at) VALUES(NULL,'telegram_video','{ch}:{m}','PLAYBACK_REPRESENTATIVE','a','READY','{NOW}','{NOW}')"):
                db.conn.execute(sql)
        db.conn.commit()
        unknown = (db.conn.execute("SELECT id FROM performers WHERE name='Unknown Performer'").fetchone()[0], db.conn.execute("SELECT id FROM studios WHERE name='Unknown Studio'").fetchone()[0])
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 50000))
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, unknown


@pytest.mark.parametrize("mode,channel,real_p,real_s", [("nsfw", "-100", "Adult Star", "Adult Studio"), ("sfw", "-200", "Family Actor", "Family Studio")])
def test_each_mode_files_its_own_videos_under_unknown(world, mode, channel, real_p, real_s):
    c, (up, us) = world
    assert c.post("/api/content-mode", json={"mode": mode}, headers=H).status_code == 200
    listed = {p["name"]: p["video_count"] for p in c.get("/api/performers", headers=H).json()}
    assert listed == {"Unknown Performer": 2, real_p: 1}, listed
    studios = {s["name"]: s["video_count"] for s in c.get("/api/studios", headers=H).json()}
    assert studios == {"Unknown Studio": 2, real_s: 1}, studios
    for url in (f"/api/performers/{up}/videos", f"/api/studios/{us}/videos"):
        body = c.get(url, headers=H).json()
        assert sorted(v["filename"] for v in (body.get("videos") or body.get("items"))) == [f"{channel}_1.mp4", f"{channel}_2.mp4"], url   # this mode's, never the other's
    shown = {v["filename"]: v for v in c.get("/api/library", headers=H).json()["items"]}
    assert shown[f"{channel}_1.mp4"]["performers"] == ["Unknown Performer"] and shown[f"{channel}_1.mp4"]["studios"] == ["Unknown Studio"]


def test_unknown_is_not_listed_in_a_mode_that_has_no_video_under_it(tmp_path):
    db = Database(tmp_path / "archive.db")
    ensure_web_credentials(db)
    with db.lock:
        db.conn.execute("INSERT INTO performers(name) VALUES ('Unknown Performer')")
        db.conn.commit()
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 50000))
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    c.post("/login", json={"username": "admin", "password": pw}, headers=H)
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    assert [p["name"] for p in c.get("/api/performers", headers=H).json()] == []
