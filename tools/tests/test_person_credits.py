"""An actor's page in SFW: the library's titles first (the page), then the other movies and shows they are in (TMDB's credits for them)."""
from telegram_archive.core.database import Database
from telegram_archive.engines import screen_identify as sid
from test_screen_home import _client, _library


def credits_fetch(url, params, headers):
    assert "/person/6384/combined_credits" in url
    return {"cast": [
        {"media_type": "movie", "id": 603, "title": "The Matrix", "release_date": "1999-03-30", "popularity": 90, "poster_path": "/m.jpg", "character": "Neo"},       # in the library: not repeated
        {"media_type": "movie", "id": 245891, "title": "John Wick", "release_date": "2014-10-22", "popularity": 80, "poster_path": "/jw.jpg", "character": "John Wick"},
        {"media_type": "tv", "id": 77, "name": "A Show", "first_air_date": "2020-01-01", "popularity": 85, "poster_path": "/s.jpg", "character": "Self", "genre_ids": [18]},
        {"media_type": "tv", "id": 78, "name": "Late Night", "first_air_date": "2019-01-01", "popularity": 99, "genre_ids": [10767], "character": "Self"},             # a talk show: left out
        {"media_type": "movie", "id": 245891, "title": "John Wick", "popularity": 80},                                                                                    # listed twice by TMDB: once
    ]}


def add_actor(db, *, tmdb_id="6384", rating="sfw"):
    with db.lock:
        db.conn.execute("INSERT INTO performers(id,name,content_rating,tmdb_id) VALUES(501,'Keanu Reeves',?,?)", (rating, tmdb_id))
        db.conn.commit()


def test_other_credits_are_listed_by_popularity_without_what_the_library_has(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", credits_fetch)
    db = Database(tmp_path / "p.db")
    _library(db)
    add_actor(db)
    c = _client(db)
    assert c.get("/api/screen/person-credits/501").json() == {"ok": True, "items": [], "reason": "tmdb"}              # no TMDB key: says so
    db.set_setting("tmdb_api_key", "k" * 32)
    got = c.get("/api/screen/person-credits/501").json()
    assert [(i["title"], i["kind"], i["imdb"]) for i in got["items"]] == [("A Show", "tv", "tmdb-77"), ("John Wick", "movie", "tmdb-245891")]
    assert got["items"][1]["character"] == "John Wick" and got["items"][1]["year"] == 2014


def test_an_unlinked_or_adult_or_unknown_person_gets_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", credits_fetch)
    db = Database(tmp_path / "q.db")
    db.set_setting("tmdb_api_key", "k" * 32)
    add_actor(db, tmdb_id="")
    c = _client(db)
    assert c.get("/api/screen/person-credits/501").json()["reason"] == "unlinked"
    assert c.get("/api/screen/person-credits/9999").json()["items"] == []
    with db.lock:
        db.conn.execute("UPDATE performers SET content_rating='nsfw', tmdb_id='6384' WHERE id=501")
        db.conn.commit()
    assert c.get("/api/screen/person-credits/501").json()["items"] == []                                              # a performer of the adult library is never sent to TMDB
