"""Reconcile the local photo index against exact Telegram message IDs.

Only messages that Telegram successfully resolves as missing/empty, or whose
existing message no longer contains image media, are removed from Archive X.
RPC errors, inaccessible channels, unexpected responses, and transient failures
are retained as unverified. This is deliberately not a history rescan.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from .. import config

BATCH_SIZE = 40


def _message_id(message: Any) -> int:
    try:
        return int(getattr(message, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _is_empty_message(message: Any) -> bool:
    if message is None:
        return True
    name = type(message).__name__.casefold()
    return name in {"messageempty", "message_empty"} or bool(getattr(message, "empty", False))


def _as_messages(value: Any) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "id"):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _indexed_items(db, channel_ids: list[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in channel_ids)
    with db.reading() as conn:
        rows = conn.execute(
            f"SELECT id,channel_id,message_id,filename,local_path,source_kind FROM photo_library_items "
            f"WHERE channel_id IN ({placeholders}) ORDER BY channel_id,message_id",
            channel_ids,
        ).fetchall()
    return [dict(row) for row in rows]


def _remove_photo_index_row(db, channel_id: str, message_id: int) -> tuple[bool, set[Path]]:
    """Remove a confirmed-stale item transactionally, returning app-owned paths."""
    from .photo_cache import resolve_database_cache_path, photo_original_path, photo_thumbnail_path, photo_preview_path

    with db.reading() as conn:
        row = conn.execute(
            "SELECT id,local_path FROM photo_library_items WHERE channel_id=? AND message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
    if not row:
        return False, set()
    photo_id = int(row["id"])
    cache_paths = {photo_original_path(channel_id, message_id), photo_thumbnail_path(channel_id, message_id), photo_preview_path(channel_id, message_id)}
    stored_path = resolve_database_cache_path(str(row["local_path"] or ""))
    if stored_path is not None:
        cache_paths.add(stored_path)
    with db.reading() as conn:
        crops = conn.execute(
            "SELECT crop_path FROM photo_face_detections WHERE photo_id=? AND COALESCE(crop_path,'')<>''",
            (photo_id,),
        ).fetchall()
    for crop in crops:
        crop_path = resolve_database_cache_path(str(crop["crop_path"] or ""))
        if crop_path is not None:
            cache_paths.add(crop_path)

    with db.lock:
        current = db.conn.execute(
            "SELECT id FROM photo_library_items WHERE channel_id=? AND message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
        if not current:
            return False, set()
        photo_id = int(current["id"])
        db.conn.execute("BEGIN IMMEDIATE")
        try:
            # Capture any crop paths added between the first query and transaction.
            for crop in db.conn.execute(
                "SELECT crop_path FROM photo_face_detections WHERE photo_id=? AND COALESCE(crop_path,'')<>''",
                (photo_id,),
            ).fetchall():
                crop_path = resolve_database_cache_path(str(crop["crop_path"] or ""))
                if crop_path is not None:
                    cache_paths.add(crop_path)
            db.conn.execute("DELETE FROM photo_face_detections WHERE photo_id=?", (photo_id,))
            db.conn.execute("DELETE FROM photo_face_analysis WHERE photo_id=?", (photo_id,))
            db.conn.execute("DELETE FROM photo_library_items WHERE id=?", (photo_id,))
            db.conn.execute(
                "DELETE FROM photo_face_groups WHERE NOT EXISTS "
                "(SELECT 1 FROM photo_face_detections d WHERE d.group_id=photo_face_groups.id)"
            )
            now = config.now_iso()
            db.conn.execute(
                "UPDATE photo_library_scan_state SET photos_found=(SELECT COUNT(*) FROM photo_library_items WHERE channel_id=?),updated_at=? WHERE channel_id=?",
                (str(channel_id), now, str(channel_id)),
            )
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
    return True, cache_paths


def _unlink_managed(paths: set[Path]) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            errors.append(f"{path.name}: {type(exc).__name__}: {exc}")
    return errors


async def _message_status(client: Any, entity: Any, message_id: int) -> tuple[str, Any]:
    """Return ('image'|'stale'|'unknown', evidence); unknown is always preserved."""
    from .photo_channel_scanner import _is_photo_message

    try:
        response = await client.get_messages(entity, ids=int(message_id))
    except Exception as exc:
        return "unknown", exc
    candidates = _as_messages(response)
    if not candidates:
        return "stale", None
    message = next((candidate for candidate in candidates if _message_id(candidate) == int(message_id)), None)
    if message is None:
        return "unknown", RuntimeError("Telegram did not return the requested message ID")
    if _is_empty_message(message):
        return "stale", None
    try:
        is_photo, _filename, _mime = _is_photo_message(message)
    except Exception as exc:
        return "unknown", exc
    return ("image", message) if is_photo else ("stale", message)


async def run(ctx) -> dict[str, Any]:
    """Job entry: check stored photos against their Telegram messages."""
    try:
        detail = json.loads((ctx.job or {}).get("detail") or "{}")
    except Exception:
        detail = {}
    raw = detail.get("channel_ids") if isinstance(detail, dict) else []
    channel_ids = list(dict.fromkeys(str(v or "").strip() for v in (raw or []) if str(v or "").strip()))
    if not channel_ids:
        raise RuntimeError("Select at least one Telegram channel to reconcile deleted images.")
    client = getattr(getattr(ctx, "telegram", None), "client", None)
    if client is None or not getattr(ctx.telegram, "connected", False):
        raise RuntimeError("Connect Telegram before reconciling indexed image messages. No local items were removed.")

    rows = await ctx.blocking(_indexed_items, ctx.db, channel_ids)
    total = len(rows)
    if not total:
        ctx.report(completed=0, total=0, progress=100.0, operation="Photo reconciliation · no indexed images", force=True)
        message = "Photo reconciliation complete: no indexed images were found in the selected channels."
        ctx.db.log(message, "INFO", event="photo_library.reconcile_completed", job_id=ctx.job_id,
                   details={"channel_ids": channel_ids, "checked": 0, "removed": 0, "retained": 0, "errors": 0})
        return {"checked": 0, "removed": 0, "retained": 0, "errors": 0, "message": message}

    rows_by_channel: dict[str, list[dict[str, Any]]] = {ch: [] for ch in channel_ids}
    for row in rows:
        rows_by_channel.setdefault(str(row["channel_id"]), []).append(row)

    removed = retained = errors = checked = image_present = telegram_messages_missing = messages_no_longer_images = cache_cleanup_errors = 0
    removed_examples: list[str] = []
    ctx.report(completed=0, total=total, current_item="", operation="Photo reconciliation · checking indexed Telegram messages", force=True)
    ctx.db.log(f"Photo reconciliation started for {len(channel_ids)} channel(s), {total} indexed photo(s)", "INFO",
               event="photo_library.reconcile_started", job_id=ctx.job_id,
               details={"channel_ids": channel_ids, "indexed_photos": total})

    for channel_id in channel_ids:
        channel_rows = rows_by_channel.get(channel_id, [])
        if not channel_rows:
            continue
        await ctx.checkpoint()
        try:
            try:
                entity = await client.get_entity(int(channel_id))
            except (TypeError, ValueError):
                entity = await client.get_entity(channel_id)
            # Validate that history can be queried before treating missing IDs as
            # deletions. Failure leaves every entry in this channel untouched.
            await client.get_messages(entity, limit=1)
        except Exception as exc:
            errors += len(channel_rows)
            retained += len(channel_rows)
            checked += len(channel_rows)
            ctx.db.log(
                f"Photo reconciliation kept {len(channel_rows)} item(s) in channel {channel_id}: channel/history could not be verified ({type(exc).__name__}: {exc})",
                "WARN", event="photo_library.reconcile_channel_unverified", job_id=ctx.job_id,
                details={"channel_id": channel_id, "item_count": len(channel_rows), "error": f"{type(exc).__name__}: {exc}"[:700]},
            )
            ctx.report(completed=checked, total=total, current_item=channel_id,
                       operation=f"Reconcile photos · kept channel {channel_id} (verification unavailable)", force=True)
            continue

        for start in range(0, len(channel_rows), BATCH_SIZE):
            await ctx.checkpoint()
            batch = channel_rows[start:start + BATCH_SIZE]
            ids = [int(row["message_id"]) for row in batch]
            batch_results: dict[int, Any] = {}
            batch_error = None
            try:
                response = await client.get_messages(entity, ids=ids)
                for message in _as_messages(response):
                    mid = _message_id(message)
                    if mid in ids:
                        batch_results[mid] = message
            except Exception as exc:
                batch_error = exc

            for row in batch:
                await ctx.checkpoint()
                mid = int(row["message_id"])
                checked += 1
                state = "unknown"
                evidence: Any = None
                message = batch_results.get(mid)
                if batch_error is None and message is not None and not _is_empty_message(message):
                    from .photo_channel_scanner import _is_photo_message
                    if _message_id(message) == mid:
                        try:
                            is_photo, _filename, _mime = _is_photo_message(message)
                            state = "image" if is_photo else "stale"
                            evidence = message
                        except Exception as exc:
                            evidence = exc
                elif batch_error is None and message is not None and _is_empty_message(message):
                    state = "stale"
                else:
                    # Missing from a batch result or a failed batch is not enough
                    # evidence to delete. Re-fetch the exact ID independently.
                    state, evidence = await _message_status(client, entity, mid)

                if state == "image":
                    retained += 1
                    image_present += 1
                elif state == "stale":
                    try:
                        was_removed, paths = await ctx.blocking(_remove_photo_index_row, ctx.db, channel_id, mid)
                        if was_removed:
                            removed += 1
                            if len(removed_examples) < 20:
                                removed_examples.append(f"{channel_id}/{mid}")
                            cleanup_errors = await ctx.blocking(_unlink_managed, paths)
                            if cleanup_errors:
                                cache_cleanup_errors += len(cleanup_errors)
                                errors += 1
                                ctx.db.log(
                                    f"Photo {channel_id}/{mid} was reconciled out of the DB, but {len(cleanup_errors)} cached file(s) need cleanup",
                                    "WARN", event="photo_library.reconcile_cache_cleanup_pending", job_id=ctx.job_id,
                                    details={"channel_id": channel_id, "message_id": mid, "errors": cleanup_errors[:8]},
                                )
                            if evidence is None:
                                telegram_messages_missing += 1
                            else:
                                messages_no_longer_images += 1
                        else:
                            retained += 1
                    except Exception as exc:
                        errors += 1
                        retained += 1
                        ctx.db.log(
                            f"Photo reconciliation could not remove stale DB row for {channel_id}/{mid}: {type(exc).__name__}: {exc}",
                            "ERROR", event="photo_library.reconcile_remove_failed", job_id=ctx.job_id,
                            details={"channel_id": channel_id, "message_id": mid, "error": f"{type(exc).__name__}: {exc}"[:700]},
                        )
                else:
                    retained += 1
                    errors += 1
                    ctx.db.log(
                        f"Photo reconciliation retained {channel_id}/{mid}: Telegram response was inconclusive",
                        "WARN", event="photo_library.reconcile_unverified_item", job_id=ctx.job_id,
                        details={"channel_id": channel_id, "message_id": mid,
                                 "error": (f"{type(evidence).__name__}: {evidence}"[:500] if isinstance(evidence, Exception) else "unexpected Telegram response")},
                    )
                ctx.report(completed=checked, total=total, current_item=f"{channel_id}/{mid}",
                           operation=f"Photo reconciliation · checked {checked:,}/{total:,} · removed {removed:,}", force=(checked == total or checked % 10 == 0))

    message = (f"Photo reconciliation complete: checked {checked:,}; removed {removed:,} stale photo(s); "
               f"retained {retained:,}; errors/unverified {errors:,}.")
    result = {"checked": checked, "removed": removed, "retained": retained, "errors": errors,
              "images_still_present": image_present, "telegram_messages_missing": telegram_messages_missing,
              "messages_no_longer_images": messages_no_longer_images,
              "cache_cleanup_errors": cache_cleanup_errors, "removed_examples": removed_examples,
              "message": message}
    ctx.db.log(message, "SUCCESS" if errors == 0 else "WARN",
               event="photo_library.reconcile_completed" if errors == 0 else "photo_library.reconcile_completed_with_errors",
               job_id=ctx.job_id, details={"channel_ids": channel_ids, **result})
    ctx.report(completed=checked, total=total, progress=100.0 if total else 100.0, current_item="",
               operation=message, force=True)
    return result
