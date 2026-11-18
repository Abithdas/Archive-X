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

    db = Database(); ensure_web_credentials(db); db.set_setting("content_mode", "nsfw")   # these tests make adult performers; the app opens in SFW
    app = create_app(db=db)
    c = TestClient(app, base_url="https://testserver")
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    assert c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H).status_code in (200, 409)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO performers(name,gender) VALUES('Ava Stone','female')")
        db.conn.commit()
        pid = db.conn.execute("SELECT id FROM performers WHERE name='Ava Stone'").fetchone()["id"]
    form = c.get(f"/api/performers/{pid}/edit").json()
    assert form["gender"] == "female"
    r = c.post(f"/api/performers/{pid}/edit", headers=H, json={"name": "Ava Stone", "image_url": "https://example.com/a.jpg", "gender": form["gender"]})
    assert r.status_code == 200, r.text
    assert r.json()["performer"]["gender"] == "female"


def test_local_performer_search_accepts_words_in_any_order_and_partial_words():
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    from telegram_archive.core.database import Database

    db = Database(); ensure_web_credentials(db); db.set_setting("content_mode", "nsfw")   # these tests make adult performers; the app opens in SFW
    c = TestClient(create_app(db=db), base_url="https://testserver")
    H = {"Origin": "https://testserver"}
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    c.post("/login", json={"username": "admin", "password": pw}, headers=H)
    with db.lock:
        for n in ("Ava Stonebridge", "Mia Rivera"):
            db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES(?)", (n,))
        db.conn.commit()
    def names(q): return [i["name"] for i in c.get("/api/photos/faces/performers", params={"q": q}).json()["items"]]
    assert names("ava stoneb") == ["Ava Stonebridge"]
    assert names("stonebridge ava") == ["Ava Stonebridge"]
    assert names("riv mi") == ["Mia Rivera"]
    assert names("zzz") == []


def test_internal_provider_keys_are_for_the_front_door_only(monkeypatch):
    monkeypatch.setenv("ARCHIVE_X_PROXY_KEY", "0123456789abcdef0123456789abcdef")
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.database import Database
    db = Database()
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    assert c.get("/internal/provider-keys").status_code == 404
    assert c.get("/internal/provider-keys", headers={"x-archive-x-internal": "wrong"}).status_code == 404
    r = c.get("/internal/provider-keys", headers={"x-archive-x-internal": "0123456789abcdef0123456789abcdef"})
    assert r.status_code == 200 and set(r.json()) == {"stashdb", "tpdb", "data_dir", "db_path"}
    remote = TestClient(create_app(db=db), base_url="https://testserver", client=("203.0.113.9", 5000))
    assert remote.get("/internal/provider-keys", headers={"x-archive-x-internal": "0123456789abcdef0123456789abcdef"}).status_code == 404
