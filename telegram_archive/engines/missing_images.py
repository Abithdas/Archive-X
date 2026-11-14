"""Fetch the pictures of performers and studios that have none.

Pictures used to arrive only when somebody ran "Update performer details" (a manual job) or "Find people" (whose first step downloads
performer pictures). A performer or studio added by a scan simply showed a placeholder until then. This looks for the ones without a stored
picture, a few at a time, in the background, and the Performers and Studios pages can start it on request.

Each try is remembered (`entity_image_attempts`), so a performer a provider has no picture for is not asked about again every cycle and
cannot crowd out the others: it is retried after a week.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

RETRY_AFTER_SECONDS = 7 * 86400
PER_KIND_PER_CYCLE = 40
_running = asyncio.Lock()


def _ensure(db) -> None:
    with db.lock:
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS entity_image_attempts (kind TEXT NOT NULL, entity_id INTEGER NOT NULL, last_attempt REAL NOT NULL, "
            "found INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(kind, entity_id))"
        )
        db.conn.commit()


_MISSING = {
    "performer": (
        "SELECT p.id FROM performers p WHERE lower(trim(p.name))<>'unknown performer' AND COALESCE(p.image_locked,0)=0 "
        "AND EXISTS (SELECT 1 FROM video_performers vp WHERE vp.performer_id=p.id) "
        "AND NOT EXISTS (SELECT 1 FROM performer_image_cache c WHERE c.performer_id=p.id AND c.data IS NOT NULL AND length(c.data)>0)"
    ),
    "studio": (
        "SELECT s.id FROM studios s WHERE lower(trim(s.name))<>'unknown studio' AND COALESCE(s.image_locked,0)=0 "
        "AND EXISTS (SELECT 1 FROM video_studios vs WHERE vs.studio_id=s.id) "
        "AND NOT EXISTS (SELECT 1 FROM studio_image_cache c WHERE c.studio_id=s.id AND c.data IS NOT NULL AND length(c.data)>0)"
    ),
}


def missing_ids(db, kind: str, *, include_recent_failures: bool = True) -> list[int]:
    _ensure(db)
    with db.reading() as rdb:
        ids = [int(r[0]) for r in rdb.execute(_MISSING[kind] + " ORDER BY 1").fetchall()]
        if include_recent_failures:
            return ids
        cutoff = time.time() - RETRY_AFTER_SECONDS
        recent = {int(r[0]) for r in rdb.execute("SELECT entity_id FROM entity_image_attempts WHERE kind=? AND last_attempt>?", (kind, cutoff)).fetchall()}
    return [i for i in ids if i not in recent]


def counts(db) -> dict[str, int]:
    return {"performers": len(missing_ids(db, "performer")), "studios": len(missing_ids(db, "studio"))}


def _note(db, kind: str, entity_id: int, found: bool) -> None:
    with db.lock:
        db.conn.execute("INSERT OR REPLACE INTO entity_image_attempts(kind,entity_id,last_attempt,found) VALUES(?,?,?,?)", (kind, int(entity_id), time.time(), 1 if found else 0))
        db.conn.commit()


async def fetch_missing(db, *, limit: int = PER_KIND_PER_CYCLE, kinds: tuple[str, ...] = ("performer", "studio"), pause: float = 0.5) -> dict[str, Any]:
    """Try to get a picture for up to `limit` performers and `limit` studios that have none and were not tried this week."""
    from .performer_image_sync import refresh_performer_image
    from .studio_image_sync import refresh_studio_image
    out: dict[str, Any] = {"tried": 0, "found": 0, "not_found": 0}
    for kind in kinds:
        refresh = refresh_performer_image if kind == "performer" else refresh_studio_image
        for entity_id in (await asyncio.to_thread(missing_ids, db, kind, include_recent_failures=False))[:max(0, int(limit))]:
            found = False
            try:
                result = await refresh(db, entity_id, force_api=False)
                found = bool(result.get("ok"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    db.log(f"Fetching the picture of {kind} {entity_id} failed: {type(exc).__name__}: {exc}", "WARN")
                except Exception:
                    pass
            await asyncio.to_thread(_note, db, kind, entity_id, found)
            out["tried"] += 1
            out["found" if found else "not_found"] += 1
            await asyncio.sleep(pause)
    return out


async def run_once(db, **kwargs) -> dict[str, Any]:
    """One pass, never two at once."""
    if _running.locked():
        return {"skipped": "already running"}
    async with _running:
        return await fetch_missing(db, **kwargs)


def is_running() -> bool:
    return _running.locked()


async def refresh_loop(db, first_delay: float = 180.0, every_hours: float = 6.0) -> None:
    """Background task: a short while after start, then every few hours."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            if str(db.setting("auto_entity_images", "1")).strip() not in {"0", "false", "off"}:
                result = await run_once(db)
                if result.get("tried"):
                    db.log(f"Fetched missing pictures: {result['found']} found, {result['not_found']} not available ({result['tried']} tried)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                db.log(f"Fetching missing pictures failed: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                pass
        await asyncio.sleep(max(0.25, float(every_hours)) * 3600)
