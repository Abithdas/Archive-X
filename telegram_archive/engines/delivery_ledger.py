"""Durable Telegram delivery history and Library registration.

A Telegram upload is not complete for Archive X until the destination message is
verified, recorded in the delivery ledger, and represented as a normal Library
media row. The ledger is intentionally separate from the live Library projection
so delivery history survives metadata edits or later channel scans.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Schema and lookups
#   - Preview asset copying
#   - Recording deliveries
#   - Repair and reconciliation
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
from pathlib import Path
from typing import Any




# ==============================================================================
# SECTION: Schema and lookups
# Ledger schema and media/relationship helpers.
# ==============================================================================

def ensure_delivery_ledger_schema(db) -> None:
    """Create the additive, idempotent delivery-history schema."""
    with db.lock:
        db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS archive_x_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_kind TEXT NOT NULL DEFAULT 'unknown',
            source_channel TEXT NOT NULL DEFAULT '',
            source_message_id INTEGER NOT NULL DEFAULT 0,
            source_media_id INTEGER,
            destination_channel TEXT NOT NULL,
            destination_video_message_id INTEGER NOT NULL,
            destination_thumbnail_message_id INTEGER NOT NULL DEFAULT 0,
            identity_key TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            fps REAL NOT NULL DEFAULT 0,
            bitrate INTEGER NOT NULL DEFAULT 0,
            codec TEXT NOT NULL DEFAULT '',
            audio_codec TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '',
            action TEXT NOT NULL DEFAULT 'TRANSFER',
            status TEXT NOT NULL DEFAULT 'VERIFIED',
            verified_at TEXT NOT NULL,
            library_media_id INTEGER,
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(destination_channel, destination_video_message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ax_deliveries_source ON archive_x_deliveries(source_channel, source_message_id);
        CREATE INDEX IF NOT EXISTS idx_ax_deliveries_identity ON archive_x_deliveries(identity_key);
        CREATE INDEX IF NOT EXISTS idx_ax_deliveries_verified ON archive_x_deliveries(status, verified_at DESC);
        """)
        db.conn.commit()


def _now() -> str:
    from .. import config
    return config.now_iso()


def _table_exists(db, name: str) -> bool:
    return db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _media_columns(db) -> set[str]:
    return {str(row[1]) for row in db.conn.execute("PRAGMA table_info(media)").fetchall()}


def _find_source_media(db, *, source_media_id: int | None, source_channel: str,
                       source_message_id: int, identity_key: str):
    if source_media_id:
        row = db.conn.execute("SELECT * FROM media WHERE id=?", (int(source_media_id),)).fetchone()
        if row:
            return row
    if source_channel and source_message_id:
        row = db.conn.execute(
            "SELECT * FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (str(source_channel), int(source_message_id)),
        ).fetchone()
        if row:
            return row
        # Older Content Automation jobs incorrectly saved the media table's
        # primary key in transfer_locations.source_message_id. Repair that form.
        if str(source_channel) == "content_automation":
            row = db.conn.execute("SELECT * FROM media WHERE id=? LIMIT 1", (int(source_message_id),)).fetchone()
            if row:
                return row
    key = str(identity_key or "").strip()
    if key:
        row = db.conn.execute(
            "SELECT * FROM media WHERE (oshash=? OR identity_key=?) "
            "AND COALESCE(status,'')<>'SOURCE_DELETED' "
            "ORDER BY CASE WHEN source_channel='content_automation' THEN 0 ELSE 1 END,id DESC LIMIT 1",
            (key, key),
        ).fetchone()
        if row:
            return row
    return None


def _copy_relationships(db, source_media_id: int, target_media_id: int, now: str) -> None:
    if not source_media_id or source_media_id == target_media_id:
        return
    copies = (
        ("video_performers", "video_id,performer_id,confidence", "?,performer_id,confidence"),
        ("video_studios", "video_id,studio_id,confidence", "?,studio_id,confidence"),
        ("video_tags", "video_id,tag_id,confidence", "?,tag_id,confidence"),
    )
    for table, target_cols, select_cols in copies:
        if _table_exists(db, table):
            db.conn.execute(
                f"INSERT OR IGNORE INTO {table}({target_cols}) SELECT {select_cols} FROM {table} WHERE video_id=?",
                (int(target_media_id), int(source_media_id)),
            )
    if _table_exists(db, "local_video_performers"):
        db.conn.execute(
            "INSERT OR IGNORE INTO local_video_performers(video_id,performer_id,created_at) "
            "SELECT ?,performer_id,created_at FROM local_video_performers WHERE video_id=?",
            (int(target_media_id), int(source_media_id)),
        )





# ==============================================================================
# SECTION: Preview asset copying
# Copies preview frames to the destination record.
# ==============================================================================

def _ensure_destination_preview_asset(
    db, *, source_row, source_media_id: int | None, destination_channel: str,
    destination_video_message_id: int, destination_thumbnail_message_id: int,
    library_media_id: int, now: str,
) -> bool:
    """Attach the already-prepared representative snapshot to a delivery record.

    Metadata and playback snapshots are prepared before upload. For historical
    deliveries, reuse that existing asset under the real Telegram
    (channel,message) identity instead of rerunning the metadata/snapshot pipeline.
    """
    entity_id = f"{destination_channel}:{int(destination_video_message_id)}"
    with db.lock:
        target_asset = db.conn.execute(
            "SELECT path,size_bytes FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=? "
            "AND asset_type='PLAYBACK_REPRESENTATIVE' AND state='READY' LIMIT 1", (entity_id,),
        ).fetchone() if _table_exists(db, "asset_registry") else None
        target_cache = db.conn.execute(
            "SELECT mime,width,height,data,visual_hash FROM thumbnail_cache WHERE source_channel=? "
            "AND video_message_id=? AND data IS NOT NULL LIMIT 1",
            (str(destination_channel), int(destination_video_message_id)),
        ).fetchone() if _table_exists(db, "thumbnail_cache") else None
        if target_asset and target_cache:
            return True

        source_cache = None
        source_asset = None
        src = dict(source_row) if source_row else {}
        src_channel = str(src.get("source_channel") or "")
        src_message_id = int(src.get("source_message_id") or 0)
        if not target_cache and src_channel and src_message_id > 0 and _table_exists(db, "thumbnail_cache"):
            source_cache = db.conn.execute(
                "SELECT mime,width,height,data,visual_hash FROM thumbnail_cache WHERE source_channel=? "
                "AND video_message_id=? AND data IS NOT NULL "
                "ORDER BY CASE WHEN source='playback-snapshot' THEN 0 ELSE 1 END LIMIT 1",
                (src_channel, src_message_id),
            ).fetchone()
        if not target_asset and _table_exists(db, "asset_registry"):
            if source_media_id:
                source_asset = db.conn.execute(
                    "SELECT path,size_bytes FROM asset_registry WHERE media_id=? AND asset_type='PLAYBACK_REPRESENTATIVE' "
                    "AND state='READY' ORDER BY updated_at DESC LIMIT 1", (int(source_media_id),),
                ).fetchone()
            if not source_asset and src_channel and src_message_id > 0:
                source_asset = db.conn.execute(
                    "SELECT path,size_bytes FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=? "
                    "AND asset_type='PLAYBACK_REPRESENTATIVE' AND state='READY' LIMIT 1",
                    (f"{src_channel}:{src_message_id}",),
                ).fetchone()

        if not target_cache and target_asset and not source_asset:
            source_asset = target_asset
        cache_to_copy = target_cache or source_cache
        data = bytes(cache_to_copy["data"]) if cache_to_copy and cache_to_copy["data"] is not None else b""
        mime = str((cache_to_copy["mime"] if cache_to_copy else "") or "image/jpeg")
        width = int((cache_to_copy["width"] if cache_to_copy else 0) or 0)
        height = int((cache_to_copy["height"] if cache_to_copy else 0) or 0)
        visual_hash = str((cache_to_copy["visual_hash"] if cache_to_copy else "") or "")
        source_asset_path = str((source_asset["path"] if source_asset else "") or "")
        if not data and source_asset_path:
            try:
                candidate = Path(source_asset_path).expanduser()
                if candidate.is_file():
                    data = candidate.read_bytes()
                    suffix = candidate.suffix.lower()
                    mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
            except OSError:
                data = b""

        # If the source snapshot is available, materialize it under the actual
        # Telegram identity. Keep the binary cache and resource registry aligned.
        target_path = ""
        if data:
            try:
                from .. import config as app_config
                from .thumbnail_cache import _asset_suffix, _write_bytes_atomic
                suffix = _asset_suffix(mime)
                target_file = app_config.THUMBNAIL_DIR / str(destination_channel) / f"{int(destination_video_message_id)}{suffix}"
                _write_bytes_atomic(target_file, data)
                target_path = str(target_file)
            except Exception as exc:
                try:
                    db.log(f"Delivery snapshot disk copy deferred for {entity_id}: {type(exc).__name__}: {exc}", "WARN")
                except Exception:
                    _swallowed_note("delivery_ledger._ensure_destination_preview_asset")
                    pass
            if _table_exists(db, "thumbnail_cache"):
                db.conn.execute(
                    "INSERT OR REPLACE INTO thumbnail_cache(source_channel,video_message_id,thumbnail_message_id,mime,width,height,data,source,visual_hash,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (str(destination_channel), int(destination_video_message_id), int(destination_thumbnail_message_id or 0) or None,
                     mime, width, height, data, "playback-snapshot", visual_hash, now),
                )
            target_cache = True
        if not target_path and target_asset:
            target_path = str(target_asset["path"] or "")
        if not target_path and source_asset_path and Path(source_asset_path).expanduser().is_file():
            target_path = source_asset_path
        if target_path and _table_exists(db, "asset_registry"):
            size_bytes = len(data) if data else int((source_asset["size_bytes"] if source_asset else 0) or 0)
            db.conn.execute(
                "INSERT INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,size_bytes,state,content_revision,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,'READY',1,?,?) "
                "ON CONFLICT(entity_type,entity_id,asset_type) DO UPDATE SET media_id=excluded.media_id,path=excluded.path,"
                "size_bytes=excluded.size_bytes,state='READY',updated_at=excluded.updated_at",
                (int(library_media_id), "telegram_video", entity_id, "PLAYBACK_REPRESENTATIVE", target_path, size_bytes, now, now),
            )
        # Existing destination cache/asset rows count only when their counterpart
        # can actually satisfy the Library's published-media predicate.
        asset_ok = db.conn.execute(
            "SELECT 1 FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=? "
            "AND asset_type='PLAYBACK_REPRESENTATIVE' AND state='READY' LIMIT 1", (entity_id,),
        ).fetchone() if _table_exists(db, "asset_registry") else None
        cache_ok = db.conn.execute(
            "SELECT 1 FROM thumbnail_cache WHERE source_channel=? AND video_message_id=? AND data IS NOT NULL LIMIT 1",
            (str(destination_channel), int(destination_video_message_id)),
        ).fetchone() if _table_exists(db, "thumbnail_cache") else None
        return bool(asset_ok and cache_ok)


