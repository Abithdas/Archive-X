"""Stremio's streaming server, set up and run by Archive X itself, the way dx.bi does it.

The torrent engine is inside Stremio's `server.js` (the streaming server Stremio and its service run): it joins the swarm, fetches the pieces the player needs first and
serves the file over HTTP with range requests. Archive X has no torrent code of its own. This module fetches that file from Stremio's own download server, makes sure
a Node.js runtime is there to run it (the PC's own when it has one, otherwise the official portable build, checked against nodejs.org's published SHA-256), starts it in
the background without a console window and stops it when the app exits. Everything it downloads has a fixed address on dl.strem.io, nodejs.org or raw.githubusercontent.com;
nothing is built from text an add-on supplies.

Files live in the app's data folder under `streamingserver/`: `server.js`, `version.txt`, `node/` (only when the PC has no suitable Node), `hide-windows.js`, `server.log`
and the server's own cache (`APP_PATH`).
"""
from __future__ import annotations

import atexit
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

from .. import config

SERVER_JS_FALLBACK = "v4.20.17"
SERVER_JS_URL = "https://dl.strem.io/server/{version}/desktop/server.js"
CARGO_URL = "https://raw.githubusercontent.com/Stremio/stremio-service/refs/heads/master/Cargo.toml"
NODE_VERSION = "v20.18.1"
NODE_BASE = "https://nodejs.org/dist/{version}/"
MIN_SERVER_JS_BYTES = 100 * 1024          # a real server.js is several megabytes; anything tiny is an error page
MAX_SERVER_JS_BYTES = 60 * 1024 * 1024
MAX_NODE_BYTES = 160 * 1024 * 1024
MIN_NODE_MAJOR = 16
RESTART_AFTER = 60.0                      # an automatic start is attempted at most this often

_lock = threading.RLock()
_state: dict = {"phase": "idle", "message": "", "percent": None, "error": "", "finished": 0.0}
_proc: subprocess.Popen | None = None
_last_start = 0.0

HIDE_WINDOWS_JS = """"use strict";
const cp = require("child_process");
for (const name of ["spawn", "spawnSync", "execFile", "execFileSync", "exec", "execSync"]) {
    const original = cp[name];
    cp[name] = function (file, ...rest) {
        const at = Array.isArray(rest[0]) || (rest[0] == null && rest.length > 1) ? 1 : 0;
        const options = rest[at];
        if (options && typeof options === "object") rest[at] = { ...options, windowsHide: true };
        else if (options == null) rest[at] = { windowsHide: true };
        else rest.splice(at, 0, { windowsHide: true });
        return original.call(this, file, ...rest);
    };
}
"""


def directory() -> Path:
    return Path(config.USER_DATA_DIR) / "streamingserver"


def server_js() -> Path:
    return directory() / "server.js"


def platform_key() -> tuple[str, str]:
    """('win' | 'darwin' | 'linux', 'x64' | 'arm64'); ValueError for anything Node does not publish a build for."""
    import platform
    system = {"win32": "win", "darwin": "darwin"}.get(sys.platform, "linux")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("amd64", "x86_64", "x64") else ""
    if not arch:
        raise ValueError(f"No Node.js build for this processor ({machine or 'unknown'}).")
    return system, arch


def node_archive_name(system: str, arch: str, version: str = NODE_VERSION) -> str:
    ext = {"win": "zip", "darwin": "tar.gz", "linux": "tar.xz"}[system]
    return f"node-{version}-{system}-{arch}.{ext}"


def bundled_node() -> Path:
    return directory() / "node" / ("node.exe" if sys.platform == "win32" else "node")


def node_major(path: str | Path, run=None) -> int:
    """The major version of a node program, or 0 when it does not start."""
    try:
        out = (run or (lambda p: subprocess.run([str(p), "-v"], capture_output=True, text=True, timeout=10,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout))(path)
        m = re.match(r"v?(\d+)\.", (out or "").strip())
        return int(m.group(1)) if m else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


def find_node(run=None, which=shutil.which) -> str:
    """The Node.js to run the server with: the one this module downloaded, else the PC's own (version 16 or newer), else ''."""
    mine = bundled_node()
    if mine.is_file() and node_major(mine, run) >= MIN_NODE_MAJOR:
        return str(mine)
    system = which("node")
    if system and node_major(system, run) >= MIN_NODE_MAJOR:
        return system
    return ""


def ready() -> bool:
    """Can the streaming server be started from here: its server.js is present and there is a Node to run it."""
    return server_js().is_file() and bool(find_node())


def state() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


# ---- downloading ---------------------------------------------------------------------------------------------------------------------------------
def _open(url: str):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "ArchiveX"}), timeout=60)


