"""The app login can be switched off: this PC then gets a session without typing, any other caller still has to sign in."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database

PW = "correct-horse-battery"
H = {"Origin": "https://testserver"}


def _app(tmp_path):
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    db = Database(tmp_path / "l.db")
    ensure_web_credentials(db)
    return db, create_app(db=db)


def _client(app, host="127.0.0.1"):
    from fastapi.testclient import TestClient
    return TestClient(app, base_url="https://testserver", client=(host, 5000))


def test_switching_the_login_off_needs_the_password_and_then_opens_this_pc_only(tmp_path):
    db, app = _app(tmp_path)
    c = _client(app)
    c.post("/api/auth/setup", json={"username": "admin", "password": PW, "confirmation": PW}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": PW}, headers=H).status_code == 200
    assert c.get("/api/login-hint").json()["login_required"] is True
    assert _client(app).post("/api/auth/open", headers=H).status_code == 403              # the login is on: nothing opens by itself
    assert c.post("/api/settings/weblogin", json={"required": False, "current_password": "wrong password!"}, headers=H).status_code == 403
    assert c.post("/api/settings/weblogin", json={"required": False, "current_password": PW}, headers=H).json()["ok"] is True
    assert c.get("/api/login-hint").json()["login_required"] is False
    here = _client(app)                                                                   # a new window of this PC, no session yet
    assert here.get("/api/content-mode").status_code == 401
    assert here.post("/api/auth/open", headers=H).status_code == 200
    assert here.get("/api/content-mode").status_code == 200                               # a normal session now
    assert _client(app, host="10.0.0.7").post("/api/auth/open", headers=H).status_code == 403   # another computer still signs in
    assert c.post("/api/settings/weblogin", json={"required": True, "current_password": PW}, headers=H).json()["ok"] is True
    assert _client(app).post("/api/auth/open", headers=H).status_code == 403              # and back on
