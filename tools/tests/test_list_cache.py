"""A remembered list is served only while nothing it is made from has changed, and never after it is old."""
from __future__ import annotations

from telegram_archive.core import content_mode as cm
from telegram_archive.core import list_cache
from telegram_archive.core.database import Database

NOW = "2026-11-26T00:00:00"


def _db(tmp_path):
    list_cache.clear()
    return Database(tmp_path / "t.db")


def _counter():
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return [{"id": calls["n"], "tags": ["a"]}]
    return calls, compute


def test_the_second_ask_is_answered_from_memory(tmp_path):
    db = _db(tmp_path)
    calls, compute = _counter()
    first = list_cache.cached(db, "x", {"q": "a"}, compute)
    second = list_cache.cached(db, "x", {"q": "a"}, compute)
    assert first == second and calls["n"] == 1


def test_a_change_to_the_data_makes_the_next_ask_work_it_out_again(tmp_path):
    db = _db(tmp_path)
    calls, compute = _counter()
    list_cache.cached(db, "x", {}, compute)
    for change in (
        "INSERT INTO media(source_channel,source_message_id,filename,created_at,updated_at) VALUES('-1001',1,'v',datetime('now'),datetime('now'))",
        "INSERT INTO performers(name) VALUES('P')",
        "INSERT INTO studios(name) VALUES('S')",
        "INSERT INTO channels(telegram_id,title,updated_at,in_library) VALUES('-1001','C',datetime('now'),1)",
        "INSERT OR REPLACE INTO settings(key,value) VALUES('theme','\"dark\"')",
    ):
        before = calls["n"]
        list_cache.cached(db, "x", {}, compute)
        assert calls["n"] == before, "unchanged data must be served from memory"
        with db.lock:
            db.conn.execute(change)
            db.conn.commit()
        list_cache.cached(db, "x", {}, compute)
        assert calls["n"] == before + 1, change


def test_an_old_answer_is_not_served(tmp_path, monkeypatch):
    db = _db(tmp_path)
    calls, compute = _counter()
    clock = {"t": 1000.0}
    monkeypatch.setattr(list_cache.time, "monotonic", lambda: clock["t"])
    list_cache.cached(db, "x", {}, compute)
    clock["t"] += list_cache.TTL_SECONDS - 1
    list_cache.cached(db, "x", {}, compute)
    assert calls["n"] == 1
    clock["t"] += 2
    list_cache.cached(db, "x", {}, compute)
    assert calls["n"] == 2


def test_other_questions_and_other_modes_have_their_own_answers(tmp_path):
    db = _db(tmp_path)
    calls, compute = _counter()
    list_cache.cached(db, "x", {"q": "a"}, compute)
    list_cache.cached(db, "x", {"q": "b"}, compute)
    list_cache.cached(db, "y", {"q": "a"}, compute)
    assert calls["n"] == 3
    list_cache.cached(db, "x", {"q": "a"}, compute)
    assert calls["n"] == 3
    with db.lock:    # a switch of content mode is a settings change, and the mode is part of the key as well
        cm.set_mode(db, "nsfw") if hasattr(cm, "set_mode") else db.set_setting("content_mode", "nsfw")
    list_cache.cached(db, "x", {"q": "a"}, compute)
    assert calls["n"] == 4


def test_a_caller_that_changes_its_answer_does_not_change_what_is_remembered(tmp_path):
    db = _db(tmp_path)
    calls, compute = _counter()
    a = list_cache.cached(db, "x", {}, compute)
    a[0]["tags"].append("changed")
    a.append({"id": 99})
    b = list_cache.cached(db, "x", {}, compute)
    assert b == [{"id": 1, "tags": ["a"]}]


def test_a_failed_computation_is_not_remembered(tmp_path):
    db = _db(tmp_path)
    attempts = {"n": 0}

    def compute():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("boom")
        return [1]
    try:
        list_cache.cached(db, "x", {}, compute)
    except RuntimeError:
        pass
    assert list_cache.cached(db, "x", {}, compute) == [1] and attempts["n"] == 2
