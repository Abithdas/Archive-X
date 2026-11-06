"""Content Automation state machine for RSS-to-Telegram delivery.

Owns persisted item state, torrent supervision, media preparation, and delivery
ordering. External engines and media operations are delegated to their domain
modules; keep cleanup and per-item transitions in this orchestration layer.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Caption construction and persisted runtime settings
#   - Automation persistence, recovery, and safe cleanup
#   - Status shaping, RSS rules, and local media selection
#   - Destination resolution and child-job coordination
#   - Media preparation and verified Telegram delivery
#   - Torrent recovery, queue reconciliation, and download slots
#   - Download health and per-item state transitions
#   - Single-item processing pipeline
#   - External engine lifecycle and automation supervisor
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import html
import inspect
import time
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .. import config
from ..metadata.fingerprint import oshash_file
from ..engines.telegram_sender import render_caption, DEFAULT_TEMPLATE
from ..engines.media_utils import (
    flood_wait_seconds, verify_destination_pair, is_video_message,
    is_telegram_direct_streamable_meta
)
from ..engines.uploader import ffmpeg_tools, probe, _delete_verified_source_file
from ..jobs.context import JobContext, JobCancelled
from ..media_manager.telegram_media_manager import convert_to_streamable_mp4
from ..core import internal_media
from ..engines.thumbnail_cache import ensure_local_playback_assets, local_playback_snapshot_path
from .torrent_engine import (
    LibtorrentWorkerEngine, QBittorrentEngine, TorrentEngineError, TorrentInfo, VIDEO_EXTENSIONS,
    ARCHIVE_X_QBIT_CATEGORY, LEGACY_ARCHIVE_X_CATEGORY_PREFIX,
)
from .qbittorrent_supervisor import QBittorrentSupervisor
from .rss import RSSItem, fetch_feed

AUTOMATION_JOB_TYPE = "CONTENT_AUTOMATION"
CONFIG_KEY = "content_automation_config"
RUNTIME_KEY = "content_automation_runtime"
# A child row may briefly show RUNNING while a worker is being reconciled. If
# its JobManager task/execution lease never returns, the parent should fail
# with the child ID and last operation instead of waiting forever.
ORCHESTRATED_CHILD_ORPHAN_GRACE_SECONDS = 15.0
# Explicit name keeps source-level diagnostics/tests aligned with the default
# event emitted for the Content Automation parent/child supervisor.
DEFAULT_CHILD_SUPERVISION_ORPHAN_EVENT = "content_automation.child_supervision_orphaned"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "rss_url": "",
    "poll_interval": 120,
    # Manual number of torrent downloads allowed concurrently. The post-download
    # processing/upload pipeline remains single-file to protect CPU, disk I/O,
    # Telegram rate limits, and cleanup ordering.
    "max_concurrent_torrents": 1,
    # V29.4.130 dead/stalled torrent rules (0 turns a rule off): a magnet that
    # never gets its metadata, or a torrent with no download progress for this
    # long, is failed so it stops holding a download slot. A torrent stuck
    # just short of 100% is re-verified once before that.
    "torrent_metadata_timeout_minutes": 30,
    "torrent_stall_timeout_minutes": 120,
    "only_1080p": True,
    "rss_must_contain": "1080p",
    "rss_must_not_contain": "",
    "rss_use_regex": False,
    # Optional RSS enclosure size gate in MiB; 0 disables either bound.
    "rss_min_size_mb": 0,
    "rss_max_size_mb": 0,
    "download_path": str(config.AUTOMATION_DOWNLOAD_DIR),
    "torrent_engine_metadata": str(config.AUTOMATION_ENGINE_DIR),
    "torrent_backend": "qbittorrent",
    "torrent_listen_port": 6881,
    "torrent_enable_dht_upnp": False,
    "torrent_allow_incoming_peers": False,
    "qbittorrent_url": "http://127.0.0.1:8080",
    "qbittorrent_username": "admin",
    "qbittorrent_password": "",
    "qbittorrent_api_key": "",
    "qbittorrent_auto_start": True,
    "qbittorrent_auto_install": True,
    "qbittorrent_auto_configure_local_webui": True,
    "destination_channel": "",
    "remove_torrent_after_delivery": True,
    "delete_local_after_delivery": True,
    "split_upload": False,
    "oversized_file_policy": "optimize",
}

_VIDEO_EXTENSIONS = VIDEO_EXTENSIONS  # kept as an alias: this name is used elsewhere below

# Downloads can run concurrently according to max_concurrent_torrents.
# Only one post-download pipeline stage is advanced at a time so conversion,
# individual snapshot generation, Telegram upload, verification, and cleanup remain
# serialized and predictable.
DOWNLOAD_STATUS = "DOWNLOADING"
PROCESSING_STATUSES = (
    "CONVERTING", "VALIDATING", "METADATA_ASSETS", "READY_TO_TRANSFER",
    "TRANSFERRING_VIDEO", "TRANSFERRING_THUMBNAIL", "VERIFYING", "CLEANUP",
)
ACTIVE_STATUSES = (DOWNLOAD_STATUS, *PROCESSING_STATUSES)
TASK_LABELS = {
    "QUEUED": "Queued -- waiting for its turn",
    "DOWNLOADING": "Downloading torrent",
    "CONVERTING": "Converting to streamable MP4",
    "VALIDATING": "Validating downloaded video",
    "METADATA_ASSETS": "Preparing Metadata & Assets",
    "READY_TO_TRANSFER": "Ready for Telegram Transfer",
    "TRANSFERRING_VIDEO": "Sending video to Telegram",
    "TRANSFERRING_THUMBNAIL": "Sending thumbnail to Telegram",
    "VERIFYING": "Verifying delivery",
    "CLEANUP": "Cleaning up completed download",
    "SKIPPING": "Skipping by operator",
    "COMPLETED": "Completed",
    "FAILED": "Failed",
    "SKIPPED": "Skipped",
}




# ==============================================================================
# SECTION: Caption construction and persisted runtime settings
# Caption building plus runtime and config persistence.
# ==============================================================================

async def build_content_automation_caption(db, video: Path, meta: dict, *, oshash_value: str | None = None,
                                           media_id: int | None = None) -> str:
    """Build captions from the prepared media record, without running identification twice."""
    from ..engines.telegram_sender import build_local_media_caption
    return await build_local_media_caption(
        db, video, meta, oshash_value=oshash_value, media_id=media_id,
        template_setting="send_caption_template",
        default_template=DEFAULT_TEMPLATE, limit=4096,
    )


MAX_RETRY_ATTEMPTS = 4  # total attempts; three persisted retries
RETRY_BACKOFF_SECONDS = (30, 120, 300)

RUNTIME_DEFAULTS: dict[str, Any] = {
    "operator_started": False,
    "paused": False,
}

def get_runtime(db) -> dict[str, Any]:
    raw = db.setting(RUNTIME_KEY, {})
    raw = dict(raw) if isinstance(raw, dict) else {}
    return {
        "operator_started": bool(raw.get("operator_started", False)),
        "paused": bool(raw.get("paused", False)),
    }

def set_runtime(db, *, operator_started: bool | None = None, paused: bool | None = None) -> dict[str, Any]:
    current = get_runtime(db)
    if operator_started is not None:
        current["operator_started"] = bool(operator_started)
    if paused is not None:
        current["paused"] = bool(paused)
    db.set_setting(RUNTIME_KEY, current)
    return current

def disarm_on_boot(db) -> None:
    """Stop Content Automation from auto-starting after an application restart."""
    cfg = get_config(db, masked=False)
    if cfg.get("enabled"):
        cfg["enabled"] = False
        save_config(db, cfg, allow_enable=True)
    set_runtime(db, operator_started=False, paused=False)


def _merge_config(raw: Any) -> dict[str, Any]:
    """Combine saved config with defaults and normalize its values."""
    raw_config = dict(raw) if isinstance(raw, dict) else {}
    out = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        # Whitelist the v18.30 configuration surface. This also removes legacy
        # legacy torrent-client credentials from the effective config when upgrading a
        # v18.28 database.
        out.update({key: raw[key] for key in DEFAULT_CONFIG if key in raw})
    out["poll_interval"] = max(30, min(3600, int(out.get("poll_interval") or 120)))
    # Honor the operator-configured per-category qBittorrent concurrency. The
    # default remains one, but values 2-10 are valid and are enforced through
    # the qBittorrent Web API without changing unrelated torrent categories.
    out["max_concurrent_torrents"] = max(1, min(10, int(out.get("max_concurrent_torrents") or 1)))
    for key, default in (("torrent_metadata_timeout_minutes", 30), ("torrent_stall_timeout_minutes", 120)):
        try:
            value = int(out.get(key, default) if out.get(key, default) is not None else default)
        except (TypeError, ValueError):
            value = default
        out[key] = max(0, min(10080, value))
    out["only_1080p"] = bool(out.get("only_1080p", True))
    out["rss_must_contain"] = str(out.get("rss_must_contain") or "").strip()
    out["rss_must_not_contain"] = str(out.get("rss_must_not_contain") or "").strip()
    out["rss_min_size_mb"] = max(0, min(10240, int(out.get("rss_min_size_mb") or 0)))
    out["rss_max_size_mb"] = max(0, min(10240, int(out.get("rss_max_size_mb") or 0)))
    if out["rss_max_size_mb"] and out["rss_min_size_mb"] > out["rss_max_size_mb"]:
        out["rss_min_size_mb"], out["rss_max_size_mb"] = out["rss_max_size_mb"], out["rss_min_size_mb"]
    out["rss_use_regex"] = bool(out.get("rss_use_regex"))
    for key in ("rss_url", "download_path", "torrent_engine_metadata", "destination_channel"):
        out[key] = str(out.get(key) or "").strip()
    # Archive X uses qBittorrent as its sole supported download backend.
    # Normalize the legacy embedded-engine setting on read so saved v29 configs
    # continue through the supported qBittorrent path after upgrade.
    out["torrent_backend"] = "qbittorrent"
    out["torrent_listen_port"] = max(0, min(65535, int(out.get("torrent_listen_port") or 6881)))
    # qBittorrent authentication lives in the persistent auth/credentials.json
    # sidecar, not in archive.db. Legacy values from the DB remain supported
    # during migration and are moved by save/get_config below.
    for key in ("qbittorrent_url", "qbittorrent_username", "qbittorrent_password", "qbittorrent_api_key"):
        out[key] = str(out.get(key) or "").strip()
    for secret_key in ("qbittorrent_username", "qbittorrent_password", "qbittorrent_api_key"):
        saved = config.get_secret(secret_key, None)
        if saved not in (None, ""):
            out[secret_key] = str(saved).strip()
    out["torrent_enable_dht_upnp"] = bool(out.get("torrent_enable_dht_upnp"))
    out["torrent_allow_incoming_peers"] = bool(out.get("torrent_allow_incoming_peers"))
    out["enabled"] = bool(out.get("enabled"))
    from ..core.upload_limits import normalize_oversized_file_policy
    out["oversized_file_policy"] = normalize_oversized_file_policy(
        raw_config.get("oversized_file_policy"),
        legacy_split_upload=bool(raw_config.get("split_upload", False)),
        default="optimize",
    )
    out["split_upload"] = out["oversized_file_policy"] == "split"
    out["qbittorrent_auto_start"] = bool(out.get("qbittorrent_auto_start", True))
    out["qbittorrent_auto_install"] = bool(out.get("qbittorrent_auto_install", True))
    out["qbittorrent_auto_configure_local_webui"] = bool(out.get("qbittorrent_auto_configure_local_webui", True))
    # Strict queue policy: completed downloads are removed before the next item starts.
    out["remove_torrent_after_delivery"] = True
    out["delete_local_after_delivery"] = True
    return out


def get_config(db, *, masked: bool = True) -> dict[str, Any]:
    raw = db.setting(CONFIG_KEY, {})
    raw = dict(raw) if isinstance(raw, dict) else {}

    # Migrate legacy qBittorrent login fields from SQLite into the persistent
    # auth sidecar. They are then removed from the database-backed config.
    migrated = False
    for secret_key in ("qbittorrent_username", "qbittorrent_password", "qbittorrent_api_key"):
        value = str(raw.get(secret_key) or "").strip()
        if value and config.get_secret(secret_key, None) in (None, ""):
            config.set_secret(secret_key, value)
            migrated = True
        if secret_key in raw:
            raw.pop(secret_key, None)
            migrated = True

    merged = _merge_config(raw)
    if migrated or any(key not in DEFAULT_CONFIG for key in raw):
        # Persist only non-secret configuration.
        clean = {k: v for k, v in merged.items() if k not in ("qbittorrent_username", "qbittorrent_password", "qbittorrent_api_key")}
        db.set_setting(CONFIG_KEY, clean)
    if masked:
        result = dict(merged)
        # Expose only non-secret saved-state metadata to the UI. Never return
        # credential values through the masked configuration endpoint.
        result["qbittorrent_username_saved"] = bool(merged.get("qbittorrent_username"))
        result["qbittorrent_password_saved"] = bool(merged.get("qbittorrent_password"))
        result["qbittorrent_api_key_saved"] = bool(merged.get("qbittorrent_api_key"))
        for secret_key in ("qbittorrent_password", "qbittorrent_api_key"):
            if result.get(secret_key):
                result[secret_key] = "••••••"
        return result
    return merged


def save_config(db, incoming: dict[str, Any], *, allow_enable: bool = False) -> dict[str, Any]:
    current = _merge_config(db.setting(CONFIG_KEY, {}))
    data = dict(incoming or {})
    # A stale browser may still submit the former split-upload checkbox only.
    # Convert that request into an explicit policy before merging with current
    # config, otherwise the current default "optimize" would mask the legacy flag.
    if "oversized_file_policy" not in data and "split_upload" in data:
        data["oversized_file_policy"] = "split" if bool(data.get("split_upload")) else "optimize"

    # Authentication data is intentionally written outside the database.
    # Blank password/key fields mean "keep the existing value".
    for secret_key in ("qbittorrent_username", "qbittorrent_password", "qbittorrent_api_key"):
        if secret_key in data:
            value = str(data.get(secret_key) or "").strip()
            if value and value != "••••••":
                config.set_secret(secret_key, value)
            data.pop(secret_key, None)

    merged = _merge_config({**current, **data})
    if not allow_enable:
        merged["enabled"] = False
    clean = {k: v for k, v in merged.items() if k not in ("qbittorrent_username", "qbittorrent_password", "qbittorrent_api_key")}
    db.set_setting(CONFIG_KEY, clean)
    return get_config(db, masked=True)




# ==============================================================================
# SECTION: Automation persistence, recovery, and safe cleanup
# Schema, recoverable items and owned-folder cleanup.
# ==============================================================================

def _ensure_schema(db) -> None:
    """Create or migrate the automation tables once per database object."""
    # Called before every item update; the table check only needs to run once
    # per process (it used to run a full CREATE/PRAGMA/COMMIT every time).
    if getattr(db, "_automation_schema_ready", False):
        return
    with db.lock:
        db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS automation_items (
            item_key TEXT PRIMARY KEY,
            source_url TEXT NOT NULL,
            guid TEXT,
            title TEXT NOT NULL,
            published_at TEXT,
            torrent_url TEXT,
            torrent_hash TEXT,
            local_path TEXT,
            resolution TEXT,
            download_progress REAL DEFAULT 0,
            download_state TEXT DEFAULT '',
            download_rate INTEGER DEFAULT 0,
            eta_seconds INTEGER DEFAULT -1,
            enclosure_size INTEGER DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            next_retry_at TEXT,
            failure_kind TEXT DEFAULT '',
            retry_stage TEXT DEFAULT '',
            raw_oshash TEXT,
            media_id INTEGER,
            metadata_job_id TEXT,
            transfer_job_id TEXT,
            media_metadata_json TEXT DEFAULT '{}',
            media_metadata_path TEXT DEFAULT '',
            media_metadata_size INTEGER DEFAULT 0,
            media_metadata_mtime_ns INTEGER DEFAULT 0,
            identity_preflight_status TEXT DEFAULT '',
            identity_preflight_filename TEXT DEFAULT '',
            identity_preflight_json TEXT DEFAULT '{}',
            identity_preflight_started_at TEXT,
            identity_preflight_completed_at TEXT,
            speed_check_started_at TEXT DEFAULT '',
            speed_check_completed_at TEXT DEFAULT '',
            speed_check_status TEXT DEFAULT '',
            speed_check_rate_sum REAL DEFAULT 0,
            speed_check_sample_count INTEGER DEFAULT 0,
            speed_check_average_rate INTEGER DEFAULT 0,
            caption TEXT DEFAULT '', 
            snapshot_path TEXT DEFAULT '',
            torrent_content_path TEXT DEFAULT '',
            torrent_state TEXT DEFAULT '',
            torrent_verified_at TEXT,
            source_cleanup_state TEXT DEFAULT 'PENDING',
            content_plan_status TEXT DEFAULT 'DISCOVERED',
            status TEXT NOT NULL,
            destination_channel TEXT,
            destination_video_id INTEGER,
            destination_thumbnail_id INTEGER,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_automation_items_status ON automation_items(status);
        CREATE INDEX IF NOT EXISTS idx_automation_items_torrent ON automation_items(torrent_hash);
        CREATE INDEX IF NOT EXISTS idx_automation_items_created ON automation_items(created_at);
        """)
        existing = {row[1] for row in db.conn.execute("PRAGMA table_info(automation_items)").fetchall()}
        migrations = {
            "download_progress": "ALTER TABLE automation_items ADD COLUMN download_progress REAL DEFAULT 0",
            "download_state": "ALTER TABLE automation_items ADD COLUMN download_state TEXT DEFAULT ''",
            "download_rate": "ALTER TABLE automation_items ADD COLUMN download_rate INTEGER DEFAULT 0",
            "eta_seconds": "ALTER TABLE automation_items ADD COLUMN eta_seconds INTEGER DEFAULT -1",
            "enclosure_size": "ALTER TABLE automation_items ADD COLUMN enclosure_size INTEGER DEFAULT 0",
            "retry_count": "ALTER TABLE automation_items ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0",
            "next_retry_at": "ALTER TABLE automation_items ADD COLUMN next_retry_at TEXT",
            "failure_kind": "ALTER TABLE automation_items ADD COLUMN failure_kind TEXT DEFAULT ''",
            "retry_stage": "ALTER TABLE automation_items ADD COLUMN retry_stage TEXT DEFAULT ''",
            "raw_oshash": "ALTER TABLE automation_items ADD COLUMN raw_oshash TEXT",
            "send_started_at": "ALTER TABLE automation_items ADD COLUMN send_started_at TEXT",
            "media_id": "ALTER TABLE automation_items ADD COLUMN media_id INTEGER",
            "metadata_job_id": "ALTER TABLE automation_items ADD COLUMN metadata_job_id TEXT",
            "transfer_job_id": "ALTER TABLE automation_items ADD COLUMN transfer_job_id TEXT",
            "media_metadata_json": "ALTER TABLE automation_items ADD COLUMN media_metadata_json TEXT DEFAULT '{}'",
            "media_metadata_path": "ALTER TABLE automation_items ADD COLUMN media_metadata_path TEXT DEFAULT ''",
            "media_metadata_size": "ALTER TABLE automation_items ADD COLUMN media_metadata_size INTEGER DEFAULT 0",
            "media_metadata_mtime_ns": "ALTER TABLE automation_items ADD COLUMN media_metadata_mtime_ns INTEGER DEFAULT 0",
            "identity_preflight_status": "ALTER TABLE automation_items ADD COLUMN identity_preflight_status TEXT DEFAULT ''",
            "identity_preflight_filename": "ALTER TABLE automation_items ADD COLUMN identity_preflight_filename TEXT DEFAULT ''",
            "identity_preflight_json": "ALTER TABLE automation_items ADD COLUMN identity_preflight_json TEXT DEFAULT '{}'",
            "identity_preflight_started_at": "ALTER TABLE automation_items ADD COLUMN identity_preflight_started_at TEXT",
            "identity_preflight_completed_at": "ALTER TABLE automation_items ADD COLUMN identity_preflight_completed_at TEXT",
            "speed_check_started_at": "ALTER TABLE automation_items ADD COLUMN speed_check_started_at TEXT DEFAULT ''",
            "speed_check_completed_at": "ALTER TABLE automation_items ADD COLUMN speed_check_completed_at TEXT DEFAULT ''",
            "speed_check_status": "ALTER TABLE automation_items ADD COLUMN speed_check_status TEXT DEFAULT ''",
            "speed_check_rate_sum": "ALTER TABLE automation_items ADD COLUMN speed_check_rate_sum REAL DEFAULT 0",
            "speed_check_sample_count": "ALTER TABLE automation_items ADD COLUMN speed_check_sample_count INTEGER DEFAULT 0",
            "speed_check_average_rate": "ALTER TABLE automation_items ADD COLUMN speed_check_average_rate INTEGER DEFAULT 0",
            "caption": "ALTER TABLE automation_items ADD COLUMN caption TEXT DEFAULT ''",
            "snapshot_path": "ALTER TABLE automation_items ADD COLUMN snapshot_path TEXT DEFAULT ''",
            "torrent_content_path": "ALTER TABLE automation_items ADD COLUMN torrent_content_path TEXT DEFAULT ''",
            "torrent_state": "ALTER TABLE automation_items ADD COLUMN torrent_state TEXT DEFAULT ''",
            "torrent_verified_at": "ALTER TABLE automation_items ADD COLUMN torrent_verified_at TEXT",
            "source_cleanup_state": "ALTER TABLE automation_items ADD COLUMN source_cleanup_state TEXT DEFAULT 'PENDING'",
            "content_plan_status": "ALTER TABLE automation_items ADD COLUMN content_plan_status TEXT DEFAULT 'DISCOVERED'",
        }
        for column, statement in migrations.items():
            if column not in existing:
                db.conn.execute(statement)
        # V29.4.91 canonical Content Automation lifecycle names.
        db.conn.execute("UPDATE automation_items SET status='READY_TO_TRANSFER' WHERE status='READY_TO_SEND'")
        db.conn.execute("UPDATE automation_items SET status='TRANSFERRING_VIDEO' WHERE status='SENDING_VIDEO'")
        db.conn.execute("UPDATE automation_items SET status='TRANSFERRING_THUMBNAIL' WHERE status='SENDING_THUMBNAIL'")
        db.conn.execute("CREATE INDEX IF NOT EXISTS idx_automation_items_retry ON automation_items(status, next_retry_at)")
        db.conn.commit()
    try:
        db._automation_schema_ready = True
    except Exception:
        pass


def _failure_error(error: str, retryable: bool, retry_count: int, delay: int | None = None) -> str:
    base = str(error or "Unknown failure").strip()
    if retryable and retry_count < MAX_RETRY_ATTEMPTS:
        shown_delay = int(delay or RETRY_BACKOFF_SECONDS[min(retry_count - 1, len(RETRY_BACKOFF_SECONDS) - 1)])
        return f"Retry scheduled ({retry_count}/{MAX_RETRY_ATTEMPTS}) in {shown_delay}s: {base}"
    if retryable:
        return f"Retry limit reached ({MAX_RETRY_ATTEMPTS}/{MAX_RETRY_ATTEMPTS}): {base}"
    return base


def _fail_item(db, item_key: str, error: str, *, retryable: bool, retry_stage: str = "",
               retry_after_seconds: int | None = None, consume_retry_budget: bool = True) -> str:
    """Persist a failure and resume transient work from its failed stage."""
    _ensure_schema(db)
    now = config.now_iso()
    with db.lock:
        row = db.conn.execute(
            "SELECT retry_count, status, retry_stage FROM automation_items WHERE item_key=?", (item_key,)
        ).fetchone()
        # An operator skip is a terminal decision. A stale in-flight download
        # poll must not turn it back into QUEUED/FAILED while torrent cleanup is
        # running or after the skip has completed.
        if row and str(row["status"] or "").upper() in {"SKIPPING", "SKIPPED"}:
            return "Skipped by operator"
        current = int((row["retry_count"] if row else 0) or 0)
        current_stage = str((row["status"] if row else "") or "")
        stage = str(retry_stage or (current_stage if current_stage in ACTIVE_STATUSES else "DOWNLOADING"))
        next_count = current + 1 if (retryable and consume_retry_budget) else current
        # Long FloodWaits are server-directed delays, not application failures.
        # Do not burn the persisted retry budget while Telegram is rate-limiting.
        should_retry = retryable and (not consume_retry_budget or next_count < MAX_RETRY_ATTEMPTS)
        if should_retry:
            delay = max(1, int(retry_after_seconds or 0)) if retry_after_seconds else RETRY_BACKOFF_SECONDS[min(max(next_count - 1, 0), len(RETRY_BACKOFF_SECONDS) - 1)]
            next_retry = config.iso_after_seconds(delay)
            message = (f"Retry scheduled (FloodWait; retry budget preserved) in {delay}s: {error}" if not consume_retry_budget else _failure_error(error, True, next_count, delay))
            db.conn.execute(
                "UPDATE automation_items SET status='QUEUED', retry_count=?, next_retry_at=?, failure_kind='transient', retry_stage=?, error=?, download_state=?, updated_at=? WHERE item_key=?",
                (next_count, next_retry, stage, message, f"Retry scheduled · stage {stage} · attempt {next_count}/{MAX_RETRY_ATTEMPTS}", now, item_key),
            )
        else:
            final_count = next_count if retryable else current
            message = _failure_error(error, retryable, final_count)
            kind = "transient-exhausted" if retryable else "permanent"
            db.conn.execute(
                "UPDATE automation_items SET status='FAILED', retry_count=?, next_retry_at=NULL, failure_kind=?, retry_stage='', error=?, download_state=?, updated_at=? WHERE item_key=?",
                (final_count, kind, message, TASK_LABELS["FAILED"], now, item_key),
            )
        db.conn.commit()
        title_row = db.conn.execute("SELECT title FROM automation_items WHERE item_key=?", (item_key,)).fetchone()
    # Failures at the upload/convert steps used to be stored on the item only,
    # so Recent activity showed nothing while delivery kept failing.
    try:
        title = str((title_row["title"] if title_row else "") or item_key)
        db.log(f"Content Automation: {title}: {message}", "WARN" if should_retry else "ERROR")
    except Exception:
        pass
    return message


