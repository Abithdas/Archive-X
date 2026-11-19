import os
import sys
import tempfile
import time
from pathlib import Path

# before anything imports the app: keys these tests save must land in a temporary folder, never in the real data folder
os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axscreen_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
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
    if url.endswith("/tv/70523/season/2"):
        return {"episodes": [{"episode_number": 3, "name": "Dark Matter", "overview": "Ep.", "air_date": "2019-06-21", "runtime": 55}, {"episode_number": 4, "name": "Lies", "air_date": "2019-06-21"}]}
    if url.endswith("/tv/70523/season/1"):
        return {"episodes": [{"episode_number": 1, "name": "Secrets", "air_date": "2017-12-01"}]}
    if "/find/tt0133093" in url:
        return {"movie_results": [{"id": 603, "title": "The Matrix", "release_date": "1999-03-30"}], "tv_results": []}
    if url.endswith("/configuration"):
        return {"images": {}}
    return {}


def test_names_become_titles_years_and_episodes():
    def p(name):
        got = si.parse_name(name)
        got.pop("ids")
        return got
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
    c.post("/api/screen/settings", json={"cinemeta_enabled": False}, headers=H)             # Cinemeta is on by itself; with it off and no key there is nothing to ask
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


def test_a_show_lists_its_episodes_in_order_and_follows_the_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(sid, "http_fetch", fake)
    db = Database(tmp_path / "t.db")
    c, H = _client(db)
    db.set_setting("tmdb_api_key", "k" * 32)
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-9000000000001','Shows','local',1,'sfw','videos','2026-01-01')")
        for i, n in ((1, "Dark.S02E03.mkv"), (2, "Dark.S01E01.mkv")):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status,duration) VALUES(?,?,?,?,?,'SCANNED',3000)", (i, "-9000000000001", i, n, n))
        db.conn.commit()
    screen = sid.make_screen(db, fake)
    for i, n in ((1, "Dark.S02E03.mkv"), (2, "Dark.S01E01.mkv")):
        sid.identify_one(db, screen, i, n)
    listed = c.get("/api/screen/series").json()["items"]                # the app opens in SFW, and this folder is SFW
    assert [(x["title"], x["episodes"], x["seasons"]) for x in listed] == [("Dark", 2, 2)]
    detail = c.get(f"/api/screen/series/{listed[0]['id']}").json()
    assert [(se["season"], [e["episode"] for e in se["episodes"]]) for se in detail["seasons"]] == [(1, [1]), (2, [3])]
    assert detail["seasons"][1]["episodes"][0]["name"] == "Dark Matter" and detail["genres"] == ["Drama"] and detail["companies"] == ["Netflix"]
    assert detail["cast"][0]["name"] == "Louis Hofmann"
    assert c.get("/api/screen/series/999").status_code == 404
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    assert c.get("/api/screen/series").json()["items"] == []            # not shown in the other mode


def test_a_direct_link_to_something_the_mode_hides_is_refused(tmp_path):
    db = Database(tmp_path / "g.db")
    c, H = _client(db)
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1001','Adult','available',1,'nsfw','both','2026-01-01')")
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1002','Films','available',1,'sfw','both','2026-01-01')")
        for ch in ("-1001", "-1002"):
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status) VALUES(?,1,'a.mp4','a','SCANNED')", (ch,))
            db.conn.execute("INSERT INTO photo_library_items(channel_id,message_id,filename,size,width,height,cached_at,created_at,updated_at) VALUES(?,1,'p.jpg',1,1,1,'x','x','x')", (ch,))
        db.conn.execute("INSERT INTO performers(name,content_rating) VALUES('Adult Person','nsfw')")
        db.conn.execute("INSERT INTO performers(name,content_rating) VALUES('Film Actor','sfw')")
        db.conn.commit()
    hidden_paths = ["/stream/-1001/1", "/api/play/info/-1001/1", "/api/thumb/-1001/1", "/api/photos/-1001/1/thumbnail", "/api/channels/-1001/videos", "/api/performers/1/gallery", "/hls/-1001/1/index.m3u8"]
    assert [c.get(p).status_code for p in hidden_paths] == [404] * len(hidden_paths)           # the app opens in SFW
    assert c.get("/api/performers/2/gallery").status_code != 404                                # the SFW actor
    assert c.get("/api/play/info/-1002/1").status_code != 404                                  # the SFW film (its route may answer something else)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    import telegram_archive.core.content_guard as g
    g._cache.clear()
    assert [c.get(p).status_code == 404 for p in hidden_paths[:4]] == [False] * 4              # now these are the visible ones
    assert c.get("/api/play/info/-1002/1").status_code == 404                                  # and the SFW film is hidden


