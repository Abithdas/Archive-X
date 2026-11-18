"""Local folders as photo sources: the scanner, the routes, and that the app can read the photos where they lie."""
import os
import sys
import tempfile
import time
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axlf_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
sys.path.insert(0, root)

import pytest  # noqa: E402
from PIL import Image  # noqa: E402

from telegram_archive.core import content_mode as cm  # noqa: E402
from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines import local_scanner as ls  # noqa: E402
from telegram_archive.engines import photo_cache  # noqa: E402


def png(path: Path, size=(40, 30), color=(200, 30, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


@pytest.fixture()
def folder(tmp_path):
    f = tmp_path / "Pictures"
    png(f / "a.png")
    png(f / "trip" / "b.jpg", (80, 60))
    png(f / "trip" / "skip" / "c.png")
    (f / "trip" / "clip.mp4").write_bytes(b"x")
    (f / "notes.txt").write_text("hi")
    (f / ".hidden").mkdir()
    png(f / ".hidden" / "d.png")
    return f


def make_source(db, folder, **kw):
    with db.lock:
        cur = db.conn.execute("INSERT INTO local_sources(path,recursive,excluded,created_at) VALUES(?,?,?,?)", (str(folder), kw.get("recursive", 1), kw.get("excluded", "[]"), "2026-01-01T00:00:00"))
        sid = cur.lastrowid
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES(?,?,?,?,?,?,?)",
                        (ls.channel_id(sid), "Pictures", "local", 1, "nsfw", "both", "2026-01-01T00:00:00"))
        db.conn.commit()
    return sid


def test_the_walk_finds_images_and_videos_and_skips_hidden_and_excluded(folder):
    kinds = {Path(p).name: k for p, k in ls.walk_files(folder, recursive=True, excluded=[])}
    assert kinds == {"a.png": "image", "b.jpg": "image", "c.png": "image", "clip.mp4": "video"}
    assert {Path(p).name for p, _ in ls.walk_files(folder, recursive=True, excluded=["trip/skip"])} == {"a.png", "b.jpg", "clip.mp4"}
    assert {Path(p).name for p, _ in ls.walk_files(folder, recursive=False, excluded=[])} == {"a.png"}


def test_a_scan_indexes_the_photos_and_follows_the_disk(tmp_path, folder):
    db = Database(tmp_path / "a.db")
    sid = make_source(db, folder)
    first = ls.scan_source(db, sid)
    assert first == {"photos": 3, "videos": 1, "added": 3, "removed": 0}
    with db.reading() as c:
        rows = {r["filename"]: r for r in c.execute("SELECT * FROM photo_library_items WHERE channel_id=?", (ls.channel_id(sid),))}
        state = c.execute("SELECT status,photos_found FROM photo_library_scan_state WHERE channel_id=?", (ls.channel_id(sid),)).fetchone()
    assert set(rows) == {"a.png", "b.jpg", "c.png"} and (rows["b.jpg"]["width"], rows["b.jpg"]["height"]) == (80, 60)
    assert rows["a.png"]["source_kind"] == "local" and rows["a.png"]["mime_type"] == "image/png"
    assert (state["status"], state["photos_found"]) == ("COMPLETE", 3)
    again = ls.scan_source(db, sid)                                    # nothing changed: nothing added, ids stay
    assert again["added"] == 0 and again["photos"] == 3
    (folder / "a.png").unlink()
    png(folder / "new.png")
    third = ls.scan_source(db, sid)
    assert third["added"] == 1 and third["removed"] == 1
    with db.reading() as c:
        assert {r[0] for r in c.execute("SELECT filename FROM photo_library_items WHERE channel_id=?", (ls.channel_id(sid),))} == {"b.jpg", "c.png", "new.png"}


def test_a_missing_folder_is_a_failed_scan_not_a_crash(tmp_path, folder):
    db = Database(tmp_path / "a.db")
    sid = make_source(db, folder / "gone")
    with pytest.raises(FileNotFoundError):
        ls.scan_source(db, sid)
    with db.reading() as c:
        assert c.execute("SELECT last_status FROM local_sources WHERE id=?", (sid,)).fetchone()[0] == "FAILED"


def test_the_app_reads_photos_inside_added_folders_only(tmp_path, folder):
    photo_cache.set_local_roots([folder])
    try:
        assert photo_cache.resolve_database_cache_path(str(folder / "a.png")) == Path(os.path.realpath(folder / "a.png"))
        assert photo_cache.resolve_database_cache_path(str(tmp_path / "other.png")) is None
        assert photo_cache.resolve_database_cache_path(str(folder / ".." / "other.png")) is None
    finally:
        photo_cache.set_local_roots([])


def test_photos_follow_the_mode_and_what_the_folder_holds(tmp_path, folder):
    db = Database(tmp_path / "a.db")
    sid = make_source(db, folder)
    ls.scan_source(db, sid)

    def count():
        tok = cm.FILTER_ACTIVE.set(True)
        try:
            with db.reading() as c:
                return c.execute("SELECT COUNT(*) FROM photo_library_items").fetchone()[0]
        finally:
            cm.FILTER_ACTIVE.reset(tok)
    assert count() == 0                                                 # the folder is NSFW and the mode is SFW
    cm.set_mode(db, "nsfw")
    assert count() == 3
    with db.lock:
        db.conn.execute("UPDATE channels SET holds='videos' WHERE telegram_id=?", (ls.channel_id(sid),))
        db.conn.commit()
    assert count() == 0                                                 # a folder marked "videos only" shows no photos


def _client(db):
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, H


def test_routes_add_scan_serve_and_remove_a_folder(tmp_path, folder):
    db = Database(tmp_path / "a.db")
    c, H = _client(db)
    r = c.post("/api/local-folders", json={"path": str(folder), "content_rating": "nsfw"}, headers=H)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    for _ in range(100):                                               # the scan runs in the background
        f = c.get("/api/local-folders").json()["folders"][0]
        if f["last_status"] == "COMPLETE":
            break
        time.sleep(0.05)
    assert (f["photos"], f["videos"], f["content_rating"], f["title"]) == (3, 1, "nsfw", "Pictures")
    assert c.post("/api/local-folders", json={"path": str(folder / "trip"), "content_rating": "sfw"}, headers=H).status_code == 409   # overlaps
    assert c.post("/api/local-folders", json={"path": str(tmp_path / "nope")}, headers=H).status_code == 400
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    channels = {ch["telegram_id"]: ch for ch in c.get("/api/photos/channels").json()}
    assert channels[f"local:{sid}"]["photo_count"] == 3 and channels[f"local:{sid}"]["photo_scan_status"] == "COMPLETE"
    img = c.get(f"/api/photos/local:{sid}/1/image")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG" or img.content[:2] == b"\xff\xd8"
    assert c.get(f"/api/photos/local:{sid}/1/thumbnail").status_code == 200
    kids = c.get("/api/local-folders/browse", params={"path": str(folder)}).json()
    assert [k["name"] for k in kids["folders"]] == ["trip"]            # hidden folders are not offered
    assert c.delete(f"/api/local-folders/{sid}", headers=H).status_code == 200
    assert c.get("/api/local-folders").json()["folders"] == [] and (folder / "a.png").exists()
    assert photo_cache.resolve_database_cache_path(str(folder / "a.png")) is None   # no longer an allowed place
