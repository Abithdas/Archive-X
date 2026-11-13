"""Canonical local resource registry and universal single-flight coordination.

Archive X treats Telegram as the large-media store and SQLite/filesystem as the
control plane. This module centralizes cheap local state checks and duplicate
work joining without performing network I/O itself.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Decision type
#   - Schema and unknown-entity defaults
#   - Media and message state
#   - Single-flight coordination
#   - Statistics and local preparation
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import asyncio
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


NO_MEDIA_ACCESS = "NO_MEDIA_ACCESS"
METADATA_ONLY = "METADATA_ONLY"
RANGE_ACCESS = "RANGE_ACCESS"
FULL_DOWNLOAD = "FULL_DOWNLOAD"

LOCAL_CACHE_HIT = "LOCAL_CACHE_HIT"
LOCAL_ASSET_HIT = "LOCAL_ASSET_HIT"
LOCAL_REFERENCE_HIT = "LOCAL_REFERENCE_HIT"
TELEGRAM_INDEX_REQUIRED = "TELEGRAM_INDEX_REQUIRED"
TELEGRAM_RANGE_REQUIRED = "TELEGRAM_RANGE_REQUIRED"
TELEGRAM_FULL_DOWNLOAD_REQUIRED = "TELEGRAM_FULL_DOWNLOAD_REQUIRED"
PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
STALE_REFRESH_REQUIRED = "STALE_REFRESH_REQUIRED"
DEDUP_JOINED = "DEDUP_JOINED"




# ==============================================================================
# SECTION: Decision type
# Local-hit versus external resource decisions.
# ==============================================================================

@dataclass(frozen=True)
class ResourceDecision:
    """Whether a resource is served locally or fetched externally."""
    access: str
    reason: str
    detail: str = ""


def external(access: str, reason: str, detail: str = "") -> ResourceDecision:
    return ResourceDecision(access, reason, detail)


_RESOURCE_SYNC_FULL_SWEEP_SECONDS = 300.0




# ==============================================================================
# SECTION: Schema and unknown-entity defaults
# Registry tables and default unknown performer links.
# ==============================================================================

def ensure_resource_tables(db) -> None:
    """Create registry tables/views once, then keep resource_state in step with media.

    v29.4.386: this runs on every status poll. It used to re-run the whole schema
    script (executescript also COMMITs any open transaction), re-scan every media
    row, and DROP/CREATE three views each time: up to ~175 ms holding the shared
    write lock, and every schema change forced all read connections to re-prepare.
    Now the schema/views/repairs run once per write connection; later calls only
    add resource_state rows for new media (cheap MAX(id) check, plus a periodic
    full OR IGNORE sweep as a safety net for out-of-order ids).
    """
    import time as _time
    conn = db.conn
    if getattr(db, "_resource_tables_ready_for", None) is conn:
        with db.lock:
            row = conn.execute("SELECT COALESCE(MAX(id),0) FROM media").fetchone()
            max_id = int(row[0] or 0)
            last_max = int(getattr(db, "_resource_state_synced_max", 0) or 0)
            due_full = _time.monotonic() - float(getattr(db, "_resource_state_full_sweep_at", 0.0) or 0.0) >= _RESOURCE_SYNC_FULL_SWEEP_SECONDS
            if max_id <= last_max and not due_full:
                return
            now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec='seconds')
            own_tx = not conn.in_transaction
            if due_full:
                cur = conn.execute("INSERT OR IGNORE INTO resource_state(media_id,updated_at) SELECT id,? FROM media", (now,))
                db._resource_state_full_sweep_at = _time.monotonic()
            else:
                cur = conn.execute("INSERT OR IGNORE INTO resource_state(media_id,updated_at) SELECT id,? FROM media WHERE id>?", (now, last_max))
            db._resource_state_synced_max = max_id
            if own_tx and conn.in_transaction:
                conn.commit()
        return
    _ensure_resource_schema(db)
    try:
        db._resource_tables_ready_for = conn
        db._resource_state_full_sweep_at = _time.monotonic()
        with db.lock:
            db._resource_state_synced_max = int(conn.execute("SELECT COALESCE(MAX(id),0) FROM media").fetchone()[0] or 0)
    except Exception:
        _swallowed_note("resource_registry.ensure_resource_tables")
        pass


def _ensure_resource_schema(db) -> None:
    """One-time (per write connection) schema, repair and view installation."""
    with db.lock:
        db.conn.executescript("""
        CREATE TABLE IF NOT EXISTS resource_state (
            media_id INTEGER PRIMARY KEY,
            catalogue_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            metadata_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            fingerprint_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            snapshot_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            entity_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            provider_state TEXT NOT NULL DEFAULT 'UNKNOWN',
            content_revision INTEGER NOT NULL DEFAULT 1,
            metadata_revision INTEGER NOT NULL DEFAULT 1,
            last_reason TEXT,
            last_external_access TEXT,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS asset_registry (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            path TEXT NOT NULL,
            sha256 TEXT,
            size_bytes INTEGER DEFAULT 0,
            state TEXT NOT NULL DEFAULT 'READY',
            content_revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_type, entity_id, asset_type)
        );
        CREATE INDEX IF NOT EXISTS idx_asset_registry_media ON asset_registry(media_id, asset_type);
        CREATE INDEX IF NOT EXISTS idx_asset_registry_state ON asset_registry(state, updated_at);
        CREATE TABLE IF NOT EXISTS resource_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            event_type TEXT NOT NULL,
            reason TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resource_events_media ON resource_events(media_id, created_at);
        CREATE TABLE IF NOT EXISTS work_registry (
            work_key TEXT PRIMARY KEY,
            work_type TEXT NOT NULL,
            media_id INTEGER,
            state TEXT NOT NULL,
            reason TEXT,
            started_at REAL,
            completed_at REAL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS split_file_groups (
            group_id TEXT PRIMARY KEY,
            destination_channel TEXT NOT NULL,
            logical_filename TEXT NOT NULL,
            primary_message_id INTEGER,
            thumbnail_message_id INTEGER,
            total_size INTEGER NOT NULL DEFAULT 0,
            part_count INTEGER NOT NULL DEFAULT 0,
            mime_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            duration REAL NOT NULL DEFAULT 0,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            fps REAL NOT NULL DEFAULT 0,
            bitrate INTEGER NOT NULL DEFAULT 0,
            codec TEXT NOT NULL DEFAULT '',
            audio_codec TEXT NOT NULL DEFAULT '',
            caption TEXT NOT NULL DEFAULT '',
            identity_key TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PREPARING',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_split_group_primary
            ON split_file_groups(destination_channel,primary_message_id)
            WHERE primary_message_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS split_file_parts (
            group_id TEXT NOT NULL,
            part_index INTEGER NOT NULL,
            destination_channel TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            logical_filename TEXT NOT NULL,
            telegram_filename TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(group_id,part_index),
            UNIQUE(destination_channel,message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_split_parts_message
            ON split_file_parts(destination_channel,message_id);
        """)
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(timespec='seconds')
        db.conn.execute("""INSERT OR IGNORE INTO resource_state(media_id,updated_at)
                           SELECT id,? FROM media""", (now,))
        # V29.4.127 repair: every media row is a catalogued video. Channels
        # scanned by Metadata & Assets before this fix were left 'UNKNOWN',
        # which kept their videos (and the channel) out of the Library.
        # Runs once per database connection (this function is called often).
        if getattr(db, "_catalogue_state_repaired", None) is not db.conn:
            db.conn.execute("""UPDATE resource_state SET catalogue_state='COMPLETE', updated_at=?
                               WHERE catalogue_state='UNKNOWN'
                                 AND media_id IN (SELECT id FROM media)""", (now,))
            # V29.4.132 repair: a video whose metadata match ended in a
            # suggestion was left metadata 'UNKNOWN' (hidden from the Library)
            # and was not re-checked for 30 days. A suggestion finishes the
            # metadata step; the video stays "needs review" until confirmed.
            db.conn.execute("""UPDATE resource_state SET metadata_state='COMPLETE',
                                      provider_state=CASE WHEN provider_state='UNKNOWN' THEN 'COMPLETE' ELSE provider_state END,
                                      last_reason='SUGGESTION_PENDING_REVIEW', updated_at=?
                               WHERE metadata_state='UNKNOWN'
                                 AND media_id IN (SELECT id FROM media WHERE metadata_source='suggested')""", (now,))
            # Same for videos whose metadata is already applied but whose step
            # was left open: a suggestion confirmed before V29.4.132 (VERIFIED,
            # step UNKNOWN), or a matched video marked STALE, which metadata
            # match never selects again (it skips MATCHED/VERIFIED videos).
            db.conn.execute("""UPDATE resource_state SET metadata_state='COMPLETE',
                                      provider_state=CASE WHEN provider_state IN ('UNKNOWN','STALE') THEN 'COMPLETE' ELSE provider_state END,
                                      last_reason='METADATA_ALREADY_APPLIED', updated_at=?
                               WHERE metadata_state IN ('UNKNOWN','STALE')
                                 AND media_id IN (SELECT id FROM media WHERE identification_state IN ('MATCHED','VERIFIED'))""", (now,))
            try:
                db._catalogue_state_repaired = db.conn
            except Exception:
                _swallowed_note("resource_registry._ensure_resource_schema")
                pass
        # Published media is the strict, fully-enriched projection used by
        # workflows that require complete metadata and entity records.
        db.conn.execute("DROP VIEW IF EXISTS archive_x_published_media")
        db.conn.execute("""CREATE VIEW archive_x_published_media AS
            SELECT m.*
            FROM media m
            JOIN resource_state rs ON rs.media_id=m.id
            WHERE rs.catalogue_state='COMPLETE'
              AND COALESCE(m.status,'')<>'SOURCE_DELETED'
              AND rs.metadata_state='COMPLETE'
              AND rs.entity_state='COMPLETE'
              AND rs.snapshot_state='READY'
              AND EXISTS (
                  SELECT 1 FROM timeline_preview_cache tpc
                  WHERE tpc.source_channel=CAST(m.source_channel AS TEXT)
                    AND tpc.video_message_id=m.source_message_id
                    AND UPPER(COALESCE(tpc.status,''))='READY'
                    AND CAST(COALESCE(tpc.frame_count,0) AS INTEGER)=36
              )
              AND EXISTS (
                  SELECT 1 FROM thumbnail_cache tc
                  WHERE tc.source_channel=CAST(m.source_channel AS TEXT)
                    AND tc.video_message_id=m.source_message_id
                    AND tc.source='playback-snapshot'
                    AND tc.data IS NOT NULL AND length(tc.data)>0
              )
              AND COALESCE(m.streamable,0)=1
              -- Only a real Telegram peer may be a Library-facing video.
              -- Synthetic Content Automation / Local Upload staging identities
              -- can satisfy readiness internally, but must never appear as a
              -- duplicate card before the verified destination message exists.
              AND (CAST(m.source_channel AS TEXT) GLOB '-[0-9]*'
                   OR (CAST(m.source_channel AS TEXT)<>'' AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9]*'))
              AND (
                  -- V29.4.129: two indexed lookups instead of one OR'ed
                  -- condition. The OR made SQLite walk every READY asset for
                  -- every video (performer page: 10 s; Library slow).
                  EXISTS (SELECT 1 FROM asset_registry ar
                          WHERE ar.entity_type='telegram_video'
                            AND ar.entity_id=(CAST(m.source_channel AS TEXT) || ':' || CAST(m.source_message_id AS TEXT))
                            AND ar.asset_type='PLAYBACK_REPRESENTATIVE'
                            AND ar.state='READY')
                  OR EXISTS (SELECT 1 FROM asset_registry ar
                             WHERE ar.media_id=m.id
                               AND ar.asset_type='PLAYBACK_REPRESENTATIVE'
                               AND ar.state='READY')
              )
        """)
        # The catalogue view is also used by preparation workflows, so it keeps
        # scanned videos visible to internal queues even before preview assets are
        # complete. The user-facing Library view below is deliberately stricter:
        # a video is published there only when the full 36-frame snapshot set is
        # validated (resource_state.snapshot_state='READY'), the representative
        # image is registered READY, and the source is streamable.
        db.conn.execute("DROP VIEW IF EXISTS archive_x_catalogued_media")
        db.conn.execute("""CREATE VIEW archive_x_catalogued_media AS
            SELECT m.*,
                   COALESCE(rs.catalogue_state,'PENDING') AS library_catalogue_state,
                   COALESCE(rs.metadata_state,'PENDING') AS library_metadata_state,
                   COALESCE(rs.entity_state,'PENDING') AS library_entity_state,
                   COALESCE(rs.snapshot_state,'PENDING') AS library_snapshot_state,
                   CASE WHEN COALESCE(rs.snapshot_state,'PENDING')='READY'
                          AND EXISTS (
                              SELECT 1 FROM timeline_preview_cache tpc
                              WHERE tpc.source_channel=CAST(m.source_channel AS TEXT)
                                AND tpc.video_message_id=m.source_message_id
                                AND UPPER(COALESCE(tpc.status,''))='READY'
                                AND CAST(COALESCE(tpc.frame_count,0) AS INTEGER)=36
                          )
                          AND EXISTS (
                              SELECT 1 FROM thumbnail_cache tc
                              WHERE tc.source_channel=CAST(m.source_channel AS TEXT)
                                AND tc.video_message_id=m.source_message_id
                                AND tc.source='playback-snapshot'
                                AND tc.data IS NOT NULL AND length(tc.data)>0
                          )
                          AND COALESCE(m.streamable,0)=1
                          AND (
                              EXISTS (SELECT 1 FROM asset_registry ar
                                      WHERE ar.entity_type='telegram_video'
                                        AND ar.entity_id=(CAST(m.source_channel AS TEXT) || ':' || CAST(m.source_message_id AS TEXT))
                                        AND ar.asset_type='PLAYBACK_REPRESENTATIVE'
                                        AND ar.state='READY')
                              OR EXISTS (SELECT 1 FROM asset_registry ar
                                         WHERE ar.media_id=m.id
                                           AND ar.asset_type='PLAYBACK_REPRESENTATIVE'
                                           AND ar.state='READY')
                          )
                        THEN 1 ELSE 0 END AS library_ready,
                   CASE
                     WHEN COALESCE(rs.snapshot_state,'PENDING')<>'READY'
                       OR NOT EXISTS (
                           SELECT 1 FROM timeline_preview_cache tpc
                           WHERE tpc.source_channel=CAST(m.source_channel AS TEXT)
                             AND tpc.video_message_id=m.source_message_id
                             AND UPPER(COALESCE(tpc.status,''))='READY'
                             AND CAST(COALESCE(tpc.frame_count,0) AS INTEGER)=36
                       ) THEN 'Snapshots pending'
                     WHEN NOT EXISTS (SELECT 1 FROM thumbnail_cache tc
                         WHERE tc.source_channel=CAST(m.source_channel AS TEXT)
                           AND tc.video_message_id=m.source_message_id
                           AND tc.source='playback-snapshot'
                           AND tc.data IS NOT NULL AND length(tc.data)>0)
                       OR (NOT EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.entity_type='telegram_video'
                         AND ar.entity_id=(CAST(m.source_channel AS TEXT) || ':' || CAST(m.source_message_id AS TEXT))
                         AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY')
                       AND NOT EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.media_id=m.id
                          AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY')) THEN 'Representative image pending'
                     WHEN COALESCE(m.streamable,0)<>1 THEN 'Not streamable'
                     ELSE 'Ready'
                   END AS library_state
            FROM media m
            LEFT JOIN resource_state rs ON rs.media_id=m.id
            WHERE COALESCE(m.status,'')<>'SOURCE_DELETED'
              AND COALESCE(m.source_message_id,0)>0
              AND (CAST(m.source_channel AS TEXT) GLOB '-[0-9]*'
                   OR (CAST(m.source_channel AS TEXT)<>'' AND CAST(m.source_channel AS TEXT) NOT GLOB '*[^0-9]*'))
        """)
        db.conn.execute("DROP VIEW IF EXISTS archive_x_library_media")
        db.conn.execute("""CREATE VIEW archive_x_library_media AS
            SELECT cm.*
            FROM archive_x_catalogued_media cm
            WHERE
              -- Only publish a video into the Library after its validated 36-frame
              -- set, representative image, and streamability are all ready. The
              -- underlying archive_x_catalogued_media view remains unrestricted so
              -- snapshot preparation can still discover and repair pending videos.
              cm.library_ready=1
              -- A split group is invisible until every part and its preview are verified.
              AND NOT EXISTS (
                  SELECT 1 FROM split_file_groups sg
                  WHERE sg.destination_channel=CAST(cm.source_channel AS TEXT)
                    AND sg.primary_message_id=cm.source_message_id
                    AND sg.status<>'VERIFIED'
              )
              -- Never expose the additional physical volume messages as separate cards.
              AND NOT EXISTS (
                  SELECT 1 FROM split_file_parts sp
                  JOIN split_file_groups sg ON sg.group_id=sp.group_id
                  WHERE sp.destination_channel=CAST(cm.source_channel AS TEXT)
                    AND sp.message_id=cm.source_message_id
                    AND (sg.status<>'VERIFIED' OR sp.message_id<>sg.primary_message_id)
              )
        """)
        db.conn.commit()
    # V29.4.131: track changes to the tables created here as well.
    from . import change_tracking
    change_tracking.ensure(db)


def ensure_unknown_performer_local_profile(db) -> bool:
    """Keep the protected Unknown Performer fallback as a local-only entity.

    A generic fallback name must never inherit a real person's provider IDs or
    provider metadata. Preserve any explicitly locked local profile/image, but
    remove unlocked provider-derived fields so its details page behaves like
    the local-only Unknown Studio profile.
    """
    if not hasattr(db, "lock") or not hasattr(db, "conn"):
        return False
    with db.lock:
        row = db.conn.execute(
            "SELECT * FROM performers WHERE lower(trim(name))='unknown performer' LIMIT 1"
        ).fetchone()
        if not row:
            return False
        keys = set(row.keys())
        image_locked = bool(row["image_locked"] or 0) if "image_locked" in keys else False
        image_source = str(row["image_source"] or "").strip().lower() if "image_source" in keys else ""
        current_image = str(row["image"] or "").strip() if "image" in keys else ""
        provider_ids_raw = str(row["provider_ids"] or "").strip() if "provider_ids" in keys else ""
        has_provider_ids = provider_ids_raw not in {"", "{}", "null", "[]"}
        profile_locked = bool(row["profile_locked"] or 0) if "profile_locked" in keys else False
        local_image_source = image_source == "manual" or image_source.startswith("provider-selected:")
        provider_image_source = image_source in {"provider", "stashdb", "stash_db", "theporndb", "tpdb"} or image_source.startswith("provider:")
        # Do not discard an image simply because its legacy source marker is
        # missing. A provider-linked, unlocked image can be removed; all other
        # existing local image state is kept intact.
        remove_provider_image = bool(not image_locked and current_image and (provider_image_source or has_provider_ids))
        preserve_image = image_locked or local_image_source or not remove_provider_image

        assignments = ["provider_ids='{}'"]
        if "name_locked" in keys:
            assignments.append("name_locked=1")
        if not profile_locked:
            for column in ("profile", "bio"):
                if column in keys:
                    assignments.append(f"{column}=NULL")
        if "gender" in keys:
            assignments.append("gender=NULL")
        if not preserve_image:
            for column, value in (("image", "NULL"), ("image_source", "NULL"), ("image_crop_version", "0")):
                if column in keys:
                    assignments.append(f"{column}={value}")

        db.conn.execute(
            f"UPDATE performers SET {', '.join(assignments)} WHERE id=?", (int(row["id"]),)
        )
        tables = {str(r[0]) for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "entity_metadata" in tables:
            db.conn.execute("DELETE FROM entity_metadata WHERE entity_type='performer' AND entity_id=?", (int(row["id"]),))
        if "entity_refresh_state" in tables:
            db.conn.execute("DELETE FROM entity_refresh_state WHERE entity_type='performer' AND entity_id=?", (int(row["id"]),))
        if not preserve_image and "performer_image_cache" in tables:
            db.conn.execute("DELETE FROM performer_image_cache WHERE performer_id=?", (int(row["id"]),))
        db.conn.commit()
        return True


def ensure_unknown_entity_links(db, media_ids: list[int] | tuple[int, ...] | None = None) -> int:
    """Classify missing performer/studio relationships as explicit Unknown entities."""
    if not hasattr(db, "lock") or not hasattr(db, "conn"):
        return 0
    ensure_resource_tables(db)
    with db.lock:
        db.conn.execute("INSERT OR IGNORE INTO performers(name) VALUES('Unknown Performer')")
        db.conn.execute("INSERT OR IGNORE INTO studios(name) VALUES('Unknown Studio')")
        perf = db.conn.execute("SELECT id FROM performers WHERE name='Unknown Performer' LIMIT 1").fetchone()
        studio = db.conn.execute("SELECT id FROM studios WHERE name='Unknown Studio' LIMIT 1").fetchone()
        if not perf or not studio:
            return 0
        # Reserved fallback profiles must remain local-only even if an older
        # build accidentally associated them with a provider record.
        ensure_unknown_performer_local_profile(db)
        params=[]
        where='1=1'
        if media_ids:
            mids=[int(x) for x in media_ids if int(x)>0]
            if not mids: return 0
            where=f"m.id IN ({','.join('?' for _ in mids)})"
            params.extend(mids)
        rows=db.conn.execute(
            f"SELECT m.id FROM media m WHERE {where} "
            "AND NOT EXISTS (SELECT 1 FROM video_performers vp WHERE vp.video_id=m.id) "
            "AND NOT EXISTS (SELECT 1 FROM local_video_performers lvp WHERE lvp.video_id=m.id) "
            "ORDER BY m.id", params).fetchall()
        changed=0
        for row in rows:
            db.conn.execute("INSERT OR IGNORE INTO video_performers(video_id,performer_id) VALUES(?,?)", (int(row['id']), int(perf['id'])))
            changed += 1
        rows=db.conn.execute(
            f"SELECT m.id FROM media m WHERE {where} "
            "AND NOT EXISTS (SELECT 1 FROM video_studios vs WHERE vs.video_id=m.id) "
            "ORDER BY m.id", params).fetchall()
        for row in rows:
            db.conn.execute("INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) VALUES(?,?,?)", (int(row['id']), int(studio['id']), 1.0))
            changed += 1
        db.conn.commit()
        return changed




# ==============================================================================
# SECTION: Media and message state
# Publication state and syncing of media and messages.
# ==============================================================================

def mark_media_entity_complete(db, media_ids: list[int] | tuple[int, ...] | None = None) -> int:
    """Finalize entity resolution after the performer/studio phases."""
    if not hasattr(db, "lock") or not hasattr(db, "conn"):
        return 0
    ensure_unknown_entity_links(db, media_ids)
    with db.lock:
        params=[]; where='1=1'
        if media_ids:
            mids=[int(x) for x in media_ids if int(x)>0]
            if not mids: return 0
            where=f"media_id IN ({','.join('?' for _ in mids)})"; params.extend(mids)
        cur=db.conn.execute(f"UPDATE resource_state SET entity_state='COMPLETE', updated_at=datetime('now') WHERE {where}", params)
        db.conn.commit()
        return int(cur.rowcount or 0)


def sync_media_state(db, media_id: int, *, catalogue: str | None = None,
                     metadata: str | None = None, fingerprint: str | None = None,
                     snapshot: str | None = None, entity: str | None = None,
                     provider: str | None = None, reason: str | None = None,
                     external_access: str | None = None, bump_revision: bool = False) -> None:
    """Update a media row's readiness states and the matching registry records."""
    ensure_resource_tables(db)
    fields = {
        "catalogue_state": catalogue, "metadata_state": metadata,
        "fingerprint_state": fingerprint, "snapshot_state": snapshot,
        "entity_state": entity, "provider_state": provider,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    if bump_revision:
        fields["content_revision"] = "content_revision + 1"
        fields["metadata_revision"] = "metadata_revision + 1"
    from .. import config
    with db.lock:
        sets = [f"{k}=?" if not isinstance(v, str) or not v.startswith("content_revision +") else f"{k}={v}" for k,v in fields.items()]
        vals = [v for v in fields.values() if not (isinstance(v, str) and v.startswith("content_revision +"))]
        sets += ["last_reason=?", "last_external_access=?", "updated_at=?"]
        vals += [reason, external_access, config.now_iso(), int(media_id)]
        db.conn.execute("INSERT OR IGNORE INTO resource_state(media_id,updated_at) VALUES(?,?)", (int(media_id), config.now_iso()))
        db.conn.execute(f"UPDATE resource_state SET {', '.join(sets)} WHERE media_id=?", vals)
        if reason:
            db.conn.execute("INSERT INTO resource_events(media_id,event_type,reason,detail,created_at) VALUES(?,?,?,?,?)",
                            (int(media_id), "RESOURCE_DECISION", reason, external_access or "", config.now_iso()))
        db.conn.commit()
    # Keep the Telegram registry's PREPARED bit aligned with the canonical
    # local readiness record. This is DB-only bookkeeping and never downloads.
    try:
        with db.lock:
            media = db.conn.execute(
                "SELECT source_channel,source_message_id,oshash,video_phash FROM media WHERE id=?", (int(media_id),)
            ).fetchone()
            ready = db.conn.execute(
                "SELECT 1 FROM resource_state rs WHERE rs.media_id=? AND rs.metadata_state='COMPLETE' "
                "AND rs.entity_state='COMPLETE' AND rs.snapshot_state='READY' "
                "AND EXISTS(SELECT 1 FROM asset_registry ar WHERE ar.media_id=rs.media_id "
                "AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY') LIMIT 1",
                (int(media_id),),
            ).fetchone()
        if media and ready and str(media['source_channel'] or '').lstrip('-').isdigit() and int(media['source_message_id'] or 0)>0:
            from .telegram_registry import mark_prepared
            mark_prepared(db, channel_id=str(media['source_channel']), message_id=int(media['source_message_id']),
                          prepared={'readiness_source':'resource_state','media_id':int(media_id)},
                          oshash=str(media['oshash'] or ''), phash=str(media['video_phash'] or ''))
    except Exception as exc:
        try:
            db.log(f"Telegram registry prepared-state sync failed for media {media_id}: {type(exc).__name__}: {exc}", "WARN")
        except Exception:
            _swallowed_note("resource_registry.sync_media_state")
            pass


def sync_message_state(db, channel_id, message_id, **states) -> None:
    """Update resource_state for a catalogued Telegram message (no-op if not catalogued).

    The single owner of the "find media id → sync state" step used by the
    metadata and snapshot pipelines. Resource-state bookkeeping must never fail
    the media operation itself, so failures are logged once here.
    """
    try:
        with db.lock:
            row = db.conn.execute("SELECT id FROM media WHERE source_channel=? AND source_message_id=?",
                                  (str(channel_id), int(message_id))).fetchone()
        if row:
            sync_media_state(db, int(row["id"]), **states)
    except Exception as exc:
        db.log(f"Resource state update failed for {channel_id}/{message_id}: {type(exc).__name__}: {exc}", "WARN")


def record_asset(db, *, media_id: int | None, entity_type: str, entity_id: str | int,
                 asset_type: str, path: str, state: str = "READY", sha256: str | None = None,
                 size_bytes: int = 0, content_revision: int = 1) -> None:
    ensure_resource_tables(db)
    from .. import config
    now = config.now_iso()
    with db.lock:
        db.conn.execute("""INSERT INTO asset_registry(media_id,entity_type,entity_id,asset_type,path,sha256,size_bytes,state,content_revision,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entity_type,entity_id,asset_type) DO UPDATE SET
            media_id=excluded.media_id,path=excluded.path,sha256=excluded.sha256,size_bytes=excluded.size_bytes,
            state=excluded.state,content_revision=excluded.content_revision,updated_at=excluded.updated_at""",
            (media_id, str(entity_type), str(entity_id), str(asset_type), str(path), sha256, int(size_bytes or 0), str(state), int(content_revision or 1), now, now))
        db.conn.commit()




# ==============================================================================
# SECTION: Single-flight coordination
# Collapses concurrent work on the same resource.
# ==============================================================================

class WorkCoordinator:
    """Universal in-process single-flight coordinator for expensive work."""
    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def run(self, key: str, factory: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]:
        joined = False
        async with self._lock:
            task = self._tasks.get(str(key))
            if task is None:
                task = asyncio.create_task(factory(), name=f"archive-x-work-{key[:80]}")
                self._tasks[str(key)] = task
            else:
                joined = True
        try:
            return await asyncio.shield(task), joined
        finally:
            if task.done():
                async with self._lock:
                    if self._tasks.get(str(key)) is task:
                        self._tasks.pop(str(key), None)


def coordinator_for(db) -> WorkCoordinator:
    obj = getattr(db, "_archive_x_work_coordinator", None)
    if obj is None:
        obj = WorkCoordinator()
        setattr(db, "_archive_x_work_coordinator", obj)
    return obj




# ==============================================================================
# SECTION: Statistics and local preparation
# Readiness statistics and marking local media prepared.
# ==============================================================================

def media_readiness_stats(db, *, channel_id: str | int | None = None, media_ids: list[int] | tuple[int, ...] | None = None) -> dict:
    """Return per-video publication/readiness counts for jobs and diagnostics."""
    if not hasattr(db, "lock") or not hasattr(db, "conn"):
        return {"total": 0, "published_ready": 0, "metadata_pending": 0, "entity_pending": 0, "snapshot_pending": 0, "catalogue_pending": 0, "needs_review": 0}
    ensure_resource_tables(db)
    clauses=["COALESCE(m.status,'')<>'SOURCE_DELETED'"]; params=[]
    if channel_id is not None:
        clauses.append('m.source_channel=?'); params.append(str(channel_id))
    if media_ids:
        mids=[int(x) for x in media_ids if int(x)>0]
        if mids:
            clauses.append(f"m.id IN ({','.join('?' for _ in mids)})"); params.extend(mids)
    where=('WHERE '+' AND '.join(clauses)) if clauses else ''
    with db.reading() as conn:
        rows=conn.execute(
            "SELECT rs.catalogue_state,rs.metadata_state,rs.entity_state,rs.snapshot_state, "
            "COALESCE(m.streamable,0) streamable, COALESCE(m.metadata_source,'') metadata_source, COALESCE(m.identification_state,'') identification_state, "
            "CASE WHEN pm.id IS NOT NULL THEN 1 ELSE 0 END published, "
            "CASE WHEN EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.entity_type='telegram_video' "
            "AND ar.entity_id=(CAST(m.source_channel AS TEXT) || ':' || CAST(m.source_message_id AS TEXT)) "
            "AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY') "
            "OR EXISTS (SELECT 1 FROM asset_registry ar WHERE ar.media_id=m.id "
            "AND ar.asset_type='PLAYBACK_REPRESENTATIVE' AND ar.state='READY') THEN 1 ELSE 0 END card_image "
            "FROM media m JOIN resource_state rs ON rs.media_id=m.id "
            "LEFT JOIN archive_x_published_media pm ON pm.id=m.id " + where, params).fetchall()
    out={'total':len(rows),'published_ready':0,'metadata_pending':0,'entity_pending':0,'snapshot_pending':0,'catalogue_pending':0,'needs_review':0,
         'card_image_pending':0,'not_streamable':0,'suggestion_pending_review':0,'not_in_library':0}
    for r in rows:
        # V29.4.132: suggested videos are in the Library but still need review.
        if str(r['metadata_source'] or '')=='suggested' and str(r['identification_state'] or '')=='PARTIAL': out['suggestion_pending_review']+=1
        if int(r['published'] or 0): out['published_ready']+=1; continue
        out['not_in_library']+=1
        if str(r['metadata_state'] or '') not in {'COMPLETE'}: out['metadata_pending']+=1
        if str(r['entity_state'] or '') not in {'COMPLETE'}: out['entity_pending']+=1
        snap_missing = str(r['snapshot_state'] or '') != 'READY'
        if snap_missing: out['snapshot_pending']+=1
        if str(r['catalogue_state'] or '') != 'COMPLETE': out['catalogue_pending']+=1
        # V29.4.132: the two Library requirements that were never reported,
        # so a video missing only these looked "done" but never appeared.
        if not int(r['card_image'] or 0) and not snap_missing: out['card_image_pending']+=1
        if not int(r['streamable'] or 0): out['not_streamable']+=1
        if str(r['metadata_state'] or '') in {'COMPLETE','FAILED'} and str(r['entity_state'] or '') in {'COMPLETE','FAILED'}:
            out['needs_review']+=1
    return out


def registry_stats(db) -> dict:
    ensure_resource_tables(db)
    with db.reading() as conn:
        total = conn.execute("SELECT COUNT(*) n FROM media").fetchone()["n"]
        states = conn.execute("SELECT snapshot_state,metadata_state,provider_state FROM resource_state").fetchall()
        assets = conn.execute("SELECT COUNT(*) n FROM asset_registry WHERE state='READY'").fetchone()["n"]
        events = conn.execute("SELECT COUNT(*) n FROM resource_events WHERE created_at >= datetime('now','-24 hours')").fetchone()["n"]
    def count(col, value):
        return sum(1 for r in states if str(r[col] or '') == value)
    return {
        "media_objects": int(total or 0),
        "ready_assets": int(assets or 0),
        "events_24h": int(events or 0),
        "metadata_complete": count("metadata_state", "COMPLETE"),
        "snapshot_ready": count("snapshot_state", "READY"),
        "provider_complete": count("provider_state", "COMPLETE"),
        "telegram_media_policy": "video-only-object-store",
    }


def mark_local_media_prepared(db, media_id: int, *, representative_path: str | None,
                              frames_ready: bool, metadata_complete: bool = True) -> dict[str, str]:
    """V29.4.223: record finished local preparation for a staging media row.

    Local Upload / Content Automation rows live under a non-Telegram namespace,
    so the Telegram snapshot path (which marks READY per channel/message) never
    ran for them and the pre-upload gate held every local file. This marks:

    * the representative image as a READY asset for this media row;
    * snapshots READY when all 36 frames are validated;
     * metadata COMPLETE once identification has run, even with no provider match,
       so unmatched videos go to Unknown Performer / Unknown Studio as intended.
       Callers that deliberately defer identification can leave that state untouched.
    """
    from pathlib import Path as _Path
    result: dict[str, str] = {}
    ensure_resource_tables(db)
    rep = _Path(representative_path) if representative_path else None
    if rep is not None and rep.is_file() and rep.stat().st_size > 0:
        record_asset(db, media_id=int(media_id), entity_type="local_media", entity_id=str(int(media_id)),
                     asset_type="PLAYBACK_REPRESENTATIVE", path=str(rep), size_bytes=int(rep.stat().st_size))
        result["representative"] = "READY"
    with db.lock:
        row = db.conn.execute(
            "SELECT catalogue_state,metadata_state FROM resource_state WHERE media_id=?", (int(media_id),)
        ).fetchone()
    metadata_state = str((row["metadata_state"] if row else "") or "").upper()
    catalogue_state = str((row["catalogue_state"] if row else "") or "").upper()
    sync_media_state(
        db, int(media_id),
        snapshot="READY" if (frames_ready and result.get("representative")) else None,
        metadata=("COMPLETE" if metadata_complete and metadata_state != "COMPLETE" else None),
        catalogue=None if catalogue_state == "COMPLETE" else "COMPLETE",
        reason="LOCAL_PREPARATION_COMPLETE",
    )
    result["snapshot"] = "READY" if (frames_ready and result.get("representative")) else "PENDING"
    result["metadata"] = "COMPLETE" if metadata_complete else (metadata_state or "UNKNOWN")
    return result
