"""Telegram Media Manager: Vertical video archiver.

Split out of ``telegram_media_manager.py`` in v29.4.387 without behaviour
changes. The main module executes the parts, in order, inside its own module
namespace, so every function, constant and ``globals()`` lookup is exactly as
when this was one file. Do not import this file directly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Vertical detection
#   - Telegram operations
#   - Thumbnail generation
#   - Scan and processing
# ------------------------------------------------------------------
# ============================================================
# VERTICAL VIDEO ARCHIVER
# ============================================================

# A video is considered vertical when its effective Telegram video
# dimensions have height > width. Telegram's DocumentAttributeVideo is
# used first, so the scan does not download the video just to classify it.
# If Telegram exposes a rotation field on the attribute, 90/270-degree
# rotation is accounted for as well.
VERTICAL_ASPECT_STRICT = True

# Existing thumbnail workflow behavior:
#   1. Generate the representative thumbnail locally from Telegram byte ranges.
#   2. Copy/forward the video to the destination.
#   3. Upload the JPG as a DOCUMENT immediately after the video.
#
# Because Telegram shows the newest message first, step 3 makes the thumbnail
# appear above the video in the destination channel, matching the user's
# existing browsing workflow.
UPLOAD_THUMBNAIL_AS_DOCUMENT = True
THUMBNAIL_TIMEOUT_SECONDS = 90


# ==============================================================================
# SECTION: Vertical detection
# Dimensions and vertical checks.
# ==============================================================================

def _video_attribute(message):
    """Return the first Telegram video attribute, when available."""
    doc = getattr(message, "document", None)
    if not doc:
        return None

    for attr in getattr(doc, "attributes", []) or []:
        name = type(attr).__name__.lower()
        if "documentattributevideo" in name or (
            "video" in name and hasattr(attr, "w") and hasattr(attr, "h")
        ):
            return attr
    return None


def get_video_dimensions(message):
    """
    Return (width, height, rotation, supports_streaming).

    Primary source: Telegram DocumentAttributeVideo.
    Fallbacks: Telethon's File wrapper and message.video/document attributes.
    """
    width = None
    height = None
    rotation = None
    supports_streaming = None

    attr = _video_attribute(message)
    if attr is not None:
        width = getattr(attr, "w", None)
        height = getattr(attr, "h", None)
        rotation = getattr(attr, "rotation", None)
        supports_streaming = getattr(attr, "supports_streaming", None)

    file_obj = getattr(message, "file", None)
    if width is None:
        width = getattr(file_obj, "width", None)
    if height is None:
        height = getattr(file_obj, "height", None)

    # Some Telethon versions expose the raw attribute through the message
    # media object even when the File wrapper has incomplete metadata.
    media = getattr(message, "media", None)
    document = getattr(media, "document", None) if media else None
    for raw_attr in getattr(document, "attributes", []) or []:
        if width is None and hasattr(raw_attr, "w"):
            width = getattr(raw_attr, "w", None)
        if height is None and hasattr(raw_attr, "h"):
            height = getattr(raw_attr, "h", None)
        if rotation is None and hasattr(raw_attr, "rotation"):
            rotation = getattr(raw_attr, "rotation", None)
        if supports_streaming is None and hasattr(raw_attr, "supports_streaming"):
            supports_streaming = getattr(raw_attr, "supports_streaming", None)

    try:
        width = int(width) if width is not None else None
    except Exception:
        width = None
    try:
        height = int(height) if height is not None else None
    except Exception:
        height = None
    try:
        rotation = int(rotation) if rotation is not None else 0
    except Exception:
        rotation = 0

    # Account for an explicitly exposed rotation matrix/angle.
    normalized_rotation = rotation % 360
    if normalized_rotation in (90, 270) and width and height:
        width, height = height, width

    return width, height, normalized_rotation, supports_streaming


def state_key(source, destination, message_id):
    return f"{int(source.id)}:{int(destination.id)}:{int(message_id)}"


# ==============================================================================
# SECTION: Telegram operations
# Forward and thumbnail upload.
# ==============================================================================

async def forward_without_tag_safe(client, destination, source, message):
    """Copy ONLY the Telegram media as a VIDEO, with NO source caption/text/link.

    Passing ``message.media`` to send_file reuses the existing Telegram media
    handle instead of downloading and re-uploading the full video.  Unlike
    forward_messages(), this creates a fresh message with an empty caption,
    so source captions, @channel text, URLs and inline text are not copied.
    """
    while True:
        try:
            # Do not use forward_messages here. Forwarding preserves the
            # original caption/text, which is exactly what this mover must not
            # copy. Telethon supports sending an existing message.media handle
            # directly as Telegram video media, reusing the Telegram-side file.
            result = await client.send_file(
                destination,
                message.media,
                caption=None,
                force_document=False,
                supports_streaming=False,
                allow_cache=True,
            )
            if isinstance(result, list):
                return result[0]
            return result
        except Exception as exc:
            wait = flood_wait_seconds(exc)
            if wait:
                _transfer_timing.TIMER.flood(wait)
                print(f"\nTelegram flood wait detected: {wait}s. Waiting {wait + 2}s...")
                await asyncio.sleep(wait + 2)
                continue
            raise


async def upload_thumbnail_document(client, destination, path):
    """Upload the 36-frame delivery contact sheet as the paired JPG DOCUMENT."""
    source_path = Path(path)
    if not source_path.is_file() or source_path.stat().st_size <= 0:
        raise RuntimeError("Telegram delivery contact sheet is missing or empty")
    payload = io.BytesIO(source_path.read_bytes())
    # Keep the established companion name expected by the pair verifier. The
    # image itself has a 36-frame grid/header and its own independent cache path.
    stem = source_path.name.removesuffix("_contact-sheet.jpg").removesuffix("_telegram_archive_contact_sheet.jpg")
    payload.name = f"{stem}.jpg" if stem else "thumbnail.jpg"
    while True:
        try:
            payload.seek(0)
            return await client.send_file(
                destination, payload, force_document=UPLOAD_THUMBNAIL_AS_DOCUMENT,
                progress_callback=progress_printer("THUMBNAIL"),
            )
        except Exception as exc:
            wait = flood_wait_seconds(exc)
            if wait:
                _transfer_timing.TIMER.flood(wait)
                print(f"\nTelegram flood wait detected: {wait}s. Waiting {wait + 2}s...")
                await asyncio.sleep(wait + 2)
                continue
            raise


_DESTINATION_DELIVERY_LOCKS = {}


def _destination_delivery_lock(destination):
    """Return a per-destination lock so video + thumbnail stay adjacent."""
    key = int(getattr(destination, "id", destination) or 0)
    lock = _DESTINATION_DELIVERY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DESTINATION_DELIVERY_LOCKS[key] = lock
    return lock


async def upload_thumbnail_bytes_document(client, destination, thumb_bytes: bytes, video_name: str):
    """Post the exact thumbnail bytes used as the video cover as a separate DOCUMENT.

    The thumbnail is deliberately posted after the video. Using the same bytes
    for both the native video cover and the visible follow-up message keeps the
    two representations identical.
    """
    if not thumb_bytes:
        raise RuntimeError("Cannot post an empty thumbnail")
    name = f"{Path(video_name).stem}.jpg" if str(video_name).strip() else "thumbnail.jpg"
    payload = io.BytesIO(bytes(thumb_bytes))
    payload.name = name
    while True:
        try:
            payload.seek(0)
            return await client.send_file(
                destination,
                payload,
                force_document=True,
                progress_callback=progress_printer("THUMBNAIL"),
            )
        except Exception as exc:
            wait = flood_wait_seconds(exc)
            if wait:
                _transfer_timing.TIMER.flood(wait)
                print(f"\nTelegram flood wait detected: {wait}s. Waiting {wait + 2}s...")
                await asyncio.sleep(wait + 2)
                continue
            raise


# ==============================================================================
# SECTION: Thumbnail generation
# Thumbnail generation under a watchdog.
# ==============================================================================

async def _await_thumbnail_watchdog(awaitable, timeout=THUMBNAIL_TIMEOUT_SECONDS, label="thumbnail"):
    """Await thumbnail work without cancelling the underlying task on timeout.

    The thumbnail generator bridges worker threads to the live Telethon event
    loop. Cancelling it at the watchdog boundary leaves Telegram GetFile
    coroutines in-flight; when the loop later closes those coroutines emit the
    CancelledError/Event loop is closed cascade. A timeout is therefore a
    diagnostic threshold, not a hard cancellation boundary.
    """
    task = asyncio.ensure_future(awaitable)
    done, pending = await asyncio.wait({task}, timeout=float(timeout))
    if pending:
        print(
            f"  Thumbnail watchdog reached {float(timeout):.0f}s for {label}; "
            f"continuing without cancellation so Telegram range requests can settle...",
            flush=True,
        )
        return await task
    return task.result()


async def generate_thumbnail_with_timeout(client, source, message, tempdir):
    """Generate the canonical representative still image."""
    db = globals().get("MEDIA_MANAGER_DB")
    if db is not None:
        from ..engines.thumbnail_cache import ensure_playback_snapshot
        snapshot = await ensure_playback_snapshot(
            db, type("TelegramAdapter", (), {"client": client, "connected": True})(),
            int(canonical_peer_id(source)), int(message.id), message=message,
        )
        out = Path(tempdir) / thumbnail_name_for_video(safe_video_name(message))
        out.write_bytes(snapshot[0])
        return out
    return await _await_thumbnail_watchdog(
        create_thumbnail_for_message(client, source, message, tempdir),
        timeout=THUMBNAIL_TIMEOUT_SECONDS,
        label=safe_video_name(message),
    )


# ==============================================================================
# SECTION: Scan and processing
# Candidate scan and per-video processing.
# ==============================================================================


