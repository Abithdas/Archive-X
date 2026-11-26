"""Background metadata synchronizer using configured scene providers.

Builds the canonical filename reference database as it goes (see
metadata/reference.py) and, per video, defers entirely to the shared
metadata/pipeline.identify_video() -- the same function the background
page enricher uses -- so there is exactly one place this logic lives.
Already-MATCHED/VERIFIED videos are skipped outright unless force=True,
and provider matching uses OSHASH before filename clues, with local references as a fallback.
"""
from __future__ import annotations
from ..core.swallowed import note as _swallowed_note
from .. import config
from ..core import content_mode
import asyncio
import json
from ..metadata.fingerprint import oshash_from_source
from ..core.telegram_range_transport import DirectTelegramRangeSource
from ..jobs.context import JobCancelled

CONCURRENCY = int(getattr(config, "METADATA_ITEM_CONCURRENCY", 5))  # bounded metadata/fingerprint workers
MESSAGE_TIMEOUT_SECONDS = 30.0
FINGERPRINT_TIMEOUT_SECONDS = float(getattr(config, "FINGERPRINT_TIMEOUT_SECONDS", 30.0))
PROVIDER_OPERATION_TIMEOUT_SECONDS = 180.0


BATCH_SIZE = 50      # videos per batch; their Telegram messages are fetched together
BATCH_MESSAGE_TIMEOUT_SECONDS = 90.0
# After the main pass, videos that are still unidentified are asked about again while the previous pass identified more (see run()).
MAX_FOLLOW_UP_ROUNDS = 3




# ==============================================================================
# SECTION: Helpers
# Fingerprints, activity, prefetch, limits and sources.
# ==============================================================================

def _fingerprint_summary(stats: dict) -> str:
    attempted = int(stats.get("fingerprint_attempted", 0) or 0)
    okay = int(stats.get("fingerprint_ok", 0) or 0)
    fallback = int(stats.get("hash_failed", 0) or 0)
    return f"fingerprints {okay}/{attempted} ok · {fallback} fallback"


def _activity(ctx, message: str, *, operation: str, current_item: str, event: str, details=None, level="INFO") -> None:
    """Write a durable job-linked breadcrumb without making telemetry a failure point."""
    emit = getattr(ctx, "activity", None)
    if callable(emit):
        emit(message, operation=operation, current_item=current_item, event=event,
             details=dict(details or {}), level=level, progress=True)
    else:
        # Compatibility for older test doubles and legacy contexts.
        ctx.report(current_item=current_item, operation=operation)


async def _prefetch_messages(ctx, client, rows) -> dict:
    """Fetch a batch's Telegram messages with one request per channel (V29.4.130).

    Telegram returns up to 100 messages for one ``get_messages(ids=[...])``
    call, so a 50-video batch costs one request instead of 50 -- the same
    batched re-fetch telegram-archiver uses. Missing/deleted messages map to
    None. If a channel's batch lookup fails, no entries are recorded for that
    chunk and workers fall back to the old one-at-a-time lookup. Returned
    messages are matched by their actual message IDs, never by list position.
    """
    by_channel: dict[int, list[int]] = {}
    for row in rows:
        try:
            by_channel.setdefault(int(row["source_channel"]), []).append(int(row["source_message_id"]))
        except Exception:
            _swallowed_note("metadata_sync._prefetch_messages")
            continue
    found: dict[tuple[int, int], object] = {}
    for channel_id, ids in by_channel.items():
        ids = list(dict.fromkeys(ids))
        for pos in range(0, len(ids), 100):
            chunk = ids[pos:pos + 100]
            try:
                messages = await ctx.cancellable(
                    lambda channel_id=channel_id, chunk=chunk: client.get_messages(channel_id, ids=chunk),
                    timeout=BATCH_MESSAGE_TIMEOUT_SECONDS, activity=True,
                )
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                try:
                    ctx.db.log(f"Metadata match: batch message lookup for {channel_id} failed "
                               f"({type(exc).__name__}: {exc}); looking up one at a time", "WARN")
                except Exception:
                    _swallowed_note("metadata_sync._prefetch_messages")
                    pass
                continue
            if not isinstance(messages, (list, tuple)):
                messages = [messages] if messages else []
            by_id = {
                int(getattr(msg, "id", 0) or 0): msg
                for msg in messages if msg and int(getattr(msg, "id", 0) or 0) > 0
            }
            for mid in chunk:
                found[(channel_id, mid)] = by_id.get(int(mid))
    return found


