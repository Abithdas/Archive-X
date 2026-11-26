"""The one-time export of thumbnails and metadata works in batches and loses nothing at the batch edges.

It used to read every thumbnail (with its image data) and every media row into memory at once: about 1 GB for 175,000 small videos, and real
thumbnails are many times larger. Now 200 thumbnails and 500 videos at a time.
"""
from __future__ import annotations

import json

from telegram_archive import config
from telegram_archive.core.database import Database

NOW = "2026-11-26T00:00:00"
MEDIA, THUMBS = 1003, 403          # 3 batches of 500 videos, 3 batches of 200 thumbnails (the last ones short)


def test_every_video_and_thumbnail_is_exported_across_the_batch_edges(tmp_path):
    db = Database(tmp_path / "t.db")
    with db.lock:
        db.conn.executemany("INSERT INTO performers(name) VALUES(?)", [(f"P{i}",) for i in range(5)])
        db.conn.executemany(
            "INSERT INTO media(source_channel,source_message_id,filename,title,identity_key,status,created_at,updated_at) VALUES('-1007777',?,?,?,?,'ACTIVE',?,?)",
            [(i, f"f{i}.mp4", f"T{i}", f"export-test-{i}", NOW, NOW) for i in range(1, MEDIA + 1)])
        ids = [r[0] for r in db.conn.execute("SELECT id FROM media WHERE source_channel='-1007777'")]
        pid = [r[0] for r in db.conn.execute("SELECT id FROM performers")]
        db.conn.executemany("INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)", [(v, pid[v % 5]) for v in ids])
        db.conn.executemany(
            "INSERT INTO thumbnail_cache(source_channel,video_message_id,mime,data,source,updated_at) VALUES('-1007777',?,'image/jpeg',?,'playback-snapshot',?)",
            [(i, bytes([i % 251]) * 20, NOW) for i in range(1, THUMBS + 1)])
        db.conn.commit()
    counts = db.hydrate_persistent_asset_store()
    total_media = db.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    total_thumbs = db.conn.execute("SELECT COUNT(*) FROM thumbnail_cache WHERE data IS NOT NULL AND source='playback-snapshot'").fetchone()[0]
    assert counts["media"] == total_media >= MEDIA and counts["thumbnails"] == total_thumbs >= THUMBS
    for i in (1, 200, 201, 400, 401, THUMBS):                      # the first, the last and both sides of each batch edge
        assert (config.THUMBNAIL_DIR / "-1007777" / f"{i}.jpg").read_bytes() == bytes([i % 251]) * 20, i
    for v in (ids[0], ids[499], ids[500], ids[999], ids[1000], ids[-1]):
        payload = json.loads((config.MEDIA_METADATA_DIR / f"{v}.json").read_text(encoding="utf-8"))
        assert payload["id"] == v and len(payload["performers"]) == 1, v
    assert db.hydrate_persistent_asset_store() == {"skipped": True}      # and it still runs once
