"""Local videos: index them like a channel's videos, probe them, build their 36 snapshots, play them from where they lie.

A video in a local folder is a row of ``media`` (``source_channel`` = the folder's numeric id, ``source_message_id`` a number kept per
folder) plus a row of ``local_video_files`` that remembers the file. Playback streams the file itself; nothing is copied.
"""
from __future__ import annotations

import json
import mimetypes
import os
import subprocess
import threading
import time
from pathlib import Path

from .. import config
from ..core.swallowed import note as _swallowed_note

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
_WORK_LOCK = threading.Lock()
_working: set[int] = set()
_state: dict[int, dict] = {}


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))


def _title_of(name: str) -> str:
    stem = os.path.splitext(name)[0]
    return " ".join(stem.replace("_", " ").replace(".", " ").split()) or name


def _resolution_category(height: int) -> str:
    h = int(height or 0)
    return "4K" if h >= 2160 else "1440p" if h >= 1440 else "1080p" if h >= 1080 else "720p" if h >= 720 else f"{h}p" if h else ""


def mime_of(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
            ".avi": "video/x-msvideo", ".wmv": "video/x-ms-wmv", ".ts": "video/mp2t", ".m2ts": "video/mp2t"}.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


def index_videos(db, channel: str, paths: list[str]) -> dict:
    """Make the video rows of one folder equal the files found. Returns counts. Probing and snapshots come later."""
    now = config.now_iso()
    added = 0
    with db.reading() as conn:
        known = {r["path"]: (int(r["message_id"]), int(r["size"] or 0), r["mtime"]) for r in conn.execute("SELECT path,message_id,size,mtime FROM local_video_files WHERE channel_id=?", (channel,))}
        next_id = int(conn.execute("SELECT COALESCE(MAX(message_id),0) FROM local_video_files WHERE channel_id=?", (channel,)).fetchone()[0]) + 1
    rows: list[tuple] = []
    moved_ids: set[int] = set()
    seen: set[str] = set()
    stats: list[tuple[str, os.stat_result]] = []
    for full in paths:
        try:
            stats.append((full, os.stat(full)))
        except OSError:
            continue
        seen.add(full)
    # A file that moved or was renamed is the same video: it is not at its old path any more, but one new file has the same size and modified
    # time. It keeps its row (so its name from the movie database, favourite, resume point and snapshots stay) and only the path changes.
    vanished: dict[tuple[int, str], list[int]] = {}
    for p, (mid_, size_, mtime_) in known.items():
        if p not in seen:
            vanished.setdefault((size_, mtime_), []).append(mid_)
    for full, st in stats:
        mtime = _iso(st.st_mtime)
        prior = known.get(full)
        if prior and prior[1] == st.st_size and prior[2] == mtime:
            continue
        mid = prior[0] if prior else None
        if mid is None:
            same = vanished.get((int(st.st_size), mtime))
            if same and len(same) == 1:   # exactly one missing file looks like it; with several equal copies nothing can be told apart, so none is guessed
                mid = same.pop()
                vanished.pop((int(st.st_size), mtime), None)
                moved_ids.add(mid)
        if mid is None:
            mid = next_id
            next_id += 1
            added += 1
        rows.append((mid, full, int(st.st_size), mtime))
    with db.lock:
        for mid, full, size, mtime in rows:
            name = os.path.basename(full)
            db.conn.execute(
                "INSERT INTO media(source_channel,source_message_id,filename,title,size,streamable,status,upload_date,created_at,updated_at) VALUES(?,?,?,?,?,1,'SCANNED',?,?,?) "
                "ON CONFLICT(source_channel,source_message_id) DO UPDATE SET filename=excluded.filename,size=excluded.size,"
                "duration=CASE WHEN media.size=excluded.size THEN media.duration ELSE NULL END,updated_at=excluded.updated_at",
                (channel, mid, name, _title_of(name), size, mtime, now, now))
            db.conn.execute(
                "INSERT INTO local_video_files(channel_id,message_id,path,size,mtime,probed,snapshots) VALUES(?,?,?,?,?,0,0) "
                "ON CONFLICT(channel_id,message_id) DO UPDATE SET path=excluded.path,size=excluded.size,mtime=excluded.mtime,"
                "probed=CASE WHEN local_video_files.size=excluded.size AND local_video_files.mtime=excluded.mtime THEN local_video_files.probed ELSE 0 END,"
                "snapshots=CASE WHEN local_video_files.size=excluded.size AND local_video_files.mtime=excluded.mtime THEN local_video_files.snapshots ELSE 0 END",
                (channel, mid, full, size, mtime))
        gone = [(v[0], p) for p, v in known.items() if p not in seen and v[0] not in moved_ids]
        for mid, _ in gone:
            db.conn.execute("DELETE FROM media WHERE source_channel=? AND source_message_id=?", (channel, mid))
            db.conn.execute("DELETE FROM local_video_files WHERE channel_id=? AND message_id=?", (channel, mid))
        db.conn.commit()
    for mid, _ in gone:
        drop_snapshots(db, channel, mid)
    return {"videos": len(seen), "added": added, "removed": len(gone), "moved": len(moved_ids)}


