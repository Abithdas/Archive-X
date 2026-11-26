"""Keeping the database file from growing and fragmenting: the space of deleted data is given back, the query statistics stay current.

A database file never shrinks by itself: deleting a channel, or rebuilding previews, only marks pages free inside it, and with `auto_vacuum` off (the
SQLite default) they are never returned. Measured on a copy of a 175,000-video library after half the previews were deleted: the file stayed 837 MB
when the content was 520 MB. What this module does:

- A new database is made with `auto_vacuum=INCREMENTAL`; an existing one is switched once by a `VACUUM` (`convert`, `compact`). A full `VACUUM` locks
  writing for seconds and needs spare disk space about the size of the file, so the start does it by itself only for a small database; for a larger
  one Settings offers "Compact database".
- `give_back_space` returns free pages in slices of 500 (each slice holds the writer for under 100 ms), a few seconds of them at most per run.
- `optimize` refreshes the query planner's statistics (`PRAGMA optimize`), which also makes queries choose better plans.
- `run_once` does those when the application is idle, and `start_background` repeats it every few hours.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

INCREMENTAL = 2
AUTO_CONVERT_BELOW_BYTES = 256 * 1024 * 1024    # a VACUUM of this much takes about two seconds: done at the start without asking
SLICE_PAGES = 500
MIN_FREE_PAGES_TO_RELEASE = 2000                # about 8 MB: below this, leaving the free pages for reuse is better than a pass
OPTIMIZE_EVERY_SECONDS = 7 * 24 * 3600
WAL_TRUNCATE_ABOVE_BYTES = 128 * 1024 * 1024
RUN_EVERY_SECONDS = 6 * 3600
FIRST_RUN_AFTER_SECONDS = 120

_RUN_LOCK = threading.Lock()                    # one maintenance pass or compaction at a time


class MaintenanceRefused(Exception):
    """Why a compaction was not started (text meant for the person)."""


def _pragma(conn, name: str) -> int:
    return int(conn.execute(f"PRAGMA {name}").fetchone()[0])


def make_new_database_incremental(conn) -> bool:
    """For a database file with no tables yet: ask for incremental vacuum before the first table exists (afterwards only `VACUUM` can change it)."""
    if conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0] != 0:
        return False
    conn.execute(f"PRAGMA auto_vacuum={INCREMENTAL}")
    return True


def _paths(db) -> tuple[Path, Path]:
    main = Path(db.path)
    return main, Path(str(main) + "-wal")


def status(db) -> dict:
    """What Settings shows: the size, the free space inside the file, whether it can be given back, and whether a compaction can run now."""
    main, wal = _paths(db)
    with db.lock:
        conn = db.conn
        page_size, pages, free, mode = (_pragma(conn, n) for n in ("page_size", "page_count", "freelist_count", "auto_vacuum"))
    file_bytes = pages * page_size
    try:
        disk_free = shutil.disk_usage(main.parent).free
    except OSError:
        disk_free = 0
    needs = int(file_bytes * 1.2) + 64 * 1024 * 1024
    return {
        "file_bytes": file_bytes,
        "free_bytes": free * page_size,
        "free_percent": round(free * 100 / pages, 1) if pages else 0.0,
        "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        "incremental": mode == INCREMENTAL,
        "auto_vacuum": mode,
        "disk_free_bytes": disk_free,
        "compact_needs_bytes": needs,
        "can_compact": disk_free >= needs,
        "last_compact": str(db.setting("db_last_compact", "") or ""),
        "last_optimize": str(db.setting("db_last_optimize", "") or ""),
        # a full compaction also removes the space wasted inside half-empty pages, which giving pages back cannot
        "compact_recommended": (not mode == INCREMENTAL and file_bytes > 64 * 1024 * 1024) or (free * 100 / pages > 15 if pages else False),
    }


def _blocking_jobs(db) -> list:
    try:
        return [j for j in db.active_jobs() if str(j.get("status") or "").upper() in {"QUEUED", "RUNNING", "PAUSING", "CANCEL_REQUESTED"}]
    except Exception:
        return []


def compact(db, *, now_iso: str = "") -> dict:
    """A full `VACUUM` (it also switches the file to incremental vacuum). Writing waits while it runs; refused when there is not room for it or a job is running."""
    st = status(db)
    if not st["can_compact"]:
        raise MaintenanceRefused(
            f"There is not enough free disk space to compact the database: it needs about {st['compact_needs_bytes'] // (1024 * 1024)} MB free "
            f"and {st['disk_free_bytes'] // (1024 * 1024)} MB is free.")
    if _blocking_jobs(db):
        raise MaintenanceRefused("A job is running. Wait for it to finish, or pause it, then compact the database.")
    if not _RUN_LOCK.acquire(blocking=False):
        raise MaintenanceRefused("The database is already being maintained. Try again in a moment.")
    try:
        started = time.monotonic()
        with db.lock:
            conn = db.conn
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute(f"PRAGMA auto_vacuum={INCREMENTAL}")
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.set_setting("db_last_compact", now_iso or time.strftime("%Y-%m-%dT%H:%M:%S"))
        after = status(db)
        return {"ok": True, "seconds": round(time.monotonic() - started, 1), "before_bytes": st["file_bytes"], "after_bytes": after["file_bytes"],
                "saved_bytes": max(0, st["file_bytes"] - after["file_bytes"]), "incremental": after["incremental"]}
    finally:
        _RUN_LOCK.release()


def convert_small_database(db) -> bool:
    """At the start: switch a small existing database to incremental vacuum with one quick VACUUM. A large one is left for the Settings button
    (it would hold up the start for as long as the VACUUM takes). Returns whether it converted."""
    try:
        st = status(db)
        if st["incremental"] or st["file_bytes"] > AUTO_CONVERT_BELOW_BYTES or not st["can_compact"] or st["file_bytes"] == 0:
            return False
        compact(db)
        return True
    except MaintenanceRefused:
        return False


def give_back_space(db, budget_seconds: float = 4.0) -> dict:
    """Return free pages to the file system in small slices, for at most `budget_seconds`. Does nothing unless the file uses incremental vacuum."""
    released = 0
    started = time.monotonic()
    with db.lock:
        conn = db.conn
        page_size, mode, free = _pragma(conn, "page_size"), _pragma(conn, "auto_vacuum"), _pragma(conn, "freelist_count")
    if mode != INCREMENTAL or free < MIN_FREE_PAGES_TO_RELEASE:
        return {"released_bytes": 0, "remaining_free_bytes": free * page_size}
    while time.monotonic() - started < budget_seconds:
        with db.lock:
            before = _pragma(db.conn, "freelist_count")
            if before <= 0:
                break
            db.conn.execute(f"PRAGMA incremental_vacuum({SLICE_PAGES})").fetchall()   # the rows must be read for the pages to be released
            db.conn.commit()
            released += before - _pragma(db.conn, "freelist_count")
        time.sleep(0.02)                                                              # the application's own writes get their turn between slices
    with db.lock:
        db.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        remaining = _pragma(db.conn, "freelist_count")
    return {"released_bytes": released * page_size, "remaining_free_bytes": remaining * page_size}


def optimize(db) -> None:
    """Refresh the query planner's statistics where tables have changed a lot (cheap: it looks at a few hundred rows of an index)."""
    with db.lock:
        db.conn.execute("PRAGMA analysis_limit=400")
        db.conn.execute("PRAGMA optimize")
        db.conn.commit()


