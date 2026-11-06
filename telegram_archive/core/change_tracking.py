"""Data-change counters the web UI watches to refresh itself (V29.4.131).

SQLite triggers bump one counter per *scope* whenever a row in a tracked table
is inserted, updated or deleted -- from any connection, job, worker thread or
process. The status payload that the browser receives over ``/ws`` (or polls
from ``/api/status``) carries the counters, so an open page sees within about a
second that videos, performers/studios, channels or settings changed and
redraws what is on screen. Nothing has to remember to call a "notify" function,
so new write paths are covered automatically.
"""
from __future__ import annotations

import sqlite3

TABLE = "data_versions"

# scope -> tables whose changes it represents
SCOPES: dict[str, tuple[str, ...]] = {
    # What a video card / list shows: the video row, its links, whether it is
    # published (resource_state, asset_registry), its thumbnail.
    "videos": ("media", "video_performers", "video_studios", "video_tags",
               "resource_state", "asset_registry", "thumbnail_cache"),
    "entities": ("performers", "studios", "tags", "entity_favorites"),
    "channels": ("channels",),
    "settings": ("settings",),
}

# Settings rows that are internal bookkeeping, not user settings.
_SETTINGS_IGNORE = ("job_state:%",)


def _trigger_sql(scope: str, table: str, event: str) -> str:
    name = f"axcv_{table}_{event.lower()}"
    when = ""
    if table == "settings":
        row = "OLD" if event == "DELETE" else "NEW"
        when = " WHEN " + " AND ".join(f"{row}.key NOT LIKE '{p}'" for p in _SETTINGS_IGNORE)
    return (f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {event} ON {table}{when} "
            f"BEGIN UPDATE {TABLE} SET version=version+1 WHERE scope='{scope}'; END")


def install(conn: sqlite3.Connection) -> int:
    """Create the counter table and the triggers for every tracked table that
    exists. Safe to call repeatedly; returns the number of tracked tables."""
    conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (scope TEXT PRIMARY KEY, version INTEGER NOT NULL DEFAULT 0)")
    conn.executemany(f"INSERT OR IGNORE INTO {TABLE}(scope, version) VALUES(?, 0)", [(s,) for s in SCOPES])
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    tracked = 0
    for scope, tables in SCOPES.items():
        for table in tables:
            if table not in existing:
                continue
            for event in ("INSERT", "UPDATE", "DELETE"):
                conn.execute(_trigger_sql(scope, table, event))
            tracked += 1
    conn.commit()
    return tracked


def ensure(db) -> None:
    """Install once per write connection, again after new tables appear."""
    try:
        conn = db.conn
    except Exception:
        return
    # Throttle the sqlite_master check: it ran (under the write lock) on every
    # status poll. New tracked tables only appear at startup/migrations.
    import time as _time
    state = getattr(db, "_change_tracking_tables", None)
    if state and state[0] == id(conn) and _time.monotonic() - getattr(db, "_change_tracking_checked_at", 0.0) < 30.0:
        return
    try:
        db._change_tracking_checked_at = _time.monotonic()
    except Exception:
        pass
    try:
        with db.lock:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name IN (%s)"
                % ",".join("?" for _ in _all_tables()), _all_tables()).fetchone()[0]
            if getattr(db, "_change_tracking_tables", None) == (id(conn), count):
                return
            install(conn)
            db._change_tracking_tables = (id(conn), count)
    except Exception:
        pass


def _all_tables() -> tuple[str, ...]:
    return tuple(t for tables in SCOPES.values() for t in tables)


def versions(db) -> dict[str, int]:
    """Current counters, e.g. {"videos": 812, "entities": 40, ...}."""
    try:
        with db.reading() as conn:
            rows = conn.execute(f"SELECT scope, version FROM {TABLE}").fetchall()
        return {str(r[0]): int(r[1]) for r in rows}
    except Exception:
        return {}
