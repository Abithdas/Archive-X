"""Identification that asks little: one answer per show, answers kept on disk, names read well, ids in names, TVmaze, quality."""
import os
import sys
import tempfile
import time

# before anything imports the app: keys and caches these tests save must land in a temporary folder
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axeff_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines import screen_identify as sid  # noqa: E402
from telegram_archive.metadata import screen_ids as si  # noqa: E402


def tmdb(url, params, headers):
    """A small TMDB: one show with two seasons, one movie, and nothing else."""
    q = str(params.get("query", "")).lower()
    if url.endswith("/search/tv"):
        return {"results": [{"id": 70523, "name": "Dark", "first_air_date": "2017-12-01", "popularity": 90}]} if q == "dark" else {"results": []}
    if url.endswith("/search/movie"):
        if q == "the crown":      # only the shortened title finds it
            return {"results": [{"id": 5, "title": "The Crown", "release_date": "2016-11-04"}]}
        if q == "the matrix" and "year" not in params:     # the right year is not the one the file name claims
            return {"results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30"}]}
        return {"results": []}
    if url.endswith("/tv/70523"):
        return {"id": 70523, "name": "Dark", "first_air_date": "2017-12-01", "genres": [{"name": "Drama"}], "networks": [{"name": "Netflix"}], "credits": {"cast": [{"id": 1, "name": "Louis Hofmann"}]},
                "seasons": [{"season_number": 1, "episode_count": 10, "name": "Season 1"}, {"season_number": 2, "episode_count": 8, "name": "Season 2"}]}
    if "/tv/70523/season/" in url:
        n = int(url.rsplit("/", 1)[1])
        return {"episodes": [{"episode_number": i, "name": f"S{n}E{i}", "air_date": "2019-01-01"} for i in range(1, 9)]}
    if url.endswith("/movie/603"):
        return {"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "genres": [], "credits": {"cast": []}}
    if "/find/tt0133093" in url:
        return {"movie_results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30"}]}
    return {}


def counting(inner):
    seen = []

    def fetch(url, params, headers):
        seen.append(url)
        return inner(url, params, headers)
    fetch.seen = seen
    return fetch


def _db(tmp_path):
    db = Database(tmp_path / "e.db")
    db.set_setting("tmdb_api_key", "k" * 32)
    return db


def _media(db, names):
    with db.lock:
        for i, n in enumerate(names, 1):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,?,'SCANNED')", (i, "-9000000000001", i, n, n))
        db.conn.commit()


def test_a_whole_show_costs_a_handful_of_requests_not_a_few_per_episode(tmp_path):
    db = _db(tmp_path)
    names = [f"Dark.S01E0{i}.mkv" for i in range(1, 7)] + [f"Dark.S02E0{i}.mkv" for i in range(1, 7)]
    _media(db, names)
    fetch = counting(tmdb)
    screen = sid.make_screen(db, fetch)
    ctx = {"memo": {}, "entities": {}}
    assert {sid.identify_one(db, screen, i, n, ctx) for i, n in enumerate(names, 1)} == {"MATCHED"}
    assert len(fetch.seen) == 4, fetch.seen          # one search, one details, one request for each of the two seasons (12 episodes before: 36 requests)
    with db.reading() as c:
        assert c.execute("SELECT COUNT(*) FROM video_performers").fetchone()[0] == 12 and c.execute("SELECT COUNT(*) FROM performers").fetchone()[0] == 1


