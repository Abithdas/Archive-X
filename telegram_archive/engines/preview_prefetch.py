"""Background individual-frame snapshot pipeline.

The cache stores 36 independently addressable JPEGs plus one prebuilt 6x6
sprite for instant local grid loading. Snapshot verification is recorded on
creation so page requests never need to decode the images again.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Helpers and row filters
#   - Paths and local frame files
#   - Grid sprites and frame quality
#   - Local preview assets
#   - Work queue
#   - Snapshot build control
#   - Snapshot build
#   - Job runner
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import os
import json
import threading
import logging
import time
from pathlib import Path
from typing import Iterable

from .. import config
from ..jobs.context import JobCancelled

PREVIEW_VERSION = 7  # V29.4.204: 640px high-quality independent frame set
FRAME_COUNT = 36
# Video-card poster = the 3rd of the 36 snapshots (zero-based index 2).
REPRESENTATIVE_FRAME_INDEX = 2
MIN_FRAME_BYTES = 256
MIN_VALID_FRAMES = 36
MIN_DISTINCT_FRAMES = 4
PREVIEW_MESSAGE_TIMEOUT_SECONDS = 30.0
RESOLVE_CHUNK = 20
PREVIEW_BUILD_TIMEOUT_SECONDS = 180.0
PREVIEW_BUILD_MAX_ACTIVE_SECONDS = 900.0
SNAPSHOT_VIDEO_MAX_SECONDS = float(getattr(config, "SNAPSHOT_VIDEO_MAX_SECONDS", 300.0))
PREVIEW_WORKER_WAIT_SECONDS = 600.0




# ==============================================================================
# SECTION: Helpers and row filters
# Activity, retries and work selection.
# ==============================================================================

def _job_activity(ctx, message: str, *, operation: str, current_item: str, event: str, details=None, level="INFO") -> None:
    """Durable per-video snapshot timeline event plus watchdog liveness."""
    emit = getattr(ctx, "activity", None)
    if callable(emit):
        emit(message, operation=operation, current_item=current_item, event=event,
             details=dict(details or {}), level=level, progress=True)
    else:
        ctx.report(current_item=current_item, operation=operation)
PREVIEW_CANCEL_GRACE_SECONDS = 20.0
RETRY_AFTER_HOURS = int(getattr(config, "SNAPSHOT_FAILED_RETRY_HOURS", 24))
MAX_ITEMS_PER_RUN = 2
_LOG = logging.getLogger(__name__)


def _app_config():
    from .. import config as app_config
    return app_config


def _now() -> str:
    return _app_config().now_iso()


# Filesystem assets (validation.json, grid_sprite.jpg and the permanent MP4
# index) cannot be tested reliably in SQL. Missing-only scans therefore use a
# broad catalogue query followed by the local, filesystem-aware predicate
# below; a stale READY row must never hide a missing playback asset.
_COOLDOWN_CLAUSE = "NOT (COALESCE(c.status,'')='FAILED' AND COALESCE(c.updated_at,'')>?)"


def _retry_cutoff() -> str:
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=RETRY_AFTER_HOURS)).isoformat(timespec="seconds")


def _saved_probe_ready(raw_info) -> bool:
    try:
        info = json.loads(raw_info or "{}") if isinstance(raw_info, (str, bytes)) else (raw_info or {})
        return bool(
            isinstance(info, dict)
            and float(info.get("duration") or 0) > 0
            and int(info.get("width") or info.get("video_width") or 0) > 0
            and int(info.get("height") or info.get("video_height") or 0) > 0
            and str(info.get("vcodec") or info.get("codec") or "").strip()
        )
    except (TypeError, ValueError):
        return False


def _mp4_index_cache_ready(channel_id: int, message_id: int, size: int = 0, manifest=None) -> bool:
    """Check that every MP4 moov/index segment named by the durable manifest exists."""
    try:
        from ..core.playback_index_cache import read_manifest, segment_path
        meta = manifest or read_manifest(int(channel_id), int(message_id), int(size) if int(size or 0) > 0 else None)
        if not isinstance(meta, dict) or not meta.get("moov_size"):
            return False
        source_size = int(size or meta.get("source_size") or 0)
        segment_size = int(meta.get("segment_size") or getattr(_app_config(), "PLAYBACK_RANGE_CHUNK_BYTES", 512 * 1024))
        if source_size <= 0 or int(meta.get("source_size") or 0) != source_size or segment_size <= 0:
            return False
        moov_offset = int(meta.get("moov_offset") or -1)
        moov_size = int(meta.get("moov_size") or 0)
        if moov_offset < 0 or moov_size <= 0 or moov_offset + moov_size > source_size:
            return False
        first_moov = moov_offset // segment_size
        last_moov = (moov_offset + moov_size - 1) // segment_size
        required = set(range(first_moov, last_moov + 1))
        if not required or not (meta.get("keyframes") or []):
            return False
        saved = set(int(x) for x in (meta.get("segments") or []))
        # Incomplete index manifests are repaired by the next explicit
        # Metadata & Assets scan; video byte ranges are warmed separately by
        # normal playback.
        if not required.issubset(saved):
            return False
        for seg in required:
            expected = min(segment_size, source_size - seg * segment_size)
            if seg < 0 or expected <= 0:
                return False
            path = segment_path(int(channel_id), int(message_id), source_size, seg)
            if not path.is_file() or path.stat().st_size != expected:
                return False
        return True
    except (OSError, TypeError, ValueError):
        return False


def _row_needs_remote_playback_assets(row) -> bool:
    """Whether Metadata & Assets must fetch missing Telegram data/cache state."""
    cid, mid = int(row["source_channel"]), int(row["source_message_id"])
    try:
        from ..core.playback_index_cache import read_telegram_location
        if not read_telegram_location(cid, mid, None):
            return True
    except Exception:
        return True
    if not _saved_probe_ready(row["probe_info"] if "probe_info" in row.keys() else None):
        return True
    try:
        filename = str(row["filename"] or row["title"] or "").strip().lower()
    except Exception:
        filename = ""
    if Path(filename).suffix not in {".mp4", ".mov", ".m4v"}:
        return False
    size = int(row["size"] or 0) if "size" in row.keys() else 0
    return not _mp4_index_cache_ready(cid, mid, size)


def _library_state_note(db, row) -> str:
    """" [library: snapshots STALE, last change STALE_REFRESH_REQUIRED]": what the database itself says about this video."""
    try:
        with db.lock:
            r = db.conn.execute(
                "SELECT rs.snapshot_state, rs.last_reason, (SELECT COUNT(*) FROM archive_x_published_media pm WHERE pm.id=m.id) AS published "
                "FROM media m JOIN resource_state rs ON rs.media_id=m.id "
                "WHERE CAST(m.source_channel AS TEXT)=? AND m.source_message_id=?",
                (str(row["source_channel"]), int(row["source_message_id"]))).fetchone()
        if not r:
            return " [library: no state row]"
        return f" [library: {'in' if r['published'] else 'not in'} the Library, snapshots {r['snapshot_state'] or 'unknown'}, last change {r['last_reason'] or 'none'}]"
    except Exception:
        return ""


def _scan_work_reason(row) -> str:
    """Why this video still needs snapshot work, in plain words; "" when it needs none.

    A scanned video can still be listed: the check wants all 36 frame files, a validation receipt, the grid picture, a saved
    database row that matches the current paths and version, and the playback-snapshot card image. Naming the first thing that
    is missing is what lets a log answer "it is in my Library, why is it listed?".
    """
    try:
        cid, mid = int(row["source_channel"]), int(row["source_message_id"])
        # Local-only migration can reuse valid frame sets saved by older preview
        # versions rather than unnecessarily regenerating them from Telegram.
        promote_existing_local_frames_sync(cid, mid)
        frames_dir = preview_frames_path(cid, mid)
        rep = representative_frame_path(cid, mid)
        if not frames_dir.is_dir():
            return "no snapshot folder on disk"
        have = sum(
            1 for idx in range(1, FRAME_COUNT + 1)
            if (frames_dir / f"frame_{idx:02d}.jpg").is_file() and (frames_dir / f"frame_{idx:02d}.jpg").stat().st_size >= MIN_FRAME_BYTES
        )
        if have < FRAME_COUNT:
            return f"only {have} of {FRAME_COUNT} snapshot frames on disk"
        if not rep.is_file():
            return "representative frame missing"
        if not _validation_receipt_ready(frames_dir):
            return "snapshot validation receipt missing or out of date"
        sprite = grid_sprite_path(cid, mid)
        if not (sprite.is_file() and sprite.stat().st_size >= 1024):
            return "grid picture missing"
        if str(row["cache_status"] or "").upper() != "READY":
            return f"database says snapshots are {str(row['cache_status'] or 'not recorded').lower()}"
        if int(row["version"] or 0) != PREVIEW_VERSION:
            return f"database has snapshot version {int(row['version'] or 0)}, now {PREVIEW_VERSION}"
        if str(row["path"] or "") != str(rep) or str(row["frames_path"] or "") != str(frames_dir):
            return "database points at a different snapshot folder (data folder moved?)"
        if int(row["frame_count"] or 0) != FRAME_COUNT:
            return f"database counts {int(row['frame_count'] or 0)} frames"
        if str(row["thumbnail_source"] or "") != "playback-snapshot":
            return f"card image comes from '{row['thumbnail_source'] or 'nowhere'}', not the playback snapshot"
        return "playback assets missing" if _row_needs_remote_playback_assets(row) else ""
    except (OSError, TypeError, ValueError, KeyError):
        return "snapshot files could not be checked"


def _row_needs_scan_work(row) -> bool:
    """Check both saved DB status and on-disk preview/playback artifacts."""
    return bool(_scan_work_reason(row))




# ==============================================================================
# SECTION: Paths and local frame files
# Frame locations, promotion and validation receipts.
# ==============================================================================

def preview_frames_path(channel_id: int, message_id: int) -> Path:
    """Canonical directory containing independent frame snapshots for a video."""
    return (_app_config().PLAYER_CACHE_DIR / "preview_frames" / str(int(channel_id)) /
            f"{int(message_id)}_v{PREVIEW_VERSION}.frames")


def preview_frame_path(channel_id: int, message_id: int, frame_index: int) -> Path:
    """Return a single zero-based snapshot path, without creating the directory."""
    idx = int(frame_index)
    if idx < 0 or idx >= FRAME_COUNT:
        raise ValueError(f"frame_index must be between 0 and {FRAME_COUNT - 1}")
    return preview_frames_path(channel_id, message_id) / f"frame_{idx + 1:02d}.jpg"


def representative_frame_path(channel_id: int, message_id: int) -> Path:
    return preview_frame_path(channel_id, message_id, REPRESENTATIVE_FRAME_INDEX)


def grid_sprite_path(channel_id: int, message_id: int) -> Path:
    return preview_frames_path(channel_id, message_id) / "grid_sprite.jpg"


def _has_all_local_frame_files(frames_dir: Path) -> bool:
    try:
        return all(
            (Path(frames_dir) / f"frame_{idx:02d}.jpg").is_file()
            and (Path(frames_dir) / f"frame_{idx:02d}.jpg").stat().st_size >= MIN_FRAME_BYTES
            for idx in range(1, FRAME_COUNT + 1)
        )
    except OSError:
        return False


def promote_existing_local_frames_sync(channel_id: int, message_id: int) -> bool:
    """Reuse a valid current/older-version frame set without contacting Telegram."""
    import shutil
    cid, mid = int(channel_id), int(message_id)
    current = preview_frames_path(cid, mid)
    with _LOCAL_PREVIEW_REPAIR_LOCK:
        # Current version already has a sound saved receipt: this is the common
        # scan path and only requires inexpensive fingerprint/file checks.
        if _has_all_local_frame_files(current):
            if _validation_receipt_ready(current):
                return True
            saved = load_saved_frame_validation_sync(current)
            if saved is not None:
                return True
            quality = frame_quality_sync(current)
            if quality.get("ready"):
                save_frame_validation_sync(current, quality)
                return True
        parent = current.parent
        try:
            candidates = list(parent.glob(f"{mid}_v*.frames"))
        except OSError:
            candidates = []
        def _version(path):
            try:
                return int(path.name.split("_v", 1)[1].split(".", 1)[0])
            except Exception:
                return -1
        candidates = sorted((x for x in candidates if x != current), key=_version, reverse=True)
        for candidate in candidates:
            if not _has_all_local_frame_files(candidate):
                continue
            quality = frame_quality_sync(candidate)
            if not quality.get("ready"):
                continue
            try:
                current.mkdir(parents=True, exist_ok=True)
                for idx in range(1, FRAME_COUNT + 1):
                    shutil.copy2(candidate / f"frame_{idx:02d}.jpg", current / f"frame_{idx:02d}.jpg")
                # The copied JPEGs are the exact bytes that were just validated;
                # save fresh v-current size/mtime fingerprints without decoding again.
                if not save_frame_validation_sync(current, quality):
                    continue
                return True
            except OSError:
                continue
        return False


def save_frame_validation_sync(frames_dir: Path, quality: dict) -> bool:
    """Persist the one-time validation result and a cheap file fingerprint."""
    try:
        root = Path(frames_dir)
        items = []
        for idx in range(1, FRAME_COUNT + 1):
            path = root / f"frame_{idx:02d}.jpg"
            stat = path.stat()
            if not path.is_file() or stat.st_size < MIN_FRAME_BYTES:
                return False
            items.append({"name": path.name, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns})
        payload = {"version": PREVIEW_VERSION, "frame_count": int(quality.get("frame_count") or 0),
                   "distinct_frames": int(quality.get("distinct_frames") or 0),
                   "corrupt_frames": list(quality.get("corrupt_frames") or []),
                   "ready": bool(quality.get("ready")), "files": items}
        temp = root / "validation.json.tmp"
        temp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(str(temp), str(root / "validation.json"))
        return True
    except Exception:
        return False


def load_saved_frame_validation_sync(frames_dir: Path) -> dict | None:
    """Reuse saved validation if all frame files still match their fingerprints."""
    try:
        root = Path(frames_dir)
        payload = json.loads((root / "validation.json").read_text(encoding="utf-8"))
        if int(payload.get("version") or 0) != PREVIEW_VERSION or not payload.get("ready"):
            return None
        files = payload.get("files")
        if not isinstance(files, list) or len(files) != FRAME_COUNT:
            return None
        for item in files:
            path = root / str(item.get("name") or "")
            stat = path.stat()
            if not path.is_file() or stat.st_size != int(item.get("size") or -1) or stat.st_mtime_ns != int(item.get("mtime_ns") or -1):
                return None
        return {"frame_count": int(payload.get("frame_count") or 0),
                "distinct_frames": int(payload.get("distinct_frames") or 0),
                "corrupt_frames": list(payload.get("corrupt_frames") or []),
                "all_indexes_present": True, "ready": True}
    except Exception:
        return None




# ==============================================================================
# SECTION: Grid sprites and frame quality
# Sprite creation, frame times and quality checks.
# ==============================================================================

def build_grid_sprite_sync(frames_dir: Path) -> bool:
    """Build or reuse the 36-frame sprite only when its source frames match."""
    from PIL import Image, ImageOps
    import tempfile
    root = Path(frames_dir)
    output = root / "grid_sprite.jpg"
    stamp_path = root / "grid_sprite.json"

    def frame_signature():
        signature = []
        for idx in range(1, FRAME_COUNT + 1):
            path = root / f"frame_{idx:02d}.jpg"
            if not path.is_file() or path.stat().st_size < MIN_FRAME_BYTES:
                return None
            stat = path.stat()
            signature.append({"name": path.name, "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)})
        return signature

    try:
        signature = frame_signature()
        if signature is None:
            return False
        if output.is_file() and output.stat().st_size >= 1024:
            try:
                saved = json.loads(stamp_path.read_text(encoding="utf-8"))
                if saved.get("version") == PREVIEW_VERSION and saved.get("frames") == signature:
                    return True
            except Exception:
                _swallowed_note("preview_prefetch.build_grid_sprite_sync")
                pass
        tile_w, tile_h, cols, rows = 320, 180, 6, 6
        sheet = Image.new("RGB", (tile_w * cols, tile_h * rows), "black")
        try:
            for idx in range(FRAME_COUNT):
                path = root / f"frame_{idx + 1:02d}.jpg"
                with Image.open(path) as source:
                    frame = source.convert("RGB")
                    fitted = ImageOps.contain(frame, (tile_w, tile_h), Image.Resampling.LANCZOS)
                    tile = Image.new("RGB", (tile_w, tile_h), "black")
                    tile.paste(fitted, ((tile_w - fitted.width) // 2, (tile_h - fitted.height) // 2))
                    sheet.paste(tile, ((idx % cols) * tile_w, (idx // cols) * tile_h))
            root.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix="grid_sprite.", suffix=".tmp", dir=str(root))
            os.close(fd)
            try:
                sheet.save(tmp, format="JPEG", quality=86, optimize=True, progressive=True)
                os.replace(tmp, output)
            finally:
                try: os.unlink(tmp)
                except OSError: pass
        finally:
            sheet.close()
        if not (output.is_file() and output.stat().st_size >= 1024):
            return False
        stamp_tmp = stamp_path.with_suffix(".json.tmp")
        stamp_tmp.write_text(json.dumps({"version": PREVIEW_VERSION, "frames": signature}, separators=(",", ":")), encoding="utf-8")
        os.replace(stamp_tmp, stamp_path)
        return True
    except Exception:
        return False


def frame_times(duration: float) -> list[float]:
    d = max(0.0, float(duration or 0))
    if d <= 0:
        return []
    return [d * (0.02 + 0.96 * i / (FRAME_COUNT - 1)) for i in range(FRAME_COUNT)]


def frame_quality_sync(frames_dir: Path) -> dict:
    """Validate the exact canonical 36-frame set and count visual diversity.

    Only frame_01.jpg through frame_36.jpg belong to the canonical set. Older
    builds and interrupted jobs can leave extra frame_*.jpg files behind; those
    must not make a complete set look like 37/36 or prevent delivery forever.
    Missing/corrupt canonical files are reported explicitly and never count as
    a ready snapshot set.
    """
    from PIL import Image
    import hashlib

    root = Path(frames_dir)
    valid = []
    fingerprints = set()
    corrupt = []
    for idx in range(1, FRAME_COUNT + 1):
        path = root / f"frame_{idx:02d}.jpg"
        try:
            if not path.is_file():
                corrupt.append(path.name)
                continue
            if path.stat().st_size < MIN_FRAME_BYTES:
                corrupt.append(path.name)
                continue
            with Image.open(path) as im:
                im.verify()
            with Image.open(path) as im:
                sample = im.convert("RGB").resize((24, 14), Image.Resampling.BILINEAR)
                digest = hashlib.sha256(sample.tobytes()).digest()
            valid.append(path)
            fingerprints.add(digest)
        except Exception:
            corrupt.append(path.name)

    count = len(valid)
    distinct = len(fingerprints)
    all_indexes_present = count == FRAME_COUNT and not corrupt
    return {
        "frame_count": count,
        "distinct_frames": distinct,
        "corrupt_frames": corrupt,
        "all_indexes_present": all_indexes_present,
        "ready": all_indexes_present and count == FRAME_COUNT and distinct >= min(MIN_DISTINCT_FRAMES, count),
    }


_LOCAL_PREVIEW_REPAIR_LOCK = threading.RLock()


def _validation_receipt_ready(frames_dir: Path) -> dict | None:
    """Return a valid saved receipt using metadata-only file checks (no JPEG decode)."""
    try:
        root = Path(frames_dir)
        payload = json.loads((root / "validation.json").read_text(encoding="utf-8"))
        files = payload.get("files") or []
        if (int(payload.get("version") or 0) != PREVIEW_VERSION
                or payload.get("ready") is not True
                or int(payload.get("frame_count") or 0) < FRAME_COUNT
                or len(files) != FRAME_COUNT):
            return None
        by_name = {str(item.get("name") or ""): item for item in files if isinstance(item, dict)}
        if len(by_name) != FRAME_COUNT:
            return None
        # Stat checks catch replaced/missing files while keeping manifest requests
        # free of Pillow decoding or JPEG verification.
        for idx in range(1, FRAME_COUNT + 1):
            name = f"frame_{idx:02d}.jpg"
            item = by_name.get(name)
            if item is None:
                return None
            path = root / name
            stat = path.stat()
            if (not path.is_file() or stat.st_size < MIN_FRAME_BYTES
                    or stat.st_size != int(item.get("size") or -1)
                    or stat.st_mtime_ns != int(item.get("mtime_ns") or -1)):
                return None
        return payload
    except (OSError, ValueError, TypeError):
        return None




# ==============================================================================
# SECTION: Local preview assets
# Publishing and backfilling local preview state.
# ==============================================================================

def _publish_local_preview_state_sync(db, channel_id: int, message_id: int,
                                      frames_dir: Path, quality: dict) -> None:
    """Repair the DB preview row from a locally validated frame set."""
    rep = representative_frame_path(channel_id, message_id)
    now = _now()
    times = frame_times(0)
    with db.lock:
        media = db.conn.execute(
            "SELECT duration,width,height FROM media WHERE CAST(source_channel AS TEXT)=? "
            "AND CAST(source_message_id AS INTEGER)=? LIMIT 1",
            (str(int(channel_id)), int(message_id)),
        ).fetchone()
        old = db.conn.execute(
            "SELECT duration,width,height,frame_times,status,version,path,frames_path,frame_count "
            "FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?",
            (str(int(channel_id)), int(message_id)),
        ).fetchone()
        duration = float((media["duration"] if media else 0) or (old["duration"] if old else 0) or 0)
        width = int((media["width"] if media else 0) or (old["width"] if old else 0) or 0)
        height = int((media["height"] if media else 0) or (old["height"] if old else 0) or 0)
        times = frame_times(duration)
        correct = bool(
            old and str(old["status"] or "").upper() == "READY"
            and int(old["version"] or 0) == PREVIEW_VERSION
            and int(old["frame_count"] or 0) >= FRAME_COUNT
            and str(old["path"] or "") == str(rep)
            and str(old["frames_path"] or "") == str(frames_dir)
            and abs(float(old["duration"] or 0) - duration) < 0.01
        )
        if correct:
            try:
                saved_times = json.loads(old["frame_times"] or "[]")
                correct = isinstance(saved_times, list) and len(saved_times) == FRAME_COUNT
            except Exception:
                correct = False
        if correct:
            return
        db.conn.execute(
            """INSERT INTO timeline_preview_cache
               (source_channel,video_message_id,path,status,duration,width,height,frame_count,version,error,updated_at,frame_times,frames_path)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_channel,video_message_id) DO UPDATE SET
                 path=excluded.path,status=excluded.status,duration=excluded.duration,
                 width=excluded.width,height=excluded.height,frame_count=excluded.frame_count,
                 version=excluded.version,error=NULL,updated_at=excluded.updated_at,
                 frame_times=excluded.frame_times,frames_path=excluded.frames_path""",
            (str(int(channel_id)), int(message_id), str(rep), "READY", duration, width, height,
             int(quality.get("frame_count") or FRAME_COUNT), PREVIEW_VERSION, None, now,
             json.dumps(times, separators=(",", ":")), str(frames_dir)),
        )
        db.conn.commit()


def ensure_local_preview_assets_sync(db, channel_id: int, message_id: int) -> bool:
    """Ensure local validation receipt, sprite and DB manifest exist.

    This helper never accesses Telegram. The fast path reads only the saved
    receipt and file metadata; Pillow decodes frames only when a required local
    artifact is absent or the saved receipt is unusable.
    """
    cid, mid = int(channel_id), int(message_id)
    promote_existing_local_frames_sync(cid, mid)
    frames_dir = preview_frames_path(cid, mid)
    rep = representative_frame_path(cid, mid)
    receipt_path = frames_dir / "validation.json"
    sprite = grid_sprite_path(cid, mid)
    with _LOCAL_PREVIEW_REPAIR_LOCK:
        receipt = _validation_receipt_ready(frames_dir)
        sprite_ok = False
        try:
            sprite_ok = sprite.is_file() and sprite.stat().st_size >= 1024
        except OSError:
            pass
        if receipt and sprite_ok and rep.is_file():
            quality = {
                "frame_count": int(receipt.get("frame_count") or FRAME_COUNT),
                "distinct_frames": int(receipt.get("distinct_frames") or 0),
                "corrupt_frames": list(receipt.get("corrupt_frames") or []),
                "ready": True,
            }
            _publish_local_preview_state_sync(db, cid, mid, frames_dir, quality)
            return True

        # No rebuild of source frames occurs here. Reuse a valid saved receipt
        # when only the sprite is missing; decode/validate the 36 JPEGs only when
        # their saved validation is absent or its lightweight fingerprints fail.
        quality = load_saved_frame_validation_sync(frames_dir) if receipt else None
        if quality is None:
            quality = frame_quality_sync(frames_dir)
        if not quality.get("ready"):
            return False
        if not receipt or not load_saved_frame_validation_sync(frames_dir):
            if not save_frame_validation_sync(frames_dir, quality):
                return False
        if not sprite_ok and not build_grid_sprite_sync(frames_dir):
            return False
        if not rep.is_file():
            return False
        _publish_local_preview_state_sync(db, cid, mid, frames_dir, quality)
        return True


def backfill_existing_local_preview_assets_sync(db) -> dict:
    """Run the one-time local backfill for complete frame sets of any version."""
    root = Path(_app_config().PLAYER_CACHE_DIR) / "preview_frames"
    marker = root / ".local-preview-backfill-v1.done"
    result = {"scanned": 0, "repaired": 0, "skipped": 0, "already_complete": False}
    if marker.is_file():
        result["already_complete"] = True
        return result
    try:
        directories = list(root.glob("*/*.frames"))
    except OSError:
        return result
    keys = set()
    for frames_dir in directories:
        try:
            cid = int(frames_dir.parent.name)
            mid = int(frames_dir.name.split("_v", 1)[0])
            keys.add((cid, mid))
        except (TypeError, ValueError):
            continue
    repair_failed = False
    for cid, mid in sorted(keys):
        result["scanned"] += 1
        try:
            # Promote older-version JPEGs locally when the current version path
            # is absent/incomplete. No frames are fetched or recreated.
            if not promote_existing_local_frames_sync(cid, mid):
                result["skipped"] += 1
                # Do not hold the one-time migration open for ordinary incomplete
                # build directories; complete-but-corrupt sets remain retryable.
                possible = list((root / str(cid)).glob(f"{mid}_v*.frames"))
                if any(_has_all_local_frame_files(candidate) for candidate in possible):
                    repair_failed = True
                continue
            if ensure_local_preview_assets_sync(db, cid, mid):
                result["repaired"] += 1
            else:
                result["skipped"] += 1
                repair_failed = True
        except Exception:
            result["skipped"] += 1
            repair_failed = True
    # If a full local set could not be repaired, do not mark migration complete;
    # the next startup can retry, while request-time self-healing remains active.
    if not repair_failed:
        try:
            root.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({"version": 1, "completed_at": _now(), **result}, separators=(",", ":")), encoding="utf-8")
        except OSError:
            pass
    try:
        db.log(
            f"Local preview backfill complete: {result['repaired']} repaired, "
            f"{result['skipped']} skipped from {result['scanned']} existing frame sets; no Telegram access",
            "INFO", event="preview.local_backfill_complete", details=result,
        )
    except Exception:
        _swallowed_note("preview_prefetch.backfill_existing_local_preview_assets_sync")
        pass
    return result




# ==============================================================================
# SECTION: Work queue
# Pending rows and metadata hydration.
# ==============================================================================

def _pending_rows(db, limit: int = 0, channels: list[int] | None = None, scope: str = "missing", message_ids: list[int] | None = None, targeted: bool = False, allow_failed_retry: bool = False, operator_channel_scan: bool = False, progress=None):
    """Select media rows that need snapshot work for the given scope."""
    scope = scope if scope in {"missing", "all"} else "missing"
    # Exact operator/page targets are authoritative requests. They must not be
    # filtered out by a stale DB cache row or the background retry cooldown; the
    # canonical builder will inspect the real filesystem and either reuse,
    # locally repair, or regenerate that exact video. Automatic maintenance
    # retains the existing cooldown behavior.
    targeted = bool(targeted or message_ids)
    # Automatic maintenance only works from rows with catalogue duration. An
    # exact operator/channel target is different: the canonical Telegram
    # message is authoritative and may contain duration/dimensions even when
    # the local media row was seeded minimally by Process Channel discovery.
    # Do not silently drop an explicitly requested video just because SQLite
    # still has duration=0. The resolved Telegram message will hydrate the
    # missing catalogue metadata before extraction.
    # Automatic maintenance stays conservative about rows with unknown duration.
    # A manually requested channel audit is different: the snapshot resolver can
    # hydrate duration from the canonical Telegram message, so excluding such
    # rows here would make them permanently invisible to an explicit All scan.
    clauses = []
    if not targeted and not operator_channel_scan:
        clauses.append(
            "(COALESCE(m.duration,0)>0 OR EXISTS (SELECT 1 FROM timeline_preview_cache c0 "
            "WHERE c0.source_channel=m.source_channel AND c0.video_message_id=m.source_message_id))"
        )
    clauses.append("COALESCE(m.status,'')<>'SOURCE_DELETED'")  # V29.4.128
    apply_retry_cooldown = not targeted and not allow_failed_retry and scope != "all" and not operator_channel_scan
    if apply_retry_cooldown:
        clauses.append(_COOLDOWN_CLAUSE)
    # Bind the cutoff only when its SQL placeholder is present. Previously an
    # unbounded/manual channel scan could omit the cooldown predicate but still
    # pass this parameter, causing SQLite's "incorrect number of bindings" error.
    params: list[object] = [_retry_cutoff()] if apply_retry_cooldown else []
    if message_ids:
        marks = ",".join("?" for _ in message_ids)
        clauses.append(f"CAST(m.source_message_id AS INTEGER) IN ({marks})")
        params.extend(int(x) for x in message_ids)
    if channels:
        marks = ",".join("?" for _ in channels)
        clauses.append(f"CAST(m.source_channel AS INTEGER) IN ({marks})")
        params.extend(int(x) for x in channels)
    with db.lock:
        # media_probe was introduced after the catalogue table and may be absent
        # in a DB opened before routes are registered (e.g. a scan manager used
        # during first-run setup). Ensure the local probe cache table is present.
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS media_probe (source_channel TEXT NOT NULL,message_id INTEGER NOT NULL,"
            "info TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(source_channel,message_id))"
        )
    query = f"""SELECT m.source_channel, m.source_message_id, m.filename, m.title, m.duration, m.width, m.height,
                      m.size, m.codec, m.audio_codec, c.status AS cache_status, c.path, c.version, c.updated_at, c.frames_path, c.frame_count,
                      t.source AS thumbnail_source, mp.info AS probe_info
               FROM media m
               LEFT JOIN timeline_preview_cache c
                 ON c.source_channel=m.source_channel AND c.video_message_id=m.source_message_id
               LEFT JOIN thumbnail_cache t
                 ON t.source_channel=m.source_channel AND t.video_message_id=m.source_message_id
               LEFT JOIN media_probe mp
                 ON mp.source_channel=CAST(m.source_channel AS TEXT) AND mp.message_id=CAST(m.source_message_id AS INTEGER)
              WHERE {' AND '.join(clauses)}
              ORDER BY m.id"""
    # For missing-only scans apply the limit only after local asset inspection;
    # a run of already-READY records must not hide a stale record farther down.
    local_filter = scope == "missing" and not targeted
    if limit and not local_filter:
        query += " LIMIT ?"
        params.append(int(limit))
    with db.lock:
        rows = db.conn.execute(query, params).fetchall()
    if local_filter:
        pending = []
        reasons: list[str] = []
        for index, row in enumerate(rows, 1):
            # Each row checks about 40 local files; on a channel with thousands of videos this loop runs for minutes, so say so now and then
            # (the job's watchdog stopped the whole job after 60 s of silence).
            if progress is not None and index % 200 == 0:
                progress(index, len(rows))
            why = _scan_work_reason(row)
            if why:
                pending.append(row)
                if len(reasons) < 12:
                    reasons.append(f"{row['filename'] or row['title'] or row['source_message_id']} (message {row['source_message_id']}): {why}{_library_state_note(db, row)}")
                if limit and len(pending) >= int(limit):
                    break
        if reasons:
            try:
                db.log(f"Snapshot check: {len(pending)} video(s) need snapshot work. " + " | ".join(reasons), "INFO")
            except Exception:
                _swallowed_note("preview_prefetch._pending_rows")
        return pending
    return rows


def needs_prefetch(db) -> bool:
    return bool(_pending_rows(db, limit=1))


def _hydrate_media_metadata_sync(db, row, message):
    """Fill missing local media metadata from an already-resolved Telegram message.

    Process Channel can seed only the minimal media identity before the full
    Telegram message has been hydrated. Snapshot extraction itself already has
    the exact Message object, so reusing that object to fill duration/size/name
    avoids a second RPC and prevents a zero-duration catalogue row from being
    mistaken for a non-processable video. Existing non-zero operator/catalogue
    values are preserved.
    """
    try:
        file_obj = getattr(message, "file", None)
        if file_obj is None:
            return {}
        duration = float(getattr(file_obj, "duration", 0) or 0)
        width = int(getattr(file_obj, "width", 0) or 0)
        height = int(getattr(file_obj, "height", 0) or 0)
        size = int(getattr(file_obj, "size", 0) or 0)
        filename = str(getattr(file_obj, "name", None) or "").strip()
        metadata = {
            "duration": duration,
            "width": width,
            "height": height,
            "size": size,
            "filename": filename,
        }
        media_id = None
        try:
            media_id = int(row["source_message_id"])
            channel_id = str(row["source_channel"])
        except Exception:
            return metadata
        updates = {}
        if duration > 0 and not float(row["duration"] or 0):
            updates["duration"] = duration
        if width > 0 and not int(row["width"] or 0):
            updates["width"] = width
        if height > 0 and not int(row["height"] or 0):
            updates["height"] = height
        row_keys = set(row.keys()) if hasattr(row, "keys") else set()
        if size > 0 and "size" in row_keys and not int(row["size"] or 0):
            updates["size"] = size
        if filename and not str(row["filename"] or "").strip():
            updates["filename"] = filename
            if not str(row["title"] or "").strip():
                updates["title"] = filename
        if updates:
            assignments = ", ".join(f"{key}=?" for key in updates)
            with db.lock:
                db.conn.execute(
                    f"UPDATE media SET {assignments}, updated_at=? WHERE source_channel=? AND source_message_id=?",
                    list(updates.values()) + [_now(), channel_id, media_id],
                )
                db.conn.commit()
        return metadata
    except Exception as exc:
        try:
            db.log(f"Playback snapshot metadata hydration {message.id}: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            _swallowed_note("preview_prefetch._hydrate_media_metadata_sync")
            pass
        return {}


async def _store_representative_snapshot(db, cid: int, mid: int, frames_dir: Path):
    quality = await asyncio.to_thread(frame_quality_sync, frames_dir)
    if not quality.get("ready"):
        raise RuntimeError(
            f"Individual snapshot quality failed: {quality.get('frame_count')}/{FRAME_COUNT} frames, "
            f"{quality.get('distinct_frames')} distinct; corrupt={len(quality.get('corrupt_frames') or [])}"
        )
    representative = representative_frame_path(int(cid), int(mid))
    if not representative.is_file():
        raise RuntimeError("representative playback snapshot is missing")
    data = await asyncio.to_thread(representative.read_bytes)
    if len(data) < MIN_FRAME_BYTES:
        raise RuntimeError("representative playback snapshot is empty or truncated")
    from .thumbnail_cache import _store_thumb_sync
    await asyncio.to_thread(_store_thumb_sync, db, cid, mid, None, "image/jpeg", 0, 0, data, "playback-snapshot")




# ==============================================================================
# SECTION: Snapshot build control
# Cancellation and watchdog control for a build.
# ==============================================================================

class SnapshotBuildControl:
    """Shared state between a snapshot build thread and its job (V29.4.130).

    The build thread reports when it really starts and every time it makes
    progress (Telegram bytes received, a frame finished). The job uses that
    to time out only a build that has stalled, and ``cancel()`` makes a
    timed-out build actually stop: it closes the build's Telegram range
    readers so their pending requests are cancelled and the worker thread
    returns, instead of running on as a hidden "zombie" that blocks the next
    videos' builds (the cause of the repeated 180 s timeouts).
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.started_at = None
        self.last_activity = None
        self.bytes = 0
        self.frames = 0
        self._sources = []

    def mark_started(self):
        now = time.monotonic()
        with self._lock:
            self.started_at = now
            self.last_activity = now

    def touch(self, nbytes: int = 0, frames: int | None = None):
        with self._lock:
            if nbytes:
                self.bytes += int(nbytes)
            if frames is not None:
                self.frames = max(self.frames, int(frames))
            self.last_activity = time.monotonic()

    def attach(self, source):
        try:
            source.on_bytes = self.touch
        except Exception:
            _swallowed_note("preview_prefetch.SnapshotBuildControl.attach")
            pass
        with self._lock:
            self._sources.append(source)
        if self.cancelled.is_set():
            self._close(source)

    @staticmethod
    def _close(source):
        try:
            source.close()
        except Exception:
            _swallowed_note("preview_prefetch.SnapshotBuildControl._close")
            pass

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            sources = list(self._sources)
        for source in sources:
            self._close(source)

    def check(self):
        if self.cancelled.is_set():
            raise SnapshotBuildStopped("snapshot build stopped")


