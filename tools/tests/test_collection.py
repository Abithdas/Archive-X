"""The Collection: what is wanted, the release chosen, the file downloaded through the streaming server and filed, and the rounds that tie it together."""
import json
import threading
from datetime import date

import pytest

from telegram_archive.core.database import Database
from telegram_archive.engines import collection as col
from telegram_archive.engines import collection_rules as rules
from telegram_archive.engines import screen_identify as sid
from test_torrent_streams import FakeStremioServer, HASH, VIDEO

TODAY = date(2026, 11, 28)
BIG = bytes(range(256)) * 8192            # 2 MB: more than the smallest download that is believed


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "c.db")
    d.set_setting("collection_dir", str(tmp_path / "Collection"))
    return d


def meta_series(*eps):
    return {"name": "Dark", "videos": [{"season": s, "episode": e, "title": f"Ep {e}", "released": r} for s, e, r in eps]}


def library(db, imdb, kind, pairs):
    """Matched library files for a title: (season, episode) pairs."""
    tid = sid.store_title(db, {"kind": "tv" if kind == "series" else "movie", "tmdb_id": abs(hash(imdb)) % 10 ** 6, "imdb_id": imdb, "title": "T", "year": 2020})
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-9000000000009','L','local',1,'sfw','videos','2026-01-01')")
        for n, (s, e) in enumerate(pairs, 100):
            db.conn.execute("INSERT INTO media(id,source_channel,source_message_id,filename,title,status) VALUES(?,?,?,?,?,'SCANNED')", (n, "-9000000000009", n, f"f{n}.mkv", f"f{n}"))
            db.conn.execute("INSERT INTO media_screen(media_id,title_id,season,episode,state,confidence,updated_at) VALUES(?,?,?,?,'MATCHED',1,'2026-01-01')", (n, tid, s or None, e or None))
        db.conn.commit()


def title(db, kind="series", imdb="tt5753856", scope="all", **kw):
    col.add_title(db, kind, imdb, kw.get("name", "Dark"), 2017, "", scope)
    with db.lock:
        db.conn.execute("UPDATE collection_titles SET added_at=? WHERE imdb=?", (kw.get("added", "2026-01-01T00:00:00"), imdb))
        db.conn.commit()


def items(db):
    return [dict(r) for r in db.conn.execute("SELECT * FROM collection_items ORDER BY season, episode").fetchall()]


# ---- what is wanted ------------------------------------------------------------------------------------------------------------------------------
def test_a_show_wants_every_aired_episode_the_library_lacks_and_not_the_ones_to_come(db):
    title(db)
    library(db, "tt5753856", "series", [(1, 1)])
    meta = meta_series((1, 1, "2020-01-01"), (1, 2, "2020-01-08"), (1, 3, "2026-12-25"), (0, 1, "2020-01-01"), (1, 4, ""))
    assert col.refresh(db, lambda k, i: meta, today=TODAY) == 1
    assert [(i["season"], i["episode"], i["state"]) for i in items(db)] == [(1, 2, "WANTED")]      # 1x1 is here, 1x3 has not aired, specials and undated are left out


def test_a_show_kept_from_now_on_ignores_episodes_that_aired_before(db):
    title(db, scope="new", added="2026-11-20T00:00:00")
    meta = meta_series((1, 1, "2020-01-01"), (1, 2, "2026-11-25"))
    col.refresh(db, lambda k, i: meta, today=TODAY)
    assert [(i["season"], i["episode"]) for i in items(db)] == [(1, 2)]


def test_a_movie_waits_for_its_release_plus_two_weeks_and_is_not_wanted_when_the_library_has_it(db):
    title(db, "movie", "tt0133093", name="The Matrix")
    assert col.refresh(db, lambda k, i: {"released": "2026-11-20"}, today=TODAY, force=True) == 0           # a cam-recording season
    assert col.refresh(db, lambda k, i: {"released": "2026-10-01"}, today=TODAY, force=True) == 1
    title(db, "movie", "tt0111161", name="Shawshank")
    library(db, "tt0111161", "movie", [(0, 0)])
    col.refresh(db, lambda k, i: {"released": "1994-09-23"}, today=TODAY, force=True)
    assert [i["title_id"] for i in items(db)] == [1]


