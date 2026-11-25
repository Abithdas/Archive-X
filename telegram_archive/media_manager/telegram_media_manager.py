"""Telegram Media Manager host module.

Holds shared helpers and runs the mm_*.py fragments inside this module's own
namespace (see _mm_load_parts), so those fragments share these globals. Splitting
them into real modules requires giving each fragment explicit imports first.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Governed downloads
#   - Console and JSON helpers
#   - Session and login
#   - Channel selection
#   - Message and thumbnail helpers
#   - Fragment loader
#   - Menu and workflows
#   - FFmpeg conversion
# ------------------------------------------------------------------
from ..core.swallowed import note as _swallowed_note
import asyncio
import json
import io
import os
import logging
import re
import sys
import time
import tempfile
import shutil
import subprocess
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path
from bisect import bisect_left, bisect_right
from struct import unpack_from
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STARTUP_IMPORT_ERRORS = []
_LOG = logging.getLogger(__name__)

APP_NAME = "Telegram Media Manager"
APP_VERSION = "4.2-512kb-sequential-file-transfer"


from ..engines.media_utils import flood_wait_seconds, is_flood_wait, TelegramRangeFloodError


from ..core import telethon_lazy   # the Telegram library is loaded when it is first used, not when the application starts
if not telethon_lazy.available():
    STARTUP_IMPORT_ERRORS.append(
        "Telethon is not installed. Run the same environment/Install.bat used by your working Telegram scripts."
    )

from .. import config
from ..core.telegram_identity import canonical_peer_id
from ..engines.telegram_sender import send_existing_video_with_cover, send_local_video_with_cover, render_caption, settings as sender_settings, _video_fields
from ..metadata.pipeline import identify_video
from ..metadata.fingerprint import oshash_from_source
from ..core.external_tools import ffmpeg as find_app_ffmpeg, ffprobe as find_app_ffprobe
from ..core.upload_limits import ensure_upload_allowed
from ..telegram.client_policy import configure_telethon_client, make_telegram_client, FLOOD_SLEEP_THRESHOLD_SECONDS, REQUEST_RETRIES

# Set by smart_router_main(); defined here so helpers never hit a NameError.
router_runtime_state = None

# Shared web-app Smart Auto context marker.
UNIFIED_AUTO_CONTEXT = None




# ==============================================================================
# SECTION: Governed downloads
# Download progress and governed source downloads.
# ==============================================================================

def _manager_conversion_progress_callback(filename: str, *, db=None, job_id=None):
    """Progress reporter for the local MP4 conversion step (0..1 fractions).

    Without it a long conversion prints nothing, the job shows no movement and the watchdog sees a quiet job.
    """
    db = db or globals().get("MEDIA_MANAGER_DB")
    job_id = str(job_id or globals().get("TRANSFER_JOB_ID") or "").strip() or None
    name = str(filename or "video")
    started = time.monotonic()
    state = {"last": 0.0, "pct": -5}

    def progress(fraction: float):
        now = time.monotonic()
        pct = int(max(0.0, min(1.0, float(fraction or 0.0))) * 100)
        final = pct >= 100
        if not final and now - state["last"] < 1.0:
            return
        state["last"] = now
        elapsed = now - started
        eta = (elapsed * (1 - fraction) / fraction) if fraction > 0.02 else None
        eta_text = f"{int(eta // 60)}:{int(eta % 60):02d}" if eta is not None else "--:--"
        line = f"Converting to MP4 · {pct}% · ETA {eta_text} · {name}"
        runner = globals().get("MEDIA_MANAGER_RUNNER")
        if runner is not None:
            try:
                with runner.lock:
                    runner.live = line[:200]
                    runner.operation = line[:200]
                runner.heartbeat(progress=True)
            except Exception:
                _swallowed_note("telegram_media_manager._manager_conversion_progress_callback")
        if db is not None and job_id:
            try:
                db.set_job_transfer_telemetry(
                    job_id, {"stage": "Converting to MP4", "current_item": name, "file_name": name,
                             "direction": "convert", "bytes_done": pct, "bytes_total": 100,
                             "eta_seconds": eta, "transfer_state": "complete" if final else "active"},
                    min_interval=1.0)
            except Exception:
                _swallowed_note("telegram_media_manager._manager_conversion_progress_callback.telemetry")
        if final or pct >= state["pct"] + 5:
            state["pct"] = pct
            print(line, flush=True)
            if db is not None:
                try:
                    db.log(line, "INFO", event="telegram_transfer.conversion_progress", job_id=job_id,
                           operation="Converting to MP4", details={"filename": name, "percent": pct})
                except Exception:
                    _swallowed_note("telegram_media_manager._manager_conversion_progress_callback.log")

    return progress


def _manager_download_progress_callback(filename: str, *, db=None, job_id=None):
    """Create the existing-job-line reporter used for large video downloads."""
    from ..engines.transfer_progress import RollingTransferProgress
    meter = RollingTransferProgress(window_seconds=10.0, minimum_seconds=2.0)
    state = {"last_report": 0.0, "last_logged_percent": -5, "last_bytes": 0, "last_total": 0,
             "completion_logged": False, "last_wait_logged": 0.0}
    db = db or globals().get("MEDIA_MANAGER_DB")
    job_id = str(job_id or globals().get("TRANSFER_JOB_ID") or "").strip() or None
    name = str(filename or "Telegram video")

    def _log(message, level="INFO", *, event, details=None):
        if db is None:
            return
        try:
            db.log(message, level, event=event, job_id=job_id, operation="Telegram video download", details=details or {})
        except Exception:
            _swallowed_note("telegram_media_manager._manager_download_progress_callback._log")
            pass

    def _store_telemetry(*, received=None, total=None, sample=None, telemetry_state=None, interval=1.0):
        if db is None or not job_id:
            return
        try:
            rate = (sample or {}).get("rate_bytes_per_second")
            db.set_job_transfer_telemetry(
                job_id,
                {"stage": "Telegram download", "current_item": name,
                 "file_name": name, "direction": "download",
                 "bytes_done": int(received if received is not None else state["last_bytes"]),
                 "bytes_total": int(total if total is not None else state["last_total"]),
                 "rate_bytes_per_second": rate,
                 "eta_seconds": (sample or {}).get("eta_seconds"),
                 "transfer_state": telemetry_state or ("active" if rate is not None else "warming_up")},
                min_interval=interval,
            )
        except Exception:
            _swallowed_note("telegram_media_manager._manager_download_progress_callback._store_telemetry")
            pass

    def _update_runner_line(line: str):
        runner = globals().get("MEDIA_MANAGER_RUNNER")
        if runner is None:
            return
        try:
            with runner.lock:
                runner.live = str(line or "")[:200]
                runner.operation = str(line or "")[:200]
            runner.heartbeat(progress=True)
        except Exception:
            _swallowed_note("telegram_media_manager._manager_download_progress_callback._update_runner_line")
            pass

    def progress(received: int, total: int):
        now = time.monotonic()
        sample = meter.update(received, total, now=now)
        percent = int(sample["percent"])
        rate = float(sample["rate_mib_per_second"] or 0.0)
        eta = str(sample["eta_label"])
        state["last_bytes"] = int(received or 0)
        state["last_total"] = int(total or 0)
        final = bool(total and int(received or 0) >= int(total))
        _store_telemetry(received=received, total=total, sample=sample,
                         telemetry_state="complete" if final else None,
                         interval=0.25 if final else 1.0)
        operation = f"Telegram transfer · video download {percent}% · {rate:.1f} MB/s · ETA {eta}"
        if final or now - float(state["last_report"]) >= 0.25:
            _update_runner_line(f"{operation} · {name}")
            print(f"\r{operation} · {name}", end="\n" if final else "\r", flush=True)
            state["last_report"] = now
        if total and (final or percent >= int(state["last_logged_percent"]) + 5):
            state["last_logged_percent"] = percent
            _log(
                f"Telegram video download {percent}% · {int(received):,}/{int(total):,} bytes · "
                f"{rate:.1f} MB/s · ETA {eta} · {name}",
                "SUCCESS" if final else "INFO", event="telegram_transfer.video_download_progress",
                details={"filename": name, "percent": percent, "bytes_received": int(received),
                         "bytes_total": int(total), "bytes_remaining": max(0, int(total)-int(received)),
                         "rate_mib_per_second": round(rate, 2), "eta_seconds": sample["eta_seconds"]},
            )
        return None

    def status(message: str, wait_seconds: int = 0):
        text = str(message or "Telegram asked to slow down")
        _update_runner_line(f"{text} · {name}")
        print(f"\r{text} · {name}", end="\r", flush=True)
        _store_telemetry(telemetry_state="paused", interval=0.25)
        if wait_seconds:
            _log(text, "WARN", event="telegram_transfer.download_wait",
                 details={"filename": name, "wait_seconds": int(wait_seconds),
                          "bytes_received": state["last_bytes"], "bytes_total": state["last_total"]})

    def complete(total_bytes: int | None = None):
        if state["completion_logged"]:
            return
        state["completion_logged"] = True
        received = int(total_bytes if total_bytes is not None else state["last_total"] or state["last_bytes"])
        sample = meter.finish(received, received or state["last_total"])
        _store_telemetry(received=received, total=received or state["last_total"],
                         sample=sample, telemetry_state="complete", interval=0.25)
        seconds = max(0.001, float(sample["elapsed_seconds"]))
        average = float(sample["average_mib_per_second"] or 0.0)
        size_text = human_size(received) if received else "0 bytes"
        message = f"Telegram download complete: {name} — {size_text} in {seconds:.0f} s ({average:.1f} MB/s)"
        _update_runner_line(message)
        print(message, flush=True)
        _log(message, "SUCCESS", event="telegram_transfer.video_download_complete",
             details={"filename": name, "bytes_total": received, "elapsed_seconds": round(seconds, 2),
                      "average_rate_mib_per_second": round(average, 2)})

    progress.status = status
    progress.complete = complete
    return progress


async def _download_media_governed(client, media, *args, **kwargs):
    """Download through aligned byte ranges, holding the Telegram file quota once per file."""
    governor = getattr(client, "_telegram_archive_governor", None)
    file_size = 0
    dc_id = 0
    document = None
    try:
        file_obj = getattr(media, "file", None)
        file_size = int(getattr(file_obj, "size", 0) or 0)
        document = getattr(media, "document", None) or getattr(getattr(media, "media", None), "document", None)
        dc_id = int(getattr(document, "dc_id", 0) or 0)
    except Exception:
        file_size = 0
        dc_id = 0
    destination = kwargs.get("file")
    thumb_request = kwargs.get("thumb") is not None
    progress_callback = kwargs.get("progress_callback")
    is_full_document_path = bool(document is not None and file_size > 0 and destination not in (None, bytes)
                                 and not thumb_request and isinstance(destination, (str, os.PathLike)))
    if is_full_document_path and file_size >= 1024 * 1024 and not callable(progress_callback):
        filename = getattr(getattr(media, "file", None), "name", None) or Path(destination).name
        progress_callback = _manager_download_progress_callback(str(filename), db=globals().get("MEDIA_MANAGER_DB"),
                                                                job_id=globals().get("TRANSFER_JOB_ID"))
        kwargs["progress_callback"] = progress_callback

    if is_full_document_path:
        # Download to an adjacent temporary file so a failed read never replaces
        # an existing valid destination. One file quota is held for the complete
        # download, while each byte request still uses the range governor.
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_name(target.name + ".ax-download-part")
        current_message = media
        import inspect

        async def refresh_reference():
            nonlocal current_message
            try:
                entity = getattr(current_message, "chat_id", None) or getattr(current_message, "peer_id", None)
                fresh = await client.get_messages(entity, ids=int(current_message.id))
                if fresh:
                    current_message = fresh
                    return fresh
            except Exception:
                _swallowed_note("telegram_media_manager._download_media_governed.refresh_reference")
                pass
            return None

        async def download_ranges(*, file_slot_held: bool):
            from ..core.aligned_telegram_range import fetch_aligned_range
            started = time.monotonic()
            try:
                with open(temp_target, "wb") as stream:
                    position = 0
                    while position < file_size:
                        size = min(512 * 1024, file_size - position)
                        data = await fetch_aligned_range(
                            client, current_message, position, size, fallback=True,
                            telethon_functions=telethon_lazy.functions(), telethon_types=telethon_lazy.types(),
                            priority="range", cache_policy="coalesce", refresh_message=refresh_reference,
                            file_slot_held=file_slot_held,
                            status_callback=getattr(progress_callback, "status", None),
                        )
                        if len(data) != size:
                            raise IOError(f"Short Telegram document read at {position}: {len(data)}/{size} bytes")
                        stream.write(data)
                        position += len(data)
                        if callable(progress_callback):
                            value = progress_callback(position, file_size)
                            if inspect.isawaitable(value):
                                await value
                actual_size = temp_target.stat().st_size
                if actual_size != file_size:
                    raise IOError(f"Telegram document size mismatch: {actual_size}/{file_size} bytes")
                os.replace(temp_target, target)
                finish = getattr(progress_callback, "complete", None)
                if callable(finish):
                    finish(file_size)
                return str(target)
            except BaseException:
                try:
                    temp_target.unlink(missing_ok=True)
                except Exception:
                    _swallowed_note("telegram_media_manager._download_media_governed.download_ranges")
                    pass
                raise

        if governor is not None and callable(getattr(governor, "telegram_file_slot", None)):
            async with governor.telegram_file_slot(file_size=file_size, dc_id=dc_id):
                return await download_ranges(file_slot_held=True)
        return await download_ranges(file_slot_held=False)

    if governor is not None and callable(getattr(governor, "telegram_file_slot", None)):
        async with governor.telegram_file_slot(file_size=file_size, dc_id=dc_id):
            result = await client.download_media(media, *args, **kwargs)
    else:
        result = await client.download_media(media, *args, **kwargs)
    finish = getattr(progress_callback, "complete", None)
    if callable(finish) and file_size >= 1024 * 1024:
        finish(file_size)
    return result


async def download_source_file(client, source, message, output_path, progress_callback=None):
    """Download a Telegram source, optionally reporting live rolling speed/ETA."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"file": str(path)}
    if progress_callback is not None:
        kwargs["progress_callback"] = progress_callback
    result = await _download_media_governed(client, message, **kwargs)
    final = Path(str(result)) if result else path
    if not final.is_file() or final.stat().st_size <= 0:
        raise RuntimeError(f"Telegram source download failed for message {getattr(message, 'id', '?')}")
    return str(final)

