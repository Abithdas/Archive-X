"""Background scanner for photo messages in selected Telegram channels.

This is deliberately separate from the video inventory. It indexes Telegram
photo media and image documents without changing or moving any Telegram posts.
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from .photo_cache import (
    cache_telegram_message, record_photo_cache, resolve_database_cache_path, photo_original_path,
)

PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif", ".gif"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_photo_message(message: Any) -> tuple[bool, str, str]:
    file_obj = getattr(message, "file", None)
    mime = _text(getattr(file_obj, "mime_type", "")).lower()
    filename = _text(getattr(file_obj, "name", ""))
    document = getattr(message, "document", None) or getattr(getattr(message, "media", None), "document", None)
    photo = getattr(message, "photo", None) or (getattr(getattr(message, "media", None), "photo", None))
    is_image_document = bool(document and (mime.startswith("image/") or Path(filename).suffix.lower() in PHOTO_EXTENSIONS))
    if photo or is_image_document:
        if not filename:
            filename = f"photo_{int(getattr(message, 'id', 0) or 0)}.jpg"
        if not mime.startswith("image/"):
            mime = mimetypes.guess_type(filename)[0] or "image/jpeg"
        if not mime.startswith("image/"):
            mime = "image/jpeg"
        return True, filename, mime
    return False, "", ""


def _message_date(message: Any) -> str:
    value = getattr(message, "date", None)
    if value is None:
        return config.now_iso()
    try:
        if getattr(value, "tzinfo", None) is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    except Exception:
        return config.now_iso()


def _save_photo(db, channel_id: str, message: Any, filename: str, mime: str) -> None:
    mid = int(getattr(message, "id", 0) or 0)
    if mid <= 0:
        return
    file_obj = getattr(message, "file", None)
    size = int(getattr(file_obj, "size", 0) or 0)
    width = int(getattr(file_obj, "width", 0) or 0)
    height = int(getattr(file_obj, "height", 0) or 0)
    caption = _text(getattr(message, "message", ""))[:4096]
    message_date = _message_date(message)
    now = config.now_iso()
    with db.lock:
        db.conn.execute(
            """INSERT INTO photo_library_items
               (channel_id,message_id,filename,caption,mime_type,size,width,height,message_date,source_kind,content_hash,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,'channel_scan','',?,?)
               ON CONFLICT(channel_id,message_id) DO UPDATE SET
                 filename=CASE WHEN photo_library_items.filename='' THEN excluded.filename ELSE photo_library_items.filename END,
                 caption=CASE WHEN excluded.caption<>'' THEN excluded.caption ELSE photo_library_items.caption END,
                 mime_type=excluded.mime_type,
                 size=CASE WHEN excluded.size>0 THEN excluded.size ELSE photo_library_items.size END,
                 width=CASE WHEN excluded.width>0 THEN excluded.width ELSE photo_library_items.width END,
                 height=CASE WHEN excluded.height>0 THEN excluded.height ELSE photo_library_items.height END,
                 message_date=COALESCE(excluded.message_date,photo_library_items.message_date),
                 source_kind=CASE WHEN photo_library_items.source_kind='local_upload' THEN 'local_upload' ELSE 'channel_scan' END,
                 updated_at=excluded.updated_at""",
            (str(channel_id), mid, filename, caption, mime, size, width, height, message_date, now, now),
        )
        db.conn.commit()


def _set_scan_state(db, channel_id: str, *, status: str, messages_scanned: int, photos_found: int, error: str = "") -> None:
    now = config.now_iso()
    with db.lock:
        db.conn.execute(
            """INSERT INTO photo_library_scan_state(channel_id,status,last_scan_at,messages_scanned,photos_found,last_error,updated_at)
               VALUES(?,?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET
                 status=excluded.status,last_scan_at=excluded.last_scan_at,messages_scanned=excluded.messages_scanned,
                 photos_found=excluded.photos_found,last_error=excluded.last_error,updated_at=excluded.updated_at""",
            (str(channel_id), str(status), now, int(messages_scanned), int(photos_found), str(error or "")[:1000], now),
        )
        db.conn.commit()


def _channel_is_fully_cached(db, channel_id: str) -> tuple[bool, int, int]:
    """Return true only when a completed scan has durable local bytes for every item."""
    with db.reading() as rdb:
        state = rdb.execute(
            "SELECT status,messages_scanned,photos_found FROM photo_library_scan_state WHERE channel_id=?",
            (str(channel_id),),
        ).fetchone()
        if not state or str(state["status"] or "").upper() != "COMPLETE":
            return False, 0, 0
        rows = rdb.execute(
            "SELECT message_id,local_path FROM photo_library_items WHERE channel_id=?",
            (str(channel_id),),
        ).fetchall()
        row_count = int(rdb.execute(
            "SELECT COUNT(*) n FROM photo_library_items WHERE channel_id=?", (str(channel_id),)
        ).fetchone()["n"] or 0)
    if row_count < int(state["photos_found"] or 0):
        return False, row_count, int(state["messages_scanned"] or 0)
    for row in rows:
        path = resolve_database_cache_path(row["local_path"]) or photo_original_path(channel_id, int(row["message_id"]))
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                return False, row_count, int(state["messages_scanned"] or 0)
        except OSError:
            return False, row_count, int(state["messages_scanned"] or 0)
    # Count DB rows rather than the last full-scan total because verified uploads
    # are inserted immediately without requiring a rescan, so row_count can grow.
    return True, row_count, int(state["messages_scanned"] or 0)


async def run(ctx) -> dict[str, Any]:
    """Scan full Telegram history once, persist image metadata and local bytes."""
    try:
        detail = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        detail = {}
    raw = detail.get("channel_ids") if isinstance(detail, dict) else []
    channel_ids = list(dict.fromkeys(_text(value) for value in (raw or []) if _text(value)))
    if not channel_ids:
        raise RuntimeError("Choose at least one Telegram channel to scan for photos.")
    client = getattr(ctx.telegram, "client", None)
    telegram_connected = bool(client is not None and getattr(ctx.telegram, "connected", False))

    total_photos = 0
    total_messages = 0
    channel_errors: list[str] = []
    ctx.report(completed=0, total=len(channel_ids), current_item="", operation="Preparing photo channel scan", force=True)
    ctx.db.log(f"Photo library scan started for {len(channel_ids)} Telegram channel(s)", "INFO",
               event="photo_library.scan_started", job_id=ctx.job_id, details={"channel_ids": channel_ids})

    for channel_index, channel_id in enumerate(channel_ids):
        await ctx.checkpoint()
        messages_scanned = 0
        photos_found = 0
        cache_failures = 0
        try:
            already_cached, cached_photos, cached_messages = await ctx.blocking(
                _channel_is_fully_cached, ctx.db, channel_id
            )
            if already_cached:
                total_photos += cached_photos
                total_messages += cached_messages
                ctx.report(completed=channel_index + 1, total=len(channel_ids), current_item=channel_id,
                           operation=f"Already indexed locally · {cached_photos:,} images", force=True)
                ctx.db.log(f"Skipped photo scan for {channel_id}: {cached_photos} images are already indexed and cached locally",
                           "INFO", event="photo_library.scan_skipped_cached", job_id=ctx.job_id,
                           details={"channel_id": channel_id, "photos_found": cached_photos})
                continue
            if not telegram_connected:
                raise RuntimeError("Telegram is disconnected and this channel is not fully indexed and cached locally")
            try:
                entity = await client.get_entity(int(channel_id))
            except (TypeError, ValueError):
                entity = await client.get_entity(channel_id)
            channel_title = _text(getattr(entity, "title", "")) or channel_id
            with ctx.db.lock:
                ctx.db.conn.execute(
                    "INSERT INTO photo_library_scan_state(channel_id,status,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(channel_id) DO UPDATE SET status=excluded.status,last_error='',updated_at=excluded.updated_at",
                    (channel_id, "SCANNING", config.now_iso()),
                )
                ctx.db.conn.commit()
            last_report = 0
            async for message in client.iter_messages(entity):
                if not message:
                    continue
                messages_scanned += 1
                mid = int(getattr(message, "id", 0) or 0)
                if mid > 0:
                    is_photo, filename, mime = _is_photo_message(message)
                    if is_photo:
                        await ctx.blocking(_save_photo, ctx.db, channel_id, message, filename, mime)
                        photos_found += 1
                        total_photos += 1
                        try:
                            cache_info = await cache_telegram_message(client, message, channel_id, mid)
                            await ctx.blocking(record_photo_cache, ctx.db, channel_id, mid, cache_info)
                        except asyncio.CancelledError:
                            raise
                        except Exception as cache_exc:
                            cache_failures += 1
                            cache_message = f"Could not cache {filename} in channel {channel_id}, message {mid}: {type(cache_exc).__name__}: {cache_exc}"
                            ctx.db.log(cache_message, "WARN", event="photo_library.cache_failed", job_id=ctx.job_id,
                                       details={"channel_id": channel_id, "message_id": mid, "filename": filename,
                                                "error": str(cache_exc)[:500]})
                if messages_scanned - last_report >= 50:
                    last_report = messages_scanned
                    total_messages += 50
                    ctx.report(completed=channel_index, total=len(channel_ids), current_item=channel_title,
                               operation=f"Scanning {channel_title} · {messages_scanned:,} messages · {photos_found:,} images",
                               force=True)
                    await ctx.checkpoint()
            total_messages += messages_scanned - last_report
            scan_status = "FAILED" if cache_failures else "COMPLETE"
            scan_error = f"{cache_failures} image(s) could not be cached locally; retry Image scan to fetch missing files" if cache_failures else ""
            _set_scan_state(ctx.db, channel_id, status=scan_status, messages_scanned=messages_scanned,
                            photos_found=photos_found, error=scan_error)
            if cache_failures:
                channel_errors.append(f"{channel_id}: {scan_error}")
            ctx.db.log(f"Photo channel scan {'completed with cache errors' if cache_failures else 'completed'}: {channel_title}; "
                       f"{messages_scanned} messages, {photos_found} image(s), {cache_failures} cache failure(s)",
                       "WARN" if cache_failures else "SUCCESS",
                       event="photo_library.channel_scan_completed_with_errors" if cache_failures else "photo_library.channel_scan_completed",
                       job_id=ctx.job_id,
                       details={"channel_id": channel_id, "messages_scanned": messages_scanned, "photos_found": photos_found,
                                "cache_failures": cache_failures})
        except asyncio.CancelledError:
            _set_scan_state(ctx.db, channel_id, status="INTERRUPTED", messages_scanned=0, photos_found=0,
                            error="Scan cancelled before this channel completed")
            raise
        except Exception as exc:
            note = f"{channel_id}: {type(exc).__name__}: {exc}"
            channel_errors.append(note)
            _set_scan_state(ctx.db, channel_id, status="FAILED", messages_scanned=locals().get("messages_scanned", 0),
                            photos_found=locals().get("photos_found", 0), error=note)
            ctx.db.log(f"Photo channel scan failed for {channel_id}: {type(exc).__name__}: {exc}", "WARN",
                       event="photo_library.channel_scan_failed", job_id=ctx.job_id,
                       details={"channel_id": channel_id, "error": note[:500]})
        ctx.report(completed=channel_index + 1, total=len(channel_ids), current_item=channel_id,
                   operation=f"Finished channel {channel_index + 1}/{len(channel_ids)}", force=True)

    summary = f"Indexed {total_photos:,} image message(s) across {len(channel_ids)} channel(s); scanned {total_messages:,} message(s)"
    if channel_errors:
        summary += f"; {len(channel_errors)} channel scan(s) failed"
    from ..core.job_results import record as record_job_result
    record_job_result(ctx.db, ctx.job_id, status="COMPLETED_WITH_ERRORS" if channel_errors else "COMPLETED",
                      progress=100, summary=summary)
    ctx.report(completed=len(channel_ids), total=len(channel_ids), progress=100, current_item="",
               operation=summary, force=True)
    return {"channels_scanned": len(channel_ids) - len(channel_errors), "channels_failed": len(channel_errors),
            "photos_found": total_photos, "messages_scanned": total_messages, "errors": channel_errors}