def fetch_text(url: str, opener=_open, limit: int = 512 * 1024) -> str:
    with opener(url) as resp:
        return resp.read(limit).decode("utf-8", "replace")


def download(url: str, dest: Path, *, limit: int, label: str, opener=_open) -> str:
    """Download to `dest` (through a .part file, so a broken download never looks like a finished one) and return its SHA-256. Progress goes to the setup state."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    got = 0
    try:
        with opener(url) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                got += len(chunk)
                if got > limit:
                    raise ValueError(f"The {label} download is larger than expected.")
                digest.update(chunk)
                out.write(chunk)
                _set(message=f"Downloading {label}…", percent=min(99, int(100 * got / total)) if total else None)
        os.replace(part, dest)
        return digest.hexdigest()
    finally:
        part.unlink(missing_ok=True)


def latest_server_version(fetch=fetch_text) -> str:
    """The server.js version Stremio's service currently ships, or '' when it cannot be read."""
    try:
        m = re.search(r"\[package\.metadata\.server\][\s\S]*?version\s*=\s*\"([^\"]+)\"", fetch(CARGO_URL))
        return m.group(1) if m and re.fullmatch(r"v\d+\.\d+\.\d+", m.group(1)) else ""
    except Exception:
        return ""


def ensure_server_js(*, fetch=fetch_text, opener=_open) -> str:
    """server.js present and current; returns its version. An existing copy keeps working when the download fails."""
    version = latest_server_version(fetch)
    have = (directory() / "version.txt").read_text(encoding="utf-8").strip() if (directory() / "version.txt").is_file() else ""
    if server_js().is_file() and (not version or version == have):
        return have or version
    use = version or SERVER_JS_FALLBACK
    _set(message="Downloading the streaming server…", percent=None)
    tmp = directory() / "server.js.download"
    try:
        download(SERVER_JS_URL.format(version=use), tmp, limit=MAX_SERVER_JS_BYTES, label="the streaming server", opener=opener)
        if tmp.stat().st_size < MIN_SERVER_JS_BYTES:
            raise ValueError("The streaming server download was incomplete.")
        os.replace(tmp, server_js())
        (directory() / "version.txt").write_text(use, encoding="utf-8")
        return use
    except Exception:
        tmp.unlink(missing_ok=True)
        if server_js().is_file():
            return have
        raise


def _expected_sha(archive: str, fetch=fetch_text) -> str:
    for line in fetch(NODE_BASE.format(version=NODE_VERSION) + "SHASUMS256.txt").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == archive and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    raise ValueError("nodejs.org does not list a checksum for this download.")


def _extract_node(archive: Path, target: Path) -> None:
    """Take only the node program out of the archive (never a path from inside it)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                name = next((n for n in z.namelist() if n.endswith("/node.exe")), None)
                if not name:
                    raise ValueError("The Node.js download does not contain node.exe.")
                with z.open(name) as src, open(part, "wb") as out:
                    shutil.copyfileobj(src, out)
        else:
            with tarfile.open(archive) as t:
                member = next((m for m in t.getmembers() if m.isfile() and m.name.endswith("/bin/node")), None)
                if member is None:
                    raise ValueError("The Node.js download does not contain the node program.")
                src = t.extractfile(member)
                with open(part, "wb") as out:
                    shutil.copyfileobj(src, out)       # type: ignore[arg-type]
        os.chmod(part, 0o755)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)


def ensure_node(*, fetch=fetch_text, opener=_open, run=None, which=shutil.which) -> str:
    """A usable Node.js: the PC's own, or the official portable build downloaded and verified. Returns its path."""
    found = find_node(run, which)
    if found:
        return found
    system, arch = platform_key()
    archive = node_archive_name(system, arch)
    _set(message="Downloading Node.js (it runs the streaming server)…", percent=None)
    expected = _expected_sha(archive, fetch)
    dest = directory() / "node" / archive
    got = download(NODE_BASE.format(version=NODE_VERSION) + archive, dest, limit=MAX_NODE_BYTES, label="Node.js", opener=opener)
    try:
        if got != expected:
            raise ValueError("The Node.js download did not match its published checksum, so it was not used.")
        _set(message="Unpacking Node.js…", percent=None)
        _extract_node(dest, bundled_node())
    finally:
        dest.unlink(missing_ok=True)
    if node_major(bundled_node(), run) < MIN_NODE_MAJOR:
        raise ValueError("The downloaded Node.js does not start on this PC.")
    return str(bundled_node())


