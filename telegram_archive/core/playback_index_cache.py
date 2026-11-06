"""Permanent cache for MP4 index ranges and parsed keyframe metadata.

Unlike the active sparse playback cache, files in this directory are durable:
releasing a video never removes the MP4 moov/index bytes already collected by an
explicit Metadata & Assets scan. The key includes source size so replacing a
Telegram message cannot make old ranges appear valid for a different file.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Paths and atomic writes
#   - Telegram location persistence
#   - Index segments and legacy migration
#   - Reading
#   - Local file indexing
# ------------------------------------------------------------------
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from .. import config

SEGMENT_SIZE = int(getattr(config, "PLAYBACK_RANGE_CHUNK_BYTES", 512 * 1024))
CACHE_VERSION = 3
LEGACY_STARTUP_CACHE_VERSION = 2
LOCATION_CACHE_VERSION = 1  # keep existing standalone Telegram locations valid
_LEGACY_MIGRATION_LOCK = threading.RLock()




# ==============================================================================
# SECTION: Paths and atomic writes
# Cache layout and atomic file writes.
# ==============================================================================

def _root() -> Path:
    return Path(config.PLAYER_CACHE_DIR) / "mp4_index"


def _location_root() -> Path:
    # This cache is independent of the MP4 index so every scanned media type
    # (including non-MP4 video) can skip get_messages on the first Play.
    return Path(config.PLAYER_CACHE_DIR) / "telegram_locations"


def telegram_location_path(channel_id: int, message_id: int, size: int) -> Path:
    return _location_root() / str(int(channel_id)) / f"{int(message_id)}_{int(size)}.json"


def _stem(channel_id: int, message_id: int, size: int) -> Path:
    return _root() / str(int(channel_id)) / f"{int(message_id)}_{int(size)}"


def manifest_path(channel_id: int, message_id: int, size: int) -> Path:
    return _stem(channel_id, message_id, size) / "index.json"


def segment_path(channel_id: int, message_id: int, size: int, segment: int) -> Path:
    return _stem(channel_id, message_id, size) / f"segment_{int(segment):06d}.bin"


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        try:
            os.unlink(name)
        except OSError:
            pass




# ==============================================================================
# SECTION: Telegram location persistence
# Saved Telegram document locations.
# ==============================================================================

def _telegram_location(message: Any) -> dict:
    """Return redaction-safe, durable file-location data from a scan message."""
    try:
        document = getattr(message, "document", None)
        file_meta = getattr(message, "file", None)
        reference = bytes(getattr(document, "file_reference", b"") or b"")
        if document is None or not reference:
            return {}
        return {
            "document_id": int(getattr(document, "id", 0) or 0),
            "access_hash": int(getattr(document, "access_hash", 0) or 0),
            "dc_id": int(getattr(document, "dc_id", 0) or 0),
            "file_reference_b64": base64.b64encode(reference).decode("ascii"),
            "name": str(getattr(file_meta, "name", None) or f"Telegram video {getattr(message, 'id', '')}"),
            "mime_type": str(getattr(file_meta, "mime_type", None) or "video/mp4"),
            "duration": max(0.0, float(getattr(file_meta, "duration", 0) or 0)),
        }
    except Exception:
        return {}


def persist_telegram_location(channel_id: int, message_id: int, message: Any,
                             source_size: int | None = None) -> bool:
    """Persist Telegram file identity independently of MP4 index success.

    Called during explicit Metadata & Assets scans. No media bytes are fetched
    here; this is only the message's document ID/access hash/DC/file reference.
    """
    try:
        location = _telegram_location(message)
        file_meta = getattr(message, "file", None)
        size = int(source_size or getattr(file_meta, "size", 0) or 0)
        if not location or size <= 0:
            return False
        data = {
            "source_channel": int(channel_id), "message_id": int(message_id),
            "source_size": size, "cache_version": LOCATION_CACHE_VERSION,
            "telegram_location": location,
        }
        _atomic_write(telegram_location_path(channel_id, message_id, size),
                      json.dumps(data, separators=(",", ":")).encode("utf-8"))
        return True
    except Exception:
        return False


def read_telegram_location(channel_id: int, message_id: int,
                           size: int | None = None) -> dict | None:
    """Read the newest valid cached Telegram location without network calls."""
    try:
        base = _location_root() / str(int(channel_id))
        if size is not None and int(size) > 0:
            paths = [telegram_location_path(channel_id, message_id, int(size))]
        else:
            paths = sorted(base.glob(f"{int(message_id)}_*.json"),
                           key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                location = data.get("telegram_location") or {}
                if (int(data.get("source_channel", -1)) != int(channel_id)
                        or int(data.get("message_id", -1)) != int(message_id)
                        or int(data.get("cache_version", 0)) != LOCATION_CACHE_VERSION
                        or int(data.get("source_size", 0)) <= 0
                        or not location.get("document_id") or not location.get("access_hash")
                        or not location.get("file_reference_b64")):
                    continue
                return data
            except (OSError, ValueError, TypeError):
                continue
    except Exception:
        return None
    return None


def update_telegram_location(channel_id: int, message_id: int, message: Any) -> bool:
    """Refresh standalone cache plus any matching MP4 index manifests."""
    try:
        file_meta = getattr(message, "file", None)
        size = int(getattr(file_meta, "size", 0) or 0)
        location = _telegram_location(message)
        if not location:
            return False
        saved = persist_telegram_location(channel_id, message_id, message, size)
        base = _root() / str(int(channel_id))
        paths = sorted(base.glob(f"{int(message_id)}_*/index.json"),
                       key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if int(data.get("source_channel", -1)) != int(channel_id) or int(data.get("message_id", -1)) != int(message_id):
                    continue
                data["telegram_location"] = location
                _atomic_write(path, json.dumps(data, separators=(",", ":")).encode("utf-8"))
            except (OSError, ValueError, TypeError):
                continue
        return bool(saved or paths)
    except Exception:
        return False




# ==============================================================================
# SECTION: Index segments and legacy migration
# Stores MP4 index segments and migrates older startup caches.
# ==============================================================================

def persist_index_segments(source: Any, key: tuple[int, int] | None, index: dict,
                           moov_bytes: bytes, moov_offset: int) -> dict | None:
    """Persist only moov-containing ranges and parsed technical/keyframe info.

    This is called by the explicit snapshot/metadata scan on its worker thread.
    `source.get_range_sync` is therefore never run on the web event loop here.
    """
    if not key or not isinstance(index, dict):
        return None
    try:
        cid, mid = int(key[0]), int(key[1])
        size = int(getattr(source, "size", 0) or 0)
        moov = bytes(moov_bytes or b"")
        moov_offset = max(0, int(moov_offset or 0))
        if size <= 0 or not moov:
            return None
        segment_size = SEGMENT_SIZE
        total = (size + segment_size - 1) // segment_size
        first_moov = moov_offset // segment_size
        last_moov = min(total - 1, (moov_offset + len(moov) - 1) // segment_size)
        if moov_offset < 0 or moov_offset + len(moov) > size or first_moov > last_moov:
            return None
        wanted = set(range(first_moov, last_moov + 1))
        timescale = max(0, int(index.get("timescale") or 0))
        pts = index.get("pts") or []
        offsets = index.get("offsets") or []
        sync_samples = index.get("sync") or []
        wanted = sorted(s for s in wanted if 0 <= s < total)
        # Store a compact timestamp -> byte-offset table for every sync sample.
        # This supports accurate live seek-preview frame selection without
        # guessing a byte offset from duration / file size.
        keyframes = []
        if timescale > 0 and offsets:
            seen_keyframes = set()
            for raw_i in sync_samples:
                try:
                    i = int(raw_i)
                    if i < 0 or i >= len(offsets) or i >= len(pts):
                        continue
                    timestamp = max(0.0, float(pts[i]) / timescale)
                    byte_offset = int(offsets[i])
                    if byte_offset < 0 or byte_offset >= size:
                        continue
                    token = (round(timestamp, 3), byte_offset)
                    if token not in seen_keyframes:
                        seen_keyframes.add(token)
                        keyframes.append([token[0], token[1]])
                except (TypeError, ValueError, OverflowError):
                    continue
            keyframes.sort(key=lambda item: (item[0], item[1]))
        saved = []
        for seg in wanted:
            start = seg * segment_size
            length = min(segment_size, size - start)
            path = segment_path(cid, mid, size, seg)
            try:
                data = path.read_bytes() if path.is_file() else b""
            except OSError:
                data = b""
            if len(data) != length:
                data = source.get_range_sync(start, length)
                if len(data) != length:
                    continue
                _atomic_write(path, data)
            saved.append(seg)
        if not set(wanted).issubset(saved):
            return None
        duration = max(0.0, float(index.get("duration") or 0))
        sample_count = max(0, int(index.get("sample_count") or 0))
        metadata = {
            "codec": str(index.get("codec_name") or "").strip().lower(),
            "width": max(0, int(index.get("width") or 0)),
            "height": max(0, int(index.get("height") or 0)),
            "duration": duration,
            "fps": (sample_count / duration) if duration > 0 else 0.0,
            "sample_count": sample_count,
            "source_channel": cid,
            "message_id": mid,
            "source_size": size,
            "segment_size": segment_size,
            "segments": saved,
            "moov_offset": moov_offset,
            "moov_size": len(moov),
            "keyframes": keyframes,
            "keyframe_count": len(keyframes),
            "telegram_location": _telegram_location(getattr(source, "message", None)),
            "cache_version": CACHE_VERSION,
        }
        keep_segments = set(saved)
        for part in _stem(cid, mid, size).glob("segment_*.bin"):
            try:
                segment = int(part.stem.split("_", 1)[1])
            except (IndexError, TypeError, ValueError):
                continue
            if segment not in keep_segments:
                try:
                    part.unlink()
                except OSError:
                    pass
        _atomic_write(manifest_path(cid, mid, size), json.dumps(metadata, separators=(",", ":")).encode("utf-8"))
        persist_telegram_location(cid, mid, getattr(source, "message", None), size)
        return metadata
    except Exception:
        # A durable-cache write failure must not abort otherwise-valid snapshot
        # extraction; playback can still use the normal Telegram range path.
        return None


def _moov_segment_ids(manifest: dict) -> set[int]:
    """Return the exact file ranges needed to read this manifest's MP4 index."""
    try:
        size = int(manifest.get("source_size") or 0)
        segment_size = int(manifest.get("segment_size") or SEGMENT_SIZE)
        offset = int(manifest.get("moov_offset") or -1)
        moov_size = int(manifest.get("moov_size") or 0)
        if size <= 0 or segment_size <= 0 or offset < 0 or moov_size <= 0 or offset + moov_size > size:
            return set()
        first = offset // segment_size
        last = min((size - 1) // segment_size, (offset + moov_size - 1) // segment_size)
        return set(range(first, last + 1))
    except (TypeError, ValueError, OverflowError):
        return set()


def _migrate_legacy_startup_manifest(path: Path, manifest: dict) -> tuple[dict | None, int]:
    with _LEGACY_MIGRATION_LOCK:
        return _migrate_legacy_startup_manifest_locked(path, manifest)


def _migrate_legacy_startup_manifest_locked(path: Path, manifest: dict) -> tuple[dict | None, int]:
    """Remove old startup-only MP4 ranges while retaining the moov/index bytes."""
    try:
        if int(manifest.get("cache_version") or 0) != LEGACY_STARTUP_CACHE_VERSION:
            return None, 0
        channel_id = int(manifest.get("source_channel") or 0)
        message_id = int(manifest.get("message_id") or 0)
        source_size = max(0, int(manifest.get("source_size") or 0))
        segment_size = max(1, int(manifest.get("segment_size") or SEGMENT_SIZE))
        expected_parent = _stem(channel_id, message_id, source_size)
        if (message_id <= 0 or source_size <= 0
                or path.parent.resolve(strict=False) != expected_parent.resolve(strict=False)):
            return None, 0
    except (OSError, TypeError, ValueError, OverflowError):
        return None, 0
    moov_segments = _moov_segment_ids(manifest)
    if not moov_segments:
        return None, 0
    bytes_removed = 0
    for part in path.parent.glob("segment_*.bin"):
        try:
            segment = int(part.stem.split("_", 1)[1])
        except (IndexError, TypeError, ValueError):
            continue
        if segment in moov_segments:
            continue
        try:
            bytes_removed += part.stat().st_size
            part.unlink()
        except OSError:
            continue

    retained = []
    for segment in sorted(moov_segments):
        part = path.parent / f"segment_{segment:06d}.bin"
        try:
            expected = min(segment_size, source_size - segment * segment_size)
            if expected > 0 and part.is_file() and part.stat().st_size == expected:
                retained.append(segment)
        except OSError:
            continue

    migrated = dict(manifest)
    migrated["segments"] = retained
    migrated["cache_version"] = CACHE_VERSION
    migrated.pop("startup_segments", None)
    migrated.pop("startup_cached_seconds", None)
    try:
        _atomic_write(path, json.dumps(migrated, separators=(",", ":")).encode("utf-8"))
    except OSError:
        return None, bytes_removed
    return migrated, bytes_removed


def cleanup_legacy_startup_cache() -> dict[str, int]:
    """Delete old startup-only ranges from MP4-index manifests, preserving moov data."""
    migrated = 0
    bytes_removed = 0
    root = _root()
    if not root.exists():
        return {"manifests_migrated": 0, "bytes_removed": 0}
    for path in root.rglob("index.json"):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        try:
            is_legacy = isinstance(manifest, dict) and int(
                manifest.get("cache_version") or 0
            ) == LEGACY_STARTUP_CACHE_VERSION
        except (TypeError, ValueError, OverflowError):
            is_legacy = False
        if not is_legacy:
            continue
        result, removed = _migrate_legacy_startup_manifest(path, manifest)
        bytes_removed += int(removed)
        if result is not None:
            migrated += 1
    return {"manifests_migrated": migrated, "bytes_removed": bytes_removed}




# ==============================================================================
# SECTION: Reading
# Manifest and segment loading.
# ==============================================================================

def read_manifest(channel_id: int, message_id: int, size: int | None = None) -> dict | None:
    """Read one durable metadata manifest; no Telegram calls or DB locks."""
    try:
        base = _root() / str(int(channel_id))
        if size is not None:
            paths = [manifest_path(channel_id, message_id, int(size))]
        else:
            paths = sorted(base.glob(f"{int(message_id)}_*/index.json"),
                           key=lambda p: p.stat().st_mtime_ns, reverse=True)
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if (int(data.get("source_channel", -1)) == int(channel_id)
                        and int(data.get("message_id", -1)) == int(message_id)
                        and (size is None or int(data.get("source_size", -1)) == int(size))):
                    version = int(data.get("cache_version", 0))
                    if version == LEGACY_STARTUP_CACHE_VERSION:
                        data, _ = _migrate_legacy_startup_manifest(path, data)
                    if data and int(data.get("cache_version", 0)) == CACHE_VERSION:
                        return data
            except (OSError, ValueError, TypeError):
                continue
    except Exception:
        return None
    return None


def load_segment(channel_id: int, message_id: int, size: int, segment: int,
                 segment_size: int | None = None) -> bytes | None:
    """Return a cached MP4 index segment; never serve removed startup-only ranges."""
    try:
        seg_size = int(segment_size or SEGMENT_SIZE)
        seg = int(segment)
        total_size = int(size)
        start = seg * seg_size
        if seg < 0 or total_size <= start:
            return None
        manifest = read_manifest(channel_id, message_id, total_size)
        if not manifest or seg not in set(int(value) for value in (manifest.get("segments") or [])):
            return None
        path = segment_path(channel_id, message_id, total_size, seg)
        expected = min(seg_size, total_size - start)
        data = path.read_bytes()
        return data if len(data) == expected else None
    except (OSError, ValueError, TypeError):
        return None




# ==============================================================================
# SECTION: Local file indexing
# Builds and stages indexes for local files.
# ==============================================================================

class _LocalFileRangeSource:
    """Minimal synchronous byte-range adapter for an exact local upload file."""
    def __init__(self, path: str | Path, message: Any = None):
        self.path = Path(path)
        self.size = int(self.path.stat().st_size)
        self.message = message

    def get_range_sync(self, start: int, length: int) -> bytes:
        start, length = max(0, int(start)), max(0, int(length))
        if start >= self.size or length <= 0:
            return b""
        with self.path.open("rb") as stream:
            stream.seek(start)
            return stream.read(min(length, self.size - start))


def persist_local_file_index(channel_id: int, message_id: int, file_path: str | Path,
                             message: Any = None) -> dict | None:
    """Index exact upload bytes and cache moov/startup/keyframe data without Telegram downloads."""
    try:
        from ..playback.media_tools import _find_moov_from_window, _parse_visual_config
        source = _LocalFileRangeSource(file_path, message=message)
        size = source.size
        if size <= 0:
            return None
        window_size = min(size, max(8 * 1024 * 1024, SEGMENT_SIZE * 4))
        head = source.get_range_sync(0, window_size)
        hit = _find_moov_from_window(head, 0, size)
        if not hit:
            tail_size = min(size, max(8 * 1024 * 1024, SEGMENT_SIZE * 4))
            tail_start = max(0, size - tail_size)
            hit = _find_moov_from_window(source.get_range_sync(tail_start, tail_size), tail_start, size)
        if not hit:
            return None
        moov_offset, moov_size = int(hit[0]), int(hit[1])
        # Reject malformed/unreasonably large metadata boxes rather than allocate
        # unbounded memory on a corrupt local file.
        if moov_size < 8 or moov_size > min(size, 128 * 1024 * 1024):
            return None
        moov = source.get_range_sync(moov_offset, moov_size)
        if len(moov) != moov_size:
            return None
        index = _parse_visual_config(moov)
        if not isinstance(index, dict):
            return None
        result = persist_index_segments(source, (int(channel_id), int(message_id)), index, moov, moov_offset)
        return result
    except Exception:
        return None


def copy_cached_playback_index_assets(source_channel: int, source_message_id: int,
                                      destination_channel: int, destination_message_id: int,
                                      file_size: int) -> dict | None:
    """Copy a prepared MP4 cache for a byte-identical Telegram-to-Telegram delivery.

    A strict size check guards against accidentally copying a manifest onto a
    different payload. The destination's Telegram file reference is refreshed
    separately from the actual destination message after the copy.
    """
    import shutil
    try:
        size = int(file_size or 0)
        source_dir = _stem(int(source_channel), int(source_message_id), size)
        source_manifest = source_dir / "index.json"
        if size <= 0 or not source_manifest.is_file():
            return None
        data = json.loads(source_manifest.read_text(encoding="utf-8"))
        if (int(data.get("source_channel", 0)) != int(source_channel)
                or int(data.get("message_id", 0)) != int(source_message_id)
                or int(data.get("source_size", 0)) != size
                or int(data.get("cache_version", 0)) != CACHE_VERSION):
            return None
        dest_dir = _stem(int(destination_channel), int(destination_message_id), size)
        building = dest_dir.with_name(dest_dir.name + ".building")
        if building.exists():
            shutil.rmtree(building, ignore_errors=True)
        building.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_dir, building)
        data["source_channel"] = int(destination_channel)
        data["message_id"] = int(destination_message_id)
        data["source_size"] = size
        data["telegram_location"] = {}
        _atomic_write(building / "index.json", json.dumps(data, separators=(",", ":")).encode("utf-8"))
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        os.replace(building, dest_dir)
        return data
    except Exception:
        return None


