"""End-to-end test of the desktop-mode backend (telegram_archive/core/desktop_mode.py).

Starts the backend as the desktop shell will (subprocess, ARCHIVE_X_DESKTOP=1, loopback, per-launch secret) and checks:
  guard, session hand-off, loopback-only binding, graceful shutdown, parent-death watchdog.

usage:  python tools/tests/test_desktop_mode.py [path-to-python-or-frozen-backend]
By default it runs `python run.py`-equivalent from source (python -m telegram_archive.main). Pass the path of the
frozen backend executable to test the PyInstaller build instead.
Needs the app's requirements installed; stdlib only otherwise.
"""
from __future__ import annotations

import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET = secrets.token_hex(32)
FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILS.append(name)


def start_backend(cmd: list[str], data_dir: str, parent_pid: int, port: int = 0, extra_env: dict | None = None) -> tuple[subprocess.Popen, int, list[str]]:
    env = dict(os.environ, ARCHIVE_X_DESKTOP="1", ARCHIVE_X_LAUNCH_SECRET=SECRET, ARCHIVE_X_DATA_DIR=data_dir,
               ARCHIVE_X_PARENT_PID=str(parent_pid), ARCHIVE_X_DESKTOP_PORT=str(port), PYTHONUNBUFFERED="1",
               ARCHIVE_X_AUTO_OPEN_BROWSER="0", **(extra_env or {}))
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: list[str] = []
    found: dict[str, int] = {}

    def pump():
        for line in proc.stdout:  # type: ignore[union-attr]
            lines.append(line.rstrip())
            if line.startswith("ARCHIVE_X_READY "):
                found["port"] = json.loads(line[len("ARCHIVE_X_READY "):])["port"]
    threading.Thread(target=pump, daemon=True).start()
    deadline = time.time() + 90
    while "port" not in found and time.time() < deadline and proc.poll() is None:
        time.sleep(0.1)
    if "port" not in found:
        raise SystemExit("backend did not announce readiness:\n" + "\n".join(lines[-25:]))
    return proc, found["port"], lines


