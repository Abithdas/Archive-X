"""SFW / NSFW: the filtered read connection, the switch with its PIN, and the channel marks."""
import os
import sys
import tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axcm_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)

import pytest  # noqa: E402

from telegram_archive.core import content_mode as cm  # noqa: E402
from telegram_archive.core.database import Database  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "a.db")
    now = "2026-01-01T00:00:00"
    with d.lock:
        d.conn.executemany("INSERT INTO channels(telegram_id,title,updated_at,in_library,content_rating) VALUES(?,?,?,?,?)",
                           [("1", "Adult", now, 1, "nsfw"), ("2", "Movies", now, 1, "sfw"), ("3", "Hidden", now, 0, "nsfw")])
        d.conn.executemany("INSERT INTO media(source_channel,source_message_id,filename) VALUES(?,?,?)",
                           [("1", 1, "a.mp4"), ("2", 1, "b.mkv"), ("3", 1, "c.mp4")])
        d.conn.execute("INSERT INTO performers(name,content_rating) VALUES('Adult Star','nsfw')")
        d.conn.execute("INSERT INTO performers(name,content_rating) VALUES('Movie Actor','sfw')")
        d.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(1,1),(2,2),(3,1)")
        d.conn.commit()
    return d


def names(d, sql, filtered):
    tok = cm.FILTER_ACTIVE.set(filtered)
    try:
        with d.reading() as c:
            return sorted(r[0] for r in c.execute(sql).fetchall())
    finally:
        cm.FILTER_ACTIVE.reset(tok)


def test_default_mode_is_sfw_and_shows_only_sfw_library_channels(db):
    assert cm.get_mode(db) == "sfw"
    assert names(db, "SELECT filename FROM media", True) == ["b.mkv"]
    assert names(db, "SELECT title FROM channels", True) == ["Movies"]
    assert names(db, "SELECT name FROM performers", True) == ["Movie Actor"]


def test_nsfw_shows_only_nsfw_channels_that_are_in_the_library(db):
    cm.set_mode(db, "nsfw")
    assert names(db, "SELECT filename FROM media", True) == ["a.mp4"]            # channel 3 is not in the library
    assert names(db, "SELECT name FROM performers", True) == ["Adult Star"]
    assert names(db, "SELECT COUNT(*) FROM video_performers WHERE performer_id=1", True) == [1]   # counts follow the mode


def test_unfiltered_connection_and_writes_see_everything(db):
    assert names(db, "SELECT filename FROM media", False) == ["a.mp4", "b.mkv", "c.mp4"]
    with db.lock:
        assert db.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 3


def test_pin_is_hashed_checked_and_locks_out(db):
    assert not cm.pin_is_set(db)
    with pytest.raises(ValueError):
        cm.set_pin(db, "12")
    cm.set_pin(db, "2468")
    assert cm.pin_is_set(db) and "2468" not in str(db.setting(cm.SETTING_PIN, ""))
    assert cm.verify_pin(db, "2468") and not cm.verify_pin(db, "0000")
    for _ in range(cm.MAX_FAILURES):
        cm.verify_pin(db, "1111")
    assert cm.locked_out() > 0
    cm._failures.clear()


def test_views_name_only_known_tables():
    assert [n for n, _ in cm.VISIBILITY_VIEWS] == ["archive_x_published_media", "archive_x_catalogued_media", "archive_x_library_media", "channels", "media", "photo_library_items", "photo_face_detections", "video_performers",
                                                    "local_video_performers", "video_studios", "video_tags", "watch_history", "scene_markers",
                                                    "performers", "studios", "tags"]


def _client(db):
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))   # first-run setup is allowed from this PC only
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, H


def test_the_pin_is_optional_and_when_there_is_one_it_is_asked_for(db):
    cm._failures.clear()
    c, H = _client(db)
    assert c.get("/api/content-mode").json() == {"ok": True, "mode": "sfw", "pin_set": False, "locked_for": 0}
    def people():   # which of the two performers (1 adult, 2 movie cast) a page can still open
        return [pid for pid in (1, 2) if c.get(f"/api/performers/{pid}/edit").status_code == 200]
    assert people() == [2]
    assert c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H).json()["mode"] == "nsfw"      # no PIN chosen: none is needed
    assert people() == [1]
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).json()["mode"] == "sfw"
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "12"}, headers=H).status_code == 400      # a PIN that is chosen must be 4 to 12 digits
    assert c.post("/api/content-mode/pin", json={"new": "2468"}, headers=H).json()["pin_set"] is True
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).json()["mode"] == "sfw"   # towards SFW: no PIN
    assert c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H).status_code == 403          # with a PIN set it is asked for
    assert c.post("/api/content-mode", json={"mode": "nsfw", "pin": "0000"}, headers=H).status_code == 403
    assert c.post("/api/content-mode", json={"mode": "nsfw", "pin": "2468"}, headers=H).status_code == 200
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    assert c.post("/api/content-mode/pin", json={"remove": True, "current": "0000"}, headers=H).status_code == 403   # removing it needs it
    assert c.post("/api/content-mode/pin", json={"remove": True, "current": "2468"}, headers=H).json()["pin_set"] is False
    assert c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H).status_code == 200            # and it is optional again


