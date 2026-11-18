import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines import local_scanner as ls  # noqa: E402
from telegram_archive.engines import screen_identify as sid  # noqa: E402
from telegram_archive.metadata import screen_ids as si  # noqa: E402

from test_local_folders import _client  # noqa: E402


def fake(url, params, headers):
    """What TMDB would answer for the few titles these tests ask about."""
    if url.endswith("/search/movie"):
        q = params["query"].lower()
        if q == "the matrix":
            return {"results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "poster_path": "/m.jpg", "overview": "A hacker."},
                                {"id": 604, "title": "The Matrix Reloaded", "release_date": "2003-05-15"}]}
        if q == "some film":
            return {"results": [{"id": 9, "title": "Some Filming Documentary", "release_date": "2019-01-01"}]}
        return {"results": []}
    if url.endswith("/search/tv"):
        return {"results": [{"id": 70523, "name": "Dark", "first_air_date": "2017-12-01", "poster_path": "/d.jpg"}]} if params["query"].lower() == "dark" else {"results": []}
    if url.endswith("/movie/603"):
        return {"id": 603, "title": "The Matrix", "release_date": "1999-03-30", "overview": "A hacker learns the truth.", "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
                "production_companies": [{"name": "Warner Bros. Pictures"}], "runtime": 136, "vote_average": 8.2, "poster_path": "/m.jpg", "external_ids": {"imdb_id": "tt0133093"},
                "credits": {"cast": [{"id": 6384, "name": "Keanu Reeves", "character": "Neo", "profile_path": "/k.jpg"}, {"id": 2975, "name": "Laurence Fishburne", "character": "Morpheus"}]}}
    if url.endswith("/tv/70523"):
        return {"id": 70523, "name": "Dark", "first_air_date": "2017-12-01", "overview": "A missing child.", "genres": [{"name": "Drama"}], "networks": [{"name": "Netflix"}],
                "credits": {"cast": [{"id": 1, "name": "Louis Hofmann", "character": "Jonas"}]}, "seasons": [{"season_number": 2, "episode_count": 8, "name": "Season 2"}]}
    if url.endswith("/tv/70523/season/2/episode/3"):
        return {"name": "Dark Matter", "overview": "Ep.", "air_date": "2019-06-21", "runtime": 55}
    if url.endswith("/configuration"):
        return {"images": {}}
    return {}


def test_names_become_titles_years_and_episodes():
    p = si.parse_name
    assert p("Breaking.Bad.S02E05.1080p.BluRay.x264-GROUP.mkv") == {"kind": "episode", "title": "Breaking Bad", "year": None, "season": 2, "episode": 5}
    assert p("The.Matrix.1999.REMASTERED.1080p.BluRay.mkv")["title"] == "The Matrix" and p("The.Matrix.1999.REMASTERED.1080p.BluRay.mkv")["year"] == 1999
    assert p("2001 A Space Odyssey 1968.mkv")["title"] == "2001 A Space Odyssey"
    assert p("Shows/Dark/Season 2/07.mkv") == {"kind": "episode", "title": "Dark", "year": None, "season": 2, "episode": 7}
    assert p("The Office (US) - 3x07 - Branch Wars.mp4")["episode"] == 7


def test_the_file_hash_is_the_size_plus_the_ends(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"\x01\x00\x00\x00\x00\x00\x00\x00" * 16384)   # 128 KiB: both ends are the same 64 KiB
    h = si.oshash(str(f))
    assert len(h) == 16 and h == si.oshash(str(f))
    (tmp_path / "small").write_bytes(b"x")
    assert si.oshash(str(tmp_path / "small")) == ""


