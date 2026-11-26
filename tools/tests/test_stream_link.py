"""The desktop app's native player opens a listed stream by its address: only a stream this app listed."""
from test_torrent_streams import H, TORRENT, FakeStremioServer, app


def test_the_native_player_gets_the_address_of_a_listed_stream_and_nothing_else(tmp_path, monkeypatch):
    server = FakeStremioServer()
    try:
        db, c = app(tmp_path, monkeypatch, [TORRENT])
        c.post("/api/screen/streaming-server", json={"url": server.url}, headers=H)
        (item,) = c.get("/api/screen/streams/movie/tt0133093").json()["items"]
        token = item["play"].rsplit("/", 1)[1]
        got = c.get(f"/api/screen/stream-link/{token}")
        assert got.status_code == 200 and got.json()["url"].startswith(f"{server.url}/0123456789abcdef0123456789abcdef01234567/3")
        assert c.get("/api/screen/stream-link/not-a-listed-token").status_code == 404      # an address that was never listed is never given out
    finally:
        server.close()
