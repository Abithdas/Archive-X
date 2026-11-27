"""When TMDB cannot be reached (some networks block it), the other providers are still asked; only when nobody could answer is the error raised."""
import pytest

from telegram_archive.metadata import screen_ids as si


def make(fetch, **kw):
    return si.Screen(fetch, **kw)


PARSED = {"kind": "movie", "title": "The Matrix", "year": 1999, "ids": {}}


def test_a_blocked_tmdb_falls_through_to_cinemeta():
    asked = []

    def fetch(url, params, headers):
        asked.append(url)
        if url.startswith(si.TMDB):
            raise ConnectionError("The provider (TMDB) closed the connection")
        return {"metas": [{"imdb_id": "tt0133093", "name": "The Matrix", "releaseInfo": "1999", "poster": "p", "description": "Neo."}]}
    found = make(fetch, tmdb="k" * 32, cinemeta=True).search(PARSED)
    assert found and found[0]["source"] == "cinemeta" and found[0]["title"] == "The Matrix"
    assert sum(1 for u in asked if u.startswith(si.TMDB)) == 1          # one failed attempt per video, not one per query variant


def test_the_error_is_raised_only_when_every_provider_failed():
    def fetch(url, params, headers):
        raise ConnectionError("blocked")
    with pytest.raises(ConnectionError):
        make(fetch, tmdb="k" * 32, cinemeta=True).search(PARSED)


def test_a_provider_that_answers_with_nothing_is_not_an_error():
    def fetch(url, params, headers):
        return {"results": []} if url.startswith(si.TMDB) else {"metas": []}
    assert make(fetch, tmdb="k" * 32, cinemeta=True).search(PARSED) == []


def test_where_to_watch_answers_empty_instead_of_a_500_when_tmdb_is_unreachable(tmp_path, monkeypatch):
    from telegram_archive.core.database import Database
    from telegram_archive.engines import screen_identify as sid
    from test_screen_home import _client

    def blocked(url, params, headers):
        raise ConnectionError("closed")
    monkeypatch.setattr(sid, "http_fetch", blocked)
    db = Database(tmp_path / "w.db")
    db.set_setting("tmdb_api_key", "k" * 32)
    got = _client(db).get("/api/screen/where/movie/tt0133093")
    assert got.status_code == 200 and got.json()["groups"] == [] and got.json()["unreachable"] is True