def _frame_directory_is_ready(directory: Path) -> bool:
    try:
        from .preview_prefetch import frame_quality_sync
        return bool(directory.is_dir() and frame_quality_sync(directory).get("ready"))
    except Exception:
        return False


def _source_frame_directories(db, *, source_row, identity_key: str, file_path: Path | None) -> list[Path]:
    """Find already-generated individual frames; this never extracts new frames."""
    candidates: list[Path] = []
    try:
        from .thumbnail_cache import local_playback_snapshot_frames_path
        if file_path is not None:
            candidates.append(local_playback_snapshot_frames_path(Path(file_path)))
    except Exception:
        _swallowed_note("delivery_ledger._source_frame_directories")
        pass

    src = dict(source_row) if source_row else {}
    src_channel = str(src.get("source_channel") or "")
    src_message_id = int(src.get("source_message_id") or 0)
    if src_channel and src_message_id > 0:
        try:
            if _table_exists(db, "timeline_preview_cache"):
                row = db.conn.execute(
                    "SELECT frames_path FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=? LIMIT 1",
                    (src_channel, src_message_id),
                ).fetchone()
                if row and str(row["frames_path"] or "").strip():
                    candidates.append(Path(str(row["frames_path"])))
            if str(src_channel).lstrip("-").isdigit():
                from .preview_prefetch import preview_frames_path
                candidates.append(preview_frames_path(int(src_channel), src_message_id))
        except Exception:
            _swallowed_note("delivery_ledger._source_frame_directories")
            pass

    key = str(identity_key or src.get("oshash") or src.get("identity_key") or "").strip().lower()
    if key and _table_exists(db, "local_playback_snapshots"):
        try:
            rows = db.conn.execute(
                "SELECT frames_path FROM local_playback_snapshots WHERE lower(source_oshash)=? "
                "AND COALESCE(frames_path,'')<>'' ORDER BY updated_at DESC LIMIT 5", (key,),
            ).fetchall()
            candidates.extend(Path(str(row["frames_path"])) for row in rows if str(row["frames_path"] or "").strip())
        except Exception:
            _swallowed_note("delivery_ledger._source_frame_directories")
            pass
    # Preserve order while avoiding duplicate checks of the same directory.
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = str(candidate.expanduser().resolve(strict=False))
        except Exception:
            resolved = str(candidate)
        if resolved not in seen:
            seen.add(resolved)
            unique.append(Path(resolved))
    return unique


def copy_delivery_preview_frames(
    db, *, destination_channel: str | int, destination_video_message_id: int,
    source_row=None, identity_key: str = "", file_path: str | Path | None = None,
    duration: float = 0, width: int = 0, height: int = 0,
) -> bool:
    """Attach the source's 36 independent frames to the destination Telegram identity.

    The hover carousel/player grid reads the destination-keyed preview directory,
    not the representative poster or the Telegram contact-sheet message. Copying
    is local-only and happens before temporary local files are cleaned up.
    """
    from .preview_prefetch import (
        FRAME_COUNT, PREVIEW_VERSION, frame_quality_sync, frame_times,
        preview_frames_path, representative_frame_path,
    )
    import json
    import os
    import shutil

    channel = str(destination_channel)
    message_id = int(destination_video_message_id or 0)
    if not channel or message_id <= 0 or not channel.lstrip("-").isdigit():
        return False
    target = preview_frames_path(int(channel), message_id)
    target_rep = representative_frame_path(int(channel), message_id)
    target_ready = _frame_directory_is_ready(target)
    source = None
    if not target_ready:
        try:
            candidate_path = Path(file_path).expanduser() if file_path else None
        except Exception:
            candidate_path = None
        for candidate in _source_frame_directories(
            db, source_row=source_row, identity_key=identity_key, file_path=candidate_path
        ):
            if _frame_directory_is_ready(candidate):
                source = candidate
                break
        if source is not None:
            building = target.with_name(target.name + ".building")
            backup = target.with_name(target.name + ".old")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.rmtree(building, ignore_errors=True)
                shutil.copytree(source, building)
                if not _frame_directory_is_ready(building):
                    raise RuntimeError("copied playback frames failed quality verification")
                shutil.rmtree(backup, ignore_errors=True)
                if target.exists():
                    os.replace(str(target), str(backup))
                try:
                    os.replace(str(building), str(target))
                except Exception:
                    if backup.exists() and not target.exists():
                        os.replace(str(backup), str(target))
                    raise
                shutil.rmtree(backup, ignore_errors=True)
                target_ready = True
            except Exception as exc:
                shutil.rmtree(building, ignore_errors=True)
                try:
                    db.log(
                        f"Delivery timeline-frame copy deferred for {channel}/{message_id}: {type(exc).__name__}: {exc}",
                        "WARN",
                    )
                except Exception:
                    _swallowed_note("delivery_ledger.copy_delivery_preview_frames")
                    pass

    quality = frame_quality_sync(target) if target_ready else {"frame_count": 0, "ready": False}
    ready = bool(quality.get("ready") and target_rep.is_file() and target_rep.stat().st_size >= 256)
    now = _now()
    with db.lock:
        if _table_exists(db, "timeline_preview_cache"):
            # The DB schema is additive across releases; always supply the core
            # columns and include optional manifest fields only if installed.
            columns = {str(row[1]) for row in db.conn.execute("PRAGMA table_info(timeline_preview_cache)").fetchall()}
            values = {
                "source_channel": channel, "video_message_id": message_id,
                "path": str(target_rep), "status": "READY" if ready else "PENDING",
                "duration": float(duration or 0), "width": int(width or 0), "height": int(height or 0),
                "frame_count": int(quality.get("frame_count") or 0), "version": PREVIEW_VERSION,
                "error": "" if ready else "Destination playback frames or representative are missing",
                "updated_at": now,
            }
            if "frames_path" in columns:
                values["frames_path"] = str(target)
            if "frame_times" in columns:
                values["frame_times"] = json.dumps(frame_times(float(duration or 0)))
            fields = [name for name in values if name in columns]
            placeholders = ",".join("?" for _ in fields)
            updates = ",".join(f"{name}=excluded.{name}" for name in fields if name not in {"source_channel", "video_message_id"})
            db.conn.execute(
                f"INSERT INTO timeline_preview_cache({','.join(fields)}) VALUES({placeholders}) "
                f"ON CONFLICT(source_channel,video_message_id) DO UPDATE SET {updates}",
                [values[name] for name in fields],
            )
        # Only a complete, verified frame set plus the separate representative
        # satisfies snapshot readiness. A poster or 36-frame upload contact sheet
        # alone is not enough to publish a video as playback-ready.
        with db.conn:
            db.conn.execute(
                "UPDATE resource_state SET snapshot_state=?,updated_at=? "
                "WHERE media_id IN (SELECT id FROM media WHERE source_channel=? AND source_message_id=?)",
                ("READY" if ready else "PENDING", now, channel, message_id),
            )
    return ready




