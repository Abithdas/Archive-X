"""The Download button on a torrent stream: the stream is queued for download into the Collection folder, with nothing else of the title looked for."""
import pytest

from telegram_archive.engines import collection as col
from telegram_archive.engines import collection_worker as cw
from telegram_archive.metadata import streaming_server as ss
from test_screen_home import H
from test_torrent_streams import HASH, TORRENT, app, server  # noqa: F401  (the fake add-on and streaming server)


@pytest.fixture
def listed(tmp_path, monkeypatch, server):  # noqa: F811
    db, c = app(tmp_path, monkeypatch, [TORRENT])
    db.set_setting("collection_dir", str(tmp_path / "Col"))
    kicks = []
    monkeypatch.setattr(cw.Worker, "kick", lambda self: kicks.append(1))
    monkeypatch.setattr(cw.Worker, "start", lambda self: kicks.append("start"))
    monkeypatch.setattr(cw, "meta_of_factory", lambda db_: (lambda kind, ident: {"name": "The Matrix", "releaseInfo": "1999", "poster": "https://img/m.jpg"}))
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    assert c.post("/api/screen/streaming-server", json={"url": server.url}, headers=H).json()["up"] is True
    item = c.get("/api/screen/streams/movie/tt0133093").json()["items"][0]
    return db, c, item, kicks


def test_a_listed_torrent_is_queued_with_its_own_hash_file_and_trackers_and_the_worker_is_woken(listed):
    db, c, item, kicks = listed
    got = c.post("/api/screen/stream-download", json={"play": item["play"], "kind": "movie", "ident": "tt0133093", "name": item["name"]}, headers=H)
    assert got.status_code == 200 and got.json()["ok"] is True
    assert kicks.count("start") == 1 and 1 in kicks                              # started and woken, although automatic downloads are off
    (title,) = col.overview(db)["titles"]
    assert (title["title"], title["year"], title["manual"], title["monitored"]) == ("The Matrix", 1999, True, False)
    row = db.conn.execute("SELECT state, info_hash, file_idx, trackers, release FROM collection_items").fetchone()
    assert (row["state"], row["info_hash"], row["file_idx"]) == ("DOWNLOADING", HASH, 3) and "tracker.example" in row["trackers"] and row["release"] == item["name"]


def test_the_same_stream_twice_is_a_plain_conflict(listed):
    _, c, item, _ = listed
    body = {"play": item["play"], "kind": "movie", "ident": "tt0133093", "name": item["name"]}
    assert c.post("/api/screen/stream-download", json=body, headers=H).status_code == 200
    again = c.post("/api/screen/stream-download", json=body, headers=H)
    assert again.status_code == 409 and "already downloading" in again.json()["detail"]


@pytest.mark.parametrize("body,status", [({"kind": "movie", "ident": "nope", "play": "/api/screen/stream/x"}, 400), ({"kind": "book", "ident": "tt0133093", "play": "/api/screen/stream/x"}, 400),
                                         ({"kind": "movie", "ident": "tt0133093", "play": "/api/screen/stream/unknown-token"}, 404),
                                         ({"kind": "series", "ident": "tt0133093", "play": "{play}"}, 400)])
def test_what_cannot_be_downloaded_is_refused(listed, body, status):
    _, c, item, _ = listed
    body = {k: (item["play"] if v == "{play}" else v) for k, v in body.items()}
    assert c.post("/api/screen/stream-download", json=body, headers=H).status_code == status


def test_it_is_an_sfw_feature_only(listed):
    _, c, item, _ = listed
    c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H)
    assert c.post("/api/screen/stream-download", json={"play": item["play"], "kind": "movie", "ident": "tt0133093"}, headers=H).status_code == 409


def test_a_download_can_be_deleted_and_a_failed_one_tried_again(listed):
    db, c, item, kicks = listed
    c.post("/api/screen/stream-download", json={"play": item["play"], "kind": "movie", "ident": "tt0133093", "name": item["name"]}, headers=H)
    iid = col.overview(db)["titles"][0]["items"][0]["id"]
    with db.lock:
        db.conn.execute("UPDATE collection_items SET state='FAILED', note='The download stalled.' WHERE id=?", (iid,))
        db.conn.commit()
    assert c.get("/api/collection").json()["titles"][0]["counts"]["FAILED"] == 1
    assert c.post(f"/api/collection/items/{iid}/retry", headers=H).status_code == 200
    assert col.overview(db)["titles"][0]["items"][0]["state"] == "DOWNLOADING"               # the same release again, not a new search
    assert c.delete(f"/api/collection/items/{iid}", headers=H).json() == {"ok": True}
    assert c.get("/api/collection").json()["titles"] == []
    assert c.delete(f"/api/collection/items/{iid}", headers=H).status_code == 404


