"""SFW Home and Discover: what to continue, what airs this week, what was added, and Cinemeta's catalogues with the library marked."""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.database import Database
from telegram_archive.engines import screen_identify as sid

PW = "correct-horse-battery"
H = {"Origin": "https://testserver"}
SEEN: list[str] = []
TMDB_ASKED: list[dict] = []
ADDON_DOWN: list[bool] = [False]


def fake(url, params, headers):
    SEEN.append(url)
    if url.endswith("/catalog/movie/top.json"):
        return {"metas": [{"imdb_id": "tt0133093", "type": "movie", "name": "The Matrix", "year": "1999", "poster": "http://p/1", "imdbRating": "8.7", "genres": ["Action", "Sci-Fi"], "description": "Neo."},
                          {"imdb_id": "tt0234215", "type": "movie", "name": "The Matrix Reloaded", "releaseInfo": "2003", "poster": "http://p/2"}]}
    if url.endswith("/catalog/series/top/genre=Drama&skip=50.json"):
        return {"metas": [{"imdb_id": "tt0903747", "type": "series", "name": "Breaking Bad", "year": "2008"}]}
    if "/catalog/movie/top/search=dune" in url:
        return {"metas": [{"imdb_id": "tt1160419", "type": "movie", "name": "Dune", "year": "2021"}]}
    if "stremio-netflix-catalog-addon" in url:
        if ADDON_DOWN[0]:
            raise OSError("the add-on is down")
        if url.endswith("/catalog/movie/nfx.json"):
            many = [{"imdb_id": f"tt{9000000 + i}", "type": "movie", "name": f"Film {i}", "year": "2020", "genres": ["Drama"] if i % 2 else ["Action"], "poster": "http://p/x", "imdbRating": "7.0"} for i in range(120)]
            return {"metas": many + [{"imdb_id": "tt0133093", "type": "movie", "name": "The Matrix", "year": "1999", "genres": ["Action"]}]}
        return {}
    if "stremio-anime-catalogs" in url:
        if "myanimelist_top-series/genre=Action" in url:
            return {"metas": [{"id": "kitsu:3936", "type": "anime", "name": "Fullmetal Alchemist: Brotherhood", "releaseInfo": "2009", "poster": "http://a/1", "description": "Two brothers.", "genres": ["Action", "Adventure"]}]}
        return {"metas": [{"id": "kitsu:12", "type": "anime", "name": "One Piece", "releaseInfo": "1999", "poster": "http://a/2", "genres": ["Action"]}]}
    if url.endswith("/catalog/movie/year.json"):
        return {"metas": [{"imdb_id": "tt9999991", "type": "movie", "name": "Newest", "year": "2026"}]}
    if url.endswith("/catalog/series/imdbRating/genre=Drama.json"):
        return {"metas": [{"imdb_id": "tt0903747", "type": "series", "name": "Breaking Bad", "year": "2008"}]}
    if url.endswith("/3/discover/movie") or url.endswith("/3/discover/tv"):
        TMDB_ASKED.append(dict(params))
        tv = url.endswith("/tv")
        if tv:
            rows = [{"id": 1396, "name": "Breaking Bad", "first_air_date": "2008-01-20", "poster_path": "/p.jpg", "vote_average": 8.7, "overview": "x"}]
        else:
            rows = [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "poster_path": "/p.jpg", "vote_average": 8.7, "overview": "x"}, {"id": 42, "title": "Other", "release_date": "2020-01-01"}]
        return {"results": rows, "total_pages": 3}
    if url.endswith("/3/movie/603/external_ids"):
        return {"imdb_id": "tt0133093"}
    if url.endswith("/3/movie/42/external_ids"):
        return {"imdb_id": None}
    if url.endswith("/3/movie/42"):
        return {"overview": "Plain.", "genres": [{"name": "Drama"}], "runtime": 90, "title": "Other", "release_date": "2020-01-01"}
    if url.endswith("/meta/movie/tt0133093.json"):
        return {"meta": {"imdb_id": "tt0133093", "type": "movie", "name": "The Matrix", "year": "1999", "runtime": "136 min", "cast": ["Keanu Reeves"], "director": ["Lana Wachowski"],
                         "trailers": [{"source": "m8e-FF8MsqU", "type": "Trailer"}], "background": "http://b/1"}}
    return {}