# ==============================================================================
# SECTION: Recording deliveries
# Records a verified delivery in the ledger and Library.
# ==============================================================================

def _canonical_destination_from_db(db, value: str | int) -> str:
    """Repair legacy raw channel IDs only when the channel table confirms the peer."""
    raw = str(value or "").strip()
    if not raw.isdigit() or not _table_exists(db, "channels"):
        return raw
    try:
        rows = db.conn.execute("SELECT CAST(telegram_id AS TEXT) AS telegram_id FROM channels").fetchall()
        matches = {str(row["telegram_id"] or "") for row in rows}
        marked = f"-100{raw}"
        if marked in matches:
            return marked
        marked_chat = f"-{raw}"
        if marked_chat in matches:
            return marked_chat
    except Exception:
        _swallowed_note("delivery_ledger._canonical_destination_from_db")
        pass
    return raw

def record_verified_delivery(
    db, *, destination_channel: str | int, destination_video_message_id: int,
    destination_thumbnail_message_id: int | None = None,
    source_channel: str | int = "local", source_message_id: int = 0,
    source_media_id: int | None = None, identity_key: str = "", filename: str = "",
    file_path: str | Path | None = None, file_size: int = 0, duration: float = 0,
    width: int = 0, height: int = 0, fps: float = 0, bitrate: int = 0,
    codec: str = "", audio_codec: str = "", caption: str = "", action: str = "TRANSFER",
) -> dict[str, Any]:
    """Record a verified Telegram post and materialize it in the Library.

    This function must be called only after the destination video/cover pair has
    been independently verified. It is idempotent for a destination message.
    """
    ensure_delivery_ledger_schema(db)
    # A delivered Telegram post inherits the preparation state of its source
    # catalogue record. Ensure the canonical publication view/state schema exists
    # before reading that source state.
    from ..core.resource_registry import ensure_resource_tables
    ensure_resource_tables(db)
    destination_channel = _canonical_destination_from_db(db, destination_channel)
    destination_video_message_id = int(destination_video_message_id or 0)
    destination_thumbnail_message_id = int(destination_thumbnail_message_id or 0)
    if not destination_channel or destination_video_message_id <= 0:
        raise ValueError("A verified delivery requires destination channel and video message ID")
    source_channel = str(source_channel or "local")
    source_message_id = int(source_message_id or 0)
    source_media_id = int(source_media_id or 0) or None
    identity_key = str(identity_key or "").strip()
    now = _now()
    meta: dict[str, Any] = {}
    meta.update({
        "duration": duration, "width": width, "height": height, "fps": fps,
        "bitrate": bitrate, "codec": codec, "audio_codec": audio_codec,
    })
    path = Path(file_path).expanduser() if file_path else None
    if path and path.is_file():
        try:
            file_size = int(file_size or path.stat().st_size)
        except OSError:
            pass
        if not filename:
            filename = path.name
    filename = str(filename or "").strip()
    file_size = int(file_size or 0)
    duration = float(duration or 0)
    width, height = int(width or 0), int(height or 0)
    fps, bitrate = float(fps or 0), int(bitrate or 0)
    codec, audio_codec = str(codec or ""), str(audio_codec or "")
    caption = str(caption or "")
    action = str(action or "TRANSFER")
    source_kind = "content_automation" if source_channel == "content_automation" else "local_upload" if source_channel in {"local", "local_files", "upload_local", "local_upload"} else "telegram_transfer"
    entity_id = f"{destination_channel}:{destination_video_message_id}"

    with db.lock:
        source_row = _find_source_media(
            db, source_media_id=source_media_id, source_channel=source_channel,
            source_message_id=source_message_id, identity_key=identity_key,
        )
        existing_media_row = db.conn.execute(
            "SELECT * FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (destination_channel, destination_video_message_id),
        ).fetchone()
        if existing_media_row:
            current = dict(existing_media_row)
            filename = filename or str(current.get("filename") or "")
            caption = caption or str(current.get("caption") or "")
            file_size = int(file_size or current.get("size") or 0)
            duration = float(duration or current.get("duration") or 0)
            width = int(width or current.get("width") or 0)
            height = int(height or current.get("height") or 0)
            fps = float(fps or current.get("fps") or 0)
            bitrate = int(bitrate or current.get("bitrate") or 0)
            codec = codec or str(current.get("codec") or "")
            audio_codec = audio_codec or str(current.get("audio_codec") or "")
            identity_key = identity_key or str(current.get("oshash") or current.get("identity_key") or "")
        if source_row:
            src = dict(source_row)
            if source_media_id is None:
                source_media_id = int(src.get("id") or 0) or None
            if not source_message_id and str(src.get("source_channel") or "") == source_channel:
                source_message_id = int(src.get("source_message_id") or 0)
            if not identity_key:
                identity_key = str(src.get("oshash") or src.get("identity_key") or "")
            filename = filename or str(src.get("filename") or "")
            if not file_size:
                file_size = int(src.get("size") or 0)
            if not duration:
                duration = float(src.get("duration") or 0)
            if not width:
                width = int(src.get("width") or 0)
            if not height:
                height = int(src.get("height") or 0)
            if not fps:
                fps = float(src.get("fps") or 0)
            if not bitrate:
                bitrate = int(src.get("bitrate") or 0)
            codec = codec or str(src.get("codec") or "")
            audio_codec = audio_codec or str(src.get("audio_codec") or "")
            caption = caption or str(src.get("caption") or "")
        if not filename:
            filename = f"Telegram video {destination_video_message_id}"
        existing = existing_media_row
        source_state = None
        if source_row:
            try:
                source_state = db.conn.execute(
                    "SELECT catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,last_reason "
                    "FROM resource_state WHERE media_id=? LIMIT 1", (int(dict(source_row).get("id") or 0),),
                ).fetchone()
            except Exception:
                source_state = None
        media_columns = _media_columns(db)
        resolution = ("4K" if height >= 2160 else "1440p" if height >= 1440 else "1080p" if height >= 1080 else "720p" if height >= 720 else f"{height}p" if height > 0 else "")
        # Clone all supported metadata fields from the prepared/scanned source,
        # then replace the Telegram identity and delivery-specific fields.
        if not existing:
            values = {key: value for key, value in (dict(source_row).items() if source_row else []) if key in media_columns and key != "id"}
            values.update({
                "source_channel": destination_channel,
                "source_message_id": destination_video_message_id,
                "filename": filename,
                "title": (str((dict(source_row).get("title") if source_row else "") or "").strip() or Path(filename).stem),
                "size": file_size, "duration": duration, "width": width, "height": height, "fps": fps,
                "bitrate": bitrate, "codec": codec, "audio_codec": audio_codec,
                "resolution_category": resolution, "streamable": 1,
                "thumbnail_message_id": destination_thumbnail_message_id or None,
                "thumbnail_status": "CACHED" if destination_thumbnail_message_id else "PENDING",
                "caption": caption, "identity_key": identity_key or None, "oshash": identity_key or None,
                "upload_date": now, "status": "DELIVERED", "created_at": now, "updated_at": now,
                "favorite": 0,
            })
            fields = [key for key in values if key in media_columns]
            db.conn.execute(
                f"INSERT OR IGNORE INTO media({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
                [values[key] for key in fields],
            )
        else:
            # Keep existing Library corrections. Fill gaps from the prepared
            # source, while updating facts that describe the actual Telegram post.
            db.conn.execute(
                "UPDATE media SET streamable=1,caption=?,thumbnail_message_id=COALESCE(?,thumbnail_message_id),"
                "thumbnail_status=CASE WHEN ? > 0 THEN 'CACHED' ELSE COALESCE(thumbnail_status,'PENDING') END,"
                "filename=CASE WHEN COALESCE(filename,'')='' THEN ? ELSE filename END,"
                "title=CASE WHEN COALESCE(title,'')='' THEN ? ELSE title END,"
                "size=CASE WHEN COALESCE(size,0)<=0 THEN ? ELSE size END,"
                "duration=CASE WHEN COALESCE(duration,0)<=0 THEN ? ELSE duration END,"
                "width=CASE WHEN COALESCE(width,0)<=0 THEN ? ELSE width END,"
                "height=CASE WHEN COALESCE(height,0)<=0 THEN ? ELSE height END,"
                "fps=CASE WHEN COALESCE(fps,0)<=0 THEN ? ELSE fps END,"
                "bitrate=CASE WHEN COALESCE(bitrate,0)<=0 THEN ? ELSE bitrate END,"
                "codec=CASE WHEN COALESCE(codec,'')='' THEN ? ELSE codec END,"
                "audio_codec=CASE WHEN COALESCE(audio_codec,'')='' THEN ? ELSE audio_codec END,"
                "identity_key=COALESCE(NULLIF(identity_key,''),?),oshash=COALESCE(NULLIF(oshash,''),?),"
                "status=CASE WHEN status='SOURCE_DELETED' THEN 'DELIVERED' ELSE status END,updated_at=? WHERE source_channel=? AND source_message_id=?",
                (caption, destination_thumbnail_message_id or None, destination_thumbnail_message_id,
                 filename, Path(filename).stem, file_size, duration, width, height, fps, bitrate, codec, audio_codec,
                 identity_key or None, identity_key or None, now, destination_channel, destination_video_message_id),
            )
        dest_row = db.conn.execute(
            "SELECT id FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
            (destination_channel, destination_video_message_id),
        ).fetchone()
        if not dest_row:
            raise RuntimeError("Verified Telegram delivery was recorded but no Library media row could be created")
        library_media_id = int(dest_row["id"])
        if source_media_id:
            _copy_relationships(db, source_media_id, library_media_id, now)
        # Reuse the snapshot prepared before delivery. Historical transfers may
        # not yet have a destination-keyed cache/asset, so remap that existing
        # preview instead of rerunning extraction.
        representative_ready = _ensure_destination_preview_asset(
            db, source_row=source_row, source_media_id=source_media_id,
            destination_channel=destination_channel,
            destination_video_message_id=destination_video_message_id,
            destination_thumbnail_message_id=destination_thumbnail_message_id,
            library_media_id=library_media_id, now=now,
        )
        frames_ready = copy_delivery_preview_frames(
            db, destination_channel=destination_channel,
            destination_video_message_id=destination_video_message_id,
            source_row=source_row, identity_key=identity_key, file_path=path,
            duration=duration, width=width, height=height,
        )

        asset = db.conn.execute(
            "SELECT asset_id FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=? "
            "AND asset_type='PLAYBACK_REPRESENTATIVE' AND state='READY' LIMIT 1", (entity_id,),
        ).fetchone() if _table_exists(db, "asset_registry") else None
        thumb = db.conn.execute(
            "SELECT 1 FROM thumbnail_cache WHERE source_channel=? AND video_message_id=? AND data IS NOT NULL LIMIT 1",
            (destination_channel, destination_video_message_id),
        ).fetchone() if _table_exists(db, "thumbnail_cache") else None
        snapshot_state = "READY" if representative_ready and asset and thumb and frames_ready else "PENDING"
        # Metadata/entity completeness must come from the media that was
        # prepared before delivery. Do not promote a destination row to READY
        # merely because Telegram accepted the upload. Missing source preparation
        # is treated conservatively as pending; the shared publication gate will
        # keep that destination out of the Library until reconciliation finishes.
        source_state_map = dict(source_state) if source_state else {}
        metadata_state = str(source_state_map.get("metadata_state") or "PENDING")
        entity_state = str(source_state_map.get("entity_state") or "PENDING")
        provider_state = str(source_state_map.get("provider_state") or "UNKNOWN")
        fingerprint_state = ("COMPLETE" if identity_key else
                             str(source_state_map.get("fingerprint_state") or "UNKNOWN"))
        db.conn.execute(
            "INSERT OR IGNORE INTO resource_state(media_id,catalogue_state,metadata_state,fingerprint_state,snapshot_state,entity_state,provider_state,last_reason,updated_at) "
            "VALUES(?,'COMPLETE',?,?,?,?,?,?,?)",
            (library_media_id, metadata_state, fingerprint_state, snapshot_state, entity_state,
             provider_state, str(source_state_map.get("last_reason") or "DELIVERY_PREPARED_STATE_INHERITED"), now),
        )
        db.conn.execute(
            "UPDATE resource_state SET catalogue_state='COMPLETE',metadata_state=?,entity_state=?,provider_state=?,"
            "fingerprint_state=?,snapshot_state=?,last_reason=?,updated_at=? WHERE media_id=?",
            (metadata_state, entity_state, provider_state, fingerprint_state, snapshot_state,
             str(source_state_map.get("last_reason") or "DELIVERY_PREPARED_STATE_INHERITED"), now, library_media_id),
        )
        if asset and _table_exists(db, "asset_registry"):
            db.conn.execute(
                "UPDATE asset_registry SET media_id=?,updated_at=? WHERE entity_type='telegram_video' AND entity_id=? "
                "AND asset_type='PLAYBACK_REPRESENTATIVE'",
                (library_media_id, now, entity_id),
            )

        # Keep the historical transfer-location ledger and the richer per-post
        # ledger in sync in one transaction. The (channel,message) pair is the
        # durable idempotency key; retries do not create duplicate Library cards.
        db.conn.execute(
            "INSERT OR REPLACE INTO transfer_locations(identity_key,source_channel,source_message_id,destination_channel,destination_message_id,destination_thumbnail_id,action,status,transferred_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (identity_key, source_channel, source_message_id, destination_channel, destination_video_message_id,
             destination_thumbnail_message_id or None, action, "VERIFIED", now),
        )
        db.conn.execute(
            "INSERT INTO archive_x_deliveries(source_kind,source_channel,source_message_id,source_media_id,destination_channel,"
            "destination_video_message_id,destination_thumbnail_message_id,identity_key,filename,file_size,duration,width,height,fps,bitrate,codec,audio_codec,caption,action,status,verified_at,library_media_id,error,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?) "
            "ON CONFLICT(destination_channel,destination_video_message_id) DO UPDATE SET "
            "source_kind=excluded.source_kind,source_channel=excluded.source_channel,source_message_id=excluded.source_message_id,"
            "source_media_id=COALESCE(excluded.source_media_id,archive_x_deliveries.source_media_id),"
            "destination_thumbnail_message_id=excluded.destination_thumbnail_message_id,identity_key=excluded.identity_key,"
            "filename=excluded.filename,file_size=excluded.file_size,duration=excluded.duration,width=excluded.width,height=excluded.height,"
            "fps=excluded.fps,bitrate=excluded.bitrate,codec=excluded.codec,audio_codec=excluded.audio_codec,caption=excluded.caption,"
            "action=excluded.action,status='VERIFIED',verified_at=excluded.verified_at,library_media_id=excluded.library_media_id,error='',updated_at=excluded.updated_at",
            (source_kind, source_channel, source_message_id, source_media_id, destination_channel, destination_video_message_id,
             destination_thumbnail_message_id, identity_key, filename, file_size, duration, width, height, fps, bitrate, codec,
             audio_codec, caption, action, "VERIFIED", now, library_media_id, now, now),
        )
        db.conn.commit()
    return {"library_media_id": library_media_id, "destination_channel": destination_channel,
            "destination_video_message_id": destination_video_message_id, "status": "VERIFIED",
            "snapshot_state": snapshot_state, "filename": filename}




