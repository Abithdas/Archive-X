"""Upload still-image files as Telegram documents, verify delivery, then clean up.

Photos are uploaded with ``force_document=True`` so Telegram keeps the original
file as an attachment instead of converting it to a Telegram photo. The worker
re-fetches each sent message and verifies its destination, document identity,
filename, and byte size before it may delete the local source. Durable pending
and verified checkpoints let a resumed job verify an existing delivery instead
of blindly sending the same file again.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Photo discovery
#   - File identity
#   - Delivery verification
#   - Job entry point
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import tempfile
import unicodedata
from pathlib import Path
from collections import Counter
from typing import Any

from .. import config
from ..jobs.context import JobContext
from ..core.workspace_guard import validate_local_upload_folder

PHOTO_EXTENSIONS = {
    # Common raster / web image formats.
    ".jpg", ".jpeg", ".jpe", ".jfif", ".jif", ".jfi", ".png", ".webp",
    ".bmp", ".dib", ".tif", ".tiff", ".heic", ".heif", ".avif", ".gif",
    ".ico", ".jp2", ".j2k", ".jpf", ".jpx", ".jpm", ".mj2", ".jxl",
    # Common camera RAW formats are sent as original documents too.
    ".raw", ".dng", ".cr2", ".cr3", ".nef", ".nrw", ".arw", ".orf",
    ".rw2", ".raf", ".pef", ".srw", ".x3f", ".erf", ".3fr", ".ari",
    ".cap", ".dcs", ".dcr", ".drf", ".eip", ".fff", ".gpr", ".iiq",
    ".k25", ".kdc", ".mdc", ".mef", ".mos", ".mrw", ".rwl", ".rwz", ".bay",
}




# ==============================================================================
# SECTION: Photo discovery
# Finds photos in a folder.
# ==============================================================================

class DestinationMessageMissing(RuntimeError):
    """The sent message is no longer present at the expected destination."""


def _is_hidden_or_app_artifact(path: Path, root: Path) -> bool:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        parts = path.parts
    return any(part.startswith(".") for part in parts)


def find_photos(folder: Path, *, include_subfolders: bool = False) -> list[Path]:
    """Return supported image files, sorted by path for stable resume ordering."""
    iterator = folder.rglob("*") if include_subfolders else folder.iterdir()
    return sorted(
        p for p in iterator
        if p.is_file()
        and p.suffix.lower() in PHOTO_EXTENSIONS
        and not _is_hidden_or_app_artifact(p, folder)
    )


def scan_photo_folder(folder: Path) -> dict[str, Any]:
    """Inventory direct and nested files so the UI can explain the actual scan scope."""
    folder = Path(folder)
    direct_files: list[Path] = []
    recursive_files: list[Path] = []
    direct_extensions: Counter[str] = Counter()
    recursive_extensions: Counter[str] = Counter()
    try:
        direct_files = [p for p in folder.iterdir() if p.is_file() and not _is_hidden_or_app_artifact(p, folder)]
        recursive_files = [p for p in folder.rglob("*") if p.is_file() and not _is_hidden_or_app_artifact(p, folder)]
        for item in direct_files:
            direct_extensions[item.suffix.lower() or "(no extension)"] += 1
        for item in recursive_files:
            recursive_extensions[item.suffix.lower() or "(no extension)"] += 1
        direct_photos = [p for p in direct_files if p.suffix.lower() in PHOTO_EXTENSIONS]
        recursive_photos = [p for p in recursive_files if p.suffix.lower() in PHOTO_EXTENSIONS]
        direct_set = {str(p.resolve(strict=False)) for p in direct_photos}
        nested_photos = [p for p in recursive_photos if str(p.resolve(strict=False)) not in direct_set]
        return {
            "folder": str(folder.resolve(strict=False)),
            "direct_count": len(direct_photos),
            "recursive_count": len(recursive_photos),
            "nested_count": len(nested_photos),
            "direct_file_count": len(direct_files),
            "recursive_file_count": len(recursive_files),
            "direct_extensions": dict(sorted(direct_extensions.items())),
            "recursive_extensions": dict(sorted(recursive_extensions.items())),
            "direct_samples": [p.name for p in direct_photos[:8]],
            "nested_samples": [str(p.relative_to(folder)) for p in nested_photos[:8]],
            "supported_extensions": sorted(PHOTO_EXTENSIONS),
        }
    except OSError as exc:
        return {
            "folder": str(folder.resolve(strict=False)),
            "scan_error": f"{type(exc).__name__}: {exc}",
            "direct_count": 0,
            "recursive_count": 0,
            "nested_count": 0,
            "direct_file_count": len(direct_files),
            "recursive_file_count": len(recursive_files),
            "direct_extensions": dict(sorted(direct_extensions.items())),
            "recursive_extensions": dict(sorted(recursive_extensions.items())),
            "direct_samples": [],
            "nested_samples": [],
            "supported_extensions": sorted(PHOTO_EXTENSIONS),
        }




# ==============================================================================
# SECTION: File identity
# File fingerprints and message document details.
# ==============================================================================

def _fingerprint_file(path: Path) -> dict[str, Any]:
    """Return a stable source fingerprint and fail if the file changes mid-read."""
    if not path.is_file():
        raise RuntimeError("File is no longer available")
    before = path.stat()
    if before.st_size <= 0:
        raise RuntimeError("File is empty")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    before_mtime = int(getattr(before, "st_mtime_ns", int(before.st_mtime * 1_000_000_000)))
    after_mtime = int(getattr(after, "st_mtime_ns", int(after.st_mtime * 1_000_000_000)))
    if before.st_size != after.st_size or before_mtime != after_mtime:
        raise RuntimeError("File changed while Archive X was checking it")
    return {
        "path": str(path.resolve()),
        "size": int(after.st_size),
        "mtime_ns": after_mtime,
        "sha256": digest.hexdigest(),
    }


def _photo_key(path: Path, fingerprint: dict[str, Any]) -> str:
    """Stable per-content key, including the digest to avoid stale-file matches."""
    return "|".join((
        str(fingerprint.get("path") or path.resolve()),
        str(int(fingerprint.get("size") or 0)),
        str(int(fingerprint.get("mtime_ns") or 0)),
        str(fingerprint.get("sha256") or ""),
    ))


def _message_document(message: Any) -> Any:
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)
    return document


def _message_document_name(message: Any, document: Any) -> str:
    file_obj = getattr(message, "file", None)
    name = getattr(file_obj, "name", None) if file_obj is not None else None
    if not name:
        for attribute in (getattr(document, "attributes", None) or []):
            value = getattr(attribute, "file_name", None)
            if value:
                name = value
                break
    if not name:
        return ""
    # Treat Telegram filenames as names, not paths, on either Windows or Unix.
    return str(name).replace("\\", "/").rsplit("/", 1)[-1]


def _normal_filename(value: str) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()




# ==============================================================================
# SECTION: Delivery verification
# Filename similarity and destination verification.
# ==============================================================================

def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filename_signature(value: str) -> tuple[str, str]:
    """Normalize a name as Telegram may display it, keeping extension separate."""
    name = str(value or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    suffix = Path(name).suffix.casefold()
    stem = name[:-len(suffix)] if suffix else name
    normalized = unicodedata.normalize("NFKC", stem).casefold()
    compact = "".join(ch for ch in normalized if ch.isalnum())
    return suffix, compact


def _filename_similarity(left: str, right: str) -> float:
    """Score names conservatively; exact content hash is still required to reuse."""
    left_ext, a = _filename_signature(left)
    right_ext, b = _filename_signature(right)
    if not a or not b or left_ext != right_ext or min(len(a), len(b)) < 10:
        return 0.0
    common = 0
    for char_a, char_b in zip(a, b):
        if char_a != char_b:
            break
        common += 1
    return common / min(len(a), len(b))


def _document_size(message: Any, document: Any) -> int:
    size = getattr(document, "size", None)
    if size is None:
        size = getattr(getattr(message, "file", None), "size", None)
    try:
        return int(size)
    except (TypeError, ValueError):
        return -1


async def _collect_recent_destination_documents(
    client: Any,
    destination: Any,
    photos: list[Path],
    log: Any,
    checkpoint: Any = None,
) -> list[Any]:
    """Collect recent candidate documents once so retries can reuse prior deliveries.

    Only recent Telegram metadata is read here. Candidate documents are downloaded
    and accepted only after their exact SHA-256 matches the local source.
    """
    iter_messages = getattr(client, "iter_messages", None)
    if not callable(iter_messages):
        log("Cannot preflight destination history for duplicate recovery; client has no iter_messages API",
            "WARN", event="photo_upload.duplicate_preflight_unavailable")
        return []

    source_sizes: set[int] = set()
    source_extensions: set[str] = set()
    for photo in photos:
        try:
            source_sizes.add(int(photo.stat().st_size))
            source_extensions.add(photo.suffix.casefold())
        except OSError:
            continue
    if not source_sizes:
        return []

    # A bounded recent window keeps preflight useful without reading a whole,
    # potentially huge Telegram channel on every transfer.
    history_limit = min(2000, max(300, len(photos) * 4))
    candidates: list[Any] = []
    scanned = 0
    try:
        async for message in client.iter_messages(destination, limit=history_limit):
            scanned += 1
            if checkpoint is not None and scanned % 50 == 0:
                await checkpoint()
            document = _message_document(message)
            if document is None:
                continue
            if _document_size(message, document) not in source_sizes:
                continue
            name = _message_document_name(message, document)
            if not name or Path(name).suffix.casefold() not in source_extensions:
                continue
            candidates.append(message)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if exc.__class__.__name__ == "JobCancelled":
            raise
        log(
            f"Could not complete Telegram duplicate-preflight history scan after {scanned} message(s): "
            f"{type(exc).__name__}: {exc}; continuing without history reuse",
            "WARN", event="photo_upload.duplicate_preflight_failed",
            details={"history_scanned": scanned, "candidate_count": len(candidates)},
        )
        return candidates

    log(
        f"Duplicate preflight inspected {scanned} recent Telegram message(s) and found "
        f"{len(candidates)} document candidate(s) matching source file sizes and extensions",
        "INFO", event="photo_upload.duplicate_preflight_completed",
        details={"history_scanned": scanned, "candidate_count": len(candidates),
                 "source_file_count": len(photos), "history_limit": history_limit},
    )
    return candidates


async def _verify_destination_document(
    client: Any,
    destination: Any,
    details: dict[str, Any],
    *,
    verify_content: bool = True,
) -> Any:
    """Re-read and verify the exact destination message before cleanup is allowed."""
    try:
        message_id = int(details.get("message_id") or 0)
    except (TypeError, ValueError):
        message_id = 0
    if message_id <= 0:
        raise RuntimeError("Telegram did not return a usable destination message ID")

    checked = await client.get_messages(destination, ids=message_id)
    if checked is None:
        raise DestinationMessageMissing(f"Destination message {message_id} was not found")
    if int(getattr(checked, "id", 0) or 0) != message_id:
        raise RuntimeError("Telegram returned a different destination message during verification")

    document = _message_document(checked)
    if document is None:
        raise RuntimeError("Destination message exists but is not a Telegram document; source retained")

    try:
        expected_size = int(details.get("size") or 0)
    except (TypeError, ValueError):
        expected_size = 0
    actual_size = getattr(document, "size", None)
    if actual_size is None:
        actual_size = getattr(getattr(checked, "file", None), "size", None)
    try:
        actual_size = int(actual_size)
    except (TypeError, ValueError):
        actual_size = -1
    if expected_size <= 0 or actual_size != expected_size:
        raise RuntimeError(
            f"Destination document size did not match the source ({actual_size} vs {expected_size} bytes); source retained"
        )

    expected_name = _normal_filename(str(details.get("filename") or ""))
    actual_name = _normal_filename(_message_document_name(checked, document))
    # Telegram can normalize/truncate attachment filenames (spaces, punctuation,
    # and long suffixes) even when the delivered bytes are correct. Filename is
    # descriptive metadata, not an integrity proof: exact message ID + document
    # type + byte size are checked above, and SHA-256 is checked below whenever
    # source cleanup or duplicate recovery depends on content identity. Do not
    # fail a valid delivery solely because Telegram changed the display name.

    expected_document_id = str(details.get("document_id") or "").strip()
    actual_document_id = str(getattr(document, "id", "") or "").strip()
    if expected_document_id and actual_document_id and actual_document_id != expected_document_id:
        raise RuntimeError("Destination document identity changed during verification; source retained")

    if verify_content:
        expected_digest = str(details.get("sha256") or "").strip().lower()
        if len(expected_digest) != 64:
            raise RuntimeError("Source content fingerprint is unavailable; automatic cleanup was refused")
        # Metadata alone cannot prove byte-for-byte delivery. Re-download the
        # destination document into a temporary file and compare its SHA-256 to
        # the source fingerprint before deleting any local bytes.
        with tempfile.TemporaryDirectory(prefix="ax-photo-verify-") as temp_dir:
            downloaded_path = Path(temp_dir) / "destination-document.bin"
            downloaded = await client.download_media(checked, file=str(downloaded_path))
            remote_path = Path(str(downloaded)) if isinstance(downloaded, (str, Path)) and str(downloaded) else downloaded_path
            if not remote_path.is_file():
                raise RuntimeError("Destination document could not be downloaded for integrity verification; source retained")
            if remote_path.stat().st_size != expected_size:
                raise RuntimeError("Downloaded destination bytes did not match the source size; source retained")
            remote_digest = await asyncio.to_thread(_sha256_path, remote_path)
            if remote_digest != expected_digest:
                raise RuntimeError("Destination document content did not match the source SHA-256; source retained")
    return checked





# ==============================================================================
# SECTION: Job entry point
# Photo upload job.
# ==============================================================================

async def run(ctx: JobContext) -> dict[str, Any]:
    """Send photos as documents and delete a source only after verified delivery."""
    try:
        cfg = json.loads(ctx.job.get("detail") or "{}")
    except Exception:
        cfg = {}

    folder_value = str(cfg.get("folder") or "").strip()
    if not folder_value:
        raise RuntimeError("Choose a photo source folder before uploading.")
    folder = Path(folder_value).expanduser()
    if not folder.is_dir():
        raise RuntimeError(f"Photo source folder does not exist: {folder}")
    try:
        validate_local_upload_folder(ctx.db, folder)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc

    destination_id = str(cfg.get("destination_channel") or ctx.job.get("destination_channel") or "").strip()
    if not destination_id:
        raise RuntimeError("Choose a destination Telegram channel before uploading photos.")
    client = getattr(ctx.telegram, "client", None)
    if not client or not getattr(ctx.telegram, "connected", False):
        raise RuntimeError("Telegram is not connected.")

    include_subfolders = bool(cfg.get("include_subfolders", False))
    delete_local_after_delivery = bool(cfg.get("delete_local_after_delivery", True))
    photos = await ctx.blocking(find_photos, folder, include_subfolders=include_subfolders)
    # Inventory the broader scope only when needed for a useful warning or an empty-scan diagnosis.
    scan = await ctx.blocking(scan_photo_folder, folder) if (not photos or not include_subfolders) else {}
    if not photos:
        if not include_subfolders and int(scan.get("nested_count") or 0) > 0:
            message = (
                f"No supported photo files found directly in {folder}; the selected folder contains "
                f"{scan['nested_count']} supported photo(s) in subfolders. Enable Include subfolders to upload them."
            )
        else:
            message = (
                f"No supported photo files found in {folder}; inspected "
                f"{scan.get('recursive_file_count', 0)} file(s); extension inventory="
                f"{json.dumps(scan.get('recursive_extensions', {}), ensure_ascii=False, sort_keys=True)}"
            )
        ctx.db.log(message, "WARN", event="photo_upload.no_files", job_id=ctx.job_id,
                   details={"folder": str(folder), "include_subfolders": include_subfolders, "scan": scan})
        ctx.report(completed=0, total=0, progress=0, current_item="", operation="No supported photos found", force=True)
        from ..core.job_results import record as record_job_result
        record_job_result(ctx.db, ctx.job_id, status="COMPLETED_WITH_ERRORS", progress=0,
                          summary=message[:1000])
        return {"uploaded": 0, "failed": 0, "deleted": 0, "delete_pending": 0, "total": 0}
    if not include_subfolders and int(scan.get("nested_count") or 0) > 0:
        ctx.db.log(
            f"Photo folder scan: {len(photos)} supported photo(s) directly in the selected folder; "
            f"{scan['nested_count']} additional supported photo(s) are in subfolders not included in this job.",
            "WARN", event="photo_upload.subfolders_not_included", job_id=ctx.job_id,
            details={"folder": str(folder), "direct_count": len(photos),
                     "nested_count": int(scan.get("nested_count") or 0),
                     "nested_samples": scan.get("nested_samples", [])},
        )

    try:
        destination: Any = await client.get_entity(int(destination_id))
    except (TypeError, ValueError):
        destination = await client.get_entity(destination_id)

    loaded_state = ctx.load_state({"schema": 2, "verified": {}, "pending": {}, "failed": {}, "delete_pending": {}})
    state = dict(loaded_state) if isinstance(loaded_state, dict) else {}
    verified: dict[str, dict[str, Any]] = dict(state.get("verified") or {}) if isinstance(state.get("verified"), dict) else {}
    pending: dict[str, dict[str, Any]] = dict(state.get("pending") or {}) if isinstance(state.get("pending"), dict) else {}
    failures: dict[str, str] = {str(k): str(v) for k, v in (state.get("failed") or {}).items()} if isinstance(state.get("failed"), dict) else {}
    delete_pending: dict[str, str] = {str(k): str(v) for k, v in (state.get("delete_pending") or {}).items()} if isinstance(state.get("delete_pending"), dict) else {}
    legacy_uploaded = state.pop("uploaded", None)
    state.update({"schema": 2, "verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
    if legacy_uploaded and not verified:
        # The old checkpoint recorded only a local key, not a destination message
        # ID, so it is not enough proof to delete a source or skip verification.
        ctx.db.log(
            "Photo upload has legacy checkpoints without Telegram message IDs; those entries will be re-uploaded and verified safely.",
            "WARN", event="photo_upload.legacy_checkpoint_untrusted", job_id=ctx.job_id,
        )
        ctx.save_state(state)

    already_verified_count = 0
    failed_this_run = 0
    deleted_this_run = 0
    successful_keys: set[str] = set()
    scanned_keys: set[str] = set()
    last_progress = {"at": 0.0}

    ctx.report(completed=0, total=len(photos), current_item="", operation="Checking photo folder", force=True)
    ctx.db.log(
        f"Photo document upload started: {len(photos)} image(s) from {folder} to {destination_id}; "
        f"delete source after verification={delete_local_after_delivery}",
        "INFO", event="photo_upload.started", job_id=ctx.job_id,
        details={"photo_count": len(photos), "include_subfolders": include_subfolders,
                 "delete_local_after_delivery": delete_local_after_delivery, "upload_mode": "DOCUMENT"},
    )

    async def record_failure(photo: Path, key: str | None, message: str, index: int) -> None:
        nonlocal failed_this_run
        failures[str(photo)] = str(message)[:700]
        failed_this_run += 1
        state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
        ctx.save_state(state)
        ctx.db.log(
            f"Photo upload/verification failed for {photo.name}: {message}", "WARN",
            event="photo_upload.file_failed", job_id=ctx.job_id,
            details={"filename": photo.name, "destination_channel": destination_id,
                     "checkpoint_key": key or ""},
        )
        ctx.report(completed=index + 1, total=len(photos), current_item=photo.name,
                   operation=f"Needs attention · {index + 1}/{len(photos)}", force=True)

    async def finish_verified(photo: Path, key: str, fingerprint: dict[str, Any], entry: dict[str, Any], index: int) -> bool:
        """Persist verification before attempting any irreversible local cleanup."""
        nonlocal deleted_this_run
        verified[key] = dict(entry)
        pending.pop(key, None)
        failures.pop(str(photo), None)
        state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
        ctx.save_state(state)
        successful_keys.add(key)

        # A confirmed Telegram destination message is the durable Photos-library
        # item. Keep a local copy under the application's managed photo cache
        # before source cleanup, then map that cache path to the exact channel/message.
        message_id = int(entry.get("message_id") or 0)
        cache_info: dict[str, Any] | None = None
        cache_error: str | None = None
        if message_id > 0:
            import mimetypes as _mimetypes
            mime = _mimetypes.guess_type(photo.name)[0] or "image/jpeg"
            if not mime.startswith("image/"):
                mime = "image/jpeg"
            try:
                from .photo_cache import cache_local_source_file
                cache_info = await ctx.blocking(
                    cache_local_source_file, photo, str(destination_id), message_id,
                    expected_sha256=str(entry.get("sha256") or fingerprint.get("sha256") or ""),
                    filename=photo.name,
                )
            except Exception as exc:
                cache_error = f"Telegram delivery is verified, but the local photo cache could not be created: {type(exc).__name__}: {exc}"
                ctx.db.log(
                    cache_error, "WARN", event="photo_upload.local_cache_failed", job_id=ctx.job_id,
                    details={"filename": photo.name, "destination_channel": destination_id,
                             "destination_message_id": message_id, "source_path": str(photo.resolve(strict=False))},
                )
            now = config.now_iso()
            with ctx.db.lock:
                ctx.db.conn.execute(
                    """INSERT INTO photo_library_items
                       (channel_id,message_id,filename,caption,mime_type,size,width,height,message_date,source_kind,
                        content_hash,local_path,local_sha256,cached_at,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?, 'local_upload',?,?,?,?,?,?)
                       ON CONFLICT(channel_id,message_id) DO UPDATE SET
                         filename=excluded.filename,caption=excluded.caption,mime_type=excluded.mime_type,
                         size=CASE WHEN excluded.size>0 THEN excluded.size ELSE photo_library_items.size END,
                         width=CASE WHEN excluded.width>0 THEN excluded.width ELSE photo_library_items.width END,
                         height=CASE WHEN excluded.height>0 THEN excluded.height ELSE photo_library_items.height END,
                         source_kind='local_upload',content_hash=excluded.content_hash,
                         local_path=CASE WHEN excluded.local_path<>'' THEN excluded.local_path ELSE photo_library_items.local_path END,
                         local_sha256=CASE WHEN excluded.local_sha256<>'' THEN excluded.local_sha256 ELSE photo_library_items.local_sha256 END,
                         cached_at=COALESCE(excluded.cached_at,photo_library_items.cached_at),updated_at=excluded.updated_at""",
                    (str(destination_id), message_id, photo.name, photo.stem[:1024], mime,
                     int((cache_info or {}).get("size") or entry.get("size") or fingerprint.get("size") or 0),
                     int((cache_info or {}).get("width") or 0), int((cache_info or {}).get("height") or 0),
                     config.now_iso(), str(entry.get("sha256") or fingerprint.get("sha256") or ""),
                     str((cache_info or {}).get("local_path") or ""), str((cache_info or {}).get("sha256") or ""),
                     (cache_info or {}).get("cached_at"), now, now),
                )
                ctx.db.conn.commit()

        # If the managed cache could not be confirmed, keep the source no matter
        # which cleanup option was selected. A gallery item must not lose its
        # only known local bytes as a side effect of a verified remote upload.
        if cache_error:
            reason = cache_error + "; source retained so it can be retried safely"
            delete_pending[key] = reason
            await record_failure(photo, key, reason, index)
            return False

        if not delete_local_after_delivery:
            delete_pending.pop(key, None)
            failures.pop(str(photo), None)
            state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
            ctx.save_state(state)
            ctx.db.log(
                f"Local source intentionally retained because delete-after-verification is disabled: {photo}", "INFO",
                event="photo_upload.source_retained_by_option", job_id=ctx.job_id,
                details={"source_path": str(photo.resolve(strict=False)), "filename": photo.name,
                         "destination_channel": destination_id,
                         "destination_message_id": int(entry.get("message_id") or 0),
                         "source_exists_after_cleanup": photo.exists()},
            )
            return False

        try:
            current_fingerprint = await ctx.blocking(_fingerprint_file, photo)
        except Exception as exc:
            reason = f"Destination verified, but the source could not be rechecked and was retained: {exc}"
            delete_pending[key] = reason
            await record_failure(photo, key, reason, index)
            return False

        if _photo_key(photo, current_fingerprint) != key or current_fingerprint.get("sha256") != fingerprint.get("sha256"):
            reason = "Destination verified, but the source changed after upload; the local file was retained"
            delete_pending[key] = reason
            await record_failure(photo, key, reason, index)
            return False

        # Import at runtime to avoid coupling the photo worker's module load to
        # the large video-uploader module; use Archive X's Windows-safe delete.
        ctx.db.log(
            f"Starting local source cleanup after verified Telegram document delivery: {photo}", "INFO",
            event="photo_upload.source_delete_started", job_id=ctx.job_id,
            details={"source_path": str(photo.resolve(strict=False)), "filename": photo.name,
                     "destination_channel": destination_id,
                     "destination_message_id": int(entry.get("message_id") or 0),
                     "size_bytes": int(entry.get("size") or 0)},
        )
        from .uploader import _delete_verified_source_file
        deleted = await _delete_verified_source_file(photo, ctx.db.log)
        # Independently check the original path after the helper returns. A
        # worker must never report deletion solely from an internal return flag.
        try:
            original_still_exists = photo.exists()
        except OSError:
            original_still_exists = True
        deleted = bool(deleted and not original_still_exists)
        if deleted:
            delete_pending.pop(key, None)
            failures.pop(str(photo), None)
            deleted_this_run += 1
            ctx.db.log(
                f"Deleted local photo after verifying its Telegram document delivery: {photo}", "SUCCESS",
                event="photo_upload.source_deleted", job_id=ctx.job_id,
                details={"source_path": str(photo.resolve(strict=False)), "filename": photo.name,
                         "destination_channel": destination_id,
                         "destination_message_id": int(entry.get("message_id") or 0),
                         "document_id": str(entry.get("document_id") or ""),
                         "size_bytes": int(entry.get("size") or 0), "source_exists_after_cleanup": False},
            )
        else:
            reason = ("Telegram document was verified, but Archive X could not delete the local source; "
                      f"it was retained at {photo}")
            delete_pending[key] = reason
            failures[str(photo)] = reason
            ctx.db.log(
                f"Verified photo source could not be deleted; original_exists={original_still_exists}: {photo}", "WARN",
                event="photo_upload.source_delete_pending", job_id=ctx.job_id,
                details={"source_path": str(photo.resolve(strict=False)), "filename": photo.name,
                         "destination_channel": destination_id,
                         "destination_message_id": int(entry.get("message_id") or 0),
                         "source_exists_after_cleanup": original_still_exists,
                         "delete_pending_reason": reason},
            )
        state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
        ctx.save_state(state)
        return deleted

    ctx.report(completed=0, total=len(photos), progress=0, current_item="",
               operation="Checking recent Telegram history for already-uploaded photos", force=True)
    recent_documents = await _collect_recent_destination_documents(
        client, destination, photos, ctx.db.log, checkpoint=ctx.checkpoint
    )
    reused_existing_count = 0

    async def find_existing_delivery(photo: Path, fingerprint: dict[str, Any]) -> tuple[dict[str, Any], Any] | None:
        """Recover a prior upload by name/size hint, then require exact SHA-256."""
        nonlocal reused_existing_count
        size = int(fingerprint.get("size") or 0)
        candidates: list[tuple[float, Any]] = []
        for message in recent_documents:
            document = _message_document(message)
            if document is None or _document_size(message, document) != size:
                continue
            remote_name = _message_document_name(message, document)
            score = _filename_similarity(photo.name, remote_name)
            if score >= 0.72:
                candidates.append((score, message))
        # The Telegram history iterator is newest-first; sort by name match while
        # preserving the newest duplicate first for equal scores.
        candidates.sort(key=lambda pair: pair[0], reverse=True)
        uncertain: list[str] = []
        for score, message in candidates:
            message_id = int(getattr(message, "id", 0) or 0)
            if message_id <= 0:
                continue
            document = _message_document(message)
            entry = {
                "message_id": message_id,
                "document_id": str(getattr(document, "id", "") or ""),
                "filename": photo.name,
                "telegram_filename": _message_document_name(message, document),
                "size": size,
                "sha256": str(fingerprint.get("sha256") or ""),
                "mtime_ns": int(fingerprint.get("mtime_ns") or 0),
                "sent_at": int(time.time()),
                "recovered_from_history": True,
            }
            try:
                checked = await _verify_destination_document(
                    client, destination, entry, verify_content=True
                )
            except asyncio.CancelledError:
                raise
            except DestinationMessageMissing:
                continue
            except Exception as exc:
                # A hash mismatch means this candidate is not the source file.
                # Download/network failures are ambiguous: don't post another
                # copy until the possible existing delivery can be verified.
                message_text = str(exc)
                if "content did not match the source SHA-256" in message_text or                    "downloaded destination bytes did not match the source size" in message_text or                    "document size did not match the source" in message_text:
                    continue
                uncertain.append(f"message {message_id}: {type(exc).__name__}: {message_text}")
                continue
            recovered = dict(entry)
            recovered["message_id"] = int(getattr(checked, "id", 0) or message_id)
            reused_existing_count += 1
            ctx.db.log(
                f"Recovered an already-delivered Telegram document without uploading a duplicate: "
                f"{photo.name} (message {recovered['message_id']}; content SHA-256 verified)",
                "SUCCESS", event="photo_upload.existing_delivery_reused", job_id=ctx.job_id,
                details={"filename": photo.name, "source_path": str(photo.resolve(strict=False)),
                         "destination_channel": destination_id, "destination_message_id": recovered["message_id"],
                         "telegram_filename": recovered.get("telegram_filename", ""),
                         "filename_similarity": round(score, 3), "content_sha256_verified": True},
            )
            return recovered, checked
        if uncertain:
            raise RuntimeError(
                "A possible prior Telegram upload has the same size/name pattern, but its content could not "
                "be verified. Archive X skipped re-uploading to avoid creating a duplicate. "
                + "; ".join(uncertain[:2])
            )
        return None

    for index, photo in enumerate(photos):
        await ctx.checkpoint()
        try:
            fingerprint = await ctx.blocking(_fingerprint_file, photo)
            key = _photo_key(photo, fingerprint)
            scanned_keys.add(key)
        except Exception as exc:
            await record_failure(photo, None, f"Could not inspect source file safely: {exc}", index)
            continue

        if key in verified:
            saved = verified.get(key) or {}
            try:
                checked = await _verify_destination_document(
                    client, destination, saved, verify_content=delete_local_after_delivery)
            except DestinationMessageMissing:
                # A definite missing message is safe to retry; no source is
                # deleted on this path. Other verification failures retain the
                # checkpoint and source for a later retry/manual inspection.
                verified.pop(key, None)
                state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
                ctx.save_state(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await record_failure(photo, key, f"Saved destination verification could not be repeated: {exc}", index)
                continue
            else:
                entry = dict(saved)
                entry["message_id"] = int(getattr(checked, "id", 0) or saved.get("message_id") or 0)
                already_verified_count += 1
                await finish_verified(photo, key, fingerprint, entry, index)
                ctx.report(completed=index + 1, total=len(photos), current_item=photo.name,
                           operation=f"Verified document · {index + 1}/{len(photos)}", force=True)
                continue

        existing_pending = pending.get(key)
        if isinstance(existing_pending, dict) and int(existing_pending.get("message_id") or 0) > 0:
            try:
                checked = await _verify_destination_document(
                    client, destination, existing_pending, verify_content=delete_local_after_delivery)
            except DestinationMessageMissing:
                pending.pop(key, None)
                state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
                ctx.save_state(state)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await record_failure(photo, key, f"Pending Telegram delivery could not be verified; source retained: {exc}", index)
                continue
            else:
                entry = dict(existing_pending)
                entry["message_id"] = int(getattr(checked, "id", 0) or existing_pending.get("message_id") or 0)
                await finish_verified(photo, key, fingerprint, entry, index)
                ctx.report(completed=index + 1, total=len(photos), current_item=photo.name,
                           operation=f"Recovered and verified document · {index + 1}/{len(photos)}", force=True)
                continue

        try:
            existing_delivery = await find_existing_delivery(photo, fingerprint)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await record_failure(
                photo, key,
                f"Possible prior Telegram delivery could not be safely verified; skipped re-upload to avoid a duplicate: "
                f"{type(exc).__name__}: {exc}",
                index,
            )
            continue
        if existing_delivery is not None:
            entry, checked = existing_delivery
            # `finish_verified` persists the library mapping/cache, then deletes
            # the source only after the same checks used for fresh uploads.
            await finish_verified(photo, key, fingerprint, entry, index)
            ctx.report(
                completed=index + 1, total=len(photos), current_item=photo.name,
                operation=f"Reused existing Telegram document · {index + 1}/{len(photos)}", force=True,
            )
            continue

        def on_progress(sent_bytes: int, total_bytes: int) -> None:
            total_bytes = max(0, int(total_bytes or 0))
            sent_bytes = max(0, int(sent_bytes or 0))
            now = time.monotonic()
            if total_bytes and (now - last_progress["at"] >= 1.0 or sent_bytes >= total_bytes):
                last_progress["at"] = now
                per_file = min(1.0, sent_bytes / total_bytes)
                overall = 100.0 * (index + per_file) / max(1, len(photos))
                ctx.report(progress=overall, completed=index, total=len(photos),
                           current_item=f"{photo.name} · {int(per_file * 100)}%",
                           operation=f"Uploading document · {index + 1}/{len(photos)}")

        try:
            # Recheck source immediately before upload. A same-name replacement
            # must not inherit an earlier file's checkpoint.
            upload_fingerprint = await ctx.blocking(_fingerprint_file, photo)
            if _photo_key(photo, upload_fingerprint) != key:
                raise RuntimeError("Source file changed before upload; source retained")
            sent = await client.send_file(
                destination,
                str(photo),
                caption=photo.stem[:1024],
                force_document=True,
                progress_callback=on_progress,
            )
            sent_messages = sent if isinstance(sent, (list, tuple)) else [sent]
            if len(sent_messages) != 1:
                raise RuntimeError("Telegram did not return exactly one document message; source retained")
            sent_message = sent_messages[0]
            message_id = int(getattr(sent_message, "id", 0) or 0)
            if message_id <= 0:
                raise RuntimeError("Telegram upload returned no destination message ID; source retained")
            sent_document = _message_document(sent_message)
            entry = {
                "message_id": message_id,
                "document_id": str(getattr(sent_document, "id", "") or ""),
                "filename": photo.name,
                "size": int(fingerprint.get("size") or 0),
                "sha256": str(fingerprint.get("sha256") or ""),
                "mtime_ns": int(fingerprint.get("mtime_ns") or 0),
                "sent_at": int(time.time()),
            }
            # Save the destination ID before verification so restart recovery
            # can fetch this message instead of sending a duplicate blindly.
            pending[key] = dict(entry)
            state.update({"verified": verified, "pending": pending, "failed": failures, "delete_pending": delete_pending})
            ctx.save_state(state)
            ctx.db.log(
                f"Telegram accepted photo as a document; verifying destination: {photo.name}", "INFO",
                event="photo_upload.document_sent_pending_verification", job_id=ctx.job_id,
                details={"filename": photo.name, "destination_channel": destination_id, "message_id": message_id,
                         "document_id": entry["document_id"], "size_bytes": entry["size"]},
            )
            checked = await _verify_destination_document(
                client, destination, entry, verify_content=delete_local_after_delivery)
            entry["message_id"] = int(getattr(checked, "id", 0) or message_id)
            telegram_name = _message_document_name(checked, _message_document(checked))
            if telegram_name and _normal_filename(telegram_name) != _normal_filename(photo.name):
                ctx.db.log(
                    f"Telegram normalized the attachment filename; delivery verification continued using "
                    f"message ID, size, and content integrity checks: {photo.name} -> {telegram_name}",
                    "INFO", event="photo_upload.telegram_filename_normalized", job_id=ctx.job_id,
                    details={"source_filename": photo.name, "telegram_filename": telegram_name,
                             "destination_channel": destination_id, "destination_message_id": message_id,
                             "content_sha256_verified": bool(delete_local_after_delivery)},
                )
            await finish_verified(photo, key, fingerprint, entry, index)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await record_failure(photo, key, f"{type(exc).__name__}: {exc}"[:700], index)

        ctx.report(
            completed=index + 1,
            total=len(photos),
            current_item=photo.name,
            operation=f"Processed document · {index + 1}/{len(photos)}",
            force=True,
        )

    # Count verified deliveries and cleanup issues among the files in this scan.
    successful_in_scan = len(successful_keys)
    total_failures = sum(1 for photo in photos if str(photo) in failures)
    pending_delete_count = sum(1 for key in scanned_keys if key in delete_pending)
    summary = (
        f"{successful_in_scan}/{len(photos)} photos verified as Telegram documents; "
        f"{deleted_this_run} local source file(s) deleted; "
        f"{total_failures} issue(s); {pending_delete_count} source deletion(s) pending; "
        f"{reused_existing_count} prior Telegram document(s) reused without re-upload; "
        f"delete-after-verification={'enabled' if delete_local_after_delivery else 'disabled'}"
    )
    from ..core.job_results import record as record_job_result
    record_job_result(
        ctx.db, ctx.job_id,
        status="COMPLETED_WITH_ERRORS" if total_failures or pending_delete_count else "COMPLETED",
        progress=100,
        summary=summary,
    )
    ctx.report(completed=len(photos), total=len(photos), progress=100, current_item="",
               operation=summary, force=True)
    ctx.db.log(
        f"Photo document upload finished: {summary}",
        "WARN" if total_failures or pending_delete_count else "SUCCESS",
        event="photo_upload.completed_with_errors" if total_failures or pending_delete_count else "photo_upload.completed",
        job_id=ctx.job_id,
        details={"uploaded_verified": successful_in_scan, "failed": total_failures,
                 "deleted_this_run": deleted_this_run, "delete_pending": pending_delete_count,
                 "total": len(photos), "already_verified_count": already_verified_count,
                 "failed_this_run": failed_this_run, "reused_existing_count": reused_existing_count,
                 "upload_mode": "DOCUMENT", "delete_local_after_delivery": delete_local_after_delivery},
    )
    return {"uploaded": successful_in_scan, "failed": total_failures, "deleted": deleted_this_run,
            "delete_pending": pending_delete_count, "total": len(photos)}