def test_a_title_is_read_again_only_after_a_few_hours_and_an_item_the_library_got_meanwhile_is_done(db):
    title(db)
    calls = []
    meta = meta_series((1, 1, "2020-01-01"))
    col.refresh(db, lambda k, i: calls.append(1) or meta, today=TODAY)
    col.refresh(db, lambda k, i: calls.append(1) or meta, today=TODAY)
    assert len(calls) == 1
    library(db, "tt5753856", "series", [(1, 1)])
    col.refresh(db, lambda k, i: meta, today=TODAY, force=True)
    assert [(i["state"], i["note"]) for i in items(db)] == [("DONE", "In your library")]


# ---- finding ---------------------------------------------------------------------------------------------------------------------------------------
def cand(name, h, seeders=50, height=1080, size=2 * rules.GB, **kw):
    return {"kind": "torrent", "name": name, "infoHash": h * 40, "seeders": seeders, "height": height, "size": size, "trackers": ["udp://t:1"], **kw}


def one_item(db, **kw):
    title(db)
    col.refresh(db, lambda k, i: meta_series((1, 1, "2020-01-01")), today=TODAY)
    return items(db)[0]


def test_the_best_release_is_chosen_and_remembered_on_the_item(db):
    it = one_item(db)
    asked = []
    finder = lambda kind, imdb, s, e: asked.append((kind, imdb, s, e)) or [cand("Dark S01E01 720p", "a", 900, 720), cand("Dark S01E01 1080p", "b", 40), cand("Dark.S01E01.HDCAM", "c", 900)]  # noqa: E731
    assert col.search_item(db, it, finder) is True
    got = items(db)[0]
    assert asked == [("series", "tt5753856", 1, 1)]
    assert (got["state"], got["info_hash"], got["attempts"]) == ("DOWNLOADING", "b" * 40, 1) and json.loads(got["trackers"]) == ["udp://t:1"]


def test_nothing_found_backs_off_and_says_so(db):
    it = one_item(db)
    assert col.search_item(db, it, lambda *a: []) is False
    got = items(db)[0]
    assert got["state"] == "WANTED" and got["note"] == "No release found yet" and got["next_at"] and got["attempts"] == 1
    col.search_item(db, {**it, "attempts": 4}, lambda *a: [cand("Dark 1080p", "d", 0)])
    assert "No acceptable release" in items(db)[0]["note"]
    assert col.backoff_hours(1) == 1 and col.backoff_hours(99) == 24


def test_an_add_on_that_fails_is_reported_not_raised(db):
    it = one_item(db)
    def boom(*a):
        raise OSError("down")
    assert col.search_item(db, it, boom) is False and "could not be asked" in items(db)[0]["note"]


def test_a_movie_has_a_larger_size_limit_than_an_episode(db):
    title(db, "movie", "tt0133093", name="M")
    col.refresh(db, lambda k, i: {"released": "2000-01-01"}, today=TODAY)
    it = items(db)[0]
    big = [cand("M 1080p", "e", 90, size=9 * rules.GB)]
    assert col.search_item(db, it, lambda *a: big) is True                                                    # 9 GB: fine for a film (limit 12)
    col.fail_item(db, items(db)[0], "x")
    title(db, imdb="tt0000001", name="S")
    col.refresh(db, lambda k, i: meta_series((1, 1, "2020-01-01")), today=TODAY, force=True)
    ep = [i for i in items(db) if i["season"] == 1][0]
    assert col.search_item(db, ep, lambda *a: big) is False                                                   # the same 9 GB is too much for an episode (limit 4)


# ---- downloading -----------------------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def server():
    s = FakeStremioServer()
    yield s
    s.close()


def chosen(db):
    it = one_item(db)
    col.search_item(db, it, lambda *a: [{**cand("Dark S01E01 1080p", "x"), "infoHash": HASH, "fileIdx": 3}])
    return items(db)[0]


