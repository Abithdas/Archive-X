"""Torrent streams (Torrentio and the like) play through the person's own Stremio streaming server, and only through it."""
import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from telegram_archive.core.database import Database
from telegram_archive.metadata import addon_client as ac
from telegram_archive.metadata import streaming_server as ss
from test_screen_home import H, PUBLIC, _client

HASH = "0123456789abcdef0123456789abcdef01234567"
VIDEO = bytes(range(256)) * 40


class FakeStremioServer:
    """What stremio-service answers: /settings as JSON, and a video at /{hash}/{file} with range requests. Remembers what was asked."""

    def __init__(self, *, settings=True):
        self.asked: list[tuple[str, str]] = []
        me = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                me.asked.append((self.path, self.headers.get("Range", "")))
                if self.path == "/settings" and settings:
                    body = json.dumps({"values": {"cacheSize": 2147483648}}).encode()
                    self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                path = urllib.parse.urlsplit(self.path).path
                if len(path.split("/")) == 3 and len(path.split("/")[1]) == 40:
                    rng = self.headers.get("Range", "")
                    if rng.startswith("bytes="):
                        a, _, b = rng[6:].partition("-")
                        start, end = int(a or 0), int(b) if b else len(VIDEO) - 1
                        chunk = VIDEO[start:end + 1]
                        self.send_response(206); self.send_header("Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{len(VIDEO)}")
                    else:
                        chunk = VIDEO
                        self.send_response(200)
                    self.send_header("Content-Type", "video/mp4"); self.send_header("Content-Length", str(len(chunk))); self.send_header("Accept-Ranges", "bytes"); self.end_headers(); self.wfile.write(chunk)
                    return
                self.send_response(404); self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


@pytest.fixture
def server():
    s = FakeStremioServer()
    yield s
    s.close()


def app(tmp_path, monkeypatch, streams):
    manifest = {"name": "Torrentio", "resources": [{"name": "stream", "types": ["movie"], "idPrefixes": ["tt"]}], "types": ["movie"], "catalogs": []}

    def get_json(url):
        return manifest if url.endswith("/manifest.json") else ({"streams": streams} if "/stream/movie/tt0133093.json" in url else {})
    monkeypatch.setattr(ac, "get_json", get_json)
    monkeypatch.setattr(ac, "_resolve", lambda host: PUBLIC)
    db = Database(tmp_path / "t.db")
    db.set_setting("catalog_addons", [{"id": "0123456789", "url": "https://torrentio.example", "name": "Torrentio"}])
    return db, _client(db)


TORRENT = {"name": "Torrentio\n4k HDR", "title": "The.Matrix.1999.2160p\n👤 41 💾 12.3 GB ⚙️ SomeIndex", "infoHash": HASH.upper(), "fileIdx": 3,
           "sources": ["tracker:udp://tracker.example:1337/announce", "dht:" + HASH, "tracker:https://t2.example/announce"], "behaviorHints": {"bingeGroup": "torrentio|4k"}}