def test_an_nsfw_folder_is_identified_by_file_hash_and_a_folder_scan_picks_the_right_providers(tmp_path, monkeypatch):
    from telegram_archive.engines import local_identify_adult as adult
    big = tmp_path / "Private"
    big.mkdir()
    (big / "scene one.mp4").write_bytes(b"\x07" * 200_000)           # long enough to have a hash
    (big / "tiny.mp4").write_bytes(b"x")                              # too small to have one
    db = Database(tmp_path / "n.db")
    c, H = _client(db)
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    r = c.post("/api/local-folders", json={"path": str(big), "content_rating": "nsfw", "holds": "videos"}, headers=H)
    sid_ = r.json()["id"]
    ch = ls.channel_id(sid_)
    for _ in range(200):
        with db.reading() as conn:
            if conn.execute("SELECT COUNT(*) FROM local_video_files WHERE channel_id=? AND snapshots<>0", (ch,)).fetchone()[0] == 2:
                break
        time.sleep(0.05)
    time.sleep(0.3)
    assert c.post(f"/api/local-folders/{sid_}/identify", headers=H).status_code == 400             # no key yet: it says so
    db.set_setting("stashdb_api_key", "k" * 20)
    seen = []

    async def lookup(_db, file_hash):
        seen.append(file_hash)
        return {"source": "stashdb", "title": "Scene One", "performers": []}
    monkeypatch.setattr(adult, "_lookup", lookup)
    monkeypatch.setattr(adult.providers, "apply_scene_match", lambda _db, cid, mid, best, oshash=None: True)
    out = adult.run(db, ch)
    assert out["ok"] and (out["matched"], out["unmatched"]) == (1, 1) and len(seen) == 1 and len(seen[0]) == 16
    with db.reading() as conn:
        assert conn.execute("SELECT oshash FROM media WHERE filename='scene one.mp4'").fetchone()[0] == seen[0]
    assert c.post(f"/api/local-folders/{sid_}/identify", headers=H).json()["kind"] == "scene databases"


def test_sfw_channel_lists_show_no_nsfw_or_orphan_channels(tmp_path):
    db = Database(tmp_path / "l.db")
    c, H = _client(db)
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1001','Adult','available',1,'nsfw','both','2026-01-01')")
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1002','Films','available',1,'sfw','both','2026-01-01')")
        for ch in ("-1001", "-1002", "-1003"):          # -1003 has no channel row at all: the "Unknown channel" a library shows for orphans
            db.conn.execute("INSERT INTO media(source_channel,source_message_id,filename,title,status,streamable) VALUES(?,1,'a.mp4','a','SCANNED',1)", (ch,))
            db.conn.execute("INSERT INTO photo_library_items(channel_id,message_id,filename,size,width,height,cached_at,created_at,updated_at) VALUES(?,1,'p.jpg',1,1,1,'x','x','x')", (ch,))
        db.conn.commit()
    def names(rows):
        return sorted(str(r.get("title") or r.get("telegram_id")) for r in rows)
    assert names(c.get("/api/photos/channels").json()) == ["Films"]                      # SFW: no adult channel, no "Unknown channel"
    assert not {"Adult", "Unknown channel"} & set(names(c.get("/api/channels").json()))
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    assert names(c.get("/api/photos/channels").json()) == ["Adult", "Unknown channel"]   # NSFW: the adult channel and the orphan, not the film channel