def test_channel_marks_are_listed_unfiltered_and_can_be_changed(db):
    c, H = _client(db)
    first = c.get("/api/telegram/channel-marks").json()
    assert sorted(m["title"] for m in first["channels"]) == ["Movies"] and first["hidden"] == 2   # SFW: no NSFW channel's name is listed, in the library or not
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    marks = c.get("/api/telegram/channel-marks").json()["channels"]
    assert sorted(m["title"] for m in marks) == ["Adult", "Hidden", "Movies"]       # NSFW: management sees every channel
    r = c.post("/api/telegram/channel-marks", json={"ids": ["3"], "in_library": True, "content_rating": "sfw"}, headers=H)
    assert r.json()["changed"] == 1
    after = {m["title"]: m for m in c.get("/api/telegram/channel-marks").json()["channels"]}
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    assert sorted(m["title"] for m in c.get("/api/telegram/channel-marks").json()["channels"]) == ["Hidden", "Movies"]   # sorted as SFW, it shows in SFW now
    assert after["Hidden"]["in_library"] == 1 and after["Hidden"]["content_rating"] == "sfw"
    assert c.post("/api/telegram/channel-marks", json={"ids": ["3"], "content_rating": "maybe"}, headers=H).status_code == 400


def test_a_channel_with_no_row_counts_as_an_nsfw_library_channel(db):
    with db.lock:
        db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename) VALUES('9',1,'orphan.mp4')")
        db.conn.commit()
    assert "orphan.mp4" not in names(db, "SELECT filename FROM media", True)          # sfw: not a movie channel
    cm.set_mode(db, "nsfw")
    assert "orphan.mp4" in names(db, "SELECT filename FROM media", True)             # nsfw: as before this feature


def test_counting_videos_per_performer_stays_fast_with_the_filter_on(tmp_path):
    """The link-table views once used `video_id IN (SELECT ...)`, which SQLite repeated for every row: 40 seconds at 100,000 videos."""
    import time
    d = Database(tmp_path / "big.db")
    now = "2026-01-01T00:00:00"
    with d.lock:
        d.conn.executemany("INSERT INTO channels(telegram_id,title,updated_at,in_library,content_rating) VALUES(?,?,?,?,?)", [(str(-1000 - i), f"c{i}", now, 1, "nsfw") for i in range(50)])
        d.conn.executemany("INSERT INTO media(source_channel,source_message_id,filename) VALUES(?,?,?)", [(str(-1000 - i % 50), i, f"f{i}.mp4") for i in range(20000)])
        d.conn.executemany("INSERT INTO performers(name,content_rating) VALUES(?,?)", [(f"p{i}", "nsfw") for i in range(1000)])
        d.conn.executemany("INSERT INTO video_performers(video_id,performer_id) VALUES(?,?)", [(i + 1, i % 1000 + 1) for i in range(20000)])
        d.conn.commit()
    cm.set_mode(d, "nsfw")
    tok = cm.FILTER_ACTIVE.set(True)
    try:
        with d.reading() as c:
            t = time.perf_counter()
            rows = c.execute("SELECT p.id, COUNT(vp.video_id) n FROM performers p LEFT JOIN video_performers vp ON vp.performer_id=p.id GROUP BY p.id").fetchall()
            took = time.perf_counter() - t
    finally:
        cm.FILTER_ACTIVE.reset(tok)
    assert len(rows) == 1000 and sum(r["n"] for r in rows) == 20000
    assert took < 2.0, f"{took:.1f}s"


def test_the_desktop_app_opens_in_sfw_whatever_it_was_left_in(tmp_path, monkeypatch):
    from telegram_archive.app import create_app
    db = Database(tmp_path / "d.db")
    cm.set_mode(db, "nsfw")
    monkeypatch.delenv("ARCHIVE_X_DESKTOP", raising=False)
    create_app(db=db)
    assert cm.get_mode(db) == "nsfw"                       # a browser or a test keeps what was chosen
    monkeypatch.setenv("ARCHIVE_X_DESKTOP", "1")
    create_app(db=db)
    assert cm.get_mode(db) == "sfw"                        # the desktop app starts safe, every time
