"""Verified Telegram message moves with copy-before-delete safety.

Split groups are copied and verified in full before any source message is
removed. This module intentionally has no HTTP/UI dependency so destructive
boundaries remain testable with a fake Telegram client.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any


def _media_file(message: Any):
    return getattr(message, "file", None)


def _media_size(message: Any) -> int:
    try:
        return int(getattr(_media_file(message), "size", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _media_name(message: Any) -> str:
    return str(getattr(_media_file(message), "name", None) or "").strip()


def _document(message: Any):
    document = getattr(message, "document", None)
    if document is None:
        document = getattr(getattr(message, "media", None), "document", None)
    return document


def _photo(message: Any):
    photo = getattr(message, "photo", None)
    if photo is None:
        photo = getattr(getattr(message, "media", None), "photo", None)
    return photo


def _is_media_message(message: Any) -> bool:
    """Accept Library video/document messages and photo companions."""
    if message is None:
        return False
    return bool(getattr(message, "video", None) or _document(message) or _photo(message))


def _identity(message: Any) -> dict:
    document = _document(message)
    photo = _photo(message)
    reference = bytes(getattr(document, "file_reference", b"") or b"") if document else b""
    media_kind = "document" if document else ("photo" if photo else ("video" if getattr(message, "video", None) else ""))
    return {
        "media_kind": media_kind,
        "document_id": int(getattr(document, "id", 0) or 0) if document else None,
        "photo_id": int(getattr(photo, "id", 0) or 0) if photo else None,
        "access_hash": str(getattr(document, "access_hash", "") or ""),
        "file_reference_b64": base64.b64encode(reference).decode("ascii") if reference else "",
        "caption": str(getattr(message, "message", None) or ""),
        "date": getattr(getattr(message, "date", None), "isoformat", lambda: "")(),
        "mime": str(getattr(getattr(message, "file", None), "mime_type", "") or ""),
    }


def _normal_name(value: str) -> str:
    return "".join(ch for ch in str(value).casefold() if ch.isalnum())


def _matches_name(original: str, copied: str) -> bool:
    if not original or not copied or original.casefold() == copied.casefold():
        return True
    a, b = _normal_name(original), _normal_name(copied)
    # Telegram can normalize/truncate long document names; reject meaningful
    # differences while retaining the existing long-prefix compatibility.
    return a == b or (len(b) >= 24 and a.startswith(b)) or (len(a) >= 24 and b.startswith(a))


async def copy_message_verified(client, source, destination, message_id: int, *, expected_name: str = "") -> dict:
    """Copy one media message and verify it, without deleting the source."""
    source_message = await client.get_messages(source, ids=int(message_id))
    if not source_message or not _is_media_message(source_message):
        raise ValueError(f"Source message {message_id} is missing or is not media")
    original_size = _media_size(source_message)
    original_name = _media_name(source_message)
    original_identity = _identity(source_message)
    if expected_name and Path(original_name).name.casefold() != Path(expected_name).name.casefold():
        raise ValueError(f"Source split-volume filename mismatch for message {message_id}: expected {expected_name!r}, got {original_name!r}")

    forwarded = await client.forward_messages(destination, source_message, from_peer=source, drop_author=True)
    if isinstance(forwarded, (list, tuple)):
        forwarded = next((x for x in forwarded if x is not None), None)
    destination_id = int(getattr(forwarded, "id", 0) or 0)
    if destination_id <= 0:
        raise RuntimeError("Telegram did not return a destination message ID")

    try:
        copied = await client.get_messages(destination, ids=destination_id)
        if not _is_media_message(copied):
            raise RuntimeError("Destination copy could not be verified as media; source was preserved")
        copied_size = _media_size(copied)
        if original_size and copied_size and original_size != copied_size:
            raise RuntimeError(f"Destination size mismatch ({copied_size} != {original_size}); source was preserved")
        copied_identity = _identity(copied)
        original_kind = original_identity.get("media_kind")
        copied_kind = copied_identity.get("media_kind")
        if original_kind and copied_kind != original_kind:
            raise RuntimeError("Destination media type mismatch; source was preserved")
        if original_identity.get("document_id") and str(copied_identity.get("document_id") or "") != str(original_identity["document_id"]):
            raise RuntimeError("Destination document ID missing or mismatched; source was preserved")
        if original_identity.get("photo_id") and str(copied_identity.get("photo_id") or "") != str(original_identity["photo_id"]):
            raise RuntimeError("Destination photo ID missing or mismatched; source was preserved")
        copied_name = _media_name(copied)
        if not _matches_name(original_name, copied_name):
            raise RuntimeError("Destination filename mismatch; source was preserved")
        if expected_name and Path(copied_name).name.casefold() != Path(expected_name).name.casefold():
            raise RuntimeError(f"Destination split-volume filename mismatch: expected {expected_name!r}, got {copied_name!r}")
    except Exception:
        # If verification fails, remove only the destination object created by
        # this attempt. Keep the source untouched for safety.
        try:
            await client.delete_messages(destination, [destination_id])
        except Exception:
            pass
        raise

    return {
        "source_message_id": int(message_id),
        "destination_message_id": destination_id,
        "size": copied_size or original_size,
        "filename": copied_name or original_name,
        "source_deleted": False,
        "destination_verified": True,
        "deletion_error": "",
        "media_kind": copied_identity.get("media_kind") or original_identity.get("media_kind"),
        "document_id": copied_identity.get("document_id") or original_identity.get("document_id"),
        "photo_id": copied_identity.get("photo_id") or original_identity.get("photo_id"),
        "access_hash": copied_identity.get("access_hash") or original_identity.get("access_hash"),
        "file_reference_b64": copied_identity.get("file_reference_b64") or original_identity.get("file_reference_b64"),
        "caption": copied_identity.get("caption") or original_identity.get("caption"),
        "date": copied_identity.get("date") or original_identity.get("date"),
        "mime": copied_identity.get("mime") or original_identity.get("mime"),
    }


async def delete_verified_source(
    client, source, message_id: int, *,
    expected_document_id: str | int | None = None,
    expected_photo_id: str | int | None = None,
) -> dict:
    """Delete only when the source media identity still matches a verified copy."""
    message_id = int(message_id)
    try:
        source_message = await client.get_messages(source, ids=message_id)
        if source_message is None:
            return {"source_message_id": message_id, "source_deleted": True, "deletion_error": ""}
        if expected_document_id:
            actual = str(getattr(_document(source_message), "id", "") or "")
            matched = actual == str(expected_document_id)
        elif expected_photo_id:
            actual = str(getattr(_photo(source_message), "id", "") or "")
            matched = actual == str(expected_photo_id)
        else:
            matched = False
        if not matched:
            return {
                "source_message_id": message_id, "source_deleted": False,
                "deletion_error": "Source media identity changed or is unavailable; automatic deletion was refused",
            }
        await client.delete_messages(source, [message_id])
        remaining = await client.get_messages(source, ids=message_id)
        deleted = remaining is None
        return {
            "source_message_id": message_id, "source_deleted": deleted,
            "deletion_error": "" if deleted else "Telegram returned the source message after deletion",
        }
    except Exception as exc:
        return {"source_message_id": message_id, "source_deleted": False,
                "deletion_error": f"{type(exc).__name__}: {exc}"}


async def move_message_verified(client, source, destination, message_id: int) -> dict:
    """Forward media, verify destination, then delete and verify the source."""
    copied = await copy_message_verified(client, source, destination, message_id)
    deletion = await delete_verified_source(
        client, source, message_id,
        expected_document_id=copied.get("document_id"),
        expected_photo_id=copied.get("photo_id"),
    )
    return {**copied, **deletion}


async def move_split_group_verified(client, source, destination, split_group: dict) -> dict:
    """Move an entire split group atomically from the user's perspective.

    Every volume and optional contact sheet is copied and verified before any
    source deletion is attempted. If a copy fails, newly created destination
    copies are best-effort removed and all source messages remain untouched.
    """
    group = dict(split_group or {})
    parts = sorted((dict(row) for row in group.get("parts") or []), key=lambda row: int(row.get("part_index") or 0))
    logical = Path(str(group.get("logical_filename") or "")).name
    expected_indices = list(range(1, len(parts) + 1))
    indices = [int(row.get("part_index") or 0) for row in parts]
    if not logical or not parts or indices != expected_indices:
        raise ValueError("Split group is incomplete; refusing to move individual volumes")
    expected_total = sum(int(row.get("size_bytes") or 0) for row in parts)
    if any(int(row.get("size_bytes") or 0) <= 0 or int(row.get("message_id") or 0) <= 0 for row in parts):
        raise ValueError("Split group contains a missing or empty volume")
    if expected_total != int(group.get("total_size") or 0):
        raise ValueError("Split group total size is inconsistent; refusing to move it")
    if int(parts[0]["message_id"]) != int(group.get("primary_message_id") or 0):
        raise ValueError("Split group primary message is not volume 001")

    copied_parts: list[dict] = []
    copied_companions: list[dict] = []
    try:
        for part in parts:
            index = int(part["part_index"])
            from ..core.split_files import split_part_expected_name
            expected_name = split_part_expected_name(logical, index, str(part.get("telegram_filename") or ""))
            if Path(str(part.get("telegram_filename") or "")).name != expected_name:
                raise ValueError(f"Registered volume {index:03d} has an unexpected filename")
            copied = await copy_message_verified(
                client, source, destination, int(part["message_id"]), expected_name=expected_name
            )
            if int(copied.get("size") or 0) != int(part["size_bytes"]):
                raise RuntimeError(f"Destination volume {index:03d} size did not match the registered size")
            copied.update({"part_index": index, "logical_filename": logical,
                           "telegram_filename": expected_name, "size_bytes": int(part["size_bytes"])})
            copied_parts.append(copied)
    except Exception:
        # Sources have not been deleted yet. Remove only destination messages
        # created by this attempt; failures to remove these are harmless orphans
        # and are explicitly logged by the caller.
        new_ids = [int(row.get("destination_message_id") or 0) for row in copied_parts + copied_companions]
        new_ids = [mid for mid in new_ids if mid > 0]
        if new_ids:
            try:
                await client.delete_messages(destination, new_ids)
            except Exception:
                pass
        raise

    # The contact sheet is a companion: failing to copy it must not block the
    # video move, and it must not be left behind in the source channel.
    thumb_id = int(group.get("thumbnail_message_id") or 0)
    thumbnail_warning = ""
    orphan_thumb_id = 0
    if thumb_id > 0:
        try:
            copied_companions.append(await copy_message_verified(client, source, destination, thumb_id))
        except Exception as exc:
            thumbnail_warning = f"Contact sheet could not be copied ({type(exc).__name__}: {exc})"
            orphan_thumb_id = thumb_id

    # Full destination group is verified. Only now attempt source deletion.
    part_deletions = []
    for part in copied_parts:
        deletion = await delete_verified_source(
            client, source, int(part["source_message_id"]),
            expected_document_id=part.get("document_id"),
        )
        part_deletions.append({**part, **deletion})
    companion_deletions = []
    for companion in copied_companions:
        deletion = await delete_verified_source(
            client, source, int(companion["source_message_id"]),
            expected_document_id=companion.get("document_id"),
            expected_photo_id=companion.get("photo_id"),
        )
        companion_deletions.append({**companion, **deletion})
    if orphan_thumb_id and all(row.get("source_deleted") for row in part_deletions):
        try:
            await client.delete_messages(source, [orphan_thumb_id])
            gone = await client.get_messages(source, ids=orphan_thumb_id) is None
            thumbnail_warning += "; it was removed from the source channel" if gone else "; it is still in the source channel"
        except Exception as exc:
            thumbnail_warning += f"; removing it from the source channel failed: {type(exc).__name__}"
    return {
        "thumbnail_warning": thumbnail_warning,
        "group_id": str(group.get("group_id") or ""),
        "logical_filename": logical,
        "total_size": expected_total,
        "parts": part_deletions,
        "thumbnail": companion_deletions[0] if companion_deletions else None,
        "primary_message_id": int(part_deletions[0]["destination_message_id"]),
        "all_destinations_verified": True,
        "source_deletion_pending": any(not row.get("source_deleted") for row in part_deletions + companion_deletions),
    }


def _canonical_stem(name: str) -> str:
    from .media_utils import canonical_media_key
    return canonical_media_key(Path(str(name or "")).name)


async def discover_companion_thumbnail(client, source, video_message_id: int, video_name: str = "",
                                       *, look_ahead: int = 3) -> int:
    """Find the contact-sheet/thumbnail posted right after a video in ``source``.

    Archive X (and the media-manager uploader before it) posts the contact
    sheet as the message immediately after its video. Older videos and ones
    sent from other tools therefore often have no saved association, which is
    why moving them left the thumbnail behind in the old channel.

    Only accept an image that follows the video before any other media and
    either carries the video's filename (``<video>.jpg``) or, for Telegram
    photo messages that have no filename, is the very next message.
    """
    video_message_id = int(video_message_id or 0)
    if video_message_id <= 0:
        return 0
    from .media_utils import is_image_message, message_filename
    ids = [video_message_id + offset for offset in range(1, max(1, int(look_ahead)) + 1)]
    try:
        messages = await client.get_messages(source, ids=ids)
    except Exception:
        return 0
    if not isinstance(messages, (list, tuple)):
        messages = [messages]
    video_key = _canonical_stem(video_name)
    first_existing = True
    for message in messages:
        if message is None:
            continue  # deleted gap
        if not is_image_message(message):
            return 0  # the next item (another video/text) starts; no companion
        name = message_filename(message)
        if name:
            if video_key and _canonical_stem(name) == video_key:
                return int(getattr(message, "id", 0) or 0)
            return 0
        if first_existing:
            return int(getattr(message, "id", 0) or 0)
        return 0
    return 0
