"""All HTTP/WebSocket routes. Route bodies are unchanged from the tested
v14 build; only the imports moved to reflect the new module layout.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Module helpers
#   - Router construction
#       - Caption and entity image helpers
#       - Authentication helpers
#       - Status aggregation
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json
import re
import time
import io
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import APIRouter, Request, WebSocket, HTTPException

from .. import config
from .transfer_lanes import transfer_lane_metrics
from ..core.upload_limits import get_upload_policy, upload_policy_dict
from ..core.workspace_guard import validate_automation_root, validate_local_upload_folder
from ..core.auth import verify_password, is_setup_required, SETUP_COMPLETE_FLAG, SETUP_REQUIRED_FLAG
from ..core.session_cookie import set_session_cookie
from ..telegram.service import TELETHON_AVAILABLE
from ..operations import automation as content_automation
from ..engines import metadata_channel_pipeline
from ..core.workflows import workflow_snapshot
from ..operations.torrent_engine import QBittorrentEngine, TorrentEngineError
from .routes_automation import register_automation_routes
from .routes_content import register_content_routes
from .routes_screen import register_screen_routes
from .routes_collection import register_collection_routes
from .routes_local import register_local_routes
from .routes_entities import register_entity_routes
from .routes_entity_pages import register_entity_page_routes
from .routes_jobs import register_job_routes
from .routes_library import register_library_routes
from .routes_media import register_media_routes
from .routes_metadata_assets import register_metadata_assets_routes
from .routes_playback import register_playback_routes
from .routes_playback_compat import register_playback_compat_routes
from .routes_tracks import register_tracks_routes
from .routes_settings import register_settings_routes
from .routes_system import register_system_routes
from .routes_tags_logs import register_tags_logs_routes
from .routes_telegram import register_telegram_routes



# ==============================================================================
# SECTION: Module helpers
# Watchdog classification and search query helpers.
# ==============================================================================

PLAYBACK_IDLE_SECONDS = 15.0
_PLAYBACK_SEEN: dict = {"bytes": -1, "at": 0.0}


def _playback_flowing(eng, seen: dict | None = None, now: float | None = None) -> bool:
    """Whether the playback engine has actually streamed something in the last PLAYBACK_IDLE_SECONDS. A video that is open but paused (its buffer
    full), ended or abandoned keeps its session, but nothing flows: it is listed as IDLE and is not counted as running work."""
    seen = _PLAYBACK_SEEN if seen is None else seen
    now = time.monotonic() if now is None else now
    served = int(getattr(eng, "bytes_served", 0) or 0)
    if int(getattr(eng, "active_http_requests", 0) or 0) > 0 or served != seen["bytes"]:
        seen["bytes"], seen["at"] = served, now
    return now - float(seen["at"]) <= PLAYBACK_IDLE_SECONDS


def _playback_watchdog_classification(state: str, status: dict) -> tuple[str, str]:
    """Report a healthy watchdog during ordinary seeking/buffering.

    SEEKING is a normal transient player state. Degrade only when the engine
    explicitly reports starvation/stall, or a seek/buffer state has made no
    progress for 12 seconds and has no active/queued request capable of moving it.
    """
    state = str(state or "UNKNOWN").upper()
    qos = status.get("playback_qos") or {}
    age = qos.get("last_progress_age_seconds")
    try:
        stale = age is not None and float(age) > 12.0
    except (TypeError, ValueError):
        stale = False
    idle_ranges = all(int(status.get(key, 0) or 0) == 0 for key in ("queued", "active", "http_streams"))
    if state in {"STARVING", "STALLED"}:
        return "DEGRADED", "Playback buffer is starved" if state == "STARVING" else "Playback has stalled"
    if state in {"SEEKING", "BUFFERING"} and stale and idle_ranges:
        return "DEGRADED", f"No playback progress for {float(age):.0f}s and no active range work"
    if state in {"PLAYING", "READY", "SEEKING", "BUFFERING"}:
        return "HEALTHY", "Interactive playback runtime"
    return "UNKNOWN", "Playback state is not currently classified"


SCENE_MATCH_FIELD_STATUS_CTE = """
WITH media_field_status AS (
    SELECT m.id,
        CASE WHEN
            EXISTS (
                SELECT 1 FROM video_performers vp
                JOIN performers p ON p.id=vp.performer_id
                WHERE vp.video_id=m.id
                  AND LOWER(TRIM(COALESCE(p.name,''))) NOT IN ('unknown performer','unknown')
            )
            OR EXISTS (
                SELECT 1 FROM local_video_performers lvp
                JOIN performers p ON p.id=lvp.performer_id
                WHERE lvp.video_id=m.id
                  AND LOWER(TRIM(COALESCE(p.name,''))) NOT IN ('unknown performer','unknown')
            )
            THEN 0 ELSE 1 END AS missing_performer,
        CASE WHEN
            EXISTS (
                SELECT 1 FROM video_studios vs
                JOIN studios s ON s.id=vs.studio_id
                WHERE vs.video_id=m.id
                  AND LOWER(TRIM(COALESCE(s.name,''))) NOT IN ('unknown studio','unknown')
            )
            OR (
                TRIM(COALESCE(m.site_name,''))<>''
                AND LOWER(TRIM(COALESCE(m.site_name,''))) NOT IN ('unknown studio','unknown')
            )
            THEN 0 ELSE 1 END AS missing_studio,
        CASE WHEN
            UPPER(COALESCE(m.identification_state,'')) IN ('UNIDENTIFIED','PARTIAL','REPAIR','REJECTED')
            OR LOWER(COALESCE(m.metadata_source,'')) IN ('needs_review','unmatched','unidentified','partial')
            THEN 1 ELSE 0 END AS unresolved_state
    FROM media m
)
"""


def _scene_match_review_unresolved_catalog(
    rdb, limit: int, offset: int, channel_id: str = "", missing_field: str = "all"
) -> tuple[int, list[dict], list[dict]]:
    """Read unresolved local metadata records without triggering provider or Telegram work."""
    base_filter = """
        (fs.missing_performer=1 OR fs.missing_studio=1 OR fs.unresolved_state=1)
        AND NOT EXISTS (
            SELECT 1 FROM metadata_suggestions s
            WHERE s.video_id=m.id AND s.status='PENDING'
              AND UPPER(COALESCE(m.identification_state,'')) NOT IN ('MATCHED','VERIFIED')
        )
    """
    filters = []
    filter_params = []
    if channel_id:
        filters.append("CAST(m.source_channel AS TEXT)=?")
        filter_params.append(str(channel_id))
    if missing_field == "performer":
        filters.append("fs.missing_performer=1")
    elif missing_field == "studio":
        filters.append("fs.missing_studio=1")
    unresolved_filter = base_filter + "".join(f"\n        AND {item}" for item in filters)
    total = int(rdb.execute(
        SCENE_MATCH_FIELD_STATUS_CTE
        + f"SELECT COUNT(*) FROM media m JOIN media_field_status fs ON fs.id=m.id WHERE {unresolved_filter}",
        tuple(filter_params),
    ).fetchone()[0] or 0)
    rows = [dict(row) for row in rdb.execute(
        SCENE_MATCH_FIELD_STATUS_CTE
        + f"""SELECT m.id AS media_id,m.source_channel AS channel_id,
                     COALESCE(NULLIF(TRIM(c.title),''),'Unknown channel') AS channel_title,
                     m.source_message_id AS message_id,m.filename,m.title,m.duration,
                     COALESCE(
                         NULLIF(CASE WHEN LOWER(TRIM(COALESCE(m.site_name,''))) NOT IN ('unknown studio','unknown')
                                     THEN TRIM(m.site_name) END,''),
                         (SELECT s.name FROM video_studios vs JOIN studios s ON s.id=vs.studio_id
                          WHERE vs.video_id=m.id
                            AND LOWER(TRIM(COALESCE(s.name,''))) NOT IN ('unknown studio','unknown')
                          ORDER BY s.name LIMIT 1)
                     ) AS studio_name,
                     m.identification_state,
                     fs.missing_performer,fs.missing_studio,fs.unresolved_state
               FROM media m JOIN media_field_status fs ON fs.id=m.id
               LEFT JOIN channels c ON CAST(c.telegram_id AS TEXT)=CAST(m.source_channel AS TEXT)
              WHERE {unresolved_filter}
              ORDER BY CASE UPPER(COALESCE(m.identification_state,''))
                           WHEN 'REPAIR' THEN 0 WHEN 'PARTIAL' THEN 1
                           WHEN 'UNIDENTIFIED' THEN 2 ELSE 3 END,
                       m.id ASC
              LIMIT ? OFFSET ?""",
        tuple(filter_params + [int(limit), int(offset)]),
    ).fetchall()]
    channel_rows = rdb.execute(
        SCENE_MATCH_FIELD_STATUS_CTE
        + f"""SELECT DISTINCT CAST(m.source_channel AS TEXT) AS channel_id,
                     COALESCE(NULLIF(TRIM(c.title),''),'Unknown channel') AS title
              FROM media m JOIN media_field_status fs ON fs.id=m.id
              LEFT JOIN channels c ON CAST(c.telegram_id AS TEXT)=CAST(m.source_channel AS TEXT)
              WHERE {base_filter}
                AND TRIM(COALESCE(m.source_channel,''))<>''
              ORDER BY LOWER(COALESCE(NULLIF(TRIM(c.title),''),'Unknown channel')),channel_id"""
    ).fetchall()
    channels = [dict(row) for row in channel_rows]
    if rows:
        ids = [int(row["media_id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        linked_people = rdb.execute(
            f"""SELECT video_id,name FROM (
                    SELECT DISTINCT vp.video_id AS video_id,p.name AS name
                    FROM video_performers vp JOIN performers p ON p.id=vp.performer_id
                    WHERE vp.video_id IN ({marks})
                      AND LOWER(TRIM(COALESCE(p.name,''))) NOT IN ('unknown performer','unknown')
                    UNION
                    SELECT DISTINCT lvp.video_id AS video_id,p.name AS name
                    FROM local_video_performers lvp JOIN performers p ON p.id=lvp.performer_id
                    WHERE lvp.video_id IN ({marks})
                      AND LOWER(TRIM(COALESCE(p.name,''))) NOT IN ('unknown performer','unknown')
                ) ORDER BY video_id,name""",
            tuple(ids + ids),
        ).fetchall()
        by_video: dict[int, list[str]] = {}
        for person in linked_people:
            by_video.setdefault(int(person["video_id"]), []).append(str(person["name"]))
        for row in rows:
            row["performers"] = by_video.get(int(row["media_id"]), [])
            row["missing_fields"] = [
                field for field, missing in (
                    ("performer", row["missing_performer"]),
                    ("studio", row["missing_studio"]),
                ) if int(missing or 0)
            ]
    return total, rows, channels



def fts_prefix_query(text: str, max_terms: int = 8) -> str:
    """Turn free user text into a safe FTS5 prefix query.

    Raw text must never reach FTS5 syntax: characters such as - ( ) * ^ : % backslash quote
    and bare AND/OR/NOT/NEAR made Library and tag search return HTTP 500.
    Each unicode word becomes a quoted literal with prefix matching; an empty
    result means "fall back to LIKE".
    """
    tokens = [t for t in re.findall(r"\w+", str(text or ""), flags=re.UNICODE) if t][:max_terms]
    return " ".join(f'"{t}"*' for t in tokens)



# Ordered: registration order decides which route matches first.
ROUTE_MODULES = (
    'routes_content',
    'routes_screen',
    'routes_local',
    'routes_system',
    'routes_telegram',
    'routes_automation',
    'routes_jobs',
    'routes_metadata_assets',
    'routes_playback',
    'routes_playback_compat',
    'routes_tracks',
    'routes_settings',
    'routes_library',
    'routes_entities',
    'routes_media',
    'routes_entity_pages',
    'routes_tags_logs',
)


# ==============================================================================
# SECTION: Router construction
# Builds the shared helpers and registers each routes_*.py group.
# ==============================================================================

def build_router(*, db, governor, telegram, jobs, sessions, login_limiter, tokens=None, player_state=None, resources=None, page_enricher=None) -> APIRouter:
    """Routes are built as a closure over the app's singletons (db, jobs,
    etc.) rather than importing module-level globals, so tests can spin
    up an isolated app instance instead of sharing process-wide state."""
    router = APIRouter()
    _entity_image_locks: dict[tuple[str, int], asyncio.Lock] = {}
    _channel_profile_photo_cache: dict[str, tuple[float, bytes, str]] = {}
    _channel_profile_photo_locks: dict[str, asyncio.Lock] = {}
    _external_player_monitors: dict[int, asyncio.Task] = {}
    _caption_sync_tasks: set[asyncio.Task] = set()
    _caption_sync_lock = asyncio.Lock()
    _tag_taxonomy_cache: dict = {"at": 0.0, "value": None}


    # ------------------------------------------------------------------
    # SUBSECTION: Caption and entity image helpers
    # Shared helpers used by route groups.
    # ------------------------------------------------------------------
    async def _sync_catalogue_caption(media_id: int) -> dict:
        from ..engines.caption_sync import sync_catalogue_caption
        async with _caption_sync_lock:
            return await sync_catalogue_caption(db, telegram, int(media_id))

    async def _sync_many_catalogue_captions(media_ids: list[int]) -> None:
        # Bulk mapping must not hold the HTTP request open for every Telegram
        # edit. Process one at a time to avoid flooding scanned/delivered channels.
        ids = list(dict.fromkeys(int(x) for x in media_ids if int(x) > 0))
        changed = unchanged = failed = 0
        for index, media_id in enumerate(ids):
            try:
                outcome = await _sync_catalogue_caption(media_id)
                if outcome.get("ok"):
                    changed += int(outcome.get("synced_count") or 0)
                    unchanged += sum(1 for item in outcome.get("targets", []) if item.get("status") == "unchanged")
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                try:
                    db.log(f"Queued Telegram caption sync failed for media_id={media_id}: {type(exc).__name__}: {exc}", "WARN")
                except Exception:
                    _swallowed_note("routes.build_router._sync_many_catalogue_captions")
                    pass
            if index + 1 < len(ids):
                await asyncio.sleep(0.5)
        try:
            db.log(
                f"Caption reconciliation batch completed: {len(ids)} catalogue item(s), "
                f"{changed} Telegram caption(s) updated, {unchanged} unchanged target(s), "
                f"{failed} item(s) need review/retry.",
                "INFO" if not failed else "WARN",
            )
        except Exception:
            _swallowed_note("routes.build_router._sync_many_catalogue_captions")
            pass

    def _queue_catalogue_caption_sync(media_ids: list[int]) -> int:
        ids = list(dict.fromkeys(int(x) for x in media_ids if int(x) > 0))
        if not ids:
            return 0
        task = asyncio.create_task(_sync_many_catalogue_captions(ids), name="telegram-caption-sync-bulk")
        _caption_sync_tasks.add(task)
        task.add_done_callback(_caption_sync_tasks.discard)
        return len(ids)

    def _entity_image_lock(kind: str, entity_id: int) -> asyncio.Lock:
        key = (str(kind), int(entity_id))
        lock = _entity_image_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _entity_image_locks[key] = lock
        return lock

    def _decode_image_mime(data: bytes, url: str, header: str = "") -> str:
        h = str(header or "").split(";", 1)[0].strip().lower()
        # Never trust Content-Type alone. A provider/CDN can return an HTML
        # challenge/error page with image/* headers, which would poison the
        # entity cache and make every card image fail.
        if h.startswith("image/"):
            try:
                from PIL import Image
                with Image.open(io.BytesIO(data)) as im:
                    im.verify()
                return h
            except Exception:
                return ""
        n = str(url or "").lower()
        if data.startswith(b"\x89PNG\r\n\x1a\n") or n.endswith(".png"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff") or n.endswith((".jpg", ".jpeg")):
            return "image/jpeg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return "image/webp"
        if data.lstrip().startswith(b"<svg") or b"<svg" in data[:512].lower():
            return "image/svg+xml"
        return ""

    async def _proxy_entity_image(kind: str, entity_id: int, url: str, *,
                                  allow_initial_smart_crop: bool = True,
                                  force_original: bool = False) -> tuple[bytes, str]:
        """Fetch provider artwork, caching a first-time crop only for automatic performer images.

        force_original is reserved for an explicit user-selected provider image:
        it bypasses a previous automatic crop and stores the original bytes.
        """
        parsed = urllib.request.urlparse(str(url))
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Artwork URL must be HTTP(S)")
        table = "performer_image_cache" if kind == "performer" else "studio_image_cache"
        key = "performer_id" if kind == "performer" else "studio_id"
        lock = _entity_image_lock(kind, entity_id)
        async with lock:
            should_smart_crop = False
            crop_pending = False
            if kind == "performer" and allow_initial_smart_crop and not force_original:
                with db.lock:
                    owner = db.conn.execute("SELECT image_locked,image_source,image_crop_version FROM performers WHERE id=?", (int(entity_id),)).fetchone()
                owner_source = str(owner["image_source"] or "").strip().lower() if owner else ""
                should_smart_crop = bool(owner and not owner["image_locked"] and owner_source != "manual" and not owner_source.startswith("provider-selected:"))
                crop_pending = bool(should_smart_crop and int(owner["image_crop_version"] or 0) < 1)
            with db.lock:
                cached = db.conn.execute(
                    f"SELECT url,mime,data FROM {table} WHERE {key}=?", (int(entity_id),)
                ).fetchone()
            if not force_original and cached and cached["data"] and str(cached["url"] or "") == str(url):
                cached_bytes = bytes(cached["data"])
                cached_mime = str(cached["mime"] or "")
                try:
                    from PIL import Image
                    with Image.open(io.BytesIO(cached_bytes)) as im:
                        im.verify()
                    if cached_mime.lower().startswith("image/"):
                        if crop_pending:
                            from ..engines.thumbnail_cache import _persist_entity_image_sync
                            await asyncio.to_thread(
                                _persist_entity_image_sync, db, kind, int(entity_id), str(url), cached_bytes,
                                smart_crop_initial=True,
                            )
                            with db.lock:
                                refreshed = db.conn.execute("SELECT mime,data FROM performer_image_cache WHERE performer_id=? AND url=?", (int(entity_id), str(url))).fetchone()
                            if refreshed and refreshed["data"]:
                                return bytes(refreshed["data"]), str(refreshed["mime"] or cached_mime)
                        return cached_bytes, cached_mime
                except Exception:
                    # Ignore poisoned legacy cache entries and re-fetch the
                    # provider URL below. Do not serve invalid bytes.
                    _swallowed_note("routes.build_router._proxy_entity_image")
                    pass

            # Local-first recovery: if the SQLite image bytes cache is missing but
            # the canonical entity artwork file still exists and matches this URL,
            # rebuild the cache from disk instead of contacting the provider again.
            from ..engines.thumbnail_cache import reconcile_entity_image_cache_from_disk_sync
            restored = None
            if not force_original:
                restored = await asyncio.to_thread(
                    reconcile_entity_image_cache_from_disk_sync, db, kind, int(entity_id), str(url)
                )
            if restored:
                if crop_pending:
                    from ..engines.thumbnail_cache import _persist_entity_image_sync
                    await asyncio.to_thread(
                        _persist_entity_image_sync, db, kind, int(entity_id), str(url), bytes(restored[0]),
                        smart_crop_initial=True,
                    )
                    with db.lock:
                        refreshed = db.conn.execute("SELECT mime,data FROM performer_image_cache WHERE performer_id=? AND url=?", (int(entity_id), str(url))).fetchone()
                    if refreshed and refreshed["data"]:
                        return bytes(refreshed["data"]), str(refreshed["mime"] or restored[1])
                return restored

            def _download() -> tuple[bytes, str, str]:
                origin = f"{parsed.scheme}://{parsed.netloc}/"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/154 Safari/537.36",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                    "Referer": origin,
                }
                req = urllib.request.Request(str(url), headers=headers)
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read(8 * 1024 * 1024 + 1)
                    if len(data) > 8 * 1024 * 1024:
                        raise ValueError("Artwork exceeds 8 MiB")
                    mime = _decode_image_mime(data, r.geturl(), r.headers.get("Content-Type", ""))
                    if not mime:
                        raise ValueError("Provider did not return a usable image")
                    return data, mime, r.geturl()

            from ..core.resource_registry import coordinator_for
            (data, mime, final_url), _joined = await coordinator_for(db).run(
                f"entity-image:{kind}:{int(entity_id)}:{str(url)}",
                lambda: asyncio.to_thread(_download),
            )
            if kind == "performer":
                # Use the same disk/SQLite cache writer as background sync so
                # the first automatic load gets one stable crop. Explicitly
                # selected provider artwork uses force_original and is never cropped.
                from ..engines.thumbnail_cache import _persist_entity_image_sync
                await asyncio.to_thread(
                    _persist_entity_image_sync, db, kind, int(entity_id), str(url), data,
                    smart_crop_initial=should_smart_crop and not force_original,
                )
                with db.lock:
                    saved = db.conn.execute(
                        "SELECT mime,data FROM performer_image_cache WHERE performer_id=? AND url=?",
                        (int(entity_id), str(url)),
                    ).fetchone()
                if saved and saved["data"]:
                    return bytes(saved["data"]), str(saved["mime"] or mime)
                # A manual edit may have won the race while the automatic crop
                # was running. Never return newly derived bytes for that stale task.
                return data, mime
            with db.lock:
                db.conn.execute(
                    f"INSERT OR REPLACE INTO {table}({key},url,mime,data,updated_at) VALUES(?,?,?,?,?)",
                    (int(entity_id), str(url), mime, data, config.now_iso()),
                )
                db.conn.commit()
            return data, mime


    # ------------------------------------------------------------------
    # SUBSECTION: Authentication helpers
    # Client keys and auth checks.
    # ------------------------------------------------------------------
    def client_key(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def require_auth(request: Request) -> dict:
        token = request.cookies.get(config.SESSION_COOKIE)
        entry = sessions.get(token)
        if not entry:
            raise HTTPException(401, "Authentication required")
        return entry

    def session_from_ws(ws: WebSocket):
        token = ws.cookies.get(config.SESSION_COOKIE)
        return sessions.get(token)



    # ------------------------------------------------------------------
    # SUBSECTION: Status aggregation
    # Status payload and cached snapshot.
    # ------------------------------------------------------------------
    def _jobs_of_this_mode(rows: list) -> tuple[list, list]:
        """(the jobs of the mode that is open, the jobs of the other mode). The other mode's tasks are not listed: only their number is shown."""
        from ..core import content_mode as _cm_jobs, job_mode as _job_mode
        try:
            return _job_mode.split_by_mode(db, rows, _cm_jobs.get_mode(db))
        except Exception:
            _swallowed_note("routes.build_router._jobs_of_this_mode")
            return rows, []

    def _active_jobs_view(active_jobs=None, watchdogs=None) -> list:
        """Shape active jobs for the UI, adding names, watchdog state and dependencies."""
        from ..jobs.manager import JOB_NAMES
        out = []
        try:
            _download_governor = governor.status()
            _download_wait_remaining = float(_download_governor.get("download_flood_wait_remaining") or 0)
            _download_wait_message = str(_download_governor.get("download_flood_wait_message") or "")
        except Exception:
            _download_wait_remaining, _download_wait_message = 0.0, ""
        wd = {}
        try:
            watchdogs = watchdogs if watchdogs is not None else jobs.watchdogs.snapshot()
            wd = {str(x.get("name")): x for x in watchdogs.get("jobs", [])}
        except Exception:
            wd = {}
        try:
            from ..core import content_mode as _cm_sfw
            sfw = _cm_sfw.get_mode(db) == "sfw"   # job text carries file and channel names, adult ones included
        except Exception:
            sfw = True
        _jobs_now = active_jobs if active_jobs is not None else _jobs_of_this_mode(db.active_jobs())[0]
        for j in _jobs_now:
            total = j.get("total")
            done = j.get("completed")
            try:
                detail = json.loads(j.get("detail") or "{}")
            except Exception:
                detail = {}
            # A scan made of stages shows one bar by stages (the items of the stage running now go in `stage`), so it does not rescale when a stage starts.
            from ..engines.metadata_channel_pipeline import stage_view
            by_stage = stage_view(str(j["job_type"]), detail)
            stage = {"completed": done, "total": total} if by_stage else None
            progress_now = by_stage["progress"] if by_stage else (j.get("progress") or 0)
            if by_stage:
                done, total = by_stage["completed"], by_stage["total"]
            out.append({
                "id": j["id"], "type": j["job_type"], "name": JOB_NAMES.get(j["job_type"], j["job_type"]),
                "status": j["status"], "progress": progress_now,
                "completed": done, "total": total, "stage": stage,
                "pending": (total - (done or 0)) if total else None,
                "operation": "" if sfw else (j.get("current_operation") or ""),
                "item": "" if sfw else (j.get("current_item") or ""),
                "error": "" if sfw else (j.get("error") or ""),
                "rate_limit_message": (_download_wait_message if str(j.get("status") or "").upper() == "RUNNING" and _download_wait_remaining > 0 else ""),
                "parent_job_id": str(detail.get("parent_job_id") or ""),
                "parent_automation_item": str(detail.get("parent_automation_item") or detail.get("automation_item") or ""),
                "started": j.get("created_at") or "",
                "updated_at": j.get("updated_at") or "",
                "watchdog_state": (wd.get(str(j["id"])) or {}).get("state", "UNKNOWN"),
                "watchdog_reason": (wd.get(str(j["id"])) or {}).get("reason", ""),
                "watchdog_heartbeat_age": (wd.get(str(j["id"])) or {}).get("heartbeat_age"),
                "watchdog_progress_age": (wd.get(str(j["id"])) or {}).get("progress_age"),
                "watchdog_activity_age": (wd.get(str(j["id"])) or {}).get("activity_age"),
                "watchdog_recovery_generation": (wd.get(str(j["id"])) or {}).get("recovery_generation"),
                "admission_state": j.get("admission_state") or "READY",
                "admission_reason": j.get("admission_reason") or "",
                "next_run_at": j.get("next_run_at"),
                "priority": j.get("priority", 50),
                "attempt": j.get("attempt", 0),
                "execution_id": j.get("execution_id"),
                "worker_id": j.get("worker_id"),
                "lease_id": j.get("lease_id"),
                "lease_expires_at": j.get("lease_expires_at"),
                "last_heartbeat_at": j.get("last_heartbeat_at"),
                "worker_state": j.get("worker_state") or "IDLE",
                "retry_count": j.get("retry_count", 0),
                "max_retries": j.get("max_retries", 3),
                "failure_class": j.get("failure_class") or "",
                "deadline_at": j.get("deadline_at"),
                "dependencies": db.job_dependencies(j["id"]) if hasattr(db, "job_dependencies") else [],
            })
        # Playback is an interactive runtime process, not a durable JobManager
        # job. Surface it in Active Jobs only while the shared playback engine
        # has an active video/range session, so operators can stop a stuck
        # playback without creating a second scheduler or job queue.
        try:
            eng = ps.get("engine")
            if eng is not None and eng.running and (eng.current_key is not None or eng.active_http_requests > 0):
                st = eng.status()
                key = list(eng.current_key) if eng.current_key is not None else None
                msg = eng.messages.get(eng.current_key) if eng.current_key is not None else None
                title = "Playback"
                if msg is not None:
                    title = getattr(msg, "file", None) and (getattr(msg.file, "name", None) or "") or getattr(msg, "message", None) or title
                elif key:
                    title = f"Video {key[1]}"
                qos = st.get("playback_qos") or {}
                state = str(qos.get("state") or "PLAYING").upper()
                if state == "IDLE": state = "PLAYING"
                if not _playback_flowing(eng):
                    state = "IDLE"     # open but nothing streaming: still listed (so it can be stopped), not counted as running
                watchdog_state, watchdog_reason = ("HEALTHY", "Open, not streaming") if state == "IDLE" else _playback_watchdog_classification(state, st)
                current = {
                    "id": "playback", "type": "PLAYBACK", "name": "Playback",
                    "status": state, "progress": 0, "completed": None, "total": None,
                    "pending": st.get("queued", 0), "operation": state, "item": title,
                    "error": "", "started": "", "updated_at": "",
                    "watchdog_state": watchdog_state,
                    "watchdog_reason": watchdog_reason,
                    "watchdog_heartbeat_age": None, "watchdog_progress_age": None,
                    "watchdog_activity_age": None, "watchdog_recovery_generation": None,
                    "admission_state": "READY", "admission_reason": "", "next_run_at": None,
                    "priority": 0, "attempt": 0, "execution_id": None, "worker_id": None,
                    "lease_id": None, "lease_expires_at": None, "last_heartbeat_at": None,
                    "worker_state": "INTERACTIVE", "retry_count": 0, "max_retries": 0,
                    "failure_class": "", "deadline_at": None, "dependencies": [],
                    "playback": {
                        "channel_id": key[0] if key else None, "message_id": key[1] if key else None,
                        "title": title, "state": state, "queued": st.get("queued", 0),
                        "active_ranges": st.get("active", 0), "http_streams": st.get("http_streams", 0),
                        "speed": st.get("speed", ""),
                        "buffer": (st.get("buffer_controller") or {}).get("foreground_seconds"),
                    },
                }
                out.insert(0, current)
        except Exception:
            _swallowed_note("routes.build_router._active_jobs_view")
            pass
        return out

    def _data_versions() -> dict:
        from ..core import change_tracking
        change_tracking.ensure(db)
        return change_tracking.versions(db)

    def status_payload() -> dict:
        """The status response; in SFW the free text that can carry file and channel names (job items, errors, events, scan names) is blanked."""
        out = _status_payload_raw()
        try:
            from ..core import content_mode as _cm_sfw
            sfw = _cm_sfw.get_mode(db) == "sfw"
        except Exception:
            sfw = True
        if not sfw:
            return out
        text = ("current_item", "current_operation", "error", "detail", "source_channel", "destination_channel")
        for key in ("current_job",):
            if isinstance(out.get(key), dict):
                out[key] = {k: ("" if k in text else v) for k, v in out[key].items()}
        if isinstance(out.get("last_jobs"), dict):
            out["last_jobs"] = {k: ({f: ("" if f in text else x) for f, x in v.items()} if isinstance(v, dict) else v) for k, v in out["last_jobs"].items()}
        out["events"] = []
        out["channel_scans"] = []
        ca = out.get("content_automation")
        if isinstance(ca, dict):
            ca["items"] = []
        return out

    def _status_payload_raw() -> dict:
        """Assemble the full status response."""
        from ..core.diagnostics import recent_structured_events
        active_jobs, last_jobs = db.job_dashboard_snapshot()
        active_jobs, other_mode_jobs = _jobs_of_this_mode(active_jobs)
        watchdogs = jobs.watchdogs.snapshot() if hasattr(jobs, "watchdogs") else {}
        if other_mode_jobs and isinstance(watchdogs, dict) and isinstance(watchdogs.get("jobs"), list):
            hidden = {str(j.get("id")) for j in other_mode_jobs}
            watchdogs = {**watchdogs, "jobs": [w for w in watchdogs["jobs"] if str(w.get("name")) not in hidden]}
        current_job = next((j for j in active_jobs if j.get("status") in ("RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED")), None)
        governor_snapshot = governor.status()
        channel_scans = __import__("telegram_archive.core.telegram_registry", fromlist=["recent_channel_scan_diagnostics"]).recent_channel_scan_diagnostics(db, limit=12)
        flood_by_channel = governor_snapshot.get("download_floodwaits_by_channel", {}) if isinstance(governor_snapshot, dict) else {}
        for scan in channel_scans:
            scan_key = str(scan.get("channel_id") or "")
            try:
                scan["floodwaits"] = max(int(scan.get("floodwaits") or 0), int(flood_by_channel.get(scan_key, 0) or 0))
            except (TypeError, ValueError):
                pass
        return {
            "telegram": {
                "available": TELETHON_AVAILABLE,
                "connected": telegram.connected,
                "status": telegram.status_text,
            },
            # Aliases consumed by the dashboard JS
            "telegram_connected": telegram.connected,
            "telegram_status": telegram.status_text,
            "stats": db.dashboard_stats(),
            "governor": governor_snapshot,
            "channel_scans": channel_scans,
            "telegram_performance": telegram.performance_status() if hasattr(telegram, "performance_status") else {},
            "current_job": current_job,
            "jobs": _active_jobs_view(active_jobs, watchdogs),        # active jobs only, never history
            "other_mode_jobs": __import__("telegram_archive.core.job_mode", fromlist=["other_summary"]).other_summary(other_mode_jobs),   # how many tasks the other mode has going
            "events": [
                {**e, "created_at": e.get("ts"), "message": e.get("message") or e.get("event") or "", "module": e.get("component") or "", "event": e.get("event"), "outcome": e.get("outcome"), "severity": e.get("severity")}
                for e in recent_structured_events(150)
            ],
            "paused": bool(jobs.paused),
            "version": config.APP_VERSION,     # shown under the storage card in the sidebar
            # V29.4.131: change counters; the page refreshes what it shows
            # when one of them moves (videos, entities, channels, settings).
            "data_versions": _data_versions(),
            "last_jobs": last_jobs,
            "content_automation": content_automation.list_status(db, active_jobs=active_jobs),
            "watchdogs": watchdogs,
            "resource_registry": __import__("telegram_archive.core.resource_registry", fromlist=["registry_stats"]).registry_stats(db),
            "job_orchestration": jobs.orchestration_snapshot() if hasattr(jobs, "orchestration_snapshot") else {},
            "resource_leases": db.resource_lease_snapshot(),
            "execution_lanes": jobs.execution_lane_capacities() if hasattr(jobs, "execution_lane_capacities") else {},
            "background_pipeline": {
                **(jobs.background_priority.status() if hasattr(jobs, "background_priority") else {"active_stage": None, "queued_stages": [], "stage_order": {}}),
                "catalogue_pending": len(page_enricher.queue) if page_enricher else 0,
                "snapshot_pending": page_enricher.snapshot_pending() if page_enricher else 0,
                "metadata_pending": len(page_enricher.metadata_queue) if page_enricher else 0,
                "automatic_lane": "shared",
                "interactive_lanes": ["authentication", "playback", "telegram_transfer"],
            },
        }

    _status_cache = {"at": 0.0, "data": None, "json": "", "mode": ""}
    _status_lock = asyncio.Lock()
    # Status aggregation is database-heavy. Keep it off the shared asyncio
    # worker pool so Telegram/metadata/native-media work cannot inflate
    # dashboard status latency. The existing one-cache-per-process contract
    # remains unchanged.
    _status_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="archive-x-status")

    async def status_snapshot(max_age: float = 1.0):
        from ..core import content_mode as _cm_mode
        mode = _cm_mode.get_mode(db)   # the cache is per mode: an NSFW snapshot must never be served to SFW
        now = time.monotonic()
        if _status_cache["data"] is not None and _status_cache["mode"] == mode and now - _status_cache["at"] < max_age:
            return _status_cache["data"], _status_cache["json"]
        async with _status_lock:
            now = time.monotonic()
            if _status_cache["data"] is None or _status_cache["mode"] != mode or now - _status_cache["at"] >= max_age:
                data = await asyncio.get_running_loop().run_in_executor(_status_executor, status_payload)
                payload = json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))
                _status_cache.update(at=time.monotonic(), data=data, json=payload, mode=mode)
            return _status_cache["data"], _status_cache["json"]

    ps = player_state if player_state is not None else {}
    ps.setdefault("engine", None)
    ps.setdefault("_playback_stop_task", None)

    # Keep route order stable; the map contains only deliberate helper exports
    # passed between adjacent registrars.
    route_exports: dict[str, object] = {}
    for _module in ROUTE_MODULES:
        if _module == "routes_content":
            register_content_routes(router, db=db, require_auth=require_auth)
        elif _module == "routes_screen":
            register_screen_routes(router, db=db, require_auth=require_auth)
            register_collection_routes(router, db=db, require_auth=require_auth)
        elif _module == "routes_local":
            register_local_routes(router, db=db, require_auth=require_auth, config=config, jobs=jobs)
        elif _module == "routes_system":
            route_exports.update(
                register_system_routes(
                    router,
                    config=config,
                    db=db,
                    governor=governor,
                    jobs=jobs,
                    login_limiter=login_limiter,
                    ps=ps,
                    require_auth=require_auth,
                    sessions=sessions,
                    telegram=telegram,
                    _active_jobs_view=_active_jobs_view,
                    client_key=client_key,
                    status_snapshot=status_snapshot,
                )
            )
        elif _module == "routes_telegram":
            route_exports.update(
                register_telegram_routes(
                    router,
                    db=db,
                    governor=governor,
                    jobs=jobs,
                    telegram=telegram,
                    require_auth=require_auth,
                    config=config,
                    content_automation=content_automation,
                    workflow_snapshot=workflow_snapshot,
                    TELETHON_AVAILABLE=TELETHON_AVAILABLE,
                    _channel_profile_photo_cache=_channel_profile_photo_cache,
                    _channel_profile_photo_locks=_channel_profile_photo_locks,
                )
            )
        elif _module == "routes_automation":
            register_automation_routes(
                router,
                db=db,
                jobs=jobs,
                telegram=telegram,
                require_auth=require_auth,
                content_automation=content_automation,
                config=config,
                validate_automation_root=validate_automation_root,
                QBittorrentEngine=QBittorrentEngine,
                TorrentEngineError=TorrentEngineError,
                get_upload_policy=get_upload_policy,
                upload_policy_dict=upload_policy_dict,
            )
        elif _module == "routes_jobs":
            register_job_routes(
                router,
                db=db,
                governor=governor,
                jobs=jobs,
                telegram=telegram,
                require_auth=require_auth,
                resources=resources,
                metadata_channel_pipeline=metadata_channel_pipeline,
                transfer_lane_metrics=transfer_lane_metrics,
                validate_local_upload_folder=validate_local_upload_folder,
            )
        elif _module == "routes_metadata_assets":
            route_exports.update(
                register_metadata_assets_routes(
                    router,
                    db=db,
                    jobs=jobs,
                    config=config,
                    metadata_channel_pipeline=metadata_channel_pipeline,
                    ps=ps,
                    require_auth=require_auth,
                    resources=resources,
                    telegram=telegram,
                    tokens=tokens,
                )
            )
        elif _module == "routes_playback":
            route_exports.update(
                register_playback_routes(
                    router,
                    config=config,
                    db=db,
                    ps=ps,
                    resources=resources,
                    require_auth=require_auth,
                    _engine_message=route_exports["_engine_message"],
                    _get_engine=route_exports["_get_engine"],
                    _index_state=route_exports["_index_state"],
                    _index_status=route_exports["_index_status"],
                    _local_url=route_exports["_local_url"],
                    _playback_claim_from=route_exports["_playback_claim_from"],
                    _validated_snapshot_counts_sync=route_exports["_validated_snapshot_counts_sync"],
                    engine_lock=route_exports["engine_lock"],
                    index_tasks=route_exports["index_tasks"],
                    require_playback_allowed=route_exports["require_playback_allowed"],
                )
            )
        elif _module == "routes_tracks":
            route_exports.update(
                register_tracks_routes(
                    router,
                    config=config,
                    require_auth=require_auth,
                    _local_url=route_exports["_local_url"],
                    require_playback_allowed=route_exports["require_playback_allowed"],
                    db=db,
                )
            )
        elif _module == "routes_playback_compat":
            register_playback_compat_routes(
                router,
                config=config,
                db=db,
                ps=ps,
                resources=resources,
                require_auth=require_auth,
                tokens=tokens,
                _external_player_monitors=_external_player_monitors,
                _compat_info=route_exports["_compat_info"],
                _engine_message=route_exports["_engine_message"],
                _get_engine=route_exports["_get_engine"],
                _local_url=route_exports["_local_url"],
                _media_hw_profile=route_exports["_media_hw_profile"],
                _playback_claim_from=route_exports["_playback_claim_from"],
                _player_port=route_exports["_player_port"],
                _select_compat_profile=route_exports["_select_compat_profile"],
                require_playback_allowed=route_exports["require_playback_allowed"],
            )
        elif _module == "routes_settings":
            register_settings_routes(
                router,
                db=db,
                jobs=jobs,
                telegram=telegram,
                config=config,
                ps=ps,
                require_auth=require_auth,
                login_limiter=login_limiter,
                sessions=sessions,
                client_key=client_key,
                session_from_ws=session_from_ws,
                status_snapshot=status_snapshot,
                fts_prefix_query=fts_prefix_query,
                SETUP_COMPLETE_FLAG=SETUP_COMPLETE_FLAG,
                SETUP_REQUIRED_FLAG=SETUP_REQUIRED_FLAG,
                is_setup_required=is_setup_required,
                set_session_cookie=set_session_cookie,
                verify_password=verify_password,
                telegram_send_code=route_exports["telegram_send_code"],
                telegram_verify_code=route_exports["telegram_verify_code"],
                telegram_verify_2fa=route_exports["telegram_verify_2fa"],
            )
        elif _module == "routes_library":
            register_library_routes(
                router,
                db=db,
                jobs=jobs,
                telegram=telegram,
                config=config,
                require_auth=require_auth,
                _queue_catalogue_caption_sync=_queue_catalogue_caption_sync,
            )
        elif _module == "routes_entities":
            register_entity_routes(
                router,
                db=db,
                jobs=jobs,
                telegram=telegram,
                require_auth=require_auth,
                config=config,
                _proxy_entity_image=_proxy_entity_image,
                _queue_catalogue_caption_sync=_queue_catalogue_caption_sync,
                _sync_catalogue_caption=_sync_catalogue_caption,
            )
        elif _module == "routes_media":
            register_media_routes(
                router,
                db=db,
                config=config,
                jobs=jobs,
                require_auth=require_auth,
                SCENE_MATCH_FIELD_STATUS_CTE=SCENE_MATCH_FIELD_STATUS_CTE,
                _queue_catalogue_caption_sync=_queue_catalogue_caption_sync,
                _scene_match_review_unresolved_catalog=_scene_match_review_unresolved_catalog,
                _sync_catalogue_caption=_sync_catalogue_caption,
            )
        elif _module == "routes_entity_pages":
            register_entity_page_routes(
                router,
                db=db,
                jobs=jobs,
                require_auth=require_auth,
                _provider_page_url=route_exports["_provider_page_url"],
                tag_taxonomy_cache=_tag_taxonomy_cache,
            )
        elif _module == "routes_tags_logs":
            register_tags_logs_routes(
                router,
                config=config,
                db=db,
                jobs=jobs,
                fts_prefix_query=fts_prefix_query,
                page_enricher=page_enricher,
                ps=ps,
                require_auth=require_auth,
                resources=resources,
                telegram=telegram,
            )
        else:
            raise RuntimeError(f"Unregistered route group: {_module}")
    # This route group already has an explicit registration interface. Keep it
    # outside the shared exec namespace; its paths do not overlap the ordered
    # route modules above.
    from .routes_experience import register as register_experience_routes
    register_experience_routes(
        router,
        db=db,
        require_auth=require_auth,
        resources=resources,
        playback_state=ps,
    )
    # The catch-all that serves the React app is registered by the app factory, after the AI assistant and test bridge routes.
    return router
