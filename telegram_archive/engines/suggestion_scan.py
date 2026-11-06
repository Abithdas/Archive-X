"""Unified manual Metadata & Suggestion Scanner.

One job can combine any of these scopes; performer/studio scope lists are already expanded by the API, including the UI's All selection:
  * channel suggestion scan
  * performer video recheck
  * site video recheck

The scanner resolves the union of target media IDs once, deduplicates them in
SQL/in memory, and then calls the canonical metadata pipeline exactly once per
video. Entity recheck targets take precedence over channel-suggestion targets
when the same video is selected by more than one scope.
"""
from __future__ import annotations
from .. import config

import asyncio
import json

from ..jobs.context import JobCancelled
from ..metadata.fingerprint import oshash_from_source
from ..metadata.pipeline import identify_video
from ..core.telegram_range_transport import DirectTelegramRangeSource

MESSAGE_TIMEOUT_SECONDS = 30.0
FINGERPRINT_TIMEOUT_SECONDS = float(getattr(config, "FINGERPRINT_TIMEOUT_SECONDS", 30.0))
PROVIDER_OPERATION_TIMEOUT_SECONDS = 180.0

CONCURRENCY = int(getattr(config, "METADATA_ITEM_CONCURRENCY", 5))
MAX_LIMIT = 5000




# ==============================================================================
# SECTION: Helpers
# Fingerprints, activity, scopes, limits and targets.
# ==============================================================================

def _fingerprint_summary(stats: dict) -> str:
    attempted = int(stats.get("fingerprint_attempted", 0) or 0)
    okay = int(stats.get("fingerprint_ok", 0) or 0)
    fallback = int(stats.get("hash_failed", 0) or 0)
    return f"fingerprints {okay}/{attempted} ok · {fallback} fallback"


def _activity(ctx, message: str, *, operation: str, current_item: str, event: str, details=None, level="INFO") -> None:
    """Write a durable job-linked breadcrumb; telemetry must not break the scan."""
    emit = getattr(ctx, "activity", None)
    if callable(emit):
        emit(message, operation=operation, current_item=current_item, event=event,
             details=dict(details or {}), level=level, progress=True)
    else:
        ctx.report(current_item=current_item, operation=operation)


def _payload(detail: str) -> dict:
    try:
        raw = json.loads(detail or "{}")
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _scopes(payload: dict) -> list[str]:
    scopes = payload.get("scopes")
    if isinstance(scopes, list):
        out = []
        for value in scopes:
            text = str(value or "").strip().lower()
            if text in {"channel", "performer", "site"} and text not in out:
                out.append(text)
        if out:
            return out
    # Backwards compatibility with v18.44.34 single-mode jobs.
    mode = str(payload.get("mode") or "channel").strip().lower()
    return {"performer_recheck": ["performer"], "site_recheck": ["site"]}.get(mode, ["channel"])


def _limits(payload: dict) -> tuple[int, bool, bool]:
    try:
        limit = max(0, min(MAX_LIMIT, int(payload.get("limit", 50))))
    except Exception:
        limit = 50
    force = bool(payload.get("force_recheck", False))
    unbounded = bool(payload.get("unbounded", False))
    if not unbounded and limit == 0:
        limit = 50
    return limit, force, unbounded


def _selected_entity_ids(ctx, payload: dict, key: str, table: str) -> set[int]:
    values = payload.get(key) or []
    if not isinstance(values, list):
        values = [values]
    normalized = {str(value or "").strip().casefold() for value in values}
    if "all" in normalized:
        with ctx.db.lock:
            rows = ctx.db.conn.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
        return {int(row[0]) for row in rows}
    out = set()
    for value in values:
        try:
            ivalue = int(value)
            if ivalue > 0:
                out.add(ivalue)
        except Exception:
            continue
    return out