def requeue_recoverable_items(db) -> dict[str, int]:
    """Give releases that were lost to known bugs another chance.

    * FAILED because the destination channel could not be resolved, or
      because external ffprobe was demanded: resume from the upload/convert
      step (the downloaded file is reused; missing files are re-fetched from
      the torrent automatically).
    * SKIPPED as "Not 1080p" by the old after-download resolution check:
      queue them to download again (their files were deleted at the time).
    """
    _ensure_schema(db)
    now = config.now_iso()
    out = {"delivery": 0, "redownload": 0, "watchdog_delivery": 0}
    with db.lock:
        rows = db.conn.execute(
            "SELECT item_key, error, resolution, local_path, torrent_hash, destination_video_id, failure_kind "
            "FROM automation_items "
            "WHERE (status='FAILED' AND (error LIKE '%Cannot find any entity%' OR error LIKE '%ffprobe is required%')) "
            "OR (status='SKIPPED' AND error LIKE 'Not 1080p:%') "
            "OR (status='FAILED' AND failure_kind='transient-exhausted' "
            "AND COALESCE(torrent_hash,'')!='' AND destination_video_id IS NULL) "
            # V29.4.221: releases that failed only because of the v29.4.220
            # local preparation code errors resume from conversion with the
            # already downloaded file (no re-download).
            "OR (status='FAILED' AND destination_video_id IS NULL AND ("
            "error LIKE '%is not defined%' OR error LIKE '%cannot import name%' "
            "OR error LIKE '%media preparation failed%' OR error LIKE '%not Library-ready%'))").fetchall()
        for r in rows:
            error = str(r["error"] or "")
            lowered_error = error.casefold()
            if r["destination_video_id"] is None and "not library-ready" in lowered_error and str(r["local_path"] or "").strip():
                # V29.4.223: held only by the local readiness bookkeeping bug;
                # the converted file and its 36 snapshots are reused.
                db.conn.execute(
                    "UPDATE automation_items SET status='QUEUED', retry_count=0, retry_stage='METADATA_ASSETS', next_retry_at='', "
                    "failure_kind='', error='', download_state=?, updated_at=? WHERE item_key=?",
                    ("Retrying after Archive X fix (stage METADATA_ASSETS; converted file and snapshots reused)", now, r["item_key"]))
                out["delivery"] += 1
            elif r["destination_video_id"] is None and (
                    "is not defined" in lowered_error or "cannot import name" in lowered_error
                    or "media preparation failed" in lowered_error):
                stage = "CONVERTING" if str(r["local_path"] or "").strip() else "DOWNLOADING"
                db.conn.execute(
                    "UPDATE automation_items SET status='QUEUED', retry_count=0, retry_stage=?, next_retry_at='', "
                    "failure_kind='', error='', download_state=?, updated_at=? WHERE item_key=?",
                    (stage, f"Retrying after Archive X fix (stage {stage}; downloaded file reused)", now, r["item_key"]))
                out["delivery"] += 1
            elif error.startswith("Not 1080p:"):
                # Deleted after download because of the old resolution check.
                db.conn.execute(
                    "UPDATE automation_items SET status='QUEUED', retry_count=0, retry_stage='', next_retry_at='', "
                    "failure_kind='', torrent_hash='', local_path='', error='', download_progress=0, "
                    "download_state=?, updated_at=? WHERE item_key=?",
                    (TASK_LABELS["QUEUED"], now, r["item_key"]))
                out["redownload"] += 1
            elif (str(r["failure_kind"] or "") == "transient-exhausted" and str(r["torrent_hash"] or "").strip()
                  and r["destination_video_id"] is None):
                # A watchdog-cancelled transfer still owns an existing downloaded
                # release. Resume at the saved delivery stage instead of allowing a
                # newer RSS torrent to overtake it. This repair happens only on an
                # explicit Start, so a repeat failure cannot create an infinite loop.
                lowered = error.casefold()
                if any(token in lowered for token in ("telegram transfer", "heartbeat_lag", "watchdog", "cancelled", "jobcancelled")):
                    stage = "TRANSFERRING_VIDEO"
                elif any(token in lowered for token in ("metadata & assets", "snapshot", "metadata_assets")):
                    stage = "METADATA_ASSETS"
                elif str(r["local_path"] or "").strip():
                    stage = "READY_TO_TRANSFER"
                else:
                    stage = "DOWNLOADING"
                db.conn.execute(
                    "UPDATE automation_items SET status='QUEUED', retry_count=0, retry_stage=?, next_retry_at='', "
                    "failure_kind='', error='', download_state=?, updated_at=? WHERE item_key=?",
                    (stage, f"Retrying existing undelivered torrent (stage {stage})", now, r["item_key"]))
                out["watchdog_delivery"] += 1
            else:
                stage = "CONVERTING" if "ffprobe" in error else "READY_TO_TRANSFER"
                db.conn.execute(
                    "UPDATE automation_items SET status='QUEUED', retry_count=0, retry_stage=?, next_retry_at='', "
                    "failure_kind='', error='', download_state=?, updated_at=? WHERE item_key=?",
                    (stage, f"Retrying delivery (stage {stage})", now, r["item_key"]))
                out["delivery"] += 1
        db.conn.commit()
    if out["delivery"] or out["redownload"] or out["watchdog_delivery"]:
        db.log(f"Content Automation: re-queued {out['delivery']} release(s) for delivery, "
               f"{out['watchdog_delivery']} exhausted-but-undelivered torrent(s) from their saved stage, and "
               f"{out['redownload']} release(s) removed by the old resolution check for download again", "INFO")
    return out



def _flood_wait_delay(exc: BaseException) -> int:
    wait = flood_wait_seconds(exc)
    return int(wait) + 1 if wait > 0 else 0

def _rss_size_allowed(size_bytes: int, cfg: dict[str, Any]) -> tuple[bool, str]:
    size = int(size_bytes or 0)
    if size <= 0:
        return True, ""
    size_mb = size / (1024 * 1024)
    min_mb = int(cfg.get("rss_min_size_mb") or 0)
    max_mb = int(cfg.get("rss_max_size_mb") or 0)
    if min_mb and size_mb < min_mb:
        return False, f"RSS size rejected: {size_mb:.1f} MiB below minimum {min_mb} MiB"
    if max_mb and size_mb > max_mb:
        return False, f"RSS size rejected: {size_mb:.1f} MiB above maximum {max_mb} MiB"
    return True, ""


def _row_to_dict(row) -> dict[str, Any]:
    return dict(row) if row else {}


AUTOMATION_ROOT_MARKER = ".archivex-owned-root.json"

def _ensure_owned_root_marker(path_value: str | Path, *, label: str) -> Path:
    root = Path(path_value).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    marker = root / AUTOMATION_ROOT_MARKER
    expected = {"label": str(label), "path": str(root)}
    if marker.exists():
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid Archive X ownership marker in {root}: {exc}") from exc
        if str(saved.get("path") or "") != str(root) or str(saved.get("label") or "") != str(label):
            raise ValueError(f"Archive X ownership marker does not match the configured {label} directory: {root}")
    else:
        marker.write_text(json.dumps({**expected, "version": 1, "created_at": config.now_iso()}, indent=2), encoding="utf-8")
    return root

def _safe_clear_root(path_value: str | Path, *, label: str) -> Path:
    """Return a safe automation-owned directory for destructive clearing.

    Content Automation is allowed to clear its configured download/cache roots,
    but never a filesystem root, the user's home directory, or Archive X's
    entire persistent data directory.
    """
    raw = Path(path_value).expanduser()
    root = raw.resolve()
    protected = {
        Path(root.anchor).resolve(),
        Path.home().resolve(),
        config.USER_DATA_DIR.resolve(),
    }
    if root in protected:
        raise ValueError(f"Refusing to clear unsafe {label} directory: {root}")
    app_owned = {config.AUTOMATION_DIR.resolve(), config.AUTOMATION_DOWNLOAD_DIR.resolve(), config.AUTOMATION_ENGINE_DIR.resolve()}
    try:
        is_app_owned = root in app_owned or root.is_relative_to(config.AUTOMATION_DIR.resolve())
    except AttributeError:
        prefix = str(config.AUTOMATION_DIR.resolve()).casefold().rstrip("\\/") + "\\"
        is_app_owned = root in app_owned or str(root).casefold().startswith(prefix)
    marker = root / AUTOMATION_ROOT_MARKER
    if not is_app_owned:
        if not marker.is_file():
            raise ValueError(
                f"Refusing to clear {label} directory without an Archive X ownership marker: {root}. "
                "Start Content Automation once with this folder so Archive X can mark it as app-owned."
            )
        try:
            saved = json.loads(marker.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"Invalid Archive X ownership marker in {root}: {exc}") from exc
        if str(saved.get("path") or "") != str(root) or str(saved.get("label") or "") != str(label):
            raise ValueError(f"Archive X ownership marker does not match the configured {label} directory: {root}")
    return root


def _clear_directory_contents(root: Path) -> int:
    """Delete all files/directories directly under an automation-owned root."""
    root.mkdir(parents=True, exist_ok=True)
    removed = 0
    for child in list(root.iterdir()):
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except FileNotFoundError:
            continue
    return removed


def _clear_tracked_automation_paths(root: Path, paths: list[str]) -> int:
    """Safely remove only files/directories previously recorded as Automation-owned.

    This is used for custom download folders. It intentionally leaves unrelated
    files in that folder untouched even when Clear All is requested.
    """
    root = root.resolve(strict=False)
    removed = 0
    seen: set[Path] = set()
    for raw in paths:
        if not raw:
            continue
        target = Path(str(raw)).expanduser().resolve(strict=False)
        try:
            inside = target == root or target.is_relative_to(root)
        except AttributeError:
            inside = target == root or str(target).casefold().startswith(str(root).casefold().rstrip("\\/") + "\\")
        if not inside or target == root or target in seen:
            continue
        seen.add(target)
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            continue
        # Prune empty Archive X-created parent directories, but never remove the
        # configured root itself or a directory containing unrelated files.
        parent = target.parent
        while parent != root:
            try:
                if any(parent.iterdir()):
                    break
                parent.rmdir()
                removed += 1
            except OSError:
                break
            parent = parent.parent
    return removed


def _is_default_automation_download_root(root: Path) -> bool:
    root = root.resolve(strict=False)
    app_roots = {config.AUTOMATION_DIR.resolve(), config.AUTOMATION_DOWNLOAD_DIR.resolve()}
    if root in app_roots:
        return True
    try:
        return root.is_relative_to(config.AUTOMATION_DIR.resolve())
    except AttributeError:
        return str(root).casefold().startswith(str(config.AUTOMATION_DIR.resolve()).casefold().rstrip("\\/") + "\\")


async def clear_all_state(db) -> dict[str, int]:
    """Clear all Content Automation queue/cache state and its visible activity.

    This deliberately does not touch Telegram library caches, metadata caches,
    or activity generated by other modules. qBittorrent automation torrents are
    removed first so their managed files cannot survive the database cleanup.
    """
    _ensure_schema(db)
    cfg = get_config(db, masked=False)
    result = {
        "queue_items": 0,
        "torrents": 0,
        "download_entries": 0,
        "engine_entries": 0,
        "jobs": 0,
        "events": 0,
    }

    # Prevent a fresh Start from using the old runtime gate while the cleanup
    # is in progress. The route also cancels/waits for any live job first.
    set_runtime(db, operator_started=False, paused=False)
    try:
        cfg = dict(cfg)
        cfg["enabled"] = False
        await asyncio.to_thread(save_config, db, cfg, allow_enable=True)
    except Exception as exc:
        await asyncio.to_thread(
            db.log,
            f"Content Automation Clear All could not disable automation before cleanup: {type(exc).__name__}: {exc}",
            "ERROR",
        )
        raise

    backend = str(cfg.get("torrent_backend") or "qbittorrent").lower()
    if backend == "qbittorrent":
        engine = None
        try:
            engine = QBittorrentEngine(
                base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
                username=cfg.get("qbittorrent_username") or "",
                password=cfg.get("qbittorrent_password") or "",
                api_key=cfg.get("qbittorrent_api_key") or "",
                auto_start=False,
                auto_install=False,
                auto_configure_local_webui=False,
            )
            result["torrents"] = int(await engine.remove_all_automation_torrents(delete_files=True))
        except Exception as exc:
            raise RuntimeError(f"qBittorrent automation cleanup failed: {type(exc).__name__}: {exc}") from exc
        finally:
            if engine is not None:
                try:
                    await engine.close()
                except Exception:
                    pass

    # Clear the Content Automation download tree safely. The default Archive X
    # automation tree is exclusively owned by the application and can be emptied.
    # A custom root is treated as potentially shared: remove only paths recorded
    # in automation_items, leaving unrelated operator files untouched.
    configured_download = Path(cfg.get("download_path") or config.AUTOMATION_DOWNLOAD_DIR).expanduser().resolve(strict=False)
    download_root = _safe_clear_root(configured_download, label="download")
    with db.lock:
        tracked_download_paths = [
            str(row["local_path"]) for row in db.conn.execute(
                "SELECT local_path FROM automation_items WHERE local_path IS NOT NULL AND TRIM(local_path) <> ''"
            ).fetchall() if row and row["local_path"]
        ]
    if _is_default_automation_download_root(download_root):
        result["download_entries"] = _clear_directory_contents(download_root)
    else:
        result["download_entries"] = _clear_tracked_automation_paths(download_root, tracked_download_paths)
        ctx_hint = "custom download folder treated as shared; only Archive X-tracked files were removed"
        await asyncio.to_thread(db.log, f"Content Automation: Clear All — {ctx_hint}", "INFO")

    # The embedded engine keeps torrent metadata/resume files here. Clear the
    # directory even when qBittorrent is selected so switching backends starts
    # from a genuinely clean automation state.
    engine_root = _safe_clear_root(
        cfg.get("torrent_engine_metadata") or config.AUTOMATION_ENGINE_DIR,
        label="torrent-engine",
    )
    result["engine_entries"] = _clear_directory_contents(engine_root)

    with db.lock:
        result["queue_items"] = int(db.conn.execute("SELECT COUNT(*) c FROM automation_items").fetchone()["c"] or 0)
        job_rows = db.conn.execute(
            "SELECT id FROM jobs WHERE job_type=?", (AUTOMATION_JOB_TYPE,)
        ).fetchall()
        job_ids = [str(r["id"]) for r in job_rows]
        result["jobs"] = len(job_ids)

        # Remove automation queue/idempotency state.
        db.conn.execute("DELETE FROM automation_items")

        # Remove automation-only legacy job-state settings and the canonical
        # durable checkpoints for these jobs.
        for jid in job_ids:
            db.conn.execute("DELETE FROM settings WHERE key=?", (f"job_state:{jid}",))
            db.conn.execute("DELETE FROM job_checkpoints WHERE job_id=?", (jid,))
        db.conn.execute("DELETE FROM jobs WHERE job_type=?", (AUTOMATION_JOB_TYPE,))

        # The Operations page uses one shared events table. Remove only entries
        # authored by Content Automation; other module history remains intact.
        cur = db.conn.execute(
            "DELETE FROM events WHERE message LIKE ? OR message LIKE ?",
            ("Content Automation%", "%Content Automation:%"),
        )
        result["events"] = int(cur.rowcount if cur.rowcount is not None else 0)
        db.conn.commit()

    return result




# ==============================================================================
# SECTION: Status shaping, RSS rules, and local media selection
# Status output, rule matching and video selection.
# ==============================================================================

def list_status(db, limit: int = 30, active_jobs=None) -> dict[str, Any]:
    """Return queue items and runtime state for the status API."""
    _ensure_schema(db)
    with db.lock:
        rows = db.conn.execute(
            "SELECT * FROM automation_items ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),)
        ).fetchall()
    counts = {}
    with db.lock:
        for r in db.conn.execute("SELECT status, COUNT(*) c FROM automation_items GROUP BY status").fetchall():
            counts[str(r["status"])] = int(r["c"])

    # Parent job survives an application restart as PAUSED. Expose it explicitly
    # so Queue & activity can offer Resume / Cancel instead of silently starting it.
    restart_pending = None
    try:
        jobs = active_jobs if active_jobs is not None else db.active_jobs(AUTOMATION_JOB_TYPE)
        if active_jobs is not None:
            jobs = [job for job in jobs if str(job.get("job_type") or "") == AUTOMATION_JOB_TYPE]
        for job in jobs:
            op = str(job.get("current_operation") or "")
            if job.get("status") == "PAUSED" and "interrupted by restart" in op.lower():
                restart_pending = {
                    "id": str(job.get("id") or ""),
                    "status": str(job.get("status") or "PAUSED"),
                    "operation": op,
                    "updated_at": str(job.get("updated_at") or ""),
                    "created_at": str(job.get("created_at") or ""),
                }
                break
    except Exception:
        restart_pending = None

    items = []
    for r in rows:
        item = _row_to_dict(r)
        item["task_label"] = TASK_LABELS.get(str(item.get("status") or ""), str(item.get("status") or ""))
        items.append(item)
    return {
        "counts": counts,
        "items": items,
        "runtime": get_runtime(db),
        "restart_pending": restart_pending,
        "workflow": ["TORRENT_VERIFY", "INVENTORY", "METADATA_MATCH", "PERFORMER_DATA", "CHANNEL_DATA", "SNAPSHOTS", "DB_COMMIT", "CONTENT_PLAN", "TELEGRAM_TRANSFER", "VERIFY", "TORRENT_REMOVE", "SOURCE_DELETE"],
        "preparation_before_delivery": True,
        "cleanup_after_verified_delivery": True,
    }


def _upsert(db, item_key: str, **fields):
    _ensure_schema(db)
    now = config.now_iso()
    with db.lock:
        row = db.conn.execute("SELECT item_key,status FROM automation_items WHERE item_key=?", (item_key,)).fetchone()
        if row:
            # Queue polling uses a snapshot of the row and may finish after an
            # operator has clicked Skip. Protect the durable skip decision from
            # stale status transitions such as DOWNLOADING -> VALIDATING.
            if str(row["status"] or "").upper() in {"SKIPPING", "SKIPPED"}:
                requested_status = str(fields.get("status") or "").upper()
                if requested_status and requested_status not in {"SKIPPING", "SKIPPED"}:
                    fields.pop("status", None)
                if not fields:
                    return
            assignments = ", ".join(f"{k}=?" for k in fields)
            values = list(fields.values()) + [now, item_key]
            db.conn.execute(f"UPDATE automation_items SET {assignments}, updated_at=? WHERE item_key=?", values)
        else:
            fields = {"item_key": item_key, "created_at": now, "updated_at": now, **fields}
            columns = ", ".join(fields)
            placeholders = ", ".join("?" for _ in fields)
            db.conn.execute(f"INSERT INTO automation_items({columns}) VALUES({placeholders})", list(fields.values()))
        db.conn.commit()


def _item_skip_requested(db, item_key: str) -> bool:
    """Return True while an operator skip is being cleaned up or is complete."""
    with db.lock:
        row = db.conn.execute(
            "SELECT status FROM automation_items WHERE item_key=?", (item_key,)
        ).fetchone()
    return bool(row and str(row["status"] or "").upper() in {"SKIPPING", "SKIPPED"})


def _get(db, item_key: str) -> dict[str, Any] | None:
    _ensure_schema(db)
    with db.lock:
        row = db.conn.execute("SELECT * FROM automation_items WHERE item_key=?", (item_key,)).fetchone()
    return _row_to_dict(row) if row else None


def _split_rule_terms(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\n]+", str(value or "")) if part.strip()]


def _rss_rule_match(title: str, torrent_url: str, cfg: dict[str, Any]) -> tuple[bool, str]:
    """RSS filename/article filtering before adding a torrent.

    Must-contain terms are ANDed; any must-not-contain term rejects the item.
    With regex mode enabled each term is treated as a case-insensitive regex.
    The check uses the RSS title plus the torrent URL basename so releases are
    filtered before a .torrent file is downloaded or a magnet is added.
    """
    target = f"{title or ''}\n{torrent_url or ''}"
    use_regex = bool(cfg.get("rss_use_regex"))
    flags = re.IGNORECASE
    # "1080p only" is a NAME filter applied before anything is downloaded:
    # the release title (or torrent link) must contain "1080p". The video's
    # real resolution is never checked afterwards.
    if cfg.get("only_1080p") and "1080p" not in target.casefold():
        return False, "1080p only: '1080p' is not in the release name"
    required = _split_rule_terms(cfg.get("rss_must_contain", ""))
    blocked = _split_rule_terms(cfg.get("rss_must_not_contain", ""))

    if use_regex:
        for term in required:
            try:
                matched = re.search(term, target, flags) is not None
            except re.error as exc:
                return False, f"Invalid required RSS regex '{term}': {exc}"
            if not matched:
                return False, f"RSS rule rejected item: must contain /{term}/"
        for term in blocked:
            try:
                matched = re.search(term, target, flags) is not None
            except re.error as exc:
                return False, f"Invalid excluded RSS regex '{term}': {exc}"
            if matched:
                return False, f"RSS rule rejected item: must not contain /{term}/"
        return True, ""

    folded = target.casefold()
    for term in required:
        if term.casefold() not in folded:
            return False, f"RSS rule rejected item: must contain '{term}'"
    for term in blocked:
        if term.casefold() in folded:
            return False, f"RSS rule rejected item: must not contain '{term}'"
    return True, ""


def _is_allowed_rss_item(item: RSSItem, cfg: dict[str, Any]) -> tuple[bool, str]:
    allowed, reason = _rss_rule_match(item.title, item.torrent_url, cfg)
    if not allowed:
        return False, reason
    return _rss_size_allowed(getattr(item, "enclosure_size", 0), cfg)


def _safe_dest_name(path: Path) -> str:
    return path.name[:240]


def _torrent_content_verified(torrent: TorrentInfo, files: list[dict[str, Any]]) -> bool:
    """Accept a torrent only after client state and actual file completion agree."""
    state = str(getattr(torrent, "state", "") or "").casefold()
    if state in {"error", "checking", "checking_files", "checking_resume_data", "downloading", "downloading_metadata", "allocating", "moving", "stalled"}:
        return False
    # V29.4.130: only the files chosen for download count; a skipped sample
    # stays at 0% and must not keep a finished torrent "verifying" forever.
    from .torrent_engine import choose_video_files
    wanted = set(choose_video_files(
        [(r.get("index", i), r.get("name"), r.get("size") or 0) for i, r in enumerate(files)]))
    video_rows = [r for i, r in enumerate(files) if int(r.get("index", i)) in wanted]
    if not video_rows:
        return False
    if float(getattr(torrent, "progress", 0.0) or 0.0) >= 0.9999:
        # 100% is necessary but not sufficient: verify every video file's
        # qBittorrent/libtorrent file progress and the actual filesystem path.
        return all(float(r.get("progress") or 0.0) >= 0.9999 for r in video_rows)
    if state in {"finished", "seeding", "uploading", "queuedup", "paused", "stopped"}:
        return all(float(r.get("progress") or 0.0) >= 0.9999 for r in video_rows)
    return False


def _pick_local_video(torrent: TorrentInfo, files: list[dict[str, Any]], download_root: Path) -> Path | None:
    candidates: list[tuple[int, Path]] = []
    content = Path(torrent.content_path) if torrent.content_path else None
    save = Path(torrent.save_path or download_root)
    roots = []
    if content:
        roots.append(content)
    roots.append(save)
    for f in files:
        name = str(f.get("name") or "")
        suffix = Path(name).suffix.lower()
        if suffix not in _VIDEO_EXTENSIONS:
            continue
        size = int(f.get("size") or 0)
        relative = Path(name)
        for root in roots:
            candidate = root / relative
            if candidate.is_file():
                candidates.append((size or candidate.stat().st_size, candidate))
                break
    if candidates:
        return max(candidates, key=lambda x: x[0])[1]
    for root in roots:
        if root.is_file() and root.suffix.lower() in _VIDEO_EXTENSIONS:
            return root
        if root.is_dir():
            found = [p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS]
            if found:
                return max(found, key=lambda p: p.stat().st_size)
    return None



