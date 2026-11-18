"""Uploading the videos of a local folder to a Telegram channel without doing their work again.

The upload job (``engines/uploader.py``) already reuses snapshots it finds recorded for a file, and carries them to the new post. Two small
steps connect a local folder to it:

* ``seed``: before the job, record the folder video's 36 frames where the upload job looks for them (and make them wide enough), so it
  finds them ready and builds nothing.
* ``carry_identity``: after a verified upload, copy what is known about the video (title, date, cast, studios, genres, the movie or show it is)
  onto the Telegram copy, so the channel's own scan has nothing left to identify.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from .. import config


def required_width(video_width: int) -> int:
    """The least width the upload pipeline accepts for reusable frames: 960, or the video's own width when it is narrower."""
    return min(960, max(1, int(video_width or 0))) if int(video_width or 0) > 0 else 640


def video_row(db, channel: str, message_id: int):
    with db.reading() as conn:
        return conn.execute(
            "SELECT m.id,m.duration,m.width,m.height,m.codec,m.fps,l.path FROM media m JOIN local_video_files l ON l.channel_id=m.source_channel AND l.message_id=m.source_message_id "
            "WHERE m.source_channel=? AND m.source_message_id=?", (str(channel), int(message_id))).fetchone()


def seed(db, channel: str, message_id: int) -> str:
    """Make one folder video ready for the upload job. Returns "seeded", "rebuilt" (frames made wider first) or "skipped: why"."""
    from ..metadata.fingerprint import oshash_file
    from . import local_videos, preview_prefetch as pp
    from .thumbnail_cache import (_snapshot_frames_meet_min_width, local_playback_representative_path, local_playback_snapshot_frames_path,
                                  local_playback_snapshot_path, record_local_playback_snapshot_sync)
    row = video_row(db, channel, message_id)
    if not row:
        return "skipped: not in the library"
    path = local_videos.file_of(db, channel, message_id)
    if not path:
        return "skipped: the file is not in its folder any more"
    cid, mid = int(channel), int(message_id)
    source = pp.preview_frames_path(cid, mid)
    verdict = "seeded"
    need = required_width(int(row["width"] or 0))
    if not (source.is_dir() and _snapshot_frames_meet_min_width(source, need)):
        if not float(row["duration"] or 0) > 0 or not local_videos.build_snapshots(db, channel, mid, path, float(row["duration"]), force=True):
            return "skipped: snapshots could not be made"
        verdict = "rebuilt"
    video = Path(path)
    frames = local_playback_snapshot_frames_path(video)
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True, exist_ok=True)
    for i in range(1, 37):
        shutil.copy2(source / f"frame_{i:02d}.jpg", frames / f"frame_{i:02d}.jpg")
    middle = frames / "frame_19.jpg"          # the representative of a local file is its middle frame
    local_playback_representative_path(video).write_bytes(middle.read_bytes())
    local_playback_snapshot_path(video).write_bytes(middle.read_bytes())
    fingerprint = oshash_file(path)
    with db.lock:
        db.conn.execute("UPDATE media SET oshash=? WHERE id=?", (fingerprint, int(row["id"])))
        db.conn.commit()
    record_local_playback_snapshot_sync(db, video, fingerprint, local_playback_snapshot_path(video),
                                        {"duration": float(row["duration"] or 0), "width": int(row["width"] or 0), "height": int(row["height"] or 0), "video_codec": row["codec"] or "", "fps": float(row["fps"] or 0)})
    return verdict


def carry_identity(db, source_path, destination_media_id: int) -> bool:
    """After a verified upload: put what is known about a folder video onto its copy in the channel. False when it is not from a local folder."""
    if int(destination_media_id or 0) <= 0:
        return False
    real = os.path.realpath(str(source_path))
    with db.reading() as conn:
        src = conn.execute(
            "SELECT m.id,m.title,m.release_date,m.identification_state,m.source_channel,m.source_message_id FROM local_video_files l JOIN media m ON m.source_channel=l.channel_id AND m.source_message_id=l.message_id WHERE l.path=?", (real,)).fetchone()
        if not src:
            return False
        screen = conn.execute("SELECT title_id,season,episode,state,confidence,source,chosen,candidates,parser FROM media_screen WHERE media_id=?", (int(src["id"]),)).fetchone()
        links = {t: [r[0] for r in conn.execute(f"SELECT {col} FROM {t} WHERE video_id=?", (int(src["id"]),))]
                 for t, col in (("video_performers", "performer_id"), ("video_studios", "studio_id"), ("video_tags", "tag_id"))}
    dest = int(destination_media_id)
    now = config.now_iso()
    with db.lock:
        if str(src["identification_state"] or "") in ("MATCHED", "VERIFIED"):
            db.conn.execute("UPDATE media SET title=?,release_date=?,identification_state=?,updated_at=? WHERE id=?", (src["title"], src["release_date"], src["identification_state"], now, dest))
        for table, col in (("video_performers", "performer_id"), ("video_studios", "studio_id"), ("video_tags", "tag_id")):
            for entity in links[table]:
                db.conn.execute(f"INSERT OR IGNORE INTO {table}(video_id,{col}) VALUES(?,?)", (dest, int(entity)))
        if screen:
            db.conn.execute(
                "INSERT INTO media_screen(media_id,title_id,season,episode,state,confidence,source,chosen,candidates,parser,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(media_id) DO UPDATE SET "
                "title_id=excluded.title_id,season=excluded.season,episode=excluded.episode,state=excluded.state,confidence=excluded.confidence,source=excluded.source,chosen=excluded.chosen,candidates=excluded.candidates,parser=excluded.parser,updated_at=excluded.updated_at",
                (dest, screen["title_id"], screen["season"], screen["episode"], screen["state"], screen["confidence"], "local folder", screen["chosen"], screen["candidates"], screen["parser"], now))
        db.conn.commit()
    if str(src["identification_state"] or "") in ("MATCHED", "VERIFIED"):
        from ..core.resource_registry import sync_media_state
        sync_media_state(db, dest, metadata="COMPLETE", entity="COMPLETE", reason="IDENTITY_FROM_LOCAL_FOLDER")   # the channel's scan has nothing left to identify
    return True
