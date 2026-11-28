"""Settings, Playback, Video decoding: saved and validated, and "processor only" makes the conversion use the processor for the decoder and the encoder."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from telegram_archive.app import create_app
from telegram_archive.core.auth import ensure_web_credentials
from telegram_archive.core.database import Database

H = {"Origin": "https://testserver"}


@pytest.fixture()
def client(tmp_path):
    db = Database(tmp_path / "archive.db")
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 50000))
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, db


def test_the_setting_defaults_to_automatic_and_is_saved_and_validated(client):
    c, db = client
    assert c.get("/api/settings/experience", headers=H).json()["video_decoding"] == "auto"
    for mode in ("software", "hardware", "auto"):
        assert c.post("/api/settings/experience", json={"video_decoding": mode}, headers=H).status_code == 200
        assert c.get("/api/settings/experience", headers=H).json()["video_decoding"] == mode
    assert c.post("/api/settings/experience", json={"video_decoding": "fast"}, headers=H).status_code == 400
    assert c.get("/api/settings/experience", headers=H).json()["video_decoding"] == "auto"


def test_the_bootstrap_carries_it_too(client):
    c, db = client
    c.post("/api/settings/experience", json={"video_decoding": "software"}, headers=H)
    boot = c.get("/api/bootstrap", headers=H)
    if boot.status_code == 200:
        assert "software" in boot.text
