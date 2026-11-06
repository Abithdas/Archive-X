"""Shared thumbnail-selection and quality policy used by Telegram media flows."""
from __future__ import annotations

from pathlib import Path


def validate_image(path: str | Path, min_width: int = 320, min_height: int = 180) -> tuple[bool, str]:
    """Return (valid, reason) without trusting file extension alone."""
    p = Path(path)
    if not p.is_file() or p.stat().st_size < 1024:
        return False, "thumbnail file is missing or too small"
    try:
        from PIL import Image
        with Image.open(p) as im:
            w, h = im.size
            if w < min_width or h < min_height:
                return False, f"thumbnail resolution too small: {w}x{h}"
            im.verify()
    except Exception as exc:
        return False, f"invalid image: {type(exc).__name__}: {exc}"
    return True, "ok"