def _channel_ids(payload: dict) -> list[str]:
    values = payload.get("channel_ids")
    if values is None:
        values = [payload.get("channel_id") or payload.get("source_channel")]
    if not isinstance(values, list):
        values = [values]
    out = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _target_map(ctx, payload: dict, scopes: list[str], limit: int):
    """Return {media_id: policy}; entity scope wins over channel scope."""
    channel_ids = _channel_ids(payload) if "channel" in scopes else []
    media_ids = []
    for value in payload.get("media_ids") or []:
        try:
            media_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    media_ids = list(dict.fromkeys(media_ids))
    performer_ids = _selected_entity_ids(ctx, payload, "performer_ids", "performers") if "performer" in scopes else set()
    site_ids = _selected_entity_ids(ctx, payload, "site_ids", "studios") if "site" in scopes else set()

    if "performer" in scopes and not performer_ids:
        raise RuntimeError("Select at least one performer")
    if "site" in scopes and not site_ids:
        raise RuntimeError("Select at least one site")
    if "channel" in scopes and not channel_ids:
        raise RuntimeError("Select at least one source channel")

    clauses = []
    params = []
    if channel_ids:
        marks = ",".join("?" for _ in channel_ids)
        clauses.append(f"m.source_channel IN ({marks})")
        params.extend(channel_ids)
    if performer_ids:
        marks = ",".join("?" for _ in performer_ids)
        clauses.append(
            f"EXISTS (SELECT 1 FROM video_performers vp WHERE vp.video_id=m.id AND vp.performer_id IN ({marks})) "
            f"OR EXISTS (SELECT 1 FROM local_video_performers lvp WHERE lvp.video_id=m.id AND lvp.performer_id IN ({marks}))"
        )
        # Same placeholders occur in two EXISTS expressions.
        params.extend(sorted(performer_ids))
        params.extend(sorted(performer_ids))
    if site_ids:
        marks = ",".join("?" for _ in site_ids)
        clauses.append(f"EXISTS (SELECT 1 FROM video_studios vs WHERE vs.video_id=m.id AND vs.studio_id IN ({marks}))")
        params.extend(sorted(site_ids))

    if not clauses:
        return {}

    filters = ["(" + " OR ".join(f"({c})" for c in clauses) + ")"]
    if media_ids:
        marks = ",".join("?" for _ in media_ids)
        filters.append(f"m.id IN ({marks})")
        params.extend(media_ids)
    sql = (
        "SELECT m.id,m.source_channel,m.source_message_id,m.filename,m.oshash "
        "FROM media m WHERE " + " AND ".join(filters) + " ORDER BY m.id"
    )
    with ctx.db.lock:
        rows = ctx.db.conn.execute(sql, params).fetchall()

    # Entity rechecks override channel suggestion policy. A video selected by
    # performer/site is processed as a forced metadata recheck; channel-only
    # selections remain suggestion-only, preserving the original scan behavior.
    entity_selected = set()
    if performer_ids:
        with ctx.db.lock:
            marks = ",".join("?" for _ in performer_ids)
            q = (
                f"SELECT DISTINCT video_id FROM video_performers WHERE performer_id IN ({marks}) "
                f"UNION SELECT DISTINCT video_id FROM local_video_performers WHERE performer_id IN ({marks})"
            )
            entity_selected.update(int(r[0]) for r in ctx.db.conn.execute(q, list(sorted(performer_ids)) * 2).fetchall())
    if site_ids:
        marks = ",".join("?" for _ in site_ids)
        with ctx.db.lock:
            entity_selected.update(int(r[0]) for r in ctx.db.conn.execute(
                f"SELECT DISTINCT video_id FROM video_studios WHERE studio_id IN ({marks})",
                list(sorted(site_ids)),
            ).fetchall())

    target = {}
    for row in rows:
        media_id = int(row["id"])
        target[media_id] = {
            "row": row,
            "force": media_id in entity_selected or bool(payload.get("force_recheck", False)),
            "suggest_only": media_id not in entity_selected,
        }

    # Limit is applied after dedupe so one video cannot consume multiple slots.
    if limit > 0:
        keep_ids = sorted(target)[:limit]
        target = {mid: target[mid] for mid in keep_ids}
    return target


async def _prefetch_source_messages(ctx, client, batch) -> dict[tuple[int, int], object]:
    """Resolve existing source messages in bounded per-channel ID batches."""
    grouped: dict[int, list[int]] = {}
    for item in batch:
        row = item["row"]
        try:
            grouped.setdefault(int(row["source_channel"]), []).append(int(row["source_message_id"]))
        except Exception:
            continue
    found: dict[tuple[int, int], object] = {}
    for channel_id, ids in grouped.items():
        ids = list(dict.fromkeys(ids))
        for start in range(0, len(ids), 100):
            chunk = ids[start:start + 100]
            try:
                result = await ctx.cancellable(
                    lambda channel_id=channel_id, chunk=chunk: client.get_messages(channel_id, ids=chunk),
                    timeout=max(MESSAGE_TIMEOUT_SECONDS, 60.0), activity=True,
                )
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                ctx.db.log(
                    f"Suggestion message batch lookup for channel {channel_id} failed; falling back to single lookups: "
                    f"{type(exc).__name__}: {exc}", "WARN",
                )
                continue
            messages = list(result) if isinstance(result, (list, tuple)) else ([result] if result else [])
            for msg in messages:
                mid = int(getattr(msg, "id", 0) or 0)
                if mid:
                    found[(channel_id, mid)] = msg
            # Telethon may omit deleted IDs from its result list; explicitly
            # remember those as missing so a successful batch is not followed
            # by one RPC per deleted message. Failed batches above still leave
            # the keys absent and use the compatibility fallback.
            for mid in chunk:
                found.setdefault((channel_id, mid), None)
    return found




