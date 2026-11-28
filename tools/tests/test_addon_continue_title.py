"""Continue watching for a stream played from an add-on (nothing downloaded): its card carries the title's own name and picture, never the stream's release file name."""
import pytest
from fastapi.testclient import TestClient

from telegram_archive.app import create_app
from telegram_archive.core.auth import ensure_web_credentials
from telegram_archive.core.database import Database

H = {"Origin": "https://testserver"}
RELEASE = "Rl.Tkn.2024.1080p.WEBRip.x264-GRP.mkv"


@pytest.fixture()
def client(tmp_path):
    db = Database(tmp_path / "archive.db")
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 50000))
    pw = "correct-horse-battery"
    c.post("/api/auth/setup", json={"username": "admin", "password": pw, "confirmation": pw}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": pw}, headers=H).status_code == 200
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    return c


def save(c, **kw):
    body = {"kind": "movie", "ident": "tt0133093", "name": RELEASE, "position": 1800, "duration": 8160, **kw}
    assert c.post("/api/screen/addon-progress", json=body, headers=H).status_code == 200


def card(c):
    return next(x for x in c.get("/api/screen/home", headers=H).json()["continue"] if x.get("addon"))


def test_the_title_and_picture_sent_with_the_progress_are_what_the_card_shows(client):
    save(client, title="The Matrix", poster="https://image.tmdb.org/t/p/w500/matrix.jpg")
    got = card(client)
    assert got["title"] == "The Matrix" and got["image"] == "https://image.tmdb.org/t/p/w500/matrix.jpg"
    assert RELEASE not in str(got)


def test_a_later_save_without_them_keeps_what_was_known(client):
    save(client, title="The Matrix", poster="https://image.tmdb.org/t/p/w500/matrix.jpg")
    save(client, position=2400)                                         # an older page: no title, no picture
    got = card(client)
    assert got["title"] == "The Matrix" and got["image"].endswith("matrix.jpg")


def test_with_no_title_known_the_release_name_is_cleaned_not_shown_raw(client):
    save(client)
    assert card(client)["title"] == "Rl Tkn"
    save(client, kind="series", ident="tt0903747", season=1, episode=2, name="BB.S01E02.720p.HDTV.x264-XYZ.mp4")
    assert {x["title"] for x in client.get("/api/screen/home", headers=H).json()["continue"] if x.get("addon")} == {"Rl Tkn", "BB"}


def test_only_an_https_picture_is_kept(client):
    save(client, title="The Matrix", poster="javascript:alert(1)")
    assert card(client)["image"] == ""
