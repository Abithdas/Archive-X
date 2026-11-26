"""The channels list reads its three big answers side by side; each must still see the content mode, and no connections may pile up."""
from __future__ import annotations

from fastapi.testclient import TestClient

from telegram_archive.app import create_app
from telegram_archive.core.auth import ensure_web_credentials
from telegram_archive.core.database import Database

NOW = "2026-11-26T00:00:00"
H = {"Origin": "https://testserver"}


def _client(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for tid, rating in (("-1001", "sfw"), ("-1002", "nsfw"), ("-1003", "nsfw")):
            db.conn.execute("INSERT INTO channels(telegram_id,title,updated_at,in_library,content_rating) VALUES(?,?,?,1,?)", (tid, f"Channel {tid}", NOW, rating))
            for m in range(1, 4):
                db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,streamable,status,created_at,updated_at) VALUES(?,?,?,1,'ACTIVE',?,?)", (tid, m, f"v{m}", NOW, NOW))
                db.conn.execute("INSERT INTO resource_state(media_id,catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,updated_at) "
                                "SELECT id,'COMPLETE','COMPLETE','COMPLETE','READY','COMPLETE','COMPLETE',? FROM media WHERE source_channel=? AND source_message_id=?", (NOW, tid, m))
                db.conn.execute("INSERT INTO timeline_preview_cache(source_channel,video_message_id,path,status,frame_count,updated_at) VALUES(?,?,'p','READY',36,?)", (tid, m, NOW))
                db.conn.execute("INSERT INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) VALUES(?,?,'image/jpeg',x'ffff','playback-snapshot',?)", (tid, m, NOW))
                db.conn.execute("INSERT INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,created_at,updated_at) VALUES(NULL,'telegram_video',?,'PLAYBACK_REPRESENTATIVE','a','READY',?,?)", (f"{tid}:{m}", NOW, NOW))
        # a channel that has videos but no channel row of its own (older data)
        db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,streamable,status,created_at,updated_at) VALUES('-1004',1,'orphan',1,'ACTIVE',?,?)", (NOW, NOW))
        db.conn.commit()
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    pw = "correct-horse-battery"
    assert c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H).status_code in (200, 201, 204)
    assert c.post("/login", json={"username": "admin", "password": "correct-horse-battery"}, headers=H).status_code == 200
    return db, c


def test_each_mode_lists_its_own_channels_with_their_counts(tmp_path):
    db, c = _client(tmp_path)
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    sfw = {x["telegram_id"]: x["video_count"] for x in c.get("/api/channels").json()}
    assert sfw == {"-1001": 3}
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    nsfw = {x["telegram_id"]: x["video_count"] for x in c.get("/api/channels").json()}
    assert nsfw == {"-1002": 3, "-1003": 3}, nsfw


def test_the_side_by_side_workers_are_made_once_and_add_no_connections_of_their_own(tmp_path):
    import threading
    db, c = _client(tmp_path)

    def growth(path):
        c.get(path)
        before = len(db._read_conns)
        for _ in range(8):
            assert c.get(path).status_code == 200
        return len(db._read_conns) - before
    # The test client runs every request in a new thread, which opens a read connection of its own (the running server reuses a bounded set of
    # threads), so what is compared is the channels list against a page that never used workers: it must not open more.
    count = lambda: len([t for t in threading.enumerate() if t.name.startswith("channels-list")])
    c.get("/api/channels")
    workers = count()                       # three for this application (an earlier test's application may still hold its own three)
    assert growth("/api/channels") <= growth("/api/performers")
    assert count() == workers, "more requests must not make more worker threads"