def drop_snapshots(db, channel: str, message_id: int) -> None:
    """Forget a video's snapshots (its file is gone, or its folder was removed): the frames on disk and the saved results."""
    import shutil
    from . import preview_prefetch as pp
    try:
        shutil.rmtree(pp.preview_frames_path(int(channel), int(message_id)), ignore_errors=True)
    except (TypeError, ValueError):
        return
    with db.lock:
        db.conn.execute("DELETE FROM timeline_preview_cache WHERE source_channel=? AND video_message_id=?", (str(channel), int(message_id)))
        db.conn.execute("DELETE FROM thumbnail_cache WHERE source_channel=? AND video_message_id=?", (str(channel), int(message_id)))
        db.conn.commit()


def file_of(db, channel_id: int | str, message_id: int) -> str | None:
    """The file behind a local video, only when its folder still holds it."""
    from .local_scanner import file_in_source
    with db.reading() as conn:
        row = conn.execute("SELECT path FROM local_video_files WHERE channel_id=? AND message_id=?", (str(channel_id), int(message_id))).fetchone()
    if not row:
        return None
    found = file_in_source(db, str(channel_id), row["path"])
    return str(found) if found and found.is_file() else None


def probe(path: str, timeout: float = 60.0) -> dict:
    from ..playback import media_tools
    ffprobe = media_tools.find_ffprobe()
    if not ffprobe:
        return {}
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], capture_output=True, timeout=timeout, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)
        return media_tools.summarize_probe(json.loads(out.stdout.decode("utf-8", "replace") or "{}")) if out.returncode == 0 else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        _swallowed_note("local_videos.probe")
        return {}


FRAME_WIDTH = 960   # as wide as the upload pipeline asks of reusable frames (or the video's own width when that is less), so uploading never rebuilds them


def _frame(ffmpeg: str, path: str, t: float, out: Path) -> bool:
    out.unlink(missing_ok=True)   # only a frame made now counts: near the very end ffmpeg can finish without writing one, and an old file must not stand in for it
    cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", path, "-frames:v", "1", "-an", "-sn", "-vf", f"scale='min({FRAME_WIDTH},iw)':-2", "-q:v", "3", str(out)]
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=45, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL)
        return done.returncode == 0 and out.is_file() and out.stat().st_size > 256
    except (OSError, subprocess.SubprocessError):
        _swallowed_note("local_videos.frame")
        return False


def build_snapshots(db, channel: str, message_id: int, path: str, duration: float, *, force: bool = False) -> bool:
    """Write the 36 frames, the sprite and the saved result for one file. True when the set is complete. ``force`` makes every frame again."""
    from ..playback import media_tools
    from . import preview_prefetch as pp
    ffmpeg = media_tools.find_ffmpeg()
    times = pp.frame_times(duration)
    if not ffmpeg or not times:
        return False
    cid, mid = int(channel), int(message_id)
    folder = pp.preview_frames_path(cid, mid)
    folder.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(times):
        target = pp.preview_frame_path(cid, mid, i)
        if not force and target.is_file() and target.stat().st_size > pp.MIN_FRAME_BYTES:
            continue
        # a frame at the very end of a short file can come back empty; step back a little and try again
        if not (_frame(ffmpeg, path, t, target) or _frame(ffmpeg, path, max(0.0, t - 1.0), target) or _frame(ffmpeg, path, 0.0, target)):
            return False
    if not pp.ensure_local_preview_assets_sync(db, cid, mid):
        return False
    # the card picture and the "ready" marks that let the Library show the video
    from .thumbnail_cache import _store_thumb_sync
    _store_thumb_sync(db, cid, mid, None, "image/jpeg", 0, 0, pp.representative_frame_path(cid, mid).read_bytes(), "playback-snapshot")
    with db.reading() as conn:
        row = conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?", (channel, mid)).fetchone()
    if row:
        from ..core.resource_registry import sync_media_state
        sync_media_state(db, int(row["id"]), catalogue="COMPLETE", snapshot="READY", reason="LOCAL_FOLDER_PREPARED")
    return True


