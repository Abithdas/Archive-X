"""Explicit registration for performer/studio editing and local mapping routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Provider candidates
#       - Duplicate maintenance
#       - Merge and cleanup
#       - Performer and studio editing
#       - Local mapping
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from .image_payloads import parse_manual_image_payload as _parse_manual_image_payload


def register_entity_routes(
    router: APIRouter,
    *,
    db,
    jobs,
    telegram,
    require_auth,
    config,
    _proxy_entity_image,
    _queue_catalogue_caption_sync,
    _sync_catalogue_caption,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Provider candidates
    # Studio and performer provider lookups.
    # ------------------------------------------------------------------
    """Register performer/studio editing, merge and mapping routes."""
    @router.get("/api/studios/provider-search")
    async def search_studio_provider_candidates(request: Request):
        """Search StashDB and ThePornDB for studio candidates without importing them."""
        require_auth(request)
        q = str(request.query_params.get("q") or "").strip()
        try:
            limit = max(1, min(10, int(request.query_params.get("limit", "8"))))
        except ValueError:
            limit = 8
        if len(q) < 2:
            return []
        from ..metadata.providers import search_studio_providers
        try:
            return await search_studio_providers(db, q, limit=limit)
        except Exception as exc:
            db.log(f"Studio provider search failed for {q!r}: {type(exc).__name__}: {exc}", "WARN")
            return []

    @router.get("/api/performer-gender/missing")
    async def performers_missing_gender(request: Request):
        """How many performers have no gender (they are hidden from the Female, Male and Trans lists)."""
        require_auth(request)
        from ..metadata.gender_repair import missing_gender_count
        return {"ok": True, "count": await asyncio.to_thread(missing_gender_count, db)}

    @router.post("/api/performer-gender/restore")
    async def performers_restore_gender(request: Request):
        """Fill in blank genders from the provider details stored with each performer's videos."""
        require_auth(request)
        from ..metadata.gender_repair import restore_missing_genders
        result = await asyncio.to_thread(restore_missing_genders, db)
        db.log(f"Restored missing performer genders: {result['restored']} of {result['checked']}")
        return {"ok": True, **result}

    @router.get("/api/performers/{performer_id}/image-candidates")
    async def performer_image_candidates(performer_id: int, request: Request):
        """Return alternative provider artwork without changing the performer."""
        require_auth(request)
        try:
            limit = max(1, min(20, int(request.query_params.get("limit", "10"))))
        except ValueError:
            limit = 10
        try:
            offset = max(0, int(request.query_params.get("offset", "0")))
        except ValueError:
            offset = 0
        with db.lock:
            row = db.conn.execute(
                "SELECT id,name,provider_ids,image,image_source FROM performers WHERE id=?",
                (performer_id,),
            ).fetchone()
        if not row:
            raise HTTPException(404, "Performer not found")
        from ..metadata.providers import performer_image_candidates_from_providers
        try:
            # Request one extra item per provider so the UI can accurately
            # expose Load more without requiring a second provider call.
            candidates = await performer_image_candidates_from_providers(
                db, str(row["name"] or ""), row["provider_ids"], limit=limit + 1, offset=offset, entity_id=int(performer_id),
            )
        except Exception as exc:
            db.log(f"Performer image candidate lookup failed id={performer_id}: {type(exc).__name__}: {exc}", "WARN")
            raise HTTPException(502, "Provider image lookup failed")
        current = str(row["image"] or "").strip()
        provider_counts = {}
        for item in candidates:
            provider_key = str(item.get("provider") or "").strip().lower()
            provider_counts[provider_key] = provider_counts.get(provider_key, 0) + 1
        has_more = any(count > limit for count in provider_counts.values())
        trimmed = []
        for provider_key in ("stashdb", "theporndb"):
            trimmed.extend([item for item in candidates if str(item.get("provider") or "").strip().lower() == provider_key][:limit])
        out = []
        for item in trimmed:
            entry = dict(item)
            entry["current"] = bool(current and entry.get("url") == current)
            out.append(entry)
        return {"ok": True, "current_image": current, "candidates": out, "offset": offset, "limit": limit,
                "has_more": has_more}


    # ------------------------------------------------------------------
    # SUBSECTION: Duplicate maintenance
    # Performer and channel duplicate audits.
    # ------------------------------------------------------------------
    @router.get("/api/performers/duplicate-audit")
    async def performer_duplicate_audit(request: Request):
        """Audit every stored performer record without changing catalogue data."""
        require_auth(request)
        from ..engines.performer_duplicate_audit import audit_performer_duplicates
        try:
            return await asyncio.to_thread(audit_performer_duplicates, db)
        except Exception as exc:
            db.log(f"Performer duplicate audit failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(500, "The performer duplicate audit failed; no catalogue changes were made") from exc

    @router.post("/api/performers/duplicate-audit/auto-cleanup")
    async def performer_duplicate_auto_cleanup(request: Request):
        """Merge only high-confidence performer duplicates; leave uncertainty for review."""
        require_auth(request)
        from ..engines.performer_duplicate_audit import auto_cleanup_performer_duplicates
        try:
            return await asyncio.to_thread(auto_cleanup_performer_duplicates, db)
        except Exception as exc:
            db.log(f"Automatic performer duplicate cleanup failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(500, "Automatic cleanup failed; all merges from this run were rolled back") from exc

    @router.get("/api/channel-maintenance/duplicates/all")
    async def channel_maintenance_duplicate_audit_all(request: Request):
        """Review verified duplicate identities across saved indexed videos.

        This is intentionally local and read-only: it never opens Telegram or
        computes missing fingerprints, so it can run while Telegram is offline.
        """
        require_auth(request)
        from ..engines.channel_duplicate_audit import audit_all_indexed_duplicates
        try:
            return await asyncio.to_thread(audit_all_indexed_duplicates, db)
        except Exception as exc:
            db.log(f"Cross-channel duplicate review failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(500, "The local cross-channel duplicate review failed; no Telegram messages were changed") from exc

    @router.get("/api/channel-maintenance/duplicates")
    async def channel_maintenance_duplicate_audit(request: Request):
        """Audit one saved Telegram channel for byte-identical duplicate video posts."""
        require_auth(request)
        if not telegram.connected or telegram.client is None:
            raise HTTPException(503, "Connect Telegram before scanning a channel")
        raw_channel_id = str(request.query_params.get("channel_id") or "").strip()
        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "Choose a valid channel before scanning")
        channel_key = str(channel_id)
        from ..engines.channel_duplicate_audit import (
            build_duplicate_groups, load_channel_rows, missing_hash_candidate_buckets, save_oshash, row_oshash,
        )
        from ..core.telegram_registry import register_known_message
        from ..engines.media_utils import is_video_message
        from ..playback.stream_server import make_record

        with db.reading() as rdb:
            channel = rdb.execute("SELECT telegram_id,title FROM channels WHERE telegram_id=?", (channel_key,)).fetchone()
            if not channel:
                has_rows = rdb.execute(
                    "SELECT 1 FROM media WHERE CAST(source_channel AS TEXT)=? AND CAST(source_message_id AS INTEGER)>0 LIMIT 1",
                    (channel_key,),
                ).fetchone()
                if not has_rows:
                    raise HTTPException(404, "Channel not found in the saved Archive X channel list")
                channel_title = "Unknown channel"
            else:
                channel_title = str(channel["title"] or "Unknown channel")
            scan_state = rdb.execute(
                "SELECT last_scan_at,last_scan_status FROM telegram_channel_registry_state WHERE channel_id=? LIMIT 1",
                (channel_key,),
            ).fetchone()
        rows = load_channel_rows(db, channel_key)
        hashes_computed = 0
        fingerprint_errors: list[str] = []
        candidate_buckets = missing_hash_candidate_buckets(rows)
        if candidate_buckets:
            try:
                source = await telegram.client.get_entity(channel_id)
                loop = asyncio.get_running_loop()
            except Exception as exc:
                raise HTTPException(502, f"Could not open the selected Telegram channel: {str(exc)[:180]}") from exc

            def _compute_oshash(message):
                from ..core.telegram_range_transport import DirectTelegramRangeSource
                from ..metadata.fingerprint import oshash_from_source
                reader = DirectTelegramRangeSource(telegram.client, message, loop)
                return oshash_from_source(reader)

            # Only files that share exact size/duration/dimensions with another
            # un-fingerprinted row are fetched. Metadata similarity is never
            # used by itself as proof that a file is a duplicate.
            seen_message_ids: set[int] = set()
            for bucket in candidate_buckets:
                for candidate in bucket:
                    message_id = int(candidate.get("source_message_id") or 0)
                    if not message_id or message_id in seen_message_ids or row_oshash(candidate):
                        continue
                    seen_message_ids.add(message_id)
                    try:
                        message = await telegram.client.get_messages(source, ids=message_id)
                        if not message or not is_video_message(message):
                            fingerprint_errors.append(f"Message {message_id}: not available as a video now; skipped")
                            continue
                        live_size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
                        indexed_size = int(candidate.get("size") or 0)
                        if live_size <= 0 or (indexed_size > 0 and live_size != indexed_size):
                            fingerprint_errors.append(f"Message {message_id}: source size changed; refresh the channel index and rescan")
                            continue
                        fingerprint = await asyncio.to_thread(_compute_oshash, message)
                        record = make_record(message)
                        if record:
                            register_known_message(
                                db, channel_id=channel_key, message_id=message_id, record=record,
                                media_id=int(candidate.get("id") or 0) or None,
                                thumbnail_message_id=int(candidate.get("thumbnail_message_id") or candidate.get("registry_thumbnail_message_id") or 0) or None,
                            )
                        save_oshash(db, channel_id=channel_key, message_id=message_id,
                                    media_id=int(candidate.get("id") or 0), fingerprint=fingerprint)
                        hashes_computed += 1
                    except Exception as exc:
                        db.log(f"Channel duplicate fingerprint failed for {channel_key}/{message_id}: {type(exc).__name__}: {exc}", "WARN")
                        fingerprint_errors.append(f"Message {message_id}: could not fingerprint ({type(exc).__name__})")

        # Reload after updating fingerprints/document IDs so the results always
        # derive from the authoritative local identity rows.
        rows = load_channel_rows(db, channel_key)
        groups = build_duplicate_groups(rows)
        warning = ""
        if not scan_state or str(scan_state["last_scan_status"] or "").upper() != "COMPLETE":
            warning = "The channel does not have a recorded complete inventory scan. Run the normal channel scan first to include new or unindexed posts."
        return {
            "ok": True,
            "channel_id": channel_key,
            "channel_title": channel_title,
            "indexed_video_count": len(rows),
            "duplicate_group_count": len(groups),
            "duplicate_video_count": sum(int(group["duplicate_count"]) for group in groups),
            "hashes_computed": hashes_computed,
            "groups": groups,
            "last_scan_at": str(scan_state["last_scan_at"] or "") if scan_state else "",
            "inventory_complete": bool(scan_state and str(scan_state["last_scan_status"] or "").upper() == "COMPLETE"),
            "warning": warning,
            "fingerprint_warnings": fingerprint_errors[:30],
            "fingerprint_warnings_truncated": len(fingerprint_errors) > 30,
        }

    @router.post("/api/channel-maintenance/delete-duplicate")
    async def channel_maintenance_delete_duplicate(request: Request):
        """Delete one selected duplicate video after live-verifying a remaining copy."""
        require_auth(request)
        if not telegram.connected or telegram.client is None:
            raise HTTPException(503, "Connect Telegram before deleting a duplicate")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Request body must be valid JSON")
        if not isinstance(body, dict) or body.get("confirm_delete") is not True:
            raise HTTPException(400, "An explicit confirmation is required before deleting a duplicate")
        try:
            channel_id = int(body.get("channel_id"))
            message_id = int(body.get("message_id"))
            keep_message_id = int(body.get("keep_message_id"))
            keep_channel_id = int(body.get("keep_channel_id", channel_id))
        except (TypeError, ValueError):
            raise HTTPException(400, "Target and retained channel/video message IDs are required")
        if (channel_id == 0 or keep_channel_id == 0 or message_id <= 0 or keep_message_id <= 0
                or (channel_id == keep_channel_id and message_id == keep_message_id)):
            raise HTTPException(400, "Choose different valid videos to delete and retain")

        from ..engines.channel_duplicate_audit import load_channel_rows, rows_share_duplicate_identity, row_document_id
        from ..engines.media_utils import is_video_message, is_image_message
        from ..playback.stream_server import make_record
        from .. import config
        from ..core.telegram_registry import ensure_telegram_registry

        channel_key = str(channel_id)
        keep_channel_key = str(keep_channel_id)
        def _load_rows():
            # Reading the saved inventory runs in a worker thread so it cannot freeze the event loop.
            rows = load_channel_rows(db, channel_key)
            return rows, rows if keep_channel_key == channel_key else load_channel_rows(db, keep_channel_key)
        target_rows, keep_rows = await asyncio.to_thread(_load_rows)
        target_by_message = {int(r.get("source_message_id") or 0): r for r in target_rows}
        keep_by_message = {int(r.get("source_message_id") or 0): r for r in keep_rows}
        target_row = target_by_message.get(message_id)
        keep_row = keep_by_message.get(keep_message_id)
        if not target_row or not keep_row:
            raise HTTPException(404, "One of the duplicate videos is no longer active in the saved channel inventory; scan again")
        if not rows_share_duplicate_identity(target_row, keep_row):
            raise HTTPException(409, "These videos no longer share a verified Telegram document ID or OSHASH. Rescan before deleting; no deletion was attempted")

        try:
            source = await telegram.client.get_entity(channel_id)
            keep_source = source if keep_channel_key == channel_key else await telegram.client.get_entity(keep_channel_id)
            target_message, keep_message = await asyncio.gather(
                telegram.client.get_messages(source, ids=message_id),
                telegram.client.get_messages(keep_source, ids=keep_message_id),
            )
        except Exception as exc:
            raise HTTPException(502, f"Could not verify the selected Telegram messages: {str(exc)[:180]}") from exc
        if not target_message or not keep_message or not is_video_message(target_message) or not is_video_message(keep_message):
            raise HTTPException(409, "One of the selected messages is no longer a video. Nothing was deleted")

        target_record, keep_record = make_record(target_message), make_record(keep_message)
        target_doc_id = str((target_record or {}).get("document_id") or "")
        keep_doc_id = str((keep_record or {}).get("document_id") or "")
        expected_target_doc = row_document_id(target_row)
        expected_keep_doc = row_document_id(keep_row)
        for expected, actual, label in ((expected_target_doc, target_doc_id, "selected video"), (expected_keep_doc, keep_doc_id, "retained copy")):
            if expected and actual and expected != actual:
                raise HTTPException(409, f"The selected {label} message identity changed since the scan. Rescan before deleting")

        # If the duplicate relationship relies on a content hash rather than a
        # shared Telegram document ID, re-hash both live messages immediately
        # before deletion so stale catalogue data cannot authorize removal.
        if not (target_doc_id and target_doc_id == keep_doc_id):
            from ..core.telegram_range_transport import DirectTelegramRangeSource
            from ..metadata.fingerprint import oshash_from_source
            loop = asyncio.get_running_loop()
            def _live_hash(message):
                return oshash_from_source(DirectTelegramRangeSource(telegram.client, message, loop))
            try:
                target_hash, keep_hash = await asyncio.gather(
                    asyncio.to_thread(_live_hash, target_message), asyncio.to_thread(_live_hash, keep_message),
                )
            except Exception as exc:
                raise HTTPException(502, f"Could not verify both video fingerprints before deletion: {str(exc)[:160]}") from exc
            if target_hash != keep_hash or int(getattr(getattr(target_message, "file", None), "size", 0) or 0) != int(getattr(getattr(keep_message, "file", None), "size", 0) or 0):
                raise HTTPException(409, "The selected messages no longer have identical content fingerprints. Nothing was deleted")

        # Delete the video's thumbnail / contact-sheet message together with it.
        # Resolve it exactly like the "move to channel" workflow does: the saved
        # association first (media row, Telegram location registry, playback cache),
        # then the contact sheet posted right after the video. Uploads made before
        # Archive X tracked the link, or sent from other tools, have no saved
        # association; without the companion lookup their thumbnail was left behind.
        from ..core.telegram_registry import (
            document_for_location, resolve_linked_thumbnail_message_id, thumbnail_message_is_unshared,
        )
        delete_ids = [message_id]
        thumb_deleted = False
        thumb_source = ""
        warnings: list[str] = []
        def _resolve_thumb():
            try:
                registry_location = document_for_location(db, channel_key, message_id) or {}
            except Exception:
                registry_location = {}
            return resolve_linked_thumbnail_message_id(
                db, channel_id=channel_key, video_message_id=message_id,
                media_thumbnail_message_id=target_row.get("thumbnail_message_id") or target_row.get("registry_thumbnail_message_id"),
                location=registry_location,
            )
        thumb_id = await asyncio.to_thread(_resolve_thumb)
        thumb_source = "saved_link" if thumb_id else ""
        if not thumb_id:
            from ..engines.safe_message_move import discover_companion_thumbnail
            try:
                thumb_id = await discover_companion_thumbnail(
                    telegram.client, source, message_id,
                    str(target_row.get("filename") or getattr(getattr(target_message, "file", None), "name", "") or ""),
                )
            except Exception:
                thumb_id = 0
            thumb_source = "companion_lookup" if thumb_id else ""
        if thumb_id > 0 and thumb_id != message_id:
            if not await asyncio.to_thread(thumbnail_message_is_unshared, db, channel_id=channel_key, video_message_id=message_id, thumbnail_message_id=thumb_id):
                warnings.append(f"Associated thumbnail message {thumb_id} is shared or referenced by another video, so it was kept")
            else:
                try:
                    thumb_message = await telegram.client.get_messages(source, ids=thumb_id)
                except Exception:
                    thumb_message = None
                if thumb_message and is_image_message(thumb_message):
                    delete_ids.append(thumb_id)
                    thumb_deleted = True
                elif not thumb_message:
                    warnings.append(f"Associated thumbnail message {thumb_id} was already missing; only the video was deleted")
                else:
                    warnings.append(f"Linked message {thumb_id} was not an image thumbnail, so it was kept")
        else:
            warnings.append("No thumbnail message is linked to this video, so only the video was deleted")

        try:
            # One Telegram delete operation keeps the video and its associated
            # image together; no thumbnail is inferred from filename alone.
            await telegram.client.delete_messages(source, delete_ids)
        except Exception as exc:
            await asyncio.to_thread(db.log, f"Channel duplicate deletion failed for {channel_key}/{message_id}: {type(exc).__name__}: {exc}", "ERROR", event="channel.duplicate_delete")
            raise HTTPException(502, f"Telegram did not confirm deletion: {str(exc)[:200]}") from exc

        def _mark_deleted():
            now = config.now_iso()
            ensure_telegram_registry(db)
            # Collect locally cached thumbnail / preview-frame locations before their
            # database rows are removed, then delete the files themselves afterwards.
            local_cache_paths: list[str] = []
            with db.lock:
                try:
                    for cached in db.conn.execute(
                        "SELECT path, frames_path FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?",
                        (channel_key, message_id),
                    ).fetchall():
                        local_cache_paths.extend(str(v) for v in (cached["path"], cached["frames_path"]) if v)
                except Exception:
                    _swallowed_note("routes_entities.register_entity_routes.channel_maintenance_delete_duplicate")
                    pass
            with db.lock:
                db.conn.execute("UPDATE media SET status='SOURCE_DELETED',updated_at=? WHERE id=?", (now, int(target_row["id"])))
                table_names = {str(row[0]) for row in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "resource_state" in table_names:
                    db.conn.execute(
                        "UPDATE resource_state SET snapshot_state='UNKNOWN',last_reason='channel_duplicate_deleted',updated_at=? WHERE media_id=?",
                        (now, int(target_row["id"])),
                    )
                if "asset_registry" in table_names:
                    db.conn.execute(
                        "UPDATE asset_registry SET state='MISSING',updated_at=? WHERE media_id=? OR (entity_type='telegram_video' AND entity_id=?)",
                        (now, int(target_row["id"]), f"{channel_key}:{message_id}"),
                    )
                for table in ("thumbnail_cache", "timeline_preview_cache"):
                    if table in table_names:
                        db.conn.execute(f"DELETE FROM {table} WHERE source_channel=? AND video_message_id=?", (channel_key, message_id))
                if "resource_events" in table_names:
                    db.conn.execute(
                        "INSERT INTO resource_events(media_id,event_type,reason,detail,created_at) VALUES(?,?,?,?,?)",
                        (int(target_row["id"]), "SOURCE_DELETED", "channel_duplicate_cleanup", "Deleted verified duplicate video from Channel Maintenance", now),
                    )
                if "telegram_document_locations" in table_names:
                    for deleted_id in delete_ids:
                        db.conn.execute(
                            "UPDATE telegram_document_locations SET location_state='DELETED_BY_CHANNEL_MAINTENANCE',last_seen_at=? WHERE channel_id=? AND message_id=?",
                            (now, channel_key, int(deleted_id)),
                        )
                db.conn.commit()
            return local_cache_paths

        local_cache_paths = await asyncio.to_thread(_mark_deleted)
        def _purge_local_assets() -> int:
            """Delete this video's cached preview frames and contact sheet from disk."""
            import shutil
            from ..engines.preview_prefetch import preview_frames_path
            from ..engines.thumbnail_cache import cleanup_telegram_delivery_contact_sheet
            cache_root = config.CACHE_DIR.resolve()
            candidates = list(local_cache_paths)
            try:
                candidates.append(str(preview_frames_path(int(channel_key), int(message_id))))
            except Exception:
                _swallowed_note("routes_entities.register_entity_routes.channel_maintenance_delete_duplicate._purge_local_assets")
                pass
            removed = 0
            for raw in dict.fromkeys(candidates):
                try:
                    target = Path(raw).resolve()
                    # Only ever delete inside the app cache, never user media.
                    if cache_root not in target.parents:
                        continue
                    if target.is_dir():
                        shutil.rmtree(target, ignore_errors=True)
                        removed += 1
                    elif target.is_file():
                        target.unlink()
                        removed += 1
                except OSError:
                    continue
            try:
                if cleanup_telegram_delivery_contact_sheet(int(channel_key), int(message_id)):
                    removed += 1
            except Exception:
                _swallowed_note("routes_entities.register_entity_routes.channel_maintenance_delete_duplicate._purge_local_assets")
                pass
            return removed
        try:
            local_assets_removed = await asyncio.to_thread(_purge_local_assets)
        except Exception as exc:
            local_assets_removed = 0
            warnings.append(f"Local cached thumbnail files could not be fully removed: {type(exc).__name__}")

        await asyncio.to_thread(
            db.log,
            f"Channel maintenance deleted duplicate video {channel_key}/{message_id}; retained {keep_channel_key}/{keep_message_id}; "
            f"thumbnail_deleted={thumb_deleted} thumbnail_source={thumb_source or '-'} local_assets_removed={local_assets_removed}",
            event="channel.duplicate_delete",
        )
        return {
            "ok": True, "channel_id": channel_key, "deleted_message_id": message_id,
            "kept_channel_id": keep_channel_key, "kept_message_id": keep_message_id, "thumbnail_deleted": thumb_deleted,
            "thumbnail_message_id": thumb_id if thumb_deleted else None, "thumbnail_source": thumb_source or None,
            "local_assets_removed": local_assets_removed, "warnings": warnings,
        }


    # ------------------------------------------------------------------
    # SUBSECTION: Merge and cleanup
    # Performer merge and junk cleanup.
    # ------------------------------------------------------------------
    @router.post("/api/performers/merge")
    async def performer_merge(request: Request):
        """Merge a user-confirmed duplicate performer into a canonical record."""
        require_auth(request)
        try:
            body = await request.json()
            keep_id = int(body.get("keep_id"))
            merge_id = int(body.get("merge_id"))
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(400, "keep_id and merge_id are required")
        if keep_id == merge_id:
            raise HTTPException(400, "Choose two different performer records")
        from ..engines.entity_identity import merge_performers
        try:
            result = await asyncio.to_thread(merge_performers, db, keep_id, merge_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            db.log(f"Performer merge failed keep={keep_id} merge={merge_id}: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(500, "The performer merge failed; no changes were committed") from exc
        with db.lock:
            affected = [int(r["video_id"]) for r in db.conn.execute(
                "SELECT video_id FROM video_performers WHERE performer_id=? UNION "
                "SELECT video_id FROM local_video_performers WHERE performer_id=?",
                (keep_id, keep_id),
            ).fetchall()]
        queued = _queue_catalogue_caption_sync(affected)
        db.log(f"Merged performer {merge_id} into {keep_id} ({result.get('name')})")
        return {"ok": True, **result, "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}

    @router.post("/api/performers/merge-batch")
    async def performer_merge_batch(request: Request):
        """Atomically merge several user-selected duplicates into one canonical performer."""
        require_auth(request)
        try:
            body = await request.json()
            keep_id = int(body.get("keep_id"))
            merge_ids = body.get("merge_ids")
            if not isinstance(merge_ids, list):
                raise ValueError("merge_ids must be a list")
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(400, "keep_id and merge_ids are required")
        from ..engines.entity_identity import merge_performers_batch
        try:
            result = await asyncio.to_thread(merge_performers_batch, db, keep_id, merge_ids)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            db.log(f"Performer batch merge failed keep={keep_id} count={len(merge_ids)}: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(500, "The batch merge failed; all selected changes were rolled back") from exc
        with db.lock:
            affected = [int(r["video_id"]) for r in db.conn.execute(
                "SELECT video_id FROM video_performers WHERE performer_id=? UNION "
                "SELECT video_id FROM local_video_performers WHERE performer_id=?",
                (keep_id, keep_id),
            ).fetchall()]
        queued = _queue_catalogue_caption_sync(affected)
        db.log(f"Merged {result.get('merged_count', 0)} performer duplicates into {keep_id} ({result.get('name')})")
        return {"ok": True, **result, "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}

    @router.post("/api/performers/junk-cleanup")
    async def performer_junk_cleanup(request: Request):
        """Merge a reviewed filename-fragment performer or delete it only when unlinked."""
        require_auth(request)
        try:
            body = await request.json()
            performer_id = int(body.get("performer_id"))
            action = str(body.get("action") or "").strip().lower()
            target_id = int(body.get("target_id")) if body.get("target_id") not in (None, "") else None
        except (TypeError, ValueError, AttributeError):
            raise HTTPException(400, "performer_id and a valid action are required")
        if action not in {"merge", "delete"}:
            raise HTTPException(400, "action must be merge or delete")
        if action == "merge":
            if not target_id or target_id == performer_id:
                raise HTTPException(400, "Choose a different target performer")
            from ..engines.entity_identity import merge_performers
            try:
                result = await asyncio.to_thread(merge_performers, db, target_id, performer_id)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except Exception as exc:
                db.log(f"Junk performer merge failed source={performer_id} target={target_id}: {type(exc).__name__}: {exc}", "ERROR")
                raise HTTPException(500, "The performer could not be merged; no changes were committed") from exc
            with db.lock:
                affected = [int(row["video_id"]) for row in db.conn.execute(
                    "SELECT video_id FROM video_performers WHERE performer_id=? UNION "
                    "SELECT video_id FROM local_video_performers WHERE performer_id=?", (target_id, target_id)
                ).fetchall()]
            queued = _queue_catalogue_caption_sync(affected)
            db.log(f"Removed junk performer fragment {performer_id} by merging into {target_id}; caption sync queued={queued}")
            return {"ok": True, "action": "merge", **result,
                    "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}

        with db.lock:
            row = db.conn.execute("SELECT id,name FROM performers WHERE id=?", (performer_id,)).fetchone()
            if not row:
                raise HTTPException(404, "Performer not found")
            if str(row["name"] or "").strip().casefold() == "unknown performer":
                raise HTTPException(400, "Unknown Performer is a protected fallback record")
            linked = int(db.conn.execute(
                "SELECT COUNT(DISTINCT video_id) FROM (SELECT video_id FROM video_performers WHERE performer_id=? "
                "UNION SELECT video_id FROM local_video_performers WHERE performer_id=?)",
                (performer_id, performer_id),
            ).fetchone()[0] or 0)
            if linked:
                raise HTTPException(409, f"This performer still has {linked} linked video(s); merge those links into a target before deleting")
            db.conn.execute("SAVEPOINT archive_x_delete_junk_performer")
            try:
                tables = {str(item[0]) for item in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "entity_metadata" in tables:
                    db.conn.execute("DELETE FROM entity_metadata WHERE entity_type='performer' AND entity_id=?", (performer_id,))
                if "performer_image_cache" in tables:
                    db.conn.execute("DELETE FROM performer_image_cache WHERE performer_id=?", (performer_id,))
                if "metadata_suggestion_performers" in tables:
                    db.conn.execute("DELETE FROM metadata_suggestion_performers WHERE performer_id=?", (performer_id,))
                if "metadata_suggestions" in tables:
                    db.conn.execute("UPDATE metadata_suggestions SET suggested_performer_id=NULL WHERE suggested_performer_id=?", (performer_id,))
                for table, column in (("performer_refresh_state", "performer_id"), ("provider_entity_cache", "performer_id")):
                    if table in tables:
                        columns = {str(item[1]) for item in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
                        if column in columns:
                            db.conn.execute(f"DELETE FROM {table} WHERE {column}=?", (performer_id,))
                db.conn.execute("DELETE FROM performers WHERE id=?", (performer_id,))
                db.conn.execute("RELEASE SAVEPOINT archive_x_delete_junk_performer")
                db.conn.commit()
            except Exception:
                db.conn.execute("ROLLBACK TO SAVEPOINT archive_x_delete_junk_performer")
                db.conn.execute("RELEASE SAVEPOINT archive_x_delete_junk_performer")
                raise
        db.log(f"Deleted unlinked junk performer fragment {performer_id} ({row['name']})")
        return {"ok": True, "action": "delete", "performer_id": performer_id, "name": str(row["name"] or "")}


    # ------------------------------------------------------------------
    # SUBSECTION: Performer and studio editing
    # Edit forms, saves and sync.
    # ------------------------------------------------------------------
    @router.get("/api/performers/{performer_id}/edit")
    async def performer_edit_data(performer_id: int, request: Request):
        require_auth(request)
        def _read_performer():
            with db.reading() as rdb:
                row = rdb.execute("SELECT id,name,image,profile,bio,gender,provider_ids,image_source,image_locked,name_locked,profile_locked FROM performers WHERE id=?", (performer_id,)).fetchone()
                return dict(row) if row else None
        row = await asyncio.to_thread(_read_performer)
        if not row:
            raise HTTPException(404, "Performer not found")
        return row

    # Apply a performer edit, including an uploaded image.
    @router.post("/api/performers/{performer_id}/edit")
    async def performer_edit(performer_id: int, request: Request):
        require_auth(request)
        body = await request.json()
        with db.lock:
            row = db.conn.execute("SELECT * FROM performers WHERE id=?", (performer_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Performer not found")
        name = str(body.get("name") or row["name"] or "").strip()
        profile = str(body.get("profile") if body.get("profile") is not None else row["profile"] or "").strip()
        bio = str(body.get("bio") if body.get("bio") is not None else row["bio"] or "").strip()
        image_url = str(body.get("image_url") or "").strip()
        provider_image_url = str(body.get("provider_image_url") or "").strip()
        provider_image_provider = str(body.get("provider_image_provider") or "provider").strip().lower()
        reset_api = bool(body.get("reset_image_to_api"))
        blob, mime = _parse_manual_image_payload(body)
        if not name:
            raise HTTPException(400, "Performer name is required")
        for candidate_url in (image_url, provider_image_url):
            if candidate_url and not re.match(r"^https?://[^\s]+$", candidate_url, re.I):
                raise HTTPException(400, "Image URL must be HTTP or HTTPS")

        try:
            # Provider-selected images are downloaded/validated through the same
            # entity-image cache used by normal API image rendering. This makes
            # the selected alternative immediately available offline and marks
            # it locked so background sync does not replace it.
            if provider_image_url:
                try:
                    image_bytes, image_mime = await _proxy_entity_image(
                        "performer", int(performer_id), provider_image_url,
                        allow_initial_smart_crop=False, force_original=True,
                    )
                except Exception as exc:
                    raise HTTPException(400, f"Selected provider image could not be downloaded: {exc}") from exc

            with db.lock:
                db.conn.execute("UPDATE performers SET name=?,profile=?,bio=?,name_locked=1,profile_locked=1 WHERE id=?", (name, profile, bio, performer_id))
                if "gender" in body:
                    # Chosen by hand: female, male, trans, or empty for unknown.
                    chosen = str(body.get("gender") or "").strip().lower()
                    if chosen not in {"", "female", "male", "trans"}:
                        raise HTTPException(400, "Gender must be female, male, trans or empty")
                    db.conn.execute("UPDATE performers SET gender=? WHERE id=?", (chosen, performer_id))
                if reset_api:
                    db.conn.execute("UPDATE performers SET image_locked=0,image_source='provider',image_crop_version=0 WHERE id=?", (performer_id,))
                    db.conn.execute("DELETE FROM performer_image_cache WHERE performer_id=?", (performer_id,))
                elif blob:
                    import hashlib
                    token = "manual-upload:" + hashlib.sha1(blob).hexdigest()
                    db.conn.execute("UPDATE performers SET image=?,image_source='manual',image_locked=1,image_crop_version=0 WHERE id=?", (token, performer_id))
                    db.conn.execute("INSERT OR REPLACE INTO performer_image_cache(performer_id,url,mime,data,updated_at) VALUES(?,?,?,?,?)", (performer_id, token, mime, blob, config.now_iso()))
                elif image_url:
                    db.conn.execute("UPDATE performers SET image=?,image_source='manual',image_locked=1,image_crop_version=0 WHERE id=?", (image_url, performer_id))
                    db.conn.execute("DELETE FROM performer_image_cache WHERE performer_id=?", (performer_id,))
                elif provider_image_url:
                    db.conn.execute(
                        "UPDATE performers SET image=?,image_source=?,image_locked=1,image_crop_version=0 WHERE id=?",
                        (provider_image_url, f"provider-selected:{provider_image_provider}", performer_id),
                    )
                    # _proxy_entity_image already populated the current URL in
                    # performer_image_cache; leave that bytes cache intact.
                db.conn.commit()
                out = db.conn.execute("SELECT * FROM performers WHERE id=?", (performer_id,)).fetchone()
        except HTTPException:
            raise
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(409, "Another performer already uses that name")
            raise
        affected = []
        if name != str(row["name"] or ""):
            with db.lock:
                affected = [int(r["video_id"]) for r in db.conn.execute(
                    "SELECT video_id FROM video_performers WHERE performer_id=? UNION "
                    "SELECT video_id FROM local_video_performers WHERE performer_id=?",
                    (int(performer_id), int(performer_id)),
                ).fetchall()]
        queued = _queue_catalogue_caption_sync(affected)
        db.log(f"Performer profile edit: {performer_id} {name}")
        return {"ok": True, "performer": dict(out),
                "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}


    @router.get("/api/studios/{studio_id}/image-candidates")
    async def studio_image_candidates(studio_id: int, request: Request):
        """Return alternative provider artwork without changing the studio."""
        require_auth(request)
        try:
            limit = max(1, min(20, int(request.query_params.get("limit", "10"))))
        except ValueError:
            limit = 10
        try:
            offset = max(0, int(request.query_params.get("offset", "0")))
        except ValueError:
            offset = 0
        with db.lock:
            row = db.conn.execute(
                "SELECT id,name,provider_ids,image FROM studios WHERE id=?", (studio_id,)
            ).fetchone()
        if not row:
            raise HTTPException(404, "Studio/site not found")
        from ..metadata.providers import studio_image_candidates_from_providers
        try:
            candidates = await studio_image_candidates_from_providers(db, str(row["name"] or ""), row["provider_ids"], limit=limit + 1, offset=offset, entity_id=int(studio_id))
        except Exception as exc:
            db.log(f"Studio image candidate lookup failed id={studio_id}: {type(exc).__name__}: {exc}", "WARN")
            raise HTTPException(502, "Provider image lookup failed")
        current = str(row["image"] or "").strip()
        provider_counts = {}
        for item in candidates:
            provider_key = str(item.get("provider") or "").strip().lower()
            provider_counts[provider_key] = provider_counts.get(provider_key, 0) + 1
        has_more = any(count > limit for count in provider_counts.values())
        trimmed = []
        for provider_key in ("stashdb", "theporndb"):
            trimmed.extend([item for item in candidates if str(item.get("provider") or "").strip().lower() == provider_key][:limit])
        out = []
        for item in trimmed:
            entry = dict(item)
            entry["current"] = bool(current and entry.get("url") == current)
            out.append(entry)
        return {"ok": True, "current_image": current, "candidates": out, "offset": offset, "limit": limit,
                "has_more": has_more}

    @router.get("/api/studios/{studio_id}/edit")
    async def studio_edit_data(studio_id: int, request: Request):
        require_auth(request)
        def _read_studio():
            with db.reading() as rdb:
                row = rdb.execute("SELECT id,name,image,provider_ids,image_source,image_locked,name_locked FROM studios WHERE id=?", (studio_id,)).fetchone()
                return dict(row) if row else None
        row = await asyncio.to_thread(_read_studio)
        if not row:
            raise HTTPException(404, "Studio/site not found")
        return row

    # Apply a studio edit, including an uploaded image.
    @router.post("/api/studios/{studio_id}/edit")
    async def studio_edit(studio_id: int, request: Request):
        require_auth(request)
        body = await request.json()
        with db.lock:
            row = db.conn.execute("SELECT * FROM studios WHERE id=?", (studio_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Studio/site not found")
        name = str(body.get("name") or row["name"] or "").strip()
        image_url = str(body.get("image_url") or "").strip()
        provider_image_url = str(body.get("provider_image_url") or "").strip()
        provider_image_provider = str(body.get("provider_image_provider") or "provider").strip().lower()
        reset_api = bool(body.get("reset_image_to_api"))
        blob, mime = _parse_manual_image_payload(body)
        if not name:
            raise HTTPException(400, "Studio/site name is required")
        for candidate_url in (image_url, provider_image_url):
            if candidate_url and not re.match(r"^https?://[^\s]+$", candidate_url, re.I):
                raise HTTPException(400, "Image URL must be HTTP or HTTPS")
        if provider_image_url:
            try:
                # A user explicitly selected this artwork. Bypass any older
                # cached copy so choosing the same provider URL refreshes its bytes.
                await _proxy_entity_image("studio", int(studio_id), provider_image_url, force_original=True)
            except Exception as exc:
                raise HTTPException(400, f"Selected provider image could not be downloaded: {exc}") from exc
        try:
            with db.lock:
                affected = [int(r["video_id"]) for r in db.conn.execute(
                    "SELECT video_id FROM video_studios WHERE studio_id=?", (int(studio_id),)
                ).fetchall()] if name != str(row["name"] or "") else []
                db.conn.execute("UPDATE studios SET name=?,name_locked=1 WHERE id=?", (name, studio_id))
                if affected:
                    marks = ",".join("?" for _ in affected)
                    db.conn.execute(
                        f"UPDATE media SET site_name=?,updated_at=? WHERE id IN ({marks})",
                        (name, config.now_iso(), *affected),
                    )
                if reset_api:
                    db.conn.execute("UPDATE studios SET image_locked=0,image_source='provider' WHERE id=?", (studio_id,))
                    db.conn.execute("DELETE FROM studio_image_cache WHERE studio_id=?", (studio_id,))
                elif blob:
                    import hashlib
                    token = "manual-upload:" + hashlib.sha1(blob).hexdigest()
                    db.conn.execute("UPDATE studios SET image=?,image_source='manual',image_locked=1 WHERE id=?", (token, studio_id))
                    db.conn.execute("INSERT OR REPLACE INTO studio_image_cache(studio_id,url,mime,data,updated_at) VALUES(?,?,?,?,?)", (studio_id, token, mime, blob, config.now_iso()))
                elif image_url:
                    db.conn.execute("UPDATE studios SET image=?,image_source='manual',image_locked=1 WHERE id=?", (image_url, studio_id))
                    db.conn.execute("DELETE FROM studio_image_cache WHERE studio_id=?", (studio_id,))
                elif provider_image_url:
                    db.conn.execute(
                        "UPDATE studios SET image=?,image_source=?,image_locked=1 WHERE id=?",
                        (provider_image_url, f"provider-selected:{provider_image_provider}", studio_id),
                    )
                db.conn.commit()
                out = db.conn.execute("SELECT * FROM studios WHERE id=?", (studio_id,)).fetchone()
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                raise HTTPException(409, "Another studio/site already uses that name")
            raise
        queued = _queue_catalogue_caption_sync(affected)
        db.log(f"Manual studio/site edit: {studio_id} {name}")
        return {"ok": True, "site": dict(out),
                "caption_sync": {"status": "queued" if queued else "not_needed", "count": queued}}

    @router.post("/api/performers/{performer_id}/sync")
    def performer_sync(performer_id:int, request:Request):
        """Compatibility endpoint; route performer rechecks through the combined module."""
        require_auth(request)
        with db.lock:
            if not db.conn.execute("SELECT id FROM performers WHERE id=?",(performer_id,)).fetchone():
                raise HTTPException(404,"Performer not found")
            rows=db.conn.execute(
                "SELECT DISTINCT m.id FROM media m JOIN (SELECT video_id FROM video_performers WHERE performer_id=? UNION SELECT video_id FROM local_video_performers WHERE performer_id=?) x ON x.video_id=m.id ORDER BY m.id",
                (performer_id,performer_id)).fetchall()
        mids=[int(r['id']) for r in rows]
        if not mids:return {"ok":True,"count":0}
        detail=json.dumps({'mode':'unified','scopes':['performer'],'performer_ids':[performer_id],'channel_ids':[],'site_ids':[],'force_recheck':True,'limit':0})
        if jobs.is_active('SUGGESTION_SCAN'):
            active=db.active_jobs('SUGGESTION_SCAN')
            return {"ok":True,"already_running":True,"id":active[0]["id"] if active else None,"count":len(mids)}
        jid=db.create_job('SUGGESTION_SCAN','','',False,detail=detail); jobs.submit(jid)
        return {"ok":True,"id":jid,"count":len(mids)}


    # ------------------------------------------------------------------
    # SUBSECTION: Local mapping
    # Local performer and site maps and quick-edit lookup.
    # ------------------------------------------------------------------
    @router.post("/api/local/performer-map")
    async def local_performer_map(request: Request):
        """POST /api/local/performer-map: Local performer map."""
        require_auth(request)
        body = await request.json()
        channel_id = str(body.get("channel_id") or "").strip()
        message_id = int(body.get("message_id") or 0)
        name = str(body.get("name") or "").strip()
        action = str(body.get("action") or "move").strip().lower()
        if not channel_id or not message_id:
            raise HTTPException(400, "channel_id and message_id are required")
        if action != "unknown" and not name:
            raise HTTPException(400, "performer name is required")
        with db.lock:
            media = db.conn.execute(
                "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                (channel_id, message_id)).fetchone()
            if not media:
                # Allow mapping directly from an uncatalogued Library card. The
                # live channel index is already cached by /videos, so create the
                # minimal local media row without another Telegram download.
                raw = db.setting(f"video_index_v2_{channel_id}", {}) or db.setting(f"video_index_{channel_id}", {}) or {}
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        raw = {}
                if isinstance(raw, dict):
                    item = raw.get(str(message_id)) or raw.get(message_id) or {}
                else:
                    item = {}
                if not item:
                    raise HTTPException(404, "Video is not available in the local index")
                db.conn.execute(
                    "INSERT OR IGNORE INTO media(source_channel,source_message_id,filename,title,size,duration,width,height,streamable,upload_date,created_at,updated_at,status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (channel_id,message_id,item.get("name") or f"video_{message_id}.mp4",item.get("name") or f"video_{message_id}.mp4",
                     item.get("size") or 0,item.get("duration") or 0,item.get("width") or 0,item.get("height") or 0,
                     1 if item.get("streamable") else 0,item.get("date") or "",config.now_iso(),config.now_iso(),"LOCAL"))
                media = db.conn.execute(
                    "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                    (channel_id, message_id)).fetchone()
            existing = None
            if action == "unknown":
                existing = db.conn.execute("SELECT * FROM performers WHERE name='Unknown Performer' LIMIT 1").fetchone()
            elif body.get("performer_id"):
                try:
                    existing = db.conn.execute("SELECT * FROM performers WHERE id=?", (int(body["performer_id"]),)).fetchone()
                except (TypeError, ValueError):
                    existing = None
            provider = str(body.get("provider") or "").strip().casefold()
            provider_id = str(body.get("provider_id") or "").strip()
            if action == "unknown":
                provider = ""
                provider_id = ""
                name = "Unknown Performer"
            if existing is None and provider and provider_id:
                # Reuse an existing local performer that already carries the
                # exact provider identity. Never create anything during search.
                rows = db.conn.execute("SELECT * FROM performers WHERE provider_ids LIKE ?", (f'%"{provider}": "{provider_id}"%',)).fetchall()
                existing = rows[0] if rows else None
            if existing is None:
                existing = db.conn.execute(
                    "SELECT * FROM performers WHERE lower(name)=lower(?) OR normalized_name=? LIMIT 1",
                    (name, db._norm_search(name))).fetchone()
            if existing:
                performer = existing
                # When an API candidate was explicitly selected, merge its
                # provider identity/artwork into the existing local performer.
                if provider and provider_id:
                    from ..engines.entity_identity import upsert_performer
                    pid = upsert_performer(
                        db, str(body.get("name") or name), provider=provider,
                        provider_id=provider_id, image=str(body.get("image") or "").strip() or None,
                        profile=str(body.get("profile") or "").strip() or None,
                    )
                    performer = db.conn.execute("SELECT * FROM performers WHERE id=?", (pid,)).fetchone()
            else:
                # A remote candidate may be imported only after the user
                # explicitly selects that provider result. A plain typed name
                # is never auto-created by the search box.
                if not (provider and provider_id):
                    raise HTTPException(404, "Performer not found. Select an API performer match instead of creating a new performer.")
                from ..engines.entity_identity import upsert_performer
                pid = upsert_performer(
                    db, name, provider=provider, provider_id=provider_id,
                    image=str(body.get("image") or "").strip() or None,
                    profile=str(body.get("profile") or "").strip() or None,
                )
                performer = db.conn.execute("SELECT * FROM performers WHERE id=?", (pid,)).fetchone()
            now=config.now_iso()
            db.conn.execute("DELETE FROM video_performers WHERE video_id=?", (media["id"],))
            db.conn.execute("DELETE FROM local_video_performers WHERE video_id=?", (media["id"],))
            db.conn.execute("INSERT OR IGNORE INTO local_video_performers(video_id,performer_id,created_at) VALUES(?,?,?)", (media["id"], performer["id"], now))
            db.conn.execute("INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)", (media["id"],"performer",1,"manual mapping",now))
            db.conn.commit()
        from ..core.resource_registry import ensure_unknown_performer_local_profile, mark_media_entity_complete
        await asyncio.to_thread(ensure_unknown_performer_local_profile, db)
        mark_media_entity_complete(db, [int(media["id"])])
        caption_sync = await _sync_catalogue_caption(int(media["id"]))
        db.log(f"Local performer mapping: {performer['name']} -> channel {channel_id} message {message_id}")
        return {"ok": True, "performer": dict(performer), "local": True, "caption_sync": caption_sync}

    @router.post("/api/local/site-map")
    async def local_site_map(request: Request):
        """POST /api/local/site-map: Explicit manual site mapping for one video."""
        require_auth(request)
        body = await request.json()
        channel_id = str(body.get("channel_id") or "").strip()
        try:
            message_id = int(body.get("message_id") or 0)
        except (TypeError, ValueError):
            message_id = 0
        name = str(body.get("name") or body.get("target_name") or "").strip()
        action = str(body.get("action") or "move").strip().lower()
        if not channel_id or not message_id:
            raise HTTPException(400, "channel_id and message_id are required")
        if action != "unknown" and not name:
            raise HTTPException(400, "site name is required")

        with db.lock:
            media = db.conn.execute(
                "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                (channel_id, message_id)).fetchone()
            if not media:
                raw = db.setting(f"video_index_v2_{channel_id}", {}) or db.setting(f"video_index_{channel_id}", {}) or {}
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except Exception:
                        raw = {}
                item = raw.get(str(message_id)) or raw.get(message_id) or {} if isinstance(raw, dict) else {}
                if not item:
                    raise HTTPException(404, "Video is not available in the local index")
                db.conn.execute(
                    "INSERT OR IGNORE INTO media(source_channel,source_message_id,filename,title,size,duration,width,height,streamable,upload_date,created_at,updated_at,status) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (channel_id, message_id, item.get("name") or f"video_{message_id}.mp4",
                     item.get("name") or f"video_{message_id}.mp4", item.get("size") or 0,
                     item.get("duration") or 0, item.get("width") or 0, item.get("height") or 0,
                     1 if item.get("streamable") else 0, item.get("date") or "",
                     config.now_iso(), config.now_iso(), "LOCAL"))
                media = db.conn.execute(
                    "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                    (channel_id, message_id)).fetchone()
            vid = int(media["id"])
            old_row = db.conn.execute(
                "SELECT GROUP_CONCAT(s.name) x FROM video_studios v JOIN studios s ON s.id=v.studio_id WHERE v.video_id=?",
                (vid,)).fetchone()
            old = str(old_row["x"] or "")
            db.conn.execute("DELETE FROM video_studios WHERE video_id=?", (vid,))
            now = config.now_iso()
            if action == "unknown":
                studio = db.conn.execute("SELECT * FROM studios WHERE name='Unknown Studio' LIMIT 1").fetchone()
                if studio is None:
                    raise HTTPException(500, "Unknown Studio entity is unavailable")
                db.conn.execute(
                    "INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,?)",
                    (vid, int(studio["id"]), 1.0))
                new = "Unknown Studio"
                db.conn.execute(
                    "UPDATE media SET site_name=NULL, site_url=NULL, updated_at=? WHERE id=?",
                    (now, vid))
            else:
                studio = None
                site_id = body.get("site_id")
                if site_id is not None and str(site_id).isdigit():
                    studio = db.conn.execute("SELECT * FROM studios WHERE id=?", (int(site_id),)).fetchone()
                if studio is None:
                    from ..engines.entity_identity import upsert_studio
                    sid = upsert_studio(db, name)
                    studio = db.conn.execute("SELECT * FROM studios WHERE id=?", (sid,)).fetchone()
                if studio is None:
                    raise HTTPException(500, "Could not resolve selected site")
                db.conn.execute(
                    "INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,?)",
                    (vid, int(studio["id"]), 1.0))
                new = str(studio["name"] or name)
                db.conn.execute(
                    "UPDATE media SET site_name=?, updated_at=? WHERE id=?",
                    (new, now, vid))
            db.conn.execute(
                "INSERT INTO metadata_mapping_history(video_id,field,old_value,new_value,action,created_at) VALUES(?,?,?,?,?,?)",
                (vid, "site", old, new, "manual", now))
            db.conn.execute(
                "INSERT OR REPLACE INTO metadata_mapping_locks(video_id,field,locked,reason,updated_at) VALUES(?,?,?,?,?)",
                (vid, "site", 1, "manual mapping", now))
            db.conn.commit()
        from ..core.resource_registry import mark_media_entity_complete
        mark_media_entity_complete(db, [vid])
        caption_sync = await _sync_catalogue_caption(vid)
        db.log(f"Local site mapping: {new} -> channel {channel_id} message {message_id}")
        return {"ok": True, "site": {"id": int(studio["id"]), "name": new} if studio else None, "local": True, "caption_sync": caption_sync}

    # ─── Metadata match suggestions (Claude engineering prompt §5-8) ───
    # Only HIGH-confidence provider matches are auto-linked (page_enricher.py);
    # everything else lands here as PENDING for the operator to Confirm/Reject.
    @router.post("/api/media/quick-edit/lookup")
    async def media_quick_edit_lookup(request: Request):
        """Fast, read-only provider lookup for the inline video quick editor.

        Searches the actual Telegram filename plus extracted and manually entered
        clues. This preserves order/date evidence instead of building a synthetic
        filename that duplicates fields or drops the original release structure.
        Interactive mode uses a bounded, concurrent first-pass search and falls
        back to ThePornDB only when the StashDB candidates need it.
        """
        require_auth(request)
        body = await request.json()
        try:
            channel_id = str(body.get("channel_id") or "").strip()
            message_id = int(body.get("message_id") or 0)
        except (TypeError, ValueError):
            channel_id, message_id = "", 0
        title = str(body.get("title") or "").strip()
        performer = str(body.get("performer") or "").strip()
        studio = str(body.get("studio") or "").strip()
        performer_query = str(body.get("performer_query") or "").strip()
        studio_query = str(body.get("studio_query") or "").strip()
        if not channel_id or not message_id:
            raise HTTPException(400, "channel_id and message_id are required")
        if not any(len(x) >= 2 for x in (title, performer, studio)):
            return {"ok": True, "matches": []}
        with db.lock:
            media = db.conn.execute(
                "SELECT id,filename,oshash FROM media WHERE source_channel=? AND source_message_id=?",
                (channel_id, message_id),
            ).fetchone()
        if not media:
            raise HTTPException(404, "Video not found")
        from ..metadata.providers import identify
        from ..metadata.filename_matching import extract_filename_parameters
        filename = str(media["filename"] or "").strip() or title or f"telegram_{message_id}.mp4"
        hints = extract_filename_parameters(db, filename)

        def _quick_key(value: object) -> str:
            leaf = re.split(r"[/\\]", str(value or ""))[-1]
            leaf = re.sub(r"(?i)\.(mp4|m4v|mkv|mov|avi|wmv|webm|ts|flv|mpg|mpeg)$", "", leaf)
            return re.sub(r"[^a-z0-9]+", "", leaf.casefold())

        filename_leaf = re.split(r"[/\\]", filename)[-1]
        title_is_filename = bool(title and _quick_key(title) == _quick_key(filename_leaf))
        # In the editor the Title field often initially contains the unchanged
        # Telegram filename. Use the cleaned title clue in that case, while an
        # explicitly edited title remains the strongest query clue.
        if title_is_filename:
            parsed_titles = hints.get("title_search_variants") or []
            search_title = str((parsed_titles[0] if parsed_titles else hints.get("title")) or "").strip()
        else:
            search_title = title
        if not search_title:
            variants = hints.get("title_search_variants") or []
            search_title = str((variants[0] if variants else hints.get("title")) or "").strip()
        selected_performer = str(body.get("performer_selected") or performer or "").strip()
        selected_studio = str(body.get("studio_selected") or studio or "").strip()
        performer_selected_explicit = bool(body.get("performer_selected_explicit"))
        studio_selected_explicit = bool(body.get("studio_selected_explicit"))
        selected_performers = [str(x or "").strip() for x in (body.get("performers") or []) if str(x or "").strip()]
        if performer_query:
            search_performers = [performer_query]
        elif performer_selected_explicit and selected_performers:
            search_performers = selected_performers
        elif selected_performers and not (hints.get("performers") or hints.get("performer_candidates")):
            search_performers = selected_performers
        else:
            search_performers = []
        has_filename_performer_clue = bool(hints.get("performers") or hints.get("performer_candidates"))
        has_filename_studio_clue = bool(hints.get("studio") or hints.get("studio_candidates"))
        context = {
            "_quick_lookup": True,
            "_quick_lookup_user_performer": bool(performer_query or performer_selected_explicit),
            "_quick_lookup_user_studio": bool(studio_query or studio_selected_explicit),
            "title": search_title,
            "titles": [search_title] if search_title else [],
            "performers": search_performers,
            "performer_names": ([selected_performer] if selected_performer and not performer_query
                                and not performer_selected_explicit and not has_filename_performer_clue else []),
            "studios": ([studio_query] if studio_query else
                        ([selected_studio] if selected_studio and studio_selected_explicit else [])),
            "studio": (selected_studio if selected_studio and not studio_query
                       and not studio_selected_explicit and not has_filename_studio_clue else ""),
            "date": hints.get("date"),
            "dates": [hints["date"]] if hints.get("date") else [],
            "year": hints.get("year"),
            "release_date": hints.get("date") or hints.get("year"),
        }
        # Do not use OSHASH here: the editor is often changing title/entity
        # clues, and an exact hash could return a stale or irrelevant title record.
        result = await identify(db, oshash=None, filename=filename, context=context, filename_parameters=hints)
        from ..engines.entity_identity import mapped_video_entity_context
        from ..metadata.suggestions import candidate_mapping_conflicts
        saved_mapping_context = mapped_video_entity_context(db, int(media["id"]))
        mapping_filter_fields = set()
        if saved_mapping_context.get("performer_names") and not (performer_query or performer_selected_explicit):
            mapping_filter_fields.add("performer")
        if saved_mapping_context.get("studio_names") and not (studio_query or studio_selected_explicit):
            mapping_filter_fields.add("studio")
        matches = []
        filtered_count = 0
        for match in result.get("matches") or []:
            if not isinstance(match, dict):
                continue
            if candidate_mapping_conflicts(
                db, int(media["id"]), match, mapping_context=saved_mapping_context,
                fields=mapping_filter_fields,
            ):
                filtered_count += 1
                continue
            row = dict(match)
            row["confidence"] = row.get("confidence") or "provider filename match — confirm"
            matches.append(row)
        # Keep this endpoint read-only even if provider services return an empty result.
        filename_hints = {key: hints.get(key) for key in (
            "title", "title_search_variants", "performers", "performer_candidates", "studio",
            "studio_candidates", "date", "year", "parameters_found",
        )}
        return {"ok": True, "matches": matches[:8], "method": result.get("method"),
                "filename_hints": filename_hints, "search_title": search_title,
                "filtered_count": filtered_count,
                "mapping_filters": [
                    label for field, label in (("performer", "confirmed performer"),
                                               ("studio", "confirmed studio"))
                    if field in mapping_filter_fields
                ],
                "mapping_filter_names": {
                    "performer": saved_mapping_context.get("performer_names", []) if "performer" in mapping_filter_fields else [],
                    "studio": saved_mapping_context.get("studio_names", []) if "studio" in mapping_filter_fields else [],
                },
                "errors": result.get("errors") or []}