async def _restore_download_for_item(ctx: JobContext, engine, row: dict[str, Any], root: Path, max_downloads: int = 1) -> Path | None:
    """Recover a missing local source file from the persisted torrent state.

    The automation database is the source of truth for the item. If a post-download
    stage wakes up and ``local_path`` is gone, first try to locate the same torrent
    in the download engine. If it is missing, re-add the persisted torrent source
    into the configured download root so qBittorrent/libtorrent can reuse any
    existing pieces on disk. The item is moved back to DOWNLOADING unless a
    complete video file is immediately discoverable.
    """
    item_key = str(row.get("item_key") or "")
    torrent_hash = str(row.get("torrent_hash") or "").strip()
    torrent = None

    if torrent_hash:
        try:
            torrent = await _torrent_get_with_recovery(ctx, engine, row, root, torrent_hash)
        except Exception as exc:
            ctx.db.log(
                f"Content Automation: could not inspect torrent {torrent_hash} while recovering "
                f"missing source for {row.get('title')}: {type(exc).__name__}: {exc}",
                "WARN",
            )

    if torrent is None and row.get("torrent_url"):
        source = str(row.get("torrent_url") or "").strip()
        try:
            restored_hash = await engine.add(source, save_path=str(root))
            if not restored_hash:
                raise TorrentEngineError("Torrent restore returned no hash")
            torrent_hash = restored_hash
            _upsert(ctx.db, item_key, torrent_hash=restored_hash, error="")
            torrent = await engine.get(restored_hash)
            ctx.db.log(
                f"Content Automation: restored missing-download torrent for {row.get('title')} "
                f"hash={restored_hash}",
                "INFO",
            )
        except Exception as exc:
            flood_delay = _flood_wait_delay(exc)
            _fail_item(
                ctx.db,
                item_key,
                f"Could not restore missing download: {exc}",
                retryable=True,
                retry_stage="DOWNLOADING",
                retry_after_seconds=flood_delay,
                consume_retry_budget=(flood_delay <= 90),
            )
            return None

    if torrent is None:
        _fail_item(
            ctx.db,
            item_key,
            "Source file is missing and the torrent is no longer available; no torrent URL is stored for recovery",
            retryable=False,
            retry_stage="DOWNLOADING",
        )
        return None

    # A restored torrent is deliberately added paused. Before starting it, honor
    # the same category-scoped qBittorrent concurrency limit used by the main
    # dispatcher. This prevents recovery from bypassing max_concurrent_torrents.
    prep: dict[str, Any] = {}
    try:
        inspector = getattr(engine, "list_active_automation_torrents", None)
        if inspector is not None:
            active = await inspector()
            active_hashes = {str(x.get("hash") or "").strip().lower() for x in active}
            if torrent.hash.lower() not in active_hashes and len(active_hashes) >= max_downloads:
                _upsert(
                    ctx.db, item_key, status="QUEUED", retry_stage="DOWNLOADING",
                    next_retry_at="", failure_kind="", error="",
                    download_state=TASK_LABELS["QUEUED"],
                )
                ctx.db.log(
                    f"Content Automation: restored torrent {torrent.hash} is waiting for a download slot "
                    f"({len(active_hashes)}/{max_downloads} active)", "INFO"
                )
                return None
        prepared = engine.prepare_download(torrent.hash)
        if inspect.isawaitable(prepared):
            prepared = await prepared
        prep = prepared or {}
    except Exception as exc:
        flood_delay = _flood_wait_delay(exc)
        _fail_item(
            ctx.db,
            item_key,
            f"Recovered torrent but could not resume download: {exc}",
            retryable=True,
            retry_stage="DOWNLOADING",
            retry_after_seconds=flood_delay,
            consume_retry_budget=(flood_delay <= 90),
        )
        return None

    try:
        refreshed = await engine.get(torrent.hash)
        if refreshed is not None:
            torrent = refreshed
    except Exception:
        pass

    if getattr(torrent, "has_metadata", True):
        try:
            await engine.select_video_only(torrent.hash)
            prepared = engine.prepare_download(torrent.hash)
            if inspect.isawaitable(prepared):
                prepared = await prepared
            prep = prepared or prep
            refreshed = await engine.get(torrent.hash)
            if refreshed is not None:
                torrent = refreshed
        except Exception as exc:
            flood_delay = _flood_wait_delay(exc)
            _fail_item(
                ctx.db,
                item_key,
                f"Recovered torrent but could not select/resume video files: {exc}",
                retryable=True,
                retry_stage="DOWNLOADING",
                retry_after_seconds=flood_delay,
                consume_retry_budget=(flood_delay <= 90),
            )
            return None

    files: list[dict[str, Any]] = []
    try:
        files = await engine.files(torrent.hash)
    except Exception:
        files = []

    video_path = _pick_local_video(torrent, files, root)
    progress = float(getattr(torrent, "progress", 0.0) or 0.0)
    if video_path and video_path.exists() and progress >= 0.9999:
        _upsert(
            ctx.db,
            item_key,
            status="VALIDATING",
            local_path=str(video_path),
            download_progress=1.0,
            download_state=TASK_LABELS["VALIDATING"],
            error="",
            failure_kind="",
            retry_stage="",
            next_retry_at="",
        )
        ctx.db.log(
            f"Content Automation: recovered completed source for {row.get('title')}: {video_path}",
            "INFO",
        )
        return video_path

    _upsert(
        ctx.db,
        item_key,
        status="DOWNLOADING",
        download_progress=min(1.0, max(0.0, progress)),
        download_state=(TASK_LABELS["DOWNLOADING"] +
                        (f" · {prep.get('detail')}" if prep.get("detail") else " · recovering missing source")),
        download_rate=int(getattr(torrent, "download_rate", 0) or 0),
        eta_seconds=int(getattr(torrent, "eta", -1) or -1),
        error="",
        failure_kind="",
        retry_stage="",
        next_retry_at="",
    )
    ctx.db.log(
        f"Content Automation: missing source restored to DOWNLOADING for {row.get('title')} "
        f"progress={progress:.1%}",
        "INFO",
    )
    return None


def _converted_cache_path(raw_path: Path) -> Path:
    return raw_path.with_name(raw_path.stem + "._telegram_archive_streamable.mp4")


def _media_file_signature(path: Path) -> dict[str, Any]:
    """Return the cheap source signature that makes a saved ffprobe/PyAV result reusable."""
    source = Path(path).expanduser().resolve(strict=False)
    stat = source.stat()
    return {"path": str(source), "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns)}


def _cached_probe_matches(item: dict[str, Any], signature: dict[str, Any]) -> dict[str, Any] | None:
    """Return saved technical metadata only when it describes these exact file bytes."""
    try:
        raw = item.get("media_metadata_json") or "{}"
        metadata = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict) or not any(metadata.get(key) for key in ("duration", "width", "height", "video_codec", "codec")):
        return None
    try:
        cached_signature = {
            "path": str(item.get("media_metadata_path") or ""),
            "size": int(item.get("media_metadata_size") or 0),
            "mtime_ns": int(item.get("media_metadata_mtime_ns") or 0),
        }
    except (TypeError, ValueError):
        return None
    if cached_signature != signature:
        return None
    return metadata


async def _probe_metadata_for_item(
    ctx: JobContext, item: dict[str, Any], path: Path, *, ffprobe_fallback: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reuse a probe only for the exact path/size/mtime; otherwise probe once and checkpoint it."""
    signature = await ctx.blocking(_media_file_signature, path)
    cached = _cached_probe_matches(item, signature)
    if cached is not None:
        ctx.activity(
            f"Reused saved technical metadata for {Path(path).name}",
            operation="Content Automation · reuse media probe", current_item=Path(path).name,
            event="content_automation.media_probe_cache_hit",
            details={"source_path": signature["path"], "file_size_bytes": signature["size"],
                     "mtime_ns": signature["mtime_ns"], "metadata": cached}, progress=True,
        )
        return cached, signature

    metadata: dict[str, Any] = {}
    probe_error: Exception | None = None
    fallback_attempted = False
    try:
        metadata = dict(await ctx.blocking(internal_media.probe, Path(path)) or {})
    except Exception as exc:
        probe_error = exc
    # Match the application's established built-in-first, ffprobe-second policy.
    # The fallback is only attempted if the built-in probe fails or returns no usable data.
    if not metadata and ffprobe_fallback:
        fallback_attempted = True
        try:
            metadata = dict(await ctx.blocking(probe, ffprobe_fallback, Path(path)) or {})
        except Exception as exc:
            probe_error = exc
    metadata["codec"] = metadata.get("video_codec") or metadata.get("codec") or ""
    metadata["vcodec"] = metadata.get("video_codec") or metadata.get("codec") or ""
    metadata["acodec"] = metadata.get("audio_codec") or metadata.get("acodec") or ""
    if not any(metadata.get(key) for key in ("duration", "width", "height", "video_codec", "codec")):
        if probe_error is not None:
            ctx.db.log(
                f"Content Automation: metadata probe failed for {Path(path).name}: "
                f"{type(probe_error).__name__}: {probe_error}",
                "WARN", event="content_automation.media_probe_failed", job_id=ctx.job_id,
            )
        metadata = {}
    if metadata:
        _upsert(
            ctx.db, str(item.get("item_key") or ""),
            media_metadata_json=json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            media_metadata_path=signature["path"], media_metadata_size=signature["size"],
            media_metadata_mtime_ns=signature["mtime_ns"],
        )
    ctx.activity(
        f"Probed technical metadata for {Path(path).name}",
        operation="Content Automation · probe media metadata", current_item=Path(path).name,
        event="content_automation.media_probe_completed",
        details={"source_path": signature["path"], "file_size_bytes": signature["size"],
                 "mtime_ns": signature["mtime_ns"], "metadata": metadata,
                 "fallback_attempted": fallback_attempted,
                 "fallback_failed": bool(fallback_attempted and not metadata and probe_error is not None)}, progress=True,
    )
    return metadata, signature


def _playback_snapshot_path(raw_path: Path) -> Path:
    return local_playback_snapshot_path(raw_path)


async def _wait_stable(path: Path, *, checks: int = 3, delay: float = 2.0) -> None:
    last = -1
    stable = 0
    while stable < checks:
        if not path.exists():
            raise RuntimeError(f"Downloaded file disappeared: {path}")
        size = path.stat().st_size
        if size > 0 and size == last:
            stable += 1
        else:
            stable = 0
        last = size
        await asyncio.sleep(delay)


def _pick_video_manifest_name(files: list[dict[str, Any]]) -> str:
    """Choose the likely video filename from torrent metadata without waiting for bytes.

    Content Automation delivers one selected video per release. The manifest
    contains filenames before the payload finishes, so use its largest supported
    video as the preliminary identity clue; never invent a name from the RSS title
    when the actual media filename is available.
    """
    candidates: list[tuple[int, str]] = []
    for entry in files or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("path") or "").strip().replace("\\", "/")
        basename = name.rsplit("/", 1)[-1]
        if Path(basename).suffix.lower() not in _VIDEO_EXTENSIONS:
            continue
        try:
            size = int(entry.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        if basename:
            candidates.append((size, basename))
    if not candidates:
        return ""
    return max(candidates, key=lambda pair: (pair[0], len(pair[1])))[1]


def _decode_identity_preflight(item: dict[str, Any], filename: str) -> dict[str, Any] | None:
    """Return a completed filename-only matcher result scoped to this video."""
    if str(item.get("identity_preflight_status") or "").upper() != "COMPLETED":
        return None
    if str(item.get("identity_preflight_filename") or "") != str(filename or ""):
        return None
    try:
        payload = json.loads(item.get("identity_preflight_json") or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["preflight_complete"] = True
    return payload


def _start_identity_preflight(ctx: JobContext, item: dict[str, Any], filename: str):
    """Start one durable background provider lookup while the torrent downloads.

    This only scores filename candidates; it does not apply scene metadata or
    create performers/studios. The final Metadata & Assets stage performs the
    exact-OSHASH check and applies only the existing confidence/safety policy.
    """
    filename = str(filename or "").strip()
    item_key = str(item.get("item_key") or "")
    if not filename or not item_key:
        return None
    cached = _get(ctx.db, item_key) or item
    if str(cached.get("identity_preflight_filename") or "") == filename:
        state = str(cached.get("identity_preflight_status") or "").upper()
        if state in {"COMPLETED", "FAILED"}:
            return None
    cache = _run_cache(ctx)
    tasks = cache.setdefault("identity_preflight_tasks", {})
    active = tasks.get(item_key)
    if active is not None and not active.done():
        if str(cached.get("identity_preflight_filename") or "") == filename:
            return active
        active.cancel()

    _upsert(
        ctx.db, item_key,
        identity_preflight_status="RUNNING",
        identity_preflight_filename=filename,
        identity_preflight_json="{}",
        identity_preflight_started_at=config.now_iso(),
        identity_preflight_completed_at=None,
    )
    ctx.db.log(
        f"Content Automation: started pre-upload filename identity analysis · item={item_key} file={filename!r}",
        "INFO", event="content_automation.identity_preflight_started", job_id=ctx.job_id,
        details={"automation_item": item_key, "filename": filename, "runs_during_download": True},
    )

    async def _work():
        """Background identification of one item from its filename."""
        try:
            from ..metadata.matching import identify_with_confidence
            result = await identify_with_confidence(ctx.db, oshash=None, filename=filename)
            # Keep candidate objects intact for the canonical suggestion writer,
            # but serialize defensively because provider payloads are external data.
            payload = {
                "filename": filename,
                "confidence": result.get("confidence"),
                "reason": result.get("reason"),
                "score": result.get("score"),
                "quality": result.get("quality"),
                "hints": result.get("hints") or {},
                "best": result.get("best"),
                "matches": result.get("matches") or [],
                "errors": result.get("errors") or [],
                "provider_searches": result.get("provider_searches") or {},
                "searched_clues": result.get("searched_clues") or [],
                "searched_dates": result.get("searched_dates") or [],
                "searched_year": result.get("searched_year"),
                "preflight_complete": True,
            }
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
            _upsert(
                ctx.db, item_key,
                identity_preflight_status="COMPLETED",
                identity_preflight_filename=filename,
                identity_preflight_json=serialized,
                identity_preflight_completed_at=config.now_iso(),
            )
            candidates = len(payload["matches"])
            ctx.db.log(
                f"Content Automation: pre-upload filename analysis completed · file={filename!r} "
                f"confidence={payload.get('confidence') or 'NO_MATCH'} candidates={candidates}",
                "INFO" if candidates else "WARN",
                event="content_automation.identity_preflight_completed", job_id=ctx.job_id,
                details={"automation_item": item_key, "filename": filename,
                         "confidence": payload.get("confidence"), "candidate_count": candidates,
                         "provider_searches": payload.get("provider_searches"),
                         "errors": payload.get("errors")},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            payload = {"filename": filename, "errors": [f"{type(exc).__name__}: {exc}"],
                       "preflight_complete": True}
            _upsert(
                ctx.db, item_key,
                identity_preflight_status="FAILED",
                identity_preflight_filename=filename,
                identity_preflight_json=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                identity_preflight_completed_at=config.now_iso(),
            )
            ctx.db.log(
                f"Content Automation: pre-upload filename analysis failed for {filename!r}: {type(exc).__name__}: {exc}",
                "WARN", event="content_automation.identity_preflight_failed", job_id=ctx.job_id,
                details={"automation_item": item_key, "filename": filename},
            )

    task = asyncio.create_task(_work(), name=f"archive-x-identity-preflight-{item_key}")
    tasks[item_key] = task
    def _release_task(done_task):
        if tasks.get(item_key) is done_task:
            tasks.pop(item_key, None)
    task.add_done_callback(_release_task)
    return task


async def _await_identity_preflight(ctx: JobContext, item: dict[str, Any], filename: str,
                                    *, timeout_seconds: float = 60.0) -> dict[str, Any] | None:
    """Give the pre-download matcher its remaining time, without blocking uploads forever."""
    item_key = str(item.get("item_key") or "")
    current = _get(ctx.db, item_key) or item
    result = _decode_identity_preflight(current, filename)
    if result is not None:
        return result
    state = str(current.get("identity_preflight_status") or "").upper()
    if str(current.get("identity_preflight_filename") or "") != filename or state not in {"RUNNING", "COMPLETED", "FAILED"}:
        _start_identity_preflight(ctx, current, filename)
    elif state == "RUNNING":
        task = _run_cache(ctx).setdefault("identity_preflight_tasks", {}).get(item_key)
        if task is None or task.done():
            _start_identity_preflight(ctx, current, filename)
    task = _run_cache(ctx).setdefault("identity_preflight_tasks", {}).get(item_key)
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=max(1.0, float(timeout_seconds)))
        except asyncio.TimeoutError:
            ctx.db.log(
                f"Content Automation: filename preflight is still running at the metadata stage; "
                f"continuing to exact/fallback matching for {filename!r}",
                "WARN", event="content_automation.identity_preflight_wait_timeout", job_id=ctx.job_id,
                details={"automation_item": item_key, "filename": filename,
                         "timeout_seconds": timeout_seconds},
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The worker persists its own failure state; final matching can retry.
            pass
    current = _get(ctx.db, item_key) or current
    return _decode_identity_preflight(current, filename)


def _run_cache(ctx) -> dict[str, Any]:
    """Per-run memory: which torrents were already started/configured and
    which log lines were already written (to avoid repeating them every poll)."""
    cache = ctx.__dict__.get("_automation_cache")
    if cache is None:
        cache = {"prepared": set(), "selected": set(), "logs": {}, "processing_paused": False,
                 "identity_preflight_tasks": {}}
        ctx.__dict__["_automation_cache"] = cache
    return cache


def _log_changed(ctx, key: str, message: str, level: str = "INFO") -> None:
    """Write a log line only when it differs from the last one for `key`.
    The scheduler runs every few seconds; the same status line used to be
    written to the event log on every pass."""
    logs = _run_cache(ctx)["logs"]
    if logs.get(key) == message:
        return
    logs[key] = message
    ctx.db.log(message, level)


def _job_config(ctx: JobContext) -> dict[str, Any]:
    try:
        detail = json.loads((ctx.job or {}).get("detail") or "{}")
        if isinstance(detail, dict) and detail.get("config"):
            return _merge_config(detail["config"])
    except Exception:
        pass
    return _merge_config(ctx.db.setting(CONFIG_KEY, {}))




# ==============================================================================
# SECTION: Destination resolution and child-job coordination
# Destination lookup and child jobs.
# ==============================================================================

async def resolve_destination(client, value: Any):
    """Turn the saved destination (a channel ID such as "-1001234567890", or
    a @username / t.me link) into a Telegram entity.

    The ID is saved as TEXT. Telethon treats a text value as a phone number or
    username, so get_entity("-1001234567890") always failed with "Cannot find
    any entity" - every download finished but was never delivered."""
    text = str(value or "").strip()
    if not text:
        raise RuntimeError("No Telegram destination channel is configured")
    if re.fullmatch(r"-?\d+", text):
        channel_id = int(text)
        try:
            return await client.get_entity(channel_id)
        except Exception as first_error:
            # Not in the session's cache yet: find it in the dialog list
            # (this also caches it for next time).
            try:
                async for dialog in client.iter_dialogs():
                    if int(getattr(dialog, "id", 0) or 0) == channel_id:
                        return dialog.entity
            except Exception:
                pass
            raise RuntimeError(f"Telegram destination channel {text} was not found for this account "
                               f"({type(first_error).__name__}: {first_error}). "
                               f"Re-select it in Operations -> Content Automation.") from first_error
    return await client.get_entity(text)



async def _wait_for_orchestrated_child(ctx: JobContext, child_id: str, *, label: str,
                                       parent_label: str = "Content Automation",
                                       event_namespace: str = "content_automation",
                                       parent_job_type: str = "CONTENT_AUTOMATION",
                                       allow_completed_with_errors: bool = False) -> dict[str, Any]:
    """Wait for an orchestrated child under explicit parent-owned supervision.

    The parent keeps its own heartbeat alive, tracks the child's durable state
    and activity journal, and refreshes the child's watchdog only while there is
    proof of a live worker/execution lease. A child may spend a long time inside
    one bounded native operation without producing new stage events; the parent
    allows that operation up to the child's configured recovery deadline. If a
    live child produces no observable progress beyond that hard limit, the
    parent stops it with a useful reason instead of waiting forever.
    """
    from datetime import datetime, timezone
    from ..core.watchdogs import DEFAULT_POLICIES

    manager = getattr(ctx, "_manager", None)
    if manager is None:
        raise RuntimeError(f"{parent_label} cannot schedule {label}: JobManager is unavailable")
    child_id = str(child_id or "").strip()
    if not child_id:
        raise RuntimeError(f"{parent_label} cannot schedule {label}: missing child job id")

    row = ctx.db.get_job(child_id) or {}
    status = str(row.get("status") or "").upper()
    if status == "PAUSED":
        manager.resume_job(child_id)
    elif status in {"INTERRUPTED", "RECOVERING"}:
        await manager.recover_job(child_id)
    elif status == "QUEUED":
        manager.submit(child_id)
    elif status in {"RUNNING", "PAUSING", "CANCEL_REQUESTED"}:
        pass
    elif status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}:
        return row
    else:
        manager.submit(child_id)

    manager.register_supervised_child(ctx.job_id, child_id, label=label)
    try:
        ctx.activity(
            f"{parent_label} supervising {label} child job {child_id}",
            operation=f"{parent_label} · supervise {label}", current_item=label,
            event=f"{event_namespace}.child_supervision_started",
            details={"child_job_id": child_id, "child_job_type": str(row.get("job_type") or ""),
                     "label": label, "supervision_policy": "parent_heartbeat_plus_child_progress"},
            progress=True, force=True,
        )
        last_activity_poll = 0.0
        last_supervision_breadcrumb = 0.0
        last_signature = None
        child_orphaned_since = None
        while True:
            # The parent owns the wait and keeps its own watchdog current. The
            # child's timeout is handled separately below using observable
            # child activity and the live execution lease.
            if ctx.watchdog is not None:
                ctx.watchdog.heartbeat(
                    ctx.job_id, progress=False, activity=True,
                    reason="orchestrated_child_wait",
                    metadata={"child_job_id": child_id, "operation": f"Waiting for {label}"},
                )
            if ctx.pipeline is not None:
                ctx.pipeline.heartbeat(activity=True)
            try:
                await ctx.checkpoint()
            except JobCancelled:
                try:
                    manager.cancel(child_id)
                except Exception:
                    pass
                raise

            row = ctx.db.get_job(child_id) or {}
            status = str(row.get("status") or "").upper()
            now_mono = time.monotonic()
            activity_id = None
            if now_mono - last_activity_poll >= 1.0:
                try:
                    activity_id = ctx.db.latest_job_activity_id(child_id)
                except Exception:
                    activity_id = None
                last_activity_poll = now_mono
            else:
                tracking = manager.supervised_child(child_id) or {}
                activity_id = tracking.get("last_activity_id")

            supervision = manager.observe_supervised_child(child_id, row, activity_id)
            child_operation = str(row.get("current_operation") or row.get("operation") or label)
            child_item = str(row.get("current_item") or label)
            signature = (status, child_operation, child_item, row.get("progress"), row.get("completed"), row.get("total"))
            if status in {"RUNNING", "PAUSING", "QUEUED", "RECOVERING"}:
                ctx.report(current_item=f"{label} · {child_item}",
                           operation=f"{parent_label} · {label}: {child_operation}")
            if signature != last_signature and last_signature is not None:
                ctx.activity(
                    f"{parent_label} observed {label} child progress: {child_operation}",
                    operation=f"{parent_label} · {label}: {child_operation}",
                    current_item=child_item,
                    event=f"{event_namespace}.child_supervision_progress",
                    details={"child_job_id": child_id, "child_status": status,
                             "child_progress": row.get("progress"), "completed": row.get("completed"),
                             "total": row.get("total"), "child_activity_id": activity_id},
                    progress=True,
                )
            last_signature = signature

            if status in {"COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED"}:
                if status != "COMPLETED" and not (
                    allow_completed_with_errors and status == "COMPLETED_WITH_ERRORS"
                ):
                    # Preserve the detailed exception on the child job. The parent
                    # should link to it instead of repeating the same long error
                    # banner in Content Automation and Metadata & Assets.
                    raise RuntimeError(
                        f"{parent_label} {label} failed in child job {child_id} ({status}). "
                        "Open the child job in Operations for the detailed error."
                    )
                ctx.db.log(
                    f"{parent_label}: {label} child job {child_id} "
                    f"{'completed with errors' if status == 'COMPLETED_WITH_ERRORS' else 'completed'}",
                    "WARN" if status == "COMPLETED_WITH_ERRORS" else "SUCCESS",
                    event=(f"{event_namespace}.child_job_completed_with_errors"
                           if status == "COMPLETED_WITH_ERRORS" else f"{event_namespace}.child_job_completed"),
                    job_id=ctx.job_id,
                    details={"child_job_id": child_id, "child_job_type": str(row.get("job_type") or ""),
                             "label": label, "child_status": status},
                )
                return row

            # Only supervise a real child execution: the JobManager task must be
            # alive and its current durable execution lease must match the child
            # row. A stale/zombie DB status alone is not enough to extend timeouts.
            child_task = getattr(manager, "running", {}).get(child_id)
            child_task_alive = child_task is not None and not child_task.done()
            execution = getattr(manager, "_execution_context", {}).get(child_id) or {}
            execution_id = str(row.get("execution_id") or "")
            execution_live = bool(execution_id and str(execution.get("execution_id") or "") == execution_id)
            lease_live = execution_live
            lease_expires = str(row.get("lease_expires_at") or "").strip()
            if lease_live and lease_expires:
                try:
                    expires = datetime.fromisoformat(lease_expires.replace("Z", "+00:00"))
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    lease_live = expires > datetime.now(timezone.utc)
                except (TypeError, ValueError):
                    # If an existing row has a malformed lease timestamp, rely
                    # on the live in-memory execution ID and task as a fallback.
                    lease_live = True

            policy_entry = None
            watchdog = getattr(manager, "watchdogs", None)
            if watchdog is not None:
                try:
                    policy_entry = watchdog.registry.get(child_id)
                except Exception:
                    policy_entry = None
                if policy_entry is None and status in {"RUNNING", "PAUSING"}:
                    try:
                        watchdog.register_job(child_id, str(row.get("job_type") or "DEFAULT"))
                        policy_entry = watchdog.registry.get(child_id)
                    except Exception:
                        policy_entry = None
            policy = getattr(policy_entry, "policy", None)
            default_policy = DEFAULT_POLICIES.get(str(row.get("job_type") or "DEFAULT"), DEFAULT_POLICIES["DEFAULT"])
            hard_limit = max(60.0, float(getattr(policy, "recovery_timeout", default_policy.recovery_timeout)))

            pipe_snapshot = {}
            try:
                pipeline = manager.pipeline_for(child_id)
                pipe_snapshot = pipeline.snapshot() if pipeline is not None else {}
            except Exception:
                pipe_snapshot = {}
            active_blocking_age = max(0.0, float(pipe_snapshot.get("active_blocking_age") or 0.0))
            waiting = bool(supervision.get("waiting"))
            no_progress_age = max(0.0, float(supervision.get("no_progress_seconds") or 0.0))
            # For a currently executing native operation, its own active age is
            # the relevant bound. Otherwise enforce a no-observable-progress
            # limit using child state changes and durable activity event IDs.
            supervised_idle_age = active_blocking_age if active_blocking_age > 0 else no_progress_age
            child_active = child_task_alive and execution_live and lease_live and status in {"RUNNING", "PAUSING"}

            if status in {"RUNNING", "PAUSING"} and not child_active:
                if child_orphaned_since is None:
                    child_orphaned_since = now_mono
                orphan_age = max(0.0, now_mono - child_orphaned_since)
                if orphan_age >= ORCHESTRATED_CHILD_ORPHAN_GRACE_SECONDS:
                    reason = (
                        f"{label} child {child_id} is orphaned: job row remains {status}, "
                        f"but its live task/execution lease is missing for {orphan_age:.1f}s; "
                        f"last operation: {child_operation}"
                    )
                    ctx.activity(
                        reason, operation=f"{parent_label} · {label} orphaned child",
                        current_item=child_item, level="ERROR",
                        event=(DEFAULT_CHILD_SUPERVISION_ORPHAN_EVENT if event_namespace == "content_automation"
                               else f"{event_namespace}.child_supervision_orphaned"),
                        details={"child_job_id": child_id, "child_status": status,
                                 "child_operation": child_operation, "child_task_alive": child_task_alive,
                                 "execution_id": execution_id, "execution_live": execution_live,
                                 "lease_live": lease_live, "orphan_age_seconds": round(orphan_age, 2)},
                        progress=False, force=True,
                    )
                    force_stop = getattr(manager, "watchdog_stop_unrecovered", None)
                    try:
                        if callable(force_stop):
                            force_stop(child_id, reason=reason)
                        else:
                            manager.cancel(child_id)
                    except Exception:
                        try:
                            manager.cancel(child_id)
                        except Exception:
                            pass
                    raise RuntimeError(reason)
            else:
                child_orphaned_since = None

            if child_active and not waiting and supervised_idle_age >= hard_limit:
                reason = (f"{label} child {child_id} made no observable progress for "
                          f"{supervised_idle_age:.1f}s (supervision limit {hard_limit:.0f}s); "
                          f"last operation: {child_operation}")
                ctx.activity(
                    reason, operation=f"{parent_label} · {label} supervision timeout",
                    current_item=child_item, level="ERROR",
                    event=f"{event_namespace}.child_supervision_timeout",
                    details={"child_job_id": child_id, "child_status": status,
                             "child_operation": child_operation, "child_activity_id": activity_id,
                             "no_progress_seconds": round(supervised_idle_age, 2),
                             "supervision_limit_seconds": hard_limit,
                             "active_blocking_age_seconds": active_blocking_age},
                    progress=False, force=True,
                )
                try:
                    manager.cancel(child_id)
                except Exception:
                    pass
                raise TimeoutError(reason)

            if child_active and watchdog is not None and (waiting or supervised_idle_age < hard_limit):
                # Parent-owned heartbeat prevents an active child from aging out
                # only while a real task + non-expired execution lease are proven.
                # The independent hard idle limit above prevents this from
                # becoming an indefinite keep-alive for a genuinely hung child.
                watchdog.heartbeat(
                    child_id, progress=True, activity=True,
                    reason="parent_supervised_child_alive",
                    metadata={"parent_job_id": ctx.job_id, "parent_job_type": parent_job_type,
                              "label": label, "child_status": status,
                              "child_operation": child_operation, "child_item": child_item,
                              "child_progress": row.get("progress"),
                              "no_progress_seconds": round(supervised_idle_age, 2),
                              "supervision_limit_seconds": hard_limit,
                              "active_blocking_age_seconds": active_blocking_age,
                              "execution_lease_live": True},
                )
                if now_mono - last_supervision_breadcrumb >= 15.0:
                    ctx.activity(
                        f"Parent supervision heartbeat: {label} child is alive",
                        operation=f"{parent_label} · supervise {label}", current_item=child_item,
                        event=f"{event_namespace}.child_supervision_heartbeat",
                        details={"child_job_id": child_id, "child_operation": child_operation,
                                 "no_progress_seconds": round(supervised_idle_age, 2),
                                 "active_blocking_age_seconds": active_blocking_age,
                                 "execution_lease_live": True}, progress=False,
                    )
                    last_supervision_breadcrumb = now_mono

            await asyncio.sleep(0.5)
    finally:
        manager.unregister_supervised_child(ctx.job_id, child_id)