class SnapshotBuildStopped(RuntimeError):
    """A snapshot build was cancelled through its control object."""
    pass




# ==============================================================================
# SECTION: Snapshot build
# The build itself and persisted probe data.
# ==============================================================================

def _build_snapshot_sync(client, msg, display_name: str, output_path: Path, frames_dir: Path,
                         duration: float, width: int, height: int, loop, frame_workers: int = 2,
                         range_concurrency: int = 2, progress_callback=None, poster_callback=None,
                         control=None):
    """Build playback snapshots without the old ffprobe/localhost probe gate.

    Snapshots are extracted directly from the video using Telegram range reads.
    """
    if control is None:
        return _build_snapshot_body(client, msg, display_name, output_path, frames_dir,
                                    duration, width, height, loop, frame_workers,
                                    range_concurrency, progress_callback, poster_callback, None)
    control.mark_started()
    try:
        control.check()
        return _build_snapshot_body(client, msg, display_name, output_path, frames_dir,
                                    duration, width, height, loop, frame_workers,
                                    range_concurrency, progress_callback, poster_callback, control)
    finally:
        control.finished.set()



def _build_snapshot_body(client, msg, display_name, output_path, frames_dir, duration, width,
                         height, loop, frame_workers, range_concurrency, progress_callback, poster_callback, control):
    """Extract snapshot frames over Telegram ranges for one message."""
    if control is not None:
        control.check()
        control.touch()
    from pathlib import Path as _Path
    from ..core.telegram_media_range_resource import DirectTelegramRangeSource
    from ..media_manager.telegram_media_manager import (
        _smart_thumbnail_from_telegram, _ffmpeg_snapshot_frames_file, safe_video_name,
        MoovNotFoundError,
    )
    source = DirectTelegramRangeSource(client, msg, loop, range_concurrency=range_concurrency,
                                       range_request_interval=0.04)
    try:
        source.archive_x_cache_key = (int(getattr(msg, "chat_id")), int(getattr(msg, "id")))
    except (TypeError, ValueError):
        source.archive_x_cache_key = None
    if control is not None:
        control.attach(source)
        user_progress = progress_callback
        def progress_callback(done, total):
            control.touch(frames=done)
            if user_progress is not None:
                user_progress(done, total)
    try:
        suffix = _Path(safe_video_name(msg)).suffix.lower()
        if (suffix in {".mp4", ".mov", ".m4v"}
                and float(duration or 0) > 0 and int(width or 0) > 0 and int(height or 0) > 0):
            try:
                ok = _smart_thumbnail_from_telegram(
                    source, display_name, str(output_path), frames_dir=str(frames_dir),
                    keyframe_only=True, worker_count=frame_workers, progress_callback=progress_callback,
                    poster_callback=poster_callback, frame_width=640, frame_quality=95,
                )
                if not ok:
                    _LOG.info("Fast Telegram keyframe snapshot path returned no usable frames for %s; falling back to FFmpeg", display_name)
            except Exception as exc:
                # The indexed/keyframe path is an optimization. Keep the reason
                # visible once, then let FFmpeg try its bounded container path.
                _LOG.info("Fast Telegram keyframe snapshot path failed for %s: %s: %s; falling back to FFmpeg",
                          display_name, type(exc).__name__, " ".join(str(exc).split())[:300])
                ok = False
            if ok:
                return {"duration": float(duration or 0), "width": int(width or 0),
                        "height": int(height or 0), "metadata_source": "telegram-or-bmff"}
        if control is not None:
            control.check()
        # Telegram may omit DocumentAttributeVideo for generic file uploads.
        # The FFmpeg helper probes the range-backed bytes when dimensions or
        # duration are unknown and writes the discovered metadata back here.
        discovered = {"duration": float(duration or 0), "width": int(width or 0),
                      "height": int(height or 0)}
        _ffmpeg_snapshot_frames_file(
            source, display_name, str(output_path), frames_dir=str(frames_dir),
            metadata=discovered,
            worker_count=frame_workers, progress_callback=progress_callback,
            frame_width=640, frame_quality=95, control=control, poster_callback=poster_callback,
        )
        return {**discovered, "metadata_source": "ffprobe" if discovered.get("codec") else "telegram"}
    finally:
        try:
            source.close()
        except Exception:
            _swallowed_note("preview_prefetch._build_snapshot_body")
            pass