def test_a_clear_match_is_applied_and_an_unclear_one_waits_for_a_person(tmp_path):
    db = Database(tmp_path / "s.db")
    db.set_setting("tmdb_api_key", "k" * 32)
    screen = sid.make_screen(db, fake)
    with db.lock:
        for i, name in enumerate(["The.Matrix.1999.mkv", "Some Film (2019).mp4", "Nothing Known Here.mkv", "Dark.S02E03.mkv"], 1):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,?,'SCANNED')", (i, "-9000000000001", i, name, name))
        db.conn.commit()
    assert [sid.identify_one(db, screen, i, n) for i, n in [(1, "The.Matrix.1999.mkv"), (2, "Some Film (2019).mp4"), (3, "Nothing Known Here.mkv"), (4, "Dark.S02E03.mkv")]] == ["MATCHED", "REVIEW", "UNMATCHED", "MATCHED"]
    with db.reading() as c:
        m = c.execute("SELECT title,release_date,identification_state FROM media WHERE id=1").fetchone()
        assert (m["title"], m["release_date"], m["identification_state"]) == ("The Matrix (1999)", "1999-03-30", "MATCHED")
        assert [r["name"] for r in c.execute("SELECT p.name FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=1 ORDER BY p.name")] == ["Keanu Reeves", "Laurence Fishburne"]
        assert {r["content_rating"] for r in c.execute("SELECT content_rating FROM performers UNION ALL SELECT content_rating FROM studios UNION ALL SELECT content_rating FROM tags")} == {"sfw"}
        assert [r["name"] for r in c.execute("SELECT t.name FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=1 ORDER BY t.name")] == ["Action", "Science Fiction"]
        assert c.execute("SELECT title FROM media WHERE id=4").fetchone()["title"] == "Dark S02E03 · Dark Matter"
        assert c.execute("SELECT name FROM screen_episodes WHERE season=2 AND episode=3").fetchone()["name"] == "Dark Matter"
        states = {r["media_id"]: r["state"] for r in c.execute("SELECT media_id,state FROM media_screen")}
        assert states == {1: "MATCHED", 2: "REVIEW", 3: "UNMATCHED", 4: "MATCHED"}
    # the person settles the doubtful one; a later run leaves that answer alone
    sid.choose(db, 2, "movie", 603, fetch=fake)
    assert sid.identify_one.__name__ and sid._record(db, 2, "UNMATCHED", {"candidates": []}) is None
    with db.reading() as c:
        assert c.execute("SELECT state,chosen FROM media_screen WHERE media_id=2").fetchone()["state"] == "MATCHED"


def test_a_name_used_by_adult_content_is_not_taken_over(tmp_path):
    db = Database(tmp_path / "c.db")
    with db.lock:
        db.conn.execute("INSERT INTO tags(name,content_rating) VALUES('Drama','nsfw')")
        db.conn.commit()
    sfw = sid._entity(db, "tags", "Drama")
    with db.reading() as c:
        assert c.execute("SELECT content_rating FROM tags WHERE name='Drama'").fetchone()[0] == "nsfw"
        assert c.execute("SELECT name,content_rating FROM tags WHERE id=?", (sfw,)).fetchone()["name"] == "Drama (Genre)"


def test_routes_keys_run_and_review(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", fake)
    root = tmp_path / "Movies"
    root.mkdir()
    for n in ("The.Matrix.1999.1080p.mkv", "Some Film (2019).mp4"):
        (root / n).write_bytes(b"not really a video")
    db = Database(tmp_path / "r.db")
    c, H = _client(db)
    assert c.post("/api/screen/identify", json={}, headers=H).status_code == 400            # no key yet
    assert c.post("/api/screen/settings", json={"tmdb_api_key": "abcd1234efgh5678"}, headers=H).json()["saved"] == ["tmdb_api_key"]
    s = c.get("/api/screen/settings").json()
    assert s["tmdb_configured"] and s["tmdb_api_key"] == "abcd…5678" and not s["omdb_configured"]
    assert c.post("/api/screen/check", json={"provider": "tmdb"}, headers=H).json()["ok"] is True
    r = c.post("/api/local-folders", json={"path": str(root), "content_rating": "sfw", "holds": "videos"}, headers=H)
    assert r.status_code == 200, r.text
    ch = ls.channel_id(r.json()["id"])
    for _ in range(300):
        with db.reading() as conn:
            done = conn.execute("SELECT COUNT(*) FROM media_screen").fetchone()[0]
        if done == 2 and not sid.progress().get("running"):
            break
        time.sleep(0.05)
    st = c.get("/api/screen/status").json()
    assert st["counts"] == {"MATCHED": 1, "REVIEW": 1} and st["pending"] == 0
    review = c.get("/api/screen/review").json()["items"]
    assert [i["filename"] for i in review] == ["Some Film (2019).mp4"] and review[0]["candidates"][0]["title"] == "Some Filming Documentary"
    assert c.post(f"/api/screen/review/{review[0]['id']}", json={"kind": "movie", "tmdb_id": 603}, headers=H).json()["title"] == "The Matrix"
    assert c.post(f"/api/screen/review/{review[0]['id']}", json={"kind": "book", "tmdb_id": 1}, headers=H).status_code == 400
    assert c.get("/api/screen/review").json()["items"] == []
    with db.reading() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media WHERE source_channel=?", (ch,)).fetchone()[0] == 2
