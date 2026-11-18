"""The Library's channel list: a channel the current mode hides must not come back as an "Unknown channel"."""
import os
import sys
import tempfile

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axchan_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from telegram_archive.core.database import Database  # noqa: E402

from test_local_folders import _client  # noqa: E402


def _seed(tmp_path):
    db = Database(tmp_path / "c.db")
    with db.lock:
        for tid, title, rating, in_lib in (("-1001", "Adult one", "nsfw", 1), ("-1002", "Films", "sfw", 1), ("-1003", "Not in library yet", "nsfw", 0)):
            db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES(?,?,'available',?,?,'both','2026-01-01')", (tid, title, in_lib, rating))
        # the document registry knows every one of them, plus a channel that has no row at all (-1004)
        for n, ch in enumerate(("-1001", "-1002", "-1003", "-1004")):
            db.conn.execute("INSERT INTO telegram_document_registry(registry_key,filename,size_bytes,created_at,updated_at) VALUES(?,?,10,'x','x')", (f"k{n}", f"f{n}.mp4"))
            db.conn.execute("INSERT INTO telegram_document_locations(channel_id,message_id,registry_key,location_state,last_seen_at) VALUES(?,1,?,'PRESENT','x')", (ch, f"k{n}"))
        db.conn.commit()
    return db


def test_sfw_lists_neither_adult_nor_unlisted_channels_as_unknown(tmp_path):
    c, _ = _client(_seed(tmp_path))
    titles = sorted(r["title"] for r in c.get("/api/channels").json())
    assert "Unknown channel" not in titles, titles
    assert "Adult one" not in titles and "Not in library yet" not in titles


def test_nsfw_lists_only_a_channel_the_database_has_no_row_for(tmp_path):
    c, H = _client(_seed(tmp_path))
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    rows = c.get("/api/channels").json()
    unknown = [r for r in rows if r["title"] == "Unknown channel"]
    assert [r["telegram_id"] for r in unknown] == ["-1004"] and unknown[0]["unnamed"] is True
    assert not {"Films", "Not in library yet"} & {r["title"] for r in rows}      # a channel of the other kind, or outside the library, is not an unknown one either


def test_a_local_folder_takes_its_folders_name_and_a_gone_folder_is_not_listed(tmp_path):
    from telegram_archive.engines import local_scanner as ls
    db = _seed(tmp_path)
    with db.lock:
        db.conn.execute("INSERT INTO local_sources(id,path,created_at) VALUES(7,?,'x')", (str(tmp_path / "My Films"),))
        for sid in (7, 8):    # 7 has a folder row, 8 does not (removed, or never saved)
            ch = ls.channel_id(sid)
            db.conn.execute("INSERT INTO telegram_document_registry(registry_key,filename,size_bytes,created_at,updated_at) VALUES(?,?,10,'x','x')", (f"l{sid}", "f.mp4"))
            db.conn.execute("INSERT INTO telegram_document_locations(channel_id,message_id,registry_key,location_state,last_seen_at) VALUES(?,1,?,'PRESENT','x')", (ch, f"l{sid}"))
        db.conn.commit()
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    by_id = {r["telegram_id"]: r["title"] for r in c.get("/api/channels").json()}
    assert by_id.get(ls.channel_id(7)) == "My Films"
    assert ls.channel_id(8) not in by_id