def _set_state(db, cid: int, mid: int, *, status: str, path: Path, duration=0, width=0, height=0,
               error=None, frames_path: Path | None = None, frame_count: int | None = None):
    if frame_count is None:
        try:
            frame_count = int(frame_quality_sync(Path(frames_path or "")).get("frame_count") or 0)
        except Exception:
            frame_count = 0
    times = frame_times(duration)
    with db.lock:
        db.conn.execute(
            """INSERT INTO timeline_preview_cache
               (source_channel,video_message_id,path,status,duration,width,height,frame_count,version,error,updated_at,frame_times,frames_path)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source_channel,video_message_id) DO UPDATE SET
                 path=excluded.path,status=excluded.status,duration=excluded.duration,
                 width=excluded.width,height=excluded.height,frame_count=excluded.frame_count,
                 version=excluded.version,error=excluded.error,updated_at=excluded.updated_at,
                 frame_times=excluded.frame_times,frames_path=excluded.frames_path""",
            (str(cid), int(mid), str(path), str(status), float(duration or 0), int(width or 0), int(height or 0),
             max(0, int(frame_count or 0)), PREVIEW_VERSION, error, _now(),
             json.dumps(times, separators=(",", ":")), str(frames_path or "")),
        )
        db.conn.commit()

def _persist_snapshot_probe_metadata_sync(db, cid: int, mid: int, message, metadata: dict) -> dict:
    """Persist FFprobe-derived metadata for Telegram videos without attributes."""
    import mimetypes
    file_obj = getattr(message, "file", None)
    name = str(getattr(file_obj, "name", None) or f"Telegram video {mid}")
    duration = float(metadata.get("duration") or 0)
    width = int(metadata.get("width") or 0)
    height = int(metadata.get("height") or 0)
    codec = str(metadata.get("codec") or metadata.get("vcodec") or "").strip().lower()
    fps = float(metadata.get("fps") or 0)
    size = int(getattr(file_obj, "size", 0) or 0)
    mime = str(getattr(file_obj, "mime_type", None) or mimetypes.guess_type(name)[0] or "application/octet-stream")
    if duration <= 0 or width <= 0 or height <= 0 or not codec:
        return {}
    info = {"size": size, "mime": mime, "name": name, "vcodec": codec, "acodec": "",
            "width": width, "height": height, "duration": duration, "fps": fps,
            "direct_likely": False, "index_cache": False,
            "metadata_source": "ffprobe-snapshot"}
    try:
        from ..playback import media_tools
        info.update(media_tools.browser_verdict(info, mime))
    except Exception:
        _swallowed_note("preview_prefetch._persist_snapshot_probe_metadata_sync")
        pass
    now = _now()
    with db.lock:
        db.conn.execute(
            "CREATE TABLE IF NOT EXISTS media_probe (source_channel TEXT NOT NULL,message_id INTEGER NOT NULL,"
            "info TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(source_channel,message_id))"
        )
        db.conn.execute(
            "INSERT OR REPLACE INTO media_probe(source_channel,message_id,info,updated_at) VALUES(?,?,?,?)",
            (str(int(cid)), int(mid), json.dumps(info), now),
        )
        db.conn.execute(
            "UPDATE media SET duration=CASE WHEN ?>0 THEN ? ELSE duration END,"
            "width=CASE WHEN ?>0 THEN ? ELSE width END,height=CASE WHEN ?>0 THEN ? ELSE height END,"
            "codec=COALESCE(NULLIF(?,''),codec),fps=CASE WHEN ?>0 THEN ? ELSE fps END,"
            "updated_at=? WHERE CAST(source_channel AS TEXT)=? AND CAST(source_message_id AS INTEGER)=?",
            (duration, duration, width, width, height, height, codec, fps, fps, now, str(int(cid)), int(mid)),
        )
        db.conn.commit()
    return info