# PyAV is deliberately NOT imported at module load time. It is a native
# extension and a broken Windows DLL can terminate the interpreter before a
# Python exception is raised. Local media operations use the isolated native
# media worker; remote/legacy paths may lazy-load PyAV only when explicitly
# needed. Startup diagnostics run in a disposable child process.
av = None

try:
    from PIL import Image, ImageDraw, ImageFont
except (ImportError, OSError) as exc:
    Image = None
    ImageDraw = None
    ImageFont = None
    STARTUP_IMPORT_ERRORS.append(f"Pillow import failed: {type(exc).__name__}: {exc}")

APP_DIR = Path(__file__).resolve().parent
CACHE_DIR = config.MEDIA_MANAGER_STATE_DIR
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = config.MEDIA_MANAGER_CONFIG_PATH
SESSION_FILE = config.MEDIA_MANAGER_SESSION_PATH
LEGACY_SESSION_FILE = APP_DIR / "telegram_uploader"
STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_vertical_video_archiver_state.json"

# ---------------------------------------------------------------------------
# Host settings (set through configure(), see the end of this file).
# When Archive X runs this module it used to swap functions inside it at run
# time. These documented settings replace that: the defaults below are the
# standalone-script behaviour.
# ---------------------------------------------------------------------------
TELEGRAM_REQUEST_BROKER_VERSION = "V28.9-adaptive-rpc-1"
LOGIN_PROVIDER = None                 # async callable(config) -> client
THUMBNAILS_UNAVAILABLE_REASON = None  # str: thumbnail generation is refused
MOVER_ANY_ORIENTATION = False         # True: "content mover" (any orientation,
                                      # videos without width/height included)
