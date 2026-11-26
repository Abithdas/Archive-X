"""Counting a performer's or studio's videos through the base link tables gives what counting through the content filter's views gives.

The lists keep only videos that are in `archive_x_library_media`, which the content mode already limits, so reading `main.video_studios` and
friends (instead of the filter's views of them, a join with `media` for every link) must change nothing but the time.
"""
from __future__ import annotations

import sqlite3

import pytest

from telegram_archive.core import content_mode as cm
from telegram_archive.core.database import Database

NOW = "2026-11-26T00:00:00"
LIB = "SELECT id FROM archive_x_library_media"


def _seed(db):
    with db.lock:
        c = db.conn
        for tid, rating in (("-1001", "sfw"), ("-1002", "nsfw"), ("-1003", "nsfw")):
            c.execute("INSERT INTO channels(telegram_id,title,updated_at,in_library,content_rating) VALUES(?,?,?,1,?)", (tid, tid, NOW, rating))
        c.executemany("INSERT INTO performers(name,content_rating) VALUES(?,?)", [("Pa", "sfw"), ("Pb", "nsfw"), ("Pc", "nsfw")])
        c.executemany("INSERT INTO studios(name,content_rating) VALUES(?,?)", [("Sa", "sfw"), ("Sb", "nsfw")])
        n = 0
        for ch in ("-1001", "-1002", "-1003", "-1004"):      # -1004 has no channel row: older data, counted as an NSFW library channel
            for m in range(1, 7):
                n += 1
                c.execute("INSERT INTO media(source_channel,source_message_id,filename,streamable,status,created_at,updated_at) VALUES(?,?,?,1,'ACTIVE',?,?)", (ch, m, f"v{n}", NOW, NOW))
                if m % 3:        # two of three videos are ready for the Library
                    c.execute("INSERT INTO resource_state(media_id,catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,updated_at) "
                              "SELECT id,'COMPLETE','COMPLETE','COMPLETE','READY','COMPLETE','COMPLETE',? FROM media WHERE source_channel=? AND source_message_id=?", (NOW, ch, m))
                    c.execute("INSERT INTO timeline_preview_cache(source_channel,video_message_id,path,status,frame_count,updated_at) VALUES(?,?,'p','READY',36,?)", (ch, m, NOW))
                    c.execute("INSERT INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) VALUES(?,?,'image/jpeg',x'ffff','playback-snapshot',?)", (ch, m, NOW))
                    c.execute("INSERT INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,state,created_at,updated_at) VALUES(NULL,'telegram_video',?,'PLAYBACK_REPRESENTATIVE','a','READY',?,?)", (f"{ch}:{m}", NOW, NOW))
        ids = [r[0] for r in c.execute("SELECT id FROM media")]
        pids = [r[0] for r in c.execute("SELECT id FROM performers WHERE name IN ('Pa','Pb','Pc') ORDER BY name")]      # by name: the database also holds the "Unknown" placeholders
        sids = [r[0] for r in c.execute("SELECT id FROM studios WHERE name IN ('Sa','Sb') ORDER BY name")]
        c.executemany("INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)", [(v, pids[v % 3]) for v in ids])
        c.executemany("INSERT INTO local_video_performers(video_id,performer_id,created_at) VALUES(?,?,?)", [(v, pids[(v + 1) % 3], NOW) for v in ids if v % 2])
        c.executemany("INSERT OR IGNORE INTO video_studios(video_id,studio_id) VALUES(?,?)", [(v, sids[v % 2]) for v in ids])
        c.commit()


def _filtered(path):
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    cm.install_views(conn)
    conn.execute("PRAGMA query_only=ON")
    return conn


STUDIOS = ("SELECT s.id, COUNT(DISTINCT CASE WHEN vs.video_id IN (" + LIB + ") THEN vs.video_id END) video_count FROM studios s "
           "LEFT JOIN {t} vs ON vs.studio_id=s.id GROUP BY s.id HAVING video_count>0 OR s.name='Unknown Studio' ORDER BY s.id")
PERFORMERS = ("SELECT p.id, COUNT(DISTINCT combined.video_id) video_count FROM performers p LEFT JOIN "
              "(SELECT performer_id, video_id FROM {a} WHERE video_id IN (" + LIB + ") UNION SELECT performer_id, video_id FROM {b} WHERE video_id IN (" + LIB + ")) combined "
              "ON combined.performer_id=p.id GROUP BY p.id HAVING video_count>0 OR p.name='Unknown Performer' ORDER BY p.id")


@pytest.mark.parametrize("mode", ["sfw", "nsfw"])
def test_the_counts_are_the_same_through_the_base_tables_in_both_modes(tmp_path, mode):
    db = Database(tmp_path / "t.db")
    _seed(db)
    db.set_setting("content_mode", mode)
    db.conn.commit()
    db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn = _filtered(db.path)
    old_s = [tuple(r) for r in conn.execute(STUDIOS.format(t="video_studios"))]
    new_s = [tuple(r) for r in conn.execute(STUDIOS.format(t="main.video_studios"))]
    old_p = [tuple(r) for r in conn.execute(PERFORMERS.format(a="video_performers", b="local_video_performers"))]
    new_p = [tuple(r) for r in conn.execute(PERFORMERS.format(a="main.video_performers", b="main.local_video_performers"))]
    assert old_s == new_s and old_p == new_p
    assert any(n for _, n in new_p) and any(n for _, n in new_s), "the data must give some counts for this to prove anything"
