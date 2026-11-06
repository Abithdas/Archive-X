"""Shared Archive X snapshot assets and Telegram delivery contact sheets.

The 36 individual JPEG frames and their representative still are canonical for
player/library previews. Telegram delivery separately composes a 6x6 contact
sheet with filename/video-properties header from those same validated frames.
The contact sheet is never served as a player timeline sprite or library card.
Telegram original thumbnails are not reused for Archive X player previews;
Telegram delivery contact sheets are built from the validated local frame set.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Thumbnail storage
#   - Snapshot lookup and locks
#   - Local playback asset paths
#   - Contact sheets
#   - Delivery artifact cleanup
#   - Local snapshot generation
#   - Send thumbnail entry points
#   - Entity images
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import mimetypes
import threading
import weakref
from pathlib import Path
from typing import Optional

from .. import config




# ==============================================================================
# SECTION: Thumbnail storage
# Image type detection and thumbnail persistence.
# ==============================================================================

def _sniff_mime(data: bytes, name: str = "") -> str:
    n = (name or "").lower()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    # Provider artwork may be served as an SVG document even under an image URL.
    prefix = data[:4096].lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    if b"<svg" in prefix[:1024].lower() and (prefix.startswith(b"<") or prefix.startswith(b"<?xml")):
        return "image/svg+xml"
    guessed = mimetypes.guess_type(n)[0] or ""
    return guessed if guessed.startswith("image/") else "image/jpeg"




def _asset_suffix(mime: str) -> str:
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/jpeg": ".jpg",
    }.get(str(mime or "").lower(), ".jpg")


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(bytes(data))
    tmp.replace(path)


def _persist_thumbnail_assets_sync(db, channel_id: int, video_id: int, mime: str, data: bytes) -> None:
    """Keep human-readable persistent copies outside SQLite as well.

    SQLite remains the existing application cache/authority, while this asset
    tree gives upgrades and manual backups a stable folder containing the
    actual scene thumbnails and scene-level mapping information.
    """
    from .. import config as app_config
    suffix = _asset_suffix(mime)
    base = app_config.THUMBNAIL_DIR / str(channel_id)
    _write_bytes_atomic(base / f"{int(video_id)}{suffix}", data)

    with db.lock:
        row = db.conn.execute(
            "SELECT identity_key,title,filename,thumbnail_message_id FROM media "
            "WHERE source_channel=? AND source_message_id=?",
            (str(channel_id), int(video_id)),
        ).fetchone()
    identity = str((row["identity_key"] if row else "") or "").strip()
    if not identity:
        return
    scene_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    scene_dir = app_config.SCENE_ASSET_DIR / scene_id
    _write_bytes_atomic(scene_dir / f"thumbnail{suffix}", data)
    mapping = {
        "identity_key": identity,
        "source_channel": str(channel_id),
        "source_message_id": int(video_id),
        "thumbnail_message_id": int((row["thumbnail_message_id"] if row else 0) or 0),
        "title": row["title"] if row else None,
        "filename": row["filename"] if row else None,
        "thumbnail_file": str((scene_dir / f"thumbnail{suffix}").relative_to(app_config.USER_DATA_DIR)),
    }
    meta = scene_dir / "thumbnail_mapping.json"
    meta_tmp = meta.with_suffix(meta.suffix + ".tmp")
    meta_tmp.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
    meta_tmp.replace(meta)

def _thumb_ids_sync(db, channel_id: int, message_id: int):
    with db.lock:
        row = db.conn.execute(
            "SELECT source_message_id,thumbnail_message_id FROM media WHERE source_channel=? AND "
            "(source_message_id=? OR thumbnail_message_id=?) ORDER BY (source_message_id=?) DESC LIMIT 1",
            (str(channel_id), int(message_id), int(message_id), int(message_id)),
        ).fetchone()
    if row:
        return int(row["source_message_id"]), int(row["thumbnail_message_id"] or 0)
    return int(message_id), 0


def thumb_cached_info_sync(db, channel_id: int, video_id: int):
    with db.lock:
        row = db.conn.execute(
            "SELECT mime,data,source,width,height,visual_hash FROM thumbnail_cache WHERE source_channel=? AND video_message_id=?",
            (str(channel_id), int(video_id)),
        ).fetchone()
    if row and row["data"]:
        return {"data": bytes(row["data"]), "mime": row["mime"] or "image/jpeg",
                "source": str(row["source"] or ""), "width": int(row["width"] or 0),
                "height": int(row["height"] or 0), "visual_hash": str(row["visual_hash"] or "")}
    return None


def _store_thumb_sync(db, channel_id: int, video_id: int, thumbnail_message_id: int | None,
                      mime: str, width: int, height: int, data: bytes, source: str,
                      mark_snapshot: bool = True):
    from .. import config as app_config
    try:
        from ..metadata.perceptual import phash64
        visual_hash = phash64(bytes(data))
    except Exception:
        visual_hash = ""
    with db.lock:
        db.conn.execute(
            "INSERT OR REPLACE INTO thumbnail_cache(source_channel,video_message_id,thumbnail_message_id,mime,width,height,data,source,visual_hash,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(channel_id), int(video_id), thumbnail_message_id, mime, int(width), int(height), data, source, visual_hash, app_config.now_iso()),
        )
        db.conn.execute(
            "UPDATE media SET thumbnail_message_id=?,thumbnail_status='CACHED',updated_at=? "
            "WHERE source_channel=? AND source_message_id=?",
            (thumbnail_message_id, app_config.now_iso(), str(channel_id), int(video_id)),
        )
        db.conn.commit()
    from ..core.resource_registry import record_asset, sync_message_state
    try:
        # The SQLite row above is authoritative; the disk copy and asset index
        # are derived and must not fail the cache write, but are logged.
        _persist_thumbnail_assets_sync(db, channel_id, video_id, mime, bytes(data))
        path = app_config.THUMBNAIL_DIR / str(int(channel_id)) / f"{int(video_id)}{_asset_suffix(mime)}"
        record_asset(db, media_id=None, entity_type="telegram_video", entity_id=f"{int(channel_id)}:{int(video_id)}",
                     asset_type="PLAYBACK_REPRESENTATIVE", path=str(path), size_bytes=len(bytes(data)))
    except Exception as exc:
        db.log(f"Snapshot disk copy failed for {channel_id}/{video_id}: {type(exc).__name__}: {exc}", "WARN")
    if mark_snapshot:
        sync_message_state(db, channel_id, video_id, snapshot="READY", reason="LOCAL_ASSET_HIT")


# ==============================================================================
# SECTION: Snapshot lookup and locks
# Cached snapshot state and build locks.
# ==============================================================================

def playback_snapshot_cached_info_sync(db, channel_id: int, video_id: int):
    """Return only the Archive X-owned playback snapshot.

    Telegram thumbnails are deliberately excluded from this lookup. The
    playback snapshot is the canonical visual asset used by the web UI and
    Telegram delivery paths.
    """
    with db.lock:
        row = db.conn.execute(
            "SELECT mime,data,source,width,height,visual_hash FROM thumbnail_cache "
            "WHERE source_channel=? AND video_message_id=? AND source='playback-snapshot'",
            (str(channel_id), int(video_id)),
        ).fetchone()
    if row and row["data"]:
        return {"data": bytes(row["data"]), "mime": row["mime"] or "image/jpeg",
                "source": str(row["source"] or ""), "width": int(row["width"] or 0),
                "height": int(row["height"] or 0), "visual_hash": str(row["visual_hash"] or "")}
    return None


def playback_snapshot_cached_sync(db, channel_id: int, video_id: int):
    info = playback_snapshot_cached_info_sync(db, channel_id, video_id)
    return (info["data"], info["mime"]) if info else None


def playback_snapshot_state_sync(db, channel_id: int, video_id: int) -> dict:
    """Report validated independent-frame and representative snapshot readiness."""
    try:
        from .preview_prefetch import preview_frames_path, representative_frame_path, FRAME_COUNT, frame_quality_sync
        frames_dir = preview_frames_path(int(channel_id), int(video_id))
        representative_path = representative_frame_path(int(channel_id), int(video_id))
        quality = frame_quality_sync(frames_dir)
        with db.lock:
            row = db.conn.execute(
                "SELECT status,version,frame_count,frames_path,path,error,updated_at FROM timeline_preview_cache "
                "WHERE source_channel=? AND video_message_id=?",
                (str(channel_id), int(video_id)),
            ).fetchone()
        representative_ready = bool(playback_snapshot_cached_sync(db, channel_id, video_id))
        if not representative_ready and representative_path.is_file():
            try:
                from PIL import Image
                with Image.open(representative_path) as im:
                    im.verify()
                representative_ready = representative_path.stat().st_size >= 256
            except Exception:
                representative_ready = False
        frames_ready = bool(quality.get("ready"))
        return {
            "representative": representative_ready,
            "frames": int(quality.get("frame_count") or 0),
            "distinct_frames": int(quality.get("distinct_frames") or 0),
            "corrupt_frames": list(quality.get("corrupt_frames") or []),
            "frames_ready": frames_ready,
            "timeline_ready": bool(frames_ready and representative_ready),
            "state": dict(row) if row else {},
        }
    except Exception as exc:
        return {"representative": bool(playback_snapshot_cached_sync(db, channel_id, video_id)),
                "frames": 0, "distinct_frames": 0, "corrupt_frames": [], "frames_ready": False,
                "timeline_ready": False, "state": {}, "error": f"{type(exc).__name__}: {exc}"}


# One in-flight snapshot build is allowed per canonical Telegram video.
# Different videos can still progress independently; callers for the same
# video join the same per-resource critical section and re-check the cache.
_SNAPSHOT_BUILD_LOCKS: weakref.WeakValueDictionary[tuple[int, int], asyncio.Lock] = weakref.WeakValueDictionary()
_SNAPSHOT_BUILD_LOCKS_GUARD = asyncio.Lock()


async def _snapshot_build_lock(channel_id: int, video_id: int) -> asyncio.Lock:
    key = (int(channel_id), int(video_id))
    async with _SNAPSHOT_BUILD_LOCKS_GUARD:
        lock = _SNAPSHOT_BUILD_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SNAPSHOT_BUILD_LOCKS[key] = lock
        return lock


# Local-file snapshots share deterministic build directories. When Upload Local,
# Content Automation and Metadata & Assets touch the same file together, they
# must join one critical section rather than deleting/replacing each other's
# .building directory. Include the event-loop identity so independent test or
# worker loops never share an asyncio.Lock.
_LOCAL_ASSET_BUILD_LOCKS: weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock] = weakref.WeakValueDictionary()
_LOCAL_ASSET_BUILD_LOCKS_GUARD = threading.Lock()


def _local_asset_build_lock(video_path: Path) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    canonical = str(Path(video_path).expanduser().resolve(strict=False)).casefold()
    key = (id(loop), canonical)
    with _LOCAL_ASSET_BUILD_LOCKS_GUARD:
        lock = _LOCAL_ASSET_BUILD_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _LOCAL_ASSET_BUILD_LOCKS[key] = lock
        return lock


async def _ensure_playback_snapshot_unlocked(db, telegram, channel_id: int, message_id: int, *,
                                   message=None, force: bool = False, require_timeline: bool = True):
    """Ensure the representative still and required individual frame snapshots exist."""
    video_id, _ = await asyncio.to_thread(_thumb_ids_sync, db, channel_id, message_id)
    lock = await _snapshot_build_lock(channel_id, video_id)
    async with lock:
        if not force:
            state = await asyncio.to_thread(playback_snapshot_state_sync, db, channel_id, video_id)
            if state.get("representative") and (not require_timeline or state.get("timeline_ready")):
                hit = await asyncio.to_thread(playback_snapshot_cached_sync, db, channel_id, video_id)
                if hit:
                    return hit
        from .preview_prefetch import (
            MIN_FRAME_BYTES, FRAME_COUNT, REPRESENTATIVE_FRAME_INDEX, _build_snapshot_sync, _set_state,
            _store_representative_snapshot, preview_frames_path, representative_frame_path,
            frame_quality_sync,
        )
        msg = message or await telegram.client.get_messages(int(channel_id), ids=int(video_id))
        if not msg:
            raise RuntimeError("video message not found")
        from ..core.resource_registry import sync_message_state
        sync_message_state(db, channel_id, video_id, snapshot="BUILDING", reason="TELEGRAM_RANGE_REQUIRED", external_access="RANGE_ACCESS")
        frames_dir = preview_frames_path(int(channel_id), int(video_id))
        output = representative_frame_path(int(channel_id), int(video_id))
        quality = await asyncio.to_thread(frame_quality_sync, frames_dir)
        if quality.get("ready") and output.is_file() and output.stat().st_size >= MIN_FRAME_BYTES:
            await _store_representative_snapshot(db, int(channel_id), int(video_id), frames_dir)
            _set_state(db, int(channel_id), int(video_id), status="READY", path=output,
                       duration=0, frames_path=frames_dir, frame_count=quality["frame_count"])
            return await asyncio.to_thread(playback_snapshot_cached_sync, db, channel_id, video_id)

        duration = 0.0
        width = height = 0
        with db.lock:
            row = db.conn.execute(
                "SELECT duration,width,height FROM media WHERE source_channel=? AND source_message_id=?",
                (str(channel_id), int(video_id)),
            ).fetchone()
        if row:
            duration = float(row["duration"] or 0); width = int(row["width"] or 0); height = int(row["height"] or 0)
        file_obj = getattr(msg, "file", None)
        duration = duration or float(getattr(file_obj, "duration", 0) or 0)
        width = width or int(getattr(file_obj, "width", 0) or 0)
        height = height or int(getattr(file_obj, "height", 0) or 0)
        display_name = str(getattr(file_obj, "name", None) or f"video {video_id}")
        # Telegram sometimes sends non-MP4 videos as generic documents, without
        # duration/width/height attributes. The range-backed FFprobe fallback
        # in the snapshot builder can discover these from the actual media bytes.

        import os, shutil
        build_dir = frames_dir.with_name(frames_dir.name + ".building")
        tmp_rep = build_dir / f"frame_{REPRESENTATIVE_FRAME_INDEX + 1:02d}.jpg"
        shutil.rmtree(build_dir, ignore_errors=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        try:
            _set_state(db, int(channel_id), int(video_id), status="PREPARING", path=output,
                       duration=duration, width=width, height=height, frames_path=frames_dir,
                       frame_count=int(quality.get("frame_count") or 0))
            discovered_metadata = await asyncio.to_thread(
                _build_snapshot_sync, telegram.client, msg, display_name, tmp_rep, build_dir,
                duration, width, height, asyncio.get_running_loop(),
            )
            if isinstance(discovered_metadata, dict):
                duration = float(discovered_metadata.get("duration") or duration)
                width = int(discovered_metadata.get("width") or width)
                height = int(discovered_metadata.get("height") or height)
                try:
                    from .preview_prefetch import _persist_snapshot_probe_metadata_sync
                    await asyncio.to_thread(_persist_snapshot_probe_metadata_sync, db, int(channel_id), int(video_id), msg, discovered_metadata)
                except Exception as metadata_exc:
                    db.log(f"Could not persist probed video metadata {channel_id}/{video_id}: {type(metadata_exc).__name__}: {metadata_exc}", "WARN")
            built = await asyncio.to_thread(frame_quality_sync, build_dir)
            if not built.get("ready") or not tmp_rep.is_file() or tmp_rep.stat().st_size < MIN_FRAME_BYTES:
                raise RuntimeError(
                    f"snapshot quality validation failed: {built.get('frame_count')}/{FRAME_COUNT} frames, "
                    f"{built.get('distinct_frames')} distinct"
                )
            backup = frames_dir.with_name(frames_dir.name + ".old")
            shutil.rmtree(backup, ignore_errors=True)
            if frames_dir.exists():
                os.replace(str(frames_dir), str(backup))
            try:
                os.replace(str(build_dir), str(frames_dir))
            except Exception:
                if backup.exists() and not frames_dir.exists(): os.replace(str(backup), str(frames_dir))
                raise
            shutil.rmtree(backup, ignore_errors=True)
            output = representative_frame_path(int(channel_id), int(video_id))
            _set_state(db, int(channel_id), int(video_id), status="READY", path=output,
                       duration=duration, width=width, height=height, frames_path=frames_dir,
                       frame_count=built["frame_count"])
            await _store_representative_snapshot(db, int(channel_id), int(video_id), frames_dir)
            hit = await asyncio.to_thread(playback_snapshot_cached_sync, db, channel_id, video_id)
            if not hit:
                raise RuntimeError("representative playback snapshot was not persisted")
            sync_message_state(db, channel_id, video_id, snapshot="READY", reason="SNAPSHOTS_VALIDATED", external_access="RANGE_ACCESS")
            return hit
        except Exception:
            sync_message_state(db, channel_id, video_id, snapshot="FAILED", reason="TELEGRAM_RANGE_REQUIRED", external_access="RANGE_ACCESS")
            raise
        finally:
            shutil.rmtree(build_dir, ignore_errors=True)



# ==============================================================================
# SECTION: Local playback asset paths
# Validation, reuse and local asset paths.
# ==============================================================================

def _valid_image_file(path: Path) -> bool:
    try:
        from PIL import Image
        if not Path(path).is_file() or Path(path).stat().st_size < 256:
            return False
        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False


def _local_media_identity_path(video_path: Path) -> str:
    return str(Path(video_path).resolve(strict=False))


def _snapshot_frames_meet_min_width(frames_dir: Path, minimum_width: int) -> bool:
    """Return true only when all 36 cached frames meet the requested raster width."""
    try:
        from PIL import Image
        paths = [Path(frames_dir) / f"frame_{index:02d}.jpg" for index in range(1, 37)]
        if not all(path.is_file() and path.stat().st_size >= 256 for path in paths):
            return False
        for path in paths:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                if int(image.width or 0) < int(minimum_width):
                    return False
        return True
    except Exception:
        return False


def local_playback_snapshot_reusable_sync(db, video_path: Path, source_oshash: str | None) -> Path | None:
    """Return a persisted local snapshot only when it matches exact content.

    Filename/path alone is never sufficient: a replaced local file must not
    inherit the previous video's snapshot. If the same fingerprint is known at
    another local path, reuse that valid snapshot by copying it to the current
    deterministic sidecar path.
    """
    fingerprint = str(source_oshash or '').strip().lower()
    if not fingerprint:
        return None
    current = Path(video_path)
    target = local_playback_snapshot_path(current)
    canonical = _local_media_identity_path(current)
    try:
        with db.lock:
            row = db.conn.execute(
                "SELECT snapshot_path, source_size, duration, width, height, codec, fps, frame_count, frames_path, representative_path "
                "FROM local_playback_snapshots WHERE canonical_path=? AND source_oshash=?",
                (canonical, fingerprint)
            ).fetchone()
            if not row:
                row = db.conn.execute(
                    "SELECT snapshot_path, source_size, duration, width, height, codec, fps, frame_count, frames_path, representative_path "
                    "FROM local_playback_snapshots WHERE source_oshash=? ORDER BY updated_at DESC LIMIT 1",
                    (fingerprint,)
                ).fetchone()
        candidates = [Path(row[0])] if row else []
        frames_dir = local_playback_snapshot_frames_path(current)
        representative = local_playback_representative_path(current)
        frames_ready = frames_dir.is_dir() and sum(1 for _ in frames_dir.glob("frame_*.jpg")) >= 36
        row_keys = set(row.keys()) if row is not None and hasattr(row, "keys") else set()
        row_frames_path = row["frames_path"] if row and "frames_path" in row_keys else (row[8] if row and len(row) > 8 else None)
        row_rep_path = row["representative_path"] if row and "representative_path" in row_keys else (row[9] if row and len(row) > 9 else None)
        row_duration = float(row["duration"] or 0) if row and "duration" in row_keys else (float(row[2] or 0) if row and len(row) > 2 else 0.0)
        row_width = int(row["width"] or 0) if row and "width" in row_keys else (int(row[3] or 0) if row and len(row) > 3 else 0)
        row_height = int(row["height"] or 0) if row and "height" in row_keys else (int(row[4] or 0) if row and len(row) > 4 else 0)
        row_codec = str(row["codec"] or "") if row and "codec" in row_keys else (str(row[5] or "") if row and len(row) > 5 else "")
        row_fps = float(row["fps"] or 0) if row and "fps" in row_keys else (float(row[6] or 0) if row and len(row) > 6 else 0.0)
        row_frame_count = int(row["frame_count"] or 36) if row and "frame_count" in row_keys else (int(row[7] or 36) if row and len(row) > 7 else 36)
        minimum_frame_width = min(960, max(1, row_width)) if row_width > 0 else 640
        frames_quality_ok = frames_ready and _snapshot_frames_meet_min_width(frames_dir, minimum_frame_width)
        if (target.is_file() and _valid_image_file(target)
                and row_frame_count == 36 and frames_quality_ok and representative.is_file() and representative.stat().st_size > 0):
            if row and Path(row[0]).resolve(strict=False) == target.resolve(strict=False):
                return target
        for candidate in candidates:
            if candidate.is_file() and _valid_image_file(candidate) and row_frame_count == 36:
                source_frames = Path(row_frames_path) if row_frames_path else local_playback_snapshot_frames_path(candidate)
                source_rep = Path(row_rep_path) if row_rep_path else None
                source_width = row_width
                source_minimum_width = min(960, max(1, source_width)) if source_width > 0 else 640
                if not (source_frames.is_dir() and sum(1 for _ in source_frames.glob("frame_*.jpg")) >= 36
                        and _snapshot_frames_meet_min_width(source_frames, source_minimum_width)):
                    continue
                target_frames = local_playback_snapshot_frames_path(current)
                target_rep = local_playback_representative_path(current)
                if candidate.resolve(strict=False) != target.resolve(strict=False):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(candidate.read_bytes())
                import shutil
                if source_frames.resolve(strict=False) != target_frames.resolve(strict=False):
                    shutil.rmtree(target_frames, ignore_errors=True)
                    shutil.copytree(source_frames, target_frames)
                if source_rep and source_rep.is_file():
                    target_rep.write_bytes(source_rep.read_bytes())
                else:
                    target_rep.write_bytes((source_frames / "frame_19.jpg").read_bytes())
                with db.lock:
                    now = config.now_iso()
                    db.conn.execute(
                        "INSERT INTO local_playback_snapshots(canonical_path,source_oshash,snapshot_path,source_size,duration,width,height,codec,fps,frame_count,frames_path,representative_path,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_path) DO UPDATE SET source_oshash=excluded.source_oshash,snapshot_path=excluded.snapshot_path,source_size=excluded.source_size,duration=excluded.duration,width=excluded.width,height=excluded.height,codec=excluded.codec,fps=excluded.fps,frame_count=excluded.frame_count,frames_path=excluded.frames_path,representative_path=excluded.representative_path,updated_at=excluded.updated_at",
                        (canonical, fingerprint, str(target), int(current.stat().st_size if current.exists() else row[1] if row else 0), row_duration, row_width, row_height, row_codec, row_fps, row_frame_count, str(target_frames), str(target_rep), now)
                    )
                    db.conn.execute("DELETE FROM local_playback_snapshot_frames WHERE canonical_path=?", (canonical,))
                    source_manifest = {}
                    for idx, frame_path in enumerate(sorted(target_frames.glob("frame_*.jpg")), 1):
                        ts = row_duration * (0.02 + 0.96 * (idx - 1) / 35) if row_duration > 0 and row_frame_count == 36 else 0.0
                        fw = row_width
                        fh = row_height
                        db.conn.execute(
                            "INSERT INTO local_playback_snapshot_frames(canonical_path,source_oshash,frame_index,timestamp,path,width,height,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                            (canonical, fingerprint, idx, ts, str(frame_path), fw, fh, now)
                        )
                    db.conn.commit()
                return target
    except Exception:
        return None
    return None


def record_local_playback_snapshot_sync(db, video_path: Path, source_oshash: str, snapshot_path: Path, metadata: dict | None = None) -> None:
    """Persist local snapshot identity, frame manifest, and video metadata."""
    fingerprint = str(source_oshash or '').strip().lower()
    if not fingerprint or not Path(snapshot_path).is_file() or Path(snapshot_path).stat().st_size <= 0:
        return
    video = Path(video_path)
    frames_dir = local_playback_snapshot_frames_path(video)
    representative = local_playback_representative_path(video)
    metadata = dict(metadata or {})
    duration = float(metadata.get("duration") or 0)
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    codec = str(metadata.get("video_codec") or metadata.get("codec") or "")
    fps = float(metadata.get("fps") or 0)
    frame_paths = sorted(frames_dir.glob("frame_*.jpg")) if frames_dir.is_dir() else []
    with db.lock:
        db.conn.execute(
            "INSERT INTO local_playback_snapshots(canonical_path,source_oshash,snapshot_path,source_size,duration,width,height,codec,fps,frame_count,frames_path,representative_path,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(canonical_path) DO UPDATE SET source_oshash=excluded.source_oshash,snapshot_path=excluded.snapshot_path,source_size=excluded.source_size,duration=excluded.duration,width=excluded.width,height=excluded.height,codec=excluded.codec,fps=excluded.fps,frame_count=excluded.frame_count,frames_path=excluded.frames_path,representative_path=excluded.representative_path,updated_at=excluded.updated_at",
            (_local_media_identity_path(video), fingerprint, str(Path(snapshot_path)), int(video.stat().st_size if video.exists() else 0), duration, width, height, codec, fps, len(frame_paths), str(frames_dir), str(representative), config.now_iso())
        )
        db.conn.execute("DELETE FROM local_playback_snapshot_frames WHERE canonical_path=?", (_local_media_identity_path(video),))
        for idx, frame_path in enumerate(frame_paths, 1):
            ts = duration * (0.02 + 0.96 * (idx - 1) / 35) if duration > 0 and len(frame_paths) == 36 else 0.0
            db.conn.execute("INSERT INTO local_playback_snapshot_frames(canonical_path,source_oshash,frame_index,timestamp,path,width,height,updated_at) VALUES(?,?,?,?,?,?,?,?)", (_local_media_identity_path(video), fingerprint, idx, ts, str(frame_path), width, height, config.now_iso()))
        db.conn.commit()


def _local_playback_asset_dir(video_path: Path) -> Path:
    """Return an Archive X-managed directory stable for this canonical file path.

    Assets deliberately live under Archive X's persistent playback asset tree,
    not in the disposable player cache or beside the user's video. Local Upload may remove the original after Telegram verifies
    delivery; that must not remove Archive X's reusable 36 frames/representative.
    """
    import hashlib
    canonical = str(Path(video_path).expanduser().resolve(strict=False)).casefold()
    key = hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return config.THUMBNAIL_DIR / "local" / key


def local_playback_snapshot_path(video_path: Path) -> Path:
    """Return the persistent Archive X-managed representative snapshot path."""
    return _local_playback_asset_dir(Path(video_path)) / "snapshot.jpg"


def local_playback_snapshot_frames_path(video_path: Path) -> Path:
    """Return the Archive X-managed directory containing the reusable 36 frames."""
    return _local_playback_asset_dir(Path(video_path)) / "frames"


def local_playback_contact_sheet_path(video_path: Path) -> Path:
    """Return the disposable Telegram-only contact sheet for local media.

    The grid lives in Archive X's delivery cache so it never pollutes a source
    folder. It is deleted after a video + cover + companion document are
    verified; only the reusable individual frames and representative remain.
    """
    import hashlib
    canonical = str(Path(video_path).expanduser().resolve(strict=False)).casefold()
    key = hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return config.PLAYER_CACHE_DIR / "telegram_delivery_contact_sheets" / "local" / f"{key}_contact_sheet_v{CONTACT_SHEET_VERSION}.jpg"


CONTACT_SHEET_VERSION = 6
CONTACT_SHEET_MARKER = b"ArchiveX-36-frame-contact-sheet-v6"




# ==============================================================================
# SECTION: Contact sheets
# Manifests, fingerprints and contact sheet builds.
# ==============================================================================

def _contact_sheet_manifest_path(path: Path) -> Path:
    return Path(str(path) + ".json")


def _contact_sheet_metadata(db=None, channel_id=None, message_id=None, message=None,
                             *, filename: str | None = None, local_path: Path | None = None,
                             explicit: dict | None = None) -> dict:
    """Collect stable video properties for the sheet header without provider calls."""
    values = dict(explicit or {})
    if db is not None and channel_id is not None and message_id is not None:
        try:
            with db.lock:
                row = db.conn.execute(
                    "SELECT filename,size,duration,width,height,fps,bitrate,codec,audio_codec "
                    "FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                    (str(channel_id), int(message_id)),
                ).fetchone()
            if row:
                for key in ("filename", "size", "duration", "width", "height", "fps", "bitrate", "codec", "audio_codec"):
                    if values.get(key) in (None, "", 0):
                        values[key] = row[key]
        except Exception:
            pass
    if message is not None:
        file_obj = getattr(message, "file", None)
        fields = {
            "filename": getattr(file_obj, "name", None), "size": getattr(file_obj, "size", None),
            "duration": getattr(file_obj, "duration", None), "width": getattr(file_obj, "width", None),
            "height": getattr(file_obj, "height", None), "mime_type": getattr(file_obj, "mime_type", None),
        }
        for key, value in fields.items():
            if values.get(key) in (None, "", 0) and value not in (None, "", 0):
                values[key] = value
        if not values.get("codec"):
            try:
                doc = getattr(message, "document", None)
                for attr in getattr(doc, "attributes", []) or []:
                    if hasattr(attr, "w") and hasattr(attr, "h"):
                        values.setdefault("width", getattr(attr, "w", 0))
                        values.setdefault("height", getattr(attr, "h", 0))
                        break
            except Exception:
                pass
    if local_path is not None:
        values.setdefault("filename", Path(local_path).name)
        try:
            values.setdefault("size", Path(local_path).stat().st_size)
        except OSError:
            pass
    if filename:
        values["filename"] = str(filename)
    return values


def _contact_sheet_fingerprint(frames_dir: Path, metadata: dict, source_path: Path | None = None) -> str:
    """Content/cache fingerprint for the 36 individual frames plus display metadata."""
    import hashlib
    facts = []
    for index in range(1, 37):
        path = Path(frames_dir) / f"frame_{index:02d}.jpg"
        try:
            st = path.stat()
            facts.append((path.name, int(st.st_size), int(st.st_mtime_ns)))
        except OSError:
            facts.append((path.name, 0, 0))
    source_fact = None
    if source_path is not None:
        try:
            stat = Path(source_path).stat()
            source_fact = (int(stat.st_size), int(stat.st_mtime_ns))
        except OSError:
            source_fact = None
    payload = json.dumps({"version": CONTACT_SHEET_VERSION, "frames": facts,
                          "metadata": {k: metadata.get(k) for k in (
                              "filename", "size", "duration", "width", "height", "fps", "bitrate",
                              "codec", "audio_codec", "mime_type", "format_name", "video_codec")},
                          "source": source_fact}, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def contact_sheet_has_current_format_sync(path: Path) -> bool:
    """Verify this is an Archive X 36-frame delivery sheet, not an old still."""
    from PIL import Image
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 512:
        return False
    try:
        with Image.open(p) as image:
            image.verify()
        with Image.open(p) as image:
            comment = image.info.get("comment", b"")
            if isinstance(comment, str):
                comment = comment.encode("utf-8", "ignore")
            if CONTACT_SHEET_MARKER in bytes(comment or b""):
                return True
    except Exception:
        return False
    try:
        manifest = json.loads(_contact_sheet_manifest_path(p).read_text(encoding="utf-8"))
        return manifest.get("marker") == CONTACT_SHEET_MARKER.decode("ascii") and int(manifest.get("version", 0)) == CONTACT_SHEET_VERSION
    except Exception:
        return False


def _write_contact_sheet_manifest(path: Path, fingerprint: str) -> None:
    manifest = {"marker": CONTACT_SHEET_MARKER.decode("ascii"), "version": CONTACT_SHEET_VERSION,
                "fingerprint": str(fingerprint), "updated_at": config.now_iso()}
    target = _contact_sheet_manifest_path(path)
    tmp = Path(str(target) + ".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(target)


def _make_contact_sheet_from_frames(frames_dir: Path, path: Path, metadata: dict,
                                    *, source_path: Path | None = None) -> Path:
    from ..core.internal_media import make_contact_sheet
    from .preview_prefetch import frame_quality_sync
    quality = frame_quality_sync(Path(frames_dir))
    if not quality.get("ready") or int(quality.get("frame_count") or 0) != 36:
        raise RuntimeError(
            f"Contact sheet requires 36 validated snapshots; got {quality.get('frame_count')}/36 "
            f"({quality.get('distinct_frames')} distinct)"
        )
    fingerprint = _contact_sheet_fingerprint(Path(frames_dir), metadata, source_path)
    make_contact_sheet(Path(frames_dir), Path(path), filename=str(metadata.get("filename") or "video"),
                       properties=metadata, rows=6, cols=6)
    _write_contact_sheet_manifest(Path(path), fingerprint)
    if not contact_sheet_has_current_format_sync(Path(path)):
        raise RuntimeError("Generated Telegram contact sheet did not pass format validation")
    return Path(path)


def _cached_contact_sheet_path(channel_id: int, message_id: int) -> Path:
    return config.PLAYER_CACHE_DIR / "telegram_delivery_contact_sheets" / str(int(channel_id)) / f"{int(message_id)}_v{CONTACT_SHEET_VERSION}.jpg"


def playback_snapshot_sheet_cached_sync(db, channel_id: int, message_id: int):
    """Load a delivery sheet only while its current 36-frame inputs still match."""
    from .preview_prefetch import frame_quality_sync, preview_frames_path
    frames_dir = preview_frames_path(int(channel_id), int(message_id))
    quality = frame_quality_sync(frames_dir)
    if not quality.get("ready") or int(quality.get("frame_count") or 0) != 36:
        return None
    metadata = _contact_sheet_metadata(db, channel_id, message_id)
    target = _cached_contact_sheet_path(channel_id, message_id)
    manifest_path = _contact_sheet_manifest_path(target)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_fingerprint = _contact_sheet_fingerprint(frames_dir, metadata)
        if (manifest.get("fingerprint") != current_fingerprint
                or not contact_sheet_has_current_format_sync(target)):
            return None
        data = target.read_bytes()
        return (data, "image/jpeg") if len(data) >= 512 else None
    except Exception:
        return None


async def ensure_playback_snapshot_sheet(db, telegram, channel_id: int, message_id: int, *,
                                         message=None, force: bool = False):
    """Ensure a 36-frame contact sheet for Telegram cover/document delivery.

    The canonical individual frames remain untouched and are still what player
    and library preview routes serve. This sheet is deliberately delivery-only.
    """
    if not force:
        cached = await asyncio.to_thread(playback_snapshot_sheet_cached_sync, db, channel_id, message_id)
        if cached:
            return cached
    # This call validates or builds the canonical 36 individual frame assets.
    await ensure_playback_snapshot(db, telegram, channel_id, message_id, message=message, force=force,
                                   require_timeline=True)
    from .preview_prefetch import preview_frames_path
    frames_dir = preview_frames_path(int(channel_id), int(message_id))
    metadata = _contact_sheet_metadata(db, channel_id, message_id, message)
    target = _cached_contact_sheet_path(channel_id, message_id)
    await asyncio.to_thread(_make_contact_sheet_from_frames, frames_dir, target, metadata)
    return target.read_bytes(), "image/jpeg"


async def get_send_contact_sheet_bytes(db, telegram, channel_id: int, message_id: int, *, strict_existing: bool = False):
    """Return the 36-frame Telegram delivery sheet, never the library still."""
    cached = await asyncio.to_thread(playback_snapshot_sheet_cached_sync, db, channel_id, message_id)
    if cached:
        return cached
    if strict_existing:
        return None
    if not getattr(telegram, "connected", False):
        raise RuntimeError("A verified Telegram contact sheet is not cached and Telegram is disconnected")
    return await ensure_playback_snapshot_sheet(db, telegram, channel_id, message_id)


def _local_contact_sheet_has_current_format(path: Path) -> bool:
    return contact_sheet_has_current_format_sync(Path(path))


def local_playback_contact_sheet_cached_sync(video_path: Path, *, metadata: dict | None = None,
                                             source_oshash: str | None = None):
    from .preview_prefetch import frame_quality_sync
    video_path = Path(video_path)
    frames_dir = local_playback_snapshot_frames_path(video_path)
    target = local_playback_contact_sheet_path(video_path)
    quality = frame_quality_sync(frames_dir)
    if not quality.get("ready") or int(quality.get("frame_count") or 0) != 36:
        return None
    props = dict(metadata or {})
    props.setdefault("filename", video_path.name)
    props.setdefault("size", video_path.stat().st_size if video_path.exists() else 0)
    fingerprint = _contact_sheet_fingerprint(frames_dir, props, video_path)
    try:
        manifest = json.loads(_contact_sheet_manifest_path(target).read_text(encoding="utf-8"))
        if (manifest.get("fingerprint") == fingerprint and manifest.get("source_oshash") == str(source_oshash or "")
                and _local_contact_sheet_has_current_format(target)):
            return target
    except Exception:
        return None
    return None


async def ensure_local_playback_contact_sheet(db, video_path: Path, source_oshash: str | None = None,
                                              *, metadata: dict | None = None, force: bool = False,
                                              ctx=None, display_filename: str | None = None) -> Path:
    """Generate/reuse a separate 36-frame, metadata-headed Telegram delivery image."""
    source = Path(video_path)
    meta = dict(metadata or {})
    if not meta:
        try:
            from ..core import internal_media
            meta = await asyncio.to_thread(internal_media.probe, str(source))
        except Exception:
            meta = {}
    meta.update(_contact_sheet_metadata(filename=display_filename or source.name, local_path=source, explicit=meta))
    target = local_playback_contact_sheet_path(source)
    if not force:
        cached = await asyncio.to_thread(local_playback_contact_sheet_cached_sync, source,
                                         metadata=meta, source_oshash=source_oshash)
        if cached:
            # Even a contact-sheet cache hit should expose all individual frame
            # names/metadata in a job timeline. The asset helper de-duplicates
            # frame breadcrumbs per job and source video. Preserve the legacy
            # no-context fast path for callers outside the two detailed jobs.
            if ctx is not None:
                await ensure_local_playback_assets(db, source, source_oshash, metadata=meta, ctx=ctx)
                ctx.activity(
                    f"Reused 6×6 contact sheet: {cached.name}",
                    operation="Contact sheet · cached 6×6 sheet ready", current_item=source.name,
                    event="snapshot.contact_sheet_cache_hit",
                    details={"source_filename": source.name, "contact_sheet": str(cached),
                             "bytes": int(cached.stat().st_size), "frames": 36, "columns": 6, "rows": 6,
                             "metadata": _snapshot_activity_metadata(source, meta)}, progress=True,
                )
            return cached
    if ctx is not None:
        await ensure_local_playback_assets(db, source, source_oshash, metadata=meta, ctx=ctx)
    else:
        await ensure_local_playback_assets(db, source, source_oshash, metadata=meta)
    frames_dir = local_playback_snapshot_frames_path(source)
    fingerprint = _contact_sheet_fingerprint(frames_dir, meta, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if ctx is not None:
        ctx.activity(
            f"Building 6×6 contact sheet from 36 verified frames for {source.name}",
            operation="Contact sheet · composing 6×6 layout", current_item=source.name,
            event="snapshot.contact_sheet_started",
            details={"source_filename": source.name, "contact_sheet": str(target),
                     "frames_dir": str(frames_dir), "frames": 36, "columns": 6, "rows": 6,
                     "metadata": _snapshot_activity_metadata(source, meta)}, progress=True,
        )
    await asyncio.to_thread(_make_contact_sheet_from_frames, frames_dir, target, meta, source_path=source)
    manifest = json.loads(_contact_sheet_manifest_path(target).read_text(encoding="utf-8"))
    manifest["source_oshash"] = str(source_oshash or "")
    manifest["fingerprint"] = fingerprint
    _contact_sheet_manifest_path(target).write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if ctx is not None:
        ctx.activity(
            f"6×6 contact sheet ready: {target.name}",
            operation="Contact sheet · 36 frames composed", current_item=source.name,
            event="snapshot.contact_sheet_ready",
            details={"source_filename": source.name, "contact_sheet": str(target),
                     "bytes": int(target.stat().st_size), "frames": 36, "columns": 6, "rows": 6,
                     "manifest": str(_contact_sheet_manifest_path(target)),
                     "metadata": _snapshot_activity_metadata(source, meta)}, progress=True,
        )
    return target




# ==============================================================================
# SECTION: Delivery artifact cleanup
# Removes temporary delivery artifacts.
# ==============================================================================

def _remove_contact_sheet_files(path: Path, *, prune_cache_dirs: bool = True) -> bool:
    """Delete a delivery-only contact sheet and its validation sidecars."""
    removed = False
    target = Path(path)
    existed = any(candidate.exists() for candidate in (
        target, _contact_sheet_manifest_path(target), Path(str(_contact_sheet_manifest_path(target)) + ".tmp")
    ))
    for candidate in (target, _contact_sheet_manifest_path(target), Path(str(_contact_sheet_manifest_path(target)) + ".tmp")):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass
    # Only prune directories under the app cache. Legacy sidecars are adjacent
    # to user-owned media, where even empty-directory cleanup is inappropriate.
    if prune_cache_dirs:
        for parent in (target.parent, target.parent.parent):
            try:
                parent.rmdir()
            except OSError:
                pass
    removed = existed and not target.exists() and not _contact_sheet_manifest_path(target).exists()
    return removed


def cleanup_local_delivery_artifacts(video_path: Path) -> bool:
    """Remove send-only contact-sheet files while retaining reusable snapshots."""
    target = local_playback_contact_sheet_path(Path(video_path))
    removed = _remove_contact_sheet_files(target)
    # Earlier Archive X versions kept generated sidecars beside the selected
    # video. Delete only those known derived names after verified delivery;
    # the managed-cache copy of frames/representative is retained for reuse.
    source = Path(video_path)
    legacy_sheet = source.with_name(source.stem + "._telegram_archive_contact_sheet.jpg")
    removed = _remove_contact_sheet_files(legacy_sheet, prune_cache_dirs=False) or removed

    # Remove only known v29.4.162-and-earlier sidecars after a managed-asset
    # copy is already complete. This migrates old installs without touching the
    # original media or deleting the reusable frames now stored by Archive X.
    managed_snapshot = local_playback_snapshot_path(source)
    managed_frames = local_playback_snapshot_frames_path(source)
    try:
        reusable_ready = (
            managed_snapshot.is_file() and managed_snapshot.stat().st_size >= 256
            and managed_frames.is_dir()
            and sum(1 for frame in managed_frames.glob("frame_*.jpg")) == 36
        )
    except OSError:
        reusable_ready = False
    if reusable_ready:
        import shutil
        legacy_snapshot = source.with_name(source.stem + "._archive_x_snapshot.jpg")
        legacy_representative = source.with_name(source.stem + "._archive_x_representative.jpg")
        legacy_frames = source.with_name(source.stem + "._archive_x_frames")
        for legacy_file in (legacy_snapshot, legacy_representative):
            try:
                legacy_file.unlink(missing_ok=True)
            except OSError:
                pass
        if legacy_frames.is_dir():
            shutil.rmtree(legacy_frames, ignore_errors=True)
    return removed


def cleanup_telegram_delivery_contact_sheet(channel_id: int, message_id: int) -> bool:
    """Delete a Telegram-origin cached contact sheet after verified delivery."""
    return _remove_contact_sheet_files(_cached_contact_sheet_path(int(channel_id), int(message_id)))


def cleanup_local_generated_snapshot_set(db, video_path: Path) -> bool:
    """Remove assets generated for a temporary converted copy, not the source.

    This is used only when upload preparation created a distinct temporary
    video. Canonical source-video snapshots are never removed by this helper.
    """
    import shutil
    source = Path(video_path)
    base = _local_playback_asset_dir(source)
    try:
        shutil.rmtree(base, ignore_errors=True)
    except Exception:
        pass
    try:
        with db.lock:
            canonical = _local_media_identity_path(source)
            db.conn.execute("DELETE FROM local_playback_snapshot_frames WHERE canonical_path=?", (canonical,))
            db.conn.execute("DELETE FROM local_playback_snapshots WHERE canonical_path=?", (canonical,))
            db.conn.commit()
    except Exception:
        pass
    cleanup_local_delivery_artifacts(source)
    return not base.exists()




# ==============================================================================
# SECTION: Local snapshot generation
# Generates local playback assets.
# ==============================================================================

def local_playback_representative_path(video_path: Path) -> Path:
    """Return the individual representative frame retained for Archive X UI use."""
    # V29.4.223: kept in Archive X's own asset folder, never beside the user's
    # video (older builds left "<name>._archive_x_representative.jpg" there).
    return _local_playback_asset_dir(Path(video_path)) / "representative.jpg"


def _snapshot_activity_metadata(source: Path, metadata: dict | None = None) -> dict:
    """Small, stable media-metadata record suitable for job timelines."""
    meta = dict(metadata or {})
    def _number(*keys, default=0.0):
        for key in keys:
            try:
                value = meta.get(key)
                if value not in (None, ""):
                    if isinstance(value, str) and "/" in value:
                        numerator, denominator = value.split("/", 1)
                        denominator_value = float(denominator)
                        return float(numerator) / denominator_value if denominator_value else float(default)
                    return float(value)
            except (TypeError, ValueError):
                continue
        return float(default)
    try:
        size = int(source.stat().st_size)
    except OSError:
        size = 0
    return {
        "filename": source.name,
        "path": str(source),
        "file_size_bytes": size,
        "container": str(meta.get("container") or meta.get("format_name") or source.suffix.lstrip(".") or "unknown"),
        "video_codec": str(meta.get("video_codec") or meta.get("codec") or meta.get("vcodec") or "unknown").lower(),
        "audio_codec": str(meta.get("audio_codec") or meta.get("acodec") or "unknown").lower(),
        "width": int(_number("width", "video_width")),
        "height": int(_number("height", "video_height")),
        "fps": round(_number("fps", "frame_rate", "avg_frame_rate"), 4),
        "duration_seconds": round(_number("duration", "duration_seconds", "length"), 3),
        "bitrate_bps": int(_number("bitrate", "bit_rate")),
    }


async def _trace_local_snapshot_frames(ctx, source: Path, metadata: dict | None, *, cache_state: str,
                                       fingerprint: str | None = None,
                                       extraction_metrics: dict | None = None) -> None:
    """Emit each required frame's name, timestamp, bytes, dimensions and source metadata.

    Duplicate asset preparation calls within one job are common (metadata preparation
    followed by transfer), so publish this list at most once per job/source.
    """
    if ctx is None or not callable(getattr(ctx, "activity", None)):
        return
    try:
        path_key = str(source.resolve(strict=False))
    except Exception:
        path_key = str(source)
    seen = getattr(ctx, "_snapshot_activity_sources", None)
    if seen is None:
        seen = set()
        setattr(ctx, "_snapshot_activity_sources", seen)
    key = (path_key, str(fingerprint or ""))
    if key in seen:
        return
    seen.add(key)
    duration = _snapshot_activity_metadata(source, metadata).get("duration_seconds") or 0.0
    video_meta = _snapshot_activity_metadata(source, metadata)
    frames_dir = local_playback_snapshot_frames_path(source)
    from PIL import Image
    for index in range(1, 37):
        name = f"frame_{index:02d}.jpg"
        frame_path = frames_dir / name
        try:
            stat = frame_path.stat()
            with Image.open(frame_path) as frame:
                width, height = frame.size
                image_format = str(frame.format or "JPEG")
                frame.verify()
        except Exception as exc:
            ctx.activity(f"Snapshot frame invalid: {source.name} · {name}",
                         operation=f"Snapshots · validating frame {index:02d}/36", current_item=f"{source.name} · {name}",
                         level="ERROR", event="snapshot.frame_invalid",
                         details={"source_filename": source.name, "frame_name": name,
                                  "frame_path": str(frame_path), "frame_index": index, "frame_count": 36,
                                  "cache_state": cache_state, "error": f"{type(exc).__name__}: {exc}"[:500],
                                  "video_metadata": video_meta}, progress=True)
            raise RuntimeError(f"Missing or invalid snapshot frame {name} for {source.name}: {exc}") from exc
        timestamp = float(duration) * (0.02 + 0.96 * ((index - 1) / 35.0)) if duration else None
        extraction = extraction_metrics or {}
        ctx.activity(
            f"Snapshot frame {index:02d}/36 {cache_state}: {name} ({width}×{height}, {stat.st_size:,} bytes)",
            operation=f"Snapshots · frame {index:02d}/36 · {name}",
            current_item=f"{source.name} · {name}", event="snapshot.frame_verified",
            details={"source_filename": source.name, "source_path": str(source),
                     "frame_name": name, "frame_path": str(frame_path), "frame_index": index,
                     "frame_count": 36, "sample_timestamp_seconds": round(timestamp, 3) if timestamp is not None else None,
                     "frame_bytes": int(stat.st_size), "frame_width": int(width), "frame_height": int(height),
                     "image_format": image_format, "cache_state": cache_state,
                     "source_oshash": fingerprint, "video_metadata": video_meta,
                     "extraction_method": str(extraction.get("extraction_method") or "cache"),
                     "workers": int(extraction.get("workers") or 0)}, progress=True)


async def generate_local_playback_snapshot(video_path: Path, output_path: Path, *, diagnostics: dict | None = None, ctx=None) -> Path:
    """Generate independent local video frames and one representative still.

    The native worker returns its extraction method and timing/byte-read metrics;
    ``diagnostics`` lets the caller persist those measurements without changing
    the frame-generation contract.
    """
    from ..core import internal_media
    source = Path(video_path)
    out = Path(output_path)
    frames_dir = local_playback_snapshot_frames_path(source)
    rep_path = local_playback_representative_path(source)
    loop = asyncio.get_running_loop()
    last_progress = {"fraction": -1.0, "reported_at": 0.0}

    def _native_progress(fraction: float) -> None:
        if ctx is None:
            return
        try:
            value = max(0.0, min(1.0, float(fraction)))
        except Exception:
            return
        now = __import__("time").monotonic()
        # Keep the UI responsive and watchdog heartbeats fresh without writing
        # a journal event for every native worker callback.
        if (value < 1.0
                and abs(value - float(last_progress["fraction"])) < 0.025
                and now - float(last_progress["reported_at"]) < 0.75):
            return
        last_progress["fraction"] = value
        last_progress["reported_at"] = now
        percent = int(round(value * 100.0))
        approx_frame = min(36, max(0, int(value * 36.0)))
        def _report_on_loop():
            try:
                ctx.report(current_item=f"{source.name} · snapshot extraction {percent}%",
                           operation=f"Snapshot extraction · {percent}% (about {approx_frame}/36 frames)")
                if getattr(ctx, "watchdog", None) is not None:
                    ctx.watchdog.heartbeat(ctx.job_id, progress=True, activity=True,
                                           reason="local_snapshot_extraction_progress",
                                           metadata={"filename": source.name, "percent": percent,
                                                     "estimated_frames": approx_frame, "frame_count": 36})
                if getattr(ctx, "pipeline", None) is not None:
                    ctx.pipeline.heartbeat(activity=True)
            except Exception:
                pass
        try:
            loop.call_soon_threadsafe(_report_on_loop)
        except RuntimeError:
            pass

    result = await asyncio.to_thread(internal_media.make_snapshot_frames, source, frames_dir,
                                     count=36, width=960, quality=95,
                                     on_progress=_native_progress if ctx is not None else None)
    from .preview_prefetch import REPRESENTATIVE_FRAME_INDEX, FRAME_COUNT, frame_quality_sync
    quality = await asyncio.to_thread(frame_quality_sync, frames_dir)
    if (not quality.get("ready") or int(quality.get("frame_count") or 0) != FRAME_COUNT
            or quality.get("corrupt_frames") or not quality.get("all_indexes_present")):
        missing = ", ".join(str(name) for name in (quality.get("corrupt_frames") or [])[:10])
        raise RuntimeError(
            f"Snapshot extraction returned an incomplete frame set for {source.name}: "
            f"{int(quality.get('frame_count') or 0)}/{FRAME_COUNT} valid frames"
            + (f"; missing/corrupt: {missing}" if missing else "")
        )
    if diagnostics is not None and isinstance(result, dict):
        diagnostics.update(result)
        diagnostics["validated_frame_count"] = int(quality.get("frame_count") or 0)
        diagnostics["distinct_frames"] = int(quality.get("distinct_frames") or diagnostics.get("distinct_frames") or 0)
    middle = frames_dir / f"frame_{REPRESENTATIVE_FRAME_INDEX + 1:02d}.jpg"
    if not middle.is_file() or not _valid_image_file(middle):
        raise RuntimeError("local representative snapshot is missing or invalid")
    out.parent.mkdir(parents=True, exist_ok=True)
    rep_path.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve(strict=False) != rep_path.resolve(strict=False):
        out.write_bytes(middle.read_bytes())
    rep_path.write_bytes(middle.read_bytes())
    if not _valid_image_file(rep_path):
        raise RuntimeError("local representative snapshot failed image validation")
    return out


async def _ensure_local_playback_assets_unlocked(db, video_path: Path, source_oshash: str | None, *, metadata: dict | None = None, force: bool = False, ctx=None) -> Path:
    """Ensure one local video has its complete Archive X application asset set.

    The persistent set is deterministic per local media identity: individual
    frames, a representative still, and a SQLite manifest containing the source
    fingerprint and video metadata. The still is used as a normal thumbnail.
    """
    source = Path(video_path)
    # V29.4.223: remove the stray picture older builds left beside the video.
    try:
        source.with_name(source.stem + "._archive_x_representative.jpg").unlink(missing_ok=True)
    except OSError:
        pass
    meta = dict(metadata or {})
    if not meta:
        try:
            from ..core import internal_media
            meta = await asyncio.to_thread(internal_media.probe, str(source))
        except Exception as exc:
            meta = {}
            if ctx is not None:
                ctx.activity(f"Video metadata probe failed for {source.name}: {type(exc).__name__}: {exc}",
                             operation="Snapshot preparation · metadata probe warning", current_item=source.name,
                             level="WARN", event="snapshot.video_metadata_probe_failed",
                             details={"source_filename": source.name, "source_path": str(source),
                                      "error_type": type(exc).__name__, "error": str(exc)[:500]}, progress=True)
    if ctx is not None:
        ctx.activity(
            f"Video metadata ready for snapshots: {source.name}",
            operation="Snapshot preparation · video metadata resolved", current_item=source.name,
            event="snapshot.video_metadata", details={"source_filename": source.name,
                "source_path": str(source), "metadata": _snapshot_activity_metadata(source, meta)}, progress=True,
        )
    fingerprint = str(source_oshash or "").strip().lower() or None
    if not fingerprint:
        try:
            from ..metadata.fingerprint import oshash_file
            fingerprint = str(await asyncio.to_thread(oshash_file, str(source))).strip().lower() or None
        except Exception as exc:
            try:
                db.log(f"Local playback snapshot: OSHASH unavailable for {source.name}: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                pass
    if not force and fingerprint:
        reusable = await asyncio.to_thread(local_playback_snapshot_reusable_sync, db, source, fingerprint)
        if reusable is not None:
            try:
                db.log(
                    f"Local playback snapshots reused from cache: {source.name}", "INFO",
                    event="local_snapshot.cache_hit",
                    details={"filename": source.name, "frames": 36, "source_oshash": fingerprint},
                )
            except Exception:
                pass
            await _trace_local_snapshot_frames(ctx, source, meta, cache_state="reused", fingerprint=fingerprint)
            if ctx is not None:
                ctx.activity(f"36 cached playback frames ready for {source.name}",
                             operation="Snapshots · 36 cached frames verified", current_item=source.name,
                             event="snapshot.frame_set_ready", details={"filename": source.name,
                             "frames": 36, "cache_state": "reused", "source_oshash": fingerprint,
                             "metadata": _snapshot_activity_metadata(source, meta)}, progress=True)
            return reusable
    output = local_playback_snapshot_path(source)
    import time as _time
    extraction_started = _time.monotonic()
    extraction_metrics: dict = {}
    if ctx is not None:
        ctx.activity(f"Starting 36-frame snapshot extraction for {source.name}",
                     operation="Snapshots · extracting 36 frames", current_item=source.name,
                     event="snapshot.extraction_started", details={"source_filename": source.name,
                     "source_path": str(source), "frames": 36, "frame_directory": str(local_playback_snapshot_frames_path(source)),
                     "metadata": _snapshot_activity_metadata(source, meta)}, progress=True)
    await generate_local_playback_snapshot(source, output, diagnostics=extraction_metrics, ctx=ctx)
    extraction_seconds = max(0.0, _time.monotonic() - extraction_started)
    if not output.is_file() or output.stat().st_size <= 0:
        raise RuntimeError("Archive X local playback snapshot generation produced no usable representative image")
    try:
        db.log(
            f"Local playback snapshots generated: {source.name} · 36 frames · {extraction_seconds:.1f}s · "
            f"{extraction_metrics.get('extraction_method') or 'unknown'}", "INFO",
            event="local_snapshot.generated",
            details={
                "filename": source.name, "frames": 36,
                "elapsed_seconds": round(extraction_seconds, 2),
                "extraction_method": str(extraction_metrics.get("extraction_method") or "unknown"),
                "distinct_frames": int(extraction_metrics.get("distinct_frames") or 0),
                "workers": int(extraction_metrics.get("workers") or 0),
                "codec": str(extraction_metrics.get("codec") or "unknown"),
                "source_bytes_read": int(extraction_metrics.get("source_bytes_read") or 0),
                "source_range_reads": int(extraction_metrics.get("source_range_reads") or 0),
                "worker_override": __import__("os").environ.get("ARCHIVE_X_SNAPSHOT_WORKERS") or "auto",
            },
        )
    except Exception:
        pass
    if fingerprint:
        await asyncio.to_thread(record_local_playback_snapshot_sync, db, source, fingerprint, output, meta)
    await _trace_local_snapshot_frames(ctx, source, meta, cache_state="generated", fingerprint=fingerprint,
                                      extraction_metrics=extraction_metrics)
    if ctx is not None:
        ctx.activity(f"36 playback frames generated and validated for {source.name}",
                     operation="Snapshots · 36 frames ready", current_item=source.name,
                     event="snapshot.frame_set_ready", details={"filename": source.name,
                     "frames": 36, "cache_state": "generated", "source_oshash": fingerprint,
                     "elapsed_seconds": round(extraction_seconds, 2),
                     "extraction_method": str(extraction_metrics.get("extraction_method") or "unknown"),
                     "distinct_frames": int(extraction_metrics.get("distinct_frames") or 0),
                     "workers": int(extraction_metrics.get("workers") or 0),
                     "metadata": _snapshot_activity_metadata(source, meta)}, progress=True)
    return output


async def ensure_local_playback_assets(db, video_path: Path, source_oshash: str | None, *,
                                       metadata: dict | None = None, force: bool = False, ctx=None) -> Path:
    """Ensure one local snapshot set, serializing concurrent requests per file."""
    source = Path(video_path)
    lock = _local_asset_build_lock(source)
    async with lock:
        return await _ensure_local_playback_assets_unlocked(
            db, source, source_oshash, metadata=metadata, force=force, ctx=ctx,
        )




# ==============================================================================
# SECTION: Send thumbnail entry points
# Ensures thumbnails and returns bytes for sending.
# ==============================================================================

async def ensure_thumbnail(db, telegram, channel_id: int, message_id: int, *,
                           semaphore: Optional[asyncio.Semaphore] = None,
                           retry_failed: bool = False):
    """Compatibility wrapper: thumbnail jobs now build playback snapshots."""
    if semaphore is None:
        return await ensure_playback_snapshot(db, telegram, channel_id, message_id)
    async with semaphore:
        return await ensure_playback_snapshot(db, telegram, channel_id, message_id)


async def get_send_thumbnail_bytes(db, telegram, channel_id: int, message_id: int, *, strict_existing: bool = False):
    """Return the representative Archive X playback snapshot for UI/repair compatibility.

    This is the individual representative frame used for Telegram thumbnail/cover delivery.
    """
    video_id, _ = await asyncio.to_thread(_thumb_ids_sync, db, channel_id, message_id)
    info = await asyncio.to_thread(playback_snapshot_cached_info_sync, db, channel_id, video_id)
    if info:
        return info["data"], info["mime"]
    if strict_existing:
        return None
    if getattr(telegram, "connected", False):
        snapshot = await ensure_playback_snapshot(db, telegram, channel_id, video_id)
        if snapshot:
            return snapshot
    raise RuntimeError("No Archive X playback snapshot is available for sending")





# ==============================================================================
# SECTION: Entity images
# Performer and studio image cache.
# ==============================================================================

def _entity_asset_locations(entity_type: str, entity_id: int) -> tuple[Path, Path]:
    if entity_type == "performer":
        return config.PERFORMER_ASSET_DIR / str(int(entity_id)), config.PERFORMER_METADATA_DIR / f"{int(entity_id)}.json"
    if entity_type == "studio":
        return config.STUDIO_ASSET_DIR / str(int(entity_id)), config.STUDIO_METADATA_DIR / f"{int(entity_id)}.json"
    raise ValueError(f"Unsupported entity type: {entity_type}")


def reconcile_entity_image_cache_from_disk_sync(db, entity_type: str, entity_id: int, url: str | None):
    """Restore an entity image DB cache row from its canonical local asset.

    The local file is reused only when the persisted entity metadata confirms
    that it belongs to the currently requested image URL. This prevents a
    stale artwork file from being associated with a newly selected provider
    image while still avoiding an unnecessary provider download when only the
    SQLite bytes cache was lost/rebuilt.
    """
    expected_url = str(url or "").strip()
    if not expected_url:
        return None
    table = "performer_image_cache" if entity_type == "performer" else "studio_image_cache"
    key = "performer_id" if entity_type == "performer" else "studio_id"
    asset_dir, metadata_path = _entity_asset_locations(entity_type, entity_id)
    try:
        meta = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if str(meta.get("image") or "").strip() != expected_url:
        return None

    candidates = [asset_dir / "image.jpg", asset_dir / "image.jpeg", asset_dir / "image.png", asset_dir / "image.webp"]
    asset = next((x for x in candidates if x.is_file() and x.stat().st_size > 0), None)
    if asset is None:
        return None
    try:
        data = asset.read_bytes()
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
    except Exception:
        return None
    mime = _sniff_mime(data, asset.name)
    if not str(mime or "").lower().startswith("image/"):
        return None
    with db.lock:
        db.conn.execute(
            f"INSERT OR REPLACE INTO {table}({key},url,mime,data,updated_at) VALUES(?,?,?,?,?)",
            (int(entity_id), expected_url, mime, data, config.now_iso()),
        )
        db.conn.commit()
    return data, mime


async def prefetch_entity_image(db, entity_type: str, entity_id: int, url: str | None,
                                *, max_bytes: int = 8 * 1024 * 1024,
                                smart_crop_initial: bool = False):
    """Fetch a provider artwork URL into the local entity-image cache."""
    import urllib.parse
    from ..core.http_client import request_bytes

    if not url:
        return False
    if smart_crop_initial and entity_type == "performer":
        with db.lock:
            owner = db.conn.execute(
                "SELECT image_locked,image_source FROM performers WHERE id=?", (int(entity_id),)
            ).fetchone()
        owner_source = str(owner["image_source"] or "").strip().lower() if owner else ""
        if not owner or bool(owner["image_locked"]) or owner_source == "manual" or owner_source.startswith("provider-selected:"):
            return False
    restored = await asyncio.to_thread(
        reconcile_entity_image_cache_from_disk_sync, db, entity_type, int(entity_id), str(url)
    )
    if restored:
        if smart_crop_initial and entity_type == "performer":
            with db.lock:
                crop_row = db.conn.execute("SELECT image,image_source,image_locked,image_crop_version FROM performers WHERE id=?", (int(entity_id),)).fetchone()
            source = str(crop_row["image_source"] or "").strip().lower() if crop_row else ""
            pending = bool(crop_row and not crop_row["image_locked"] and source != "manual" and not source.startswith("provider-selected:") and int(crop_row["image_crop_version"] or 0) < 1)
            if pending:
                await asyncio.to_thread(_persist_entity_image_sync, db, entity_type, int(entity_id), str(url), bytes(restored[0]), smart_crop_initial=True)
        return True
    parsed = urllib.parse.urlparse(str(url))
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Artwork URL must use http:// or https://")
    def _download():
        data, content_type, status = request_bytes(str(url), timeout=20, max_bytes=max_bytes)
        return data, str(content_type or ""), int(status or 0)

    # Provider artwork download is itself single-flight. Two UI cards or
    # maintenance workers requesting the same entity join one network call.
    from ..core.resource_registry import coordinator_for
    (data, content_type, status), _joined = await coordinator_for(db).run(
        f"entity-image:{entity_type}:{int(entity_id)}:{str(url)}",
        lambda: asyncio.to_thread(_download),
    )
    # Validation, SQLite and disk writes are synchronous; run them off the
    # event loop so artwork sync can never stall the dashboard or playback.
    try:
        return await asyncio.to_thread(
            _persist_entity_image_sync, db, entity_type, int(entity_id), str(url), data,
            smart_crop_initial=smart_crop_initial, content_type=content_type,
        )
    except Exception as exc:
        detail = f"provider artwork rejected: URL={str(url)} content_type={content_type or 'unknown'} status={status} bytes={len(data or b'')} error={type(exc).__name__}: {exc}"
        try: db.log(detail, "WARN")
        except Exception: pass
        raise RuntimeError(detail) from exc


def _persist_entity_image_sync(db, entity_type: str, entity_id: int, url: str, data: bytes,
                               *, smart_crop_initial: bool = False, content_type: str = "") -> bool:
    """Validate and store raster or safely-sanitized SVG provider artwork."""
    import re
    import xml.etree.ElementTree as ET
    table = "performer_image_cache" if entity_type == "performer" else "studio_image_cache"
    key = "performer_id" if entity_type == "performer" else "studio_id"
    mime = _sniff_mime(data, str(url))
    declared = str(content_type or "").split(";", 1)[0].strip().lower()
    if mime == "image/jpeg" and declared in {"image/webp", "image/png", "image/svg+xml"}:
        mime = declared
    if mime == "image/svg+xml":
        # SVG is rendered by browsers, so reject scripts, foreign objects, event
        # handlers, and external references before it enters the image cache.
        try:
            text = bytes(data).decode("utf-8-sig", errors="strict")
            if re.search(r"<!DOCTYPE|<!ENTITY", text, re.I):
                raise ValueError("SVG DOCTYPE/entity declarations are not allowed")
            root = ET.fromstring(text)
            if root.tag.split("}")[-1].lower() != "svg":
                raise ValueError("root element is not svg")
            for elem in root.iter():
                tag = elem.tag.split("}")[-1].lower()
                if tag in {"script", "foreignobject", "iframe", "object", "embed", "audio", "video"}:
                    raise ValueError(f"unsafe SVG element: {tag}")
                for attr, value in elem.attrib.items():
                    name = attr.split("}")[-1].lower()
                    val = str(value or "").strip().lower()
                    if name.startswith("on") or (name in {"href", "src"} and val and not val.startswith(("#", "data:image/"))):
                        raise ValueError(f"unsafe SVG attribute: {name}")
                    if "url(" in val and not re.search(r"url\(\s*#[^)]+\)", val):
                        raise ValueError("external SVG CSS references are not allowed")
                style_text = str(elem.text or "") if tag == "style" else ""
                if re.search(r"@import|url\(\s*(?:https?:|//|file:|javascript:)", style_text, re.I):
                    raise ValueError("external SVG stylesheet references are not allowed")
            data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        except Exception as exc:
            raise RuntimeError(f"provider artwork is not a valid safe SVG: {type(exc).__name__}: {exc}") from exc
    else:
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
                mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(im.format, mime)
        except Exception as exc:
            raise RuntimeError(f"provider artwork is not a valid raster image: {type(exc).__name__}: {exc}") from exc
    if not str(mime or '').lower().startswith('image/'):
        raise RuntimeError(f'provider artwork did not resolve to an image MIME type (declared={content_type!r})')

    # Smart framing applies only when a provider image is first acquired for an
    # unlocked performer. User-selected provider images are fetched through the
    # manual-image path with smart_crop_initial=False; uploads and manual URLs
    # bypass this function's crop path entirely. Recheck the lock at write time.
    with db.lock:
        if entity_type == "performer":
            row = db.conn.execute("SELECT * FROM performers WHERE id=?", (int(entity_id),)).fetchone()
        else:
            row = db.conn.execute("SELECT * FROM studios WHERE id=?", (int(entity_id),)).fetchone()
    stored_data = bytes(data)
    stored_mime = mime
    crop_applied = False
    auto_crop_flow = False
    if smart_crop_initial and entity_type == "performer" and mime != "image/svg+xml":
        if row is None:
            return False
        source = str(row["image_source"] or "").strip().lower()
        manually_owned = bool(row["image_locked"] or source == "manual" or source.startswith("provider-selected:"))
        if manually_owned:
            return False
        auto_crop_flow = True
        try:
            from .performer_smart_crop import initial_performer_crop
            stored_data, stored_mime, crop_applied = initial_performer_crop(bytes(data))
        except Exception as exc:
            # Cropping is enhancement-only: a failure must never hide valid
            # provider artwork or turn an otherwise-successful sync into a failure.
            stored_data, stored_mime, crop_applied = bytes(data), mime, False
            try:
                db.log(f"Initial performer smart-crop skipped for {entity_id}: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                pass

    with db.lock:
        # Close the race where a user picked an image while cropping was
        # running. Check before writing anything; otherwise an automatic task
        # could replace a manually selected image/cache row.
        if entity_type == "performer" and auto_crop_flow:
            current = db.conn.execute("SELECT image_locked,image_source FROM performers WHERE id=?", (int(entity_id),)).fetchone()
            current_source = str(current["image_source"] or "").strip().lower() if current else ""
            if current and (bool(current["image_locked"]) or current_source == "manual" or current_source.startswith("provider-selected:")):
                db.conn.commit()
                return False
        db.conn.execute(
            f"INSERT OR REPLACE INTO {table}({key},url,mime,data,updated_at) VALUES(?,?,?,?,?)",
            (int(entity_id), str(url), stored_mime, stored_data, config.now_iso()),
        )
        if entity_type == "performer" and auto_crop_flow:
            db.conn.execute("UPDATE performers SET image_crop_version=1 WHERE id=? AND COALESCE(image_locked,0)=0", (int(entity_id),))
        db.conn.commit()
    if entity_type == "performer":
        asset_dir = config.PERFORMER_ASSET_DIR / str(int(entity_id))
        metadata_dir = config.PERFORMER_METADATA_DIR
    else:
        asset_dir = config.STUDIO_ASSET_DIR / str(int(entity_id))
        metadata_dir = config.STUDIO_METADATA_DIR
    suffix = _asset_suffix(stored_mime)
    _write_bytes_atomic(asset_dir / f"image{suffix}", stored_data)
    try:
        from ..core.resource_registry import record_asset
        record_asset(db, media_id=None, entity_type=entity_type, entity_id=int(entity_id),
                     asset_type="ENTITY_IMAGE", path=str(asset_dir / f"image{suffix}"), size_bytes=len(stored_data))
    except Exception as exc:
        # The asset registry is an index of files already written above; a
        # failed index row must not fail the artwork itself, but is logged.
        db.log(f"Entity artwork asset index update failed for {entity_type} {entity_id}: {type(exc).__name__}: {exc}", "WARN")
    if row:
        payload = {k: row[k] for k in row.keys()}
        # This cache entry is keyed by the candidate URL being persisted. The
        # row may still contain the previous provider URL until its caller's
        # following transaction commits, so use the actual cache key here.
        payload["image"] = str(url)
        if entity_type == "performer" and auto_crop_flow:
            payload["image_crop_version"] = 1
        payload["image_file"] = str((asset_dir / f"image{suffix}").relative_to(config.USER_DATA_DIR))
        path = metadata_dir / f"{int(entity_id)}.json"
        path_tmp = path.with_suffix(path.suffix + ".tmp")
        path_tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        path_tmp.replace(path)
    return True



async def ensure_playback_snapshot(db, telegram, channel_id: int, message_id: int, *,
                                   message=None, force: bool = False, require_timeline: bool = True):
    """Create/validate remote snapshots while holding Archive X's global video lock."""
    channel, mid = int(channel_id), int(message_id)
    identity = ""
    try:
        with db.lock:
            row = db.conn.execute("SELECT oshash,identity_key FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                                  (str(channel), mid)).fetchone()
        if row:
            identity = str(row["oshash"] or row["identity_key"] or "")
    except Exception:
        pass
    from ..core.video_locks import video_operation_lock
    async with video_operation_lock(identity_key=identity, source_channel=str(channel), source_message_id=mid):
        if not force:
            try:
                from ..core.resource_registry import ensure_resource_tables
                ensure_resource_tables(db)
                with db.lock:
                    published = db.conn.execute(
                        "SELECT 1 FROM archive_x_published_media pm JOIN media m ON m.id=pm.id "
                        "WHERE m.source_channel=? AND m.source_message_id=? LIMIT 1", (str(channel), mid),
                    ).fetchone()
                if published:
                    hit = await asyncio.to_thread(playback_snapshot_cached_sync, db, channel, mid)
                    state = await asyncio.to_thread(playback_snapshot_state_sync, db, channel, mid)
                    if hit and state.get("timeline_ready"):
                        return hit
            except Exception:
                pass
        return await _ensure_playback_snapshot_unlocked(
            db, telegram, channel, mid, message=message, force=force, require_timeline=require_timeline,
        )
