"""Local folders as photo sources: walk a folder, index its images like a channel's photos, keep the index equal to the disk.

A local folder is a row in ``channels`` (telegram_id ``local:<id>``) plus a row in ``local_sources``. Its photos are rows of
``photo_library_items`` with ``source_kind='local'`` and ``local_path`` = the file; the file is read where it lies and is never copied.
Videos are counted here and indexed by the later video step.
"""
from __future__ import annotations

import mimetypes
import os
import threading
import time
from pathlib import Path

from .. import config
from ..core.safe_paths import inside
from ..core.swallowed import note as _swallowed_note

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v", ".webm", ".ts", ".mpg", ".mpeg", ".flv", ".m2ts"}
SOURCE_KIND = "local"
MAX_FILES = 500_000


def channel_id(source_id: int) -> str:
    return f"local:{int(source_id)}"


def is_local_channel(value: str) -> bool:
    return str(value or "").startswith("local:")


def source_id_of(channel: str) -> int | None:
    try:
        return int(str(channel).split(":", 1)[1]) if is_local_channel(channel) else None
    except (IndexError, ValueError):
        return None


def _skip_dir(name: str) -> bool:
    return name.startswith(".") or name.startswith("$") or name.lower() in {"system volume information", "node_modules", "__pycache__"}


def walk_files(root: Path, *, recursive: bool, excluded: list[str]):
    """Yield (path, kind) for every image or video under ``root``, leaving out excluded subfolders and hidden or system ones."""
    root = Path(os.path.realpath(root))
    out = [os.path.realpath(os.path.join(root, e)) for e in excluded if str(e).strip()]
    count = 0
    stack = [str(root)]
    while stack:
        here = stack.pop()
        try:
            entries = list(os.scandir(here))
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    full = os.path.realpath(e.path)
                    if recursive and not _skip_dir(e.name) and not any(full == x or full.startswith(x + os.sep) for x in out):
                        stack.append(e.path)
                elif e.is_file(follow_symlinks=False):
                    ext = os.path.splitext(e.name)[1].lower()
                    kind = "image" if ext in IMAGE_EXTENSIONS else "video" if ext in VIDEO_EXTENSIONS else ""
                    if kind and not e.name.startswith("."):
                        count += 1
                        if count > MAX_FILES:
                            return
                        yield e.path, kind
            except OSError:
                continue


def _image_info(path: str) -> tuple[int, int, str]:
    from PIL import Image
    with Image.open(path) as im:   # opens the header only
        w, h = im.size
        fmt = (im.format or "").lower()
    mime = {"jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp", "tiff": "image/tiff"}.get(fmt) or mimetypes.guess_type(path)[0] or "image/jpeg"
    return int(w), int(h), mime


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))


_progress: dict[int, dict] = {}
_progress_lock = threading.Lock()


def progress(source_id: int) -> dict:
    with _progress_lock:
        return dict(_progress.get(int(source_id), {}))