def _persist_index_probe_sync(db, cid: int, mid: int, row=None, message=None) -> bool:
    """Copy scanned MP4 technical metadata into media_probe and media columns."""
    try:
        from ..core.playback_index_cache import read_manifest
        size = 0
        try: size = int(row["size"] or 0) if row is not None else 0
        except Exception:
            _swallowed_note("preview_prefetch._persist_index_probe_sync")
            pass
        if not size and message is not None:
            size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        meta = read_manifest(int(cid), int(mid), size if size > 0 else None)
        if not meta:
            return False
        width, height = int(meta.get("width") or 0), int(meta.get("height") or 0)
        duration, fps = float(meta.get("duration") or 0), float(meta.get("fps") or 0)
        codec = str(meta.get("codec") or "").strip().lower()
        mime = str(getattr(getattr(message, "file", None), "mime_type", "") or "video/mp4")
        name = str(getattr(getattr(message, "file", None), "name", "") or "")
        if row is not None:
            try: name = name or str(row["filename"] or row["title"] or "")
            except Exception:
                _swallowed_note("preview_prefetch._persist_index_probe_sync")
                pass
        info = {"size": int(meta.get("source_size") or size or 0), "mime": mime, "name": name,
                "vcodec": codec, "acodec": "", "width": width, "height": height,
                "duration": duration, "fps": fps, "direct_likely": True,
                "index_cache": True, "moov_offset": int(meta.get("moov_offset") or 0),
                "moov_size": int(meta.get("moov_size") or 0)}
        try:
            from ..playback import media_tools
            info.update(media_tools.browser_verdict(info, mime))
        except Exception:
            _swallowed_note("preview_prefetch._persist_index_probe_sync")
            pass
        with db.lock:
            db.conn.execute("CREATE TABLE IF NOT EXISTS media_probe (source_channel TEXT NOT NULL,message_id INTEGER NOT NULL,info TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(source_channel,message_id))")
            db.conn.execute("INSERT OR REPLACE INTO media_probe(source_channel,message_id,info,updated_at) VALUES(?,?,?,?)",
                            (str(int(cid)), int(mid), json.dumps(info), _now()))
            db.conn.execute(
                "UPDATE media SET codec=COALESCE(NULLIF(?,''),codec), width=CASE WHEN ?>0 THEN ? ELSE width END,"
                " height=CASE WHEN ?>0 THEN ? ELSE height END, duration=CASE WHEN ?>0 THEN ? ELSE duration END,"
                " fps=CASE WHEN ?>0 THEN ? ELSE fps END WHERE source_channel=? AND source_message_id=?",
                (codec, width, width, height, height, duration, duration, fps, fps, str(int(cid)), int(mid)))
            db.conn.commit()
        return True
    except Exception:
        return False




