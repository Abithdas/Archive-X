"""SFW Home and Discover: what to continue, what airs this week, what was added, and Cinemeta's catalogues with the library marked."""
import sys
from datetime import date, timedelta
import pytest
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
    if url == "https://anime-kitsu.strem.fun/meta/series/kitsu:12.json":
        return {"meta": {"id": "kitsu:12", "type": "series", "name": "One Piece", "releaseInfo": "1999", "poster": "http://a/2", "genres": ["Action"], "description": "Pirates.",
                         "videos": [{"season": 1, "episode": 1, "title": "Romance Dawn", "released": "1999-10-20T00:00:00.000Z"}, {"season": 1, "episode": 2, "title": "Buggy"}]}}
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
    if url.endswith("/3/find/tt0133093"):
        return {"movie_results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30"}], "tv_results": []}
    if url.endswith("/3/movie/603/watch/providers"):
        return {"results": {"US": {"link": "https://www.themoviedb.org/movie/603-the-matrix/watch?locale=US", "flatrate": [{"provider_name": "Netflix", "logo_path": "/n.jpg"}], "rent": [{"provider_name": "Apple TV", "logo_path": "/a.jpg"}]},
                            "DE": {"link": "https://evil.example/x", "buy": [{"provider_name": "Amazon", "logo_path": "/z.jpg"}]}}}
    if url.endswith("/3/movie/603/external_ids"):
        return {"imdb_id": "tt0133093"}
    if url.endswith("/3/movie/42/external_ids"):
        return {"imdb_id": None}
    if url.endswith("/3/movie/42"):
        return {"overview": "Plain.", "genres": [{"name": "Drama"}], "runtime": 90, "title": "Other", "release_date": "2020-01-01"}
    if url.endswith("/meta/movie/tt0133093.json"):
        return {"meta": {"imdb_id": "tt0133093", "type": "movie", "name": "The Matrix", "year": "1999", "runtime": "136 min", "cast": ["Keanu Reeves"], "director": ["Lana Wachowski"],
                         "trailers": [{"source": "m8e-FF8MsqU", "type": "Trailer"}], "background": "http://b/1"}}
    if url.endswith("/meta/series/tt0903747.json"):
        return {"meta": {"id": "tt0903747", "type": "series", "name": "Breaking Bad", "year": "2008", "background": "https://b/bb.jpg", "logo": "https://l/bb.png",
                         "videos": [{"season": 1, "episode": 2, "title": "Cat's in the Bag...", "released": "2008-01-27T00:00:00.000Z", "overview": "Walt.", "thumbnail": "https://t/2.jpg"},
                                    {"season": 1, "episode": 1, "title": "Pilot", "released": "2008-01-20T00:00:00.000Z", "thumbnail": "javascript:x"}, {"season": 0, "episode": 1, "title": "Special"}, {"nonsense": True}]}}
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
    one = c.get("/api/screen/discover/series/kitsu:12").json()          # an anime from the lists: its own page, with episodes
    assert one["title"] == "One Piece" and one["overview"] == "Pirates." and [e["name"] for e in one["episodes"][0]["episodes"]] == ["Romance Dawn", "Buggy"] and one["library"] is None
    assert c.get("/api/screen/discover/series/kitsu:..").status_code == 400
    assert c.get("/api/screen/discover?type=movie&genre=../etc").status_code == 400
    assert c.get("/api/screen/discover?type=movie&genre=Drama,../etc").status_code == 400
    assert [i["title"] for i in c.get("/api/screen/discover?type=series&genre=Drama,Comedy&catalog=featured").json()["items"]] == ["Breaking Bad"]   # several genres: the first goes to the source, a title without genres is kept
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