def _local_stage_key(file_path: str | Path) -> int:
    """Stable positive message-like ID for a prepared local-file index stage."""
    import hashlib
    path = Path(file_path).expanduser().resolve(strict=False)
    stat = path.stat()
    token = f"{str(path).casefold()}|{stat.st_size}|{stat.st_mtime_ns}"
    return (int(hashlib.sha256(token.encode("utf-8", "surrogatepass")).hexdigest()[:14], 16) % 2_000_000_000) + 1


def prepare_local_file_index(file_path: str | Path) -> dict | None:
    """Build a durable MP4/startup/keyframe cache before an upload starts."""
    try:
        path = Path(file_path).expanduser().resolve(strict=False)
        size = int(path.stat().st_size)
        stage_id = _local_stage_key(path)
        current = read_manifest(0, stage_id, size)
        if current:
            return {"source_channel": 0, "message_id": stage_id, "source_size": size}
        built = persist_local_file_index(0, stage_id, path)
        if built:
            return {"source_channel": 0, "message_id": stage_id, "source_size": size}
    except Exception:
        return None
    return None


def remove_local_file_index_stage(file_path: str | Path) -> None:
    """Remove only the private index stage after its destination copy is committed."""
    import shutil
    try:
        path = Path(file_path).expanduser().resolve(strict=False)
        size = int(path.stat().st_size)
        stage = _stem(0, _local_stage_key(path), size)
        shutil.rmtree(stage, ignore_errors=True)
    except Exception:
        pass