def _client(db):
    from fastapi.testclient import TestClient
    from telegram_archive.app import create_app
    from telegram_archive.core.auth import ensure_web_credentials
    ensure_web_credentials(db)
    c = TestClient(create_app(db=db), base_url="https://testserver", client=("127.0.0.1", 5000))
    c.post("/api/auth/setup", json={"username": "admin", "password": PW, "confirmation": PW}, headers=H)
    assert c.post("/login", json={"username": "admin", "password": PW}, headers=H).status_code == 200
    return c


def _library(db):
    matrix = sid.store_title(db, {"kind": "movie", "tmdb_id": 603, "imdb_id": "tt0133093", "title": "The Matrix", "year": 1999, "poster": "/m.jpg", "backdrop": "/mb.jpg", "overview": "Neo.", "genres": ["Action"], "rating": 8.2})
    show = sid.store_title(db, {"kind": "tv", "tmdb_id": 1396, "imdb_id": "tt0903747", "title": "Breaking Bad", "year": 2008, "poster": "/bb.jpg", "backdrop": "/bbb.jpg"})
    soon = (date.today() + timedelta(days=3)).isoformat()
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-9000000000001','Films','local',1,'sfw','videos','2026-01-01')")
        for mid, name, title_id, season, episode in ((1, "The.Matrix.1999.mkv", matrix, None, None), (2, "Breaking.Bad.S01E01.mkv", show, 1, 1)):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status,duration) VALUES(?,?,?,?,?,'SCANNED',3600)", (mid, "-9000000000001", mid, name, name))
            db.conn.execute("INSERT INTO media_screen(media_id,title_id,season,episode,state,confidence,updated_at) VALUES(?,?,?,?,'MATCHED',1,'2026-01-01')", (mid, title_id, season, episode))
        db.conn.execute("INSERT INTO screen_episodes(title_id,season,episode,name,air_date) VALUES(?,?,?,?,?)", (show, 1, 2, "Cat's in the Bag", soon))
        db.conn.execute("INSERT INTO watch_history(media_id,position,duration,last_played_at,completed,updated_at) VALUES(2,600,3000,'2026-11-01T10:00:00',0,'2026-11-01T10:00:00')")
        db.conn.commit()


