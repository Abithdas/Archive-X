"""Start the app again when its database file is damaged, keeping every row that can still be read.

SQLite reports "database disk image is malformed" when a page of the file is unreadable (an unclean shutdown, a full disk, a failing drive, a
cleaner that touched the file). The application used to stop at start-up with that message and could not be opened again until the file was
deleted by hand. Now the damaged file is moved aside (nothing is deleted), a fresh database is created, and each table is copied across row by
row; a damaged page costs only the rows on it. The damaged copy stays in a `damaged-<time>` folder next to the database.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

CORRUPTION_WORDS = ("malformed", "not a database", "disk image", "file is encrypted or is not a database")
SKIP_STRIDE = 64          # rowids stepped over after an unreadable page, to land past it
MAX_SKIPS = 4000          # per table, so a hopelessly damaged table cannot keep the start waiting


def is_corruption(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.DatabaseError) and any(w in str(exc).lower() for w in CORRUPTION_WORDS)


def quarantine(db_path: Path) -> Path:
    """Move the database and its -wal/-shm files into a new `damaged-<time>` folder beside it; returns the moved database file."""
    folder = db_path.parent / f"damaged-{time.strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    moved = folder / db_path.name
    for suffix in ("", "-wal", "-shm", "-journal"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.move(str(src), str(folder / (db_path.name + suffix)))
    return moved


def _virtual_prefixes(conn) -> tuple[str, ...]:
    try:
        return tuple(r[0] + "_" for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND sql LIKE 'CREATE VIRTUAL%'"))
    except sqlite3.Error:
        return ()


def _rows(old, table: str, columns: list[str]):
    """Yield the rows of a table that can be read; steps over unreadable pages instead of stopping at the first one."""
    cols = ",".join(f'"{c}"' for c in columns)
    try:
        old.execute(f'SELECT rowid FROM "{table}" LIMIT 0')
    except sqlite3.OperationalError as exc:
        if "rowid" not in str(exc).lower():
            return
        # A table without a rowid cannot be stepped over; read it until the first unreadable page.
        try:
            yield from old.execute(f'SELECT {cols} FROM "{table}"')
        except sqlite3.Error:
            pass
        return
    last = 0
    skips = 0
    while True:
        try:
            for row in old.execute(f'SELECT rowid,{cols} FROM "{table}" WHERE rowid>? ORDER BY rowid', (last,)):
                last = row[0]
                yield row[1:]
            return
        except sqlite3.Error:
            skips += 1
            if skips > MAX_SKIPS:
                return
            # Step past the page that could not be read and carry on from the next readable row.
            last += SKIP_STRIDE
            try:
                top = old.execute(f'SELECT max(rowid) FROM "{table}"').fetchone()[0]
            except sqlite3.Error:
                return
            if top is None or last > top:
                return


def salvage(damaged: Path, new_db) -> dict:
    """Copy what can be read from the damaged file into the freshly created `new_db`. Returns {table: rows copied} and the rows left behind."""
    result: dict = {"copied": {}, "tables_unreadable": [], "rows_copied": 0}
    try:
        old = sqlite3.connect(str(damaged))
    except sqlite3.Error:
        result["tables_unreadable"].append("(the whole file)")
        return result
    try:
        try:
            old_tables = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        except sqlite3.Error:
            result["tables_unreadable"].append("(the table list)")
            return result
        with new_db.lock:
            conn = new_db.conn
            skip = _virtual_prefixes(conn)
            fresh = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            for table in old_tables:
                if table not in fresh or any(table.startswith(p) for p in skip):
                    continue
                try:
                    old_cols = [r[1] for r in old.execute(f'PRAGMA table_info("{table}")')]
                except sqlite3.Error:
                    result["tables_unreadable"].append(table)
                    continue
                new_cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
                columns = [c for c in old_cols if c in new_cols]
                if not columns:
                    continue
                marks = ",".join("?" for _ in columns)
                insert = f'INSERT OR IGNORE INTO "{table}"({",".join(chr(34) + c + chr(34) for c in columns)}) VALUES({marks})'
                count = 0
                batch: list = []
                for row in _rows(old, table, columns):
                    batch.append(row)
                    if len(batch) >= 500:
                        conn.executemany(insert, batch)
                        count += len(batch)
                        batch = []
                if batch:
                    conn.executemany(insert, batch)
                    count += len(batch)
                conn.commit()
                result["copied"][table] = count
                result["rows_copied"] += count
    finally:
        old.close()
    return result


def describe(result: dict, folder: Path) -> str:
    unread = result.get("tables_unreadable") or []
    text = (f"The database file was damaged (SQLite: disk image is malformed). It was moved to {folder}, a new database was created, and "
            f"{result.get('rows_copied', 0)} rows from {len(result.get('copied', {}))} tables were copied back.")
    if unread:
        text += f" Could not be read: {', '.join(unread[:8])}."
    return text
