"""Changing a performer's picture must not clear their gender (the list's default filter is Female, so the card would vanish)."""
import os, sys, tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axgender_"), TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP="1", ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)


def test_edit_form_reports_gender_so_saving_keeps_it():
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    from telegram_archive.core.database import Database

    db = Database(); ensure_web_credentials(db)
    app = create_app(db=db)
    c = TestClient(app, base_url="https://testserver")
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    assert c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H).status_code == 200
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    with db.lock:
        db.conn.execute("INSERT INTO performers(name,gender) VALUES('Ava Stone','female')")
        db.conn.commit()
        pid = db.conn.execute("SELECT id FROM performers WHERE name='Ava Stone'").fetchone()["id"]
    form = c.get(f"/api/performers/{pid}/edit").json()
    assert form["gender"] == "female"
    r = c.post(f"/api/performers/{pid}/edit", headers=H, json={"name": "Ava Stone", "image_url": "https://example.com/a.jpg", "gender": form["gender"]})
    assert r.status_code == 200, r.text
    assert r.json()["performer"]["gender"] == "female"
