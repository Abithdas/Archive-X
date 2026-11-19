"""Starts the real backend on a throw-away data folder and points Nuclei at it, twice: without the desktop cookie (the front door must
answer 403 to everything) and with it (what a signed-in window can reach). Nuclei is MIT-licensed and is only run here; nothing of it ships.

    python tools/security/dast.py --nuclei /path/to/nuclei [--templates DIR] [--out report.jsonl]

Exit code 1 when Nuclei reports anything at medium or above, or when the front door lets an unauthenticated request through.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEVERITIES = "medium,high,critical"
TAGS = "misconfig,exposure,cors,cve,takeover,lfi,ssrf,xss,redirect,traversal,injection"


def start(data_dir: str, secret: str) -> tuple[subprocess.Popen, int]:
    env = dict(os.environ, ARCHIVE_X_DESKTOP="1", ARCHIVE_X_LAUNCH_SECRET=secret, ARCHIVE_X_DATA_DIR=data_dir, PYTHONPATH=str(ROOT))
    proc = subprocess.Popen([sys.executable, "-m", "telegram_archive.main"], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.time() + 120
    port = 0
    while time.time() < deadline and proc.poll() is None:
        line = proc.stdout.readline()
        m = re.search(r"127\.0\.0\.1:(\d+)", line or "") or re.search(r'"port"\s*:\s*(\d+)', line or "")
        if m:
            port = int(m.group(1))
            break
    if not port:
        proc.kill()
        raise SystemExit("the backend did not announce a port")
    return proc, port


def cookies(port: int, secret: str) -> str:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    resp = opener.open(f"http://127.0.0.1:{port}/desktop/session?token={secret}", timeout=30)
    jar = [h.split(";")[0] for h in resp.headers.get_all("Set-Cookie") or []]
    if not jar:
        raise SystemExit("no session cookie came back from /desktop/session")
    return "; ".join(jar)


def front_door(port: int) -> list[str]:
    """Every path must be refused without the cookie."""
    bad = []
    for path in ("/", "/api/channels", "/api/jobs", "/api/settings", "/api/ai/models", "/docs", "/openapi.json", "/desktop/info", "/video/1?c=1&t=x"):
        try:
            code = urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=15).status
        except urllib.error.HTTPError as exc:
            code = exc.code
        except Exception:
            continue   # the player feed is a separate port; a refused connection is also a closed door
        if code < 400:
            bad.append(f"{path} -> {code}")
    return bad


def nuclei(binary: str, url: str, out: Path, templates: str | None, header: str | None) -> int:
    cmd = [binary, "-u", url, "-severity", SEVERITIES, "-tags", TAGS, "-jsonl", "-o", str(out), "-silent", "-disable-update-check", "-rate-limit", "50"]
    if templates:
        cmd += ["-t", templates]
    if header:
        cmd += ["-H", header]
    subprocess.run(cmd, check=False, timeout=1500)
    return sum(1 for _ in out.open()) if out.exists() else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nuclei", default="nuclei")
    ap.add_argument("--templates")
    ap.add_argument("--out", default="nuclei.jsonl")
    args = ap.parse_args()
    secret = secrets.token_hex(16)
    failed = 0
    with tempfile.TemporaryDirectory() as data:
        proc, port = start(data, secret)
        try:
            base = f"http://127.0.0.1:{port}"
            bad = front_door(port)
            for b in bad:
                print(f"FRONT DOOR OPEN: {b}")
            failed += len(bad)
            out = Path(args.out)
            a = Path(str(out) + ".anonymous")
            found_anon = nuclei(args.nuclei, base, a, args.templates, None)
            found_auth = nuclei(args.nuclei, base, out, args.templates, "Cookie: " + cookies(port, secret))
            print(f"nuclei: {found_anon} finding(s) without the cookie, {found_auth} with it")
            for p in (a, out):
                if p.exists():
                    for line in p.read_text().splitlines()[:50]:
                        try:
                            d = json.loads(line)
                            print(f"  [{d['info']['severity']}] {d['template-id']} {d.get('matched-at', '')}")
                        except Exception:
                            pass
            failed += found_anon + found_auth
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except Exception:
                proc.kill()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