ON_EMPTY_SCAN = None                  # async callable(client, source, mode, any_orientation)
PAUSE_EVENT = None                    # threading.Event supplied by Archive X runner
TRANSFER_JOB_ID = None                  # current Archive X job id
TRANSFER_RESUME_JOB_ID = None           # explicit restart-resume job id

VIDEO_EXTENSIONS = {
    ".mp4", ".m4v", ".m4p", ".mov", ".qt", ".mkv", ".mk3d", ".avi",
    ".divx", ".wmv", ".asf", ".flv", ".f4v", ".f4p", ".mpg",
    ".mpeg", ".mpe", ".mpv", ".m2v", ".mpeg1", ".mpeg2", ".webm",
    ".ts", ".mts", ".m2ts", ".m2t", ".3gp", ".3g2", ".vob",
    ".ogv", ".ogx", ".rm", ".rmvb", ".amv", ".mxf", ".roq",
    ".nsv", ".yuv",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
ROWS, COLS = 6, 6
COUNT = ROWS * COLS
OUT_WIDTH = 1536
PADDING = 5
JPEG_QUALITY = 95




# ==============================================================================
# SECTION: Console and JSON helpers
# State files, prompts and progress printers.
# ==============================================================================

def load_json(path, default):
    if Path(path) == CONFIG_FILE:
        from ..core.encrypted_files import read_json
        candidates = [Path(path), Path(str(path) + ".latest")]
        candidates = [candidate for candidate in candidates if candidate.exists()]
        if not candidates:
            return default
        candidate = max(candidates, key=lambda item: item.stat().st_mtime)
        return read_json(candidate, "archive-x-media-manager-v1", default)
    # If Windows briefly locked the main state file during the previous run,
    # save_json may have left a .latest sidecar. Prefer it when it is newer.
    candidates = [path]
    sidecar = path.with_suffix(path.suffix + ".latest")
    if sidecar.exists():
        candidates.append(sidecar)
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    last_error = None
    for candidate in candidates:
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TypeError, ValueError) as exc:
            last_error = exc
            continue
    if last_error is not None:
        db = globals().get("MEDIA_MANAGER_DB")
        if db is not None:
            db.log(
                f"Telegram Media Manager state read failed for {path}: "
                f"{type(last_error).__name__}: {last_error}",
                "WARN",
            )
    return default


def save_json(path, data):
    """Save JSON safely on Windows, including when the old file is briefly locked."""
    if Path(path) == CONFIG_FILE:
        from ..core.encrypted_files import write_json
        write_json(Path(path), data, "archive-x-media-manager-v1")
        return
    payload = json.dumps(data, indent=2, ensure_ascii=False)
    tmp = path.with_suffix(path.suffix + ".tmp")
    last_error = None

    for attempt in range(6):
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(str(tmp), str(path))
            try:
                sidecar = path.with_suffix(path.suffix + ".latest")
                if sidecar.exists():
                    sidecar.unlink()
            except OSError:
                pass
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.25 * (attempt + 1))
        except OSError as exc:
            last_error = exc
            time.sleep(0.15 * (attempt + 1))

    # Antivirus/indexing software can keep the main JSON file locked briefly.
    # Keep the newest state in a sidecar instead of aborting the Telegram move.
    sidecar = path.with_suffix(path.suffix + ".latest")
    try:
        sidecar.write_text(payload, encoding="utf-8")
        print(f"  WARNING: state file is locked; saved latest state to {sidecar}")
        return
    except (OSError, UnicodeError, TypeError) as exc:
        last_error = exc
        db = globals().get("MEDIA_MANAGER_DB")
        if db is not None:
            db.log(
                f"Telegram Media Manager state sidecar save failed for {sidecar}: "
                f"{type(exc).__name__}: {exc}",
                "WARN",
            )

    raise last_error or RuntimeError(f"Could not save state file: {path}")


def ask_nonempty(prompt):
    while True:
        v = input(prompt).strip()
        if v:
            return v