def _do_setup(fetch, opener, run, which) -> dict:
    try:
        ensure_server_js(fetch=fetch, opener=opener)
        ensure_node(fetch=fetch, opener=opener, run=run, which=which)
        _set(phase="done", message="The streaming server is set up.", percent=100, error="", finished=time.time())
    except Exception as exc:
        _set(phase="failed", message="", percent=None, error=str(exc)[:300] or type(exc).__name__, finished=time.time())
    return state()


def _claim() -> bool:
    with _lock:
        if _state["phase"] == "working":
            return False
        _state.update(phase="working", message="Starting…", percent=None, error="")
        return True


def setup(*, fetch=fetch_text, opener=_open, run=None, which=shutil.which) -> dict:
    """Make the streaming server runnable: server.js, then Node.js. Never raises; the outcome is in the state. While a setup is already running it just returns the state."""
    if not _claim():
        return state()
    return _do_setup(fetch, opener, run, which)


def setup_in_background() -> dict:
    if _claim():
        threading.Thread(target=_do_setup, args=(fetch_text, _open, None, shutil.which), name="streaming-server-setup", daemon=True).start()
    return state()


# ---- running it ----------------------------------------------------------------------------------------------------------------------------------
def running() -> bool:
    return _proc is not None and _proc.poll() is None


def launch_command() -> tuple[list[str], dict]:
    """(argv, env) to run the server: node, a small helper that keeps console windows from flashing on Windows, then server.js."""
    from ..core import external_tools
    node = find_node()
    if not node or not server_js().is_file():
        raise RuntimeError("The streaming server is not set up yet.")
    args = [node]
    if sys.platform == "win32":
        helper = directory() / "hide-windows.js"
        helper.write_text(HIDE_WINDOWS_JS, encoding="utf-8")
        args += ["--require", str(helper)]
    args.append(str(server_js()))
    env = dict(os.environ)
    env["APP_PATH"] = str(directory())           # the server keeps its torrent cache under this folder
    for key, tool in (("FFMPEG_BIN", external_tools.ffmpeg()), ("FFPROBE_BIN", external_tools.ffprobe())):
        if tool:
            env[key] = tool
    return args, env


def start_process(spawn=None) -> bool:
    """Start the server in the background (no console window). True when a new process was started."""
    global _proc, _last_start
    with _lock:
        if running():
            return False
        args, env = launch_command()
        _last_start = time.monotonic()
        directory().mkdir(parents=True, exist_ok=True)
        log = open(directory() / "server.log", "ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        _proc = (spawn or subprocess.Popen)(args, cwd=str(directory()), env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
        return True


def stop() -> None:
    global _proc
    with _lock:
        p, _proc = _proc, None
    if p is not None and p.poll() is None:
        try:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        except OSError:
            pass


def warm(base_is_local_default: bool, up: bool) -> bool:
    """Start it in the background when it is set up, not answering and was not just tried; for a title page that lists torrents. True when a start was begun."""
    if up or not base_is_local_default or running() or not ready() or time.monotonic() - _last_start < RESTART_AFTER:
        return False
    threading.Thread(target=lambda: _safe_start(), name="streaming-server-start", daemon=True).start()
    return True


def _safe_start() -> None:
    try:
        start_process()
    except Exception:
        pass


atexit.register(stop)
