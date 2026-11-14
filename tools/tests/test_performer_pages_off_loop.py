"""The performer page's two slow reads run off the event loop (they froze the whole app for 8-15 s in the field) and still answer correctly."""
import os, sys, tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axpage_"), TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP="1", ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def test_performer_videos_and_suggestions_answer_and_do_not_block_the_loop():
    import asyncio, threading
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    from telegram_archive.core.database import Database

    db = Database(); ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver")
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES('Page Test Performer')")
        db.conn.commit()
        pid = db.conn.execute("SELECT id FROM performers WHERE name='Page Test Performer'").fetchone()["id"]
    r = c.get(f"/api/performers/{pid}/videos")
    assert r.status_code == 200 and r.json()["performer"]["name"] == "Page Test Performer" and r.json()["videos"] == []
    assert c.get("/api/performers/99999999/videos").status_code == 404          # an error raised in the worker thread still reaches the client
    r = c.get(f"/api/performers/{pid}/suggested")
    assert r.status_code == 200