def test_the_file_is_downloaded_through_the_streaming_server_and_filed_under_the_show(db, server, monkeypatch):
    import telegram_archive.metadata.streaming_server as ss
    monkeypatch.setattr(ss, "reachable", lambda base: True)
    big = BIG
    class Resp:
        def __init__(self, data): self.data, self.pos = data, 0; self.headers = {"Content-Length": str(len(data)), "Content-Type": "video/x-matroska"}; self.status = 200
        def read(self, n): chunk = self.data[self.pos:self.pos + n]; self.pos += len(chunk); return chunk
        def __enter__(self): return self
        def __exit__(self, *a): return False
    opened = []
    it = chosen(db)
    path = col.download_item(db, it, server.url, open_torrent=lambda base, url, rng: opened.append((url, rng)) or Resp(big))
    assert path.replace("\\", "/").endswith("/Series/Dark (2017)/Season 01/Dark - S01E01.mkv")
    assert open(path, "rb").read() == big and opened[0][1] == "" and f"/{HASH}/3" in opened[0][0]
    assert not col._part_path(db, it["id"]).exists()


def test_a_download_that_was_interrupted_resumes_where_it_stopped(db, server):
    it = chosen(db)
    col._part_path(db, it["id"]).parent.mkdir(parents=True, exist_ok=True)
    col._part_path(db, it["id"]).write_bytes(BIG[:1024 * 1024])
    seen = {}
    class Resp:
        status = 206
        headers = {"Content-Length": str(len(BIG) - 1024 * 1024), "Content-Range": f"bytes {1024 * 1024}-{len(BIG) - 1}/{len(BIG)}", "Content-Type": "video/mp4"}
        pos = 1024 * 1024
        def read(self, n): chunk = BIG[self.pos:self.pos + n]; self.pos += len(chunk); return chunk
        def __enter__(self): return self
        def __exit__(self, *a): return False
    path = col.download_item(db, it, server.url, open_torrent=lambda base, url, rng: seen.setdefault("range", rng) and Resp())
    assert seen["range"] == f"bytes={1024 * 1024}-" and open(path, "rb").read() == BIG and path.endswith(".mp4")


def test_a_stop_keeps_the_partial_file_and_the_item_stays_downloading(db, server):
    it = chosen(db)
    class Resp:
        status, headers, pos = 200, {"Content-Length": str(len(BIG)), "Content-Type": "video/mp4"}, 0
        def read(self, n): chunk = BIG[self.pos:self.pos + n]; self.pos += len(chunk); return chunk
        def __enter__(self): return self
        def __exit__(self, *a): return False
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 1
    with pytest.raises(col.Stopped):
        col.download_item(db, it, server.url, open_torrent=lambda *a: Resp(), should_stop=stop)
    assert col._part_path(db, it["id"]).exists() and items(db)[0]["state"] == "DOWNLOADING"


def test_failures_are_plain_and_a_failed_release_is_never_tried_again(db, server):
    it = chosen(db)
    class Short:
        status, headers = 200, {"Content-Length": str(10 * 1024 * 1024)}
        def read(self, n): return b"" if getattr(self, "done", False) else (setattr(self, "done", True) or b"x" * 2048)
        def __enter__(self): return self
        def __exit__(self, *a): return False
    with pytest.raises(RuntimeError, match="ended early"):
        col.download_item(db, it, server.url, open_torrent=lambda *a: Short())
    col.fail_item(db, it, "The download ended early.")
    got = items(db)[0]
    assert got["state"] == "WANTED" and got["info_hash"] == "" and HASH in json.loads(got["avoid"]) and got["note"] == "The download ended early." and not col._part_path(db, it["id"]).exists()
    assert col.search_item(db, {**got, "next_at": ""}, lambda *a: [{**cand("same", "x"), "infoHash": HASH}]) is False      # the only release is the one that failed


def test_an_unreachable_server_is_a_plain_reason(db):
    it = chosen(db)
    def refuse(*a):
        raise OSError("connection refused")
    with pytest.raises(RuntimeError, match="could not open the torrent"):
        col.download_item(db, it, "http://127.0.0.1:9", open_torrent=refuse)


