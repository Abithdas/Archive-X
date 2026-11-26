"""Which videos the Library shows, kept as a table instead of worked out again for every page.

`archive_x_library_media_exact` is the rule: a video is shown when its snapshots (36 frames), its thumbnail, its representative image and
streamability are all ready and no split-file group holds it back. It asks seven indexed questions about each video, about 14 microseconds
each time, and every page, count, channel list and performer or studio list asked it again for every video: with 75,000 prepared videos the
Library count alone took 0.8 s and the pages built on it 1 to 1.7 s.

`ax_library_ready` holds the videos for which the rule is true. Database triggers on everything the rule reads (the video, its resource
state, snapshot and thumbnail rows, the representative image, the split-file tables) work the rule out again for the one video that changed,
whichever part of the application or the front door wrote it. `archive_x_library_media` is then the rule's answer read from the table.
`check` compares the two and `rebuild` makes the table equal to the rule again.
"""
from __future__ import annotations

import sqlite3

TABLE = "ax_library_ready"
EXACT_VIEW = "archive_x_library_media_exact"
MARKER = "ax_library_ready_v1"

_NEEDS = ("media", "resource_state", "timeline_preview_cache", "thumbnail_cache", "asset_registry", "split_file_groups", "split_file_parts")


def _refresh(ids_sql: str) -> str:
    return (f"DELETE FROM {TABLE} WHERE media_id IN ({ids_sql}); "
            f"INSERT OR IGNORE INTO {TABLE}(media_id) SELECT id FROM {EXACT_VIEW} WHERE id IN ({ids_sql});")


def _by_key(channel: str, message: str) -> str:
    return f"SELECT id FROM media WHERE source_channel={channel} AND source_message_id={message}"


def _asset_ids(row: str) -> str:
    # asset_registry names a Telegram video as "<channel>:<message>"; a channel id has no colon.
    return (f"SELECT {row}.media_id WHERE {row}.media_id IS NOT NULL UNION "
            f"SELECT id FROM media WHERE {row}.entity_type='telegram_video' AND instr({row}.entity_id,':')>0 "
            f"AND source_channel=substr({row}.entity_id,1,instr({row}.entity_id,':')-1) "
            f"AND source_message_id=CAST(substr({row}.entity_id,instr({row}.entity_id,':')+1) AS INTEGER)")


def _group_ids(row: str) -> str:
    return (f"{_by_key(row + '.destination_channel', row + '.primary_message_id')} UNION "
            f"SELECT m.id FROM media m JOIN split_file_parts sp ON sp.destination_channel=m.source_channel AND sp.message_id=m.source_message_id "
            f"WHERE sp.group_id={row}.group_id")


def _part_ids(row: str) -> str:
    return (f"{_by_key(row + '.destination_channel', row + '.message_id')} UNION "
            f"SELECT m.id FROM media m JOIN split_file_groups sg ON sg.destination_channel=m.source_channel AND sg.primary_message_id=m.source_message_id "
            f"WHERE sg.group_id={row}.group_id")


def _triggers() -> list[str]:
    out: list[str] = []

    def add(name: str, when: str, table: str, body: str) -> None:
        out.append(f"CREATE TRIGGER IF NOT EXISTS ax_lr_{name} AFTER {when} ON {table} BEGIN {body} END")

    for table, ids in (
        ("resource_state", lambda r: f"SELECT {r}.media_id"),
        ("timeline_preview_cache", lambda r: _by_key(f"{r}.source_channel", f"{r}.video_message_id")),
        ("thumbnail_cache", lambda r: _by_key(f"{r}.source_channel", f"{r}.video_message_id")),
        ("asset_registry", _asset_ids),
        ("split_file_groups", _group_ids),
        ("split_file_parts", _part_ids),
    ):
        add(f"{table}_insert", "INSERT", table, _refresh(ids("NEW")))
        add(f"{table}_update", "UPDATE", table, _refresh(ids("NEW")) + " " + _refresh(ids("OLD")))
        add(f"{table}_delete", "DELETE", table, _refresh(ids("OLD")))
    add("media_insert", "INSERT", "media", _refresh("SELECT NEW.id"))
    add("media_update", "UPDATE OF status, streamable, source_channel, source_message_id", "media", _refresh("SELECT NEW.id"))
    add("media_delete", "DELETE", "media", f"DELETE FROM {TABLE} WHERE media_id=OLD.id;")
    return out


def rebuild(conn: sqlite3.Connection) -> int:
    """Make the table equal to the rule again; returns how many videos it holds."""
    conn.execute(f"DELETE FROM {TABLE}")
    conn.execute(f"INSERT OR IGNORE INTO {TABLE}(media_id) SELECT id FROM {EXACT_VIEW}")
    return int(conn.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])


def check(conn: sqlite3.Connection) -> tuple[int, int]:
    """(videos the rule shows that the table lacks, videos the table holds that the rule does not show). Both are 0 when they agree."""
    missing = conn.execute(f"SELECT COUNT(*) FROM {EXACT_VIEW} e WHERE NOT EXISTS (SELECT 1 FROM {TABLE} t WHERE t.media_id=e.id)").fetchone()[0]
    extra = conn.execute(f"SELECT COUNT(*) FROM {TABLE} t WHERE NOT EXISTS (SELECT 1 FROM {EXACT_VIEW} e WHERE e.id=t.media_id)").fetchone()[0]
    return int(missing), int(extra)


def install(conn: sqlite3.Connection) -> None:
    """Create the table and the triggers (once), and fill the table the first time. The rule view must exist already."""
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if any(t not in have for t in _NEEDS):
        return   # a database without the readiness tables has no Library to keep
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (media_id INTEGER PRIMARY KEY)")
    for sql in _triggers():
        conn.execute(sql)
    done = conn.execute("SELECT 1 FROM settings WHERE key=?", (MARKER,)).fetchone() if "settings" in have else None
    if not done:
        rebuild(conn)
        if "settings" in have:
            conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?, '1')", (MARKER,))
