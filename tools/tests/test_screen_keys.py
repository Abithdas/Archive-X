"""Provider keys for movies and TV: what is pasted is cleaned down to the key, and text that is plainly not a key is refused in words."""
import os
import sys
import tempfile

import pytest

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axkeys_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.web.routes_screen import clean_key  # noqa: E402

from test_local_folders import _client  # noqa: E402


@pytest.mark.parametrize("pasted", [" 1a2b3c4d ", "\"1a2b3c4d\"\n", "http://www.omdbapi.com/?apikey=1a2b3c4d", "Here is your key: 1a2b3c4d", "1a2b3c4d​"])
def test_an_omdb_key_is_found_in_what_was_pasted(pasted):
    assert clean_key("omdb_api_key", pasted) == "1a2b3c4d"


def test_the_activation_link_and_other_text_are_not_keys():
    with pytest.raises(ValueError, match="activation link"):
        clean_key("omdb_api_key", "http://www.omdbapi.com/apikey.aspx?VERIFYKEY=abc-123-def")
    with pytest.raises(ValueError, match="8 letters and digits"):
        clean_key("omdb_api_key", "not a key at all, sorry")
    assert clean_key("omdb_api_key", "   ") == ""


def test_other_providers_lose_only_decoration():
    assert clean_key("tmdb_api_key", "Bearer eyJ.abc-d_e.fff") == "eyJ.abc-d_e.fff"
    assert clean_key("tmdb_api_key", " 0123456789abcdef0123456789abcdef\n") == "0123456789abcdef0123456789abcdef"


def test_saving_stores_the_clean_key_and_refuses_a_link_without_saving_anything(tmp_path):
    db = Database(tmp_path / "k.db")
    c, H = _client(db)
    r = c.post("/api/screen/settings", json={"omdb_api_key": "http://www.omdbapi.com/?apikey=1a2b3c4d", "tmdb_api_key": "t" * 32}, headers=H)
    assert r.status_code == 200 and sorted(r.json()["saved"]) == ["omdb_api_key", "tmdb_api_key"]
    assert db.setting("omdb_api_key") == "1a2b3c4d"
    bad = c.post("/api/screen/settings", json={"omdb_api_key": "http://www.omdbapi.com/apikey.aspx?VERIFYKEY=zzz", "opensubtitles_api_key": "os-key-123456"}, headers=H)
    assert bad.status_code == 400 and "activation link" in bad.json()["detail"]
    assert not db.setting("opensubtitles_api_key")          # one wrong key saves nothing
    assert c.post("/api/screen/settings", json={"clear": ["omdb_api_key"]}, headers=H).status_code == 200
    assert not db.setting("omdb_api_key")
    assert c.get("/api/screen/settings").json()["omdb_configured"] is False


# --- the real fetch, against a local server, with `requests` unavailable (as in the installed app) -------------------------------------------

def _server(handler_cls):
    import http.server
    import threading
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def test_the_real_fetch_needs_no_requests_package_and_reports_the_providers_words(monkeypatch):
    import http.server
    import json
    from telegram_archive.engines import screen_identify as sid
    monkeypatch.setitem(sys.modules, "requests", None)          # importing it now raises ImportError, as in the installed app
    hits = {"slow": 0}

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, extra=None):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if "apikey=bad" in self.path:
                return self._send(401, {"Response": "False", "Error": "Invalid API key!"})
            if "missing" in self.path:
                return self._send(404, {"status_message": "not found"})
            if "slow" in self.path:
                hits["slow"] += 1
                return self._send(429, {}, {"Retry-After": "0"}) if hits["slow"] == 1 else self._send(200, {"ok": True})
            self._send(200, {"Response": "True", "seen": self.headers.get("X-Test", ""), "path": self.path})

    srv, base = _server(H)
    try:
        got = sid.http_fetch(base, {"i": "tt1", "apikey": "good", "skip": None}, {"X-Test": "yes"})
        assert got["Response"] == "True" and got["seen"] == "yes" and "skip" not in got["path"] and "apikey=good" in got["path"]
        with pytest.raises(sid.ProviderError) as e:
            sid.http_fetch(base, {"apikey": "bad"}, {})
        assert e.value.status == 401 and e.value.detail == "Invalid API key!"
        assert "OMDb does not recognise this key" in sid.key_trouble("omdb", e.value)
        assert sid.http_fetch(base + "missing", {}, {}) == {}                       # a 404 is "nothing found", not an error
        assert sid.http_fetch(base + "slow", {}, {}) == {"ok": True} and hits["slow"] == 2   # told to slow down once, then asked again
    finally:
        srv.shutdown()


def test_the_key_check_route_uses_that_fetch_and_says_why(tmp_path, monkeypatch):
    import json
    from telegram_archive.engines import screen_identify as sid
    monkeypatch.setitem(sys.modules, "requests", None)
    db = Database(tmp_path / "k.db")
    db.set_setting("omdb_api_key", "1a2b3c4d")
    seen = []

    def fake_http(url, params, headers):
        seen.append(params.get("apikey"))
        raise sid.ProviderError(401, "Invalid API key!")
    monkeypatch.setattr(sid, "http_fetch", fake_http)
    c, H = _client(db)
    r = c.post("/api/screen/check", json={"provider": "omdb"}, headers=H).json()
    assert r["ok"] is False and "activation link" in r["error"] and seen == ["1a2b3c4d"], r