def scan_source(db, source_id: int) -> dict:
    """Bring the index of one folder in line with the disk. Returns the counts. Safe to run again at any time."""
    sid = int(source_id)
    with db.reading() as conn:
        row = conn.execute("SELECT path,recursive,excluded FROM local_sources WHERE id=?", (sid,)).fetchone()
    if not row:
        raise KeyError(f"no local folder {sid}")
    import json
    root = Path(row["path"])
    ch = channel_id(sid)
    now = config.now_iso()
    with _progress_lock:
        _progress[sid] = {"running": True, "seen": 0, "added": 0, "started": time.time()}
    with db.lock:
        db.conn.execute("UPDATE local_sources SET last_status='SCANNING',last_error='' WHERE id=?", (sid,))
        db.conn.execute("INSERT INTO photo_library_scan_state(channel_id,status,last_scan_at,updated_at) VALUES(?,'RUNNING',?,?) ON CONFLICT(channel_id) DO UPDATE SET status='RUNNING',updated_at=excluded.updated_at", (ch, now, now))
        db.conn.commit()
    photos = videos = added = 0
    try:
        if not root.is_dir():
            raise FileNotFoundError(f"The folder is not there: {root}")
        try:
            excluded = [str(x) for x in json.loads(row["excluded"] or "[]")]
        except ValueError:
            excluded = []
        with db.reading() as conn:
            known = {r["local_path"]: (int(r["message_id"]), int(r["size"] or 0), r["message_date"]) for r in conn.execute("SELECT local_path,message_id,size,message_date FROM photo_library_items WHERE channel_id=?", (ch,))}
            next_id = int(conn.execute("SELECT COALESCE(MAX(message_id),0) FROM photo_library_items WHERE channel_id=?", (ch,)).fetchone()[0]) + 1
        seen: set[str] = set()
        batch: list[tuple] = []
        for path, kind in walk_files(root, recursive=bool(row["recursive"]), excluded=excluded):
            if kind == "video":
                videos += 1
                continue
            full = os.path.realpath(path)
            seen.add(full)
            try:
                st = os.stat(full)
            except OSError:
                continue
            photos += 1
            prior = known.get(full)
            if prior and prior[1] == st.st_size and prior[2] == _iso(st.st_mtime):
                continue
            try:
                w, h, mime = _image_info(full)
            except Exception:   # not a readable image after all
                _swallowed_note("local_scanner.scan_source.image")
                photos -= 1
                continue
            mid = prior[0] if prior else next_id
            if not prior:
                next_id += 1
                added += 1
            batch.append((ch, mid, os.path.basename(full), mime, st.st_size, w, h, _iso(st.st_mtime), SOURCE_KIND, full, now, now, now))
            with _progress_lock:
                _progress[sid].update(seen=photos, added=added)
            if len(batch) >= 200:
                _write_batch(db, batch)
                batch = []
        if batch:
            _write_batch(db, batch)
        gone = [p for p in known if p not in seen]
        with db.lock:
            for i in range(0, len(gone), 400):
                part = gone[i:i + 400]
                db.conn.execute(f"DELETE FROM photo_library_items WHERE channel_id=? AND local_path IN ({','.join('?' for _ in part)})", [ch, *part])
            db.conn.execute("UPDATE local_sources SET last_status='COMPLETE',last_scan_at=?,photos=?,videos=? WHERE id=?", (now, photos, videos, sid))
            db.conn.execute("UPDATE photo_library_scan_state SET status='COMPLETE',last_scan_at=?,photos_found=?,messages_scanned=?,last_error='',updated_at=? WHERE channel_id=?", (now, photos, photos, now, ch))
            db.conn.commit()
        return {"photos": photos, "videos": videos, "added": added, "removed": len(gone)}
    except Exception as exc:
        with db.lock:
            db.conn.execute("UPDATE local_sources SET last_status='FAILED',last_error=? WHERE id=?", (f"{type(exc).__name__}: {exc}"[:300], sid))
            db.conn.execute("UPDATE photo_library_scan_state SET status='FAILED',last_error=?,updated_at=? WHERE channel_id=?", (f"{type(exc).__name__}: {exc}"[:300], config.now_iso(), ch))
            db.conn.commit()
        raise
    finally:
        with _progress_lock:
            _progress[sid] = {"running": False, "seen": photos, "added": added}


def _write_batch(db, batch: list[tuple]) -> None:
    with db.lock:
        db.conn.executemany(
            "INSERT INTO photo_library_items(channel_id,message_id,filename,mime_type,size,width,height,message_date,source_kind,local_path,cached_at,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(channel_id,message_id) DO UPDATE SET filename=excluded.filename,mime_type=excluded.mime_type,size=excluded.size,"
            "width=excluded.width,height=excluded.height,message_date=excluded.message_date,local_path=excluded.local_path,updated_at=excluded.updated_at",
            batch)
        db.conn.commit()


def root_of(db, channel: str) -> Path | None:
    sid = source_id_of(channel)
    if sid is None:
        return None
    with db.reading() as conn:
        row = conn.execute("SELECT path FROM local_sources WHERE id=?", (sid,)).fetchone()
    return Path(row["path"]) if row else None


def file_in_source(db, channel: str, local_path: str) -> Path | None:
    """The file of a local photo, only if it really lies inside its folder."""
    root = root_of(db, channel)
    if root is None:
        return None
    try:
        return inside(root, os.path.relpath(local_path, root))
    except ValueError:
        return None
