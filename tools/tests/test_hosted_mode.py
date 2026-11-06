"""Hosted mode (ARCHIVE_X_HOSTED=1, e.g. Replit): plain HTTP, login seeded from secrets, no remote first-run claim.

usage:  python tools/tests/test_hosted_mode.py [path-to-python]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.argv[1] if len(sys.argv) > 1 else sys.executable
CODE = "from telegram_archive.main import main; main()"
FAILS: list[str] = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def req(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Origin": url.split("/", 3)[0] + "//" + url.split("/")[2], "X-Forwarded-Proto": "https"})
    try:
        with urllib.request.urlopen(r, timeout=8) as resp:
            return resp.status, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers


def env(data, port, password):
    e = dict(os.environ, ARCHIVE_X_HOSTED="1", PORT=str(port), ARCHIVE_X_DATA_DIR=data, ARCHIVE_X_AUTO_OPEN_BROWSER="0")
    e.pop("ARCHIVE_X_DESKTOP", None)
    if password:
        e["ARCHIVE_X_ADMIN_PASSWORD"] = password
    else:
        e.pop("ARCHIVE_X_ADMIN_PASSWORD", None)
    return e


with tempfile.TemporaryDirectory() as d:
    p = subprocess.run([PY, "-c", CODE], cwd=ROOT, env=env(d, free_port(), ""), capture_output=True, text=True, timeout=90)
    check("refuses to start without ARCHIVE_X_ADMIN_PASSWORD", p.returncode != 0 and "ARCHIVE_X_ADMIN_PASSWORD" in (p.stdout + p.stderr))
    p = subprocess.run([PY, "-c", CODE], cwd=ROOT, env=env(d, free_port(), "short"), capture_output=True, text=True, timeout=90)
    check("rejects a password under 12 characters", p.returncode != 0)

with tempfile.TemporaryDirectory() as d:
    port = free_port()
    proc = subprocess.Popen([PY, "-c", CODE], cwd=ROOT, env=env(d, port, "hosted-test-password-1"), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/health", timeout=2)
                break
            except Exception:
                time.sleep(1)
        check("serves plain HTTP on $PORT", req(base + "/health")[0] == 200)
        check("wrong password is refused", req(base + "/login", {"username": "admin", "password": "nope"})[0] == 401)
        code, headers = req(base + "/login", {"username": "admin", "password": "hosted-test-password-1"})
        cookie = headers.get("set-cookie", "") if headers else ""
        check("secret password logs in", code == 200)
        check("cookie suits the embedded HTTPS preview", all(x in cookie for x in ("Secure", "SameSite=none", "Partitioned", "HttpOnly")), cookie)
        check("remote first-run setup is closed", req(base + "/api/auth/setup", {})[0] == 409)
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except Exception:
            proc.kill()

print("\nALL PASSED" if not FAILS else f"\nFAILED: {FAILS}")
sys.exit(1 if FAILS else 0)
