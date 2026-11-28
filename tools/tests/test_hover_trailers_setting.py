"""Settings, Playback, Trailers in expanded cards: on by default, saved, and the same in the settings bootstrap."""
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


def test_trailers_in_cards_are_on_by_default_and_can_be_turned_off_and_on(client):
    c, _ = client
    assert c.get("/api/settings/experience", headers=H).json()["hover_trailers"] is True
    for value in (False, True, False):
        assert c.post("/api/settings/experience", json={"hover_trailers": value}, headers=H).status_code == 200
        assert c.get("/api/settings/experience", headers=H).json()["hover_trailers"] is value