def run_once(db, *, now: float | None = None) -> dict:
    """One quiet pass: give space back, trim a large write-ahead log, refresh statistics once a week. Skipped while a job is running."""
    if _blocking_jobs(db) or not _RUN_LOCK.acquire(blocking=False):
        return {"skipped": True}
    try:
        out: dict = {"skipped": False}
        out.update(give_back_space(db))
        main, wal = _paths(db)
        if wal.exists() and wal.stat().st_size > WAL_TRUNCATE_ABOVE_BYTES:
            with db.lock:
                db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            out["wal_trimmed"] = True
        stamp = now if now is not None else time.time()
        try:
            last = float(db.setting("db_last_optimize_at", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if stamp - last >= OPTIMIZE_EVERY_SECONDS:
            optimize(db)
            db.set_setting("db_last_optimize_at", stamp)
            db.set_setting("db_last_optimize", time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stamp)))
            out["optimized"] = True
        return out
    finally:
        _RUN_LOCK.release()


def start_background(db, stop: threading.Event | None = None) -> threading.Thread:
    """A daemon thread: the first pass two minutes after the start, then every six hours."""
    stop = stop or threading.Event()

    def loop():
        delay = FIRST_RUN_AFTER_SECONDS
        while not stop.wait(delay):
            try:
                result = run_once(db)
                if result.get("released_bytes") or result.get("optimized") or result.get("wal_trimmed"):
                    db.log(f"Database maintenance: {result}", "INFO", event="database.maintenance")
            except Exception as exc:                      # maintenance must never take anything else down
                try:
                    db.log(f"Database maintenance failed: {type(exc).__name__}: {exc}", "WARN", event="database.maintenance_failed")
                except Exception:
                    pass
            delay = RUN_EVERY_SECONDS

    thread = threading.Thread(target=loop, name="db-maintenance", daemon=True)
    thread.start()
    return thread
