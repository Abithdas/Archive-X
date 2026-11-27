"""Durable local storage for indexed Telegram photos.

The database stores the Telegram channel/message identity, a relative local path,
and a content digest. Original bytes are cached under the Archive X user-data
folder so gallery views and face analysis do not repeatedly download media.
"""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .. import config


def photo_key(channel_id: str, message_id: int) -> str:
    return hashlib.sha256(f"{str(channel_id)}:{int(message_id)}".encode("utf-8")).hexdigest()


def photo_original_path(channel_id: str, message_id: int) -> Path:
    return Path(config.CACHE_DIR) / "photo_library" / "originals" / f"{photo_key(channel_id, message_id)}.bin"


# The Photos grid is width-driven (masonry columns up to ~280 CSS px, so ~560 px on a 2x screen) and portrait photos are tall,
# so the thumbnail box is limited by width first. Photos smaller than the box are never enlarged.
THUMBNAIL_BOX = (640, 960)
PREVIEW_BOX = (1440, 1440)
THUMBNAIL_QUALITY = 84
PREVIEW_QUALITY = 88


def photo_thumbnail_path(channel_id: str, message_id: int) -> Path:
    # ".w640" marks the current thumbnail size; older 480x360 thumbnails (plain ".jpg") are regenerated from the original on demand.
    return Path(config.CACHE_DIR) / "photo_library" / "thumbnails" / f"{photo_key(channel_id, message_id)}.w640.jpg"


def legacy_photo_thumbnail_path(channel_id: str, message_id: int) -> Path:
    return Path(config.CACHE_DIR) / "photo_library" / "thumbnails" / f"{photo_key(channel_id, message_id)}.jpg"


def render_jpeg_derivative(source_path: Path, target: Path, box: tuple[int, int], quality: int, background: tuple[int, int, int] = (28, 32, 40)) -> None:
    """Write a reduced JPEG of the original (never of another derivative), atomically.

    Transparent images are reduced in premultiplied-alpha space and only then flattened, so invisible colour data in
    transparent pixels cannot leak into a dark or coloured fringe along the edges."""
    import os
    import tempfile
    from PIL import Image, ImageOps

    from ..core.imaging import open_reduced
    with open_reduced(source_path, box) as source:   # a very large JPEG is decoded at a reduced size straight away
        image = ImageOps.exif_transpose(source)
        image.seek(0)
        has_alpha = "A" in image.getbands() or image.mode in ("P", "LA", "PA") and "transparency" in image.info
        if has_alpha:
            rgba = image.convert("RGBA")
            premultiplied = rgba.convert("RGBa")
            premultiplied.thumbnail(box, Image.Resampling.LANCZOS)
            reduced = premultiplied.convert("RGBA")
            flat = Image.new("RGB", reduced.size, background)
            flat.paste(reduced, mask=reduced.getchannel("A"))
            image = flat
        else:
            image = image.convert("RGB") if image.mode != "RGB" else image
            image.thumbnail(box, Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=target.stem + ".", suffix=".part.jpg", dir=str(target.parent))
        os.close(fd)
        temp = Path(temp_name)
        try:
            image.save(temp, format="JPEG", quality=quality, optimize=True, progressive=True)
            if temp.stat().st_size <= 512:
                raise RuntimeError("Generated image is empty")
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)


HUGE_PREVIEW_BOX = (2880, 2880)
HUGE_PREVIEW_QUALITY = 90
HUGE_AT_PIXELS = 24_000_000


def preview_box_for(path: Path, box: tuple[int, int] = PREVIEW_BOX, quality: int = PREVIEW_QUALITY) -> tuple[tuple[int, int], int]:
    """The preview size and quality for this original: the usual 1440 px, or 2880 px at a finer quality when it is 24 megapixels or more (the viewer shows the
    preview of such a picture instead of its original, which a browser cannot decode comfortably)."""
    width, height = _dimensions(path)
    return ((HUGE_PREVIEW_BOX, HUGE_PREVIEW_QUALITY) if width * height >= HUGE_AT_PIXELS else (box, quality))


def photo_preview_path(channel_id: str, message_id: int) -> Path:
    """Durable medium-resolution preview for responsive full-screen navigation."""
    return Path(config.CACHE_DIR) / "photo_library" / "previews" / f"{photo_key(channel_id, message_id)}.jpg"


def cache_path_for_database(path: Path) -> str:
    resolved = path.resolve()
    root = Path(config.USER_DATA_DIR).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        # The cache should normally live under USER_DATA_DIR; absolute paths
        # remain supported for deployments that deliberately relocate it.
        return str(resolved)


# Folders the person added as local photo sources (Settings, Local files). A photo of theirs is read where it lies, so the folder is
# an allowed place besides the app's own trees. Kept in step by web/routes_local.py and loaded at start-up.
_LOCAL_ROOTS: set[str] = set()


def set_local_roots(paths) -> None:
    global _LOCAL_ROOTS
    _LOCAL_ROOTS = {os.path.realpath(str(p)) for p in paths if str(p).strip()}


def _in_local_root(resolved: Path) -> bool:
    text = os.path.realpath(str(resolved))
    return any(text.startswith(root + os.sep) for root in _LOCAL_ROOTS)