# ==============================================================================
# SECTION: Repair and reconciliation
# Hides moved sources and repairs legacy rows.
# ==============================================================================

def mark_verified_transfer_source_hidden(
    db, *, source_channel: str | int, source_message_id: int,
    destination_channel: str | int, destination_message_id: int,
    source_deleted: bool = False,
) -> dict[str, int | bool | str]:
    """Hide a Telegram transfer's source only after its destination is verified.

    ``record_verified_delivery`` intentionally creates a separate destination
    media row so prepared metadata/assets can be inherited.  The source row
    must then be retired separately; otherwise both rows remain in
    ``archive_x_published_media`` and the same video appears under both
    channels.  When deletion is not yet confirmed, the destination remains the
    Library's canonical copy and the source location is marked pending deletion.
    """
    from .. import config
    from ..core.telegram_registry import ensure_telegram_registry

    ensure_delivery_ledger_schema(db)
    ensure_telegram_registry(db)
    source_channel = str(source_channel or "").strip()
    destination_channel = str(destination_channel or "").strip()
    source_message_id = int(source_message_id or 0)
    destination_message_id = int(destination_message_id or 0)
    if (not source_channel or not destination_channel or source_message_id <= 0
            or destination_message_id <= 0
            or (source_channel == destination_channel and source_message_id == destination_message_id)):
        return {"verified": False, "source_rows_hidden": 0, "locations_retired": 0, "cleanup_queued": 0}

    now = config.now_iso()
    with db.lock:
        delivery = db.conn.execute(
            "SELECT source_media_id,library_media_id FROM archive_x_deliveries "
            "WHERE source_kind='telegram_transfer' AND status='VERIFIED' "
            "AND source_channel=? AND source_message_id=? AND destination_channel=? "
            "AND destination_video_message_id=? ORDER BY id DESC LIMIT 1",
            (source_channel, source_message_id, destination_channel, destination_message_id),
        ).fetchone()
        if not delivery:
            return {"verified": False, "source_rows_hidden": 0, "locations_retired": 0, "cleanup_queued": 0}

        source_rows = db.conn.execute(
            "SELECT id,status FROM media WHERE source_channel=? AND source_message_id=?",
            (source_channel, source_message_id),
        ).fetchall()
        hidden = 0
        source_media_ids = []
        for row in source_rows:
            media_id = int(row["id"])
            source_media_ids.append(media_id)
            if str(row["status"] or "") != "SOURCE_DELETED":
                db.conn.execute(
                    "UPDATE media SET status='SOURCE_DELETED',updated_at=? WHERE id=?",
                    (now, media_id),
                )
                hidden += 1
                if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='resource_events'").fetchone():
                    db.conn.execute(
                        "INSERT INTO resource_events(media_id,event_type,reason,detail,created_at) VALUES(?,?,?,?,?)",
                        (media_id, "SOURCE_DELETED", "verified_transfer_source_hidden",
                         f"Destination {destination_channel}:{destination_message_id} was verified; source is no longer the Library copy.", now),
                    )

        location = db.conn.execute(
            "SELECT l.location_state,r.document_id FROM telegram_document_locations l "
            "LEFT JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
            "WHERE l.channel_id=? AND l.message_id=? LIMIT 1",
            (source_channel, source_message_id),
        ).fetchone()
        previous_state = str(location["location_state"] or "") if location else ""
        already_deleted = bool(source_deleted or previous_state == "MOVED_BY_ARCHIVE_X")
        target_state = "MOVED_BY_ARCHIVE_X" if already_deleted else "MOVED_PENDING_DELETE"
        locations_retired = 0
        if location and previous_state != target_state:   # already in that state: nothing to write (this ran for every verified transfer at every start)
            db.conn.execute(
                "UPDATE telegram_document_locations SET location_state=?,last_seen_at=? "
                "WHERE channel_id=? AND message_id=?",
                (target_state, now, source_channel, source_message_id),
            )
            locations_retired = 1

        cleanup_queued = 0
        document_id = str((location["document_id"] if location else "") or "")
        settled = not hidden and not locations_retired
        if settled and db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_move_cleanup'"
        ).fetchone() and db.conn.execute(
            "SELECT 1 FROM telegram_move_cleanup WHERE source_channel=? AND source_message_id=?", (source_channel, source_message_id),
        ).fetchone():
            # Reconciled before and the cleanup is already queued or done: leave it as it is (a repeat would push its retry time back each start).
            db.conn.commit()
            return {"verified": True, "source_rows_hidden": 0, "locations_retired": 0, "cleanup_queued": 0, "location_state": target_state}
        if already_deleted:
            if db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_move_cleanup'").fetchone():
                db.conn.execute(
                    "UPDATE telegram_move_cleanup SET status='DONE',last_error='',next_attempt_at=NULL,updated_at=? "
                    "WHERE source_channel=? AND source_message_id=?",
                    (now, source_channel, source_message_id),
                )
        elif document_id and db.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_move_cleanup'"
        ).fetchone():
            db.conn.execute(
                "INSERT INTO telegram_move_cleanup(source_channel,source_message_id,destination_channel,destination_message_id,"
                "document_id,retry_count,last_error,next_attempt_at,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,0,'',datetime('now','+1 minute'),'PENDING',?,?) "
                "ON CONFLICT(source_channel,source_message_id) DO UPDATE SET "
                "destination_channel=excluded.destination_channel,destination_message_id=excluded.destination_message_id,"
                "document_id=COALESCE(NULLIF(excluded.document_id,''),telegram_move_cleanup.document_id),"
                "status=CASE WHEN telegram_move_cleanup.status IN ('DONE','NEEDS_REVIEW') THEN telegram_move_cleanup.status ELSE 'PENDING' END,"
                "next_attempt_at=CASE WHEN telegram_move_cleanup.status IN ('DONE','NEEDS_REVIEW') THEN telegram_move_cleanup.next_attempt_at ELSE excluded.next_attempt_at END,"
                "updated_at=excluded.updated_at",
                (source_channel, source_message_id, destination_channel, destination_message_id,
                 document_id, now, now),
            )
            cleanup_queued = 1
        db.conn.commit()

    if hidden or locations_retired:
        try:
            db.log(
                f"Verified transfer source reconciled {source_channel}:{source_message_id} -> "
                f"{destination_channel}:{destination_message_id}; hidden={hidden}, "
                f"location_state={target_state}, cleanup_queued={cleanup_queued}",
                "INFO", event="library.transfer_source_reconciled",
            )
        except Exception:
            _swallowed_note("delivery_ledger.mark_verified_transfer_source_hidden")
            pass
    return {"verified": True, "source_rows_hidden": hidden, "locations_retired": locations_retired,
            "cleanup_queued": cleanup_queued, "location_state": target_state}


