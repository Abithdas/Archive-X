import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from telegram_archive.core.http_middleware import TrustedProxyMiddleware


def run(headers, client=("127.0.0.1", 5555)):
    seen = {}

    async def app(scope, receive, send):
        seen["client"] = scope["client"]
        seen["headers"] = dict(scope["headers"])

    mw = TrustedProxyMiddleware(app, key="s3cret")
    asyncio.run(mw({"type": "http", "client": client, "headers": [(k.encode(), v.encode()) for k, v in headers]}, None, None))
    return seen


def test_the_real_address_is_used_only_with_the_right_key():
    ok = run([("X-Archive-X-Proxy-Key", "s3cret"), ("X-Archive-X-Peer", "192.168.1.9")])
    assert ok["client"] == ("192.168.1.9", 0)
    assert run([("X-Archive-X-Proxy-Key", "wrong"), ("X-Archive-X-Peer", "192.168.1.9")])["client"] == ("127.0.0.1", 5555)
    assert run([("X-Archive-X-Peer", "192.168.1.9")])["client"] == ("127.0.0.1", 5555)   # a bare claim is ignored


def test_client_supplied_forwarding_headers_never_reach_the_app():
    seen = run([("X-Forwarded-For", "1.2.3.4"), ("X-Archive-X-Peer", "9.9.9.9"), ("Host", "x")])
    assert b"x-forwarded-for" not in {k.lower() for k in seen["headers"]} and b"x-archive-x-peer" not in {k.lower() for k in seen["headers"]}
    assert seen["headers"][b"Host"] == b"x"
