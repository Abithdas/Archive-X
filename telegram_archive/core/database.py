"""SQLite persistence: settings, channels, jobs, media, events.

This is the single shared catalogue every module reads/writes through —
no module is allowed to open its own sqlite3 connection (see the project's
"Important reuse rule": no operating mode gets its own state format).
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Module helpers
#   - Database class
#       - Connection setup and schema creation
#       - Job orchestration schema
#       - Execution lanes
#       - Resource leases
#       - Job dependencies, executions and retry policy
#       - Job journal schema
#       - Search indexes and data repairs
#       - Column and identity migrations
#       - Persistent metadata and secret settings
#       - Settings
#       - AI assistant persistence
#       - Event log and buffered writes
#       - Job transfer telemetry
#       - Shutdown
#       - Jobs
#       - Statistics
# ------------------------------------------------------------------
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .. import config
from .ai_persistence import AIPersistence




# ==============================================================================
# SECTION: Module helpers
# Schema-retry detection, read connections and JSON merge helpers.
# ==============================================================================

def _is_schema_retry(exc: BaseException) -> bool:
    """SQLite asks readers to retry when the schema changed mid-statement."""
    name = getattr(exc, "sqlite_errorname", "") or ""
    text = str(exc)
    return name == "SQLITE_SCHEMA" or "vtable constructor failed" in text or "database schema has changed" in text


class _ReadConnection:
    """Read-only connection that retries statements on SQLITE_SCHEMA (V29.4.126).

    When the writer creates a table while a web request is reading, SQLite
    reports SQLITE_SCHEMA ("vtable constructor failed: studio_fts" for search).
    That is a request to re-prepare the statement, not a real failure.
    """

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def execute(self, sql, params=()):
        for attempt in range(4):
            try:
                return self._conn.execute(sql, params)
            except sqlite3.OperationalError as exc:
                if attempt == 3 or not _is_schema_retry(exc):
                    raise
                time.sleep(0.005 * (attempt + 1))

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _json_map(value):
    try:
        data = json.loads(value or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge_provider_maps(left, right):
    out = _json_map(left)
    other = _json_map(right)
    merged = {}
    for key in dict.fromkeys([*out.keys(), *other.keys()]):
        raw_values = []
        for value in (out.get(key), other.get(key)):
            values = value if isinstance(value, (list, tuple, set)) else [value]
            for item in values:
                text = str(item or '').strip()
                if text and text not in raw_values:
                    raw_values.append(text)
        if len(raw_values) == 1:
            merged[str(key)] = raw_values[0]
        elif raw_values:
            merged[str(key)] = raw_values
    return json.dumps(merged, ensure_ascii=False, sort_keys=True)


def _merge_alias_list(existing, new_name):
    try:
        values = json.loads(existing or "[]")
        if not isinstance(values, list): values = []
    except Exception:
        values = []
    name = " ".join(str(new_name or "").strip().split())
    if name and name.casefold() not in {str(v).casefold() for v in values}:
        values.append(name)
    return json.dumps(values, ensure_ascii=False)




# ==============================================================================
# SECTION: Database class
# Single persistence facade. The groups below are natural mixin/repository split points.
# ==============================================================================

class Database:

    # ------------------------------------------------------------------
    # SUBSECTION: Connection setup and schema creation
    # Connections, indexes, read pool and init_schema.
    # ------------------------------------------------------------------
    """SQLite persistence facade; see the SUBSECTION banners for its feature groups."""
    def __init__(self, path: Path = config.DB_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.reference_repair_channels = []
        self._event_pending = 0
        self._event_last_commit = 0.0
        self._event_buf: list = []
        self._event_buf_lock = threading.Lock()
        self._event_commit_batch = 25
        self._event_commit_interval = 0.50
        self._job_transfer_telemetry_write_times = {}
        self._read_local = threading.local()
        self._read_conns: set[sqlite3.Connection] = set()
        self._read_conns_lock = threading.Lock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA cache_size=-32768")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA mmap_size=268435456")
        self.ai_persistence = AIPersistence(self)
        self.init_schema()
        self._create_v6_indexes()
        self._migrate_secret_settings()
        # V29.4.131: change counters the web UI watches to refresh itself.
        from . import change_tracking
        change_tracking.ensure(self)

    def _create_v6_indexes(self):
        """Additive indexes for high-frequency V6 reads."""
        with self.lock:
            self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_media_upload_date_id ON media(upload_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_source_channel_date ON media(source_channel, upload_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_favorite_upload_date_id ON media(favorite, upload_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_resolution_upload_date_id ON media(resolution_category, upload_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_size_id ON media(size DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_duration_id ON media(duration DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_filename_nocase ON media(filename COLLATE NOCASE, id);
            CREATE INDEX IF NOT EXISTS idx_media_height_upload_date_id ON media(height, upload_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_effective_date_id ON media(COALESCE(NULLIF(upload_date,''),created_at) DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_media_source_effective_date_id ON media(source_channel, COALESCE(NULLIF(upload_date,''),created_at) DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_history_last_played ON watch_history(last_played_at DESC, media_id);
            CREATE INDEX IF NOT EXISTS idx_watch_history_completed ON watch_history(completed, last_played_at DESC);
            CREATE INDEX IF NOT EXISTS idx_entity_favorites_type_created ON entity_favorites(entity_type, created_at DESC, entity_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at DESC, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_created_id ON events(created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_provider_sync_updated ON provider_sync_state(provider, updated_at DESC);
            """)
            self.conn.commit()

    @contextmanager
    def reading(self):
        """Yield a thread-local read connection for SELECT-heavy web paths."""
        if str(self.path) == ':memory:':
            with self.lock:
                yield self.conn
            return
        conn = getattr(self._read_local, 'conn', None)
        if conn is None:
            path = Path(self.path).resolve()
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA query_only=ON')
            conn.execute('PRAGMA busy_timeout=1500')
            conn.execute('PRAGMA temp_store=MEMORY')
            conn.execute('PRAGMA cache_size=-32768')
            conn.execute('PRAGMA mmap_size=268435456')
            self._read_local.conn = conn
            with self._read_conns_lock:
                self._read_conns.add(conn)
        yield _ReadConnection(conn)

    def _close_read_connections(self):
        with self._read_conns_lock:
            conns = list(self._read_conns)
            self._read_conns.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        try:
            self._read_local.conn = None
        except Exception:
            pass

    def init_schema(self):
        """Create tables and indexes, then run the migrations and repairs."""
        self.ai_persistence.ensure_schema()
        with self.lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT UNIQUE,
                title TEXT NOT NULL,
                username TEXT,
                image TEXT,
                image_source TEXT,
                title_locked INTEGER DEFAULT 0,
                image_locked INTEGER DEFAULT 0,
                role TEXT DEFAULT 'available',  -- available/intake/library/trash/archive
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS channel_image_cache (
                telegram_id TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                mime TEXT NOT NULL,
                data BLOB NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                source_channel TEXT,
                destination_channel TEXT,
                status TEXT NOT NULL,
                progress REAL DEFAULT 0,
                current_item TEXT,
                detail TEXT,
                error TEXT,
                destructive INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                admission_state TEXT NOT NULL DEFAULT 'READY',
                admission_reason TEXT NOT NULL DEFAULT '',
                next_run_at TEXT,
                priority INTEGER NOT NULL DEFAULT 50,
                attempt INTEGER NOT NULL DEFAULT 0,
                admission_updated_at TEXT,
                execution_id TEXT,
                worker_id TEXT,
                lease_id TEXT,
                lease_expires_at TEXT,
                last_heartbeat_at TEXT,
                worker_state TEXT NOT NULL DEFAULT 'IDLE',
                retry_count INTEGER NOT NULL DEFAULT 0,
                max_retries INTEGER NOT NULL DEFAULT 3,
                failure_class TEXT NOT NULL DEFAULT '',
                deadline_at TEXT,
                resource_requirements TEXT NOT NULL DEFAULT '{}'
            );

            -- Generic durable job checkpoint contract. The job row remains the
            -- authoritative lifecycle/progress summary; this table stores the
            -- latest restart-safe execution checkpoint without overloading the
            -- human-readable job detail field.
            CREATE TABLE IF NOT EXISTS job_checkpoints (
                job_id TEXT PRIMARY KEY,
                stage TEXT,
                item_id TEXT,
                item_index INTEGER,
                total_items INTEGER,
                current_operation TEXT,
                last_completed_operation TEXT,
                input_fingerprint TEXT,
                output_fingerprint TEXT,
                retry_count INTEGER DEFAULT 0,
                payload TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_job_checkpoints_stage ON job_checkpoints(stage, updated_at);

            CREATE TABLE IF NOT EXISTS job_dependencies (
                job_id TEXT NOT NULL,
                depends_on_job_id TEXT NOT NULL,
                dependency_type TEXT NOT NULL DEFAULT 'SUCCESS',
                created_at TEXT NOT NULL,
                PRIMARY KEY(job_id, depends_on_job_id),
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE,
                FOREIGN KEY(depends_on_job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_job_dependencies_parent ON job_dependencies(depends_on_job_id);

            CREATE TABLE IF NOT EXISTS job_executions (
                execution_id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                worker_id TEXT NOT NULL,
                lease_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                lease_expires_at TEXT NOT NULL,
                ended_at TEXT,
                failure_class TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_job_executions_job ON job_executions(job_id, attempt);
            CREATE INDEX IF NOT EXISTS idx_job_executions_lease ON job_executions(lease_expires_at, status);

            -- Durable job journal. The Database owns creation and lifecycle
            -- transition entries so state changes and their history are atomic.
            CREATE TABLE IF NOT EXISTS job_journal (
                id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, job_id TEXT NOT NULL,
                sequence INTEGER NOT NULL, event_type TEXT NOT NULL, from_status TEXT,
                status TEXT, checkpoint TEXT, operation TEXT, detail TEXT, payload TEXT,
                error_type TEXT, retry_count INTEGER, launch_id TEXT, created_at TEXT NOT NULL,
                UNIQUE(job_id, sequence)
            );
            CREATE INDEX IF NOT EXISTS idx_job_journal_job ON job_journal(job_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_job_journal_event ON job_journal(event_type, created_at);

            -- Section 13 of the system design reference: catalogue fields
            -- needed to power the web library UI (resolution/studio/
            -- performer/tag filters). Populated by the scanner + analyzer
            -- once those are ported (see video/ stubs); the schema is
            -- final now so the active web/routes.py router has the canonical library behavior
            -- real to query against as each piece comes online.
            CREATE TABLE IF NOT EXISTS media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_channel TEXT,
                source_message_id INTEGER,
                filename TEXT,
                title TEXT,
                size INTEGER,
                duration REAL,
                width INTEGER,
                height INTEGER,
                fps REAL,
                bitrate INTEGER,
                codec TEXT,
                audio_codec TEXT,
                orientation TEXT,
                resolution_category TEXT,
                streamable INTEGER,
                thumbnail_message_id INTEGER,
                thumbnail_status TEXT,
                caption TEXT,
                identity_key TEXT,
                oshash TEXT,
                upload_date TEXT,
                release_date TEXT,
                favorite INTEGER DEFAULT 0,
                status TEXT,
                created_at TEXT,
                updated_at TEXT,
                UNIQUE(source_channel, source_message_id)
            );

            -- Photo library index is separate from video media so image documents
            -- never leak into the video listing. A photo is keyed by its Telegram
            -- channel/message location; uploaded local images are entered after
            -- destination verification and remain browsable even if the local copy
            -- is subsequently deleted.
            CREATE TABLE IF NOT EXISTS photo_library_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                filename TEXT NOT NULL DEFAULT '',
                caption TEXT NOT NULL DEFAULT '',
                mime_type TEXT NOT NULL DEFAULT 'image/jpeg',
                size INTEGER NOT NULL DEFAULT 0,
                width INTEGER NOT NULL DEFAULT 0,
                height INTEGER NOT NULL DEFAULT 0,
                message_date TEXT,
                source_kind TEXT NOT NULL DEFAULT 'channel_scan',
                content_hash TEXT NOT NULL DEFAULT '',
                -- Durable local cache reference. Image bytes live on disk under
                -- USER_DATA_DIR/cache/photo_library, never in temporary HTTP buffers.
                local_path TEXT NOT NULL DEFAULT '',
                local_sha256 TEXT NOT NULL DEFAULT '',
                cached_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(channel_id, message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_photo_library_channel_date
                ON photo_library_items(channel_id, message_date DESC, message_id DESC);
            CREATE INDEX IF NOT EXISTS idx_photo_library_filename
                ON photo_library_items(filename COLLATE NOCASE, id);

            CREATE TABLE IF NOT EXISTS photo_library_scan_state (
                channel_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'NEVER',
                last_scan_at TEXT,
                messages_scanned INTEGER NOT NULL DEFAULT 0,
                photos_found INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL
            );

            -- Local, image-derived face clusters for the Photos library. These
            -- are anonymous visual groups until the user explicitly assigns a
            -- group to a performer; profile names are never inferred by the ML job.
            CREATE TABLE IF NOT EXISTS photo_face_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                label TEXT NOT NULL DEFAULT '',
                performer_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_photo_face_groups_performer
                ON photo_face_groups(performer_id, id);

            -- Face embedding of each performer's profile picture, used only to
            -- suggest (never auto-assign) a performer for an unnamed face group.
            CREATE TABLE IF NOT EXISTS performer_face_embeddings (
                performer_id INTEGER PRIMARY KEY,
                image_key TEXT NOT NULL,
                embedding_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS photo_face_detections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id INTEGER NOT NULL,
                face_index INTEGER NOT NULL,
                group_id INTEGER NOT NULL,
                bbox_json TEXT NOT NULL DEFAULT '[]',
                embedding_json TEXT NOT NULL DEFAULT '[]',
                crop_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(photo_id, face_index)
            );
            CREATE INDEX IF NOT EXISTS idx_photo_face_detections_group
                ON photo_face_detections(group_id, photo_id);
            CREATE INDEX IF NOT EXISTS idx_photo_face_detections_photo
                ON photo_face_detections(photo_id, face_index);

            CREATE TABLE IF NOT EXISTS photo_face_analysis (
                photo_id INTEGER PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'PENDING',
                face_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                analyzed_at TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS photo_face_suggestion_feedback (
                group_id INTEGER NOT NULL,
                performer_id INTEGER NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                similarity_score REAL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(group_id, performer_id, source)
            );
            CREATE INDEX IF NOT EXISTS idx_photo_face_suggestion_feedback_status
                ON photo_face_suggestion_feedback(status, source, group_id);

            CREATE TABLE IF NOT EXISTS studios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                provider_ids TEXT
            );

            CREATE TABLE IF NOT EXISTS performers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                image TEXT,
                profile TEXT,
                provider_ids TEXT
            );

            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL
            );

            CREATE TABLE IF NOT EXISTS thumbnail_cache (
                source_channel TEXT NOT NULL,
                video_message_id INTEGER NOT NULL,
                thumbnail_message_id INTEGER,
                mime TEXT NOT NULL,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                data BLOB NOT NULL,
                source TEXT DEFAULT 'telegram',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_channel, video_message_id)
            );

            -- Persistent timeline-preview manifest. The actual individual frames stay on the
            -- filesystem so SQLite does not grow by hundreds of KB per video;
            -- SQLite remains the authoritative index/status store.
            CREATE TABLE IF NOT EXISTS timeline_preview_cache (
                source_channel TEXT NOT NULL,
                video_message_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'READY',
                duration REAL DEFAULT 0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                frame_count INTEGER DEFAULT 36,
                version INTEGER DEFAULT 6,
                error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (source_channel, video_message_id)
            );
            CREATE INDEX IF NOT EXISTS idx_timeline_preview_status
                ON timeline_preview_cache(status, updated_at);

            CREATE TABLE IF NOT EXISTS performer_image_cache (
                performer_id INTEGER PRIMARY KEY,
                url TEXT,
                mime TEXT,
                data BLOB,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS metadata_cache (
                provider TEXT NOT NULL,
                query_key TEXT NOT NULL,
                response TEXT,
                fetched_at REAL NOT NULL,
                PRIMARY KEY (provider, query_key)
            );
            CREATE INDEX IF NOT EXISTS idx_metadata_cache_fetched ON metadata_cache(provider, fetched_at);


            CREATE TABLE IF NOT EXISTS studio_image_cache (
                studio_id INTEGER PRIMARY KEY,
                url TEXT,
                mime TEXT,
                data BLOB,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS video_studios (
                video_id INTEGER NOT NULL,
                studio_id INTEGER NOT NULL,
                confidence REAL DEFAULT 1.0,
                PRIMARY KEY (video_id, studio_id)
            );

            CREATE TABLE IF NOT EXISTS video_performers (
                video_id INTEGER NOT NULL,
                performer_id INTEGER NOT NULL,
                confidence REAL DEFAULT 1.0,
                PRIMARY KEY (video_id, performer_id)
            );

            CREATE TABLE IF NOT EXISTS video_tags (
                video_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                confidence REAL DEFAULT 1.0,
                PRIMARY KEY (video_id, tag_id)
            );

            CREATE TABLE IF NOT EXISTS local_video_performers (
                video_id INTEGER NOT NULL,
                performer_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (video_id, performer_id)
            );

            CREATE INDEX IF NOT EXISTS idx_local_video_performers_performer ON local_video_performers(performer_id);
            CREATE INDEX IF NOT EXISTS idx_local_video_performers_performer_video ON local_video_performers(performer_id, video_id);
            CREATE INDEX IF NOT EXISTS idx_video_performers_performer_video ON video_performers(performer_id, video_id);
            CREATE INDEX IF NOT EXISTS idx_video_performers_video_performer ON video_performers(video_id, performer_id);
            CREATE INDEX IF NOT EXISTS idx_video_studios_studio_video ON video_studios(studio_id, video_id);
            CREATE INDEX IF NOT EXISTS idx_video_studios_video_studio ON video_studios(video_id, studio_id);
            CREATE INDEX IF NOT EXISTS idx_video_tags_tag_video ON video_tags(tag_id, video_id);
            CREATE INDEX IF NOT EXISTS idx_video_tags_video_tag ON video_tags(video_id, tag_id);
            CREATE INDEX IF NOT EXISTS idx_media_thumbnail_status_id ON media(thumbnail_status, id);
            CREATE INDEX IF NOT EXISTS idx_media_thumbnail_updated ON media(thumbnail_status, updated_at);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                level TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            -- V29.4.186: per-job activity timeline. The general events table is
            -- intentionally compact; this indexed stream keeps process steps,
            -- frame names, and media metadata queryable from the job detail view.
            CREATE TABLE IF NOT EXISTS job_activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                level TEXT NOT NULL,
                event TEXT NOT NULL,
                operation TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_job_activity_job_id_id
                ON job_activity_events(job_id, id DESC);

            -- One replaceable telemetry snapshot per job, separate from the
            -- user-visible activity timeline and its sampled progress events.
            CREATE TABLE IF NOT EXISTS job_transfer_telemetry (
                job_id TEXT PRIMARY KEY,
                telemetry TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
            );

            -- Operations: persistent RSS -> qBittorrent -> Telegram automation state.
            -- This is deliberately separate from jobs: jobs track the one visible
            -- parent operation while this table tracks each release/idempotency key.
            CREATE TABLE IF NOT EXISTS automation_items (
                item_key TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                guid TEXT,
                title TEXT NOT NULL,
                published_at TEXT,
                torrent_url TEXT,
                torrent_hash TEXT,
                local_path TEXT,
                resolution TEXT,
                download_progress REAL DEFAULT 0,
                download_state TEXT DEFAULT '',
                download_rate INTEGER DEFAULT 0,
                eta_seconds INTEGER DEFAULT -1,
                enclosure_size INTEGER DEFAULT 0,
                retry_count INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT,
                failure_kind TEXT DEFAULT '',
                retry_stage TEXT DEFAULT '',
                raw_oshash TEXT,
                status TEXT NOT NULL,
                destination_channel TEXT,
                destination_video_id INTEGER,
                destination_thumbnail_id INTEGER,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_automation_items_status ON automation_items(status);
            CREATE INDEX IF NOT EXISTS idx_automation_items_torrent ON automation_items(torrent_hash);
            CREATE INDEX IF NOT EXISTS idx_automation_items_created ON automation_items(created_at);

            CREATE TABLE IF NOT EXISTS automation_delivery_fingerprints (
                destination_channel TEXT NOT NULL,
                destination_video_id INTEGER NOT NULL,
                item_key TEXT NOT NULL,
                raw_oshash TEXT NOT NULL,
                original_filename TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY(destination_channel, destination_video_id)
            );
            CREATE INDEX IF NOT EXISTS idx_automation_delivery_oshash ON automation_delivery_fingerprints(raw_oshash);

            -- v15: every destructive or moving action is PROPOSED first,
            -- with a human-readable reason, and only executed after the
            -- operator approves it in the dashboard. Nothing is ever
            -- deleted directly: 'TRASH' moves to the channel whose
            -- role='trash'; 'DELETE' is only offered for items already
            -- in the trash channel.
            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,          -- MOVE / TRASH / DELETE / REPAIR_THUMB / CONVERT
                media_id INTEGER,
                source_channel TEXT,
                message_ids TEXT,                   -- JSON list, frozen at proposal time
                destination_channel TEXT,
                reason TEXT NOT NULL,               -- shown to the operator: WHY this is needed
                evidence TEXT,                      -- JSON: sizes, durations, match scores...
                status TEXT NOT NULL DEFAULT 'PROPOSED',  -- PROPOSED/APPROVED/REJECTED/DONE/FAILED
                proposed_by_job TEXT,
                created_at TEXT NOT NULL,
                decided_at TEXT,
                result TEXT
            );

            -- v15: per-channel scan position, so a PC that is only on
            -- occasionally catches up on everything posted while it was
            -- off (live-watch alone would miss it).
            CREATE TABLE IF NOT EXISTS scan_checkpoints (
                channel_id TEXT PRIMARY KEY,
                last_message_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            -- Confidence-gated metadata matches awaiting user review (see
            -- metadata/matching.py). Only HIGH-confidence matches are ever
            -- auto-linked; everything else lands here as PENDING until the
            -- operator Confirms or Rejects it from the Performer picker.
            CREATE TABLE IF NOT EXISTS metadata_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                provider_record_id TEXT,
                suggested_performer_id INTEGER,
                suggested_performer_name TEXT,
                confidence TEXT NOT NULL,        -- HIGH/MEDIUM/LOW
                score REAL,
                reason TEXT,
                payload TEXT,                    -- JSON: full normalized provider match
                status TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING/CONFIRMED/REJECTED
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(video_id, provider, provider_record_id)
            );
            CREATE INDEX IF NOT EXISTS idx_suggestions_video ON metadata_suggestions(video_id, status);
            CREATE INDEX IF NOT EXISTS idx_suggestions_performer ON metadata_suggestions(suggested_performer_id, status);
            CREATE INDEX IF NOT EXISTS idx_suggestions_status ON metadata_suggestions(status);

            -- A scene candidate can contain several performers and one studio.
            -- Keep suggestion associations separately instead of hiding a match
            -- under only the first cast member or an unrelated local record.
            CREATE TABLE IF NOT EXISTS metadata_suggestion_performers (
                suggestion_id INTEGER NOT NULL,
                performer_id INTEGER NOT NULL,
                PRIMARY KEY (suggestion_id, performer_id)
            );
            CREATE INDEX IF NOT EXISTS idx_suggestion_performers_entity
                ON metadata_suggestion_performers(performer_id, suggestion_id);
            CREATE TABLE IF NOT EXISTS metadata_suggestion_studios (
                suggestion_id INTEGER NOT NULL,
                studio_id INTEGER NOT NULL,
                PRIMARY KEY (suggestion_id, studio_id)
            );
            CREATE INDEX IF NOT EXISTS idx_suggestion_studios_entity
                ON metadata_suggestion_studios(studio_id, suggestion_id);
            -- Entity-page "not this performer/studio" decisions are scoped to
            -- one relationship. They must not reject the provider scene record,
            -- which remains available for later full identification review.
            CREATE TABLE IF NOT EXISTS metadata_entity_suggestion_decisions (
                video_id INTEGER NOT NULL,
                entity_type TEXT NOT NULL CHECK(entity_type IN ('performer','studio')),
                entity_id INTEGER NOT NULL,
                decision TEXT NOT NULL CHECK(decision='REJECTED'),
                created_at TEXT NOT NULL,
                PRIMARY KEY(video_id, entity_type, entity_id)
            );
            CREATE INDEX IF NOT EXISTS idx_entity_suggestion_decisions_entity
                ON metadata_entity_suggestion_decisions(entity_type, entity_id, decision, video_id);

            -- Canonical filename reference database (Claude engineering
            -- prompt: canonical filename reference + targeted matching).
            -- One row per identified scene/video ("canonical identity");
            -- reference_aliases holds every filename variant seen for it,
            -- so repeat uploads of the same scene under a different name
            -- resolve instantly from the index instead of re-querying a
            -- provider or re-running AI matching.
            CREATE TABLE IF NOT EXISTS reference_keys (
                canonical_key TEXT PRIMARY KEY,
                title TEXT,
                performer_names TEXT,       -- JSON list, matching-purposes only (no bios)
                studio_name TEXT,
                site_name TEXT,
                release_date TEXT,
                provider TEXT,
                provider_record_id TEXT,
                provider_url TEXT,
                metadata_hash TEXT,         -- detects real provider-side changes (section 18)
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reference_keys_provider ON reference_keys(provider, provider_record_id);

            CREATE TABLE IF NOT EXISTS reference_aliases (
                canonical_key TEXT NOT NULL,
                normalized_filename TEXT NOT NULL,
                original_filename TEXT,
                source_channel TEXT,
                source_message_id INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (canonical_key, normalized_filename)
            );
            CREATE INDEX IF NOT EXISTS idx_reference_aliases_filename ON reference_aliases(normalized_filename);
            CREATE INDEX IF NOT EXISTS idx_reference_aliases_key ON reference_aliases(canonical_key);

            CREATE INDEX IF NOT EXISTS idx_actions_status ON pending_actions(status);
            CREATE INDEX IF NOT EXISTS idx_media_upload ON media(upload_date);
            CREATE INDEX IF NOT EXISTS idx_media_channel_msg ON media(source_channel, source_message_id);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            CREATE INDEX IF NOT EXISTS idx_media_identity ON media(identity_key);
            CREATE INDEX IF NOT EXISTS idx_media_resolution ON media(resolution_category);
            CREATE INDEX IF NOT EXISTS idx_thumbnail_cache_updated ON thumbnail_cache(updated_at);

            -- Local media identity -> persisted playback snapshot mapping.
            -- The content fingerprint prevents a replaced file with the same
            -- filename/path from inheriting the old snapshot.
            CREATE TABLE IF NOT EXISTS local_playback_snapshots (
                canonical_path TEXT PRIMARY KEY,
                source_oshash TEXT NOT NULL,
                snapshot_path TEXT NOT NULL,
                source_size INTEGER DEFAULT 0,
                duration REAL DEFAULT 0,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                codec TEXT,
                fps REAL DEFAULT 0,
                frame_count INTEGER DEFAULT 36,
                frames_path TEXT,
                representative_path TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_local_playback_snapshots_oshash
                ON local_playback_snapshots(source_oshash);

            -- Local 36-frame snapshot manifest. Image bytes stay on disk so the
            -- SQLite database does not balloon; SQLite stores the identity,
            -- timestamp and exact filesystem location of every application frame.
            CREATE TABLE IF NOT EXISTS local_playback_snapshot_frames (
                canonical_path TEXT NOT NULL,
                source_oshash TEXT NOT NULL,
                frame_index INTEGER NOT NULL,
                timestamp REAL DEFAULT 0,
                path TEXT NOT NULL,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(canonical_path, frame_index)
            );
            CREATE INDEX IF NOT EXISTS idx_local_playback_snapshot_frames_oshash
                ON local_playback_snapshot_frames(source_oshash);
            CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

            -- V5.5 user experience / metadata provider state.
            CREATE TABLE IF NOT EXISTS watch_history (
                media_id INTEGER PRIMARY KEY,
                position REAL NOT NULL DEFAULT 0,
                duration REAL NOT NULL DEFAULT 0,
                play_count INTEGER NOT NULL DEFAULT 0,
                total_watch_seconds REAL NOT NULL DEFAULT 0,
                last_played_at TEXT,
                completed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_watch_history_recent ON watch_history(last_played_at DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_history_resume ON watch_history(completed, position, last_played_at DESC);

            CREATE TABLE IF NOT EXISTS entity_favorites (
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(entity_type, entity_id)
            );

            CREATE TABLE IF NOT EXISTS scene_markers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                media_id INTEGER NOT NULL,
                position REAL NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                tag TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_scene_markers_media_position ON scene_markers(media_id, position);

            CREATE TABLE IF NOT EXISTS entity_refresh_state (
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                last_success_at TEXT,
                last_attempt_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'UNKNOWN',
                error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(entity_type, entity_id)
            );
            CREATE INDEX IF NOT EXISTS idx_entity_refresh_success ON entity_refresh_state(entity_type, status, last_success_at DESC);

            -- Artwork refresh is tracked independently from provider metadata. A
            -- successful metadata lookup must not hide a missing/failed entity image.
            CREATE TABLE IF NOT EXISTS entity_artwork_state (
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                last_success_at TEXT,
                last_attempt_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'UNKNOWN',
                source TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(entity_type, entity_id)
            );
            CREATE INDEX IF NOT EXISTS idx_entity_artwork_state_status
                ON entity_artwork_state(entity_type, status, last_attempt_at DESC);

            CREATE TABLE IF NOT EXISTS provider_sync_state (
                provider TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                cursor TEXT NOT NULL DEFAULT '',
                etag TEXT NOT NULL DEFAULT '',
                last_sync_at TEXT,
                next_sync_at TEXT,
                status TEXT NOT NULL DEFAULT 'IDLE',
                error TEXT NOT NULL DEFAULT '',
                request_count INTEGER NOT NULL DEFAULT 0,
                last_latency_ms REAL,
                rate_remaining INTEGER,
                rate_reset_at REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(provider, entity_type)
            );

            CREATE TABLE IF NOT EXISTS provider_field_sources (
                media_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                provider TEXT NOT NULL,
                provider_record_id TEXT NOT NULL DEFAULT '',
                value_hash TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                is_effective INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(media_id, field, provider)
            );
            CREATE INDEX IF NOT EXISTS idx_provider_field_sources_media ON provider_field_sources(media_id, field);

            CREATE TABLE IF NOT EXISTS api_cache_stats (
                provider TEXT PRIMARY KEY,
                hits INTEGER NOT NULL DEFAULT 0,
                misses INTEGER NOT NULL DEFAULT 0,
                throttled INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0,
                last_request_at TEXT,
                updated_at TEXT NOT NULL
            );

            """)
            # Full-text search over title/filename/caption for the library
            # search box (design doc: search must stay fast at 20k+ items).
            try:
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS media_fts USING fts5("
                    "title, filename, caption, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')"
                )
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS performer_fts USING fts5("
                    "name, aliases, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')"
                )
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS tag_fts USING fts5("
                    "name, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')"
                )
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS studio_fts USING fts5("
                    "name, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4 5 6 7 8 10 12')"
                )
                self.fts_available = True
            except sqlite3.OperationalError:
                self.fts_available = False
            self._migrate_columns()
            self.ai_persistence.migrate_legacy_schema()
            self._migrate_entity_identity()
            self._repair_unsafe_title_references()
            self._repair_ambiguous_title_mappings()
            self._rebuild_search_indexes()
            self._install_search_triggers()
            # Indexes that depend on migration columns must be created after the
            # ALTER TABLE pass; SQLite rejects CREATE INDEX on a missing column.
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_metadata_source ON media(metadata_source)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_identification_state ON media(identification_state)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_thumbnail_status_id ON media(thumbnail_status, id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_thumbnail_updated ON media(thumbnail_status, updated_at)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_performers_image_state ON performers(image_locked, image, id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_studios_image_state ON studios(image_locked, image, id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_studios_name_id ON studios(name, id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tags_name_id ON tags(name, id)")
            # System-wide local resource registry: SQLite remains the control-plane
            # authority while Telegram remains the large-video object store.
            from .resource_registry import ensure_resource_tables
            ensure_resource_tables(self)
            # V29.4.222: inventory-only Telegram document registry migration.
            # This copies locally available catalogue/readiness state and never
            # downloads messages or media from Telegram.
            from .telegram_registry import ensure_telegram_registry
            ensure_telegram_registry(self)
            self.conn.commit()
            self._migrate_job_orchestration_schema_locked()
            self._migrate_job_journal_schema_locked()
            self._repair_legacy_catalogue_ids()
            self._load_persistent_metadata_keys()
            self._backfill_identification_state()
            # V29.4.168: delivery history is durable and verified Telegram posts
            # are materialized as Library records. The one-time DB-only reconcile
            # repairs deliveries made by older builds without uploading again.
            try:
                from ..engines.delivery_ledger import ensure_delivery_ledger_schema, reconcile_verified_deliveries
                ensure_delivery_ledger_schema(self)
                with self.lock:
                    marker = self.conn.execute("SELECT value FROM settings WHERE key='archive_x_delivery_reconcile_v168'").fetchone()
                if not marker or str(marker["value"] or "") != "1":
                    result = reconcile_verified_deliveries(self)
                    self.log(
                        "Verified delivery history reconcile: "
                        f"checked={int(result.get('checked') or 0)}, registered={int(result.get('registered') or 0)}, "
                        f"errors={int(result.get('errors') or 0)}",
                        "INFO" if not result.get("errors") else "WARN",
                    )
                    if not result.get("errors"):
                        with self.lock:
                            self.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('archive_x_delivery_reconcile_v168','1')")
                            self.conn.commit()
            except Exception as exc:
                self.log(f"Verified delivery history reconcile was deferred: {type(exc).__name__}: {exc}", "WARN")
            # V29.4.197: repair historical upload rows keyed by raw positive
            # entity IDs and remap preserved preview assets to canonical peers.
            # This is a local DB/filesystem repair; it never re-uploads media.
            try:
                from ..engines.delivery_ledger import repair_legacy_channel_peer_ids, reconcile_verified_deliveries
                with self.lock:
                    marker = self.conn.execute("SELECT value FROM settings WHERE key='archive_x_peer_identity_snapshots_v197'").fetchone()
                if not marker or str(marker["value"] or "") != "1":
                    peer_result = repair_legacy_channel_peer_ids(self)
                    delivery_result = reconcile_verified_deliveries(self)
                    self.log(
                        "Channel identity/preview repair: "
                        f"channels={int(peer_result.get('channels') or 0)}, "
                        f"media_rekeyed={int(peer_result.get('media_rows') or 0)}, "
                        f"duplicates={int(peer_result.get('duplicates') or 0)}, "
                        f"delivery_checked={int(delivery_result.get('checked') or 0)}, "
                        f"errors={int(delivery_result.get('errors') or 0)}",
                        "INFO" if not delivery_result.get("errors") else "WARN",
                    )
                    if not delivery_result.get("errors"):
                        with self.lock:
                            self.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('archive_x_peer_identity_snapshots_v197','1')")
                            self.conn.commit()
            except Exception as exc:
                self.log(f"Channel identity/preview repair was deferred: {type(exc).__name__}: {exc}", "WARN")
            # V29.4.231: historical Telegram transfers materialized destination
            # rows but left the old source media row active after source deletion.
            # Repair that stale Library state from verified delivery history only;
            # this is DB-only and does not contact Telegram at startup.
            try:
                from ..engines.delivery_ledger import repair_verified_transfer_source_library_rows
                moved_result = repair_verified_transfer_source_library_rows(self)
                if any(int(moved_result.get(k) or 0) for k in (
                    "source_rows_hidden", "locations_retired", "cleanup_queued", "destinations_marked_prepared"
                )):
                    self.log(
                        "Verified transfer source Library reconciliation: "
                        f"checked={int(moved_result.get('checked') or 0)}, "
                        f"source_rows_hidden={int(moved_result.get('source_rows_hidden') or 0)}, "
                        f"locations_retired={int(moved_result.get('locations_retired') or 0)}, "
                        f"cleanup_queued={int(moved_result.get('cleanup_queued') or 0)}, "
                        f"destinations_marked_prepared={int(moved_result.get('destinations_marked_prepared') or 0)}",
                        "INFO",
                    )
            except Exception as exc:
                self.log(f"Verified transfer source Library reconciliation was deferred: {type(exc).__name__}: {exc}", "WARN")
            # Re-run the DB-only migration after legacy delivery reconciliation and
            # peer-ID repair have materialized any additional media rows.
            try:
                from .telegram_registry import ensure_telegram_registry
                self._telegram_registry_ready = None
                ensure_telegram_registry(self)
            except Exception as exc:
                self.log(f"Telegram document registry migration was deferred: {type(exc).__name__}: {exc}", "WARN")


    # ------------------------------------------------------------------
    # SUBSECTION: Job orchestration schema
    # Schema migration for job orchestration tables.
    # ------------------------------------------------------------------
    def _migrate_job_orchestration_schema_locked(self):
        """Additive migration for the canonical Job Orchestrator admission contract."""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)").fetchall()}
        additions = {
            "admission_state": "TEXT NOT NULL DEFAULT 'READY'",
            "admission_reason": "TEXT NOT NULL DEFAULT ''",
            "next_run_at": "TEXT",
            "priority": "INTEGER NOT NULL DEFAULT 50",
            "attempt": "INTEGER NOT NULL DEFAULT 0",
            "admission_updated_at": "TEXT",
            "execution_id": "TEXT",
            "worker_id": "TEXT",
            "lease_id": "TEXT",
            "lease_expires_at": "TEXT",
            "last_heartbeat_at": "TEXT",
            "worker_state": "TEXT NOT NULL DEFAULT 'IDLE'",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
            "max_retries": "INTEGER NOT NULL DEFAULT 3",
            "failure_class": "TEXT NOT NULL DEFAULT ''",
            "deadline_at": "TEXT",
            "resource_requirements": "TEXT NOT NULL DEFAULT '{}'",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_admission ON jobs(status, admission_state, priority, next_run_at, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_admission_reason ON jobs(admission_state, admission_reason, updated_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_execution_lease ON jobs(lease_expires_at, worker_state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_retry ON jobs(status, next_run_at, retry_count, priority, created_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_deadline ON jobs(status, deadline_at, priority, created_at)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS job_dependencies (job_id TEXT NOT NULL, depends_on_job_id TEXT NOT NULL, dependency_type TEXT NOT NULL DEFAULT 'SUCCESS', created_at TEXT NOT NULL, PRIMARY KEY(job_id, depends_on_job_id), FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE, FOREIGN KEY(depends_on_job_id) REFERENCES jobs(id) ON DELETE CASCADE)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_job_dependencies_parent ON job_dependencies(depends_on_job_id)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS job_executions (execution_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, attempt INTEGER NOT NULL, worker_id TEXT NOT NULL, lease_id TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT NOT NULL, heartbeat_at TEXT NOT NULL, lease_expires_at TEXT NOT NULL, ended_at TEXT, failure_class TEXT NOT NULL DEFAULT '', FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_job_executions_job ON job_executions(job_id, attempt)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_job_executions_lease ON job_executions(lease_expires_at, status)")
        self._migrate_execution_lane_schema_locked()
        self._migrate_resource_lease_schema_locked()
        self.conn.execute("UPDATE jobs SET admission_state=CASE WHEN status='QUEUED' THEN 'READY' ELSE 'READY' END WHERE admission_state IS NULL OR admission_state=''")
        self.conn.execute("UPDATE jobs SET admission_updated_at=COALESCE(admission_updated_at, updated_at) WHERE admission_updated_at IS NULL")
        self.conn.commit()



    # ------------------------------------------------------------------
    # SUBSECTION: Execution lanes
    # Lane slot acquire, heartbeat, release and expiry.
    # ------------------------------------------------------------------
    def _migrate_execution_lane_schema_locked(self):
        """Durable execution-lane capacity/slot ownership contract (V29.4.20+)."""
        self.conn.execute("""CREATE TABLE IF NOT EXISTS execution_lane_slots (
            slot_id TEXT PRIMARY KEY, lane_id TEXT NOT NULL, job_id TEXT NOT NULL,
            execution_id TEXT, lease_id TEXT, state TEXT NOT NULL DEFAULT 'ACTIVE',
            acquired_at TEXT NOT NULL, expires_at TEXT, last_heartbeat TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_lane_slots_lane_state ON execution_lane_slots(lane_id,state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_lane_slots_execution ON execution_lane_slots(execution_id,state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_lane_slots_expiry ON execution_lane_slots(expires_at,state)")

    def acquire_execution_lane_slots(self, job_id, execution_id, lane_ids, *, capacities, lease_id=None, lease_seconds=45):
        import uuid
        from datetime import datetime, timezone, timedelta
        lanes=sorted(set(str(x) for x in (lane_ids or []) if x))
        now=datetime.now(timezone.utc); ts=now.isoformat(); exp=(now+timedelta(seconds=max(5,int(lease_seconds)))).isoformat()
        with self.lock:
            self.conn.execute("DELETE FROM execution_lane_slots WHERE expires_at IS NOT NULL AND expires_at < ? AND state IN ('ACTIVE','DRAINING')", (ts,))
            # Single query to get all lane counts instead of one per lane
            if lanes:
                ph=",".join("?" for _ in lanes)
                lane_counts={row["lane_id"]:int(row["n"]) for row in self.conn.execute(
                    f"SELECT lane_id,COUNT(*) n FROM execution_lane_slots WHERE lane_id IN ({ph}) AND state IN ('ACTIVE','DRAINING') GROUP BY lane_id",lanes).fetchall()}
            else:
                lane_counts={}
            for lane in lanes:
                cap=max(1,int(capacities.get(lane,1)))
                used=lane_counts.get(lane,0)
                if used>=cap:
                    return False
            for lane in lanes:
                sid=uuid.uuid4().hex
                self.conn.execute("INSERT INTO execution_lane_slots(slot_id,lane_id,job_id,execution_id,lease_id,state,acquired_at,expires_at,last_heartbeat) VALUES(?,?,?,?,?,?,?,?,?)",(sid,lane,str(job_id),str(execution_id),lease_id or execution_id,'ACTIVE',ts,exp,ts))
            self.conn.commit()
        return True

    def release_execution_lane_slots(self, *, execution_id=None, job_id=None, state='RELEASED'):
        target=str(state or 'RELEASED')
        with self.lock:
            if execution_id:
                cur=self.conn.execute("UPDATE execution_lane_slots SET state=?,expires_at=NULL WHERE execution_id=? AND state IN ('ACTIVE','DRAINING')",(target,execution_id))
            elif job_id:
                cur=self.conn.execute("UPDATE execution_lane_slots SET state=?,expires_at=NULL WHERE job_id=? AND state IN ('ACTIVE','DRAINING')",(target,job_id))
            else:
                cur=self.conn.execute("UPDATE execution_lane_slots SET state=?,expires_at=NULL WHERE state IN ('ACTIVE','DRAINING')",(target,))
            self.conn.commit()
        return cur.rowcount

    def expire_execution_lane_slots(self):
        from datetime import datetime, timezone
        ts=datetime.now(timezone.utc).isoformat()
        with self.lock:
            rows=self.conn.execute("SELECT slot_id FROM execution_lane_slots WHERE state IN ('ACTIVE','DRAINING') AND expires_at IS NOT NULL AND expires_at < ?",(ts,)).fetchall()
            if rows:
                self.conn.executemany("UPDATE execution_lane_slots SET state='EXPIRED',expires_at=NULL WHERE slot_id=?",[(r[0],) for r in rows])
                self.conn.commit()
            return len(rows)

    def execution_lane_snapshot(self, capacities=None):
        self.expire_execution_lane_slots()
        capacities=capacities or {}
        with self.reading() as conn:
            rows=conn.execute("SELECT lane_id,state,COUNT(*) n FROM execution_lane_slots GROUP BY lane_id,state").fetchall()
            active={}
            expired=0
            for row in rows:
                if row['state']=='EXPIRED': expired+=int(row['n'])
                elif row['state'] in ('ACTIVE','DRAINING'): active[row['lane_id']]=active.get(row['lane_id'],0)+int(row['n'])
            lanes={}
            for lane,cap in capacities.items():
                a=active.get(lane,0); lanes[lane]={'capacity':int(cap),'active':a,'available':max(0,int(cap)-a)}
            return {'lanes':lanes,'expired_slots':expired,'total_active':sum(active.values())}


    # ------------------------------------------------------------------
    # SUBSECTION: Resource leases
    # Lease acquire, bind, heartbeat, release and invalidation.
    # ------------------------------------------------------------------
    def _migrate_resource_lease_schema_locked(self):
        """Create/migrate the single durable resource-lease ownership contract.

        Older releases stored only a launch-owned resource key.  The key remains
        compatible, while the lease is now explicitly linked to job execution,
        has lifecycle/heartbeat timestamps, and uses the same execution lease ID
        once a worker starts.
        """
        self.conn.execute("""CREATE TABLE IF NOT EXISTS resource_leases (
            resource_key TEXT PRIMARY KEY,
            job_id TEXT,
            owner_launch_id TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            lease_id TEXT,
            execution_id TEXT,
            resource_class TEXT NOT NULL DEFAULT 'BACKGROUND',
            resource_type TEXT NOT NULL DEFAULT 'GENERIC',
            reserved_amount REAL NOT NULL DEFAULT 1,
            actual_amount REAL NOT NULL DEFAULT 0,
            created_at TEXT,
            expires_at TEXT,
            last_heartbeat TEXT,
            state TEXT NOT NULL DEFAULT 'RESERVED'
        )""")
        columns={row[1] for row in self.conn.execute("PRAGMA table_info(resource_leases)").fetchall()}
        additions={
            "lease_id":"TEXT", "execution_id":"TEXT",
            "resource_class":"TEXT NOT NULL DEFAULT 'BACKGROUND'",
            "resource_type":"TEXT NOT NULL DEFAULT 'GENERIC'",
            "reserved_amount":"REAL NOT NULL DEFAULT 1",
            "actual_amount":"REAL NOT NULL DEFAULT 0",
            "created_at":"TEXT", "expires_at":"TEXT",
            "last_heartbeat":"TEXT",
            "state":"TEXT NOT NULL DEFAULT 'RESERVED'",
        }
        for name, sql_type in additions.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE resource_leases ADD COLUMN {name} {sql_type}")
        self.conn.execute("UPDATE resource_leases SET created_at=COALESCE(created_at, acquired_at), last_heartbeat=COALESCE(last_heartbeat, updated_at), state=COALESCE(NULLIF(state,''),'RESERVED'), resource_type=COALESCE(NULLIF(resource_type,''),resource_key)")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_resource_leases_lease_id ON resource_leases(lease_id) WHERE lease_id IS NOT NULL")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_leases_execution ON resource_leases(execution_id, state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_leases_expiry ON resource_leases(expires_at, state)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_resource_leases_job_state ON resource_leases(job_id, state)")

    def acquire_resource_lease(self, resource_key, job_id, *, owner_launch_id, resource_class="BACKGROUND", resource_type=None, reserved_amount=1, lease_seconds=45):
        import uuid
        from datetime import datetime, timezone, timedelta
        now=datetime.now(timezone.utc); ts=now.isoformat(); exp=(now+timedelta(seconds=max(5,int(lease_seconds)))).isoformat()
        lease_id=uuid.uuid4().hex
        key=str(resource_key); rtype=str(resource_type or key)
        with self.lock:
            self.conn.execute("""INSERT INTO resource_leases(resource_key,job_id,owner_launch_id,acquired_at,updated_at,lease_id,execution_id,resource_class,resource_type,reserved_amount,actual_amount,created_at,expires_at,last_heartbeat,state)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(resource_key) DO UPDATE SET job_id=excluded.job_id,owner_launch_id=excluded.owner_launch_id,updated_at=excluded.updated_at,lease_id=excluded.lease_id,execution_id=NULL,resource_class=excluded.resource_class,resource_type=excluded.resource_type,reserved_amount=excluded.reserved_amount,actual_amount=0,created_at=excluded.created_at,expires_at=excluded.expires_at,last_heartbeat=excluded.last_heartbeat,state='RESERVED'""",
                (key,str(job_id) if job_id is not None else None,str(owner_launch_id),ts,ts,lease_id,None,str(resource_class or "BACKGROUND"),rtype,float(reserved_amount or 0),0.0,ts,exp,ts,"RESERVED"))
            self.conn.commit()
        return {"lease_id":lease_id,"resource_key":key,"job_id":job_id,"state":"RESERVED","expires_at":exp}

    def invalidate_stale_resource_leases(self, owner_launch_id):
        with self.lock:
            cur=self.conn.execute("UPDATE resource_leases SET state='EXPIRED',expires_at=NULL,updated_at=? WHERE owner_launch_id != ? AND state IN ('RESERVED','ACTIVE','RELEASING')",(config.now_iso(),str(owner_launch_id)))
            count=int(cur.rowcount or 0); self.conn.commit()
            return count

    def expire_resource_leases(self):
        ts=config.now_iso()
        with self.lock:
            expired=self.conn.execute(
                "SELECT resource_key,job_id,execution_id,expires_at FROM resource_leases "
                "WHERE state IN ('RESERVED','ACTIVE') AND expires_at IS NOT NULL AND expires_at < ?",
                (ts,)
            ).fetchall()
            if expired:
                self.conn.executemany(
                    "UPDATE resource_leases SET state='EXPIRED',expires_at=NULL,updated_at=? WHERE resource_key=?",
                    [(ts,r['resource_key']) for r in expired]
                )
                self.conn.commit()
            return [dict(r) for r in expired]

    def resource_lease_snapshot(self):
        self.expire_resource_leases()
        self.expire_execution_lane_slots()
        with self.reading() as rdb:
            counts=rdb.execute("SELECT state,COUNT(*) AS n FROM resource_leases GROUP BY state").fetchall()
            active=rdb.execute("SELECT * FROM resource_leases WHERE state IN ('RESERVED','ACTIVE','RELEASING') ORDER BY updated_at DESC LIMIT 100").fetchall()
        out={"states":{},"active":int(sum(int(r['n'] or 0) for r in counts if str(r['state']) in {'RESERVED','ACTIVE','RELEASING'})),"expired":0,"leases":[]}
        for r in counts: out['states'][str(r['state'])]=int(r['n'] or 0)
        out['expired']=int(out['states'].get('EXPIRED',0))
        out['leases']=[dict(r) for r in active]
        out['leaked']=int(out['states'].get('EXPIRED',0))
        return out


    # ------------------------------------------------------------------
    # SUBSECTION: Job dependencies, executions and retry policy
    # Orchestration state for jobs: dependencies, execution records and admission.
    # ------------------------------------------------------------------
    def add_job_dependencies(self, jid, dependencies, dependency_type="SUCCESS"):
        deps=[str(x) for x in (dependencies or []) if str(x) and str(x)!=str(jid)]
        if not deps: return
        ts=config.now_iso()
        with self.lock:
            unique_deps=list(dict.fromkeys(deps))
            # Batch-verify all dependency jobs exist
            ph=",".join("?" for _ in unique_deps)
            found={str(r[0]) for r in self.conn.execute(f"SELECT id FROM jobs WHERE id IN ({ph})",unique_deps).fetchall()}
            for dep in unique_deps:
                if dep not in found:
                    raise ValueError(f"Dependency job not found: {dep}")
            # Pre-load the full dependency graph for cycle detection
            all_edges=self.conn.execute("SELECT job_id,depends_on_job_id FROM job_dependencies").fetchall()
            dep_graph={}
            for edge in all_edges:
                dep_graph.setdefault(str(edge[0]),[]).append(str(edge[1]))
            for dep in unique_deps:
                frontier=[dep]; seen=set()
                while frontier:
                    node=frontier.pop()
                    if node==str(jid):
                        raise ValueError(f"Job dependency cycle detected: {jid} -> {dep}")
                    if node in seen: continue
                    seen.add(node)
                    frontier.extend(dep_graph.get(node,[]))
                self.conn.execute("INSERT OR IGNORE INTO job_dependencies(job_id,depends_on_job_id,dependency_type,created_at) VALUES(?,?,?,?)", (jid,dep,str(dependency_type or "SUCCESS").upper(),ts))
            self.conn.commit()

    def job_dependencies(self, jid):
        with self.lock:
            rows=self.conn.execute("SELECT d.*, j.status AS dependency_status FROM job_dependencies d LEFT JOIN jobs j ON j.id=d.depends_on_job_id WHERE d.job_id=? ORDER BY d.created_at", (jid,)).fetchall()
        return [dict(r) for r in rows]

    def acquire_job_execution(self, jid, *, worker_id, lease_seconds=45):
        import uuid
        from datetime import datetime, timezone, timedelta
        now=datetime.now(timezone.utc); ts=now.isoformat(); exp=(now+timedelta(seconds=max(5,int(lease_seconds)))).isoformat()
        execution_id=uuid.uuid4().hex; lease_id=uuid.uuid4().hex
        with self.lock:
            self._begin_immediate_locked()
            try:
                row=self.conn.execute("SELECT status,attempt,execution_id,worker_id FROM jobs WHERE id=?",(jid,)).fetchone()
                if not row: raise ValueError(f"Job not found: {jid}")
                if row["execution_id"]: raise RuntimeError(f"Job already has active execution: {jid}")
                attempt=int(row["attempt"] or 0)+1
                self.conn.execute("UPDATE jobs SET execution_id=?,worker_id=?,lease_id=?,lease_expires_at=?,last_heartbeat_at=?,worker_state='STARTING',attempt=?,updated_at=? WHERE id=?",(execution_id,worker_id,lease_id,exp,ts,attempt,ts,jid))
                self.conn.execute("INSERT INTO job_executions(execution_id,job_id,attempt,worker_id,lease_id,status,started_at,heartbeat_at,lease_expires_at) VALUES(?,?,?,?,?,?,?,?,?)",(execution_id,jid,attempt,worker_id,lease_id,"RUNNING",ts,ts,exp))
                # Bind all RESERVED resource ownership in the same transaction as
                # execution acquisition. A partial execution can therefore never
                # survive a resource-binding failure.
                self.conn.execute("UPDATE resource_leases SET execution_id=?,lease_id=?,state='ACTIVE',expires_at=?,last_heartbeat=?,updated_at=? WHERE job_id=? AND state='RESERVED'",(execution_id,lease_id,exp,ts,ts,jid))
                self._append_job_journal_locked(jid,"EXECUTION_STARTED",status=row["status"],operation="Worker execution started",payload={"execution_id":execution_id,"worker_id":worker_id,"lease_id":lease_id,"attempt":attempt})
                self.conn.commit()
            except Exception:
                self.conn.rollback(); raise
        return {"execution_id":execution_id,"lease_id":lease_id,"attempt":attempt,"lease_expires_at":exp}


    def interrupt_stale_job_execution(self, jid, *, reason="Previous application launch ended before execution completed"):
        """Atomically invalidate previous-launch execution ownership.

        Startup recovery is authoritative for the new process: the previous
        process can no longer legitimately renew this execution lease. Clear
        execution/resource/lane ownership together so recovery can create a
        fresh execution without being blocked by stale ownership.
        """
        ts=config.now_iso()
        with self.lock:
            self._begin_immediate_locked()
            try:
                row=self.conn.execute("SELECT execution_id FROM jobs WHERE id=?",(jid,)).fetchone()
                execution_id=str(row["execution_id"] or "") if row else ""
                if execution_id:
                    self.conn.execute("UPDATE job_executions SET status='INTERRUPTED',ended_at=?,failure_class=COALESCE(NULLIF(failure_class,''),'UNKNOWN') WHERE execution_id=? AND ended_at IS NULL",(ts,execution_id))
                    self.conn.execute("UPDATE resource_leases SET state='EXPIRED',expires_at=NULL,updated_at=? WHERE execution_id=? AND state IN ('RESERVED','ACTIVE','RELEASING')",(ts,execution_id))
                    self.conn.execute("UPDATE execution_lane_slots SET state='RELEASED',expires_at=NULL WHERE execution_id=? AND state IN ('ACTIVE','DRAINING')",(execution_id,))
                    self.conn.execute("UPDATE jobs SET execution_id=NULL,worker_id=NULL,lease_id=NULL,lease_expires_at=NULL,last_heartbeat_at=NULL,worker_state='INTERRUPTED',updated_at=? WHERE id=? AND execution_id=?",(ts,jid,execution_id))
                self.conn.commit()
                return execution_id or None
            except Exception:
                self.conn.rollback(); raise

    def heartbeat_job_execution(self, jid, execution_id, *, worker_state="RUNNING", lease_seconds=45):
        from datetime import datetime, timezone, timedelta
        now=datetime.now(timezone.utc); ts=now.isoformat(); exp=(now+timedelta(seconds=max(5,int(lease_seconds)))).isoformat()
        with self.lock:
            cur=self.conn.execute("UPDATE jobs SET last_heartbeat_at=?,lease_expires_at=?,worker_state=?,updated_at=? WHERE id=? AND execution_id=?",(ts,exp,str(worker_state or "RUNNING"),ts,jid,execution_id))
            self.conn.execute("UPDATE job_executions SET heartbeat_at=?,lease_expires_at=?,status=? WHERE execution_id=?",(ts,exp,"RUNNING",execution_id))
            self.conn.execute("UPDATE resource_leases SET expires_at=?,last_heartbeat=?,updated_at=? WHERE execution_id=? AND state='ACTIVE'",(exp,ts,ts,execution_id))
            self.conn.commit()
            return cur.rowcount > 0

    def finish_job_execution(self, jid, execution_id, *, status="COMPLETED", failure_class=""):
        ts=config.now_iso()
        with self.lock:
            self.conn.execute("UPDATE job_executions SET status=?,ended_at=?,failure_class=? WHERE execution_id=?",(status,ts,str(failure_class or ""),execution_id))
            self.conn.execute("UPDATE resource_leases SET state='RELEASED',expires_at=NULL,updated_at=? WHERE execution_id=? AND state IN ('RESERVED','ACTIVE','RELEASING')",(ts,execution_id))
            self.conn.execute("UPDATE execution_lane_slots SET state='RELEASED',expires_at=NULL WHERE execution_id=? AND state IN ('ACTIVE','DRAINING')",(execution_id,))
            self.conn.execute("UPDATE jobs SET execution_id=NULL,worker_id=NULL,lease_id=NULL,lease_expires_at=NULL,last_heartbeat_at=NULL,worker_state=?,failure_class=?,updated_at=? WHERE id=? AND execution_id=?",("EXITED",str(failure_class or ""),ts,jid,execution_id))
            self.conn.commit()

    def expired_job_executions(self):
        ts=config.now_iso()
        with self.lock:
            rows=self.conn.execute(
                "SELECT * FROM jobs WHERE execution_id IS NOT NULL "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < ?",
                (ts,)
            ).fetchall()
        return [dict(r) for r in rows]

    def set_job_retry_policy(self, jid, *, retry_count=None, max_retries=None, failure_class=None, next_run_at=None):
        fields={};
        if retry_count is not None: fields["retry_count"]=max(0,int(retry_count))
        if max_retries is not None: fields["max_retries"]=max(0,int(max_retries))
        if failure_class is not None: fields["failure_class"]=str(failure_class or "")[:80]
        if next_run_at is not None or retry_count is not None: fields["next_run_at"]=next_run_at
        if not fields: return
        self.update_job(jid, **fields)

    def set_job_admission(self, jid, state, *, reason="", next_run_at=None, priority=None, operation=None, detail=None, payload=None):
        """Persist the orchestrator's admission decision and journal it atomically."""
        state = str(state or "READY").upper()
        allowed = {"READY", "WAITING", "BLOCKED", "DELAYED"}
        if state not in allowed:
            raise ValueError(f"Unknown job admission state: {state}")
        ts = config.now_iso()
        with self.lock:
            # Database.log() intentionally batches low-priority events in the
            # same SQLite connection. Flush that buffered transaction before
            # beginning the atomic admission mutation so the new contract does
            # not conflict with the existing event batching path.
            self._begin_immediate_locked()
            try:
                row = self.conn.execute("SELECT status,job_type,admission_state,admission_reason FROM jobs WHERE id=?", (jid,)).fetchone()
                if not row:
                    raise ValueError(f"Job not found: {jid}")
                normalized_reason = str(reason or "")
                normalized_priority = max(0, min(100, int(priority))) if priority is not None else None
                admission_changed = (str(row["admission_state"] or "READY").upper() != state or str(row["admission_reason"] or "") != normalized_reason)
                existing = self.conn.execute("SELECT next_run_at,priority FROM jobs WHERE id=?", (jid,)).fetchone()
                admission_changed = admission_changed or existing["next_run_at"] != next_run_at
                if normalized_priority is not None:
                    admission_changed = admission_changed or int(existing["priority"] or 50) != normalized_priority
                fields = {"admission_state": state, "admission_reason": normalized_reason, "next_run_at": next_run_at, "admission_updated_at": ts, "updated_at": ts}
                if operation:
                    fields["current_operation"] = str(operation)[:300]
                if normalized_priority is not None:
                    fields["priority"] = normalized_priority
                sets=", ".join(f"{k}=?" for k in fields); vals=list(fields.values())+[jid]
                self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)
                if admission_changed:
                    changed = {"admission_state": state, "admission_reason": normalized_reason, "next_run_at": next_run_at}
                    if normalized_priority is not None:
                        changed["priority"] = normalized_priority
                    self._append_job_journal_locked(jid, "ADMISSION_CHANGED", status=row["status"], operation=operation or row["admission_state"], detail=detail or normalized_reason, payload={"job_type":row["job_type"], **changed})
                self.conn.commit()
            except Exception:
                self.conn.rollback(); raise

    def job_orchestration_snapshot(self):
        """Return the durable orchestration control-plane snapshot."""
        with self.reading() as rdb:
            rows=rdb.execute("SELECT admission_state,admission_reason,COUNT(*) AS n FROM jobs WHERE status='QUEUED' GROUP BY admission_state,admission_reason").fetchall()
            running=rdb.execute("SELECT worker_state,COUNT(*) AS n FROM jobs WHERE status IN ('RUNNING','PAUSING','CANCEL_REQUESTED') GROUP BY worker_state").fetchall()
            deps=rdb.execute("SELECT COUNT(*) AS n FROM job_dependencies d JOIN jobs j ON j.id=d.job_id WHERE j.status='QUEUED'").fetchone()
            retry=rdb.execute("SELECT COUNT(*) AS n FROM jobs WHERE status='QUEUED' AND retry_count>0").fetchone()
            active_leases=rdb.execute("SELECT COUNT(*) AS n FROM jobs WHERE execution_id IS NOT NULL").fetchone()
        states={"READY":0,"WAITING":0,"BLOCKED":0,"DELAYED":0}
        reasons={}
        for r in rows:
            state=str(r["admission_state"] or "READY").upper(); n=int(r["n"] or 0)
            states[state]=states.get(state,0)+n
            if r["admission_reason"]:
                reasons[str(r["admission_reason"])]=reasons.get(str(r["admission_reason"]),0)+n
        workers={str(r["worker_state"] or "UNKNOWN"):int(r["n"] or 0) for r in running}
        return {"states":states,"reasons":reasons,"queued":sum(states.values()),"workers":workers,"active_leases":int(active_leases["n"] or 0),"dependency_edges":int(deps["n"] or 0),"retrying":int(retry["n"] or 0)}


    # ------------------------------------------------------------------
    # SUBSECTION: Job journal schema
    # Schema migration for the job journal.
    # ------------------------------------------------------------------
    def _migrate_job_journal_schema_locked(self):
        """Additive migration for older job_journal schemas."""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(job_journal)").fetchall()}
        for name, sql_type in {"event_id":"TEXT","from_status":"TEXT","payload":"TEXT","error_type":"TEXT","retry_count":"INTEGER"}.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE job_journal ADD COLUMN {name} {sql_type}")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_job_journal_job ON job_journal(job_id, sequence)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_job_journal_event ON job_journal(event_type, created_at)")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_job_journal_event_id ON job_journal(event_id) WHERE event_id IS NOT NULL")
        self.conn.commit()


    # ------------------------------------------------------------------
    # SUBSECTION: Search indexes and data repairs
    # Name normalization, title-mapping repairs, FTS rebuilds and triggers.
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_search(value):
        import unicodedata
        text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
        return " ".join(text.lower().split())

    def _repair_unsafe_title_references(self):
        """Remove unsafe title-only scene references from older builds.

        Older versions treated a normalized title such as ``The Pizza Can Wait``
        as a reusable scene identity. That is not unique enough and could link
        the wrong performer/studio when multiple scenes share the same title.
        Existing media touched by those unsafe references are reset to PARTIAL
        so the normal provider/fingerprint pipeline can identify them again.
        """
        with self.lock:
            affected = self.conn.execute(
                """SELECT DISTINCT ra.source_channel, ra.source_message_id
                   FROM reference_aliases ra
                   JOIN reference_keys rk ON rk.canonical_key=ra.canonical_key
                  WHERE rk.canonical_key LIKE 'title:%'
                    AND ra.source_channel IS NOT NULL
                    AND ra.source_message_id IS NOT NULL"""
            ).fetchall()
            repaired = 0
            repair_channels = set()

            # Batch-fetch media IDs for all affected (channel, message) pairs
            affected_pairs = [(str(r["source_channel"]), int(r["source_message_id"])) for r in affected]
            media_id_lookup = {}
            if affected_pairs:
                unique_channels = list({p[0] for p in affected_pairs})
                for ch in unique_channels:
                    msgs_for_ch = [p[1] for p in affected_pairs if p[0] == ch]
                    for start in range(0, len(msgs_for_ch), 500):
                        chunk = msgs_for_ch[start:start + 500]
                        ph = ",".join("?" for _ in chunk)
                        for m in self.conn.execute(
                            f"SELECT id,source_channel,source_message_id FROM media "
                            f"WHERE source_channel=? AND source_message_id IN ({ph})",
                            [ch, *chunk],
                        ).fetchall():
                            media_id_lookup[(str(m["source_channel"]), int(m["source_message_id"]))] = int(m["id"])

            # Batch delete video_performers and video_studios for all affected media
            affected_mids = []
            for row in affected:
                channel = str(row["source_channel"])
                repair_channels.add(channel)
                message = int(row["source_message_id"])
                mid = media_id_lookup.get((channel, message))
                if mid is not None:
                    affected_mids.append(mid)

            for start in range(0, len(affected_mids), 500):
                chunk = affected_mids[start:start + 500]
                ph = ",".join("?" for _ in chunk)
                self.conn.execute(f"DELETE FROM video_performers WHERE video_id IN ({ph})", chunk)
                self.conn.execute(f"DELETE FROM video_studios WHERE video_id IN ({ph})", chunk)

            now = config.now_iso()
            for start in range(0, len(affected_mids), 500):
                chunk = affected_mids[start:start + 500]
                ph = ",".join("?" for _ in chunk)
                self.conn.execute(
                    f"""UPDATE media SET identification_state='REPAIR',
                           metadata_source='needs_review', metadata_synced_at=NULL,
                           provider_scene_id=NULL, provider_updated_at=NULL,
                           site_name=NULL, site_url=NULL, provider_url=NULL,
                           provider_payload=NULL, updated_at=?
                       WHERE id IN ({ph})""",
                    [now, *chunk],
                )
            repaired = len(affected_mids)

            # Remove the unsafe reference keys and their aliases so they cannot
            # be reused by a later metadata sync. Code-based references remain.
            self.conn.execute(
                "DELETE FROM reference_aliases WHERE canonical_key IN (SELECT canonical_key FROM reference_keys WHERE canonical_key LIKE 'title:%')"
            )
            self.conn.execute("DELETE FROM reference_keys WHERE canonical_key LIKE 'title:%'")
            self.reference_repair_channels = sorted(repair_channels)
            if repaired:
                self.log(f"Reference safety repair reset {repaired} media records previously linked by title-only scene keys", "WARN")
            self.conn.commit()
        return repaired

    def _repair_ambiguous_title_mappings(self):
        """Quarantine old metadata links where a repeated title has conflicting casts.

        A title such as ``The Pizza Can Wait`` is not a scene identity. Older
        builds could persist a filename/provider result against the title and
        later reuse it for another upload with the same title. If the same
        normalized title currently has multiple distinct performer sets, the
        safest automatic repair is to remove provider-managed relationships,
        mark every affected video REPAIR, and let the OSHASH/provider pipeline
        identify each file independently. Explicit local performer mappings
        are never removed.
        """
        import re
        def norm_title(value):
            text = str(value or "").strip().casefold()
            text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
            return re.sub(r"\s+", " ", text).strip()

        with self.lock:
            marker = self.conn.execute("SELECT value FROM settings WHERE key=?", ("v18.25_ambiguous_title_repair_done",)).fetchone()
            if marker and str(marker["value"] or "") == "1":
                return 0
            rows = self.conn.execute(
                "SELECT id,source_channel,source_message_id,title,provider_scene_id,identification_state FROM media "
                "WHERE TRIM(COALESCE(title,''))<>'' ORDER BY id"
            ).fetchall()
            groups = {}
            for row in rows:
                key = norm_title(row["title"])
                if key:
                    groups.setdefault(key, []).append(row)

            # Batch-fetch all video_performers for cast comparison
            all_media_ids = [int(r["id"]) for r in rows]
            vp_by_video = {}
            for start in range(0, len(all_media_ids), 500):
                chunk = all_media_ids[start:start + 500]
                ph = ",".join("?" for _ in chunk)
                for vp_row in self.conn.execute(
                    f"SELECT video_id, performer_id FROM video_performers WHERE video_id IN ({ph})", chunk
                ).fetchall():
                    vp_by_video.setdefault(int(vp_row["video_id"]), []).append(int(vp_row["performer_id"]))

            affected = []
            for key, group in groups.items():
                if len(group) < 2:
                    continue
                performer_sets = []
                for row in group:
                    ids = tuple(sorted(vp_by_video.get(int(row["id"]), [])))
                    performer_sets.append(ids)
                if len(set(performer_sets)) < 2:
                    continue
                # Only quarantine rows that still use provider/catalogue links.
                # VERIFIED rows are explicit operator-approved records and are
                # not silently changed.
                for row, pset in zip(group, performer_sets):
                    if str(row["identification_state"] or "") == "VERIFIED":
                        continue
                    if not pset and not row["provider_scene_id"]:
                        continue
                    affected.append(row)

            repair_channels = set()
            repaired = 0
            for row in affected:
                mid = int(row["id"])
                repair_channels.add(str(row["source_channel"]))
                self.conn.execute("DELETE FROM video_performers WHERE video_id=?", (mid,))
                self.conn.execute("DELETE FROM video_studios WHERE video_id=?", (mid,))
                self.conn.execute(
                    """UPDATE media SET identification_state='REPAIR', metadata_source='needs_review',
                       metadata_synced_at=NULL, provider_scene_id=NULL, provider_updated_at=NULL,
                       site_name=NULL, site_url=NULL, provider_url=NULL, provider_payload=NULL, updated_at=?
                       WHERE id=?""",
                    (config.now_iso(), mid),
                )
                repaired += 1

            if repair_channels:
                self.reference_repair_channels = sorted(set(self.reference_repair_channels or []) | repair_channels)
            if repaired:
                self.log(f"Ambiguous-title metadata repair quarantined {repaired} video mappings for independent fingerprint identification", "WARN")
            self.conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", ("v18.25_ambiguous_title_repair_done", "1"))
            self.conn.commit()
        return repaired

    _SEARCH_INDEX_VERSION = "search-index-v2"

    def _search_indexes_in_sync(self) -> bool:
        """Triggers keep FTS current; rebuild only after a version change or drift."""
        try:
            if str(self.setting("search_index_version") or "") != self._SEARCH_INDEX_VERSION:
                return False
            for table, fts in (("media", "media_fts"), ("performers", "performer_fts"),
                               ("tags", "tag_fts"), ("studios", "studio_fts")):
                a = self.conn.execute(f"SELECT COUNT(*), COALESCE(MAX(id),0) FROM {table}").fetchone()
                b = self.conn.execute(f"SELECT COUNT(*), COALESCE(MAX(rowid),0) FROM {fts}").fetchone()
                if tuple(a) != tuple(b):
                    return False
            return True
        except sqlite3.OperationalError:
            return False

    def _refresh_normalized_names(self):
        for table in ("performers", "tags", "studios"):
            rows = self.conn.execute(f"SELECT id,name,normalized_name FROM {table}").fetchall()
            changes = [(self._norm_search(r["name"]), r["id"]) for r in rows
                       if self._norm_search(r["name"]) != (r["normalized_name"] or "")]
            if changes:
                self.conn.executemany(f"UPDATE {table} SET normalized_name=? WHERE id=?", changes)

    def _rebuild_search_indexes(self):
        if not getattr(self, "fts_available", False):
            return
        # v29.4.390: this deleted and rebuilt every search index (and re-normalised
        # every performer/tag/studio name) on every start. The insert/update/delete
        # triggers already keep them current, so rebuild only when out of sync.
        if self._search_indexes_in_sync():
            # Still refresh normalised names (small tables): some writers insert tags
            # or rename performers/studios without updating normalized_name.
            try:
                self._refresh_normalized_names()
            except sqlite3.OperationalError:
                pass
            return
        try:
            self.conn.execute("DELETE FROM media_fts")
            self.conn.execute("INSERT INTO media_fts(rowid,title,filename,caption) SELECT id,COALESCE(title,''),COALESCE(filename,''),COALESCE(caption,'') FROM media")
            self.conn.execute("DELETE FROM performer_fts")
            self.conn.execute("INSERT INTO performer_fts(rowid,name,aliases) SELECT id,COALESCE(name,''),COALESCE(aliases,'') FROM performers")
            self.conn.execute("DELETE FROM tag_fts")
            self.conn.execute("INSERT INTO tag_fts(rowid,name) SELECT id,COALESCE(name,'') FROM tags")
            self.conn.execute("DELETE FROM studio_fts")
            self.conn.execute("INSERT INTO studio_fts(rowid,name) SELECT id,COALESCE(name,'') FROM studios")
            self._refresh_normalized_names()
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES('search_index_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(self._SEARCH_INDEX_VERSION),))
        except sqlite3.OperationalError:
            pass

    def _install_search_triggers(self):
        if not getattr(self, "fts_available", False):
            return
        # V29.4.126: FTS5 tables reject "INSERT OR REPLACE" on an existing
        # rowid with "constraint failed". The old update triggers used it, so
        # every re-save of a known video/performer/tag/studio (for example the
        # channel inventory refresh) failed. Triggers now delete the old index
        # row and insert the new one. Old-style triggers in existing databases
        # are replaced.
        specs = [
            ("media", "media_fts", "title,filename,caption", ("title", "filename", "caption")),
            ("performers", "performer_fts", "name,aliases", ("name", "aliases")),
            ("tags", "tag_fts", "name", ("name",)),
            ("studios", "studio_fts", "name", ("name",)),
        ]
        prefix = {"media": "media", "performers": "performer", "tags": "tag", "studios": "studio"}
        for table, fts, watched, cols in specs:
            p = prefix[table]
            values = ",".join(f"COALESCE(new.{c},'')" for c in cols)
            col_list = ",".join(cols)
            insert = f"INSERT INTO {fts}(rowid,{col_list}) VALUES(new.id,{values});"
            triggers = {
                f"{p}_fts_ai": f"CREATE TRIGGER {p}_fts_ai AFTER INSERT ON {table} BEGIN DELETE FROM {fts} WHERE rowid=new.id; {insert} END",
                f"{p}_fts_au": f"CREATE TRIGGER {p}_fts_au AFTER UPDATE OF {watched} ON {table} BEGIN DELETE FROM {fts} WHERE rowid=old.id; {insert} END",
                f"{p}_fts_ad": f"CREATE TRIGGER {p}_fts_ad AFTER DELETE ON {table} BEGIN DELETE FROM {fts} WHERE rowid=old.id; END",
            }
            for name, sql in triggers.items():
                try:
                    row = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
                    existing = str(row[0] or "") if row else ""
                    if existing and "INSERT OR REPLACE INTO" in existing.upper():
                        self.conn.execute(f"DROP TRIGGER IF EXISTS {name}")
                        existing = ""
                    if not existing:
                        self.conn.execute(sql)
                except sqlite3.OperationalError:
                    pass


    # ------------------------------------------------------------------
    # SUBSECTION: Column and identity migrations
    # Additive column migrations, entity identity and catalogue ID repair.
    # ------------------------------------------------------------------
    def _migrate_columns(self):
        """Add columns introduced after a database was first created, so
        an existing archive.db from an older version upgrades in place
        instead of needing to be deleted."""
        self.conn.execute("""CREATE TABLE IF NOT EXISTS entity_metadata (
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            provider_id TEXT,
            payload TEXT NOT NULL,
            normalized TEXT NOT NULL DEFAULT '{}',
            fetched_at REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(entity_type, entity_id, provider)
        )""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_metadata_lookup ON entity_metadata(entity_type, entity_id, provider)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_metadata_provider_id ON entity_metadata(provider, provider_id)")

        wanted = {
            "media": {
                "caption": "TEXT", "thumbnail_status": "TEXT",
                "metadata_source": "TEXT", "provider_scene_id": "TEXT",
                "provider_updated_at": "TEXT", "metadata_synced_at": "TEXT",
                "scene_type": "TEXT", "site_name": "TEXT", "site_url": "TEXT",
                "provider_url": "TEXT", "provider_payload": "TEXT",
                "identification_state": "TEXT", "video_phash": "TEXT",
                "uploaded_oshash": "TEXT", "uploaded_probe_json": "TEXT",
            },
            "jobs": {"detail": "TEXT", "dry_run": "INTEGER", "completed": "INTEGER", "total": "INTEGER",
                     "current_operation": "TEXT"},
            "performers": {"image": "TEXT", "profile": "TEXT", "bio": "TEXT", "gender": "TEXT", "provider_ids": "TEXT", "normalized_name": "TEXT", "canonical_key": "TEXT", "aliases": "TEXT", "image_source": "TEXT", "image_locked": "INTEGER DEFAULT 0", "image_crop_version": "INTEGER NOT NULL DEFAULT 0", "name_locked": "INTEGER DEFAULT 0", "profile_locked": "INTEGER DEFAULT 0"},
            "studios": {"provider_ids": "TEXT", "normalized_name": "TEXT", "canonical_key": "TEXT", "aliases": "TEXT", "image": "TEXT", "image_source": "TEXT", "image_locked": "INTEGER DEFAULT 0", "name_locked": "INTEGER DEFAULT 0"},
            "tags": {"normalized_name": "TEXT"},
            "thumbnail_cache": {"visual_hash": "TEXT"},
            "ai_chat_messages": {"investigation_id": "INTEGER"},
            "ai_observations": {"investigation_id": "INTEGER"},
            "timeline_preview_cache": {"frame_times": "TEXT", "frames_path": "TEXT"},
            "local_playback_snapshots": {
                "duration": "REAL DEFAULT 0", "width": "INTEGER DEFAULT 0", "height": "INTEGER DEFAULT 0",
                "codec": "TEXT", "fps": "REAL DEFAULT 0", "frame_count": "INTEGER DEFAULT 36",
                "frames_path": "TEXT", "representative_path": "TEXT",
            },
            "scene_markers": {"source": "TEXT NOT NULL DEFAULT 'local'", "provider": "TEXT", "provider_marker_id": "TEXT"},
            "photo_library_items": {"local_path": "TEXT NOT NULL DEFAULT ''", "local_sha256": "TEXT NOT NULL DEFAULT ''", "cached_at": "TEXT"},
        }
        self.conn.execute("""CREATE TABLE IF NOT EXISTS transfer_locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identity_key TEXT,
            source_channel TEXT NOT NULL,
            source_message_id INTEGER NOT NULL,
            destination_channel TEXT NOT NULL,
            destination_message_id INTEGER NOT NULL,
            destination_thumbnail_id INTEGER,
            action TEXT NOT NULL DEFAULT 'TRANSFER',
            status TEXT NOT NULL DEFAULT 'VERIFIED',
            transferred_at TEXT NOT NULL,
            UNIQUE(destination_channel, destination_message_id)
        )""")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_locations_identity ON transfer_locations(identity_key)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_locations_source ON transfer_locations(source_channel, source_message_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_transfer_locations_destination ON transfer_locations(destination_channel, destination_message_id)")

        # Operator mapping state is deliberately separate from provider
        # relationships. A lock prevents a later metadata sync from silently
        # undoing a manual correction.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS metadata_mapping_locks (
            video_id INTEGER NOT NULL, field TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 1,
            reason TEXT, updated_at TEXT NOT NULL, PRIMARY KEY(video_id, field)
        )""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS metadata_mapping_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, video_id INTEGER NOT NULL, field TEXT NOT NULL,
            old_value TEXT, new_value TEXT, action TEXT NOT NULL, created_at TEXT NOT NULL
        )""")
        for table, cols in wanted.items():
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col, typ in cols.items():
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_scene_markers_provider ON scene_markers(media_id, provider, provider_marker_id)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_media_video_phash ON media(video_phash)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_photo_library_cached ON photo_library_items(cached_at, channel_id, message_id)")

    def _migrate_entity_identity(self):
        """Backfill canonical entity keys and safely merge exact formatting duplicates."""
        from ..engines.entity_identity import canonical_key, normalized_name
        with self.lock:
            self.conn.execute("""CREATE TABLE IF NOT EXISTS entity_merge_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                survivor_id INTEGER NOT NULL,
                merged_id INTEGER NOT NULL,
                survivor_name TEXT,
                merged_name TEXT,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            for table in ("performers", "studios"):
                rows = self.conn.execute(f"SELECT id,name FROM {table}").fetchall()
                for row in rows:
                    key = canonical_key(row["name"])
                    norm = normalized_name(row["name"])
                    aliases = json.dumps([])
                    self.conn.execute(
                        f"UPDATE {table} SET canonical_key=COALESCE(NULLIF(canonical_key,''),?), normalized_name=COALESCE(NULLIF(normalized_name,''),?), aliases=COALESCE(aliases,?) WHERE id=?",
                        (key, norm, aliases, row["id"]),
                    )

                groups = self.conn.execute(
                    f"SELECT canonical_key, COUNT(*) c FROM {table} WHERE canonical_key<>'' GROUP BY canonical_key HAVING COUNT(*)>1"
                ).fetchall()
                for group in groups:
                    key = group["canonical_key"]
                    duplicates = self.conn.execute(
                        f"SELECT * FROM {table} WHERE canonical_key=? ORDER BY id", (key,)
                    ).fetchall()
                    if len(duplicates) < 2:
                        continue
                    # Prefer the record with provider IDs, then the one with
                    # more relationships, then the earliest ID.
                    scored = []
                    rel_table = "video_performers" if table == "performers" else "video_studios"
                    rel_col = "performer_id" if table == "performers" else "studio_id"
                    for row in duplicates:
                        rels = self.conn.execute(
                            f"SELECT COUNT(*) c FROM {rel_table} WHERE {rel_col}=?", (row["id"],)
                        ).fetchone()["c"]
                        provider_count = len(_json_map(row["provider_ids"]))
                        scored.append((provider_count, rels, -int(row["id"]), row))
                    survivor = max(scored, key=lambda x: x[:3])[3]
                    sid = int(survivor["id"])
                    for row in duplicates:
                        mid = int(row["id"])
                        if mid == sid:
                            continue
                        if table == "performers":
                            self.conn.execute(
                                "INSERT OR IGNORE INTO video_performers(video_id,performer_id,confidence) SELECT video_id,?,confidence FROM video_performers WHERE performer_id=?",
                                (sid, mid),
                            )
                            self.conn.execute(
                                "INSERT OR IGNORE INTO local_video_performers(video_id,performer_id,created_at) SELECT video_id,?,created_at FROM local_video_performers WHERE performer_id=?",
                                (sid, mid),
                            )
                            self.conn.execute(
                                "UPDATE metadata_suggestions SET suggested_performer_id=? WHERE suggested_performer_id=?",
                                (sid, mid),
                            )
                        else:
                            self.conn.execute(
                                "INSERT OR IGNORE INTO video_studios(video_id,studio_id,confidence) SELECT video_id,?,confidence FROM video_studios WHERE studio_id=?",
                                (sid, mid),
                            )
                        merged_provider = _merge_provider_maps(survivor["provider_ids"], row["provider_ids"])
                        merged_aliases = _merge_alias_list(survivor["aliases"], row["name"])
                        survivor_locked_image = bool(survivor.get("image_locked", 0))
                        survivor_locked_name = bool(survivor.get("name_locked", 0))
                        survivor_locked_profile = bool(survivor.get("profile_locked", 0)) if table == "performers" else False
                        fields = ["provider_ids=?", "aliases=?"]
                        vals = [merged_provider, merged_aliases]
                        if not survivor_locked_image:
                            fields.append("image=COALESCE(NULLIF(image,''),?)")
                            vals.append(row["image"])
                        if table == "performers" and not survivor_locked_profile:
                            fields.append("profile=COALESCE(NULLIF(profile,''),?)")
                            vals.append(row["profile"])
                        vals.append(sid)
                        self.conn.execute(f"UPDATE {table} SET {', '.join(fields)} WHERE id=?", vals)
                        self.conn.execute(
                            "INSERT INTO entity_merge_audit(entity_type,canonical_key,survivor_id,merged_id,survivor_name,merged_name,reason,created_at) VALUES(?,?,?,?,?,?,?,?)",
                            (table, key, sid, mid, survivor["name"], row["name"], "same canonical key after formatting normalization", config.now_iso()),
                        )
                        self.conn.execute(f"DELETE FROM {table} WHERE id=?", (mid,))
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_performers_canonical_key ON performers(canonical_key) WHERE canonical_key<>''")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_studios_canonical_key ON studios(canonical_key) WHERE canonical_key<>''")
            self.conn.commit()

    def _repair_legacy_catalogue_ids(self):
        """Normalize old positive channel IDs and remove legacy fake-image rows.

        Early scanner builds used channel_entity.id (positive raw ID) while the
        web/library uses Telethon dialog IDs (negative -100... IDs). This caused
        every selected channel to appear uncatalogued. Earlier scans also could
        misclassify JPG thumbnail documents as videos ending in .jpg.mp4. Repair
        those records once at startup so the existing archive can be used without
        deleting data or forcing a full re-import.
        """
        # Run the richer upload/cache repair before the legacy normalizer below
        # rewrites media and thumbnail_cache IDs. Otherwise preview manifests and
        # their frame directories can be stranded under the old positive ID.
        try:
            from ..engines.delivery_ledger import repair_legacy_channel_peer_ids
            repair_legacy_channel_peer_ids(self)
        except Exception as exc:
            try:
                self.log(
                    f"Early channel peer/preview repair deferred: {type(exc).__name__}: {exc}",
                    "WARN",
                )
            except Exception:
                pass
        # Use the full collision-safe identity migration rather than blanket
        # UPDATEs. A previous partial repair can leave a SOURCE_DELETED legacy
        # media row beside its canonical row; rewriting both keys in one UPDATE
        # violates media's UNIQUE(source_channel, source_message_id) constraint
        # and prevented the application from starting. The migration merges the
        # duplicate into the canonical record, keeps canonical assets authoritative,
        # and merges per-channel high-water marks before removing the raw key.
        try:
            from .telegram_identity_migration import rekey_legacy_channel_rows
            with self.lock:
                channel_rows = self.conn.execute(
                    "SELECT DISTINCT telegram_id FROM channels WHERE COALESCE(telegram_id,'')<>''"
                ).fetchall()
            canonical_ids = sorted({str(row["telegram_id"] or "").strip() for row in channel_rows})
            preview_cache_root = config.PLAYER_CACHE_DIR / "preview_frames"
            # v29.4.390: this repair walked every table (and two whole path tables in
            # Python) once per channel on EVERY start: seconds to minutes for large
            # libraries with many channels. Run it when the channel set changed since
            # the last clean pass, or when legacy raw-ID videos are present (an
            # indexed check); otherwise skip.
            import hashlib as _hashlib
            _raw_ids = [c[4:] for c in canonical_ids if c.startswith("-100") and c[4:].isdigit()]
            _marker_key = "legacy_channel_rekey_clean_v1"
            _marker = _hashlib.sha256(",".join(canonical_ids).encode()).hexdigest()
            _legacy_present = False
            if _raw_ids:
                with self.lock:
                    _ph = ",".join("?" * len(_raw_ids))
                    _legacy_present = self.conn.execute(
                        f"SELECT 1 FROM media WHERE source_channel IN ({_ph}) LIMIT 1", _raw_ids
                    ).fetchone() is not None
            if not _raw_ids or (not _legacy_present and str(self.setting(_marker_key) or "") == _marker):
                canonical_ids = []
            _rekey_clean = True
            for canonical in canonical_ids:
                if not canonical.startswith("-100") or not canonical[4:].isdigit():
                    continue
                legacy = canonical[4:]
                try:
                    stats = rekey_legacy_channel_rows(
                        self, legacy, canonical, cache_roots=[preview_cache_root]
                    )
                    changed = sum(int(value or 0) for key, value in stats.items() if key != "cache_files_shadowed")
                    if changed:
                        self.log(
                            f"Startup channel-ID repair {legacy} -> {canonical}: "
                            f"media_rekeyed={stats.get('media_rekeyed', 0)}, "
                            f"media_merged={stats.get('media_merged', 0)}, "
                            f"scan_checkpoints_merged={stats.get('scan_checkpoints_merged', 0)}, "
                            f"preview_duplicates_removed={stats.get('timeline_preview_cache_duplicates_removed', 0)}",
                            "INFO",
                        )
                except Exception as exc:
                    _rekey_clean = False
                    # A single malformed legacy record must never prevent startup.
                    # The migration uses a savepoint, so this channel's DB changes
                    # roll back cleanly and can be retried on the next launch.
                    self.log(
                        f"Startup channel-ID repair deferred for {legacy} -> {canonical}: "
                        f"{type(exc).__name__}: {exc}",
                        "WARN",
                    )
            if canonical_ids and _rekey_clean:
                self.set_setting(_marker_key, _marker)
        except Exception as exc:
            self.log(
                f"Startup channel-ID repair could not initialize: {type(exc).__name__}: {exc}",
                "WARN",
            )

        with self.lock:
            bad = self.conn.execute(
                "SELECT id,source_channel,source_message_id FROM media "
                "WHERE lower(filename) LIKE '%.jpg.mp4' OR lower(filename) LIKE '%.jpeg.mp4' "
                "OR lower(filename) LIKE '%.png.mp4' OR lower(filename) LIKE '%.webp.mp4'"
            ).fetchall()
            bad_ids = [int(row["id"]) for row in bad]
            for start in range(0, len(bad_ids), 500):
                chunk = bad_ids[start:start + 500]
                ph = ",".join("?" for _ in chunk)
                self.conn.execute(f"DELETE FROM video_performers WHERE video_id IN ({ph})", chunk)
                self.conn.execute(f"DELETE FROM video_studios WHERE video_id IN ({ph})", chunk)
                self.conn.execute(f"DELETE FROM video_tags WHERE video_id IN ({ph})", chunk)
                self.conn.execute(f"DELETE FROM media WHERE id IN ({ph})", chunk)
            self.conn.commit()

    _PERSISTENT_METADATA_KEYS = ("tpdb_api_token", "tpdb_token", "stashdb_api_key")


    # ------------------------------------------------------------------
    # SUBSECTION: Persistent metadata and secret settings
    # Metadata key persistence, asset-store hydration and secret migration.
    # ------------------------------------------------------------------
    def _load_persistent_metadata_keys(self):
        """Migrate legacy provider keys to the same encrypted credential vault."""
        from .encrypted_files import read_json, write_json
        path = config.METADATA_KEYS_PATH
        # v18.9: the Claude/Anthropic assistant was removed; drop its key.
        try:
            self.conn.execute("DELETE FROM settings WHERE key='anthropic_api_key'")
            self.conn.commit()
        except Exception as exc:
            self.log(
                f"Could not remove retired Anthropic metadata key: {type(exc).__name__}: {exc}",
                "WARN",
            )
        payload = read_json(path, "archive-x-provider-keys-v1")
        with self.lock:
            for key in self._PERSISTENT_METADATA_KEYS:
                if config.get_secret(key) not in (None, ""):
                    continue
                row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                value = str(payload.get(key) or "").strip()
                if row:
                    try:
                        existing = json.loads(row["value"])
                    except (ValueError, TypeError):
                        existing = row["value"]
                    if existing not in (None, ""):
                        value = str(existing)
                if value:
                    config.set_secret(key, value)
        # Do not leave stale provider values that could resurrect a deleted key.
        # The database's normal secret migration removes legacy runtime rows.
        if path.exists():
            write_json(path, {}, "archive-x-provider-keys-v1")

    def _backfill_identification_state(self):
        """One-time-per-row classification of existing media into the
        UNIDENTIFIED/PARTIAL/MATCHED/VERIFIED/REJECTED states (section 12).
        Idempotent: only ever touches rows where the column is still NULL,
        so it never overwrites a state set later by matching/suggestions."""
        with self.lock:
            self.conn.execute(
                "UPDATE media SET identification_state='VERIFIED' "
                "WHERE identification_state IS NULL AND metadata_source IS NOT NULL "
                "AND metadata_source NOT IN ('', 'none', 'suggested') "
                "AND id IN (SELECT video_id FROM metadata_suggestions WHERE status='CONFIRMED')"
            )
            self.conn.execute(
                "UPDATE media SET identification_state='MATCHED' "
                "WHERE identification_state IS NULL AND metadata_source IS NOT NULL "
                "AND metadata_source NOT IN ('', 'none', 'suggested')"
            )
            self.conn.execute(
                "UPDATE media SET identification_state='PARTIAL' "
                "WHERE identification_state IS NULL AND metadata_source='suggested'"
            )
            self.conn.execute(
                "UPDATE media SET identification_state='UNIDENTIFIED' WHERE identification_state IS NULL"
            )
            self.conn.commit()

    def _persist_metadata_key(self, key: str, value: Any):
        if key in self._PERSISTENT_METADATA_KEYS:
            config.set_secret(key, value)

    def hydrate_persistent_asset_store(self) -> dict:
        """Export existing DB-backed thumbnails and metadata into stable user folders.

        This runs once per archive generation after an upgrade. New thumbnails,
        entity artwork and scene references are written to the same folders by
        their respective services, so the filesystem tree remains useful as a
        human-readable backup independent from the versioned application files.
        """
        marker_key = "persistent_asset_store_v1"
        try:
            if self.setting(marker_key):
                return {"skipped": True}
        except Exception as exc:
            self.log(
                f"Persistent asset-store marker could not be read: {type(exc).__name__}: {exc}",
                "ERROR",
            )
            raise

        import hashlib
        from pathlib import Path
        counts = {"thumbnails": 0, "performers": 0, "studios": 0, "scenes": 0, "media": 0}

        def atomic_bytes(path: Path, data: bytes):
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)

        def suffix(mime):
            return {"image/png": ".png", "image/webp": ".webp", "image/jpeg": ".jpg"}.get(str(mime or "").lower(), ".jpg")

        with self.reading() as rdb:
            thumbs = rdb.execute(
                "SELECT source_channel,video_message_id,thumbnail_message_id,mime,data FROM thumbnail_cache "
                "WHERE data IS NOT NULL AND source='playback-snapshot'"
            ).fetchall()
            performer_images = rdb.execute("SELECT * FROM performer_image_cache WHERE data IS NOT NULL").fetchall()
            studio_images = rdb.execute("SELECT * FROM studio_image_cache WHERE data IS NOT NULL").fetchall()
            performers = rdb.execute("SELECT * FROM performers ORDER BY id").fetchall()
            studios = rdb.execute("SELECT * FROM studios ORDER BY id").fetchall()
            refs = rdb.execute("SELECT * FROM reference_keys ORDER BY canonical_key").fetchall()
            media_rows = rdb.execute("SELECT * FROM media ORDER BY id").fetchall()

        # Pre-index lookups to avoid per-row queries
        performer_by_id = {int(p["id"]): p for p in performers}
        studio_by_id = {int(s["id"]): s for s in studios}

        # Build media lookup by (channel, message_id) for thumbnail resolution
        with self.reading() as rdb:
            media_identity = rdb.execute(
                "SELECT source_channel,source_message_id,identity_key,title,filename,thumbnail_message_id FROM media "
                "WHERE TRIM(COALESCE(identity_key,''))<>''"
            ).fetchall()
        media_by_channel_msg = {}
        for m in media_identity:
            media_by_channel_msg[(str(m["source_channel"]), int(m["source_message_id"]))] = m

        for row in thumbs:
            try:
                data = bytes(row["data"])
                ext = suffix(row["mime"])
                atomic_bytes(config.THUMBNAIL_DIR / str(row["source_channel"]) / f"{int(row['video_message_id'])}{ext}", data)
                m = media_by_channel_msg.get((str(row["source_channel"]), int(row["video_message_id"])))
                if m and m["identity_key"]:
                    identity = str(m["identity_key"])
                    scene_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
                    scene_dir = config.SCENE_ASSET_DIR / scene_id
                    scene_file = scene_dir / f"thumbnail{ext}"
                    atomic_bytes(scene_file, data)
                    mapping = {
                        "identity_key": identity,
                        "source_channel": str(row["source_channel"]),
                        "source_message_id": int(row["video_message_id"]),
                        "thumbnail_message_id": int(row["thumbnail_message_id"] or 0),
                        "title": m["title"], "filename": m["filename"],
                        "thumbnail_file": str(scene_file.relative_to(config.USER_DATA_DIR)),
                    }
                    path = scene_dir / "thumbnail_mapping.json"
                    path.write_text(json.dumps(mapping, indent=2, ensure_ascii=False), encoding="utf-8")
                counts["thumbnails"] += 1
            except Exception as exc:
                self.log(
                    f"Persistent thumbnail export failed for {row['source_channel']}/{row['video_message_id']}: "
                    f"{type(exc).__name__}: {exc}",
                    "WARN",
                )
                continue

        for row in performer_images:
            try:
                ext = suffix(row["mime"])
                asset = config.PERFORMER_ASSET_DIR / str(int(row["performer_id"])) / f"image{ext}"
                atomic_bytes(asset, bytes(row["data"]))
                entity = performer_by_id.get(int(row["performer_id"]))
                if entity:
                    payload = {k: entity[k] for k in entity.keys()}
                    payload["image_file"] = str(asset.relative_to(config.USER_DATA_DIR))
                    (config.PERFORMER_METADATA_DIR / f"{int(row['performer_id'])}.json").write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                counts["performers"] += 1
            except Exception as exc:
                self.log(
                    f"Persistent performer asset export failed for {row['performer_id']}: "
                    f"{type(exc).__name__}: {exc}",
                    "WARN",
                )
                continue

        for row in studio_images:
            try:
                ext = suffix(row["mime"])
                asset = config.STUDIO_ASSET_DIR / str(int(row["studio_id"])) / f"image{ext}"
                atomic_bytes(asset, bytes(row["data"]))
                entity = studio_by_id.get(int(row["studio_id"]))
                if entity:
                    payload = {k: entity[k] for k in entity.keys()}
                    payload["image_file"] = str(asset.relative_to(config.USER_DATA_DIR))
                    (config.STUDIO_METADATA_DIR / f"{int(row['studio_id'])}.json").write_text(
                        json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                counts["studios"] += 1
            except Exception as exc:
                self.log(
                    f"Persistent studio asset export failed for {row['studio_id']}: "
                    f"{type(exc).__name__}: {exc}",
                    "WARN",
                )
                continue

        for row in refs:
            try:
                key = str(row["canonical_key"] or "")
                if not key:
                    continue
                scene_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
                try:
                    performers_list = json.loads(row["performer_names"] or "[]")
                except Exception:
                    performers_list = []
                payload = {
                    "canonical_key": key, "title": row["title"],
                    "performers": performers_list, "studio": row["studio_name"],
                    "site": row["site_name"], "release_date": row["release_date"],
                    "provider": row["provider"], "provider_record_id": row["provider_record_id"],
                    "provider_url": row["provider_url"], "metadata_hash": row["metadata_hash"],
                    "updated_at": row["updated_at"],
                }
                (config.SCENE_METADATA_DIR / f"{scene_id}.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                if row["site_name"]:
                    site_id = hashlib.sha256(str(row["site_name"]).strip().casefold().encode("utf-8")).hexdigest()[:20]
                    (config.SITE_METADATA_DIR / f"{site_id}.json").write_text(
                        json.dumps({"name": row["site_name"], "provider": row["provider"],
                                    "provider_url": row["provider_url"], "updated_at": row["updated_at"]},
                                   indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                counts["scenes"] += 1
            except Exception as exc:
                self.log(
                    f"Persistent scene reference export failed for {row['canonical_key']}: "
                    f"{type(exc).__name__}: {exc}",
                    "WARN",
                )
                continue

        # Batch-fetch all video-performer/studio/tag associations instead of per-row queries
        media_ids = [int(r["id"]) for r in media_rows]
        vp_map = {}  # video_id -> [performer_dict, ...]
        vs_map = {}  # video_id -> [studio_dict, ...]
        vt_map = {}  # video_id -> [tag_dict, ...]
        if media_ids:
            with self.lock:
                for start in range(0, len(media_ids), 500):
                    chunk = media_ids[start:start + 500]
                    ph = ",".join("?" for _ in chunk)
                    for vp_row in self.conn.execute(
                        f"SELECT vp.video_id, p.* FROM performers p "
                        f"JOIN video_performers vp ON vp.performer_id=p.id "
                        f"WHERE vp.video_id IN ({ph}) ORDER BY p.name", chunk
                    ).fetchall():
                        vp_map.setdefault(int(vp_row["video_id"]), []).append(
                            {k: vp_row[k] for k in vp_row.keys() if k != "video_id"})
                    for vs_row in self.conn.execute(
                        f"SELECT vs.video_id, s.* FROM studios s "
                        f"JOIN video_studios vs ON vs.studio_id=s.id "
                        f"WHERE vs.video_id IN ({ph}) ORDER BY s.name", chunk
                    ).fetchall():
                        vs_map.setdefault(int(vs_row["video_id"]), []).append(
                            {k: vs_row[k] for k in vs_row.keys() if k != "video_id"})
                    for vt_row in self.conn.execute(
                        f"SELECT vt.video_id, t.* FROM tags t "
                        f"JOIN video_tags vt ON vt.tag_id=t.id "
                        f"WHERE vt.video_id IN ({ph}) ORDER BY t.name", chunk
                    ).fetchall():
                        vt_map.setdefault(int(vt_row["video_id"]), []).append(
                            {k: vt_row[k] for k in vt_row.keys() if k != "video_id"})

        for row in media_rows:
            try:
                mid = int(row["id"])
                payload = {k: row[k] for k in row.keys()}
                payload["performers"] = vp_map.get(mid, [])
                payload["studios"] = vs_map.get(mid, [])
                payload["tags"] = vt_map.get(mid, [])
                if row["identity_key"]:
                    scene_id = hashlib.sha256(str(row["identity_key"]).encode("utf-8")).hexdigest()[:20]
                    payload["scene_metadata_file"] = str((config.SCENE_METADATA_DIR / f"{scene_id}.json").relative_to(config.USER_DATA_DIR))
                (config.MEDIA_METADATA_DIR / f"{mid}.json").write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
                counts["media"] += 1
            except Exception as exc:
                self.log(
                    f"Persistent media metadata export failed for {row['id']}: "
                    f"{type(exc).__name__}: {exc}",
                    "WARN",
                )
                continue

        manifest = {
            "version": 1,
            "description": "Persistent Archive X user data and metadata layout",
            "generated_at": config.now_iso(),
            "database": str(config.DB_PATH.relative_to(config.USER_DATA_DIR)),
            "auth": str(config.AUTH_DIR.relative_to(config.USER_DATA_DIR)),
            "thumbnail_folder": str(config.THUMBNAIL_DIR.relative_to(config.USER_DATA_DIR)),
            "scene_folder": str(config.SCENE_ASSET_DIR.relative_to(config.USER_DATA_DIR)),
            "metadata_folder": str(config.METADATA_DIR.relative_to(config.USER_DATA_DIR)),
            "assets_folder": str(config.ASSET_DIR.relative_to(config.USER_DATA_DIR)),
            "ai_evidence_folder": str(config.AI_EVIDENCE_DIR.relative_to(config.USER_DATA_DIR)),
            "cache_folder": str(config.CACHE_DIR.relative_to(config.USER_DATA_DIR)),
            "automation_folder": str(config.AUTOMATION_DIR.relative_to(config.USER_DATA_DIR)),
            "state_folder": str(config.STATE_DIR.relative_to(config.USER_DATA_DIR)),
            "media_manager_state_folder": str(config.MEDIA_MANAGER_STATE_DIR.relative_to(config.USER_DATA_DIR)),
            "runtime_state_folder": str(config.RUNTIME_STATE_DIR.relative_to(config.USER_DATA_DIR)),
            "logs_folder": str(config.LOG_DIR.relative_to(config.USER_DATA_DIR)),
            "settings_file": str(config.CONFIG_PATH.relative_to(config.USER_DATA_DIR)),
            "counts": counts,
        }
        (config.USER_DATA_DIR / "PERSISTENT_DATA_MANIFEST.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        self.set_setting(marker_key, config.now_iso())
        return counts

    _LEGACY_SECRET_KEYS = tuple(sorted(config.SECRET_KEYS))

    def _migrate_secret_settings(self):
        """Move sensitive settings out of SQLite into auth/credentials.json.

        Older releases stored Telegram/API/login credentials in the database.
        Read those values once, write them to the persistent auth store, then
        remove the database copies. Non-secret catalogue/application state stays
        in SQLite.
        """
        with self.lock:
            rows = self.conn.execute(
                "SELECT key,value FROM settings WHERE key IN (%s)" % ",".join("?" * len(self._LEGACY_SECRET_KEYS)),
                self._LEGACY_SECRET_KEYS,
            ).fetchall() if self._LEGACY_SECRET_KEYS else []
        if not rows:
            return
        for row in rows:
            key = str(row["key"])
            try:
                value = json.loads(row["value"])
            except Exception:
                value = row["value"]
            if value not in (None, "") and config.get_secret(key) in (None, ""):
                config.set_secret(key, value)
        with self.lock:
            self.conn.executemany("DELETE FROM settings WHERE key=?", [(str(r["key"]),) for r in rows])
            self.conn.commit()


    # ------------------------------------------------------------------
    # SUBSECTION: Settings
    # Key/value settings read and write.
    # ------------------------------------------------------------------
    def setting(self, key: str, default: Any = None) -> Any:
        if config.is_secret_key(key):
            secret = config.get_secret(key, None)
            if secret not in (None, ""):
                return secret
            # One-time fallback for a database created by an older process that
            # initialized before the migration hook ran.
            with self.lock:
                row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            if row:
                try:
                    value = json.loads(row["value"])
                except Exception:
                    value = row["value"]
                if value not in (None, ""):
                    config.set_secret(key, value)
                    with self.lock:
                        self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
                        self.conn.commit()
                    return value
            return default

        with self.lock:
            row = self.conn.execute(
                "SELECT value FROM settings WHERE key=?", (key,)
            ).fetchone()
        if not row:
            if key in self._PERSISTENT_METADATA_KEYS:
                self._load_persistent_metadata_keys()
                with self.lock:
                    row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
                if not row:
                    return default
            else:
                return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

    def set_setting(self, key: str, value: Any):
        if config.is_secret_key(key):
            config.set_secret(key, value)
            with self.lock:
                self.conn.execute("DELETE FROM settings WHERE key=?", (key,))
                self.conn.commit()
            return
        with self.lock:
            self.conn.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
            self.conn.commit()
        if key in self._PERSISTENT_METADATA_KEYS and value:
            self._persist_metadata_key(key, value)


    # ------------------------------------------------------------------
    # SUBSECTION: AI assistant persistence
    # Investigations, chat messages, observations and evidence history.
    # ------------------------------------------------------------------
    def ai_create_investigation(self, title: str, stage: str = "OBSERVE", status: str = "ACTIVE") -> int:
        return self.ai_persistence.create_investigation(title, stage=stage, status=status)

    def ai_list_investigations(self, limit: int = 50) -> list[dict]:
        return self.ai_persistence.list_investigations(limit=limit)

    def ai_get_investigation(self, investigation_id: int | None) -> dict | None:
        return self.ai_persistence.get_investigation(investigation_id)

    def ai_active_investigation(self) -> dict:
        return self.ai_persistence.active_investigation()

    def ai_set_active_investigation(self, investigation_id: int) -> dict:
        return self.ai_persistence.set_active_investigation(investigation_id)

    def ai_update_investigation(self, investigation_id: int, *, title: str | None = None, status: str | None = None, stage: str | None = None, summary: str | None = None, local_session_id: str | None = None) -> dict:
        return self.ai_persistence.update_investigation(
            investigation_id,
            title=title,
            status=status,
            stage=stage,
            summary=summary,
            local_session_id=local_session_id,
        )

    def ai_add_chat_message(self, conversation_id: str, role: str, content: str, response_id: str = "", investigation_id: int | None = None) -> int:
        return self.ai_persistence.add_chat_message(
            conversation_id,
            role,
            content,
            response_id=response_id,
            investigation_id=investigation_id,
        )

    def ai_list_chat_messages(self, limit: int = 120, investigation_id: int | None = None) -> list[dict]:
        return self.ai_persistence.list_chat_messages(limit=limit, investigation_id=investigation_id)

    def ai_clear_chat_messages(self, investigation_id: int | None = None):
        return self.ai_persistence.clear_chat_messages(investigation_id=investigation_id)

    def ai_add_observation(self, kind: str, content: str, investigation_id: int | None = None) -> int:
        return self.ai_persistence.add_observation(kind, content, investigation_id=investigation_id)

    def ai_list_observations(self, limit: int = 60, investigation_id: int | None = None) -> list[dict]:
        return self.ai_persistence.list_observations(limit=limit, investigation_id=investigation_id)

    def ai_record_evidence_history(self, *, evidence_id: str, investigation_id: int | None, kind: str, reason: str, scope: str, source_page: str, source: str, status: str, sha256: str, summary: dict | None = None) -> None:
        return self.ai_persistence.record_evidence_history(
            evidence_id=evidence_id,
            investigation_id=investigation_id,
            kind=kind,
            reason=reason,
            scope=scope,
            source_page=source_page,
            source=source,
            status=status,
            sha256=sha256,
            summary=summary,
        )

    def ai_list_evidence_history(self, investigation_id: int | None = None, limit: int = 50) -> list[dict]:
        return self.ai_persistence.list_evidence_history(
            investigation_id=investigation_id,
            limit=limit,
        )


    # ------------------------------------------------------------------
    # SUBSECTION: Event log and buffered writes
    # Buffered event writes, flush scheduling and recent event queries.
    # ------------------------------------------------------------------
    def _begin_immediate_locked(self):
        """Start an atomic write transaction on the shared connection.

        V29.4.126: Database.log() batches events in an open implicit
        transaction, and other writers may leave one open too. Starting
        BEGIN IMMEDIATE on top of that raised "cannot start a transaction
        within a transaction" (seen failing Metadata & Assets phases). Any
        pending work is flushed and committed first. Caller holds self.lock.
        """
        if self.conn.in_transaction:
            self.conn.commit()
        try:
            self._flush_event_buffer_locked()
        except Exception:
            pass
        self.conn.execute("BEGIN IMMEDIATE")

    # Event log buffering (v29.4.386).
    # Log rows are held in memory and written in their own short transaction only
    # when no other transaction is open on the shared connection. Previously they
    # were INSERTed into whatever transaction was open, so (a) a caller's rollback
    # silently discarded recent log lines and (b) a log() call in the middle of a
    # caller's multi-statement write could COMMIT that write half-done.
    _EVENT_BUFFER_MAX = 5000

    def _write_buffered_events_locked(self) -> None:
        with self._event_buf_lock:
            batch, self._event_buf = self._event_buf, []
        if not batch:
            return
        try:
            self.conn.executemany(
                "INSERT INTO events(level,message,created_at) VALUES(?,?,?)",
                [(lvl, msg, ts) for lvl, msg, ts, _act in batch],
            )
            activity = [item[3] for item in batch if item[3]]
            if activity:
                self.conn.executemany(
                    "INSERT INTO job_activity_events(job_id,level,event,operation,message,details,created_at) "
                    "VALUES(?,?,?,?,?,?,?)", activity,
                )
                self._job_activity_prune_counter = getattr(self, "_job_activity_prune_counter", 0) + len(activity)
                if self._job_activity_prune_counter >= 500:
                    self._job_activity_prune_counter = 0
                    self.conn.execute(
                        "DELETE FROM job_activity_events WHERE id < "
                        "(SELECT COALESCE(MAX(id),0)-50000 FROM job_activity_events)"
                    )
            self.conn.commit()
        except Exception:
            # Only our own inserts are in this transaction (none was open before).
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._event_pending = 0
            self._event_last_commit = time.monotonic()

    def _flush_event_buffer_locked(self):
        """Write buffered log rows if that cannot disturb another transaction.

        Caller holds self.lock. If a transaction is open on this thread it belongs
        to the caller (the lock is re-entrant); leave the rows buffered and let the
        timer write them once that transaction has finished.
        """
        if not self._event_buf:
            return
        if self.conn.in_transaction:
            self._schedule_event_flush()
            return
        self._write_buffered_events_locked()

    def _schedule_event_flush(self):
        """Write buffered log rows shortly even if no further event arrives.

        The timer runs only when no other thread holds self.lock, so a transaction
        still open at that point was abandoned by its caller: commit it (keeps the
        V29.4.131 behaviour that stops other connections seeing "database is
        locked"), then write the buffered rows.
        """
        if getattr(self, "_event_flush_timer", None) is not None:
            return
        def flush():
            with self.lock:
                self._event_flush_timer = None
                try:
                    if self.conn.in_transaction:
                        self.conn.commit()
                    self._write_buffered_events_locked()
                except Exception:
                    pass
        timer = threading.Timer(self._event_commit_interval, flush)
        timer.daemon = True
        self._event_flush_timer = timer
        timer.start()

    def log(self, message: str, level: str = "INFO", *, event: str = "application",
            job_id: str | None = None, operation: str | None = None,
            details: dict | None = None, error: BaseException | None = None):
        """Record an application event in the buffered event log and diagnostics."""
        # Low-priority UI/event history is buffered in small batches. Explicit
        # state changes and recent_events() flush the pending rows, so the user
        # still sees current activity while high-frequency progress logs avoid
        # one SQLite transaction per event.
        activity_job_id = str(job_id or "").strip()
        if not activity_job_id:
            try:
                from .observability import context_snapshot
                activity_job_id = str(context_snapshot().get("job_id") or "").strip()
            except Exception:
                activity_job_id = ""
        timestamp = config.now_iso()
        activity = None
        if activity_job_id and activity_job_id not in {"-", "None", "0"}:
            try:
                from .diagnostics import _safe
                safe_details = _safe(details or {})
            except Exception:
                safe_details = details or {}
            activity = (activity_job_id, str(level or "INFO").upper(), str(event or "application"),
                        str(operation or ""), str(message or ""),
                        json.dumps(safe_details, ensure_ascii=False, separators=(",", ":"), default=str), timestamp)
        with self._event_buf_lock:
            if len(self._event_buf) >= self._EVENT_BUFFER_MAX:
                del self._event_buf[: len(self._event_buf) - self._EVENT_BUFFER_MAX + 1]
            self._event_buf.append((level, message, timestamp, activity))
            self._event_pending = pending = len(self._event_buf)
        now = time.monotonic()
        if pending >= self._event_commit_batch or (now - self._event_last_commit) >= self._event_commit_interval:
            # Never wait for the writer lock just to log: if another thread is busy,
            # the timer writes the rows once it is free.
            if self.lock.acquire(blocking=False):
                try:
                    self._flush_event_buffer_locked()
                except Exception:
                    pass
                finally:
                    self.lock.release()
            else:
                self._schedule_event_flush()
        else:
            self._schedule_event_flush()
        try:
            from .diagnostics import record_event
            record_event(event=event, level=level, message=message, job_id=job_id or activity_job_id,
                         operation=operation, details=details, error=error)
        except Exception:
            pass

    def log_exception(self, message: str, exc: BaseException, *, level: str = "ERROR",
                      event: str = "exception", job_id: str | None = None,
                      operation: str | None = None, details: dict | None = None):
        self.log(message, level, event=event, job_id=job_id, operation=operation,
                 details=details, error=exc)

    def flush_event_buffer(self) -> None:
        """Commit buffered event rows before a caller starts an explicit transaction."""
        with self.lock:
            self._flush_event_buffer_locked()

    def recent_events(self, limit: int = 100):
        with self.lock:
            self._flush_event_buffer_locked()
            rows = self.conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def latest_job_activity_id(self, job_id) -> int:
        """Return the newest durable activity-event ID for one job in O(1) query time.

        Parent-child supervisors use this cheap watermark to detect real child
        activity without repeatedly loading whole timeline pages.
        """
        jid = str(job_id or "").strip()
        if not jid:
            return 0
        with self.lock:
            self._flush_event_buffer_locked()
            row = self.conn.execute(
                "SELECT COALESCE(MAX(id),0) AS latest_id FROM job_activity_events WHERE job_id=?",
                (jid,),
            ).fetchone()
        return int((row["latest_id"] if row else 0) or 0)

    def recent_job_activity(self, job_ids, limit: int = 200, before_id: int | None = None):
        """Return a bounded chronological timeline page, optionally before an event ID."""
        if isinstance(job_ids, (str, int)):
            ids = [str(job_ids)]
        else:
            ids = [str(x) for x in (job_ids or []) if str(x or "").strip()]
        ids = list(dict.fromkeys(x for x in ids if x not in {"", "-", "None"}))
        if not ids:
            return []
        ids = ids[:800]  # stay below SQLite's common parameter limit
        limit = max(1, min(int(limit or 200), 500))
        marks = ",".join("?" for _ in ids)
        where = f"job_id IN ({marks})"
        params: list[Any] = list(ids)
        if before_id is not None:
            try:
                where += " AND id < ?"
                params.append(max(0, int(before_id)))
            except (TypeError, ValueError):
                pass
        params.append(limit)
        with self.lock:
            self._flush_event_buffer_locked()
            rows = self.conn.execute(
                f"SELECT id,job_id,level,event,operation,message,details,created_at "
                f"FROM job_activity_events WHERE {where} ORDER BY id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        result = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["details"] = json.loads(item.get("details") or "{}")
            except Exception:
                item["details"] = {"raw": str(item.get("details") or "")[:2000]}
            result.append(item)
        return result


    # ------------------------------------------------------------------
    # SUBSECTION: Job transfer telemetry
    # Per-job transfer telemetry.
    # ------------------------------------------------------------------
    def set_job_transfer_telemetry(self, job_id, values: dict, *, min_interval: float = 1.0) -> bool:
        """Replace/merge a bounded per-job live snapshot without adding history rows."""
        jid = str(job_id or "").strip()
        if not jid or not isinstance(values, dict):
            return False
        allowed = {
            "stage", "current_item", "file_name", "direction", "bytes_done",
            "bytes_total", "rate_bytes_per_second", "eta_seconds", "transfer_state",
        }
        nullable = {"rate_bytes_per_second", "eta_seconds"}
        update = {
            key: value for key, value in values.items()
            if key in allowed and (value is not None or key in nullable)
        }
        if not update:
            return False
        now_mono = time.monotonic()
        with self.lock:
            row = self.conn.execute(
                "SELECT telemetry FROM job_transfer_telemetry WHERE job_id=?", (jid,)
            ).fetchone()
            try:
                current = json.loads(row["telemetry"] or "{}") if row else {}
                if not isinstance(current, dict):
                    current = {}
            except (TypeError, ValueError):
                current = {}
            changed_stage = "stage" in update and str(update["stage"]) != str(current.get("stage") or "")
            last_write = float(self._job_transfer_telemetry_write_times.get(jid, 0.0))
            interval = max(0.25, float(min_interval or 0))
            # A stage transition must be visible promptly, but even transitions
            # are capped to four writes per second. Item/byte samples use the
            # regular one-second cadence.
            if now_mono - last_write < interval and not (
                changed_stage and now_mono - last_write >= 0.25
            ):
                return False
            if update.get("file_name") and str(update["file_name"]) != str(current.get("file_name") or ""):
                for key in ("direction", "bytes_done", "bytes_total", "rate_bytes_per_second", "eta_seconds", "transfer_state"):
                    current.pop(key, None)
            current.update(update)
            timestamp = config.now_iso()
            self.conn.execute(
                "INSERT INTO job_transfer_telemetry(job_id,telemetry,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(job_id) DO UPDATE SET telemetry=excluded.telemetry,updated_at=excluded.updated_at",
                (jid, json.dumps(current, ensure_ascii=False, separators=(",", ":"), default=str), timestamp),
            )
            self.conn.commit()
            self._job_transfer_telemetry_write_times[jid] = now_mono
        return True

    def get_job_transfer_telemetry(self, job_ids) -> dict[str, dict]:
        """Read the latest telemetry for a bounded set of jobs in one query."""
        if isinstance(job_ids, (str, int)):
            ids = [str(job_ids)]
        else:
            ids = [str(value) for value in (job_ids or []) if str(value or "").strip()]
        ids = list(dict.fromkeys(value for value in ids if value not in {"", "-", "None"}))[:800]
        if not ids:
            return {}
        marks = ",".join("?" for _ in ids)
        with self.lock:
            rows = self.conn.execute(
                f"SELECT job_id,telemetry,updated_at FROM job_transfer_telemetry WHERE job_id IN ({marks})",
                tuple(ids),
            ).fetchall()
        result = {}
        for row in rows:
            try:
                telemetry = json.loads(row["telemetry"] or "{}")
                if not isinstance(telemetry, dict):
                    continue
            except (TypeError, ValueError):
                continue
            telemetry["updated_at"] = row["updated_at"]
            result[str(row["job_id"])] = telemetry
        return result


    # ------------------------------------------------------------------
    # SUBSECTION: Shutdown
    # Close the database.
    # ------------------------------------------------------------------
    def close(self):
        self._close_read_connections()
        with self.lock:
            timer = getattr(self, "_event_flush_timer", None)
            if timer is not None:
                timer.cancel()
                self._event_flush_timer = None
            try:
                if self.conn.in_transaction:
                    self.conn.commit()
                self._write_buffered_events_locked()
            except Exception:
                pass
            try:
                self.conn.close()
            except Exception:
                pass


    # ------------------------------------------------------------------
    # SUBSECTION: Jobs
    # Job creation, journal, transitions, checkpoints and queries.
    # ------------------------------------------------------------------
    def create_job(self, job_type, source="", destination="", destructive=False, detail="", dry_run=False, *, depends_on=None, priority=None, max_retries=3, deadline_at=None, resource_requirements=None):
        import uuid
        jid = uuid.uuid4().hex[:12]
        ts = config.now_iso()
        with self.lock:
            self._begin_immediate_locked()
            try:
                self.conn.execute(
                    """INSERT INTO jobs
                    (id,job_type,source_channel,destination_channel,status,progress,current_item,detail,dry_run,error,destructive,created_at,updated_at,admission_state,admission_reason,next_run_at,priority,attempt,admission_updated_at,execution_id,worker_id,lease_id,lease_expires_at,last_heartbeat_at,worker_state,retry_count,max_retries,failure_class,deadline_at,resource_requirements)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (jid, job_type, source, destination, "QUEUED", 0, "", detail or "", int(bool(dry_run)), "", int(destructive), ts, ts, "READY", "", None, 50 if priority is None else max(0,min(100,int(priority))), 0, ts, None, None, None, None, None, "IDLE", 0, max(0,int(max_retries)), "", deadline_at, json.dumps(resource_requirements or {}, ensure_ascii=False, separators=(",",":"))),
                )
                self._append_job_journal_locked(jid, "JOB_CREATED", status="QUEUED", detail="Job created and queued", payload={"job_type":job_type,"source":source,"destination":destination,"dry_run":bool(dry_run)})
                self.conn.commit()
            except Exception:
                self.conn.rollback(); raise
        self.add_job_dependencies(jid, depends_on)
        return jid

    JOB_STATUSES = frozenset({
        "QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED",
        "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERING",
    })
    JOB_STATUS_TRANSITIONS = {
        "QUEUED": {"QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED", "RECOVERING"},
        "RUNNING": {"RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "CANCELLED", "INTERRUPTED", "RECOVERING", "QUEUED"},
        "PAUSING": {"PAUSING", "PAUSED", "RUNNING", "QUEUED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED", "RECOVERING"},
        "PAUSED": {"PAUSED", "RUNNING", "QUEUED", "CANCEL_REQUESTED", "CANCELLED", "INTERRUPTED", "RECOVERING"},
        "CANCEL_REQUESTED": {"CANCEL_REQUESTED", "CANCELLED", "FAILED", "INTERRUPTED", "RECOVERING"},
        "INTERRUPTED": {"INTERRUPTED", "RECOVERING", "CANCELLED"},
        "RECOVERING": {"RECOVERING", "QUEUED", "RUNNING", "FAILED", "CANCELLED", "INTERRUPTED"},
        "FAILED": {"FAILED", "QUEUED"},
        "CANCELLED": {"CANCELLED", "QUEUED"},
        "COMPLETED": {"COMPLETED"},
        "COMPLETED_WITH_ERRORS": {"COMPLETED_WITH_ERRORS"},
    }

    def _append_job_journal_locked(self, jid, event_type, *, status=None, from_status=None, checkpoint=None, operation=None, detail=None, payload=None, error_type=None, retry_count=None, launch_id=None, created_at=None):
        import uuid
        ts = created_at or config.now_iso()
        row = self.conn.execute("SELECT COALESCE(MAX(sequence),0)+1 AS n FROM job_journal WHERE job_id=?", (jid,)).fetchone()
        encode = lambda value: None if value is None else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        self.conn.execute("""INSERT INTO job_journal
            (event_id,job_id,sequence,event_type,from_status,status,checkpoint,operation,detail,payload,error_type,retry_count,launch_id,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uuid.uuid4().hex,jid,int(row["n"]),str(event_type),from_status,status,encode(checkpoint),operation,detail,encode(payload),error_type,retry_count,launch_id,ts))

    def append_job_journal(self, jid, event_type, **kwargs):
        """Append a semantic event to the canonical durable job journal."""
        with self.lock:
            self._begin_immediate_locked()
            try:
                self._append_job_journal_locked(jid,event_type,**kwargs); self.conn.commit()
            except Exception:
                self.conn.rollback(); raise

    def transition_job(self, jid, status, **fields):
        """Persist a validated lifecycle transition and journal event atomically."""
        status = str(status or "").upper()
        with self.lock:
            self._begin_immediate_locked()
            try:
                row = self.conn.execute("SELECT status, job_type FROM jobs WHERE id=?", (jid,)).fetchone()
                if not row: raise ValueError(f"Job not found: {jid}")
                current = str(row["status"] or "").upper()
                if status not in self.JOB_STATUSES: raise ValueError(f"Unknown job lifecycle status: {status}")
                if status not in self.JOB_STATUS_TRANSITIONS.get(current,set()): raise ValueError(f"Invalid job transition {current} -> {status}")
                fields=dict(fields); fields["status"]=status; fields["updated_at"]=config.now_iso()
                sets=", ".join(f"{k}=?" for k in fields); vals=list(fields.values())+[jid]
                self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)
                self._append_job_journal_locked(jid,"STATE_CHANGED",from_status=current,status=status,operation=fields.get("current_operation"),detail=fields.get("detail"),payload={"job_type":row["job_type"],"changed_fields":sorted(k for k in fields if k not in {"status","updated_at"})},error_type=type(fields.get("error")).__name__ if fields.get("error") else None)
                self.conn.commit()
            except Exception:
                self.conn.rollback(); raise

    def update_job(self, jid, **fields):
        """Update a job while enforcing lifecycle transitions when status changes."""
        if "status" in fields:
            status = str(fields.pop("status") or "").upper()
            self.transition_job(jid, status, **fields)
            return
        fields["updated_at"] = config.now_iso()
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [jid]
        with self.lock:
            self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)
            self.conn.commit()

    def save_job_checkpoint(self, jid, payload=None, *, stage=None, item_id=None, item_index=None, total_items=None, current_operation=None, last_completed_operation=None, input_fingerprint=None, output_fingerprint=None, retry_count=None, update_fields=None):
        """Persist the latest restart-safe checkpoint and optional job fields atomically.

        If the supplied job fields contain a lifecycle status, that transition is
        validated and its journal entry is committed in the same SQLite transaction.
        """
        ts = config.now_iso()
        payload_text = None if payload is None else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        fields = dict(update_fields or {})
        fields.pop("updated_at", None)
        with self.lock:
            self._begin_immediate_locked()
            try:
                row = self.conn.execute("SELECT id,status,job_type FROM jobs WHERE id=?", (jid,)).fetchone()
                if not row: raise ValueError(f"Job not found: {jid}")
                current = str(row["status"] or "").upper()
                requested = fields.pop("status", None)
                if requested is not None:
                    requested = str(requested or "").upper()
                    if requested not in self.JOB_STATUSES: raise ValueError(f"Unknown job lifecycle status: {requested}")
                    if requested not in self.JOB_STATUS_TRANSITIONS.get(current,set()): raise ValueError(f"Invalid job transition {current} -> {requested}")
                    fields["status"] = requested
                if fields:
                    fields["updated_at"] = ts
                    sets=", ".join(f"{k}=?" for k in fields); vals=list(fields.values())+[jid]
                    self.conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", vals)
                if requested is not None and requested != current:
                    self._append_job_journal_locked(jid,"STATE_CHANGED",from_status=current,status=requested,operation=fields.get("current_operation"),detail=fields.get("detail"),payload={"job_type":row["job_type"],"changed_fields":sorted(k for k in fields if k not in {"status","updated_at"})},error_type=type(fields.get("error")).__name__ if fields.get("error") else None)
                self.conn.execute(
                    """INSERT INTO job_checkpoints
                       (job_id,stage,item_id,item_index,total_items,current_operation,last_completed_operation,input_fingerprint,output_fingerprint,retry_count,payload,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(job_id) DO UPDATE SET
                        stage=excluded.stage,item_id=excluded.item_id,item_index=excluded.item_index,total_items=excluded.total_items,current_operation=excluded.current_operation,last_completed_operation=excluded.last_completed_operation,input_fingerprint=excluded.input_fingerprint,output_fingerprint=excluded.output_fingerprint,retry_count=excluded.retry_count,payload=excluded.payload,updated_at=excluded.updated_at""",
                    (jid,stage,None if item_id is None else str(item_id)[:300],item_index,total_items,None if current_operation is None else str(current_operation)[:200],None if last_completed_operation is None else str(last_completed_operation)[:200],input_fingerprint,output_fingerprint,0 if retry_count is None else int(retry_count),payload_text,ts),
                )
                self.conn.commit()
            except Exception:
                self.conn.rollback(); raise

    def get_job_checkpoint(self, jid):
        """Return the latest generic checkpoint for a job, if one exists."""
        import json
        with self.lock:
            row = self.conn.execute("SELECT * FROM job_checkpoints WHERE job_id=?", (jid,)).fetchone()
        if not row:
            return None
        result = dict(row)
        raw = result.get("payload")
        if raw:
            try:
                result["payload"] = json.loads(raw)
            except (TypeError, ValueError):
                result["payload"] = raw
        return result

    def jobs(self, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    ACTIVE_STATUSES = ("QUEUED", "RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED", "INTERRUPTED", "RECOVERING")

    def active_jobs(self, job_type=None):
        """Only jobs that are still to do or in progress (never history)."""
        sql = "SELECT * FROM jobs WHERE status IN ('QUEUED','RUNNING','PAUSING','PAUSED','CANCEL_REQUESTED','INTERRUPTED','RECOVERING')"
        args = []
        if job_type:
            sql += " AND job_type=?"
            args.append(job_type)
        with self.lock:
            rows = self.conn.execute(sql + " ORDER BY created_at", args).fetchall()
        return [dict(r) for r in rows]

    def job_dashboard_snapshot(self):
        """Return active and most-recent terminal jobs from one read snapshot.

        Operations polls this data frequently, so keep both dashboard views on
        the same thread-local read connection and avoid opening separate read
        transactions for active/history data. The response shape intentionally
        matches the existing active_jobs()/last_jobs() consumers.
        """
        with self.reading() as _rdb:
            active_rows = _rdb.execute(
                "SELECT * FROM jobs WHERE status IN ('QUEUED','RUNNING','PAUSING','PAUSED','CANCEL_REQUESTED','INTERRUPTED','RECOVERING') "
                "ORDER BY created_at"
            ).fetchall()
            terminal_rows = _rdb.execute(
                "SELECT j.job_type, j.status, j.error, j.updated_at, j.completed, j.total FROM jobs j "
                "JOIN (SELECT job_type, MAX(updated_at) mu FROM jobs "
                "WHERE status IN ('COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED') GROUP BY job_type) m "
                "ON m.job_type=j.job_type AND m.mu=j.updated_at "
                "WHERE j.status IN ('COMPLETED','COMPLETED_WITH_ERRORS','FAILED','CANCELLED')"
            ).fetchall()
        return (
            [dict(r) for r in active_rows],
            {r["job_type"]: dict(r) for r in terminal_rows},
        )

    def last_jobs(self):
        """Most recent finished job of each module (for the module cards)."""
        return self.job_dashboard_snapshot()[1]

    def get_job(self, jid):
        with self.lock:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE id=?", (jid,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        with self.lock:
            evts = self.conn.execute(
                "SELECT message FROM job_activity_events WHERE job_id=? ORDER BY id DESC LIMIT 60",
                (jid,)
            ).fetchall()
        d["events"] = [r["message"] for r in reversed(evts)]
        with self.lock:
            deps = self.conn.execute("SELECT d.job_id,d.depends_on_job_id,d.dependency_type,d.created_at,j.status AS dependency_status FROM job_dependencies d LEFT JOIN jobs j ON j.id=d.depends_on_job_id WHERE d.job_id=? ORDER BY d.created_at", (jid,)).fetchall()
            executions = self.conn.execute("SELECT * FROM job_executions WHERE job_id=? ORDER BY attempt DESC LIMIT 10", (jid,)).fetchall()
        d["dependencies"] = [dict(r) for r in deps]
        d["executions"] = [dict(r) for r in executions]
        try:
            d["resource_requirements"] = json.loads(d.get("resource_requirements") or "{}")
        except Exception:
            pass
        return d

    # ------------------------------------------------------------------
    # SUBSECTION: Statistics
    # Library and dashboard statistics.
    # ------------------------------------------------------------------
    def stats(self):
        return self.dashboard_stats()

    def dashboard_stats(self):
        """Return Operations dashboard counts through one read-side statement."""
        with self.reading() as _rdb:
            row = _rdb.execute(
                "SELECT "
                "(SELECT COUNT(*) FROM media) AS media, "
                "(SELECT COUNT(*) FROM jobs) AS jobs, "
                "(SELECT COUNT(*) FROM jobs WHERE status='FAILED') AS failed, "
                "(SELECT COUNT(*) FROM jobs WHERE status IN ('QUEUED','RUNNING','PAUSED')) AS pending, "
                "(SELECT COUNT(*) FROM pending_actions WHERE status='PROPOSED') AS awaiting_approval"
            ).fetchone()
        return {
            "media": int(row["media"] or 0),
            "jobs": int(row["jobs"] or 0),
            "failed": int(row["failed"] or 0),
            "pending": int(row["pending"] or 0),
            "awaiting_approval": int(row["awaiting_approval"] or 0),
        }
