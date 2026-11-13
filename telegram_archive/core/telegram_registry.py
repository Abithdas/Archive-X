"""Durable Telegram document registry.

A Telegram document ID is the primary identity across channel locations. The
registry stores only metadata available from message objects and existing local
state; registry migration never requests media bytes from Telegram.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Helpers
#   - Schema and message registration
#   - Lookups and channel scan records
#   - Thumbnail links
#   - Maintenance
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import base64
import json
import sqlite3
from typing import Any

from .. import config




# ==============================================================================
# SECTION: Helpers
# Time, JSON and table checks.
# ==============================================================================

def _now() -> str:
    return config.now_iso()


def _json(value: Any, default: Any) -> str:
    try:
        return json.dumps(value if value is not None else default, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return json.dumps(default, ensure_ascii=False, separators=(",", ":"))


def _has_table(db, name: str) -> bool:
    return db.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None




# ==============================================================================
# SECTION: Schema and message registration
# Registry tables and known message registration.
# ==============================================================================

def ensure_telegram_registry(db) -> None:
    """Create registry schema and index existing SQLite records without downloads."""
    if not hasattr(db, "conn") or not hasattr(db, "lock"):
        return
    if getattr(db, "_telegram_registry_ready", None) is db.conn:
        return
    now = _now()
    with db.lock:
        db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS telegram_document_registry (
            registry_key TEXT PRIMARY KEY,
            document_id TEXT,
            identity_kind TEXT NOT NULL DEFAULT 'LEGACY_MESSAGE',
            access_hash TEXT,
            filename TEXT,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            duration REAL NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            mime_type TEXT,
            caption TEXT,
            message_date TEXT,
            grouped_id TEXT,
            companion_message_ids_json TEXT NOT NULL DEFAULT '[]',
            known_state TEXT NOT NULL DEFAULT 'KNOWN',
            prepared_state TEXT NOT NULL DEFAULT 'PENDING',
            prepared_at TEXT,
            prepared_json TEXT NOT NULL DEFAULT '{}',
            oshash TEXT,
            phash TEXT,
            media_id INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_telegram_registry_document_id
            ON telegram_document_registry(document_id) WHERE document_id IS NOT NULL AND document_id<>'';
        CREATE INDEX IF NOT EXISTS idx_telegram_registry_prepared ON telegram_document_registry(prepared_state,updated_at);
        CREATE INDEX IF NOT EXISTS idx_telegram_registry_oshash ON telegram_document_registry(oshash) WHERE oshash IS NOT NULL AND oshash<>'';
        CREATE TABLE IF NOT EXISTS telegram_document_locations (
            channel_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            registry_key TEXT NOT NULL,
            access_hash TEXT,
            file_reference_b64 TEXT,
            thumbnail_message_id INTEGER,
            message_date TEXT,
            caption TEXT,
            location_state TEXT NOT NULL DEFAULT 'PRESENT',
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY(channel_id,message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_locations_registry ON telegram_document_locations(registry_key,location_state);
        CREATE INDEX IF NOT EXISTS idx_telegram_locations_channel ON telegram_document_locations(channel_id,location_state,message_id);
        CREATE TABLE IF NOT EXISTS telegram_move_cleanup (
            source_channel TEXT NOT NULL,
            source_message_id INTEGER NOT NULL,
            destination_channel TEXT NOT NULL,
            destination_message_id INTEGER NOT NULL,
            document_id TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_attempt_at TEXT,
            status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(source_channel,source_message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_move_cleanup_due ON telegram_move_cleanup(status,next_attempt_at);
        CREATE TABLE IF NOT EXISTS telegram_channel_registry_state (
            channel_id TEXT PRIMARY KEY,
            high_water_mark INTEGER NOT NULL DEFAULT 0,
            last_scan_at TEXT,
            last_scan_status TEXT NOT NULL DEFAULT 'NEVER',
            last_scan_detail TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );
        """)
        # V29.4.223: v29.4.222 registered local staging rows as if they were
        # channels; remove them so they stop appearing as channel cards.
        stale_keys = [r[0] for r in db.conn.execute(
            "SELECT registry_key FROM telegram_document_locations WHERE channel_id GLOB '*[^0-9-]*'").fetchall()]
        if stale_keys:
            db.conn.execute("DELETE FROM telegram_document_locations WHERE channel_id GLOB '*[^0-9-]*'")
            for stale in stale_keys:
                db.conn.execute(
                    "DELETE FROM telegram_document_registry WHERE registry_key=? AND NOT EXISTS("
                    "SELECT 1 FROM telegram_document_locations l WHERE l.registry_key=?)", (stale, stale))
        db.conn.execute("DELETE FROM telegram_channel_registry_state WHERE channel_id GLOB '*[^0-9-]*'")
        # Legacy rows cannot reveal Telegram's internal document ID without a
        # message lookup. Keep them represented under a location-scoped key;
        # the next ordinary scan upgrades that key when a document ID is seen.
        media_rows = db.conn.execute(
            "SELECT m.* FROM media m WHERE COALESCE(m.source_channel,'')<>'' AND COALESCE(m.source_message_id,0)>0 "
            # V29.4.223: only real Telegram channels; local staging rows
            # ("local_upload", "content_automation") are not channel messages.
            "AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9-]*' "
            "AND NOT EXISTS(SELECT 1 FROM telegram_document_locations l "
            "WHERE l.channel_id=m.source_channel AND l.message_id=CAST(m.source_message_id AS INTEGER))"
        ).fetchall()
        state_columns = _has_table(db, "resource_state")
        asset_columns = _has_table(db, "asset_registry")
        for row in media_rows:
            r = dict(row)
            channel_id, message_id = str(r.get("source_channel") or ""), int(r.get("source_message_id") or 0)
            existing = db.conn.execute(
                "SELECT registry_key FROM telegram_document_locations WHERE channel_id=? AND message_id=?",
                (channel_id, message_id),
            ).fetchone()
            if existing:
                continue
            key = f"legacy:{channel_id}:{message_id}"
            prepared = False
            readiness: dict[str, Any] = {"source": "legacy_sqlite_migration", "media": {
                k: r.get(k) for k in ("title", "filename", "size", "duration", "width", "height", "fps", "bitrate", "codec", "audio_codec", "orientation", "resolution_category", "streamable", "caption", "identity_key", "oshash", "video_phash", "favorite", "status", "upload_date", "release_date")
            }}
            if state_columns:
                rs = db.conn.execute("SELECT * FROM resource_state WHERE media_id=?", (int(r["id"]),)).fetchone()
                if rs:
                    readiness["resource_state"] = dict(rs)
                    prepared = (
                        str(rs["metadata_state"] or "").upper() == "COMPLETE"
                        and str(rs["entity_state"] or "").upper() == "COMPLETE"
                        and str(rs["snapshot_state"] or "").upper() == "READY"
                    )
            if prepared and asset_columns:
                prepared = db.conn.execute(
                    "SELECT 1 FROM asset_registry WHERE media_id=? AND asset_type='PLAYBACK_REPRESENTATIVE' AND state='READY' LIMIT 1",
                    (int(r["id"]),),
                ).fetchone() is not None
            db.conn.execute(
                "INSERT OR IGNORE INTO telegram_document_registry(registry_key,identity_kind,filename,size_bytes,duration,width,height,caption,known_state,prepared_state,prepared_at,prepared_json,oshash,phash,media_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, "LEGACY_MESSAGE", str(r.get("filename") or r.get("title") or ""), int(r.get("size") or 0),
                 float(r.get("duration") or 0), int(r.get("width") or 0), int(r.get("height") or 0),
                 str(r.get("caption") or ""), "KNOWN", "PREPARED" if prepared else "PENDING",
                 now if prepared else None, _json(readiness, {}), str(r.get("oshash") or ""),
                 str(r.get("video_phash") or ""), int(r["id"]), now, now),
            )
            db.conn.execute(
                "INSERT OR IGNORE INTO telegram_document_locations(channel_id,message_id,registry_key,thumbnail_message_id,caption,location_state,last_seen_at) VALUES(?,?,?,?,?,'PRESENT',?)",
                (channel_id, message_id, key, int(r.get("thumbnail_message_id") or 0) or None, str(r.get("caption") or ""), now),
            )
        db.conn.commit()
        try:
            db._telegram_registry_ready = db.conn
        except Exception:
            _swallowed_note("telegram_registry.ensure_telegram_registry")
            pass


