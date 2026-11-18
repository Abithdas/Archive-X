"""Nothing adult in SFW: the places an adult name could still get through (management lists, provider searches, raw logs, matching)."""
import os
import sys
import tempfile

os.environ.update(ARCHIVE_X_DATA_DIR=tempfile.mkdtemp(prefix="axleak_"), ARCHIVE_X_AUTO_OPEN_BROWSER="0", ARCHIVE_X_LOCAL_RESCAN="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
sys.path.insert(0, os.path.dirname(__file__))

from telegram_archive.core import content_mode as cm  # noqa: E402
from telegram_archive.core.database import Database  # noqa: E402
from telegram_archive.engines import screen_identify as sid  # noqa: E402

from test_local_folders import _client  # noqa: E402

M = "ZZADULT"


def _db(tmp_path):
    db = Database(tmp_path / "l.db")
    with db.lock:
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1001',?,'available',1,'nsfw','both','2026-01-01')", (M + " Channel",))
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1002','Films','available',1,'sfw','both','2026-01-01')")
        db.conn.execute("INSERT INTO channels(telegram_id,title,role,in_library,content_rating,holds,updated_at) VALUES('-1003','New channel','available',0,'nsfw','both','2026-01-01')")
        db.conn.commit()
    return db


def _nsfw(c, H):
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200


def test_the_channel_catalogue_does_not_list_adult_channels_in_sfw(tmp_path):
    db = _db(tmp_path)
    assert cm.hidden_channels(db) == {"-1001"}                       # in the library as NSFW; a channel still outside the library is not hidden
    c, H = _client(db)
    assert sorted(r["title"] for r in c.get("/api/source-channels").json()) == ["Films", "New channel"]
    _nsfw(c, H)
    assert sorted(r["title"] for r in c.get("/api/source-channels").json()) == ["Films", "New channel", M + " Channel"]
    assert cm.hidden_channels(db) == set()


def test_adult_provider_searches_are_not_available_in_sfw(tmp_path, monkeypatch):
    import telegram_archive.metadata.providers as providers
    asked = []

    async def fake(db, name, limit=8):
        asked.append(name)
        return [{"provider": "stashdb", "provider_id": "1", "name": M + " Person"}]
    monkeypatch.setattr(providers, "search_performer_providers", fake)
    monkeypatch.setattr(providers, "search_studio_providers", fake)
    db = _db(tmp_path)
    db.set_setting("stashdb_api_key", "k" * 20)
    c, H = _client(db)
    for url in ("/api/performers/provider-search?q=zzadult", "/api/studios/provider-search?q=zzadult", "/api/photos/faces/performers/provider-search?q=zzadult"):
        body = c.get(url).json()
        assert M not in str(body) and asked == [], (url, body)       # nothing is returned and the provider is not even asked
    assert c.post("/api/photos/faces/performers/import", json={"name": "X Y", "provider": "stashdb", "provider_id": "9"}, headers=H).status_code == 403
    _nsfw(c, H)
    for url in ("/api/performers/provider-search?q=zzadult", "/api/studios/provider-search?q=zzadult", "/api/photos/faces/performers/provider-search?q=zzadult"):
        assert M in str(c.get(url).json()), url                       # NSFW: unchanged
    assert len(asked) == 3


def test_raw_logs_and_reports_are_exported_from_nsfw_only(tmp_path):
    db = _db(tmp_path)
    db.log(M + " moved " + M + " file.mp4")
    c, H = _client(db)
    for url in ("/api/log/download", "/api/diagnostics/report", "/api/diagnostics/report/html"):
        r = c.get(url)
        assert r.status_code == 403 and M not in r.text, url
    _nsfw(c, H)
    assert c.get("/api/log/download").status_code == 200


def test_a_cast_member_never_takes_over_an_adult_record_that_the_matcher_would_pick(tmp_path):
    db = _db(tmp_path)
    with db.lock:   # an adult performer known under a name that is an alias of the actor's spelling
        db.conn.execute("INSERT INTO performers(name,content_rating,aliases,canonical_key,normalized_name) VALUES('Mia Wallace','nsfw','[\"Mia Wallis\"]','mia wallace','mia wallace')")
        db.conn.commit()
    actor = sid._entity(db, "performers", "Mia Wallace", 1234)
    with db.reading() as c:
        rows = {r["name"]: r["content_rating"] for r in c.execute("SELECT name,content_rating FROM performers")}
    assert rows["Mia Wallace"] == "nsfw"                               # the adult record keeps its mark
    assert rows.get("Mia Wallace (Actor)") == "sfw" and actor is not None
    studio = sid._entity(db, "studios", "Some Studio")
    again = sid._entity(db, "studios", "Some Studio")
    assert studio == again                                              # the SFW record is found again, not duplicated


def test_the_front_door_can_ask_which_mode_is_on_but_a_browser_cannot(tmp_path, monkeypatch):
    db = _db(tmp_path)
    c, H = _client(db)
    monkeypatch.setenv("ARCHIVE_X_PROXY_KEY", "p" * 32)
    assert c.get("/internal/content-mode").status_code == 404                                       # no secret
    assert c.get("/internal/content-mode", headers={"x-archive-x-internal": "wrong"}).status_code == 404
    assert c.get("/internal/content-mode", headers={"x-archive-x-internal": "p" * 32}).json() == {"mode": "sfw"}
    _nsfw(c, H)
    assert c.get("/internal/content-mode", headers={"x-archive-x-internal": "p" * 32}).json() == {"mode": "nsfw"}