def workers_for(db) -> int:
    """How many videos are prepared at once: 2 by default, 1 to 4 by the person's choice (Settings, `local_prepare_workers`)."""
    try:
        return max(1, min(4, int(db.setting("local_prepare_workers") or 2)))
    except (TypeError, ValueError):
        return 2


def _prepare_one(db, channel: str, item: dict) -> bool:
    """Probe one video and build its snapshots. True when it ended up ready."""
    mid = int(item["message_id"])
    path = file_of(db, channel, mid)
    if not path:
        return False
    info: dict = {}
    if not item["probed"]:
        info = probe(path)
        with db.lock:
            if info:
                db.conn.execute(
                    "UPDATE media SET duration=?,width=?,height=?,fps=?,bitrate=?,codec=?,audio_codec=?,orientation=?,resolution_category=?,updated_at=? WHERE source_channel=? AND source_message_id=?",
                    (info["duration"], info["width"], info["height"], info["fps"], info["bit_rate"], info["vcodec"], info["acodec"],
                     "portrait" if info["height"] > info["width"] else "landscape", _resolution_category(info["height"]), config.now_iso(), channel, mid))
            db.conn.execute("UPDATE local_video_files SET probed=? WHERE channel_id=? AND message_id=?", (1 if info else 2, channel, mid))
            db.conn.commit()
    if item["snapshots"]:
        return True
    with db.reading() as conn:
        row = conn.execute("SELECT duration FROM media WHERE source_channel=? AND source_message_id=?", (channel, mid)).fetchone()
    duration = float((info.get("duration") if info else None) or (row["duration"] if row else 0) or 0)
    ok = duration > 0 and build_snapshots(db, channel, mid, path, duration, force=True)   # a new or changed file: whatever frames exist belong to something else
    with db.lock:
        db.conn.execute("UPDATE local_video_files SET snapshots=? WHERE channel_id=? AND message_id=?", (1 if ok else 2, channel, mid))
        db.conn.commit()
    return ok


def process(db, channel: str) -> dict:
    """Probe every unprobed video of a folder, then build snapshots for the ones that have none, a few at a time. Safe to run again."""
    from concurrent.futures import ThreadPoolExecutor
    sid_key = int(channel)
    work_key = (id(db), sid_key)   # one run per folder of one library; another database (a test, a restored copy) has its own folders with the same numbers
    with _WORK_LOCK:
        if work_key in _working:
            return dict(_state.get(sid_key, {}))
        _working.add(work_key)
    try:
        with db.reading() as conn:
            todo = [dict(r) for r in conn.execute("SELECT channel_id,message_id,path,probed,snapshots FROM local_video_files WHERE channel_id=? AND (probed=0 OR snapshots=0) ORDER BY message_id", (channel,))]
        workers = workers_for(db)
        _state[sid_key] = {"running": True, "total": len(todo), "done": 0, "failed": 0, "workers": workers, "started": time.time()}

        def one(item: dict) -> None:
            try:
                ok = _prepare_one(db, channel, item)
            except Exception:
                _swallowed_note("local_videos.process")
                ok = False
            with _WORK_LOCK:
                _state[sid_key]["done"] += 1
                if not ok:
                    _state[sid_key]["failed"] += 1
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="local-prepare") as pool:
            list(pool.map(one, todo))
        return dict(_state[sid_key])
    finally:
        with _WORK_LOCK:
            _state[sid_key] = {**_state.get(sid_key, {}), "running": False}
            _working.discard(work_key)


def running_work() -> dict[int, dict]:
    """The folders whose videos are being prepared now, by source id."""
    with _WORK_LOCK:
        return {k: dict(v) for k, v in _state.items() if v.get("running")}


def progress(channel: str) -> dict:
    try:
        return dict(_state.get(int(channel), {}))
    except (TypeError, ValueError):
        return {}