def register_known_message(db, *, channel_id: str | int, message_id: int,
                           record: dict[str, Any], media_id: int | None = None,
                           thumbnail_message_id: int | None = None) -> dict[str, Any]:
    """Upsert cheap message inventory, with no media download or preparation."""
    ensure_telegram_registry(db)
    channel, message = str(channel_id), int(message_id)
    if not channel or message <= 0:
        raise ValueError("channel_id and a positive message_id are required")
    if not channel.lstrip("-").isdigit():
        raise ValueError(f"not a Telegram channel id: {channel}")
    doc_id_raw = record.get("document_id")
    document_id = str(doc_id_raw) if doc_id_raw not in (None, "", 0, "0") else ""
    key = f"doc:{document_id}" if document_id else f"legacy:{channel}:{message}"
    now = _now()
    reference = str(record.get("file_reference_b64") or "")
    access_hash = str(record.get("access_hash") or "")
    date = str(record.get("date") or record.get("message_date") or "")
    caption = str(record.get("caption") or "")
    companions = list(record.get("companion_message_ids") or [])
    if thumbnail_message_id and int(thumbnail_message_id) not in {int(x) for x in companions if str(x).isdigit()}:
        companions.append(int(thumbnail_message_id))
    known = {
        "filename": str(record.get("name") or record.get("filename") or ""),
        "size_bytes": max(0, int(record.get("size") or record.get("size_bytes") or 0)),
        "duration": max(0.0, float(record.get("duration") or 0)),
        "width": max(0, int(record.get("width") or 0)),
        "height": max(0, int(record.get("height") or 0)),
        "mime_type": str(record.get("mime") or record.get("mime_type") or ""),
        "caption": caption, "message_date": date,
        "grouped_id": str(record.get("grouped_id") or ""),
        "companions": companions,
        "oshash": str(record.get("oshash") or ""),
        "phash": str(record.get("phash") or record.get("video_phash") or ""),
    }
    with db.lock:
        prior = db.conn.execute(
            "SELECT registry_key FROM telegram_document_locations WHERE channel_id=? AND message_id=?",
            (channel, message),
        ).fetchone()
        prior_key = str(prior["registry_key"] or "") if prior else ""
        # If a refresh lacks the Telegram document object, do not downgrade a
        # previously captured document identity to a location-only legacy key.
        if not document_id and prior_key.startswith("doc:"):
            key = prior_key
            document_id = prior_key[4:]
        # Preserve prepared artifacts and identity fingerprints; this is an
        # inventory-only refresh and must not downgrade a completed record.
        db.conn.execute(
            "INSERT INTO telegram_document_registry(registry_key,document_id,identity_kind,access_hash,filename,size_bytes,duration,width,height,mime_type,caption,message_date,grouped_id,companion_message_ids_json,known_state,prepared_state,prepared_json,oshash,phash,media_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'KNOWN','PENDING','{}',?,?,?, ?,?) "
            "ON CONFLICT(registry_key) DO UPDATE SET "
            "access_hash=CASE WHEN excluded.access_hash<>'' THEN excluded.access_hash ELSE telegram_document_registry.access_hash END, "
            "filename=CASE WHEN excluded.filename<>'' THEN excluded.filename ELSE telegram_document_registry.filename END, "
            "size_bytes=CASE WHEN excluded.size_bytes>0 THEN excluded.size_bytes ELSE telegram_document_registry.size_bytes END, "
            "duration=CASE WHEN excluded.duration>0 THEN excluded.duration ELSE telegram_document_registry.duration END, "
            "width=CASE WHEN excluded.width>0 THEN excluded.width ELSE telegram_document_registry.width END, "
            "height=CASE WHEN excluded.height>0 THEN excluded.height ELSE telegram_document_registry.height END, "
            "mime_type=CASE WHEN excluded.mime_type<>'' THEN excluded.mime_type ELSE telegram_document_registry.mime_type END, "
            "caption=CASE WHEN excluded.caption<>'' THEN excluded.caption ELSE telegram_document_registry.caption END, "
            "message_date=CASE WHEN excluded.message_date<>'' THEN excluded.message_date ELSE telegram_document_registry.message_date END, "
            "grouped_id=CASE WHEN excluded.grouped_id<>'' THEN excluded.grouped_id ELSE telegram_document_registry.grouped_id END, "
            "companion_message_ids_json=CASE WHEN excluded.companion_message_ids_json<>'[]' THEN excluded.companion_message_ids_json ELSE telegram_document_registry.companion_message_ids_json END, "
            "oshash=CASE WHEN excluded.oshash<>'' THEN excluded.oshash ELSE telegram_document_registry.oshash END, "
            "phash=CASE WHEN excluded.phash<>'' THEN excluded.phash ELSE telegram_document_registry.phash END, "
            "media_id=COALESCE(excluded.media_id,telegram_document_registry.media_id), updated_at=excluded.updated_at",
            (key, document_id or None, "TELEGRAM_DOCUMENT" if document_id else "LEGACY_MESSAGE", access_hash,
             known["filename"], known["size_bytes"], known["duration"], known["width"], known["height"],
             known["mime_type"], known["caption"], known["message_date"], known["grouped_id"],
             _json(known["companions"], []), known["oshash"], known["phash"], int(media_id) if media_id else None, now, now),
        )
        db.conn.execute(
            "INSERT INTO telegram_document_locations(channel_id,message_id,registry_key,access_hash,file_reference_b64,thumbnail_message_id,message_date,caption,location_state,last_seen_at) "
            "VALUES(?,?,?,?,?,?,?,?, 'PRESENT',?) ON CONFLICT(channel_id,message_id) DO UPDATE SET "
            "registry_key=excluded.registry_key,access_hash=CASE WHEN excluded.access_hash<>'' THEN excluded.access_hash ELSE telegram_document_locations.access_hash END, "
            "file_reference_b64=CASE WHEN excluded.file_reference_b64<>'' THEN excluded.file_reference_b64 ELSE telegram_document_locations.file_reference_b64 END, "
            "thumbnail_message_id=COALESCE(excluded.thumbnail_message_id,telegram_document_locations.thumbnail_message_id), "
            "message_date=CASE WHEN excluded.message_date<>'' THEN excluded.message_date ELSE telegram_document_locations.message_date END, "
            "caption=CASE WHEN excluded.caption<>'' THEN excluded.caption ELSE telegram_document_locations.caption END, "
            "location_state='PRESENT',last_seen_at=excluded.last_seen_at",
            (channel, message, key, access_hash, reference, int(thumbnail_message_id) if thumbnail_message_id else None,
             date, caption, now),
        )
        if prior_key and prior_key != key:
            db.conn.execute(
                "DELETE FROM telegram_document_registry WHERE registry_key=? AND identity_kind='LEGACY_MESSAGE' "
                "AND NOT EXISTS(SELECT 1 FROM telegram_document_locations WHERE registry_key=?)",
                (prior_key, prior_key),
            )
        db.conn.commit()
    return {"registry_key": key, "document_id": document_id or None, "identity_kind": "TELEGRAM_DOCUMENT" if key.startswith("doc:") else "LEGACY_MESSAGE"}