# ==============================================================================
# SECTION: Job runner
# Build orchestration, the job entry point and coverage stats.
# ==============================================================================

def _governor_wait_reason(ctx) -> str:
    """Why background Telegram reads are legitimately held back right now."""
    governor = getattr(getattr(getattr(ctx, "telegram", None), "client", None), "_telegram_archive_governor", None)
    if governor is None:
        return ""
    try:
        snap = governor.snapshot()
    except Exception:
        return ""
    if snap.get("traffic_mode") == "PLAYBACK_PRIORITY":
        return "a video is playing (background reads wait)"
    flood = float(snap.get("flood_wait_remaining") or 0)
    if flood > 0:
        return f"Telegram FloodWait {flood:.0f}s left"
    cooldown = float(snap.get("cooldown_remaining") or 0)
    if cooldown > 0:
        return f"network cooldown {cooldown:.0f}s left"
    if getattr(governor, "_background_paused", False):
        return "background Telegram work is paused"
    return ""


async def _run_snapshot_build(ctx, func, *args, stall_seconds: float | None = None,
                              max_active_seconds: float | None = None,
                              max_wall_seconds: float | None = None,
                              worker_wait_seconds: float | None = None,
                              poll_seconds: float = 1.0):
    """Run one snapshot build with stall-based timing (V29.4.130).

    * The clock starts when the build actually begins on a worker thread, not
      when it was queued.
    * The build fails only when it makes no progress for ``stall_seconds`` of
      running time; time the governor holds background reads back (FloodWait,
      network cooldown, playback priority) does not count.
    * On timeout, cancel or job stop, the build is told to stop and its
      Telegram readers are closed, and we wait (bounded) for the worker thread
      to return before giving its slot back.
    """
    stall = PREVIEW_BUILD_TIMEOUT_SECONDS if stall_seconds is None else float(stall_seconds)
    max_active = PREVIEW_BUILD_MAX_ACTIVE_SECONDS if max_active_seconds is None else float(max_active_seconds)
    max_wall = SNAPSHOT_VIDEO_MAX_SECONDS if max_wall_seconds is None else float(max_wall_seconds)
    worker_wait = PREVIEW_WORKER_WAIT_SECONDS if worker_wait_seconds is None else float(worker_wait_seconds)
    control = SnapshotBuildControl()
    if _accepts_control(func):
        task = asyncio.ensure_future(ctx.blocking(func, *args, control=control))
    else:
        # A build function without progress reporting: treat it as started
        # at once so only the overall work limit applies to it.
        control.mark_started()
        stall = max(stall, max_active)
        task = asyncio.ensure_future(ctx.blocking(func, *args))
    queued_at = time.monotonic()
    active = 0.0            # working time (not held back by the governor)
    wall_active = 0.0       # wall time, including throttling; playback hold-back is excluded
    idle = 0.0              # working time since the build's last progress
    last_tick = None
    last_seen_activity = None

    async def _stop(reason: str):
        control.cancel()
        done, _ = await asyncio.wait({task}, timeout=PREVIEW_CANCEL_GRACE_SECONDS)
        if not done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:
                ctx.db.log("Snapshot build thread is still finishing after it was stopped; "
                           "its slot was released anyway", "WARN")
            except Exception:
                _swallowed_note("preview_prefetch._run_snapshot_build._stop")
                pass
        else:
            _consume(task)
        raise TimeoutError(reason)

    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if done:
                return task.result()
            now = time.monotonic()
            waiting = _governor_wait_reason(ctx)
            if control.started_at is None:
                if waiting:
                    queued_at = now
                elif now - queued_at >= worker_wait:
                    await _stop(f"snapshot build waited {worker_wait:.0f}s for a free worker "
                                "(earlier builds were still running)")
                continue
            elapsed = 0.0 if last_tick is None else max(0.0, now - last_tick)
            last_tick = now
            playback_holdback = waiting.startswith("a video is playing")
            if not playback_holdback:
                wall_active += elapsed
            if wall_active >= max_wall:
                await _stop(
                    f"snapshot build exceeded {max_wall:.0f}s wall-clock budget "
                    f"excluding playback hold-back ({control.frames}/{FRAME_COUNT} frames, "
                    f"{control.bytes / 1048576:.1f} MiB received)"
                )
            activity = control.last_activity
            if activity != last_seen_activity:
                last_seen_activity = activity
                idle = 0.0
            elif not waiting:
                idle += elapsed
            if waiting:
                # Legitimate hold-back: the clocks pause (they do not restart,
                # so frequent short FloodWaits cannot hide a stuck build).
                continue
            active += elapsed
            if idle >= stall:
                await _stop(
                    f"snapshot build made no progress for {stall:.0f}s "
                    f"({control.frames}/{FRAME_COUNT} frames, {control.bytes / 1048576:.1f} MiB received)"
                )
            if active >= max_active:
                await _stop(
                    f"snapshot build still unfinished after {max_active:.0f}s of work "
                    f"({control.frames}/{FRAME_COUNT} frames, {control.bytes / 1048576:.1f} MiB received)"
                )
    except (JobCancelled, asyncio.CancelledError):
        control.cancel()
        if not task.done():
            done, _ = await asyncio.wait({task}, timeout=PREVIEW_CANCEL_GRACE_SECONDS)
            if not done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        _consume(task)
        raise


def _accepts_control(func) -> bool:
    import inspect
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "control" or p.kind is inspect.Parameter.VAR_KEYWORD for p in params)


def _consume(task):
    """Retrieve a finished task's error so asyncio does not warn about it."""
    if task.done() and not task.cancelled():
        try:
            task.exception()
        except BaseException:
            pass


def _probe_info_sync(db, cid: int, mid: int) -> dict:
    try:
        with db.lock:
            row = db.conn.execute(
                "SELECT info FROM media_probe WHERE source_channel=? AND message_id=?",
                (str(int(cid)), int(mid)),
            ).fetchone()
        if row:
            value = json.loads(row["info"] or "{}")
            return value if isinstance(value, dict) else {}
    except Exception:
        _swallowed_note("preview_prefetch._probe_info_sync")
        pass
    return {}


