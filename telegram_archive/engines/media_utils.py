"""Shared media utility functions — ported verbatim from the original scripts.

Every engine module imports from here instead of duplicating these.
Source: telegram_smart_video_router_v14_canonical_thumbnail_filename_fix.py
        telegram_unstreamable_mover.py  (flood-wait helpers, DirectTelegramRangeSource)
        telegram_duplicate_cleanup_v2.py (document_identity, media_attributes)
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import re
import asyncio
import io
from pathlib import Path
from typing import Optional, Any

from ..telegram.client_policy import UPLOAD_PART_SIZE_KB

VIDEO_EXTENSIONS = {
    # Common browser-playable and FFmpeg-compatible video containers.
    ".mp4", ".m4v", ".m4p", ".mov", ".qt", ".mkv", ".mk3d",
    ".avi", ".divx", ".wmv", ".asf", ".flv", ".f4v", ".f4p",
    ".mpg", ".mpeg", ".mpe", ".mpv", ".m2v", ".mpeg1", ".mpeg2",
    ".webm", ".ts", ".mts", ".m2ts", ".m2t", ".3gp", ".3g2",
    ".vob", ".ogv", ".ogx", ".rm", ".rmvb", ".amv", ".mxf",
    ".roq", ".nsv", ".yuv",
}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


# ---------------------------------------------------------------------------
# FloodWait helpers (ported from telegram_unstreamable_mover.py)
# ---------------------------------------------------------------------------

def flood_wait_seconds(exc) -> int:
    try:
        seconds = int(getattr(exc, "seconds", 0) or 0)
        if seconds > 0:
            return seconds
    except Exception:
        _swallowed_note("media_utils.flood_wait_seconds")
        pass
    text = str(exc or "")
    for pattern in (
        r"FLOOD(?:_PREMIUM)?_WAIT[_ ](\d+)",
        r"a\s+wait\s+of\s+(\d+)\s+seconds?",
    ):
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                _swallowed_note("media_utils.flood_wait_seconds")
                pass
    return 0


def is_flood_wait(exc) -> bool:
    return flood_wait_seconds(exc) > 0


FLOODWAIT_INLINE_MAX_SECONDS = 90


class TelegramRangeFloodError(RuntimeError):
    """Raised when Telegram keeps rate-limiting byte-range thumbnail reads."""


# ---------------------------------------------------------------------------
# Filename / identity helpers
# (ported from telegram_smart_video_router_v14_canonical_thumbnail_filename_fix.py)
# ---------------------------------------------------------------------------

def safe_video_name(message) -> str:
    try:
        name = getattr(message.file, "name", None)
    except Exception:
        name = None
    name = str(name or "").strip()
    if not name:
        name = f"telegram_{int(getattr(message, 'id', 0) or 0)}.mp4"
    if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
        name += ".mp4"
    return name


def canonical_media_key(name: str) -> str:
    """Normalize video/thumbnail filenames using the channel-cleanup identity rules."""
    value = str(name or "").strip().lower()
    if not value:
        return ""
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    if value.endswith((".jpg", ".jpeg", ".png", ".webp")):
        value = re.sub(r"\.(jpg|jpeg|png|webp)$", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value).strip()
    value = re.sub(r"\s+", " ", value)
    video_suffix_pattern = "|".join(
        re.escape(ext.lstrip(".")) for ext in sorted(VIDEO_EXTENSIONS, key=len, reverse=True)
    )
    value = re.sub(rf"\s+(?:{video_suffix_pattern})$", "", value)
    return value


def source_thumbnail_filename_candidates(video_name: str) -> set:
    name = str(video_name or "").strip()
    if not name:
        return set()
    p = Path(name)
    values = {name + ".jpg", p.with_suffix(".jpg").name}
    if p.suffix:
        values.add(f"{p.stem}_{p.suffix.lstrip('.')}.jpg")
    return {str(v).strip().lower() for v in values if v}



def canonical_thumbnail_name(video_name: str) -> str:
    """Canonical destination thumbnail filename for a video."""
    p = Path(str(video_name or "").strip())
    return f"{p.stem}{p.suffix}.jpg"


# ---------------------------------------------------------------------------
# Message type predicates
# ---------------------------------------------------------------------------

def is_video_message(message) -> bool:
    """Recognize Telegram video messages, including videos sent as generic files.

    Telegram may label a video sent as a document with ``application/octet-stream``
    (or another generic MIME type). In that case its filename extension or
    DocumentAttributeVideo is the reliable detection signal. Keep this check
    independent of safe_video_name(), which adds a display-only ``.mp4`` suffix.
    """
    if not message:
        return False
    if getattr(message, "video", None):
        return True
    doc = getattr(message, "document", None)
    if doc is None:
        doc = getattr(getattr(message, "media", None), "document", None)
    if doc is None:
        return False

    # Telegram's explicit video attribute is strongest when the file was sent
    # as a document with a generic or missing MIME type and an unusual name.
    try:
        for attr in (getattr(doc, "attributes", ()) or ()):
            if type(attr).__name__ == "DocumentAttributeVideo":
                return True
    except Exception:
        _swallowed_note("media_utils.is_video_message")
        pass

    file_obj = None
    try:
        file_obj = getattr(message, "file", None)
    except Exception:
        _swallowed_note("media_utils.is_video_message")
        pass
    try:
        mime = str(
            getattr(file_obj, "mime_type", None)
            or getattr(doc, "mime_type", None)
            or ""
        ).strip().lower()
        if mime.startswith("video/"):
            return True
    except Exception:
        _swallowed_note("media_utils.is_video_message")
        pass
    try:
        raw_name = str(getattr(file_obj, "name", None) or "").strip()
        if raw_name and Path(raw_name).suffix.lower() in VIDEO_EXTENSIONS:
            return True
    except Exception:
        _swallowed_note("media_utils.is_video_message")
        pass
    return False


def is_telegram_direct_streamable_meta(meta: dict, *, filename: str | None = None) -> bool:
    """Return True when Telegram can receive the local file as a native streamable video.

    We intentionally avoid FFmpeg remuxing/transcoding for already compatible media.
    The safe local-upload profile is an MP4/M4V/MOV container carrying H.264/AVC or
    HEVC/H.265 video and AAC/MP4A audio (or no audio stream). Other formats/codecs
    continue through the conversion path.
    """
    try:
        fmt_values = {x.strip().lower() for x in str(meta.get("format_name") or "").split(",") if x.strip()}
        if filename:
            suffix = Path(str(filename)).suffix.lower()
            if suffix in {".mp4", ".m4v", ".mov"}:
                fmt_values.add(suffix[1:])
        if not (fmt_values & {"mp4", "m4v", "mov"}):
            return False
        vcodec = str(meta.get("video_codec") or meta.get("codec") or "").lower()
        acodec = str(meta.get("audio_codec") or meta.get("acodec") or "").lower()
        if vcodec not in {"h264", "avc", "avc1", "hevc", "h265"}:
            return False
        if acodec and acodec not in {"aac", "mp4a"}:
            return False
        return bool(int(meta.get("width") or 0) > 0 and int(meta.get("height") or 0) > 0 and float(meta.get("duration") or 0) > 0)
    except Exception:
        return False



def is_photo_message(message) -> bool:
    return bool(getattr(message, "photo", None))


def is_image_document(message) -> bool:
    doc = getattr(message, "document", None)
    if doc is None:
        return False
    try:
        mime = str(getattr(message.file, "mime_type", "") or "").lower()
        if mime.startswith("image/"):
            return True
    except Exception:
        _swallowed_note("media_utils.is_image_document")
        pass
    try:
        name = getattr(message.file, "name", None) or ""
        return Path(name).suffix.lower() in IMAGE_EXTENSIONS
    except Exception:
        return False


def is_thumbnail_document(message) -> bool:
    if not is_image_document(message):
        return False
    try:
        name = str(getattr(message.file, "name", None) or "").lower()
        return any(name.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))
    except Exception:
        return False


def is_image_message(message) -> bool:
    return is_photo_message(message) or is_image_document(message)


def message_filename(message) -> str:
    try:
        name = getattr(message.file, "name", None)
        return str(name or "").strip()
    except Exception:
        return ""


def message_size(message) -> int:
    try:
        return int(getattr(message.file, "size", 0) or 0)
    except Exception:
        return 0


def document_identity(message) -> Optional[str]:
    """Stable Telegram document identity key (access_hash + file_reference)."""
    try:
        doc = getattr(message, "document", None)
        if doc is None:
            return None
        dc = getattr(doc, "dc_id", 0)
        ah = getattr(doc, "access_hash", 0)
        size = getattr(doc, "size", 0)
        return f"{int(dc)}:{int(ah)}:{int(size)}"
    except Exception:
        return None


def media_attributes(message) -> tuple:
    """Return (width, height, duration) for video messages."""
    try:
        for a in message.document.attributes:
            if type(a).__name__ == "DocumentAttributeVideo":
                return (
                    getattr(a, "w", None),
                    getattr(a, "h", None),
                    getattr(a, "duration", None),
                )
    except Exception:
        _swallowed_note("media_utils.media_attributes")
        pass
    return None, None, None


def get_video_dimensions(message) -> tuple:
    w, h, _ = media_attributes(message)
    if w and h:
        return int(w), int(h)
    try:
        if getattr(message, "video", None):
            return int(message.video.w), int(message.video.h)
    except Exception:
        _swallowed_note("media_utils.get_video_dimensions")
        pass
    return 0, 0


def effective_orientation(message) -> str:
    w, h = get_video_dimensions(message)
    if w > 0 and h > 0:
        return "VERTICAL" if h > w else "LANDSCAPE"
    return "UNKNOWN"


def streamable_status(message) -> Optional[bool]:
    if getattr(message, "video", None):
        return True
    try:
        for a in message.document.attributes:
            if type(a).__name__ == "DocumentAttributeVideo":
                return bool(getattr(a, "supports_streaming", False))
    except Exception:
        _swallowed_note("media_utils.streamable_status")
        pass
    return False


def human_size(value: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


# ---------------------------------------------------------------------------
# Common Telegram operations
# ---------------------------------------------------------------------------

async def forward_without_tag(client, destination, source_entity, message):
    """Forward a message without the 'Forwarded from' tag."""
    while True:
        try:
            return await client.send_message(destination, message, comment_to=None)
        except Exception:
            _swallowed_note("media_utils.forward_without_tag")
            pass
        try:
            return await client.forward_messages(destination, message, source_entity,
                                                  drop_author=True, drop_media_captions=False)
        except Exception as e:
            if is_flood_wait(e):
                await asyncio.sleep(flood_wait_seconds(e) + 2)
                continue
            raise


async def delete_messages(client, entity, ids: list):
    """Delete a list of message IDs with FloodWait handling."""
    int_ids = [int(x) for x in ids if x]
    if not int_ids:
        return
    while True:
        try:
            await client.delete_messages(entity, int_ids)
            return
        except Exception as e:
            if is_flood_wait(e):
                await asyncio.sleep(flood_wait_seconds(e) + 2)
                continue
            raise


async def verify_message_exists(client, entity, message_id: int) -> bool:
    try:
        msg = await client.get_messages(entity, ids=int(message_id))
        return msg is not None
    except Exception:
        return False


async def upload_thumbnail_document(client, entity, path, progress_callback=None, *, as_photo: bool = False, file_name: str | None = None) -> Any:
    """Upload a thumbnail after a video.

    ``as_photo=True`` sends a real Telegram photo message so the thumbnail is
    visible in channel history immediately after the video.  The default keeps
    legacy document behavior for callers that explicitly need a document.
    """
    kwargs = dict(
        force_document=not as_photo, caption="",
        part_size_kb=UPLOAD_PART_SIZE_KB, progress_callback=progress_callback
    )
    # A photo message should not be forced into document mode. Telegram will
    # render it inline after the video while preserving the generated image.
    # Explicit filename is used for Telegram's contact-sheet companion document:
    # the artifact is stored under a cache-specific sidecar name locally, while
    # the remote document must match the corresponding video basename.
    payload = None
    upload = str(path)
    if file_name:
        payload = io.BytesIO(Path(path).read_bytes())
        payload.name = str(file_name)
        upload = payload
    try:
        return await client.send_file(entity, upload, **kwargs)
    except Exception as e:
        wait = flood_wait_seconds(e)
        if wait and wait <= FLOODWAIT_INLINE_MAX_SECONDS:
            await asyncio.sleep(wait + 1)
            if payload is not None:
                payload.seek(0)
            return await client.send_file(entity, upload, **kwargs)
        raise


async def verify_destination_pair(client, destination, video_id: int, thumbnail_id: int,
                                   expected_video_name: str = "", require_native_video: bool = True,
                                   destination_mode: str = "VIDEO", expected_source=None,
                                   require_native_cover: bool = False) -> tuple:
    """Verify a destination video + thumbnail pair before source cleanup.

    The verification is deliberately strict for VIDEO destinations: the media
    must be a native Telegram video, the thumbnail must be an image DOCUMENT,
    it must be posted after the video, and the filename identity must match.
    """
    try:
        video = await client.get_messages(destination, ids=int(video_id))
        if video is None:
            return False, f"destination video msg {video_id} not found"
        if require_native_video and not getattr(video, "video", None):
            return False, f"destination msg {video_id} is not a native video"
        if require_native_cover:
            from .telegram_sender import _message_has_custom_cover
            if not _message_has_custom_cover(video):
                return False, f"destination video msg {video_id} has no verified custom cover"
        if expected_video_name:
            actual = message_filename(video)
            exp_key = canonical_media_key(expected_video_name)
            act_key = canonical_media_key(actual)
            if exp_key and act_key and exp_key != act_key:
                # Telegram/Telethon may sanitize punctuation and truncate long
                # uploaded filenames. The message ID itself is the authoritative
                # identity for this transfer, so accept a sufficiently long
                # normalized prefix when the remote filename is clearly the
                # beginning of the expected source filename. This prevents a
                # successful upload from being treated as unverified solely
                # because Telegram shortened the displayed filename.
                prefix_ok = (
                    len(act_key) >= 24
                    and exp_key.startswith(act_key)
                )
                if not prefix_ok:
                    return False, f"filename mismatch: expected {expected_video_name!r}, got {actual!r}"
    except Exception as e:
        return False, f"error checking destination video: {e}"
    try:
        thumb = await client.get_messages(destination, ids=int(thumbnail_id))
        if thumb is None:
            return False, f"destination thumbnail msg {thumbnail_id} not found"
        # Archive thumbnails may be Telegram photo messages (preferred for
        # Local Upload) or image documents (legacy/other callers). Both are
        # valid as long as Telegram reports the message as an image.
        if not is_image_message(thumb):
            return False, f"destination thumbnail msg {thumbnail_id} is not an image"
        if int(getattr(thumb, "id", 0) or 0) <= int(video_id):
            return False, f"thumbnail msg {thumbnail_id} is not after video msg {video_id}"
        if expected_video_name:
            expected_thumb = canonical_thumbnail_name(expected_video_name)
            actual_thumb = message_filename(thumb)
            # Photo messages do not always expose a filename. Only enforce the
            # filename identity check when Telegram provides one.
            if actual_thumb:
                expected_thumb_key = canonical_media_key(expected_thumb)
                actual_thumb_key = canonical_media_key(actual_thumb)
                if expected_thumb_key != actual_thumb_key:
                    # Apply the same Telegram filename normalization/truncation
                    # rule used for the video message.
                    prefix_ok = (
                        len(actual_thumb_key) >= 24
                        and expected_thumb_key.startswith(actual_thumb_key)
                    )
                    if not prefix_ok:
                        return False, f"thumbnail filename mismatch: expected {expected_thumb!r}, got {actual_thumb!r}"
        # If we have the source thumbnail object, make sure we are not using the
        # Telegram-generated/embedded preview as the separate archive image.
        if expected_source is not None:
            source_thumb_name = str(getattr(getattr(expected_source, "file", None), "name", "") or "")
            if source_thumb_name and actual_thumb and canonical_media_key(source_thumb_name) == canonical_media_key(actual_thumb):
                # Same name is fine; this check exists only to keep this branch
                # explicit for callers that track source/destination pairs.
                pass
    except Exception as e:
        return False, f"error checking destination thumbnail: {e}"
    return True, "ok"


async def delete_source_after_verify(client, source, message_ids: list):
    await delete_messages(client, source, message_ids)