def mark_prepared(db, *, channel_id: str | int, message_id: int,
                  prepared: dict[str, Any] | None = None, oshash: str = "", phash: str = "") -> bool:
    """Mark the matching document PREPARED once; safe to repeat after a crash."""
    now = _now()
    with db.lock:
        location = db.conn.execute(
            "SELECT registry_key FROM telegram_document_locations WHERE channel_id=? AND message_id=?",
            (str(channel_id), int(message_id)),
        ).fetchone()
        if not location:
            return False
        cur = db.conn.execute(
            "UPDATE telegram_document_registry SET prepared_state='PREPARED',prepared_at=COALESCE(prepared_at,?), "
            "prepared_json=CASE WHEN prepared_state='PREPARED' AND prepared_json<>'{}' THEN prepared_json ELSE ? END, "
            "oshash=CASE WHEN ?<>'' THEN ? ELSE oshash END,phash=CASE WHEN ?<>'' THEN ? ELSE phash END,updated_at=? "
            "WHERE registry_key=?",
            (now, _json(prepared or {}, {}), str(oshash or ""), str(oshash or ""), str(phash or ""), str(phash or ""), now, str(location["registry_key"])),
        )
        db.conn.commit()
        return cur.rowcount > 0




# ==============================================================================
# SECTION: Lookups and channel scan records
# Location lookup, scan records and diagnostics.
# ==============================================================================