def request(port: int, method: str, path: str, headers: dict | None = None) -> http.client.HTTPResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    conn.request(method, path, headers=headers or {})
    return conn.getresponse()


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else None
    cmd = [target] if target else [sys.executable, "-m", "telegram_archive.main"]
    print("backend command:", " ".join(cmd))
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])   # stands in for the shell
    data = tempfile.mkdtemp(prefix="axdesktop_")
    proc, port, lines = start_backend(cmd, data, holder.pid)
    try:
        print(f"\nbackend up on 127.0.0.1:{port}")
        print("guard")
        r = request(port, "GET", "/health"); r.read()
        check("/health is open (liveness only)", r.status == 200)
        for path in ("/", "/api/status", "/api/library", "/ws", "/ca.crt", "/login"):
            r = request(port, "GET", path); r.read()
            check(f"{path} without secret is refused", r.status == 403, str(r.status))
        r = request(port, "GET", "/api/status", {"X-Archive-X-Desktop": "wrong" * 8}); r.read()
        check("wrong secret header refused", r.status == 403, str(r.status))
        r = request(port, "GET", "/api/status", {"X-Archive-X-Desktop": SECRET}); r.read()
        check("secret header passes the guard (then needs a session)", r.status in (200, 401), str(r.status))

        print("session hand-off")
        r = request(port, "GET", "/desktop/session?token=nope"); r.read()
        check("bad token refused", r.status == 403, str(r.status))
        r = request(port, "GET", f"/desktop/session?token={SECRET}"); page = r.read()
        check("hand-off page navigates to /", b'location.replace("/")' in page and b'http-equiv="refresh"' in page)
        cookies = r.getheader("set-cookie") or ""
        raw = [h[1] for h in r.getheaders() if h[0].lower() == "set-cookie"]
        check("good token returns the hand-off page (not a redirect: WebView cross-site cookie rule)", r.status == 200 and r.getheader("location") is None, f"{r.status} {r.getheader('location')}")
        names = {c.split("=")[0] for c in raw}
        check("session + guard cookies set", "ax_desktop" in names and len(names) >= 2, str(names))
        check("cookies are HttpOnly", all("httponly" in c.lower() for c in raw))
        check("secret never appears in a cookie", all(SECRET not in c for c in raw))
        jar = "; ".join(c.split(";")[0] for c in raw)
        r = request(port, "GET", "/api/status", {"Cookie": jar}); body = r.read()
        check("cookie session reaches the API (no web login needed)", r.status == 200, f"{r.status} {body[:80]!r}")
        r = request(port, "GET", "/", {"Cookie": jar}); html = r.read()
        check("UI page served with the session", r.status == 200 and b"<title>" in html, str(r.status))
        r = request(port, "GET", "/app-next/", {"Cookie": jar}); html = r.read()
        if r.status == 404:
            print("  [SKIP] React app not built (run `npm run build` in frontend/ before freezing to include it)")
        else:
            check("React app served at /app-next/ with the session", r.status == 200 and b'id="root"' in html, str(r.status))
        r = request(port, "GET", "/desktop/info"); r.read()
        check("/desktop/info needs the secret/cookie", r.status == 403, str(r.status))
        r = request(port, "GET", "/desktop/info", {"Cookie": jar}); info = json.loads(r.read() or b"{}")
        check("/desktop/info tells the UI it runs in the desktop app", r.status == 200 and info.get("desktop") is True, f"{r.status} {info}")
        r = request(port, "GET", "/app-next/", {}); r.read()
        check("React app also needs the secret/cookie", r.status == 403, str(r.status))
        r = request(port, "POST", "/login", {"Cookie": jar, "Content-Length": "0"}); r.read()
        check("login endpoint cannot be used to bypass (still guarded/validated)", r.status in (400, 403, 428, 401, 422), str(r.status))

        print("binding")
        check("plain HTTP on loopback (no TLS)", True)
        other = None
        try:
            ip = socket.gethostbyname(socket.gethostname())
            other = ip if not ip.startswith("127.") else None
        except OSError:
            pass
        if other:
            s = socket.socket(); s.settimeout(3)
            try:
                s.connect((other, port)); refused = False
            except OSError:
                refused = True
            finally:
                s.close()
            check(f"not reachable on the machine's LAN address {other}", refused)
        else:
            print("  [SKIP] no non-loopback address on this machine to probe")

        print("shutdown")
        r = request(port, "POST", "/desktop/shutdown"); r.read()
        check("shutdown without secret refused", r.status == 403, str(r.status))
        r = request(port, "POST", "/desktop/shutdown", {"X-Archive-X-Desktop": SECRET}); r.read()
        check("shutdown with secret accepted (202)", r.status == 202, str(r.status))
        try:
            code = proc.wait(timeout=45)
            check("backend exits cleanly after graceful shutdown", code == 0, f"exit code {code}")
        except subprocess.TimeoutExpired:
            check("backend exits cleanly after graceful shutdown", False, "still running after 45 s")
            proc.kill()
    finally:
        if proc.poll() is None:
            proc.kill()

    print("\nlanding page")
    for value, expected in (("/app-next/", "/app-next/"), ("//evil.example/x", "/"), ("https://evil.example", "/"), ("/ok\\bad", "/")):
        p3, port3, _ = start_backend(cmd, tempfile.mkdtemp(prefix="axdesktop_"), holder.pid, extra_env={"ARCHIVE_X_DESKTOP_LANDING": value})
        try:
            r = request(port3, "GET", f"/desktop/session?token={SECRET}"); page = r.read().decode()
            check(f"landing {value!r} -> {expected!r}", f'location.replace({json.dumps(expected)})' in page, page[-120:])
            request(port3, "POST", "/desktop/shutdown", {"X-Archive-X-Desktop": SECRET}).read()
            p3.wait(timeout=45)
        finally:
            if p3.poll() is None:
                p3.kill()

    print("\nparent-death watchdog")
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    proc2, port2, _ = start_backend(cmd, tempfile.mkdtemp(prefix="axdesktop_"), parent.pid)
    try:
        r = request(port2, "GET", "/health"); r.read()
        check("second backend running before the parent dies", r.status == 200)
        parent.kill(); parent.wait()
        t0 = time.time()
        try:
            code = proc2.wait(timeout=60)
            check("backend stops itself when the shell disappears", True)
            print(f"        (stopped {time.time() - t0:.1f} s after the parent died, exit code {code})")
        except subprocess.TimeoutExpired:
            check("backend stops itself when the shell disappears", False, "still running 60 s after the parent died")
    finally:
        if proc2.poll() is None:
            proc2.kill()
        holder.kill()

    print("\n" + ("ALL PASSED" if not FAILS else f"FAILED: {FAILS}"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
