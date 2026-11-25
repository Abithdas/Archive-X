"""A real performer or studio replaces the "Unknown" placeholder of a video, whichever way it was added.

"Unknown Performer" and "Unknown Studio" say nobody has been named yet. Once a video has a real performer (or studio) the placeholder is wrong, and it was
only taken away on some paths (the file-name reader did, a hand edit or a bulk edit did not), so a video could show "Unknown Performer" next to the name
that was just added. The rule lives in the database instead of in each path: adding a link to anyone else deletes the video's placeholder link.
Adding the placeholder itself does nothing, so choosing "Unknown" on purpose still works.
"""
from __future__ import annotations

PERFORMER_TABLES = ("video_performers", "local_video_performers")


def _trigger(table: str, column: str, entity_table: str, placeholder: str, others: tuple[str, ...]) -> str:
    # Deleting from the other tables of the same kind too: a video named in one is named for every list that reads the other.
    drops = "".join(
        f" DELETE FROM {t} WHERE video_id=NEW.video_id AND {column} IN (SELECT id FROM {entity_table} WHERE lower(trim(name))='{placeholder}');"
        for t in others)
    return (f"CREATE TRIGGER IF NOT EXISTS ax_placeholder_{table}_insert AFTER INSERT ON {table} "
            f"WHEN NEW.{column} NOT IN (SELECT id FROM {entity_table} WHERE lower(trim(name))='{placeholder}') BEGIN{drops} END")


def install(conn) -> None:
    """Create the triggers, and clear the placeholders that videos already carry next to a real name. Safe to run again."""
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "performers" in existing:
        tables = tuple(t for t in PERFORMER_TABLES if t in existing)
        for t in tables:
            conn.execute(_trigger(t, "performer_id", "performers", "unknown performer", tables))
    if "studios" in existing and "video_studios" in existing:
        conn.execute(_trigger("video_studios", "studio_id", "studios", "unknown studio", ("video_studios",)))
    if "performers" in existing:
        for t in (t for t in PERFORMER_TABLES if t in existing):
            conn.execute(
                f"DELETE FROM {t} WHERE performer_id IN (SELECT id FROM performers WHERE lower(trim(name))='unknown performer') "
                f"AND video_id IN (SELECT video_id FROM {t} x WHERE x.performer_id NOT IN (SELECT id FROM performers WHERE lower(trim(name))='unknown performer'))")
    if "studios" in existing and "video_studios" in existing:
        conn.execute(
            "DELETE FROM video_studios WHERE studio_id IN (SELECT id FROM studios WHERE lower(trim(name))='unknown studio') "
            "AND video_id IN (SELECT video_id FROM video_studios x WHERE x.studio_id NOT IN (SELECT id FROM studios WHERE lower(trim(name))='unknown studio'))")
    conn.commit()
