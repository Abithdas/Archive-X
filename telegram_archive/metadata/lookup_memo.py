"""What the provider lookup was asked, remembered for videos it found nothing for.

A "no match" already stamped the video (`metadata_synced_at`) and the next run left it alone for 30 days. That stamp knew nothing about WHAT was
searched, so a video whose filename was fixed, whose file hash was read later, or that was checked before a provider key or the visual match was
switched on waited out the 30 days although the new evidence might find it.

The memo keeps the evidence of the last no-match: filename, file hash and which providers were asked (names only, never keys). A run asks again
before the 30 days only when one of those changed, and `force` still asks about everything. The table is made on first use.
"""
from __future__ import annotations

TABLE = "metadata_lookup_memo"


def ensure(db) -> None:
    with db.lock:
        db.conn.execute(
            f"CREATE TABLE IF NOT EXISTS {TABLE} (media_id INTEGER PRIMARY KEY, filename TEXT, oshash TEXT NOT NULL DEFAULT '', "
            "providers TEXT NOT NULL DEFAULT '', checked_at TEXT NOT NULL)")
        db.conn.commit()


def providers_signature(db, visual: bool) -> str:
    """Which lookups were available: the providers that have a key, and whether the visual match was on. Never the keys themselves."""
    stash = bool(db.setting("stashdb_api_key"))
    tpdb = bool(db.setting("tpdb_api_token") or db.setting("tpdb_token"))
    return f"stashdb={int(stash)};tpdb={int(tpdb)};visual={int(bool(visual))}"


def changed_clause(providers: str) -> tuple[str, list]:
    """SQL for "this video has a no-match memo whose evidence is no longer the current evidence"; use inside a query on `media`."""
    return (f"EXISTS (SELECT 1 FROM {TABLE} lm WHERE lm.media_id=media.id AND (lm.filename IS NOT media.filename "
            "OR lm.oshash <> COALESCE(media.oshash,'') OR lm.providers <> ?))", [providers])


def remember(db, media_ids, providers: str, now: str) -> int:
    """Record the current filename and hash of these videos as the evidence of a no-match. Returns how many were recorded."""
    ids = [int(i) for i in media_ids]
    if not ids:
        return 0
    with db.lock:
        db.conn.executemany(
            f"INSERT OR REPLACE INTO {TABLE}(media_id, filename, oshash, providers, checked_at) "
            "SELECT id, filename, COALESCE(oshash,''), ?, ? FROM media WHERE id=?",
            [(providers, now, i) for i in ids])
        db.conn.commit()
    return len(ids)


def forget(db, media_ids) -> None:
    ids = [int(i) for i in media_ids]
    if not ids:
        return
    with db.lock:
        db.conn.executemany(f"DELETE FROM {TABLE} WHERE media_id=?", [(i,) for i in ids])
        db.conn.commit()
