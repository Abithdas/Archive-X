"""Playback engine (v18.8) — one shared engine for the browser, VLC and MPC-HC.

Built on the telegram_player.py (v34/v35) design that plays smoothly:
512 KiB segments, raw upload.getFile on dedicated transfer connections, one
global priority scheduler, a sparse on-disk cache of the current video and
next-video prefetch.

What v18.7 fixes compared to the v18.6 port
-------------------------------------------
* v18.6 created a brand-new stream server (new Telegram connections, new
  empty cache, channel re-index) on EVERY browser range request and on every
  VLC channel switch, because the "is it running?" check read an attribute
  that never existed. Each new server also wiped the previous server's disk
  cache file for the same video, so players could receive zero-filled bytes.
  Now there is exactly one engine for every channel.
* The iter_download fallback treated `limit` as bytes (Telethon treats it as
  a number of chunks) and downloaded the whole rest of the file for one 512 KiB
  segment. Fixed: exactly one chunk.
* Fully cached ranges were written straight onto the socket with sendfile
  after aiohttp had only *buffered* the HTTP headers, which could put the
  body before the headers (players then skip to the next item). Cached data
  is now written through the normal response path.
* Disk reads/writes run in a worker thread so the web server never freezes.
* A foreground request can now upgrade a queued background download of the
  same segment (the old per-segment lock blocked the upgrade).
* Newer foreground requests (seeks) are served before older ones.
* Background work may use idle bandwidth while a player keeps its HTTP
  connection open, but always leaves a free slot for playback/seek.
* Failed segment downloads are retried (other connection, flood-wait aware)
  instead of breaking the HTTP stream, which made VLC jump to the next video.
* Byte ranges are honoured completely with a sliding download window instead
  of oversized windows, so VLC/Chrome keep one smooth connection.
* Disk cache is bounded (old videos are deleted automatically) and cleaned on
  start; verbose logs go to data/playback.log instead of the console (a
  Windows console in selection mode freezes every print()).

Module map
----------
The top-level helpers handle indexing and cache policy. SparseVideoCache and
SegmentTransferScheduler own disk ranges and shared transfer priority.
PlaybackEngine coordinates those helpers with Telegram sessions, playback
leases, prefetch, and HTTP range responses. StreamTokens and the feed handler
form the signed external-player boundary.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Logging
#   - Video index
#   - Range cache and connection policy
#   - HTTP range parsing
#   - Sparse on-disk video cache
#   - Global priority scheduler
#   - Telegram error helpers
#   - PlaybackEngine
#       - Engine lifecycle, leases and idle cleanup
#       - Worker supervision, transfer sessions and bandwidth
#       - Telegram entity and message resolution
#       - Sparse disk and memory segment cache
#       - Aligned range fetch and segment storage
#       - Foreground admission and playhead prefetch
#       - Background caching and next-video warmup
#       - Browser and external player delivery
#   - Signed links and local player feed
# ------------------------------------------------------------------
from __future__ import annotations
from ..core.background_tasks import retain as _retain_task

from ..telegram.client_policy import configure_telethon_client, make_telegram_client, FLOOD_SLEEP_THRESHOLD_SECONDS, REQUEST_RETRIES
from ..telegram.capabilities import TelegramAccountProfile
from ..systems.playback import PlaybackController
import asyncio
import base64
import json
import ctypes
import hashlib
import heapq
import hmac as _hmac
import threading
import logging
import math
import os
import re
import secrets
import time
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

from .algorithms import IntervalSet, RobustRateEstimator, AdaptiveBufferController, aging_priority
from .qos import PlaybackQoSController
from .performance import PlaybackTrace
from .media_resource import ArchiveXMediaResource
from ..core.retry_policy import RetryPolicy
from ..core.aligned_telegram_range import fetch_aligned_range
from logging.handlers import RotatingFileHandler
from typing import Optional

from .. import config

try:
    from telethon import TelegramClient, functions, types
    from telethon.sessions import MemorySession
    TELETHON_AVAILABLE = True
except ImportError:  # pragma: no cover - app still starts without Telethon
    TelegramClient = functions = types = MemorySession = None
    TELETHON_AVAILABLE = False

try:
    from aiohttp import web as _aiohttp_web
    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _aiohttp_web = None
    AIOHTTP_AVAILABLE = False


# ---------------------------------------------------------------- logging

LOG_PATH = config.PLAYBACK_LOG_PATH
_log = logging.getLogger("telegram_archive.playback")
_log.propagate = False
_log.setLevel(logging.DEBUG if os.getenv("TELEGRAM_ARCHIVE_PLAYBACK_DEBUG") else logging.INFO)



# ==============================================================================
# SECTION: Logging
# Playback log handler and debug helpers.
# ==============================================================================

def _ensure_playback_log_handler() -> None:
    """Create/refresh the file handler lazily, including on Windows startup.

    The prior import-time setup permanently selected NullHandler if the app's
    data/log directory had not yet been created. Re-check the canonical config
    path whenever a playback event is written so diagnostics and logging agree.
    """
    global LOG_PATH
    try:
        target = Path(config.PLAYBACK_LOG_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        same = any(isinstance(handler, RotatingFileHandler)
                   and Path(getattr(handler, "baseFilename", "")).resolve() == target.resolve()
                   for handler in list(_log.handlers))
        if same:
            LOG_PATH = target
            return
        for handler in list(_log.handlers):
            if isinstance(handler, logging.FileHandler):
                _log.removeHandler(handler)
                try:
                    handler.close()
                except Exception:
                    pass
        handler = RotatingFileHandler(
            target, maxBytes=int(getattr(config, "PLAYBACK_LOG_MAX_BYTES", 5 * 1024 * 1024)),
            backupCount=int(getattr(config, "PLAYBACK_LOG_BACKUPS", 2)), encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        _log.addHandler(handler)
        LOG_PATH = target
    except Exception as exc:
        # Keep this failure visible to the regular app log; do not cache a
        # NullHandler that would silence playback.log for the rest of the run.
        logging.getLogger(__name__).warning("Could not initialize playback.log at %s: %s", LOG_PATH, exc)

def _stream_log(message: str) -> None:
    try:
        _ensure_playback_log_handler()
        _log.info(message)
        from ..core.diagnostics import record_event
        record_event(event="playback.message", level="INFO", message=message, details={"component": "playback"})
    except Exception as exc:
        logging.getLogger(__name__).debug("Playback event logging failed: %s", exc, exc_info=True)

def _dbg(message: str) -> None:
    if _log.isEnabledFor(logging.DEBUG):
        try:
            _ensure_playback_log_handler()
            _log.debug(message)
            from ..core.diagnostics import record_event
            record_event(event="playback.debug", level="DEBUG", message=message, details={"component": "playback"})
        except Exception as exc:
            logging.getLogger(__name__).debug("Playback debug logging failed: %s", exc, exc_info=True)


def _human_bytes(value: float) -> str:
    v = float(value or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(v) < 1024 or unit == "TB":
            return f"{v:.1f} {unit}"
        v /= 1024
    return f"{v:.1f} TB"


def content_disposition(name: str) -> str:
    """inline Content-Disposition that is safe for non-English file names.

    HTTP headers must be plain ASCII (Starlette refuses anything else, which
    used to break browser playback of files with non-English names)."""
    name = str(name or "video.mp4")
    ascii_name = re.sub(r'[^A-Za-z0-9._ ()\[\]-]+', "_", name)[:150] or "video.mp4"
    quoted = urllib.parse.quote(name[:200], safe="")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


# ---------------------------------------------------------------- constants

HOST = "127.0.0.1"
SEGMENT = int(getattr(config, "PLAYBACK_RANGE_CHUNK_BYTES", 512 * 1024))  # canonical 512 KiB playback unit
OPEN_ENDED_RANGE_MAX = SEGMENT          # Bound browser `bytes=X-` responses to one playback segment
RAM_CACHE_BYTES = max(SEGMENT, int(getattr(config, "PLAYBACK_RAM_CACHE_BYTES", 128 * 1024 * 1024)))
RAM_CACHE_SEGMENTS = max(1, RAM_CACHE_BYTES // max(1, SEGMENT))  # secondary entry-count guard
MESSAGE_CACHE_LIMIT = 256
FREE_MIN_FETCH_CONCURRENCY = 2
FREE_INITIAL_FETCH_CONCURRENCY = 4
FREE_MAX_FETCH_CONCURRENCY = 8
PREMIUM_MIN_FETCH_CONCURRENCY = 4
PREMIUM_INITIAL_FETCH_CONCURRENCY = 8
PREMIUM_MAX_FETCH_CONCURRENCY = 16
FREE_INITIAL_TRANSFER_SESSIONS = 4
FREE_MAX_TRANSFER_SESSIONS = 6
PREMIUM_INITIAL_TRANSFER_SESSIONS = 8
PREMIUM_MAX_TRANSFER_SESSIONS = 16
BACKGROUND_FETCH_CONCURRENCY = 6
# V28.8.20: seek previews sort directly after the playback window (priority 0,
# epoch -1 => heap key (0, +1)) and ahead of background preload (priority 1+).
# At priority 1 with epoch -1 a preview sorted BEHIND every queued read-ahead
# segment of the playing video, which is what made previews take ~5 s.
PREVIEW_PRIORITY = 2
# Interactive playback follows the Telegram Web pattern observed in the
# reference HAR: one canonical 512 KiB byte range per browser/cache/Telegram
# playback unit. The old 1 MiB cache segment + 2x512 KiB interactive split is
# intentionally removed so normal playback, seek, cache and diagnostics all
# use the same range contract.
INTERACTIVE_FETCH_CHUNK = SEGMENT
PREVIEW_SPLIT_MAX_PARTS = 1
INTERACTIVE_KINDS = frozenset({"preview", "seek"})
PREVIEW_SPLIT_MIN_PART = INTERACTIVE_FETCH_CHUNK
PLAYBACK_WINDOW_SEGMENTS = int(getattr(config, "PLAYBACK_FOREGROUND_MIN_SEGMENTS", 2))
PLAYBACK_READAHEAD_SEGMENTS = 24      # ~12 MiB fallback; avoid unbounded speculative startup work
# NoBuf-inspired bounded playhead read-ahead. Keep 512 KiB as the canonical cache
# segment, then admit growing batches (1, 2, 4, 8 MiB equivalent) as ranges arrive.
# Active HTTP streaming is conservatively serial by default; up to three range
# workers can warm the cache in the small gaps between browser range responses.
PLAYHEAD_READAHEAD_KIND = "readahead"
PLAYHEAD_READAHEAD_SECONDS = float(getattr(config, "PLAYBACK_PLAYHEAD_READAHEAD_SECONDS", 180.0))
PLAYHEAD_READAHEAD_MIN_SEGMENTS = int(getattr(config, "PLAYBACK_PLAYHEAD_READAHEAD_MIN_SEGMENTS", 1))
PLAYHEAD_READAHEAD_MAX_BYTES = max(SEGMENT, int(getattr(config, "PLAYBACK_PLAYHEAD_READAHEAD_MAX_BYTES", 192 * 1024 * 1024)))
PLAYHEAD_READAHEAD_MAX_SEGMENTS = max(1, min(
    int(getattr(config, "PLAYBACK_PLAYHEAD_READAHEAD_MAX_SEGMENTS", 384)),
    PLAYHEAD_READAHEAD_MAX_BYTES // max(1, SEGMENT),
))
# Number of canonical 512 KiB segments admitted per read-ahead batch. The byte
# work grows 512 KiB -> 1 -> 2 -> 4 -> 8 MiB without a single large RPC.
PLAYHEAD_READAHEAD_BATCH_LADDER = (1, 2, 4, 8, 16)
# Minimum forward runway before the progressive scheduler expands the batch.
PLAYHEAD_READAHEAD_MIN_BYTES = int(getattr(config, "PLAYBACK_PLAYHEAD_READAHEAD_MIN_BYTES", 1 * 1024 * 1024))
# Account-aware global transfer/QoS ceilings. The player-specific scheduler below
# applies a more conservative active-stream cap and shares the global governor.
PREMIUM_PLAYBACK_LANES = int(getattr(config, "PLAYBACK_PREMIUM_LANES", 16))
FREE_PLAYBACK_LANES = int(getattr(config, "PLAYBACK_FREE_LANES", 8))
# Exact seek-preview keyframes may use one spare foreground lane while the
# browser has at least this much playable buffer (Telegram mobile behaviour:
# scrub thumbnails keep updating while the video plays).
PLAYBACK_PREVIEW_MIN_BUFFER_SECONDS = float(getattr(config, "PLAYBACK_PREVIEW_MIN_BUFFER_SECONDS", 6.0))
# V29.4.120: Chromium leaves short gaps between Range requests (start-up, after
# a seek, while paused with a full buffer). Treating every gap as "playback
# ended" wiped the read-ahead and queued work dozens of times per second. The
# runtime is only released after this much continuous idleness.
PLAYBACK_IDLE_CLEAR_GRACE_SECONDS = float(getattr(config, "PLAYBACK_IDLE_CLEAR_GRACE_SECONDS", 30.0))
PLAYBACK_WINDOW_MIN_SEGMENTS = int(getattr(config, "PLAYBACK_FOREGROUND_MIN_SEGMENTS", 2))
PLAYBACK_WINDOW_MAX_SEGMENTS = int(getattr(config, "PLAYBACK_FOREGROUND_MAX_SEGMENTS", 4))
SEGMENT_TIMEOUT_SECONDS = 40.0
SEGMENT_ATTEMPTS = 4
IDLE_WATCH_INTERVAL_SECONDS = 30.0
IDLE_RELEASE_SECONDS = 300.0          # V29.4.129: idle video released (tab closed etc.)
RELEASED_VIDEO_QUIET_SECONDS = 15.0   # late pieces of a left video are dropped this long
PLAYBACK_CLAIM_TOMBSTONE_SECONDS = 15 * 60
MAX_CACHED_VIDEOS = 2                 # Open range-cache handles: current and next video
DEFAULT_RANGE_CACHE_BUDGET_GIB = 50
DISK_CACHE_BUDGET = DEFAULT_RANGE_CACHE_BUDGET_GIB * 1024 ** 3
RANGE_CACHE_RETENTION = "idle_30m"
RANGE_CACHE_TTL_SECONDS = 30 * 60
RANGE_CACHE_PREFETCH_TTL_SECONDS = 10 * 60
# Telegram Desktop's current document path accepts a server-declared preload
# prefix only below 6 MiB. Archive X uses the same conservative cap, estimating
# needed bytes from duration/size rather than assuming every video's bitrate.
RANGE_CACHE_SCHEMA = 1
OPEN_RANGE_CACHE_PATHS: set[Path] = set()
_OPEN_RANGE_CACHE_PATHS_LOCK = threading.Lock()

DISK_CACHE_DIR = config.PLAYER_CACHE_DIR
DISK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
RANGE_CACHE_DIR = DISK_CACHE_DIR / "video_ranges"
RANGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- video index



# ==============================================================================
# SECTION: Video index
# Records, channel index build and update.
# ==============================================================================

def _persist_message_location(channel_id: int, message_id: int, message) -> None:
    """Refresh only the location portion of an existing durable index manifest."""
    from ..core.playback_index_cache import update_telegram_location
    update_telegram_location(int(channel_id), int(message_id), message)


def make_record(message) -> Optional[dict]:
    """Build a cheap inventory record directly from Telegram message metadata."""
    import base64
    f = getattr(message, "file", None)
    if not f or not getattr(f, "size", None):
        return None
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)
    width = int(getattr(f, "width", 0) or 0)
    height = int(getattr(f, "height", 0) or 0)
    duration = float(getattr(f, "duration", 0) or 0)
    try:
        for attr in getattr(document, "attributes", ()) or ():
            if type(attr).__name__ == "DocumentAttributeVideo":
                width = int(getattr(attr, "w", 0) or width)
                height = int(getattr(attr, "h", 0) or height)
                duration = float(getattr(attr, "duration", 0) or duration)
    except (TypeError, ValueError):
        pass
    reference = bytes(getattr(document, "file_reference", b"") or b"") if document else b""
    return {
        "id": int(message.id),
        "date": message.date.isoformat() if getattr(message, "date", None) else "",
        "name": getattr(f, "name", None) or f"Telegram video {message.id}",
        "size": int(f.size),
        "mime": getattr(f, "mime_type", None) or "video/mp4",
        "duration": duration,
        "width": width,
        "height": height,
        "caption": str(getattr(message, "message", None) or ""),
        "grouped_id": str(getattr(message, "grouped_id", None) or ""),
        "document_id": int(getattr(document, "id", 0) or 0) if document else None,
        "access_hash": str(getattr(document, "access_hash", "") or "") if document else "",
        "file_reference_b64": base64.b64encode(reference).decode("ascii") if reference else "",
    }


INDEX_VERSION = 3   # v3 broadens generic-document video recognition


def _is_video(message) -> bool:
    from ..engines.media_utils import is_video_message
    try:
        return bool(is_video_message(message))
    except Exception:
        return False


INDEX_SCAN_CHECKPOINT_VERSION = 2
INDEX_SCAN_CHECKPOINT_EVERY = 100


async def _emit_index_checkpoint(callback, state: dict) -> None:
    """Persist a restart-safe snapshot without requiring a specific DB type."""
    if callback is None:
        return
    result = callback(state)
    if hasattr(result, "__await__"):
        await result


async def build_channel_index(
    client, channel, progress=None, min_id: int = 0,
    max_videos: int | None = None, *, initial_records: dict | None = None,
    checkpoint: dict | None = None, checkpoint_callback=None,
    checkpoint_every: int = INDEX_SCAN_CHECKPOINT_EVERY,
) -> dict:
    """Index native videos and video documents with durable per-pass cursors.

    Telegram history is fetched newest-to-oldest in two filtered passes. The
    checkpoint records the pass and last message ID, because a single cursor
    cannot safely resume both independent filters. When an interrupted scan is
    resumed, ``max_id`` continues below the last seen message (and ``min_id``
    retains the lower bound for an incremental scan).

    ``checkpoint_callback`` can persist the supplied dictionary; it may be a
    normal or async callable. Partial records are kept in the checkpoint and
    are only published as a complete index after both passes succeed.
    """
    from telethon.tl.types import InputMessagesFilterVideo, InputMessagesFilterDocument

    base_min_id = max(0, int(min_id or 0))
    limit = max(0, int(max_videos or 0)) or None
    records: dict[str, dict] = {str(k): dict(v) for k, v in (initial_records or {}).items() if isinstance(v, dict)}
    stats = {"messages": 0, "native_videos": 0, "document_videos": 0, "other_documents": 0, "passes_completed": 0}
    # Candidate lists are checkpointed so a restart in the middle of a full
    # history traversal can reconstruct split groups without the prior database.
    split_volume_candidates: list[dict] = []
    split_thumbnail_candidates: list[dict] = []
    split_volume_ids: set[int] = set()
    phase_index, cursor_id = 0, 0
    initial_count = len(records)

    if isinstance(checkpoint, dict) and checkpoint.get("version") == INDEX_SCAN_CHECKPOINT_VERSION:
        try:
            compatible = (
                int(checkpoint.get("base_min_id") or 0) == base_min_id
                and (max(0, int(checkpoint.get("max_videos") or 0)) or None) == limit
                and isinstance(checkpoint.get("records"), dict)
                and int(checkpoint.get("phase_index") or 0) in (0, 1, 2)
            )
        except (TypeError, ValueError, OverflowError):
            compatible = False
        if compatible:
            records = {str(k): dict(v) for k, v in checkpoint["records"].items() if isinstance(v, dict)}
            raw_stats = checkpoint.get("stats") if isinstance(checkpoint.get("stats"), dict) else {}
            for name in stats:
                stats[name] = max(0, int(raw_stats.get(name) or 0))
            initial_count = max(0, int(checkpoint.get("initial_count", len(initial_records or {})) or 0))
            phase_index = max(0, min(2, int(checkpoint.get("phase_index") or 0)))
            cursor_id = max(0, int(checkpoint.get("cursor_id") or 0))
            split_volume_candidates = [dict(row) for row in checkpoint.get("split_volume_candidates", []) if isinstance(row, dict)]
            split_thumbnail_candidates = [dict(row) for row in checkpoint.get("split_thumbnail_candidates", []) if isinstance(row, dict)]
            split_volume_ids = {int(row.get("id") or 0) for row in split_volume_candidates if int(row.get("id") or 0) > 0}
            # A checkpoint created before split-volume awareness can contain a
            # physical `.001` row for a verified logical group. Overlay the
            # authoritative group record supplied by the metadata pipeline.
            for key, seed in (initial_records or {}).items():
                if isinstance(seed, dict) and seed.get("is_split_group"):
                    previous = records.get(str(key), {})
                    merged = dict(previous)
                    merged.update(seed)
                    if previous.get("_split_group_seen_part_ids"):
                        merged["_split_group_seen_part_ids"] = list(previous["_split_group_seen_part_ids"])
                    records[str(key)] = merged

    # Track physical Telegram message IDs for registered logical split groups.
    # All volume messages must be present in a completed full-history scan or
    # the logical row is removed from the inventory result (and marked stale by
    # the caller). Normal Part/CD/Disc media has no such metadata and is not
    # grouped here.
    split_message_owner: dict[int, str] = {}
    for key, record in records.items():
        if not record.get("is_split_group"):
            continue
        required_ids = [int(value) for value in record.get("_split_group_part_ids", []) if int(value or 0) > 0]
        seen_ids = {int(value) for value in record.get("_split_group_seen_part_ids", []) if int(value or 0) > 0}
        record["_split_group_part_ids"] = required_ids
        record["_split_group_seen_part_ids"] = sorted(seen_ids)
        for part_id in required_ids:
            split_message_owner[part_id] = str(key)

    filters = ((InputMessagesFilterVideo, "native"), (InputMessagesFilterDocument, "document"))
    checkpoint_every = max(1, int(checkpoint_every or INDEX_SCAN_CHECKPOINT_EVERY))
    current_checkpoint = {}

    async def persist(next_phase: int, next_cursor: int, *, complete: bool = False):
        state = {
            "version": INDEX_SCAN_CHECKPOINT_VERSION,
            "base_min_id": base_min_id,
            "max_videos": limit,
            "initial_count": initial_count,
            "phase_index": int(next_phase),
            "phase": "complete" if complete else ("native" if next_phase == 0 else "document"),
            "cursor_id": int(next_cursor),
            "records": dict(records),
            "split_volume_candidates": list(split_volume_candidates),
            "split_thumbnail_candidates": list(split_thumbnail_candidates),
            "stats": dict(stats),
            "complete": bool(complete),
        }
        current_checkpoint.clear()
        current_checkpoint.update(state)
        await _emit_index_checkpoint(checkpoint_callback, state)

    if phase_index < 2 and not current_checkpoint:
        await persist(phase_index, cursor_id)

    scan_complete = True
    limit_hit = False
    for index in range(phase_index, len(filters)):
        flt, kind = filters[index]
        resume_cursor = cursor_id if index == phase_index else 0
        # The API's limit counts every message matching the filter, not videos.
        # Generic document filters may yield PDFs, images and arbitrary files
        # before a video. Enforce max_videos below after classification instead
        # of allowing those unrelated documents to consume the scan limit.
        kwargs = {"filter": flt}
        if base_min_id:
            kwargs["min_id"] = base_min_id
        # max_id is exclusive. It means continue below the last observed item
        # during a descending scan; omitting it on a fresh pass preserves
        # compatibility with simpler test clients and Telethon's default mode.
        if resume_cursor:
            kwargs["max_id"] = resume_cursor
        seen_in_pass = 0
        stopped_by_limit = False
        async for msg in client.iter_messages(channel, **kwargs):
            if not msg:
                continue
            seen_in_pass += 1
            stats["messages"] += 1
            mid = int(getattr(msg, "id", 0) or 0)
            if mid > 0:
                cursor_id = mid
                split_owner = split_message_owner.get(mid)
                if split_owner and split_owner in records:
                    seen = set(int(value) for value in records[split_owner].get("_split_group_seen_part_ids", []) if int(value or 0) > 0)
                    seen.add(mid)
                    records[split_owner]["_split_group_seen_part_ids"] = sorted(seen)
            split_candidate = False
            # Telegram can classify a numbered upload as either a generic
            # document or a video depending on how it was originally sent.
            # Detection is filename-based, so do it in both inventory passes.
            if mid > 0:
                file_obj = getattr(msg, "file", None)
                telegram_name = str(getattr(file_obj, "name", None) or "").strip()
                if telegram_name:
                    from ..core.split_files import recognize_split_volume
                    parsed_volume = recognize_split_volume(telegram_name)
                    if parsed_volume is not None:
                        split_candidate = True
                        if mid not in split_volume_ids:
                            raw_record = make_record(msg) or {}
                            split_volume_candidates.append({
                                "id": mid, "name": telegram_name,
                                "size": int(getattr(file_obj, "size", 0) or 0),
                                "mime": str(getattr(file_obj, "mime_type", "") or ""),
                                "caption": str(getattr(msg, "message", None) or ""),
                                "duration": float(getattr(file_obj, "duration", 0) or 0),
                                "width": int(getattr(file_obj, "width", 0) or 0),
                                "height": int(getattr(file_obj, "height", 0) or 0),
                                "fps": float(getattr(file_obj, "fps", 0) or 0),
                                "bitrate": int(getattr(file_obj, "bitrate", 0) or 0),
                                "codec": str(getattr(file_obj, "video_codec", "") or ""),
                                "audio_codec": str(getattr(file_obj, "audio_codec", "") or ""),
                                "record": dict(raw_record),
                            })
                            split_volume_ids.add(mid)
                        # Numbered volumes are never indexed as standalone media,
                        # even if Telegram reports a video MIME type for .001.
                        # For a group already registered in this database, however,
                        # the primary message ID is also the logical card key. Keep
                        # that seed so reconstruction reuses its persistent group ID.
                        if not (split_owner and str(mid) == str(split_owner)):
                            records.pop(str(mid), None)
                    elif kind == "document" and Path(telegram_name).suffix.casefold() in {".jpg", ".jpeg"}:
                        raw_record = make_record(msg) or {}
                        split_thumbnail_candidates.append({
                            "id": mid, "name": telegram_name,
                            "size": int(getattr(file_obj, "size", 0) or 0),
                            "mime": str(getattr(file_obj, "mime_type", "") or ""),
                            "caption": str(getattr(msg, "message", None) or ""),
                            "record": dict(raw_record),
                        })
            if not split_candidate and str(mid) not in records:
                if kind == "document" and not _is_video(msg):
                    stats["other_documents"] += 1
                else:
                    rec = make_record(msg)
                    if rec:
                        records[str(rec["id"])] = rec
                        stats["native_videos" if getattr(msg, "video", None) else "document_videos"] += 1
            added = len(records) - initial_count
            if limit and added >= limit:
                stopped_by_limit = True
                break
            if progress and stats["messages"] % 200 == 0:
                progress(dict(stats, videos=len(records), phase=kind, cursor_id=cursor_id, complete=False))
            if seen_in_pass % checkpoint_every == 0:
                await persist(index, cursor_id)

        if stopped_by_limit:
            scan_complete = False
            limit_hit = True
            await persist(index, cursor_id)
            break

        # The current filter is complete. Advance the phase atomically so a
        # restart cannot repeat the entire first pass or skip the second one.
        cursor_id = 0
        stats["passes_completed"] = max(int(stats.get("passes_completed") or 0), index + 1)
        await persist(index + 1, 0, complete=(index + 1 == len(filters) and scan_complete))

    enumeration_complete = bool(scan_complete and not limit_hit and int(stats.get("passes_completed") or 0) == len(filters))
    full_history = bool(enumeration_complete and base_min_id == 0)
    if split_volume_candidates:
        from ..core.split_files import discover_telegram_split_groups
        discovered_groups = discover_telegram_split_groups([*split_volume_candidates, *split_thumbnail_candidates])
        # All recognised .001/.002/... documents are physical volumes, not
        # independent cards. Only a complete, ordered sequence is reinserted as
        # a logical record. Orphan/malformed volume names remain non-playable.
        for mid in split_volume_ids:
            # Preserve the primary record for a previously registered group;
            # its stable database group ID must survive re-discovery. Fresh
            # databases have no split_message_owner entry, so physical part
            # rows are still removed before the synthetic logical record is added.
            if split_message_owner.get(int(mid)) != str(int(mid)):
                records.pop(str(mid), None)
        for group_record in discovered_groups:
            key = str(int(group_record["id"]))
            prior = records.get(key, {})
            if prior.get("is_split_group"):
                merged = dict(group_record)
                merged.update({name: prior[name] for name in (
                    "duration", "width", "height", "fps", "bitrate", "codec", "audio_codec", "caption",
                    "thumbnail_message_id", "_split_group_group_id"
                ) if prior.get(name) not in (None, "", 0, 0.0)})
                merged["_split_group_parts"] = group_record["_split_group_parts"]
                merged["_split_group_part_ids"] = group_record["_split_group_part_ids"]
                merged["_split_group_seen_part_ids"] = group_record["_split_group_seen_part_ids"]
                records[key] = merged
            else:
                records[key] = group_record
    full_history = bool(enumeration_complete and base_min_id == 0)
    if full_history:
        for key, record in list(records.items()):
            if not record.get("is_split_group"):
                continue
            required_ids = {int(value) for value in record.get("_split_group_part_ids", []) if int(value or 0) > 0}
            seen_ids = {int(value) for value in record.get("_split_group_seen_part_ids", []) if int(value or 0) > 0}
            if not required_ids or not required_ids.issubset(seen_ids):
                # Never present a partial logical file after a full reconciliation.
                records.pop(key, None)
                continue
            record.pop("_split_group_part_ids", None)
            record.pop("_split_group_seen_part_ids", None)
    # `complete` now means the requested enumeration finished. `full_history`
    # separately records whether the scan covered all history or only an
    # incremental min_id window. Callers must not conflate the two.
    stats["enumeration_complete"] = enumeration_complete
    stats["full_history"] = full_history
    stats["complete"] = enumeration_complete
    stats["limit_hit"] = bool(limit_hit)
    stats["videos"] = len(records)
    if progress:
        progress(dict(stats, phase="complete" if enumeration_complete else ("limit" if limit_hit else "partial"), cursor_id=cursor_id))
    if checkpoint_callback is not None:
        await persist(2 if scan_complete else int(current_checkpoint.get("phase_index", 0)), cursor_id, complete=scan_complete)
    return records


async def update_channel_index(client, channel, records: dict, *, checkpoint: dict | None = None,
                               checkpoint_callback=None, progress=None) -> dict:
    """Append newly posted videos, resuming safely if the incremental scan stops."""
    max_id = max((int(x) for x in records), default=0)
    saved_min_id = max_id
    if isinstance(checkpoint, dict) and checkpoint.get("version") == INDEX_SCAN_CHECKPOINT_VERSION:
        saved_min_id = max(0, int(checkpoint.get("base_min_id") or max_id))
    return await build_channel_index(
        client, channel, min_id=saved_min_id, initial_records=records,
        checkpoint=checkpoint, checkpoint_callback=checkpoint_callback, progress=progress,
    )




# ==============================================================================
# SECTION: Range cache and connection policy
# Cache policy, tuning, keep flags and pruning.
# ==============================================================================

def _range_cache_manifest_path(cache_path: Path) -> Path:
    return cache_path.with_suffix(".json")


def configure_range_cache_policy(*, budget_gib=DEFAULT_RANGE_CACHE_BUDGET_GIB, retention="idle_30m") -> dict:
    """Apply persistent range-cache budget and retention settings."""
    global DISK_CACHE_BUDGET, RANGE_CACHE_RETENTION, RANGE_CACHE_TTL_SECONDS
    try:
        budget = int(float(budget_gib))
    except (TypeError, ValueError, OverflowError):
        budget = DEFAULT_RANGE_CACHE_BUDGET_GIB
    budget = max(1, min(500, budget))
    policy = str(retention or "idle_30m").strip().lower()
    if policy not in {"manual", "idle_10m", "idle_30m"}:
        policy = "idle_30m"
    DISK_CACHE_BUDGET = budget * 1024 ** 3
    RANGE_CACHE_RETENTION = policy
    RANGE_CACHE_TTL_SECONDS = 10 * 60 if policy == "idle_10m" else (30 * 60 if policy == "idle_30m" else None)
    return {
        "budget_gib": budget,
        "budget_bytes": DISK_CACHE_BUDGET,
        "retention": RANGE_CACHE_RETENTION,
        "retention_seconds": RANGE_CACHE_TTL_SECONDS,
    }


CONNECTION_PROFILE = "balanced"
CONNECTION_PROFILES = ("gentle", "balanced", "fast")


def configure_connection_profile(name) -> str:
    """Select how many parallel Telegram connections playback may open."""
    global CONNECTION_PROFILE
    name = str(name or "balanced").strip().lower()
    CONNECTION_PROFILE = name if name in CONNECTION_PROFILES else "balanced"
    # Parallel Telegram reads while the browser is streaming. One connection
    # makes every cold read (start, seek) latency-bound: 512 KiB per round trip.
    # Measured with the real scheduler and a 200 ms / 3 MB/s link: 1 or 2
    # connections took 6.2 s for 8 MiB after a seek, 3 took 3.4 s, 4+ about 3.0 s.
    active, idle = {"gentle": (1, 2), "balanced": (3, 3), "fast": (5, 5)}[CONNECTION_PROFILE]
    config.PLAYBACK_STREAM_ACTIVE_CONNECTIONS = active
    config.PLAYBACK_STREAM_IDLE_READAHEAD_CONNECTIONS = idle
    return CONNECTION_PROFILE


configure_connection_profile("balanced")


def apply_connection_profile(engine) -> dict:
    """Scale an engine's fetch/transfer limits from its account baseline.

    gentle   - few connections (kind to Telegram's flood limits, slower start)
    balanced - the account defaults (Premium 8/16, Free 4/8)
    fast     - 1.5x the defaults for very fast links
    """
    base = getattr(engine, "_base_connection_limits", None)
    if base is None:
        base = (engine.min_fetch_concurrency, engine.fetch_concurrency, engine.max_fetch_concurrency,
                engine.initial_transfer_sessions, engine.max_transfer_sessions)
        engine._base_connection_limits = base
    bmin, binit, bmax, sinit, smax = base
    if CONNECTION_PROFILE == "gentle":
        vals = (1, min(binit, 2), min(bmax, 3), min(sinit, 2), min(smax, 3))
    elif CONNECTION_PROFILE == "fast":
        vals = (bmin, int(binit * 1.5), int(bmax * 1.5), int(sinit * 1.5), int(smax * 1.5))
    else:
        vals = base
    (engine.min_fetch_concurrency, engine.fetch_concurrency, engine.max_fetch_concurrency,
     engine.initial_transfer_sessions, engine.max_transfer_sessions) = vals
    engine.adaptive_max_fetch_concurrency = engine.max_fetch_concurrency
    try:
        engine._adapt_best_concurrency = int(engine.fetch_concurrency)
        lanes = PREMIUM_PLAYBACK_LANES if getattr(engine, "premium", False) else FREE_PLAYBACK_LANES
        engine.qos.playback_lanes = max(1, min(int(engine.max_fetch_concurrency), lanes))
    except Exception:
        pass
    return {"profile": CONNECTION_PROFILE, "fetch": f"{vals[1]}/{vals[2]}", "sessions": f"{vals[3]}/{vals[4]}"}


def configure_playback_tuning(*, readahead_seconds=None, ram_mb=None) -> dict:
    """Apply the look-ahead (seconds) and RAM buffer (MiB) used for smooth playback."""
    global PLAYHEAD_READAHEAD_SECONDS, RAM_CACHE_BYTES, RAM_CACHE_SEGMENTS
    try:
        if readahead_seconds is not None:
            PLAYHEAD_READAHEAD_SECONDS = float(max(30, min(600, int(float(readahead_seconds)))))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        if ram_mb is not None:
            RAM_CACHE_BYTES = max(SEGMENT, int(max(64, min(1024, int(float(ram_mb))))) * 1024 * 1024)
            RAM_CACHE_SEGMENTS = max(1, RAM_CACHE_BYTES // max(1, SEGMENT))
    except (TypeError, ValueError, OverflowError):
        pass
    return {"readahead_seconds": int(PLAYHEAD_READAHEAD_SECONDS), "ram_mb": int(RAM_CACHE_BYTES // (1024 * 1024))}


def range_cache_badges() -> dict:
    """Per-video cached percentage for library cards, read from the manifests."""
    items = {}
    if not RANGE_CACHE_DIR.exists():
        return items
    for meta_path in RANGE_CACHE_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
            size = int(meta.get("size") or 0)
            cached = max(0, int(meta.get("cached_bytes") or 0))
            if size <= 0 or cached <= 0 or not meta_path.with_suffix(".bin").exists():
                continue
            percent = min(100.0, 100.0 * cached / size)
            if percent < 1.0:
                continue
            key = f"{int(meta.get('channel_id') or 0)}:{int(meta.get('message_id') or 0)}"
            items[key] = {"percent": round(percent, 1), "bytes": cached, "size": size,
                          "kept": bool(meta.get("kept")), "complete": bool(meta.get("complete"))}
        except (OSError, ValueError, TypeError):
            continue
    return items


def kept_range_cache_summary() -> dict:
    """Count videos whose cached ranges are kept by the user (exempt from expiry)."""
    count = 0
    cached = 0
    if RANGE_CACHE_DIR.exists():
        for meta_path in RANGE_CACHE_DIR.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(meta, dict) and meta.get("kept") and meta_path.with_suffix(".bin").exists():
                    count += 1
                    cached += max(0, int(meta.get("cached_bytes") or 0))
            except (OSError, ValueError, TypeError):
                continue
    return {"kept_entries": count, "kept_bytes": cached}


def release_all_kept_range_caches() -> dict:
    """Return every kept video to the normal retention policy (bytes stay until expiry)."""
    released = 0
    if RANGE_CACHE_DIR.exists():
        for meta_path in RANGE_CACHE_DIR.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict) or not meta.get("kept"):
                    continue
                meta["kept"] = False
                temp = meta_path.with_suffix(".json.tmp")
                temp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
                os.replace(temp, meta_path)
                released += 1
            except (OSError, ValueError, TypeError):
                continue
    return {"ok": True, "released": released}


def range_cache_usage() -> dict:
    """Return downloaded (not sparse-file logical) bytes and complete entries."""
    entries = 0
    complete = 0
    cached_bytes = 0
    files = 0
    if not RANGE_CACHE_DIR.exists():
        return {"bytes": 0, "entries": 0, "complete": 0, "files": 0}
    for meta_path in RANGE_CACHE_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            data_path = meta_path.with_suffix(".bin")
            if not data_path.exists() or data_path.stat().st_size != int(meta.get("size") or 0):
                continue
            entries += 1
            cached_bytes += max(0, int(meta.get("cached_bytes") or 0))
            complete += int(bool(meta.get("complete")))
            files += 1
        except (OSError, ValueError, TypeError):
            continue
    return {"bytes": cached_bytes, "entries": entries, "complete": complete, "files": files}


def prune_range_cache_to_budget(*, budget_bytes=None, keep_paths=None, keep_keys=None) -> dict:
    """Evict least-recently-used inactive range files until under the soft budget."""
    budget = max(0, int(DISK_CACHE_BUDGET if budget_bytes is None else budget_bytes))
    protected_paths = {Path(path).resolve() for path in (keep_paths or ())}
    with _OPEN_RANGE_CACHE_PATHS_LOCK:
        protected_paths.update(OPEN_RANGE_CACHE_PATHS)
    protected_keys = set()
    for key in keep_keys or ():
        try:
            protected_keys.add((int(key[0]), int(key[1])))
        except (TypeError, ValueError, IndexError):
            continue

    if not RANGE_CACHE_DIR.exists():
        return {"bytes_before": 0, "bytes_after": 0, "removed": 0, "bytes_removed": 0}

    total = 0
    candidates = []
    for meta_path in RANGE_CACHE_DIR.glob("*.json"):
        data_path = meta_path.with_suffix(".bin")
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or not data_path.exists():
                continue
            if data_path.stat().st_size != int(meta.get("size") or 0):
                continue
            cached_bytes = max(0, int(meta.get("cached_bytes") or 0))
            total += cached_bytes
            key = (int(meta.get("channel_id") or 0), int(meta.get("message_id") or 0))
            last_access = float(meta.get("last_access") or meta_path.stat().st_mtime)
            if meta.get("kept"):
                last_access = float("inf")  # evicted only after every unkept cache
            candidates.append((last_access, meta_path, data_path, key, cached_bytes))
        except (OSError, ValueError, TypeError):
            continue

    before = total
    removed = removed_bytes = 0
    candidates.sort(key=lambda item: item[0])
    for _, meta_path, data_path, key, cached_bytes in candidates:
        if total <= budget:
            break
        if data_path.resolve() in protected_paths or key in protected_keys:
            continue
        try:
            data_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            meta_path.with_suffix(".json.tmp").unlink(missing_ok=True)
        except OSError:
            continue
        total = max(0, total - cached_bytes)
        removed += 1
        removed_bytes += cached_bytes
    return {
        "bytes_before": before,
        "bytes_after": total,
        "removed": removed,
        "bytes_removed": removed_bytes,
    }


def range_cache_status_for_video(channel_id: int, message_id: int, *, expected_size: int | None = None, fallback_name: str = "") -> dict:
    """Describe verified byte coverage, including keyframe-calibrated time ranges."""
    cid, mid = int(channel_id), int(message_id)
    out = {
        "ok": True, "channel_id": cid, "message_id": mid,
        "name": str(fallback_name or ""), "size": max(0, int(expected_size or 0)),
        "segment_bytes": int(SEGMENT), "total_segments": 0, "segments_cached": 0,
        "cached_bytes": 0, "percent": 0.0, "complete": False, "ranges": [],
        "source": "telegram", "cache_kind": "playback", "duration": 0.0,
        "time_mapping": "approximate", "time_ranges": [],
    }
    if not RANGE_CACHE_DIR.exists():
        return out
    for meta_path in RANGE_CACHE_DIR.glob("*.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
            if int(meta.get("channel_id") or 0) != cid or int(meta.get("message_id") or 0) != mid:
                continue
            size = int(meta.get("size") or 0)
            if size <= 0 or (expected_size and size != int(expected_size)):
                continue
            data_path = meta_path.with_suffix(".bin")
            if not data_path.exists() or data_path.stat().st_size != size:
                continue
            total_segments = (size + SEGMENT - 1) // SEGMENT
            segments = sorted({int(seg) for seg in (meta.get("segments") or []) if 0 <= int(seg) < total_segments})
            ranges = []
            if segments:
                first = previous = segments[0]
                for seg in segments[1:]:
                    if seg == previous + 1:
                        previous = seg
                        continue
                    ranges.append({"start_byte": first * SEGMENT, "end_byte": min(size, (previous + 1) * SEGMENT) - 1})
                    first = previous = seg
                ranges.append({"start_byte": first * SEGMENT, "end_byte": min(size, (previous + 1) * SEGMENT) - 1})
            cached_bytes = sum(min(SEGMENT, max(0, size - seg * SEGMENT)) for seg in segments)
            complete = bool(total_segments and len(segments) >= total_segments)
            # Prefer the same persisted keyframe index used by the seek-preview
            # and preload paths. Without an index, use an explicitly approximate
            # byte/time interpolation rather than pretending VBR is linear.
            duration = 0.0
            anchors = []
            try:
                from ..core.playback_index_cache import read_manifest
                idx = read_manifest(cid, mid, size) or {}
                duration = max(0.0, float(idx.get("duration") or 0))
                for raw in idx.get("keyframes") or []:
                    try:
                        timestamp, offset = float(raw[0]), int(raw[1])
                    except (TypeError, ValueError, IndexError, OverflowError):
                        continue
                    if math.isfinite(timestamp) and timestamp >= 0 and 0 <= offset < size:
                        anchors.append((offset, timestamp))
                anchors.sort(key=lambda item: item[0])
            except Exception:
                duration, anchors = 0.0, []

            def byte_time(offset):
                if duration <= 0:
                    return None
                offset = max(0, min(size, int(offset)))
                if len(anchors) < 2:
                    return min(duration, offset / max(1, size) * duration)
                if offset <= anchors[0][0]:
                    first_offset, first_time = anchors[0]
                    if first_offset <= 0:
                        return min(duration, first_time)
                    return min(duration, max(0.0, offset / first_offset * first_time))
                for left, right in zip(anchors, anchors[1:]):
                    if left[0] <= offset <= right[0]:
                        if right[0] <= left[0]:
                            return left[1]
                        fraction = (offset - left[0]) / (right[0] - left[0])
                        return min(duration, max(0.0, left[1] + fraction * (right[1] - left[1])))
                last_offset, last_time = anchors[-1]
                if size <= last_offset:
                    return min(duration, last_time)
                return min(duration, max(last_time, last_time + (offset - last_offset) / max(1, size - last_offset) * (duration - last_time)))

            time_ranges = []
            if duration > 0:
                for item in ranges:
                    start_time = byte_time(item["start_byte"])
                    end_time = byte_time(min(size, item["end_byte"] + 1))
                    if start_time is not None and end_time is not None and end_time >= start_time:
                        time_ranges.append({"start_seconds": start_time, "end_seconds": min(duration, end_time)})
            out.update({
                "name": str(meta.get("name") or fallback_name or ""), "size": size,
                "total_segments": total_segments, "segments_cached": len(segments),
                "cached_bytes": cached_bytes,
                "percent": round(100.0 * cached_bytes / size, 2) if size else 0.0,
                "complete": complete,
                "source": "local" if complete else "telegram",
                "cache_kind": str(meta.get("cache_kind") or "playback"),
                "kept": bool(meta.get("kept")),
                "duration": duration,
                "time_mapping": "keyframes" if len(anchors) >= 2 else "approximate",
                "time_ranges": time_ranges,
                "ranges": ranges,
            })
            return out
        except (OSError, ValueError, TypeError):
            continue
    return out


def set_range_cache_kept(channel_id: int, message_id: int, kept: bool = True) -> dict:
    """Mark one video's persisted byte ranges as kept (exempt from idle expiry)."""
    cid, mid = int(channel_id), int(message_id)
    changed = 0
    if RANGE_CACHE_DIR.exists():
        for meta_path in RANGE_CACHE_DIR.glob("*.json"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if not isinstance(meta, dict):
                    continue
                if int(meta.get("channel_id") or 0) != cid or int(meta.get("message_id") or 0) != mid:
                    continue
                meta["kept"] = bool(kept)
                meta["last_access"] = time.time()
                temp = meta_path.with_suffix(".json.tmp")
                temp.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
                os.replace(temp, meta_path)
                changed += 1
            except (OSError, ValueError, TypeError):
                continue
    return {"ok": True, "kept": bool(kept), "updated": changed}


def clear_persistent_range_cache_for_video(channel_id: int, message_id: int) -> dict:
    """Delete only the persistent range bytes for one video, never other assets."""
    cid, mid = int(channel_id), int(message_id)
    removed_files = 0
    removed_bytes = 0
    if not RANGE_CACHE_DIR.exists():
        return {"ok": True, "removed_files": 0, "bytes_cleared": 0}
    for meta_path in list(RANGE_CACHE_DIR.glob("*.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict) or int(meta.get("channel_id") or 0) != cid or int(meta.get("message_id") or 0) != mid:
                continue
            data_path = meta_path.with_suffix(".bin")
            try:
                removed_bytes += max(0, int(meta.get("cached_bytes") or 0))
            except (ValueError, TypeError):
                pass
            for path in (data_path, meta_path, meta_path.with_suffix(".json.tmp")):
                try:
                    if path.exists():
                        path.unlink()
                        removed_files += 1
                except OSError:
                    pass
        except (OSError, ValueError, TypeError):
            continue
    return {"ok": True, "removed_files": removed_files, "bytes_cleared": removed_bytes}


def clear_persistent_range_cache() -> dict:
    """Delete only downloaded video byte ranges, not snapshots or MP4 indexes."""
    before = range_cache_usage()
    removed = 0
    RANGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for pattern in ("*.bin", "*.json", "*.tmp"):
        for path in RANGE_CACHE_DIR.glob(pattern):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return {"removed_files": removed, "bytes_cleared": before["bytes"], "entries_cleared": before["entries"]}


def _punch_sparse_range(path: Path, offset: int, length: int) -> bool:
    """Release one sparse-file extent without changing offsets of other ranges.

    Returns False when the filesystem does not support safe hole punching; in
    that case callers retain the manifest entry rather than claiming disk space
    was released when it was not.
    """
    offset, length = max(0, int(offset)), max(0, int(length))
    if length <= 0:
        return True
    try:
        if os.name == "nt":
            class FILE_ZERO_DATA_INFORMATION(ctypes.Structure):
                _fields_ = [("FileOffset", ctypes.c_longlong), ("BeyondFinalZero", ctypes.c_longlong)]

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            kernel32.CreateFileW.restype = ctypes.c_void_p
            kernel32.DeviceIoControl.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
            kernel32.DeviceIoControl.restype = ctypes.c_int
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.CreateFileW(str(path), 0x40000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
            invalid_handle = ctypes.c_void_p(-1).value
            if handle in (None, invalid_handle):
                return False
            try:
                info = FILE_ZERO_DATA_INFORMATION(offset, offset + length)
                returned = ctypes.c_ulong()
                # FSCTL_SET_ZERO_DATA deallocates the extent for a sparse file.
                return bool(kernel32.DeviceIoControl(
                    ctypes.c_void_p(handle), 0x000980C8, ctypes.byref(info),
                    ctypes.sizeof(info), None, 0, ctypes.byref(returned), None,
                ))
            finally:
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        fd = os.open(path, os.O_RDWR)
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            fallocate = getattr(libc, "fallocate", None)
            if fallocate is None:
                return False
            fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_longlong, ctypes.c_longlong]
            fallocate.restype = ctypes.c_int
            # Linux: FALLOC_FL_KEEP_SIZE | FALLOC_FL_PUNCH_HOLE.
            return fallocate(fd, 0x01 | 0x02, offset, length) == 0
        finally:
            os.close(fd)
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def prune_expired_range_cache(*, now: float | None = None, keep_paths=None) -> dict:
    """Delete sparse video-range caches past the selected idle TTL.

    Manual retention keeps valid ranges until an explicit clear or size-budget
    eviction. Active/current cache paths supplied by the caller are preserved.
    """
    cutoff_now = time.time() if now is None else float(now)
    ttl = RANGE_CACHE_TTL_SECONDS
    keep = {Path(path).resolve() for path in (keep_paths or ())}
    removed = 0
    removed_bytes = 0
    ranges_removed = 0
    if not RANGE_CACHE_DIR.exists():
        return {"removed": 0, "ranges_removed": 0, "bytes_removed": 0}
    handled_data = set()
    for meta_path in RANGE_CACHE_DIR.glob("*.json"):
        data_path = meta_path.with_suffix(".bin")
        handled_data.add(data_path.resolve())
        if data_path.resolve() in keep:
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
            if meta.get("kept"):
                continue  # the user chose "Keep current buffers"
            last = float(meta.get("last_access") or meta_path.stat().st_mtime)
            cache_kind = str(meta.get("cache_kind") or "playback").lower()
            cached_size = max(0, int(meta.get("cached_bytes") or 0))

            # Manual retention applies to every verified range, including
            # speculative prefetch. Idle policies retain their shorter TTL for
            # prefetch-only caches and segments.
            effective_ttl = (
                None if ttl is None
                else RANGE_CACHE_PREFETCH_TTL_SECONDS if cache_kind == "prefetch"
                else ttl
            )
            if effective_ttl is not None and cutoff_now - last > effective_ttl:
                data_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                meta_path.with_suffix(".json.tmp").unlink(missing_ok=True)
                removed += 1
                removed_bytes += cached_size
                continue

            # A video cache can contain both playback-used and speculative
            # segments. Expire old speculative segments individually instead of
            # promoting the entire file to the longer playback TTL. This keeps
            # useful playback ranges while bounding abandoned prefetch data.
            raw_segments = meta.get("segments") or []
            raw_kinds = meta.get("segment_kinds") or {}
            raw_activity = meta.get("segment_activity") or {}
            stale_prefetch = []
            if ttl is not None:
                for raw_seg in raw_segments:
                    try:
                        seg = int(raw_seg)
                        if str(raw_kinds.get(str(seg), cache_kind)).lower() != "prefetch":
                            continue
                        activity = float(raw_activity.get(str(seg), last))
                        if cutoff_now - activity > RANGE_CACHE_PREFETCH_TTL_SECONDS:
                            stale_prefetch.append(seg)
                    except (TypeError, ValueError, OverflowError):
                        continue
            if stale_prefetch:
                size_bytes = max(0, int(meta.get("size") or 0))
                # Only clear a segment from the manifest after its backing sparse
                # extent was successfully punched out of the file.
                punched = []
                for seg in stale_prefetch:
                    length = min(SEGMENT, max(0, size_bytes - seg * SEGMENT))
                    if _punch_sparse_range(data_path, seg * SEGMENT, length):
                        punched.append(seg)
                if punched:
                    ranges_removed += len(punched)
                    stale_set = set(punched)
                    meta["segments"] = [int(seg) for seg in raw_segments if int(seg) not in stale_set]
                    for seg in punched:
                        removed_bytes += min(SEGMENT, max(0, size_bytes - seg * SEGMENT))
                        raw_kinds.pop(str(seg), None)
                        raw_activity.pop(str(seg), None)
                    meta["segment_kinds"] = raw_kinds
                    meta["segment_activity"] = raw_activity
                    remaining = sorted(set(int(seg) for seg in meta["segments"]))
                    total_segments = (size_bytes + SEGMENT - 1) // SEGMENT
                    meta["cached_bytes"] = sum(min(SEGMENT, max(0, size_bytes - seg * SEGMENT)) for seg in remaining)
                    meta["complete"] = bool(total_segments and len(remaining) >= total_segments)
                    if not remaining:
                        data_path.unlink(missing_ok=True)
                        meta_path.unlink(missing_ok=True)
                        meta_path.with_suffix(".json.tmp").unlink(missing_ok=True)
                        removed += 1
                    else:
                        temp_path = meta_path.with_suffix(".json.tmp")
                        temp_path.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
                        os.replace(temp_path, meta_path)
        except (OSError, ValueError, TypeError):
            continue
    # A crash can leave a sparse .bin with no sidecar. It has no trusted range
    # map and must not persist indefinitely outside the TTL policy.
    for data_path in RANGE_CACHE_DIR.glob("*.bin"):
        if data_path.resolve() in keep or data_path.resolve() in handled_data:
            continue
        try:
            orphan_ttl = ttl if ttl is not None else RANGE_CACHE_PREFETCH_TTL_SECONDS
            if cutoff_now - data_path.stat().st_mtime <= orphan_ttl:
                continue
            data_path.unlink(missing_ok=True)
            data_path.with_suffix(".json").unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return {"removed": removed, "ranges_removed": ranges_removed, "bytes_removed": removed_bytes}


def cleanup_stale_disk_cache() -> None:
    """Remove expired sparse range caches and incomplete temp files at startup."""
    try:
        for path in DISK_CACHE_DIR.glob("*.partial*"):
            try:
                path.unlink()
            except OSError:
                pass
        RANGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        for path in RANGE_CACHE_DIR.glob("*.tmp"):
            try:
                path.unlink()
            except OSError:
                pass
        result = prune_expired_range_cache()
        if result.get("removed") or result.get("ranges_removed"):
            _stream_log(
                f"RANGE CACHE TTL startup removed_files={result.get('removed', 0)} "
                f"removed_ranges={result.get('ranges_removed', 0)} bytes={result.get('bytes_removed', 0)}"
            )
    except Exception as exc:
        logging.getLogger(__name__).warning("Playback range-cache TTL startup check failed: %s", exc)




# ==============================================================================
# SECTION: HTTP range parsing
# Parses Range headers.
# ==============================================================================

def parse_range(size: int, range_header: str, *, max_bytes: int | None = None):
    """Parse one HTTP byte range -> (start, end, status).

    Browser open-ended ranges (`bytes=X-`) are bounded to one playback segment.
    Callers may also pass ``max_bytes`` to bound explicit and suffix ranges:
    browsers sometimes explicitly request a range through EOF, and the caller
    must be able to return a smaller, correctly-described 206 response rather
    than materialize hundreds of megabytes before sending the first byte.
    """
    size = int(size)
    if size <= 0:
        raise ValueError("empty file")
    limit = max(1, int(max_bytes)) if max_bytes is not None else None
    rh = (range_header or "").strip()
    if not rh:
        return 0, size - 1, 200
    try:
        unit, ranges = rh.split("=", 1)
        if unit.strip().lower() != "bytes":
            raise ValueError
        ranges = ranges.split(",", 1)[0].strip()   # multi-range: serve the first
        first, last = ranges.split("-", 1)
        first, last = first.strip(), last.strip()
        if first:
            start = int(first)
            if last:
                end = int(last)
            else:
                # Chromium commonly sends `bytes=X-` after a seek. Do not
                # expose the entire tail of a large remote file in one HTTP
                # response; keep the response aligned with the playback
                # engine's canonical playback segment/cache unit.
                open_ended_limit = limit or OPEN_ENDED_RANGE_MAX
                end = min(size - 1, start + open_ended_limit - 1)
            if limit is not None:
                end = min(end, start + limit - 1)
        else:
            suffix = int(last)
            if suffix <= 0:
                raise ValueError
            start = max(0, size - suffix)
            end = size - 1
            if limit is not None:
                # A large suffix range still needs the tail of the file (for
                # example, an MP4 index), so preserve the end and trim from
                # the beginning rather than returning the wrong bytes.
                start = max(start, end - limit + 1)
        if start < 0 or start >= size:
            raise ValueError
        end = min(end, size - 1)
        if start > end:
            raise ValueError
        return start, end, 206
    except Exception as exc:
        raise ValueError("invalid range") from exc


# ============================================================
# Sparse on-disk cache of one video
# ============================================================



# ==============================================================================
# SECTION: Sparse on-disk video cache
# Sparse segment file with a manifest. Candidate for its own module.
# ==============================================================================

def _extend_file_fast(path, size):
    """Set a (sparse) file's logical length without writing zeros.

    On Windows, Python's file.truncate() extends via _chsize_s, which pads the file with zero
    bytes: about 7 seconds for a 1.5 GB video, and the first Play waits on it. SetEndOfFile on a
    file already marked sparse is instant.
    """
    if os.name == "nt":
        try:
            import msvcrt
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.SetFilePointerEx.argtypes = [ctypes.c_void_p, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_ulong]
            k32.SetFilePointerEx.restype = ctypes.c_int
            k32.SetEndOfFile.argtypes = [ctypes.c_void_p]
            k32.SetEndOfFile.restype = ctypes.c_int
            with open(path, "r+b") as f:
                handle = msvcrt.get_osfhandle(f.fileno())
                done = bool(k32.SetFilePointerEx(handle, int(size), None, 0) and k32.SetEndOfFile(handle))
            if done and os.path.getsize(path) == int(size):
                return
        except Exception as exc:
            _stream_log(f"RANGE CACHE fast extend unavailable path={path}: {type(exc).__name__}: {exc}")
    with open(path, "r+b") as f:
        f.truncate(int(size))


class SparseVideoCache:
    """Persistent sparse range cache for one Telegram video.

    The data file stays under player_cache/video_ranges across app restarts and
    between videos. A sidecar manifest lists fully written 512 KiB segments;
    unlisted sparse regions are never served. ``close`` releases the open file
    handle but keeps bytes. ``cleanup`` is reserved for an explicit clear.
    """
    def __init__(self, vkey, message):
        self.key = (int(vkey[0]), int(vkey[1]))
        self.message_id = int(message.id)
        self.size = int(message.file.size)
        self.name = str(message.file.name or f"video-{self.message_id}")
        digest = hashlib.sha256(f"{self.key[0]}:{self.key[1]}:{self.size}".encode("utf-8")).hexdigest()
        RANGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.path = RANGE_CACHE_DIR / f"{digest}.bin"
        self.meta_path = RANGE_CACHE_DIR / f"{digest}.json"
        self.downloaded: set[int] = set()
        self.downloaded_ranges = IntervalSet()
        self.downloaded_bytes = 0
        # The sidecar is a byte-range index as well as an idle-expiry record.
        # Idle policies expire speculative-only data sooner; manual retention
        # keeps all verified ranges until clear or budget eviction.
        self.cache_kind = "playback"
        # "Keep current buffers": a kept cache never expires by idle TTL.
        self.kept = False
        self.segment_kinds: dict[int, str] = {}
        self.segment_activity: dict[int, float] = {}
        self.lock = asyncio.Lock()
        self.readers = 0
        self._io_lock = threading.Lock()
        self._io = 0
        self.last_used = time.monotonic()
        self.last_access_epoch = time.time()
        self._last_manifest_touch = 0.0
        # Metadata updates must never wait for manifest JSON serialization or disk
        # I/O. A separate writer lock orders atomic sidecar replacements.
        self._manifest_lock = threading.RLock()
        self._manifest_writer_lock = threading.Lock()
        self._manifest_dirty_segments = 0
        self._file = None
        self._open_file()
        with _OPEN_RANGE_CACHE_PATHS_LOCK:
            OPEN_RANGE_CACHE_PATHS.add(self.path.resolve())

    @property
    def total_segments(self) -> int:
        return (self.size + SEGMENT - 1) // SEGMENT

    @property
    def cached_bytes(self) -> int:
        return int(self.downloaded_bytes)

    @property
    def complete(self) -> bool:
        return self.total_segments > 0 and len(self.downloaded) >= self.total_segments

    def _manifest_valid(self, manifest):
        return (isinstance(manifest, dict)
                and int(manifest.get("schema") or 0) == RANGE_CACHE_SCHEMA
                and int(manifest.get("channel_id") or 0) == self.key[0]
                and int(manifest.get("message_id") or 0) == self.message_id
                and int(manifest.get("size") or 0) == self.size
                and int(manifest.get("segment_bytes") or 0) == SEGMENT)

    def _open_file(self):
        """Open or create the sparse file and restore its manifest if still valid."""
        manifest = None
        loaded_manifest = False
        if self.path.exists() and self.meta_path.exists():
            try:
                candidate = json.loads(self.meta_path.read_text(encoding="utf-8"))
                if self.path.stat().st_size == self.size and self._manifest_valid(candidate):
                    manifest = candidate
                    loaded_manifest = True
            except (OSError, ValueError, TypeError):
                manifest = None
        if manifest is None:
            # Incompatible/missing metadata means none of the sparse bytes are
            # trusted; start a clean file for this video identity.
            try:
                self.path.unlink(missing_ok=True)
                self.meta_path.unlink(missing_ok=True)
            except OSError:
                pass
            # Create an empty file first. On Windows, mark it sparse before
            # extending its logical length; truncating to the full video size
            # first can reserve the entire disk space despite only a few ranges
            # being downloaded.
            with open(self.path, "wb"):
                pass
            if os.name == "nt":
                try:
                    FSCTL_SET_SPARSE = 0x900C4
                    GENERIC_WRITE = 0x40000000
                    handle = ctypes.windll.kernel32.CreateFileW(str(self.path), GENERIC_WRITE, 1 | 2 | 4, None, 3, 0, None)
                    if handle not in (0, -1):
                        returned = ctypes.c_ulong()
                        ok = ctypes.windll.kernel32.DeviceIoControl(handle, FSCTL_SET_SPARSE, None, 0, None, 0, ctypes.byref(returned), None)
                        ctypes.windll.kernel32.CloseHandle(handle)
                        if not ok:
                            _stream_log(f"RANGE CACHE sparse marking failed path={self.path}")
                except Exception as exc:
                    _stream_log(f"RANGE CACHE sparse marking unavailable path={self.path}: {type(exc).__name__}: {exc}")
            _extend_file_fast(self.path, self.size)
        else:
            try:
                self.last_access_epoch = float(manifest.get("last_access") or time.time())
            except (TypeError, ValueError):
                self.last_access_epoch = time.time()
            self.cache_kind = "prefetch" if str(manifest.get("cache_kind") or "playback").lower() == "prefetch" else "playback"
            self.kept = bool(manifest.get("kept"))
            raw_kinds = manifest.get("segment_kinds") or {}
            raw_activity = manifest.get("segment_activity") or {}
            for raw in manifest.get("segments") or []:
                try:
                    seg = int(raw)
                except (ValueError, TypeError):
                    continue
                if seg < 0 or seg >= self.total_segments:
                    continue
                self.downloaded.add(seg)
                self.downloaded_ranges.add(seg * SEGMENT, min(self.size, (seg + 1) * SEGMENT) - 1)
                self.downloaded_bytes += min(SEGMENT, max(0, self.size - seg * SEGMENT))
                try:
                    stamp = float(raw_activity.get(str(seg), self.last_access_epoch))
                except (TypeError, ValueError):
                    stamp = self.last_access_epoch
                self.segment_activity[seg] = stamp
                self.segment_kinds[seg] = "prefetch" if str(raw_kinds.get(str(seg), self.cache_kind)).lower() == "prefetch" else "playback"
        self._file = open(self.path, "r+b", buffering=0)
        # Existing manifests already are durable. Rewriting a potentially huge
        # sidecar every time a video is reopened adds avoidable startup I/O.
        if not loaded_manifest:
            self._persist_manifest_sync()

    def touch(self, kind="playback"):
        """Update hot-path access metadata without doing synchronous disk I/O."""
        now = time.time()
        self.last_used = time.monotonic()
        with self._manifest_lock:
            kind_value = str(kind or "playback").lower()
            if kind_value == "prefetch" and not self.downloaded and self.cache_kind != "prefetch":
                self.cache_kind = "prefetch"
                self._manifest_dirty_segments += 1
            elif kind_value != "prefetch" and self.cache_kind != "playback":
                self.cache_kind = "playback"
                self._manifest_dirty_segments += 1
            # Segment activity is updated when bytes are actually written/read;
            # do not walk every cached segment during a scheduler heartbeat.
            if now - self.last_access_epoch >= 5:
                self.last_access_epoch = now
                self._manifest_dirty_segments += 1

    def _manifest_payload_unlocked(self):
        downloaded = set(self.downloaded)
        return {
            "schema": RANGE_CACHE_SCHEMA,
            "channel_id": self.key[0],
            "message_id": self.message_id,
            "name": self.name,
            "size": self.size,
            "segment_bytes": SEGMENT,
            "segments": sorted(downloaded),
            "cached_bytes": int(self.downloaded_bytes),
            "complete": bool(self.total_segments and len(downloaded) >= self.total_segments),
            "last_access": float(self.last_access_epoch),
            "cache_kind": self.cache_kind,
            "kept": bool(self.kept),
            "segment_kinds": {str(seg): kind for seg, kind in self.segment_kinds.items() if seg in downloaded},
            "segment_activity": {str(seg): stamp for seg, stamp in self.segment_activity.items() if seg in downloaded},
        }

    def _manifest_payload(self):
        with self._manifest_lock:
            return self._manifest_payload_unlocked()

    def _persist_manifest_sync(self):
        # Order writers, but hold the metadata lock only long enough to copy the
        # index. JSON encoding and disk writes occur with no hot-path lock held.
        with self._manifest_writer_lock:
            with self._manifest_lock:
                payload = self._manifest_payload_unlocked()
                dirty_snapshot = self._manifest_dirty_segments
                self._manifest_dirty_segments = 0
            temp = self.meta_path.with_suffix(".json.tmp")
            try:
                serialized = json.dumps(payload, separators=(",", ":"))
                temp.write_text(serialized, encoding="utf-8")
                os.replace(temp, self.meta_path)
                self._last_manifest_touch = time.monotonic()
            except Exception:
                with self._manifest_lock:
                    self._manifest_dirty_segments += max(1, dirty_snapshot)
                raise

    def _io_enter(self):
        with self._io_lock:
            self._io += 1

    def _io_exit(self):
        with self._io_lock:
            self._io -= 1

    def busy(self) -> bool:
        with self._io_lock:
            return self._io > 0 or self.readers > 0

    def has(self, seg) -> bool:
        return int(seg) in self.downloaded

    def has_range(self, start, end) -> bool:
        if start > end:
            return True
        return self.downloaded_ranges.contains(int(start), int(end))

    def _write_sync(self, offset, data):
        self._io_enter()
        try:
            f = self._file
            if f is None:
                return False
            f.seek(offset)
            f.write(data)
            return True
        finally:
            self._io_exit()

    async def write_segment(self, seg, data, kind="playback"):
        """Write one segment off-thread and record it as present."""
        if not data or self._file is None:
            return
        seg = int(seg)
        expected = min(SEGMENT, max(0, self.size - seg * SEGMENT))
        if expected <= 0 or len(data) != expected:
            _stream_log(f"RANGE CACHE rejected short segment key={self.key} seg={seg} bytes={len(data)} expected={expected}")
            return
        should_flush = False
        async with self.lock:
            ok = await asyncio.to_thread(self._write_sync, seg * SEGMENT, data)
            if ok:
                with self._manifest_lock:
                    was_cached = seg in self.downloaded
                    old_segment_kind = self.segment_kinds.get(seg)
                    if not was_cached:
                        self.downloaded.add(seg)
                        self.downloaded_ranges.add(seg * SEGMENT, min(self.size, (seg + 1) * SEGMENT) - 1)
                        self.downloaded_bytes += expected
                        self._manifest_dirty_segments += 1
                    now = time.time()
                    kind_value = str(kind or "playback").lower()
                    segment_kind = "prefetch" if kind_value in {"prefetch", "preload", "readahead", "cache", "prewarm", "next-video", "next_prefix"} else "playback"
                    if was_cached and old_segment_kind != segment_kind:
                        self._manifest_dirty_segments += 1
                    if not self.segment_kinds or self.downloaded_bytes == expected:
                        self.cache_kind = segment_kind
                    elif segment_kind == "playback":
                        self.cache_kind = "playback"
                    self.segment_kinds[seg] = segment_kind
                    self.segment_activity[seg] = now
                    self.last_access_epoch = now
                    should_flush = (
                        self._manifest_dirty_segments >= 16
                        or time.monotonic() - self._last_manifest_touch >= 0.75
                        or self.complete
                    )
        if should_flush:
            try: await asyncio.to_thread(self._persist_manifest_sync)
            except (OSError, ValueError, TypeError): pass
        self.last_used = time.monotonic()

    def _read_sync(self, start, count):
        self._io_enter()
        try:
            with open(self.path, "rb", buffering=0) as f:
                f.seek(start)
                return f.read(count)
        finally:
            self._io_exit()

    async def read_range(self, start, end):
        # Register the reader while holding the same lock used by segment expiry
        # and writes. This prevents a pruner from punching a range between the
        # coverage check and the start of a disk read.
        async with self.lock:
            if self._file is None or not self.has_range(start, end):
                return None
            self.readers += 1
        try:
            data = await asyncio.to_thread(self._read_sync, int(start), int(end) - int(start) + 1)
        except Exception:
            return None
        finally:
            self.readers -= 1
        if len(data) != int(end) - int(start) + 1:
            return None
        self.last_used = time.monotonic()
        now = time.time()
        with self._manifest_lock:
            promote_needed = self.cache_kind != "playback"
            self.cache_kind = "playback"
            first_segment = max(0, int(start) // SEGMENT)
            last_segment = max(first_segment, int(end) // SEGMENT)
            for seg in range(first_segment, min(last_segment, self.total_segments - 1) + 1):
                if seg in self.downloaded:
                    promote_needed = promote_needed or self.segment_kinds.get(seg) != "playback"
                    self.segment_kinds[seg] = "playback"
                    self.segment_activity[seg] = now
            should_flush = now - self.last_access_epoch >= 30 or promote_needed
            if should_flush:
                self.last_access_epoch = now
                self._manifest_dirty_segments += 1
        if should_flush:
            try: await asyncio.to_thread(self._persist_manifest_sync)
            except OSError: pass
        return data

    async def prune_stale_prefetch_segments(self, *, now: float | None = None):
        """Remove expired speculative ranges only when the retention policy allows it."""
        cutoff = time.time() if now is None else float(now)
        if self._file is None or self.busy() or RANGE_CACHE_TTL_SECONDS is None:
            return []
        async with self.lock:
            if self._file is None or self.busy() or RANGE_CACHE_TTL_SECONDS is None:
                return []
            stale = []
            for seg in sorted(self.downloaded):
                if self.segment_kinds.get(seg, self.cache_kind) != "prefetch":
                    continue
                stamp = float(self.segment_activity.get(seg, self.last_access_epoch) or self.last_access_epoch)
                if cutoff - stamp > RANGE_CACHE_PREFETCH_TTL_SECONDS:
                    stale.append(seg)
            if not stale:
                return []
            punched = []
            for seg in stale:
                length = min(SEGMENT, max(0, self.size - seg * SEGMENT))
                if await asyncio.to_thread(_punch_sparse_range, self.path, seg * SEGMENT, length):
                    punched.append(seg)
            if not punched:
                return []
            with self._manifest_lock:
                for seg in punched:
                    self.downloaded.discard(seg)
                    self.segment_kinds.pop(seg, None)
                    self.segment_activity.pop(seg, None)
                    self.downloaded_bytes = max(0, self.downloaded_bytes - min(SEGMENT, max(0, self.size - seg * SEGMENT)))
                self.downloaded_ranges = IntervalSet(
                    (seg * SEGMENT, min(self.size, (seg + 1) * SEGMENT) - 1)
                    for seg in sorted(self.downloaded)
                )
                self.cache_kind = "playback" if any(k == "playback" for k in self.segment_kinds.values()) else "prefetch"
                self._manifest_dirty_segments += 1
            await asyncio.to_thread(self._persist_manifest_sync)
            return punched

    def idle_ttl_seconds(self):
        """Return the effective idle TTL for this cache's current class."""
        if self.kept or RANGE_CACHE_TTL_SECONDS is None:
            return None
        if self.cache_kind == "prefetch":
            return RANGE_CACHE_PREFETCH_TTL_SECONDS
        return RANGE_CACHE_TTL_SECONDS

    def close(self):
        """Close the handle but retain downloaded ranges for later sessions."""
        f, self._file = self._file, None
        try:
            if f is not None:
                self._persist_manifest_sync()
        except (OSError, ValueError, TypeError):
            pass
        try:
            if f is not None:
                f.close()
        except Exception:
            pass
        if f is not None:
            with _OPEN_RANGE_CACHE_PATHS_LOCK:
                OPEN_RANGE_CACHE_PATHS.discard(self.path.resolve())

    def cleanup(self):
        """Explicitly delete this video's persistent downloaded byte cache."""
        self.close()
        self.downloaded.clear()
        self.downloaded_ranges.clear()
        self.downloaded_bytes = 0
        for path in (self.path, self.meta_path, self.meta_path.with_suffix(".json.tmp")):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


# ============================================================
# Global priority scheduler
# ============================================================




# ==============================================================================
# SECTION: Global priority scheduler
# SegmentTransferScheduler. Candidate for its own module.
# ==============================================================================

def sniff_image_type(data: bytes) -> str:
    head = bytes(data[:12])
    if head.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


class SegmentTransferScheduler:
    """One queue for every Telegram segment download.

    Order: priority (0 playback window, 1 read-ahead, 2 next video,
    3 background fill), then the newest foreground request (so a seek wins
    over the position the player just left), then first-come first-served.
    Duplicate requests share one download; a higher-priority request upgrades
    a queued one.
    """

    def __init__(self, engine):
        self.engine = engine
        self.heap = []
        self.pending = {}
        self.sequence = 0
        self.condition = asyncio.Condition()
        self.worker_tasks = []
        self.running = False
        self.active_jobs = 0
        self.total_completed = 0
        self.completed_by_priority = {0: 0, 1: 0, 2: 0, 3: 0}
        self.supervisor_task = None
        self.active_entries = {}

    async def start(self):
        if self.running:
            return
        self.running = True
        for i in range(self.engine.max_fetch_concurrency):
            self.worker_tasks.append(asyncio.create_task(self._worker(i), name=f"tg-transfer-{i + 1}"))
        self.supervisor_task = asyncio.create_task(self._supervise(), name="tg-transfer-supervisor")

    async def _supervise(self):
        while self.running:
            await asyncio.sleep(1.0)
            alive = [t for t in self.worker_tasks if not t.done()]
            if len(alive) < len(self.worker_tasks):
                _stream_log(f"SCHEDULER restarting {len(self.worker_tasks) - len(alive)} worker(s)")
                used = {t.get_name() for t in alive}
                self.worker_tasks = alive
                for i in range(self.engine.max_fetch_concurrency):
                    name = f"tg-transfer-{i + 1}"
                    if name not in used:
                        self.worker_tasks.append(asyncio.create_task(self._worker(i), name=name))
            if self.heap:
                await self._notify()

    async def stop(self):
        self.running = False
        if self.supervisor_task:
            self.supervisor_task.cancel()
        await self._notify()
        for t in self.worker_tasks:
            t.cancel()
        if self.worker_tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*self.worker_tasks, return_exceptions=True), 2.0)
            except Exception:
                pass
        self.worker_tasks.clear()
        for entry in list(self.pending.values()):
            if not entry["future"].done():
                entry["future"].cancel()
        self.pending.clear()
        self.heap.clear()

    async def _notify(self):
        async with self.condition:
            self.condition.notify_all()

    def wake(self):
        try:
            _retain_task(asyncio.get_running_loop().create_task(self._notify()))
        except RuntimeError:
            pass

    def _push(self, entry):
        self.sequence += 1
        # V29.4.129: within one priority and seek generation, a range the
        # browser is waiting for goes before speculative read-ahead, and
        # read-ahead is fetched nearest-to-the-playhead first. Before, both
        # shared first-come-first-served order, so the browser's next range
        # queued behind the whole read-ahead backlog (diagnostics: 11.4 s
        # average queue wait, 13 s for a browser range, 1.2 s transfer).
        speculative = entry.get("kind") in {PLAYHEAD_READAHEAD_KIND, "cache", "prewarm", "preview"}
        order = (1, int(entry.get("seg", 0)), self.sequence) if speculative else (0, 0, self.sequence)
        heapq.heappush(self.heap, (entry["priority"], -entry["epoch"], order, entry["key"], entry))

    async def submit(self, message, key, seg, priority=1, epoch=0, kind="playback"):
        """Queue (or join) one segment download and wait for its bytes.

        ``kind=preview`` is interactive but must never steer the global
        playback AIMD controller or outrank an already-active playback read.
        """
        priority, epoch = int(priority), int(epoch)
        if hasattr(self.engine, "qos"):
            priority = self.engine.qos.priority((key[0], key[1]), int(seg), priority, str(kind or "playback"))
        # A new foreground request must be able to reclaim workers occupied by
        # low-priority preload work. The transfer future itself remains
        # single-flight; only the lower-priority owners are preempted.
        if kind in {"playback", "seek"} and priority <= 1:
            self.cancel_active_lower_priority(min_priority=2, exclude_key=key)

        entry = self.pending.get(key)
        if (entry is not None and kind in {"playback", "seek"} and priority <= 1
                and entry.get("kind") not in {"playback", "seek", PLAYHEAD_READAHEAD_KIND, "preview"}):
            # A background owner (prewarm/preload/bgcache) must not keep the browser's
            # byte range in the background lane: that lane is closed during playback,
            # so the range would hang until the browser gives up (playback stops after
            # the first few seconds while a seek still works).
            entry["kind"] = str(kind)
            if not entry["started"]:
                self._push(entry)
                await self._notify()
            else:
                task = entry.get("task")
                if task is not None and not task.done():
                    # Already running on the background lane: cancel it and queue a fresh
                    # foreground entry. Existing waiters retry via _await_browser_segment.
                    entry["cancelled_by_playback"] = True
                    self.pending.pop(key, None)
                    task.cancel()
                    entry = None
        if entry is not None:
            self.engine.metrics["scheduler_joined"] += 1
            if kind in INTERACTIVE_KINDS:
                entry["kind"] = str(kind)
            elif kind == "playback" and entry.get("kind") in {PLAYHEAD_READAHEAD_KIND, "cache"}:
                # The browser now waits on a range that read-ahead already
                # queued: it is no longer speculative, so it moves ahead of
                # the remaining read-ahead (V29.4.129).
                entry["kind"] = "playback"
                self.engine.metrics["readahead_joined"] = self.engine.metrics.get("readahead_joined", 0) + 1
                if not entry["started"]:
                    self._push(entry)
                    await self._notify()
            if (priority, -epoch) < (entry["priority"], -entry["epoch"]):
                entry["priority"], entry["epoch"] = priority, epoch
                if not entry["started"]:
                    self._push(entry)
                    await self._notify()
            entry["waiters"] = int(entry.get("waiters", 0)) + 1
            try:
                return await asyncio.shield(entry["future"])
            finally:
                await self._release_waiter(entry)

        entry = {
            "key": key, "message": message, "seg": int(seg), "priority": priority,
            "epoch": epoch, "kind": str(kind or "playback"), "started": False,
            "waiters": 1, "enqueued_at": time.monotonic(),
            "future": asyncio.get_running_loop().create_future(),
        }
        self.pending[key] = entry
        self._push(entry)
        await self._notify()
        try:
            return await asyncio.shield(entry["future"])
        finally:
            await self._release_waiter(entry)

    async def _release_waiter(self, entry):
        entry["waiters"] = max(0, int(entry.get("waiters", 0)) - 1)
        if entry["waiters"] != 0 or entry["future"].done():
            return
        key = entry["key"]
        # Do not leave an orphaned Telegram download alive after its only
        # caller (usually the timed-out initial/seek warm request) has gone
        # away. If Chromium joined the same single-flight future, its waiter
        # keeps the transfer alive.
        if self.pending.get(key) is entry and not entry["started"]:
            self.pending.pop(key, None)
            entry["future"].cancel()
            self.engine.metrics["scheduler_cancelled"] += 1
            self.wake()
            return
        if entry.get("started"):
            task = entry.get("task")
            if task is not None and not task.done():
                entry["cancelled_by_waiter"] = True
                task.cancel()

    def cancel_active_lower_priority(self, *, min_priority=2, exclude_key=None):
        """Preempt active background/cache ranges for an interactive request."""
        min_priority = int(min_priority)
        cancelled = 0
        for key, entry in list(self.active_entries.items()):
            if exclude_key is not None and key == exclude_key:
                continue
            if int(entry.get("priority", 3)) < min_priority:
                continue
            task = entry.get("task")
            if task is not None and not task.done():
                entry["cancelled_by_playback"] = True
                task.cancel()
                cancelled += 1
        if cancelled:
            self.engine.metrics["active_range_cancelled"] = self.engine.metrics.get("active_range_cancelled", 0) + cancelled
            self.wake()
        return cancelled

    def diagnostic_snapshot(self, limit: int = 24) -> dict:
        """Return a bounded scheduler view suitable for incident diagnostics."""
        def row(entry):
            key=entry.get("key") or (0,0,0)
            return {
                "channel_id": int(key[0]), "message_id": int(key[1]), "segment": int(entry.get("seg", key[2])),
                "priority": int(entry.get("priority", 3)), "epoch": int(entry.get("epoch", 0)),
                "kind": str(entry.get("kind") or "playback"), "started": bool(entry.get("started")),
                "age_ms": round(max(0.0, (time.monotonic()-float(entry.get("enqueued_at", time.monotonic())))*1000.0),1),
                "scheduler_wait_ms": round(max(0.0, (float(entry.get("started_at", time.monotonic()))-float(entry.get("enqueued_at", time.monotonic())))*1000.0),1) if entry.get("started_at") else None,
                "waiters": int(entry.get("waiters", 0)),
                "cancelled_by_seek": bool(entry.get("cancelled_by_seek")),
                "cancelled_by_waiter": bool(entry.get("cancelled_by_waiter")),
                "cancelled_by_playback": bool(entry.get("cancelled_by_playback")),
            }
        pending=sorted((row(x) for x in self.pending.values()), key=lambda x:(x["priority"],-x["epoch"],-x["age_ms"]))[:max(1,int(limit))]
        active=sorted((row(x) for x in self.active_entries.values()), key=lambda x:(x["priority"],-x["epoch"],-x["age_ms"]))[:max(1,int(limit))]
        return {"pending_count":len(self.pending),"active_count":len(self.active_entries),"workers":len(self.worker_tasks),"active_jobs":self.active_jobs,"pending":pending,"active":active}

    def demote(self, keys, priority=3):
        """Lower queued (not yet started) jobs, e.g. after the player left."""
        changed = False
        for key in keys:
            entry = self.pending.get(key)
            if entry and not entry["started"] and entry["priority"] < priority:
                entry["priority"], entry["epoch"] = priority, 0
                self._push(entry)
                changed = True
        if changed:
            self.wake()

    def cancel_stale_video(self, vkey, current_epoch, min_priority=1):
        """Cancel queued work for one video that belongs to an older seek epoch."""
        vkey = (int(vkey[0]), int(vkey[1]))
        current_epoch = int(current_epoch)
        changed = False
        for key, entry in list(self.pending.items()):
            if entry["started"] or entry["priority"] < int(min_priority):
                continue
            if (key[0], key[1]) != vkey:
                continue
            if int(entry.get("epoch", 0)) >= current_epoch:
                continue
            self.pending.pop(key, None)
            self.engine.metrics["scheduler_cancelled"] += 1
            if not entry["future"].done():
                entry["future"].cancel()
            changed = True
        if changed:
            self.wake()

    def cancel_video(self, vkey):
        """Cancel every queued and in-flight range of one video (V29.4.129).

        Used when the viewer leaves a video: nothing of it may keep
        downloading in the background.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        cancelled = 0
        for key, entry in list(self.pending.items()):
            if (key[0], key[1]) != vkey or entry["started"]:
                continue
            self.pending.pop(key, None)
            if not entry["future"].done():
                entry["future"].cancel()
            cancelled += 1
        for key, entry in list(self.active_entries.items()):
            if (key[0], key[1]) != vkey:
                continue
            task = entry.get("task")
            if task is not None and not task.done():
                entry["cancelled_by_release"] = True
                task.cancel()
                cancelled += 1
        if cancelled:
            self.engine.metrics["scheduler_cancelled"] += cancelled
            self.wake()
        return cancelled

    def cancel_active_stale_video(self, vkey, current_epoch):
        """Cancel in-flight Telegram range fetches from an older seek generation.

        The scheduler worker survives because the actual transfer is a child task.
        This makes a large seek latest-wins at the network layer instead of only
        removing queued work.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        current_epoch = int(current_epoch)
        cancelled = 0
        for key, entry in list(self.active_entries.items()):
            if (key[0], key[1]) != vkey:
                continue
            if int(entry.get("epoch", 0)) >= current_epoch:
                continue
            task = entry.get("task")
            if task is not None and not task.done():
                entry["cancelled_by_seek"] = True
                task.cancel()
                cancelled += 1
        if cancelled:
            self.engine.metrics["active_range_cancelled"] = self.engine.metrics.get("active_range_cancelled", 0) + cancelled
            self.engine.qos.stale_cancelled += cancelled
            self.wake()
        return cancelled

    def cancel_active_stale_speculative_video(self, vkey, current_epoch):
        """Cancel only stale in-flight speculation after a browser seek.

        Browser-demand ranges keep the established complete-into-cache policy:
        a seek must not cancel a range that the browser is already waiting for.
        Old read-ahead/cache/prewarm ranges have no browser waiter, however, and
        can occupy every Telegram lane after rapid seeks. Cancel those speculative
        children while preserving active demand ranges and any range adopted into
        the new seek window.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        current_epoch = int(current_epoch)
        speculative_kinds = {PLAYHEAD_READAHEAD_KIND, "cache", "prewarm"}
        cancelled = 0
        for key, entry in list(self.active_entries.items()):
            if (key[0], key[1]) != vkey or int(entry.get("epoch", 0)) >= current_epoch:
                continue
            if str(entry.get("kind") or "") not in speculative_kinds:
                continue
            task = entry.get("task")
            if task is not None and not task.done():
                entry["cancelled_by_seek"] = True
                entry["cancelled_speculation"] = True
                task.cancel()
                cancelled += 1
        if cancelled:
            self.engine.metrics["active_range_cancelled"] = self.engine.metrics.get("active_range_cancelled", 0) + cancelled
            self.engine.metrics["stale_speculation_cancelled"] = self.engine.metrics.get("stale_speculation_cancelled", 0) + cancelled
            self.wake()
        return cancelled

    def adopt_window(self, vkey, first, last, epoch):
        """Move queued/active ranges inside a new seek window to that generation.

        A short skip (+10 s, or a click a little ahead) usually lands inside
        the read-ahead that is already downloading. Those transfers are still
        exactly what the player needs, so they must not be cancelled as stale.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        first, last, epoch = int(first), int(last), int(epoch)
        adopted = 0
        for key, entry in list(self.pending.items()):
            if (key[0], key[1]) != vkey or not (first <= int(key[2]) <= last):
                continue
            if int(entry.get("epoch", 0)) >= epoch:
                continue
            entry["epoch"] = epoch
            if not entry["started"]:
                self._push(entry)
            adopted += 1
        for key, entry in list(self.active_entries.items()):
            if (key[0], key[1]) == vkey and first <= int(key[2]) <= last and int(entry.get("epoch", 0)) < epoch:
                entry["epoch"] = epoch
                adopted += 1
        if adopted:
            self.engine.metrics["readahead_adopted"] = self.engine.metrics.get("readahead_adopted", 0) + adopted
            self.wake()
        return adopted

    def cancel_readahead(self, keep_vkey=None):
        """Drop playhead read-ahead that does not belong to ``keep_vkey``."""
        keep = None if keep_vkey is None else (int(keep_vkey[0]), int(keep_vkey[1]))
        cancelled = 0
        for key, entry in list(self.pending.items()):
            if entry.get("kind") != PLAYHEAD_READAHEAD_KIND or (key[0], key[1]) == keep:
                continue
            if entry["started"]:
                task = entry.get("task")
                if task is not None and not task.done():
                    task.cancel()
                    cancelled += 1
                continue
            self.pending.pop(key, None)
            if not entry["future"].done():
                entry["future"].cancel()
            cancelled += 1
        if cancelled:
            self.engine.metrics["readahead_cancelled"] = self.engine.metrics.get("readahead_cancelled", 0) + cancelled
            self.wake()
        return cancelled

    def _active_kind_count(self, kind):
        return sum(1 for e in self.active_entries.values() if e.get("kind") == kind)

    def _admissible_during_playback(self, entry, target):
        """Lane rules while the browser is streaming (V29.4.118).

        * P0 and browser P1 ranges: always.
        * Playhead read-ahead (P1): only while one worker stays free for the
          exact range the browser may request next.
        * Seek-preview keyframes (P2): one at a time, only with a healthy
          playable buffer and a free worker.
        * Everything else at P2+: never (unchanged V29.4.109 rule).
        """
        priority = int(entry["priority"])
        kind = entry.get("kind")
        if (kind == PLAYHEAD_READAHEAD_KIND
                and time.monotonic() < float(getattr(self.engine, "_flood_premium_wait_until", 0.0) or 0.0)):
            return False
        free_after = target - self.active_jobs - 1
        if priority <= 1:
            if kind == PLAYHEAD_READAHEAD_KIND:
                return free_after >= 1 and not self._readahead_capped(entry, target)
            return True
        if kind == "preview":
            # Live seek previews may use a spare lane, but never the final
            # worker reserved for the exact playback range.
            return free_after >= 1 and self._active_kind_count("preview") < (2 if free_after >= 3 else 1)
        if kind == "preload":
            # Card-click/next-video prefixes may use one spare worker only.
            qos = getattr(self.engine, "qos", None)
            healthy = bool(qos and qos.background_prefetch_allowed(True))
            return free_after >= 1 and healthy and self._active_kind_count("preload") < 1
        return False

    def _readahead_capped(self, entry, target) -> bool:
        if entry.get("kind") != PLAYHEAD_READAHEAD_KIND:
            return False
        cap = self.engine.qos.readahead_lane_cap(target) if hasattr(self.engine, "qos") else target
        return self._active_kind_count(PLAYHEAD_READAHEAD_KIND) >= cap

    def _pick_during_playback(self, target):
        """Return the best admissible queued entry while playback is active.

        V29.4.129: scans the whole queue (in order). With the read-ahead cap a
        full read-ahead backlog would otherwise hide a preview or other
        admissible entry queued behind the first 64 items.
        """
        for item in sorted(self.heap):
            if not self._valid(item):
                continue
            entry = item[4]
            if self._admissible_during_playback(entry, target):
                return entry
        return None

    def _pick_idle(self, target, reserve):
        """Best entry while no browser range is open (between Chrome's
        requests): same order as before, but capped read-ahead waits."""
        for item in sorted(self.heap):
            if not self._valid(item):
                continue
            entry = item[4]
            if (entry.get("kind") == PLAYHEAD_READAHEAD_KIND
                    and time.monotonic() < float(getattr(self.engine, "_flood_premium_wait_until", 0.0) or 0.0)):
                continue
            if self._readahead_capped(entry, target):
                continue
            if item[0] <= 1 or self.active_jobs < max(1, target - reserve):
                return entry
            return None
        return None

    def _valid(self, item):
        entry = item[4]
        return (self.pending.get(item[3]) is entry and not entry["started"]
                and entry["priority"] == item[0] and entry["epoch"] == -item[1])

    async def _get_job(self, worker_id):
        """Wait for the next admissible segment job for a worker."""
        async with self.condition:
            while self.running:
                target = int(self.engine.fetch_concurrency)
                if self.engine.active_http_requests > 0:
                    # NoBuf-inspired: keep the critical browser stream serial by
                    # default. It avoids bursts of parallel Telegram reads while
                    # the browser is waiting for bytes; queued ranges stay in the
                    # shared cache/scheduler and will be serviced after delivery.
                    target = max(1, min(target, int(getattr(config, "PLAYBACK_STREAM_ACTIVE_CONNECTIONS", 1))))
                elif self.engine.current_key is not None:
                    # Gaps between browser Range responses can warm the current
                    # video's forward cache, but keep this pool small. Explicit
                    # full-file downloads use their separate transfer pipeline.
                    target = max(1, min(target, int(getattr(config, "PLAYBACK_STREAM_IDLE_READAHEAD_CONNECTIONS", 3))))
                # Drop stale heap entries (upgraded/demoted/cancelled jobs).
                while self.heap and not self._valid(self.heap[0]):
                    heapq.heappop(self.heap)
                if worker_id < target and self.active_jobs < target and self.heap:
                    top = self.heap[0]
                    # Drop long-waiting low-priority items into the normal queue
                    # only while playback is idle. Active playback always wins.
                    if top[0] >= 2 and self.engine.active_http_requests == 0:
                        entry = top[4]
                        eff = aging_priority(entry["priority"], entry.get("enqueued_at", time.monotonic()), time.monotonic())
                        if eff < top[0]:
                            heapq.heappop(self.heap)
                            entry["priority"] = eff
                            self._push(entry)
                            continue
                    # Keep interactive playback/seek ahead of background work,
                    # but do use genuinely spare transfer workers while playback
                    # is active. The old rule blocked every priority-2/3 job while
                    # a browser stream was open, which made the disk cache stop
                    # filling exactly when the user was watching. That guaranteed
                    # later seeks would fall back to Telegram again.
                    active_playback = self.engine.active_http_requests > 0
                    reserve = self.engine.qos.network_reserve(True) if active_playback else 1
                    if active_playback:
                        # V29.4.118: pick the best entry the playback lane
                        # rules admit (exact range > browser P1 > read-ahead >
                        # one healthy-buffer preview). Background P2+ work is
                        # still held back entirely.
                        entry = self._pick_during_playback(target)
                        if entry is None:
                            await self.condition.wait()
                            continue
                        entry["started"] = True
                        entry["started_at"] = time.monotonic()
                        if int(entry["priority"]) >= 2:
                            entry["playback_lane"] = True
                        self.active_jobs += 1
                        return entry
                    if active_playback and top[0] >= 2:
                        # V29.4.109: browser playback owns the Telegram range
                        # lane. Do not allow timeline previews, neighbour/cache
                        # fills, or other P2/P3 media work to consume a transfer
                        # slot while a browser range is active. Earlier versions
                        # allowed a small background budget here; diagnostics
                        # showed that even this spare work could leave the browser
                        # with zero playable forward buffer.
                        await self.condition.wait()
                        continue
                    entry = self._pick_idle(target, reserve)
                    if entry is not None:
                        entry["started"] = True
                        entry["started_at"] = time.monotonic()
                        self.active_jobs += 1
                        return entry
                await self.condition.wait()
            return None

    async def _worker(self, worker_id):
        """Download queued segments and resolve their futures."""
        while self.running:
            entry = await self._get_job(worker_id)
            if entry is None:
                return
            key, fut = entry["key"], entry["future"]
            try:
                if int(entry.get("priority", 3)) <= 1:
                    wait_ms = max(0.0, (time.monotonic() - float(entry.get("enqueued_at", time.monotonic()))) * 1000.0)
                    self.engine.metrics["foreground_scheduler_wait_ms"] = round(
                        0.75 * float(self.engine.metrics.get("foreground_scheduler_wait_ms", 0.0)) + 0.25 * wait_ms, 1
                    )
                _stream_log(f"SCHEDULER started channel={key[0]} message={key[1]} segment={key[2]} priority={int(entry.get('priority', 3))} kind={str(entry.get('kind') or 'playback')} epoch={int(entry.get('epoch', 0))}")
                child = asyncio.create_task(self.engine._download_segment(entry), name=f"segment-{key[0]}-{key[1]}-{key[2]}")
                entry["task"] = child
                self.active_entries[key] = entry
                try:
                    data = await child
                except asyncio.CancelledError:
                    _stream_log(f"SCHEDULER child cancelled channel={key[0]} message={key[1]} segment={key[2]} priority={int(entry.get('priority', 3))} kind={str(entry.get('kind') or 'playback')} waiters={int(entry.get('waiters', 0))}")
                    # A seek/preemption/waiter may cancel only this child
                    # transfer. Keep the scheduler worker alive for the next
                    # request unless the worker itself is being shut down.
                    if asyncio.current_task() is not None and asyncio.current_task().cancelling():
                        raise
                    if not fut.done():
                        fut.cancel()
                    continue
                self.total_completed += 1
                pr = max(0, min(3, int(entry.get("priority", 1))))
                self.completed_by_priority[pr] = self.completed_by_priority.get(pr, 0) + 1
                if not fut.done():
                    fut.set_result(data)
            except asyncio.CancelledError:
                if not fut.done():
                    fut.cancel()
                raise
            except Exception as exc:
                if not fut.done():
                    fut.set_exception(exc)
                    fut.exception()   # mark as retrieved; waiting callers still get it
                message = entry.get("message")
                document = getattr(message, "document", None)
                reference_age = _message_reference_age_seconds(message) if message is not None else None
                _stream_log(
                    f"SEGMENT FAILED key={key} dc={int(getattr(document, 'dc_id', 0) or 0)} "
                    f"reference_age_s={reference_age if reference_age is not None else 'live/unknown'} "
                    f"error_name={type(exc).__name__} error={exc}"
                )
            finally:
                self.active_entries.pop(key, None)
                entry.pop("task", None)
                async with self.condition:
                    self.active_jobs = max(0, self.active_jobs - 1)
                    if self.pending.get(key) is entry:
                        self.pending.pop(key, None)
                    self.condition.notify_all()




# ==============================================================================
# SECTION: Telegram error helpers
# Classifies migration, file reference and auth errors.
# ==============================================================================

def _file_migrate_dc(exc):
    """Extract the requested Telegram file DC from FILE_MIGRATE_X errors."""
    text = f"{type(exc).__name__} {exc}".upper()
    match = re.search(r"FILE[_ ]?MIGRATE[_ ]?(\d+)", text)
    if not match:
        match = re.search(r"FILEMIGRATE[_ ]?(\d+)", text)
    try:
        value = int(match.group(1)) if match else int(getattr(exc, "new_dc", 0) or 0)
        return value if 1 <= value <= 10 else None
    except (TypeError, ValueError):
        return None


def _is_file_reference_error(exc):
    text = f"{type(exc).__name__} {exc}".upper()
    return "FILE_REFERENCE" in text or "FILEREFERENCE" in text


def _is_dc_auth_error(exc):
    """Recognize Telegram sender/auth failures that need one fresh-message retry."""
    text = f"{type(exc).__name__} {exc}".upper().replace("-", "_")
    compact = re.sub(r"[^A-Z0-9]", "", text)
    markers = (
        "AUTH_KEY_UNREGISTERED", "AUTH_KEY_INVALID", "AUTH_KEY_DUPLICATED",
        "AUTH_BYTES_INVALID", "SESSION_REVOKED", "AUTH_KEY_PERM_EMPTY",
        "AUTH_KEY_NOT_FOUND", "AUTHORIZATION_KEY_INVALID",
    )
    return any(marker in text or marker.replace("_", "") in compact for marker in markers)


def _message_reference_age_seconds(message):
    saved_at = getattr(message, "_archive_x_reference_saved_at", None)
    if saved_at is None:
        return None
    try:
        return max(0, int(time.time() - float(saved_at)))
    except (TypeError, ValueError, OverflowError):
        return None


# ============================================================
# Playback engine
# ============================================================



# ==============================================================================
# SECTION: PlaybackEngine
# Shared playback engine. The groups below are the natural split points.
# ==============================================================================

class PlaybackEngine:
    """Streams Telegram videos from any channel to browser/VLC/MPC-HC."""


    # ------------------------------------------------------------------
    # SUBSECTION: Engine lifecycle, leases and idle cleanup
    # Construction, claims, releases, idle cleanup and status.
    # ------------------------------------------------------------------
    def __init__(self, client, premium=False, resources=None, account_profile=None, job_manager=None):
        """Set up clients, profile, caches, controllers and the scheduler."""
        self.client = client
        self.governor = getattr(client, "_telegram_archive_governor", None)
        profile = account_profile if isinstance(account_profile, TelegramAccountProfile) else None
        self.account_profile = profile
        self.premium = bool(profile.premium) if profile is not None else bool(premium)
        # Shared resource governor supplied by the web application. Older
        # builds passed this keyword from routes.py but the engine constructor
        # did not accept it, causing browser and external playback to fail
        # before streaming began with: unexpected keyword argument 'resources'.
        self.resources = resources
        self.job_manager = job_manager
        if profile is not None:
            self.fetch_concurrency = profile.playback_initial_fetch
            self.min_fetch_concurrency = profile.playback_min_fetch
            self.max_fetch_concurrency = profile.playback_max_fetch
            self.max_transfer_sessions = profile.playback_max_transfer_sessions
            self.initial_transfer_sessions = profile.playback_initial_transfer_sessions
        elif self.premium:
            self.fetch_concurrency = PREMIUM_INITIAL_FETCH_CONCURRENCY
            self.min_fetch_concurrency = PREMIUM_MIN_FETCH_CONCURRENCY
            self.max_fetch_concurrency = PREMIUM_MAX_FETCH_CONCURRENCY
            self.max_transfer_sessions = PREMIUM_MAX_TRANSFER_SESSIONS
            self.initial_transfer_sessions = PREMIUM_INITIAL_TRANSFER_SESSIONS
        else:
            self.fetch_concurrency = FREE_INITIAL_FETCH_CONCURRENCY
            self.min_fetch_concurrency = FREE_MIN_FETCH_CONCURRENCY
            self.max_fetch_concurrency = FREE_MAX_FETCH_CONCURRENCY
            self.max_transfer_sessions = FREE_MAX_TRANSFER_SESSIONS
            self.initial_transfer_sessions = FREE_INITIAL_TRANSFER_SESSIONS
        self.adaptive_max_fetch_concurrency = self.max_fetch_concurrency
        apply_connection_profile(self)
        self._flood_premium_wait_until = 0.0
        self.flood_premium_waits = 0
        self.cdn_redirects = 0
        # Count raw upload.getFile RPCs for playback diagnostics. _raw_get_file
        # increments this before its first Telegram request; omitting the
        # initialization aborts every uncached segment with AttributeError.
        self.rpc_requests = 0

        self.running = False
        self._adapt_samples = []
        self._rate_estimator = RobustRateEstimator(window=8, alpha=0.35)
        self.buffer_controller = AdaptiveBufferController(
            min_buffer=getattr(config, "PLAYBACK_BUFFER_MIN_SECONDS", 2.0),
            foreground_min=getattr(config, "PLAYBACK_FOREGROUND_MIN_SECONDS", 2.0),
            seek_target=getattr(config, "PLAYBACK_SEEK_BUFFER_TARGET_SECONDS", 2.0),
            low_water=getattr(config, "PLAYBACK_BUFFER_LOW_WATER_SECONDS", 2.0),
            high_water=getattr(config, "PLAYBACK_BUFFER_HIGH_WATER_SECONDS", 14.0),
        )
        self.qos = PlaybackQoSController()
        self.qos.playback_lanes = max(1, min(int(self.max_fetch_concurrency),
                                             PREMIUM_PLAYBACK_LANES if self.premium else FREE_PLAYBACK_LANES))
        self.retry_policy = RetryPolicy(max_attempts=SEGMENT_ATTEMPTS if "SEGMENT_ATTEMPTS" in globals() else 4)
        self._adapt_last_speed = 0.0
        self._last_concurrency_change = 0.0
        self._adapt_best_speed = 0.0
        self._adapt_best_concurrency = int(self.fetch_concurrency)
        self._adapt_lock = asyncio.Lock()
        self._pool_lock = asyncio.Lock()
        # The main Telethon client manages a separate exported-sender pool per
        # Telegram data centre. Keep small counters here for diagnostics; never
        # route a document from DC X through a raw GetFile sender in DC Y.
        self._dc_request_counts = {}

        self.entities = {}
        self.messages = OrderedDict()          # (cid, mid) -> Message (real or saved-location proxy)
        self.message_locks = {}
        self.message_refreshed_at = {}   # vkey -> monotonic time of the last completed live refresh
        self.segments = OrderedDict()          # (cid, mid, seg) -> bytes
        self._readahead_ladder = {}            # (cid, mid) -> 0..4 progressive batch level
        self._readahead_completed = {}         # (cid, mid) -> successful speculative segment count
        self.media_resources = {}
        self.disk_caches = OrderedDict()       # (cid, mid) -> SparseVideoCache
        self.disk_cache_init_locks = {}         # serialize async persistent-cache open per video
        self.preload_tasks = {}                # explicit card-click preload tasks
        self.pending_seek_preloads = {}        # resume-position windows queued behind an active prefix preload
        self.last_playhead = {}
        self.readahead_inflight = set()
        self.current_key = None
        # Explicit seek buffering is tracked separately from active HTTP requests;
        # normal playback ranges must not be mistaken for a seek indefinitely.
        self._seek_buffer_target_key = None
        self._seek_buffer_target_segment = None
        self.active_http_requests = 0
        self.epoch = 0
        self.play_order = []                   # [(cid, mid), ...]
        self.next_prefix_started = set()
        self.next_prefix_tasks = {}         # current video -> one queued-next prefix task
        self.next_prefix_owners = {}        # next video -> current video that requested the warmup
        self.bytes_downloaded = 0
        self.bytes_served = 0
        self.trace = PlaybackTrace(max_events=2048)
        self.metrics = {
            "cache_ram_hits": 0, "cache_disk_hits": 0, "cache_misses": 0,
            "scheduler_joined": 0, "scheduler_cancelled": 0,
            "seeks": 0, "preview_requests": 0, "buffer_rebuffers": 0,
            "range_requests": 0, "range_cache_hits": 0, "range_cache_misses": 0,
            "active_range_cancelled": 0,
            "stale_speculation_cancelled": 0,
            "media_resource_reads": 0, "media_resource_preview_reads": 0,
            "foreground_active_ranges": 0, "foreground_completed_ranges": 0,
            "foreground_bytes_per_second": 0.0, "foreground_telegram_wait_ms": 0.0,
            "foreground_telegram_transfer_ms": 0.0, "foreground_scheduler_wait_ms": 0.0,
            "browser_delivery_completed": 0, "browser_delivery_client_cancelled": 0,
            "browser_delivery_ready_bytes": 0, "browser_delivery_ready_ms": 0.0,
            "browser_delivery_last_ms": 0.0,
        }

        self.transfer_clients = []
        self._sticky_clients = {}      # vkey -> transfer client
        self.connection_stats = {}
        self.raw_bad_dcs = set()
        self.scheduler = SegmentTransferScheduler(self)
        self.tracked_tasks = set()
        # Browser HTTP range collectors are tracked separately from background
        # work. The HTTP handler shields these collectors from client disconnect
        # cancellation so an already-admitted Telegram range can finish and be
        # reused by the browser's next Range request.
        self.browser_range_tasks = set()
        self._background_cache_task = None   # progressive full-file download
        self._background_cache_vkey = None
        self._background_cache_retry_segments = set()
        self.released_videos = {}      # (cid, mid) -> monotonic time the viewer left it
        # Active browser claims prevent a delayed release from an earlier
        # same-video view from cancelling a newly attached stream.
        self.playback_claims = {}      # (cid, mid) -> client_id -> (generation, token)
        self._pending_cleanup = set()  # released temp caches still being read/written
        self.controller = PlaybackController(engine=self)

    # ---------------- lifecycle

    async def start(self):
        if self.running:
            return
        # Disk-cache cleanup can walk a large persistent cache tree. Keep that
        # filesystem work off the ASGI event loop, including during background
        # engine warm-up immediately after Telegram connects.
        await asyncio.to_thread(cleanup_stale_disk_cache)
        self._starting = True
        try:
            await self._ensure_transfer_sessions(self.initial_transfer_sessions)
            await self.scheduler.start()
            self.running = True
        finally:
            self._starting = False
        self._idle_watch_task = asyncio.create_task(self._idle_watch(), name="playback-idle-watch")
        if self.governor is not None and hasattr(self.governor, "configure_playback_lanes"):
            try:
                self.governor.configure_playback_lanes(self.qos.playback_lanes, self.qos.playback_lanes)
            except Exception as exc:
                _stream_log(f"ENGINE could not configure playback lanes: {type(exc).__name__}: {exc}")
        _stream_log(f"ENGINE started premium={self.premium} lanes={self.qos.playback_lanes} transfer_connections={len(self.transfer_clients)} "
                    f"concurrency={self.fetch_concurrency}/{self.max_fetch_concurrency}")

    def request_stop(self, reason: str = "operator") -> bool:
        """Synchronously mark playback stopped and cancel active range work.

        The web API uses this fast phase before scheduling slower Telegram client
        and persistent-cache teardown in the background. That way a stuck seek
        cannot leave the Stop button waiting for every transfer to unwind.
        """
        if getattr(self, "_stop_requested", False):
            return False
        self._stop_requested = True
        key = self.current_key
        self.running = False
        self.qos.end()
        self._seek_buffer_target_key = None
        self._seek_buffer_target_segment = None
        tasks = (
            list(self.tracked_tasks)
            + list(self.preload_tasks.values())
            + list(self.next_prefix_tasks.values())
            + list(self.browser_range_tasks)
            + [getattr(self, "_idle_clear_task", None), getattr(self, "_idle_watch_task", None)]
        )
        current = asyncio.current_task()
        for task in tasks:
            if task is not None and task is not current and not task.done():
                task.cancel()
        if key is not None:
            self.trace.record("stop", key=f"{key[0]}:{key[1]}", detail=f"request:{reason}")
        _stream_log(f"ENGINE stop requested reason={str(reason)[:40]} active_http={self.active_http_requests} queued={len(self.scheduler.pending)} active={self.scheduler.active_jobs}")
        return True

    async def stop(self):
        """Cancel tasks, close caches and disconnect transfer sessions."""
        self.request_stop("engine_stop")
        tasks = [t for t in list(self.tracked_tasks) + list(self.preload_tasks.values()) + list(self.next_prefix_tasks.values()) + list(self.browser_range_tasks) if t and not t.done()]
        for name in ("_idle_clear_task", "_idle_watch_task"):
            pending = getattr(self, name, None)
            if pending is not None and not pending.done():
                tasks.append(pending)
        for t in tasks:
            t.cancel()
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2.0)
            except Exception:
                pass
        self.tracked_tasks.clear()
        self.browser_range_tasks.clear()
        self.preload_tasks.clear()
        self.pending_seek_preloads.clear()
        self.next_prefix_tasks.clear()
        self.next_prefix_owners.clear()
        try:
            await asyncio.wait_for(self.scheduler.stop(), 3.0)
        except Exception:
            pass
        for cache in list(self.disk_caches.values()) + list(self._pending_cleanup):
            for _ in range(20):
                if not cache.busy():
                    break
                await asyncio.sleep(0.1)
            await asyncio.to_thread(cache.close)
        self.disk_caches.clear()
        self._pending_cleanup.clear()
        self.segments.clear()
        self.released_videos.clear()
        self.playback_claims.clear()
        for tc in list(self.transfer_clients):
            try:
                await asyncio.wait_for(tc.disconnect(), 1.0)
            except Exception:
                pass
        self.transfer_clients.clear()
        self.connection_stats.clear()
        self._sticky_clients.clear()
        self.current_key = None
        self.qos.end()
        self._playback_lease = False
        await self.controller.close()
        _stream_log("ENGINE stopped")

    def _schedule_cache_close(self, cache) -> bool:
        """Close an open persistent cache handle off-loop without deleting bytes."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            cache.close()
            return True
        _retain_task(loop.create_task(asyncio.to_thread(cache.close), name=f"playback-cache-close-{cache.message_id}"))
        return False

    def claim_playback(self, vkey, *, client_id: str, generation: int, token: str) -> bool:
        """Register the current browser view before resolving its first range."""
        key = (int(vkey[0]), int(vkey[1]))
        client_id = str(client_id or "").strip()
        token = str(token or "").strip()
        generation = int(generation)
        if not client_id or not token or generation < 1:
            return False
        now = time.monotonic()
        for old_key, old_claims in list(self.playback_claims.items()):
            for old_client, old_claim in list(old_claims.items()):
                if (not old_claim.get("active")
                        and now - float(old_claim.get("updated_at") or 0) > PLAYBACK_CLAIM_TOMBSTONE_SECONDS):
                    old_claims.pop(old_client, None)
            if not old_claims:
                self.playback_claims.pop(old_key, None)
        claims = self.playback_claims.setdefault(key, {})
        previous = claims.get(client_id)
        if previous is not None:
            previous_generation = int(previous.get("generation") or 0)
            previous_token = str(previous.get("token") or "")
            if generation < previous_generation:
                return False
            if generation == previous_generation and token != previous_token:
                return False
            if generation == previous_generation and not previous.get("active"):
                return False
        claims[client_id] = {
            "generation": generation, "token": token,
            "active": True, "updated_at": now,
        }
        self.released_videos.pop(key, None)
        return True

    def release_video(self, vkey, *, reason: str = "released", client_id: str | None = None,
                      generation: int | None = None, token: str | None = None) -> dict:
        """Stop and forget one video completely (V29.4.129).

        Called when the viewer goes Back or moves to another video. Active
        Telegram work and RAM bytes are released, while verified disk ranges
        remain in the persistent cache until the user clears them or LRU evicts
        old entries. Only the playing video receives full-file cache priority.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        if client_id and token and generation is not None:
            claims = self.playback_claims.setdefault(vkey, {})
        else:
            claims = self.playback_claims.get(vkey, {})
        if client_id and token and generation is not None:
            client_id = str(client_id)
            expected = (int(generation), str(token))
            current = claims.get(client_id)
            if current is not None and (
                int(current.get("generation") or 0) != expected[0]
                or str(current.get("token") or "") != expected[1]
                or not current.get("active")
            ):
                return {"stopped": 0, "ram_pieces": 0, "temp_file_deleted": False,
                        "cache_retained": True, "ignored": "stale_release"}
            if current is not None:
                current["active"] = False
                current["updated_at"] = time.monotonic()
            else:
                claims[client_id] = {
                    "generation": expected[0], "token": expected[1],
                    "active": False, "updated_at": time.monotonic(),
                }
            if any(claim.get("active") for owner, claim in claims.items() if owner != client_id):
                return {"stopped": 0, "ram_pieces": 0, "temp_file_deleted": False,
                        "cache_retained": True, "ignored": "another_viewer_active"}
        else:
            for claim in claims.values():
                claim["active"] = False
                claim["updated_at"] = time.monotonic()
        stopped = 0
        # Cancel background progressive cache if it's for this video
        if self._background_cache_vkey == vkey:
            self._cancel_background_cache()
        self.pending_seek_preloads.pop(vkey, None)
        for tasks in (self.preload_tasks, self.next_prefix_tasks):
            task = tasks.pop(vkey, None)
            if task is not None and not task.done():
                task.cancel()
                stopped += 1
        self.next_prefix_owners.pop(vkey, None)
        for next_key, owner_key in list(self.next_prefix_owners.items()):
            if owner_key != vkey:
                continue
            self.next_prefix_owners.pop(next_key, None)
            task = self.next_prefix_tasks.pop(next_key, None)
            if task is not None and not task.done():
                task.cancel()
                stopped += 1
        for task in list(self.browser_range_tasks):
            if getattr(task, "_ax_vkey", None) == vkey and not task.done():
                task.cancel()
                stopped += 1
        stopped += self.scheduler.cancel_video(vkey)
        getattr(self, "media_resources", {}).pop(vkey, None)
        now_mark = time.monotonic()
        self.released_videos = {k: t for k, t in self.released_videos.items() if now_mark - t <= RELEASED_VIDEO_QUIET_SECONDS}
        self.released_videos[vkey] = now_mark
        self.readahead_inflight = {k for k in self.readahead_inflight if (k[0], k[1]) != vkey}
        self._readahead_completed.pop(vkey, None)
        self._readahead_ladder.pop(vkey, None)
        self.last_playhead.pop(vkey, None)
        self.next_prefix_started.discard(vkey)
        self._sticky_clients.pop(vkey, None)
        ram = [k for k in self.segments if (k[0], k[1]) == vkey]
        for k in ram:
            self.segments.pop(k, None)
        if self._seek_buffer_target_key == vkey:
            self._seek_buffer_target_key = None
            self._seek_buffer_target_segment = None
        closed = False
        cache = self.disk_caches.pop(vkey, None)
        if cache is not None:
            if not cache.busy():
                closed = self._schedule_cache_close(cache)
            else:
                self._cleanup_when_unread(cache)
        if self.current_key == vkey:
            self.current_key = None
            self.qos.end()
        self.trace.record("stop", key=f"{vkey[0]}:{vkey[1]}", detail=f"release:{reason}")
        _stream_log(f"RELEASE {vkey} ({reason}): {stopped} transfer(s) stopped, "
                    f"{len(ram)} RAM piece(s) dropped, persistent byte cache {'closed/retained' if closed else ('pending close/retained' if cache else 'not open')}")
        return {"stopped": stopped, "ram_pieces": len(ram), "temp_file_deleted": False, "cache_retained": cache is not None}

    def release_other_videos(self, keep_vkey=None, *, keep_keys=(), reason: str = "switched") -> int:
        """Release transient state except the current video and its next queued item."""
        keep = set()
        if keep_vkey is not None:
            keep.add((int(keep_vkey[0]), int(keep_vkey[1])))
        keep.update((int(k[0]), int(k[1])) for k in (keep_keys or ()))
        videos = set(self.disk_caches) | set(self.preload_tasks) | set(self.next_prefix_tasks)
        videos |= {(k[0], k[1]) for k in self.segments}
        videos |= {(k[0], k[1]) for k in self.scheduler.pending}
        videos |= {(k[0], k[1]) for k in self.scheduler.active_entries}
        released = 0
        for v in videos:
            if v not in keep:
                self.release_video(v, reason=reason)
                released += 1
        return released

    async def _idle_watch(self):
        """Safety net (V29.4.129): release any video nobody has played for
        IDLE_RELEASE_SECONDS -- covers a closed tab or a sleeping computer,
        when the page never sends its release."""
        while self.running:
            try:
                await asyncio.sleep(IDLE_WATCH_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                return
            for cache in list(self._pending_cleanup):
                if not cache.busy():
                    await asyncio.to_thread(cache.close)
                    self._pending_cleanup.discard(cache)
            # Prune expired speculative segments even in an open cache handle;
            # the global pruner skips open files for Windows safety. Clear their
            # RAM mirrors too so they cannot be served after their manifest entry.
            for cache_key, cache in list(self.disk_caches.items()):
                try:
                    stale_segments = await cache.prune_stale_prefetch_segments()
                    for seg in stale_segments:
                        self.segments.pop((cache_key[0], cache_key[1], int(seg)), None)
                    if stale_segments:
                        _stream_log(f"RANGE CACHE speculative ranges expired key={cache_key} segments={len(stale_segments)} cached_bytes={cache.cached_bytes}")
                except Exception as exc:
                    _stream_log(f"RANGE CACHE speculative segment expiry failed key={cache_key}: {type(exc).__name__}: {exc}")
            keep_paths = set()
            if self.current_key in self.disk_caches:
                keep_paths.add(self.disk_caches[self.current_key].path)
            await asyncio.to_thread(prune_expired_range_cache, keep_paths=keep_paths)
            budget_result = await asyncio.to_thread(
                prune_range_cache_to_budget,
                budget_bytes=DISK_CACHE_BUDGET,
                keep_keys=self._protected_videos(),
            )
            if budget_result.get("removed"):
                _stream_log(
                    f"RANGE CACHE budget evicted={budget_result['removed']} "
                    f"bytes={budget_result['bytes_removed']} remaining={budget_result['bytes_after']} "
                    f"limit={DISK_CACHE_BUDGET}"
                )
            # Expired idle cache handles must be closed before their files can be removed on Windows.
            active_key = self.current_key if self.active_http_requests > 0 else None
            for cache_key, cache in list(self.disk_caches.items()):
                if cache_key == active_key or cache.busy():
                    continue
                cache_ttl = cache.idle_ttl_seconds()
                if cache_ttl is not None and time.time() - float(cache.last_access_epoch or 0) > cache_ttl:
                    self.disk_caches.pop(cache_key, None)
                    await asyncio.to_thread(cache.cleanup)
            if self.active_http_requests > 0:
                continue
            idle_for = time.monotonic() - float(getattr(self, "_last_range_activity", 0.0) or 0.0)
            if idle_for < IDLE_RELEASE_SECONDS:
                continue
            videos = set(self.disk_caches) | set(self.preload_tasks) | set(self.next_prefix_tasks)
            if self.current_key is not None:
                videos.add(self.current_key)
            for v in videos:
                self.release_video(v, reason="idle")

    def _cleanup_when_unread(self, cache, attempts: int = 40):
        """Close a persistent cache handle as soon as its last reader finishes."""
        self._pending_cleanup.add(cache)

        async def wait_then_delete():
            try:
                for _ in range(attempts):
                    if not cache.busy():
                        break
                    await asyncio.sleep(0.25)
            finally:
                if not cache.busy():
                    await asyncio.to_thread(cache.close)
                    self._pending_cleanup.discard(cache)
        try:
            self._spawn(wait_then_delete(), f"release-cache-{cache.message_id}")
        except RuntimeError:
            pass

    def clear_current_video_if_idle(self, *, reason: str = "playback_idle", grace: float | None = None) -> bool:
        """Clear the synthetic playback runtime identity once playback is idle.

        ``current_key`` identifies the video currently owned by the interactive
        playback engine; it is not itself proof that a browser is still playing.
        Browser/player teardown can leave the engine alive for reuse, so the
        Active Jobs projection must not treat the last video as an indefinitely
        active playback job.
        """
        if self.active_http_requests > 0:
            return False
        if self.resources is not None and getattr(self.resources, "interactive_playback", False):
            return False
        key = self.current_key
        if key is None:
            return False
        delay = PLAYBACK_IDLE_CLEAR_GRACE_SECONDS if grace is None else max(0.0, float(grace))
        if delay > 0:
            # V29.4.120: defer; only clear if no browser range arrives meanwhile.
            stamp = float(getattr(self, "_last_range_activity", 0.0))
            pending = getattr(self, "_idle_clear_task", None)
            if pending is not None and not pending.done():
                pending.cancel()

            async def deferred(k=key, st=stamp, why=reason, wait=delay):
                try:
                    await asyncio.sleep(wait)
                except asyncio.CancelledError:
                    return
                if self.current_key != k or float(getattr(self, "_last_range_activity", 0.0)) > st:
                    return
                self.clear_current_video_if_idle(reason=why, grace=0)
            try:
                self._idle_clear_task = asyncio.get_running_loop().create_task(deferred(), name="playback-idle-clear")
            except RuntimeError:
                pass
            return False
        self.current_key = None
        self.last_playhead.pop(key, None)
        self.qos.end()
        self.scheduler.cancel_stale_video(key, self.epoch + 1, min_priority=0)
        self.scheduler.wake()
        self.trace.record("stop", key=f"{key[0]}:{key[1]}", detail=str(reason))
        return True

    def status(self) -> dict:
        """Return engine, cache and scheduler status for the API."""
        return {
            "running": self.running,
            "current_video": {
                "channel_id": int(self.current_key[0]), "message_id": int(self.current_key[1]),
                "title": (getattr(getattr(self.messages.get(self.current_key), "file", None), "name", None)
                           or f"Video {int(self.current_key[1])}")
            } if self.current_key is not None else None,
            "premium": self.premium,
            "account_profile": self.account_profile.to_dict() if self.account_profile else None,
            "transfer_connections": len(self.transfer_clients),
            "transfer_sessions_initial": self.initial_transfer_sessions,
            "transfer_sessions_max": self.max_transfer_sessions,
            "concurrency": self.fetch_concurrency,
            "concurrency_min": self.min_fetch_concurrency,
            "concurrency_max": self.max_fetch_concurrency,
            "adaptive_max_concurrency": self.adaptive_max_fetch_concurrency,
            "queued": len(self.scheduler.pending),
            "active": self.scheduler.active_jobs,
            "http_streams": self.active_http_requests,
            "cached_videos": len(self.disk_caches),
            "downloaded": _human_bytes(self.bytes_downloaded),
            "served": _human_bytes(self.bytes_served),
            "playback_sessions": len(self.controller.sessions),
            "playback_generations": {k: v.generation for k, v in self.controller.sessions.items()},
            "playback_backends": self.controller.backend_status(),
            "speed": _human_bytes(self._adapt_last_speed) + "/s",
            "flood_premium_waits": self.flood_premium_waits,
            "flood_premium_wait_until": self._flood_premium_wait_until if self._flood_premium_wait_until > time.monotonic() else None,
            "cdn_redirects": self.cdn_redirects,
            "completed_by_priority": dict(self.scheduler.completed_by_priority),
            "cache_segments_ram": len(self.segments),
            "cache_ram_bytes": sum(len(value) for value in self.segments.values()),
            "ram_budget_bytes": RAM_CACHE_BYTES,
            "cache_budget_bytes": DISK_CACHE_BUDGET,
            "metrics": dict(self.metrics),
            "media_resource": {"shared": True, "random_access": True, "preview_isolation": False, "active_video_segments_pinned": True, "resources": len(getattr(self,"media_resources",{}))},
            "range_policy": {
                "chunk_bytes": SEGMENT,
                "chunk_kib": SEGMENT // 1024,
                "open_ended_max_bytes": OPEN_ENDED_RANGE_MAX,
                "prebuffer_target_seconds": PLAYHEAD_READAHEAD_SECONDS,
                "prebuffer_max_bytes": PLAYHEAD_READAHEAD_MAX_BYTES,
                "ram_budget_bytes": RAM_CACHE_BYTES,
                "active_stream_connections": int(getattr(config, "PLAYBACK_STREAM_ACTIVE_CONNECTIONS", 1)),
                "idle_readahead_connections": int(getattr(config, "PLAYBACK_STREAM_IDLE_READAHEAD_CONNECTIONS", 3)),
                "http_partial_content": True,
                "sparse_cache": True,
                "seek_generation_cancellation": True,
                "browser_delivery": "materialized_exact_range",
                "browser_disconnect_safe": True,
                "background_media_blocked_during_browser_stream": True,
            },
            "next_video_prefix": {"jobs": len(self.next_prefix_tasks),
                                  "protected": [list(k) for k in sorted(self._protected_videos()) if k]},
            "performance_trace": self.trace.snapshot(limit=128),
            "playback_qos": self.qos.status(),
            "scheduler_detail": self.scheduler.diagnostic_snapshot(),
            "foreground_active_ranges": sum(1 for row in self.scheduler.active_entries.values() if int(row.get("priority", 3)) <= 1),
            "buffer_controller": {
                "target_seconds": self.buffer_controller.last_decision.target_seconds,
                "foreground_seconds": self.buffer_controller.last_decision.foreground_seconds,
                "readahead_seconds": self.buffer_controller.last_decision.readahead_seconds,
                "reason": self.buffer_controller.last_decision.reason,
            },
        }


    # ------------------------------------------------------------------
    # SUBSECTION: Worker supervision, transfer sessions and bandwidth
    # Sessions, client choice and throughput.
    # ------------------------------------------------------------------
    def _spawn(self, coro, label="task"):
        task = asyncio.create_task(coro, name=label)
        self.tracked_tasks.add(task)

        def done(t):
            self.tracked_tasks.discard(t)
            if not t.cancelled():
                t.exception()
        task.add_done_callback(done)
        return task

    # ---------------- transfer connections

    async def _ensure_transfer_sessions(self, target):
        """Grow or shrink extra Telegram transfer sessions to the target count."""
        if not TELETHON_AVAILABLE:
            return
        target = max(1, min(int(target), self.max_transfer_sessions))
        async with self._pool_lock:
            # Remove dead transfer clients before sizing the pool. Telethon's
            # auto-reconnect handles transient drops; clients that remain down
            # are replaced by this background health pass.
            for dead in list(self.transfer_clients):
                try:
                    healthy = bool(dead.is_connected())
                except Exception:
                    healthy = False
                if healthy:
                    continue
                self.transfer_clients.remove(dead)
                self.connection_stats.pop(id(dead), None)
                for key, client in list(self._sticky_clients.items()):
                    if client is dead:
                        self._sticky_clients.pop(key, None)
                try:
                    await asyncio.wait_for(dead.disconnect(), 1.0)
                except Exception:
                    pass
            if len(self.transfer_clients) >= target:
                return
            try:
                base = self.client.session
                dc_id, address, port, auth_key = int(base.dc_id), base.server_address, int(base.port), base.auth_key
                api_id = getattr(self.client, "api_id", None) or getattr(self.client, "_api_id", None)
                api_hash = getattr(self.client, "api_hash", None) or getattr(self.client, "_api_hash", None)
                if not api_id or not api_hash or auth_key is None:
                    _stream_log("TRANSFER POOL unavailable: missing API/session information")
                    return

                async def make_one():
                    mem = MemorySession()
                    mem.set_dc(dc_id, address, port)
                    mem.auth_key = auth_key
                    tc = make_telegram_client(mem, int(api_id), str(api_hash),
                                        governor=self.governor, playback_priority=True,
                                        receive_updates=False, request_retries=REQUEST_RETRIES,
                                        flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD_SECONDS, connection_retries=10, auto_reconnect=True)
                    await asyncio.wait_for(tc.connect(), 20)
                    if not await tc.is_user_authorized():
                        await tc.disconnect()
                        raise RuntimeError("transfer session not authorized")
                    return tc

                need = target - len(self.transfer_clients)
                results = await asyncio.gather(*(make_one() for _ in range(need)), return_exceptions=True)
                for r in results:
                    if isinstance(r, BaseException):
                        _stream_log(f"TRANSFER SESSION failed: {type(r).__name__}: {r}")
                        continue
                    self.transfer_clients.append(r)
                    self.connection_stats[id(r)] = {"client": r, "inflight": 0, "ewma_bps": 0.0,
                                                    "failures": 0, "cooldown_until": 0.0}
                _stream_log(f"TRANSFER POOL {len(self.transfer_clients)} connection(s) dc={dc_id}")
            except Exception as e:
                _stream_log(f"TRANSFER POOL init failed: {type(e).__name__}: {e}")
        self.scheduler.wake()

    def _choose_client(self, avoid=None, vkey=None):
        if vkey is not None:
            sticky = self._sticky_clients.get((int(vkey[0]), int(vkey[1])))
            if sticky is not None and sticky is not avoid:
                st = self.connection_stats.get(id(sticky))
                if st is not None and st["cooldown_until"] <= time.monotonic():
                    return sticky
        stats = [self.connection_stats[id(tc)] for tc in self.transfer_clients if id(tc) in self.connection_stats]
        if not stats:
            return self.client
        now = time.monotonic()
        ready = [s for s in stats if s["cooldown_until"] <= now and s["client"] is not avoid] or stats
        best = min(ready, key=lambda s: (s["inflight"], -s["ewma_bps"], s["failures"]))
        if vkey is not None:
            self._sticky_clients[(int(vkey[0]), int(vkey[1]))] = best["client"]
        return best["client"]

    def _client_sample(self, client, amount, elapsed, failed):
        st = self.connection_stats.get(id(client))
        if not st:
            return
        if not failed and amount > 0:
            bps = amount / max(elapsed, 0.001)
            st["ewma_bps"] = bps if not st["ewma_bps"] else 0.75 * st["ewma_bps"] + 0.25 * bps
            st["failures"] = max(0, st["failures"] - 1)
            st["cooldown_until"] = 0.0
        elif failed:
            st["failures"] = min(8, st["failures"] + 1)
            st["cooldown_until"] = time.monotonic() + min(5.0, 0.25 * (2 ** (st["failures"] - 1)))

    def _current_media_bps(self) -> float:
        if self.current_key:
            msg = self.messages.get(self.current_key)
            if msg is not None:
                return self._media_bps(msg)
        return 0.0

    def _contiguous_buffer_seconds(self, vkey=None) -> float:
        """Return the locally cached playback window after the current playhead.

        The previous controller was always fed ``0`` seconds, so it could never
        tell the difference between a healthy local buffer and a starving one.
        That made readahead decisions reactive instead of predictive.
        """
        vkey = vkey or self.current_key
        if not vkey:
            return 0.0
        message = self.messages.get(vkey)
        if message is None:
            return 0.0
        start = max(0, int(self.last_playhead.get(vkey, 0)))
        cache = self.disk_caches.get(vkey)
        if cache is None:
            return 0.0
        total = cache.total_segments
        contiguous = 0
        while start + contiguous < total and cache.has(start + contiguous):
            contiguous += 1
            if contiguous >= PLAYBACK_WINDOW_MAX_SEGMENTS * 4:
                break
        size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        duration = float(getattr(getattr(message, "file", None), "duration", 0) or 0.0)
        if size <= 0 or duration <= 0:
            return 0.0
        return min(duration, (contiguous * SEGMENT / size) * duration)

    async def _adapt_after_transfer(self, amount, elapsed, *, concurrency=None):
        """Update throughput estimates and concurrency after a transfer."""
        if amount <= 0 or elapsed <= 0:
            return
        speed = amount / elapsed
        expand = False
        async with self._adapt_lock:
            self._adapt_samples.append(speed)
            self._adapt_samples = self._adapt_samples[-16:]
            robust = self._rate_estimator.add(speed)
            if len(self._rate_estimator) >= 3:
                buffered_seconds = self._contiguous_buffer_seconds(self.current_key)
                seek_buffering = bool(self.current_key and self._seek_buffer_target_key == self.current_key)
                decision = self.buffer_controller.observe(
                    bytes_downloaded=int(amount), elapsed=float(elapsed),
                    media_bps=max(1.0, self._current_media_bps()),
                    current_buffer_seconds=buffered_seconds,
                    seeking=seek_buffering,
                    active_requests=self.active_http_requests,
                )
                self.qos.observe_buffer(buffered_seconds, decision.target_seconds)
                if seek_buffering and buffered_seconds >= self.buffer_controller.seek_target:
                    self._seek_buffer_target_key = None
                    self._seek_buffer_target_segment = None
            if len(self._rate_estimator) < 6:
                return
            current_level = max(self.min_fetch_concurrency, int(concurrency or self.fetch_concurrency))
            old, self._adapt_last_speed = self._adapt_last_speed, robust
            if self._adapt_best_speed <= 0:
                self._adapt_best_speed = robust
                self._adapt_best_concurrency = current_level
            if old <= 0:
                return
            now = time.monotonic()
            if now - self._last_concurrency_change < 6.0:
                return
            gain = (robust - old) / max(old, 1.0)
            # Keep the best observed speed/concurrency pair. Never silently
            # expand the allowed ceiling just because a throttle cooldown ended.
            if gain > 0.08 and robust > self._adapt_best_speed:
                self._adapt_best_speed = robust
                self._adapt_best_concurrency = current_level
            ceiling = max(self.min_fetch_concurrency, int(self.adaptive_max_fetch_concurrency))
            if gain < -0.08:
                if self.fetch_concurrency > self._adapt_best_concurrency:
                    next_level = max(self.min_fetch_concurrency, self._adapt_best_concurrency)
                else:
                    next_level = max(self.min_fetch_concurrency, self.fetch_concurrency - 1)
                if next_level != self.fetch_concurrency:
                    old_level = self.fetch_concurrency
                    self.fetch_concurrency = next_level
                    self.adaptive_max_fetch_concurrency = max(
                        self.min_fetch_concurrency,
                        min(self.adaptive_max_fetch_concurrency, max(self._adapt_best_concurrency, next_level)),
                    )
                    self._last_concurrency_change = now
                    _stream_log(
                        f"ADAPT throughput fell ({_human_bytes(robust)}/s); concurrency "
                        f"{old_level}->{self.fetch_concurrency}; best={self._adapt_best_concurrency}/"
                        f"{_human_bytes(self._adapt_best_speed)}/s"
                    )
            elif gain > 0.08 and self.fetch_concurrency < ceiling and current_level >= self.fetch_concurrency:
                old_level = self.fetch_concurrency
                self.fetch_concurrency = min(ceiling, self.fetch_concurrency + 1)
                self._last_concurrency_change = now
                expand = True
                _stream_log(
                    f"ADAPT concurrency -> {self.fetch_concurrency}/{ceiling} "
                    f"(throughput improved to {_human_bytes(robust)}/s; best="
                    f"{self._adapt_best_concurrency}/{_human_bytes(self._adapt_best_speed)}/s)"
                )
        if expand and self.fetch_concurrency > len(self.transfer_clients):
            self._spawn(self._ensure_transfer_sessions(self.fetch_concurrency), "transfer-pool-expand")
        self.scheduler.wake()

    @staticmethod
    def _wait_seconds_from_exception(exc) -> int:
        """Extract a Telegram wait value from the exception object or text."""
        value = getattr(exc, "seconds", None)
        try:
            if value is not None:
                return max(0, int(value))
        except Exception:
            pass
        text = f"{type(exc).__name__} {exc}".upper()
        m = re.search(r"FLOOD(?:_PREMIUM)?_WAIT[_ ](\d+)", text)
        if m:
            try:
                return max(0, int(m.group(1)))
            except Exception:
                pass
        return 0

    def _note_throttle(self, exc):
        """Reduce concurrency after a throttling error and tell the governor."""
        now = time.monotonic()
        old = self.fetch_concurrency
        text = (type(exc).__name__ + " " + str(exc)).upper()
        premium_wait = "FLOODPREMIUMWAIT" in text or "FLOOD_PREMIUM_WAIT" in text
        if premium_wait:
            self.flood_premium_waits += 1
            wait = self._wait_seconds_from_exception(exc)
            # Keep the cooldown short in the interactive controller while the
            # retry path uses the real server wait value when it is available.
            self._flood_premium_wait_until = max(
                self._flood_premium_wait_until,
                now + min(120, max(10, wait + 10)),
            )
            # Reduce pressure, but never collapse a Free account to a single
            # worker. The previous 1-worker floor increased first-buffer time
            # immediately after the capability profile was introduced.
            self.adaptive_max_fetch_concurrency = max(
                self.min_fetch_concurrency,
                min(self.adaptive_max_fetch_concurrency, max(self.min_fetch_concurrency, old - 1)),
            )
            step = 2 if self.premium else 1
            self.fetch_concurrency = max(self.min_fetch_concurrency, old - step)
            self.adaptive_max_fetch_concurrency = max(self.min_fetch_concurrency, min(self.adaptive_max_fetch_concurrency, self.fetch_concurrency))
            level = "PREMIUM" if self.premium else "FREE"
            _stream_log(
                f"FLOOD PREMIUM WAIT profile={level}: concurrency {old}->{self.fetch_concurrency}; "
                f"adaptive_max={self.adaptive_max_fetch_concurrency}; wait={wait}s; "
                f"resume_after={self._flood_premium_wait_until:.1f}"
            )
        else:
            self.fetch_concurrency = max(self.min_fetch_concurrency, old - (2 if self.premium else 1))
            self.adaptive_max_fetch_concurrency = max(self.min_fetch_concurrency, min(self.adaptive_max_fetch_concurrency, self.fetch_concurrency))
            _stream_log(f"THROTTLE {type(exc).__name__}: concurrency {old}->{self.fetch_concurrency}")
        self._last_concurrency_change = now
        if self.governor is not None:
            try:
                wait = self._wait_seconds_from_exception(exc)
                if wait:
                    self.governor.note_floodwait(wait, kind="playback", request="GetFile")
                else:
                    self.governor.note_range_failure()
            except Exception:
                pass
        self.scheduler.wake()


    # ------------------------------------------------------------------
    # SUBSECTION: Telegram entity and message resolution
    # Entities and playback messages.
    # ------------------------------------------------------------------
    async def _entity(self, cid):
        cid = int(cid)
        ent = self.entities.get(cid)
        if ent is None:
            ent = await self.client.get_entity(cid)
            self.entities[cid] = ent
        return ent

    def _saved_location_message(self, cid, mid):
        """Build a range-reader proxy from scan-persisted Telegram file location.

        This avoids get_messages on first Play. The original Telegram message is
        fetched only when the saved file_reference is rejected as expired.
        """
        try:
            from ..core.playback_index_cache import read_manifest, read_telegram_location
            cached_location = read_telegram_location(int(cid), int(mid), None)
            record = read_manifest(int(cid), int(mid), None)
            location = ((cached_location or {}).get("telegram_location")
                        or (record or {}).get("telegram_location") or {})
            size = int((cached_location or {}).get("source_size") or (record or {}).get("source_size") or 0)
            saved_at = None
            if location:
                try:
                    from ..core.playback_index_cache import telegram_location_path
                    saved_path = telegram_location_path(int(cid), int(mid), size)
                    saved_at = saved_path.stat().st_mtime if saved_path.is_file() else None
                except (OSError, ValueError, TypeError):
                    saved_at = None
            reference = base64.b64decode(location.get("file_reference_b64") or "", validate=True)
            document_id = int(location.get("document_id") or 0)
            access_hash = int(location.get("access_hash") or 0)
            dc_id = int(location.get("dc_id") or 0)
            if not (reference and size > 0 and document_id > 0 and access_hash):
                return None
            document = types.Document(
                id=document_id, access_hash=access_hash, file_reference=reference,
                date=int(time.time()),
                mime_type=str(location.get("mime_type") or "video/mp4"),
                size=size, dc_id=dc_id, attributes=[], thumbs=None, video_thumbs=None,
            )
            file_meta = SimpleNamespace(
                size=size, name=str(location.get("name") or f"Telegram video {int(mid)}"),
                mime_type=str(location.get("mime_type") or "video/mp4"),
                duration=float(location.get("duration") or (record or {}).get("duration") or 0),
            )
            # iter_download accepts a Document directly; raw GetFile uses the
            # document's immutable ID/access-hash and the stored file reference.
            return SimpleNamespace(id=int(mid), document=document, media=document,
                                   file=file_meta, date=None, _archive_x_reference_saved_at=saved_at,
                                   _archive_x_saved_location=True, _archive_x_channel_id=int(cid))
        except Exception as exc:
            _stream_log(f"SAVED LOCATION proxy error channel={int(cid)} message={int(mid)} error={type(exc).__name__}: {exc}")
            return None

    async def get_message(self, cid, mid, refresh=False):
        vkey = (int(cid), int(mid))
        if not refresh and vkey in self.messages:
            self.messages.move_to_end(vkey)
            return self.messages[vkey]
        requested_at = time.monotonic()
        lock = self.message_locks.setdefault(vkey, asyncio.Lock())
        async with lock:
            if not refresh and vkey in self.messages:
                return self.messages[vkey]
            if (refresh and vkey in self.messages
                    and self.message_refreshed_at.get(vkey, 0.0) >= requested_at):
                # Another request refreshed this message while we waited for the lock.
                # Concurrent Play requests (prebuffer ranges, info, previews) all see the
                # saved-location proxy at the same moment; refetching once per request made
                # them queue up for tens of seconds.
                self.messages.move_to_end(vkey)
                return self.messages[vkey]
            if not refresh:
                saved = await asyncio.to_thread(self._saved_location_message, *vkey)
                if saved is not None:
                    self.messages[vkey] = saved
                    self.messages.move_to_end(vkey)
                    while len(self.messages) > MESSAGE_CACHE_LIMIT:
                        old_key, _ = self.messages.popitem(last=False)
                        self.message_locks.pop(old_key, None)
                    return saved
            entity = await self._entity(vkey[0])
            m = await self.client.get_messages(entity, ids=vkey[1])
            f = getattr(m, "file", None) if m else None
            if not m or not f or not getattr(f, "size", 0):
                raise FileNotFoundError(f"video message {vkey[1]} not found in channel {vkey[0]}")
            self.messages[vkey] = m
            self.messages.move_to_end(vkey)
            self.message_refreshed_at[vkey] = time.monotonic()
            # A refreshed message supplies a current file_reference. Keep the
            # persisted startup cache in sync for the next app launch as well.
            try:
                await asyncio.to_thread(_persist_message_location, vkey[0], vkey[1], m)
            except Exception as exc:
                _stream_log(f"MESSAGE LOCATION persist failed channel={vkey[0]} message={vkey[1]} error={type(exc).__name__}: {exc}")
            while len(self.messages) > MESSAGE_CACHE_LIMIT:
                old_key, _ = self.messages.popitem(last=False)
                self.message_locks.pop(old_key, None)
            return m

    async def get_playback_message(self, cid, mid):
        """Resolve a durable scan-time location to a live Telegram Message before streaming.

        The scan-time proxy is useful for cold metadata lookups, but it is not a
        full Telethon Message. Its file_reference may be stale and its media
        wrapper differs from uploaded/in-memory messages. On the first actual
        playback request after startup, fetch the message once and then use the
        same live-message fetch path for scanned and uploaded videos.
        """
        message = await self.get_message(cid, mid)
        if getattr(message, "_archive_x_saved_location", False):
            document = getattr(message, "document", None)
            _stream_log(
                f"PLAY resolve saved-location proxy channel={int(cid)} message={int(mid)} "
                f"document_id={int(getattr(document, 'id', 0) or 0)} "
                f"dc={int(getattr(document, 'dc_id', 0) or 0)} action=refresh_live_message"
            )
            # This is intentionally a single cheap get_messages on Play, not
            # a scan or media download. It supplies a fresh Telegram reference
            # and the same Message.media representation used for uploaded files.
            message = await self.get_message(cid, mid, refresh=True)
        document = getattr(message, "document", None)
        file_meta = getattr(message, "file", None)
        if document is None or not int(getattr(file_meta, "size", 0) or 0):
            raise FileNotFoundError(
                f"Telegram playback message {int(mid)} in channel {int(cid)} has no video document"
            )
        _stream_log(
            f"PLAY message ready channel={int(cid)} message={int(mid)} "
            f"document_id={int(getattr(document, 'id', 0) or 0)} "
            f"dc={int(getattr(document, 'dc_id', 0) or 0)} "
            f"saved_proxy={bool(getattr(message, '_archive_x_saved_location', False))}"
        )
        return message


    # ------------------------------------------------------------------
    # SUBSECTION: Sparse disk and memory segment cache
    # Disk cache access, eviction and RAM cache.
    # ------------------------------------------------------------------
    def get_disk_cache(self, vkey, message):
        cache = self.disk_caches.get(vkey)
        if cache is None:
            cache = SparseVideoCache(vkey, message)
            self.disk_caches[vkey] = cache
            _dbg(f"DISK CACHE created {vkey} size={cache.size}")
            self._evict_disk_caches()
        else:
            self.disk_caches.move_to_end(vkey)
        return cache

    def _protected_videos(self):
        keep = {self.current_key} | set(self.preload_tasks) | set(self.next_prefix_tasks)
        keep |= set(self._upcoming(self.current_key, 1))
        return {k for k in keep if k}

    def _evict_disk_caches(self):
        keep = self._protected_videos()
        while True:
            total = sum(c.cached_bytes for c in self.disk_caches.values())
            if len(self.disk_caches) <= MAX_CACHED_VIDEOS and total <= DISK_CACHE_BUDGET:
                break
            victim = next((k for k, c in self.disk_caches.items() if k not in keep and not c.busy()), None)
            if victim is None:
                break
            cache = self.disk_caches.pop(victim)
            cache.close()
            for seg_key in [k for k in self.segments if (k[0], k[1]) == victim]:
                self.segments.pop(seg_key, None)
            _dbg(f"DISK CACHE evicted {victim}")
        result = prune_range_cache_to_budget(
            budget_bytes=DISK_CACHE_BUDGET,
            keep_keys=keep,
        )
        if result.get("removed"):
            _stream_log(
                f"RANGE CACHE budget evicted={result['removed']} "
                f"bytes={result['bytes_removed']} remaining={result['bytes_after']} "
                f"limit={DISK_CACHE_BUDGET}"
            )

    async def _evict_disk_caches_async(self):
        """Evict sparse temp files without running filesystem cleanup on-loop."""
        keep = self._protected_videos()
        while True:
            total = sum(c.cached_bytes for c in self.disk_caches.values())
            if len(self.disk_caches) <= MAX_CACHED_VIDEOS and total <= DISK_CACHE_BUDGET:
                return
            victim = next((k for k, c in self.disk_caches.items() if k not in keep and not c.busy()), None)
            if victim is None:
                return
            cache = self.disk_caches.pop(victim)
            for seg_key in [k for k in self.segments if (k[0], k[1]) == victim]:
                self.segments.pop(seg_key, None)
            await asyncio.to_thread(cache.close)
            _dbg(f"DISK CACHE evicted {victim}")

    async def get_disk_cache_async(self, vkey, message):
        """Create/open the active sparse file on a worker thread (one per video)."""
        vkey = (int(vkey[0]), int(vkey[1]))
        lock = self.disk_cache_init_locks.setdefault(vkey, asyncio.Lock())
        async with lock:
            cache = self.disk_caches.get(vkey)
            if cache is not None:
                self.disk_caches.move_to_end(vkey)
                return cache
            # Shield the constructor thread from request cancellation. If the
            # HTTP task leaves while the sparse file is being created, wait for
            # the constructor result and clean it up rather than orphaning an
            # open cache file that is no longer registered in disk_caches.
            create_task = asyncio.create_task(asyncio.to_thread(SparseVideoCache, vkey, message))
            try:
                cache = await asyncio.shield(create_task)
            except asyncio.CancelledError:
                try:
                    orphan_cache = await create_task
                    await asyncio.to_thread(orphan_cache.close)
                except Exception as cleanup_exc:
                    _stream_log(f"DISK CACHE cancelled creation cleanup failed {vkey}: {type(cleanup_exc).__name__}: {cleanup_exc}")
                raise
            self.disk_caches[vkey] = cache
            _dbg(f"DISK CACHE created {vkey} size={cache.size}")
            await self._evict_disk_caches_async()
            return cache

    def _ram_get(self, skey):
        data = self.segments.get(skey)
        if data is not None:
            self.segments.move_to_end(skey)
        return data

    def _ram_put(self, skey, data):
        if skey in self.segments:
            self.segments.pop(skey, None)
        self.segments[skey] = data
        self.segments.move_to_end(skey)
        # Bound actual bytes, not only the number of 512 KiB entries. The last
        # segment of a file may be short, and cache/index reads can overlap.
        while self.segments and (
            len(self.segments) > RAM_CACHE_SEGMENTS
            or sum(len(value) for value in self.segments.values()) > RAM_CACHE_BYTES
        ):
            self.segments.popitem(last=False)


    # ------------------------------------------------------------------
    # SUBSECTION: Aligned range fetch and segment storage
    # Telegram fetches, split and preview paths, storing segments.
    # ------------------------------------------------------------------
    async def _raw_get_file(self, tc, message, offset, amount):
        """Read aligned upload.getFile blocks using the shared range transport.

        The callback preserves the existing client's authenticated exported-DC
        sender pool and one-time FILE_MIGRATE handling. CDN/no-byte responses use
        the single bounded iter_download fallback implemented by the shared helper.
        """
        document = getattr(message, "document", None)
        if document is None:
            return None
        size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        offset, amount = max(0, int(offset)), max(0, int(amount))
        if size > 0:
            amount = min(amount, max(0, size - offset))
        if amount <= 0:
            return b""
        message_id = int(getattr(message, "id", 0) or 0)
        base_dc = int(getattr(getattr(tc, "session", None), "dc_id", 0) or 0)
        target_dc_initial = int(getattr(document, "dc_id", 0) or base_dc or 0) or base_dc
        reference_age = _message_reference_age_seconds(message)

        async def request_call(request, pos, limit):
            """Make one getFile call on the right DC, following migrations."""
            nonlocal target_dc_initial
            target_dc = int(getattr(document, "dc_id", 0) or target_dc_initial or base_dc or 0)
            route_retried = False
            while True:
                self.rpc_requests += 1
                _stream_log(
                    f"GETFILE scheduled channel={int(getattr(message, '_archive_x_channel_id', 0) or 0)} "
                    f"message={message_id} offset={int(pos)} limit={int(limit)} dc={target_dc or 'unknown'} "
                    f"base_dc={base_dc or 'unknown'} reference_age_s={reference_age if reference_age is not None else 'live/unknown'}"
                )
                try:
                    if target_dc and base_dc and target_dc != base_dc:
                        borrower = getattr(self.client, "_borrow_exported_sender", None)
                        returner = getattr(self.client, "_return_exported_sender", None)
                        caller = getattr(self.client, "_call", None)
                        if not (callable(borrower) and callable(returner) and callable(caller)):
                            raise RuntimeError(
                                f"Telegram file belongs to DC {target_dc}, session DC is {base_dc}, "
                                "and the authenticated client has no exported-sender pool"
                            )
                        sender = await borrower(target_dc)
                        self._dc_request_counts[target_dc] = int(self._dc_request_counts.get(target_dc, 0)) + 1
                        _stream_log(
                            f"GETFILE request sent message={message_id} offset={int(pos)} limit={int(limit)} "
                            f"dc={target_dc} route=exported_dc_pool pool_requests={self._dc_request_counts[target_dc]}"
                        )
                        try:
                            result = await caller(sender, request)
                        finally:
                            await returner(sender)
                    else:
                        _stream_log(
                            f"GETFILE request sent message={message_id} offset={int(pos)} limit={int(limit)} "
                            f"dc={target_dc or base_dc or 'unknown'} route=foreground_transfer_pool"
                        )
                        result = await tc(request)
                    raw = getattr(result, "bytes", None)
                    if raw:
                        _stream_log(
                            f"GETFILE first bytes received message={message_id} offset={int(pos)} "
                            f"dc={target_dc or base_dc or 'unknown'} bytes={len(raw)}"
                        )
                    if raw is None:
                        if type(result).__name__.lower().endswith("filecdnredirect"):
                            self.cdn_redirects += 1
                            _stream_log(f"GETFILE CDN redirect message={message_id} offset={int(pos)} dc={target_dc}")
                        else:
                            _stream_log(
                                f"GETFILE returned no bytes message={message_id} offset={int(pos)} "
                                f"dc={target_dc} result={type(result).__name__}"
                            )
                    else:
                        _stream_log(
                            f"GETFILE completed message={message_id} offset={int(pos)} dc={target_dc} "
                            f"requested={int(limit)} received={len(raw)}"
                        )
                    return result
                except asyncio.CancelledError:
                    _stream_log(
                        f"GETFILE cancelled message={message_id} offset={int(pos)} limit={int(limit)} "
                        f"dc={target_dc} reference_age_s={reference_age if reference_age is not None else 'live/unknown'}"
                    )
                    raise
                except Exception as exc:
                    migrated_dc = _file_migrate_dc(exc)
                    if migrated_dc and not route_retried:
                        old_dc = target_dc
                        target_dc = int(migrated_dc)
                        target_dc_initial = target_dc
                        route_retried = True
                        try:
                            document.dc_id = target_dc
                            channel_value = int(getattr(message, "_archive_x_channel_id", 0) or 0)
                            if channel_value and message_id:
                                await asyncio.to_thread(_persist_message_location, channel_value, message_id, message)
                        except Exception as persist_exc:
                            _stream_log(
                                f"GETFILE DC location persistence failed message={message_id} dc={target_dc} "
                                f"error_name={type(persist_exc).__name__} error={persist_exc}"
                            )
                        _stream_log(
                            f"GETFILE DC migrate message={message_id} offset={int(pos)} from_dc={old_dc} "
                            f"to_dc={target_dc} error={type(exc).__name__}: {exc}"
                        )
                        continue
                    _stream_log(
                        f"GETFILE error message={message_id} offset={int(pos)} limit={int(limit)} dc={target_dc} "
                        f"error_name={type(exc).__name__} error={exc} "
                        f"reference_age_s={reference_age if reference_age is not None else 'live/unknown'}"
                    )
                    raise

        async def refresh_message_reference():
            nonlocal message, document
            try:
                channel_value = int(getattr(message, "_archive_x_channel_id", 0) or 0)
                entity = self.channels.get(channel_value) or getattr(message, "chat_id", None) or getattr(message, "peer_id", None)
                fresh = await tc.get_messages(entity, ids=message_id)
                if fresh:
                    try:
                        setattr(fresh, "_archive_x_channel_id", int(getattr(message, "_archive_x_channel_id", 0) or 0))
                    except Exception:
                        pass
                    message = fresh
                    document = getattr(message, "document", None)
                    self.messages[(int(getattr(message, "_archive_x_channel_id", 0) or 0), message_id)] = fresh
                    try:
                        channel_value = int(getattr(message, "_archive_x_channel_id", 0) or 0)
                        if channel_value:
                            await asyncio.to_thread(_persist_message_location, channel_value, message_id, fresh)
                    except Exception:
                        pass
                    return fresh
            except Exception:
                pass
            return None

        data = await fetch_aligned_range(
            tc, message, offset, amount, request_call=request_call, fallback=True,
            telethon_functions=functions, telethon_types=types,
            priority="playback", cache_policy="coalesce",
            refresh_message=refresh_message_reference,
        )
        _stream_log(
            f"GETFILE assembled message={message_id} offset={offset} amount={amount} received={len(data)} "
            f"dc={int(getattr(document, 'dc_id', 0) or base_dc or 0)}"
        )
        return data

    async def _fetch_split(self, tc, message, seg):
        """Fetch one segment as parallel sub-requests; None if not applicable."""
        size = int(message.file.size)
        offset = int(seg) * SEGMENT
        amount = min(SEGMENT, size - offset)
        document = getattr(message, "document", None)
        doc_dc = getattr(document, "dc_id", None)
        home_dc = getattr(getattr(tc, "session", None), "dc_id", None)
        if (amount <= PREVIEW_SPLIT_MIN_PART or document is None or doc_dc in self.raw_bad_dcs
                or not (doc_dc is None or home_dc is None or doc_dc == home_dc)):
            return None
        connections = max(1, len(self.transfer_clients))
        # Kept as a compatibility helper for older interactive callers. The
        # canonical segment is already 512 KiB, so this path now resolves to a
        # single Telegram range instead of splitting the segment.
        parts = 1
        part = min(INTERACTIVE_FETCH_CHUNK, (SEGMENT + parts - 1) // parts)
        pieces = [(offset + i * part, min(part, amount - i * part)) for i in range(parts) if amount - i * part > 0]
        clients = self._split_clients(len(pieces), tc)

        async def one(client, off, n):
            st = self.connection_stats.get(id(client))
            if st and client is not tc:
                st["inflight"] += 1
            started = time.monotonic()
            try:
                data = await self._raw_get_file(client, message, off, n)
                if data is not None and len(data) == n:
                    self._client_sample(client, n, time.monotonic() - started, False)
                return data
            finally:
                if st and client is not tc:
                    st["inflight"] = max(0, st["inflight"] - 1)

        results = await asyncio.gather(*(one(c, o, n) for c, (o, n) in zip(clients, pieces)))
        if any(r is None or len(r) != n for r, (_o, n) in zip(results, pieces)):
            return None
        return b"".join(results)

    async def _fetch_preview(self, tc, message, seg):
        """Interactive seek-preview fetch: split in parallel, else the normal path."""
        try:
            data = await self._fetch_split(tc, message, seg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = (type(exc).__name__ + " " + str(exc)).upper()
            if "FILE_REFERENCE" in text or "FILEREFERENCE" in text or "FLOOD" in text:
                raise
            _stream_log(f"PREVIEW split fetch failed msg={message.id} seg={seg}: {type(exc).__name__}: {exc}")
            data = None
        if data is not None:
            self.metrics["preview_split_fetches"] = self.metrics.get("preview_split_fetches", 0) + 1
            return data
        return await self._fetch_once(tc, message, seg)

    async def _fetch_once(self, tc, message, seg):
        """Fetch one 512 KiB playback segment through the single shared path."""
        size = int(message.file.size)
        offset = int(seg) * SEGMENT
        amount = min(SEGMENT, size - offset)
        if amount <= 0:
            return b""
        document = getattr(message, "document", None)
        doc_dc = int(getattr(document, "dc_id", 0) or 0) if document is not None else 0
        if document is not None and doc_dc not in self.raw_bad_dcs:
            data = await self._raw_get_file(tc, message, offset, amount)
        else:
            # The common helper still bounds fallback to a single iterator chunk.
            data = await fetch_aligned_range(
                tc, message, offset, amount, fallback=True, allow_direct=False,
                telethon_functions=functions, telethon_types=types,
                priority="playback", cache_policy="coalesce",
            )
        if len(data) != amount:
            raise IOError(f"short read {len(data)}/{amount} bytes")
        return data

    async def _download_segment(self, entry):
        """Worker body: download one segment with retries, then store it."""
        cid, mid, seg = entry["key"]
        vkey = (cid, mid)
        message = self.messages.get(vkey) or entry["message"]
        last_exc = None
        avoid = None
        kind = str(entry.get("kind") or "playback")
        priority = int(entry.get("priority", 1))
        # A hover/preview request can be promoted to P0/P1 by the QoS controller
        # when it targets the current playback position. Once promoted, it is no
        # longer allowed to use the slower preview-fetch path: the bytes are now
        # on the critical playback path and must use the canonical foreground
        # Telegram range request. This distinction is important because the
        # previous implementation could leave the exact seek target labelled
        # ``preview`` while it occupied a playback worker for 6+ seconds.
        promoted_preview = kind == "preview" and priority <= 1 and self.active_http_requests > 0
        foreground = (kind in {"playback", "seek"} or promoted_preview) and priority <= 1
        # V29.4.118: playhead read-ahead is foreground media on P1.
        foreground = foreground or (kind == PLAYHEAD_READAHEAD_KIND and priority <= 1)
        preview = kind == "preview" and not promoted_preview
        # A preview admitted by the scheduler during healthy playback holds a
        # spare foreground worker; it must use a playback range lane because
        # the background lane is closed while the browser is streaming.
        playback_lane = foreground or kind == "preview" or bool(entry.get("playback_lane"))
        if promoted_preview:
            kind = "playback"
            entry["kind"] = "playback"
        attempts = (
            int(getattr(config, "PLAYBACK_PREVIEW_RANGE_ATTEMPTS", 1)) if preview else
            int(getattr(config, "PLAYBACK_FOREGROUND_RANGE_ATTEMPTS", 2)) if foreground else
            SEGMENT_ATTEMPTS
        )
        timeout_seconds = (
            float(getattr(config, "PLAYBACK_PREVIEW_RANGE_TIMEOUT_SECONDS", 6.0)) if preview else
            float(getattr(config, "PLAYBACK_FOREGROUND_RANGE_TIMEOUT_SECONDS", 8.0)) if foreground else
            SEGMENT_TIMEOUT_SECONDS
        )
        attempt = 0
        attempt_limit = max(1, attempts)
        file_reference_refreshes = 0
        timeout_refreshes = 0
        dc_auth_refreshes = 0
        while attempt < attempt_limit:
            tc = self._choose_client(avoid, vkey=vkey)
            st = self.connection_stats.get(id(tc))
            if st:
                st["inflight"] += 1
            started = time.monotonic()
            concurrency_at_start = int(self.fetch_concurrency)
            try:
                fetch = self._fetch_preview if kind in INTERACTIVE_KINDS else self._fetch_once
                # Browser playback is the highest-priority Telegram workload.
                # Route each foreground byte range through the shared governor
                # so metadata/transfer traffic cannot create a competing range
                # burst, while playback itself remains immediately eligible.
                governor_wait_started = time.monotonic()
                if self.governor is not None:
                    if playback_lane:
                        try:
                            self.governor.set_playback_emergency(
                                self.qos.context.state in {"STARVING", "STALLED"}
                            )
                        except Exception as exc:
                            _stream_log(f"GOVERNOR playback-emergency update failed message={mid if 'mid' in locals() else getattr(message, 'id', 0)} error={type(exc).__name__}: {exc}")
                        media_file = getattr(message, "file", None)
                        media_size = int(getattr(media_file, "size", 0) or 0)
                        async with self.governor.telegram_playback_range_slot(
                            deadline_seconds=getattr(config, "TRANSFER_GOVERNOR_PLAYBACK_DEADLINE_SECONDS", 3.0),
                            file_size=media_size,
                            request_bytes=SEGMENT,
                        ):
                            governor_wait_ms = (time.monotonic() - governor_wait_started) * 1000.0
                            transfer_started = time.monotonic()
                            data = await asyncio.wait_for(fetch(tc, message, seg), timeout_seconds)
                            transfer_ms = (time.monotonic() - transfer_started) * 1000.0
                    else:
                        media_file = getattr(message, "file", None)
                        media_size = int(getattr(media_file, "size", 0) or 0)
                        async with self.governor.telegram_range_slot(
                            file_size=media_size,
                            request_bytes=SEGMENT,
                            allow_during_playback=kind in INTERACTIVE_KINDS,
                        ):
                            governor_wait_ms = (time.monotonic() - governor_wait_started) * 1000.0
                            transfer_started = time.monotonic()
                            data = await asyncio.wait_for(fetch(tc, message, seg), timeout_seconds)
                            transfer_ms = (time.monotonic() - transfer_started) * 1000.0
                else:
                    governor_wait_ms = 0.0
                    transfer_started = time.monotonic()
                    data = await asyncio.wait_for(fetch(tc, message, seg), timeout_seconds)
                    transfer_ms = (time.monotonic() - transfer_started) * 1000.0
                elapsed = time.monotonic() - started
                if foreground:
                    self.metrics["foreground_telegram_wait_ms"] = round(
                        0.75 * float(self.metrics.get("foreground_telegram_wait_ms", 0.0)) + 0.25 * governor_wait_ms, 1
                    )
                    self.metrics["foreground_telegram_transfer_ms"] = round(
                        0.75 * float(self.metrics.get("foreground_telegram_transfer_ms", 0.0)) + 0.25 * transfer_ms, 1
                    )
                    self.metrics["foreground_completed_ranges"] = int(self.metrics.get("foreground_completed_ranges", 0)) + 1
                    self.metrics["foreground_bytes_per_second"] = round(len(data or b"") / max(0.001, transfer_ms / 1000.0), 1)
                self._client_sample(tc, len(data), elapsed, False)
                # A seek-preview is an interactive side request. Do not let a
                # single preview's transfer speed change the global playback
                # AIMD ceiling or make the main video more/less aggressive.
                # Split (interactive) fetches are not comparable with single
                # canonical playback requests, so they never steer the AIMD controller.
                if kind not in INTERACTIVE_KINDS:
                    await self._adapt_after_transfer(len(data), elapsed, concurrency=concurrency_at_start)
                self.bytes_downloaded += len(data)
                await self._store_segment(vkey, message, seg, data, kind=kind, epoch=int(entry.get("epoch", 0) or 0))
                _stream_log(f"SEGMENT fetch completed channel={cid} message={mid} segment={seg} kind={kind} priority={priority} bytes={len(data or b'')} elapsed_ms={(time.monotonic() - started) * 1000.0:.1f}")
                return data
            except asyncio.CancelledError:
                _stream_log(f"SEGMENT fetch cancelled channel={cid} message={mid} segment={seg} kind={kind} priority={priority} attempt={attempt + 1}")
                raise
            except Exception as exc:
                _stream_log(f"SEGMENT fetch error channel={cid} message={mid} segment={seg} kind={kind} priority={priority} attempt={attempt + 1} error={type(exc).__name__}: {exc}")
                if getattr(exc, "_archive_x_download_flood_noted", False) or getattr(exc, "_archive_x_file_reference_retry_attempted", False):
                    last_exc = exc
                    break
                last_exc = exc
                self._client_sample(tc, 0, time.monotonic() - started, True)
                avoid = tc
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
                    document = getattr(message, "document", None)
                    document_dc = int(getattr(document, "dc_id", 0) or 0) if document is not None else 0
                    reference_age = _message_reference_age_seconds(message)
                    _stream_log(
                        f"RANGE request timeout channel={cid} message={mid} segment={seg} dc={document_dc} "
                        f"reference_age_s={reference_age if reference_age is not None else 'live/unknown'} "
                        f"elapsed_limit_s={timeout_seconds} error_name={type(exc).__name__}"
                    )
                    if timeout_refreshes:
                        _stream_log(f"RANGE request timeout after refreshed location; stopping channel={cid} message={mid} segment={seg} dc={document_dc}")
                        break
                    timeout_refreshes += 1
                    try:
                        message = await self.get_message(cid, mid, refresh=True)
                        entry["message"] = message
                        self.messages[vkey] = message
                        _stream_log(
                            f"RANGE timeout message refresh succeeded channel={cid} message={mid} segment={seg} "
                            f"dc={int(getattr(getattr(message, 'document', None), 'dc_id', 0) or 0)}"
                        )
                    except Exception as refresh_exc:
                        _stream_log(
                            f"RANGE timeout message refresh failed channel={cid} message={mid} segment={seg} "
                            f"error_name={type(refresh_exc).__name__} error={refresh_exc}"
                        )
                        last_exc = RuntimeError(
                            f"Telegram range timed out and message refresh failed: {type(refresh_exc).__name__}: {refresh_exc}"
                        )
                        break
                    if attempt + 1 >= attempt_limit:
                        attempt_limit = attempt + 2
                    attempt += 1
                    continue
                if _is_file_reference_error(exc):
                    # An expired scan-time reference has exactly one recovery:
                    # fetch the real message, persist its current location, then
                    # retry this exact segment once. A second rejection fails
                    # promptly with the Telegram error instead of looping.
                    reference_age = _message_reference_age_seconds(message)
                    _stream_log(
                        f"FILE_REFERENCE expired channel={cid} message={mid} segment={seg} "
                        f"dc={int(getattr(getattr(message, 'document', None), 'dc_id', 0) or 0)} "
                        f"reference_age_s={reference_age if reference_age is not None else 'live/unknown'} "
                        f"error_name={type(exc).__name__} error={exc}"
                    )
                    if file_reference_refreshes:
                        _stream_log(f"FILE_REFERENCE second rejection; stopping channel={cid} message={mid} segment={seg}")
                        break
                    file_reference_refreshes += 1
                    if attempt + 1 >= attempt_limit:
                        attempt_limit = attempt + 2
                    try:
                        message = await self.get_message(cid, mid, refresh=True)
                        entry["message"] = message
                        self.messages[vkey] = message
                        _stream_log(
                            f"FILE_REFERENCE refresh succeeded channel={cid} message={mid} segment={seg} "
                            f"dc={int(getattr(getattr(message, 'document', None), 'dc_id', 0) or 0)}"
                        )
                    except Exception as refresh_exc:
                        _stream_log(
                            f"FILE_REFERENCE refresh failed channel={cid} message={mid} segment={seg} "
                            f"error_name={type(refresh_exc).__name__} error={refresh_exc}; "
                            f"original={type(exc).__name__}: {exc}"
                        )
                        last_exc = RuntimeError(
                            f"Telegram file reference expired and message refresh failed: {type(refresh_exc).__name__}: {refresh_exc}"
                        )
                        break
                    attempt += 1
                    continue
                if _is_dc_auth_error(exc):
                    document = getattr(message, "document", None)
                    dc = int(getattr(document, "dc_id", 0) or 0) if document is not None else 0
                    if dc_auth_refreshes:
                        _stream_log(
                            f"DC AUTH second failure; stopping channel={cid} message={mid} segment={seg} "
                            f"dc={dc} error_name={type(exc).__name__} error={exc}"
                        )
                        break
                    dc_auth_refreshes += 1
                    _stream_log(
                        f"DC AUTH refresh message channel={cid} message={mid} segment={seg} dc={dc} "
                        f"error_name={type(exc).__name__} error={exc}"
                    )
                    try:
                        message = await self.get_message(cid, mid, refresh=True)
                        entry["message"] = message
                        self.messages[vkey] = message
                        new_dc = int(getattr(getattr(message, "document", None), "dc_id", 0) or 0)
                        _stream_log(
                            f"DC AUTH live-message refreshed channel={cid} message={mid} segment={seg} "
                            f"old_dc={dc} new_dc={new_dc}; retrying exact range once"
                        )
                    except Exception as refresh_exc:
                        _stream_log(
                            f"DC AUTH message refresh failed channel={cid} message={mid} segment={seg} dc={dc} "
                            f"error_name={type(refresh_exc).__name__} error={refresh_exc}"
                        )
                        last_exc = RuntimeError(
                            f"Telegram DC authorization failed and live-message refresh failed: "
                            f"{type(refresh_exc).__name__}: {refresh_exc}"
                        )
                        break
                    if attempt + 1 >= attempt_limit:
                        attempt_limit = attempt + 2
                    attempt += 1
                    continue
                decision = self.retry_policy.classify(exc, attempt + 1)
                if decision.category == "flood_wait":
                    self._note_throttle(exc)
                if decision.category in {"permanent"} or not decision.retry:
                    break
                if decision.category == "flood_wait" and decision.delay > 30:
                    break
                _stream_log(f"SEGMENT retry channel={cid} message={mid} segment={seg} attempt={attempt + 1} category={decision.category} delay={decision.delay:.2f}s error={type(exc).__name__}: {exc}")
                await asyncio.sleep(max(0.05, decision.delay))
                attempt += 1
            finally:
                if st:
                    st["inflight"] = max(0, st["inflight"] - 1)
        raise last_exc or IOError("segment download failed")

    def _recently_released(self, vkey) -> bool:
        stamp = self.released_videos.get((int(vkey[0]), int(vkey[1])))
        if stamp is None:
            return False
        if time.monotonic() - stamp > RELEASED_VIDEO_QUIET_SECONDS:
            self.released_videos.pop((int(vkey[0]), int(vkey[1])), None)
            return False
        return True

    async def _store_segment(self, vkey, message, seg, data, *, kind="playback", epoch=0):
        # V29.4.129: a piece that lands after the viewer left this video is
        # not kept (no RAM, no temp file re-created).
        if self._recently_released(vkey) and self.current_key != (int(vkey[0]), int(vkey[1])):
            return
        skey = (vkey[0], vkey[1], int(seg))
        self._ram_put(skey, data)
        cache = self.disk_caches.get(vkey)
        # Requested ranges and the next-video prefix are eligible for persistent
        # disk caching. Open handles are
        # bounded separately from the on-disk LRU, so this does not keep many
        # large files open or compete at foreground priority.
        if cache is None and self.running and bool(getattr(config, "PLAYBACK_PERSISTENT_RANGE_CACHE", True)):
            cache = await self.get_disk_cache_async(vkey, message)
        if cache is not None:
            try:
                await cache.write_segment(seg, data, kind=kind)
            except Exception as e:
                _stream_log(f"DISK CACHE write failed {vkey} seg={seg}: {type(e).__name__}: {e}")

    @property
    def segment_size(self): return SEGMENT

    def media_resource(self, vkey, message):
        key=(int(vkey[0]),int(vkey[1])); resources=getattr(self,"media_resources",None)
        if resources is None: resources=self.media_resources={}
        resource=resources.get(key)
        if resource is None: resource=resources[key]=ArchiveXMediaResource(self,key,message)
        else: resource.message=message
        return resource

    async def get_segment(self, vkey, message, seg, priority=1, epoch=0, kind="playback"):
        """Return a segment from RAM or disk, or schedule its download."""
        seg = int(seg)
        skey = (vkey[0], vkey[1], seg)
        self.metrics["range_requests"] = self.metrics.get("range_requests", 0) + 1
        data = self._ram_get(skey)
        if data is not None:
            self.metrics["cache_ram_hits"] += 1
            self.qos.cache_hits += 1
            self.metrics["range_cache_hits"] = self.metrics.get("range_cache_hits", 0) + 1
            return data
        # Index/start ranges saved during Metadata & Assets are immutable local
        # assets. Read them in a worker thread before opening a temporary sparse
        # file or queueing any Telegram transfer.
        _gs_t0 = time.monotonic()
        try:
            from ..core.playback_index_cache import load_segment
            data = await asyncio.to_thread(load_segment, int(vkey[0]), int(vkey[1]),
                                           int(message.file.size), int(seg), SEGMENT)
            if time.monotonic() - _gs_t0 > 1.0:
                _stream_log(f"GET_SEGMENT slow stage=index_cache_read t={time.monotonic() - _gs_t0:.1f}s message={int(vkey[1])} segment={seg} kind={kind}")
        except Exception as exc:
            _stream_log(f"INDEX CACHE read error channel={int(vkey[0])} message={int(vkey[1])} segment={int(seg)} error={type(exc).__name__}: {exc}")
            data = None
        if data is not None:
            self._ram_put(skey, data)
            # The MP4 index/startup cache is a source for the persistent video
            # byte cache, not a separate island. Promote its verified segment so
            # prefix warming and full-file fill do not repeatedly reread it from
            # the index cache or leave it out of the persistent cache manifest.
            if bool(getattr(config, "PLAYBACK_PERSISTENT_RANGE_CACHE", True)):
                try:
                    cached = self.disk_caches.get(vkey)
                    if cached is None:
                        cached = await self.get_disk_cache_async(vkey, message)
                    if cached is not None and not cached.has(seg):
                        await cached.write_segment(seg, data, kind=kind)
                except Exception as cache_exc:
                    _stream_log(f"INDEX CACHE promotion failed channel={int(vkey[0])} message={int(vkey[1])} segment={int(seg)} error={type(cache_exc).__name__}: {cache_exc}")
            self.metrics["cache_disk_hits"] += 1
            self.qos.cache_hits += 1
            self.metrics["range_cache_hits"] = self.metrics.get("range_cache_hits", 0) + 1
            return data
        cache = self.disk_caches.get(vkey)
        if cache is None and bool(getattr(config, "PLAYBACK_PERSISTENT_RANGE_CACHE", True)):
            _dc_t0 = time.monotonic()
            cache = await self.get_disk_cache_async(vkey, message)
            if time.monotonic() - _dc_t0 > 1.0:
                _stream_log(f"GET_SEGMENT slow stage=open_disk_cache t={time.monotonic() - _dc_t0:.1f}s message={int(vkey[1])} segment={seg} kind={kind}")
        if cache is not None and cache.has(seg):
            end = min(cache.size - 1, seg * SEGMENT + SEGMENT - 1)
            data = await cache.read_range(seg * SEGMENT, end)
            if data is not None:
                self.metrics["cache_disk_hits"] += 1
                self.qos.cache_hits += 1
                self.metrics["range_cache_hits"] = self.metrics.get("range_cache_hits", 0) + 1
                self._ram_put(skey, data)
                return data
            data = self._ram_get(skey)
            if data is not None:
                self.metrics["cache_ram_hits"] += 1
                return data
        self.metrics["cache_misses"] += 1
        self.qos.cache_misses += 1
        self.metrics["range_cache_misses"] = self.metrics.get("range_cache_misses", 0) + 1
        doc = getattr(message, "document", None)
        document_dc = int(getattr(doc, "dc_id", 0) or 0)
        reference_age = _message_reference_age_seconds(message)
        _stream_log(
            f"RANGE MISS scheduled channel={int(vkey[0])} message={int(vkey[1])} segment={seg} "
            f"requested_priority={int(priority)} kind={str(kind)} epoch={int(epoch)} dc={document_dc} "
            f"reference_age_s={reference_age if reference_age is not None else 'live/unknown'}"
        )
        # No await between the checks above and the pending lookup inside
        # submit(), so a download that just finished is never fetched twice.
        started = time.monotonic()
        try:
            data = await self.scheduler.submit(message, skey, seg, priority, epoch, kind=kind)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            _stream_log(f"RANGE MISS completed channel={int(vkey[0])} message={int(vkey[1])} segment={seg} requested_priority={int(priority)} kind={str(kind)} bytes={len(data or b'')} elapsed_ms={elapsed_ms:.1f}")
            # Persist only diagnostically useful range evidence: committed seeks,
            # slow playback ranges, and all failed ranges. Normal fast read-ahead
            # remains metrics-only so diagnostics cannot become a playback load.
            if kind == "seek" or elapsed_ms >= float(getattr(config, "PLAYBACK_DIAGNOSTIC_SLOW_RANGE_MS", 500.0)):
                try:
                    from ..core.diagnostics import record_event
                    record_event(
                        event="playback.range.completed", level="INFO" if elapsed_ms < 2000 else "WARN",
                        message=f"Playback segment {seg} acquired",
                        details={
                            "component": "playback", "channel_id": int(vkey[0]), "message_id": int(vkey[1]),
                            "segment": seg, "segment_bytes": len(data or b""), "priority": int(priority),
                            "epoch": int(epoch), "kind": str(kind), "cache": "telegram",
                            "duration_ms": round(elapsed_ms, 1),
                            "scheduler_wait_ms": round(float(self.metrics.get("foreground_scheduler_wait_ms", 0.0)), 1) if kind in {"playback", "seek"} else None,
                            "telegram_wait_ms": round(float(self.metrics.get("foreground_telegram_wait_ms", 0.0)), 1) if kind in {"playback", "seek"} else None,
                            "telegram_transfer_ms": round(float(self.metrics.get("foreground_telegram_transfer_ms", 0.0)), 1) if kind in {"playback", "seek"} else None,
                            "forward_playable_seconds": round(float(self.qos.context.buffered_seconds), 2),
                        },
                    )
                except Exception as diagnostic_exc:
                    _dbg(f"RANGE MISS completion diagnostic failed channel={int(vkey[0])} message={int(vkey[1])} segment={seg}: {type(diagnostic_exc).__name__}: {diagnostic_exc}")
            return data
        except asyncio.CancelledError:
            _stream_log(f"RANGE MISS cancelled channel={int(vkey[0])} message={int(vkey[1])} segment={seg} kind={str(kind)} epoch={int(epoch)}")
            # Superseded seeks/releases cancel obsolete range work by design.
            # Preserve cancellation semantics without surfacing normal navigation
            # as a playback acquisition error in diagnostics.
            raise
        except BaseException as exc:
            elapsed_ms = (time.monotonic() - started) * 1000.0
            try:
                from ..core.diagnostics import record_event
                record_event(
                    event="playback.range.failed", level="ERROR",
                    message=f"Playback segment {seg} acquisition failed", error=exc,
                    details={
                        "component": "playback", "channel_id": int(vkey[0]), "message_id": int(vkey[1]),
                        "segment": seg, "priority": int(priority), "epoch": int(epoch), "kind": str(kind),
                        "cache": "telegram", "duration_ms": round(elapsed_ms, 1),
                        "scheduler_wait_ms": round(float(self.metrics.get("foreground_scheduler_wait_ms", 0.0)), 1) if kind in {"playback", "seek"} else None,
                        "telegram_wait_ms": round(float(self.metrics.get("foreground_telegram_wait_ms", 0.0)), 1) if kind in {"playback", "seek"} else None,
                        "telegram_transfer_ms": round(float(self.metrics.get("foreground_telegram_transfer_ms", 0.0)), 1) if kind in {"playback", "seek"} else None,
                        "forward_playable_seconds": round(float(self.qos.context.buffered_seconds), 2),
                    },
                )
            except Exception as diagnostic_exc:
                _dbg(f"RANGE MISS failure diagnostic failed channel={int(vkey[0])} message={int(vkey[1])} segment={seg}: {type(diagnostic_exc).__name__}: {diagnostic_exc}")
            raise

    # ---------------- playback planning

    def _media_bps(self, message) -> float:
        f = getattr(message, "file", None)
        size = float(getattr(f, "size", 0) or 0)
        duration = float(getattr(f, "duration", 0) or 0)
        return size / duration if size > 0 and duration > 0 else 0.0


    # ------------------------------------------------------------------
    # SUBSECTION: Foreground admission and playhead prefetch
    # Window sizing and readahead.
    # ------------------------------------------------------------------
    def playback_window_segments(self, message):
        """Foreground segment window sized from estimated media bitrate.

        Restored from v18.44.05: a fixed 12-second target keeps the player
        predictable instead of letting the adaptive controller resize the
        foreground window while the browser is playing.
        """
        f = getattr(message, "file", None)
        size = int(getattr(f, "size", 0) or 0)
        duration = float(getattr(f, "duration", 0) or 0)
        if size <= 0 or duration <= 0:
            return PLAYBACK_WINDOW_SEGMENTS
        bytes_per_sec = size / max(duration, 1.0)
        target_seconds = float(getattr(config, "PLAYBACK_FOREGROUND_TARGET_SECONDS", 4.0))
        wanted = int((bytes_per_sec * target_seconds + SEGMENT - 1) // SEGMENT)
        return max(PLAYBACK_WINDOW_MIN_SEGMENTS, min(PLAYBACK_WINDOW_MAX_SEGMENTS, wanted))

    def adaptive_readahead_segments(self, message):
        f = getattr(message, "file", None)
        size = int(getattr(f, "size", 0) or 0)
        duration = float(getattr(f, "duration", 0) or 0)
        if size <= 0 or duration <= 0:
            return PLAYBACK_READAHEAD_SEGMENTS
        video_bps = size / duration
        ratio = max(self._adapt_last_speed, 1.0) / max(video_bps, 1.0)
        seconds = 10 if ratio >= 2 else 15
        return max(2, min(64, int((video_bps * seconds + SEGMENT - 1) // SEGMENT)))

    def playhead_readahead_segments(self, message):
        """Telegram-Web-style forward window, sized from the media bitrate."""
        f = getattr(message, "file", None)
        size = int(getattr(f, "size", 0) or 0)
        duration = float(getattr(f, "duration", 0) or 0)
        floor = max(PLAYHEAD_READAHEAD_MIN_SEGMENTS, (PLAYHEAD_READAHEAD_MIN_BYTES + SEGMENT - 1) // SEGMENT)
        if size <= 0 or duration <= 0:
            return max(1, min(PLAYHEAD_READAHEAD_MAX_SEGMENTS, floor))
        wanted = int((size / max(duration, 1.0) * PLAYHEAD_READAHEAD_SECONDS + SEGMENT - 1) // SEGMENT)
        return max(1, min(PLAYHEAD_READAHEAD_MAX_SEGMENTS, max(floor, wanted)))

    def schedule_playhead_readahead(self, vkey, message, seg, epoch, *, skip=()):
        """Keep the next N segments after ``seg`` downloading in parallel.

        Ranges are queued nearest-first at P1 in the current generation, so a
        seek cancels them and the browser's exact P0 range always jumps ahead.
        Already cached or in-flight segments are skipped, which makes calling
        this after every served segment cheap.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        total = (int(message.file.size) + SEGMENT - 1) // SEGMENT
        window = self.playhead_readahead_segments(message)
        completed = max(0, int(self._readahead_completed.get(vkey, 0)))
        level = min(len(PLAYHEAD_READAHEAD_BATCH_LADDER) - 1, int(math.ceil(math.log2(completed + 1))))
        self._readahead_ladder[vkey] = level
        batch_limit = int(PLAYHEAD_READAHEAD_BATCH_LADDER[level])
        outstanding = sum(1 for key in self.readahead_inflight
                          if key[0] == vkey[0] and key[1] == vkey[1]
                          and int(seg) < key[2] < min(total, int(seg) + 1 + window))
        available = max(0, batch_limit - outstanding)
        queued = 0
        # Queue only the current growth batch, nearest first. Larger batches are
        # admitted after successful ranges complete; this avoids flooding
        # Telegram with the entire 180-second runway on the first click.
        # Tail/index ranges are served by the stored media index or requested
        # when the container actually needs them.
        for s in range(int(seg) + 1, min(total, int(seg) + 1 + window)):
            if queued >= available:
                break
            if s in skip:
                continue
            skey = (vkey[0], vkey[1], s)
            if skey in self.segments or skey in self.readahead_inflight or skey in self.scheduler.pending:
                continue
            cache = self.disk_caches.get(vkey)
            if cache is not None and cache.has(s):
                continue
            self._readahead(vkey, message, s, 1, epoch, kind=PLAYHEAD_READAHEAD_KIND)
            queued += 1
        if queued:
            self.metrics["readahead_queued"] = self.metrics.get("readahead_queued", 0) + queued
        return queued

    def note_seek_target(self, vkey, segment: int) -> None:
        """Remember the explicit seek target for priority and warm-window policy."""
        key = (int(vkey[0]), int(vkey[1]))
        seg = max(0, int(segment))
        self._seek_buffer_target_key = key
        self._seek_buffer_target_segment = seg
        self.last_playhead[key] = seg
        # The connection has already proven itself on this video: restart the
        # read-ahead ramp part-way (up to 8 parallel segments) instead of from a
        # single 512 KiB request, so a seek reaches full speed in one round trip.
        # A brand-new video still starts from zero (see begin_video).
        proven = max(0, min(7, int(self._readahead_completed.get(key, 0))))
        self._readahead_completed[key] = proven
        self._readahead_ladder[key] = min(len(PLAYHEAD_READAHEAD_BATCH_LADDER) - 1, int(math.ceil(math.log2(proven + 1))))

    def begin_video(self, vkey, message, first_seg=0):
        """Activate a video for browser or external-player range requests.

        This lifecycle method is called by both the `/stream` range path and
        the VLC/MPC-HC launcher. Keeping it on PlaybackEngine is essential:
        the active video key controls foreground QoS, cache retention and
        read-ahead admission. It is idempotent for overlapping HTTP ranges.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        first_seg = max(0, int(first_seg))
        is_seek_target = (
            self._seek_buffer_target_key == vkey
            and self._seek_buffer_target_segment == first_seg
        )
        self.released_videos.pop(vkey, None)

        if self.current_key == vkey:
            self.last_playhead[vkey] = first_seg
            cache = self.disk_caches.get(vkey)
            if cache is not None:
                cache.touch()
            # Browser ranges overlap. Do not reset QoS for every range, but do
            # update the generation when an explicit seek has just been noted.
            context = getattr(self.qos, "context", None)
            if is_seek_target and (
                getattr(context, "generation", None) != int(self.epoch)
                or getattr(context, "target_segment", None) != first_seg
                or getattr(context, "video_id", None) != vkey
            ):
                self.qos.begin(vkey, self.epoch, first_seg, seeking=True)
            elif getattr(context, "video_id", None) != vkey:
                self.qos.begin(vkey, self.epoch, first_seg, seeking=False)
            return

        if self.job_manager is not None:
            try:
                self.job_manager.cancel_playback_conflicting_maintenance()
            except Exception as exc:
                _stream_log(f"PLAY maintenance cancellation failed: {type(exc).__name__}: {exc}")

        self.current_key = vkey
        self.last_playhead[vkey] = first_seg
        self._readahead_completed[vkey] = 0
        self._readahead_ladder[vkey] = 0
        cache = self.disk_caches.get(vkey)
        if cache is not None:
            cache.touch()
        self.qos.begin(vkey, self.epoch, first_seg, seeking=is_seek_target)
        # A switch releases transient work for previous videos but persistent
        # verified byte-cache files remain on disk for future playback.
        self.release_other_videos(vkey, keep_keys=self._upcoming(vkey, 1), reason="switched")
        self.scheduler.cancel_readahead(keep_vkey=vkey)
        self.scheduler.cancel_active_lower_priority(
            min_priority=2,
            exclude_key=(vkey[0], vkey[1], first_seg),
        )
        _stream_log(
            f"PLAY {vkey} {getattr(message.file, 'name', '') or ''} "
            f"size={_human_bytes(message.file.size)} start_segment={first_seg}"
        )
        # V29.4.383: start progressive background download of entire video
        self._start_background_cache(vkey, message)

    # ---- Background progressive cache (V29.4.383) ----
    # Downloads the entire current video to disk cache at low priority,
    # continuing even when the browser is paused / idle.  This makes
    # seeking back to an already-watched position instant and fills the
    # green "disk cache" bar in the timeline.

    def _cancel_background_cache(self):
        """Cancel any running background progressive cache task."""
        task = self._background_cache_task
        if task is not None and not task.done():
            task.cancel()
        self._background_cache_task = None
        self._background_cache_vkey = None


    # ------------------------------------------------------------------
    # SUBSECTION: Background caching and next-video warmup
    # Progressive cache, preload and warmup.
    # ------------------------------------------------------------------
    def _start_background_cache(self, vkey, message):
        """Launch a background loop that progressively caches every segment."""
        self._cancel_background_cache()
        vkey = (int(vkey[0]), int(vkey[1]))
        self._background_cache_vkey = vkey
        self._background_cache_retry_segments = set()
        total_segments = (int(message.file.size) + SEGMENT - 1) // SEGMENT

        async def _progressive_loop():
            """Fill missing segments at P2 without building an unbounded queue."""
            try:
                next_segment = 0
                while (next_segment < total_segments
                       or self._background_cache_retry_segments) and self.running:
                    if self.current_key != vkey:
                        return  # video switched
                    # Keep background work out of the way while any browser
                    # range is waiting for bytes. Paused/idle playback still
                    # resumes filling the persistent cache.
                    if self.active_http_requests > 0:
                        await asyncio.sleep(0.1)
                        continue
                    # The scheduler cannot preempt a Telegram transfer already
                    # in flight. Limit this full-file fill to two queued/active
                    # segments so it cannot build a huge backlog ahead of seeks.
                    pending_background = sum(
                        1 for key, entry in self.scheduler.pending.items()
                        if key[:2] == vkey and str(entry.get("kind") or "") == "bgcache"
                    )
                    if pending_background >= 2:
                        await asyncio.sleep(0.1)
                        continue
                    if self._background_cache_retry_segments:
                        seg = min(self._background_cache_retry_segments)
                        self._background_cache_retry_segments.discard(seg)
                    else:
                        seg = next_segment
                        next_segment += 1
                    # Skip segments already on disk or in RAM
                    cache = self.disk_caches.get(vkey)
                    skey = (vkey[0], vkey[1], seg)
                    if (skey in self.segments
                            or skey in self.readahead_inflight
                            or skey in self.scheduler.pending
                            or (cache is not None and cache.has(seg))):
                        seg += 1
                        continue
                    # Queue at P2 — below browser P0 and readahead P1
                    self._readahead(vkey, message, seg, priority=2, epoch=self.epoch, kind="bgcache")
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                _stream_log(f"BGCACHE loop error {vkey}: {type(exc).__name__}: {exc}")

        self._background_cache_task = self._spawn(
            _progressive_loop(), f"bgcache-{vkey[1]}"
        )

    def _readahead(self, vkey, message, seg, priority=1, epoch=0, kind="cache"):
        """Queue one low-priority range without blocking the HTTP delivery path."""
        vkey = (int(vkey[0]), int(vkey[1]))
        seg = int(seg)
        skey = (vkey[0], vkey[1], seg)
        if skey in self.readahead_inflight or skey in self.segments:
            return None
        cache = self.disk_caches.get(vkey)
        if cache is not None and cache.has(seg):
            return None
        self.readahead_inflight.add(skey)

        async def run():
            succeeded = False
            try:
                await self.get_segment(vkey, message, seg, int(priority), int(epoch), kind=kind)
                succeeded = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _stream_log(
                    f"READAHEAD failed channel={vkey[0]} message={vkey[1]} segment={seg} "
                    f"priority={priority} kind={kind}: {type(exc).__name__}: {exc}"
                )
            finally:
                self.readahead_inflight.discard(skey)
                if kind == PLAYHEAD_READAHEAD_KIND and succeeded:
                    count = int(self._readahead_completed.get(vkey, 0)) + 1
                    self._readahead_completed[vkey] = count
                    self._readahead_ladder[vkey] = min(
                        len(PLAYHEAD_READAHEAD_BATCH_LADDER) - 1,
                        int(math.ceil(math.log2(count + 1))),
                    )
                if kind == PLAYHEAD_READAHEAD_KIND and succeeded and self.running and self.current_key == vkey:
                    target = int(self.last_playhead.get(vkey, seg))
                    self.schedule_playhead_readahead(vkey, message, target, self.epoch)

        return self._spawn(run(), f"readahead-{vkey[1]}-{seg}")

    def set_play_order(self, keys):
        for task in list(self.next_prefix_tasks.values()):
            if task is not None and not task.done():
                task.cancel()
        self.next_prefix_tasks.clear()
        self.next_prefix_owners.clear()
        self.play_order = [(int(c), int(m)) for c, m in keys]
        self.next_prefix_started.clear()

    def _upcoming(self, vkey, count=1):
        """Return only the next ``count`` items in the current playback order."""
        if not vkey or not self.play_order:
            return []
        try:
            pos = self.play_order.index((int(vkey[0]), int(vkey[1])))
        except ValueError:
            return []
        return self.play_order[pos + 1:pos + 1 + max(0, int(count))]

    def _initial_prefix_segments(self, message, seconds=15.0):
        size = max(0, int(getattr(getattr(message, "file", None), "size", 0) or 0))
        duration = max(0.0, float(getattr(getattr(message, "file", None), "duration", 0) or 0))
        if size <= 0:
            return 0
        total = max(1, (size + SEGMENT - 1) // SEGMENT)
        seconds = min(max(1.0, float(seconds)), duration) if duration > 0 else max(1.0, float(seconds))
        estimate = int(math.ceil(size * seconds / duration)) if duration > 0 else SEGMENT * 2
        target_bytes = min(size, max(SEGMENT, estimate))
        return max(1, min(total, (target_bytes + SEGMENT - 1) // SEGMENT))

    @staticmethod
    def _preload_window_segments(size, duration, keyframes, start_seconds, seconds):
        """Map a requested time window to byte-cache segments, preferring indexed sync samples."""
        size = max(0, int(size or 0))
        if size <= 0:
            return []
        total = max(1, (size + SEGMENT - 1) // SEGMENT)
        duration = max(0.0, float(duration or 0))
        start = max(0.0, float(start_seconds or 0))
        window = max(1.0, float(seconds or 1))
        if duration > 0:
            start = min(start, duration)
            end = min(duration, start + window)
        else:
            end = start + window

        points = []
        for item in keyframes or ():
            try:
                timestamp, offset = float(item[0]), int(item[1])
            except (TypeError, ValueError, IndexError, OverflowError):
                continue
            if math.isfinite(timestamp) and timestamp >= 0 and 0 <= offset < size:
                points.append((timestamp, offset))
        points.sort(key=lambda point: (point[0], point[1]))

        if points:
            preceding = [point for point in points if point[0] <= start]
            following = next((point for point in points if point[0] >= end), None)
            start_offset = (preceding[-1] if preceding else points[0])[1]
            if following:
                end_offset = following[1]
            elif duration > 0 and end >= points[-1][0]:
                end_offset = size - 1
            else:
                end_offset = points[-1][1]
        elif duration > 0:
            start_offset = int(size * min(start, duration) / duration)
            end_offset = int(size * min(end, duration) / duration)
        else:
            start_offset = 0
            end_offset = min(size - 1, SEGMENT * 2)

        if end_offset < start_offset:
            if duration > 0:
                start_offset = int(size * min(start, duration) / duration)
                end_offset = int(size * min(end, duration) / duration)
            else:
                start_offset, end_offset = 0, min(size - 1, SEGMENT * 2)
        first = max(0, min(total - 1, start_offset // SEGMENT))
        # Include a segment beyond the end keyframe so a sample straddling a
        # segment boundary is still available to the player.
        last = max(first, min(total - 1, end_offset // SEGMENT + 1))
        return list(range(first, last + 1))

    def start_video_preload(self, vkey, seconds=30.0, start_seconds=0.0, speed_limit_bps=None):
        """Queue an MP4 time window; optional bandwidth pacing applies to background reads only."""
        key = (int(vkey[0]), int(vkey[1]))
        seconds = max(1.0, min(120.0, float(seconds or 1.0)))
        start_seconds = max(0.0, float(start_seconds or 0.0))
        try:
            speed_limit_bps = int(float(speed_limit_bps)) if speed_limit_bps not in (None, "", 0, "0") else 0
        except (TypeError, ValueError, OverflowError):
            speed_limit_bps = 0
        speed_limit_bps = max(0, min(20 * 1024 * 1024, speed_limit_bps))
        if not self.running:
            return {"ok": False, "status": "unavailable", "detail": "playback engine is not running"}
        existing = self.preload_tasks.get(key)
        if existing is not None and not existing.done():
            if start_seconds > 0:
                self.pending_seek_preloads[key] = (start_seconds, seconds)
                return {"ok": True, "status": "queued", "channel_id": key[0],
                        "message_id": key[1], "start_seconds": start_seconds}
            return {"ok": True, "status": "already-running", "channel_id": key[0], "message_id": key[1]}
        self.released_videos.pop(key, None)
        task = self._spawn(self._preload_video_ranges(key, seconds, start_seconds, speed_limit_bps), f"card-click-preload-{key[1]}")
        self.preload_tasks[key] = task
        def done(finished, cache_key=key):
            if self.preload_tasks.get(cache_key) is finished:
                self.preload_tasks.pop(cache_key, None)
                pending = self.pending_seek_preloads.pop(cache_key, None)
                if pending and self.running:
                    self.start_video_preload(cache_key, seconds=pending[1], start_seconds=pending[0])
        task.add_done_callback(done)
        return {"ok": True, "status": "queued", "channel_id": key[0], "message_id": key[1]}

    async def _preload_video_ranges(self, vkey, seconds=30.0, start_seconds=0.0, speed_limit_bps=0):
        """Preload a video's start or a window into the disk cache."""
        try:
            message = await self.get_playback_message(*vkey)
            size = int(getattr(message.file, "size", 0) or 0)
            if size <= 0:
                return
            name = str(getattr(message.file, "name", "") or "").lower()
            mime = str(getattr(message.file, "mime_type", "") or "").lower()
            if not (name.endswith((".mp4", ".m4v", ".mov")) or mime in {"video/mp4", "video/quicktime"}):
                return
            cache = await self.get_disk_cache_async(vkey, message)
            cache.touch(kind="prefetch")
            moov_segments = []
            manifest = {}
            try:
                from ..core.playback_index_cache import read_manifest
                manifest = await asyncio.to_thread(read_manifest, int(vkey[0]), int(vkey[1]), size)
                if manifest:
                    offset = max(0, int(manifest.get("moov_offset") or 0))
                    moov_size = max(0, int(manifest.get("moov_size") or 0))
                    if moov_size:
                        first = offset // SEGMENT
                        last = min((size - 1) // SEGMENT, (offset + moov_size - 1) // SEGMENT)
                        moov_segments = list(range(first, last + 1))
            except Exception as exc:
                _stream_log(f"CARD PRELOAD manifest unavailable key={vkey}: {type(exc).__name__}: {exc}")
            duration = max(0.0, float(getattr(message.file, "duration", 0) or 0))
            async def preload_window(window_start, window_seconds):
                if window_start <= 0:
                    bounded_seconds = min(max(1.0, float(window_seconds)), duration) if duration > 0 else float(window_seconds)
                    data_segments = list(range(self._initial_prefix_segments(message, bounded_seconds)))
                    label = "prefix"
                else:
                    data_segments = self._preload_window_segments(
                        size, duration, manifest.get("keyframes") or [],
                        window_start, window_seconds,
                    )
                    label = f"seek@{float(window_start):.2f}s"
                # Fetch one small slice first for fast startup, then the index
                # (for tail-moov MP4s), then the rest of the requested window.
                # Every item is still stored as a canonical 512 KiB verified segment.
                sequence = list(dict.fromkeys(data_segments[:1] + moov_segments + data_segments[1:]))
                _stream_log(f"CARD PRELOAD start key={vkey} window={label} seconds={float(window_seconds):.1f} segments={len(data_segments)} moov_segments={len(moov_segments)}")
                pacing_started = time.monotonic()
                paced_bytes = 0
                for seg in sequence:
                    if not self.running:
                        return
                    cache.touch(kind="prefetch")
                    if cache.has(seg):
                        continue
                    data = await self.get_segment(vkey, message, seg, 2, 0, kind="preload")
                    paced_bytes += len(data or b"")
                    if speed_limit_bps and paced_bytes:
                        target_elapsed = paced_bytes / float(speed_limit_bps)
                        delay = target_elapsed - (time.monotonic() - pacing_started)
                        if delay > 0:
                            await asyncio.sleep(delay)
                cache.touch(kind="prefetch")
                _stream_log(f"CARD PRELOAD ready key={vkey} window={label} segments={len(sequence)} cached_bytes={cache.cached_bytes}")

            await preload_window(start_seconds, seconds)
            while self.running:
                pending = self.pending_seek_preloads.pop(vkey, None)
                if not pending:
                    break
                await preload_window(pending[0], pending[1])
        except asyncio.CancelledError:
            _stream_log(f"CARD PRELOAD cancelled key={vkey}")
        except Exception as exc:
            _stream_log(f"CARD PRELOAD failed key={vkey}: {type(exc).__name__}: {exc}")

    def start_next_prefix(self, vkey):
        """Optionally preload a short prefix of the next queued video."""
        if float(getattr(config, "PLAYBACK_NEXT_HEADSTART_SECONDS", 30.0) or 0) <= 0:
            return None
        key = (int(vkey[0]), int(vkey[1]))
        upcoming = self._upcoming(key, 1)
        if key in self.next_prefix_started or not upcoming:
            return None
        next_key = (int(upcoming[0][0]), int(upcoming[0][1]))
        self.next_prefix_owners[next_key] = key
        existing = self.next_prefix_tasks.get(next_key)
        if existing is not None and not existing.done():
            self.next_prefix_started.add(key)
            return existing
        self.next_prefix_started.add(key)
        task = self._spawn(self._prefetch_next_prefix(key), f"next-video-prefix-{next_key[1]}")
        self.next_prefix_tasks[next_key] = task
        def done(finished, cache_key=next_key):
            if self.next_prefix_tasks.get(cache_key) is finished:
                self.next_prefix_tasks.pop(cache_key, None)
                self.next_prefix_owners.pop(cache_key, None)
        task.add_done_callback(done)
        return task

    async def _prefetch_next_prefix(self, vkey):
        upcoming = self._upcoming(vkey, 1)
        if not upcoming:
            return
        nkey = upcoming[0]
        try:
            message = await self.get_playback_message(*nkey)
            segments = self._initial_prefix_segments(message, seconds=max(30.0, min(120.0, float(getattr(config, "PLAYBACK_NEXT_HEADSTART_SECONDS", 30.0) or 30.0))))
            cache = await self.get_disk_cache_async(nkey, message)
            cache.touch(kind="prefetch")
            for seg in range(segments):
                if (self.current_key not in (None, vkey)
                        or self.next_prefix_owners.get(nkey) != vkey
                        or self._upcoming(vkey, 1) != [nkey]):
                    return
                if cache.has(seg):
                    continue
                await self.get_segment(nkey, message, seg, 2, 0, kind="preload")
            cache.touch(kind="prefetch")
            _dbg(f"NEXT VIDEO PREFIX ready {nkey} segments={segments}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _stream_log(f"NEXT VIDEO prefix prefetch failed {nkey}: {type(exc).__name__}: {exc}")

    async def clear_persistent_cache(self):
        """Clear range bytes; require stopped browser playback to avoid removing open data."""
        if self.active_http_requests > 0:
            raise RuntimeError("Stop browser/external-player playback before clearing the video byte cache.")
        # Prevent a cancelled prefix task's completion callback from relaunching
        # a queued resume window while this explicit cache clear is in progress.
        self.pending_seek_preloads.clear()
        tasks = list(self.preload_tasks.values()) + list(self.next_prefix_tasks.values())
        for task in tasks:
            if task is not None and not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # Let scheduler workers unwind canceled background RPCs before closing
        # files. Foreground HTTP streams are prohibited above, so this wait is
        # only for optional cache/prefix jobs.
        for _ in range(40):
            if self.scheduler.active_jobs <= 0 and not any(cache.busy() for cache in self.disk_caches.values()):
                break
            await asyncio.sleep(0.05)
        if self.scheduler.active_jobs > 0 or any(cache.busy() for cache in self.disk_caches.values()):
            raise RuntimeError("A cache transfer is still finishing. Retry Clear video-byte cache in a moment.")
        # Snapshot counts before cleanup. Explicit clearing may delete open
        # handles' files only after every range/task has drained; normal release
        # always calls close() and keeps the persistent bytes.
        before = await asyncio.to_thread(range_cache_usage)
        for cache in list(self.disk_caches.values()):
            await asyncio.to_thread(cache.cleanup)
        self.disk_caches.clear()
        self.segments.clear()
        self.preload_tasks.clear()
        self.pending_seek_preloads.clear()
        self.next_prefix_tasks.clear()
        self.next_prefix_owners.clear()
        self.next_prefix_started.clear()
        result = await asyncio.to_thread(clear_persistent_range_cache)
        result["bytes_cleared"] = int(before.get("bytes", result.get("bytes_cleared", 0)))
        result["entries_cleared"] = int(before.get("entries", result.get("entries_cleared", 0)))
        return result

    async def warm_first_segment(self, vkey, message, timeout=25.0, segments=4, start_seg=0):
        """Warm a bounded initial playback buffer before the browser attaches.

        Metadata and timeline snapshots are not media bytes.  The browser must
        have actual video data available before playback starts or a fast UI can
        outrun Telegram on the first range requests.
        """
        self.released_videos.pop((int(vkey[0]), int(vkey[1])), None)   # V29.4.129: opened again
        started = time.monotonic()
        session = await self.controller.session(f"{vkey[0]}:{vkey[1]}")
        generation = int(session.generation)
        self.epoch = max(int(self.epoch), generation)
        self.qos.begin(vkey, generation, int(start_seg), seeking=True)
        total = (int(message.file.size) + SEGMENT - 1) // SEGMENT
        target = max(1, min(int(segments), total))
        first = max(0, min(int(start_seg), max(0, total - 1)))
        wanted = list(range(first, min(total, first + target)))
        # The exact first segment is the latency-critical seek/start segment.
        # Fetch it through the interactive split path; the remaining warm window
        # can use normal playback requests without delaying the first byte.
        jobs = [self.get_segment(vkey, message, first, 0, generation, kind="seek")]
        jobs.extend(self.get_segment(vkey, message, seg, 1, generation, kind="playback")
                    for seg in wanted if seg != first)
        # Initial warm-up is advisory. The browser source is already attached and
        # owns the authoritative playback request, so a short warm-up timeout must
        # never cancel the shared segment transfers underneath it. The previous
        # wait_for(gather(...)) propagated cancellation into get_segment() and
        # scheduler._release_waiter(), which could cancel segment 0/1 just as
        # Chromium was waiting for its first bytes. Shield the aggregate so the
        # Telegram work continues to populate the shared cache after this HTTP
        # helper returns a timeout.
        gathered = asyncio.gather(*jobs, return_exceptions=False)
        try:
            await asyncio.wait_for(asyncio.shield(gathered), timeout=timeout)
        except asyncio.TimeoutError:
            def _finish_warm(t):
                if not t.cancelled():
                    try:
                        t.exception()
                    except Exception:
                        pass
            gathered.add_done_callback(_finish_warm)
            raise
        _stream_log(f"WARM initial-buffer {vkey} start={first} segments={len(wanted)} in {time.monotonic() - started:.2f}s")
        return len(wanted)


    # ------------------------------------------------------------------
    # SUBSECTION: Browser and external player delivery
    # Range delivery for browser and aiohttp players.
    # ------------------------------------------------------------------
    def prepare_video(self, vkey, message, head_segments=2):
        """Compatibility no-op; only stream range requests admit Telegram reads.

        The historical implementation downloaded the video's head and tail as
        soon as details were opened. MP4 index/start ranges are now persisted by
        Metadata & Assets scans, so this method intentionally schedules nothing.
        """
        return {"ok": True, "local_index_only": True}

    async def _iter_range_light(self, vkey, message, start, end, *, priority=PREVIEW_PRIORITY, epoch=-1, kind="preview"):
        """Small reads for ffprobe / seek previews.

        Preview traffic is given interactive priority over queued read-ahead and
        background preload work, while an active playback stream always wins. Only a
        bounded number of preview segments are in flight so hovering the seek
        bar cannot consume the playback worker pool. All reads still reuse the
        same RAM/sparse-disk cache as normal playback.
        """
        size = int(message.file.size)
        start, end = int(start), min(int(end), size - 1)
        first, last = start // SEGMENT, end // SEGMENT
        # Keep one preview segment alongside active playback; allow two when
        # idle so previews can outrun unrelated background cache filling.
        if kind == "preview":
            # A keyframe can straddle a playback-range boundary; keep both
            # halves in parallel instead of paying two serial Telegram round trips.
            window = 2
        else:
            window = 1 if self.active_http_requests > 0 else min(2, max(1, int(self.fetch_concurrency)))
        tasks = {}
        next_seg = first
        try:
            while next_seg <= last or tasks:
                while next_seg <= last and len(tasks) < window:
                    seg = next_seg
                    next_seg += 1
                    tasks[seg] = asyncio.ensure_future(
                        self.get_segment(vkey, message, seg, priority, epoch, kind=kind)
                    )
                seg = min(tasks)
                data = await tasks.pop(seg)
                seg_start = seg * SEGMENT
                a = max(start - seg_start, 0)
                b = min(end - seg_start + 1, len(data))
                if b > a:
                    yield data if (a == 0 and b == len(data)) else data[a:b]
        finally:
            for t in tasks.values():
                t.add_done_callback(lambda f: f.cancelled() or f.exception())
            self.scheduler.demote([(vkey[0], vkey[1], s) for s in tasks], 3)

    def buffer_status(self, vkey, message, start_seg=0, target_segments=4):
        """Return the contiguous locally cached playback buffer.

        This is intentionally cheap and never downloads anything. It is used by
        the browser before first play so the player starts with a real buffer
        instead of racing Telegram on the first several range requests.
        """
        vkey = (int(vkey[0]), int(vkey[1]))
        start_seg = max(0, int(start_seg))
        target_segments = max(1, int(target_segments))
        cache = self.disk_caches.get(vkey)
        contiguous = 0
        if cache is not None:
            total = cache.total_segments
            while start_seg + contiguous < total and cache.has(start_seg + contiguous):
                contiguous += 1
                if contiguous >= target_segments:
                    break
        size = int(getattr(getattr(message, "file", None), "size", 0) or 0)
        duration = float(getattr(getattr(message, "file", None), "duration", 0) or 0)
        buffered_bytes = contiguous * SEGMENT
        if size and buffered_bytes > size:
            buffered_bytes = size
        buffered_seconds = (buffered_bytes / size * duration) if size > 0 and duration > 0 else 0.0
        return {
            "segments": contiguous,
            "target_segments": target_segments,
            "bytes": buffered_bytes,
            "seconds": round(buffered_seconds, 2),
            "ready": contiguous >= target_segments,
        }

    def _note_first_byte(self, ms: float) -> None:
        """Track time-to-first-byte of browser range requests (start/seek latency)."""
        samples = getattr(self, "_first_byte_samples", None)
        if samples is None:
            samples = self._first_byte_samples = []
        samples.append(float(ms))
        del samples[:-50]
        ordered = sorted(samples)
        metrics = getattr(self, "metrics", None)
        if isinstance(metrics, dict):
            metrics["seek_first_byte_ms_last"] = round(ms, 1)
            metrics["seek_first_byte_ms_p50"] = round(ordered[len(ordered) // 2], 1)
            metrics["seek_first_byte_ms_p90"] = round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))], 1)

    async def read_browser_range(self, cid, message, start, end):
        """Materialize one browser HTTP Range response before sending HTTP.

        Chromium asks for small byte ranges. Returning a normal Response only
        after the exact range is ready avoids keeping an HTTP/2 streaming
        response open while the next Telegram segment is being acquired. The
        collector is shielded from client-disconnect cancellation so an
        admitted foreground Telegram transfer can finish and populate the
        shared cache for the browser's next request.
        """
        started = time.monotonic()
        request = asyncio.create_task(
            self._collect_browser_range(cid, message, start, end),
            name=f"browser-range-{int(message.id)}-{int(start)}",
        )
        request._ax_vkey = (int(cid), int(message.id))
        self.browser_range_tasks.add(request)
        request.add_done_callback(self.browser_range_tasks.discard)
        try:
            # A broken scheduler/client path must not leave Chrome holding an
            # unanswered Range request indefinitely. The deadline bounds the
            # HTTP wait; per-segment logic performs the one-time reference/DC
            # recovery before this guard fires.
            deadline = max(0.05, float(getattr(config, "PLAYBACK_BROWSER_RANGE_DEADLINE_SECONDS", 12.0)))
            done, _pending = await asyncio.wait({request}, timeout=deadline)
            if not done:
                self.metrics["browser_delivery_deadlines"] = int(self.metrics.get("browser_delivery_deadlines", 0)) + 1
                _stream_log(
                    f"BROWSER_RANGE deadline exceeded channel={int(cid)} message={int(message.id)} "
                    f"start={int(start)} end={int(end)} deadline_s={deadline:.1f}; "
                    "collector cancelled; browser receives HTTP 503"
                )
                request.cancel()
                raise TimeoutError(
                    f"Telegram playback range {int(start)}-{int(end)} did not complete within {deadline:.1f}s"
                )
            data = await request
            self.metrics["browser_delivery_completed"] = int(self.metrics.get("browser_delivery_completed", 0)) + 1
            self.metrics["browser_delivery_last_ms"] = round((time.monotonic() - started) * 1000.0, 1)
            return data
        except asyncio.CancelledError:
            self.metrics["browser_delivery_client_cancelled"] = int(self.metrics.get("browser_delivery_client_cancelled", 0)) + 1
            _stream_log(f"BROWSER_RANGE cancelled channel={int(cid)} message={int(message.id)} start={int(start)} end={int(end)}")
            # Do not cancel the collector. Its result remains cacheable.
            raise

    async def read_browser_prebuffer_range(self, cid, message, start, end):
        """Warm one bounded browser-cache range without claiming playback priority."""
        chunks = []
        async for chunk in self.iter_range(cid, message, start, end, mode="prewarm"):
            if chunk:
                chunks.append(bytes(chunk))
        return b"".join(chunks)

    async def _collect_browser_range(self, cid, message, start, end):
        """Read a byte range into one bytes object for the browser."""
        started = time.monotonic()
        chunks = []
        total = 0
        try:
            async for chunk in self.iter_range(cid, message, start, end, mode="play", browser=True):
                if chunk:
                    chunks.append(bytes(chunk))
                    total += len(chunk)
            data = b"".join(chunks)
            self.metrics["browser_delivery_ready_bytes"] = int(total)
            self.metrics["browser_delivery_ready_ms"] = round((time.monotonic() - started) * 1000.0, 1)
            try:
                from ..core.diagnostics import record_event
                record_event(
                    event="playback.browser_delivery",
                    level="INFO" if (time.monotonic() - started) < 2000 else "WARN",
                    message=f"Browser range ready {int(start)}-{int(end)}",
                    details={
                        "component": "playback", "channel_id": int(cid), "message_id": int(message.id),
                        "start": int(start), "end": int(end), "requested_bytes": int(end - start + 1),
                        "served_bytes": int(len(data)),
                        "delivery_ready_ms": round((time.monotonic() - started) * 1000.0, 1),
                        "http_mode": "materialized_range",
                        "cancel_safe": True,
                    },
                )
            except Exception:
                pass
            return data
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            try:
                from ..core.diagnostics import record_event
                record_event(
                    event="playback.browser_delivery_failed", level="ERROR",
                    message=f"Browser range failed {int(start)}-{int(end)}", error=exc,
                    details={
                        "component": "playback", "channel_id": int(cid), "message_id": int(message.id),
                        "start": int(start), "end": int(end),
                        "delivery_ms": round((time.monotonic() - started) * 1000.0, 1),
                        "http_mode": "materialized_range",
                    },
                )
            except Exception:
                pass
            raise

    async def _await_browser_segment(self, task, vkey, message, seg, session, attempts=3):
        """Await one segment; survive internal cancellation (V29.4.120).

        A segment future shared through the scheduler can be cancelled by a
        seek-generation change, a waiter release or a preemption while this
        request still needs the bytes. Previously that CancelledError escaped
        into the HTTP handler and Chrome reported PIPELINE_ERROR_READ ("data
        source error"), which pushed HEVC videos into the slow compatibility
        transcoder. Only a cancellation of this request itself propagates.
        """
        for _ in range(max(1, int(attempts))):
            try:
                return await task
            except asyncio.CancelledError:
                current = asyncio.current_task()
                cancelling = getattr(current, "cancelling", None)
                if current is None or (cancelling is not None and cancelling()):
                    raise
                self.metrics["browser_range_recovered"] = self.metrics.get("browser_range_recovered", 0) + 1
                task = asyncio.ensure_future(self.get_segment(
                    vkey, message, seg, 0, int(getattr(session, "generation", self.epoch)), kind="playback"))
        return await task

    async def iter_range(self, cid, message, start, end, mode="play", browser=False):
        """Yield bytes [start, end] of a video, downloading ahead of the player.

        mode="probe" is used by ffprobe and the seek-preview generator: it only
        downloads what is asked for and leaves the playing video alone."""
        vkey = (int(cid), int(message.id))
        if mode != "play":
            if mode == "preview":
                async for chunk in self._iter_range_light(
                    vkey, message, start, end,
                    priority=PREVIEW_PRIORITY, epoch=-1, kind="preview"
                ):
                    yield chunk
            elif mode == "prewarm":
                # Background preview pre-generation: priority 2 is held back by
                # the scheduler whenever a playback request is active.
                async for chunk in self._iter_range_light(
                    vkey, message, start, end, priority=2, epoch=0, kind="prewarm"
                ):
                    yield chunk
            else:
                async for chunk in self._iter_range_light(vkey, message, start, end, priority=1, epoch=0, kind="probe"):
                    yield chunk
            return
        size = int(message.file.size)
        start, end = int(start), min(int(end), size - 1)
        first, last = start // SEGMENT, end // SEGMENT
        total = (size + SEGMENT - 1) // SEGMENT
        self._last_range_activity = time.monotonic()
        _iter_t0 = time.monotonic()
        session = await self.controller.session(f"{vkey[0]}:{vkey[1]}")
        if time.monotonic() - _iter_t0 > 1.0:
            _stream_log(f"ITER slow stage=controller_session t={time.monotonic() - _iter_t0:.1f}s message={vkey[1]}")
        size_for_position = max(float(getattr(message.file, "size", 0) or 1), 1.0)
        duration_for_position = max(float(getattr(message.file, "duration", 0) or 0.0), 0.0)
        position_seconds = (start / size_for_position) * duration_for_position
        previous_seg = self.last_playhead.get(vkey)
        # Chromium is allowed to issue multiple non-contiguous byte ranges while
        # probing MP4 indexes, filling its buffer, and preparing future samples.
        # Those ranges are normal playback requests, not user seeks. Treating
        # every byte discontinuity as a new generation caused Archive X to cancel
        # valid foreground transfers that Chrome had already requested, which is
        # precisely the pattern behind the 2-second-play/long-loading failures.
        # Browser generations are therefore advanced only by an explicit seek
        # marker from the UI. Native aiohttp players retain the historical
        # discontinuity detection because they do not use the browser marker.
        pending_seek = session.consume_pending_seek() if browser else None
        if browser:
            if pending_seek is not None:
                epoch = session.new_seek(pending_seek)
                self.epoch = max(self.epoch + 1, epoch)
                self.note_seek_target(vkey, first)
                if self.current_key == vkey:
                    self.metrics["seeks"] += 1
                self.trace.record("seek", key=f"{vkey[0]}:{vkey[1]}", value=float(pending_seek), detail=f"generation={epoch};browser_explicit=true")
                self.scheduler.adopt_window(vkey, first, first + self.playhead_readahead_segments(message), epoch)
                self.scheduler.cancel_stale_video(vkey, epoch, min_priority=1)
                # Keep ranges the browser is already waiting for, but cancel
                # obsolete active speculation so repeated seeks cannot leave
                # every Telegram transfer lane occupied by old read-ahead.
                self.scheduler.cancel_active_stale_speculative_video(vkey, epoch)
                # V29.4.121: like Telegram Web K, transfers already in flight
                # are not cancelled on a browser seek - they complete into the
                # cache (cancelling wasted half-downloaded parts). Only queued
                # stale work is dropped; the new target's P0 range has its own
                # reserved lane.
                self.scheduler.wake()
            else:
                epoch = session.begin_request(position_seconds)
        else:
            sequential = self.current_key == vkey and previous_seg is not None and first <= int(previous_seg) + 1
            if sequential:
                epoch = session.begin_request(position_seconds)
            else:
                epoch = session.new_seek(position_seconds)
                self.epoch = max(self.epoch + 1, epoch)
                self.note_seek_target(vkey, first)
                if self.current_key == vkey:
                    self.metrics["seeks"] += 1
                self.trace.record("seek", key=f"{vkey[0]}:{vkey[1]}", value=position_seconds, detail=f"generation={epoch}")
                self.scheduler.cancel_stale_video(vkey, epoch, min_priority=1)
                cancel_active = getattr(self.scheduler, "cancel_active_stale_video", None)
                if cancel_active is not None:
                    cancel_active(vkey, epoch)
                self.scheduler.wake()
        self.begin_video(vkey, message, first)
        ahead = self.adaptive_readahead_segments(message)
        foreground_window = self.playback_window_segments(message)
        tasks = {}
        self.active_http_requests += 1
        # The playback lease belongs to the engine, not to one request. Browsers
        # overlap range requests (A starts, B starts, A ends, B ends); tying the
        # lease to the first request leaked it whenever that request was not
        # also the last one, and the resource watchdog had to reclaim it.
        if self.resources is not None and not getattr(self, "_playback_lease", False):
            self._playback_lease = True
            await self.resources.playback_enter()
        if time.monotonic() - _iter_t0 > 1.0:
            _stream_log(f"ITER slow stage=before_first_segment t={time.monotonic() - _iter_t0:.1f}s message={vkey[1]} browser={browser}")
        request_started = time.monotonic()
        first_byte_pending = True
        try:
            if browser:
                # The single active HTTP lane may still be occupied by a P2
                # background fill from the gap between browser Range requests.
                # Preempt it so the exact foreground bytes can start now, and
                # put cancelled background segments back into the fill queue.
                retry_segments = getattr(self, "_background_cache_retry_segments", None)
                if retry_segments is None:
                    retry_segments = self._background_cache_retry_segments = set()
                for key, entry in list(self.scheduler.active_entries.items()):
                    if (key[:2] == vkey
                            and str(entry.get("kind") or "") == "bgcache"
                            and int(entry.get("priority", 3)) >= 2
                            and key != (vkey[0], vkey[1], first)):
                        retry_segments.add(int(key[2]))
                self.scheduler.cancel_active_lower_priority(
                    min_priority=2,
                    exclude_key=(vkey[0], vkey[1], first),
                )
            for seg in range(first, last + 1):
                for s in range(seg, min(last, seg + foreground_window - 1) + 1):
                    if s not in tasks:
                        # Protect the seek target plus a short contiguous burst.
                        # The diagnostic showed the target segment completing while
                        # the very next playback segment remained delayed long enough
                        # for Chromium to cancel the HTTP request. Keeping the first
                        # few segments at foreground priority lets the player refill
                        # immediately after a discontinuous seek without promoting
                        # the entire readahead window.
                        seek_burst = max(1, int(getattr(config, "PLAYBACK_SEEK_BURST_SEGMENTS", 3)))
                        kind = "seek" if s == first else "playback"
                        priority = 0 if (s - first) < seek_burst else 1
                        tasks[s] = asyncio.ensure_future(self.get_segment(vkey, message, s, priority, epoch, kind=kind))
                # Do not enqueue speculative Telegram work before the browser has
                # received the foreground byte. Once playback has a real buffer,
                # admit only the small QoS-approved readahead window. The old code
                # could queue dozens of P2 ranges (often 48+) per HTTP request;
                # those tasks then occupied scheduler workers waiting on the
                # Governor's single background range lane.
                data = await self._await_browser_segment(tasks.pop(seg), vkey, message, seg, session)
                seg_start = seg * SEGMENT
                a = max(start - seg_start, 0)
                b = min(end - seg_start + 1, len(data))
                self.last_playhead[vkey] = seg
                if b > a:
                    chunk = data if (a == 0 and b == len(data)) else data[a:b]
                    self.bytes_served += len(chunk)
                    first_delivery = first_byte_pending
                    if first_delivery:
                        first_byte_pending = False
                        self._note_first_byte((time.monotonic() - request_started) * 1000.0)
                    # Deliver the requested bytes first. The async generator resumes
                    # only after the HTTP handler has accepted this chunk; only then
                    # queue the next-video prefix and playhead readahead. Cached startup bytes can therefore reach Chromium
                    # before speculative cache work is queued, while later video
                    # ranges continue downloading progressively in parallel.
                    yield chunk
                    if self.resources is not None:
                        self.resources.note_playback_activity()
                    if first_delivery:
                        if self.qos.background_prefetch_allowed(True):
                            self.start_next_prefix(vkey)
                    # Keep a bitrate-sized rolling window ahead of the playhead,
                    # but queue it after delivery. Explicit target ranges from a
                    # seek retain their P0 priority above these P1 read-ahead jobs.
                    self.schedule_playhead_readahead(vkey, message, max(seg, last), epoch, skip=tasks)
        finally:
            self.active_http_requests = max(0, self.active_http_requests - 1)
            if (self.active_http_requests == 0 and self.resources is not None
                    and getattr(self, "_playback_lease", False)):
                self._playback_lease = False
                await self.resources.playback_exit()
            self._last_range_activity = time.monotonic()
            if self.active_http_requests == 0:
                self.clear_current_video_if_idle(reason="browser_range_idle")
            leftover = [(vkey[0], vkey[1], s) for s in tasks]
            for t in tasks.values():
                # Keep those downloads (they fill the cache) but stop waiting.
                t.add_done_callback(lambda f: f.cancelled() or f.exception())
            self.scheduler.demote(leftover, 3)
            self.scheduler.wake()

    async def handle_aiohttp(self, request, cid, mid, mode="play"):
        """aiohttp handler used by the player feed for VLC/MPC-HC/ffmpeg."""
        web = _aiohttp_web
        try:
            message = await self.get_playback_message(cid, mid)
        except FileNotFoundError as e:
            return web.Response(status=404, text=str(e))
        except Exception as e:
            _stream_log(f"HTTP metadata error {cid}/{mid}: {type(e).__name__}: {e}")
            return web.Response(status=503, text=str(e))
        f = message.file
        size = int(f.size)
        mime = getattr(f, "mime_type", None) or "video/mp4"
        base = {"Content-Type": mime, "Accept-Ranges": "bytes",
                "Content-Disposition": content_disposition(getattr(f, "name", None) or f"{mid}.mp4"),
                "Cache-Control": "no-store"}
        if request.method == "HEAD":
            return web.Response(status=200, headers={**base, "Content-Length": str(size)})
        try:
            start, end, status = parse_range(size, request.headers.get("Range", ""))
        except ValueError:
            return web.Response(status=416, headers={"Content-Range": f"bytes */{size}"})
        headers = {**base, "Content-Length": str(end - start + 1)}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        range_started = time.monotonic()
        _dbg(f"HTTP {cid}/{mid} {start}-{end}/{size} UA={request.headers.get('User-Agent', '')[:40]}")
        response = web.StreamResponse(status=status, headers=headers)
        await response.prepare(request)
        agen = self.iter_range(cid, message, start, end, mode=mode)
        try:
            async for chunk in agen:
                await response.write(chunk)
            await response.write_eof()
        except (ConnectionError, asyncio.CancelledError):
            pass
        except Exception as e:
            _stream_log(f"HTTP stream error {cid}/{mid}: {type(e).__name__}: {e}")
        finally:
            await agen.aclose()
            elapsed_ms = (time.monotonic() - range_started) * 1000.0
            if elapsed_ms >= float(getattr(config, "PLAYBACK_DIAGNOSTIC_SLOW_RANGE_MS", 500.0)) or status == 206 and start > 0:
                try:
                    from ..core.diagnostics import record_event
                    record_event(
                        event="playback.http_range",
                        level="INFO" if elapsed_ms < 2000 else "WARN",
                        message=f"HTTP playback range {start}-{end}",
                        details={
                            "component": "playback", "channel_id": int(cid), "message_id": int(mid),
                            "start": int(start), "end": int(end), "size": int(size),
                            "status": int(status), "duration_ms": round(elapsed_ms, 1),
                            "user_agent": str(request.headers.get("User-Agent", ""))[:80],
                        },
                    )
                except Exception:
                    pass
        return response


# Backwards-compatible name used by older imports.
SmoothStreamServer = PlaybackEngine


# ---------------------------------------------------------------- signed links



# ==============================================================================
# SECTION: Signed links and local player feed
# Stream tokens and the local player feed.
# ==============================================================================

class StreamTokens:
    """Creates and checks signed stream links."""
    def __init__(self, db):
        secret = db.setting("stream_token_secret")
        if not secret:
            secret = secrets.token_hex(32)
            db.set_setting("stream_token_secret", secret)
        self.secret = bytes.fromhex(secret)

    def make(self, channel_id: int, msg_id: int, ttl: int = 7 * 24 * 3600) -> str:
        exp = int(time.time()) + int(ttl)
        payload = f"{int(channel_id)}:{int(msg_id)}:{exp}"
        sig = _hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()[:32]
        return f"{exp}.{sig}"

    def check(self, channel_id: int, msg_id: int, token: str) -> bool:
        try:
            exp_s, sig = token.split(".", 1)
            exp = int(exp_s)
        except Exception:
            return False
        if exp < time.time():
            return False
        payload = f"{int(channel_id)}:{int(msg_id)}:{exp}"
        want = _hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()[:32]
        return _hmac.compare_digest(sig, want)


_FEED_RUNNING = False


async def serve_local_player_feed(state: dict, tokens: StreamTokens, port: int, host: str = "127.0.0.1"):
    """Plain-HTTP feed for VLC/MPC-HC and FFmpeg helpers.

    The configured port is preferred. If it is occupied, choose the first
    available port in a small bounded range instead of silently rebinding to
    127.0.0.1 (which breaks playback from other devices). The effective port
    is stored in ``state['player_port']`` so every generated URL follows it.
    """
    if not AIOHTTP_AVAILABLE:
        raise RuntimeError("aiohttp is required for the playback feed")
    global _FEED_RUNNING
    if _FEED_RUNNING:
        _stream_log("Player feed already running; not starting it again")
        return
    _FEED_RUNNING = True
    web = _aiohttp_web
    local_app = web.Application()

    async def local_handle(request):
        try:
            msg_id = int(request.match_info["msg_id"])
            cid = int(request.rel_url.query.get("c", "0") or 0)
        except ValueError:
            return web.Response(status=400)
        if not tokens.check(cid, msg_id, request.rel_url.query.get("t", "")):
            return web.Response(status=403, text="invalid or expired link")
        if not cid:
            return web.Response(status=400, text="missing channel")
        get_engine = state.get("get_engine") if state else None
        if not get_engine:
            return web.Response(status=503, text="playback engine not ready")
        try:
            engine = await get_engine()
        except Exception as e:
            return web.Response(status=503, text=f"playback engine not ready: {getattr(e, 'detail', e)}")
        raw_mode = str(request.rel_url.query.get("m") or "")
        mode = raw_mode if raw_mode in {"probe", "preview", "prewarm"} else "play"
        return await engine.handle_aiohttp(request, cid, msg_id, mode=mode)

    # Exactly one GET and one HEAD route. (v18.6 used add_get() - which also
    # registers HEAD by default - plus add_head(), so aiohttp raised "method
    # HEAD is already registered" and the whole feed stopped.)
    async def playlist_handle(request):
        # Playlist URLs are intentionally bearer-style random URLs so VLC on
        # iPad/iPhone does not need the Archive X browser session cookie.
        # The individual media URLs inside the playlist remain independently
        # signed and expire on their own.
        playlist_id = str(request.match_info.get("playlist_id") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", playlist_id):
            return web.Response(status=404, text="playlist not found")
        playlist_dir = config.PLAYER_CACHE_DIR / "device_playlists"
        path = playlist_dir / f"{playlist_id}.m3u"
        try:
            if not path.is_file():
                return web.Response(status=404, text="playlist not found")
            # Device playlist links are short-lived. Individual stream tokens
            # are still checked by /video/{msg_id}.
            if time.time() - path.stat().st_mtime > 12 * 3600:
                path.unlink(missing_ok=True)
                return web.Response(status=404, text="playlist expired")
            return web.FileResponse(path, headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Content-Type": "audio/x-mpegurl; charset=utf-8",
                "Content-Disposition": 'inline; filename="archive-x.m3u"',
            })
        except FileNotFoundError:
            return web.Response(status=404, text="playlist not found")
        except Exception as exc:
            _stream_log(f"Device playlist read failed: {type(exc).__name__}: {exc}")
            return web.Response(status=500, text="playlist unavailable")

    local_app.router.add_route("GET", "/video/{msg_id}", local_handle)
    local_app.router.add_route("HEAD", "/video/{msg_id}", local_handle)
    local_app.router.add_route("GET", "/playlist/{playlist_id}.m3u", playlist_handle)
    runner = web.AppRunner(local_app, access_log=None)
    await runner.setup()
    chosen_port = None
    last_error = None
    # Avoid the common Windows EADDRINUSE case caused by a stale process or
    # another local service. Keep the configured port first, then try a small
    # deterministic range so generated LAN URLs remain usable.
    for candidate in range(int(port), int(port) + 21):
        try:
            site = web.TCPSite(runner, host, candidate)
            await site.start()
            chosen_port = candidate
            break
        except OSError as exc:
            last_error = exc
            try:
                await runner.cleanup()
            except Exception:
                pass
            runner = web.AppRunner(local_app, access_log=None)
            await runner.setup()
            continue
    if chosen_port is None:
        _FEED_RUNNING = False
        try:
            await runner.cleanup()
        except Exception:
            pass
        raise OSError(f"Could not bind player feed on {host}:{port}-{int(port)+20}: {last_error}")
    state["player_port"] = int(chosen_port)
    state["player_host"] = host
    if chosen_port != int(port):
        _stream_log(f"Player feed port {port} busy; selected {chosen_port} on {host}", "WARN")
    _stream_log(f"Player feed on http://{host}:{chosen_port}")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        _FEED_RUNNING = False
        await runner.cleanup()
