"""Validation helpers for explicit, permission-checked Telegram channel sync."""
from __future__ import annotations

import io


def has_change_info_permission(participant) -> bool:
    """Whether a Telegram participant is creator or has change-info admin rights."""
    if participant is None:
        return False
    name = type(participant).__name__.casefold()
    if bool(getattr(participant, "is_creator", False)) or "participantcreator" in name:
        return True
    # Telethon's get_permissions() returns custom.ParticipantPermissions, whose
    # change_info flag is exposed directly (not under admin_rights). Raw MTProto
    # participant objects can instead expose the flag in admin_rights.
    direct_change_info = getattr(participant, "change_info", None)
    if direct_change_info is not None:
        return bool(direct_change_info)
    rights = getattr(participant, "admin_rights", None)
    return bool(getattr(rights, "change_info", False))


def normalize_telegram_photo(image_bytes: bytes, *, max_bytes: int = 10 * 1024 * 1024) -> bytes:
    """Validate a supported raster image and normalize it to JPEG for Telegram."""
    from PIL import Image

    raw = bytes(image_bytes or b"")
    if not raw:
        raise ValueError("No local artwork bytes were supplied")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = str(probe.format or "").upper()
            width, height = probe.size
            if fmt not in {"JPEG", "PNG", "WEBP", "GIF"}:
                raise ValueError("Upload a PNG, JPEG, WebP, or GIF image")
            if width <= 0 or height <= 0 or width > 12000 or height > 12000 or width * height > 40_000_000:
                raise ValueError("Image dimensions exceed the 40-megapixel safety limit")
            probe.verify()
        with Image.open(io.BytesIO(raw)) as source:
            output = io.BytesIO()
            source.convert("RGB").save(output, format="JPEG", quality=92, optimize=True)
            normalized = output.getvalue()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Uploaded artwork is not a valid supported image") from exc
    if not normalized or len(normalized) > int(max_bytes):
        raise ValueError("Normalized Telegram photo exceeds the 10 MiB limit")
    return normalized