def test_answers_are_kept_between_runs_and_a_miss_is_forgotten_sooner(tmp_path):
    db = _db(tmp_path)
    first = counting(tmdb)
    s1 = sid.make_screen(db, first)
    s1.search(si.parse_name("Dark.S01E01.mkv"))
    s1.search(si.parse_name("Nothing Known Here.mkv"))
    assert len(first.seen) >= 2
    second = counting(tmdb)
    s2 = sid.make_screen(db, second)                  # a new run, a new Screen: nothing in memory
    assert s2.search(si.parse_name("Dark.S01E01.mkv"))[0]["tmdb_id"] == 70523
    s2.search(si.parse_name("Nothing Known Here.mkv"))
    assert second.seen == []                          # answered from the database, hits and misses alike
    with db.lock:                                     # a miss older than a week is asked again; a hit of the same age is not
        db.conn.execute("UPDATE metadata_cache SET fetched_at=? WHERE provider='tmdb'", (time.time() - 8 * 24 * 3600,))
        db.conn.commit()
    third = counting(tmdb)
    s3 = sid.make_screen(db, third)
    s3.search(si.parse_name("Dark.S01E01.mkv"))
    assert third.seen == []
    s3.search(si.parse_name("Nothing Known Here.mkv"))
    assert third.seen != []


def test_titles_are_compared_by_words_and_retried_shorter_and_without_the_year():
    assert si.similarity("Matrix, The", "The Matrix") > 0.9 and si.similarity("The Office US", "Office") < 0.85
    screen = si.Screen(tmdb, tmdb="k" * 32)
    crown = screen.search(si.parse_name("The Crown Willsmania.mkv"))              # junk after the real title
    assert crown and crown[0]["title"] == "The Crown" and si.REVIEW <= crown[0]["score"] < si.ACCEPT      # found, but only a person confirms a shortened guess
    matrix = screen.search(si.parse_name("The Matrix (2005).mkv"))                # wrong year: found again without it
    assert matrix and matrix[0]["tmdb_id"] == 603


def test_an_id_in_the_name_settles_the_match_and_costs_no_search():
    p = si.parse_name("Movies/The Matrix {tmdb-603}/The.Matrix.1999.mkv")
    assert p["ids"]["tmdb"] == 603 and p["title"] == "The Matrix"
    i = si.parse_name("Heat [tmdbid=949] (1995).mkv")
    assert i["ids"]["tmdb"] == 949 and i["title"] == "Heat" and i["year"] == 1995
    assert si.parse_name("The.Matrix.tt0133093.mkv")["ids"]["imdb"] == "tt0133093"
    fetch = counting(tmdb)
    screen = si.Screen(fetch, tmdb="k" * 32)
    assert screen.search(p)[0]["source"] == "id" and fetch.seen == []
    found = screen.search(si.parse_name("anything tt0133093.mkv"))
    assert found[0]["tmdb_id"] == 603 and found[0]["source"] == "id"              # an IMDb id is turned into the TMDB title


def test_quality_of_a_copy_and_which_copy_comes_first():
    q = si.quality_of
    assert q("Film.2019.1080p.WEB-DL.x264.mkv") == {"resolution": "1080p", "height": 1080, "cam": False, "source": "WEB-DL"}
    assert q("Film.2019.HDCAM.mkv")["cam"] and q("Film DTS.mkv")["cam"] is False and q("Film.TS.x264.mkv")["cam"]
    assert q("clip.mkv", height=1088)["resolution"] == "1080p" and q("clip.mkv", height=0)["resolution"] == ""
    versions = [q("Film.CAM.2160p.mkv"), q("Film.720p.mkv"), q("Film.1080p.mkv")]
    assert [v["resolution"] for v in sorted(versions, key=si.version_sort_key)] == ["1080p", "720p", "2160p"]   # a recording never beats a real copy


