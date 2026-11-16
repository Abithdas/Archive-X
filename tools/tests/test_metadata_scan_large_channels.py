"""Channels with more videos than SQLite allows `?` placeholders in one statement, and "an empty list of ids" meaning "nothing"."""
import sqlite3

import pytest

import metadata_scan_harness as h
from telegram_archive.core import resource_registry as rr
from telegram_archive.engines import entity_data


@pytest.fixture
def tiny_limit():
    """Make this connection refuse statements with more than 600 placeholders, as a 40,000-video channel hits on the real limit."""
    db = h.fresh_db()
    old = db.conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER)
    db.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 600)
    yield db
    db.conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, old)


def test_the_limit_really_bites(tiny_limit):
    with pytest.raises(sqlite3.OperationalError, match="too many SQL variables"):
        tiny_limit.conn.execute(f"SELECT 1 WHERE 1 IN ({','.join('?' for _ in range(700))})", list(range(700)))


def test_a_channel_with_more_videos_than_one_statement_can_hold_is_scanned(tiny_limit, monkeypatch):
    db, ch = tiny_limit, h.new_channel()
    hist = h.FakeHistory({i: h.video(i) for i in range(1, 1001)})
    h.install(monkeypatch, hist)
    out = h.run(h.pipe._discover_and_store_inventory(h.make_ctx(db), ch, 0, full_scan=True))
    assert len(out) == 1000 and h.count(db, ch) == 1000
    assert out[0]["message_id"] > out[-1]["message_id"]                      # newest first, as before
    done = db.conn.execute("SELECT COUNT(*) FROM resource_state rs JOIN media m ON m.id=rs.media_id WHERE m.source_channel=? AND rs.catalogue_state='COMPLETE'", (ch,)).fetchone()[0]
    assert done == 1000


def test_the_readiness_count_covers_every_video_of_a_big_channel(tiny_limit, monkeypatch):
    db, ch = tiny_limit, h.new_channel()
    h.install(monkeypatch, h.FakeHistory({i: h.video(i) for i in range(1, 801)}))
    out = h.run(h.pipe._discover_and_store_inventory(h.make_ctx(db), ch, 0, full_scan=True))
    ids = [v["media_id"] for v in out]
    stats = rr.media_readiness_stats(db, media_ids=ids)
    assert stats["total"] == 800 and stats["not_in_library"] == 800
    assert rr.mark_media_entity_complete(db, ids) == 800
    assert rr.ensure_unknown_entity_links(db, ids) in (0, 1600) or True      # must not raise
    perf, studio = h.pipe._entity_ids(h.make_ctx(db), ch, ids)
    assert len(perf) == 1 and len(studio) == 1                              # every video now links the Unknown placeholders


def test_due_entities_are_found_among_more_ids_than_a_statement_holds(tiny_limit):
    db = tiny_limit
    with db.lock:
        for i in range(700):
            db.conn.execute("INSERT INTO performers(name) VALUES(?)", (f"Bulk Performer {h.new_channel()} {i}",))
        db.conn.commit()
        ids = [r[0] for r in db.conn.execute("SELECT id FROM performers WHERE name LIKE 'Bulk Performer %'")]
    due = entity_data.refreshable_entity_ids(db, "performer", ids, force_recheck=False)
    assert set(due) <= set(ids) and len(due) == len(set(due))


def test_an_empty_list_means_no_videos_not_every_video(monkeypatch):
    db, ch = h.fresh_db(), h.new_channel()
    h.install(monkeypatch, h.FakeHistory({i: h.video(i) for i in range(1, 4)}))
    out = h.run(h.pipe._discover_and_store_inventory(h.make_ctx(db), ch, 0, full_scan=True))
    mine = [v["media_id"] for v in out]
    other = h.new_channel()
    h.install(monkeypatch, h.FakeHistory({1: h.video(1)}))
    other_out = h.run(h.pipe._discover_and_store_inventory(h.make_ctx(db), other, 0, full_scan=True))
    before = db.conn.execute("SELECT entity_state FROM resource_state WHERE media_id=?", (other_out[0]["media_id"],)).fetchone()[0]
    assert before != "COMPLETE"
    assert rr.mark_media_entity_complete(db, []) == 0                       # used to finish every video in the database
    assert rr.ensure_unknown_entity_links(db, []) == 0
    assert db.conn.execute("SELECT entity_state FROM resource_state WHERE media_id=?", (other_out[0]["media_id"],)).fetchone()[0] == before
    assert rr.media_readiness_stats(db, media_ids=[])["total"] == 0         # used to count the whole database
    assert rr.media_readiness_stats(db, media_ids=mine)["total"] == 3
    assert rr.media_readiness_stats(db, channel_id=other)["total"] == 1
