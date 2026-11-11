"""Display-size copies of stored images (performer, studio, channel and video artwork).

The stored image is the master and is never changed. A request may ask for a smaller copy with ``?w=<width>``; the copy is made from the
master (never from another copy) with Lanczos, is never larger than the master, and is cached on disk by the master's content hash, so
a changed master automatically gets new copies. Animated images and anything that cannot be decoded are served unchanged."""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path

from .. import config

# Widths a page may ask for. A fixed list keeps the number of cached copies small and the endpoint cheap to abuse-proof.
ALLOWED_WIDTHS = (160, 240, 320, 480, 640, 960)
QUALITY = 86
_MAX_PIXELS = 60_000_000


def requested_width(value: object) -> int:
    """The allowed width asked for, or 0 for "serve the master"."""
    try:
        width = int(str(value))
    except (TypeError, ValueError):
        return 0
    return width if width in ALLOWED_WIDTHS else 0


def _cache_path(master: bytes, width: int, ext: str) -> Path:
    digest = hashlib.sha1(master).hexdigest()
    return Path(config.CACHE_DIR) / "image_derivatives" / digest[:2] / f"{digest}.w{width}.{ext}"


def reduce_bytes(master: bytes, width: int) -> tuple[bytes, str] | None:
    """A copy of ``master`` that is ``width`` pixels wide, or None when the master should be served as it is
    (already that small or smaller, animated, not a decodable bitmap, or implausibly large)."""
    from PIL import Image, ImageOps

    try:
        with Image.open(io.BytesIO(master)) as source:
            if getattr(source, "is_animated", False) or source.width * source.height > _MAX_PIXELS:
                return None
            if source.width <= width:
                return None   # never enlarge, and a copy of the same size would only lose quality
            image = ImageOps.exif_transpose(source)
            has_alpha = "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)
            height = max(1, round(image.height * width / image.width))
            if has_alpha:
                # Reduce in premultiplied-alpha space so invisible colour in transparent pixels cannot bleed into the edges.
                reduced = image.convert("RGBA").convert("RGBa").resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")
                out = io.BytesIO()
                reduced.save(out, format="PNG", optimize=True)
                return out.getvalue(), "image/png"
            rgb = image.convert("RGB") if image.mode != "RGB" else image
            reduced = rgb.resize((width, height), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            reduced.save(out, format="JPEG", quality=QUALITY, optimize=True, progressive=True)
            return out.getvalue(), "image/jpeg"
    except Exception:
        return None


def derive(master: bytes, mime: str, width: int) -> tuple[bytes, str]:
    """The bytes and MIME type to serve for ``width`` (0 means the master). Safe to call from a worker thread."""
    if not width or not master:
        return master, mime
    for ext in ("jpg", "png"):
        cached = _cache_path(master, width, ext)
        try:
            if cached.is_file() and cached.stat().st_size > 64:
                return cached.read_bytes(), "image/png" if ext == "png" else "image/jpeg"
        except OSError:
            pass
    made = reduce_bytes(master, width)
    if made is None:
        return master, mime
    data, out_mime = made
    target = _cache_path(master, width, "png" if out_mime == "image/png" else "jpg")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=target.stem + ".", suffix=".part", dir=str(target.parent))
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(name, target)
    except OSError:
        pass   # serving the copy matters more than caching it
    return data, out_mime