PUBLIC = [(0, 0, 0, "", ("93.184.216.34", 443))]
MANIFEST = {"id": "x.catalogs", "name": "My Catalogs", "version": "1.2.0", "description": "Test lists.", "resources": ["catalog"], "types": ["movie", "series"],
            "catalogs": [{"type": "movie", "id": "top", "name": "Top films", "extra": [{"name": "genre"}, {"name": "skip"}]},
                         {"type": "series", "id": "all", "name": "All shows", "extra": []},
                         {"type": "movie", "id": "search", "name": "Search", "extra": [{"name": "search", "isRequired": True}]}]}


def test_the_person_can_add_a_catalogue_addon_by_its_address_and_its_lists_show_in_discover(tmp_path, monkeypatch):
    from telegram_archive.metadata import addon_client as ac
    seen: list[str] = []

    def get_json(url):
        seen.append(url)
        if url.endswith("/manifest.json"):
            return MANIFEST if "good.example" in url else {"name": "Streams only", "resources": ["stream"], "catalogs": []}
        if "/catalog/movie/top/genre=Drama" in url:
            return {"metas": [{"id": "tt0133093", "type": "movie", "name": "The Matrix", "year": "1999", "genres": ["Drama"]}]}
        if url.endswith("/catalog/series/all.json"):
            return {"metas": [{"id": "tt0903747", "type": "series", "name": "Breaking Bad", "genres": ["Drama"]}, {"id": "tt0000002", "type": "series", "name": "Other", "genres": ["Comedy"]}]}
        return {"metas": []}
    monkeypatch.setattr(ac, "get_json", get_json)
    monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    db = Database(tmp_path / "a.db")
    _library(db)
    c = _client(db)
    assert c.get("/api/screen/addons").json()["items"] == [] and len(c.get("/api/screen/addons").json()["builtin"]) == 3
    for bad, why in (("http://good.example/manifest.json", "https://"), ("https://127.0.0.1/manifest.json", "inside"), ("https://user:pw@good.example/manifest.json", "not an add-on address"),
                     ("https://good.example:8080/manifest.json", "ports"), ("nonsense", "https://")):
        if "127.0.0.1" in bad:
            monkeypatch.setattr(ac, "_resolve", lambda host: [(0, 0, 0, "", ("127.0.0.1", 443))])
        r = c.post("/api/screen/addons", json={"url": bad}, headers=H)
        assert r.status_code == 400 and why in r.json()["detail"], (bad, r.text)
        monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    got = c.post("/api/screen/addons", json={"url": "stremio://good.example/cfg%7B%7D/manifest.json"}, headers=H).json()      # a stremio:// link is the same address over HTTPS
    add = got["items"][0]
    assert add["name"] == "My Catalogs" and add["url"] == "https://good.example/cfg%7B%7D" and [l["name"] for l in add["lists"]] == ["Top films", "All shows"]   # the search-only list is left out
    assert c.post("/api/screen/addons", json={"url": "https://good.example/cfg%7B%7D/manifest.json"}, headers=H).status_code == 400   # the same add-on twice
    aid = add["id"]
    # its lists in Discover: the add-on's own genre and paging when it has them, ours when it has not; titles with IMDb numbers are marked
    top = c.get(f"/api/screen/discover?catalog=addon:{aid}:movie:top&genre=Drama").json()
    assert [i["title"] for i in top["items"]] == ["The Matrix"] and top["items"][0]["library"] == {"kind": "movie", "id": 1} and top["items"][0]["kind"] == "movie"
    assert any("/catalog/movie/top/genre=Drama.json" in u for u in seen)
    shows = c.get(f"/api/screen/discover?catalog=addon:{aid}:series:all&genre=Drama").json()
    assert [i["title"] for i in shows["items"]] == ["Breaking Bad"] and shows["items"][0]["kind"] == "tv"
    assert c.get(f"/api/screen/discover?catalog=addon:{aid}:series:nope").status_code == 502 and c.get("/api/screen/discover?catalog=addon:0123456789:movie:top").status_code == 404
    assert c.get("/api/screen/discover?catalog=addon:../x").status_code == 400
    # a saved add-on that stops answering is still listed, with what is wrong
    monkeypatch.setattr(ac, "get_json", lambda url: (_ for _ in ()).throw(OSError("down")))
    discover_cache_clear = c.get("/api/screen/addons")                                                         # (answers are kept ten minutes, so this one still has the lists)
    assert discover_cache_clear.json()["items"][0]["name"] == "My Catalogs"
    after = c.delete(f"/api/screen/addons/{aid}", headers=H).json()
    assert after["items"] == [] and c.get(f"/api/screen/discover?catalog=addon:{aid}:movie:top").status_code == 404