def repair_verified_transfer_source_library_rows(db) -> dict[str, int]:
    """DB-only startup repair for source rows left visible by older transfer builds."""
    from ..core.telegram_registry import mark_prepared
    ensure_delivery_ledger_schema(db)
    with db.lock:
        rows = [dict(row) for row in db.conn.execute(
            "SELECT DISTINCT source_channel,source_message_id,destination_channel,destination_video_message_id "
            "FROM archive_x_deliveries WHERE source_kind='telegram_transfer' AND status='VERIFIED' "
            "AND COALESCE(source_channel,'')<>'' AND COALESCE(destination_channel,'')<>'' "
            "AND COALESCE(source_message_id,0)>0 AND COALESCE(destination_video_message_id,0)>0 "
            "AND (source_channel<>destination_channel OR source_message_id<>destination_video_message_id) "
            "ORDER BY source_channel,source_message_id"
        ).fetchall()]
    totals = {"checked": 0, "source_rows_hidden": 0, "locations_retired": 0,
              "cleanup_queued": 0, "destinations_marked_prepared": 0}
    for row in rows:
        totals["checked"] += 1
        result = mark_verified_transfer_source_hidden(
            db, source_channel=row["source_channel"], source_message_id=int(row["source_message_id"]),
            destination_channel=row["destination_channel"], destination_message_id=int(row["destination_video_message_id"]),
            source_deleted=False,
        )
        for key in ("source_rows_hidden", "locations_retired", "cleanup_queued"):
            totals[key] += int(result.get(key) or 0)
        # Existing destination Library rows can already be completely ready
        # while their Telegram document registry still says PENDING. Repair
        # only when the persistent readiness/representative-asset gate proves
        # that preparation really is complete.
        destination_channel = str(row["destination_channel"])
        destination_message_id = int(row["destination_video_message_id"])
        try:
            with db.lock:
                # Use the same canonical publication gate as the Library. The
                # published view accepts a READY representative asset keyed by
                # destination identity (channel:message) even when its legacy
                # media_id still points at the source row. Requiring only
                # asset_registry.media_id=m.id left otherwise-ready transfers
                # permanently PENDING after a move.
                dest = db.conn.execute(
                    "SELECT m.id,m.oshash,m.video_phash FROM media m "
                    "JOIN archive_x_published_media pm ON pm.id=m.id "
                    "WHERE m.source_channel=? AND m.source_message_id=? LIMIT 1",
                    (destination_channel, destination_message_id),
                ).fetchone()
            if dest:
                # Already prepared with the same hashes: nothing to repair. mark_prepared rewrites the row and commits every time, so
                # calling it for every verified transfer made each start write (and flush to disk) thousands of rows.
                with db.lock:
                    done = db.conn.execute(
                        "SELECT 1 FROM telegram_document_locations l JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
                        "WHERE l.channel_id=? AND l.message_id=? AND r.prepared_state='PREPARED' AND COALESCE(r.prepared_json,'{}')<>'{}' "
                        "AND (?='' OR COALESCE(r.oshash,'')=?) AND (?='' OR COALESCE(r.phash,'')=?) LIMIT 1",
                        (destination_channel, destination_message_id, str(dest["oshash"] or ""), str(dest["oshash"] or ""),
                         str(dest["video_phash"] or ""), str(dest["video_phash"] or "")),
                    ).fetchone()
                if done:
                    continue
                did_mark = mark_prepared(
                    db, channel_id=destination_channel, message_id=destination_message_id,
                    prepared={"source": "verified_transfer_startup_repair", "media_id": int(dest["id"])},
                    oshash=str(dest["oshash"] or ""), phash=str(dest["video_phash"] or ""),
                )
                totals["destinations_marked_prepared"] += int(bool(did_mark))
        except Exception as exc:
            try:
                db.log(
                    f"Verified transfer destination prepared-state repair deferred for "
                    f"{destination_channel}:{destination_message_id}: {type(exc).__name__}: {exc}", "WARN"
                )
            except Exception:
                _swallowed_note("delivery_ledger.repair_verified_transfer_source_library_rows")
                pass
    return totals