def test_the_torrent_address_reads_back_as_it_was_built(server):  # noqa: F811
    url = ss.torrent_url(server.url, HASH, 3, ["udp://t.example:1", "https://t2.example/a"])
    assert ss.parse_torrent_url(server.url, url) == (HASH, 3, ["udp://t.example:1", "https://t2.example/a"])
    assert ss.parse_torrent_url(server.url, ss.torrent_url(server.url, HASH, None, [])) == (HASH, None, [])
    with pytest.raises(ValueError):
        ss.parse_torrent_url(server.url, server.url + "/settings")


def test_a_stream_carries_its_release_file_name_from_the_first_line_of_its_description():
    from telegram_archive.metadata import addon_client as ac
    got, _ = ac.classify_streams([{**TORRENT, "title": "The.End.of.Oak.Street.2026.1080p.WEB-DL\n👤 41 💾 1.5 GB ⚙️ ThePirateBay"}, {"infoHash": HASH, "name": "x"}], "Torrentio")
    assert got[0]["release"] == "The.End.of.Oak.Street.2026.1080p.WEB-DL" and got[0]["seeders"] == 41 and "release" not in got[1]


# ---- Continue watching starts the same torrent again ---------------------------------------------------------------------------------------------
def _play(c, item, **extra):
    body = {"kind": "movie", "ident": "tt0133093", "name": "The Matrix", "position": 600, "duration": 1000, "src": item["play"], **extra}
    return c.post("/api/screen/addon-progress", json=body, headers=H)


def test_the_torrent_that_was_playing_is_remembered_with_the_position_and_the_card_can_resume(listed):
    db, c, item, _ = listed
    assert _play(c, item).json() == {"ok": True, "kept": True}
    card = next(x for x in c.get("/api/screen/home").json()["continue"] if x.get("addon"))
    assert card["resume"] is True
    got = c.post("/api/screen/stream-resume", json={"kind": "movie", "ident": "tt0133093"}, headers=H)
    assert got.status_code == 200
    out = got.json()
    assert out["name"] == "The Matrix" and out["position"] == 600.0 and out["play"].startswith("/api/screen/stream/")
    again = c.get(out["play"], headers={"Range": "bytes=0-9"})                                      # the new address plays the same torrent through the server
    assert again.status_code == 206 and len(again.content) == 10


def test_a_save_without_the_stream_keeps_the_one_already_known_and_a_plain_stream_has_none(listed):
    db, c, item, _ = listed
    _play(c, item)
    c.post("/api/screen/addon-progress", json={"kind": "movie", "ident": "tt0133093", "name": "The Matrix", "position": 700, "duration": 1000}, headers=H)   # an older page: no src
    assert next(x for x in c.get("/api/screen/home").json()["continue"] if x.get("addon"))["resume"] is True
    _play(c, item, ident="tt0111161", name="Other", src="/api/screen/stream/unknown")                 # a stream that is not a listed torrent: nothing to resume
    other = next(x for x in c.get("/api/screen/home").json()["continue"] if x.get("addon") and x["addon"]["ident"] == "tt0111161")
    assert other["resume"] is False
    assert c.post("/api/screen/stream-resume", json={"kind": "movie", "ident": "tt0111161"}, headers=H).status_code == 404


def test_resuming_says_plainly_when_the_streaming_server_is_not_running_or_nothing_is_remembered(listed):
    db, c, item, _ = listed
    assert c.post("/api/screen/stream-resume", json={"kind": "movie", "ident": "tt0133093"}, headers=H).status_code == 404
    _play(c, item)
    db.set_setting(ss.SETTING, "http://127.0.0.1:9")                                                  # nothing listens there now
    down = c.post("/api/screen/stream-resume", json={"kind": "movie", "ident": "tt0133093"}, headers=H)
    assert down.status_code == 409 and "not running" in down.json()["detail"]
    for bad in ({"kind": "book", "ident": "tt0133093"}, {"kind": "movie", "ident": "nope"}, {"kind": "series", "ident": "tt0133093", "season": "x"}):
        assert c.post("/api/screen/stream-resume", json=bad, headers=H).status_code == 400


def test_resuming_is_an_sfw_feature_only(listed):
    _, c, item, _ = listed
    _play(c, item)
    c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H)
    assert c.post("/api/screen/stream-resume", json={"kind": "movie", "ident": "tt0133093"}, headers=H).status_code == 409
