"""The Skip button's times: TheIntroDB for movies and series, AniSkip for anime (found through Kitsu), SFW only, and never a reason for the player to fail."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from telegram_archive.engines import screen_identify as sid
from telegram_archive.engines import skip_times as st
from telegram_archive.core.database import Database
import tools.tests.test_screen_home as th


@pytest.fixture(autouse=True)
def _fresh():
    st._cache.clear()
    yield
    st._cache.clear()


def _fake(seen, answers):
    def fetch(url, params, headers):
        seen.append((url, dict(params)))
        for needle, answer in answers.items():
            if needle in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return {}
    return fetch


def test_a_series_episode_asks_theintrodb_by_imdb_number_season_and_episode(monkeypatch):
    seen = []
    monkeypatch.setattr(sid, "http_fetch", _fake(seen, {"theintrodb": {"intro": [{"start_ms": 12000, "end_ms": 72500}], "credits": [{"start_ms": 2400000, "end_ms": None}], "recap": [{"start_ms": 1000, "end_ms": 1500}]}}))
    got = st.lookup("tt0903747", "series", 1, 2)
    assert got == [{"type": "intro", "start": 12.0, "end": 72.5}, {"type": "credits", "start": 2400.0, "end": None}]   # the half-second recap is a blip and is dropped
    assert seen == [("https://api.theintrodb.org/v3/media", {"imdb_id": "tt0903747", "season": 1, "episode": 2})]
    st.lookup("tt0903747", "series", 1, 2)
    assert len(seen) == 1                                                       # asked once: the answer is kept


def test_a_movie_by_tmdb_number_has_no_season_and_an_empty_answer_is_kept_too(monkeypatch):
    seen = []
    monkeypatch.setattr(sid, "http_fetch", _fake(seen, {"theintrodb": {}}))
    assert st.lookup("tmdb:603", "movie") == [] and st.lookup("tmdb:603", "movie") == []
    assert seen == [("https://api.theintrodb.org/v3/media", {"tmdb_id": "603"})]


def test_an_anime_episode_goes_kitsu_to_myanimelist_to_aniskip(monkeypatch):
    seen = []
    monkeypatch.setattr(sid, "http_fetch", _fake(seen, {"kitsu.app": {"data": [{"attributes": {"externalId": "21"}}]},
                                                      "aniskip": {"results": [{"skipType": "op", "interval": {"startTime": 5, "endTime": 95}}, {"skipType": "ed", "interval": {"startTime": 1300, "endTime": 1390}}, {"skipType": "nope", "interval": {"startTime": 1, "endTime": 50}}]}}))
    assert st.lookup("kitsu:12", "series", 1, 3) == [{"type": "intro", "start": 5.0, "end": 95.0}, {"type": "credits", "start": 1300.0, "end": 1390.0}]
    assert "/21/3?" in seen[1][0]


@pytest.mark.parametrize("ident,kind,season,episode", [("nope", "movie", None, None), ("tt1", "series", None, None), ("tt1", "series", 1, 0), ("../x", "movie", None, None)])
def test_what_cannot_be_asked_is_not_asked(monkeypatch, ident, kind, season, episode):
    seen = []
    monkeypatch.setattr(sid, "http_fetch", _fake(seen, {}))
    assert st.lookup(ident, kind, season, episode) == [] and seen == []


def test_a_provider_that_is_down_gives_nothing_and_is_asked_again_next_time(monkeypatch):
    seen = []
    monkeypatch.setattr(sid, "http_fetch", _fake(seen, {"theintrodb": RuntimeError("down")}))
    assert st.lookup("tt1", "movie") == [] and st.lookup("tt1", "movie") == []
    assert len(seen) == 2


def test_the_routes_answer_for_a_matched_video_and_for_an_addon_title_in_sfw_and_refuse_in_nsfw(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(sid, "http_fetch", _fake(seen, {"theintrodb": {"intro": [{"start_ms": 1000, "end_ms": 61000}]}}))
    db = Database(tmp_path / "s.db")
    th._library(db)
    c = th._client(db)
    cid = "-9000000000001"
    got = c.get(f"/api/screen/skip-times/{cid}/2").json()
    assert got == {"segments": [{"type": "intro", "start": 1.0, "end": 61.0}]}
    assert seen[0][1] == {"imdb_id": "tt0903747", "season": 1, "episode": 1}
    assert c.get(f"/api/screen/skip-times/{cid}/999").json() == {"segments": []}            # not a video of the library
    assert c.get("/api/screen/addon-skip-times?kind=movie&ident=tt0133093").json()["segments"][0]["end"] == 61.0
    assert c.get("/api/screen/addon-skip-times?kind=movie&ident=bad").status_code == 400
    c.post("/api/content-mode", json={"mode": "nsfw"}, headers=th.H)
    assert c.get(f"/api/screen/skip-times/{cid}/2").status_code == 409
    assert c.get("/api/screen/addon-skip-times?kind=movie&ident=tt0133093").status_code == 409