# ---- a round ---------------------------------------------------------------------------------------------------------------------------------------
def test_a_round_finds_downloads_and_brings_the_file_into_the_library(db, server):
    title(db)
    imported = []
    class Resp:
        status, headers, pos = 200, {"Content-Length": str(len(BIG)), "Content-Type": "video/x-matroska"}, 0
        def read(self, n): chunk = BIG[self.pos:self.pos + n]; self.pos += len(chunk); return chunk
        def __enter__(self): return self
        def __exit__(self, *a): return False
    out = col.tick(db, meta_of=lambda k, i: meta_series((1, 1, "2020-01-01")), finder=lambda *a: [{**cand("Dark S01E01 1080p", "x"), "infoHash": HASH, "fileIdx": 1}],
                   server_base=server.url, ensure_server=lambda: (True, ""), download=lambda d, it, base, **kw: col.download_item(d, it, base, open_torrent=lambda *a: Resp(), **kw),
                   import_files=lambda d, root: imported.append(root), today=TODAY)
    assert out == {"new": 1, "searched": 1, "downloaded": 1} and imported == [col.settings(db)["dir"]]
    got = items(db)[0]
    assert got["state"] == "DONE" and got["path"].endswith("Dark - S01E01.mkv")


def test_a_round_does_not_download_when_the_streaming_server_cannot_be_reached(db):
    title(db)
    out = col.tick(db, meta_of=lambda k, i: meta_series((1, 1, "2020-01-01")), finder=lambda *a: [{**cand("Dark 1080p", "x"), "infoHash": HASH}], server_base="http://127.0.0.1:9",
                   ensure_server=lambda: (False, "The streaming server is not running."), import_files=lambda d, r: None, today=TODAY)
    assert out["downloaded"] == 0
    got = items(db)[0]
    assert got["state"] == "DOWNLOADING" and "not running" in got["note"]


def test_a_failed_download_goes_back_to_wanted_and_the_round_goes_on(db, server):
    title(db)
    def broken(d, it, base, **kw):
        raise RuntimeError("The download stalled.")
    out = col.tick(db, meta_of=lambda k, i: meta_series((1, 1, "2020-01-01")), finder=lambda *a: [{**cand("Dark 1080p", "x"), "infoHash": HASH}], server_base=server.url,
                   ensure_server=lambda: (True, ""), download=broken, import_files=lambda d, r: None, today=TODAY)
    assert out["downloaded"] == 0 and items(db)[0]["state"] == "WANTED" and items(db)[0]["note"] == "The download stalled."


def test_a_round_that_is_told_to_stop_does_nothing_more(db, server):
    title(db)
    stop = threading.Event()
    stop.set()
    out = col.tick(db, meta_of=lambda k, i: meta_series((1, 1, "2020-01-01")), finder=lambda *a: [], server_base=server.url, ensure_server=lambda: (True, ""), stop=stop,
                   import_files=lambda d, r: None, today=TODAY)
    assert out["searched"] == 0 and out["downloaded"] == 0


# ---- the folder ------------------------------------------------------------------------------------------------------------------------------------
def test_the_collection_folder_becomes_an_sfw_library_folder_once(db, tmp_path):
    root = str(tmp_path / "Collection")
    sid_ = col.ensure_source(db, root)
    assert sid_ and col.ensure_source(db, root) == sid_ and col.ensure_source(db, root + "/Series/x") == sid_       # found again, and a folder inside is covered
    row = db.conn.execute("SELECT content_rating, holds FROM channels WHERE role='local'").fetchone()
    assert (row["content_rating"], row["holds"]) == ("sfw", "videos")
    other = tmp_path / "Library"
    (other / "Collection").mkdir(parents=True)
    col.ensure_source(db, str(other / "Collection"))
    assert col.ensure_source(db, str(other)) is None                                                               # it would swallow a folder that is already a source


def test_settings_validate_and_the_overview_lists_titles_with_counts(db):
    assert col.settings(db)["on"] is False and col.settings(db)["quality"] == 1080
    out = col.save_settings(db, {"on": True, "quality": 720, "max_gb_movie": 20})
    assert out["on"] is True and out["quality"] == 720 and out["max_gb_movie"] == 20
    with pytest.raises(ValueError):
        col.save_settings(db, {"quality": 999})
    with pytest.raises(ValueError):
        col.save_settings(db, {"dir": str(__import__("telegram_archive").config.USER_DATA_DIR)})
    title(db)
    col.refresh(db, lambda k, i: meta_series((1, 1, "2020-01-01"), (1, 2, "2020-01-08")), today=TODAY)
    ov = col.overview(db)
    assert ov["titles"][0]["counts"]["WANTED"] == 2 and ov["titles"][0]["items"][0]["label"] == "S01E01 · Ep 1"
    assert col.remove_title(db, ov["titles"][0]["id"]) is True and col.overview(db)["titles"] == [] and items(db) == []
