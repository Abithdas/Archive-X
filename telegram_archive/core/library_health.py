"""Read-only library health checks and local-only artwork-cache repair helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import config


def library_catalogue_counts(db) -> dict[str, int]:
    """Return counts for records visible in the current Library."""
    library_media = "SELECT id FROM archive_x_library_media"
    with db.reading() as conn:
        video_count = int(conn.execute(
            "SELECT COUNT(*) FROM archive_x_library_media"
        ).fetchone()[0] or 0)
        performer_count = int(conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT p.id FROM performers p "
            "LEFT JOIN ("
            f"SELECT performer_id,video_id FROM video_performers WHERE video_id IN ({library_media}) "
            "UNION "
            f"SELECT performer_id,video_id FROM local_video_performers WHERE video_id IN ({library_media})"
            ") linked ON linked.performer_id=p.id "
            "GROUP BY p.id HAVING COUNT(DISTINCT linked.video_id)>0 OR p.name='Unknown Performer'"
            ")"
        ).fetchone()[0] or 0)
        studio_count = int(conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT s.id FROM studios s "
            "LEFT JOIN video_studios vs ON vs.studio_id=s.id "
            f"GROUP BY s.id HAVING COUNT(DISTINCT CASE WHEN vs.video_id IN ({library_media}) THEN vs.video_id END)>0 "
            "OR s.name='Unknown Studio'"
            ")"
        ).fetchone()[0] or 0)
    return {
        "videos": video_count,
        "performers": performer_count,
        "studios": studio_count,
    }


def diagnose(db, *, limit: int = 100, stale_job_hours: int = 24) -> dict[str, Any]:
    """Return bounded, actionable health findings without contacting Telegram/providers."""
    limit = max(1, min(1000, int(limit or 100)))
    stale_hours = max(1, min(24 * 30, int(stale_job_hours or 24)))
    active_media = "COALESCE(m.status,'') <> 'SOURCE_DELETED'"
    with db.reading() as conn:
        active_count = int(conn.execute(f"SELECT COUNT(*) FROM media m WHERE {active_media}").fetchone()[0] or 0)
        missing_snapshot_count = int(conn.execute(
            f"SELECT COUNT(*) FROM media m LEFT JOIN thumbnail_cache t "
            "ON t.source_channel=m.source_channel AND t.video_message_id=m.source_message_id "
            "AND t.source='playback-snapshot' WHERE " + active_media + " AND (t.video_message_id IS NULL OR t.data IS NULL OR length(t.data)=0)"
        ).fetchone()[0] or 0)
        preview_rows = conn.execute(
            "SELECT source_channel,video_message_id,path,frames_path,frame_count,status "
            "FROM timeline_preview_cache WHERE status='READY' ORDER BY updated_at DESC LIMIT ?", (limit * 5,)
        ).fetchall()
        bad_snapshot_blobs = int(conn.execute(
            "SELECT COUNT(*) FROM thumbnail_cache WHERE source='playback-snapshot' AND (data IS NULL OR length(data)=0)"
        ).fetchone()[0] or 0)
        artwork_missing = {}
        for table in ("performer_image_cache", "studio_image_cache"):
            artwork_missing[table] = int(conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE COALESCE(url,'')<>'' AND (data IS NULL OR length(data)=0)"
            ).fetchone()[0] or 0)
        stale_jobs = conn.execute(
            "SELECT id,job_type,status,updated_at,current_item,error FROM jobs "
            "WHERE status IN ('RUNNING','PAUSING','PAUSED','CANCEL_REQUESTED','INTERRUPTED','RECOVERING') "
            "AND julianday(updated_at) < julianday('now', ?) ORDER BY updated_at LIMIT ?",
            (f"-{stale_hours} hours", limit),
        ).fetchall()

    def _file_ready(value: str) -> bool:
        try:
            candidate = Path(value)
            return candidate.is_file() and candidate.stat().st_size > 4096
        except (OSError, ValueError):
            return False

    def _frames_ready(value: str) -> bool:
        try:
            directory = Path(value)
            return directory.is_dir() and sum(1 for _ in directory.glob("frame_*.jpg")) >= 36
        except (OSError, ValueError):
            return False

    missing_preview_files = []
    for row in preview_rows:
        path = str(row["path"] or "")
        frames_path = str(row["frames_path"] or "")
        sheet_ok = bool(path and _file_ready(path))
        frames_ok = bool(frames_path and _frames_ready(frames_path))
        if not sheet_ok or not frames_ok:
            missing_preview_files.append({
                "channel_id": str(row["source_channel"]),
                "message_id": int(row["video_message_id"]),
                "sheet_missing": not sheet_ok,
                "frames_missing_or_incomplete": not frames_ok,
                "frame_count_recorded": int(row["frame_count"] or 0),
            })
            if len(missing_preview_files) >= limit:
                break

    findings = []
    if missing_snapshot_count:
        findings.append({"code": "SNAPSHOT_CACHE_MISSING", "severity": "warning",
                         "count": missing_snapshot_count,
                         "message": "Active media items missing an Archive X representative snapshot."})
    if bad_snapshot_blobs:
        findings.append({"code": "SNAPSHOT_CACHE_EMPTY", "severity": "error",
                         "count": bad_snapshot_blobs,
                         "message": "Snapshot cache rows exist but contain no image bytes."})
    if missing_preview_files:
        findings.append({"code": "PREVIEW_FILES_MISSING", "severity": "warning",
                         "count": len(missing_preview_files),
                         "message": "Ready preview manifest entries have missing or incomplete local files."})
    for table, count in artwork_missing.items():
        if count:
            findings.append({"code": "ARTWORK_CACHE_BYTES_MISSING", "severity": "warning",
                             "count": count, "table": table,
                             "message": "Artwork cache has a URL but no cached bytes; local artwork recovery may be possible."})
    if stale_jobs:
        findings.append({"code": "STALE_BACKGROUND_JOBS", "severity": "warning",
                         "count": len(stale_jobs),
                         "message": f"Jobs in active states have not updated for at least {stale_hours} hours."})

    return {
        "schema": 1,
        "checked_at": config.now_iso(),
        "summary": {
            "active_media": active_count,
            "missing_snapshot_cache": missing_snapshot_count,
            "empty_snapshot_blobs": bad_snapshot_blobs,
            "ready_preview_rows_checked": len(preview_rows),
            "missing_preview_files": len(missing_preview_files),
            "artwork_cache_missing_bytes": artwork_missing,
            "stale_jobs": len(stale_jobs),
            "finding_count": len(findings),
        },
        "findings": findings,
        "details": {
            "missing_preview_files": missing_preview_files,
            "stale_jobs": [dict(row) for row in stale_jobs],
        },
        "repair_policy": "Only local artwork cache recovery is automatic; no Telegram message/channel is modified.",
    }


def repair_artwork_cache_from_disk(db, *, limit: int = 100) -> dict[str, Any]:
    """Recover missing image-cache bytes from verified canonical local files only."""
    from ..engines.thumbnail_cache import reconcile_entity_image_cache_from_disk_sync

    limit = max(1, min(500, int(limit or 100)))
    candidates = []
    with db.reading() as conn:
        for table, entity_type, id_field in (
            ("performer_image_cache", "performer", "performer_id"),
            ("studio_image_cache", "studio", "studio_id"),
        ):
            rows = conn.execute(
                f"SELECT {id_field} AS entity_id,url FROM {table} "
                "WHERE COALESCE(url,'')<>'' AND (data IS NULL OR length(data)=0) "
                f"ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            candidates.extend((entity_type, int(row["entity_id"]), str(row["url"] or "")) for row in rows)
    repaired, skipped, failures = [], [], []
    for entity_type, entity_id, url in candidates[:limit]:
        try:
            result = reconcile_entity_image_cache_from_disk_sync(db, entity_type, entity_id, url)
            (repaired if result else skipped).append({"entity_type": entity_type, "entity_id": entity_id})
        except Exception as exc:
            failures.append({"entity_type": entity_type, "entity_id": entity_id,
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
    if repaired:
        try:
            db.log(f"Library health repaired {len(repaired)} artwork cache row(s) from verified local files")
        except Exception:
            pass
    return {"examined": min(len(candidates), limit), "repaired": len(repaired),
            "skipped": len(skipped), "failed": len(failures),
            "repaired_items": repaired, "skipped_items": skipped, "errors": failures,
            "network_requests": 0, "telegram_mutations": 0}