def document_for_location(db, channel_id: str | int, message_id: int) -> dict[str, Any] | None:
    with db.lock:
        row = db.conn.execute(
            "SELECT r.*,l.access_hash AS location_access_hash,l.file_reference_b64,l.thumbnail_message_id,l.location_state "
            "FROM telegram_document_locations l JOIN telegram_document_registry r ON r.registry_key=l.registry_key "
            "WHERE l.channel_id=? AND l.message_id=? LIMIT 1", (str(channel_id), int(message_id)),
        ).fetchone()
    return dict(row) if row else None


def record_channel_scan(db, channel_id: str | int, *, high_water_mark: int = 0,
                        status: str = "COMPLETE", detail: dict[str, Any] | None = None) -> None:
    now = _now()
    with db.lock:
        db.conn.execute(
            "INSERT INTO telegram_channel_registry_state(channel_id,high_water_mark,last_scan_at,last_scan_status,last_scan_detail,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET "
            "high_water_mark=MAX(telegram_channel_registry_state.high_water_mark,excluded.high_water_mark), "
            "last_scan_at=excluded.last_scan_at,last_scan_status=excluded.last_scan_status, "
            "last_scan_detail=excluded.last_scan_detail,updated_at=excluded.updated_at",
            (str(channel_id), max(0, int(high_water_mark)), now, str(status or "COMPLETE"), _json(detail or {}, {}), now),
        )
        db.conn.commit()


