"""Local folders as photo sources: the scanner, the routes, and that the app can read the photos where they lie."""
import os
import sys
import tempfile
import threading
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
    assert first == {"photos": 3, "videos": 1, "added": 3, "removed": 0, "moved": 0}
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
    os.utime(folder / "new.png", (time.time() + 3600, time.time() + 3600))     # made later: a new photo, not the old one moved
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
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200   # the folder here is NSFW
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
    channels = {ch["telegram_id"]: ch for ch in c.get("/api/photos/channels").json()}
    assert channels[ls.channel_id(sid)]["photo_count"] == 3 and channels[ls.channel_id(sid)]["photo_scan_status"] == "COMPLETE"
    img = c.get(f"/api/photos/{ls.channel_id(sid)}/1/image")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG" or img.content[:2] == b"\xff\xd8"
    assert c.get(f"/api/photos/{ls.channel_id(sid)}/1/thumbnail").status_code == 200
    kids = c.get("/api/local-folders/browse", params={"path": str(folder)}).json()
    assert [k["name"] for k in kids["folders"]] == ["trip"]            # hidden folders are not offered
    assert c.delete(f"/api/local-folders/{sid}", headers=H).status_code == 200
    assert c.get("/api/local-folders").json()["folders"] == [] and (folder / "a.png").exists()
    assert photo_cache.resolve_database_cache_path(str(folder / "a.png")) is None   # no longer an allowed place


def _make_video(path: Path, seconds: int = 4) -> bool:
    import shutil
    import subprocess
    if not shutil.which("ffmpeg"):
        return False
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=15:duration={seconds}", "-pix_fmt", "yuv420p", str(path)], capture_output=True)
    return done.returncode == 0 and path.is_file()


