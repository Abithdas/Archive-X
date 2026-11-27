"""A live-updates connection that is refused before it opens reaches the page only as "closed (1006), never opened": the server says why in its log."""
import pytest
from starlette.websockets import WebSocketDisconnect

from telegram_archive.core.database import Database
from test_screen_home import _client


def _messages(db):
    return [str(e.get("message")) for e in db.recent_events(200) if str(e.get("message", "")).startswith("Live updates connection refused")]


def test_a_connection_without_a_session_is_refused_and_the_reason_is_logged(tmp_path):
    db = Database(tmp_path / "t.db")
    c = _client(db)
    c.cookies.clear()                                   # the page has no session on the connection
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws"):
            pass
    got = _messages(db)
    assert got and "no session cookie sent with the connection" in got[0] and "cookie=no" in got[0]


def test_a_page_and_connection_with_different_addresses_are_refused_and_logged(tmp_path):
    db = Database(tmp_path / "t.db")
    c = _client(db)
    from telegram_archive import config
    token = c.cookies.get(config.SESSION_COOKIE)
    assert token                                         # a signed-in page: only the addresses differ
    with pytest.raises(WebSocketDisconnect):
        with c.websocket_connect("/ws", headers={"origin": "http://elsewhere.example", "cookie": f"{config.SESSION_COOKIE}={token}"}):
            pass
    got = _messages(db)
    assert got and "differ" in got[0] and "origin=http://elsewhere.example" in got[0]