def recent_channel_scan_diagnostics(db, limit: int = 12) -> list[dict[str, Any]]:
    """Read saved per-channel scan diagnostics from the existing registry state table."""
    ensure_telegram_registry(db)
    with db.lock:
        rows = db.conn.execute(
            "SELECT channel_id,last_scan_at,last_scan_status,high_water_mark,last_scan_detail "
            "FROM telegram_channel_registry_state WHERE last_scan_at<>'' "
            "ORDER BY last_scan_at DESC LIMIT ?", (max(1, min(50, int(limit))),),
        ).fetchall()
    out = []
    for row in rows:
        try:
            detail = json.loads(row["last_scan_detail"] or "{}")
            if not isinstance(detail, dict):
                detail = {}
        except Exception:
            detail = {}
        out.append({
            "channel_id": str(row["channel_id"]),
            "last_scan_at": str(row["last_scan_at"] or ""),
            "status": str(row["last_scan_status"] or "UNKNOWN"),
            "high_water_mark": int(row["high_water_mark"] or 0),
            "messages_scanned": int(detail.get("messages") or 0),
            "new_videos_found": int(detail.get("new_videos_found") or 0),
            "floodwaits": int(detail.get("floodwaits") or 0),
            "full_history": bool(detail.get("full_history")),
        })
    return out



# ==============================================================================
# SECTION: Thumbnail links
# Linked thumbnail resolution.
# ==============================================================================

