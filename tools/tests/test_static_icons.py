"""The app's own logo comes from the app; the other icons can still be replaced from the data folder."""
from pathlib import Path

from telegram_archive import config
from telegram_archive.app import create_app
from telegram_archive.core.database import Database

BUNDLED = Path(__file__).resolve().parents[2] / "telegram_archive" / "web" / "static" / "icons"


def test_an_old_logo_kept_in_the_data_folder_does_not_replace_the_new_one(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    icons = tmp_path / "icons"
    icons.mkdir()
    (icons / "archive-x.png").write_bytes(b"OLD LOGO")       # left there by an older version
    (icons / "vlc.png").write_bytes(b"MY OWN VLC ICON")      # a deliberate replacement
    monkeypatch.setattr(config, "ICON_DIR", icons)
    c = TestClient(create_app(db=Database(tmp_path / "i.db")), base_url="https://testserver", client=("127.0.0.1", 5000))
    assert c.get("/static/icons/archive-x.png").content == (BUNDLED / "archive-x.png").read_bytes()
    assert c.get("/static/icons/vlc.png").content == b"MY OWN VLC ICON"      # the other icons keep working as before
