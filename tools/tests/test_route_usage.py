import json, os, sys, tempfile
from pathlib import Path

root = str(Path(__file__).resolve().parents[2])
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axusage_"), TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP="1", ARCHIVE_X_AUTO_OPEN_BROWSER="0")
sys.path.insert(0, root)
from telegram_archive.core.route_usage import RouteUsage


def test_counts_are_kept_per_route_template_and_survive_a_restart(tmp_path):
    f = tmp_path / "usage.json"
    u = RouteUsage(f)
    for _ in range(3):
        u.hit("GET", "/api/performers/{performer_id}/videos")
    u.hit("POST", "/api/play/stop")
    assert u.snapshot()["routes"]["GET /api/performers/{performer_id}/videos"]["count"] == 3
    u.flush()
    again = RouteUsage(f)
    again.hit("POST", "/api/play/stop")
    assert again.snapshot()["routes"]["POST /api/play/stop"]["count"] == 2
    assert again.snapshot()["routes"]["GET /api/performers/{performer_id}/videos"]["count"] == 3
    assert "counting_since" in again.snapshot()


def test_a_damaged_file_starts_fresh_instead_of_failing(tmp_path):
    f = tmp_path / "usage.json"
    f.write_text("{not json")
    assert RouteUsage(f).snapshot()["routes"] == {}


def test_the_app_counts_the_route_that_answered_and_nothing_about_the_request():
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.database import Database
    app = create_app(db=Database())
    c = TestClient(app, base_url="https://testserver")
    c.get("/health")
    c.get("/health")
    c.get("/api/performers/123/videos")                          # no login: still a call to that route (it answers 401)
    routes = app.state.route_usage.snapshot()["routes"]
    assert routes["GET /health"]["count"] == 2
    assert any(k.startswith("GET /api/performers/{performer_id}/videos") for k in routes)
    assert not any("123" in key for key in routes)   # the keys only: the timestamps in the values can contain any digits
