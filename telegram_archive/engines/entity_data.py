"""Unified manual performer/studio entity data synchronization.

One operator-facing job handles performer and studio provider-data refreshes,
including structured metadata and artwork. The existing entity-specific image
refresh functions remain the canonical artwork/cache implementations; this
engine builds one target list, shares one bounded concurrency gate, and reports
one progress stream. The legacy module name is retained for compatibility.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Target selection
#   - Job helpers
#   - Job entry point
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json
from collections import Counter
from typing import Any

from .performer_image_sync import refresh_performer_image
from .studio_image_sync import refresh_studio_image
from ..metadata.providers import fetch_entity_metadata, store_entity_metadata, _merge_entity_local_fields
DEFAULT_BATCH_LIMIT = 50
MAX_BATCH_LIMIT = 5000
ENTITY_PROVIDER_TIMEOUT_SECONDS = 120.0
ENTITY_IMAGE_TIMEOUT_SECONDS = 120.0
ENTITY_REFRESH_FAILURE_COOLDOWN_MINUTES = 30


ARTWORK_SUCCESS_COOLDOWN_DAYS = 7
ARTWORK_NO_PROVIDER_COOLDOWN_HOURS = 24
ARTWORK_FAILURE_COOLDOWN_MINUTES = 30
ARTWORK_SUCCESS_OUTCOMES = {"cached", "already_current", "preserved_existing"}




# ==============================================================================
# SECTION: Target selection
# SQL for finding entities that are due a refresh.
# ==============================================================================

def _metadata_due_sql(entity_type: str, alias: str) -> str:
    """SQL clause for provider-metadata freshness, independent of artwork state."""
    return f"""(
        NOT EXISTS (
            SELECT 1 FROM entity_metadata em
            WHERE em.entity_type='{entity_type}' AND em.entity_id={alias}.id
              AND COALESCE(json_extract(em.normalized,'$.details'),'') <> ''
              AND julianday(em.updated_at) >= julianday('now','-7 days')
        )
        AND NOT EXISTS (
            SELECT 1 FROM entity_refresh_state er
            WHERE er.entity_type='{entity_type}' AND er.entity_id={alias}.id
              AND er.status='SUCCESS'
              AND julianday(er.last_success_at) >= julianday('now','-7 days')
        )
        AND NOT EXISTS (
            SELECT 1 FROM entity_refresh_state er
            WHERE er.entity_type='{entity_type}' AND er.entity_id={alias}.id
              AND er.status='FAILED'
              AND julianday(er.last_attempt_at) >= julianday('now','-{ENTITY_REFRESH_FAILURE_COOLDOWN_MINUTES} minutes')
        )
    )"""


def _artwork_due_sql(entity_type: str, alias: str) -> str:
    """SQL clause: repair missing cache or retry artwork separately from metadata."""
    if entity_type == "performer":
        cache_table, cache_key = "performer_image_cache", "performer_id"
    else:
        cache_table, cache_key = "studio_image_cache", "studio_id"
    cache_valid = (
        f"EXISTS (SELECT 1 FROM {cache_table} ic WHERE ic.{cache_key}={alias}.id "
        f"AND ic.url={alias}.image AND ic.data IS NOT NULL AND length(ic.data)>0)"
    )
    good_artwork_state = f"""EXISTS (
        SELECT 1 FROM entity_artwork_state eas
        WHERE eas.entity_type='{entity_type}' AND eas.entity_id={alias}.id
          AND eas.status IN ('CACHED','ALREADY_CURRENT','PRESERVED_EXISTING')
          AND julianday(eas.last_success_at) >= julianday('now','-{ARTWORK_SUCCESS_COOLDOWN_DAYS} days')
    )"""
    cooldown = f"""NOT EXISTS (
        SELECT 1 FROM entity_artwork_state eas
        WHERE eas.entity_type='{entity_type}' AND eas.entity_id={alias}.id
          AND (
            (eas.status='NO_PROVIDER_ARTWORK'
             AND COALESCE({alias}.image,'')=''
             AND julianday(eas.last_attempt_at) >= julianday('now','-{ARTWORK_NO_PROVIDER_COOLDOWN_HOURS} hours'))
            OR (eas.status='DOWNLOAD_FAILED'
             AND julianday(eas.last_attempt_at) >= julianday('now','-{ARTWORK_FAILURE_COOLDOWN_MINUTES} minutes'))
          )
    )"""
    # Manual artwork is user-owned. Do not schedule artwork-only retries for a
    # locked image; metadata refresh can still proceed when its own freshness
    # rules require it.
    return f"(COALESCE({alias}.image_locked,0)=0 AND (NOT ({cache_valid} AND {good_artwork_state})) AND {cooldown})"


def refreshable_entity_ids(db, entity_type: str, entity_ids, *, force_recheck: bool = False) -> list[int]:
    """Return linked entities needing metadata refresh OR artwork repair.

    Metadata freshness no longer masks absent or failed artwork. A negative
    artwork lookup has its own retry cooldown; a cache miss for an existing URL
    remains immediately repairable after the short download-failure cooldown.
    """
    kind = str(entity_type or "").strip().casefold()
    if kind not in {"performer", "studio"}:
        return []
    ids_set: set[int] = set()
    for value in entity_ids or []:
        try:
            entity_id = int(value)
        except (TypeError, ValueError):
            continue
        if entity_id > 0:
            ids_set.add(entity_id)
    ids = sorted(ids_set)
    if not ids or force_recheck or not hasattr(db, "conn") or not hasattr(db, "lock"):
        return ids

    table = "performers" if kind == "performer" else "studios"
    placeholders = ",".join("?" for _ in ids)
    due_clause = f"({_metadata_due_sql(kind, 'entity')} OR {_artwork_due_sql(kind, 'entity')})"
    with db.lock:
        rows = db.conn.execute(
            f"SELECT entity.id FROM {table} entity WHERE entity.id IN ({placeholders}) AND {due_clause} ORDER BY entity.id",
            tuple(ids),
        ).fetchall()
    return [int(row[0]) for row in rows]




# ==============================================================================
# SECTION: Job helpers
# Activity reporting and thread offload.
# ==============================================================================

def _activity(ctx, message: str, *, operation: str, current_item: str, event: str, details=None, level="INFO") -> None:
    """Write a durable job-linked progress event without coupling success to logging."""
    emit = getattr(ctx, "activity", None)
    if callable(emit):
        emit(message, operation=operation, current_item=current_item, event=event,
             details=dict(details or {}), level=level, progress=True)
    else:
        ctx.report(current_item=current_item, operation=operation)


def _offload(ctx, func, *args):
    """Run synchronous SQLite work on the job's bounded pipeline, never on the loop."""
    pipeline = getattr(ctx, "pipeline", None)
    if pipeline is not None:
        return pipeline.blocking(func, *args)
    return asyncio.to_thread(func, *args)




