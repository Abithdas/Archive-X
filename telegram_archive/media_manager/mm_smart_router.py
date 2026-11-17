"""Telegram Media Manager: Telegram smart video router / migrator.

Split out of ``telegram_media_manager.py`` in v29.4.387 without behaviour
changes. The main module executes the parts, in order, inside its own module
namespace, so every function, constant and ``globals()`` lookup is exactly as
when this was one file. Do not import this file directly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Message and thumbnail helpers
#   - Scan and classification
#   - State and verification
#   - Thumbnail selection
#   - Delivery preparation
#   - Processing and entry point
# ------------------------------------------------------------------
# ============================================================
# TELEGRAM SMART VIDEO ROUTER / MIGRATOR
# ============================================================
# Purpose:
#   Scan one source channel and route each video according to its
#   Telegram media type, thumbnail presence, and effective orientation.
#
# ROUTING RULES
#   1. VERTICAL video (native streamable OR successfully converted):
#        -> VERTICAL destination
#        -> use the persisted Archive X playback snapshot
#
#   2. NORMAL/HORIZONTAL native streamable video WITH thumbnail:
#        -> MAIN STREAMABLE destination
#        -> copy video + Archive X playback snapshot
#
#   3. NORMAL/HORIZONTAL native streamable video WITHOUT thumbnail:
#        -> MISSING THUMBNAIL destination
#        -> ensure playback snapshot first, then copy video + snapshot
#
#   4. Telegram FILE/DOCUMENT video, or native video that is not streamable:
#        -> download temporarily to PC
#        -> attempt MP4 streamable conversion/remux/transcode
#        -> success: route as normal or vertical streamable video
#        -> failure: route original file/video + Archive X playback snapshot to
#                   NON-STREAMABLE FILE destination
#
# SAFETY GATES
#   - Scan is oldest -> newest.
#   - No source message is deleted before destination verification.
#   - Destination must contain VIDEO followed immediately by THUMBNAIL.
#   - Thumbnail is always uploaded as a Telegram DOCUMENT with the exact
#     canonical name: <video filename>.jpg
#   - Telegram source thumbnails are not used; the Archive X playback snapshot is canonical.
#   - Streamable Telegram videos are copied/forwarded without downloading.
#   - Corrupt/stuck thumbnail generation skips that item and leaves source intact.
#   - Conversion errors fall back to the non-streamable-file destination.
#   - Progress/state are persisted so interrupted runs can resume.
# ============================================================

ROUTER_STATE_FILE = config.MEDIA_MANAGER_STATE_DIR / "telegram_smart_video_router_state.json"
THUMB_SCAN_AHEAD = 10
CONVERSION_TIMEOUT_SECONDS = 60 * 60 * 4

# Native Telegram media is treated as streamable only when Telegram explicitly
# says supports_streaming=True. If a Telethon build does not expose that flag,
# native MP4/M4V/MOV is treated as streamable rather than forcing a full download.
STREAMABLE_FALLBACK_EXTENSIONS = {".mp4", ".m4v", ".mov"}

DEST_MAIN = "MAIN_STREAMABLE"
DEST_MISSING = "MISSING_THUMBNAIL"
DEST_VERTICAL = "VERTICAL"
DEST_FILE = "NON_STREAMABLE_FILE"
DEST_ROLE_NAMES = {
    DEST_MAIN: "MAIN STREAMABLE",
    DEST_MISSING: "MISSING THUMBNAIL",
    DEST_VERTICAL: "VERTICAL",
    DEST_FILE: "NON-STREAMABLE FILE",
}




# ==============================================================================
# SECTION: Message and thumbnail helpers
# State keys, message details and thumbnail matching.
# ==============================================================================

def channel_display_name(entity):
    return (
        getattr(entity, "title", None)
        or getattr(entity, "username", None)
        or str(getattr(entity, "id", "?"))
    )


def message_size(message):
    try:
        return int(getattr(message.file, "size", 0) or 0)
    except Exception:
        return 0


def message_mime_local(message):
    try:
        return str(getattr(message.file, "mime_type", "") or "").lower()
    except Exception:
        return ""


def thumbnail_name_for_video(video_name):
    """Preferred generated-thumbnail filename: video basename without extension + .jpg."""
    name = str(video_name or "").strip()
    if not name:
        return "thumbnail.jpg"
    stem = Path(name).stem or name
    return f"{stem}.jpg"


def normalized_thumbnail_filename(name):
    """Normalize punctuation the way Telegram may sanitize document filenames."""
    value = str(name or "").strip().lower()
    if not value:
        return ""
    # Compare the filename body separately from its final .jpg extension.
    if value.endswith(".jpg"):
        value = value[:-4]
    value = re.sub(r"[^a-z0-9]+", "_", value)
    return value.strip("_")


def thumbnail_filename_matches(video_name, actual_name):
    """Accept all thumbnail conventions used by the channel and Telegram sanitization."""
    actual = normalized_thumbnail_filename(actual_name)
    if not actual:
        return False

    expected = set(source_thumbnail_filename_candidates(video_name))
    expected.add(thumbnail_name_for_video(video_name).lower())
    normalized_expected = {normalized_thumbnail_filename(x) for x in expected if x}

    if actual in normalized_expected:
        return True

    # Telegram can strip the video extension from the thumbnail body. Accept
    # that form when it corresponds to the video's basename.
    try:
        stem = normalized_thumbnail_filename(Path(str(video_name)).stem + ".jpg")
        ext = Path(str(video_name)).suffix.lower().lstrip(".")
        if actual == stem or (ext and actual == f"{stem}_{normalized_thumbnail_filename(ext)}"):
            return True
    except Exception:
        pass
    return False


def image_message(message):
    if not message:
        return False
    if getattr(message, "photo", None):
        return True
    doc = getattr(message, "document", None)
    if not doc:
        return False
    mime = message_mime_local(message)
    name = ""
    try:
        name = str(getattr(message.file, "name", "") or "").lower()
    except Exception:
        pass
    return mime.startswith("image/") or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"))


def video_candidate_message(message):
    if not message:
        return False
    if getattr(message, "video", None):
        return True
    doc = getattr(message, "document", None)
    if not doc:
        return False
    mime = message_mime_local(message)
    if mime.startswith("video/"):
        return True
    try:
        name = str(getattr(message.file, "name", "") or "").lower()
    except Exception:
        name = ""
    if Path(name).suffix.lower() in VIDEO_EXTENSIONS:
        return True
    try:
        return any(type(attr).__name__ == "DocumentAttributeVideo"
                   for attr in (getattr(doc, "attributes", ()) or ()))
    except Exception:
        return False


def effective_orientation(message):
    width, height, rotation, _ = get_video_dimensions(message)
    if width and height:
        return ("VERTICAL" if height > width else "HORIZONTAL"), width, height, rotation
    return "UNKNOWN", width, height, rotation


def streamable_status(message):
    """Return True/False/None for Telegram streamability."""
    if not getattr(message, "video", None):
        return False
    _, _, _, supports_streaming = get_video_dimensions(message)
    if supports_streaming is True:
        return True
    if supports_streaming is False:
        return False
    # Fallback only when Telegram/Telethon does not expose the flag.
    try:
        ext = Path(str(getattr(message.file, "name", "") or "")).suffix.lower()
    except Exception:
        ext = ""
    return True if ext in STREAMABLE_FALLBACK_EXTENSIONS else None


from ..engines.media_utils import source_thumbnail_filename_candidates as expected_thumbnail_names_router   # one shared implementation



# ==============================================================================
# SECTION: Scan and classification
# Scans the source and classifies records.
# ==============================================================================

def source_video_record(message):
    orientation, width, height, rotation = effective_orientation(message)
    status = streamable_status(message)
    return {
        "message_id": int(message.id),
        "filename": safe_video_name(message),
        "size": message_size(message),
        "width": width,
        "height": height,
        "rotation": rotation,
        "orientation": orientation,
        "streamable": status,
        "is_native_video": bool(getattr(message, "video", None)),
        "is_document": bool(getattr(message, "document", None)),
        "thumbnail_id": None,
        "thumbnail_filename": "",
        "date": getattr(message, "date", None).isoformat() if getattr(message, "date", None) else None,
    }


async def scan_source_router(client, source):
    """One-pass scan. Only lightweight video records are retained."""
    print("\n" + "=" * 86)
    print(" SCANNING SOURCE CHANNEL — SMART ROUTING ANALYSIS")
    print("=" * 86)
    print("Order: OLDEST -> NEWEST")
    print("History is read once; full videos are never downloaded during the scan.")
    print("A thumbnail is associated only by exact filename or immediate-next-message rule.")

    started = time.monotonic()
    last_status = started
    scanned = 0
    videos = []
    pending = []
    orphan_images = 0

    async for message in client.iter_messages(source, reverse=True):
        scanned += 1
        if not message:
            continue

        now = time.monotonic()
        if now - last_status >= 2.0:
            print(
                f"\rScanned messages: {scanned:,} | Videos: {len(videos):,} | "
                f"Pending pair checks: {len(pending):,}",
                end="",
                flush=True,
            )
            last_status = now

        # Finalize old pending videos after the bounded look-ahead window.
        current_id = int(message.id)
        still_pending = []
        for rec in pending:
            if current_id - rec["message_id"] <= THUMB_SCAN_AHEAD:
                still_pending.append(rec)
            else:
                videos.append(rec)
        pending = still_pending

        if video_candidate_message(message):
            pending.append(source_video_record(message))
            continue

        if image_message(message):
            exact_candidates = []
            immediate_candidates = []
            for rec in pending:
                if rec.get("thumbnail_id"):
                    continue
                try:
                    delta = int(message.id) - int(rec["message_id"])
                except Exception:
                    continue
                if delta <= 0 or delta > THUMB_SCAN_AHEAD:
                    continue
                exact = str(getattr(message.file, "name", "") or "").strip().lower() in expected_thumbnail_names_router(rec["filename"])
                if exact:
                    exact_candidates.append((delta, rec))
                if delta == 1:
                    immediate_candidates.append(rec)

            chosen = None
            if exact_candidates:
                exact_candidates.sort(key=lambda x: (x[0], x[1]["message_id"]))
                chosen = exact_candidates[0][1]
            elif immediate_candidates:
                immediate_candidates.sort(key=lambda r: r["message_id"], reverse=True)
                chosen = immediate_candidates[0]

            if chosen:
                chosen["thumbnail_id"] = int(message.id)
                try:
                    chosen["thumbnail_filename"] = str(getattr(message.file, "name", "") or "")
                except Exception:
                    chosen["thumbnail_filename"] = ""
            else:
                orphan_images += 1

    videos.extend(pending)
    videos.sort(key=lambda x: x["message_id"])

    print("\n")
    elapsed = time.monotonic() - started
    print(
        f"SCAN COMPLETE | Messages: {scanned:,} | Videos: {len(videos):,} | "
        f"Orphan images: {orphan_images:,} | Time: {elapsed:.1f}s"
    )
    return videos, {
        "messages_scanned": scanned,
        "videos_found": len(videos),
        "orphan_images": orphan_images,
        "elapsed_seconds": elapsed,
    }


def classify_record(record):
    vertical = record.get("orientation") == "VERTICAL"
    thumb = bool(record.get("thumbnail_id"))
    native_streamable = record.get("streamable") is True

    if native_streamable and vertical:
        return "VERTICAL"
    if native_streamable and thumb:
        return "MAIN_STREAMABLE"
    if native_streamable and not thumb:
        return "MISSING_THUMBNAIL"
    return "CONVERT_OR_FILE"


def classification_label(record):
    category = classify_record(record)
    if category == "VERTICAL":
        return "VERTICAL -> VERTICAL CHANNEL"
    if category == "MAIN_STREAMABLE":
        return "STREAMABLE + THUMB -> MAIN CHANNEL"
    if category == "MISSING_THUMBNAIL":
        return "STREAMABLE + NO THUMB -> MISSING THUMB CHANNEL"
    return "NON-STREAMABLE FILE -> TRY CONVERSION -> MAIN/VERTICAL OR FILE FALLBACK"


def print_router_dry_run(records, state, destinations, scan_stats):
    """Print what the router would do without changing anything."""
    print("\n" + "=" * 110)
    print(" SMART ROUTER DRY RUN")
    print("=" * 110)
    print("Priority: VERTICAL -> STREAMABLE+THUMB -> STREAMABLE NO THUMB -> CONVERT/FALLBACK FILE")
    print("Transfer requires Archive X 36-frame contact sheets; missing snapshot assets are retried once.")
    print("Conversion is attempted only for non-streamable/document-video candidates.")
    print("\nDESTINATIONS")
    for role, entity in destinations.items():
        print(f"  {DEST_ROLE_NAMES[role]:24s}: {channel_display_name(entity)}")

    counts = {k: 0 for k in ("VERTICAL", "MAIN_STREAMABLE", "MISSING_THUMBNAIL", "CONVERT_OR_FILE")}
    completed = 0
    pending = 0

    print("\n" + "-" * 110)
    for n, rec in enumerate(records, 1):
        cat = classify_record(rec)
        counts[cat] += 1
        entry = state.get("items", {}).get(str(rec["message_id"]), {})
        status = entry.get("status", "PENDING")
        if status == "COMPLETE":
            completed += 1
        else:
            pending += 1
        thumb = f"YES msg={rec['thumbnail_id']}" if rec.get("thumbnail_id") else "NO"
        wh = f"{rec.get('width') or '?'}x{rec.get('height') or '?'}"
        print(
            f"{n:5d}. msg={rec['message_id']:>7} | {wh:>11} | "
            f"{rec.get('orientation','UNKNOWN'):10s} | {thumb:16s} | "
            f"{classification_label(rec):67s} | {rec['filename']} | {status}"
        )
    print("-" * 110)
    print("COUNTS")
    print(f"  Vertical                         : {counts['VERTICAL']:,}")
    print(f"  Streamable + thumbnail           : {counts['MAIN_STREAMABLE']:,}")
    print(f"  Streamable + missing thumbnail   : {counts['MISSING_THUMBNAIL']:,}")
    print(f"  Non-streamable / file conversion: {counts['CONVERT_OR_FILE']:,}")
    print(f"  Already complete                 : {completed:,}")
    print(f"  Need processing                  : {pending:,}")
    print(f"  Orphan images left untouched     : {scan_stats['orphan_images']:,}")




# ==============================================================================
# SECTION: State and verification
# Router state and destination verification.
# ==============================================================================

def load_router_state():
    state = load_json(ROUTER_STATE_FILE, {})
    state.setdefault("version", 1)
    state.setdefault("items", {})
    state.setdefault("source_channel_id", None)
    state.setdefault("destinations", {})
    return state


def save_router_state(state):
    save_json(ROUTER_STATE_FILE, state)


def _telegram_video_signature(message):
    attr = _video_attribute(message)
    width = height = 0
    duration = 0.0
    try:
        width, height, _, _ = get_video_dimensions(message)
    except Exception:
        pass
    if attr is not None:
        try: duration = float(getattr(attr, "duration", 0) or 0)
        except Exception: duration = 0.0
    if not duration:
        try: duration = float(getattr(getattr(message, "file", None), "duration", 0) or 0)
        except Exception: duration = 0.0
    return {"width": int(width or 0), "height": int(height or 0), "duration": float(duration or 0)}


async def verify_destination_pair(client, destination, video_id, thumbnail_id, expected_video_name=None, require_native_video=True, destination_mode=None, expected_source=None, expected_meta=None, require_adjacent=True):
    """Strong verification before source deletion."""
    if not video_id or not thumbnail_id:
        return False, "missing destination message IDs"

    try:
        video = await client.get_messages(destination, ids=int(video_id))
        thumb = await client.get_messages(destination, ids=int(thumbnail_id))
    except Exception as exc:
        return False, f"destination fetch failed: {type(exc).__name__}: {exc}"

    if not video or not getattr(video, "id", None):
        return False, "destination video message not found"
    if not thumb or not getattr(thumb, "id", None):
        return False, "destination thumbnail message not found"

    if int(thumb.id) <= int(video.id):
        return False, f"thumbnail is not after video (video={video.id}, thumb={thumb.id})"
    if require_adjacent and int(thumb.id) != int(video.id) + 1:
        return False, f"thumbnail is not immediately after video (video={video.id}, thumb={thumb.id})"

    if destination_mode == "VIDEO":
        if not getattr(video, "video", None):
            return False, "destination is not a native Telegram VIDEO"
    elif destination_mode == "FILE":
        if not getattr(video, "document", None) or getattr(video, "video", None):
            return False, "destination is not a Telegram FILE/DOCUMENT"
    elif require_native_video and not getattr(video, "video", None):
        return False, "destination video is not a native Telegram video"
    elif not require_native_video and not (getattr(video, "video", None) or getattr(video, "document", None)):
        return False, "destination fallback video/file message has no media"

    if expected_video_name:
        actual_name = safe_video_name(video)
        if actual_name.lower() != str(expected_video_name).lower():
            return False, f"video filename mismatch: expected={expected_video_name}, actual={actual_name}"

    # Strong media identity checks before destructive source deletion.
    if expected_source is not None:
        expected = _telegram_video_signature(expected_source)
        actual = _telegram_video_signature(video)
        if expected["width"] and actual["width"] and expected["width"] != actual["width"]:
            return False, f"video width mismatch: expected={expected['width']}, actual={actual['width']}"
        if expected["height"] and actual["height"] and expected["height"] != actual["height"]:
            return False, f"video height mismatch: expected={expected['height']}, actual={actual['height']}"
        if expected["duration"] and actual["duration"] and abs(expected["duration"] - actual["duration"]) > 2.0:
            return False, f"video duration mismatch: expected={expected['duration']:.1f}s, actual={actual['duration']:.1f}s"
    if expected_meta is not None:
        ew, eh = int(expected_meta.get("width") or 0), int(expected_meta.get("height") or 0)
        actual = _telegram_video_signature(video)
        if ew and actual["width"] and ew != actual["width"]:
            return False, f"converted width mismatch: expected={ew}, actual={actual['width']}"
        if eh and actual["height"] and eh != actual["height"]:
            return False, f"converted height mismatch: expected={eh}, actual={actual['height']}"
        ed = float(expected_meta.get("duration") or 0)
        if ed and actual["duration"] and abs(ed - actual["duration"]) > 2.0:
            return False, f"converted duration mismatch: expected={ed:.1f}s, actual={actual['duration']:.1f}s"

    # Thumbnail MUST be a DOCUMENT, never a Telegram photo.
    if not getattr(thumb, "document", None) or getattr(thumb, "photo", None):
        return False, "destination thumbnail is not an image DOCUMENT"

    thumb_name = ""
    try:
        thumb_name = str(getattr(thumb.file, "name", "") or "")
    except Exception:
        pass
    expected_name = expected_video_name or safe_video_name(video)
    if not thumbnail_filename_matches(expected_name, thumb_name):
        expected_candidates = sorted(source_thumbnail_filename_candidates(expected_name))
        return False, (
            "thumbnail filename mismatch: "
            f"expected one of={expected_candidates}, actual={thumb_name or '[none]'}"
        )

    return True, "video + thumbnail pair verified"


async def delete_source_after_verify(client, source, message_ids):
    """Delete only after destination verification, then confirm Telegram deletion."""
    ids = [int(x) for x in message_ids if x]
    if not ids:
        return
    await delete_messages(client, source, ids)
    # Telegram deletion is an external side effect. Confirm it instead of
    # assuming a successful API call means the messages are gone.
    try:
        remaining = await client.get_messages(source, ids=ids)
        if not isinstance(remaining, list):
            remaining = [remaining] if remaining else []
        remaining = [m for m in remaining if m and getattr(m, "id", None)]
        if remaining:
            raise RuntimeError(
                "source deletion could not be confirmed for message IDs: "
                + ", ".join(str(int(m.id)) for m in remaining)
            )
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"could not confirm source deletion: {type(exc).__name__}: {exc}") from exc




# ==============================================================================
# SECTION: Thumbnail selection
# Copies, generates or selects thumbnails.
# ==============================================================================

async def copy_thumbnail_as_document_canonical(client, destination, source, thumbnail, video_name, tempdir):
    """Copy an existing thumbnail as a DOCUMENT while preserving a recognized filename."""
    try:
        source_name = str(getattr(thumbnail.file, "name", "") or "").strip()
    except Exception:
        source_name = ""
    target_name = source_name if source_name.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) else thumbnail_name_for_video(video_name)
    temp_path = Path(tempdir) / target_name

    downloaded = None
    try:
        print(f"  Downloading source thumbnail temporarily: msg={thumbnail.id}")
        while True:
            try:
                downloaded = await _download_media_governed(client, 
                    thumbnail,
                    file=str(temp_path),
                    progress_callback=progress_printer("THUMBNAIL DL"),
                )
                break
            except Exception as exc:
                wait = flood_wait_seconds(exc)
                if wait:
                    print(f"\nTelegram flood wait detected: {wait}s. Waiting {wait + 2}s...")
                    await asyncio.sleep(wait + 2)
                    continue
                raise
        if not downloaded or not Path(downloaded).exists():
            raise RuntimeError("could not download source thumbnail")
        print(f"\n  Uploading canonical thumbnail DOCUMENT: {target_name}")
        while True:
            try:
                attrs = [DocumentAttributeFilename(target_name)] if DocumentAttributeFilename else None
                return await client.send_file(
                    destination,
                    str(downloaded),
                    force_document=True,
                    attributes=attrs,
                    progress_callback=progress_printer("THUMBNAIL UP"),
                )
            except Exception as exc:
                wait = flood_wait_seconds(exc)
                if wait:
                    print(f"\nTelegram flood wait detected: {wait}s. Waiting {wait + 2}s...")
                    await asyncio.sleep(wait + 2)
                    continue
                raise
    finally:
        try:
            if downloaded and Path(downloaded).exists():
                Path(downloaded).unlink()
        except Exception:
            pass


async def copy_existing_thumbnail(client, destination, source, thumbnail, video_name, tempdir):
    # Always canonicalize as DOCUMENT so PHOTO thumbnails do not survive as photos.
    return await copy_thumbnail_as_document_canonical(
        client, destination, source, thumbnail, video_name, tempdir
    )


async def generate_local_thumbnail(video_path, display_name, output_path):
    """Generate one local video-derived representative thumbnail."""
    from ..core import internal_media
    meta = await asyncio.to_thread(internal_media.probe, str(video_path))
    duration = float(meta.get("duration") or 0)
    if duration <= 0:
        raise RuntimeError("local video has no usable duration")
    return await asyncio.to_thread(
        internal_media.make_thumbnail, str(video_path), str(output_path),
        timestamp=duration * 0.35, width=640, height=360,
    )


async def _select_manager_thumbnail(client, source, source_video, tempdir, *, explicit_thumbnail_id=None,
                                    generate_fallback=True, local_video_path=None, display_name=None,
                                    force_snapshot=False):
    """Prepare a 36-frame, filename/properties-headed image for Telegram delivery.

    This contact sheet is a delivery artifact only. The player and Library keep
    using the independent frame files and the representative still. Existing
    Telegram covers/thumbnails are deliberately not reused here because the
    requested Telegram delivery contract is a consistent 36-frame grid.
    """
    db = globals().get("MEDIA_MANAGER_DB")
    source_thumb_message = None
    chosen_name = str(display_name or safe_video_name(source_video) or
                      (Path(local_video_path).name if local_video_path else "video"))
    safe_stem = Path(chosen_name).stem or "video"
    out = Path(tempdir) / f"{safe_stem}_contact-sheet.jpg"

    # Converted/local output must be represented by the exact bytes being sent,
    # rather than the original Telegram source's metadata or possibly different
    # dimensions. Contact sheets are cached separately from playback snapshots.
    if local_video_path is not None:
        if not generate_fallback:
            from ..engines.thumbnail_cache import local_playback_contact_sheet_cached_sync
            sheet = await asyncio.to_thread(local_playback_contact_sheet_cached_sync, Path(local_video_path))
            if not sheet:
                return None, None, None, source_thumb_message
            out.write_bytes(Path(sheet).read_bytes())
            return out.read_bytes(), "playback-contact-sheet-36", out, source_thumb_message
        from ..engines.thumbnail_cache import ensure_local_playback_contact_sheet
        metadata = None
        try:
            from ..core import internal_media
            metadata = await asyncio.to_thread(internal_media.probe, str(local_video_path))
        except Exception:
            metadata = {}
        sheet = await ensure_local_playback_contact_sheet(
            db, Path(local_video_path), metadata=metadata, force=bool(force_snapshot)
        )
        out.write_bytes(Path(sheet).read_bytes())
        try:
            from ..engines.thumbnail_cache import local_playback_snapshot_path
            rep_src = local_playback_snapshot_path(Path(local_video_path))
            if rep_src.is_file():
                (Path(tempdir) / f"{safe_stem}_representative.jpg").write_bytes(rep_src.read_bytes())
        except Exception:
            pass
        return out.read_bytes(), "playback-contact-sheet-36", out, source_thumb_message

    if source_video is None or getattr(source_video, "id", None) is None:
        return None, None, None, source_thumb_message
    if db is None:
        raise RuntimeError("Archive X database is required for 36-frame Telegram contact-sheet delivery")

    from ..engines.thumbnail_cache import (
        ensure_playback_snapshot_sheet, playback_snapshot_sheet_from_saved_frames_sync,
    )
    channel_id = int(canonical_peer_id(source))
    message_id = int(getattr(source_video, "id", 0) or 0)
    if not generate_fallback:
        # Saved snapshots only: the cached sheet, or one made now from the 36 saved frames (no Telegram download either way).
        sheet = await asyncio.to_thread(playback_snapshot_sheet_from_saved_frames_sync, db, channel_id, message_id)
    else:
        sheet = await ensure_playback_snapshot_sheet(
            db, type("TelegramAdapter", (), {"client": client, "connected": True})(),
            channel_id, message_id, message=source_video, force=bool(force_snapshot),
        )
    if not sheet:
        return None, None, None, source_thumb_message
    data, mime = sheet
    if not data or not str(mime or "").lower().startswith("image/"):
        raise RuntimeError("Contact-sheet service returned invalid image data")
    out.write_bytes(bytes(data))
    from PIL import Image
    try:
        with Image.open(out) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError("Generated Telegram contact sheet failed JPEG/image verification") from exc
    # Also retain the canonical representative JPEG beside the temporary sheet
    # for destination-library cache seeding; never seed the library from a grid.
    try:
        from ..engines.thumbnail_cache import playback_snapshot_cached_sync
        representative = await asyncio.to_thread(playback_snapshot_cached_sync, db, channel_id, message_id)
        if representative:
            (Path(tempdir) / f"{safe_stem}_representative.jpg").write_bytes(representative[0])
    except Exception:
        pass
    return bytes(data), "playback-contact-sheet-36", out, source_thumb_message


class ContactSheetUnavailable(RuntimeError):
    """The required Archive X 36-frame contact sheet could not be prepared."""


async def _select_manager_thumbnail_with_retry(*args, **kwargs):
    """Prepare the canonical Archive X contact sheet, retrying once on failure."""
    if kwargs.get("generate_fallback") is False:
        # "Use saved snapshots only": one look at what is saved; no second attempt and nothing is made from Telegram.
        result = await _select_manager_thumbnail(*args, **kwargs)
        if result and result[0]:
            return result
        raise ContactSheetUnavailable(
            "Needs snapshots: this video has no saved 36-frame snapshot set, and the transfer is set to skip such videos. "
            "Scan the channel (or choose 'Make missing snapshots from Telegram first') and run the transfer again."
        )
    last_error = None
    for attempt in (1, 2):
        try:
            result = await _select_manager_thumbnail(*args, **kwargs)
            if result and result[0]:
                return result
            raise RuntimeError("Archive X 36-frame contact sheet was not available")
        except Exception as exc:
            last_error = exc
            print(f"  Contact sheet attempt {attempt}/2 failed: {type(exc).__name__}: {exc}")
    raise ContactSheetUnavailable(
        f"Needs snapshots: Archive X 36-frame contact sheet failed twice ({type(last_error).__name__}: {last_error})"
    ) from last_error




# ==============================================================================
# SECTION: Delivery preparation
# Snapshots, captions and cleanup.
# ==============================================================================

async def _store_delivery_representative_snapshot(db, destination_id, destination_video_id, destination_thumb_id,
                                                   *, source=None, source_video=None, generated_sheet=None,
                                                   local_video_path=None):
    """Seed destination UI cache with a representative still, never the grid image."""
    if db is None:
        return False
    data = None
    # The selector places a representative JPEG beside its temporary contact sheet.
    if generated_sheet is not None:
        try:
            sheet_path = Path(generated_sheet)
            rep_path = sheet_path.with_name(sheet_path.stem.removesuffix("_contact-sheet") + "_representative.jpg")
            if rep_path.is_file() and rep_path.stat().st_size >= 256:
                data = rep_path.read_bytes()
        except Exception:
            data = None
    if data is None and local_video_path is not None:
        try:
            from ..engines.thumbnail_cache import local_playback_snapshot_path
            rep = local_playback_snapshot_path(Path(local_video_path))
            if rep.is_file() and rep.stat().st_size >= 256:
                data = rep.read_bytes()
        except Exception:
            data = None
    source_channel_key = ""
    if source is not None:
        try:
            from ..core.telegram_identity import canonical_peer_id
            source_channel_key = canonical_peer_id(source)
        except Exception:
            source_channel_key = str(getattr(source, "id", source) or "")
    if data is None and source is not None and source_video is not None:
        try:
            from ..engines.thumbnail_cache import playback_snapshot_cached_sync
            cached = await asyncio.to_thread(
                playback_snapshot_cached_sync, db, int(source_channel_key),
                int(getattr(source_video, "id", 0) or 0),
            )
            if cached:
                data = bytes(cached[0])
        except Exception:
            data = None
    if not data:
        return False
    from ..engines.thumbnail_cache import _store_thumb_sync
    from ..core.telegram_identity import canonical_peer_id
    destination_key = canonical_peer_id(destination_id)
    await asyncio.to_thread(
        _store_thumb_sync, db, destination_key, int(destination_video_id),
        int(destination_thumb_id) if destination_thumb_id else None,
        "image/jpeg", 0, 0, data, "playback-snapshot", False,
    )
    try:
        from ..engines.delivery_ledger import copy_delivery_preview_frames
        await asyncio.to_thread(
            copy_delivery_preview_frames, db, destination_channel=destination_key,
            destination_video_message_id=int(destination_video_id),
            source_row=None, file_path=local_video_path,
        ) if local_video_path is not None else None
        if local_video_path is None and source_channel_key and source_video is not None:
            with db.lock:
                source_row = db.conn.execute(
                    "SELECT * FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                    (source_channel_key, int(getattr(source_video, "id", 0) or 0)),
                ).fetchone()
            if source_row is None:
                source_row = {"source_channel": source_channel_key,
                              "source_message_id": int(getattr(source_video, "id", 0) or 0)}
            await asyncio.to_thread(
                copy_delivery_preview_frames, db, destination_channel=destination_key,
                destination_video_message_id=int(destination_video_id), source_row=source_row,
            )
    except Exception as exc:
        try: db.log(f"Destination timeline frame handoff deferred: {type(exc).__name__}: {exc}", "WARN")
        except Exception: pass
    return True


def _caption_values_with_provider_match(values: dict, match: dict | None) -> dict:
    """Overlay a provider result onto caption values without changing locked DB mappings.

    Filename-only matches can be useful immediately for a send but remain pending
    in the catalogue according to the existing confidence policy. This lets the
    destination caption reflect the best configured provider result without
    silently changing a protected/manual database mapping.
    """
    if not match:
        return values
    out = dict(values)
    locks = {str(x).strip().lower() for x in (values.get("mapping_locks") or [])}
    if "title" not in locks and str(match.get("title") or "").strip():
        out["title"] = str(match.get("title") or "").strip()
    if "site" not in locks:
        studio = str(match.get("studio") or match.get("site") or "").strip()
        if studio:
            out["studio"] = studio
    if "performer" not in locks:
        raw = match.get("performer_details") or match.get("performers") or []
        names = []
        for item in raw:
            name = item.get("name") if isinstance(item, dict) else item
            name = str(name or "").strip()
            if name and name not in names:
                names.append(name)
        if names:
            out["performers"] = names
    raw_tags = match.get("tags") or []
    tags = []
    for tag in raw_tags:
        name = str(tag or "").strip()
        if name and name not in tags:
            tags.append(name)
    if tags:
        out["tags"] = tags
    date = str(match.get("date") or "").strip()
    if date:
        out["date"] = date
    return out


async def _prepare_transfer_catalogue_caption(db, source, source_video):
    """Build a transfer caption only from persisted Archive X catalogue state.

    Telegram Transfer Manager must not re-run Metadata & Assets/provider matching
    when the metadata pass has already been persisted. Transfer consumes the
    database catalogue as its source of truth; missing remote metadata is not
    silently refreshed from this path.
    """
    if db is None:
        return ""
    channel_id = int(canonical_peer_id(source))
    message_id = int(getattr(source_video, "id", 0) or 0)
    try:
        values = _video_fields(db, channel_id, message_id, source_video)
        return render_caption(sender_settings(db)["template"], values, 1024)
    except Exception as exc:
        print(f"  Persisted catalogue caption unavailable; sending without caption: {type(exc).__name__}: {exc}")
        return ""


async def _prepare_manager_video_delivery(client, db, source, source_video, local_video_path=None):
    """Prepare metadata/caption for every Media Manager Telegram send.

    Metadata matching order:
      1. Existing local reference data, when already known.
      2. Configured provider API using OSHASH (computed from two Telegram ranges)
         when the stored fingerprint is missing.
      3. Provider filename parsing when no hash match is available.

    The actual provider policy remains centralized in ``metadata.pipeline`` and
    ``metadata.providers``; this function only makes sure a hash is available
    before the send.
    """
    if db is None:
        return ""
    channel_id = int(canonical_peer_id(source))
    message_id = int(getattr(source_video, "id", 0) or 0)
    filename = safe_video_name(source_video)
    oshash = None
    try:
        with db.lock:
            row = db.conn.execute(
                "SELECT oshash FROM media WHERE source_channel=? AND source_message_id=? LIMIT 1",
                (str(channel_id), message_id),
            ).fetchone()
        oshash = str(row["oshash"] or "").strip() if row else None
    except Exception:
        row = None

    # Media Manager may be asked to send a video before the metadata worker has
    # fingerprinted it. OSHASH needs only the first/last 64 KiB and is therefore
    # cheap compared with downloading or decoding the full video.
    if not oshash:
        try:
            if local_video_path is not None:
                from ..metadata.fingerprint import oshash_file
                oshash = await asyncio.to_thread(oshash_file, str(local_video_path))
                print(f"  Metadata fingerprint: OSHASH={oshash} (local HIFI file)")
            else:
                # OSHASH is a tiny priority operation and must not reserve the
                # whole-file quota used by snapshot/thumbnail extraction.
                from ..core.telegram_range_transport import DirectTelegramRangeSource as FingerprintRangeSource
                source_range = FingerprintRangeSource(client, source_video, asyncio.get_running_loop(),
                                                      fingerprint_lane=True)
                oshash = await asyncio.to_thread(oshash_from_source, source_range)
                print(f"  Metadata fingerprint: OSHASH={oshash}")
            with db.lock:
                db.conn.execute(
                    "UPDATE media SET oshash=?,updated_at=? WHERE source_channel=? AND source_message_id=?",
                    (oshash, config.now_iso(), str(channel_id), message_id),
                )
                db.conn.commit()
        except Exception as exc:
            print(f"  OSHASH unavailable; provider filename parsing will be attempted: {type(exc).__name__}: {exc}")

    match_result = None
    try:
        match_result = await identify_video(
            db, channel_id=channel_id, message_id=message_id, filename=filename,
            oshash=oshash, force=False, suggest_only=False,
        )
    except Exception as exc:
        print(f"  Metadata/API check unavailable; using current catalogue data: {type(exc).__name__}: {exc}")
    try:
        values = _video_fields(db, channel_id, message_id, source_video)
        # A filename-only provider match can be used for this outgoing caption
        # immediately while remaining a pending suggestion in the catalogue.
        if isinstance(match_result, dict) and match_result.get("best"):
            values = _caption_values_with_provider_match(values, match_result.get("best"))
        return render_caption(sender_settings(db)["template"], values, 1024)
    except Exception as exc:
        print(f"  Caption build unavailable; sending without caption: {type(exc).__name__}: {exc}")
        return ""


def _cleanup_verified_delivery_artifacts(source, source_video, *, local_video_path=None, db=None,
                                         temporary_local_video=False):
    """Clear delivery-only contact sheets after verification, keeping source snapshots.

    Local normalized copies are temporary by construction and have their own
    managed snapshot set removed too. The original Telegram/local source's
    reusable individual frames and representative stay in Archive X.
    """
    try:
        from ..engines.thumbnail_cache import (
            cleanup_telegram_delivery_contact_sheet,
            cleanup_local_delivery_artifacts,
            cleanup_local_generated_snapshot_set,
        )
        channel_id = int(canonical_peer_id(source)) if source is not None else 0
        message_id = int(getattr(source_video, "id", 0) or 0)
        if channel_id and message_id:
            cleanup_telegram_delivery_contact_sheet(channel_id, message_id)
        if local_video_path is not None:
            if temporary_local_video and db is not None:
                cleanup_local_generated_snapshot_set(db, Path(local_video_path))
            else:
                cleanup_local_delivery_artifacts(Path(local_video_path))
    except Exception as exc:
        print(f"  WARNING: verified transfer auxiliary-file cleanup incomplete: {type(exc).__name__}: {exc}")




# ==============================================================================
# SECTION: Processing and entry point
# Native or converted processing and the router main.
# ==============================================================================

async def process_native_pair(client, source, destination, record, state_entry, route, db=None):
    """Move a natively streamable video and thumbnail, verifying first."""
    msg_id = int(record["message_id"])
    source_video = await client.get_messages(source, ids=msg_id)
    if not source_video:
        raise RuntimeError("source video message no longer exists")

    thumb_id = None
    video_name = safe_video_name(source_video)

    with tempfile.TemporaryDirectory(prefix="tg_router_") as td:
        print("  Metadata: reuse persisted Archive X catalogue; no Metadata & Assets rerun...")
        caption = await _prepare_transfer_catalogue_caption(db, source, source_video)
        print("  Thumbnail policy: Archive X 36-frame contact sheet only")
        print(f"  Contact-sheet watchdog threshold: {THUMBNAIL_TIMEOUT_SECONDS * 2 // 60} minutes (two attempts)")
        cover_bytes, thumbnail_kind, generated_thumb, source_thumb = await _await_thumbnail_watchdog(
            _select_manager_thumbnail_with_retry(
                client, source, source_video, td,
                explicit_thumbnail_id=record.get("thumbnail_id"),
                generate_fallback=True,
                force_snapshot=False,
            ),
            timeout=THUMBNAIL_TIMEOUT_SECONDS * 2,
            label=safe_video_name(source_video),
        )
        if not cover_bytes:
            raise RuntimeError("No usable thumbnail was available or generated")
        print(f"  Using thumbnail: {thumbnail_kind}")

        dest_video_id = state_entry.get("destination_video_id")
        dest_thumb_id = state_entry.get("destination_thumbnail_id")
        created_video = False

        # Keep the video and its visible thumbnail message together. The same
        # Archive X playback snapshot is used for Telegram's native cover and
        # the immediately-following DOCUMENT message.
        created_media = False
        async with _destination_delivery_lock(destination):
            if not dest_video_id:
                print(f"  Copying source VIDEO msg {msg_id} -> {channel_display_name(destination)} with custom cover...")
                new_video = await send_existing_video_with_cover(
                    client, destination, source_video, cover_bytes, caption=caption
                )
                dest_video_id = int(getattr(new_video, "id", new_video))
                created_video = True
                state_entry["destination_video_id"] = dest_video_id
                state_entry["status"] = "VIDEO_COPIED"
                state_entry["caption"] = caption
                state_entry["thumbnail_kind"] = thumbnail_kind
                save_router_state(router_runtime_state)
            else:
                print(f"  Destination VIDEO already exists: msg={dest_video_id}")

            # Replace an old destination thumbnail if it predates the 36-frame
            # Telegram contact-sheet delivery policy.
            if dest_thumb_id and state_entry.get("thumbnail_kind") not in {"playback-contact-sheet-36"}:
                print(f"  Replacing legacy destination thumbnail msg={dest_thumb_id} with the 36-frame contact sheet...")
                await delete_messages(client, destination, [int(dest_thumb_id)])
                dest_thumb_id = None
                state_entry.pop("destination_thumbnail_id", None)
                state_entry["status"] = "VIDEO_COPIED"
                save_router_state(router_runtime_state)

            if not dest_thumb_id:
                print(f"  Posting 36-frame contact-sheet DOCUMENT after VIDEO msg={dest_video_id}...")
                thumb_msg = await upload_thumbnail_bytes_document(
                    client, destination, cover_bytes, video_name
                )
                dest_thumb_id = int(getattr(thumb_msg, "id", thumb_msg) or 0)
                if not dest_thumb_id or dest_thumb_id <= int(dest_video_id):
                    raise RuntimeError(
                        f"Thumbnail was not posted after video: video={dest_video_id}, thumbnail={dest_thumb_id}"
                    )
                state_entry["destination_thumbnail_id"] = dest_thumb_id
                state_entry["status"] = "PAIR_COPIED"
                state_entry["thumbnail_kind"] = thumbnail_kind
                save_router_state(router_runtime_state)
            else:
                print(f"  Destination THUMBNAIL already exists: msg={dest_thumb_id}")

        ok, reason = await verify_destination_pair(
            client, destination, dest_video_id, dest_thumb_id,
            expected_video_name=video_name, destination_mode="VIDEO",
            expected_source=source_video, require_adjacent=created_video,
        )
        if not ok:
            state_entry["status"] = "DESTINATION_VERIFY_FAILED"
            state_entry["error"] = reason
            save_router_state(router_runtime_state)
            raise RuntimeError(f"destination verification failed: {reason}")

        print(f"  DESTINATION VERIFIED: {channel_display_name(destination)} | video={dest_video_id} | thumbnail={dest_thumb_id} | native cover={thumbnail_kind}")

        previous_thumbnail_id = int(state_entry.get("source_thumbnail_message_id_to_delete") or 0)
        source_thumbnail_id = await _resolve_associated_source_thumbnail_for_move(
            client, source, source_video, db, known_message_id=previous_thumbnail_id or None,
        )
        if source_thumbnail_id:
            state_entry["source_thumbnail_message_id_to_delete"] = int(source_thumbnail_id)
            save_router_state(router_runtime_state)
            if not previous_thumbnail_id:
                print(f"  Associated SOURCE thumbnail found: msg={source_thumbnail_id}")
        else:
            state_entry.pop("source_thumbnail_message_id_to_delete", None)

        await _store_delivery_representative_snapshot(
            db, destination, dest_video_id, dest_thumb_id,
            source=source, source_video=source_video, generated_sheet=generated_thumb,
        )
        _cleanup_verified_delivery_artifacts(source, source_video, db=db)

        source_ids = [msg_id]
        source_thumbnail_id = int(state_entry.get("source_thumbnail_message_id_to_delete") or 0)
        if source_thumbnail_id > 0 and source_thumbnail_id != msg_id:
            source_ids.append(source_thumbnail_id)
        await delete_source_after_verify(client, source, source_ids)
        if source_thumbnail_id > 0 and source_thumbnail_id in source_ids[1:]:
            _mark_moved_source_thumbnail_deleted(db, source, source_thumbnail_id)

        state_entry["source_deleted"] = True
        state_entry["status"] = "COMPLETE"
        state_entry.pop("error", None)
        state_entry["destination_channel_id"] = int(destination.id)
        save_router_state(router_runtime_state)
        print(f"  SOURCE DELETED AFTER VERIFICATION: {source_ids}")


async def process_converted_or_fallback(client, source, destinations, record, state_entry, db=None):
    """Download and convert a video, or use the fallback destination."""
    msg_id = int(record["message_id"])
    source_video = await client.get_messages(source, ids=msg_id)
    if not source_video:
        raise RuntimeError("source video message no longer exists")

    thumb_id = record.get("thumbnail_id")
    source_thumb = await client.get_messages(source, ids=int(thumb_id)) if thumb_id else None
    original_name = safe_video_name(source_video)
    # Telegram → Telegram transfer consumes the persisted catalogue. Metadata &
    # Assets is an independent operator workflow and must not be restarted from
    # inside a transfer item while both jobs are running concurrently.
    caption = await _prepare_transfer_catalogue_caption(db, source, source_video)
    source_orientation, _, _, _ = effective_orientation(source_video)

    with tempfile.TemporaryDirectory(prefix="tg_router_convert_") as td:
        input_name = original_name or f"telegram_{msg_id}.bin"
        input_path = Path(td) / input_name
        source_bytes = int(getattr(getattr(source_video, "file", None), "size", 0) or 0)
        download_progress = (_manager_download_progress_callback(
            safe_video_name(source_video) or input_path.name,
            db=globals().get("MEDIA_MANAGER_DB"), job_id=globals().get("TRANSFER_JOB_ID"),
        ) if source_bytes >= 1024 * 1024 else None)
        await download_source_file(client, source, source_video, input_path,
                                   progress_callback=download_progress)

        output_name = f"{Path(original_name).stem}.mp4" if Path(original_name).suffix else f"telegram_{msg_id}.mp4"
        output_path = Path(td) / output_name

        conversion_error = None
        converted_meta = None
        try:
            converted_meta = await asyncio.wait_for(
                convert_to_streamable_mp4(input_path, output_path),
                timeout=CONVERSION_TIMEOUT_SECONDS,
            )
            print(
                f"  CONVERSION SUCCESS: {human_size(output_path.stat().st_size)} | "
                f"{converted_meta.get('width')}x{converted_meta.get('height')} | "
                f"{converted_meta.get('video_codec')}"
            )
        except Exception as exc:
            conversion_error = exc
            print(f"  CONVERSION FAILED: {type(exc).__name__}: {exc}")

        if converted_meta is not None:
            # Prefer final converted dimensions over stale Telegram document metadata.
            is_vertical = int(converted_meta.get("height") or 0) > int(converted_meta.get("width") or 0)
            destination_role = DEST_VERTICAL if is_vertical else DEST_MAIN
            destination = destinations[destination_role]
            state_entry["route"] = destination_role
            state_entry["conversion"] = "SUCCESS"
            state_entry["converted_filename"] = output_name
            state_entry["status"] = "CONVERTED"
            save_router_state(router_runtime_state)

            # The converted output gets an Archive X 36-frame contact sheet
            # generated from the exact local bytes being transferred.
            cover_bytes, thumbnail_kind, generated_thumb, source_thumb = await _await_thumbnail_watchdog(
                _select_manager_thumbnail_with_retry(
                    client, source, source_video, td,
                    explicit_thumbnail_id=thumb_id,
                    generate_fallback=True,
                    local_video_path=output_path,
                    display_name=output_name,
                    force_snapshot=False,
                    ),
                timeout=THUMBNAIL_TIMEOUT_SECONDS * 2,
                label=safe_video_name(source_video),
            )
            if not cover_bytes:
                raise RuntimeError("No usable thumbnail was available or generated")
            thumb_path = generated_thumb if generated_thumb is not None else None

            dest_video_id = state_entry.get("destination_video_id")
            dest_thumb_id = state_entry.get("destination_thumbnail_id")
            created_video = False

            async with _destination_delivery_lock(destination):
                if not dest_video_id:
                    print(f"  Uploading CONVERTED native VIDEO -> {channel_display_name(destination)} with custom cover")
                    new_video = await send_local_video_with_cover(
                        client, destination, output_path, cover_bytes,
                        caption=caption,
                        progress_callback=progress_printer("VIDEO UPLOAD", with_rate=True),
                        status_callback=upload_status_printer("VIDEO UPLOAD"),
                        supports_streaming=True,
                    )
                    print()
                    dest_video_id = int(getattr(new_video, "id", new_video))
                    created_video = True
                    state_entry["destination_video_id"] = dest_video_id
                    state_entry["status"] = "VIDEO_COPIED"
                    state_entry["caption"] = caption
                    state_entry["thumbnail_kind"] = thumbnail_kind
                    save_router_state(router_runtime_state)
                else:
                    print(f"  Destination VIDEO already exists: msg={dest_video_id}")

                if dest_thumb_id and state_entry.get("thumbnail_kind") != "playback-contact-sheet-36":
                    print(f"  Replacing legacy destination thumbnail msg={dest_thumb_id} with the 36-frame contact sheet...")
                    await delete_messages(client, destination, [int(dest_thumb_id)])
                    dest_thumb_id = None
                    state_entry.pop("destination_thumbnail_id", None)
                    state_entry["status"] = "VIDEO_COPIED"
                    save_router_state(router_runtime_state)

                if not dest_thumb_id:
                    print(f"  Posting 36-frame contact-sheet DOCUMENT after VIDEO msg={dest_video_id}...")
                    thumb_msg = await upload_thumbnail_bytes_document(
                        client, destination, cover_bytes, output_name
                    )
                    dest_thumb_id = int(getattr(thumb_msg, "id", thumb_msg) or 0)
                    if not dest_thumb_id or dest_thumb_id <= int(dest_video_id):
                        raise RuntimeError(
                            f"Thumbnail was not posted after video: video={dest_video_id}, thumbnail={dest_thumb_id}"
                        )
                    state_entry["destination_thumbnail_id"] = dest_thumb_id
                    state_entry["status"] = "PAIR_COPIED"
                    state_entry["thumbnail_kind"] = thumbnail_kind
                    save_router_state(router_runtime_state)
                else:
                    print(f"  Destination THUMBNAIL already exists: msg={dest_thumb_id}")

            ok, reason = await verify_destination_pair(
                client, destination, dest_video_id, dest_thumb_id,
                expected_video_name=output_name, destination_mode="VIDEO",
                expected_meta=converted_meta, require_adjacent=created_video,
            )
            if not ok:
                state_entry["status"] = "DESTINATION_VERIFY_FAILED"
                state_entry["error"] = reason
                save_router_state(router_runtime_state)
                raise RuntimeError(f"destination verification failed: {reason}")

            print(f"  DESTINATION VERIFIED: {channel_display_name(destination)} | video={dest_video_id} | thumbnail={dest_thumb_id} | native cover={thumbnail_kind}")
            previous_thumbnail_id = int(state_entry.get("source_thumbnail_message_id_to_delete") or 0)
            source_thumbnail_id = await _resolve_associated_source_thumbnail_for_move(
                client, source, source_video, db, known_message_id=previous_thumbnail_id or None,
            )
            if source_thumbnail_id:
                state_entry["source_thumbnail_message_id_to_delete"] = int(source_thumbnail_id)
                save_router_state(router_runtime_state)
                if not previous_thumbnail_id:
                    print(f"  Associated SOURCE thumbnail found: msg={source_thumbnail_id}")
            else:
                state_entry.pop("source_thumbnail_message_id_to_delete", None)
            _cleanup_verified_delivery_artifacts(
                source, source_video, local_video_path=output_path, db=db,
                temporary_local_video=True,
            )
            source_ids = [msg_id]
            source_thumbnail_id = int(state_entry.get("source_thumbnail_message_id_to_delete") or 0)
            if source_thumbnail_id > 0 and source_thumbnail_id != msg_id:
                source_ids.append(source_thumbnail_id)
            await delete_source_after_verify(client, source, source_ids)
            if source_thumbnail_id > 0 and source_thumbnail_id in source_ids[1:]:
                _mark_moved_source_thumbnail_deleted(db, source, source_thumbnail_id)

            state_entry["source_deleted"] = True
            state_entry["status"] = "COMPLETE"
            state_entry.pop("error", None)
            save_router_state(router_runtime_state)
            print(f"  SOURCE DELETED AFTER CONVERSION + VERIFICATION: {source_ids}")
            return

        # ------------------------------------------------------------
        # Conversion failed: never silently move an unstreamable original.
        # Keep the source untouched and leave the job resumable for repair.
        # ------------------------------------------------------------
        state_entry["conversion"] = "FAILED"
        state_entry["status"] = "CONVERSION_FAILED"
        state_entry["conversion_error"] = f"{type(conversion_error).__name__}: {conversion_error}"
        state_entry["source_deleted"] = False
        save_router_state(router_runtime_state)
        raise RuntimeError(
            "Video conversion failed; source was NOT deleted and the original was NOT silently moved. "
            "Retry conversion or explicitly choose a FILE fallback. "
            + state_entry["conversion_error"]
        )

async def choose_router_destinations(client, source):
    destinations = {}
    selected_ids = {int(source.id)}
    for role in (DEST_MAIN, DEST_MISSING, DEST_VERTICAL, DEST_FILE):
        destinations[role] = await choose_channel(
            client,
            f"DESTINATION — {DEST_ROLE_NAMES[role]}",
            exclude_ids=selected_ids,
        )
        selected_ids.add(int(destinations[role].id))
    return destinations


async def smart_router_main(*, client_override=None, source_override=None, destinations_override=None, auto_confirm=False, disconnect_client=True):
    """Router entry: scan the source, classify and process each record."""
    global router_runtime_state

    print("=" * 86)
    print(" TELEGRAM SMART VIDEO ROUTER / MIGRATOR")
    print("=" * 86)
    print("\nWorkflow:")
    print("  1. Scan the source channel once, oldest -> newest.")
    print("  2. Detect thumbnails using your existing VIDEO -> THUMBNAIL rules.")
    print("  3. Route vertical videos to the vertical destination.")
    print("  4. Route normal streamable pairs to the main destination.")
    print("  5. Route normal streamable videos with no thumbnail to missing-thumbnail destination and generate one.")
    print("  6. Attempt conversion for file/document videos; success -> main/vertical, failure -> file destination.")
    print("  7. Verify destination VIDEO + native Telegram cover; no separate thumbnail message is posted.")
    print("  8. ONLY after verification, delete source video + source thumbnail.")
    print("  9. Failed items stay in source and are retried next run.\n")

    config = load_json(CONFIG_FILE, {})
    router_runtime_state = load_router_state()
    client = client_override
    owns_client = client is None

    try:
        if client is None:
            client = await login_and_get_client(config)
        me = await client.get_me()
        print(
            f"Logged in as: {getattr(me, 'first_name', '') or getattr(me, 'username', '') or 'account'}"
        )

        print("\n" + "#" * 86)
        print(" STEP 1/5 — SOURCE CHANNEL")
        print("#" * 86)
        source = source_override or await choose_channel(client, "SOURCE")

        print("\n" + "#" * 86)
        print(" STEP 2/5 — DESTINATION CHANNELS")
        print("#" * 86)
        if destinations_override is not None:
            destinations = destinations_override
        else:
            print("You will select four destinations in this order:")
            print("  1. MAIN STREAMABLE")
            print("  2. MISSING THUMBNAIL")
            print("  3. VERTICAL")
            print("  4. NON-STREAMABLE FILE")
            destinations = await choose_router_destinations(client, source)

        destination_ids = {k: int(v.id) for k, v in destinations.items()}
        if len(set(destination_ids.values())) != len(destination_ids):
            raise RuntimeError("All four destination channels must be different from each other.")
        if int(source.id) in destination_ids.values():
            raise RuntimeError("Source channel cannot also be a destination channel.")

        source_title = channel_display_name(source)
        router_runtime_state["source_channel_id"] = int(source.id)
        router_runtime_state["source_title"] = source_title
        router_runtime_state["destinations"] = {
            role: {"id": int(entity.id), "title": channel_display_name(entity)}
            for role, entity in destinations.items()
        }
        save_router_state(router_runtime_state)

        print("\n" + "=" * 86)
        print(" CHANNEL CONFIGURATION")
        print("=" * 86)
        print(f"Source                : {source_title}")
        for role, entity in destinations.items():
            print(f"{DEST_ROLE_NAMES[role]:22s}: {channel_display_name(entity)}")
        print("Mode                  : MOVE")
        print("Source deletion       : AFTER destination verification ONLY")
        print("Thumbnail requirement : CANONICAL DOCUMENT <video>.jpg")

        print("\n" + "#" * 86)
        print(" STEP 3/5 — SCAN")
        print("#" * 86)
        records, scan_stats = await scan_source_router(client, source)
        if not records:
            print("\nNo video candidates found in the source channel.")
            return

        print("\n" + "#" * 86)
        print(" STEP 4/5 — DRY RUN")
        print("#" * 86)
        print_router_dry_run(records, router_runtime_state, destinations, scan_stats)

        pending = []
        for rec in records:
            entry = router_runtime_state["items"].get(str(rec["message_id"]), {})
            if entry.get("status") == "COMPLETE" and entry.get("source_deleted"):
                continue
            pending.append(rec)

        if not pending:
            print("\nEverything scanned is already complete according to the state file.")
            return

        print("\n" + "#" * 86)
        print(" STEP 5/5 — EXECUTION")
        print("#" * 86)
        answer = "y" if auto_confirm else input(
            f"\nProcess {len(pending):,} pending video(s)? This WILL delete source messages only after verification. [y/N]: "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("\nCancelled. No Telegram messages were changed.")
            return

        success = 0
        failed = 0
        skipped = 0

        for index, rec in enumerate(pending, 1):
            msg_id = int(rec["message_id"])
            entry = router_runtime_state["items"].setdefault(str(msg_id), {})
            entry.update({
                "source_message_id": msg_id,
                "source_filename": rec["filename"],
                "source_thumbnail_id": rec.get("thumbnail_id"),
                "category": classify_record(rec),
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "status": entry.get("status", "STARTED"),
            })
            save_router_state(router_runtime_state)

            print("\n" + "-" * 110)
            print(
                f"PROCESS {index}/{len(pending)} | msg={msg_id} | "
                f"{rec.get('width') or '?'}x{rec.get('height') or '?'} | "
                f"{rec.get('orientation','UNKNOWN')} | {rec['filename']}"
            )
            print(f"Classification: {classification_label(rec)}")

            try:
                cat = classify_record(rec)
                if cat == "VERTICAL":
                    await process_native_pair(
                        client, source, destinations[DEST_VERTICAL], rec, entry, DEST_VERTICAL, globals().get("MEDIA_MANAGER_DB")
                    )
                elif cat == "MAIN_STREAMABLE":
                    await process_native_pair(
                        client, source, destinations[DEST_MAIN], rec, entry, DEST_MAIN, globals().get("MEDIA_MANAGER_DB")
                    )
                elif cat == "MISSING_THUMBNAIL":
                    await process_native_pair(
                        client, source, destinations[DEST_MISSING], rec, entry, DEST_MISSING, globals().get("MEDIA_MANAGER_DB")
                    )
                else:
                    await process_converted_or_fallback(
                        client, source, destinations, rec, entry, globals().get("MEDIA_MANAGER_DB")
                    )
                success += 1
            except ContactSheetUnavailable as exc:
                skipped += 1
                entry["status"] = "NEEDS_SNAPSHOTS"
                entry["error"] = str(exc)
                entry["needs_snapshots"] = True
                save_router_state(router_runtime_state)
                print(f"\n  SKIPPED — NEEDS SNAPSHOTS: {exc}")
                print("  Source is untouched. The batch will continue with the next video.")
            except TimeoutError as exc:
                failed += 1
                entry["status"] = "ERROR_TIMEOUT"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                save_router_state(router_runtime_state)
                print(f"\n  TIMEOUT: {exc}")
                print("  Source is untouched. Moving to next video.")
            except Exception as exc:
                failed += 1
                entry["status"] = "ERROR"
                entry["error"] = f"{type(exc).__name__}: {exc}"
                save_router_state(router_runtime_state)
                print(f"\n  ERROR: {type(exc).__name__}: {exc}")
                print("  Source is retained unless the destination was already fully verified.")

        all_complete = sum(
            1 for item in router_runtime_state.get("items", {}).values()
            if item.get("status") == "COMPLETE" and item.get("source_deleted")
        )
        error_count = sum(
            1 for item in router_runtime_state.get("items", {}).values()
            if str(item.get("status", "")).startswith("ERROR")
        )

        print("\n" + "=" * 86)
        print("SMART ROUTER RUN COMPLETE")
        print("=" * 86)
        print(f"Messages scanned           : {scan_stats['messages_scanned']:,}")
        print(f"Videos found               : {scan_stats['videos_found']:,}")
        print(f"Orphan images left         : {scan_stats['orphan_images']:,}")
        print(f"Processed successfully     : {success:,}")
        print(f"Failed items               : {failed:,}")
        print(f"Skipped — needs snapshots  : {skipped:,}")
        print(f"All-time source deletions  : {all_complete:,}")
        print(f"State error records        : {error_count:,}")
        print(f"State file                 : {ROUTER_STATE_FILE}")
        print("\nSource deletion rule: destination VIDEO + native Telegram cover must verify first.")
        if failed:
            print("Failed items remain in the source and can be retried on the next run.")

    except KeyboardInterrupt:
        print("\nStopped by user. State has been saved; verified items remain resumable.")
    except Exception as exc:
        print(f"\nFATAL ERROR: {type(exc).__name__}: {exc}")
    finally:
        if client and disconnect_client and owns_client:
            try:
                await client.disconnect()
            except Exception:
                pass