def test_discover_lists_catalogues_marks_what_the_library_has_and_validates(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", fake)
    db = Database(tmp_path / "d.db")
    _library(db)
    c = _client(db)
    got = c.get("/api/screen/discover?type=movie").json()
    assert got["disabled"] is False and [i["title"] for i in got["items"]] == ["The Matrix", "The Matrix Reloaded"]
    assert got["items"][0]["library"] == {"kind": "movie", "id": 1} and got["items"][1]["library"] is None      # the one you have is marked, with where to open it
    assert got["items"][0]["year"] == 1999 and got["items"][1]["year"] == 2003 and got["items"][0]["rating"] == "8.7"
    assert [i["title"] for i in c.get("/api/screen/discover?type=series&genre=Drama&skip=50").json()["items"]] == ["Breaking Bad"]
    assert c.get("/api/screen/discover?type=movie&search=dune").json()["items"][0]["title"] == "Dune"
    assert c.get("/api/screen/discover?type=music").status_code == 400
    assert c.get("/api/screen/discover?type=movie&genre=../etc").status_code == 400
    before = len(SEEN)
    c.get("/api/screen/discover?type=movie")
    assert len(SEEN) == before                                                                                  # the same catalogue is not fetched again within minutes
    one = c.get("/api/screen/discover/movie/tt0133093").json()
    assert one["trailer"] == "m8e-FF8MsqU" and one["cast"] == ["Keanu Reeves"] and one["runtime"] == "136 min" and one["library"] == {"kind": "movie", "id": 1}
    assert c.get("/api/screen/discover/movie/tt0000001").status_code == 404 and c.get("/api/screen/discover/movie/nope").status_code == 400
    c.post("/api/screen/settings", json={"cinemeta_enabled": False}, headers=H)
    assert c.get("/api/screen/discover?type=movie").json() == {"ok": True, "disabled": True, "items": []}       # switched off: nothing is sent


def test_home_has_what_to_continue_what_airs_what_was_added_and_a_billboard(tmp_path):
    db = Database(tmp_path / "h.db")
    _library(db)
    c = _client(db)
    home = c.get("/api/screen/home").json()
    assert [(x["title"], x["sub"], x["pct"], x["left"]) for x in home["continue"]] == [("Breaking Bad", "S1 · E1", 20, 40)]
    assert [(x["title"], x["season"], x["episode"]) for x in home["airing"]] == [("Breaking Bad", 1, 2)]
    assert {x["title"] for x in home["recent"]} == {"The Matrix", "Breaking Bad"}
    assert {x["title"] for x in home["billboard"]} == {"The Matrix", "Breaking Bad"} and all(x["backdrop"] for x in home["billboard"])


def test_discover_has_new_and_featured_from_cinemeta_streaming_lists_from_an_addon_with_tmdb_as_the_backup_and_anime(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", fake)
    ADDON_DOWN[0] = False
    db = Database(tmp_path / "s.db")
    _library(db)
    c = _client(db)
    assert [i["title"] for i in c.get("/api/screen/discover?type=movie&catalog=new").json()["items"]] == ["Newest"]
    assert [i["title"] for i in c.get("/api/screen/discover?type=series&catalog=featured&genre=Drama").json()["items"]] == ["Breaking Bad"]
    assert c.get("/api/screen/discover?type=movie&catalog=nope").status_code == 400
    # a streaming list comes from the Streaming Catalogs add-on: no key, whole titles with IMDb numbers, cut into pages and by genre here
    first = c.get("/api/screen/discover?type=movie&catalog=netflix").json()
    assert len(first["items"]) == 50 and first["more"] is True and first["next"] == 50 and first["items"][0]["imdb"] == "tt9000000"
    assert [i["title"] for i in c.get("/api/screen/discover?type=movie&catalog=netflix&skip=100").json()["items"]][-2:] == ["Film 119", "The Matrix"]   # the last page has the rest
    drama = c.get("/api/screen/discover?type=movie&catalog=netflix&genre=Drama").json()["items"]
    assert drama and all("Drama" in i["genres"] for i in drama)
    assert [i["library"] for i in c.get("/api/screen/discover?type=movie&catalog=netflix&skip=100").json()["items"]][-1] == {"kind": "movie", "id": 1}   # marked by its IMDb number
    # the add-on is down: TMDB's "where to watch" answers when its key is there, and the page says what is missing when it is not
    ADDON_DOWN[0] = True
    assert c.get("/api/screen/discover?type=movie&catalog=disney").status_code == 502
    db.set_setting("tmdb_api_key", "k" * 32)
    got = c.get("/api/screen/discover?type=movie&catalog=disney&genre=Drama").json()
    assert [i["title"] for i in got["items"]] == ["The Matrix", "Other"] and got["more"] is True and got["next"] == 1
    asked = TMDB_ASKED[-1]
    assert asked["with_watch_providers"] == 337 and asked["with_genres"] == 18 and asked["page"] == 1 and asked["watch_region"] == "US"
    assert got["items"][0]["imdb"] == "tmdb-603" and got["items"][0]["library"] == {"kind": "movie", "id": 1}     # marked from the library by its TMDB number
    assert c.get("/api/screen/discover?type=series&catalog=prime&skip=1").json()["items"][0]["title"] == "Breaking Bad" and TMDB_ASKED[-1]["page"] == 2
    assert c.get("/api/screen/discover?type=movie&catalog=apple&genre=Sport").json()["items"] == []            # TMDB has no such genre
    assert c.get("/api/screen/discover/movie/tmdb-603").json()["cast"] == ["Keanu Reeves"]
    plain = c.get("/api/screen/discover/movie/tmdb-42").json()
    assert plain["overview"] == "Plain." and plain["genres"] == ["Drama"] and plain["runtime"] == "90 min"
    # anime (MyAnimeList's lists): Kitsu numbers, so they can be browsed but are never marked as in the library
    anime = c.get("/api/screen/discover?catalog=anime-airing").json()
    assert [i["title"] for i in anime["items"]] == ["One Piece"] and anime["items"][0]["imdb"] == "kitsu:12" and anime["items"][0]["kind"] == "tv" and anime["items"][0]["library"] is None
    assert c.get("/api/screen/discover?catalog=anime-movies").json()["items"][0]["kind"] == "movie"
    assert c.get("/api/screen/discover?catalog=anime-airing&skip=96").json()["next"] == 97                     # the anime lists page by offset: the next page starts where this one ended
    assert c.get("/api/screen/discover?catalog=anime-series&genre=Action").json()["items"][0]["overview"] == "Two brothers."
    c.post("/api/screen/settings", json={"cinemeta_enabled": False}, headers=H)
    assert c.get("/api/screen/discover?catalog=anime-airing").json()["disabled"] is True and c.get("/api/screen/discover?type=movie&catalog=netflix").json()["disabled"] is True