def _ensure_playback_cache_from_message_sync(db, client, message, loop, row, cid: int, mid: int) -> tuple[bool, bool]:
    """Create only missing index/probe assets for a video with valid local frames.

    This is called only from an explicit Metadata & Assets job and runs in a
    worker thread. It parses the MP4 moov/sample tables and persists the start
    ranges; it does not decode or regenerate the 36 preview frames.
    """
    f = getattr(message, "file", None)
    size = int(getattr(f, "size", 0) or (row["size"] if row is not None else 0) or 0)
    try:
        from ..core.playback_index_cache import persist_telegram_location
        persist_telegram_location(cid, mid, message, size)
    except Exception:
        _swallowed_note("preview_prefetch._ensure_playback_cache_from_message_sync")
        pass
    name = str(getattr(f, "name", "") or "")
    if not name and row is not None:
        try:
            name = str(row["filename"] or row["title"] or "")
        except Exception:
            _swallowed_note("preview_prefetch._ensure_playback_cache_from_message_sync")
            pass
    suffix = Path(name).suffix.lower()
    requires_index = suffix in {".mp4", ".mov", ".m4v"}
    index_meta = None
    if requires_index:
        try:
            from ..core.playback_index_cache import read_manifest, persist_index_segments
            index_meta = read_manifest(cid, mid, size if size > 0 else None)
            index_ok = _mp4_index_cache_ready(cid, mid, size, index_meta)
            if not index_ok:
                from ..core.telegram_media_range_resource import DirectTelegramRangeSource
                from ..media_manager.telegram_media_manager import _load_moov, _parse_visual_config
                source = DirectTelegramRangeSource(client, message, loop, range_concurrency=2,
                                                   range_request_interval=0.04)
                source.archive_x_cache_key = (int(cid), int(mid))
                try:
                    moov_bytes, moov_offset = _load_moov(source)
                    parsed = _parse_visual_config(moov_bytes)
                    index_meta = persist_index_segments(source, (int(cid), int(mid)), parsed,
                                                        moov_bytes, moov_offset)
                finally:
                    try:
                        source.close()
                    except Exception:
                        _swallowed_note("preview_prefetch._ensure_playback_cache_from_message_sync")
                        pass
                index_ok = _mp4_index_cache_ready(cid, mid, size, index_meta)
            if index_ok:
                _persist_index_probe_sync(db, cid, mid, row, message)
        except Exception:
            index_ok = False
    else:
        index_ok = True

    info = _probe_info_sync(db, cid, mid)
    if not _saved_probe_ready(info):
        # The MP4 index provides exact stream codec/dimensions/duration without
        # an ffprobe pass on Play. _persist_index_probe_sync normally writes this;
        # this fallback also persists Telegram's own attributes for non-MP4s.
        width = int(getattr(f, "width", 0) or (row["width"] if row is not None else 0) or 0)
        height = int(getattr(f, "height", 0) or (row["height"] if row is not None else 0) or 0)
        duration = float(getattr(f, "duration", 0) or (row["duration"] if row is not None else 0) or 0)
        codec = str((index_meta or {}).get("codec") or "")
        if not codec and row is not None:
            try:
                codec = str(row["codec"] or "")
            except Exception:
                codec = ""
        if codec and width > 0 and height > 0 and duration > 0:
            info = {"size": size, "mime": str(getattr(f, "mime_type", "") or "video/mp4"),
                    "name": name, "vcodec": codec, "acodec": "", "width": width,
                    "height": height, "duration": duration,
                    "fps": float((index_meta or {}).get("fps") or 0),
                    "direct_likely": True, "index_cache": bool(index_ok),
                    "moov_offset": int((index_meta or {}).get("moov_offset") or 0),
                    "moov_size": int((index_meta or {}).get("moov_size") or 0)}
            with db.lock:
                db.conn.execute(
                    "INSERT OR REPLACE INTO media_probe(source_channel,message_id,info,updated_at) VALUES(?,?,?,?)",
                    (str(int(cid)), int(mid), json.dumps(info), _now()),
                )
                db.conn.execute(
                    "UPDATE media SET codec=COALESCE(NULLIF(?,''),codec),width=CASE WHEN ?>0 THEN ? ELSE width END,"
                    "height=CASE WHEN ?>0 THEN ? ELSE height END,duration=CASE WHEN ?>0 THEN ? ELSE duration END,"
                    "fps=CASE WHEN ?>0 THEN ? ELSE fps END WHERE CAST(source_channel AS TEXT)=? "
                    "AND CAST(source_message_id AS INTEGER)=?",
                    (codec, width, width, height, height, duration, duration, info["fps"], info["fps"], str(int(cid)), int(mid)),
                )
                db.conn.commit()
    info = _probe_info_sync(db, cid, mid)
    return bool(_saved_probe_ready(info)), bool(index_ok)