def test_the_addon_address_check_refuses_inside_addresses_and_trusts_the_proxy_only_for_names(monkeypatch):
    import urllib.request
    from telegram_archive.metadata import addon_client as ac

    def down(host):
        raise OSError("no direct lookup")
    monkeypatch.setattr(ac, "_resolve", down)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    try:
        ac.check_public("https://good.example/manifest.json")
        raise AssertionError("an unresolvable name must be refused without a proxy")
    except ValueError as exc:
        assert "could not be found" in str(exc)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://proxy:3128"})
    assert ac.check_public("https://good.example/manifest.json") == "https://good.example/manifest.json"             # behind a proxy the proxy looks the name up
    for bad in ("https://127.0.0.1/x", "https://10.0.0.5/x", "https://192.168.0.1/x", "https://169.254.169.254/x", "https://[::1]/x", "https://localhost/x", "https://nas.local/x", "https://intranet/x", "https://printer.lan/x"):
        try:
            ac.check_public(bad)
            raise AssertionError(bad)
        except ValueError as exc:
            assert "inside" in str(exc), bad                                                                          # an inside address is refused even with a proxy
    assert ac.base_of("stremio://good.example/cfg/manifest.json") == "https://good.example/cfg"


def test_where_to_watch_names_the_services_and_links_only_to_tmdb(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", fake)
    db = Database(tmp_path / "w.db")
    c = _client(db)
    assert c.get("/api/screen/where/movie/tt0133093").json() == {"ok": True, "needs_key": True}                  # TMDB's data needs its key
    db.set_setting("tmdb_api_key", "k" * 32)
    got = c.get("/api/screen/where/movie/tt0133093?region=us").json()
    assert got["region"] == "US" and [g["label"] for g in got["groups"]] == ["Stream", "Rent"] and got["groups"][0]["providers"][0]["name"] == "Netflix"
    assert got["link"] == "https://www.themoviedb.org/movie/603-the-matrix/watch?locale=US"
    assert c.get("/api/screen/where/movie/tmdb-603?region=DE").json()["link"] == "https://www.themoviedb.org/movie/603/watch?locale=DE"   # a link that is not TMDB's own is never passed on
    assert c.get("/api/screen/where/movie/tt0133093?region=FR").json()["groups"] == []                         # nothing in that country
    assert c.get("/api/screen/where/series/tt0133093").json()["groups"] == []                                  # the number names a movie, not a series
    assert c.get("/api/screen/where/movie/nope").status_code == 400 and c.get("/api/screen/where/movie/tt0133093?region=../").status_code == 400


def test_the_calendar_file_lists_the_coming_episodes_of_your_shows(tmp_path):
    db = Database(tmp_path / "c.db")
    _library(db)
    c = _client(db)
    r = c.get("/api/screen/calendar.ics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar") and "attachment" in r.headers["content-disposition"]
    day = (date.today() + timedelta(days=3)).strftime("%Y%m%d")
    assert "BEGIN:VCALENDAR" in r.text and "SUMMARY:Breaking Bad S1E2" in r.text and f"DTSTART;VALUE=DATE:{day}" in r.text and r.text.count("BEGIN:VEVENT") == 1
    assert "DESCRIPTION:Cat's in the Bag" in r.text and "\r\n" in r.text
    assert c.get("/api/screen/calendar.ics?days=1").text.count("BEGIN:VEVENT") == 0                                # nothing airs tomorrow
    from telegram_archive.web.routes_screen import ics_calendar
    long = ics_calendar([{"uid": "u", "date": "2026-01-02", "title": "A, B; C\nD " + "é" * 60, "note": ""}])
    assert "A\\, B\; C\\nD" in long.replace("\r\n ", "") and all(len(x.encode()) <= 75 for x in long.split("\r\n"))


def test_cast_and_studios_count_movies_and_series_not_videos(tmp_path):
    db = Database(tmp_path / "k.db")
    _library(db)
    with db.lock:
        db.conn.execute("DROP VIEW IF EXISTS archive_x_library_media")                       # the real view waits for snapshots; here every video is published
        db.conn.execute("CREATE VIEW archive_x_library_media AS SELECT * FROM media")
        db.conn.execute("INSERT INTO performers(id,name) VALUES(1,'Keanu Reeves')")
        db.conn.execute("INSERT INTO studios(id,name) VALUES(1,'HBO')")
        db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status,duration) VALUES(3,'-9000000000001',3,'Breaking.Bad.S01E03.mkv','x','SCANNED',3600)")
        db.conn.execute("INSERT INTO media_screen(media_id,title_id,season,episode,state,confidence,updated_at) VALUES(3,(SELECT title_id FROM media_screen WHERE media_id=2),1,3,'MATCHED',1,'2026-01-01')")
        for mid in (1, 2, 3):
            db.conn.execute("INSERT INTO video_performers(video_id,performer_id) VALUES(?,1)", (mid,))
            db.conn.execute("INSERT INTO video_studios(video_id,studio_id) VALUES(?,1)", (mid,))
        db.conn.commit()
    from telegram_archive.web.performer_catalogue import list_performers_sync, title_counts
    p = [x for x in list_performers_sync(db, {"brief": "1"}) if x["name"] == "Keanu Reeves"][0]
    assert (p["video_count"], p["movies"], p["series"]) == (3, 1, 1)                       # three videos: one movie, and two episodes of one series
    with db.reading() as r:
        assert title_counts(r, "SELECT studio_id AS eid, video_id FROM video_studios") == {1: {"movies": 1, "series": 1}}


