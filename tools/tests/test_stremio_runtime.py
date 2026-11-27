"""Archive X sets up and runs Stremio's streaming server itself (like dx.bi): server.js from Stremio, Node.js verified against its published checksum, started in the background."""
import hashlib
import shutil
import time
import io
import tarfile
import zipfile

import pytest

from telegram_archive import config
from telegram_archive.metadata import stremio_runtime as sr

CARGO = '[package]\nname = "stremio-service"\n\n[package.metadata.server]\nversion = "v4.20.99"\n'


class Resp(io.BytesIO):
    def __init__(self, data):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "USER_DATA_DIR", tmp_path)
    sr._state.update(phase="idle", message="", percent=None, error="", finished=0.0)
    sr.stop()
    monkeypatch.setattr(sr, "_last_start", 0.0)
    return tmp_path


def opener_for(files):
    asked = []

    def opener(url):
        asked.append(url)
        for suffix, data in files.items():
            if url.endswith(suffix):
                return Resp(data)
        raise OSError(f"no such file: {url}")
    opener.asked = asked
    return opener


def test_node_archive_names_and_platforms(monkeypatch):
    assert sr.node_archive_name("win", "x64", "v20.1.0") == "node-v20.1.0-win-x64.zip"
    assert sr.node_archive_name("linux", "arm64", "v20.1.0") == "node-v20.1.0-linux-arm64.tar.xz"
    assert sr.node_archive_name("darwin", "x64", "v20.1.0") == "node-v20.1.0-darwin-x64.tar.gz"
    monkeypatch.setattr("platform.machine", lambda: "AMD64")
    assert sr.platform_key()[1] == "x64"
    monkeypatch.setattr("platform.machine", lambda: "riscv64")
    with pytest.raises(ValueError):
        sr.platform_key()


def test_the_node_to_use_is_ours_then_the_pcs_and_must_be_new_enough(home):
    ours = sr.bundled_node()
    ours.parent.mkdir(parents=True)
    ours.write_bytes(b"x")
    assert sr.find_node(run=lambda p: "v20.18.1\n", which=lambda n: "/usr/bin/node") == str(ours)
    assert sr.find_node(run=lambda p: "v14.0.0" if str(p) != "/usr/bin/node" else "v18.2.0", which=lambda n: "/usr/bin/node") == "/usr/bin/node"   # ours is too old
    assert sr.find_node(run=lambda p: "v10.0.0", which=lambda n: "/usr/bin/node") == ""
    assert sr.find_node(run=lambda p: "", which=lambda n: None) == ""


def test_server_js_comes_from_stremios_download_server_at_the_version_its_service_ships(home):
    body = b"x" * (200 * 1024)
    opener = opener_for({"/v4.20.99/desktop/server.js": body})
    assert sr.ensure_server_js(fetch=lambda url: CARGO, opener=opener) == "v4.20.99"
    assert sr.server_js().read_bytes() == body and (home / "streamingserver" / "version.txt").read_text() == "v4.20.99"
    assert opener.asked == ["https://dl.strem.io/server/v4.20.99/desktop/server.js"]
    again = opener_for({})
    assert sr.ensure_server_js(fetch=lambda url: CARGO, opener=again) == "v4.20.99" and not again.asked        # current: nothing downloaded


def test_a_tiny_download_is_an_error_page_not_a_server_and_an_old_copy_survives_a_failed_update(home):
    with pytest.raises(ValueError):
        sr.ensure_server_js(fetch=lambda url: CARGO, opener=opener_for({"server.js": b"<html>404</html>"}))
    assert not sr.server_js().exists()
    sr.server_js().parent.mkdir(parents=True, exist_ok=True)
    sr.server_js().write_bytes(b"old" * 50000)
    (home / "streamingserver" / "version.txt").write_text("v4.0.0")
    sr.ensure_server_js(fetch=lambda url: CARGO, opener=opener_for({}))                                         # the update fails: the old copy keeps working
    assert sr.server_js().read_bytes().startswith(b"old")