def _require_prepared_media_library_ready(db, media_id: int) -> dict[str, Any]:
    """Require scanned-video publication readiness before a local source is sent.

    A local staging row is deliberately not a Library-facing Telegram record, so
    this check validates the publication requirements directly instead of asking
    whether the staging identity itself appears in ``archive_x_published_media``.
    The destination record is made Library-visible only after Telegram delivery
    has been verified and registered under its real channel/message IDs.
    """
    from ..core.resource_registry import ensure_resource_tables
    ensure_resource_tables(db)
    with db.lock:
        row = db.conn.execute(
            "SELECT m.id,m.source_channel,m.source_message_id,m.filename,m.streamable,"
            "rs.catalogue_state,rs.metadata_state,rs.entity_state,rs.snapshot_state,rs.provider_state,"
            "EXISTS(SELECT 1 FROM asset_registry ar WHERE ar.media_id=m.id "
            "AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY') AS media_asset_ready,"
            "EXISTS(SELECT 1 FROM asset_registry ar WHERE ar.entity_type='telegram_video' "
            "AND ar.entity_id=(CAST(m.source_channel AS TEXT) || ':' || CAST(m.source_message_id AS TEXT)) "
            "AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY') AS telegram_asset_ready "
            "FROM media m LEFT JOIN resource_state rs ON rs.media_id=m.id WHERE m.id=? LIMIT 1",
            (int(media_id),),
        ).fetchone()
    if not row:
        raise RuntimeError(f"Prepared media record {media_id} is missing; refusing to send before Library preparation")
    details = dict(row)
    missing = []
    if str(details.get("catalogue_state") or "") != "COMPLETE": missing.append("catalogue")
    if int(details.get("streamable") or 0) != 1: missing.append("streamability")
    if str(details.get("metadata_state") or "") != "COMPLETE": missing.append("metadata")
    if str(details.get("entity_state") or "") != "COMPLETE": missing.append("performer/studio resolution")
    if str(details.get("snapshot_state") or "") != "READY": missing.append("36-frame snapshots/representative state")
    if not (int(details.get("media_asset_ready") or 0) or int(details.get("telegram_asset_ready") or 0)):
        missing.append("representative image asset")
    if missing:
        reason = ", ".join(missing)
        try:
            db.log(f"Pre-upload readiness gate: withholding delivery for media_id={media_id}; Library readiness pending: {reason}", "WARN")
        except Exception:
            pass
        raise RuntimeError(f"Video is not Library-ready after Metadata & Assets ({reason}); upload has been held")
    return details




# ==============================================================================
# SECTION: Media preparation and verified Telegram delivery
# Metadata assets and publish.
# ==============================================================================

async def _prepare_metadata_assets(ctx: JobContext, cfg: dict[str, Any], item: dict[str, Any], media_path: Path) -> dict[str, Any]:
    """Prepare and commit one torrent media item through a real child job.

    The child is submitted to JobManager so Metadata & Assets receives the
    same admission, execution lease, watchdog, checkpoint, retry, journal,
    diagnostics, and recovery lifecycle as every other background job.
    """
    current = _get(ctx.db, item["item_key"]) or item
    identity_preflight = await _await_identity_preflight(ctx, current, media_path.name)
    ctx.activity(f"Metadata & Assets preparation starting for {media_path.name}",
                 operation="Content Automation · metadata and snapshot preparation", current_item=media_path.name,
                 event="content_automation.metadata_assets_started",
                 details={"automation_item": item.get("item_key"), "source_path": str(media_path),
                          "source_filename": media_path.name,
                          "source_bytes": int(media_path.stat().st_size) if media_path.is_file() else 0}, progress=True)
    existing_job_id = str(current.get("metadata_job_id") or "").strip()
    child = ctx.db.get_job(existing_job_id) if existing_job_id else None
    if child and str(child.get("status") or "").upper() == "COMPLETED":
        media_id = int(current.get("media_id") or 0)
        if media_id:
            with ctx.db.lock:
                row = ctx.db.conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
            snapshot_existing = local_playback_snapshot_path(media_path)
            if row and snapshot_existing.is_file():
                _require_prepared_media_library_ready(ctx.db, media_id)
                oshash_existing = str(current.get("raw_oshash") or row["oshash"] or "").strip()
                caption_existing = str(current.get("caption") or "")
                if not caption_existing:
                    caption_existing = await build_content_automation_caption(
                        ctx.db, media_path, {}, oshash_value=oshash_existing or None, media_id=media_id,
                    )
                _upsert(ctx.db, item["item_key"], status="READY_TO_TRANSFER", media_id=media_id, snapshot_path=str(snapshot_existing), caption=caption_existing, content_plan_status="READY_TO_TRANSFER", download_state=TASK_LABELS["READY_TO_TRANSFER"], error="")
                ctx.activity(f"Reusing completed Metadata & Assets result for {media_path.name}",
                             operation="Content Automation · reuse prepared metadata and snapshots", current_item=media_path.name,
                             event="content_automation.metadata_assets_reused",
                             details={"media_id": media_id, "snapshot_path": str(snapshot_existing),
                                      "metadata_job_id": existing_job_id, "caption": caption_existing[:2000],
                                      "source_oshash": oshash_existing}, progress=True)
                return dict(row)

    # Reuse a live/recoverable child rather than creating a second preparation
    # job for the same automation item. Failed/cancelled terminal children are
    # replaced by the parent retry, preserving the normal retry boundary.
    if child and str(child.get("status") or "").upper() not in {"COMPLETED", "FAILED", "CANCELLED", "COMPLETED_WITH_ERRORS"}:
        child_id = existing_job_id
    else:
        oshash = str(current.get("raw_oshash") or "").strip()
        if not oshash:
            oshash = await ctx.blocking(oshash_file, str(media_path))
            _upsert(ctx.db, item["item_key"], raw_oshash=oshash)
        metadata, metadata_signature = await _probe_metadata_for_item(ctx, current, media_path)
        ctx.activity(f"Source media metadata ready for {media_path.name}",
                     operation="Content Automation · source metadata prepared", current_item=media_path.name,
                     event="content_automation.source_metadata_ready",
                     details={"source_filename": media_path.name, "source_path": metadata_signature["path"],
                              "file_size_bytes": metadata_signature["size"], "mtime_ns": metadata_signature["mtime_ns"],
                              "metadata": {k: metadata.get(k) for k in ("video_codec", "audio_codec", "codec", "width", "height", "fps", "duration", "bitrate", "format_name", "container") if k in metadata},
                              "oshash": oshash}, progress=True)
        detail = {
            "workflow": "local_media",
            "parent_job_id": ctx.job_id,
            "parent_automation_item": item["item_key"],
            # Metadata has already been probed by validation/this stage. Pass it
            # to the canonical child pipeline so it doesn't probe the same file again.
            "media_items": [{"path": str(media_path), "filename": media_path.name, "oshash": oshash,
                              "metadata": metadata, "metadata_signature": metadata_signature,
                              "identity_preflight": identity_preflight}],
            "force_metadata": False,
            "force_entity": False,
            "stages": ["inventory", "metadata", "performers", "channel_data", "snapshots"],
        }
        child_id = ctx.db.create_job(
            "METADATA_CHANNEL_PIPELINE", "content_automation", cfg.get("destination_channel") or "", False,
            detail=json.dumps(detail, ensure_ascii=False), priority=40, max_retries=3,
        )
        _upsert(ctx.db, item["item_key"], metadata_job_id=child_id, content_plan_status="PREPARING",
                media_metadata_json=json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
                media_metadata_path=metadata_signature["path"], media_metadata_size=metadata_signature["size"],
                media_metadata_mtime_ns=metadata_signature["mtime_ns"],
                download_state=TASK_LABELS["METADATA_ASSETS"])
        ctx.activity(f"Metadata & Assets child job queued: {child_id}",
                     operation="Content Automation · wait for Metadata & Assets", current_item=media_path.name,
                     event="content_automation.metadata_child_queued",
                     details={"child_job_id": child_id, "source_filename": media_path.name,
                              "stages": ["inventory", "metadata", "performers", "channel_data", "snapshots"]}, progress=True)

    child_row = await _wait_for_orchestrated_child(ctx, child_id, label="Metadata & Assets")
    try:
        child_detail = json.loads(child_row.get("detail") or "{}")
    except Exception:
        child_detail = {}
    queue = child_detail.get("channel_queue") if isinstance(child_detail.get("channel_queue"), list) else []
    videos = queue[0].get("videos") if queue and isinstance(queue[0], dict) else []
    videos = videos if isinstance(videos, list) else []
    if not videos:
        raise RuntimeError("Metadata & Assets completed without a media record")
    prepared = videos[0]
    ctx.activity(f"Metadata & Assets child job completed for {media_path.name}",
                 operation="Content Automation · commit prepared metadata", current_item=media_path.name,
                 event="content_automation.metadata_child_completed",
                 details={"child_job_id": child_id, "child_status": str(child_row.get("status") or ""),
                          "prepared_video_count": len(videos), "prepared_video": {k: prepared.get(k) for k in ("filename", "path", "media_id", "oshash", "title", "studio", "performers", "date") if k in prepared}}, progress=True)
    media_id = int(prepared.get("media_id") or 0)
    if not media_id:
        raise RuntimeError("Metadata & Assets completed without a media identity")
    # Reload after the child finishes: it may have refreshed the probe cache or
    # advanced the durable automation checkpoint while this parent was waiting.
    current = _get(ctx.db, item["item_key"]) or current
    snapshot_path = local_playback_snapshot_path(media_path)
    if not snapshot_path.is_file():
        raise RuntimeError("Metadata & Assets completed without a representative playback snapshot")
    oshash = str(current.get("raw_oshash") or prepared.get("oshash") or "").strip()
    metadata = {}
    try:
        metadata = json.loads(current.get("media_metadata_json") or "{}")
    except Exception:
        metadata = {}
    caption = await build_content_automation_caption(
        ctx.db, media_path, metadata, oshash_value=oshash or None, media_id=media_id,
    )
    with ctx.db.lock:
        row = ctx.db.conn.execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    if not row:
        raise RuntimeError(f"Metadata & Assets media row {media_id} is missing after preparation")
    _require_prepared_media_library_ready(ctx.db, media_id)
    _upsert(ctx.db, item["item_key"], media_id=media_id, snapshot_path=str(snapshot_path), caption=caption,
            media_metadata_json=json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            status="READY_TO_TRANSFER", content_plan_status="READY_TO_TRANSFER",
            download_state=TASK_LABELS["READY_TO_TRANSFER"], error="", retry_stage="", next_retry_at="", failure_kind="")
    ctx.db.log(
        f"Content Automation: Metadata & Assets committed before transfer · item={item['item_key']} media_id={media_id} "
        f"job={child_id} file={media_path}",
        "SUCCESS", event="content_automation.preparation_completed", job_id=ctx.job_id,
        details={"child_job_id": child_id, "media_id": media_id},
    )
    ctx.activity(f"Metadata, entities and snapshot manifest committed for {media_path.name}",
                 operation="Content Automation · metadata preparation committed", current_item=media_path.name,
                 event="content_automation.metadata_assets_committed",
                 details={"media_id": media_id, "metadata_job_id": child_id,
                          "snapshot_path": str(snapshot_path), "caption": caption[:2000],
                          "metadata": {k: metadata.get(k) for k in ("video_codec", "audio_codec", "codec", "width", "height", "fps", "duration", "bitrate", "format_name", "container") if k in metadata}}, progress=True)
    return dict(row)

async def _publish(ctx: JobContext, cfg: dict[str, Any], item: dict[str, Any], video_path: Path) -> tuple[int, int]:
    """Deliver one DB-prepared media item through Telegram Transfer Manager."""
    if not ctx.telegram.client or not ctx.telegram.connected:
        raise RuntimeError("Telegram is not connected")
    current = _get(ctx.db, item["item_key"]) or item
    media_id = int(current.get("media_id") or 0)
    if not media_id:
        raise RuntimeError("Content Automation item has no prepared media_id")
    snapshot_path = Path(str(current.get("snapshot_path") or ""))
    if not snapshot_path.is_file():
        raise RuntimeError("Prepared playback snapshot is missing")
    caption = str(current.get("caption") or "")
    if not caption:
        raise RuntimeError("Content Automation item has no committed destination caption")
    try:
        meta = json.loads(current.get("media_metadata_json") or "{}")
    except Exception:
        meta = {}
    destination = await resolve_destination(ctx.telegram.client, cfg["destination_channel"])
    expected_name = video_path.name
    from ..core.telegram_identity import canonical_peer_id
    destination_key = canonical_peer_id(destination) or str(cfg.get("destination_channel") or "")
    raw_identity = str(current.get("raw_oshash") or "").strip()
    if raw_identity:
        with ctx.db.lock:
            existing_transfer = ctx.db.conn.execute(
                "SELECT destination_message_id,destination_thumbnail_id FROM transfer_locations WHERE identity_key=? AND destination_channel=? AND status='VERIFIED' ORDER BY id DESC LIMIT 1",
                (raw_identity, destination_key),
            ).fetchone()
        if existing_transfer:
            existing_message_id = int(existing_transfer["destination_message_id"])
            existing_thumbnail_id = int(existing_transfer["destination_thumbnail_id"] or 0)
            # A transfer ledger row alone is not enough to prove that a split
            # delivery is still usable: a later full channel scan may have
            # discovered a missing volume and hidden the logical Library card.
            # Re-verify all volumes before treating this item as already
            # published, and never create duplicate uploads on transient RPC
            # failures.
            from ..core.split_files import load_split_group, finalize_split_group
            split_group = await asyncio.to_thread(
                load_split_group, ctx.db, str(destination_key), existing_message_id,
            )
            if split_group:
                group_id = str(split_group.get("group_id") or "")
                if str(split_group.get("status") or "").upper() == "VERIFIED":
                    from ..engines.delivery_pipeline import verify_split_delivery, split_verification_is_definite_failure
                    ok, reason = await verify_split_delivery(ctx.telegram.client, destination, split_group)
                    if ok:
                        saved_thumb = await ctx.telegram.client.get_messages(destination, ids=existing_thumbnail_id) if existing_thumbnail_id else None
                        ok = bool(saved_thumb is not None and existing_thumbnail_id == int(split_group.get("thumbnail_message_id") or 0))
                        if not ok:
                            reason = "Split upload contact sheet is missing or does not match the verified group"
                    if ok:
                        _upsert(ctx.db, item["item_key"], status="CLEANUP", content_plan_status="ALREADY_PUBLISHED",
                                destination_video_id=existing_message_id, destination_thumbnail_id=existing_thumbnail_id,
                                download_state=TASK_LABELS["CLEANUP"], error="")
                        ctx.db.log(
                            f"Content Automation: verified split destination duplicate reused for media {media_id} -> {destination_key}",
                            "INFO", event="content_automation.destination_duplicate", job_id=ctx.job_id,
                        )
                        return existing_message_id, existing_thumbnail_id
                    if not split_verification_is_definite_failure(reason):
                        raise RuntimeError(f"Could not safely verify existing split delivery; retry without re-uploading: {reason}")
                    ctx.db.log(
                        f"Content Automation: existing split delivery is incomplete; re-publishing item {item['item_key']}: {reason}",
                        "WARN", event="content_automation.split_delivery_incomplete", job_id=ctx.job_id,
                        details={"group_id": group_id, "reason": reason},
                    )
                # Incomplete/preparing groups must remain hidden and must not be
                # treated as successful duplicate deliveries. Mark them
                # INCOMPLETE before the normal transfer path creates a new group.
                if group_id and str(split_group.get("status") or "").upper() != "INCOMPLETE":
                    await asyncio.to_thread(finalize_split_group, ctx.db, group_id, status="INCOMPLETE")
            else:
                _upsert(ctx.db, item["item_key"], status="CLEANUP", content_plan_status="ALREADY_PUBLISHED",
                        destination_video_id=existing_message_id, destination_thumbnail_id=existing_thumbnail_id,
                        download_state=TASK_LABELS["CLEANUP"], error="")
                ctx.db.log(
                    f"Content Automation: destination duplicate detected; reusing verified transfer for media {media_id} -> {destination_key}",
                    "INFO", event="content_automation.destination_duplicate", job_id=ctx.job_id,
                )
                return existing_message_id, existing_thumbnail_id
    existing_video = current.get("destination_video_id")
    existing_thumb = current.get("destination_thumbnail_id")
    if existing_video and existing_thumb:
        from ..core.split_files import load_split_group
        split_group = await asyncio.to_thread(load_split_group, ctx.db, str(destination_key), int(existing_video))
        if split_group and str(split_group.get("status") or "").upper() == "VERIFIED":
            from ..engines.delivery_pipeline import verify_split_delivery
            ok, reason = await verify_split_delivery(ctx.telegram.client, destination, split_group)
            saved_thumb = await ctx.telegram.client.get_messages(destination, ids=int(existing_thumb)) if ok else None
            ok = bool(ok and saved_thumb is not None)
            if not saved_thumb and not reason:
                reason = "Split upload contact sheet is missing"
        else:
            ok, reason = await verify_destination_pair(
                ctx.telegram.client, destination, int(existing_video), int(existing_thumb),
                expected_video_name=expected_name, require_native_video=True,
            )
        if ok:
            _upsert(ctx.db, item["item_key"], status="CLEANUP", content_plan_status="DELIVERY_VERIFIED", download_state=TASK_LABELS["CLEANUP"], error="")
            return int(existing_video), int(existing_thumb)
        _upsert(ctx.db, item["item_key"], status="VERIFYING", error=reason)
    # Honor Skip only if no verified destination duplicate exists. A skipped
    # upload is not delivery success; keep the source and torrent, and never
    # enter the cleanup stage.
    from ..core.upload_limits import get_upload_policy, normalize_oversized_file_policy, human_size
    oversized_policy = normalize_oversized_file_policy(
        cfg.get("oversized_file_policy"), legacy_split_upload=bool(cfg.get("split_upload", False)), default="optimize"
    )
    try:
        source_size = int(video_path.stat().st_size)
    except OSError as exc:
        raise RuntimeError(f"Cannot check upload size for {video_path.name}: {exc}") from exc
    account_upload_policy = await get_upload_policy(ctx.telegram.client)
    if source_size > int(account_upload_policy.safe_limit_bytes) and oversized_policy == "skip":
        reason = (f"Skipped oversized file {video_path.name} ({human_size(source_size)} exceeds "
                  f"{human_size(account_upload_policy.safe_limit_bytes)} safe upload limit); "
                  "policy is Skip, so the local file and torrent were retained.")
        _upsert(ctx.db, item["item_key"], status="SKIPPED", content_plan_status="SKIPPED",
                download_state=TASK_LABELS["SKIPPED"], error=reason, failure_kind="",
                next_retry_at="", retry_stage="", source_cleanup_state="PRESERVED_POLICY_SKIP")
        ctx.db.log(f"Content Automation: {reason}", "WARN",
                   event="content_automation.oversized_upload_skipped", job_id=ctx.job_id,
                   details={"automation_item": item["item_key"], "file_size_bytes": source_size,
                            "safe_limit_bytes": int(account_upload_policy.safe_limit_bytes)})
        return 0, 0

    # The destination Library item is a projection of this fully prepared
    # source record. Re-check immediately before delivery so retries or manual
    # state changes cannot bypass the same gate used for scanned channel videos.
    _require_prepared_media_library_ready(ctx.db, media_id)
    transfer_detail = {
        "source": "local", "source_type": "content_automation", "workflow": "CONTENT_AUTOMATION",
        "parent_job_id": ctx.job_id, "automation_item": item["item_key"], "media_id": media_id,
        "destination_channel": destination_key, "preparation": "METADATA_ASSETS_FIRST",
        "prepared_video_path": str(video_path), "prepared_snapshot_path": str(snapshot_path),
        "caption": caption, "video_meta_json": json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
        "file_name": expected_name, "identity_key": str(current.get("raw_oshash") or ""),
        "oversized_file_policy": str(cfg.get("oversized_file_policy") or ("split" if cfg.get("split_upload") else "optimize")),
        "split_upload": str(cfg.get("oversized_file_policy") or ("split" if cfg.get("split_upload") else "optimize")) == "split",
    }
    transfer_job_id = str(current.get("transfer_job_id") or "").strip()
    existing_transfer_job = ctx.db.get_job(transfer_job_id) if transfer_job_id else None
    existing_transfer_status = str((existing_transfer_job or {}).get("status") or "").upper()
    if existing_transfer_job and existing_transfer_status in {"FAILED", "CANCELLED", "INTERRUPTED", "COMPLETED", "COMPLETED_WITH_ERRORS"}:
        # If Telegram accepted the video or contact sheet before a watchdog/app
        # stop, carry those destination IDs into the retry job. A retry must not
        # blindly upload the same video again. The child re-verifies saved IDs.
        try:
            prior_detail = json.loads(existing_transfer_job.get("detail") or "{}")
        except Exception:
            prior_detail = {}
        for checkpoint_key in ("video_message_id", "thumbnail_message_id"):
            if prior_detail.get(checkpoint_key):
                transfer_detail[checkpoint_key] = int(prior_detail[checkpoint_key])
        if isinstance(prior_detail.get("split_delivery"), dict):
            transfer_detail["split_delivery"] = prior_detail["split_delivery"]
    if not transfer_job_id or existing_transfer_status in {"FAILED", "CANCELLED", "INTERRUPTED", "COMPLETED", "COMPLETED_WITH_ERRORS"}:
        transfer_job_id = ctx.db.create_job(
            "TELEGRAM_TRANSFER", "content_automation", destination_key, False,
            detail=json.dumps(transfer_detail, ensure_ascii=False), priority=40, max_retries=3,
        )
        _upsert(ctx.db, item["item_key"], transfer_job_id=transfer_job_id)
    _upsert(ctx.db, item["item_key"], status="TRANSFERRING_VIDEO", content_plan_status="TRANSFERRING", send_started_at=config.now_iso(), download_state=TASK_LABELS["TRANSFERRING_VIDEO"])
    ctx.report(detail="Telegram Transfer Manager · child job queued", current_item=expected_name, operation="Telegram Transfer · Video", force=True)
    transfer_row = await _wait_for_orchestrated_child(ctx, transfer_job_id, label="Telegram Transfer")
    transfer_detail_saved = {}
    try:
        transfer_detail_saved = json.loads(transfer_row.get("detail") or "{}")
    except Exception:
        transfer_detail_saved = {}
    video_id = int(transfer_detail_saved.get("result_video_id") or 0)
    thumb_id = int(transfer_detail_saved.get("result_thumbnail_id") or 0)
    if not video_id or not thumb_id:
        raise RuntimeError(f"Telegram Transfer child job {transfer_job_id} completed without verified destination IDs")
    raw_hash = str(current.get("raw_oshash") or "").strip()
    if raw_hash:
        with ctx.db.lock:
            ctx.db.conn.execute(
                "INSERT INTO automation_delivery_fingerprints(destination_channel,destination_video_id,item_key,raw_oshash,original_filename,created_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(destination_channel,destination_video_id) DO UPDATE SET item_key=excluded.item_key,raw_oshash=excluded.raw_oshash,original_filename=excluded.original_filename,created_at=excluded.created_at",
                (destination_key, int(video_id), str(item["item_key"]), raw_hash, str(current.get("title") or expected_name), config.now_iso()),
            )
            ctx.db.conn.execute(
                "UPDATE media SET oshash=? WHERE source_channel=? AND source_message_id=?",
                (raw_hash, destination_key, int(video_id)),
            )
            ctx.db.conn.commit()
    _upsert(ctx.db, item["item_key"], status="CLEANUP", content_plan_status="DELIVERY_VERIFIED",
            destination_video_id=int(video_id), destination_thumbnail_id=int(thumb_id),
            download_state=TASK_LABELS["CLEANUP"], error="", failure_kind="", next_retry_at="")
    return int(video_id), int(thumb_id)