# ==============================================================================
# SECTION: Job entry point
# Metadata and suggestion scan job.
# ==============================================================================

async def run(ctx):
    """Job entry: scan selected videos for metadata suggestions."""
    payload = _payload(ctx.job.get("detail") or "{}")
    scopes = _scopes(payload)
    limit, force_recheck, unbounded = _limits(payload)
    payload["force_recheck"] = force_recheck

    if not scopes:
        raise RuntimeError("Select at least one scan scope")
    if not (ctx.db.setting("stashdb_api_key") or ctx.db.setting("tpdb_api_token") or ctx.db.setting("tpdb_token")):
        raise RuntimeError("Configure StashDB API key or ThePornDB token first")

    target = _target_map(ctx, payload, scopes, limit)
    rows = [target[mid] for mid in sorted(target)]
    total = len(rows)
    scope_label = "+".join(scopes)
    if not total:
        ctx.report(completed=0, total=0, progress=100, operation="No videos require processing", force=True)
        ctx.db.log(f"Unified Metadata & Suggestion Scanner: {scope_label}: no videos selected")
        return

    stats = {"done": 0, "matched": 0, "suggested": 0, "skipped": 0, "no_match": 0, "hash_failed": 0, "fingerprint_ok": 0, "fingerprint_attempted": 0, "failed": 0}
    sem = asyncio.Semaphore(CONCURRENCY)
    client = ctx.telegram.client

    async def one(item, prefetched=None):
        """Identify one target and report progress."""
        row = item["row"]
        async with sem:
            await ctx.checkpoint()
            filename = str(row["filename"] or f"telegram_{row['source_message_id']}.mp4")
            policy = "entity recheck" if not item["suggest_only"] else "channel suggestion"
            ctx.report(completed=stats["done"], total=total, current_item=filename,
                       operation=f"Processing {policy} ({scope_label})", detail=_fingerprint_summary(stats))
            channel_id = int(row["source_channel"])
            message_id = int(row["source_message_id"])
            _activity(ctx, f"Suggestion/metadata item started: {filename}",
                      operation=f"Metadata suggestions · resolve message ({scope_label})", current_item=filename,
                      event="suggestion.item_started", details={"filename": filename, "media_id": int(row["id"]),
                      "channel_id": channel_id, "message_id": message_id, "policy": policy})
            try:
                _activity(ctx, f"Fetching Telegram media details for {filename}",
                          operation="Metadata suggestions · fetch Telegram message", current_item=filename,
                          event="suggestion.message_lookup_started", details={"channel_id": channel_id, "message_id": message_id})
                key = (channel_id, message_id)
                if prefetched is not None and key in prefetched:
                    message = prefetched[key]
                else:
                    message = await ctx.cancellable(lambda: client.get_messages(channel_id, ids=message_id),
                                                    timeout=MESSAGE_TIMEOUT_SECONDS, activity=True)
                if not message or not getattr(message, "file", None):
                    stats["failed"] += 1
                    _activity(ctx, f"Telegram message unavailable for {filename}",
                              operation="Metadata suggestions · source unavailable", current_item=filename,
                              event="suggestion.message_missing", level="WARN",
                              details={"channel_id": channel_id, "message_id": message_id})
                    return
                filename = getattr(message.file, "name", None) or filename
                _activity(ctx, f"Telegram message details resolved for {filename}",
                          operation="Metadata suggestions · source ready", current_item=filename,
                          event="suggestion.message_lookup_completed", details={"channel_id": channel_id, "message_id": message_id})

                oshash = str(row["oshash"] or "").strip() if "oshash" in row.keys() else None
                stats["fingerprint_attempted"] += 1
                if oshash:
                    stats["fingerprint_ok"] += 1
                if not oshash:
                    source = None
                    _activity(ctx, f"Fingerprint lookup started for {filename}",
                              operation="Metadata suggestions · calculate source fingerprint", current_item=filename,
                              event="suggestion.fingerprint_started", details={"channel_id": channel_id, "message_id": message_id})
                    try:
                        source = DirectTelegramRangeSource(client, message, asyncio.get_running_loop(), fingerprint_lane=True)
                        oshash = await ctx.cancellable(
                            lambda: asyncio.to_thread(oshash_from_source, source),
                            timeout=FINGERPRINT_TIMEOUT_SECONDS, activity=True,
                        )
                        stats["fingerprint_ok"] += 1
                        _activity(ctx, f"Source fingerprint ready for {filename}",
                                  operation="Metadata suggestions · fingerprint ready", current_item=filename,
                                  event="suggestion.fingerprint_completed", details={"has_oshash": bool(oshash),
                                  "channel_id": channel_id, "message_id": message_id})
                    except (JobCancelled, asyncio.CancelledError):
                        raise
                    except Exception as exc:
                        stats["hash_failed"] += 1
                        _activity(ctx, f"Fingerprint unavailable for {filename} ({type(exc).__name__}: {str(exc)[:240]}); continuing with filename evidence",
                                  operation="Metadata suggestions · filename fallback", current_item=filename,
                                  event="suggestion.fingerprint_failed", level="WARN",
                                  details={"error_type": type(exc).__name__, "error": str(exc)[:400],
                                           "channel_id": channel_id, "message_id": message_id})
                    finally:
                        if source is not None:
                            try:
                                source.close()
                            except Exception:
                                pass

                _activity(ctx, f"Scene matching started for {filename}",
                          operation="Metadata suggestions · identify scene", current_item=filename,
                          event="suggestion.scene_match_started", details={"channel_id": channel_id,
                          "message_id": message_id, "fingerprint_available": bool(oshash), "policy": policy})
                result = await ctx.cancellable(lambda: identify_video(
                    ctx.db,
                    channel_id=channel_id,
                    message_id=message_id,
                    filename=filename,
                    oshash=oshash,
                    force=item["force"],
                    suggest_only=item["suggest_only"],
                ), timeout=PROVIDER_OPERATION_TIMEOUT_SECONDS, activity=True)
                action = result.get("action")
                _activity(ctx, f"Scene matching completed for {filename}: {action or 'no_match'}",
                          operation="Metadata suggestions · result", current_item=filename,
                          event="suggestion.scene_match_completed", details={"channel_id": channel_id,
                          "message_id": message_id, "action": action or "no_match",
                          "confidence": result.get("confidence"), "provider": result.get("provider")})
                if action == "skipped":
                    stats["skipped"] += 1
                elif action == "applied":
                    stats["matched"] += 1
                elif action == "suggested":
                    stats["suggested"] += 1
                else:
                    stats["no_match"] += 1
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                stats["failed"] += 1
                _activity(ctx, f"Metadata suggestion processing failed for {filename}: {type(exc).__name__}",
                          operation="Metadata suggestions · item failed", current_item=filename,
                          event="suggestion.item_failed", level="WARN", details={"filename": filename,
                          "channel_id": channel_id, "message_id": message_id, "error_type": type(exc).__name__,
                          "error": str(exc)[:500]})
                if stats["failed"] <= 5:
                    ctx.db.log(f"Unified Metadata & Suggestion Scanner: {filename}: {type(exc).__name__}: {exc}", "WARN")
            finally:
                _activity(ctx, f"Metadata suggestion item checkpoint: {filename}",
                          operation="Metadata suggestions · item checkpoint", current_item=filename,
                          event="suggestion.item_checkpoint", details={"filename": filename,
                          "channel_id": channel_id, "message_id": message_id, "processed_items": stats["done"] + 1})
                stats["done"] += 1
                ctx.report(completed=stats["done"], total=total, current_item=filename,
                           operation=f"Processing videos ({scope_label})")

    for start in range(0, total, 50):
        await ctx.checkpoint()
        batch = rows[start:start + 50]
        prefetched = await _prefetch_source_messages(ctx, client, batch)
        results = await asyncio.gather(*(one(item, prefetched) for item in batch), return_exceptions=True)
        for result in results:
            if isinstance(result, (JobCancelled, asyncio.CancelledError)):
                raise result

    ctx.report(completed=total, total=total, operation="Finished", detail=_fingerprint_summary(stats), force=True)
    extra = []
    if stats["suggested"]:
        extra.append(f"{stats['suggested']} suggestions")
    if stats["skipped"]:
        extra.append(f"{stats['skipped']} skipped")
    if stats["hash_failed"]:
        extra.append(f"{stats['hash_failed']} filename-only fallbacks")
    if stats["failed"]:
        extra.append(f"{stats['failed']} failed")
    ctx.db.log(
        f"Unified Metadata & Suggestion Scanner: {total} checked, {stats['matched']} matched, {stats['no_match']} no match; "
        f"{_fingerprint_summary(stats)}"
        + ("; " + "; ".join(extra) if extra else ""),
        "SUCCESS" if not stats["failed"] else "WARN",
    )
