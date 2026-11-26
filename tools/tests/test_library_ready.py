"""The stored list of Library videos (`ax_library_ready`) must equal the rule (`archive_x_library_media_exact`) after every change.

Random changes to every table the rule reads (the video, its resource state, snapshot and thumbnail rows, the representative image, the split-file
groups and parts), including REPLACE and deletes, and after each one the two must agree. A video missing from the Library, or a stale one still
shown, is the failure this guards against.
"""
from __future__ import annotations

import random

from telegram_archive.core import library_ready as lr
from telegram_archive.core.database import Database

CHANNELS = ("-1001000000001", "-1001000000002")
MESSAGES = (1, 2, 3, 4, 5, 6)
NOW = "2026-11-26T00:00:00"


def _db(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        for ch in CHANNELS:
            for m in MESSAGES:
                db.conn.execute(
                    "INSERT INTO media(source_channel,source_message_id,filename,streamable,status,created_at,updated_at) VALUES(?,?,?,1,'ACTIVE',?,?)",
                    (ch, m, f"v{m}.mp4", NOW, NOW))
        db.conn.commit()
    return db


def _agree(db):
    with db.lock:
        assert lr.check(db.conn) == (0, 0)
        shown = {r[0] for r in db.conn.execute("SELECT id FROM archive_x_library_media")}
        exact = {r[0] for r in db.conn.execute("SELECT id FROM archive_x_library_media_exact")}
    assert shown == exact


def _operations(rng):
    ch, m = rng.choice(CHANNELS), rng.choice(MESSAGES)
    kind = rng.randrange(16)
    mid = f"(SELECT id FROM media WHERE source_channel='{ch}' AND source_message_id={m})"
    state = rng.choice(("READY", "READY", "PENDING"))
    frames = rng.choice((36, 36, 12))
    if kind == 0:
        return ("INSERT OR REPLACE INTO resource_state(media_id,catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,updated_at) "
                f"VALUES({mid},'COMPLETE','COMPLETE','COMPLETE','{state}','COMPLETE','COMPLETE','{NOW}')")
    if kind == 1:
        return f"UPDATE resource_state SET snapshot_state='{state}' WHERE media_id={mid}"
    if kind == 2:
        return f"DELETE FROM resource_state WHERE media_id={mid}"
    if kind == 3:
        return (f"INSERT OR REPLACE INTO timeline_preview_cache(source_channel,video_message_id,path,status,frame_count,updated_at) "
                f"VALUES('{ch}',{m},'p','{state}',{frames},'{NOW}')")
    if kind == 4:
        return f"DELETE FROM timeline_preview_cache WHERE source_channel='{ch}' AND video_message_id={m}"
    if kind == 5:
        return (f"INSERT OR REPLACE INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) "
                f"VALUES('{ch}',{m},'image/jpeg',x'{'ff' * rng.choice((0, 8))}','playback-snapshot','{NOW}')")
    if kind == 6:
        return f"DELETE FROM thumbnail_cache WHERE source_channel='{ch}' AND video_message_id={m}"
    if kind == 7:   # the representative image named by "<channel>:<message>"
        return (f"INSERT OR REPLACE INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,created_at,updated_at) "
                f"VALUES(NULL,'telegram_video','{ch}:{m}','PLAYBACK_REPRESENTATIVE','a','{state}','{NOW}','{NOW}')")
    if kind == 8:   # the representative image named by the video's id
        return (f"INSERT OR REPLACE INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,created_at,updated_at) "
                f"VALUES({mid},'other','x{ch}{m}','PLAYBACK_REPRESENTATIVE','a','{state}','{NOW}','{NOW}')")
    if kind == 9:
        return f"DELETE FROM asset_registry WHERE entity_id IN ('{ch}:{m}','x{ch}{m}')"
    if kind == 10:
        return f"UPDATE media SET streamable={rng.choice((0, 1))} WHERE source_channel='{ch}' AND source_message_id={m}"
    if kind == 11:
        return f"UPDATE media SET status='{rng.choice(('ACTIVE', 'SOURCE_DELETED'))}' WHERE source_channel='{ch}' AND source_message_id={m}"
    if kind == 12:
        return (f"INSERT OR REPLACE INTO split_file_groups(group_id,destination_channel,logical_filename,primary_message_id,total_size,part_count,mime_type,duration,"
                f"width,height,fps,bitrate,codec,audio_codec,caption,identity_key,status,created_at,updated_at) "
                f"VALUES('g{ch}{m}','{ch}','f',{m},1,2,'v',1,1,1,1,1,'c','a','','k','{rng.choice(('VERIFIED', 'PENDING'))}','{NOW}','{NOW}')")
    if kind == 13:
        return (f"INSERT OR REPLACE INTO split_file_parts(group_id,part_index,destination_channel,message_id,logical_filename,telegram_filename,size_bytes,created_at) "
                f"VALUES('g{ch}{m}',{rng.choice((0, 1))},'{ch}',{rng.choice(MESSAGES)},'f','t',1,'{NOW}')")
    if kind == 14:
        return f"DELETE FROM split_file_groups WHERE group_id='g{ch}{m}'"
    return f"DELETE FROM media WHERE source_channel='{ch}' AND source_message_id={m}"


def _make_ready(db, ch, m):
    with db.lock:
        for sql in (
            f"INSERT OR REPLACE INTO resource_state(media_id,catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,updated_at) "
            f"SELECT id,'COMPLETE','COMPLETE','COMPLETE','READY','COMPLETE','COMPLETE','{NOW}' FROM media WHERE source_channel='{ch}' AND source_message_id={m}",
            f"INSERT OR REPLACE INTO timeline_preview_cache(source_channel,video_message_id,path,status,frame_count,updated_at) VALUES('{ch}',{m},'p','READY',36,'{NOW}')",
            f"INSERT OR REPLACE INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) VALUES('{ch}',{m},'image/jpeg',x'ffff','playback-snapshot','{NOW}')",
            f"INSERT OR REPLACE INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,created_at,updated_at) "
            f"VALUES(NULL,'telegram_video','{ch}:{m}','PLAYBACK_REPRESENTATIVE','a','READY','{NOW}','{NOW}')",
        ):
            db.conn.execute(sql)
        db.conn.commit()


def test_a_video_appears_when_everything_it_needs_is_ready_and_leaves_when_any_of_it_goes(tmp_path):
    db = _db(tmp_path)
    count = lambda: db.conn.execute("SELECT COUNT(*) FROM archive_x_library_media").fetchone()[0]
    assert count() == 0
    _make_ready(db, CHANNELS[0], 1)
    assert count() == 1
    for undo in ("DELETE FROM thumbnail_cache", "DELETE FROM timeline_preview_cache", "DELETE FROM asset_registry", "DELETE FROM resource_state",
                 "UPDATE media SET streamable=0", "UPDATE media SET status='SOURCE_DELETED'"):
        _make_ready(db, CHANNELS[0], 1)
        with db.lock:
            db.conn.execute("UPDATE media SET streamable=1,status='ACTIVE'")
            db.conn.commit()
        assert count() == 1
        with db.lock:
            db.conn.execute(undo)
            db.conn.commit()
        assert count() == 0, undo
        _agree(db)


def test_a_split_group_that_is_not_verified_hides_its_video(tmp_path):
    db = _db(tmp_path)
    _make_ready(db, CHANNELS[0], 2)
    with db.lock:
        db.conn.execute(
            "INSERT INTO split_file_groups(group_id,destination_channel,logical_filename,primary_message_id,total_size,part_count,mime_type,duration,width,height,"
            "fps,bitrate,codec,audio_codec,caption,identity_key,status,created_at,updated_at) "
            f"VALUES('g','{CHANNELS[0]}','f',2,1,2,'v',1,1,1,1,1,'c','a','','k','PENDING','{NOW}','{NOW}')")
        db.conn.commit()
    assert db.conn.execute("SELECT COUNT(*) FROM archive_x_library_media").fetchone()[0] == 0
    with db.lock:
        db.conn.execute("UPDATE split_file_groups SET status='VERIFIED'")
        db.conn.commit()
    assert db.conn.execute("SELECT COUNT(*) FROM archive_x_library_media").fetchone()[0] == 1
    _agree(db)


def test_after_any_sequence_of_changes_the_stored_list_equals_the_rule(tmp_path):
    for seed in range(6):
        rng = random.Random(seed)
        db = _db(tmp_path / f"s{seed}") if (tmp_path / f"s{seed}").mkdir() is None else None
        for ch in CHANNELS:           # start with a mix of ready videos so removals are exercised too
            for m in MESSAGES[:3]:
                _make_ready(db, ch, m)
        _agree(db)
        for step in range(250):
            sql = _operations(rng)
            with db.lock:
                try:
                    db.conn.execute(sql)
                    db.conn.commit()
                except Exception as exc:   # a change the schema refuses (a duplicate part, say) leaves nothing half done
                    db.conn.rollback()
                    assert "UNIQUE" in str(exc) or "constraint" in str(exc).lower(), (sql, exc)
            _agree(db)


def test_the_table_is_filled_from_the_rule_when_it_is_first_made(tmp_path):
    db = _db(tmp_path)
    _make_ready(db, CHANNELS[1], 3)
    with db.lock:
        db.conn.execute("DELETE FROM ax_library_ready")
        db.conn.execute("DELETE FROM settings WHERE key=?", (lr.MARKER,))
        db.conn.commit()
        lr.install(db.conn)
        db.conn.commit()
    _agree(db)
    assert db.conn.execute("SELECT COUNT(*) FROM ax_library_ready").fetchone()[0] == 1
