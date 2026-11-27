"""Local folders as photo sources: walk a folder, index its images like a channel's photos, keep the index equal to the disk.

A local folder is a row in ``channels`` (telegram_id = ``channel_id(id)``, a number far below any Telegram id) plus a row in ``local_sources``. Its photos are rows of
``photo_library_items`` with ``source_kind='local'`` and ``local_path`` = the file; the file is read where it lies and is never copied.
Videos go to ``local_videos`` (rows of ``media``).
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


LOCAL_BASE = -9_000_000_000_000   # below every Telegram channel id (-100…), so the playback routes, which take whole numbers, accept it


def channel_id(source_id: int) -> str:
    return str(LOCAL_BASE - int(source_id))


def is_local_channel(value) -> bool:
    try:
        return int(str(value)) < LOCAL_BASE
    except (TypeError, ValueError):
        return False


def source_id_of(channel) -> int | None:
    return LOCAL_BASE - int(str(channel)) if is_local_channel(channel) else None


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
    from ..core.imaging import allow_large_images
    allow_large_images()   # a 150-megapixel picture is a picture, not a decompression bomb
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


def _clock(started: float | None, done: int, total: int) -> dict:
    """Seconds spent so far and, once something is done, a guess of the seconds left."""
    if not started:
        return {}
    spent = max(0.0, time.time() - float(started))
    out: dict = {"elapsed_s": int(spent)}
    if done > 0 and total > done:
        out["eta_s"] = int(spent / done * (total - done))
    return out


RECENT_SECONDS = 600   # a finished metadata scan that had failures stays on the lanes this long, so a failure is seen and not just gone
CLEAN_RECENT_SECONDS = 30   # one that finished without any: a line that says "finished" for ten minutes looks like a scan that never ends


def _identify_rows(db) -> list[dict]:
    """The metadata scans (movies and shows, or scene databases) running now or finished a moment ago, shaped like lane rows."""
    from . import local_identify_adult, screen_identify
    now = time.time()
    rows = []
    for key, mod, what in (("sfw", screen_identify, "Naming movies and shows"), ("nsfw", local_identify_adult, "Matching scenes by file hash")):
        st = mod.progress()
        total, done = int(st.get("total") or 0), int(st.get("done") or 0)
        running = bool(st.get("running"))
        finished = float(st.get("finished") or 0)
        if not total and not running:
            continue
        failed = int(st.get("failed") or 0)
        if not running and (not finished or now - finished > (RECENT_SECONDS if failed else CLEAN_RECENT_SECONDS)):
            continue
        ch = str(st.get("channel") or "")
        sid = source_id_of(ch) if ch else None
        matched = int(st.get("matched") or 0)
        name = "All folders and channels"
        if sid is not None:
            with db.reading() as conn:
                r = conn.execute("SELECT s.path, c.title FROM local_sources s LEFT JOIN channels c ON c.telegram_id=CAST(? - s.id AS TEXT) WHERE s.id=?", (LOCAL_BASE, sid)).fetchone()
            name = str((r["title"] or r["path"]) if r else sid)
        bits = [f"{done:,} of {total:,} looked at", f"{matched:,} named" if matched else "", f"{failed:,} could not be checked" if failed else ""]
        reason = str(st.get("reason") or "")
        if running:
            status, op = "RUNNING", f"{what} · " + " · ".join(b for b in bits if b)
        else:
            status = "COMPLETED_WITH_ERRORS" if failed else "COMPLETED"
            op = f"{what} finished · " + " · ".join(b for b in bits if b)
        if failed and reason:
            op += f" · {reason}"
        rows.append({
            "id": f"identify:{key}", "type": "LOCAL_SCAN", "status": status, "operation": op, "current_item": "",
            "completed": done, "total": total, "progress": round(100.0 * done / total, 1) if total else 0.0,
            "source": "local", "workflow": "", "source_channel": name, "destination_channel": "",
            "metrics": _clock(st.get("started"), done, total) if running else {},
        })
    return rows


def lane_rows(db) -> list[dict]:
    """Folders being looked through or prepared right now, shaped like the rows of the transfer lanes, so Transfers shows them as jobs."""
    from . import local_videos
    with _progress_lock:
        scans = {k: dict(v) for k, v in _progress.items() if v.get("running")}
    work = local_videos.running_work()
    ids = sorted(set(scans) | set(work))
    if not ids:
        return _identify_rows(db)
    with db.reading() as conn:
        titles = {int(r["id"]): (r["title"] or r["path"]) for r in conn.execute(
            "SELECT s.id, s.path, c.title FROM local_sources s LEFT JOIN channels c ON c.telegram_id=CAST(? - s.id AS TEXT)", (LOCAL_BASE,))}
    rows = []
    for sid in ids:
        scan, prep = scans.get(sid), work.get(sid)
        name = str(titles.get(sid, sid))
        if scan:
            found = int(scan.get("seen", 0)) + int(scan.get("videos", 0))
            op, done, total, started = f"Looking through the folder · {found:,} files found", 0, 0, scan.get("started")
        else:
            done, total = int(prep.get("done", 0)), int(prep.get("total", 0))
            op, started = f"Preparing videos: probing and building snapshots · {done:,} of {total:,}", prep.get("started")
        rows.append({
            "id": f"local:{sid}", "type": "LOCAL_SCAN", "status": "RUNNING", "operation": op, "current_item": "",
            "completed": done, "total": total, "progress": round(100.0 * done / total, 1) if total else 0.0,
            "source": "local", "workflow": "", "source_channel": name, "destination_channel": "",
            "metrics": _clock(started, done, total),
        })
    return rows + _identify_rows(db)


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
    video_paths: list[str] = []
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
        fresh: list[tuple] = []   # photos with no row yet: (path, message id, size, modified)
        for path, kind in walk_files(root, recursive=bool(row["recursive"]), excluded=excluded):
            if kind == "video":
                videos += 1
                video_paths.append(os.path.realpath(path))
                if videos % 50 == 0:
                    with _progress_lock:
                        _progress[sid].update(videos=videos)
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
                fresh.append((full, mid, int(st.st_size), _iso(st.st_mtime)))
            batch.append((ch, mid, os.path.basename(full), mime, st.st_size, w, h, _iso(st.st_mtime), SOURCE_KIND, full, now, now, now))
            with _progress_lock:
                _progress[sid].update(seen=photos, added=added, videos=videos)
            if len(batch) >= 200:
                _write_batch(db, batch)
                batch = []
        if batch:
            _write_batch(db, batch)
        # A photo that moved or was renamed is the same photo: one missing file has the size and modified time of one new file. It keeps its row, so
        # the people it was named after, its favourite and its tags stay; only the path changes.
        lost: dict[tuple, list[str]] = {}
        for p, (_m, size_, mtime_) in known.items():
            if p not in seen:
                lost.setdefault((size_, mtime_), []).append(p)
        sigs = [(size_, mtime_) for _f, _m, size_, mtime_ in fresh]
        moved = 0
        for full, mid, size_, mtime_ in fresh:
            old = lost.get((size_, mtime_))
            if old and len(old) == 1 and sigs.count((size_, mtime_)) == 1:
                with db.lock:
                    db.conn.execute("DELETE FROM photo_library_items WHERE channel_id=? AND message_id=?", (ch, mid))
                    db.conn.execute("UPDATE photo_library_items SET local_path=?,filename=?,updated_at=? WHERE channel_id=? AND message_id=?", (full, os.path.basename(full), now, ch, known[old[0]][0]))
                    db.conn.commit()
                added -= 1
                moved += 1
        from . import local_videos
        local_videos.index_videos(db, ch, video_paths)
        gone = [p for p in known if p not in seen]
        with db.lock:
            for i in range(0, len(gone), 400):
                part = gone[i:i + 400]
                db.conn.execute(f"DELETE FROM photo_library_items WHERE channel_id=? AND local_path IN ({','.join('?' for _ in part)})", [ch, *part])
            db.conn.execute("UPDATE local_sources SET last_status='COMPLETE',last_scan_at=?,photos=?,videos=? WHERE id=?", (now, photos, videos, sid))
            db.conn.execute("UPDATE photo_library_scan_state SET status='COMPLETE',last_scan_at=?,photos_found=?,messages_scanned=?,last_error='',updated_at=? WHERE channel_id=?", (now, photos, photos, now, ch))
            db.conn.commit()
        return {"photos": photos, "videos": videos, "added": added, "removed": len(gone) - moved, "moved": moved}
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