async def _build_one(ctx, row, *, message=None, frame_workers: int = 2, range_concurrency: int = 2, force_rebuild: bool = False):
    """Build, validate and publish the snapshot set for one row."""
    cid, mid = int(row["source_channel"]), int(row["source_message_id"])
    frames_dir = preview_frames_path(cid, mid)
    out = representative_frame_path(cid, mid)
    quality = await asyncio.to_thread(load_saved_frame_validation_sync, frames_dir)
    if quality is None:
        quality = await asyncio.to_thread(frame_quality_sync, frames_dir)
        if quality.get("ready"):
            await asyncio.to_thread(save_frame_validation_sync, frames_dir, quality)
    video_name = str(row["title"] or row["filename"] or f"Telegram video {mid}")
    common = {"channel_id": cid, "message_id": mid, "filename": video_name,
              "frame_count": int(quality.get("frame_count") or 0),
              "frames_ready": bool(quality.get("ready")), "representative_path": str(out),
              "frames_path": str(frames_dir)}
    # On older installations, a complete snapshot set may predate the durable
    # MP4 index cache. During an explicit Metadata & Assets scan, regenerate the
    # source index in that case rather than declaring the cache complete and
    # making the first Play request fetch the moov/start ranges.
    suffix = Path(str(row["filename"] or row["title"] or "")).suffix.lower()
    needs_mp4_index = suffix in {".mp4", ".mov", ".m4v"}
    index_ready = not needs_mp4_index
    if needs_mp4_index:
        try:
            from ..core.playback_index_cache import read_manifest
            source_size = int(row["size"] or 0)
            saved_index = await asyncio.to_thread(read_manifest, cid, mid, source_size if source_size > 0 else None)
            index_ready = await asyncio.to_thread(_mp4_index_cache_ready, cid, mid, source_size, saved_index)
        except Exception:
            index_ready = False
    local_frames_ready = bool(quality.get("ready") and out.is_file())
    if local_frames_ready and not force_rebuild:
        # Existing 36-frame sets are authoritative. Repair their local receipt
        # and sprite first; never regenerate valid frames just because a separate
        # playback index or probe cache is missing.
        local_assets_ready = await asyncio.to_thread(ensure_local_preview_assets_sync, ctx.db, cid, mid)
        if not local_assets_ready:
            return False
        probe_ready = _saved_probe_ready(row["probe_info"] if "probe_info" in row.keys() else None)
        try:
            from ..core.playback_index_cache import read_telegram_location
            location_ready = await asyncio.to_thread(read_telegram_location, cid, mid, None) is not None
        except Exception:
            location_ready = False
        if not (probe_ready and index_ready and location_ready):
            msg = message
            if msg is None:
                msg = await ctx.cancellable(
                    lambda: ctx.telegram.client.get_messages(cid, ids=mid),
                    timeout=PREVIEW_MESSAGE_TIMEOUT_SECONDS, activity=True,
                )
            if msg is not None:
                probe_ready, index_ready = await asyncio.to_thread(
                    _ensure_playback_cache_from_message_sync, ctx.db, ctx.telegram.client, msg,
                    asyncio.get_running_loop(), row, cid, mid,
                )
                try:
                    from ..core.playback_index_cache import read_telegram_location
                    location_ready = await asyncio.to_thread(read_telegram_location, cid, mid, None) is not None
                except Exception:
                    location_ready = False
        if not await asyncio.to_thread(build_grid_sprite_sync, frames_dir):
            raise RuntimeError("Could not create the local 36-frame grid sprite")
        if not probe_ready:
            # Keep snapshots READY and usable while retaining this video as
            # pending for Metadata & Assets retry; do not replace valid frames.
            _set_state(ctx.db, cid, mid, status="READY", path=out, duration=row["duration"],
                       width=row["width"], height=row["height"], frames_path=frames_dir,
                       frame_count=quality["frame_count"])
            return False
        if not index_ready or not location_ready:
            _set_state(ctx.db, cid, mid, status="READY", path=out, duration=row["duration"],
                       width=row["width"], height=row["height"], frames_path=frames_dir,
                       frame_count=quality["frame_count"])
            return False
        await asyncio.to_thread(_persist_index_probe_sync, ctx.db, cid, mid, row, message)
        _job_activity(ctx, f"Reusing validated Telegram snapshot cache for {video_name}",
                      operation="Snapshots · reuse existing 36-frame set", current_item=video_name,
                      event="telegram_snapshot.cache_reuse_started", details=common)
        await _store_representative_snapshot(ctx.db, cid, mid, frames_dir)
        _set_state(ctx.db, cid, mid, status="READY", path=out, duration=row["duration"],
                   width=row["width"], height=row["height"], frames_path=frames_dir,
                   frame_count=quality["frame_count"])
        _job_activity(ctx, f"Validated 36 cached Telegram snapshot frames for {video_name}",
                      operation="Snapshots · cache ready", current_item=video_name,
                      event="telegram_snapshot.cache_ready", details={**common, "frame_count": int(quality["frame_count"])})
        return True

    await ctx.checkpoint()
    _job_activity(ctx, f"Preparing Telegram snapshot extraction for {video_name}",
                  operation="Snapshots · initialize frame extraction", current_item=video_name,
                  event="telegram_snapshot.preparation_started", details=common)
    msg = message
    build_dir = frames_dir.with_name(frames_dir.name + ".building")
    tmp_rep = build_dir / f"frame_{REPRESENTATIVE_FRAME_INDEX + 1:02d}.jpg"
    try:
        _set_state(ctx.db, cid, mid, status="PREPARING", path=out, duration=row["duration"],
                   width=row["width"], height=row["height"], frames_path=frames_dir,
                   frame_count=quality.get("frame_count", 0))
        if msg is None:
            _job_activity(ctx, f"Fetching Telegram video message for {video_name}",
                          operation="Snapshots · resolve Telegram source", current_item=video_name,
                          event="telegram_snapshot.message_lookup_started", details=common)
            msg = await ctx.cancellable(lambda: ctx.telegram.client.get_messages(cid, ids=mid),
                                        timeout=PREVIEW_MESSAGE_TIMEOUT_SECONDS, activity=True)
        if not msg:
            raise RuntimeError("video message not found")
        try:
            from ..core.playback_index_cache import persist_telegram_location
            await asyncio.to_thread(persist_telegram_location, cid, mid, msg,
                                    int(getattr(getattr(msg, "file", None), "size", 0) or 0))
        except Exception:
            _swallowed_note("preview_prefetch._build_one")
            pass
        display_name = str(getattr(getattr(msg, "file", None), "name", None) or f"video {mid}")
        _job_activity(ctx, f"Telegram source resolved for snapshot extraction: {display_name}",
                      operation="Snapshots · source ready", current_item=display_name,
                      event="telegram_snapshot.source_ready", details={**common, "source_filename": display_name,
                      "duration_seconds": float(row["duration"] or 0), "width": int(row["width"] or 0),
                      "height": int(row["height"] or 0)})
        import shutil
        shutil.rmtree(build_dir, ignore_errors=True)
        build_dir.mkdir(parents=True, exist_ok=True)
        loop = asyncio.get_running_loop()
        import threading
        from io import BytesIO
        provisional_poster_published = threading.Event()
        provisional_poster_lock = threading.Lock()

        def _publish_provisional_poster(image):
            """Publish the first valid frame as a card image without marking timeline READY."""
            if provisional_poster_published.is_set():
                return True
            with provisional_poster_lock:
                if provisional_poster_published.is_set():
                    return True
                try:
                    from .thumbnail_cache import _store_thumb_sync
                    output = BytesIO()
                    image.convert("RGB").save(output, format="JPEG", quality=95, optimize=False)
                    data = output.getvalue()
                    if len(data) < MIN_FRAME_BYTES:
                        return False
                    out.parent.mkdir(parents=True, exist_ok=True)
                    temp_path = out.with_name(out.name + ".poster.tmp")
                    try:
                        with open(temp_path, "wb") as handle:
                            handle.write(data)
                            handle.flush()
                        os.replace(str(temp_path), str(out))
                    finally:
                        temp_path.unlink(missing_ok=True)
                    # Keep the thumbnail usable immediately, but do not mark
                    # the 36-frame timeline READY until its full validation.
                    _store_thumb_sync(ctx.db, cid, mid, None, "image/jpeg", 0, 0,
                                      data, "playback-snapshot", mark_snapshot=False)
                    provisional_poster_published.set()
                    def _announce_poster():
                        try:
                            _job_activity(ctx, f"Representative preview available while timeline snapshots continue for {video_name}",
                                          operation="Snapshots · poster ready; timeline continues",
                                          current_item=video_name, event="telegram_snapshot.poster_ready",
                                          details={**common, "provisional": True, "frame_count": 0})
                        except Exception:
                            _swallowed_note("preview_prefetch._build_one._publish_provisional_poster._announce_poster")
                            pass
                    loop.call_soon_threadsafe(_announce_poster)
                    return True
                except Exception as exc:
                    try:
                        _LOG.info("Could not publish provisional snapshot poster for %s: %s: %s",
                                  video_name, type(exc).__name__, str(exc)[:240])
                    except Exception:
                        _swallowed_note("preview_prefetch._build_one._publish_provisional_poster")
                        pass
                    return False

        def _frame_progress(done: int, total: int):
            done_i, total_i = int(done), int(total)
            def _emit_frame_progress():
                try:
                    _job_activity(ctx, f"Telegram snapshot frame {done_i:02d}/{total_i:02d} decoded for {display_name}",
                                  operation=f"Snapshots · Telegram frame {done_i:02d}/{total_i:02d}",
                                  current_item=display_name, event="telegram_snapshot.frame_completed",
                                  details={**common, "source_filename": display_name,
                                           "frame_index": done_i, "frame_count": total_i},
                                  level="INFO")
                    if getattr(ctx, "pipeline", None) is not None:
                        ctx.pipeline.heartbeat(activity=True)
                except Exception:
                    _swallowed_note("preview_prefetch._build_one._frame_progress._emit_frame_progress")
                    pass
            try:
                loop.call_soon_threadsafe(_emit_frame_progress)
            except RuntimeError:
                pass
        _job_activity(ctx, f"Generating 36 individual snapshot frames from Telegram for {display_name}",
                      operation="Snapshots · poster-first, then 36-frame timeline", current_item=display_name,
                      event="telegram_snapshot.extraction_started", details={**common, "source_filename": display_name,
                      "requested_frames": FRAME_COUNT, "frame_workers": frame_workers,
                      "range_concurrency": range_concurrency}, level="INFO")
        discovered_metadata = await _run_snapshot_build(
            ctx, _build_snapshot_sync, ctx.telegram.client, msg, display_name, tmp_rep,
            build_dir, float(row["duration"] or 0), int(row["width"] or 0), int(row["height"] or 0),
            asyncio.get_running_loop(), frame_workers, range_concurrency, _frame_progress,
            _publish_provisional_poster,
        )
        resolved_duration = float(row["duration"] or 0)
        resolved_width = int(row["width"] or 0)
        resolved_height = int(row["height"] or 0)
        if isinstance(discovered_metadata, dict):
            resolved_duration = float(discovered_metadata.get("duration") or resolved_duration)
            resolved_width = int(discovered_metadata.get("width") or resolved_width)
            resolved_height = int(discovered_metadata.get("height") or resolved_height)
            try:
                await asyncio.to_thread(_persist_snapshot_probe_metadata_sync, ctx.db, cid, mid, msg, discovered_metadata)
            except Exception as metadata_exc:
                _LOG.warning("Could not persist probed media metadata for %s: %s", display_name, metadata_exc)
        common.update({"duration": resolved_duration, "width": resolved_width, "height": resolved_height})
        _job_activity(ctx, f"Validating extracted Telegram snapshot frames for {display_name}",
                      operation="Snapshots · validate extracted frames", current_item=display_name,
                      event="telegram_snapshot.validation_started", details={**common, "build_directory": str(build_dir)})
        built_quality = await asyncio.to_thread(frame_quality_sync, build_dir)
        if not built_quality.get("ready"):
            raise RuntimeError(
                f"Snapshot quality validation failed: {built_quality.get('frame_count')}/{FRAME_COUNT} frames, "
                f"{built_quality.get('distinct_frames')} distinct, {len(built_quality.get('corrupt_frames') or [])} corrupt"
            )
        await asyncio.to_thread(save_frame_validation_sync, build_dir, built_quality)
        if not await asyncio.to_thread(build_grid_sprite_sync, build_dir):
            raise RuntimeError("Could not create the local 36-frame grid sprite")
        await asyncio.to_thread(_persist_index_probe_sync, ctx.db, cid, mid, row, msg)
        # Publish individual snapshots and the sprite with rollback protection. A completed
        # temporary set replaces the old one; if directory publication fails,
        # restore the old snapshot instead of erasing the last usable cache.
        frames_dir.parent.mkdir(parents=True, exist_ok=True)
        backup_dir = frames_dir.with_name(frames_dir.name + ".previous")
        shutil.rmtree(backup_dir, ignore_errors=True)
        had_previous = frames_dir.exists()
        if had_previous:
            os.replace(str(frames_dir), str(backup_dir))
        try:
            os.replace(str(build_dir), str(frames_dir))
        except Exception:
            if had_previous and backup_dir.exists() and not frames_dir.exists():
                os.replace(str(backup_dir), str(frames_dir))
            raise
        else:
            shutil.rmtree(backup_dir, ignore_errors=True)
        out = representative_frame_path(cid, mid)
        _set_state(ctx.db, cid, mid, status="READY", path=out, duration=resolved_duration,
                   width=resolved_width, height=resolved_height, frames_path=frames_dir,
                   frame_count=built_quality["frame_count"])
        await _store_representative_snapshot(ctx.db, cid, mid, frames_dir)
        _job_activity(ctx, f"Telegram snapshot set validated and published for {display_name}",
                      operation="Snapshots · 36-frame set ready", current_item=display_name,
                      event="telegram_snapshot.ready", details={**common, "source_filename": display_name,
                      "frame_count": int(built_quality["frame_count"]), "distinct_frames": int(built_quality.get("distinct_frames") or 0),
                      "representative_path": str(out), "frames_path": str(frames_dir)})
        return True
    except (JobCancelled, asyncio.CancelledError):
        _job_activity(ctx, f"Telegram snapshot extraction interrupted for {video_name}",
                      operation="Snapshots · interrupted at safe checkpoint", current_item=video_name,
                      event="telegram_snapshot.interrupted", level="WARN", details=common)
        raise
    except Exception as exc:
        try:
            await asyncio.to_thread(_persist_index_probe_sync, ctx.db, cid, mid, row, msg)
        except Exception:
            _swallowed_note("preview_prefetch._build_one")
            pass
        _job_activity(ctx, f"Telegram snapshot generation failed for {video_name}: {type(exc).__name__}",
                      operation="Snapshots · extraction failed", current_item=video_name,
                      event="telegram_snapshot.failed", level="ERROR", details={**common,
                      "error_type": type(exc).__name__, "error": str(exc)[:600]})
        _set_state(ctx.db, cid, mid, status="FAILED", path=out, duration=locals().get("resolved_duration", row["duration"]),
                   width=locals().get("resolved_width", row["width"]), height=locals().get("resolved_height", row["height"]), frames_path=frames_dir,
                   frame_count=quality.get("frame_count", 0), error=f"{type(exc).__name__}: {exc}"[:500])
        ctx.db.log(f"Individual preview frames {cid}/{mid} failed: {type(exc).__name__}: {exc}", "WARN")
        return False
    finally:
        tmp_rep.unlink(missing_ok=True)
        try:
            import shutil
            shutil.rmtree(build_dir, ignore_errors=True)
        except Exception:
            _swallowed_note("preview_prefetch._build_one")
            pass