def test_tvmaze_names_a_show_when_there_is_no_tmdb_key_and_the_person_allowed_it():
    def tvmaze(url, params, headers):
        if url.endswith("/search/shows"):
            return [{"score": 1, "show": {"id": 42, "name": "Dark", "premiered": "2017-12-01", "externals": {"imdb": "tt5753856"}, "image": {"medium": "https://img/d.jpg"}, "summary": "<p>A missing child.</p>"}}]
        if url.endswith("/shows/42"):
            return {"id": 42, "name": "Dark", "premiered": "2017-12-01", "genres": ["Drama"], "network": {"name": "Netflix"}, "externals": {"imdb": "tt5753856"}, "summary": "<p>A missing child.</p>", "image": {"original": "https://img/d.jpg"},
                    "_embedded": {"cast": [{"person": {"name": "Louis Hofmann", "image": {"medium": "https://img/l.jpg"}}, "character": {"name": "Jonas"}}],
                                  "episodes": [{"season": 1, "number": 1, "name": "Secrets", "airdate": "2017-12-01", "runtime": 50}, {"season": 1, "number": 2, "name": "Lies", "airdate": "2017-12-01"}]}}
        return {}
    fetch = counting(tvmaze)
    off = si.Screen(fetch)                                           # not switched on: no request at all
    assert off.search(si.parse_name("Dark.S01E01.mkv")) == [] and fetch.seen == []
    screen = si.Screen(fetch, tvmaze=True)
    r = si.identify(screen, "Dark.S01E01.mkv")
    assert r["state"] == "MATCHED" and r["best"]["tmdb_id"] == -42 and r["best"]["source"] == "tvmaze"
    d = screen.details("tv", -42)
    assert d["title"] == "Dark" and d["companies"] == ["Netflix"] and d["cast"][0]["name"] == "Louis Hofmann" and d["seasons"] == [{"season": 1, "episodes": 2, "name": "Season 1"}]
    assert d["overview"] == "A missing child."                       # the page's own markup is removed
    assert screen.episode(-42, 1, 2)["name"] == "Lies" and si.image_url("https://img/d.jpg") == "https://img/d.jpg"
    assert len([u for u in fetch.seen if u.endswith("/shows/42")]) == 1   # cast and episodes came in one request


def test_a_show_from_tvmaze_does_not_become_a_second_row_when_tmdb_has_it(tmp_path):
    db = _db(tmp_path)
    tmdb_row = sid.store_title(db, {"kind": "tv", "tmdb_id": 70523, "imdb_id": "tt5753856", "title": "Dark"})
    again = sid.store_title(db, {"kind": "tv", "tmdb_id": -42, "imdb_id": "tt5753856", "title": "Dark"})
    assert again == tmdb_row


def test_unmatched_videos_are_looked_at_again_only_when_the_name_reading_improved(tmp_path):
    db = _db(tmp_path)
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-9000000000001','Films','local',1,'sfw','videos','2026-01-01')")
        db.conn.commit()
    _media(db, ["old.mkv", "chosen.mkv", "current.mkv", "fresh.mkv"])
    with db.lock:
        for mid, state, chosen, parser in ((1, "UNMATCHED", 0, 1), (2, "UNMATCHED", 1, 0), (3, "UNMATCHED", 0, si.PARSER_VERSION)):
            db.conn.execute("INSERT INTO media_screen(media_id,state,chosen,parser,updated_at) VALUES(?,?,?,?,'x')", (mid, state, chosen, parser))
        db.conn.commit()
    assert sorted(r["filename"] for r in sid.pending(db)) == ["fresh.mkv", "old.mkv"]    # never-seen, and unmatched under an older reading; not a person's choice, not today's reading


def _client():
    from test_local_folders import _client as make
    return make


def _sfw_channel(db):
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-9000000000001','Films','local',1,'sfw','videos','2026-01-01')")
        db.conn.commit()