def ask_yes_no(prompt, default=False):
    """Explicit Y/N confirmation for operations that can modify Telegram."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        answer = input(prompt + suffix).strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Please enter Y or N.")


def human_size(value):
    value = float(value or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or u == "TB":
            return f"{value:.1f} {u}"
        value /= 1024
    return f"{value:.1f} TB"


def progress_printer(label, *, with_rate: bool = False):
    start = time.monotonic()
    last = [start]
    def cb(current, total):
        now = time.monotonic()
        if now - last[0] < 0.25 and current != total:
            return
        last[0] = now
        pct = current / total * 100 if total else 0
        line = f"\r{label}: {pct:6.2f}% | {human_size(current)}/{human_size(total)}"
        if with_rate:
            elapsed = max(0.001, now - start)
            mib_per_second = (max(0, int(current or 0)) / elapsed) / (1024 * 1024)
            remaining = max(0, int(total or 0) - int(current or 0))
            eta = int(remaining / max(1.0, max(0, int(current or 0)) / elapsed)) if total else 0
            eta_label = (f"{eta // 3600:d}:{(eta % 3600) // 60:02d}:{eta % 60:02d}"
                         if eta >= 3600 else f"{eta // 60:02d}:{eta % 60:02d}")
            line += f" | {mib_per_second:.1f} MB/s | ETA {eta_label}"
        print(line, end="", flush=True)
    return cb


def upload_status_printer(label):
    def cb(message, wait_seconds=0):
        print(f"\n{label}: {message}", flush=True)
    return cb




# ==============================================================================
# SECTION: Session and login
# Session lookup, connection and login.
# ==============================================================================

async def probe_session(session_path, api_id, api_hash):
    client = None
    try:
        client = make_telegram_client(str(session_path), int(api_id), api_hash, request_retries=REQUEST_RETRIES, flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD_SECONDS)
        await client.connect()
        ok = await client.is_user_authorized()
        await client.disconnect()
        return ok
    except Exception:
        try:
            if client:
                await client.disconnect()
        except Exception:
            _swallowed_note("telegram_media_manager.probe_session")
            pass
        return False


def candidate_session_paths():
    out, seen = [], set()
    for p in (SESSION_FILE, LEGACY_SESSION_FILE):
        k = str(p.resolve()).lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


async def connect_with_retry(client, label="Telegram connection", attempts=8, require_authorized=True):
    """Reconnect Telegram with backoff instead of letting transient socket errors stop the run."""
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            if client.is_connected():
                return True
        except Exception:
            _swallowed_note("telegram_media_manager.connect_with_retry")
            pass

        try:
            print(f"\n[TELEGRAM] {label}: connecting (attempt {attempt}/{attempts})...", flush=True)
            await client.connect()
            if not require_authorized:
                print("[TELEGRAM] Connection established.", flush=True)
                return True
            if await client.is_user_authorized():
                print("[TELEGRAM] Connection restored.", flush=True)
                return True
            raise RuntimeError("Telegram session is no longer authorized")
        except Exception as exc:
            last_error = exc
            print(
                f"[TELEGRAM] Connection attempt {attempt}/{attempts} failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )
            try:
                await client.disconnect()
            except Exception:
                _swallowed_note("telegram_media_manager.connect_with_retry")
                pass
            if attempt < attempts:
                delay = min(30, 3 * attempt)
                print(f"[TELEGRAM] Waiting {delay}s before retry...", flush=True)
                await asyncio.sleep(delay)

    raise RuntimeError(f"Could not restore Telegram connection after {attempts} attempts: {last_error}")


def is_transient_telegram_connection_error(exc):
    """Identify Windows/Telegram socket failures that are safe to retry."""
    if isinstance(exc, (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)):
        return True
    text = str(exc or "").lower()
    markers = (
        "server closed the connection",
        "semaphore timeout",
        "winerror 121",
        "winerror 64",
        "connection reset",
        "connection aborted",
        "connection lost",
        "timed out",
        "timeout",
        "broken pipe",
        "cannot connect",
    )
    return any(m in text for m in markers)


async def login_and_get_client(config):
    """Return a logged-in client from the host provider or an interactive login."""
    if LOGIN_PROVIDER is not None:
        return await LOGIN_PROVIDER(config)
    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    if not api_id or not api_hash:
        legacy = load_json(config.MEDIA_MANAGER_CONFIG_PATH, {})
        api_id = api_id or legacy.get("api_id")
        api_hash = api_hash or legacy.get("api_hash")
    if not api_id or not api_hash:
        print("\nFIRST-TIME TELEGRAM SETUP")
        print("Get API ID/hash from https://my.telegram.org")
        while True:
            raw = ask_nonempty("Telegram API ID: ")
            try:
                api_id = int(raw); break
            except ValueError:
                print("API ID must be a number.")
        api_hash = ask_nonempty("Telegram API hash: ")
        config["api_id"], config["api_hash"] = api_id, api_hash
        save_json(CONFIG_FILE, config)

    selected = None
    print("\nChecking existing Telegram sessions...")
    for p in candidate_session_paths():
        if p.exists() or Path(str(p) + ".session").exists():
            if await probe_session(p, api_id, api_hash):
                selected = p
                print(f"Using authorized session: {p}")
                break
    if selected is None:
        selected = SESSION_FILE
    # Use a more tolerant connection profile. The mover may scan thousands of
    # messages, and Windows can report Telegram socket drops as WinError 64/121.
    # Telethon is allowed to reconnect automatically, while our scan layer below
    # also resumes from the last message ID instead of restarting the scan.
    client = make_telegram_client(
        str(selected), int(api_id), api_hash,
        timeout=30,
        connection_retries=10,
        retry_delay=5,
        auto_reconnect=True,
        request_retries=REQUEST_RETRIES,
        flood_sleep_threshold=FLOOD_SLEEP_THRESHOLD_SECONDS,
    )
    configure_telethon_client(client)
    await connect_with_retry(client, label="initial Telegram connection", require_authorized=False)
    if not await client.is_user_authorized():
        phone = ask_nonempty("Telegram phone number (international format): ")
        await client.send_code_request(phone)
        code = ask_nonempty("Telegram login code: ")
        try:
            await client.sign_in(phone=phone, code=code)
        except telethon_lazy.errors().SessionPasswordNeededError:
            await client.sign_in(password=ask_nonempty("Telegram 2-step verification password: "))
    return client




# ==============================================================================
# SECTION: Channel selection
# Channel catalog and interactive choice.
# ==============================================================================

async def load_channel_catalog(client):
    """Load and freeze the channel-number catalog once per run.

    Telegram dialog order can change between API calls. Therefore SOURCE and
    DESTINATION must never call get_dialogs() independently. One immutable
    catalog is created and all numeric selections use it.
    """
    print("\nLoading Telegram channel list once...", flush=True)
    dialogs = await client.get_dialogs(limit=None)

    channels = []
    seen_ids = set()
    for d in dialogs:
        entity = getattr(d, "entity", None)
        if not entity or not getattr(entity, "broadcast", False):
            continue
        entity_id = int(getattr(entity, "id", 0) or 0)
        if not entity_id or entity_id in seen_ids:
            continue
        seen_ids.add(entity_id)
        title = str(getattr(entity, "title", None) or getattr(d, "name", None) or entity_id)
        channels.append({
            "id": entity_id,
            "title": title,
            "username": getattr(entity, "username", None),
            "entity": entity,
        })

    if not channels:
        raise RuntimeError("No Telegram channels are available for selection.")

    # Sort once, assign numbers once, then never rebuild this list during the run.
    channels.sort(key=lambda x: (x["title"].casefold(), x["id"]))
    for n, item in enumerate(channels, 1):
        item["number"] = n
    return channels


def print_channel_catalog(catalog, blocked_ids=None, source_id=None):
    blocked_ids = {int(x) for x in (blocked_ids or set())}
    source_id = int(source_id) if source_id is not None else None

    print("\nAVAILABLE CHANNELS")
    print("------------------")
    for item in catalog:
        marker = ""
        if item["id"] in blocked_ids:
            if source_id is not None and item["id"] == source_id:
                marker = "  [SOURCE - DO NOT SELECT]"
            else:
                marker = "  [ALREADY SELECTED - DO NOT SELECT]"
        suffix = f"  @{item['username']}" if item.get("username") else ""
        print(f" {item['number']:3d}. {item['title']}{suffix}{marker}")


async def choose_channel_from_catalog(client, catalog, role, blocked_ids=None, source_id=None, show_catalog=True):
    """Prompt for a channel by number or ID, skipping blocked channels."""
    blocked_ids = {int(x) for x in (blocked_ids or set())}
    print("\n" + "=" * 82)
    print(f" {role.upper()} CHANNEL SELECTION")
    print("=" * 82)
    print("Channel numbers are FIXED for this run.")
    if show_catalog:
        print_channel_catalog(catalog, blocked_ids=blocked_ids, source_id=source_id)
    else:
        print("Use the SAME fixed channel numbers shown above.")
    print("\nEnter the channel NUMBER, or type @username/channel link.")

    while True:
        a = ask_nonempty(f"{role} channel > ")
        if a.isdigit():
            number = int(a)
            item = next((x for x in catalog if x["number"] == number), None)
            if item is None:
                print(f"Enter a number from 1 to {len(catalog)}.")
                continue
            entity_id = int(item["id"])
            if entity_id in blocked_ids:
                if source_id is not None and entity_id == int(source_id):
                    print("That is the SOURCE channel. Choose a different number.")
                else:
                    print("That channel is already selected. Choose a different number.")
                continue
            print(f"\nSelected {role.lower()} channel: {item['title']} (number {number}, id {entity_id})", flush=True)
            return item["entity"]

        try:
            entity = await client.get_entity(a)
            entity_id = int(getattr(entity, "id", 0) or 0)
            if entity_id in blocked_ids:
                if source_id is not None and entity_id == int(source_id):
                    print("That is the SOURCE channel. Choose another channel.")
                else:
                    print("That channel is already selected. Choose another channel.")
                continue
            print(
                f"\nSelected {role.lower()} channel: "
                f"{getattr(entity, 'title', None) or getattr(entity, 'username', None) or entity_id} "
                f"(id {entity_id})",
                flush=True,
            )
            return entity
        except Exception as exc:
            print(f"Could not open channel: {type(exc).__name__}: {exc}")


async def choose_channel(client, role, exclude_id=None, exclude_ids=None):
    """Compatibility wrapper for older smart-router code."""
    catalog = await load_channel_catalog(client)
    blocked = set(exclude_ids or set())
    if exclude_id is not None:
        blocked.add(int(exclude_id))
    return await choose_channel_from_catalog(
        client, catalog, role, blocked_ids=blocked, source_id=exclude_id
    )




# ==============================================================================
# SECTION: Message and thumbnail helpers
# Message predicates and source thumbnail lookup.
# ==============================================================================

def is_video_message(message):
    # Keep terminal and modular Media Manager scans aligned with the main Library scanner.
    try:
        from ..engines.media_utils import is_video_message as _shared_is_video_message
        return bool(_shared_is_video_message(message))
    except Exception:
        if getattr(message, "video", None):
            return True
        doc = getattr(message, "document", None)
        if not doc:
            return False
        mime = str(getattr(doc, "mime_type", "") or "").lower()
        if mime.startswith("video/"):
            return True
        try:
            for attr in getattr(doc, "attributes", ()) or ():
                if type(attr).__name__ == "DocumentAttributeVideo":
                    return True
            return Path(str(getattr(message.file, "name", "") or "")).suffix.lower() in VIDEO_EXTENSIONS
        except Exception:
            return False


def is_photo_message(message):
    return bool(message and getattr(message, "photo", None))


def safe_video_name(message):
    try:
        name = message.file.name
    except Exception:
        name = None
    if not name:
        name = f"telegram_{message.id}.mp4"
    if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
        name += ".mp4"
    return name


def source_thumbnail_filename_candidates(video_name):
    """Known thumbnail filename conventions used by the existing uploader."""
    name = str(video_name or "").strip()
    if not name:
        return set()
    p = Path(name)
    values = {
        name + ".jpg",
        p.with_suffix(".jpg").name,
    }
    if p.suffix:
        values.add(f"{p.stem}_{p.suffix.lstrip('.')}.jpg")
    return {str(v).strip().lower() for v in values if v}


def source_thumbnail_name(message):
    try:
        name = message.file.name
        return str(name or "")
    except Exception:
        return ""


async def find_source_thumbnail(client, source, video):
    """Deprecated compatibility shim. Archive X thumbnails are local snapshots."""
    return None


async def resolve_saved_entity(client, entity_id: int):
    """Resolve a persisted Telegram entity even when Telethon lacks its access hash.

    Bare numeric user/channel IDs are not sufficient for every Telethon request.
    Prefer the normal entity cache, then hydrate the cache from the existing
    dialog list and return the exact dialog entity. This is only used for
    persisted transfer configurations; normal channel selection still uses the
    frozen catalog.
    """
    entity_id = int(entity_id)
    try:
        return await client.get_entity(entity_id)
    except Exception as first_exc:
        dialogs = await client.get_dialogs(limit=None)
        for dialog in dialogs:
            entity = getattr(dialog, "entity", None)
            if entity is not None:
                raw_id = int(getattr(entity, "id", 0) or 0)
                if raw_id == entity_id or str(canonical_peer_id(entity)) == str(entity_id):
                    return entity
        raise RuntimeError(
            f"Saved Telegram entity {entity_id} is not available in the current account dialogs; "
            "reselect the source/destination channel instead of retrying the stale entity ID."
        ) from first_exc


# ---------------------------------------------------------------------------
# The large feature sections live in media_manager/mm_*.py (split in v29.4.387).
# They are executed here, in order, inside this module's own namespace, so the
# result is identical to the original single file.
# ---------------------------------------------------------------------------
_MM_PARTS = ('mm_thumbnail_engine', 'mm_vertical_archiver', 'mm_smart_router', 'mm_vertical_mover', 'mm_channel_organizer', 'mm_duplicate_finder')




# ==============================================================================
# SECTION: Fragment loader
# Executes mm_*.py in this namespace; those files share these globals.
# ==============================================================================

def _mm_load_parts(namespace):
    from importlib.machinery import SourceFileLoader as _Loader
    from pathlib import Path as _Path
    for _name in _MM_PARTS:
        _path = _Path(__file__).with_name(_name + ".py")
        # The code comes from the usual __pycache__ bytecode when it is current (and is compiled and saved there when it is not): compiling these six
        # large files on every start took about 55 ms of it. A folder that cannot be written to just compiles each time, as before.
        _module = "telegram_archive.media_manager." + _name
        exec(_Loader(_module, str(_path)).get_code(_module), namespace)


_mm_load_parts(globals())
del _mm_load_parts


# ============================================================================
# UNIFIED ENTRYPOINT
# ============================================================================

MODE_DESCRIPTIONS = {
    "1": "Transfer job — VIDEO/FILE source to VIDEO/FILE destination with orientation, duplicate policy and multi-source channel processing",
    "2": "Duplicate job — scan source channels for duplicate pairs/items and move older copies to the selected trash channel",
    "3": "Channel cleanup — repair missing thumbnail/metadata captions and remove orphaned thumbnail/image messages",
}




# ==============================================================================
# SECTION: Menu and workflows
# Interactive menu, unified auto and host configuration.
# ==============================================================================

def print_main_menu():
    print("\n" + "=" * 94)
    print(" TELEGRAM JOB MANAGER")
    print(f" VERSION {APP_VERSION}")
    print("=" * 94)
    print("Start by choosing the type of job. The transfer workflow then asks for media type,\n"
          "orientation, source-channel count, and destination before any scan/download work begins.\n")
    for k, v in MODE_DESCRIPTIONS.items():
        print(f"  {k}. {v}")
    print("  0. Exit")


async def main():
    print_main_menu()
    while True:
        choice = input("\nSelect module > ").strip()
        if choice == "0":
            return
        if choice in ("1", "transfer", "t"):
            await telegram_transfer_job_main()
            return
        if choice in ("2", "duplicate", "d"):
            await duplicate_finder_main()
            return
        if choice in ("3", "cleanup", "c"):
            await channel_cleanup_job_main()
            return
        print("Invalid selection. Choose 0-3.")

# ---------------------------------------------------------------------------
# Host API used by Archive X (media_manager/runner.py)
# ---------------------------------------------------------------------------
_STATE_FILE_NAMES = ("STATE_FILE", "ROUTER_STATE_FILE", "VERTICAL_MOBILE_STATE_FILE",
                     "ORGANIZER_STATE_FILE", "DUPLICATE_STATE_FILE")

WORKFLOWS = {
    "TRANSFER": "Transfer job",
    "DUPLICATE": "Duplicate job",
    "CLEANUP": "Channel cleanup",
    "AUTO": "Smart Auto — all Telegram workflows",
    "1": "Content mover (any orientation)",
    "2": "Vertical/mobile mover",
    "3": "Channel organizer",
    "4": "Duplicate finder",
    "5": "Smart router",
}


async def unified_smart_auto_main():
    """Run all Telegram media workflows as one guided operation.

    Smart routing subsumes the old content mover and vertical/mobile routing
    paths. The same Telegram session then runs thumbnail/orphan repair and
    duplicate cleanup against the same source.
    """
    global UNIFIED_AUTO_CONTEXT
    print("=" * 100)
    print(" TELEGRAM TRANSFER MANAGER — SMART AUTO")
    print("=" * 100)
    print("One guided workflow: smart routing + vertical/mobile handling +")
    print("missing-thumbnail/orphan repair + duplicate cleanup.")
    print("Safety: every destructive action remains COPY -> VERIFY -> DELETE.")

    config = load_json(CONFIG_FILE, {})
    client = None
    try:
        client = await login_and_get_client(config)
        me = await client.get_me()
        print(f"\nLogged in as: {getattr(me, 'first_name', '') or getattr(me, 'username', '') or 'account'}")

        catalog = await load_channel_catalog(client)
        print("\nChannel list loaded ONCE. Channel numbers remain fixed for this run.")

        source = await choose_channel_from_catalog(client, catalog, "SOURCE", show_catalog=True)
        source_id = int(source.id)
        selected_ids = {source_id}

        print("\nSMART ROUTING DESTINATIONS")
        print("  MAIN       = normal streamable videos")
        print("  MISSING    = streamable videos without thumbnails")
        print("  VERTICAL   = portrait / mobile videos")
        print("  FILE       = conversion-failed / non-streamable fallback")
        destinations = {}
        for role in (DEST_MAIN, DEST_MISSING, DEST_VERTICAL, DEST_FILE):
            entity = await choose_channel_from_catalog(
                client, catalog, f"DESTINATION — {DEST_ROLE_NAMES[role]}",
                blocked_ids=set(selected_ids), source_id=source_id, show_catalog=False
            )
            destinations[role] = entity
            selected_ids.add(int(entity.id))

        repair = await choose_channel_from_catalog(
            client, catalog, "REPAIR DESTINATION",
            blocked_ids=set(selected_ids), source_id=source_id, show_catalog=False
        )
        selected_ids.add(int(repair.id))

        trash = await choose_channel_from_catalog(
            client, catalog, "TRASH DESTINATION",
            blocked_ids=set(selected_ids), source_id=source_id, show_catalog=False
        )

        print("\n" + "=" * 100)
        print(" SMART AUTO CONFIGURATION")
        print("=" * 100)
        print(f"Source                 : {channel_display_name(source)}")
        print(f"Main destination      : {channel_display_name(destinations[DEST_MAIN])}")
        print(f"Missing-thumbnail     : {channel_display_name(destinations[DEST_MISSING])}")
        print(f"Vertical/mobile       : {channel_display_name(destinations[DEST_VERTICAL])}")
        print(f"Non-streamable FILE   : {channel_display_name(destinations[DEST_FILE])}")
        print(f"Repair destination    : {channel_display_name(repair)}")
        print(f"Trash destination     : {channel_display_name(trash)}")
        print("\nThe single approval below enables all phases. Failed items stay in source.")
        if not ask_yes_no("Start Smart Auto? This may delete source messages after verification.", default=False):
            print("Cancelled. No Telegram messages were changed.")
            return

        print("\n" + "#" * 100)
        print(" PHASE 1/3 — SMART ROUTING / CONTENT MOVES")
        print("#" * 100)
        await smart_router_main(
            client_override=client,
            source_override=source,
            destinations_override=destinations,
            auto_confirm=True,
            disconnect_client=False,
        )

        print("\n" + "#" * 100)
        print(" PHASE 2/3 — THUMBNAIL / ORPHAN ORGANIZER")
        print("#" * 100)
        await telegram_channel_organizer_main(
            client_override=client,
            source_override=source,
            repair_override=repair,
            trash_override=trash,
            auto_confirm=True,
            disconnect_client=False,
        )

        print("\n" + "#" * 100)
        print(" PHASE 3/3 — DUPLICATE CLEANUP")
        print("#" * 100)
        await duplicate_finder_main(
            client_override=client,
            source_override=source,
            trash_override=trash,
            auto_confirm=True,
            disconnect_client=False,
        )

        print("\n" + "=" * 100)
        print(" SMART AUTO COMPLETE")
        print("=" * 100)
        print("All Telegram transfer workflows were processed through one shared session.")
        print("Verified failures remain in the source for a later retry.")
    except KeyboardInterrupt:
        print("\nStopped by user. Verified completed items remain resumable.")
        raise
    finally:
        UNIFIED_AUTO_CONTEXT = None
        if client:
            try:
                await client.disconnect()
            except Exception:
                _swallowed_note("telegram_media_manager.unified_smart_auto_main")
                pass


def configure(*, input_fn=None, print_fn=None, state_dir=None, login=None,
              thumbnails_unavailable_reason=None, on_empty_scan=None,
              startup_errors=None, pillow=None, pause_event=None):
    """Set how this module talks to its host. Every argument is optional;
    calling configure() with no arguments restores standalone behaviour
    for the hooks."""
    global input, print, APP_DIR, LOGIN_PROVIDER, THUMBNAILS_UNAVAILABLE_REASON, ON_EMPTY_SCAN, PAUSE_EVENT
    global Image, ImageDraw, ImageFont, av
    import builtins as _b
    input = input_fn or _b.input
    print = print_fn or _b.print
    if state_dir is not None:
        state_dir = Path(state_dir)
        state_dir.mkdir(parents=True, exist_ok=True)
        APP_DIR = state_dir
        for name in _STATE_FILE_NAMES:
            current = globals().get(name)
            if current is not None:
                globals()[name] = state_dir / Path(current).name
    LOGIN_PROVIDER = login
    THUMBNAILS_UNAVAILABLE_REASON = thumbnails_unavailable_reason or None
    ON_EMPTY_SCAN = on_empty_scan
    PAUSE_EVENT = pause_event
    if startup_errors is not None:
        STARTUP_IMPORT_ERRORS[:] = list(startup_errors)
    if pillow is not None and Image is None:
        Image, ImageDraw, ImageFont = pillow
    # Never import PyAV into the host process.
    av = None


async def run_workflow(mode: str, *, job_id=None, resume_job_id=None, job_detail=None, job_manager=None, manager_loop=None):
    """Run one workflow by its canonical mode identifier."""
    global MOVER_ANY_ORIENTATION
    mode = str(mode)
    if mode not in WORKFLOWS:
        raise ValueError(f"Unknown workflow {mode}")
    previous = MOVER_ANY_ORIENTATION
    MOVER_ANY_ORIENTATION = (mode == "1")
    try:
        if mode == "TRANSFER":
            # Legacy reference: await telegram_transfer_job_main()
            await telegram_transfer_job_main(job_id=job_id, resume_job_id=resume_job_id, job_detail=job_detail, job_manager=job_manager, manager_loop=manager_loop)
        elif mode == "DUPLICATE":
            await duplicate_finder_main()
        elif mode == "CLEANUP":
            await channel_cleanup_job_main()
        elif mode == "AUTO":
            await unified_smart_auto_main()
        elif mode in ("1", "2"):
            if mode == "1":
                print("Content mover: VIDEO/FILE mover for videos of ANY orientation.")
            await vertical_mobile_mover_main()
        elif mode == "3":
            await telegram_channel_organizer_main()
        elif mode == "4":
            await duplicate_finder_main()
        else:
            await smart_router_main()
    finally:
        MOVER_ANY_ORIENTATION = previous


if __name__ == "__main__":
    import traceback
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped by user.")
    except BaseException:
        crash_log = config.MEDIA_MANAGER_STATE_DIR / "telegram_media_manager_error.log"
        try:
            crash_log.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            _swallowed_note("telegram_media_manager.<module>")
            pass
        print("\nUnhandled fatal error.")
        print(f"Error log: {crash_log}")
    finally:
        try:
            input("\nPress Enter to close...")
        except Exception:
            _swallowed_note("telegram_media_manager.<module>")
            pass




# ==============================================================================
# SECTION: FFmpeg conversion
# FFmpeg progress, probing and streamable conversion.
# ==============================================================================

def _ffmpeg_time_seconds(value: str) -> float:
    """Parse an FFmpeg progress time value (``out_time_us``/``out_time_ms`` are
    microseconds despite the name; ``out_time`` is HH:MM:SS.micro)."""
    value = str(value or "").strip()
    if not value or value.upper() == "N/A":
        return 0.0
    if ":" in value:
        try:
            hours, minutes, seconds = value.split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except (TypeError, ValueError):
            return 0.0
    try:
        return max(0.0, float(value) / 1_000_000.0)
    except (TypeError, ValueError):
        return 0.0


def _run_ffmpeg_with_progress_sync(cmd, duration: float, label: str, on_progress=None) -> None:
    """Run FFmpeg (expects ``-progress pipe:1``) and report fractional progress.

    Runs in a worker thread with a plain subprocess so it works with any asyncio
    event-loop type on Windows. ``on_progress`` receives a float 0.0-1.0, the
    same convention as core.internal_media. A non-zero exit raises with the
    tail of FFmpeg's stderr so the real local failure reason is reported.
    """
    stderr_lines: "queue.Queue[str]" = queue.Queue()
    proc = subprocess.Popen(
        [str(part) for part in cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

    def _drain_stderr():
        try:
            for line in proc.stderr:
                stderr_lines.put(line.rstrip())
        except Exception:
            _swallowed_note("telegram_media_manager._run_ffmpeg_with_progress_sync._drain_stderr")
            pass

    drain = threading.Thread(target=_drain_stderr, name=f"ffmpeg-{label}-stderr", daemon=True)
    drain.start()
    from ..core.child_processes import tracked as _tracked
    _track = _tracked(proc, f"FFmpeg · {label}", kind="ffmpeg", job_id=globals().get("TRANSFER_JOB_ID"))
    _handle = _track.__enter__()
    total = float(duration or 0.0)
    last_reported = -1.0
    try:
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key in {"out_time_us", "out_time_ms", "out_time"} and total > 0 and on_progress is not None:
                fraction = max(0.0, min(1.0, _ffmpeg_time_seconds(value) / total))
                _handle.set_progress(fraction)
                if fraction - last_reported >= 0.01:
                    last_reported = fraction
                    try:
                        on_progress(fraction)
                    except Exception:
                        _swallowed_note("telegram_media_manager._run_ffmpeg_with_progress_sync")
                        pass
            elif key == "progress" and value == "end" and on_progress is not None:
                try:
                    on_progress(1.0)
                except Exception:
                    _swallowed_note("telegram_media_manager._run_ffmpeg_with_progress_sync")
                    pass
        returncode = proc.wait()
    except BaseException:
        try:
            proc.kill()
        except Exception:
            _swallowed_note("telegram_media_manager._run_ffmpeg_with_progress_sync")
            pass
        _track.__exit__(None, None, None)
        raise
    _track.__exit__(None, None, None)
    drain.join(timeout=2)
    if returncode != 0:
        tail = []
        while not stderr_lines.empty():
            tail.append(stderr_lines.get_nowait())
        detail = " | ".join(x for x in tail[-6:] if x) or f"exit code {returncode}"
        raise RuntimeError(f"Local media preparation failed ({label}): FFmpeg {detail}")


async def run_ffmpeg_with_progress(cmd, duration: float, label: str, on_progress=None) -> None:
    """Async wrapper for :func:`_run_ffmpeg_with_progress_sync` (v29.4.221:
    this helper was referenced by convert_to_streamable_mp4 but never defined)."""
    await asyncio.to_thread(_run_ffmpeg_with_progress_sync, cmd, duration, label, on_progress)


def _ffprobe_local_sync(path) -> dict:
    """External-ffprobe probe returning the same keys as core.internal_media.probe."""
    ffprobe = find_app_ffprobe()
    if not ffprobe:
        raise RuntimeError("Local media probe failed: neither PyAV nor ffprobe is available")
    result = subprocess.run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(f"Local media probe failed: ffprobe {(result.stderr or '').strip()[-300:] or result.returncode}")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams") or []
    fmt = data.get("format") or {}
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    def _float(value) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    duration = _float(fmt.get("duration")) or _float((video or {}).get("duration"))
    fps = 0.0
    rate = str((video or {}).get("avg_frame_rate") or "")
    if "/" in rate:
        num, _, den = rate.partition("/")
        fps = _float(num) / _float(den) if _float(den) else 0.0
    return {
        "duration": duration,
        "format_name": str(fmt.get("format_name") or ""),
        "video_codec": str((video or {}).get("codec_name") or ""),
        "audio_codec": str((audio or {}).get("codec_name") or ""),
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "fps": fps,
        "pix_fmt": str((video or {}).get("pix_fmt") or ""),
        "has_audio": audio is not None,
        "bitrate": int(_float(fmt.get("bit_rate"))),
    }


async def ffprobe_local(path) -> dict:
    """Async external-ffprobe fallback (v29.4.221: previously referenced but undefined)."""
    return await asyncio.to_thread(_ffprobe_local_sync, path)


async def convert_to_streamable_mp4(input_path, output_path, on_progress=None):
    """Normalize a downloaded video for Telegram streaming without needless re-encoding.

    Telegram-streamable target:
      - MP4 container
      - H.264/AVC OR H.265/HEVC video
      - AAC audio
      - +faststart for progressive/range playback

    The video stream is preserved whenever it is already H.264/H.265.  Only the
    container and/or audio stream are changed when necessary.  Other video codecs
    are transcoded to H.264 + AAC.
    """
    # Prefer the application's PyAV environment. External FFmpeg is a fallback
    # only for operations PyAV cannot perform (for example synthetic silent AAC).
    try:
        from ..core import internal_media
        meta = await asyncio.to_thread(internal_media.probe, input_path)
    except Exception as internal_probe_error:
        ffmpeg = find_app_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(f"Internal PyAV media engine failed: {internal_probe_error}")
        meta = await ffprobe_local(input_path)
    duration = float(meta.get("duration") or 0.0)
    format_names = {x.strip().lower() for x in meta.get("format_name", "").split(",") if x.strip()}
    vcodec = (meta.get("video_codec") or "").lower()
    acodec = (meta.get("audio_codec") or "").lower()

    supported_video = vcodec in {"h264", "avc1", "hevc", "h265"}
    aac_audio = acodec in {"aac", "mp4a"}
    streamable_container = bool(format_names & {"mp4", "mov", "m4v"})
    already_streamable = streamable_container and supported_video and aac_audio

    print(
        f"  Format check: container={next(iter(format_names), '?')} "
        f"| video={vcodec or 'none'} | audio={acodec or 'none'} "
        f"| target=MP4 + (H.264/H.265) + AAC "
        f"| {'COMPATIBLE' if already_streamable else 'NORMALIZE REQUIRED'}"
    )

    # Internal PyAV path first. Compatible H.264/H.265 + AAC is remuxed;
    # H.264/H.265 with non-AAC audio preserves video and re-encodes audio.
    try:
        from ..core import internal_media
        if already_streamable:
            # Lossless local remux. Do not invoke the PyAV template-stream API
            # here because PyAV 18.x changed OutputContainer.add_stream().
            ffmpeg = find_app_ffmpeg()
            if not ffmpeg:
                raise RuntimeError("FFmpeg is required for lossless compatible remux")
            print("  Conversion path: FFmpeg lossless remux H.264/H.265 + AAC (+faststart).")
            cmd = [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-progress", "pipe:1", "-nostats", "-y",
                "-i", str(input_path),
                "-map", "0:v:0", "-map", "0:a:0",
                "-c", "copy", "-movflags", "+faststart",
                str(output_path),
            ]
            await run_ffmpeg_with_progress(cmd, duration, "REMUX", on_progress=on_progress)
        elif supported_video and meta.get("audio_codec"):
            print("  Conversion path: INTERNAL PyAV preserve H.264/H.265; encode AAC.")
            await asyncio.to_thread(internal_media.transcode, input_path, output_path, preserve_video=True, on_progress=on_progress)
        elif supported_video and not meta.get("audio_codec"):
            raise RuntimeError("internal PyAV path does not synthesize a silent AAC source")
        else:
            print("  Conversion path: INTERNAL PyAV transcode video to H.264 + AAC.")
            await asyncio.to_thread(internal_media.transcode, input_path, output_path, preserve_video=False, on_progress=on_progress)
    except Exception as internal_error:
        # Compatibility fallback only. Do not uninstall/reinstall PyAV.
        ffmpeg = find_app_ffmpeg()
        if not ffmpeg:
            raise RuntimeError(f"Internal PyAV media engine failed and external FFmpeg is unavailable: {internal_error}")
        print(f"  Internal PyAV unavailable for this operation; using external FFmpeg fallback: {internal_error}")
        if already_streamable:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(input_path), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", "-movflags", "+faststart", str(output_path)]
            await run_ffmpeg_with_progress(cmd, duration, "REMUX", on_progress=on_progress)
        elif supported_video and meta.get("audio_codec"):
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(input_path), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path)]
            await run_ffmpeg_with_progress(cmd, duration, "AUDIO CONVERT", on_progress=on_progress)
        elif supported_video:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(input_path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(output_path)]
            await run_ffmpeg_with_progress(cmd, duration, "ADD AAC", on_progress=on_progress)
        else:
            cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(input_path), "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(output_path)] if meta.get("audio_codec") else [ffmpeg, "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats", "-y", "-i", str(input_path), "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(output_path)]
            await run_ffmpeg_with_progress(cmd, duration, "CONVERT", on_progress=on_progress)

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise RuntimeError("FFmpeg produced no usable output file")

    # Final validation: never upload a file that fails the Telegram-compatible
    # container/codec contract.
    try:
        from ..core import internal_media
        final_meta = await asyncio.to_thread(internal_media.probe, output_path)
    except Exception:
        final_meta = await ffprobe_local(output_path)
    final_v = (final_meta.get("video_codec") or final_meta.get("vcodec") or "").lower()
    final_a = (final_meta.get("audio_codec") or final_meta.get("acodec") or "").lower()
    final_formats = {x.strip().lower() for x in (final_meta.get("format_name") or final_meta.get("format") or "").split(",") if x.strip()}

    if final_v not in {"h264", "avc1", "hevc", "h265"}:
        raise RuntimeError(f"converted video codec is not H.264/H.265: {final_v or 'none'}")
    if final_a not in {"aac", "mp4a"}:
        raise RuntimeError(f"converted audio codec is not AAC: {final_a or 'none'}")
    if "mp4" not in final_formats:
        raise RuntimeError("converted file is not MP4")
    return final_meta




async def media_process_one(client, source, destination, message, state, source_mode, destination_mode, db=None, orientation="VERTICAL", cover_policy="AUTOMATIC", save_state_fn=None):
    """Run one media-manager item under the process-wide stable identity lock."""
    source_channel = canonical_peer_id(source)
    source_message_id = int(getattr(message, "id", 0) or 0)
    identity_key = ""
    if db is not None and source_channel and source_message_id > 0:
        try:
            with db.lock:
                row = db.conn.execute("SELECT identity_key,oshash FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                    (str(source_channel), source_message_id)).fetchone()
            if row:
                identity_key = str(row["oshash"] or row["identity_key"] or "")
        except Exception:
            identity_key = ""
    from ..core.video_locks import video_operation_lock
    async with video_operation_lock(identity_key=identity_key, source_channel=str(source_channel or ""),
                                   source_message_id=source_message_id, file_path=safe_video_name(message)):
        return await _media_process_one_unlocked(
            client, source, destination, message, state, source_mode, destination_mode, db=db,
            orientation=orientation, cover_policy=cover_policy, save_state_fn=save_state_fn,
        )