def resolve_linked_thumbnail_message_id(db, *, channel_id: str | int, video_message_id: int,
                                         media_thumbnail_message_id: int | None = None,
                                         location: dict[str, Any] | None = None) -> int:
    """Resolve only an explicitly linked Telegram thumbnail/contact-sheet message.

    Prefer the media row, then the Telegram location registry, then the playback
    thumbnail cache. Never infer a companion from adjacent message IDs or titles.
    """
    video_id = int(video_message_id)
    for candidate in (
        media_thumbnail_message_id,
        (location or {}).get("thumbnail_message_id"),
    ):
        try:
            value = int(candidate or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0 and value != video_id:
            return value

    if _has_table(db, "thumbnail_cache"):
        try:
            with db.lock:
                row = db.conn.execute(
                    "SELECT thumbnail_message_id FROM thumbnail_cache "
                    "WHERE source_channel=? AND video_message_id=? "
                    "AND COALESCE(thumbnail_message_id,0)>0 ORDER BY updated_at DESC LIMIT 1",
                    (str(channel_id), video_id),
                ).fetchone()
            value = int((row["thumbnail_message_id"] if row else 0) or 0)
            if value > 0 and value != video_id:
                return value
        except Exception:
            # An absent or legacy cache table should not prevent a video move;
            # the canonical media/location associations were checked above.
            _swallowed_note("telegram_registry.resolve_linked_thumbnail_message_id")
            pass
    return 0


def thumbnail_message_is_unshared(db, *, channel_id: str | int, video_message_id: int,
                                  thumbnail_message_id: int) -> bool:
    """Return whether an explicitly linked thumbnail has no other saved owners.

    Fail closed if the association cannot be checked. A thumbnail may be
    deleted with its video only when no other active video, playback-cache row,
    or Telegram location points to it.
    """
    try:
        channel_key = str(channel_id)
        video_id = int(video_message_id)
        thumbnail_id = int(thumbnail_message_id)
    except (TypeError, ValueError):
        return False
    if not channel_key or video_id <= 0 or thumbnail_id <= 0 or thumbnail_id == video_id:
        return False

    active_sql = "UPPER(COALESCE(status,'')) NOT IN ('SOURCE_DELETED','DELETED_BY_ARCHIVE_X')"
    try:
        with db.reading() as rdb:
            tables = {
                str(row[0])
                for row in rdb.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
            if "media" not in tables:
                return False
            another_video_owns_id = rdb.execute(
                "SELECT 1 FROM media WHERE CAST(source_channel AS TEXT)=? "
                "AND CAST(source_message_id AS INTEGER)=? AND " + active_sql + " LIMIT 1",
                (channel_key, thumbnail_id),
            ).fetchone()
            another_video_uses_thumb = rdb.execute(
                "SELECT 1 FROM media WHERE CAST(source_channel AS TEXT)=? "
                "AND CAST(thumbnail_message_id AS INTEGER)=? "
                "AND CAST(source_message_id AS INTEGER)<>? AND " + active_sql + " LIMIT 1",
                (channel_key, thumbnail_id, video_id),
            ).fetchone()
            if another_video_owns_id or another_video_uses_thumb:
                return False

            if "thumbnail_cache" in tables:
                cached_reference = rdb.execute(
                    "SELECT 1 FROM thumbnail_cache AS tc "
                    "LEFT JOIN media AS m ON CAST(m.source_channel AS TEXT)=CAST(tc.source_channel AS TEXT) "
                    "AND CAST(m.source_message_id AS INTEGER)=CAST(tc.video_message_id AS INTEGER) "
                    "WHERE CAST(tc.source_channel AS TEXT)=? "
                    "AND CAST(tc.thumbnail_message_id AS INTEGER)=? "
                    "AND CAST(tc.video_message_id AS INTEGER)<>? "
                    "AND (m.id IS NULL OR " + active_sql.replace("status", "m.status") + ") LIMIT 1",
                    (channel_key, thumbnail_id, video_id),
                ).fetchone()
                if cached_reference:
                    return False

            if "telegram_document_locations" in tables:
                registry_reference = rdb.execute(
                    "SELECT 1 FROM telegram_document_locations "
                    "WHERE CAST(channel_id AS TEXT)=? "
                    "AND CAST(thumbnail_message_id AS INTEGER)=? "
                    "AND CAST(message_id AS INTEGER)<>? "
                    "AND UPPER(COALESCE(location_state,'')) NOT IN "
                    "('DELETED_BY_ARCHIVE_X','DELETED_BY_CHANNEL_MAINTENANCE') LIMIT 1",
                    (channel_key, thumbnail_id, video_id),
                ).fetchone()
                if registry_reference:
                    return False
    except Exception:
        return False
    return True




# ==============================================================================
# SECTION: Maintenance
# Location rekeying, pending deletes and high-water marks.
# ==============================================================================

def rekey_location(db, *, old_channel: str, old_message: int, new_channel: str,
                   new_message: int, thumbnail_message_id: int | None = None,
                   destination_record: dict[str, Any] | None = None) -> None:
    """Move a registry location while retaining its document's prepared state."""
    now = _now()
    old_channel, new_channel = str(old_channel), str(new_channel)
    old_message, new_message = int(old_message), int(new_message)
    with db.lock:
        row = db.conn.execute(
            "SELECT * FROM telegram_document_locations WHERE channel_id=? AND message_id=?", (old_channel, old_message),
        ).fetchone()
        if row:
            rec = dict(row)
            # A destination row may already exist for this document; preserve it
            # and simply retire the source location instead of making duplicates.
            db.conn.execute(
                "INSERT INTO telegram_document_locations(channel_id,message_id,registry_key,access_hash,file_reference_b64,thumbnail_message_id,message_date,caption,location_state,last_seen_at) "
                "VALUES(?,?,?,?,?,?,?,?, 'PRESENT',?) ON CONFLICT(channel_id,message_id) DO UPDATE SET "
                "registry_key=excluded.registry_key,access_hash=excluded.access_hash,file_reference_b64=excluded.file_reference_b64, "
                "thumbnail_message_id=COALESCE(excluded.thumbnail_message_id,telegram_document_locations.thumbnail_message_id), "
                "message_date=excluded.message_date,caption=excluded.caption,location_state='PRESENT',last_seen_at=excluded.last_seen_at",
                (new_channel, new_message, rec["registry_key"], rec.get("access_hash"),
                 str((destination_record or {}).get("file_reference_b64") or rec.get("file_reference_b64") or ""),
                 int(thumbnail_message_id or rec.get("thumbnail_message_id") or 0) or None,
                 str((destination_record or {}).get("date") or rec.get("message_date") or ""),
                 str((destination_record or {}).get("caption") or rec.get("caption") or ""), now),
            )
            db.conn.execute(
                "UPDATE telegram_document_locations SET location_state='MOVED_BY_ARCHIVE_X',last_seen_at=? WHERE channel_id=? AND message_id=?",
                (now, old_channel, old_message),
            )
        db.conn.commit()


async def retry_pending_move_deletions(db, client, *, limit: int = 10) -> dict[str, int]:
    """Retry only source deletions whose destination was already verified.

    The source document ID is rechecked before deletion to avoid deleting a
    different message if Telegram IDs or local cleanup state were corrupted.
    Missing identity is routed to review rather than guessed from filenames.
    """
    ensure_telegram_registry(db)
    with db.lock:
        rows = db.conn.execute(
            "SELECT * FROM telegram_move_cleanup WHERE status='PENDING' "
            "AND (next_attempt_at IS NULL OR next_attempt_at<=datetime('now')) "
            "ORDER BY created_at LIMIT ?", (max(1, min(100, int(limit))),),
        ).fetchall()
    result = {"checked": 0, "deleted": 0, "deferred": 0, "needs_review": 0}
    for row_obj in rows:
        row = dict(row_obj)
        result["checked"] += 1
        source_channel = str(row.get("source_channel") or "")
        source_message_id = int(row.get("source_message_id") or 0)
        expected_identity = str(row.get("document_id") or "")
        expected_photo_id = ""
        if expected_identity.startswith("photo:"):
            expected_photo_id = expected_identity.partition(":")[2]
            if not expected_photo_id.isdigit() or int(expected_photo_id) <= 0:
                expected_photo_id = ""
        if not expected_identity or (expected_identity.startswith("photo:") and not expected_photo_id):
            with db.lock:
                db.conn.execute(
                    "UPDATE telegram_move_cleanup SET status='NEEDS_REVIEW',last_error=?,updated_at=? "
                    "WHERE source_channel=? AND source_message_id=?",
                    ("No valid document/photo identity was stored; automatic deletion was refused", _now(), source_channel, source_message_id),
                )
                db.conn.commit()
            result["needs_review"] += 1
            continue
        try:
            try:
                source_peer = int(source_channel)
            except (TypeError, ValueError):
                source_peer = source_channel
            message = await client.get_messages(source_peer, ids=source_message_id)
            if message is None:
                with db.lock:
                    db.conn.execute(
                        "UPDATE telegram_move_cleanup SET status='DONE',last_error='',updated_at=? "
                        "WHERE source_channel=? AND source_message_id=?",
                        (_now(), source_channel, source_message_id),
                    )
                    db.conn.execute(
                        "UPDATE telegram_document_locations SET location_state='MOVED_BY_ARCHIVE_X',last_seen_at=? "
                        "WHERE channel_id=? AND message_id=?", (_now(), source_channel, source_message_id),
                    )
                    db.conn.commit()
                result["deleted"] += 1
                continue
            document = getattr(message, "document", None) or getattr(getattr(message, "media", None), "document", None)
            if expected_photo_id:
                photo = getattr(message, "photo", None) or getattr(getattr(message, "media", None), "photo", None)
                actual_identity = str(getattr(photo, "id", "") or "")
                identity_kind = "photo"
                identity_matches = actual_identity == expected_photo_id
            else:
                actual_identity = str(getattr(document, "id", "") or "")
                identity_kind = "document"
                identity_matches = actual_identity == expected_identity
            if not identity_matches:
                with db.lock:
                    db.conn.execute(
                        "UPDATE telegram_move_cleanup SET status='NEEDS_REVIEW',last_error=?,updated_at=? "
                        "WHERE source_channel=? AND source_message_id=?",
                        (f"Source identity changed (expected {identity_kind} {expected_photo_id or expected_identity}, saw {actual_identity or 'none'})", _now(), source_channel, source_message_id),
                    )
                    db.conn.commit()
                result["needs_review"] += 1
                continue
            await client.delete_messages(source_peer, [source_message_id])
            remaining = await client.get_messages(source_peer, ids=source_message_id)
            if remaining is None:
                with db.lock:
                    db.conn.execute(
                        "UPDATE telegram_move_cleanup SET status='DONE',last_error='',updated_at=? "
                        "WHERE source_channel=? AND source_message_id=?",
                        (_now(), source_channel, source_message_id),
                    )
                    db.conn.execute(
                        "UPDATE telegram_document_locations SET location_state='MOVED_BY_ARCHIVE_X',last_seen_at=? "
                        "WHERE channel_id=? AND message_id=?", (_now(), source_channel, source_message_id),
                    )
                    db.conn.commit()
                result["deleted"] += 1
            else:
                raise RuntimeError("Telegram still returns the source after deletion")
        except Exception as exc:
            retry_count = int(row.get("retry_count") or 0) + 1
            delay_minutes = min(360, max(1, 2 ** min(retry_count, 8)))
            with db.lock:
                db.conn.execute(
                    "UPDATE telegram_move_cleanup SET retry_count=?,last_error=?,next_attempt_at=datetime('now',?),updated_at=? "
                    "WHERE source_channel=? AND source_message_id=?",
                    (retry_count, f"{type(exc).__name__}: {exc}"[:500], f"+{delay_minutes} minutes", _now(), source_channel, source_message_id),
                )
                db.conn.commit()
            result["deferred"] += 1
    return result


def advance_channel_high_water(db, channel_id: str | int, message_id: int, *, status: str = "MOVE_REGISTERED") -> int:
    """Record a directly registered Telegram message without pretending a scan ran."""
    channel = str(channel_id)
    message_id = max(0, int(message_id))
    now = _now()
    with db.lock:
        row = db.conn.execute(
            "SELECT high_water_mark,last_scan_at,last_scan_status,last_scan_detail FROM telegram_channel_registry_state WHERE channel_id=?",
            (channel,),
        ).fetchone()
        high = max(message_id, int(row["high_water_mark"] or 0)) if row else message_id
        db.conn.execute(
            "INSERT INTO telegram_channel_registry_state(channel_id,high_water_mark,last_scan_at,last_scan_status,last_scan_detail,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(channel_id) DO UPDATE SET "
            "high_water_mark=MAX(telegram_channel_registry_state.high_water_mark,excluded.high_water_mark), "
            "last_scan_status=excluded.last_scan_status,last_scan_detail=excluded.last_scan_detail,updated_at=excluded.updated_at",
            (channel, high, str(row["last_scan_at"] or "") if row else None, status, _json({"source": "direct_message_registration", "message_id": message_id}, {}), now),
        )
        db.conn.commit()
    # Keep the legacy quick-scan checkpoint aligned so a later scan does not
    # prepare/re-download a message already registered through a verified move/upload.
    try:
        key = f"inventory_highwater_v1_{int(channel)}"
        prior = db.setting(key, None)
        prior_value = max(0, int(prior or 0))
        if high > prior_value:
            db.set_setting(key, high)
    except (TypeError, ValueError):
        pass
    return high