# ==============================================================================
# SECTION: Job entry point
# Entity data sync job.
# ==============================================================================

async def run(ctx):
    """Job entry: refresh metadata and artwork for the selected entities."""
    try:
        detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        detail = {}
    scope = str(detail.get("scope") or "all").strip().casefold()
    if scope not in {"all", "performers", "studios"}:
        scope = "all"
    try:
        limit = max(0, min(MAX_BATCH_LIMIT, int(detail.get("limit", DEFAULT_BATCH_LIMIT))))
    except Exception:
        limit = DEFAULT_BATCH_LIMIT
    force_recheck = bool(detail.get("force_recheck", False))
    unbounded = bool(detail.get("unbounded", False))
    # Normalize a legacy provider association on the protected fallback before
    # collecting targets for this refresh.
    from ..core.resource_registry import ensure_unknown_performer_local_profile
    await _offload(ctx, ensure_unknown_performer_local_profile, ctx.db)
    if unbounded:
        limit = 0

    raw_entity_ids = detail.get("entity_ids") or {}
    entity_ids = {"performer": set(), "studio": set()}
    if isinstance(raw_entity_ids, dict):
        for kind in ("performer", "studio"):
            vals = raw_entity_ids.get(kind) or []
            if not isinstance(vals, (list, tuple, set)):
                vals = [vals]
            for value in vals:
                try:
                    if int(value) > 0:
                        entity_ids[kind].add(int(value))
                except (TypeError, ValueError):
                    continue

    # A channel-pipeline video may target the entities linked to that one video.
    # Resolve those IDs once and then use the same explicit-ID path as Details-page sync.
    video_ids = set()
    for value in detail.get("video_ids") or detail.get("media_ids") or []:
        try:
            ivalue = int(value)
            if ivalue > 0:
                video_ids.add(ivalue)
        except (TypeError, ValueError):
            continue
    if video_ids:
        with ctx.db.lock:
            marks = ",".join("?" for _ in video_ids)
            entity_ids["performer"].update(int(r[0]) for r in ctx.db.conn.execute(
                f"SELECT DISTINCT performer_id FROM video_performers WHERE video_id IN ({marks})", tuple(sorted(video_ids))
            ).fetchall())
            entity_ids["studio"].update(int(r[0]) for r in ctx.db.conn.execute(
                f"SELECT DISTINCT studio_id FROM video_studios WHERE video_id IN ({marks})", tuple(sorted(video_ids))
            ).fetchall())

    # Explicit entity/video IDs target only those entities; they must never widen into a bulk scan.
    targeted = bool(entity_ids["performer"] or entity_ids["studio"])

    def _select_targets() -> list[tuple[str, int, str]]:
        targets: list[tuple[str, int, str]] = []
        with ctx.db.lock:
            if scope in {"all", "performers"} and (not targeted or entity_ids["performer"]):
                where = (
                    ("1=1" if entity_ids["performer"] else "COALESCE(p.image_locked,0)=0") + " AND " +
                    ("1=1" if entity_ids["performer"] else "EXISTS (SELECT 1 FROM video_performers vp WHERE vp.performer_id=p.id)")
                )
                where += " AND lower(trim(COALESCE(p.name,''))) <> 'unknown performer'"
                if entity_ids["performer"]:
                    placeholders = ",".join("?" for _ in entity_ids["performer"])
                    where += f" AND p.id IN ({placeholders})"
                if not force_recheck:
                    where += f" AND ({_metadata_due_sql('performer', 'p')} OR {_artwork_due_sql('performer', 'p')})"
                sql = f"SELECT p.id,p.name FROM performers p WHERE {where} ORDER BY p.id"
                params = tuple(sorted(entity_ids["performer"])) if entity_ids["performer"] else ()
                if limit and not entity_ids["performer"]:
                    params = (limit,)
                rows = ctx.db.conn.execute(sql + (" LIMIT ?" if limit and not entity_ids["performer"] else ""), params).fetchall()
                targets.extend(("performer", int(r["id"]), str(r["name"] or "")) for r in rows)

            if scope in {"all", "studios"} and (not targeted or entity_ids["studio"]):
                where = (
                    ("1=1" if entity_ids["studio"] else "COALESCE(s.image_locked,0)=0") + " AND " +
                    ("1=1" if entity_ids["studio"] else "EXISTS (SELECT 1 FROM video_studios vs WHERE vs.studio_id=s.id)")
                )
                if entity_ids["studio"]:
                    placeholders = ",".join("?" for _ in entity_ids["studio"])
                    where += f" AND s.id IN ({placeholders})"
                if not force_recheck:
                    where += f" AND ({_metadata_due_sql('studio', 's')} OR {_artwork_due_sql('studio', 's')})"
                sql = f"SELECT s.id,s.name FROM studios s WHERE {where} ORDER BY s.id"
                params = tuple(sorted(entity_ids["studio"])) if entity_ids["studio"] else ()
                if limit and not entity_ids["studio"]:
                    params = (limit,)
                rows = ctx.db.conn.execute(sql + (" LIMIT ?" if limit and not entity_ids["studio"] else ""), params).fetchall()
                targets.extend(("studio", int(r["id"]), str(r["name"] or "")) for r in rows)

        return targets

    targets = await _offload(ctx, _select_targets)
    total = len(targets)
    if not total:
        label = "performers and studios" if scope == "all" else ("performers" if scope == "performers" else "studios")
        ctx.report(completed=0, total=0, progress=100, operation=f"No {label} need entity data sync", force=True)
        return

    sem = asyncio.Semaphore(4)
    stats = {"updated": 0, "missing": 0, "failed": 0, "metadata_rows_stored": 0}
    artwork_stats: Counter[str] = Counter()
    completed = 0
    completed_lock = asyncio.Lock()

    def _record_entity_refresh(entity_type: str, entity_id: int, status: str, error: str = ""):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with ctx.db.lock:
            if status == "SUCCESS":
                ctx.db.conn.execute("INSERT INTO entity_refresh_state(entity_type,entity_id,last_success_at,last_attempt_at,status,error) VALUES(?,?,?,?,?,?) ON CONFLICT(entity_type,entity_id) DO UPDATE SET last_success_at=excluded.last_success_at,last_attempt_at=excluded.last_attempt_at,status=excluded.status,error=''", (entity_type, entity_id, now, now, status, ""))
            else:
                ctx.db.conn.execute("INSERT INTO entity_refresh_state(entity_type,entity_id,last_success_at,last_attempt_at,status,error) VALUES(?,?,NULL,?,?,?) ON CONFLICT(entity_type,entity_id) DO UPDATE SET last_attempt_at=excluded.last_attempt_at,status=excluded.status,error=excluded.error", (entity_type, entity_id, now, status, str(error)[:500]))
            ctx.db.conn.commit()

    def _record_artwork_refresh(entity_type: str, entity_id: int, outcome: str, source: str = "", image_url: str = "", error: str = ""):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        status = str(outcome or "unknown").strip().upper()[:48]
        success_outcomes = tuple(item.upper() for item in ARTWORK_SUCCESS_OUTCOMES)
        initial_success = now if status in success_outcomes else None
        with ctx.db.lock:
            ctx.db.conn.execute(
                "INSERT INTO entity_artwork_state(entity_type,entity_id,last_success_at,last_attempt_at,status,source,image_url,error) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(entity_type,entity_id) DO UPDATE SET "
                "last_success_at=CASE WHEN excluded.status IN ('CACHED','ALREADY_CURRENT','PRESERVED_EXISTING') "
                "THEN excluded.last_attempt_at ELSE entity_artwork_state.last_success_at END, "
                "last_attempt_at=excluded.last_attempt_at,status=excluded.status,source=excluded.source, "
                "image_url=excluded.image_url,error=excluded.error",
                (entity_type, int(entity_id), initial_success, now, status, str(source or "")[:120],
                 str(image_url or "")[:2000], str(error or "")[:500]),
            )
            ctx.db.conn.commit()

    async def one(entity_type: str, entity_id: int, name: str):
        """Refresh one performer or studio and report progress."""
        nonlocal completed
        from ..core.scan_schedule import wait_for_metadata_scan_window
        await wait_for_metadata_scan_window(ctx)
        await ctx.checkpoint()
        provider_refresh_success = False
        artwork_started = False
        artwork_recorded = False
        async with sem:
            _activity(ctx, f"Entity refresh started: {entity_type} · {name}",
                      operation="Entity sync · load provider identifiers", current_item=f"{entity_type.title()}: {name}",
                      event="entity.refresh_started", details={"entity_type": entity_type, "entity_id": int(entity_id), "name": name})
            try:
                table = "performers" if entity_type == "performer" else "studios"
                def _provider_ids():
                    with ctx.db.lock:
                        row = ctx.db.conn.execute(f"SELECT provider_ids FROM {table} WHERE id=?", (entity_id,)).fetchone()
                    return row["provider_ids"] if row else None

                def _store(rows):
                    stored_count = store_entity_metadata(ctx.db, entity_type, entity_id, rows)
                    _merge_entity_local_fields(ctx.db, entity_type, entity_id, rows)
                    return stored_count

                ctx.report(completed=completed, total=total, current_item=f"{entity_type.title()}: {name}",
                           operation="Checking provider metadata · StashDB / ThePornDB")
                provider_ids = await _offload(ctx, _provider_ids)
                _activity(ctx, f"Fetching provider metadata for {entity_type}: {name}",
                          operation="Entity sync · fetch StashDB / ThePornDB metadata", current_item=f"{entity_type.title()}: {name}",
                          event="entity.provider_metadata_started", details={"entity_type": entity_type, "entity_id": int(entity_id), "provider_ids_present": bool(provider_ids)})
                provider_rows = await ctx.cancellable(
                    lambda: fetch_entity_metadata(ctx.db, entity_type, name, provider_ids),
                    timeout=ENTITY_PROVIDER_TIMEOUT_SECONDS, activity=True,
                )
                provider_refresh_success = True
                stored = await _offload(ctx, _store, provider_rows) if provider_rows else 0
                _activity(ctx, f"Provider metadata processed for {entity_type}: {name} ({len(provider_rows or [])} row(s), {stored} stored)",
                          operation="Entity sync · provider metadata stored", current_item=f"{entity_type.title()}: {name}",
                          event="entity.provider_metadata_completed", details={"entity_type": entity_type, "entity_id": int(entity_id),
                          "provider_rows": len(provider_rows or []), "stored_rows": int(stored)})
                ctx.report(completed=completed, total=total, current_item=f"{entity_type.title()}: {name}",
                           operation="Caching artwork locally")
                _activity(ctx, f"Checking local/provider artwork for {entity_type}: {name}",
                          operation="Entity sync · cache artwork", current_item=f"{entity_type.title()}: {name}",
                          event="entity.artwork_started", details={"entity_type": entity_type, "entity_id": int(entity_id)})
                artwork_started = True
                if entity_type == "performer":
                    result = await ctx.cancellable(lambda: refresh_performer_image(ctx.db, entity_id, force_api=force_recheck, provider_rows=provider_rows), timeout=ENTITY_IMAGE_TIMEOUT_SECONDS)
                else:
                    result = await ctx.cancellable(lambda: refresh_studio_image(ctx.db, entity_id, force_api=force_recheck, provider_rows=provider_rows), timeout=ENTITY_IMAGE_TIMEOUT_SECONDS)
                stats["metadata_rows_stored"] += int(stored or 0)
                outcome = str(result.get("outcome") or "").strip().casefold()
                if not outcome:
                    reason = str(result.get("reason") or "").casefold()
                    if result.get("ok"):
                        outcome = "cached"
                    elif reason in {"no_provider_image", "no_provider_artwork"}:
                        outcome = "no_provider_artwork"
                    elif reason == "manual_image_locked":
                        outcome = "manual_image_locked"
                    elif reason == "protected_local_fallback":
                        outcome = "protected_local_fallback"
                    else:
                        outcome = "download_failed"
                artwork_stats[outcome] += 1
                await _offload(ctx, _record_artwork_refresh, entity_type, entity_id, outcome,
                               str(result.get("source") or ""), str(result.get("image") or ""),
                               str(result.get("error") or result.get("reason") or ""))
                artwork_recorded = True
                if stored or result.get("ok"):
                    stats["updated"] += 1
                if outcome == "no_provider_artwork":
                    stats["missing"] += 1
                elif outcome == "download_failed":
                    stats["failed"] += 1
                elif not result.get("ok") and outcome not in {"manual_image_locked", "protected_local_fallback", "entity_not_found"}:
                    stats["failed"] += 1
                _activity(ctx, f"Artwork result: {entity_type} · {name} · {outcome.replace('_', ' ')}",
                          operation="Entity sync · artwork result", current_item=f"{entity_type.title()}: {name}",
                          event="entity.artwork_result",
                          level="WARN" if outcome in {"no_provider_artwork", "download_failed"} else "INFO",
                          details={"entity_type": entity_type, "entity_id": int(entity_id),
                          "outcome": outcome, "stored_metadata_rows": int(stored),
                          "source": str(result.get("source") or "")[:120],
                          "cache_ok": outcome in ARTWORK_SUCCESS_OUTCOMES,
                          "preserved_existing": bool(result.get("preserved_existing")),
                          "reason": str(result.get("reason") or "")[:200],
                          "error": str(result.get("error") or "")[:300]})
                if provider_refresh_success:
                    await _offload(ctx, _record_entity_refresh, entity_type, entity_id, "SUCCESS", "")
            except Exception as exc:
                try:
                    await _offload(ctx, _record_entity_refresh, entity_type, entity_id, "FAILED", str(exc))
                except Exception:
                    pass
                if artwork_started and not artwork_recorded:
                    try:
                        await _offload(ctx, _record_artwork_refresh, entity_type, entity_id, "download_failed", "", "", f"{type(exc).__name__}: {exc}")
                        artwork_stats["download_failed"] += 1
                    except Exception:
                        pass
                stats["failed"] += 1
                _activity(ctx, f"Entity refresh failed: {entity_type} · {name} · {type(exc).__name__}",
                          operation="Entity sync · item failed", current_item=f"{entity_type.title()}: {name}",
                          event="entity.refresh_failed", level="WARN", details={"entity_type": entity_type,
                          "entity_id": int(entity_id), "error_type": type(exc).__name__, "error": str(exc)[:500]})
                ctx.db.log(
                    f"{entity_type.title()} entity data sync failed for {name}: {type(exc).__name__}: {exc}",
                    "WARN",
                )
            finally:
                async with completed_lock:
                    completed += 1
                    prefix = "Performer" if entity_type == "performer" else "Studio"
                    ctx.report(
                        completed=completed,
                        total=total,
                        current_item=f"{prefix}: {name}",
                        operation="Updating performer/studio entity data",
                    )

    # Keep task creation bounded for large libraries. At most 25 coroutine
    # objects exist at a time, while the semaphore limits provider work to 4.
    for start in range(0, len(targets), 25):
        batch = targets[start:start + 25]
        # ctx.cancellable keeps the job watchdog fed while provider requests
        # are in flight (only while the event loop is genuinely responsive)
        # and makes Stop interrupt the batch immediately.
        async def _run_batch(batch=batch):
            await asyncio.gather(*(one(*target) for target in batch))

        await ctx.cancellable(_run_batch, activity=True)
    outcome_order = ("cached", "already_current", "preserved_existing", "no_provider_artwork", "download_failed", "manual_image_locked", "manual_image_preserved", "protected_local_fallback", "entity_not_found")
    artwork_summary = ", ".join(f"{label.replace('_', ' ')}={artwork_stats.get(label, 0)}" for label in outcome_order)
    final_summary = (
        f"Entity sync finished · metadata rows stored={stats['metadata_rows_stored']} · "
        f"artwork: {artwork_summary} · failures={stats['failed']}"
    )
    _activity(ctx, final_summary, operation="Entity sync · artwork summary", current_item="Artwork summary",
              event="entity.artwork_summary", level="WARN" if stats["failed"] else "INFO",
              details={"scope": scope, "metadata_rows_stored": stats["metadata_rows_stored"],
                       "artwork_outcomes": dict(artwork_stats), "failed": stats["failed"],
                       "force_recheck": force_recheck})
    ctx.report(completed=total, total=total, progress=100, operation=final_summary, force=True)
    ctx.db.log(
        "Unified entity data sync: "
        f"updated={stats['updated']}, metadata_rows_stored={stats['metadata_rows_stored']}, "
        f"artwork_outcomes={dict(artwork_stats)}, missing_artwork={stats['missing']}, "
        f"failed={stats['failed']}, scope={scope}, force_recheck={force_recheck}",
        "SUCCESS" if stats["failed"] == 0 else "WARN",
    )
