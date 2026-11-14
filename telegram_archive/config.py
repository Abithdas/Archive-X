"""Application paths, persistent user-data layout and shared utilities.

Versioned application files live beside run.py.  User-owned state deliberately
lives outside the installation directory so replacing/extracting a new version
cannot remove Telegram login state, API credentials, metadata, thumbnails or
other accumulated library data.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - User-data location and pending moves
#   - Secrets store
#   - Small shared utilities
#   - Legacy data migration
# ------------------------------------------------------------------
from __future__ import annotations

from .core.swallowed import note as _swallowed_note
import json
import os
import sys
import shutil
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
APP_DIR = PACKAGE_DIR.parent

# The selected data root is stored outside the movable data tree so the
# application can remember a new drive/folder across upgrades and restarts.
# Environment override remains highest priority for portable/custom installs.
DATA_LOCATION_FILE = (Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Archive X" / ".data_location.json") if os.name == "nt" else (Path(os.getenv("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "Archive X" / ".data_location.json")



# ==============================================================================
# SECTION: User-data location and pending moves
# Where user data lives, plus deferred data-folder moves and restores.
# ==============================================================================

def _read_data_location_state() -> dict:
    try:
        value = json.loads(DATA_LOCATION_FILE.read_text(encoding="utf-8")) if DATA_LOCATION_FILE.exists() else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _default_user_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Archive X"
    return Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "Archive X"


_location_state = _read_data_location_state()
_env_data = (os.getenv("ARCHIVE_X_DATA_DIR", "").strip() or os.getenv("TELEGRAM_ARCHIVE_DATA_DIR", "").strip())
if _env_data:
    USER_DATA_DIR = Path(_env_data).expanduser().resolve()
else:
    _active = str(_location_state.get("active_path") or "").strip()
    USER_DATA_DIR = Path(_active).expanduser().resolve() if _active else _default_user_data_dir().resolve()

# If a move was requested in Settings, the old tree remains authoritative
# until the next clean startup.  Startup then copies/verifies the complete
# tree, removes the old tree, and only then switches the active root.
_PENDING_DATA_MOVE = str(_location_state.get("pending_path") or "").strip()

# Compatibility alias: older modules used DATA_DIR for runtime state.
DATA_DIR = USER_DATA_DIR
LEGACY_DATA_DIR = APP_DIR / "data"
LEGACY_CONFIG_PATH = APP_DIR / "config.json"

DATABASE_DIR = USER_DATA_DIR / "database"
AUTH_DIR = USER_DATA_DIR / "auth"
ASSET_DIR = USER_DATA_DIR / "assets"
THUMBNAIL_DIR = ASSET_DIR / "thumbnails" / "playback"
PERFORMER_ASSET_DIR = ASSET_DIR / "performers"
STUDIO_ASSET_DIR = ASSET_DIR / "studios"
SCENE_ASSET_DIR = ASSET_DIR / "scenes"
METADATA_DIR = USER_DATA_DIR / "metadata"
SCENE_METADATA_DIR = METADATA_DIR / "scenes"
MEDIA_METADATA_DIR = METADATA_DIR / "media"
PERFORMER_METADATA_DIR = METADATA_DIR / "performers"
STUDIO_METADATA_DIR = METADATA_DIR / "studios"
SITE_METADATA_DIR = METADATA_DIR / "sites"
REFERENCE_DIR = METADATA_DIR / "reference"
AI_DIR = USER_DATA_DIR / "ai"
AI_MODELS_DIR = AI_DIR / "models"
AI_EVIDENCE_DIR = AI_DIR / "evidence"
CACHE_DIR = USER_DATA_DIR / "cache"
PLAYER_CACHE_DIR = CACHE_DIR / "player_cache"
PLAYBACK_PERSISTENT_RANGE_CACHE = True  # V29.4.233: retain downloaded video ranges across playback sessions
PLAYBACK_ASSET_DIR = PLAYER_CACHE_DIR / "assets"
AUTOMATION_DIR = USER_DATA_DIR / "automation"
AUTOMATION_DOWNLOAD_DIR = AUTOMATION_DIR / "downloads"
AUTOMATION_ENGINE_DIR = AUTOMATION_DIR / "torrent_engine"
STATE_DIR = USER_DATA_DIR / "state"
RUNTIME_STATE_DIR = STATE_DIR / "runtime"
MEDIA_MANAGER_STATE_DIR = STATE_DIR / "media_manager"
MEDIA_MANAGER_CONFIG_PATH = MEDIA_MANAGER_STATE_DIR / "telegram_uploader_config.json"
MEDIA_MANAGER_SESSION_PATH = AUTH_DIR / "telegram_media_manager.session"
LOG_DIR = USER_DATA_DIR / "logs"
PLAYBACK_LOG_PATH = LOG_DIR / "playback.log"
# Telegram-Web-compatible browser playback range/cache unit.
PLAYBACK_RANGE_CHUNK_BYTES = 512 * 1024
# NoBuf-inspired byte-range playback defaults. Each Telegram cache segment is
# still 512 KiB; the scheduler ramps its speculative window 512 KiB -> 1 -> 2
# -> 4 -> 8 MiB and then maintains a bounded forward runway.
PLAYBACK_RAM_CACHE_BYTES = 128 * 1024 * 1024
PLAYBACK_PLAYHEAD_READAHEAD_SECONDS = 180.0
PLAYBACK_PLAYHEAD_READAHEAD_MAX_BYTES = 192 * 1024 * 1024
PLAYBACK_PLAYHEAD_READAHEAD_MAX_SEGMENTS = 384
PLAYBACK_STREAM_ACTIVE_CONNECTIONS = 1
PLAYBACK_STREAM_IDLE_READAHEAD_CONNECTIONS = 3
PLAYBACK_SEEK_PREVIEW_DEBOUNCE_MS = 500
PLAYBACK_NEXT_HEADSTART_SECONDS = 30.0  # 0 disables; supported range is 30-120 s
PLAYBACK_PREVIEW_WORKERS = 2
PLAYBACK_PREVIEW_MAX_PENDING = 12
PLAYBACK_BUFFER_MIN_SECONDS = 2.0
PLAYBACK_FOREGROUND_MIN_SECONDS = 2.0
PLAYBACK_SEEK_BUFFER_TARGET_SECONDS = 2.0
PLAYBACK_BUFFER_LOW_WATER_SECONDS = 2.0
PLAYBACK_BUFFER_HIGH_WATER_SECONDS = 14.0
# Soft admission deadline for foreground playback/seek range requests. A missed
# deadline is recorded for diagnostics but never cancels an otherwise valid
# Telegram request.
TRANSFER_GOVERNOR_PLAYBACK_DEADLINE_SECONDS = 3.0
# Dedicated foreground media admission. These lanes are separate from the
# background Telegram range pool so cache/metadata work cannot starve playback.
TELEGRAM_BACKGROUND_RANGE_CONCURRENCY = 8  # byte-range requests; independent of app-config file-operation limits
# Dedicated high-priority OSHASH reads; they do not consume file-operation quotas.
TELEGRAM_FINGERPRINT_CONCURRENCY = 4
FINGERPRINT_TIMEOUT_SECONDS = 30.0
METADATA_ITEM_CONCURRENCY = 5
# Snapshot extraction is bounded per video, including network throttling (except playback hold-back).
SNAPSHOT_VIDEO_MAX_SECONDS = 300.0
SNAPSHOT_FAILED_RETRY_HOURS = 24
PLAYBACK_FOREGROUND_RANGE_CONCURRENCY = 6
PLAYBACK_FOREGROUND_RPC_CONCURRENCY = 4
PLAYBACK_FOREGROUND_EMERGENCY_CONCURRENCY = 10
PLAYBACK_FOREGROUND_RANGE_INTERVAL_SECONDS = 0.02
PLAYBACK_FOREGROUND_RPC_INTERVAL_SECONDS = 0.02
# Foreground browser playback/seek ranges must fail fast enough for a newer
# Chrome Range request to take over. Background Telegram reads keep the longer
# legacy retry budget below. These are admission/transfer safeguards, not UI
# timeouts.
PLAYBACK_FOREGROUND_RANGE_TIMEOUT_SECONDS = 5.5
PLAYBACK_BROWSER_RANGE_DEADLINE_SECONDS = 12.0  # bound a browser Range response; return HTTP 503 if the scheduler stalls
PLAYBACK_FOREGROUND_RANGE_ATTEMPTS = 3
# After a seek, protect a short contiguous burst so the browser can refill
# immediately after the target segment instead of waiting behind cache work.
PLAYBACK_SEEK_BURST_SEGMENTS = 3
PLAYBACK_PREVIEW_RANGE_TIMEOUT_SECONDS = 6.0
PLAYBACK_PREVIEW_RANGE_ATTEMPTS = 1
# Foreground playback deliberately keeps a small near-playhead window. Larger
# cache fills use the existing background range governor so they cannot occupy
# foreground Telegram capacity during startup/seek.
PLAYBACK_FOREGROUND_TARGET_SECONDS = 2.0
PLAYBACK_FOREGROUND_MIN_SEGMENTS = 2
PLAYBACK_FOREGROUND_MAX_SEGMENTS = 4
# V29.4.32 low-latency liveness/progress pipeline. These are internal
# performance controls, not user-facing settings: changing them can weaken
# recovery guarantees or increase database/browser load.
WATCHDOG_SUPERVISOR_INTERVAL_SECONDS = 1.0
# The asyncio job watchdog is backed by an independent thread and a separate
# enforcer process. These controls are internal reliability safeguards.
# The guardian is started as `python watchdog_guardian.py ...`. The installed backend has no separate Python to run it with (its "python" is
# the app itself, so the call started a second copy of the app, which refused with "already running", three times every ten minutes), and the
# desktop shell already watches the backend and reports if it stops. So it is off in the installed app and in desktop mode, and on elsewhere.
# ARCHIVE_X_WATCHDOG_GUARDIAN=1 or 0 overrides.
_guardian_choice = (os.getenv("ARCHIVE_X_WATCHDOG_GUARDIAN") or "").strip()
WATCHDOG_GUARDIAN_ENABLED = (_guardian_choice == "1") if _guardian_choice in {"0", "1"} else not (getattr(sys, "frozen", False) or os.getenv("ARCHIVE_X_DESKTOP") == "1")
WATCHDOG_GUARDIAN_POLL_SECONDS = 0.5
WATCHDOG_GUARDIAN_PUBLISH_SECONDS = 0.5
WATCHDOG_EVENT_LOOP_STALE_SECONDS = 6.0
WATCHDOG_TASK_RECOVERY_SECONDS = 5.0
WATCHDOG_TASK_ENFORCER_SECONDS = 12.0
WATCHDOG_PUBLISHER_STALE_SECONDS = 8.0
WATCHDOG_GUARDIAN_STARTUP_GRACE_SECONDS = 8.0
WATCHDOG_GUARDIAN_RESTART_LIMIT = 2
WATCHDOG_GUARDIAN_RESTART_WINDOW_SECONDS = 600.0
# V29.4.223: the independent guardian restarts the app only for a real hang.
# Short busy spells (starting playback, remuxing a large file) used to exceed
# the 6-12 s in-app thresholds and caused a restart plus a new browser tab.
WATCHDOG_GUARDIAN_LOOP_HANG_SECONDS = 180.0
WATCHDOG_GUARDIAN_TASK_HANG_SECONDS = 240.0
WATCHDOG_GUARDIAN_PUBLISHER_HANG_SECONDS = 240.0
WATCHDOG_GUARDIAN_STARTUP_HANG_GRACE_SECONDS = 90.0
# Automatically stop active jobs that remain in a watchdog-unhealthy state this long.
WATCHDOG_UNRECOVERED_GRACE_SECONDS = 60.0
WATCHDOG_EXECUTION_LEASE_HEARTBEAT_SECONDS = 2.0
JOB_PROGRESS_PERSIST_INTERVAL_SECONDS = 0.5
OPERATIONS_STATUS_CACHE_SECONDS = 0.25
OPERATIONS_WEBSOCKET_INTERVAL_SECONDS = 0.25
CLI_LOG_PATH = LOG_DIR / "launcher-cli.log"
BROWSER_DIAGNOSTICS_MAX_RESOURCES = 40
DIAGNOSTICS_EVENT_MAX_BYTES = 10 * 1024 * 1024
DIAGNOSTICS_EVENT_BACKUPS = 5
DIAGNOSTICS_DEDUP_WINDOW_SECONDS = 60.0
DIAGNOSTICS_INCIDENT_WINDOW_SECONDS = 15.0
DIAGNOSTICS_RECENT_EVENT_LIMIT = 600
CLI_LOG_MAX_BYTES = 10 * 1024 * 1024
CLI_LOG_BACKUPS = 5
PLAYBACK_LOG_MAX_BYTES = 5 * 1024 * 1024
PLAYBACK_LOG_BACKUPS = 2
PLAYBACK_DIAGNOSTIC_SLOW_RANGE_MS = 500.0
ICON_DIR = ASSET_DIR / "icons"
FONT_DIR = ASSET_DIR / "fonts"
CERT_DIR = AUTH_DIR / "cert"

DB_PATH = DATABASE_DIR / "archive.db"
METADATA_KEYS_PATH = AUTH_DIR / "provider_keys.json"
SECRETS_PATH = AUTH_DIR / "credentials.json"
CONFIG_PATH = USER_DATA_DIR / "settings.json"
SESSION_PATH = AUTH_DIR / "archive_x.session"
CERT_PATH = CERT_DIR / "cert.pem"
KEY_PATH = CERT_DIR / "key.pem"
REFERENCE_EXPORT_PATH = REFERENCE_DIR / "reference_export.txt"


def _write_data_location_state(*, active_path: Path, pending_path: Path | None = None) -> None:
    payload = {
        "version": 1,
        "active_path": str(Path(active_path).expanduser().resolve()),
        "pending_path": str(Path(pending_path).expanduser().resolve()) if pending_path else "",
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = DATA_LOCATION_FILE.with_suffix(DATA_LOCATION_FILE.suffix + ".tmp")
    DATA_LOCATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA_LOCATION_FILE)


def _tree_signature(root: Path, *, ignore_names: set[str] | None = None) -> tuple[int, int]:
    """Cheap copy verification: number of files and total byte size."""
    count = 0
    total = 0
    ignored = set(ignore_names or ())
    if not root.exists():
        return 0, 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and path.name not in ignored:
                count += 1
                total += path.stat().st_size
        except OSError:
            continue
    return count, total


def get_data_location_state() -> dict:
    state = _read_data_location_state()
    return {
        "active_path": str(Path(state.get("active_path") or USER_DATA_DIR).expanduser().resolve()),
        "pending_path": str(Path(state.get("pending_path")).expanduser().resolve()) if state.get("pending_path") else "",
        "environment_override": bool(os.getenv("ARCHIVE_X_DATA_DIR", "").strip() or os.getenv("TELEGRAM_ARCHIVE_DATA_DIR", "").strip()),
    }


def request_data_move(target_path: str) -> dict:
    source = Path(USER_DATA_DIR).expanduser().resolve()
    target = Path(str(target_path or "").strip()).expanduser().resolve()
    if not target.is_absolute():
        raise ValueError("Destination must be an absolute folder path")
    if source == target:
        raise ValueError("Destination is already the current Archive X data folder")
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("Destination cannot be inside the current Archive X data folder")
    # Avoid moving the data into the application installation tree. This keeps
    # upgrades/re-extraction from mixing program files with user-owned state.
    try:
        target.relative_to(APP_DIR.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("Choose a folder outside the Archive X application folder")
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        # An existing Archive X destination is allowed only when it is the
        # same destination from a previous interrupted move.
        manifest = target / "PERSISTENT_DATA_MANIFEST.json"
        db_file = target / "database" / "archive.db"
        if not manifest.exists() and not db_file.exists():
            raise ValueError("Destination folder must be empty")
    _write_data_location_state(active_path=source, pending_path=target)
    return {"active_path": str(source), "pending_path": str(target), "restart_required": True}


def cancel_pending_data_move() -> dict:
    state = _read_data_location_state()
    active = Path(state.get("active_path") or USER_DATA_DIR).expanduser().resolve()
    _write_data_location_state(active_path=active)
    return {"active_path": str(active), "pending_path": "", "cancelled": True}


def _complete_pending_data_move() -> None:
    """Finish a queued user-data folder move at startup and repoint the path globals."""
    global USER_DATA_DIR, DATA_DIR, DATABASE_DIR, AUTH_DIR, ASSET_DIR, THUMBNAIL_DIR
    global PERFORMER_ASSET_DIR, STUDIO_ASSET_DIR, SCENE_ASSET_DIR, METADATA_DIR
    global SCENE_METADATA_DIR, MEDIA_METADATA_DIR, PERFORMER_METADATA_DIR, STUDIO_METADATA_DIR
    global SITE_METADATA_DIR, REFERENCE_DIR, AI_DIR, AI_MODELS_DIR, AI_EVIDENCE_DIR, CACHE_DIR, PLAYER_CACHE_DIR, PLAYBACK_ASSET_DIR
    global AUTOMATION_DIR, AUTOMATION_DOWNLOAD_DIR, AUTOMATION_ENGINE_DIR, STATE_DIR, RUNTIME_STATE_DIR
    global MEDIA_MANAGER_STATE_DIR, MEDIA_MANAGER_CONFIG_PATH, MEDIA_MANAGER_SESSION_PATH, LOG_DIR, PLAYBACK_LOG_PATH, ICON_DIR, FONT_DIR, CERT_DIR
    global DB_PATH, METADATA_KEYS_PATH, SECRETS_PATH, CONFIG_PATH, SESSION_PATH, CERT_PATH, KEY_PATH, REFERENCE_EXPORT_PATH

    target_text = _PENDING_DATA_MOVE
    if not target_text:
        return
    source = Path(USER_DATA_DIR).resolve()
    target = Path(target_text).expanduser().resolve()
    if source == target:
        _write_data_location_state(active_path=source)
        return
    try:
        target.relative_to(source)
        raise RuntimeError("The new data folder cannot be inside the current Archive X data folder.")
    except ValueError:
        pass
    if not source.exists():
        raise RuntimeError(f"Current Archive X data folder does not exist: {source}")
    target.mkdir(parents=True, exist_ok=True)
    print(f"[storage] Moving Archive X data: {source} -> {target}", flush=True)
    # Never merge Archive X into an unrelated non-empty folder. A partially
    # copied target from an interrupted move is safe to resume because it has
    # the same pending source recorded in the control file.
    marker = target / ".archive_x_move_in_progress.json"
    existing = [p for p in target.iterdir() if p.name != marker.name]
    if existing:
        marker_source = ""
        try:
            marker_source = str(json.loads(marker.read_text(encoding="utf-8")).get("source") or "")
        except Exception:
            _swallowed_note("config._complete_pending_data_move")
            pass
        if not marker_source or Path(marker_source).resolve() != source:
            # If it already looks like this application's data, allow a safe
            # resume; otherwise require an empty destination.
            if not (target / "PERSISTENT_DATA_MANIFEST.json").exists() and not (target / "database" / "archive.db").exists():
                raise RuntimeError(f"Destination is not empty: {target}")
    marker.write_text(json.dumps({"source": str(source), "target": str(target), "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}, indent=2), encoding="utf-8")
    src_sig = _tree_signature(source)
    shutil.copytree(source, target, dirs_exist_ok=True)
    # The migration marker is control metadata, not part of the user-data tree.
    # Keep it until verification succeeds so an interrupted copy can resume.
    dst_sig = _tree_signature(target, ignore_names={marker.name})
    if src_sig != dst_sig:
        raise RuntimeError(f"Archive X data verification failed: source={src_sig}, destination={dst_sig}")
    try:
        marker.unlink()
    except OSError:
        pass
    # Only delete the old tree after the complete destination signature matches.
    print("[storage] Data copy verified; removing old Archive X data folder.", flush=True)
    shutil.rmtree(source)
    _write_data_location_state(active_path=target)
    USER_DATA_DIR = target
    DATA_DIR = USER_DATA_DIR
    DATABASE_DIR = USER_DATA_DIR / "database"
    AUTH_DIR = USER_DATA_DIR / "auth"
    ASSET_DIR = USER_DATA_DIR / "assets"
    THUMBNAIL_DIR = ASSET_DIR / "thumbnails" / "playback"
    PERFORMER_ASSET_DIR = ASSET_DIR / "performers"
    STUDIO_ASSET_DIR = ASSET_DIR / "studios"
    SCENE_ASSET_DIR = ASSET_DIR / "scenes"
    METADATA_DIR = USER_DATA_DIR / "metadata"
    SCENE_METADATA_DIR = METADATA_DIR / "scenes"
    MEDIA_METADATA_DIR = METADATA_DIR / "media"
    PERFORMER_METADATA_DIR = METADATA_DIR / "performers"
    STUDIO_METADATA_DIR = METADATA_DIR / "studios"
    SITE_METADATA_DIR = METADATA_DIR / "sites"
    REFERENCE_DIR = METADATA_DIR / "reference"
    AI_DIR = USER_DATA_DIR / "ai"
    AI_MODELS_DIR = AI_DIR / "models"
    AI_EVIDENCE_DIR = AI_DIR / "evidence"
    CACHE_DIR = USER_DATA_DIR / "cache"
    PLAYER_CACHE_DIR = CACHE_DIR / "player_cache"
    PLAYBACK_ASSET_DIR = PLAYER_CACHE_DIR / "assets"
    AUTOMATION_DIR = USER_DATA_DIR / "automation"
    AUTOMATION_DOWNLOAD_DIR = AUTOMATION_DIR / "downloads"
    AUTOMATION_ENGINE_DIR = AUTOMATION_DIR / "torrent_engine"
    STATE_DIR = USER_DATA_DIR / "state"
    RUNTIME_STATE_DIR = STATE_DIR / "runtime"
    MEDIA_MANAGER_STATE_DIR = STATE_DIR / "media_manager"
    MEDIA_MANAGER_CONFIG_PATH = MEDIA_MANAGER_STATE_DIR / "telegram_uploader_config.json"
    MEDIA_MANAGER_SESSION_PATH = AUTH_DIR / "telegram_media_manager.session"
    LOG_DIR = USER_DATA_DIR / "logs"
    PLAYBACK_LOG_PATH = LOG_DIR / "playback.log"
    ICON_DIR = ASSET_DIR / "icons"
    FONT_DIR = ASSET_DIR / "fonts"
    CERT_DIR = AUTH_DIR / "cert"
    DB_PATH = DATABASE_DIR / "archive.db"
    METADATA_KEYS_PATH = AUTH_DIR / "provider_keys.json"
    SECRETS_PATH = AUTH_DIR / "credentials.json"
    CONFIG_PATH = USER_DATA_DIR / "settings.json"
    SESSION_PATH = AUTH_DIR / "archive_x.session"
    CERT_PATH = CERT_DIR / "cert.pem"
    KEY_PATH = CERT_DIR / "key.pem"
    REFERENCE_EXPORT_PATH = REFERENCE_DIR / "reference_export.txt"


if _PENDING_DATA_MOVE and not _env_data:
    _complete_pending_data_move()

def _apply_pending_restore() -> None:
    """Apply a validated staged restore before the database is opened."""
    marker_path = USER_DATA_DIR.parent / ".archive_x_pending_restore.json"
    if not marker_path.exists():
        return
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    stage = Path(str(marker.get("staging_path") or "")).expanduser().resolve()
    current = Path(USER_DATA_DIR).resolve()
    if not stage.is_dir():
        raise RuntimeError(f"Pending Archive X restore staging folder is missing: {stage}")
    rollback = current.parent / f"Archive X PreRestore {datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if current.exists():
        os.replace(current, rollback)
    os.replace(stage, current)
    marker_path.unlink(missing_ok=True)


if not _env_data:
    _apply_pending_restore()

for _path in (
    USER_DATA_DIR, DATABASE_DIR, AUTH_DIR, ASSET_DIR, THUMBNAIL_DIR,
    PERFORMER_ASSET_DIR, STUDIO_ASSET_DIR, SCENE_ASSET_DIR, METADATA_DIR,
    SCENE_METADATA_DIR, MEDIA_METADATA_DIR, PERFORMER_METADATA_DIR, STUDIO_METADATA_DIR,
    SITE_METADATA_DIR, REFERENCE_DIR, AI_DIR, AI_MODELS_DIR, AI_EVIDENCE_DIR, CACHE_DIR, PLAYER_CACHE_DIR, PLAYBACK_ASSET_DIR,
    AUTOMATION_DIR, AUTOMATION_DOWNLOAD_DIR, AUTOMATION_ENGINE_DIR,
    FONT_DIR, STATE_DIR, MEDIA_MANAGER_STATE_DIR, LOG_DIR, ICON_DIR, CERT_DIR,
):
    _path.mkdir(parents=True, exist_ok=True)
MEDIA_MANAGER_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)


def write_persistent_data_manifest() -> Path:
    """Write the authoritative inventory marker for an Archive X data root.

    The manifest intentionally describes the expected structure rather than
    enumerating every user-generated file. It is used by storage migration and
    destination validation to distinguish an Archive X data directory from an
    unrelated folder.
    """
    expected_directories = [
        "database",
        "auth",
        "auth/cert",
        "assets",
        "assets/thumbnails/playback",
        "assets/performers",
        "assets/studios",
        "assets/scenes",
        "assets/icons",
        "assets/fonts",
        "metadata",
        "metadata/media",
        "metadata/scenes",
        "metadata/performers",
        "metadata/studios",
        "metadata/sites",
        "metadata/reference",
        "ai/evidence",
        "cache/player_cache",
        "automation",
        "automation/downloads",
        "automation/torrent_engine",
        "state",
        "state/runtime",
        "state/media_manager",
        "logs",
    ]
    payload = {
        "manifest_version": DATA_MANIFEST_VERSION,
        "application": "Archive X",
        "application_version": APP_VERSION,
        "data_schema_version": 1,
        "data_root": str(USER_DATA_DIR),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "expected_directories": expected_directories,
        "compatibility": {
            "legacy_data_environment": "TELEGRAM_ARCHIVE_DATA_DIR",
            "legacy_session_filename": "telegram_archive.session",
            "current_session_filename": "archive_x.session",
        },
    }
    path = USER_DATA_DIR / "PERSISTENT_DATA_MANIFEST.json"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# Keys that contain login/session/API secrets. They are kept in auth/
# credentials.json rather than in the application database.
SECRET_KEYS = frozenset({
    "web_username",
    "web_password_hash",
    "telegram_api_id",
    "telegram_api_hash",
    "telegram_phone",
    "telegram_proxy_settings",
    "qbittorrent_username",
    "qbittorrent_password",
    "qbittorrent_api_key",
    "stream_token_secret",
    "tpdb_api_token",
    "tpdb_token",
    "stashdb_api_key",
})




# ==============================================================================
# SECTION: Secrets store
# Load, read, write and delete persisted secrets.
# ==============================================================================

def load_secrets() -> dict:
    from .core.encrypted_files import read_json
    return read_json(SECRETS_PATH, "archive-x-credentials-v1")


def get_secret(key: str, default=None):
    value = load_secrets().get(str(key))
    return default if value is None else value


def set_secret(key: str, value) -> None:
    from .core.encrypted_files import LOCK, write_json
    with LOCK:
        payload = load_secrets()
        if value in (None, ""):
            payload.pop(str(key), None)
        else:
            payload[str(key)] = value
        write_json(SECRETS_PATH, payload, "archive-x-credentials-v1")


def is_secret_key(key: str) -> bool:
    return str(key) in SECRET_KEYS

HOST = os.getenv("TELEGRAM_ARCHIVE_HOST", "0.0.0.0")
# Canonical release identifier for the current application build. Keep in sync with tests/_version.py.
APP_VERSION = "v29.5.400"

# V29.4.20–V29.4.25 execution-lane policy. JobOrchestrator remains the admission authority.
EXECUTION_LANE_CAPACITIES = {
    # Separate channel/operation lanes can make progress in parallel. The
    # shared Telegram governor, three transfer permits, and resource scheduler
    # remain the global safety ceilings across these logical lanes.
    "METADATA": 3, "SUGGESTIONS": 2, "ENTITY_DATA": 3, "THUMBNAILS": 3,
    "METADATA_ASSETS": 3, "METADATA_CHANNEL_PIPELINE": 3,
    "TRANSFER_LOCAL": 2, "TRANSFER_TELEGRAM": 3, "MEDIA_MANAGER": 3, "PREVIEW": 1,
    "CONTENT_AUTOMATION": 1, "CONTENT_AUTOMATION_DELIVERY": 3, "BACKUP_CREATE": 1,
}
EXECUTION_LANE_CLASSES = {
    "METADATA":"BACKGROUND", "SUGGESTIONS":"BACKGROUND", "ENTITY_DATA":"BACKGROUND", "THUMBNAILS":"BACKGROUND",
    "METADATA_ASSETS":"BACKGROUND", "METADATA_CHANNEL_PIPELINE":"BACKGROUND", "PREVIEW":"INTERACTIVE_SUPPORT",
    "TRANSFER_LOCAL":"BULK", "TRANSFER_TELEGRAM":"BULK", "MEDIA_MANAGER":"BULK", "CONTENT_AUTOMATION":"BULK", "CONTENT_AUTOMATION_DELIVERY":"BULK", "BACKUP_CREATE":"MAINTENANCE",
}
EXECUTION_LANE_LEASE_SECONDS = 45
EXECUTION_LANE_RECOVERY_GRACE_SECONDS = 5

# A completed channel index is reused locally for this period before a cheap
# incremental Telegram history check is requested again. Manual refresh can
# bypass this window.
CHANNEL_INDEX_FRESHNESS_SECONDS = 300
DATA_MANIFEST_VERSION = 1

# Web credentials are provisioned during first-run setup; no default password is embedded.

PORT = int(os.getenv("TELEGRAM_ARCHIVE_PORT", "8443"))
LOCAL_PLAYER_PORT = int(os.getenv("TELEGRAM_ARCHIVE_PLAYER_PORT", "8444"))
LOCAL_PLAYER_HOST = os.getenv("TELEGRAM_ARCHIVE_PLAYER_HOST", "0.0.0.0")
METADATA_CACHE_TTL_SECONDS = max(0.0, float(os.getenv("ARCHIVE_X_METADATA_CACHE_TTL_SECONDS", str(90 * 86400))))

# Browser-facing transport. "auto" uses HTTP/3/QUIC when Hypercorn + aioquic
# are installed, otherwise keeps the existing HTTPS/TCP server. "on" forces
# the HTTP/3 attempt (with safe TCP fallback if the optional dependency is
# missing). "off" always uses the existing Uvicorn HTTPS server.
HTTP3_MODE = os.getenv("TELEGRAM_ARCHIVE_HTTP3", "auto").strip().lower() or "auto"
# Browser opening is convenience-only and can be disabled for headless/server installs.
AUTO_OPEN_BROWSER = os.getenv("ARCHIVE_X_AUTO_OPEN_BROWSER", "1" if os.name == "nt" else "0").strip().lower() not in {"0", "false", "no", "off"}
# Automatic idle snapshot extraction was removed in v29.4.143. Generating a
# 36-frame grid makes multiple Telegram range requests per video and must be
# triggered explicitly from the Snapshot Cache / Metadata & Assets workflow.
AUTO_PREVIEW_PREFETCH_ON_IDLE = False
TELEGRAM_BACKGROUND_CONNECT_TIMEOUT_SECONDS = max(5.0, float(os.getenv("ARCHIVE_X_TELEGRAM_CONNECT_TIMEOUT_SECONDS", "20")))
HTTP3_ALT_SVC_MAX_AGE = int(os.getenv("TELEGRAM_ARCHIVE_HTTP3_ALT_SVC", "86400"))
SESSION_COOKIE = "session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
LOGIN_MAX_ATTEMPTS = 8
LOGIN_WINDOW_SECONDS = 300

# Keep a lightweight authoritative structure marker available for migration and
# future builds. This runs after pending data moves and directory creation.
write_persistent_data_manifest()




# ==============================================================================
# SECTION: Small shared utilities
# Timestamps and LAN address lookup.
# ==============================================================================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_after_seconds(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(0, float(seconds)))).isoformat(timespec="seconds")


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        _swallowed_note("config.local_ip")
        pass
    try:
        for item in socket.gethostbyname_ex(socket.gethostname())[2]:
            if item and not item.startswith("127."):
                return item
    except Exception:
        _swallowed_note("config.local_ip")
        pass
    return "127.0.0.1"




# ==============================================================================
# SECTION: Legacy data migration
# Copies data forward from older install layouts.
# ==============================================================================

def _copy_if_missing(src: Path, dst: Path, moved: list[str]) -> None:
    if not src.exists() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    moved.append(f"{src} -> {dst}")


def migrate_legacy_data() -> list[str]:
    """Copy legacy in-install state into the version-independent user store.

    Nothing in the legacy ``data/`` tree is deleted.  This makes migration safe
    when a user later removes the old application directory after upgrading.
    """
    moved: list[str] = []
    root = APP_DIR
    legacy = LEGACY_DATA_DIR

    # Database/WAL/SHM must move together when they exist.
    for name, target in (
        ("archive.db", DB_PATH),
        ("archive.db-wal", DB_PATH.parent / "archive.db-wal"),
        ("archive.db-shm", DB_PATH.parent / "archive.db-shm"),
        ("metadata_keys.json", METADATA_KEYS_PATH),
        ("archive_x.session", SESSION_PATH),
        ("playback.log", PLAYBACK_LOG_PATH),
        ("reference_export.txt", REFERENCE_EXPORT_PATH),
    ):
        source = (legacy / name) if legacy.exists() else (root / name)
        _copy_if_missing(source, target, moved)

    # Existing certificate/session sidecars and large runtime caches.
    _copy_if_missing(legacy / "cert", CERT_DIR, moved)
    _copy_if_missing(legacy / "player_cache", PLAYER_CACHE_DIR, moved)
    _copy_if_missing(legacy / "automation_downloads", AUTOMATION_DOWNLOAD_DIR, moved)
    _copy_if_missing(legacy / "automation_torrent_engine", AUTOMATION_ENGINE_DIR, moved)
    _copy_if_missing(legacy / "media_manager", MEDIA_MANAGER_STATE_DIR, moved)
    _copy_if_missing(legacy / "icons", ICON_DIR, moved)
    _copy_if_missing(legacy / "fonts", FONT_DIR, moved)
    _copy_if_missing(legacy / "ai_evidence", AI_EVIDENCE_DIR, moved)

    # Old settings/config is only a compatibility input.  The new settings
    # file lives in the persistent user-data root.
    if not CONFIG_PATH.exists():
        source_cfg = LEGACY_CONFIG_PATH
        if source_cfg.exists():
            _copy_if_missing(source_cfg, CONFIG_PATH, moved)

    # Legacy session/cert files that predate the data/ directory.
    _copy_if_missing(AUTH_DIR / "telegram_archive.session", SESSION_PATH, moved)
    for src, dst in (
        (root / "archive_x.session", SESSION_PATH),
        (root / "telegram_archive.session", SESSION_PATH),
        (root / "cert", CERT_DIR),
        (root / "archive.db", DB_PATH),
        (root / "archive.db-wal", DB_PATH.parent / "archive.db-wal"),
        (root / "archive.db-shm", DB_PATH.parent / "archive.db-shm"),
    ):
        _copy_if_missing(src, dst, moved)

    # Standalone Telegram media-manager compatibility state. These paths were
    # historically stored beside the application or under LOCALAPPDATA.
    legacy_mm = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "TelegramVideoThumbnailUploader"
    _copy_if_missing(legacy_mm / "telegram_uploader_config.json", MEDIA_MANAGER_CONFIG_PATH, moved)
    _copy_if_missing(legacy_mm / "telegram_uploader", MEDIA_MANAGER_SESSION_PATH, moved)
    _copy_if_missing(legacy_mm / "telegram_uploader.session", MEDIA_MANAGER_SESSION_PATH, moved)
    _copy_if_missing(root / "telegram_uploader_config.json", MEDIA_MANAGER_CONFIG_PATH, moved)
    _copy_if_missing(root / "telegram_uploader", MEDIA_MANAGER_SESSION_PATH, moved)
    for name in (
        "telegram_vertical_video_archiver_state.json",
        "telegram_smart_video_router_state.json",
        "telegram_vertical_mobile_mover_state.json",
        "telegram_transfer_job_state.json",
        "telegram_channel_cleanup_state.json",
        "telegram_channel_organizer_state.json",
        "telegram_duplicate_finder_state.json",
    ):
        _copy_if_missing(root / name, MEDIA_MANAGER_STATE_DIR / name, moved)
    return moved

# Durable Job Orchestrator control-plane defaults. These are safety defaults,
# not operator-facing workload settings; persisted job fields remain the source
# of truth for individual jobs.
JOB_ORCHESTRATOR_LEASE_SECONDS = 45
JOB_ORCHESTRATOR_MAX_RETRIES = 3
JOB_ORCHESTRATOR_MAX_RUNTIME_QUEUE = 256
JOB_ORCHESTRATOR_RETRY_MAX_DELAY_SECONDS = 300
JOB_ORCHESTRATOR_PRIORITY_AGING_MINUTES = 5

# Maximum grace period for job-owned Python blocking work to exit after cancellation.
JOB_PIPELINE_CANCEL_DRAIN_SECONDS = 5.0