def test_movies_are_one_card_with_their_copies_best_first_and_cinema_prints_hidden(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", tmdb)
    db = _db(tmp_path)
    _sfw_channel(db)
    with db.lock:
        rows = [("The.Matrix.1999.720p.mkv", 720), ("The.Matrix.1999.1080p.WEB-DL.mkv", 1080), ("The.Matrix.1999.HDCAM.2160p.mkv", 2160), ("Heat.1995.HDTS.mkv", 480)]
        for i, (n, h) in enumerate(rows, 1):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,height,size,duration,status) VALUES(?,?,?,?,?,?,1000,7000,'SCANNED')", (i, "-9000000000001", i, n, n, h))
        db.conn.commit()
    screen = sid.make_screen(db, tmdb)
    for i in (1, 2, 3):
        sid.identify_one(db, screen, i, rows[i - 1][0], {"memo": {}, "entities": {}})
    sid.apply_match(db, 4, {"kind": "movie", "tmdb_id": 949, "title": "Heat", "year": 1995, "cast": [], "companies": [], "genres": []}, None)
    c, H = _client()(db)
    items = c.get("/api/screen/movies").json()["items"]
    matrix = next(m for m in items if m["title"] == "The Matrix")
    assert (matrix["versions"], matrix["best"], matrix["cam_only"]) == (2, "1080p", False)          # the recording is hidden, the other two are one card
    heat = next(m for m in items if m["title"] == "Heat")
    assert heat["cam_only"] is True and heat["versions"] == 1                                       # all there is is a recording: shown, and said so
    assert next(m for m in c.get("/api/screen/movies?cam=1").json()["items"] if m["title"] == "The Matrix")["versions"] == 3
    detail = c.get(f"/api/screen/movies/{matrix['id']}").json()
    assert [v["resolution"] for v in detail["versions"]] == ["1080p", "720p"] and detail["versions"][0]["mid"] == 2 and detail["versions"][0]["source"] == "WEB-DL"
    assert c.get("/api/screen/movies/9999").status_code == 404


def test_a_show_says_which_episodes_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", tmdb)
    db = _db(tmp_path)
    _sfw_channel(db)
    names = ["Dark.S01E01.mkv", "Dark.S02E03.mkv", "Dark.S02E04.mkv"]
    _media(db, names)
    screen = sid.make_screen(db, tmdb)
    ctx = {"memo": {}, "entities": {}}
    for i, n in enumerate(names, 1):
        sid.identify_one(db, screen, i, n, ctx)
    c, H = _client()(db)
    d = c.get(f"/api/screen/series/{c.get('/api/screen/series').json()['items'][0]['id']}").json()
    by = {s["season"]: s for s in d["seasons"]}
    assert by[1]["missing"] == [2, 3, 4, 5, 6, 7, 8, 9, 10] and by[2]["missing"] == [1, 2, 5, 6, 7, 8]


def test_tvmaze_is_a_switch_the_person_turns_on_and_ids_and_names_can_be_typed(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", tmdb)
    db = _db(tmp_path)
    _sfw_channel(db)
    _media(db, ["blurry.mkv"])
    c, H = _client()(db)
    assert c.get("/api/screen/settings").json()["tvmaze_enabled"] is False
    assert c.post("/api/screen/settings", json={"tvmaze_enabled": True}, headers=H).json()["saved"] == ["tvmaze_enabled"]
    assert c.get("/api/screen/settings").json()["tvmaze_enabled"] is True and sid.tvmaze_on(db)
    assert [t["title"] for t in c.get("/api/screen/search", params={"q": "dark", "kind": "tv"}).json()["items"]] == ["Dark"]
    assert c.get("/api/screen/search", params={"q": "tt0133093", "kind": "movie"}).json()["items"][0]["tmdb_id"] == 603
    assert c.get("/api/screen/search", params={"q": "x", "kind": "book"}).status_code == 400
    assert c.post("/api/screen/review/1", json={"kind": "movie"}, headers=H).status_code == 400                     # nothing to go on
    assert c.post("/api/screen/review/1", json={"kind": "movie", "imdb_id": "nonsense"}, headers=H).status_code == 400
    r = c.post("/api/screen/review/1", json={"kind": "movie", "imdb_id": "tt0133093"}, headers=H)
    assert r.status_code == 200 and r.json()["title"] == "The Matrix"
    with db.reading() as conn:
        assert conn.execute("SELECT state,chosen FROM media_screen WHERE media_id=1").fetchone()["chosen"] == 1