def test_any_stremio_addon_can_be_added_and_the_one_on_this_pc_needs_the_switch(tmp_path, monkeypatch):
    from telegram_archive.metadata import addon_client as ac
     # what a manifest offers: lists are used, everything else is named; streams are never asked for
    streams = ac.parse_manifest({"name": "Streams only", "resources": ["stream"], "catalogs": []})
    assert streams["lists"] == [] and streams["provides"] == ["Streams"] and "Title pages ask for streams" in streams["note"] and "no lists" in streams["note"]
    subs = ac.parse_manifest({"name": "Subs", "resources": ["subtitles", {"name": "meta", "types": ["movie"]}]})
    assert subs["provides"] == ["Subtitles", "Details"] and "subtitle menu" in subs["note"]
    typed = ac.parse_manifest({"name": "Typed", "resources": ["catalog"], "catalogs": [{"type": "movie", "id": "s", "extra": [{"name": "search", "isRequired": True}]}]})
    assert typed["lists"] == [] and typed["provides"] == ["Lists"]
    with pytest.raises(ValueError, match="Stremio add-on"):
        ac.parse_manifest({"hello": "world"})
    # this PC or network: http, any port and private addresses, only while the person has allowed it
    ac.set_allow_local(False)
    for url in ("http://127.0.0.1:7000/manifest.json", "http://localhost:11470/manifest.json", "https://192.168.0.5:8443/manifest.json"):
        with pytest.raises(ValueError):
            ac.check_public(url)
    ac.set_allow_local(True)
    try:
        assert ac.check_public("http://127.0.0.1:7000/manifest.json") == "http://127.0.0.1:7000/manifest.json"
        assert ac.base_of("http://localhost:11470/manifest.json") == "http://localhost:11470"
        assert ac.check_public("https://192.168.0.5:8443/x") == "https://192.168.0.5:8443/x"
        with pytest.raises(ValueError, match="not an add-on address"):
            ac.check_public("http://user:pw@127.0.0.1/x")
    finally:
        ac.set_allow_local(False)
    # the switch is saved through the route and shows in the list
    monkeypatch.setattr(ac, "get_json", lambda url: {"name": "Local", "resources": ["catalog"], "catalogs": [{"type": "movie", "id": "all"}]})
    db = Database(tmp_path / "l.db")
    c = _client(db)
    assert c.get("/api/screen/addons").json()["allow_local"] is False
    assert c.post("/api/screen/addons", json={"url": "http://127.0.0.1:7000/manifest.json"}, headers=H).status_code == 400
    assert c.post("/api/screen/addons/settings", json={"allow_local": True}, headers=H).json()["allow_local"] is True
    added = c.post("/api/screen/addons", json={"url": "http://127.0.0.1:7000/manifest.json"}, headers=H)
    assert added.status_code == 200 and added.json()["items"][0]["url"] == "http://127.0.0.1:7000"
    c.post("/api/screen/addons/settings", json={"allow_local": False}, headers=H)
    ac.set_allow_local(False)


