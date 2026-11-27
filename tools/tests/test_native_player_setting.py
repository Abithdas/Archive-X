"""The Experience setting that decides when the desktop app's native player plays a video."""
import pytest
from fastapi.testclient import TestClient

from telegram_archive.core.database import Database

H = {"Origin": "https://testserver"}


@pytest.fixture()
def client(tmp_path):
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    db = Database(tmp_path / "t.db")
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 50000))
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    return c, db


def test_the_default_is_a_button_only_never_automatic(client):
    c, _ = client
    assert c.get("/api/settings/experience", headers=H).json()["native_player"] == "off"


@pytest.mark.parametrize("mode", ["off", "always", "fallback"])
def test_each_mode_is_saved_and_read_back(client, mode):
    c, db = client
    assert c.post("/api/settings/experience", json={"native_player": mode}, headers=H).status_code == 200
    assert c.get("/api/settings/experience", headers=H).json()["native_player"] == mode
    assert db.setting("ta_native_player") == mode


def test_a_mode_it_does_not_know_is_refused_and_nothing_changes(client):
    c, _ = client
    c.post("/api/settings/experience", json={"native_player": "always"}, headers=H)
    assert c.post("/api/settings/experience", json={"native_player": "sometimes"}, headers=H).status_code == 400
    assert c.get("/api/settings/experience", headers=H).json()["native_player"] == "always"


def test_a_damaged_stored_value_reads_as_the_default(client):
    c, db = client
    db.set_setting("ta_native_player", "banana")
    assert c.get("/api/settings/experience", headers=H).json()["native_player"] == "off"


def test_other_experience_settings_do_not_touch_it(client):
    c, _ = client
    c.post("/api/settings/experience", json={"native_player": "always"}, headers=H)
    c.post("/api/settings/experience", json={"autoplay": False}, headers=H)
    assert c.get("/api/settings/experience", headers=H).json()["native_player"] == "always"
