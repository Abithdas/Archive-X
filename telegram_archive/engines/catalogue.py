"""Local catalogue writer (v18.9).

Creates/updates `media` rows (with studio/performer/tag hints parsed from the
filename) for videos the Library shows. Used by the background page enricher;
the old Scan Channel module that also used it was replaced by the unified
Telegram Media Manager.
"""
from __future__ import annotations


def _save_to_catalogue(db, channel_id: int, channel_title: str, videos: dict, images: dict):
    """Write scanned records into the SQLite catalogue, including metadata parsed from filenames."""
    from .. import config
    from ..metadata.filename_matching import extract_filename_parameters
    ts = config.now_iso()
    with db.lock:
        db.conn.execute(
            "INSERT INTO channels(telegram_id,title,username,role,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(telegram_id) DO UPDATE SET title=CASE WHEN COALESCE(channels.title_locked,0)=1 THEN channels.title ELSE excluded.title END, updated_at=excluded.updated_at",
            (str(channel_id), channel_title or "Unknown Channel", None, "available", ts)
        )
        for vid in videos.values():
            fname = vid["filename"]
            hints = extract_filename_parameters(db, fname)
            h = vid["height"] or 0
            res_cat = ("4K" if h>=2160 else "1440p" if h>=1440 else "1080p" if h>=1080
                       else "720p" if h>=720 else f"{h}p" if h else "")
            automation_hash = None
            try:
                mapped = db.conn.execute(
                    "SELECT raw_oshash FROM automation_delivery_fingerprints WHERE destination_channel=? AND destination_video_id=?",
                    (str(channel_id), int(vid["message_id"])),
                ).fetchone()
                automation_hash = str(mapped["raw_oshash"] or "").strip() if mapped else None
            except Exception:
                automation_hash = None
            existing = db.conn.execute(
                "SELECT id,filename,size,duration,width,height,caption,oshash FROM media WHERE source_channel=? AND source_message_id=?",
                (str(channel_id), int(vid["message_id"])),
            ).fetchone()
            db.conn.execute(
                """INSERT INTO media(source_channel,source_message_id,filename,title,
                   size,duration,width,height,fps,orientation,streamable,resolution_category,
                   thumbnail_message_id,oshash,upload_date,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source_channel,source_message_id) DO UPDATE SET
                   filename=excluded.filename,
                   title=CASE WHEN COALESCE(media.identification_state,'') IN ('MATCHED','VERIFIED')
                                   AND COALESCE(TRIM(media.title),'')<>'' THEN media.title ELSE excluded.title END,
                   size=excluded.size,
                   duration=excluded.duration, width=excluded.width, height=excluded.height,
                   thumbnail_message_id=excluded.thumbnail_message_id,
                   orientation=excluded.orientation, streamable=excluded.streamable,
                   resolution_category=excluded.resolution_category,
                   oshash=COALESCE(excluded.oshash, media.oshash),
                   updated_at=excluded.updated_at""",
                (str(channel_id), vid["message_id"], fname,
                 hints.get("title") or fname,
                 vid["size"], vid["duration"], vid["width"], vid["height"],
                 vid.get("fps") or 0,
                 vid["orientation"], 1 if vid["streamable"] else 0, res_cat,
                 vid.get("thumbnail_id"), automation_hash, str(vid.get("upload_date") or ""),
                 "SCANNED", ts, ts)
            )
            # Store parsed studio/performers/tags
            row = db.conn.execute(
                "SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                (str(channel_id), vid["message_id"])).fetchone()
            if not row: continue
            vid_id = row["id"]
            # A catalogue refresh is cheap local state. Only source changes invalidate
            # derived resources; unchanged Telegram records remain fully reusable.
            def _same_source_value(old, new):
                # SQLite may return duration as float (e.g. 22.0) while the
                # Telegram adapter supplies int (22). Treat equivalent numeric
                # values as equal so rescanning an unchanged file is idempotent.
                if old is not None and new is not None and not isinstance(old, bool) and not isinstance(new, bool):
                    try:
                        old_number, new_number = float(old), float(new)
                        return old_number == new_number
                    except (TypeError, ValueError):
                        pass
                return str(old or "") == str(new or "")

            source_fields = [
                ("filename", fname), ("size", vid["size"]),
                ("duration", vid["duration"]), ("width", vid["width"]),
                ("height", vid["height"]),
            ]
            # An absent automation fingerprint is not a source change and must
            # not invalidate an existing fingerprint saved by another workflow.
            if automation_hash:
                source_fields.append(("oshash", automation_hash))
            changed = (not existing) or any(
                not _same_source_value(existing[k], value) for k, value in source_fields
            )
            db.conn.execute(
                "INSERT OR IGNORE INTO resource_state(media_id,catalogue_state,updated_at) VALUES(?,?,?)",
                (int(vid_id), "COMPLETE", ts),
            )
            if changed:
                db.conn.execute(
                    """UPDATE resource_state SET catalogue_state='COMPLETE', metadata_state='STALE',
                     fingerprint_state='STALE', snapshot_state=CASE WHEN snapshot_state='READY' THEN 'STALE' ELSE snapshot_state END,
                     content_revision=content_revision+1, metadata_revision=metadata_revision+1,
                     last_reason='STALE_REFRESH_REQUIRED', updated_at=? WHERE media_id=?""",
                    (ts, int(vid_id)),
                )
                db.conn.execute(
                    "INSERT INTO resource_events(media_id,event_type,reason,detail,created_at) VALUES(?,?,?,?,?)",
                    (int(vid_id), "CATALOGUE_REFRESH", "STALE_REFRESH_REQUIRED", "Telegram catalogue source changed", ts),
                )
            # Filename names are clues, not authoritative entity identities. Do not
            # create performer/studio rows directly from free-form filename text:
            # provider-confirmed matching creates canonical entities, while the
            # existing filename-entity linker may attach uniquely known local rows
            # when provider lookup has no candidate. This avoids duplicate or
            # incorrectly inferred entities during repeated catalogue scans.
            # Tags
            for tname in hints.get("tags", []):
                if not tname.strip(): continue
                db.conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (tname.strip(),))
                tid = db.conn.execute("SELECT id FROM tags WHERE name=?", (tname.strip(),)).fetchone()["id"]
                db.conn.execute("INSERT OR IGNORE INTO video_tags(video_id,tag_id) VALUES(?,?)", (vid_id, tid))
        db.conn.commit()