def test_movies_and_series_carry_what_their_copies_have_for_the_filters(tmp_path):
    from telegram_archive.metadata import screen_ids as si
    q = si.quality_of("Dune.2021.2160p.UHD.BluRay.HDR.DV.German.x265.mkv")
    assert (q["height"], q["source"], q["hdr"], q["languages"]) == (2160, "BluRay", ["HDR", "DV"], ["German"])
    assert si.quality_of("Film.1080p.WEB-DL.MULTi.mkv")["languages"] == ["Multi"] and si.quality_of("Film.720p.mkv")["hdr"] == []
    assert si.facets_of([si.quality_of("a.1080p.WEB-DL.mkv"), si.quality_of("a.2160p.BluRay.HDR.mkv")]) == {"res": [2160, 1080], "sources": ["BluRay", "WEB-DL"], "hdr": ["HDR"], "langs": []}
    db = Database(tmp_path / "f.db")
    _library(db)
    with db.lock:
        db.conn.execute("UPDATE media SET filename='The.Matrix.1999.2160p.BluRay.HDR.mkv' WHERE id=1")
        db.conn.execute("UPDATE media SET filename='Breaking.Bad.S01E01.720p.WEB-DL.FRENCH.mkv' WHERE id=2")
        db.conn.commit()
    c = _client(db)
    movie = c.get("/api/screen/movies").json()["items"][0]
    assert (movie["res"], movie["sources"], movie["hdr"]) == ([2160], ["BluRay"], ["HDR"])
    show = c.get("/api/screen/series").json()["items"][0]
    assert (show["res"], show["sources"], show["langs"]) == ([720], ["WEB-DL"], ["French"])