def test_a_torrent_stream_plays_through_the_streaming_server_with_ranges(tmp_path, monkeypatch, server):
    db, c = app(tmp_path, monkeypatch, [TORRENT])
    assert c.post("/api/screen/streaming-server", json={"url": server.url}, headers=H).json()["up"] is True
    got = c.get("/api/screen/streams/movie/tt0133093").json()
    (item,) = got["items"]
    assert item["kind"] == "play" and item["torrent"] is True and item["play"].startswith("/api/screen/stream/")
    assert "infoHash" not in item and "url" not in item and "trackers" not in item           # nothing of the torrent's inner address reaches the page
    assert (item["seeders"], item["provider"]) == (41, "SomeIndex") and item["size"] == int(12.3 * 1024 ** 3) and item["bingeGroup"] == "torrentio|4k"
    assert got["torrents"] is True and got["streaming_server"] == {"url": server.url, "up": True}
    r = c.get(item["play"], headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == VIDEO[10:20] and r.headers["content-range"] == f"bytes 10-19/{len(VIDEO)}"
    asked, rng = [a for a in server.asked if a[0].startswith(f"/{HASH}/")][-1]
    parts = urllib.parse.urlsplit(asked)
    assert parts.path == f"/{HASH}/3" and rng == "bytes=10-19"                              # the hash in lower case, the file the add-on named
    assert urllib.parse.parse_qs(parts.query)["tr"] == ["udp://tracker.example:1337/announce", "https://t2.example/announce"]   # trackers yes, "dht:" no


def test_without_a_running_server_the_torrent_is_listed_and_says_what_is_missing(tmp_path, monkeypatch):
    db, c = app(tmp_path, monkeypatch, [TORRENT])
    db.set_setting(ss.SETTING, "http://127.0.0.1:9")                                         # nothing listens there
    got = c.get("/api/screen/streams/movie/tt0133093").json()
    (item,) = got["items"]
    assert item["kind"] == "blocked" and item["torrent"] is True and item["host"] == "streaming server" and "play" not in item
    assert got["streaming_server"] == {"url": "http://127.0.0.1:9", "up": False}


def test_the_address_is_saved_checked_and_can_be_reset(tmp_path, monkeypatch, server):
    db, c = app(tmp_path, monkeypatch, [])
    first = c.get("/api/screen/streaming-server").json()
    assert first["url"] == ss.DEFAULT and first["saved"] is False and first["default"] == ss.DEFAULT
    for bad in ("https://example.com", "ftp://127.0.0.1", "http://user:pw@127.0.0.1:11470", "http://127.0.0.1:11470/x?y=1"):
        assert c.post("/api/screen/streaming-server", json={"url": bad}, headers=H).status_code == 400, bad
    saved = c.post("/api/screen/streaming-server", json={"url": server.url.replace("http://", "")}, headers=H).json()      # no scheme: http is assumed
    assert saved == {"url": server.url, "default": ss.DEFAULT, "saved": True, "up": True}
    assert db.setting(ss.SETTING) == server.url
    reset = c.post("/api/screen/streaming-server", json={"url": ""}, headers=H).json()
    assert reset["url"] == ss.DEFAULT and reset["saved"] is False


def test_only_torrent_links_of_the_configured_server_are_ever_opened_through_it(server):
    base = server.url
    ok = ss.torrent_url(base, HASH, 2, ["udp://t.example:1"])
    assert ss.is_torrent_url(base, ok) and ss.is_torrent_url(base, f"{base}/{HASH}/-1")
    for bad in (f"{base}/settings", f"{base}/{HASH}", f"{base}/{HASH}/2/extra", f"{base}/{HASH}/x", f"{base}/{HASH.upper()}/2",
                "http://127.0.0.1:1/" + HASH + "/2", f"http://user@{base[7:]}/{HASH}/2", f"{base}/{HASH}/2#frag", "https://example.com/" + HASH + "/2"):
        assert not ss.is_torrent_url(base, bad), bad
    with pytest.raises(ValueError):
        ss.open_torrent(base, f"{base}/settings")
    with pytest.raises(ValueError):
        ss.torrent_url(base, "not-a-hash", 1, [])


def test_hashes_files_and_trackers_are_read_strictly():
    b32 = "AEBAGBAFAYDQQCIKBMGA2DQPCAIREEYUCULBOGAZDINRYHI"[:32]
    assert ss.info_hash_of(HASH) == HASH and ss.info_hash_of(HASH.upper()) == HASH and ss.info_hash_of("abc") == "" and ss.info_hash_of(None) == ""
    assert len(ss.info_hash_of(b32)) == 40 and ss.info_hash_of("!" * 32) == ""
    assert ss.torrent_url(ss.DEFAULT, HASH, None, []).endswith(f"/{HASH}/-1")                 # no file named: the server picks the video
    assert ss.torrent_url(ss.DEFAULT, HASH, "7", []).endswith(f"/{HASH}/7") and ss.torrent_url(ss.DEFAULT, HASH, -5, []).endswith("/-1") and ss.torrent_url(ss.DEFAULT, HASH, 10 ** 9, []).endswith("/-1")
    assert ss.trackers_of(["tracker:udp://a.example:1", "udp://a.example:1", "javascript:alert(1)", "tracker:file:///etc/passwd", "dht:x", 5, None, "tracker:http://b.example/x"]) == ["udp://a.example:1", "http://b.example/x"]
    assert len(ss.trackers_of([f"udp://h{i}.example:1" for i in range(40)])) == ss.MAX_TRACKERS


def test_an_add_on_cannot_point_a_url_stream_at_the_streaming_server_or_this_pc(tmp_path, monkeypatch, server):
    sneaky = [{"name": "Sneaky settings", "url": f"{server.url}/settings"}, {"name": "Sneaky torrent path", "url": f"{server.url}/{HASH}/0"}]
    db, c = app(tmp_path, monkeypatch, sneaky)
    c.post("/api/screen/streaming-server", json={"url": server.url}, headers=H)
    got = c.get("/api/screen/streams/movie/tt0133093").json()
    assert [i["kind"] for i in got["items"]] == ["blocked", "blocked"]                       # a plain url on this PC needs the add-ons-on-this-PC switch, and is never the server's own
    assert not any(a[0] == "/settings" and a[1] for a in server.asked)


def test_the_streaming_server_is_an_sfw_feature_like_the_rest_of_the_add_ons(tmp_path, monkeypatch, server):
    db, c = app(tmp_path, monkeypatch, [TORRENT])
    assert c.post("/api/content-mode", json={"mode": "nsfw"}, headers=H).json()["mode"] == "nsfw"
    assert c.get("/api/screen/streaming-server").status_code == 409
    assert c.post("/api/screen/streaming-server", json={"url": server.url}, headers=H).status_code == 409
    assert c.get("/api/screen/streams/movie/tt0133093").status_code == 409
    assert not any(a[0].startswith(f"/{HASH}") for a in server.asked)


def test_a_server_that_does_not_answer_with_settings_is_not_taken_for_a_streaming_server():
    other = FakeStremioServer(settings=False)
    try:
        assert ss.reachable(other.url) is False
    finally:
        other.close()
    assert ss.reachable("http://127.0.0.1:9") is False
