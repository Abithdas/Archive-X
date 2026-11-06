"""Validation helpers for image data URLs submitted by the web UI."""
from __future__ import annotations

import base64
import re

from fastapi import HTTPException


def parse_manual_image_payload(data: dict) -> tuple[bytes, str]:
    raw = str(data.get("image_data") or "").strip()
    if not raw:
        return b"", ""
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", raw, re.S)
    if not match:
        raise HTTPException(400, "Image upload must be a valid data URL")
    mime = match.group(1).lower()
    if mime not in {"image/jpeg", "image/png", "image/webp", "image/gif", "image/svg+xml"}:
        raise HTTPException(400, "Unsupported image type")
    try:
        image_bytes = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise HTTPException(400, f"Invalid image data: {exc}")
    if not image_bytes:
        raise HTTPException(400, "Image is empty")
    if len(image_bytes) > 8 * 1024 * 1024:
        raise HTTPException(413, "Image must be 8 MiB or smaller")
    return image_bytes, mime