def test_a_title_page_has_the_episodes_and_where_to_watch_opens_each_service(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", fake)
    db = Database(tmp_path / "p.db")
    _library(db)
    c = _client(db)
    db.set_setting("tmdb_api_key", "k" * 32)
    one = c.get("/api/screen/discover/series/tt0903747").json()
    assert [(x["season"], [e["episode"] for e in x["episodes"]]) for x in one["episodes"]] == [(0, [1]), (1, [1, 2])]          # in order, specials first
    ep = one["episodes"][1]["episodes"]
    assert ep[1]["name"] == "Cat's in the Bag..." and ep[1]["released"] == "2008-01-27" and ep[1]["thumb"] == "https://t/2.jpg" and ep[0]["thumb"] == ""    # a picture address that is not https is dropped
    assert one["logo"] == "https://l/bb.png" and one["background"] == "https://b/bb.jpg"
    where = c.get("/api/screen/where/movie/tt0133093?region=US&title=The%20Matrix%3A%20Reloaded").json()
    flat = {p["name"]: p["open"] for g in where["groups"] for p in g["providers"]}
    assert flat["Netflix"] == "https://www.netflix.com/search?q=The%20Matrix%3A%20Reloaded" and flat["Apple TV"] == "https://tv.apple.com/search?term=The%20Matrix%3A%20Reloaded"
    assert [p["open"] for g in c.get("/api/screen/where/movie/tt0133093?region=US").json()["groups"] for p in g["providers"]] == ["", ""]   # no title, no service links


def test_streams_from_addons_open_elsewhere_or_play_only_from_own_or_public_sources(tmp_path, monkeypatch):
    import io
    from telegram_archive.metadata import addon_client as ac
    manifests = {"https://s.example": {"name": "Streams", "resources": [{"name": "stream", "types": ["movie", "series"], "idPrefixes": ["tt"]}], "types": ["movie"], "catalogs": []},
                 "https://subs.example": {"name": "Subs", "resources": ["subtitles"], "types": ["movie"]}}
    asked: list[str] = []

    def get_json(url):
        asked.append(url)
        base = url.split("/manifest.json")[0] if url.endswith("/manifest.json") else url.split("/stream/")[0]
        if url.endswith("/manifest.json"):
            return manifests[base]
        if "/stream/movie/tt0133093.json" in url or "/stream/series/tt0903747%3A1%3A2.json" in url:
            return {"streams": [{"name": "Trailer", "ytId": "abcdefghijk"}, {"name": "Netflix", "externalUrl": "https://www.netflix.com/title/20557937"}, {"name": "Torrent", "infoHash": "abc"},
                                {"name": "Home server", "url": "http://192.168.1.5:8096/Videos/1/stream.mkv"}, {"name": "Archive", "url": "https://archive.org/download/film/film.mp4"},
                                {"name": "Debrid", "url": "https://download.real-debrid.example/d/AB/film.mkv"}]}
        return {}
    monkeypatch.setattr(ac, "get_json", get_json)
    monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    db = Database(tmp_path / "st.db")
    c = _client(db)
    db.set_setting("catalog_addons", [{"id": "0123456789", "url": "https://s.example", "name": "Streams"}, {"id": "abcdef0123", "url": "https://subs.example", "name": "Subs"}])
    got = c.get("/api/screen/streams/movie/tt0133093").json()
    kinds = {i["name"]: i["kind"] for i in got["items"]}
    assert got["addons"] == ["Streams"] and got["skipped"] == 1                                              # only the add-on that answers for streams is asked; the torrent is left out
    assert kinds == {"Trailer": "link", "Netflix": "link", "Home server": "blocked", "Archive": "play", "Debrid": "play"}   # the home server needs the switch; public HTTPS hosts are playable
    assert not any("subs.example/stream" in u for u in asked) and all("url" not in i for i in got["items"])
    c.post("/api/screen/addons/settings", json={"allow_local": True}, headers=H)
    on = {i["name"]: i for i in c.get("/api/screen/streams/movie/tt0133093").json()["items"]}
    assert on["Home server"]["kind"] == "play" and on["Archive"]["kind"] == "play" and on["Debrid"]["kind"] == "play"
    assert c.get("/api/screen/streams/series/tt0903747?season=1&episode=2").json()["addons"] == ["Streams"]
    assert c.get("/api/screen/streams/series/tt0903747").json()["items"] == []                                 # a show's stream needs its season and episode
    assert c.get("/api/screen/streams/movie/nope").status_code == 400

    # playing: only a listed address, with the range passed on and the answer streamed back
    seen: dict = {}

    class Resp(io.BytesIO):
        status = 206
        headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 0-4/10", "Content-Length": "5", "Accept-Ranges": "bytes"}

    def open_stream(url, byte_range=""):
        seen.update(url=url, range=byte_range)
        return Resp(b"hello")
    monkeypatch.setattr(ac, "open_stream", open_stream)
    r = c.get(on["Archive"]["play"], headers={"Range": "bytes=0-4"})
    assert r.status_code == 206 and r.content == b"hello" and r.headers["content-range"] == "bytes 0-4/10"
    assert seen == {"url": "https://archive.org/download/film/film.mp4", "range": "bytes=0-4"}
    assert c.get("/api/screen/stream/nottoken").status_code == 404
    ac.set_allow_local(False)


def test_a_stream_is_played_only_from_an_own_source_or_a_public_https_host(monkeypatch):
    from telegram_archive.metadata import addon_client as ac
    monkeypatch.setattr(ac, "_resolve", lambda host: [(0, 0, 0, "", ("93.184.216.34", 443))])
    ac.set_allow_local(False)
    assert ac.stream_origin("https://archive.org/download/a/b.mp4") == "public" and ac.stream_origin("https://ia800.us.archive.org/x.mp4") == "public"
    assert ac.stream_origin("http://archive.org/x.mp4") == "" and ac.stream_origin("https://evilarchive.org/x.mp4") == "public" and ac.stream_origin("http://192.168.1.5/x.mkv") == ""
    ac.set_allow_local(True)
    try:
        assert ac.stream_origin("http://192.168.1.5:8096/x.mkv") == "own" and ac.stream_origin("http://localhost:11470/x") == "own" and ac.stream_origin("http://nas/x.mkv") == "own"
        assert ac.stream_origin("https://download.real-debrid.example/x.mkv") == "public" and ac.stream_origin("http://u:p@192.168.1.5/x") == "" and ac.stream_origin("file:///etc/passwd") == ""
    finally:
        ac.set_allow_local(False)



def test_subtitles_from_an_addon_are_listed_for_a_library_video_and_fetched_on_choice(tmp_path, monkeypatch):
    from telegram_archive.metadata import addon_client as ac
    manifests = {"https://os.example": {"name": "OpenSubtitles v3", "resources": ["subtitles"], "types": ["movie", "series"], "idPrefixes": ["tt"], "catalogs": []}}
    asked: list[str] = []

    def get_json(url):
        asked.append(url)
        if url.endswith("/manifest.json"):
            return manifests[url.split("/manifest.json")[0]]
        if "/subtitles/movie/tt0133093" in url or "/subtitles/series/tt0903747%3A1%3A1" in url:
            return {"subtitles": [{"id": "1", "url": "https://subs5.example/en/1.srt", "lang": "spa"}, {"id": "2", "url": "https://subs5.example/en/2.srt", "lang": "eng"},
                                  {"id": "3", "url": "http://192.168.1.5/x.srt", "lang": "fre"}]}
        return {}
    monkeypatch.setattr(ac, "get_json", get_json)
    monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    monkeypatch.setattr(ac, "open_stream", lambda url, byte_range="": __import__("io").BytesIO(b"1\n00:00:01,000 --> 00:00:02,000\nHello\n"))
    db = Database(tmp_path / "sub.db")
    _library(db)
    c = _client(db)
    db.set_setting("catalog_addons", [{"id": "0123456789", "url": "https://os.example", "name": "OpenSubtitles v3"}])
    got = c.get("/api/screen/subtitles/-9000000000001/1").json()
    assert [i["label"] for i in got["items"]] == ["English · OpenSubtitles v3", "Spanish · OpenSubtitles v3"]      # English first; the home-network file is left out
    assert all("url" not in i for i in got["items"])
    assert len(c.get("/api/screen/subtitles/-9000000000001/2").json()["items"]) == 2                              # an episode asks with its season and episode
    assert c.get("/api/screen/subtitles/-9000000000001/99").json()["items"] == []                                 # not a video the library knows
    body = c.get("/api/screen/subtitle/" + got["items"][0]["token"])
    assert body.status_code == 200 and "Hello" in body.text and body.headers["content-type"].startswith("text/vtt")
    assert c.get("/api/screen/subtitle/nope").status_code == 404


def test_extra_details_from_addons_are_text_and_https_links_only(tmp_path, monkeypatch):
    from telegram_archive.metadata import addon_client as ac
    manifests = {"https://dd.example": {"name": "Deep Dive", "resources": ["meta"], "types": ["movie", "series"], "idPrefixes": ["tt"], "catalogs": []},
                 "https://st.example": {"name": "Stinger", "resources": ["meta"], "types": ["movie"], "idPrefixes": ["tt"], "catalogs": []}}

    def get_json(url):
        if url.endswith("/manifest.json"):
            return manifests[url.split("/manifest.json")[0]]
        if url == "https://dd.example/meta/series/tt0903747.json":
            return {"meta": {"id": "tt0903747", "description": "Production notes.", "links": [{"name": "Reviews", "category": "x", "url": "https://r.example/1"}, {"name": "Bad", "url": "javascript:alert(1)"}]}}
        return {}
    monkeypatch.setattr(ac, "get_json", get_json)
    db = Database(tmp_path / "m.db")
    c = _client(db)
    db.set_setting("catalog_addons", [{"id": "0123456789", "url": "https://dd.example", "name": "Deep Dive"}, {"id": "abcdef0123", "url": "https://st.example", "name": "Stinger"}])
    got = c.get("/api/screen/addon-meta/series/tt0903747").json()
    assert [(i["addon"], i["text"], [l["name"] for l in i["links"]]) for i in got["items"]] == [("Deep Dive", "Production notes.", ["Reviews"])]    # a movie-only add-on is not asked about a show; no javascript: link
    assert c.get("/api/screen/addon-meta/series/nope").status_code == 400


def test_addon_catalogs_that_can_be_searched_are_asked_too_and_subtitles_get_the_file_name(tmp_path, monkeypatch):
    from telegram_archive.metadata import addon_client as ac
    manifests = {"https://cat.example": {"name": "Cat", "resources": ["catalog"], "types": ["movie"], "behaviorHints": {"configurable": True},
                                         "catalogs": [{"type": "movie", "id": "find", "name": "Find", "extra": [{"name": "search", "isRequired": True}]}]},
                 "https://os.example": {"name": "OS", "resources": ["subtitles"], "types": ["movie"], "idPrefixes": ["tt"], "catalogs": []}}
    asked: list[str] = []

    def get_json(url):
        asked.append(url)
        if url.endswith("/manifest.json"):
            return manifests[url.split("/manifest.json")[0]]
        if "/catalog/movie/find/search=dune.json" in url:
            return {"metas": [{"id": "tt1160419", "type": "movie", "name": "Dune"}, {"id": "tt0133093", "type": "movie", "name": "The Matrix"}]}
        if "/subtitles/movie/tt0133093/filename=The.Matrix.1999.mkv" in url:
            return {"subtitles": [{"id": "1", "url": "https://subs5.example/1.srt", "lang": "eng"}]}
        return {}
    monkeypatch.setattr(ac, "get_json", get_json)
    monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    monkeypatch.setattr(sid, "http_fetch", fake)
    db = Database(tmp_path / "s2.db")
    _library(db)
    c = _client(db)
    db.set_setting("catalog_addons", [{"id": "0123456789", "url": "https://cat.example", "name": "Cat"}, {"id": "abcdef0123", "url": "https://os.example", "name": "OS"}])
    view = c.get("/api/screen/addons").json()["items"]
    cat = next(i for i in view if i["name"] == "Cat")
    assert cat["configurable"] is True and cat["searches"] == [{"type": "movie", "id": "find"}] and cat["lists"] == [] and "also searched" in cat["note"]
    titles = [i["title"] for i in c.get("/api/screen/discover?type=movie&search=dune").json()["items"]]
    assert titles[0] == "Dune" and titles.count("Dune") == 1 and "The Matrix" in titles       # the add-on's finds follow Cinemeta's, without repeats
    assert c.get("/api/screen/subtitles/-9000000000001/1").json()["items"] != []
    assert any("filename=The.Matrix.1999.mkv" in u for u in asked)