def _limits(detail: str):
    limit = 50
    unbounded = False
    force = False
    repair_only = False
    media_ids = []
    operation = "Looking up metadata"
    log_prefix = "Metadata match"
    try:
        payload = json.loads(detail or "{}")
        # 0 means scan the entire selected channel. Positive values cap the scan.
        limit = max(0, min(5000, int(payload.get("limit", 50))))
        unbounded = bool(payload.get("unbounded", False))
        if not unbounded and limit == 0:
            limit = 50
        force = bool(payload.get("force", False))
        repair_only = bool(payload.get("repair_only", False))
        media_ids = [int(x) for x in (payload.get("media_ids") or []) if str(x).isdigit()]
        if str(payload.get("mode") or "") == "performer_recheck":
            operation = "Rechecking linked performer videos"
            log_prefix = "Performer video recheck"
        elif str(payload.get("mode") or "") == "site_recheck":
            operation = "Rechecking linked site videos"
            log_prefix = "Site video recheck"
    except Exception:
        _swallowed_note("metadata_sync._limits")
        pass
    return limit, force, repair_only, media_ids, operation, log_prefix, unbounded

def _sources(ctx) -> list:
    try:
        payload = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        payload = {}
    out = [str(x).strip() for x in (payload.get("source_channels") or []) if str(x).strip()]
    single = str(ctx.job.get("source_channel") or "").strip()
    if single and single not in out:
        out.append(single)
    return out




# ==============================================================================
# SECTION: Job entry point
# Background metadata sync job and result recording.
# ==============================================================================

