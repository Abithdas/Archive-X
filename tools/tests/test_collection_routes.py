"""The Collection's routes: SFW only, validated, and the worker is only ever woken when automatic downloads are on."""
import pytest

from telegram_archive.core.database import Database
from telegram_archive.engines import collection as col
from telegram_archive.engines import collection_worker as cw
from test_screen_home import H, _client


@pytest.fixture
def app(tmp_path, monkeypatch):
    db = Database(tmp_path / "r.db")
    db.set_setting("collection_dir", str(tmp_path / "Col"))
    kicks = []
    monkeypatch.setattr(cw.Worker, "kick", lambda self: kicks.append(1))
    monkeypatch.setattr(cw.Worker, "start", lambda self: None)
    c = _client(db)
    assert c.post("/api/content-mode", json={"mode": "sfw"}, headers=H).status_code == 200
    return db, c, kicks


def test_state_lists_nothing_at_first_with_the_server_status_and_settings(app):
    db, c, _ = app
    got = c.get("/api/collection").json()
    assert got["titles"] == [] and got["settings"]["on"] is False and got["settings"]["quality"] == 1080
    assert set(got["server"]) == {"up", "ready", "installed"} and got["running"] is False


def test_keeping_a_title_validates_it_and_uses_the_name_the_page_sends(app):
    db, c, kicks = app
    assert c.post("/api/collection/titles", json={"kind": "movie", "imdb": "nope"}, headers=H).status_code == 400
    assert c.post("/api/collection/titles", json={"kind": "book", "imdb": "tt0133093"}, headers=H).status_code == 400
    got = c.post("/api/collection/titles", json={"kind": "series", "imdb": "tt5753856", "title": "Dark", "year": 2017, "poster": "https://img/p.jpg", "scope": "new"}, headers=H).json()
    assert got["ok"] is True and got["title"] == "Dark"
    t = c.get("/api/collection").json()["titles"][0]
    assert (t["title"], t["year"], t["scope"], t["kind"]) == ("Dark", 2017, "new", "series") and kicks == []      # automatic downloads are off: nothing is woken
    c.post("/api/collection/settings", json={"on": True}, headers=H)
    c.post("/api/collection/titles", json={"kind": "movie", "imdb": "tt0133093", "title": "The Matrix", "year": 1999}, headers=H)
    assert len(kicks) >= 2


def test_a_title_can_be_removed_and_unknown_ones_are_not_found(app):
    db, c, _ = app
    tid = c.post("/api/collection/titles", json={"kind": "movie", "imdb": "tt0133093", "title": "M", "year": 1999}, headers=H).json()["id"]
    assert c.delete(f"/api/collection/titles/{tid}", headers=H).json() == {"ok": True}
    assert c.delete(f"/api/collection/titles/{tid}", headers=H).status_code == 404


def test_settings_are_validated(app):
    db, c, _ = app
    assert c.post("/api/collection/settings", json={"quality": 480}, headers=H).status_code == 400
    ok = c.post("/api/collection/settings", json={"quality": 2160, "max_gb_episode": 6}, headers=H).json()["settings"]
    assert ok["quality"] == 2160 and ok["max_gb_episode"] == 6


def test_retry_and_skip_act_on_unfinished_items_only(app):
    db, c, kicks = app
    tid = c.post("/api/collection/titles", json={"kind": "series", "imdb": "tt5753856", "title": "Dark", "year": 2017}, headers=H).json()["id"]
    with db.lock:
        db.conn.execute("INSERT INTO collection_items(title_id,season,episode,state,attempts,note) VALUES(?,1,1,'WANTED',3,'No release found yet')", (tid,))
        db.conn.execute("INSERT INTO collection_items(title_id,season,episode,state) VALUES(?,1,2,'DONE')", (tid,))
        db.conn.commit()
    ids = {(r["season"], r["episode"]): r["id"] for r in db.conn.execute("SELECT * FROM collection_items")}
    assert c.post(f"/api/collection/items/{ids[(1, 1)]}/skip", headers=H).json()["ok"] is True
    assert db.conn.execute("SELECT state FROM collection_items WHERE id=?", (ids[(1, 1)],)).fetchone()[0] == "SKIPPED"
    assert c.post(f"/api/collection/items/{ids[(1, 1)]}/retry", headers=H).json()["ok"] is True
    row = db.conn.execute("SELECT state, attempts FROM collection_items WHERE id=?", (ids[(1, 1)],)).fetchone()
    assert (row[0], row[1]) == ("WANTED", 0)
    assert c.post(f"/api/collection/items/{ids[(1, 2)]}/skip", headers=H).status_code == 404         # done: nothing to skip
    assert c.post(f"/api/collection/items/{ids[(1, 1)]}/explode", headers=H).status_code == 404


def test_a_round_can_only_be_asked_for_when_automatic_downloads_are_on(app):
    db, c, kicks = app
    assert c.post("/api/collection/run", headers=H).status_code == 409
    c.post("/api/collection/settings", json={"on": True}, headers=H)
    assert c.post("/api/collection/run", headers=H).json() == {"ok": True}


def test_nothing_of_it_is_reachable_in_nsfw_mode(app):
    db, c, _ = app
    assert c.post("/api/content-mode", json={"mode": "nsfw", "new_pin": "2468"}, headers=H).status_code == 200
    assert c.get("/api/collection").status_code == 409
    assert c.post("/api/collection/titles", json={"kind": "movie", "imdb": "tt0133093", "title": "M"}, headers=H).status_code == 409


def test_the_worker_starts_idle_stops_quickly_and_the_server_check_explains_itself(tmp_path, monkeypatch):
    from telegram_archive.metadata import streaming_server as ss, stremio_runtime as rt
    db = Database(tmp_path / "w.db")
    w = cw.Worker(db, interval=0.05)
    monkeypatch.setattr(w.stop_event, "wait", lambda t=None: False)       # no 45 s wait for the app to open
    ran = []
    monkeypatch.setattr(w, "run_once", lambda: ran.append(1) or {})
    w.start()
    w.stop()
    assert w.thread is not None and not w.thread.is_alive() and ran == []      # automatic downloads are off: a round never ran
    ensure = cw.ensure_server_factory(db)
    monkeypatch.setattr(ss, "reachable", lambda base: False)
    monkeypatch.setattr(ss, "start", lambda base: {"up": False, "found": False, "reason": "x"})
    monkeypatch.setattr(rt, "ready", lambda: False)
    ok, why = ensure()
    assert ok is False and "Set it up" in why
    monkeypatch.setattr(ss, "start", lambda base: {"up": True})
    assert ensure() == (True, "")