# ==============================================================================
# SECTION: Torrent recovery, queue reconciliation, and download slots
# qBittorrent recovery and concurrency.
# ==============================================================================

async def _torrent_get_with_recovery(ctx: JobContext, engine, row: dict, root: Path, torrent_hash: str) -> TorrentInfo | None:
    """Read torrent status, transparently restoring the torrent after a native worker crash."""
    try:
        return await engine.get(torrent_hash)
    except TorrentEngineError as exc:
        message = str(exc)
        if "worker exited unexpectedly" not in message.lower() or not row.get("torrent_url"):
            raise
        source = str(row.get("torrent_url") or "").strip()
        if not source:
            raise
        ctx.db.log(
            f"Content Automation: torrent worker crashed for {row.get('title')}; restarting and restoring the torrent. "
            f"Worker diagnostics: {getattr(engine, '_diagnostic_path', 'unknown')}",
            "WARN",
        )
        restored_hash = await engine.add(source, save_path=str(root))
        if not restored_hash:
            raise TorrentEngineError(f"Torrent worker restarted but restore returned no torrent hash: {message}")
        await engine.resume(restored_hash)
        if restored_hash.lower() != str(torrent_hash).lower():
            _upsert(ctx.db, row["item_key"], torrent_hash=restored_hash)
            torrent_hash = restored_hash
        return await engine.get(torrent_hash)


async def _reconcile_qbit_archive_queue(ctx: JobContext, engine, max_downloads: int) -> dict[str, Any] | None:
    """Reconcile qBittorrent's real Archive X download state before dispatch.

    qBittorrent is connected through its Web API, so it is the authoritative
    source for which Archive X torrents are actually active. The check is
    category-scoped and does not modify unrelated qBittorrent downloads.
    """
    inspector = getattr(engine, "list_active_automation_torrents", None)
    reconciler = getattr(engine, "reconcile_automation_download_limit", None)
    if inspector is None or reconciler is None:
        return None
    try:
        active = await inspector()
        active_hashes = [str(row.get("hash") or "").strip() for row in active if str(row.get("hash") or "").strip()]
        if len(active_hashes) <= max_downloads:
            return {"active": active_hashes, "stopped": []}

        # Prefer torrents already represented by a DOWNLOADING DB row, then let
        # qBittorrent's add order decide the remaining slot(s).
        with ctx.db.lock:
            db_rows = [dict(row) for row in ctx.db.conn.execute(
                "SELECT torrent_hash, item_key, status FROM automation_items "
                "WHERE torrent_hash IS NOT NULL AND torrent_hash!=''"
            ).fetchall()]
        preferred = tuple(
            str(row.get("torrent_hash") or "").strip()
            for row in db_rows
            if str(row.get("status") or "") == DOWNLOAD_STATUS
        )
        result = await reconciler(max_downloads=max_downloads, keep_hashes=preferred)
        stopped = {str(value or "").strip().lower() for value in result.get("stopped", [])}
        if stopped:
            by_hash = {str(row.get("torrent_hash") or "").strip().lower(): row for row in db_rows}
            for torrent_hash in stopped:
                row = by_hash.get(torrent_hash)
                if not row or str(row.get("status") or "") != DOWNLOAD_STATUS:
                    continue
                _upsert(
                    ctx.db, row["item_key"], status="QUEUED", retry_stage="DOWNLOADING",
                    next_retry_at="", failure_kind="", error="", download_state=TASK_LABELS["QUEUED"],
                )
            ctx.db.log(
                f"Content Automation: qBittorrent Web API reconciled Archive X queue · "
                f"active={len(active_hashes)} limit={max_downloads} stopped={len(stopped)}",
                "WARN",
            )
        return result
    except Exception as exc:
        # The normal DB scheduler remains the fallback if qBittorrent is briefly
        # unreachable. The caller already has retry/recovery handling around it.
        ctx.db.log(
            f"Content Automation: qBittorrent queue reconciliation unavailable: {type(exc).__name__}: {exc}",
            "WARN",
        )
        return None