async def run(ctx):
    """Job entry: build snapshots for pending rows with bounded concurrency."""
    detail = {}
    try:
        raw = ctx.job.get("detail") or "{}"
        detail = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        detail = {}
    channels = []
    for value in detail.get("channels") or detail.get("thumbnail_channels") or []:
        try:
            channels.append(int(value))
        except Exception:
            _swallowed_note("preview_prefetch.run")
            continue
    channels = list(dict.fromkeys(channels))
    message_ids = []
    for value in detail.get("message_ids") or []:
        try:
            message_ids.append(int(value))
        except Exception:
            _swallowed_note("preview_prefetch.run")
            continue
    message_ids = list(dict.fromkeys(message_ids))
    try:
        limit = max(0, min(5000, int(detail.get("limit", detail.get("thumbnail_limit", 50)))))
    except Exception:
        limit = 50
    if not bool(detail.get("unbounded", False)) and limit == 0:
        limit = 50
    scope = str(detail.get("scope", detail.get("thumbnail_scope", "missing")) or "missing").strip().casefold()
    if scope not in {"missing", "all"}:
        scope = "missing"
    # ``all`` means "inspect the entire selected set", not "destroy and
    # regenerate everything". Every scan is reuse-first. A complete local
    # snapshot is authoritative regardless of whether the operator selected
    # Missing or All. Rebuilds happen only when the local asset set is
    # incomplete/invalid.
    force_rebuild = False
    if message_ids:
        effective_limit = limit or len(message_ids)
    elif channels:
        # Operator runs are bounded by default; the UI sets unbounded=true only for an explicit full run.
        effective_limit = limit
    else:
        # Idle maintenance stays incremental.
        effective_limit = limit or MAX_ITEMS_PER_RUN
    targeted = bool(message_ids)
    loop = asyncio.get_running_loop()

    def _checking(done: int, of: int) -> None:
        # called from the worker thread: report on the loop, which also tells the watchdog the job is alive
        loop.call_soon_threadsafe(lambda: ctx.report(operation=f"Checking local snapshots · {done:,} of {of:,} videos"))

    rows=await asyncio.to_thread(_pending_rows, ctx.db, effective_limit, channels or None, scope, message_ids or None, targeted,
                                 bool(detail.get("unbounded", False) or detail.get("retry_failed", False)),
                                 bool(detail.get("operator_channel_scan", False)), _checking)
    if message_ids:
        # A targeted user request is a priority lane over ordinary background
        # maintenance. Preserve the requested message order instead of letting
        # the database's global ID order place unrelated videos first.
        target_order = {int(mid): pos for pos, mid in enumerate(message_ids)}
        channel_order = {int(cid): pos for pos, cid in enumerate(channels)}
        rows.sort(key=lambda row: (channel_order.get(int(row["source_channel"]), len(channel_order)),
                                   target_order.get(int(row["source_message_id"]), len(target_order))))
    total=len(rows)
    if not total:
        ctx.report(completed=0,total=0,progress=100,operation="Timeline preview cache already warm",force=True)
        await asyncio.to_thread(_record_snapshot_result, ctx, channels, message_ids, 0, 0)
        return

    # Resolve Telegram messages in batches before starting media extraction.
    # The previous implementation performed one get_messages() RPC per video.
    # When Telegram control-plane latency increased, every item consumed the
    # full 30-second cancellable timeout and was reported as a preview failure,
    # even though the media/range extractor itself had never started.
    # Telethon accepts a list of message IDs, so one bounded RPC per channel
    # eliminates the serial timeout cascade and also keeps playback/range work
    # out of the message-resolution phase.
    message_map: dict[tuple[int, int], object] = {}
    message_resolution_failed: set[tuple[int, int]] = set()

    async def _resolve_messages(chunk_rows):
        """Fetch Telegram messages for a chunk of rows that need remote work."""
        # V29.4.128: resolve only the next small group of videos, right before
        # their snapshots are made. Resolving a whole large channel up front
        # (4,565 videos = 92 back-to-back requests) triggered Telegram
        # FloodWaits and produced no snapshot for many minutes.
        by_channel: dict[int, list[int]] = {}
        # Missing-only can select a row whose 36-frame files are already complete
        # when only the DB state or representative snapshot needs repair. Those
        # rows are repaired entirely from local files and must not trigger even a
        # Telegram message lookup. Explicit scope=all is the only mode that forces
        # media access for an already-complete local snapshot set.
        for row in chunk_rows:
            from ..core.scan_schedule import wait_for_metadata_scan_window
            await wait_for_metadata_scan_window(ctx)
            cid = int(row["source_channel"]); mid = int(row["source_message_id"])
            out = representative_frame_path(cid, mid)
            frames_dir = preview_frames_path(cid, mid)
            local_complete = (
                out.is_file() and out.stat().st_size > MIN_FRAME_BYTES
                and all((frames_dir / f"frame_{idx:02d}.jpg").is_file()
                        for idx in range(1, FRAME_COUNT + 1))
            )
            if local_complete and not _row_needs_remote_playback_assets(row):
                # A complete local snapshot never needs Telegram metadata merely
                # because validation.json or grid_sprite.jpg needs repair. Only
                # resolve a Telegram message when the saved probe or MP4 index
                # cache is genuinely missing.
                continue
            by_channel.setdefault(cid, []).append(mid)
        for channel_id, ids in by_channel.items():
            unique_ids = list(dict.fromkeys(ids))
            for start in range(0, len(unique_ids), 50):
                batch = unique_ids[start:start + 50]
                try:
                    from .preview_resolver import retry_async
                    messages = await retry_async(
                        lambda: ctx.cancellable(
                            lambda channel_id=channel_id, batch=batch: ctx.telegram.client.get_messages(channel_id, ids=batch),
                            timeout=PREVIEW_MESSAGE_TIMEOUT_SECONDS,
                        ), attempts=3, base_delay=0.4, max_delay=2.0,
                        on_retry=lambda exc, attempt, delay: ctx.db.log(
                            f"Preview message lookup retry {attempt}/3 for {channel_id}/{batch[0]}-{batch[-1]} "
                            f"after {type(exc).__name__}; waiting {delay:.1f}s", "WARN"),
                    )
                    if not isinstance(messages, (list, tuple)):
                        # Telethon normally returns a list for an ID list. Keep
                        # compatibility with clients/test doubles that return a
                        # single Message object when only one message is requested.
                        messages = [messages] if messages else []
                    for msg in messages:
                        msg_id = int(getattr(msg, "id", 0) or 0)
                        if msg_id:
                            message_map[(channel_id, msg_id)] = msg
                    missing = [msg_id for msg_id in batch if (channel_id, msg_id) not in message_map]
                    if missing and len(batch) == 1 and len(messages) == 1 and not getattr(messages[0], "id", None):
                        message_map[(channel_id, missing[0])] = messages[0]
                        missing = []
                    if missing and len(batch) > 1 and len(messages) == 1 and not getattr(messages[0], "id", None):
                        # A compatibility client returned an unidentifiable single
                        # object for a batched request. Resolve those IDs through
                        # the existing cancellable single-message path instead of
                        # incorrectly declaring the whole batch failed.
                        for msg_id in missing:
                            try:
                                from .preview_resolver import retry_async
                                one = await retry_async(
                                    lambda: ctx.cancellable(
                                        lambda channel_id=channel_id, msg_id=msg_id: ctx.telegram.client.get_messages(channel_id, ids=msg_id),
                                        timeout=PREVIEW_MESSAGE_TIMEOUT_SECONDS,
                                    ), attempts=3, base_delay=0.4, max_delay=2.0,
                                )
                                if one:
                                    message_map[(channel_id, msg_id)] = one
                                else:
                                    message_resolution_failed.add((channel_id, msg_id))
                            except Exception:
                                message_resolution_failed.add((channel_id, msg_id))
                    else:
                        for msg_id in missing:
                            message_resolution_failed.add((channel_id, msg_id))
                except Exception as exc:
                    for msg_id in batch:
                        message_resolution_failed.add((channel_id, msg_id))
                    ctx.db.log(
                        f"Timeline preview message batch {channel_id}/{batch[0]}-{batch[-1]} failed: "
                        f"{type(exc).__name__}: {exc}", "WARN"
                    )

    ok=failed=0
    resources = getattr(ctx._manager, "resources", None)
    policy = resources.media_policy() if resources is not None else {}
    video_concurrency = max(1, min(2, int(policy.get("snapshot_video_concurrency") or 1)))
    frame_workers = max(1, min(4, int(policy.get("snapshot_frame_workers") or 2)))
    range_concurrency = max(1, min(4, int(policy.get("snapshot_range_concurrency") or frame_workers)))
    ctx.report(
        completed=0, total=total,
        detail=(f"snapshots {ok} ready · {failed} failed (skipped) · {video_concurrency} video worker(s) · "
                f"{frame_workers} frame decoder(s) · {range_concurrency} Telegram range(s)"),
        operation="Preparing individual video snapshots", force=True,
    )

    semaphore = asyncio.Semaphore(video_concurrency)
    completed_items = 0

    async def process_one(row):
        """Build one row under the semaphore and update counters."""
        nonlocal completed_items, ok, failed
        async with semaphore:
            await ctx.checkpoint()
            message_id = str(row["source_message_id"])
            video_name = str(row["title"] or row["filename"] or f"Video {message_id}").strip()
            ctx.report(
                current_item=video_name,
                detail=(f"snapshots {ok} ready · {failed} failed (skipped) · {video_concurrency} video worker(s) · "
                        f"{frame_workers} frame decoder(s) · {range_concurrency} Telegram range(s)"),
                operation="Preparing individual video snapshots",
            )
            key = (int(row["source_channel"]), int(row["source_message_id"]))
            if key in message_resolution_failed:
                good = False
                ctx.db.log(
                    f"Timeline preview cache {key[0]}/{key[1]} skipped: Telegram message metadata unavailable",
                    "WARN",
                )
            else:
                resolved_message = message_map.get(key)
                build_row = dict(row)
                if resolved_message is not None:
                    hydrated = await asyncio.to_thread(_hydrate_media_metadata_sync, ctx.db, row, resolved_message)
                    for field in ("duration", "width", "height", "size"):
                        value = hydrated.get(field)
                        if value:
                            build_row[field] = value
                    filename = str(hydrated.get("filename") or "").strip()
                    if filename and not str(build_row.get("filename") or "").strip():
                        build_row["filename"] = filename
                    if filename and not str(build_row.get("title") or "").strip():
                        build_row["title"] = filename
                # Every Telegram byte range is admitted by the shared aligned
                # transport. Do not wrap the whole snapshot job in a file slot:
                # nested admission would deadlock at the per-DC cap.
                good = await _build_one(
                    ctx, build_row, message=resolved_message,
                    frame_workers=frame_workers, range_concurrency=range_concurrency,
                    force_rebuild=force_rebuild,
                )
            completed_items += 1
            ok += 1 if good else 0
            failed += 0 if good else 1
            ctx.report(
                completed=completed_items, total=total, current_item=video_name,
                detail=(f"snapshots {ok} ready · {failed} failed (skipped) · {video_concurrency} video worker(s) · "
                        f"{frame_workers} frame decoder(s) · {range_concurrency} Telegram range(s)"),
                operation="Preparing individual video snapshots", force=True,
            )
            return good

    # Resolve and build in groups so ready snapshots (and published videos)
    # appear progressively instead of after every message lookup.
    for start in range(0, len(rows), RESOLVE_CHUNK):
        from ..core.scan_schedule import wait_for_metadata_scan_window
        await wait_for_metadata_scan_window(ctx)
        chunk = rows[start:start + RESOLVE_CHUNK]
        await ctx.checkpoint()
        await _resolve_messages(chunk)
        await asyncio.gather(*(process_one(row) for row in chunk))
    ctx.db.log(f"Playback snapshots: {ok} ready, {failed} failed; local playback snapshots generated from media", "SUCCESS" if failed==0 else "WARN")
    await asyncio.to_thread(_record_snapshot_result, ctx, channels, message_ids, ok, failed)




def snapshot_coverage(db, channels: list[int] | None = None, message_ids: list[int] | None = None) -> tuple[int, int]:
    """Return (videos with complete local snapshot sets, all videos) in selection."""
    clauses = ["COALESCE(m.status,'')<>'SOURCE_DELETED'"]
    params: list[object] = []
    if channels:
        clauses.append(f"CAST(m.source_channel AS INTEGER) IN ({','.join('?' for _ in channels)})")
        params.extend(int(x) for x in channels)
    if message_ids:
        clauses.append(f"CAST(m.source_message_id AS INTEGER) IN ({','.join('?' for _ in message_ids)})")
        params.extend(int(x) for x in message_ids)
    with db.reading() as conn:
        row = conn.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN rs.snapshot_state='READY' THEN 1 ELSE 0 END) ready "
            "FROM media m LEFT JOIN resource_state rs ON rs.media_id=m.id WHERE " + " AND ".join(clauses),
            params).fetchone()
    return int(row["ready"] or 0), int(row["total"] or 0)


def _record_snapshot_result(ctx, channels, message_ids, ok: int, failed: int) -> None:
    """V29.4.133: the job ends with what was verified, not "Completed 100%".

    A run is bounded (50 videos unless "All" is chosen) and failed builds were
    only logged, so the Assets Manager showed a finished job while most of the
    channel still had no snapshots.
    """
    try:
        from ..core import job_results
        parts = [f"{ok} built"] + ([f"{failed} failed"] if failed else [])
        progress = None
        if channels or message_ids:
            ready, total = snapshot_coverage(ctx.db, channels or None, message_ids or None)
            waiting = max(0, total - ready)
            progress = 100.0 * ready / total if total else 100.0
            parts.append(f"{ready:,} of {total:,} videos have snapshots")
            if waiting:
                parts.append(f"{waiting:,} still waiting — run again or choose All")
        job_results.record(ctx.db, ctx.job_id,
                           status="COMPLETED_WITH_ERRORS" if failed else "COMPLETED",
                           progress=progress, summary=", ".join(parts))
    except Exception as exc:
        ctx.db.log(f"Playback snapshots: result not recorded: {type(exc).__name__}: {exc}", "WARN")
