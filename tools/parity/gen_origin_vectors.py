"""Test vectors for the cross-site guard, produced by the real Python middleware (BrowserMutationGuardMiddleware).

    python tools/parity/gen_origin_vectors.py            # rewrite crates/ax-server/tests/origin_vectors.json
    python tools/parity/gen_origin_vectors.py --check    # exit 1 if the committed file is out of date

The Rust implementation (crates/ax-server/src/guard.rs) must give the same answer for every vector.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "crates/ax-server/tests/origin_vectors.json"

METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
PATHS = ["/api/status", "/api/", "/api", "/apix", "/login", "/logout", "/login/", "/photos", "/API/status"]
ORIGINS = [None, "", "https://archive.test", "https://archive.test:8443", "http://archive.test", "HTTPS://ARCHIVE.TEST", "https://evil.test",
           "https://archive.test.evil.test", "https://user@archive.test", "null", "archive.test", "//archive.test", "https:archive.test",
           "https://archive.test/", "https://archive.test/path?x=1", "https://archive.test#frag", "https://[::1]:8443", "ftp://archive.test", "https://[::1", "https://::1]:8443", "https://archive.test:8443"]
HOSTS = ["archive.test", "archive.test:8443", "ARCHIVE.TEST", "[::1]:8443", "[::1", "::1]:8443", ""]


def verdict(method, path, origin, host):
    from telegram_archive.core.http_middleware import BrowserMutationGuardMiddleware

    async def inner(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    status = []

    async def send(message):
        if message["type"] == "http.response.start":
            status.append(message["status"])

    headers = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    scope = {"type": "http", "method": method, "path": path, "headers": headers}
    asyncio.run(BrowserMutationGuardMiddleware(inner)(scope, None, send))
    return status[0] != 403


def build():
    return [{"method": m, "path": p, "origin": o, "host": h, "allowed": verdict(m, p, o, h)}
            for m in METHODS for p in PATHS for o in ORIGINS for h in HOSTS]


def main():
    text = json.dumps(build(), separators=(",", ":")) + "\n"
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("origin vectors are out of date: run python tools/parity/gen_origin_vectors.py")
            return 1
        print("origin vectors are current")
        return 0
    OUT.write_text(text)
    d = json.loads(text)
    print(f"wrote {OUT.relative_to(ROOT)}: {len(d)} vectors, {sum(1 for v in d if not v['allowed'])} rejected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
