"""A second performer record for an existing name is deliberate in two cases; the log must say which, so duplicates can be traced."""
import json
import sqlite3
import threading

from telegram_archive.engines.entity_identity import upsert_performer


class _Db:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE performers (id INTEGER PRIMARY KEY, name TEXT UNIQUE, canonical_key TEXT UNIQUE, normalized_name TEXT, aliases TEXT,"
            " provider_ids TEXT, image TEXT, profile TEXT, bio TEXT, gender TEXT, image_locked INT DEFAULT 0, name_locked INT DEFAULT 0, profile_locked INT DEFAULT 0)"
        )
        self.lock = threading.RLock()
        self.logs = []

    def log(self, message, level="INFO", **kw):
        self.logs.append((level, message, kw))


def test_a_different_id_at_the_same_provider_creates_a_logged_second_record():
    db = _Db()
    first = upsert_performer(db, "Anna Test", provider="stashdb", provider_id="AAA")
    second = upsert_performer(db, "Anna Test", provider="stashdb", provider_id="BBB")
    assert first != second
    (level, message, kw), = db.logs
    assert level == "WARN" and kw["event"] == "performer.lookalike_created"
    assert "another stashdb id" in message and "AAA" in message and f"#{first}" in message
    assert kw["details"]["existing_ids"] == [first]


def test_the_same_person_from_another_provider_is_one_record_and_logs_nothing():
    db = _Db()
    first = upsert_performer(db, "Anna Test", provider="stashdb", provider_id="AAA")
    again = upsert_performer(db, "Anna Test", provider="tpdb", provider_id="123")
    assert first == again and db.logs == []


def test_several_matching_records_are_reported_as_ambiguous():
    db = _Db()
    a = upsert_performer(db, "Zoe Test", provider="stashdb", provider_id="1")
    db.conn.execute("INSERT INTO performers(name,canonical_key,normalized_name,aliases,provider_ids) VALUES('ZOE TEST','zoetest#x','zoe test','[]','{}')")
    db.conn.execute("UPDATE performers SET canonical_key='zoetest#y' WHERE id=?", (a,))
    db.conn.execute("UPDATE performers SET normalized_name='zoe test' WHERE id=?", (a,))
    new = upsert_performer(db, "Zoe  Test", provider="", provider_id=None)
    assert new not in (a,)
    assert any("more than one existing record matches" in m for _l, m, _k in db.logs), db.logs
    json.dumps([k["details"] for _l, _m, k in db.logs])  # details are plain data
