"""Fetch the pictures of performers and studios that have none.

Pictures used to arrive only when somebody ran "Update performer details" (a manual job) or "Find people" (whose first step downloads
performer pictures). A performer or studio added by a scan simply showed a placeholder until then. This looks for the ones without a stored
picture, a few at a time, in the background, and the Performers and Studios pages can start it on request.

Each try is remembered (`entity_image_attempts`) with its outcome, so a performer a provider has no picture for is not asked about again
every cycle and cannot crowd out the others: it is retried after a week. A try that failed for a passing reason (a network error, a download
that did not complete, a provider that was busy) is retried after a few hours, because the same wait made the Fetch button look broken:
a failed first try blocked the next ones for a week.

Nothing here needs a button. `auto_repair` runs when a metadata scan finishes its performer and studio phases and when the Performers or
Studios page is opened; it also fills in blank performer genders. Every pass writes one line to the log with the outcome counts.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

RETRY_AFTER_SECONDS = 7 * 86400
# Outcomes that say "try again soon": the picture may well exist, the attempt just did not get that far.
TRANSIENT_RETRY_SECONDS = 6 * 3600
TRANSIENT_OUTCOMES = {"error", "download_failed", "provider_error", "cache_write_rejected", "timeout"}
PER_KIND_PER_CYCLE = 40
AUTO_REPAIR_COOLDOWN_SECONDS = 600
_last_auto_repair: dict[str, float] = {}
_running = asyncio.Lock()


def _ensure(db) -> None:
    with db.lock:
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS entity_image_attempts (kind TEXT NOT NULL, entity_id INTEGER NOT NULL, last_attempt REAL NOT NULL, "
            "found INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT '', PRIMARY KEY(kind, entity_id))"
        )
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(entity_image_attempts)").fetchall()}
        if "outcome" not in cols:
            db.conn.execute("ALTER TABLE entity_image_attempts ADD COLUMN outcome TEXT NOT NULL DEFAULT ''")
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
        now = time.time()
        marks = ",".join("?" for _ in TRANSIENT_OUTCOMES)
        recent = {int(r[0]) for r in rdb.execute(
            f"SELECT entity_id FROM entity_image_attempts WHERE kind=? AND ((outcome IN ({marks}) AND last_attempt>?) OR (outcome NOT IN ({marks}) AND last_attempt>?))",
            (kind, *sorted(TRANSIENT_OUTCOMES), now - TRANSIENT_RETRY_SECONDS, *sorted(TRANSIENT_OUTCOMES), now - RETRY_AFTER_SECONDS),
        ).fetchall()}
    return [i for i in ids if i not in recent]


def counts(db) -> dict[str, int]:
    return {"performers": len(missing_ids(db, "performer")), "studios": len(missing_ids(db, "studio"))}


def _note(db, kind: str, entity_id: int, found: bool, outcome: str = "") -> None:
    with db.lock:
        db.conn.execute(
            "INSERT OR REPLACE INTO entity_image_attempts(kind,entity_id,last_attempt,found,outcome) VALUES(?,?,?,?,?)",
            (kind, int(entity_id), time.time(), 1 if found else 0, str(outcome or "")[:60]),
        )
        db.conn.commit()


async def fetch_missing(db, *, limit: int = PER_KIND_PER_CYCLE, kinds: tuple[str, ...] = ("performer", "studio"), pause: float = 0.5) -> dict[str, Any]:
    """Try to get a picture for up to `limit` performers and `limit` studios that have none and were not tried this week."""
    from .performer_image_sync import refresh_performer_image
    from .studio_image_sync import refresh_studio_image
    out: dict[str, Any] = {"tried": 0, "found": 0, "not_found": 0, "outcomes": {}}
    for kind in kinds:
        refresh = refresh_performer_image if kind == "performer" else refresh_studio_image
        for entity_id in (await asyncio.to_thread(missing_ids, db, kind, include_recent_failures=False))[:max(0, int(limit))]:
            found = False
            outcome = "error"
            try:
                result = await refresh(db, entity_id, force_api=False)
                found = bool(result.get("ok"))
                outcome = str(result.get("outcome") or result.get("reason") or ("found" if found else "not_found"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                try:
                    db.log(f"Fetching the picture of {kind} {entity_id} failed: {type(exc).__name__}: {exc}", "WARN")
                except Exception:
                    pass
            await asyncio.to_thread(_note, db, kind, entity_id, found, outcome)
            out["outcomes"][outcome] = out["outcomes"].get(outcome, 0) + 1
            out["tried"] += 1
            out["found" if found else "not_found"] += 1
            await asyncio.sleep(pause)
    return out


async def run_once(db, **kwargs) -> dict[str, Any]:
    """One pass, never two at once."""
    if _running.locked():
        return {"skipped": "already running"}
    async with _running:
        result = await fetch_missing(db, **kwargs)
        if result.get("tried"):
            why = ", ".join(f"{n} {k}" for k, n in sorted(result["outcomes"].items(), key=lambda kv: -kv[1]))
            db.log(f"Fetched missing pictures: {result['found']} found, {result['not_found']} not available ({result['tried']} tried: {why})")
        return result


async def auto_repair(db, kind: str, *, reason: str, force: bool = False, limit: int = PER_KIND_PER_CYCLE) -> dict[str, Any]:
    """Fill in what can be filled in for performers (kind "performers") or studios, without anyone pressing a button.

    Performers: blank genders from provider data already stored, then the missing pictures. Studios: the missing pictures.
    Called when a metadata scan finishes its performer or studio phase, and when the Performers or Studios page is opened (at most once
    every ten minutes per kind, so opening the page repeatedly does not hammer the providers). Never raises.
    """
    entity = "performer" if kind == "performers" else "studio"
    now = time.monotonic()
    if not force and now - _last_auto_repair.get(entity, -1e9) < AUTO_REPAIR_COOLDOWN_SECONDS:
        return {"skipped": "cooldown"}
    _last_auto_repair[entity] = now
    out: dict[str, Any] = {"reason": reason}
    try:
        if entity == "performer":
            from ..metadata.gender_repair import restore_missing_genders
            g = await asyncio.to_thread(restore_missing_genders, db)
            out["gender"] = g
            if g.get("restored") or g.get("checked"):
                db.log(f"Performer genders ({reason}): {g['restored']} restored from stored provider data, {g['still_blank']} still blank")
        out["pictures"] = await run_once(db, limit=limit, kinds=(entity,))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        try:
            db.log(f"Automatic repair of {kind} ({reason}) failed: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            pass
    return out


def is_running() -> bool:
    return _running.locked()


async def refresh_loop(db, first_delay: float = 180.0, every_hours: float = 6.0) -> None:
    """Background task: a short while after start, then every few hours."""
    await asyncio.sleep(first_delay)
    while True:
        try:
            if str(db.setting("auto_entity_images", "1")).strip() not in {"0", "false", "off"}:
                await run_once(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                db.log(f"Fetching missing pictures failed: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                pass
        await asyncio.sleep(max(0.25, float(every_hours)) * 3600)
