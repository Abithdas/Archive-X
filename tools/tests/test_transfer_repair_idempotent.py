"""The start-up repair of verified transfers must not rewrite finished rows.

It used to call mark_prepared for every verified transfer at every start; that rewrites the row and commits each time, so a library
with thousands of transfers wrote (and flushed to disk) thousands of rows at every launch, which kept the disk at 100 %.
"""
from __future__ import annotations


from telegram_archive.core.database import Database
from telegram_archive.core.telegram_registry import ensure_telegram_registry
from telegram_archive.engines import delivery_ledger


def _seed(db, n: int) -> None:
    ensure_telegram_registry(db)
    delivery_ledger.ensure_delivery_ledger_schema(db)
    with db.lock:
        # The real Library view needs a long list of readiness rows; the repair only needs "this media row is published".
        db.conn.execute("DROP VIEW IF EXISTS archive_x_published_media")
        db.conn.execute("CREATE VIEW archive_x_published_media AS SELECT id FROM media")
        for i in range(1, n + 1):
            cur = db.conn.execute(
                "INSERT INTO media(filename,source_channel,source_message_id,status,created_at,updated_at,oshash,video_phash) "
                "VALUES(?,?,?,?,datetime('now'),datetime('now'),?,?)", (f"v{i}.mp4", "-100200", 1000 + i, "ACTIVE", f"h{i}", f"p{i}"))
            db.conn.execute(
                "INSERT INTO archive_x_deliveries(source_kind,source_channel,source_message_id,destination_channel,destination_video_message_id,"
                "status,verified_at,created_at,updated_at) VALUES('telegram_transfer','-100100',?,?,?,'VERIFIED',datetime('now'),datetime('now'),datetime('now'))", (i, "-100200", 1000 + i))
            db.conn.execute(
                "INSERT INTO telegram_document_registry(registry_key,document_id,created_at,updated_at) VALUES(?,?,datetime('now'),datetime('now'))",
                (f"k{i}", f"d{i}"))
            db.conn.execute(
                "INSERT INTO telegram_document_locations(channel_id,message_id,registry_key,last_seen_at) VALUES(?,?,?,datetime('now'))",
                ("-100200", 1000 + i, f"k{i}"))
        db.conn.commit()


def test_a_second_start_writes_nothing_for_transfers_that_are_already_prepared(tmp_path):
    db = Database(tmp_path / "t.db")
    _seed(db, 5)
    first = delivery_ledger.repair_verified_transfer_source_library_rows(db)
    assert first["destinations_marked_prepared"] == 5
    stamp = db.conn.execute("SELECT group_concat(updated_at) FROM telegram_document_registry").fetchone()[0]
    changes = db.conn.total_changes
    second = delivery_ledger.repair_verified_transfer_source_library_rows(db)
    assert second["destinations_marked_prepared"] == 0
    assert db.conn.execute("SELECT group_concat(updated_at) FROM telegram_document_registry").fetchone()[0] == stamp
    assert db.conn.total_changes == changes   # not one row written


def test_a_transfer_not_yet_prepared_is_still_repaired(tmp_path):
    db = Database(tmp_path / "t2.db")
    _seed(db, 2)
    delivery_ledger.repair_verified_transfer_source_library_rows(db)
    with db.lock:
        db.conn.execute("UPDATE telegram_document_registry SET prepared_state='PENDING',prepared_json='{}' WHERE registry_key='k1'")
        db.conn.commit()
    again = delivery_ledger.repair_verified_transfer_source_library_rows(db)
    assert again["destinations_marked_prepared"] == 1
    assert db.conn.execute("SELECT prepared_state FROM telegram_document_registry WHERE registry_key='k1'").fetchone()[0] == "PREPARED"