def resolve_database_cache_path(value: str | None) -> Path | None:
    """Resolve only paths owned by the app's user-data/cache trees."""
    text = str(value or "").strip()
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path(config.USER_DATA_DIR) / candidate
    try:
        resolved = candidate.resolve()
        user_root = Path(config.USER_DATA_DIR).resolve()
        cache_root = Path(config.CACHE_DIR).resolve()
        if resolved.is_relative_to(user_root) or resolved.is_relative_to(cache_root) or _in_local_root(resolved):
            return resolved
        # Reject arbitrary absolute paths from stale or manually edited DB rows.
        return None
    except (OSError, RuntimeError):
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dimensions(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image, ImageOps
        from ..core.imaging import allow_large_images
        allow_large_images()
        with Image.open(path) as source:
            # the header is enough for the size; only a picture that carries a rotation needs its sides swapped
            width, height = int(source.width), int(source.height)
            try:
                if source.getexif().get(0x0112, 1) in (5, 6, 7, 8):
                    width, height = height, width
            except Exception:
                pass
            return width, height
    except Exception:
        return 0, 0


def inspect_cached_file(path: Path, *, fallback_name: str = "") -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("Local photo cache file is missing or empty")
    size = int(path.stat().st_size)
    digest = _sha256(path)
    width, height = _dimensions(path)
    mime = mimetypes.guess_type(fallback_name or path.name)[0] or "image/jpeg"
    if not mime.startswith("image/"):
        mime = "image/jpeg"
    return {
        "path": path.resolve(),
        "local_path": cache_path_for_database(path),
        "sha256": digest,
        "size": size,
        "width": width,
        "height": height,
        "mime_type": mime,
        "cached_at": config.now_iso(),
    }


def cache_local_source_file(
    source_path: str | Path,
    channel_id: str,
    message_id: int,
    *,
    expected_sha256: str = "",
    filename: str = "",
) -> dict[str, Any]:
    """Atomically copy a verified upload source to its durable library cache."""
    source = Path(source_path).resolve()
    if not source.is_file() or source.stat().st_size <= 0:
        raise RuntimeError("The verified upload source is no longer available to cache")
    source_digest = _sha256(source)
    expected = str(expected_sha256 or "").strip().lower()
    if expected and source_digest != expected:
        raise RuntimeError("The local source no longer matches the verified Telegram content")

    target = photo_original_path(str(channel_id), int(message_id))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 0:
        if _sha256(target) == source_digest:
            return inspect_cached_file(target, fallback_name=filename or source.name)

    if source == target.resolve():
        return inspect_cached_file(target, fallback_name=filename or source.name)

    fd, temp_name = tempfile.mkstemp(prefix=target.stem + ".", suffix=".part", dir=str(target.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as inp:
            shutil.copyfileobj(inp, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if temp.stat().st_size != source.stat().st_size or _sha256(temp) != source_digest:
            raise RuntimeError("Cached photo bytes do not match the verified source")
        temp.replace(target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return inspect_cached_file(target, fallback_name=filename or source.name)


async def cache_telegram_message(client: Any, message: Any, channel_id: str, message_id: int) -> dict[str, Any]:
    """Download an indexed Telegram image once, atomically retaining it locally."""
    target = photo_original_path(str(channel_id), int(message_id))
    target.parent.mkdir(parents=True, exist_ok=True)
    filename = str(getattr(getattr(message, "file", None), "name", "") or f"photo_{int(message_id)}.jpg")
    if target.is_file() and target.stat().st_size > 0:
        return await asyncio.to_thread(inspect_cached_file, target, fallback_name=filename)

    fd, temp_name = tempfile.mkstemp(prefix=target.stem + ".download.", suffix=".part", dir=str(target.parent))
    os.close(fd)
    temp = Path(temp_name)
    try:
        downloaded = await client.download_media(message, file=str(temp))
        actual = Path(str(downloaded)) if isinstance(downloaded, (str, Path)) and str(downloaded) else temp
        if not actual.is_file() or actual.stat().st_size <= 0:
            raise RuntimeError("Telegram returned an empty image download")
        # Some clients return a path other than the requested target path.
        if actual.resolve() != temp.resolve():
            await asyncio.to_thread(shutil.copyfile, actual, temp)
        if temp.stat().st_size <= 0:
            raise RuntimeError("Downloaded photo is empty")
        temp.replace(target)
        return await asyncio.to_thread(inspect_cached_file, target, fallback_name=filename)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def record_photo_cache(db, channel_id: str, message_id: int, info: dict[str, Any]) -> None:
    """Persist local-cache metadata against the exact Telegram message row."""
    now = config.now_iso()
    with db.lock:
        db.conn.execute(
            """UPDATE photo_library_items SET local_path=?,local_sha256=?,cached_at=?,
                      content_hash=CASE WHEN ?<>'' THEN ? ELSE content_hash END,
                      size=CASE WHEN ?>0 THEN ? ELSE size END,
                      width=CASE WHEN ?>0 THEN ? ELSE width END,
                      height=CASE WHEN ?>0 THEN ? ELSE height END,
                      mime_type=CASE WHEN ?<>'' THEN ? ELSE mime_type END,
                      updated_at=? WHERE channel_id=? AND message_id=?""",
            (str(info.get("local_path") or ""), str(info.get("sha256") or ""),
             str(info.get("cached_at") or now), str(info.get("sha256") or ""), str(info.get("sha256") or ""),
             int(info.get("size") or 0), int(info.get("size") or 0),
             int(info.get("width") or 0), int(info.get("width") or 0),
             int(info.get("height") or 0), int(info.get("height") or 0),
             str(info.get("mime_type") or ""), str(info.get("mime_type") or ""), now,
             str(channel_id), int(message_id)),
        )
        db.conn.commit()