async def run(ctx):
    """ONE job for all selected channels; progress = videos checked / total."""
    from ..metadata.pipeline import identify_video
    sources = _sources(ctx)
    limit, force, repair_only, media_ids, operation, log_prefix, unbounded = _limits(ctx.job.get("detail") or "{}")
    try:
        _detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        _detail = {}
    reconcile_filename_entities = bool(_detail.get("reconcile_filename_entities", True))
    visual_match = bool(_detail.get("visual_match", True))   # the optional pHash scan (off: filename and file-hash matching only)
    key_ready = bool(ctx.db.setting("stashdb_api_key") or ctx.db.setting("tpdb_api_token") or ctx.db.setting("tpdb_token"))

    # Already-VERIFIED/MATCHED videos are the whole point of the reference
    # database -- they're excluded from the candidate set up front (section
    # 12) rather than fetched and then skipped one at a time, so a library
    # that's mostly identified stays fast to re-sync.
    where = ["(identification_state IS NULL OR identification_state NOT IN ('MATCHED','VERIFIED'))"]
    if repair_only:
        where = ["identification_state='REPAIR'"]
    params = []
    if force:
        where = ["1=1"]
    else:
        where.append("(metadata_synced_at IS NULL OR metadata_synced_at < datetime('now','-30 day'))")
    ctx.report(operation=("Finding videos requiring metadata repair" if repair_only else ("Finding videos linked to performer" if log_prefix == "Performer video recheck" else ("Finding videos linked to site" if log_prefix == "Site video recheck" else "Finding videos without metadata"))), force=True)
    rows, seen = [], set()
    for source_id in (sources or [""]):
        w, p = list(where), list(params)
        w.append("COALESCE(status,'')<>'SOURCE_DELETED'")  # V29.4.128
        w.append(f"source_channel {content_mode.NOT_SFW_SOURCE}")  # StashDB and ThePornDB are never asked about SFW channels or folders
        if source_id:
            w.append("source_channel=?")
            p.append(source_id)
        if media_ids:
            marks=','.join('?' for _ in media_ids)
            w.append(f"id IN ({marks})")
            p.extend(media_ids)
        sql = f"SELECT id,source_channel,source_message_id,filename,oshash FROM media WHERE {' AND '.join(w)} ORDER BY id"
        if limit > 0:
            sql += " LIMIT ?"
            p.append(limit)
        with ctx.db.lock:
            for r in ctx.db.conn.execute(sql, p).fetchall():
                if r["id"] not in seen:          # a video is checked once per run
                    seen.add(r["id"])
                    rows.append(r)
    total = len(rows)
    if not total:
        if not key_ready:
            raise RuntimeError("Configure StashDB API key or ThePornDB token first")
        ctx.report(completed=0, total=0, progress=100, operation="No videos need metadata", force=True)
        ctx.db.log("Metadata match: no videos need metadata")
        return

    if not key_ready:
        # With no configured provider there is no external hash/filename lookup
        # to prioritize. Keep the deterministic local fallback available, then
        # report the missing provider configuration as before.
        if reconcile_filename_entities:
            from ..metadata.filename_entities import link_known_entities_from_filenames
            filename_links = await asyncio.to_thread(
                link_known_entities_from_filenames, ctx.db, [int(r["id"]) for r in rows]
            )
            if any(filename_links.get(k, 0) for k in ("performer_links_added", "studio_links_added", "unknown_links_replaced", "ambiguous_entities_skipped")):
                ctx.db.log(
                    "Filename entity fallback (no metadata providers configured): "
                    f"{filename_links['performer_links_added']} performer link(s), "
                    f"{filename_links['studio_links_added']} studio link(s), "
                    f"{filename_links['unknown_links_replaced']} Unknown placeholder(s) replaced, "
                    f"{filename_links['ambiguous_entities_skipped']} ambiguous alias(es) skipped",
                    "INFO" if not filename_links["ambiguous_entities_skipped"] else "WARN",
                    event="metadata.filename_entity_match", job_id=ctx.job_id,
                    details={"phase": "no_provider_fallback", **filename_links},
                )
        raise RuntimeError("Configure StashDB API key or ThePornDB token first")

    stats = {"done": 0, "matched": 0, "suggested": 0, "skipped": 0, "no_match": 0, "hash_failed": 0, "fingerprint_ok": 0, "fingerprint_attempted": 0, "failed": 0}
    filename_fallback_ids: list[int] = []
    # Videos left without an answer, with what is needed to ask again: id -> {channel, message, filename, oshash, last}. A video recognised
    # later in a pass can add the performer or studio an earlier one needed, and a provider can time out for one video and not the next.''
    unresolved: dict[int, dict] = {}
    sem = asyncio.Semaphore(CONCURRENCY)
    client = ctx.telegram.client

    async def fingerprint_of(row, message, filename, channel_id, message_id):
        """The OSHASH of this video's file: the stored one, else read from two small pieces of the Telegram file. Never raises except for a cancel."""
        oshash = str(row["oshash"] or "").strip() if "oshash" in row.keys() else None
        stats["fingerprint_attempted"] += 1
        if oshash:
            stats["fingerprint_ok"] += 1
        if not oshash:
            source = None
            _activity(ctx, f"Fingerprint lookup started for {filename}",
                      operation="Metadata matching · calculate source fingerprint", current_item=filename,
                      event="metadata.fingerprint_started", details={"filename": filename,
                      "channel_id": channel_id, "message_id": message_id})
            try:
                source = DirectTelegramRangeSource(client, message, asyncio.get_running_loop(), fingerprint_lane=True)
                oshash = await ctx.cancellable(
                    lambda: asyncio.to_thread(oshash_from_source, source),
                    timeout=FINGERPRINT_TIMEOUT_SECONDS, activity=True,
                )
                stats["fingerprint_ok"] += 1
                # Metadata matching can run far ahead of the snapshot
                # worker on a large channel. Pin only the two 64 KiB
                # OSHASH intervals to temporary scan storage so the
                # later MP4-index/snapshot phase can reuse them even if
                # the bounded RAM cache has moved on to other videos.
                try:
                    from ..core import scan_range_cache
                    fsize = int(getattr(getattr(message, "file", None), "size", 0) or 0)
                    cache_key = scan_range_cache.key_for_message(message, fsize)
                    span = min(64 * 1024, fsize)
                    if cache_key and span > 0:
                        await asyncio.to_thread(
                            scan_range_cache.pin_ranges, cache_key,
                            [(0, span), (max(0, fsize - span), span)],
                        )
                except Exception:
                    _swallowed_note("metadata_sync.run.one")
                    pass
                _activity(ctx, f"Source fingerprint ready for {filename}",
                          operation="Metadata matching · fingerprint ready", current_item=filename,
                          event="metadata.fingerprint_completed", details={"filename": filename,
                          "has_oshash": bool(oshash), "channel_id": channel_id, "message_id": message_id})
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                stats["hash_failed"] += 1     # filename lookup still runs
                _activity(ctx, f"Fingerprint unavailable for {filename} ({type(exc).__name__}: {str(exc)[:240]}); continuing with filename evidence",
                          operation="Metadata matching · filename fallback", current_item=filename,
                          event="metadata.fingerprint_failed", level="WARN",
                          details={"filename": filename, "error_type": type(exc).__name__,
                                   "error": str(exc)[:400], "channel_id": channel_id, "message_id": message_id})
            finally:
                if source is not None:
                    try:
                        source.close()
                    except Exception:
                        _swallowed_note("metadata_sync.run.one")
                        pass
        return oshash

    fingerprints: dict[int, str | None] = {}

    async def warm_fingerprints(batch, prefetched) -> None:
        """Read the fingerprints of a batch's files together, then ask StashDB about all of them in ONE request (instead of one request, 0.5 s
        apart, per video). The per-video lookup that follows finds the answer in the cache. Anything that goes wrong here just leaves the
        normal per-video path to do the work, as before."""
        hashes: list[str] = []

        async def read(row) -> None:
            key = (int(row["source_channel"]), int(row["source_message_id"]))
            message = (prefetched or {}).get(key)
            if not message or not getattr(message, "file", None):
                return
            stored = str(row["oshash"] or "").strip() if "oshash" in row.keys() else ""
            if stored:
                hashes.append(stored)
                return
            async with sem:
                await ctx.checkpoint()
                filename = getattr(message.file, "name", None) or row["filename"] or f"telegram_{key[1]}.mp4"
                try:
                    value = await fingerprint_of(row, message, filename, key[0], key[1])
                except (JobCancelled, asyncio.CancelledError):
                    raise
                except Exception:
                    return
            fingerprints[int(row["id"])] = value
            if value:
                hashes.append(value)

        ctx.report(completed=stats["done"], total=total, operation="Metadata matching · reading file fingerprints", force=True)
        await asyncio.gather(*(read(r) for r in batch))
        key = ctx.db.setting("stashdb_api_key")
        if key and hashes:
            from ..metadata.providers import stashdb_by_oshash_many
            try:
                await ctx.cancellable(lambda: stashdb_by_oshash_many(ctx.db, key, hashes), timeout=PROVIDER_OPERATION_TIMEOUT_SECONDS * 2, activity=True)
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                _swallowed_note("metadata_sync.warm_fingerprints")
                ctx.db.log(f"Metadata match: batched fingerprint lookup skipped ({type(exc).__name__}); looking up one video at a time", "INFO")

    async def one(row, prefetched=None):
        """Identify one source message and record the result."""
        channel_id = int(row["source_channel"])
        message_id = int(row["source_message_id"])
        async with sem:
            await ctx.checkpoint()
            ctx.report(completed=stats["done"], total=total, current_item=row["filename"],
                       operation=operation, detail=_fingerprint_summary(stats))
            _activity(ctx, f"Metadata item started: {row['filename']}",
                      operation="Metadata matching · resolve source message", current_item=row["filename"],
                      event="metadata.item_started", details={"filename": row["filename"],
                      "media_id": int(row["id"]), "channel_id": channel_id, "message_id": message_id})
            try:
                key = (channel_id, message_id)
                if prefetched is not None and key in prefetched:
                    message = prefetched[key]
                    _activity(ctx, f"Reused prefetched Telegram message details for {row['filename']}",
                              operation="Metadata matching · reuse message lookup", current_item=row["filename"],
                              event="metadata.message_reused", details={"channel_id": channel_id, "message_id": message_id})
                else:
                    _activity(ctx, f"Fetching Telegram message metadata for {row['filename']}",
                              operation="Metadata matching · fetch Telegram message", current_item=row["filename"],
                              event="metadata.message_lookup_started", details={"channel_id": channel_id, "message_id": message_id})
                    message = await ctx.cancellable(lambda: client.get_messages(channel_id, ids=message_id), timeout=MESSAGE_TIMEOUT_SECONDS, activity=True)
                if message:
                    _activity(ctx, f"Telegram message details resolved for {row['filename']}",
                              operation="Metadata matching · source message ready", current_item=row["filename"],
                              event="metadata.message_lookup_completed", details={"channel_id": channel_id, "message_id": message_id,
                              "has_file": bool(getattr(message, "file", None))})
                if not message or not getattr(message, "file", None):
                    stats["failed"] += 1
                    return
                filename = getattr(message.file, "name", None) or row["filename"] or f"telegram_{message_id}.mp4"

                # Hash the original Telegram file before provider matching even
                # when a local filename reference exists. If no provider has an
                # exact hash result, identify_video continues with filename clues
                # and then falls back to the saved local reference.
                if int(row["id"]) in fingerprints:
                    oshash = fingerprints.pop(int(row["id"]))        # already read together with the rest of its batch
                else:
                    oshash = await fingerprint_of(row, message, filename, channel_id, message_id)

                _activity(ctx, f"Identifying scene metadata for {filename}",
                          operation="Metadata matching · match title and entities", current_item=filename,
                          event="metadata.scene_match_started", details={"filename": filename,
                          "channel_id": channel_id, "message_id": message_id, "fingerprint_available": bool(oshash)})
                result = await ctx.cancellable(lambda: identify_video(
                    ctx.db, channel_id=channel_id, message_id=message_id,
                    filename=filename, oshash=oshash, force=force,
                    reconcile_filename_entities=False, visual_scan=visual_match,
                ), timeout=PROVIDER_OPERATION_TIMEOUT_SECONDS, activity=True)
                action = result.get("action")
                _activity(ctx, f"Scene identification completed for {filename}: {action or 'no_match'}",
                          operation="Metadata matching · identification result", current_item=filename,
                          event="metadata.scene_match_completed", details={"filename": filename,
                          "channel_id": channel_id, "message_id": message_id, "action": action or "no_match",
                          "confidence": result.get("confidence"), "provider": result.get("provider")})
                if action == "skipped":
                    stats["skipped"] += 1
                elif action == "applied":
                    stats["matched"] += 1
                elif action == "suggested":
                    stats["suggested"] += 1
                elif action == "no_match":
                    stats["no_match"] += 1
                    filename_fallback_ids.append(int(row["id"]))
                    unresolved[int(row["id"])] = {"channel": channel_id, "message": message_id, "filename": filename, "oshash": oshash, "last": "no_match"}
                elif action == "provider_error":
                    stats["failed"] += 1
                    unresolved[int(row["id"])] = {"channel": channel_id, "message": message_id, "filename": filename, "oshash": oshash, "last": "failed"}
                else:
                    stats["no_match"] += 1
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                stats["failed"] += 1
                if "filename" in locals() and "oshash" in locals():
                    # a timeout or provider hiccup after the file was read: worth one more try
                    unresolved[int(row["id"])] = {"channel": channel_id, "message": message_id, "filename": filename, "oshash": oshash, "last": "failed"}
                _activity(ctx, f"Metadata processing failed for {row['filename']}: {type(exc).__name__}",
                          operation="Metadata matching · item failed", current_item=row["filename"],
                          event="metadata.item_failed", level="WARN",
                          details={"filename": row["filename"], "media_id": int(row["id"]),
                                   "channel_id": channel_id, "message_id": message_id,
                                   "error_type": type(exc).__name__, "error": str(exc)[:500]})
                if stats["failed"] <= 5:
                    ctx.db.log(f"{log_prefix}: {row['filename']}: {type(exc).__name__}: {exc}", "WARN")
            finally:
                _activity(ctx, f"Metadata item processing ended: {row['filename']}",
                          operation="Metadata matching · item checkpoint", current_item=row["filename"],
                          event="metadata.item_checkpoint", details={"filename": row["filename"],
                          "channel_id": channel_id, "message_id": message_id, "processed_items": stats["done"] + 1})
                stats["done"] += 1
                ctx.report(completed=stats["done"], total=total, current_item=row["filename"],
                           operation=operation, detail=_fingerprint_summary(stats))

    # Bounded batches: a failed item never stops the rest, cancel stops soon.
    for start in range(0, total, BATCH_SIZE):
        from ..core.scan_schedule import wait_for_metadata_scan_window
        await wait_for_metadata_scan_window(ctx)
        await ctx.checkpoint()
        batch = rows[start:start + BATCH_SIZE]
        prefetched = await _prefetch_messages(ctx, client, batch)
        await warm_fingerprints(batch, prefetched)
        results = await asyncio.gather(*(one(r, prefetched) for r in batch), return_exceptions=True)
        for r in results:
            if isinstance(r, (JobCancelled, asyncio.CancelledError)):
                raise r
    # Follow-up rounds. One pass asks about each video once, in turn, with what the catalogue knew at that moment. Identifying one video
    # adds performers and studios that would have helped an earlier one, and a provider can time out for one video and answer the next, so a
    # single pass left some unidentified that a second scan or a "recheck metadata" then found. Ask again about the videos still without
    # an answer while the previous round identified more (and once after a provider error), at most MAX_FOLLOW_UP_ROUNDS times.
    async def follow_up_round(round_number: int) -> int:
        resolved_now = 0
        pending_ids = list(unresolved)

        async def again(media_id: int) -> None:
            nonlocal resolved_now
            info = unresolved.get(media_id)
            if not info:
                return
            async with sem:
                await ctx.checkpoint()
                try:
                    result = await ctx.cancellable(lambda: identify_video(
                        ctx.db, channel_id=info["channel"], message_id=info["message"], filename=info["filename"], oshash=info["oshash"] or None,
                        force=force, reconcile_filename_entities=False, visual_scan=visual_match,
                    ), timeout=PROVIDER_OPERATION_TIMEOUT_SECONDS, activity=True)
                except (JobCancelled, asyncio.CancelledError):
                    raise
                except Exception:
                    return                                   # still unanswered; the next round or scan will try again
                action = result.get("action")
                if action in ("applied", "suggested", "skipped"):
                    if info["last"] == "failed":
                        stats["failed"] = max(0, stats["failed"] - 1)
                    else:
                        stats["no_match"] = max(0, stats["no_match"] - 1)
                    stats["matched" if action == "applied" else "suggested" if action == "suggested" else "skipped"] += 1
                    unresolved.pop(media_id, None)
                    if media_id in filename_fallback_ids:
                        filename_fallback_ids.remove(media_id)
                    resolved_now += 1
                elif action == "provider_error":
                    info["last"] = "failed"

        for start in range(0, len(pending_ids), BATCH_SIZE):
            await wait_for_metadata_scan_window(ctx)
            await ctx.checkpoint()
            await asyncio.gather(*(again(i) for i in pending_ids[start:start + BATCH_SIZE]))
        ctx.db.log(f"Metadata match: follow-up round {round_number} asked about {len(pending_ids)} unidentified video(s) and identified {resolved_now} more", "INFO")
        return resolved_now

    from ..core.scan_schedule import wait_for_metadata_scan_window
    errors_to_retry = any(v["last"] == "failed" for v in unresolved.values())
    progress = stats["matched"] > 0 or errors_to_retry
    for round_number in range(1, MAX_FOLLOW_UP_ROUNDS + 1):
        if not unresolved or not progress:
            break
        progress = (await follow_up_round(round_number)) > 0 and any(v["last"] == "no_match" or v["last"] == "failed" for v in unresolved.values())

    # Apply deterministic filename entity links only to videos for which the
    # provider pipeline returned no scene candidate. Exact hash matches and
    # reviewable provider candidates remain authoritative and are not overlaid.
    if reconcile_filename_entities and filename_fallback_ids:
        from ..metadata.filename_entities import link_known_entities_from_filenames
        filename_links = await asyncio.to_thread(
            link_known_entities_from_filenames, ctx.db, filename_fallback_ids
        )
        if any(filename_links.get(k, 0) for k in ("performer_links_added", "studio_links_added", "unknown_links_replaced", "ambiguous_entities_skipped")):
            ctx.db.log(
                "Filename entity fallback after provider no-match: "
                f"{filename_links['performer_links_added']} performer link(s), "
                f"{filename_links['studio_links_added']} studio link(s), "
                f"{filename_links['unknown_links_replaced']} Unknown placeholder(s) replaced, "
                f"{filename_links['ambiguous_entities_skipped']} ambiguous alias(es) skipped",
                "INFO" if not filename_links["ambiguous_entities_skipped"] else "WARN",
                event="metadata.filename_entity_match", job_id=ctx.job_id,
                details={"phase": "provider_no_match_fallback", **filename_links},
            )
    # Videos the providers still could not name get one look at their own snapshots (a studio's watermark, performers' faces). Suggestions
    # only: a person accepts them in Quick edit. Bounded, so a big library is worked through over several runs.
    await ctx.checkpoint()
    snapshot_summary = await asyncio.to_thread(_snapshot_pass, ctx.db)
    ctx.report(completed=total, total=total, operation="Finished", detail=_fingerprint_summary(stats), force=True)
    extra = []
    if snapshot_summary.get("studio_suggestions") or snapshot_summary.get("performer_suggestions"):
        extra.append(f"{snapshot_summary['studio_suggestions']} studio and {snapshot_summary['performer_suggestions']} performer suggestion(s) from snapshots (needs confirmation)")
    if stats["suggested"]:
        extra.append(f"{stats['suggested']} suggested (needs confirmation)")
    if stats["skipped"]:
        extra.append(f"{stats['skipped']} already identified, skipped")
    if stats["hash_failed"]:
        extra.append(f"{stats['hash_failed']} identified by filename only (file fingerprint unavailable)")
    if stats["failed"]:
        extra.append(f"{stats['failed']} failed")
    ctx.db.log(f"Metadata match: {total} checked, {stats['matched']} matched, {stats['no_match']} no match; "
               f"{_fingerprint_summary(stats)}"
               + ("; " + "; ".join(extra) if extra else ""), "SUCCESS" if not stats["failed"] else "WARN")
    await asyncio.to_thread(_record_metadata_result, ctx, sources, total, stats)   # counts every video of each channel: not on the event loop