def _backlog_title_key(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\.(mp4|mkv|mov|m4v|avi|wmv|webm|ts|m2ts)$", "", text, flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


async def _adopt_existing_qbit_torrents(ctx: JobContext, cfg: dict[str, Any], engine) -> dict[str, int]:
    """Attach leftover Archive X category torrents to the durable automation queue.

    Previous releases can survive an application restart in qBittorrent without a
    corresponding live DOWNLOADING row (especially seeding/paused items). If they
    are not adopted, they do not consume a slot and a fresh RSS release can overtake
    them. Exact normalized-title matches reuse an existing RSS row; otherwise a
    deterministic qBittorrent backlog row is created so existing local data is reused.
    """
    lister = getattr(engine, "list_automation_torrents", None)
    if lister is None:
        # Non-qBittorrent engines do not expose an external category inventory.
        return {"adopted": 0, "matched": 0, "ignored": 0}
    cache = _run_cache(ctx)
    try:
        raw_rows = await lister()
    except Exception as exc:
        cache["qbit_backlog_inventory_ok"] = False
        ctx.db.log(f"Content Automation: existing qBittorrent backlog scan failed: {type(exc).__name__}: {exc}", "WARN")
        return {"adopted": 0, "matched": 0, "ignored": 0}
    # A successful empty response is different from a failed/inaccessible list.
    # Dispatch must never start fresh RSS downloads when it cannot see the full
    # Archive X category, since seeding/paused torrents may otherwise be missed.
    cache["qbit_backlog_inventory_ok"] = True
    if not raw_rows:
        return {"adopted": 0, "matched": 0, "ignored": 0}

    with ctx.db.lock:
        all_rows = [dict(row) for row in ctx.db.conn.execute("SELECT * FROM automation_items").fetchall()]
    rows_by_hash = {str(row.get("torrent_hash") or "").strip().lower(): row
                    for row in all_rows if str(row.get("torrent_hash") or "").strip()}
    queued_by_title: dict[str, list[dict[str, Any]]] = {}
    completed_titles = {_backlog_title_key(row.get("title")) for row in all_rows
                        if str(row.get("status") or "").upper() in {"COMPLETED", "SKIPPED"}}
    for row in all_rows:
        if str(row.get("status") or "").upper() != "QUEUED" or str(row.get("torrent_hash") or "").strip():
            continue
        key = _backlog_title_key(row.get("title"))
        if key:
            queued_by_title.setdefault(key, []).append(row)

    adopted = matched = ignored = 0
    for torrent in sorted(raw_rows, key=lambda row: float(row.get("added_on") or 0)):
        torrent_hash = str(torrent.get("hash") or "").strip()
        if not torrent_hash:
            continue
        hash_key = torrent_hash.lower()
        if hash_key in rows_by_hash:
            ignored += 1
            continue
        title = str(torrent.get("name") or torrent.get("name_orig") or torrent_hash).strip()
        title_key = _backlog_title_key(title)
        # If a verified/completed item with the same normalized title is already in
        # the durable ledger, this is a lingering qBittorrent row, not a new delivery.
        if title_key and title_key in completed_titles:
            ignored += 1
            continue
        candidates = queued_by_title.get(title_key, []) if title_key else []
        candidate = candidates.pop(0) if candidates else None
        source_key = str(torrent.get("magnet_uri") or "").strip() or f"qBittorrent://{torrent_hash}"
        torrent_url = str(torrent.get("magnet_uri") or "").strip()
        fields = {
            "torrent_hash": torrent_hash,
            "status": DOWNLOAD_STATUS,
            "retry_stage": "",
            "next_retry_at": "",
            "download_progress": max(0.0, min(1.0, float(torrent.get("progress") or 0.0))),
            "download_rate": int(torrent.get("dlspeed") or 0),
            "download_state": "Existing qBittorrent backlog · resumed before new RSS downloads",
            "torrent_state": str(torrent.get("state") or ""),
            "torrent_content_path": str(torrent.get("content_path") or torrent.get("save_path") or cfg.get("download_path") or ""),
            "destination_channel": str(cfg.get("destination_channel") or ""),
            "error": "",
            "failure_kind": "",
        }
        if candidate is not None:
            _upsert(ctx.db, str(candidate["item_key"]), **fields)
            rows_by_hash[hash_key] = {**candidate, **fields}
            matched += 1
        else:
            item_key = f"qbit-existing:{hash_key}"
            _upsert(
                ctx.db, item_key, source_url=source_key, guid=hash_key, title=title,
                torrent_url=torrent_url, retry_count=0, enclosure_size=int(torrent.get("size") or 0),
                content_plan_status="EXISTING_QBIT_BACKLOG", **fields,
            )
            rows_by_hash[hash_key] = {"item_key": item_key, "title": title, **fields}
            adopted += 1
    if adopted or matched:
        ctx.db.log(
            f"Content Automation: existing qBittorrent backlog reconciled · adopted={adopted}, "
            f"matched to RSS={matched}, already tracked/terminal={ignored}; existing items take priority over new downloads",
            "WARN" if adopted or matched else "INFO",
        )
    return {"adopted": adopted, "matched": matched, "ignored": ignored}


async def _enforce_download_concurrency(ctx: JobContext, engine, max_downloads: int) -> list[dict]:
    """Repair and enforce the configured number of active download slots."""
    max_downloads = max(1, min(10, int(max_downloads or 1)))
    await _reconcile_qbit_archive_queue(ctx, engine, max_downloads)
    with ctx.db.lock:
        rows = [dict(r) for r in ctx.db.conn.execute(
            "SELECT * FROM automation_items WHERE status='DOWNLOADING' ORDER BY created_at ASC"
        ).fetchall()]
    if len(rows) <= max_downloads:
        return rows

    # Prefer any already-completed local file over an incomplete torrent, then
    # keep qBittorrent's actual add order. This is crucial after restart when
    # several old torrents were left in the Archive X category: one slot should
    # finish delivery of the oldest ready file rather than keep polling a newer
    # partial download selected by RSS/database creation timestamps.
    lister = getattr(engine, "list_automation_torrents", None)
    qbit_by_hash: dict[str, dict[str, Any]] = {}
    if lister is not None:
        try:
            for torrent in await lister():
                value = str(torrent.get("hash") or "").strip().lower()
                if value:
                    qbit_by_hash[value] = dict(torrent)
        except Exception as exc:
            ctx.db.log(f"Content Automation: could not rank existing torrents before concurrency enforcement: {type(exc).__name__}: {exc}", "WARN")

    def _existing_priority(row: dict[str, Any]):
        torrent_hash = str(row.get("torrent_hash") or "").strip().lower()
        torrent = qbit_by_hash.get(torrent_hash, {})
        state = str(torrent.get("state") or row.get("torrent_state") or "").casefold()
        progress = max(float(row.get("download_progress") or 0.0), float(torrent.get("progress") or 0.0))
        complete = progress >= 0.9999 or state in {"seeding", "uploading", "queuedup", "pausedup", "stoppedup", "finished"}
        try:
            added_on = float(torrent.get("added_on") or 0.0)
        except (TypeError, ValueError):
            added_on = 0.0
        return (0 if complete else 1, added_on if added_on > 0 else float("inf"), str(row.get("created_at") or ""))

    rows.sort(key=_existing_priority)
    pauser = getattr(engine, "pause_all_automation_torrents", None)
    if pauser is not None:
        try:
            paused = pauser()
            paused = await paused if inspect.isawaitable(paused) else int(paused or 0)
            ctx.db.log(f"Content Automation: concurrency guard paused {int(paused or 0)} Archive X torrent(s); limit={max_downloads}", "WARN")
        except Exception as exc:
            ctx.db.log(f"Content Automation: concurrency guard could not pause excess torrents: {type(exc).__name__}: {exc}", "WARN")

    keep = rows[:max_downloads]
    overflow = rows[max_downloads:]
    for row in overflow:
        _upsert(ctx.db, row["item_key"], status="QUEUED", retry_stage="DOWNLOADING", next_retry_at="", failure_kind="", error="", download_state=TASK_LABELS["QUEUED"])
    ctx.db.log(f"Content Automation: concurrency limit {max_downloads}; moved {len(overflow)} excess downloads back to queue", "WARN")
    return keep


async def _fill_download_slots(ctx: JobContext, cfg: dict, engine, root: Path) -> int:
    """Immediately dispatch queued releases into available qBittorrent slots.

    This is the single authoritative download dispatcher. It is called once
    immediately after the RSS bootstrap scan and again from the scheduler loop.
    """
    # First reconcile against qBittorrent's actual Web API state. The database
    # can lag behind an add request, so it must not be the only source of truth.
    max_downloads = max(1, min(10, int(cfg.get("max_concurrent_torrents") or 1)))
    # If the full qBittorrent inventory has not been obtained successfully this
    # run, retry it here and fail closed. Counting only active downloads is not
    # enough because paused/seeding rows may be completed but undelivered.
    if getattr(engine, "list_automation_torrents", None) is not None:
        if _run_cache(ctx).get("qbit_backlog_inventory_ok") is not True:
            await _adopt_existing_qbit_torrents(ctx, cfg, engine)
        if _run_cache(ctx).get("qbit_backlog_inventory_ok") is not True:
            _log_changed(
                ctx, "backlog_inventory",
                "Content Automation: dispatcher blocked because the complete qBittorrent Archive X inventory is unavailable; "
                "no fresh torrent will be added until existing downloads can be reconciled.",
                "WARN",
            )
            return 0
    qbit_state = await _reconcile_qbit_archive_queue(ctx, engine, max_downloads)
    qbit_active_count = len((qbit_state or {}).get("active") or [])

    # Strict lifecycle gate: a completed torrent owns the pipeline until
    # validation, conversion, Telegram upload, verification, and cleanup are
    # finished. Do not dispatch another torrent while any post-download stage
    # is active. This prevents the scheduler from mistaking a processing item
    # for a free download slot.
    with ctx.db.lock:
        downloading_count = int(ctx.db.conn.execute(
            "SELECT COUNT(*) c FROM automation_items WHERE status='DOWNLOADING'"
        ).fetchone()["c"] or 0)
        processing_placeholders = ",".join("?" for _ in PROCESSING_STATUSES)
        processing_count = int(ctx.db.conn.execute(
            f"SELECT COUNT(*) c FROM automation_items WHERE status IN ({processing_placeholders})",
            list(PROCESSING_STATUSES),
        ).fetchone()["c"] or 0)
        # A post-download step that is waiting for a timed retry still owns the
        # serialized pipeline. It must not become an apparent free slot merely
        # because _fail_item has persisted it as QUEUED until next_retry_at.
        queued_processing_retry_count = int(ctx.db.conn.execute(
            f"SELECT COUNT(*) c FROM automation_items WHERE status='QUEUED' AND retry_stage IN ({processing_placeholders})",
            list(PROCESSING_STATUSES),
        ).fetchone()["c"] or 0)
        # A download-stage retry that already owns a qBittorrent hash must remain
        # ahead of fresh RSS rows even while its backoff timer is in the future.
        # The old dispatcher filtered it out by next_retry_at, then selected a new
        # RSS row and left the existing downloaded file stranded.
        queued_existing_download_retry_count = int(ctx.db.conn.execute(
            "SELECT COUNT(*) c FROM automation_items WHERE status='QUEUED' "
            "AND retry_stage='DOWNLOADING' AND COALESCE(torrent_hash,'')!=''"
        ).fetchone()["c"] or 0)
        failed_existing_backlog_count = int(ctx.db.conn.execute(
            "SELECT COUNT(*) c FROM automation_items WHERE status='FAILED' AND failure_kind='transient-exhausted' "
            "AND COALESCE(torrent_hash,'')!='' AND destination_video_id IS NULL"
        ).fetchone()["c"] or 0)
    # Download concurrency is configurable, but a post-download item (including
    # one waiting for a retry) owns the processing lane until delivery/verification/
    # cleanup finishes. A restart must not let an unrelated RSS item take its place.
    cache = _run_cache(ctx)
    pipeline_hold_count = processing_count + queued_processing_retry_count + failed_existing_backlog_count
    if not pipeline_hold_count:
        cache["processing_paused"] = False
    if pipeline_hold_count and cache["processing_paused"]:
        return 0
    if processing_count:
        # Defensive reconciliation: if an older run left a downloader active
        # alongside a processing item, pause only Archive X torrents.
        # This does not touch unrelated qBittorrent categories. Done once per
        # processing period (it used to run - and log - every few seconds).
        cache["processing_paused"] = True
        try:
            pauser = getattr(engine, "pause_all_automation_torrents", None)
            if pauser is not None:
                paused = pauser()
                paused = await paused if inspect.isawaitable(paused) else int(paused or 0)
                if paused:
                    ctx.db.log(
                        f"Content Automation: paused {int(paused)} download(s) while post-download processing is active",
                        "WARN",
                    )
        except Exception as exc:
            ctx.db.log(
                f"Content Automation: could not pause excess download(s) during processing: {type(exc).__name__}: {exc}",
                "WARN",
            )
        _log_changed(ctx, "dispatch",
            f"Content Automation: dispatcher blocked; post-download pipeline active={processing_count}. "
            "Waiting for upload/verification/cleanup before starting the next torrent.",
            "INFO",
        )
        return 0
    if queued_processing_retry_count:
        # A future-dated retry still owns the serialized lane. Pause any older
        # active downloader so it cannot consume resources or advance in parallel
        # while the preceding release is waiting to resume its delivery stage.
        cache["processing_paused"] = True
        try:
            pauser = getattr(engine, "pause_all_automation_torrents", None)
            if pauser is not None:
                paused = pauser()
                paused = await paused if inspect.isawaitable(paused) else int(paused or 0)
                if paused:
                    ctx.db.log(
                        f"Content Automation: paused {int(paused)} download(s) while an existing post-download retry is pending",
                        "WARN",
                    )
        except Exception as exc:
            ctx.db.log(
                f"Content Automation: could not pause downloads for a pending post-download retry: {type(exc).__name__}: {exc}",
                "WARN",
            )
        _log_changed(
            ctx, "dispatch",
            f"Content Automation: dispatcher blocked; {queued_processing_retry_count} post-download retry item(s) "
            "are pending. Waiting for the previous item's processing/delivery/cleanup to finish before starting another download.",
            "INFO",
        )
        return 0
    if queued_existing_download_retry_count:
        with ctx.db.lock:
            due_existing_retry = ctx.db.conn.execute(
                "SELECT item_key FROM automation_items WHERE status='QUEUED' AND retry_stage='DOWNLOADING' "
                "AND COALESCE(torrent_hash,'')!='' AND (next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?) "
                "ORDER BY created_at ASC LIMIT 1",
                (config.now_iso(),),
            ).fetchone()
        if due_existing_retry is None:
            _log_changed(
                ctx, "dispatch",
                f"Content Automation: dispatcher blocked; {queued_existing_download_retry_count} existing qBittorrent download(s) "
                "are waiting for their retry window. No newer RSS torrent will be added until the existing backlog is resumed.",
                "INFO",
            )
            return 0
    if failed_existing_backlog_count:
        cache["processing_paused"] = True
        try:
            pauser = getattr(engine, "pause_all_automation_torrents", None)
            if pauser is not None:
                paused = pauser()
                paused = await paused if inspect.isawaitable(paused) else int(paused or 0)
                if paused:
                    ctx.db.log(
                        f"Content Automation: paused {int(paused)} download(s) while an exhausted existing delivery awaits recovery",
                        "WARN",
                    )
        except Exception as exc:
            ctx.db.log(
                f"Content Automation: could not pause downloads for an exhausted delivery backlog: {type(exc).__name__}: {exc}",
                "WARN",
            )
        _log_changed(
            ctx, "dispatch",
            f"Content Automation: dispatcher blocked; {failed_existing_backlog_count} existing downloaded release(s) "
            "remain undelivered after retries. Repair/retry this backlog before starting new RSS downloads.",
            "WARN",
        )
        return 0
    # For the qBittorrent backend, an active Archive X torrent consumes
    # the slot even when the DB has not recorded it yet. This closes the race
    # where multiple add requests could arrive while the first add was still
    # being acknowledged by qBittorrent.
    if qbit_active_count >= max_downloads:
        # This function's contract is an integer count. Returning [] here
        # makes the scheduler's ``started_count > 0`` check raise TypeError
        # as soon as qBittorrent reports that the configured slots are full.
        return 0
    downloading_count = max(downloading_count, qbit_active_count)
    slots = max(0, max_downloads - downloading_count)
    started = 0
    if slots <= 0:
        return 0

    _log_changed(ctx, "dispatch",
        f"Content Automation: scheduler dispatch check · active={downloading_count} "
        f"limit={max_downloads} · free_slots={slots} · engine={type(engine).__name__}",
        "INFO",
    )
    while slots > 0:
        with ctx.db.lock:
            if queued_existing_download_retry_count:
                next_row = ctx.db.conn.execute(
                    "SELECT * FROM automation_items "
                    "WHERE status='QUEUED' AND retry_stage='DOWNLOADING' AND COALESCE(torrent_hash,'')!='' "
                    "AND (next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?) "
                    "ORDER BY created_at ASC LIMIT 1",
                    (config.now_iso(),),
                ).fetchone()
            else:
                next_row = ctx.db.conn.execute(
                    "SELECT * FROM automation_items "
                    "WHERE status='QUEUED' "
                    "AND (retry_stage='' OR retry_stage='DOWNLOADING' OR retry_stage IS NULL) "
                    "AND (next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?) "
                    "ORDER BY CASE WHEN COALESCE(torrent_hash,'')!='' THEN 0 ELSE 1 END, created_at ASC LIMIT 1",
                    (config.now_iso(),),
                ).fetchone()
        if next_row is None:
            break

        candidate = dict(next_row)
        title = str(candidate.get("title") or candidate.get("item_key") or "unknown")
        source = str(candidate.get("torrent_url") or "").strip()
        existing_hash = str(candidate.get("torrent_hash") or "").strip()
        if not source and not existing_hash:
            _fail_item(ctx.db, candidate["item_key"], "Queued release has no torrent URL or existing qBittorrent hash", retryable=False)
            slots -= 1
            continue

        ctx.report(detail=f"Resuming existing torrent: {title}" if existing_hash else f"Starting {title}",
                   current_item=title, operation="Resuming existing download" if existing_hash else "Adding torrent", force=True)
        try:
            torrent_hash = ""
            existing_complete = False
            existing_torrent = None
            if existing_hash:
                try:
                    existing_torrent = await asyncio.wait_for(engine.get(existing_hash), timeout=20)
                except Exception:
                    existing_torrent = None
                if existing_torrent is not None:
                    torrent_hash = existing_hash
                    try:
                        existing_files = await engine.files(existing_hash) if getattr(existing_torrent, "has_metadata", True) else []
                        completed_path = _pick_local_video(existing_torrent, existing_files, root)
                        existing_complete = bool(
                            completed_path and completed_path.is_file()
                            and _torrent_content_verified(existing_torrent, existing_files)
                        )
                    except Exception:
                        existing_complete = False

            if not torrent_hash:
                ctx.db.log(
                    f"Content Automation: qBittorrent add request · title={title!r} "
                    f"source={source[:220]!r} category={ARCHIVE_X_QBIT_CATEGORY!r} save_path={str(root)!r}",
                    "INFO",
                )
                torrent_hash = await asyncio.wait_for(
                    engine.add(source, save_path=str(root), category=ARCHIVE_X_QBIT_CATEGORY),
                    # A qBittorrent 5.2.x remote-URL add can legitimately remain
                    # pending while the client downloads/resolves the .torrent.
                    # The engine polls that pending add before returning a hash.
                    timeout=210,
                )
                if not torrent_hash:
                    raise RuntimeError("qBittorrent add returned no torrent hash")

            # Verify the exact returned hash exists before committing the DB row.
            verified = await asyncio.wait_for(engine.get(torrent_hash), timeout=20)
            if verified is None:
                raise RuntimeError(f"qBittorrent returned hash {torrent_hash}, but the torrent is not visible in qBittorrent")

            # qBittorrent persists across Archive X restarts. Save its identity as
            # soon as the server confirms the torrent exists, before start/prep can
            # fail; otherwise retry/restart treats the paused torrent as an orphan
            # and may dispatch another item while qBittorrent is resuming it.
            _upsert(ctx.db, candidate["item_key"], torrent_hash=torrent_hash)

            try:
                keep_reconciler = getattr(engine, "reconcile_automation_download_limit", None)
                if keep_reconciler is not None:
                    await keep_reconciler(max_downloads=max_downloads, keep_hashes=(torrent_hash,))
                if existing_complete:
                    # This is a completed/seeded backlog item, not a fresh
                    # download. Leave qBittorrent's seeding/paused state alone;
                    # the next state-machine pass will hand its local file to
                    # validation and delivery immediately.
                    state = str(getattr(existing_torrent, "state", "seeding") or "seeding")
                else:
                    prepare = engine.prepare_download(torrent_hash)
                    prepare = await prepare if inspect.isawaitable(prepare) else (prepare or {})
                    state = str(prepare.get("state") or "unknown") if isinstance(prepare, dict) else "unknown"
            except Exception as start_exc:
                raise RuntimeError(f"Torrent was added but could not be started: {start_exc}") from start_exc

            speed_fields: dict[str, Any] = {}
            if existing_complete:
                speed_fields = {
                    "speed_check_status": "PASSED",
                    "speed_check_completed_at": config.now_iso(),
                }
            elif not str(candidate.get("speed_check_started_at") or "").strip():
                speed_fields = {
                    "speed_check_started_at": config.now_iso(),
                    "speed_check_completed_at": "",
                    "speed_check_status": "CHECKING",
                    "speed_check_rate_sum": 0.0,
                    "speed_check_sample_count": 0,
                    "speed_check_average_rate": 0,
                }
            elif str(candidate.get("speed_check_status") or "").upper() not in {"PASSED", "SKIP_FAILED"}:
                speed_fields = {"speed_check_status": "CHECKING"}

            _upsert(
                ctx.db, candidate["item_key"], torrent_hash=torrent_hash, status=DOWNLOAD_STATUS,
                retry_stage="", download_state=(f"Existing completed torrent · {state}" if existing_complete else TASK_LABELS[DOWNLOAD_STATUS]),
                download_progress=(1.0 if existing_complete else 0.0),
                torrent_state=state, error="", next_retry_at="", failure_kind="",
                **speed_fields,
            )
            ctx.db.log(
                f"Content Automation: qBittorrent torrent active · title={title!r} hash={torrent_hash} state={state}",
                "INFO",
            )
            # Start provider matching as soon as the item is accepted by the
            # torrent engine. If magnet metadata has not exposed the true video
            # filename yet, the RSS/video label is a provisional clue; the
            # actual manifest filename replaces it as soon as it becomes known.
            provisional_label = str(candidate.get("title") or "").strip()
            if provisional_label:
                _start_identity_preflight(ctx, candidate, provisional_label)
            downloading_count += 1
            slots -= 1
            started += 1
        except Exception as exc:
            flood_delay = _flood_wait_delay(exc)
            retryable = True
            _fail_item(
                ctx.db, candidate["item_key"], f"Could not start torrent: {exc}",
                retryable=retryable, retry_stage="DOWNLOADING", retry_after_seconds=flood_delay,
                consume_retry_budget=(flood_delay <= 90),
            )
            ctx.db.log(
                f"Content Automation: qBittorrent dispatch failed · title={title!r}: {type(exc).__name__}: {exc}",
                "ERROR",
            )
            # Consume this slot for the remainder of this dispatch pass so a bad
            # RSS item cannot cause a tight add/retry loop. Persisted retry timing
            # will make it eligible again later.
            slots -= 1

    if started:
        ctx.db.log(f"Content Automation: dispatcher started {started} torrent(s) this pass", "INFO")
    else:
        with ctx.db.lock:
            queued_left = int(ctx.db.conn.execute(
                "SELECT COUNT(*) c FROM automation_items WHERE status='QUEUED' AND (retry_stage='' OR retry_stage='DOWNLOADING' OR retry_stage IS NULL)"
            ).fetchone()["c"] or 0)
        if queued_left:
            _log_changed(ctx, "dispatch", f"Content Automation: dispatcher started 0 torrents; {queued_left} queued release(s) remain", "WARN")
    return started


class _AlreadyPrepared(Exception):
    """Internal: the torrent is already running with the right settings."""


# A download is only "watched" while polls keep arriving; after a gap this
# long (automation paused, app busy) the stall clock starts again.
STALL_WATCH_GAP_SECONDS = 300.0
NEAR_COMPLETE_PROGRESS = 0.999
# A newly-added torrent gets one minute to demonstrate a useful download rate.
# Rates from qBittorrent/libtorrent are bytes per second; the UI displays KiB/s.
INITIAL_SPEED_CHECK_WINDOW_SECONDS = 60.0
INITIAL_SPEED_CHECK_MIN_BYTES_PER_SECOND = 300 * 1024
_clock = time.monotonic  # replaced in tests


def _parse_speed_check_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result
    except (TypeError, ValueError):
        return None


def _initial_speed_check_detail(row: dict[str, Any]) -> str:
    """Return short queue-card telemetry while the one-minute gate is active."""
    status = str(row.get("speed_check_status") or "").upper()
    if status == "SKIP_FAILED":
        return "slow-speed auto-skip failed · use Skip"
    if status != "CHECKING":
        return ""
    started = _parse_speed_check_timestamp(row.get("speed_check_started_at"))
    now = _parse_speed_check_timestamp(config.now_iso())
    elapsed = max(0, int((now - started).total_seconds())) if started and now else 0
    count = max(0, int(row.get("speed_check_sample_count") or 0))
    rate_sum = max(0.0, float(row.get("speed_check_rate_sum") or 0.0))
    average_kib = (rate_sum / count / 1024.0) if count else 0.0
    return f"speed check {min(elapsed, 60)}/60s · avg {average_kib:.0f}/300 KB/s"




# ==============================================================================
# SECTION: Download health and per-item state transitions
# Speed checks and stalled torrent handling.
# ==============================================================================

async def _update_initial_speed_check(ctx: JobContext, row: dict[str, Any], torrent: TorrentInfo) -> bool:
    """Sample the new torrent's first minute; return True when this poll must stop.

    A simple mean of the engine's regular download-rate samples avoids discarding
    a torrent because of one momentary slowdown. The timestamp and samples are
    stored in SQLite so a process restart does not silently reset the minute.
    """
    item_key = str(row.get("item_key") or "")
    current = _get(ctx.db, item_key) or row
    state = str(current.get("status") or "").upper()
    if state in {"SKIPPING", "SKIPPED"}:
        return True
    if state != "DOWNLOADING":
        return True

    check_status = str(current.get("speed_check_status") or "").upper()
    if check_status in {"PASSED", "SKIP_FAILED", "SKIPPED"}:
        return False

    now_text = config.now_iso()
    now_dt = _parse_speed_check_timestamp(now_text) or datetime.now(timezone.utc)
    started_dt = _parse_speed_check_timestamp(current.get("speed_check_started_at"))
    if started_dt is None:
        started_dt = now_dt
        _upsert(
            ctx.db, item_key,
            speed_check_started_at=started_dt.isoformat(timespec="seconds"),
            speed_check_completed_at="", speed_check_status="CHECKING",
            speed_check_rate_sum=0.0, speed_check_sample_count=0,
            speed_check_average_rate=0,
        )
        current = _get(ctx.db, item_key) or current

    elapsed = max(0.0, (now_dt - started_dt).total_seconds())
    current_rate = max(0, int(getattr(torrent, "download_rate", 0) or 0))
    count = max(0, int(current.get("speed_check_sample_count") or 0))
    rate_sum = max(0.0, float(current.get("speed_check_rate_sum") or 0.0))

    # Do not fail a very small release that finishes within the minute; its rate
    # naturally falls to zero after completion, but it no longer needs a slot.
    if float(getattr(torrent, "progress", 0.0) or 0.0) >= 0.9999:
        average = int(rate_sum / count) if count else current_rate
        _upsert(
            ctx.db, item_key, speed_check_status="PASSED",
            speed_check_completed_at=now_text, speed_check_average_rate=average,
        )
        return False

    rate_sum += current_rate
    count += 1
    average = int(rate_sum / count) if count else 0
    _upsert(
        ctx.db, item_key, speed_check_status="CHECKING",
        speed_check_rate_sum=rate_sum, speed_check_sample_count=count,
        download_rate=current_rate, speed_check_average_rate=average,
    )

    if elapsed < INITIAL_SPEED_CHECK_WINDOW_SECONDS:
        return False

    if average >= INITIAL_SPEED_CHECK_MIN_BYTES_PER_SECOND:
        _upsert(
            ctx.db, item_key, speed_check_status="PASSED",
            speed_check_completed_at=now_text, speed_check_average_rate=average,
        )
        ctx.db.log(
            f"Content Automation: initial torrent speed check passed · title={current.get('title') or item_key!r} "
            f"average={average / 1024.0:.0f} KB/s threshold=300 KB/s window=60s",
            "INFO", event="content_automation.speed_check_passed", job_id=getattr(ctx, "job_id", None),
            details={"automation_item": item_key, "average_bytes_per_second": average,
                     "threshold_bytes_per_second": INITIAL_SPEED_CHECK_MIN_BYTES_PER_SECOND,
                     "window_seconds": INITIAL_SPEED_CHECK_WINDOW_SECONDS},
        )
        return False

    # Claim the skip transition before cleanup to prevent a parallel poll/operator
    # action from advancing the same item to validation or upload.
    with ctx.db.lock:
        changed = ctx.db.conn.execute(
            "UPDATE automation_items SET status='SKIPPING',download_state=?,error=?,speed_check_status='SKIPPING',"
            "speed_check_completed_at=?,speed_check_average_rate=?,updated_at=? "
            "WHERE item_key=? AND status='DOWNLOADING'",
            ("Skipping: download speed below 300 KB/s", "Automatic speed check: below 300 KB/s during first minute",
             now_text, average, now_text, item_key),
        )
        ctx.db.conn.commit()
    if changed.rowcount != 1:
        return True

    avg_kib = average / 1024.0
    message = f"Automatic speed check: average {avg_kib:.0f} KB/s below 300 KB/s during the first 60 seconds"
    current = _get(ctx.db, item_key) or current
    try:
        cleaned = await _cleanup_controlled_item(ctx.db, current)
    except Exception as exc:
        cleaned = False
        cleanup_error = f"{type(exc).__name__}: {exc}"
    else:
        cleanup_error = "Torrent is still present in the download engine"

    if cleaned is False:
        detail = f"Automatic slow-speed skip failed: {cleanup_error}"
        with ctx.db.lock:
            ctx.db.conn.execute(
                "UPDATE automation_items SET status='DOWNLOADING',download_state=?,error=?,speed_check_status='SKIP_FAILED',updated_at=? "
                "WHERE item_key=? AND status='SKIPPING'",
                ("Downloading · slow-speed auto-skip failed; use Skip", detail, config.now_iso(), item_key),
            )
            ctx.db.conn.commit()
        ctx.db.log(
            f"Content Automation: automatic slow-speed skip failed for {current.get('title') or item_key}: {cleanup_error}",
            "WARN", event="content_automation.speed_check_skip_failed", job_id=getattr(ctx, "job_id", None),
            details={"automation_item": item_key, "average_bytes_per_second": average},
        )
        return True

    _upsert(
        ctx.db, item_key, status="SKIPPED", error=message,
        download_state=TASK_LABELS["SKIPPED"], speed_check_status="SKIPPED",
        speed_check_completed_at=now_text, speed_check_average_rate=average,
        download_rate=0, eta_seconds=-1, retry_stage="", next_retry_at="", failure_kind="",
    )
    ctx.db.log(
        f"Content Automation: automatically skipped slow torrent · title={current.get('title') or item_key!r} "
        f"average={avg_kib:.0f} KB/s threshold=300 KB/s window=60s; continuing queue",
        "WARN", event="content_automation.slow_torrent_skipped", job_id=getattr(ctx, "job_id", None),
        details={"automation_item": item_key, "average_bytes_per_second": average,
                 "threshold_bytes_per_second": INITIAL_SPEED_CHECK_MIN_BYTES_PER_SECOND,
                 "window_seconds": INITIAL_SPEED_CHECK_WINDOW_SECONDS},
    )
    return True


async def _torrent_dead_or_stalled(ctx: JobContext, cfg: dict, engine, row: dict, torrent) -> bool:
    """Dead/stalled torrent rules (V29.4.130); True when the item was failed.

    The rule download managers such as tclone_bot use: judge a torrent by
    *progress*, not by total time. A magnet that gets no metadata within
    ``torrent_metadata_timeout_minutes``, or a torrent whose progress has not
    moved for ``torrent_stall_timeout_minutes``, is failed and paused so it
    stops holding a download slot. A torrent stuck just short of 100% is
    re-verified once (a recheck often finds the "missing" pieces already on
    disk) before that rule applies.
    """
    now = _clock()
    watch = _run_cache(ctx).setdefault("stall_watch", {})
    key = str(getattr(torrent, "hash", "") or row.get("torrent_hash") or row.get("item_key"))
    progress = float(getattr(torrent, "progress", 0.0) or 0.0)
    entry = watch.get(key)
    if entry is None or now - entry["polled"] > STALL_WATCH_GAP_SECONDS:
        entry = {"mark": progress, "since": now, "polled": now, "rechecked": bool(entry and entry.get("rechecked"))}
        watch[key] = entry
    entry["polled"] = now
    if progress > entry["mark"] + 1e-6 or progress < entry["mark"] - 1e-6:
        # Rising progress is activity; so is a drop (a recheck found bad
        # pieces and the torrent is downloading them again) -- the new,
        # lower value becomes the mark to beat.
        entry["mark"] = progress
        entry["since"] = now
    idle_minutes = (now - entry["since"]) / 60.0
    title = row.get("title") or row.get("item_key")

    async def _give_up(message: str) -> bool:
        watch.pop(key, None)
        try:
            await engine.pause(torrent.hash)
        except Exception:
            pass
        _fail_item(ctx.db, row["item_key"], message, retryable=False)
        return True

    if not getattr(torrent, "has_metadata", True):
        limit = int(cfg.get("torrent_metadata_timeout_minutes", 30) or 0)
        if limit and idle_minutes >= limit:
            return await _give_up(
                f"Dead torrent: no metadata after {limit} minutes (no peers are sharing it)"
            )
        return False

    if progress >= 0.9999:
        return False
    limit = int(cfg.get("torrent_stall_timeout_minutes", 120) or 0)
    if not limit or idle_minutes < limit:
        return False
    if progress >= NEAR_COMPLETE_PROGRESS and not entry["rechecked"]:
        entry["rechecked"] = True
        entry["since"] = now
        try:
            done = await engine.recheck(torrent.hash)
        except Exception as exc:
            done = False
            ctx.db.log(f"Content Automation: {title}: recheck failed: {exc}", "WARN")
        if done:
            ctx.db.log(f"Content Automation: {title}: stalled at {progress:.2%}; re-verifying downloaded data once", "WARN")
            return False
    return await _give_up(
        f"Stalled torrent: no download progress for {limit} minutes (stuck at {progress:.1%})"
    )




# ==============================================================================
# SECTION: Single-item processing pipeline
# State machine for one item, stages 1 to 6.
# ==============================================================================

async def _advance_active_item(ctx: JobContext, cfg: dict, engine, row: dict, root: Path, *, download_only: bool = False) -> None:
    """Advance one automation item through its state machine.

    When ``download_only`` is true, only the DOWNLOADING stage is polled. A
    completed torrent is moved to VALIDATING and the function returns so the
    serialized post-download pipeline can process it separately. This prevents
    multiple concurrently-finished torrents from converting/uploading at once.
    """
    item_key = row["item_key"]

    # A per-item Skip can be requested while an engine/provider operation is
    # awaiting. Re-read durable state rather than trusting this poll's snapshot.
    if _item_skip_requested(ctx.db, item_key):
        return

    # --- Stage 1: downloading -----------------------------------------
    if row.get("status") == "DOWNLOADING":
        torrent_hash = str(row.get("torrent_hash") or "")
        if not torrent_hash:
            _fail_item(ctx.db, item_key, "No torrent hash recorded for this item", retryable=False)
            return
        torrent = await _torrent_get_with_recovery(ctx, engine, row, root, torrent_hash)
        if _item_skip_requested(ctx.db, item_key):
            return
        if not torrent and row.get("torrent_url"):
            # A process restart creates a fresh libtorrent session. Re-attach
            # the persistent item to the same torrent source so completed/
            # partial data already on disk can be reused instead of failing.
            try:
                restored_hash = await engine.add(row["torrent_url"], save_path=str(root))
                if _item_skip_requested(ctx.db, item_key):
                    return
                if restored_hash and restored_hash.lower() != torrent_hash.lower():
                    torrent_hash = restored_hash
                    _upsert(ctx.db, item_key, torrent_hash=restored_hash)
                torrent = await engine.get(torrent_hash)
                if _item_skip_requested(ctx.db, item_key):
                    return
            except Exception as exc:
                flood_delay = _flood_wait_delay(exc)
                _fail_item(ctx.db, item_key, f"Could not restore torrent after restart: {exc}", retryable=True, retry_stage="DOWNLOADING", retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
                return
        if not torrent:
            _fail_item(ctx.db, item_key, "Torrent is no longer present in the download engine", retryable=False)
            return

        # A completed Paused/Stopped torrent must be accepted without resuming it.
        # Inspect the client/file list first so operator pause state never causes
        # an already-complete download to restart.
        try:
            early_files = await engine.files(torrent.hash) if torrent.has_metadata else []
        except Exception:
            early_files = []
        if _item_skip_requested(ctx.db, item_key):
            return
        if torrent.has_metadata and early_files:
            manifest_name = _pick_video_manifest_name(early_files)
            if manifest_name:
                _start_identity_preflight(ctx, row, manifest_name)
        if torrent.has_metadata and _torrent_content_verified(torrent, early_files):
            early_video = _pick_local_video(torrent, early_files, root)
            if early_video and early_video.is_file():
                _upsert(ctx.db, item_key, status="VALIDATING", local_path=str(early_video),
                        torrent_content_path=str(torrent.content_path or torrent.save_path or root),
                        torrent_state=str(torrent.state or ""), torrent_verified_at=config.now_iso(),
                        download_progress=1.0, download_state=TASK_LABELS["VALIDATING"], error="", failure_kind="", retry_stage="", next_retry_at="")
                ctx.db.log(f"Content Automation: accepted completed torrent without resuming · hash={torrent.hash} state={torrent.state} progress={float(torrent.progress):.1%} file={early_video}", "INFO", event="content_automation.torrent_ready", job_id=ctx.job_id)
                return

        # A newly-added magnet legitimately starts in the metadata-resolving
        # state. Start/resume the torrent first, then apply video-only file
        # priorities once metadata exists. The old order treated
        # ``select_video_only() == False`` as a hard failure and therefore
        # stopped every magnet before its metadata could arrive.
        cache = _run_cache(ctx)
        needs_start = (torrent.hash not in cache["prepared"]
                       or bool(getattr(torrent, "paused", False))
                       or str(getattr(torrent, "state", "")) in {"paused", "queued", "error"})
        try:
            prepare = getattr(engine, "prepare_download", None)
            if prepare is None or not needs_start:
                # Already started and configured: just watch it. Every poll
                # used to force-start it again, re-apply file priorities and
                # write a log line (about a dozen engine calls per torrent
                # every few seconds).
                prep = {}
                raise _AlreadyPrepared()
            prepared = prepare(torrent.hash)
            prep = await prepared if inspect.isawaitable(prepared) else (prepared or {})
            if _item_skip_requested(ctx.db, item_key):
                return
            cache["prepared"].add(torrent.hash)
            ctx.db.log(
                f"Content Automation: torrent prepared {torrent.hash} "
                f"state={prep.get('state', 'unknown')} sequential={prep.get('sequential')} "
                f"first_last_piece={prep.get('first_last_piece')} "
                f"peers={prep.get('peers', 0)} seeds={prep.get('seeds', 0)} "
                f"rate={prep.get('download_rate', 0)} detail={prep.get('detail', '')}",
                "INFO",
            )
        except _AlreadyPrepared:
            pass
        except Exception as exc:
            cache["prepared"].discard(torrent.hash)
            flood_delay = _flood_wait_delay(exc)
            _fail_item(ctx.db, item_key, f"Torrent did not start: {exc}", retryable=True, retry_stage="DOWNLOADING", retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
            return

        # Refresh immediately after start/resume. If the torrent is still
        # waiting for magnet metadata, that is a normal in-progress state: keep
        # DOWNLOADING and let the next five-second poll try file selection
        # again. Do not fail the release while metadata is still resolving.
        refreshed = await _torrent_get_with_recovery(ctx, engine, row, root, torrent.hash)
        if _item_skip_requested(ctx.db, item_key):
            return
        if refreshed is not None:
            torrent = refreshed
        if await _update_initial_speed_check(ctx, row, torrent):
            return
        if not torrent.has_metadata:
            dead = await _torrent_dead_or_stalled(ctx, cfg, engine, row, torrent)
            if _item_skip_requested(ctx.db, item_key):
                return
            if dead:
                return
            detail_parts = [str(prep.get("detail") or "Waiting for torrent metadata")]
            gate_detail = _initial_speed_check_detail(_get(ctx.db, item_key) or row)
            if gate_detail:
                detail_parts.append(gate_detail)
            detail = " · ".join(part for part in detail_parts if part)
            _upsert(
                ctx.db, item_key,
                download_progress=float(torrent.progress),
                download_state=f"{TASK_LABELS['DOWNLOADING']} · {detail}",
                download_rate=int(torrent.download_rate),
                eta_seconds=int(torrent.eta),
                error=torrent.error or "",
            )
            return

        # Metadata is ready. First determine whether qBittorrent has already
        # completed the torrent. qBittorrent reports a fully downloaded torrent
        # as ``stalledUP``/``uploading`` (mapped to ``seeding`` by the engine).
        # The previous implementation checked completion only through
        # ``progress`` after another prepare/priority cycle, which could leave a
        # completed torrent stuck in DOWNLOADING forever.
        try:
            files = await engine.files(torrent.hash)
        except Exception:
            files = []
        if _item_skip_requested(ctx.db, item_key):
            return

        if files:
            manifest_name = _pick_video_manifest_name(files)
            if manifest_name:
                _start_identity_preflight(ctx, row, manifest_name)

        video_path = _pick_local_video(torrent, files, root)
        completed_verified = _torrent_content_verified(torrent, files)

        # A torrent is ready only when the client reports a terminal/non-download
        # state and every supported video file reports complete progress. This
        # intentionally accepts 100% Paused/Stopped/Seeding, but rejects hash
        # checking, missing-files, errors, and partial content.
        if video_path and video_path.exists() and completed_verified:
            if _item_skip_requested(ctx.db, item_key):
                return
            _upsert(
                ctx.db, item_key, status="VALIDATING", local_path=str(video_path),
                torrent_content_path=str(torrent.content_path or torrent.save_path or root),
                torrent_state=str(torrent.state or ""), torrent_verified_at=config.now_iso(),
                download_progress=1.0, download_state=TASK_LABELS["VALIDATING"],
                error="", failure_kind="", retry_stage="", next_retry_at="",
            )
            ctx.db.log(
                f"Content Automation: completed torrent handed to post-download pipeline "
                f"title={row.get('title')!r} hash={torrent.hash} state={torrent.state} "
                f"progress={float(torrent.progress):.1%} file={video_path}",
                "INFO",
            )
            return

        # Torrent is not complete yet. Apply video-only priorities so only the
        # supported video receives download priority.
        just_selected = False
        if torrent.hash not in cache["selected"]:
            try:
                selected = await engine.select_video_only(torrent.hash)
                if _item_skip_requested(ctx.db, item_key):
                    return
                if not selected:
                    _fail_item(ctx.db, item_key, "Torrent metadata is ready but no supported video file was found", retryable=False)
                    return
                cache["selected"].add(torrent.hash)
                just_selected = True
            except Exception as exc:
                _fail_item(ctx.db, item_key, f"File selection failed: {exc}", retryable=False)
                return

        # Apply the download strategy again after file priorities exist so the
        # active video receives sequential/first-last piece treatment (once).
        try:
            prepare = getattr(engine, "prepare_download", None)
            if prepare is not None and just_selected:
                prepared = prepare(torrent.hash)
                prep = await prepared if inspect.isawaitable(prepared) else (prepared or {})
                if _item_skip_requested(ctx.db, item_key):
                    return
        except Exception as exc:
            flood_delay = _flood_wait_delay(exc)
            _fail_item(ctx.db, item_key, f"Torrent did not start: {exc}", retryable=True, retry_stage="DOWNLOADING", retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
            return

        # Refresh state after start/resume so the UI reflects the actual state.
        refreshed = await _torrent_get_with_recovery(ctx, engine, row, root, torrent.hash)
        if _item_skip_requested(ctx.db, item_key):
            return
        if refreshed is not None:
            torrent = refreshed

        detail_parts = [str(prep.get("detail") or torrent.error or "")]
        gate_detail = _initial_speed_check_detail(_get(ctx.db, item_key) or row)
        if gate_detail:
            detail_parts.append(gate_detail)
        detail = " · ".join(part for part in detail_parts if part)
        _upsert(
            ctx.db, item_key,
            download_progress=float(torrent.progress),
            download_state=(TASK_LABELS["DOWNLOADING"] + (f" · {detail}" if detail else "")),
            download_rate=int(torrent.download_rate), eta_seconds=int(torrent.eta), error=torrent.error or "",
        )
        if torrent.progress < 0.9999:
            await _torrent_dead_or_stalled(ctx, cfg, engine, row, torrent)
            if _item_skip_requested(ctx.db, item_key):
                return
            return  # still downloading -- nothing further to do this poll

        # Final completion check after the priority/start refresh.
        try:
            files = await engine.files(torrent.hash)
        except Exception:
            files = []
        if _item_skip_requested(ctx.db, item_key):
            return
        video_path = _pick_local_video(torrent, files, root)
        if not video_path:
            _fail_item(ctx.db, item_key, "Completed torrent contains no supported video file", retryable=False)
            return
        if not _torrent_content_verified(torrent, files) or not video_path.is_file():
            _upsert(ctx.db, item_key, download_progress=float(torrent.progress), download_state=f"{TASK_LABELS['DOWNLOADING']} · verifying completed content")
            return
        if _item_skip_requested(ctx.db, item_key):
            return
        _upsert(ctx.db, item_key, status="VALIDATING", local_path=str(video_path),
                torrent_content_path=str(torrent.content_path or torrent.save_path or root),
                torrent_state=str(torrent.state or ""), torrent_verified_at=config.now_iso(),
                download_progress=1.0, download_state=TASK_LABELS["VALIDATING"])
        ctx.db.log(
            f"Content Automation: torrent content verified and handed to post-download pipeline "
            f"title={row.get('title')!r} hash={torrent.hash} state={torrent.state} progress={float(torrent.progress):.1%} file={video_path}",
            "SUCCESS", event="content_automation.torrent_ready", job_id=ctx.job_id,
        )
        return

    # --- Recovery preflight for persisted post-download states -----------
    # If the application restarts, a user moves/deletes a file, qBittorrent is
    # reset, or a temporary conversion cache disappears, repair the state from
    # the persisted torrent/local paths before attempting the next stage.
    current_status = str(row.get("status") or "")
    if current_status in {"VALIDATING", "CONVERTING", "METADATA_ASSETS", "READY_TO_TRANSFER", "TRANSFERRING_VIDEO", "TRANSFERRING_THUMBNAIL", "VERIFYING"}:
        raw_value = str(row.get("local_path") or "").strip()
        raw_path = Path(raw_value) if raw_value else None
        if raw_path is None or not raw_path.is_file():
            max_downloads = max(1, min(10, int(cfg.get("max_concurrent_torrents") or 1)))
            recovered = await _restore_download_for_item(ctx, engine, row, root, max_downloads=max_downloads)
            if recovered is None:
                return
            row = _get(ctx.db, item_key) or row
            current_status = str(row.get("status") or "")
            if current_status != "VALIDATING":
                return
        else:
            # Persisted local_path is good. Do not force a torrent restore just
            # because qBittorrent is temporarily unavailable; post-download
            # processing can continue from the local source.
            row = _get(ctx.db, item_key) or row

        # A READY_TO_TRANSFER/TRANSFERRING/VERIFYING row may point to a converted cache
        # which was deleted independently. Re-enter CONVERTING when the original
        # raw source is available, rather than silently sending the wrong file.
        if current_status in {"READY_TO_TRANSFER", "TRANSFERRING_VIDEO", "TRANSFERRING_THUMBNAIL", "VERIFYING"}:
            raw_path = Path(str(row.get("local_path") or ""))
            converted_path = _converted_cache_path(raw_path)
            if not converted_path.is_file():
                _upsert(
                    ctx.db,
                    item_key,
                    status="CONVERTING",
                    retry_stage="",
                    next_retry_at="",
                    failure_kind="",
                    error="",
                    download_state=TASK_LABELS["CONVERTING"],
                )
                row = _get(ctx.db, item_key) or row

    # --- Stage 2: validate the finished download -----------------------
    if row.get("status") == "VALIDATING":
        video_path = Path(row["local_path"])
        ctx.activity(f"Validating completed download: {video_path.name}",
                     operation="Content Automation · validate downloaded file", current_item=video_path.name,
                     event="content_automation.validation_started",
                     details={"source_path": str(video_path), "source_bytes": int(video_path.stat().st_size) if video_path.is_file() else 0,
                              "torrent_hash": str(row.get("torrent_hash") or "")}, progress=True)
        await _wait_stable(video_path)
        ffmpeg, ffprobe = ffmpeg_tools()
        try:
            from ..core import internal_media
            meta = await ctx.blocking(internal_media.probe, video_path)
            # Match the legacy automation keys used by the UI.
            meta = {**meta, "codec": meta.get("video_codec", ""), "vcodec": meta.get("video_codec", ""), "acodec": meta.get("audio_codec", "")}
        except Exception as internal_probe_error:
            if not ffprobe:
                raise RuntimeError(f"Internal PyAV probe failed and external ffprobe is unavailable: {internal_probe_error}")
            meta = await ctx.blocking(probe, ffprobe, video_path)
        meta = dict(meta or {})
        meta["codec"] = meta.get("video_codec") or meta.get("codec") or ""
        meta["vcodec"] = meta.get("video_codec") or meta.get("codec") or ""
        meta["acodec"] = meta.get("audio_codec") or meta.get("acodec") or ""
        resolution = f"{meta['width']}x{meta['height']}"
        ctx.activity(f"Download metadata resolved: {video_path.name} · {resolution} · {meta.get('video_codec') or meta.get('codec') or 'unknown'}",
                     operation="Content Automation · source metadata verified", current_item=video_path.name,
                     event="content_automation.validation_metadata",
                     details={"source_filename": video_path.name, "source_path": str(video_path),
                              "file_size_bytes": int(video_path.stat().st_size),
                              "metadata": {k: meta.get(k) for k in ("video_codec", "audio_codec", "codec", "vcodec", "acodec", "width", "height", "fps", "duration", "bitrate", "format_name", "container") if k in meta}}, progress=True)
        # No resolution check here: "1080p only" is applied to the release
        # NAME before download. A downloaded release is always delivered
        # (it used to be deleted when the picture was e.g. 1920x800).
        raw_oshash = str(row.get("raw_oshash") or "").strip()
        if not raw_oshash:
            try:
                raw_oshash = await ctx.blocking(oshash_file, str(video_path))
                _upsert(ctx.db, item_key, raw_oshash=raw_oshash)
            except Exception as exc:
                ctx.db.log(f"Content Automation: raw OSHASH failed for {video_path.name}: {type(exc).__name__}: {exc}", "WARN")
        probe_signature = _media_file_signature(video_path)
        _upsert(ctx.db, item_key, status="CONVERTING", resolution=resolution, error="",
                download_progress=0.0, download_state=TASK_LABELS["CONVERTING"], raw_oshash=raw_oshash or None,
                media_metadata_json=json.dumps(meta, ensure_ascii=False, separators=(",", ":")),
                media_metadata_path=probe_signature["path"], media_metadata_size=probe_signature["size"],
                media_metadata_mtime_ns=probe_signature["mtime_ns"])
        row = _get(ctx.db, item_key) or row

    # --- Stage 3: convert to a streamable MP4 ---------------------------
    # local_path always stays the ORIGINAL raw download; the converted
    # file is a deterministically-named sibling, so cleanup later can find
    # both without needing a separate DB column.
    if row.get("status") == "CONVERTING":
        raw_path = Path(row["local_path"])
        converted_path = _converted_cache_path(raw_path)
        ctx.activity(f"Checking stream compatibility for {raw_path.name}",
                     operation="Content Automation · stream compatibility check", current_item=raw_path.name,
                     event="content_automation.stream_compatibility_started",
                     details={"source_path": str(raw_path), "output_path": str(converted_path)}, progress=True)

        # Do not remux/transcode an MP4/M4V/MOV that is already H.264/HEVC +
        # AAC (or no audio). The finished torrent can go straight to Telegram.
        try:
            ffmpeg, ffprobe = ffmpeg_tools()
            # probe() uses the app's built-in PyAV engine first and ffprobe only
            # as a fallback. Demanding ffprobe here made every release fail at
            # this step on PCs without a separate FFmpeg install.
            meta_ready, _raw_signature = await _probe_metadata_for_item(ctx, row, raw_path, ffprobe_fallback=ffprobe)
            def _progress_cb(pct, key=item_key):
                # Conversion helpers report a 0.0-1.0 fraction (PyAV and FFmpeg
                # paths); older callers passed an integer percent. Accept both.
                value = float(pct or 0)
                fraction = value if value <= 1.0 else value / 100.0
                fraction = max(0.0, min(1.0, fraction))
                _upsert(ctx.db, key, download_progress=fraction,
                        download_state=f"{TASK_LABELS['CONVERTING']} ({int(round(fraction * 100))}%)")

            direct_streamable = is_telegram_direct_streamable_meta(meta_ready, filename=raw_path.name)
            if direct_streamable:
                ctx.activity(f"Remuxing {raw_path.name} with faststart (stream copy; no re-encoding)",
                             operation="Content Automation · faststart remux", current_item=raw_path.name,
                             event="content_automation.faststart_remux_started",
                             details={"source_path": str(raw_path), "output_path": str(converted_path)}, progress=True)
            else:
                ctx.activity(f"Converting {raw_path.name} to a streamable MP4", operation="Content Automation · media conversion", current_item=raw_path.name,
                             event="content_automation.conversion_started", details={"source_path": str(raw_path), "output_path": str(converted_path)}, progress=True)
            # The conversion helper stream-copies already compatible inputs with
            # -movflags +faststart; other formats are normalized as needed.
            await convert_to_streamable_mp4(raw_path, converted_path, on_progress=_progress_cb)
            ctx.activity(f"Streamable MP4 preparation completed: {converted_path.name}", operation="Content Automation · conversion completed", current_item=converted_path.name,
                         event="content_automation.conversion_completed", details={"source_path": str(raw_path), "output_path": str(converted_path),
                         "output_bytes": int(converted_path.stat().st_size) if converted_path.is_file() else 0,
                         "mode": "faststart-stream-copy" if direct_streamable else "normalized"}, progress=True)
            _upsert(ctx.db, item_key, status="READY_TO_TRANSFER", download_progress=1.0,
                    download_state="Faststart remux ready" if direct_streamable else TASK_LABELS["READY_TO_TRANSFER"], error="")
            row = _get(ctx.db, item_key) or row
        except Exception as exc:
            try:
                if converted_path.exists():
                    converted_path.unlink()
            except Exception:
                pass
            flood_delay = _flood_wait_delay(exc)
            _fail_item(ctx.db, item_key, f"Local media preparation failed (not a network problem): {exc}", retryable=True, retry_stage="CONVERTING", retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
            return

    # --- Stage 4: Metadata & Assets commit before transfer --------------
    if row.get("status") in {"METADATA_ASSETS", "READY_TO_TRANSFER"}:
        raw_path = Path(str(row.get("local_path") or ""))
        converted_path = _converted_cache_path(raw_path)
        prepared_path = converted_path if converted_path.is_file() else raw_path
        ctx.activity(f"Preparing metadata, entity links and 36 snapshot frames for {prepared_path.name}",
                     operation="Content Automation · metadata, entities and snapshots", current_item=prepared_path.name,
                     event="content_automation.metadata_assets_stage_started",
                     details={"source_path": str(prepared_path), "source_filename": prepared_path.name,
                              "source_bytes": int(prepared_path.stat().st_size) if prepared_path.is_file() else 0,
                              "snapshot_frames": 36,
                              "next_owner": "Telegram Transfer Manager creates/reuses the 6×6 delivery contact sheet"}, progress=True)
        try:
            await _prepare_metadata_assets(ctx, cfg, row, prepared_path)
        except Exception as exc:
            current = _get(ctx.db, item_key) or row
            retry_stage = "METADATA_ASSETS"
            flood_delay = _flood_wait_delay(exc)
            _fail_item(ctx.db, item_key, f"Metadata & Assets preparation failed: {exc}", retryable=True, retry_stage=retry_stage, retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
            return
        row = _get(ctx.db, item_key) or row
        ctx.activity(f"Metadata and snapshot preparation stage completed for {prepared_path.name}",
                     operation="Content Automation · metadata assets ready for transfer", current_item=prepared_path.name,
                     event="content_automation.metadata_assets_stage_completed",
                     details={"automation_item": item_key, "media_id": row.get("media_id"), "snapshot_path": row.get("snapshot_path"), "caption": str(row.get("caption") or "")[:2000]}, progress=True)

    # --- Stage 5: Telegram Transfer Manager delivery + verification ------
    if row.get("status") in {"READY_TO_TRANSFER", "TRANSFERRING_VIDEO", "TRANSFERRING_THUMBNAIL", "VERIFYING"}:
        raw_path = Path(row["local_path"])
        converted_path = _converted_cache_path(raw_path)
        send_path = converted_path if converted_path.exists() else raw_path
        ctx.activity(f"Beginning verified Telegram transfer for {send_path.name}",
                     operation="Content Automation · Telegram transfer and verification", current_item=send_path.name,
                     event="content_automation.transfer_stage_started",
                     details={"source_path": str(send_path), "file_size_bytes": int(send_path.stat().st_size) if send_path.is_file() else 0,
                              "destination_channel": str(cfg.get("destination_channel") or ""),
                              "automation_item": item_key, "caption": str(row.get("caption") or "")[:2000]}, progress=True)
        try:
            await _publish(ctx, cfg, row, send_path)
        except Exception as exc:
            current = _get(ctx.db, item_key) or row
            retry_stage = str(current.get("status") or "READY_TO_TRANSFER")
            permanent = isinstance(exc, ValueError) and "Destination verification permanent failure:" in str(exc)
            flood_delay = _flood_wait_delay(exc)
            if not (ctx.telegram.client is not None and ctx.telegram.connected):
                # Telegram offline (PC just woke up, network drop): wait for
                # the reconnect instead of burning the 4 attempts and giving up.
                _fail_item(ctx.db, item_key, f"Waiting for Telegram connection: {exc}", retryable=True,
                           retry_stage=retry_stage, retry_after_seconds=60, consume_retry_budget=False)
                return
            _fail_item(ctx.db, item_key, f"{type(exc).__name__}: {exc}", retryable=not permanent, retry_stage=retry_stage,
                       retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
            return
        row = _get(ctx.db, item_key) or row
        if str(row.get("status") or "").upper() == "SKIPPED":
            ctx.activity(f"Skipped oversized file {send_path.name}; source retained by policy",
                         operation="Content Automation · oversized upload skipped", current_item=send_path.name,
                         event="content_automation.oversized_upload_skipped",
                         details={"automation_item": item_key, "state": "SKIPPED"}, progress=True)
            return
        ctx.activity(f"Verified Telegram transfer completed for {send_path.name}",
                     operation="Content Automation · transfer verified", current_item=send_path.name,
                     event="content_automation.transfer_stage_completed",
                     details={"automation_item": item_key, "destination_video_id": row.get("destination_video_id"),
                              "destination_thumbnail_id": row.get("destination_thumbnail_id"), "state": row.get("status")}, progress=True)

    # --- Stage 6: cleanup before the next queue item can start ----------
    if row.get("status") == "CLEANUP":
        local_text = str(row.get("local_path") or "").strip()
        raw_path = Path(local_text) if local_text else None
        converted_path = _converted_cache_path(raw_path) if raw_path else None
        # Playback assets are Archive-X-owned reusable data and remain after
        # source cleanup. Only the torrent payload and conversion work copy are
        # deleted here.

        def _inside_root(p: Path | None) -> bool:
            # Never delete anything outside the automation download folder
            # (an empty path used to resolve to the current directory).
            if p is None:
                return False
            try:
                return p.resolve().is_relative_to(root.resolve()) and p.resolve() != root.resolve()
            except Exception:
                return False
        try:
            torrent_hash = str(row.get("torrent_hash") or "").strip()
            if torrent_hash:
                # The torrent client owns the source until delivery has been verified.
                # Remove the torrent first so it cannot recreate a deleted file.
                _upsert(ctx.db, item_key, source_cleanup_state="TORRENT_REMOVAL_PENDING", download_state="Removing torrent from download engine")
                ctx.db.log(f"Content Automation: removing verified torrent before source deletion · hash={torrent_hash} content={row.get('torrent_content_path') or ''}", "INFO", event="content_automation.torrent_removal_started", job_id=ctx.job_id)
                await engine.remove(torrent_hash, delete_files=True)
                try:
                    lingering = await engine.get(torrent_hash)
                except Exception as exc:
                    raise RuntimeError(f"Could not verify qBittorrent removal for {torrent_hash}: {exc}") from exc
                if lingering is not None:
                    raise RuntimeError(f"qBittorrent still owns torrent {torrent_hash} after removal request")
                _upsert(ctx.db, item_key, source_cleanup_state="FILE_DELETION_PENDING")
                ctx.db.log(f"Content Automation: torrent removed and verified; deleting downloaded source files · hash={torrent_hash}", "INFO", event="content_automation.torrent_removed", job_id=ctx.job_id)
            for local_path in (raw_path, converted_path):
                if local_path is not None and local_path.exists() and local_path.is_file():
                    if not _inside_root(local_path):
                        ctx.db.log(f"Content Automation: refused to delete {local_path} (outside the download folder)", "WARN")
                        continue
                    deleted = await _delete_verified_source_file(local_path, ctx.db.log)
                    if not deleted:
                        raise RuntimeError(f"Local cleanup could not delete {local_path}")
            if raw_path is not None and _inside_root(raw_path.parent) and raw_path.parent.exists():
                try:
                    raw_path.parent.rmdir()
                except OSError:
                    pass
        except FileNotFoundError:
            pass
        except Exception as exc:
            _fail_item(ctx.db, item_key, f"Cleanup failed: {exc}", retryable=True, retry_stage="CLEANUP", consume_retry_budget=False)
            ctx.db.log(f"Content Automation: cleanup blocked queue advancement for {item_key}: {exc}", "WARN")
            return

        # Non-1080p items use CLEANUP and then become SKIPPED. Delivered items
        # become COMPLETED only after the local/qBittorrent cleanup succeeds.
        final_status = "SKIPPED" if str(row.get("error") or "").startswith("Not 1080p:") else "COMPLETED"
        _upsert(ctx.db, item_key, status=final_status, source_cleanup_state="CLEANED", content_plan_status=("PUBLISHED" if final_status == "COMPLETED" else "SKIPPED"), error=("" if final_status == "COMPLETED" else str(row.get("error") or "")),
                failure_kind="", next_retry_at="", download_progress=1.0, download_state=TASK_LABELS[final_status])




# ==============================================================================
# SECTION: External engine lifecycle and automation supervisor
# Engine control and the main run loop.
# ==============================================================================

async def pause_qbittorrent_automation_torrents(db) -> int:
    """Pause Archive X-owned qBittorrent torrents without starting qBittorrent.

    This is called during application boot so an external qBittorrent process
    cannot continue a previously active automation item while Content
    Automation itself is waiting for an explicit operator start. Credentials
    already stored in the application config are reused; they are never
    regenerated or overwritten.
    """
    try:
        cfg = get_config(db, masked=False)
        if str(cfg.get("torrent_backend") or "qbittorrent").lower() != "qbittorrent":
            return 0
        engine = QBittorrentEngine(
            base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
            username=cfg.get("qbittorrent_username") or "",
            password=cfg.get("qbittorrent_password") or "",
            api_key=cfg.get("qbittorrent_api_key") or "",
            auto_start=False,
            auto_install=False,
            auto_configure_local_webui=False,
        )
        try:
            if not await engine._supervisor.reachable():
                return 0
            paused = await engine.pause_all_automation_torrents()
            if paused:
                db.log(f"Content Automation: boot safety paused {paused} Archive X qBittorrent torrent(s); automation remains stopped", "INFO")
            return paused
        finally:
            await engine.close()
    except Exception as exc:
        # qBittorrent being unavailable must never prevent the archive UI from
        # starting. The next explicit automation start will perform normal
        # dependency startup/connection.
        try:
            db.log(f"Content Automation: boot qBittorrent safety check skipped: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            pass
        return 0


async def ensure_qbittorrent(cfg: dict[str, Any], *, db=None) -> dict[str, Any]:
    """Make the local qBittorrent dependency available without UI intervention."""
    supervisor = QBittorrentSupervisor(
        cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
        auto_start=bool(cfg.get("qbittorrent_auto_start", True)),
        auto_install=bool(cfg.get("qbittorrent_auto_install", True)),
        auto_configure_local_webui=bool(cfg.get("qbittorrent_auto_configure_local_webui", True)),
    )
    try:
        result = await supervisor.ensure_ready()
        # Explicit automation startup should also bring the desktop qBittorrent
        # window up/forward on Windows. WebUI readiness alone is not enough for
        # an operator who needs to see the client and its active downloads.
        try:
            gui = await supervisor.launch_gui()
            result["gui_action"] = gui.get("action", "")
        except Exception as gui_exc:
            # GUI activation is helpful but must not block automation when a
            # qBittorrent WebUI is otherwise healthy. Record the issue clearly.
            result["gui_error"] = str(gui_exc)
            if db is not None:
                db.log(f"Content Automation: qBittorrent GUI activation warning: {type(gui_exc).__name__}: {gui_exc}", "WARN")
        if db is not None:
            try:
                engine = QBittorrentEngine(
                    base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
                    username=cfg.get("qbittorrent_username") or "",
                    password=cfg.get("qbittorrent_password") or "",
                    api_key=cfg.get("qbittorrent_api_key") or "",
                    auto_start=False, auto_install=False, auto_configure_local_webui=False,
                )
                try:
                    root = Path(str(cfg.get("download_path") or config.AUTOMATION_DOWNLOAD_DIR)).expanduser()
                    root.mkdir(parents=True, exist_ok=True)
                    await engine._ensure_category(ARCHIVE_X_QBIT_CATEGORY, str(root))
                finally:
                    await engine.close()
                db.log(f"Content Automation: qBittorrent category ready: {ARCHIVE_X_QBIT_CATEGORY}")
            except Exception as cat_exc:
                db.log(f"Content Automation: qBittorrent category check failed: {type(cat_exc).__name__}: {cat_exc}", "WARN")
            db.log(f"Content Automation: qBittorrent ready ({result.get('version') or 'running'}) · {result.get('action')}")
        return result
    except Exception as exc:
        if db is not None:
            db.log(f"Content Automation: qBittorrent auto-connect failed: {type(exc).__name__}: {exc}", "WARN")
        raise


async def _control_engine(db, cfg):
    backend = str(cfg.get("torrent_backend") or "qbittorrent").lower()
    if backend == "qbittorrent":
        return QBittorrentEngine(
            base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
            username=cfg.get("qbittorrent_username") or "",
            password=cfg.get("qbittorrent_password") or "",
            api_key=cfg.get("qbittorrent_api_key") or "",
            auto_start=False, auto_install=False, auto_configure_local_webui=False,
        )
    return LibtorrentWorkerEngine(
        metadata_dir=Path(cfg["torrent_engine_metadata"]).expanduser(),
        listen_port=cfg["torrent_listen_port"],
        enable_dht_upnp=cfg["torrent_enable_dht_upnp"],
        allow_incoming_peers=cfg["torrent_allow_incoming_peers"],
    )

async def _cleanup_controlled_item(db, row: dict[str, Any]) -> bool:
    cfg = get_config(db, masked=False)
    engine = None
    try:
        torrent_hash = str(row.get("torrent_hash") or "").strip()
        if torrent_hash:
            engine = await _control_engine(db, cfg)
            try:
                await engine.remove(torrent_hash, delete_files=True)
                lingering = await engine.get(torrent_hash)
                if lingering is not None:
                    raise RuntimeError(f"qBittorrent still owns torrent {torrent_hash} after removal request")
            except Exception as exc:
                # Never delete the local source while qBittorrent may still own it.
                db.log(f"Content Automation: controlled torrent cleanup blocked for {torrent_hash}: {exc}", "WARN")
                return False
        local_path = str(row.get("local_path") or "").strip()
        if local_path:
            raw = Path(local_path)
            candidates = (raw, _converted_cache_path(raw), _playback_snapshot_path(raw))
            for candidate in candidates:
                if candidate.exists() and candidate.is_file():
                    if not await _delete_verified_source_file(candidate, db.log):
                        db.log(f"Content Automation: controlled local cleanup could not remove {candidate}", "WARN")
    finally:
        if engine is not None:
            try:
                await engine.close()
            except Exception:
                pass
    return True

async def pause_active_torrent(db) -> dict[str, Any]:
    """Pause all currently active Content Automation torrents (safe for concurrency > 1)."""
    _ensure_schema(db)
    cfg = get_config(db, masked=False)
    with db.lock:
        rows = [dict(r) for r in db.conn.execute(
            "SELECT * FROM automation_items WHERE status='DOWNLOADING' AND torrent_hash IS NOT NULL AND torrent_hash!='' ORDER BY created_at ASC"
        ).fetchall()]
    if not rows:
        return {"ok": True, "paused": False, "items": []}
    engine = await _control_engine(db, cfg)
    paused = []
    try:
        for row in rows:
            try:
                await engine.pause(str(row["torrent_hash"]))
                paused.append(row)
            except Exception as exc:
                db.log(f"Content Automation: pause failed for {row.get('title')}: {type(exc).__name__}: {exc}", "WARN")
    finally:
        await engine.close()
    return {"ok": True, "paused": bool(paused), "items": paused, "count": len(paused)}


async def run(ctx: JobContext) -> None:
    """Content Automation pipeline with configurable download concurrency.

    ``max_concurrent_torrents`` controls how many torrents may be in the
    DOWNLOADING state at once. Once a torrent finishes, its post-download
    pipeline (validate -> prepare -> Telegram upload -> verify -> cleanup) is
    still serialized, so only one completed release is processed at a time.
    This lets qBittorrent keep the manually selected number of downloads moving
    while Telegram delivery work happens in the foreground pipeline.
    """
    _ensure_schema(ctx.db)
    cfg = _job_config(ctx)
    if not cfg["rss_url"]:
        raise RuntimeError("Configure an RSS feed URL in Operations → Content Automation")
    if not cfg["destination_channel"]:
        raise RuntimeError("Configure a Telegram destination channel in Operations → Content Automation")

    root = _ensure_owned_root_marker(cfg["download_path"], label="download")
    metadata_dir = _ensure_owned_root_marker(cfg["torrent_engine_metadata"], label="torrent-engine")
    backend = str(cfg.get("torrent_backend") or "qbittorrent").lower()
    ctx.activity("Content Automation starting; settings and durable queue loaded",
                 operation="Content Automation · initialize download backend", current_item="Automation queue",
                 event="content_automation.started",
                 details={"backend": backend, "download_path": str(root), "metadata_path": str(metadata_dir),
                          "destination_channel": str(cfg.get("destination_channel") or ""),
                          "max_concurrent_torrents": int(cfg.get("max_concurrent_torrents") or 1),
                          "rss_url_configured": bool(cfg.get("rss_url"))}, progress=True)
    engine = None
    try:
        if backend == "qbittorrent":
            qbt_url = cfg.get("qbittorrent_url") or "http://127.0.0.1:8080"
            if cfg.get("qbittorrent_auto_start", True):
                await ensure_qbittorrent(cfg, db=ctx.db)
            engine = QBittorrentEngine(
                base_url=qbt_url,
                username=cfg.get("qbittorrent_username") or "",
                password=cfg.get("qbittorrent_password") or "",
                api_key=cfg.get("qbittorrent_api_key") or "",
                auto_start=bool(cfg.get("qbittorrent_auto_start", True)),
                auto_install=bool(cfg.get("qbittorrent_auto_install", True)),
                auto_configure_local_webui=bool(cfg.get("qbittorrent_auto_configure_local_webui", True)),
            )
        else:
            engine = await asyncio.to_thread(
                LibtorrentWorkerEngine, metadata_dir=metadata_dir,
                listen_port=cfg["torrent_listen_port"],
                enable_dht_upnp=cfg["torrent_enable_dht_upnp"],
                allow_incoming_peers=cfg["torrent_allow_incoming_peers"],
            )
        info = await engine.version()
        if backend == "qbittorrent":
            # Preserve qBittorrent's existing torrent states. On explicit Start,
            # the durable automation ledger adopts any paused/seeding/in-progress
            # Archive X torrent and resumes its persisted stage before new RSS.
            # The scheduler—not a blanket startup pause—enforces the lane limit.
            engine_detail = f"qBittorrent {info['version']} · external client at {cfg.get('qbittorrent_url')}"
        else:
            engine_detail = f"Built-in {info['backend']} {info['version']} · isolated worker PID {getattr(engine, 'worker_pid', None) or '?'}"
        ctx.report(detail=engine_detail, operation="Download engine ready")
    except TorrentEngineError as exc:
        if engine is not None:
            try:
                await engine.close()
            except Exception:
                pass
        raise RuntimeError(
            f"{exc} -- use Operations -> Content Automation -> Test download engine before starting the run."
        ) from exc
    try:

        # Durable automation_items are per-release checkpoints. Preserve the
        # current stage, torrent hash, local path, and retry deadline across app
        # restarts; bulk-resetting active rows makes the next release look free.
        with ctx.db.lock:
            recovered_rows = int(ctx.db.conn.execute(
                "SELECT COUNT(*) c FROM automation_items WHERE status IN ("
                + ",".join("?" for _ in ACTIVE_STATUSES)
                + ")", list(ACTIVE_STATUSES)
            ).fetchone()["c"] or 0)
        if recovered_rows:
            ctx.db.log(
                f"Content Automation: preserving {recovered_rows} in-flight release checkpoint(s) from the previous run; "
                "existing stages will resume before new downloads are dispatched.",
                "INFO",
            )
        try:
            requeue_recoverable_items(ctx.db)
        except Exception as exc:
            ctx.db.log(f"Content Automation: could not re-queue earlier failed releases: {exc}", "WARN")

        # Check the destination channel once at start, so a wrong channel is
        # reported immediately instead of after the first download.
        try:
            if ctx.telegram.client is not None and ctx.telegram.connected:
                dest = await resolve_destination(ctx.telegram.client, cfg["destination_channel"])
                ctx.db.log(f"Content Automation: delivering to Telegram channel "
                           f"'{getattr(dest, 'title', None) or getattr(dest, 'username', None) or cfg['destination_channel']}'")
            else:
                ctx.db.log("Content Automation: Telegram is not connected - downloads will wait before upload", "WARN")
        except Exception as exc:
            ctx.db.log(f"Content Automation: destination channel problem: {exc}", "ERROR")

        # Prime the internal RSS queue immediately on explicit Start. The old
        # loop waited for one full poll interval (default 120s) before the first
        # RSS scan, which made Start Automation appear idle even though the job
        # was healthy. This first scan only queues releases; the normal scheduler
        # below remains the sole code path that adds torrents to qBittorrent.
        loop_clock = asyncio.get_running_loop().time
        last_rss_at = loop_clock()          # the initial scan below counts as a poll
        paused_applied_at = None            # when pause was last pushed to the engine
        engine_down_since = None            # first engine failure in a row
        rss_fail_count = 0
        try:
            initial_items = await fetch_feed(cfg["rss_url"])
            for rss_item in initial_items:
                existing = _get(ctx.db, rss_item.key)
                if existing and existing.get("status") == "COMPLETED":
                    continue
                allowed, reject_reason = _is_allowed_rss_item(rss_item, cfg)
                if not allowed:
                    if existing is None:
                        _upsert(ctx.db, rss_item.key, source_url=rss_item.source_url, guid=rss_item.guid, title=rss_item.title,
                                published_at=rss_item.published_at, torrent_url=rss_item.torrent_url or "", status="SKIPPED",
                                destination_channel=cfg["destination_channel"], download_state=TASK_LABELS["SKIPPED"],
                                enclosure_size=int(getattr(rss_item, "enclosure_size", 0) or 0), error=reject_reason)
                    continue
                if not rss_item.torrent_url:
                    continue
                if existing is None:
                    _upsert(ctx.db, rss_item.key, source_url=rss_item.source_url, guid=rss_item.guid, title=rss_item.title,
                            published_at=rss_item.published_at, torrent_url=rss_item.torrent_url, status="QUEUED",
                            destination_channel=cfg["destination_channel"], download_state=TASK_LABELS["QUEUED"],
                            download_progress=0.0, enclosure_size=int(getattr(rss_item, "enclosure_size", 0) or 0),
                            retry_count=0, next_retry_at="", failure_kind="", retry_stage="")
            try:
                await _adopt_existing_qbit_torrents(ctx, cfg, engine)
            except Exception as backlog_exc:
                ctx.db.log(
                    f"Content Automation: could not adopt existing qBittorrent backlog: "
                    f"{type(backlog_exc).__name__}: {backlog_exc}", "WARN"
                )
            queued_count = 0
            missing_torrent_count = 0
            skipped_count = 0
            for queued_item in initial_items:
                existing = _get(ctx.db, queued_item.key)
                if existing and existing.get("status") == "QUEUED":
                    if str(existing.get("torrent_url") or "").strip():
                        queued_count += 1
                    else:
                        missing_torrent_count += 1
                elif existing and existing.get("status") == "SKIPPED":
                    skipped_count += 1
                elif queued_item.torrent_url:
                    queued_count += 1
                else:
                    missing_torrent_count += 1
            ctx.db.log(
                f"Content Automation: initial RSS scan received {len(initial_items)} item(s); "
                f"torrent links={queued_count}, missing torrent links={missing_torrent_count}, skipped={skipped_count}",
                "INFO",
            )
            if initial_items and queued_count == 0:
                sample = "; ".join(str(x.title or "")[:100] for x in initial_items[:5])
                ctx.db.log(
                    "Content Automation: RSS returned items but no usable torrent/magnet URLs were detected. "
                    "Check the RSS feed's <enclosure type=...> or torrent link fields. "
                    f"Sample: {sample}",
                    "WARN",
                )
            elif not initial_items:
                ctx.db.log("Content Automation: RSS scan returned 0 items", "WARN")
            else:
                ctx.db.log(f"Content Automation: initial RSS scan queued {queued_count} release(s); scheduler will fill download slots")
            # Do not wait for the first scheduler tick. Start the first configured
            # download slot immediately after the bootstrap RSS scan.
            try:
                immediate_started = await _fill_download_slots(ctx, cfg, engine, root)
                ctx.db.log(f"Content Automation: immediate post-RSS dispatch started {immediate_started} torrent(s)", "INFO")
            except Exception as dispatch_exc:
                ctx.db.log(f"Content Automation: immediate post-RSS dispatch failed: {type(dispatch_exc).__name__}: {dispatch_exc}", "ERROR")
        except Exception as exc:
            ctx.db.log(f"Content Automation: initial RSS scan failed: {type(exc).__name__}: {exc}", "WARN")

        while True:
            await ctx.checkpoint()
            runtime = get_runtime(ctx.db)
            if not runtime.get("operator_started"):
                ctx.report(detail="Waiting for explicit Start Automation", operation="Stopped", force=True)
                return
            if runtime.get("paused"):
                # Push the pause to the torrent engine once, then only re-check
                # every 30 s (it used to call the engine for every torrent
                # every second for as long as the pause lasted).
                if paused_applied_at is not None and loop_clock() - paused_applied_at < 30:
                    await asyncio.sleep(1)
                    continue
                paused_applied_at = loop_clock()
                try:
                    # Pause every active torrent when concurrency is greater than one.
                    with ctx.db.lock:
                        pause_rows = [dict(r) for r in ctx.db.conn.execute(
                            "SELECT * FROM automation_items WHERE status='DOWNLOADING' AND torrent_hash IS NOT NULL AND torrent_hash!=''"
                        ).fetchall()]
                    for pause_row in pause_rows:
                        try:
                            await engine.pause(str(pause_row["torrent_hash"]))
                        except Exception as pause_exc:
                            ctx.db.log(f"Content Automation: pause retry failed for {pause_row.get('title')}: {type(pause_exc).__name__}: {pause_exc}", "WARN")
                    ctx.report(detail="Paused by operator", operation="Paused", force=True)
                except Exception as exc:
                    ctx.report(detail=f"Paused · retry: {type(exc).__name__}: {exc}", operation="Paused", force=True)
                await asyncio.sleep(1)
                continue

            paused_applied_at = None

            # IMPORTANT: advance one completed/post-download item BEFORE touching
            # the RSS endpoint. RSS is an external network dependency and can
            # take time. Meanwhile, qBittorrent may keep several torrents
            # downloading in the background according to max_concurrent_torrents.
            with ctx.db.lock:
                processing_placeholders = ",".join("?" for _ in PROCESSING_STATUSES)
                processing_row = ctx.db.conn.execute(
                    f"SELECT * FROM automation_items WHERE status IN ({processing_placeholders}) ORDER BY created_at ASC LIMIT 1",
                    list(PROCESSING_STATUSES),
                ).fetchone()
            # Regression guard: older builds used the name ``active_row`` in
            # this section. Keep an explicit alias so a stale copied handler or
            # diagnostic path can never raise NameError before scheduling.
            active_row = processing_row

            processed_one = False
            if processing_row is not None:
                processing_dict = dict(processing_row)
                ctx.report(
                    current_item=str(processing_dict.get("title") or ""),
                    detail=processing_dict.get("download_state") or TASK_LABELS.get(processing_dict.get("status"), processing_dict.get("status", "")),
                    operation=str(processing_dict.get("status") or "Processing"),
                    force=True,
                )
                try:
                    await _advance_active_item(ctx, cfg, engine, processing_dict, root)
                except (JobCancelled, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    key = processing_dict["item_key"]
                    current = _get(ctx.db, key) or processing_dict
                    stage = str(current.get("status") or "VALIDATING")
                    permanent = isinstance(exc, ValueError) and "Destination verification permanent failure:" in str(exc)
                    flood_delay = _flood_wait_delay(exc)
                    _fail_item(ctx.db, key, f"{type(exc).__name__}: {exc}", retryable=not permanent, retry_stage=stage,
                               retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
                processed_one = True

            # Poll every active download, but repair any old run that exceeded
            # the configured limit before resuming torrent activity.
            max_downloads = int(cfg.get("max_concurrent_torrents") or 1)
            try:
                downloading_rows = await _enforce_download_concurrency(ctx, engine, max_downloads)
                engine_down_since = None
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                # qBittorrent restarting/updating or briefly unreachable used
                # to end the whole automation run. Keep going and retry;
                # give up only after 30 minutes without the engine.
                if engine_down_since is None:
                    engine_down_since = loop_clock()
                    ctx.db.log(f"Content Automation: download engine unreachable, retrying: {type(exc).__name__}: {exc}", "WARN")
                if loop_clock() - engine_down_since > 1800:
                    raise RuntimeError(f"Download engine unreachable for 30 minutes: {exc}") from exc
                ctx.report(detail=f"Download engine unreachable — retrying ({type(exc).__name__})",
                           operation="Waiting for download engine", force=True)
                await asyncio.sleep(15)
                continue

            download_changed = False
            with ctx.db.lock:
                holding = int(ctx.db.conn.execute(
                    "SELECT COUNT(*) c FROM automation_items WHERE status IN (%s)" % ",".join("?" for _ in PROCESSING_STATUSES),
                    list(PROCESSING_STATUSES)).fetchone()["c"] or 0)
            if holding:
                # A release is being processed/uploaded: downloads are held
                # (paused by the dispatcher). Polling them here used to
                # force-start them again, so they flipped between running and
                # paused every few seconds.
                downloading_rows = []
            for row_dict in downloading_rows:
                try:
                    before = str(row_dict.get("status") or "")
                    await _advance_active_item(ctx, cfg, engine, row_dict, root, download_only=True)
                    after = str((_get(ctx.db, row_dict["item_key"]) or {}).get("status") or before)
                    download_changed = download_changed or after != before
                except (JobCancelled, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    key = row_dict["item_key"]
                    current = _get(ctx.db, key) or row_dict
                    stage = str(current.get("status") or DOWNLOAD_STATUS)
                    permanent = isinstance(exc, ValueError) and "Destination verification permanent failure:" in str(exc)
                    flood_delay = _flood_wait_delay(exc)
                    _fail_item(ctx.db, key, f"{type(exc).__name__}: {exc}", retryable=not permanent, retry_stage=stage,
                               retry_after_seconds=flood_delay, consume_retry_budget=(flood_delay <= 90))
                    download_changed = True

            # If a queued item is a retry of a post-download stage, move ONLY
            # ONE such item into the serialized processing pipeline.
            with ctx.db.lock:
                retry_placeholders = ",".join("?" for _ in PROCESSING_STATUSES)
                processing_in_flight = ctx.db.conn.execute(
                    f"SELECT COUNT(*) c FROM automation_items WHERE status IN ({retry_placeholders})",
                    list(PROCESSING_STATUSES),
                ).fetchone()["c"]
            if int(processing_in_flight or 0) == 0:
                with ctx.db.lock:
                    retry_row = ctx.db.conn.execute(
                        f"SELECT * FROM automation_items WHERE status='QUEUED' AND retry_stage IN ({retry_placeholders}) "
                        "AND (next_retry_at IS NULL OR next_retry_at='' OR next_retry_at<=?) "
                        "ORDER BY created_at ASC LIMIT 1",
                        [*PROCESSING_STATUSES, config.now_iso()],
                    ).fetchone()
                if retry_row is not None:
                    retry_dict = dict(retry_row)
                    retry_stage = str(retry_dict.get("retry_stage") or "VALIDATING")
                    _upsert(ctx.db, retry_dict["item_key"], status=retry_stage, next_retry_at="",
                            failure_kind="", error="", download_state=TASK_LABELS.get(retry_stage, retry_stage))
                    processed_one = True

            # Fill available qBittorrent download slots. This is the only
            # scheduler path that creates new DOWNLOADING rows.
            try:
                started_count = await _fill_download_slots(ctx, cfg, engine, root)
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                ctx.db.log(f"Content Automation: could not start queued download(s): {type(exc).__name__}: {exc}", "WARN")
                started_count = 0
            started_new_item = started_count > 0

            with ctx.db.lock:
                counts = {r["status"]: r["c"] for r in ctx.db.conn.execute(
                    "SELECT status, COUNT(*) c FROM automation_items GROUP BY status").fetchall()}
            total = sum(v for k, v in counts.items() if k != "SKIPPED")
            done = counts.get("COMPLETED", 0)
            downloading_count = counts.get(DOWNLOAD_STATUS, 0)
            processing_count = sum(counts.get(s, 0) for s in PROCESSING_STATUSES)
            ctx.report(
                total=total, completed=done, progress=(done / total * 100 if total else 0),
                current_item=(str(dict(active_row).get("title") or "") if active_row is not None else ""),
                detail=(f"{downloading_count}/{max_downloads} torrents downloading · "
                        f"{counts.get('QUEUED', 0)} queued · {done} completed"),
                operation=("Processing" if processed_one else "Monitoring"),
            )

            # Downloaders are intentionally left running while the serialized
            # processing pipeline works on one completed release. Polling is
            # frequent enough to keep all download telemetry current.
            busy = started_new_item or download_changed or processed_one
            await asyncio.sleep(5 if busy else min(30, cfg["poll_interval"]))

            # RSS is polled on its own schedule (the configured poll interval).
            # It used to be fetched on every loop pass - every ~10 s while a
            # release was being processed - which can get the feed's server
            # to block this PC. A failed poll no longer ends the run.
            if loop_clock() - last_rss_at < float(cfg["poll_interval"]):
                continue
            last_rss_at = loop_clock()
            ctx.report(detail="Polling RSS feed", operation="RSS scan", force=True)
            try:
                items = await fetch_feed(cfg["rss_url"])
                rss_fail_count = 0
                try:
                    await _adopt_existing_qbit_torrents(ctx, cfg, engine)
                except Exception as backlog_exc:
                    ctx.db.log(
                        f"Content Automation: periodic qBittorrent backlog scan failed: "
                        f"{type(backlog_exc).__name__}: {backlog_exc}", "WARN"
                    )
            except (JobCancelled, asyncio.CancelledError):
                raise
            except Exception as exc:
                rss_fail_count += 1
                if rss_fail_count in (1, 5) or rss_fail_count % 20 == 0:
                    ctx.db.log(f"Content Automation: RSS poll failed ({rss_fail_count} in a row): "
                               f"{type(exc).__name__}: {exc}", "WARN")
                continue

            for rss_item in items:
                existing = _get(ctx.db, rss_item.key)
                if existing and existing.get("status") == "COMPLETED":
                    continue

                allowed, reject_reason = _is_allowed_rss_item(rss_item, cfg)
                if not allowed:
                    if existing is None:
                        _upsert(ctx.db, rss_item.key, source_url=rss_item.source_url, guid=rss_item.guid, title=rss_item.title,
                                published_at=rss_item.published_at, torrent_url=rss_item.torrent_url or "", status="SKIPPED",
                                destination_channel=cfg["destination_channel"], download_state=TASK_LABELS["SKIPPED"],
                                enclosure_size=int(getattr(rss_item, "enclosure_size", 0) or 0), error=reject_reason)
                    elif existing.get("status") == "QUEUED":
                        _upsert(ctx.db, rss_item.key, status="SKIPPED", download_state=TASK_LABELS["SKIPPED"], error=reject_reason)
                    continue

                if not rss_item.torrent_url:
                    if not existing:
                        _upsert(ctx.db, rss_item.key, source_url=rss_item.source_url, guid=rss_item.guid, title=rss_item.title,
                                published_at=rss_item.published_at, torrent_url="", status="SKIPPED", destination_channel=cfg["destination_channel"],
                                error="No torrent/magnet URL in RSS item")
                    continue
                if existing is None:
                    # New releases join the queue -- they do NOT start
                    # downloading immediately. The scheduler below fills up
                    # to the configured concurrency limit, oldest first.
                    _upsert(ctx.db, rss_item.key, source_url=rss_item.source_url, guid=rss_item.guid, title=rss_item.title,
                            published_at=rss_item.published_at, torrent_url=rss_item.torrent_url, status="QUEUED",
                            destination_channel=cfg["destination_channel"], download_state=TASK_LABELS["QUEUED"],
                            download_progress=0.0, enclosure_size=int(getattr(rss_item, "enclosure_size", 0) or 0),
                            retry_count=0, next_retry_at="", failure_kind="", retry_stage="")

            # Existing rows may have been queued by an older configuration.
            # Apply the RSS filename rule to those queued rows too
            # so a newly enabled 1080p-only rule removes old 720p entries from
            # the waiting queue without starting them.
            with ctx.db.lock:
                queued_rows = ctx.db.conn.execute(
                    "SELECT item_key,title,torrent_url,enclosure_size,retry_count,torrent_hash FROM automation_items WHERE status='QUEUED'"
                ).fetchall()
            for queued_row in queued_rows:
                allowed, reject_reason = _rss_rule_match(queued_row["title"], queued_row["torrent_url"], cfg)
                if allowed:
                    allowed, reject_reason = _rss_size_allowed(int(queued_row["enclosure_size"] or 0), cfg)
                if not allowed and int(queued_row["retry_count"] or 0) == 0 and not str(queued_row["torrent_hash"] or "").strip():
                    _upsert(ctx.db, queued_row["item_key"], status="SKIPPED", download_state=TASK_LABELS["SKIPPED"], error=reject_reason)

            # RSS only populates the internal queue here. The authoritative
            # scheduler above fills download slots on the next loop iteration;
            # there is intentionally no second scheduler after RSS scanning.

            with ctx.db.lock:
                counts = {r["status"]: r["c"] for r in ctx.db.conn.execute(
                    "SELECT status, COUNT(*) c FROM automation_items GROUP BY status").fetchall()}
            total = sum(v for k, v in counts.items() if k != "SKIPPED")
            done = counts.get("COMPLETED", 0)
            current_title = (dict(active_row).get("title") if active_row is not None else None) or ""
            ctx.report(
                total=total, completed=done, progress=(done / total * 100 if total else 0),
                current_item=current_title,
                detail=f"{counts.get('QUEUED', 0)} queued, {done} completed" + (f" -- now: {current_title}" if current_title else ""),
                operation="Monitoring",
            )

            # The newly-created DOWNLOADING row is deliberately left for the
            # next loop iteration.  That keeps the state machine single-path:
            # every active item is advanced at the top of the loop, and the RSS
            # scanner can never delay or race the download pipeline.
            await asyncio.sleep(5)
    finally:
        await engine.close()