def test_a_local_video_is_listed_probed_streamed_and_gets_its_snapshots(tmp_path):
    root = tmp_path / "Movies"
    root.mkdir()
    if not _make_video(root / "Some Film (2019).mp4"):
        pytest.skip("ffmpeg is not available")
    db = Database(tmp_path / "v.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    r = c.post("/api/local-folders", json={"path": str(root), "content_rating": "nsfw", "holds": "videos"}, headers=H)
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    ch = ls.channel_id(sid)
    for _ in range(1200):                                                # scan, probe and snapshots run in the background
        work = c.get("/api/local-folders").json()["folders"][0].get("video_work") or {}
        with db.reading() as conn:
            row = conn.execute("SELECT probed,snapshots FROM local_video_files WHERE channel_id=?", (ch,)).fetchone()
        if row and row["snapshots"] and not work.get("running"):
            break
        time.sleep(0.1)
    assert row and (row["probed"], row["snapshots"]) == (1, 1)
    with db.reading() as conn:
        m = conn.execute("SELECT * FROM media WHERE source_channel=?", (ch,)).fetchone()
    assert (m["title"], m["width"], m["height"]) == ("Some Film (2019)", 320, 180) and 3.5 < m["duration"] < 4.5
    mid = m["source_message_id"]
    info = c.get(f"/api/play/info/{ch}/{mid}").json()
    assert info["local_file"] is True and info["width"] == 320 and info["mime"] == "video/mp4"
    full = c.get(f"/stream/{ch}/{mid}")
    part = c.get(f"/stream/{ch}/{mid}", headers={"Range": "bytes=0-99"})
    assert full.status_code == 200 and part.status_code == 206 and len(part.content) == 100 and full.content[:100] == part.content
    assert c.get(f"/api/play/preview-manifest/{ch}/{mid}").json()["ready"] is True
    assert c.get(f"/api/play/preview-grid/{ch}/{mid}").status_code == 200
    live = c.get(f"/api/play/preview/{ch}/{mid}?t=1.5&w=320&pv=15&lw=1")   # the hover frame on the timeline: straight from the file, no Telegram
    assert live.status_code == 200 and live.headers["content-type"] == "image/jpeg"
    assert c.get(f"/api/thumb/{ch}/{mid}").headers.get("X-Thumbnail-Status") != "pending"
    listed = c.get("/api/library").json()
    rows = next((v for v in listed.values() if isinstance(v, list)), [])
    assert [v.get("title") for v in rows] == ["Some Film (2019)"]
    (root / "Some Film (2019).mp4").unlink()                            # gone from the disk, gone from the library
    assert c.post(f"/api/local-folders/{sid}/scan", headers=H).status_code == 200
    for _ in range(100):
        with db.reading() as conn:
            if not conn.execute("SELECT 1 FROM media WHERE source_channel=?", (ch,)).fetchone():
                break
        time.sleep(0.05)
    with db.reading() as conn:
        assert not conn.execute("SELECT 1 FROM media WHERE source_channel=?", (ch,)).fetchone()


def test_nsfw_folders_are_not_listed_in_sfw(tmp_path):
    root = tmp_path / "Private"
    root.mkdir()
    db = Database(tmp_path / "h.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    assert c.post("/api/local-folders", json={"path": str(root), "content_rating": "nsfw"}, headers=H).status_code == 200
    assert [f["title"] for f in c.get("/api/local-folders").json()["folders"]] == ["Private"]
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    r = c.get("/api/local-folders").json()
    assert r["folders"] == [] and r["hidden"] == 1                      # neither its name nor its path is shown


def test_several_videos_are_prepared_at_once_and_all_of_them_finish(tmp_path):
    import threading
    from telegram_archive.engines import local_videos as lv
    root = tmp_path / "Many"
    root.mkdir()
    made = 0
    for i in range(4):
        made += 1 if _make_video(root / f"clip {i}.mp4", seconds=3) else 0
    if made < 4:
        pytest.skip("ffmpeg is not available")
    db = Database(tmp_path / "p.db")
    db.set_setting("local_prepare_workers", 3)
    c, H = _client(db)
    assert lv.workers_for(db) == 3
    db.set_setting("local_prepare_workers", 99)
    assert lv.workers_for(db) == 4                                      # never more than four at once
    db.set_setting("local_prepare_workers", 3)
    r = c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H)
    seen, lock, peak, now = set(), threading.Lock(), [0], [0]
    real = lv.build_snapshots

    def watched(*a, **k):
        with lock:
            now[0] += 1
            peak[0] = max(peak[0], now[0])
        try:
            time.sleep(0.2)                                             # long enough for the others to be running too
            return real(*a, **k)
        finally:
            with lock:
                now[0] -= 1
    lv.build_snapshots = watched
    try:
        sid = c.post("/api/local-folders", json={"path": str(root), "content_rating": "nsfw", "holds": "videos"}, headers=H).json()["id"]
        ch = ls.channel_id(sid)
        for _ in range(600):
            with db.reading() as conn:
                done = conn.execute("SELECT COUNT(*) FROM local_video_files WHERE channel_id=? AND snapshots=1", (ch,)).fetchone()[0]
            if done == 4:
                break
            time.sleep(0.1)
    finally:
        lv.build_snapshots = real
    assert done == 4 and peak[0] >= 2, (done, peak[0])                  # all four are ready, and more than one was being built at a time


def test_how_many_videos_are_prepared_at_once_can_be_chosen(tmp_path):
    db = Database(tmp_path / "w.db")
    c, H = _client(db)
    assert c.get("/api/local-folders/settings").json()["workers"] == 2
    assert c.post("/api/local-folders/settings", json={"workers": 3}, headers=H).json()["workers"] == 3
    assert c.get("/api/local-folders/settings").json()["workers"] == 3
    assert [c.post("/api/local-folders/settings", json={"workers": w}, headers=H).status_code for w in (0, 5, "many")] == [400, 400, 400]


def _prepared_folder(tmp_path, name="Movies", size="320x180", count=1, mode="nsfw"):
    """A folder with real videos, added and fully prepared (probe + snapshots)."""
    import subprocess
    root = tmp_path / name
    root.mkdir()
    for i in range(count):
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=15:duration=3", "-pix_fmt", "yuv420p", str(root / f"film {i}.mp4")], capture_output=True)
        if done.returncode != 0:
            pytest.skip("ffmpeg is not available")
    db = Database(tmp_path / "u.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    sid = c.post("/api/local-folders", json={"path": str(root), "content_rating": mode, "holds": "videos"}, headers=H).json()["id"]
    ch = ls.channel_id(sid)
    for _ in range(600):
        with db.reading() as conn:
            ready = conn.execute("SELECT COUNT(*) FROM local_video_files WHERE channel_id=? AND snapshots=1", (ch,)).fetchone()[0]
        if ready == count:
            break
        time.sleep(0.1)
    time.sleep(0.3)
    return db, c, H, sid, ch, root


def test_a_folder_video_is_made_ready_for_the_upload_job_so_it_builds_no_snapshots(tmp_path):
    from PIL import Image
    from telegram_archive.engines import local_upload_bridge as bridge
    from telegram_archive.engines import thumbnail_cache as tc
    from telegram_archive.metadata.fingerprint import oshash_file
    db, c, H, sid, ch, root = _prepared_folder(tmp_path, size="1280x720")
    with db.reading() as conn:
        mid = conn.execute("SELECT message_id FROM local_video_files WHERE channel_id=?", (ch,)).fetchone()[0]
    path = root / "film 0.mp4"
    from telegram_archive.engines import preview_prefetch as pp
    first = pp.preview_frame_path(int(ch), mid, 0)
    assert Image.open(first).width == 960                                        # folder frames are as wide as the upload job asks (they were 640)
    assert bridge.seed(db, ch, mid) == "seeded"
    fingerprint = oshash_file(str(path))
    assert tc.local_playback_snapshot_reusable_sync(db, path, fingerprint) is not None   # the upload job recognises them as ready: it would build nothing
    assert Image.open(tc.local_playback_snapshot_frames_path(path) / "frame_36.jpg").width == 960
    with db.reading() as conn:
        assert conn.execute("SELECT oshash FROM media WHERE source_channel=?", (ch,)).fetchone()[0] == fingerprint
    # frames from an older version (640 wide) are made wide first, then seeded
    for i in range(36):
        f = pp.preview_frame_path(int(ch), mid, i)
        Image.open(f).resize((640, 360)).save(f, quality=90)
    assert bridge.seed(db, ch, mid) == "rebuilt"
    assert Image.open(pp.preview_frame_path(int(ch), mid, 5)).width == 960
    assert tc.local_playback_snapshot_reusable_sync(db, path, fingerprint) is not None


def test_what_is_known_about_a_folder_video_goes_onto_its_telegram_copy(tmp_path):
    from telegram_archive.engines import local_upload_bridge as bridge
    from telegram_archive.engines import screen_identify as sid_
    db, c, H, sid, ch, root = _prepared_folder(tmp_path, mode="sfw")
    with db.reading() as conn:
        src = conn.execute("SELECT id,source_message_id FROM media WHERE source_channel=?", (ch,)).fetchone()
    sid_.apply_match(db, src["id"], {"kind": "movie", "tmdb_id": 603, "title": "The Matrix", "year": 1999, "release_date": "1999-03-30", "cast": [{"name": "Keanu Reeves", "tmdb_id": 6384}], "companies": ["Warner Bros."], "genres": ["Action"]}, None, chosen=True)
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1005','Films','available',1,'sfw','videos','2026-01-01')")
        db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(900,'-1005',77,'film 0.mp4','film 0','SCANNED')")
        db.conn.commit()
    assert bridge.carry_identity(db, root / "film 0.mp4", 900) is True
    with db.reading() as conn:
        m = conn.execute("SELECT title,release_date,identification_state FROM media WHERE id=900").fetchone()
        assert (m["title"], m["release_date"], m["identification_state"]) == ("The Matrix (1999)", "1999-03-30", "MATCHED")
        assert [r[0] for r in conn.execute("SELECT p.name FROM video_performers v JOIN performers p ON p.id=v.performer_id WHERE v.video_id=900")] == ["Keanu Reeves"]
        assert [r[0] for r in conn.execute("SELECT t.name FROM video_tags v JOIN tags t ON t.id=v.tag_id WHERE v.video_id=900")] == ["Action"]
        s = conn.execute("SELECT state,chosen,title_id FROM media_screen WHERE media_id=900").fetchone()
        assert (s["state"], s["chosen"]) == ("MATCHED", 1) and s["title_id"]
        rs = conn.execute("SELECT metadata_state,entity_state FROM resource_state WHERE media_id=900").fetchone()
        assert (rs["metadata_state"], rs["entity_state"]) == ("COMPLETE", "COMPLETE")        # the channel's own scan has nothing to identify
    assert bridge.carry_identity(db, tmp_path / "not from a folder.mp4", 900) is False
    assert bridge.carry_identity(db, root / "film 0.mp4", 0) is False


def test_uploading_a_folder_keeps_the_files_and_follows_the_safety_rules(tmp_path, monkeypatch):
    from telegram_archive.jobs.manager import JobManager
    submitted = []
    monkeypatch.setattr(JobManager, "submit", lambda self, jid: submitted.append(jid))
    monkeypatch.setattr(JobManager, "is_active", lambda self, kind: False)
    db, c, H, sid, ch, root = _prepared_folder(tmp_path, mode="sfw")
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1005','Films','available',1,'sfw','videos','2026-01-01')")
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1006','Adult','available',1,'nsfw','videos','2026-01-01')")
        db.conn.commit()
    url = f"/api/local-folders/{sid}/upload"
    assert c.post(url, json={"destination": "not a number"}, headers=H).status_code == 400
    assert c.post(url, json={"destination": "-4040"}, headers=H).status_code == 404                  # not a channel the app knows
    wrong = c.post(url, json={"destination": "-1006"}, headers=H)
    assert wrong.status_code == 409 and "holds one kind only" in wrong.json()["detail"] and not submitted   # an SFW folder does not go into an NSFW channel
    r = c.post(url, json={"destination": "-1005"}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert (out["seeded"], out["rebuilt"], out["skipped"], out["keep_files"]) == (1, 0, 0, True) and submitted == [out["job_id"]]
    with db.reading() as conn:
        job = conn.execute("SELECT job_type,destination_channel,detail FROM jobs WHERE id=?", (out["job_id"],)).fetchone()
    import json as _json
    detail = _json.loads(job["detail"])
    assert job["job_type"] == "UPLOAD_LOCAL" and job["destination_channel"] == "-1005"
    assert detail["delete_local_after_delivery"] is False and detail["folder"] == str(root) and detail["include_subfolders"] is True      # the files stay on this PC
    gone = c.post(url, json={"destination": "-1005", "keep_files": False}, headers=H).json()
    assert _json.loads(db.get_job(gone["job_id"])["detail"])["delete_local_after_delivery"] is True                                           # only when asked for
    assert c.post(f"/api/local-folders/{sid}", json={"excluded": [str(root / "x")]}, headers=H).status_code == 200
    with db.lock:
        db.conn.execute("UPDATE local_sources SET excluded='[\"sub\"]' WHERE id=?", (sid,))
        db.conn.commit()
    assert c.post(url, json={"destination": "-1005"}, headers=H).status_code == 409                    # left-out subfolders cannot be honoured by the upload yet


def test_snapshots_follow_the_file_a_replaced_video_gets_new_ones_and_removing_cleans_up(tmp_path):
    import subprocess
    from PIL import Image
    from telegram_archive.engines import preview_prefetch as pp
    db, c, H, sid, ch, root = _prepared_folder(tmp_path, size="320x180")
    with db.reading() as conn:
        mid = conn.execute("SELECT message_id FROM local_video_files WHERE channel_id=?", (ch,)).fetchone()[0]
    frames = pp.preview_frames_path(int(ch), mid)
    assert Image.open(frames / "frame_01.jpg").width == 320
    # the same file name now holds a different (wider, longer) video: the old snapshots must not stay
    done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=15:duration=4", "-pix_fmt", "yuv420p", str(root / "film 0.mp4")], capture_output=True)
    assert done.returncode == 0
    assert c.post(f"/api/local-folders/{sid}/scan", headers=H).status_code == 200
    for _ in range(600):
        with db.reading() as conn:
            row = conn.execute("SELECT snapshots,probed FROM local_video_files WHERE channel_id=?", (ch,)).fetchone()
        if row and row["snapshots"] == 1 and row["probed"] == 1 and Image.open(frames / "frame_01.jpg").width == 640:
            break
        time.sleep(0.1)
    assert Image.open(frames / "frame_01.jpg").width == 640                   # new content, new frames
    assert c.delete(f"/api/local-folders/{sid}", headers=H).status_code == 200
    assert not frames.exists()                                                 # nothing left on the disk
    with db.reading() as conn:
        assert conn.execute("SELECT COUNT(*) FROM timeline_preview_cache WHERE source_channel=?", (ch,)).fetchone()[0] == 0


def test_each_mode_lists_only_its_own_folders(tmp_path):
    a, b = tmp_path / "Films", tmp_path / "Private"
    a.mkdir(); b.mkdir()
    db = Database(tmp_path / "m.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    assert c.post("/api/local-folders", json={"path": str(a), "content_rating": "sfw"}, headers=H).status_code == 200
    assert c.post("/api/local-folders", json={"path": str(b), "content_rating": "nsfw"}, headers=H).status_code == 200
    r = c.get("/api/local-folders").json()
    assert [f["title"] for f in r["folders"]] == ["Private"] and r["hidden"] == 1      # NSFW does not list the SFW folder
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    r = c.get("/api/local-folders").json()
    assert [f["title"] for f in r["folders"]] == ["Films"] and r["hidden"] == 1


def test_a_scan_in_progress_shows_as_a_lane_with_time_and_a_guess(tmp_path):
    from telegram_archive.engines import local_videos as lv
    db = Database(tmp_path / "l.db")
    c, H = _client(db)
    sid = c.post("/api/local-folders", json={"path": str(tmp_path), "content_rating": "nsfw"}, headers=H)
    sid = sid.json()["id"] if sid.status_code == 200 else None
    if sid is None:
        assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
        sid = c.post("/api/local-folders", json={"path": str(tmp_path), "content_rating": "nsfw"}, headers=H).json()["id"]
    time.sleep(0.5)
    lv._state[sid] = {"running": True, "total": 10, "done": 4, "failed": 0, "workers": 2, "started": time.time() - 40}
    try:
        rows = [r for r in c.get("/api/telegram-transfer/lanes").json()["jobs"] if r["type"] == "LOCAL_SCAN"]
        assert len(rows) == 1
        row = rows[0]
        assert (row["completed"], row["total"], row["progress"]) == (4, 10, 40.0)
        assert 35 <= row["metrics"]["elapsed_s"] <= 50 and 50 <= row["metrics"]["eta_s"] <= 75      # 40 s for 4, so about 60 s for the other 6
    finally:
        lv._state.pop(sid, None)


def test_snapshots_can_be_made_on_request_only_for_videos_on_this_pc(tmp_path, folder):
    db = Database(tmp_path / "a.db")
    c, H = _client(db)
    assert c.post("/api/play/make-snapshots/-1001234/5", headers=H).status_code == 400            # a Telegram video: Metadata & Assets makes those
    assert c.post(f"/api/play/make-snapshots/{ls.channel_id(1)}/5", headers=H).status_code == 404   # no such file on this PC


def test_a_metadata_scan_is_a_lane_with_progress_and_says_why_it_failed(tmp_path, folder, monkeypatch):
    import urllib.error
    from telegram_archive.engines import local_identify_adult as lia
    from telegram_archive.engines import local_videos
    db = Database(tmp_path / "a.db")
    sid = make_source(db, folder)
    ch = ls.channel_id(sid)
    clips = []
    for n in range(3):
        f = folder / f"clip{n}.mp4"
        f.write_bytes(b"v" * 200_000)
        clips.append(str(f))
    local_videos.index_videos(db, ch, clips)
    db.set_setting("stashdb_api_key", "k")

    async def reset(*_a, **_k):
        raise urllib.error.URLError(ConnectionResetError(10054, "An existing connection was forcibly closed by the remote host"))
    monkeypatch.setattr(lia, "_lookup", reset)
    out = lia.run(db, ch)
    assert out["failed"] == 3 and "closed the connection" in out["reason"]
    rows = [r for r in ls.lane_rows(db) if r["id"] == "identify:nsfw"]
    assert len(rows) == 1                                                # shown after it ended, so the failure is seen
    r = rows[0]
    assert r["status"] == "COMPLETED_WITH_ERRORS" and r["source_channel"] == "Pictures" and (r["completed"], r["total"]) == (3, 3)
    assert "3 could not be checked" in r["operation"] and "closed the connection" in r["operation"]
    monkeypatch.setattr(lia, "_state", {**lia._state, "finished": 1.0})  # long ago
    assert not [x for x in ls.lane_rows(db) if x["id"] == "identify:nsfw"]


def test_the_automatic_recheck_does_not_ask_again_for_videos_the_scene_databases_did_not_know(tmp_path, folder, monkeypatch):
    """The card on Transfers that never went away: every 15 minutes all the unmatched videos were hashed and asked about again, and each run kept its card for ten more minutes."""
    from telegram_archive.engines import local_identify_adult as lia
    from telegram_archive.engines import local_videos
    db = Database(tmp_path / "a.db")
    ch = ls.channel_id(make_source(db, folder))
    clips = []
    for n in range(2):
        f = folder / f"c{n}.mp4"
        f.write_bytes(b"v" * 200_000)
        clips.append(str(f))
    local_videos.index_videos(db, ch, clips)
    db.set_setting("stashdb_api_key", "k")
    lia._tried.clear()
    asked = []

    async def nothing(_db, file_hash):
        asked.append(file_hash)
        return None
    monkeypatch.setattr(lia, "_lookup", nothing)
    lia.run(db, ch, retry=False)
    assert len(asked) == 2
    before = dict(lia.progress())
    lia.run(db, ch, retry=False)                      # the next automatic check: nothing new, nothing asked, no new card
    assert len(asked) == 2 and lia.progress() == before
    lia.run(db, ch)                                   # a person's scan asks again
    assert len(asked) == 4


def test_a_folder_being_prepared_in_one_library_does_not_block_the_same_folder_number_in_another(tmp_path, monkeypatch):
    """The CI failure of 19 November: a run left over from an earlier test held folder number 1, so the next one returned at once and nothing was probed."""
    from telegram_archive.engines import local_videos as lv
    clip = tmp_path / "one.mp4"
    if not _make_video(clip, seconds=2):
        pytest.skip("ffmpeg is not available")
    ch = ls.channel_id(1)
    first, second = Database(tmp_path / "a.db"), Database(tmp_path / "b.db")
    lv.index_videos(first, ch, [str(clip)])
    lv.index_videos(second, ch, [str(clip)])
    release, started, seen = threading.Event(), threading.Event(), []

    def prepare(db, channel, item):
        seen.append(id(db))
        if db is first:
            started.set()
            release.wait(30)
        return True
    monkeypatch.setattr(lv, "_prepare_one", prepare)
    t = threading.Thread(target=lv.process, args=(first, ch), daemon=True)
    t.start()
    assert started.wait(30)                    # the first library is busy with its folder 1
    try:
        lv.process(second, ch)                 # the second library has a folder 1 as well, and must be prepared
        assert id(second) in seen
    finally:
        release.set()
        t.join(30)


def test_a_moved_or_renamed_video_keeps_its_row_and_a_deleted_one_goes(tmp_path):
    """Moving a file inside its folder (or renaming it) is the same video: its id, so its name from the movie database, favourite and snapshots, stays.
    A file that is gone from the disk is removed on the next scan."""
    root = tmp_path / "Movies"
    root.mkdir()
    (root / "a.mp4").write_bytes(b"a" * 300)
    (root / "b.mp4").write_bytes(b"b" * 500)            # different sizes, so each file is told apart
    db = Database(tmp_path / "m.db")
    sid = make_source(db, root)
    ch = ls.channel_id(sid)

    def rows():
        with db.reading() as c:
            return {os.path.basename(r["path"]): int(r["message_id"]) for r in c.execute("SELECT path,message_id FROM local_video_files WHERE channel_id=?", (ch,))}
    ls.scan_source(db, sid)
    first = rows()
    assert set(first) == {"a.mp4", "b.mp4"}
    (root / "sub").mkdir()
    os.replace(root / "a.mp4", root / "sub" / "a renamed.mp4")   # moved and renamed in one go
    (root / "b.mp4").unlink()                                    # deleted
    result = ls.scan_source(db, sid)
    after = rows()
    assert after == {"a renamed.mp4": first["a.mp4"]}            # the same id, at its new place; the deleted one is gone
    with db.reading() as c:
        assert c.execute("SELECT COUNT(*) FROM media WHERE source_channel=?", (ch,)).fetchone()[0] == 1
    assert result["videos"] == 1


def test_a_moved_photo_keeps_its_row_so_the_people_it_was_named_after_stay(tmp_path):
    root = tmp_path / "Pics"
    png(root / "one.png", (40, 30))
    png(root / "two.png", (90, 70), (10, 200, 10))
    db = Database(tmp_path / "p.db")
    sid = make_source(db, root)
    ch = ls.channel_id(sid)
    ls.scan_source(db, sid)

    def ids():
        with db.reading() as c:
            return {os.path.basename(r["local_path"]): int(r["message_id"]) for r in c.execute("SELECT local_path,message_id FROM photo_library_items WHERE channel_id=?", (ch,))}
    first = ids()
    (root / "album").mkdir()
    os.replace(root / "one.png", root / "album" / "holiday.png")
    (root / "two.png").unlink()
    result = ls.scan_source(db, sid)
    assert ids() == {"holiday.png": first["one.png"]}      # same row, new place; the deleted photo is gone
    assert result["added"] == 0 and result["removed"] == 1 and result["moved"] == 1