def test_without_a_version_online_the_known_good_one_is_fetched(home):
    opener = opener_for({"/v4.20.17/desktop/server.js": b"y" * (150 * 1024)})
    assert sr.ensure_server_js(fetch=lambda url: (_ for _ in ()).throw(OSError("offline")), opener=opener) == "v4.20.17"


def make_zip(inner="node-v20.18.1-win-x64/node.exe"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(inner, b"MZ-node")
        z.writestr("node-v20.18.1-win-x64/README.md", "x")
    return buf.getvalue()


def make_tar():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:xz") as t:
        data = b"ELF-node"
        info = tarfile.TarInfo("node-v20.18.1-linux-x64/bin/node")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize("system,blob", [("win", make_zip()), ("linux", make_tar())])
def test_node_is_downloaded_checked_against_the_published_checksum_and_unpacked(home, monkeypatch, system, blob):
    monkeypatch.setattr(sr, "platform_key", lambda: (system, "x64"))
    name = sr.node_archive_name(system, "x64")
    sums = f"{hashlib.sha256(blob).hexdigest()}  {name}\n{'0' * 64}  something-else.zip\n".encode()
    opener = opener_for({"SHASUMS256.txt": sums, name: blob})
    fetch = lambda url: sr.fetch_text(url, opener=opener)  # noqa: E731

    def run(p):                                  # not there before the download, working once it has been unpacked
        return "v20.18.1" if sr.bundled_node().is_file() else ""
    path = sr.ensure_node(fetch=fetch, opener=opener, run=run, which=lambda n: None)
    assert path == str(sr.bundled_node()) and sr.bundled_node().read_bytes() in (b"MZ-node", b"ELF-node")
    assert not (sr.directory() / "node" / name).exists()                                                         # the archive is removed


def test_a_node_download_that_does_not_match_its_checksum_is_never_used(home, monkeypatch):
    monkeypatch.setattr(sr, "platform_key", lambda: ("linux", "x64"))
    name = sr.node_archive_name("linux", "x64")
    blob = make_tar()
    opener = opener_for({"SHASUMS256.txt": f"{'a' * 64}  {name}\n".encode(), name: blob})
    with pytest.raises(ValueError, match="checksum"):
        sr.ensure_node(fetch=lambda url: sr.fetch_text(url, opener=opener), opener=opener, run=lambda p: "", which=lambda n: None)
    assert not sr.bundled_node().exists()


def test_the_pcs_own_node_is_used_and_nothing_is_downloaded(home):
    opener = opener_for({})
    assert sr.ensure_node(fetch=lambda url: "", opener=opener, run=lambda p: "v18.0.0", which=lambda n: "/usr/bin/node") == "/usr/bin/node"
    assert not opener.asked


def test_setup_reports_done_or_the_reason_it_failed_and_never_raises(home):
    body = b"x" * (200 * 1024)
    ok = sr.setup(fetch=lambda url: CARGO, opener=opener_for({"server.js": body}), run=lambda p: "v20.0.0", which=lambda n: "/usr/bin/node")
    assert ok["phase"] == "done" and ok["percent"] == 100 and ok["error"] == ""
    sr.server_js().unlink()
    bad = sr.setup(fetch=lambda url: (_ for _ in ()).throw(OSError("offline")), opener=opener_for({}), run=lambda p: "", which=lambda n: None)
    assert bad["phase"] == "failed" and bad["error"]


def test_a_second_setup_while_one_runs_just_reports_the_first(home):
    sr._state["phase"] = "working"
    assert sr.setup()["phase"] == "working"


def test_the_server_is_started_with_node_the_script_and_the_tools_and_stopped(home, monkeypatch):
    sr.server_js().parent.mkdir(parents=True, exist_ok=True)
    sr.server_js().write_bytes(b"x" * 1000)
    monkeypatch.setattr(sr, "find_node", lambda *a, **k: "/usr/bin/node")
    monkeypatch.setattr("telegram_archive.core.external_tools.ffmpeg", lambda: "/opt/ffmpeg")
    monkeypatch.setattr("telegram_archive.core.external_tools.ffprobe", lambda: "/opt/ffprobe")
    seen = {}

    class P:
        def __init__(self, args, **kw):
            seen.update(args=args, **kw)
            self.alive = True
        def poll(self): return None if self.alive else 0
        def terminate(self): self.alive = False
        def wait(self, timeout=None): return 0
        def kill(self): self.alive = False
    assert sr.start_process(spawn=P) is True and sr.running()
    assert seen["args"][0] == "/usr/bin/node" and seen["args"][-1] == str(sr.server_js())
    assert seen["env"]["FFMPEG_BIN"] == "/opt/ffmpeg" and seen["env"]["APP_PATH"] == str(sr.directory())
    assert sr.start_process(spawn=P) is False                       # already running: not started twice
    sr.stop()
    assert not sr.running()


def test_it_cannot_be_started_before_it_is_set_up(home, monkeypatch):
    monkeypatch.setattr(sr, "find_node", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="not set up"):
        sr.start_process()


def test_warm_starts_it_only_when_set_up_down_local_and_not_just_tried(home, monkeypatch):
    started = []
    monkeypatch.setattr(sr, "ready", lambda: True)
    monkeypatch.setattr(sr, "_safe_start", lambda: started.append(1))
    monkeypatch.setattr(sr.threading, "Thread", lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    assert sr.warm(True, up=True) is False and sr.warm(False, up=False) is False
    assert sr.warm(True, up=False) is True and started == [1]
    monkeypatch.setattr(sr, "_last_start", sr.time.monotonic())
    assert sr.warm(True, up=False) is False                         # tried a moment ago


def test_the_setup_routes_report_the_state_and_start_the_background_setup(tmp_path, monkeypatch):
    from test_screen_home import H, _client
    from telegram_archive.core.database import Database
    started = []
    monkeypatch.setattr(sr, "setup_in_background", lambda: (started.append(1), {**sr.state(), "phase": "working", "message": "Downloading the streaming server…"})[1])
    db = Database(tmp_path / "d.db")
    c = _client(db)
    got = c.post("/api/screen/streaming-server/setup", headers=H).json()
    assert got["ok"] is True and got["phase"] == "working" and started == [1]
    status = c.get("/api/screen/streaming-server").json()
    assert status["ready"] is False and "phase" in status["setup"]
    assert c.get("/api/screen/streaming-server/setup").json()["ready"] is False


def test_start_runs_the_server_the_app_set_up_when_no_other_one_is_installed(tmp_path, monkeypatch):
    from telegram_archive.metadata import streaming_server as ss
    monkeypatch.setattr(ss, "installed_program", lambda *a, **k: "")
    monkeypatch.setattr(sr, "ready", lambda: True)
    started = []
    monkeypatch.setattr(sr, "start_process", lambda: started.append(1) or True)
    answers = iter([False, False, True])
    out = ss.start("http://127.0.0.1:11470", probe=lambda b: next(answers), sleep=lambda s: None)
    assert out["up"] is True and out["started"] is True and started == [1]
    monkeypatch.setattr(sr, "ready", lambda: False)
    out = ss.start("http://127.0.0.1:11470", probe=lambda b: False, sleep=lambda s: None)
    assert out["found"] is False and "Set it up" in out["reason"]


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_a_real_node_process_is_started_answers_and_is_stopped(home, monkeypatch):
    """End to end with a stand-in for server.js: the real launch command, a real node process, the real reachability check, then the stop."""
    import socket
    from telegram_archive.metadata import streaming_server as ss
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    sr.server_js().parent.mkdir(parents=True, exist_ok=True)
    sr.server_js().write_text(
        "const http=require('http');http.createServer((q,r)=>{r.setHeader('Content-Type','application/json');r.end(JSON.stringify({values:{cacheSize:1}}))})"
        ".listen(Number(process.env.FAKE_PORT),'127.0.0.1');", encoding="utf-8")
    monkeypatch.setenv("FAKE_PORT", str(port))
    assert sr.ready() is True
    base = f"http://127.0.0.1:{port}"
    assert ss.reachable(base) is False
    assert sr.start_process() is True
    try:
        for _ in range(60):
            if ss.reachable(base):
                break
            time.sleep(0.1)
        assert ss.reachable(base) is True and sr.running()
    finally:
        sr.stop()
    assert not sr.running()
