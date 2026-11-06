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


def photo_thumbnail_path(channel_id: str, message_id: int) -> Path:
    return Path(config.CACHE_DIR) / "photo_library" / "thumbnails" / f"{photo_key(channel_id, message_id)}.jpg"


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
        if resolved.is_relative_to(user_root) or resolved.is_relative_to(cache_root):
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
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            return int(image.width), int(image.height)
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