SNAPSHOT_LOOKS_PER_RUN = 25


def _snapshot_pass(db) -> dict:
    """snapshot_identity.run for a bounded number of videos; never lets a problem here fail the metadata run."""
    try:
        from . import snapshot_identity
        summary = snapshot_identity.run(db, limit=SNAPSHOT_LOOKS_PER_RUN)
        try:
            from . import performer_snapshot_faces
            performer_snapshot_faces.grow_all(db, reader=snapshot_identity.make_face_reader(), max_videos=SNAPSHOT_LOOKS_PER_RUN // 2)
        except Exception:
            _swallowed_note("metadata_sync._snapshot_pass.gallery")   # no face models yet: the gallery grows once they exist
        return summary
    except Exception:
        _swallowed_note("metadata_sync._snapshot_pass")
        return {}


def _record_metadata_result(ctx, sources, total, stats) -> None:
    """V29.4.133: end with what was verified (failures, videos still waiting)."""
    try:
        from ..core import job_results
        from ..core.resource_registry import media_readiness_stats
        parts = [f"{total} checked"] + ([f"{stats['failed']} failed"] if stats.get("failed") else [])
        progress = None
        if sources:
            pending = everything = 0
            for source in sources:
                r = media_readiness_stats(ctx.db, channel_id=source)
                pending += int(r.get("metadata_pending") or 0)
                everything += int(r.get("total") or 0)
            if everything:
                progress = 100.0 * (everything - pending) / everything
            if pending:
                parts.append(f"{pending:,} of {everything:,} videos still need metadata")
        job_results.record(ctx.db, ctx.job_id, status="COMPLETED_WITH_ERRORS" if stats.get("failed") else "COMPLETED",
                           progress=progress, summary=", ".join(parts))
    except Exception:
        _swallowed_note("metadata_sync._record_metadata_result")
        pass