def repair_legacy_channel_peer_ids(db) -> dict[str, int]:
    """Re-key legacy positive raw channel IDs to known canonical channel IDs.

    Early upload paths persisted ``entity.id`` (a raw positive channel ID), but
    the scanner, channel filters and playback cache use Telethon's marked
    ``telegram_id``. This DB-only repair is constrained to raw IDs that map to an
    existing row in ``channels``; unknown numeric IDs are left untouched.
    """
    from ..core.resource_registry import ensure_resource_tables
    from .. import config as app_config
    import os
    import shutil

    ensure_resource_tables(db)
    with db.lock:
        if not _table_exists(db, "channels"):
            return {"channels": 0, "media_rows": 0, "duplicates": 0}
        channel_ids = [str(row[0] or "").strip() for row in db.conn.execute(
            "SELECT telegram_id FROM channels WHERE COALESCE(telegram_id,'')<>''"
        ).fetchall()]
    mapping: dict[str, str] = {}
    for canonical in channel_ids:
        if canonical.startswith("-100") and canonical[4:].isdigit():
            mapping[canonical[4:]] = canonical
        elif canonical.startswith("-") and canonical[1:].isdigit():
            mapping.setdefault(canonical[1:], canonical)

    moved = duplicates = 0
    for raw, canonical in mapping.items():
        with db.lock:
            rows = [dict(row) for row in db.conn.execute(
                "SELECT id,source_message_id FROM media WHERE source_channel=? ORDER BY id", (raw,)
            ).fetchall()]
        for row in rows:
            message_id = int(row.get("source_message_id") or 0)
            media_id = int(row.get("id") or 0)
            if not media_id or message_id <= 0:
                continue
            with db.lock:
                target = db.conn.execute(
                    "SELECT id FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                    (canonical, message_id),
                ).fetchone()
                if not target:
                    db.conn.execute(
                        "UPDATE media SET source_channel=?,updated_at=? WHERE id=? AND source_channel=?",
                        (canonical, app_config.now_iso(), media_id, raw),
                    )
                    moved += 1
                else:
                    target_id = int(target["id"])
                    _copy_relationships(db, media_id, target_id, app_config.now_iso())
                    db.conn.execute(
                        "UPDATE media SET status='SOURCE_DELETED',updated_at=? WHERE id=?",
                        (app_config.now_iso(), media_id),
                    )
                    duplicates += 1

                # Move per-message cache keys if the canonical target is absent;
                # otherwise discard only the duplicate old key.
                for table, message_col in (("thumbnail_cache", "video_message_id"),
                                           ("timeline_preview_cache", "video_message_id")):
                    if not _table_exists(db, table):
                        continue
                    canonical_exists = db.conn.execute(
                        f"SELECT 1 FROM {table} WHERE source_channel=? AND {message_col}=? LIMIT 1",
                        (canonical, message_id),
                    ).fetchone()
                    if canonical_exists:
                        db.conn.execute(
                            f"DELETE FROM {table} WHERE source_channel=? AND {message_col}=?",
                            (raw, message_id),
                        )
                    else:
                        db.conn.execute(
                            f"UPDATE {table} SET source_channel=? WHERE source_channel=? AND {message_col}=?",
                            (canonical, raw, message_id),
                        )

                old_asset_id = f"{raw}:{message_id}"
                new_asset_id = f"{canonical}:{message_id}"
                if _table_exists(db, "asset_registry"):
                    has_new_asset = db.conn.execute(
                        "SELECT 1 FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=? LIMIT 1",
                        (new_asset_id,),
                    ).fetchone()
                    if has_new_asset:
                        db.conn.execute(
                            "DELETE FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=?",
                            (old_asset_id,),
                        )
                    else:
                        db.conn.execute(
                            "UPDATE asset_registry SET entity_id=?,updated_at=? "
                            "WHERE entity_type='telegram_video' AND entity_id=?",
                            (new_asset_id, app_config.now_iso(), old_asset_id),
                        )
                # Cache files are derivable from the DB but copying/moving them
                # avoids regenerating already-valid frames after the ID repair.
                try:
                    from .preview_prefetch import preview_frames_path, representative_frame_path, frame_quality_sync
                    old_frames = preview_frames_path(int(raw), message_id)
                    new_frames = preview_frames_path(int(canonical), message_id)
                    if old_frames.is_dir() and not new_frames.exists():
                        new_frames.parent.mkdir(parents=True, exist_ok=True)
                        os.replace(str(old_frames), str(new_frames))
                    cache = db.conn.execute(
                        "SELECT status FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?",
                        (canonical, message_id),
                    ).fetchone() if _table_exists(db, "timeline_preview_cache") else None
                    if cache and _table_exists(db, "timeline_preview_cache"):
                        quality = frame_quality_sync(new_frames)
                        db.conn.execute(
                            "UPDATE timeline_preview_cache SET path=?,frames_path=?,frame_count=?,status=?,version=?,error=?,updated_at=? "
                            "WHERE source_channel=? AND video_message_id=?",
                            (str(representative_frame_path(int(canonical), message_id)), str(new_frames),
                             int(quality.get("frame_count") or 0), "READY" if quality.get("ready") else "PENDING",
                             6, "" if quality.get("ready") else "Frames need repair after channel-ID migration",
                             app_config.now_iso(), canonical, message_id),
                        )
                        db.conn.execute(
                            "UPDATE resource_state SET snapshot_state=?,updated_at=? WHERE media_id IN "
                            "(SELECT id FROM media WHERE source_channel=? AND source_message_id=?)",
                            ("READY" if quality.get("ready") else "PENDING", app_config.now_iso(), canonical, message_id),
                        )
                except Exception:
                    _swallowed_note("delivery_ledger.repair_legacy_channel_peer_ids")
                    pass

        # Recover cache/frame keys even if an earlier startup already moved
        # media.source_channel and thumbnail_cache.source_channel to canonical
        # IDs. That older migration did not know about timeline manifests or
        # preview-frame directories, so those IDs may now be orphaned.
        stranded_ids: set[int] = set()
        with db.lock:
            for table, message_col in (("thumbnail_cache", "video_message_id"),
                                       ("timeline_preview_cache", "video_message_id")):
                if _table_exists(db, table):
                    for item in db.conn.execute(
                        f"SELECT DISTINCT {message_col} AS mid FROM {table} WHERE source_channel=?", (raw,)
                    ).fetchall():
                        try:
                            stranded_ids.add(int(item["mid"]))
                        except Exception:
                            _swallowed_note("delivery_ledger.repair_legacy_channel_peer_ids")
                            pass
            if _table_exists(db, "asset_registry"):
                for item in db.conn.execute(
                    "SELECT entity_id FROM asset_registry WHERE entity_type='telegram_video' AND entity_id LIKE ?",
                    (f"{raw}:%",),
                ).fetchall():
                    try:
                        stranded_ids.add(int(str(item["entity_id"]).split(":", 1)[1]))
                    except Exception:
                        _swallowed_note("delivery_ledger.repair_legacy_channel_peer_ids")
                        pass
        # Also recover valid frame directories when their timeline DB row was
        # already canonicalized or removed by an older cleanup path.
        try:
            legacy_frame_root = Path(app_config.PLAYER_CACHE_DIR) / "preview_frames" / raw
            if legacy_frame_root.is_dir():
                for directory in legacy_frame_root.glob("*_v6.frames"):
                    try:
                        stranded_ids.add(int(directory.name.split("_v", 1)[0]))
                    except Exception:
                        _swallowed_note("delivery_ledger.repair_legacy_channel_peer_ids")
                        pass
        except Exception:
            _swallowed_note("delivery_ledger.repair_legacy_channel_peer_ids")
            pass
        for message_id in stranded_ids:
            if message_id <= 0:
                continue
            with db.lock:
                for table, message_col in (("thumbnail_cache", "video_message_id"),
                                           ("timeline_preview_cache", "video_message_id")):
                    if not _table_exists(db, table):
                        continue
                    canonical_exists = db.conn.execute(
                        f"SELECT 1 FROM {table} WHERE source_channel=? AND {message_col}=? LIMIT 1",
                        (canonical, message_id),
                    ).fetchone()
                    if canonical_exists:
                        db.conn.execute(
                            f"DELETE FROM {table} WHERE source_channel=? AND {message_col}=?",
                            (raw, message_id),
                        )
                    else:
                        db.conn.execute(
                            f"UPDATE {table} SET source_channel=? WHERE source_channel=? AND {message_col}=?",
                            (canonical, raw, message_id),
                        )
                old_asset_id = f"{raw}:{message_id}"
                new_asset_id = f"{canonical}:{message_id}"
                if _table_exists(db, "asset_registry"):
                    canonical_asset = db.conn.execute(
                        "SELECT 1 FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=? LIMIT 1",
                        (new_asset_id,),
                    ).fetchone()
                    if canonical_asset:
                        db.conn.execute(
                            "DELETE FROM asset_registry WHERE entity_type='telegram_video' AND entity_id=?",
                            (old_asset_id,),
                        )
                    else:
                        db.conn.execute(
                            "UPDATE asset_registry SET entity_id=?,updated_at=? WHERE entity_type='telegram_video' AND entity_id=?",
                            (new_asset_id, app_config.now_iso(), old_asset_id),
                        )
            try:
                from .preview_prefetch import preview_frames_path, representative_frame_path, frame_quality_sync
                old_frames = preview_frames_path(int(raw), message_id)
                new_frames = preview_frames_path(int(canonical), message_id)
                if old_frames.is_dir() and (not new_frames.is_dir() or not frame_quality_sync(new_frames).get("ready")):
                    backup = new_frames.with_name(new_frames.name + ".legacy-backup")
                    new_frames.parent.mkdir(parents=True, exist_ok=True)
                    shutil.rmtree(backup, ignore_errors=True)
                    if new_frames.exists():
                        os.replace(str(new_frames), str(backup))
                    try:
                        os.replace(str(old_frames), str(new_frames))
                        shutil.rmtree(backup, ignore_errors=True)
                    except Exception:
                        if backup.exists() and not new_frames.exists():
                            os.replace(str(backup), str(new_frames))
                        raise
                quality = frame_quality_sync(new_frames)
                with db.lock:
                    columns = {str(col[1]) for col in db.conn.execute("PRAGMA table_info(timeline_preview_cache)").fetchall()} if _table_exists(db, "timeline_preview_cache") else set()
                    if columns and db.conn.execute(
                        "SELECT 1 FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=? LIMIT 1",
                        (canonical, message_id),
                    ).fetchone():
                        assignments = []
                        values = []
                        updates = {
                            "path": str(representative_frame_path(int(canonical), message_id)),
                            "frames_path": str(new_frames),
                            "frame_count": int(quality.get("frame_count") or 0),
                            "status": "READY" if quality.get("ready") else "PENDING",
                            "version": 6,
                            "error": "" if quality.get("ready") else "Frames need repair after channel-ID migration",
                            "updated_at": app_config.now_iso(),
                        }
                        for col, value in updates.items():
                            if col in columns:
                                assignments.append(f"{col}=?")
                                values.append(value)
                        if assignments:
                            values.extend([canonical, message_id])
                            db.conn.execute(
                                "UPDATE timeline_preview_cache SET " + ",".join(assignments) + " WHERE source_channel=? AND video_message_id=?",
                                values,
                            )
                    db.conn.execute(
                        "UPDATE resource_state SET snapshot_state=?,updated_at=? WHERE media_id IN "
                        "(SELECT id FROM media WHERE source_channel=? AND source_message_id=?)",
                        ("READY" if quality.get("ready") else "PENDING", app_config.now_iso(), canonical, message_id),
                    )
            except Exception:
                _swallowed_note("delivery_ledger.repair_legacy_channel_peer_ids")
                pass

        # Canonicalize destination-history keys too, without deleting history.
        for table, channel_col, message_col in (
            ("archive_x_deliveries", "destination_channel", "destination_video_message_id"),
            ("transfer_locations", "destination_channel", "destination_message_id"),
            ("automation_delivery_fingerprints", "destination_channel", "destination_video_id"),
            ("automation_items", "destination_channel", "destination_video_id"),
        ):
            if not _table_exists(db, table):
                continue
            cols = {str(r[1]) for r in db.conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if channel_col not in cols:
                continue
            if message_col in cols and table in {"archive_x_deliveries", "automation_delivery_fingerprints"}:
                # Avoid uniqueness conflicts where a canonical delivery is already recorded.
                records = db.conn.execute(
                    f"SELECT rowid,{message_col} AS mid FROM {table} WHERE {channel_col}=?", (raw,)
                ).fetchall()
                for item in records:
                    mid = int(item["mid"] or 0)
                    collision = db.conn.execute(
                        f"SELECT rowid FROM {table} WHERE {channel_col}=? AND {message_col}=? LIMIT 1",
                        (canonical, mid),
                    ).fetchone()
                    if collision:
                        db.conn.execute(f"DELETE FROM {table} WHERE rowid=?", (int(item["rowid"]),))
                    else:
                        db.conn.execute(f"UPDATE {table} SET {channel_col}=? WHERE rowid=?", (canonical, int(item["rowid"])))
            else:
                db.conn.execute(f"UPDATE {table} SET {channel_col}=? WHERE {channel_col}=?", (canonical, raw))
        with db.lock:
            db.conn.commit()
    return {"channels": len(mapping), "media_rows": moved, "duplicates": duplicates}

def reconcile_verified_deliveries(db) -> dict[str, int]:
    """Backfill Library records and detailed history from previously verified transfers.

    This is a local database-only repair: it never re-uploads Telegram media. It
    is idempotent and safe to run again after an interrupted reconciliation.
    """
    ensure_delivery_ledger_schema(db)
    has_transfers = _table_exists(db, "transfer_locations")
    has_automation_fingerprints = _table_exists(db, "automation_delivery_fingerprints")
    if not has_transfers and not has_automation_fingerprints:
        return {"checked": 0, "registered": 0, "errors": 0}
    with db.lock:
        rows = [dict(r) for r in db.conn.execute(
            "SELECT * FROM transfer_locations WHERE status='VERIFIED' AND COALESCE(destination_channel,'')<>'' "
            "AND COALESCE(destination_message_id,0)>0 ORDER BY id"
        ).fetchall()] if has_transfers else []
        # Older Content Automation builds persisted destination ids in their own
        # fingerprint ledger after send. Include those posts even when the common
        # transfer_locations row was never written by the older child-job path.
        if has_automation_fingerprints:
            item_columns = {str(r[1]) for r in db.conn.execute("PRAGMA table_info(automation_items)").fetchall()} if _table_exists(db, "automation_items") else set()
            if "automation_items" in {str(r[0]) for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}:
                media_expr = "ai.media_id" if "media_id" in item_columns else "NULL"
                thumb_expr = "COALESCE(ai.destination_thumbnail_id,0)" if "destination_thumbnail_id" in item_columns else "0"
                caption_expr = "COALESCE(ai.caption,'')" if "caption" in item_columns else "''"
                hash_expr = "COALESCE(NULLIF(fp.raw_oshash,''),NULLIF(ai.raw_oshash,''),'')" if "raw_oshash" in item_columns else "COALESCE(NULLIF(fp.raw_oshash,''),'')"
                items_join = "LEFT JOIN automation_items ai ON ai.item_key=fp.item_key"
            else:
                media_expr, thumb_expr, caption_expr = "NULL", "0", "''"
                hash_expr = "COALESCE(NULLIF(fp.raw_oshash,''),'')"
                items_join = ""
            source_msg_expr = "COALESCE(sm.source_message_id,0)" if media_expr != "NULL" else "0"
            media_join = f"LEFT JOIN media sm ON sm.id={media_expr}" if media_expr != "NULL" else ""
            fingerprint_rows = db.conn.execute(
                "SELECT fp.destination_channel,fp.destination_video_id AS destination_message_id," + thumb_expr + " AS destination_thumbnail_id,"
                "'content_automation' AS source_channel," + source_msg_expr + " AS source_message_id," + media_expr + " AS source_media_id,"
                + hash_expr + " AS identity_key,fp.original_filename, " + caption_expr + " AS stored_caption,"
                "'CONTENT_AUTOMATION' AS action,'VERIFIED' AS status,fp.created_at AS transferred_at "
                "FROM automation_delivery_fingerprints fp " + items_join + " " + media_join + " "
                "WHERE COALESCE(fp.destination_channel,'')<>'' AND COALESCE(fp.destination_video_id,0)>0 "
                "ORDER BY fp.created_at"
            ).fetchall()
            seen = {(str(r.get("destination_channel") or ""), int(r.get("destination_message_id") or 0)) for r in rows}
            for candidate in fingerprint_rows:
                item = dict(candidate)
                key = (str(item.get("destination_channel") or ""), int(item.get("destination_message_id") or 0))
                if key not in seen:
                    rows.append(item)
                    seen.add(key)
    checked = registered = errors = 0
    for row in rows:
        checked += 1
        try:
            destination_channel = _canonical_destination_from_db(db, str(row.get("destination_channel") or ""))
            destination_id = int(row.get("destination_message_id") or 0)
            source_channel = str(row.get("source_channel") or "local")
            source_message_id = int(row.get("source_message_id") or 0)
            identity_key = str(row.get("identity_key") or "")
            source_media_id = int(row.get("source_media_id") or 0) or None
            with db.lock:
                already = db.conn.execute(
                    "SELECT library_media_id FROM archive_x_deliveries WHERE destination_channel=? AND destination_video_message_id=?",
                    (destination_channel, destination_id),
                ).fetchone()
                dest_exists = db.conn.execute(
                    "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                    (destination_channel, destination_id),
                ).fetchone()
                source_row = _find_source_media(db, source_media_id=source_media_id, source_channel=source_channel,
                                                source_message_id=source_message_id, identity_key=identity_key)
                # Historic Content Automation entries stored a media primary key
                # in source_message_id; _find_source_media already repairs it.
                source_media_id = int(source_row["id"]) if source_row else None
                if already and already["library_media_id"] and dest_exists:
                    copy_delivery_preview_frames(
                        db, destination_channel=destination_channel, destination_video_message_id=destination_id,
                        source_row=source_row, identity_key=identity_key,
                    )
                    published = db.conn.execute(
                        "SELECT 1 FROM archive_x_published_media WHERE id=? LIMIT 1",
                        (int(dest_exists["id"]),),
                    ).fetchone()
                    if published:
                        continue
                filename = str((dict(source_row).get("filename") if source_row else "") or row.get("original_filename") or "")
                if not filename and _table_exists(db, "automation_delivery_fingerprints"):
                    fp = db.conn.execute(
                        "SELECT original_filename,raw_oshash FROM automation_delivery_fingerprints WHERE destination_channel=? AND destination_video_id=? LIMIT 1",
                        (destination_channel, destination_id),
                    ).fetchone()
                    if fp:
                        filename = str(fp["original_filename"] or "")
                        identity_key = identity_key or str(fp["raw_oshash"] or "")
                if not filename and dest_exists:
                    found = db.conn.execute("SELECT filename FROM media WHERE id=?", (int(dest_exists["id"]),)).fetchone()
                    filename = str(found["filename"] or "") if found else ""
            before_id = int(dest_exists["id"]) if dest_exists else 0
            result = record_verified_delivery(
                db, destination_channel=destination_channel, destination_video_message_id=destination_id,
                destination_thumbnail_message_id=int(row.get("destination_thumbnail_id") or 0),
                source_channel=source_channel, source_message_id=source_message_id,
                source_media_id=source_media_id, identity_key=identity_key,
                filename=filename, caption=str((dict(source_row).get("caption") if source_row else "") or row.get("stored_caption") or ""),
                action=str(row.get("action") or "TRANSFER"),
            )
            library_media_id = int(result.get("library_media_id") or 0)
            with db.lock:
                catalogued = db.conn.execute(
                    "SELECT id FROM archive_x_catalogued_media WHERE id=? LIMIT 1", (library_media_id,)
                ).fetchone() if library_media_id else None
            if not catalogued:
                errors += 1
                try:
                    db.log(
                        f"Verified delivery could not be registered in the catalogue: {destination_channel}/{destination_id}",
                        "WARN"
                    )
                except Exception:
                    _swallowed_note("delivery_ledger.reconcile_verified_deliveries")
                    pass
                continue
            if not before_id and library_media_id:
                registered += 1
        except Exception as exc:
            errors += 1
            try:
                db.log(f"Delivery history reconcile failed for {row.get('destination_channel')}/{row.get('destination_message_id')}: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                _swallowed_note("delivery_ledger.reconcile_verified_deliveries")
                pass
    return {"checked": checked, "registered": registered, "errors": errors}
