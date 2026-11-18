"""A playback session is listed as running only while it is streaming; an open but idle one is IDLE, and the status carries the app version."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.web.routes import PLAYBACK_IDLE_SECONDS, _playback_flowing  # noqa: E402

H = {"Origin": "https://testserver"}


def engine(served=0, active=0):
    return SimpleNamespace(bytes_served=served, active_http_requests=active)


def test_bytes_flowing_means_playing_and_silence_for_a_while_means_idle():
    seen = {"bytes": -1, "at": 0.0}
    eng = engine(served=1000)
    assert _playback_flowing(eng, seen, now=100.0) is True                          # first sight: something was served
    assert _playback_flowing(eng, seen, now=100.0 + PLAYBACK_IDLE_SECONDS - 1) is True
    assert _playback_flowing(eng, seen, now=100.0 + PLAYBACK_IDLE_SECONDS + 1) is False   # nothing since: paused, ended or abandoned
    eng.bytes_served = 5000
    assert _playback_flowing(eng, seen, now=200.0) is True                          # playing again
    assert _playback_flowing(eng, seen, now=200.0 + PLAYBACK_IDLE_SECONDS + 1) is False


def test_an_open_request_counts_as_streaming_even_when_no_new_bytes_arrived_yet():
    seen = {"bytes": -1, "at": 0.0}
    eng = engine(served=10, active=1)
    assert _playback_flowing(eng, seen, now=10.0) is True
    assert _playback_flowing(eng, seen, now=10.0 + PLAYBACK_IDLE_SECONDS * 3) is True
    eng.active_http_requests = 0
    assert _playback_flowing(eng, seen, now=10.0 + PLAYBACK_IDLE_SECONDS * 3 + 1) is True    # the request just ended: it only now starts the clock
    assert _playback_flowing(eng, seen, now=10.0 + PLAYBACK_IDLE_SECONDS * 5) is False


def test_an_engine_without_counters_is_idle_after_the_window():
    seen = {"bytes": -1, "at": 0.0}
    assert _playback_flowing(SimpleNamespace(), seen, now=1.0) is True
    assert _playback_flowing(SimpleNamespace(), seen, now=1.0 + PLAYBACK_IDLE_SECONDS + 1) is False


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
    return c


def test_the_status_carries_the_app_version(client):
    from telegram_archive import config
    out = client.get("/api/status", headers=H).json()
    assert out["version"] == config.APP_VERSION and out["version"]
