"""A listed stream a browser cannot play (MPEG-4 video, AC3 audio in MKV) is converted on this PC into an MP4 it can."""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from test_screen_home import H
from test_torrent_streams import HASH, TORRENT, app

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is not installed")


@pytest.fixture
def mkv(tmp_path):
    out = tmp_path / "odd.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=15:duration=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                    "-c:v", "mpeg4", "-c:a", "ac3", "-shortest", str(out)], check=True)
    return out.read_bytes()


class FileServer:
    def __init__(self, data):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/settings":
                    body = json.dumps({"values": {}}).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                    return
                rng = self.headers.get("Range", "")
                if rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start, end = int(a or 0), int(b) if b else len(data) - 1
                    chunk = data[start:end + 1]
                    self.send_response(206); self.send_header("Content-Range", f"bytes {start}-{start + len(chunk) - 1}/{len(data)}")
                else:
                    chunk = data
                    self.send_response(200)
                self.send_header("Content-Length", str(len(chunk))); self.send_header("Accept-Ranges", "bytes"); self.end_headers()
                try:
                    self.wfile.write(chunk)
                except OSError:
                    pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown(); self.httpd.server_close()


def _token(tmp_path, monkeypatch, server):
    db, c = app(tmp_path, monkeypatch, [TORRENT])
    c.post("/api/screen/streaming-server", json={"url": server.url}, headers=H)
    (item,) = c.get("/api/screen/streams/movie/tt0133093").json()["items"]
    return c, item["play"].rsplit("/", 1)[1]


def _probe(data, tmp_path):
    f = tmp_path / "out.mp4"
    f.write_bytes(data)
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(f)], capture_output=True, check=True)
    return json.loads(r.stdout)


def test_an_unplayable_stream_comes_out_as_h264_and_aac_mp4(tmp_path, monkeypatch, mkv):
    server = FileServer(mkv)
    try:
        c, token = _token(tmp_path, monkeypatch, server)
        info = c.get(f"/api/screen/stream-compat/{token}/info").json()
        assert info["vcodec"] == "mpeg4" and info["acodec"] == "ac3" and 3 < info["duration"] < 5
        got = c.get(f"/api/screen/stream-compat/{token}")
        assert got.status_code == 200 and got.headers["content-type"] == "video/mp4"
        streams = {s["codec_type"]: s["codec_name"] for s in _probe(got.content, tmp_path)["streams"]}
        assert streams == {"video": "h264", "audio": "aac"}
    finally:
        server.close()


def test_a_start_time_converts_only_what_comes_after_it(tmp_path, monkeypatch, mkv):
    server = FileServer(mkv)
    try:
        c, token = _token(tmp_path, monkeypatch, server)
        got = c.get(f"/api/screen/stream-compat/{token}", params={"t": 2})
        assert got.status_code == 200 and got.headers["x-compat-start"] == "2.000"
        assert float(_probe(got.content, tmp_path)["format"]["duration"]) < 2.6
    finally:
        server.close()


def test_only_a_listed_stream_can_be_converted(tmp_path, monkeypatch, mkv):
    server = FileServer(mkv)
    try:
        c, _ = _token(tmp_path, monkeypatch, server)
        assert c.get("/api/screen/stream-compat/not-a-listed-token").status_code == 404
        assert c.get("/api/screen/stream-compat/not-a-listed-token/info").status_code == 404
    finally:
        server.close()
