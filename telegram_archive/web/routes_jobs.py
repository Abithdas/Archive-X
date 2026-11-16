"""Explicit registration for job, transfer, suggestion, and Media Manager routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Jobs and resource controls
#       - Job controls
#       - Scan job starters
#       - Telegram Media Manager
#       - Telegram transfers
#       - Metadata & Assets preflight
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json

from fastapi import APIRouter, HTTPException, Request


def register_job_routes(
    router: APIRouter,
    *,
    db,
    governor,
    jobs,
    telegram,
    require_auth,
    resources,
    metadata_channel_pipeline,
    transfer_lane_metrics,
    validate_local_upload_folder,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Jobs and resource controls
    # Job creation, resources, recovery and pipelines.
    # ------------------------------------------------------------------
    """Register job, transfer, suggestion and Media Manager routes."""
    @router.post("/api/jobs")
    async def create_job(request: Request):
        """Start Metadata match or Upload Local Files (one active job each)."""
        require_auth(request)
        data = await request.json()
        job_type = str(data.get("job_type") or "")
        if job_type not in ("METADATA_SYNC", "UPLOAD_LOCAL"):
            raise HTTPException(400, "Unknown module")
        if jobs.is_active(job_type):
            raise HTTPException(409, "This module is already running — see Active jobs")
        source = str(data.get("source_channel") or data.get("source", ""))
        destination = str(data.get("destination_channel") or data.get("destination", ""))
        detail = str(data.get("detail", ""))
        depends_on = data.get("depends_on") or data.get("dependencies") or []
        if isinstance(depends_on, str):
            depends_on = [x.strip() for x in depends_on.split(",") if x.strip()]
        priority = data.get("priority")
        max_retries = data.get("max_retries", 3)
        deadline_at = data.get("deadline_at")
        resource_requirements = data.get("resource_requirements") or {}
        if job_type == "UPLOAD_LOCAL":
            try:
                payload = json.loads(detail or "{}")
            except Exception:
                payload = {}
            folder = str(payload.get("folder") or "").strip()
            if not folder:
                raise HTTPException(400, "Local Upload requires a source folder.")
            try:
                validate_local_upload_folder(db, folder)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
        jid = db.create_job(job_type, source, destination, False, detail=detail, depends_on=depends_on, priority=priority, max_retries=max_retries, deadline_at=deadline_at, resource_requirements=resource_requirements)
        jobs.submit(jid)
        return {"id": jid}

    @router.get("/api/operations/resources")
    def resource_control_status(request: Request):
        require_auth(request)
        return {"ok": True, "resources": resources.snapshot() if resources is not None else {}, "resource_leases": db.resource_lease_snapshot(), "execution_lanes": jobs.execution_lane_capacities() if hasattr(jobs, "execution_lane_capacities") else {}, "governor": governor.status()}

    # Apply and persist resource control changes.
    @router.post("/api/operations/resources")
    async def resource_control_update(request: Request):
        require_auth(request)
        if resources is None:
            raise HTTPException(503, "Resource controller is unavailable")
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(400, "Invalid resource control payload")
        mode = data.get("mode")
        limits = data.get("limits")
        gate = data.get("playback_gate")
        expires = data.get("expires_seconds")
        label = data.get("label") or ""
        background_paused = data.get("background_paused")
        try:
            if mode is not None:
                mode = str(mode).lower().strip()
                if mode not in {"automatic", "playback", "balanced", "background", "manual"}:
                    raise ValueError("Invalid resource control mode")
            clean_limits = None
            if limits is not None:
                if not isinstance(limits, dict):
                    raise ValueError("limits must be an object")
                clean_limits = {k: int(v) for k, v in limits.items() if k in {"conversion","thumbnail","preview","metadata","upload"}}
            resources.configure(mode=mode, limits=clean_limits, playback_gate=gate, expires_seconds=expires, label=label)
            if background_paused is not None:
                if bool(background_paused):
                    resources.pause_background(label or "Paused by operator")
                else:
                    resources.resume_background()
            control = resources.status()
            # Temporary overrides are intentionally session-scoped. A reboot should
            # return to the last safe persisted policy rather than resurrecting an
            # expired/manual override unexpectedly.
            persisted_mode = "automatic" if control.get("override_seconds_remaining") else control["mode"]
            db.set_setting("ax_resource_control_mode", persisted_mode)
            db.set_setting("ax_resource_manual_limits", control["manual_limits"])
            db.set_setting("ax_resource_playback_gate", control["playback_gate"])
            db.log(f"Resource control changed: {control['mode_label']}")
            return {"ok": True, "resources": resources.snapshot(), "resource_leases": db.resource_lease_snapshot(), "governor": governor.status()}
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))

    @router.get("/api/jobs/recovery")
    async def recovery_status(request: Request):
        require_auth(request)
        return {"ok": True, "recovery": jobs.recovery_snapshot()}

    @router.post("/api/jobs/{job_id}/retry")
    async def retry_job(job_id: str, request: Request):
        require_auth(request)
        job=db.get_job(job_id)
        if not job: raise HTTPException(404, "Job not found")
        if job.get("status") not in {"FAILED","CANCELLED","INTERRUPTED"}: raise HTTPException(409, "Job is not retryable from its current state")
        if job.get("job_type") in {"ASSET_PREFETCH", "PREVIEW_PREFETCH"}:
            jobs.clear_maintenance_suppression(job.get("job_type"), reason="Operator explicitly retried maintenance")
        if job.get("status")=="INTERRUPTED": return await jobs.recover_job(job_id)
        db.update_job(job_id,status="QUEUED",error="",current_operation="Retry queued")
        jobs.submit(job_id)
        jobs.recovery.record(job_id,"RETRY_QUEUED",status="QUEUED",operation="Retry",payload={"source":"api"})
        return {"ok":True,"status":"QUEUED"}

    @router.get("/api/jobs/pipelines")
    async def job_pipelines(request: Request):
        """GET /api/jobs/pipelines: Job pipelines."""
        require_auth(request)
        return {"ok": True, "pipelines": jobs.pipeline_snapshot()}


    # ------------------------------------------------------------------
    # SUBSECTION: Job controls
    # Pause, resume, recover, skip and cancel.
    # ------------------------------------------------------------------
    @router.post("/api/jobs/{job_id}/pause")
    def pause_job(job_id: str, request: Request):
        require_auth(request)
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.get("job_type") not in {
            "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC", "THUMBNAIL_INDEXER",
            "METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "UPLOAD_LOCAL", "MEDIA_MANAGER", "TELEGRAM_TRANSFER",
            "ASSET_PREFETCH", "PREVIEW_PREFETCH", "CONTENT_AUTOMATION", "BACKUP_CREATE",
        }:
            raise HTTPException(409, "This job type does not support pause/resume controls")
        if job.get("status") in {"INTERRUPTED", "RECOVERING"}:
            raise HTTPException(409, "Interrupted/recovering jobs cannot be paused. Use Recover & Resume or Stop.")
        try:
            status = jobs.pause_job(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True, "status": status}

    @router.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: str, request: Request):
        require_auth(request)
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        if job.get("job_type") not in {
            "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC", "THUMBNAIL_INDEXER",
            "METADATA_ASSETS", "METADATA_CHANNEL_PIPELINE", "UPLOAD_LOCAL", "MEDIA_MANAGER", "TELEGRAM_TRANSFER",
            "ASSET_PREFETCH", "PREVIEW_PREFETCH", "CONTENT_AUTOMATION", "BACKUP_CREATE",
        }:
            raise HTTPException(409, "This job type does not support pause/resume controls")
        try:
            status = jobs.resume_job(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True, "status": status}

    @router.post("/api/jobs/{job_id}/recover")
    async def recover_job(job_id: str, request: Request):
        require_auth(request)
        try:
            return await jobs.recover_job(job_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post("/api/jobs/{job_id}/skip-current")
    def skip_job_current(job_id: str, request: Request):
        require_auth(request)
        job = db.get_job(job_id)
        if not job or job.get("job_type") != "UPLOAD_LOCAL":
            raise HTTPException(404, "Upload job not found")
        jobs.skip_current(job_id)
        return {"ok": True, "status": "SKIP_REQUESTED", "current_item": job.get("current_item") or ""}

    @router.post("/api/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str, request: Request):
        """POST /api/jobs/{job_id}/cancel: Cancel job."""
        require_auth(request)
        try:
            status = jobs.cancel(job_id)
        except ValueError as exc:
            raise HTTPException(409 if str(exc) != "Job not found" else 404, str(exc))
        from ..media_manager.runner import RUNNER
        if RUNNER.job_id == job_id and RUNNER.running:
            RUNNER.stop()
        latest = db.get_job(job_id) or {}
        return {"ok": True, "status": str(latest.get("status") or status), "stopping": str(latest.get("status") or status) == "CANCEL_REQUESTED"}


    # ------------------------------------------------------------------
    # SUBSECTION: Scan job starters
    # Suggestion scan and metadata sync.
    # ------------------------------------------------------------------
    @router.post("/api/suggestions/scan")
    async def suggestion_scan_job(request: Request):
        """Run the unified Metadata & Suggestion Scanner.

        Any combination of channel suggestion scan, performer recheck and site
        recheck can be submitted as one job. The engine deduplicates media IDs
        and processes each video once; performer/site scopes take precedence over
        channel-only suggestion behavior for overlapping videos.
        """
        require_auth(request)
        data = await request.json()
        raw_scopes = data.get("scopes")
        scopes = []
        if isinstance(raw_scopes, list):
            for value in raw_scopes:
                mode = str(value or "").strip().lower()
                if mode in ("channel", "performer", "site") and mode not in scopes:
                    scopes.append(mode)

        # v18.44.34 compatibility: accept the previous single-operation payload.
        if not scopes:
            legacy_mode = str(data.get("mode") or "channel").strip().lower()
            if legacy_mode == "performer":
                scopes = ["performer"]
            elif legacy_mode == "site":
                scopes = ["site"]
            elif legacy_mode in ("performer_recheck", "site_recheck"):
                scopes = ["performer" if legacy_mode == "performer_recheck" else "site"]
            else:
                scopes = ["channel"]

        try:
            limit = max(25, min(5000, int(data.get("limit", 50))))
        except Exception:
            limit = 50
        unbounded = bool(data.get("unbounded", False))
        if unbounded:
            limit = 0
        force_recheck = bool(data.get("force_recheck", False))

        channel_ids = []
        raw_channels = data.get("channel_ids")
        if raw_channels is None:
            raw_channels = [data.get("channel_id") or data.get("source_channel")]
        if not isinstance(raw_channels, list):
            raw_channels = [raw_channels]
        for value in raw_channels:
            text = str(value or "").strip()
            if text and text not in channel_ids:
                channel_ids.append(text)

        def parse_ids(key, singular_key):
            raw = data.get(key)
            if raw is None:
                raw = [data.get(singular_key)]
            if not isinstance(raw, list):
                raw = [raw]
            out = []
            for value in raw:
                if str(value or '').strip().lower() == 'all':
                    out.append('all')
                    continue
                try:
                    iv = int(value)
                except Exception:
                    _swallowed_note("routes_jobs.register_job_routes.suggestion_scan_job.parse_ids")
                    continue
                if iv > 0 and iv not in out:
                    out.append(iv)
            return out

        performer_ids = parse_ids("performer_ids", "performer_id")
        site_ids = parse_ids("site_ids", "site_id")

        # Expand the special UI-level "all" selection into concrete IDs once,
        # keeping the job payload explicit and deterministic for the worker.
        with db.lock:
            if 'all' in performer_ids:
                performer_ids = [int(r[0]) for r in db.conn.execute("SELECT id FROM performers ORDER BY id").fetchall()]
            if 'all' in site_ids:
                site_ids = [int(r[0]) for r in db.conn.execute("SELECT id FROM studios ORDER BY id").fetchall()]

        if "channel" in scopes:
            if not channel_ids:
                raise HTTPException(400, "Select at least one source channel")
            with db.lock:
                marks = ",".join("?" for _ in channel_ids)
                rows = db.conn.execute(
                    f"SELECT telegram_id FROM channels WHERE telegram_id IN ({marks})", channel_ids
                ).fetchall()
            available = {str(r[0]) for r in rows}
            missing = [value for value in channel_ids if value not in available]
            if missing:
                raise HTTPException(404, "One or more selected channels are not available")

        if "performer" in scopes:
            if not performer_ids:
                raise HTTPException(400, "Select at least one performer")
            with db.lock:
                marks = ",".join("?" for _ in performer_ids)
                rows = db.conn.execute(
                    f"SELECT id FROM performers WHERE id IN ({marks})", performer_ids
                ).fetchall()
            available = {int(r[0]) for r in rows}
            if any(value not in available for value in performer_ids):
                raise HTTPException(404, "One or more selected performers were not found")

        if "site" in scopes:
            if not site_ids:
                raise HTTPException(400, "Select at least one site")
            with db.lock:
                marks = ",".join("?" for _ in site_ids)
                rows = db.conn.execute(
                    f"SELECT id FROM studios WHERE id IN ({marks})", site_ids
                ).fetchall()
            available = {int(r[0]) for r in rows}
            if any(value not in available for value in site_ids):
                raise HTTPException(404, "One or more selected sites were not found")

        detail = json.dumps({
            "mode": "unified",
            "scopes": scopes,
            "channel_ids": channel_ids,
            "performer_ids": performer_ids,
            "site_ids": site_ids,
            "limit": limit,
            "unbounded": unbounded,
            "force_recheck": force_recheck,
        })
        job_source = channel_ids[0] if len(channel_ids) == 1 else ""

        if jobs.is_active("SUGGESTION_SCAN"):
            active = db.active_jobs("SUGGESTION_SCAN")
            return {"ok": True, "already_running": True, "id": active[0]["id"] if active else None}

        jid = db.create_job("SUGGESTION_SCAN", job_source, "", False, detail=detail)
        jobs.submit(jid)
        return {
            "ok": True, "id": jid, "scopes": scopes, "limit": limit,
            "channel_ids": channel_ids or None,
            "performer_ids": performer_ids or None,
            "site_ids": site_ids or None,
        }

    @router.post("/api/metadata/sync")
    async def metadata_sync_job(request: Request):
        """POST /api/metadata/sync: Metadata sync job."""
        require_auth(request)
        data = await request.json()
        raw_sources = data.get("source_channels")
        sources = []
        if isinstance(raw_sources, list):
            for value in raw_sources:
                text = str(value or "").strip()
                if text and text not in sources:
                    sources.append(text)
        if not sources:
            source = str(data.get("source_channel") or data.get("channel_id") or "").strip()
            if source:
                sources = [source]
        # Full-channel work must be explicit; normal operator requests are bounded.
        try:
            limit = max(25, min(5000, int(data.get("limit", 50))))
        except Exception:
            limit = 50
        unbounded = bool(data.get("unbounded", False))
        if unbounded:
            limit = 0
        force = bool(data.get("force", False))
        if not sources:
            raise HTTPException(400, "Select at least one source channel")
        detail = json.dumps({"limit": limit, "unbounded": unbounded, "force": force, "source_channels": sources})
        # Coalesce: channels asked for while a sync is waiting are added to it;
        # while one is running, at most ONE follow-up job waits behind it.
        from ..jobs.manager import merge_metadata_detail
        queued = [j for j in db.active_jobs("METADATA_SYNC") if j["status"] == "QUEUED"]
        if queued:
            merged = merge_metadata_detail(queued[0], [{"detail": detail}])
            db.update_job(queued[0]["id"], detail=merged)
            return {"ok": True, "id": queued[0]["id"], "merged": True,
                    "source_channels": json.loads(merged)["source_channels"]}
        running = bool(db.active_jobs("METADATA_SYNC"))
        jid = db.create_job("METADATA_SYNC", "", "", False, detail=detail)
        jobs.submit(jid)
        return {"ok": True, "id": jid, "queued_behind_running": running, "limit": limit,
                "force": force, "source_channels": sources}

    # ---------------- Telegram Media Manager ----------------
    from ..media_manager.runner import RUNNER as media_manager, MODES as MM_MODES


    # ------------------------------------------------------------------
    # SUBSECTION: Telegram Media Manager
    # Media Manager state and input.
    # ------------------------------------------------------------------
    @router.get("/api/media-manager")
    async def media_manager_state(request: Request):
        """GET /api/media-manager: Media manager state."""
        require_auth(request)
        try:
            since = int(request.query_params.get("since", "0") or 0)
        except ValueError:
            since = 0
        snap = media_manager.snapshot(since)
        snap["modes"] = [{"id": k, "name": v[0], "description": v[1]} for k, v in MM_MODES.items()]
        return snap

    @router.get("/api/media-manager/preflight")
    async def media_manager_preflight(request: Request):
        """GET /api/media-manager/preflight: Media manager preflight."""
        require_auth(request)
        from ..core.media_deps import preflight
        return await asyncio.to_thread(preflight)

    @router.post("/api/media-manager/start")
    async def media_manager_start(request: Request):
        """POST /api/media-manager/start: Media manager start."""
        require_auth(request)
        data = await request.json()
        mode = str(data.get("mode") or "")
        if mode not in MM_MODES:
            raise HTTPException(400, "Choose a workflow")
        if media_manager.running or jobs.is_active("MEDIA_MANAGER"):
            raise HTTPException(409, "Telegram Media Manager is already running")
        if not telegram.connected:
            raise HTTPException(503, "Telegram is not connected")
        jid = db.create_job("MEDIA_MANAGER", "", "", True, detail=json.dumps({"mode": mode}))
        jobs.submit(jid)
        return {"ok": True, "id": jid}

    @router.post("/api/media-manager/input")
    async def media_manager_input(request: Request):
        """POST /api/media-manager/input: Media manager input."""
        require_auth(request)
        data = await request.json()
        if not media_manager.answer(str(data.get("text") or "")):
            raise HTTPException(409, "The Telegram Media Manager is not waiting for an answer")
        return {"ok": True}

    @router.post("/api/media-manager/stop")
    async def media_manager_stop(request: Request):
        """POST /api/media-manager/stop: Media manager stop."""
        require_auth(request)
        if media_manager.job_id:
            jobs.cancel(media_manager.job_id)
        for active in db.active_jobs("MEDIA_MANAGER"):
            jobs.cancel(active["id"])
        media_manager.stop()
        return {"ok": True}


    # ------------------------------------------------------------------
    # SUBSECTION: Telegram transfers
    # Transfer lanes, photo scan and control.
    # ------------------------------------------------------------------
    @router.get("/api/telegram-transfer/lanes")
    async def telegram_transfer_lanes(request: Request):
        """Live transfer/scan lane progress and shared resource occupancy."""
        require_auth(request)
        relevant = {
            "TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER",
            "METADATA_CHANNEL_PIPELINE", "METADATA_ASSETS", "CONTENT_AUTOMATION",
        }
        def build_rows():
            rows = []
            lane_jobs = [job for job in db.active_jobs() if str(job.get("job_type") or "") in relevant]
            telemetry_by_job = db.get_job_transfer_telemetry([job["id"] for job in lane_jobs]) if lane_jobs else {}
            with db.lock:
                channel_names = {str(row["telegram_id"]): str(row["title"] or row["telegram_id"])
                                 for row in db.conn.execute("SELECT telegram_id,title FROM channels").fetchall()}
            for job in lane_jobs:
                job_type = str(job.get("job_type") or "")
                if job_type not in relevant:
                    continue
                detail = {}
                try:
                    detail = json.loads(job.get("detail") or "{}")
                    if not isinstance(detail, dict): detail = {}
                except Exception:
                    detail = {}
                try:
                    completed = int(job.get("completed") or 0)
                    total = int(job.get("total") or 0)
                except (TypeError, ValueError):
                    completed, total = 0, 0
                progress = float(job.get("progress") or (100.0 * completed / total if total else 0.0))
                if not 0 <= progress <= 100: progress = max(0.0, min(100.0, progress))
                rows.append({
                    "id": str(job.get("id") or ""), "type": job_type,
                    "status": str(job.get("status") or ""),
                    "operation": str(job.get("current_operation") or detail.get("stage") or "Queued"),
                    "current_item": str(job.get("current_item") or detail.get("current_item") or ""),
                    "completed": completed, "total": total, "progress": round(progress, 1),
                    "source": str(detail.get("source") or ""),
                    "workflow": str(detail.get("workflow") or ""),
                    "source_channel": channel_names.get(str(job.get("source_channel") or ""), str(job.get("source_channel") or "")),
                    "destination_channel": channel_names.get(str(job.get("destination_channel") or ""), str(job.get("destination_channel") or "")),
                    "metrics": transfer_lane_metrics(job, telemetry_by_job.get(str(job.get("id") or ""), {})),
                })
            return rows
        rows = await asyncio.to_thread(build_rows)   # SQLite reads and the db lock must not run on the event loop
        resource_snapshot = resources.snapshot() if resources is not None else {}
        try:
            governor_snapshot = governor.snapshot()
        except Exception:
            governor_snapshot = {}
        return {"ok": True, "jobs": rows, "resources": resource_snapshot,
                "governor": governor_snapshot}

    @router.post("/api/telegram-transfer/scan-photos")
    async def telegram_transfer_scan_photos(request: Request):
        """Read-only source-folder preflight for the image-document upload workflow."""
        require_auth(request)
        data = await request.json()
        folder_value = str(data.get("folder") or "").strip()
        if not folder_value:
            raise HTTPException(400, "Enter a local photo source folder")
        from pathlib import Path as _Path
        folder = _Path(folder_value).expanduser()
        if not folder.is_dir():
            raise HTTPException(400, f"Photo source folder does not exist: {folder}")
        try:
            validate_local_upload_folder(db, str(folder))
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        from ..engines.photo_uploader import scan_photo_folder
        from asyncio import to_thread as _to_thread
        return await _to_thread(scan_photo_folder, folder)

    @router.post("/api/telegram-transfer/start")
    async def telegram_transfer_start(request: Request):
        """Canonical start endpoint for the single Telegram Transfer Manager workflow."""
        require_auth(request)
        data = await request.json()
        source = str(data.get("source") or "").strip().casefold()
        if source not in {"local", "telegram"}:
            raise HTTPException(400, "Choose Local Files or Telegram Media")
        media_type = str(data.get("media_type") or "VIDEO").strip().upper()
        if media_type not in {"VIDEO", "PHOTOS"}:
            raise HTTPException(400, "Unsupported local media type")
        if source == "telegram" and media_type != "VIDEO":
            raise HTTPException(400, "Photo uploads require a local source folder")
        from ..core.resolution_filter import normalize_resolution_filter
        try:
            resolution_filter = normalize_resolution_filter(data.get("resolution_filter"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        from ..core.duration_filter import normalize_max_minutes
        try:
            max_duration_minutes = normalize_max_minutes(data.get("max_duration_minutes"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not telegram.connected:
            raise HTTPException(503, "Telegram is not connected")
        # Different workflows occupy independent scheduler lanes. The durable
        # execution lease still serializes an identical lane and the shared
        # Telegram governor caps aggregate background resource use.

        detail = {
            "source": source, "workflow": "PROCESS",
            "preparation": "METADATA_ASSETS_FIRST", "auto_confirm": True,
            "resolution_filter": resolution_filter,
            "media_type": media_type,
        }
        if max_duration_minutes is not None and media_type == "VIDEO":
            detail["max_duration_minutes"] = max_duration_minutes
        destination = ""
        if source == "local":
            folder = str(data.get("folder") or "").strip()
            destination = str(data.get("destination_channel") or "").strip()
            if not folder:
                raise HTTPException(400, "Enter a local source folder")
            if not destination:
                raise HTTPException(400, "Choose a destination channel")
            try:
                validate_local_upload_folder(db, folder)
            except ValueError as exc:
                raise HTTPException(409, str(exc))
            from ..core.upload_limits import normalize_oversized_file_policy
            try:
                oversized_file_policy = normalize_oversized_file_policy(
                    data.get("oversized_file_policy"),
                    legacy_split_upload=bool(data.get("split_upload", False)),
                    default="optimize",
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            local_orientation = str(data.get("orientation") or "BOTH").upper()
            if local_orientation not in {"HORIZONTAL", "VERTICAL", "BOTH"}:
                raise HTTPException(400, "Invalid video orientation")
            detail.update({
                "orientation": local_orientation,
                "folder": folder,
                "destination_channel": destination,
                "include_subfolders": bool(data.get("include_subfolders", False)),
                "delete_local_after_delivery": bool(data.get("delete_local_after_delivery", True)),
                "oversized_file_policy": oversized_file_policy,
                "split_upload": oversized_file_policy == "split",
                "source_mode": "LOCAL",
                "destination_mode": "TELEGRAM",
            })
            job_type = "UPLOAD_LOCAL"
            jid = db.create_job(job_type, "", destination, False, detail=json.dumps(detail))
        else:
            raw_sources = data.get("source_channels")
            if not isinstance(raw_sources, list):
                raw_sources = []
            source_channels=[]
            for value in raw_sources:
                value=str(value or "").strip()
                if value and value not in source_channels: source_channels.append(value)
            destination=str(data.get("destination_channel") or "").strip()
            if not source_channels: raise HTTPException(400,"Select at least one source channel")
            if not destination: raise HTTPException(400,"Choose a destination channel")
            if destination in source_channels: raise HTTPException(400,"Destination cannot be a source channel")
            source_mode=str(data.get("source_mode") or "VIDEO").upper()
            destination_mode=str(data.get("destination_mode") or "VIDEO").upper()
            orientation=str(data.get("orientation") or "BOTH").upper()
            if source_mode not in {"VIDEO","FILE"} or destination_mode not in {"VIDEO","FILE"}: raise HTTPException(400,"Invalid media format")
            if orientation not in {"HORIZONTAL","VERTICAL","BOTH"}: raise HTTPException(400,"Invalid video orientation")
            dup=data.get("duplicate_check") if isinstance(data.get("duplicate_check"),dict) else {}
            cleanup=data.get("cleanup") if isinstance(data.get("cleanup"),dict) else {}
            cleanup_enabled=bool(cleanup.get("enabled")); trash=str(cleanup.get("trash_channel") or "").strip()
            if cleanup_enabled and not trash: raise HTTPException(400,"Choose a trash channel when cleanup is enabled")
            snapshot_policy = str(data.get("snapshot_policy") or "existing").strip().lower()
            if snapshot_policy not in {"existing", "generate"}: raise HTTPException(400, "Invalid snapshot policy")
            requested_mode = str(data.get("mode") or "").strip().upper()
            from ..media_manager.runner import MODES as MEDIA_MANAGER_MODES
            detail["mode"] = requested_mode if requested_mode in MEDIA_MANAGER_MODES else "TRANSFER"
            detail.update({
                "source_channels": source_channels,
                "destination_channel": destination,
                "source_mode": source_mode,
                "destination_mode": destination_mode,
                "orientation": orientation,
                "duplicate_check": {"source":bool(dup.get("source",True)),"destination":bool(dup.get("destination",True)),"database":bool(dup.get("database",True))},
                "cleanup": {"enabled":cleanup_enabled,"trash_channel":trash},
                "snapshot_policy": snapshot_policy,
                "source_count": len(source_channels),
                "processing_policy": "INVENTORY_THEN_METADATA_ASSETS_THEN_TRANSFER",
            })
            job_type="TELEGRAM_TRANSFER"
            jid=db.create_job(job_type, ",".join(source_channels), destination, True, detail=json.dumps(detail))
        jobs.submit(jid)
        return {"ok":True,"id":jid,"source":source,"workflow":"PROCESS"}

    @router.get("/api/telegram-transfer")
    async def telegram_transfer_state(request: Request):
        """State for the canonical Telegram Transfer Manager console."""
        require_auth(request)
        try:
            since = int(request.query_params.get("since", "0") or 0)
        except ValueError:
            since = 0
        snap = media_manager.snapshot(since)
        snap["transfer_job_active"] = jobs.transfer_active()
        snap["modes"] = [{"id": k, "name": v[0], "description": v[1]} for k, v in MM_MODES.items()]
        return snap

    @router.post("/api/telegram-transfer/input")
    async def telegram_transfer_input(request: Request):
        require_auth(request)
        data = await request.json()
        if not media_manager.answer(str(data.get("text") or "")):
            raise HTTPException(409, "The Telegram Transfer Manager is not waiting for an answer")
        return {"ok": True}

    @router.post("/api/telegram-transfer/stop")
    async def telegram_transfer_stop(request: Request):
        require_auth(request)
        if media_manager.job_id:
            jobs.cancel(media_manager.job_id)
        # Cancel the persisted active transfer lanes as well. This makes Stop
        # authoritative even when the interactive runner thread already exited
        # but its JobManager row has not yet transitioned out of RUNNING.
        for job_type in ("TELEGRAM_TRANSFER", "MEDIA_MANAGER"):
            for active in db.active_jobs(job_type):
                jobs.cancel(active["id"])
        media_manager.stop()
        return {"ok": True}


    # ------------------------------------------------------------------
    # SUBSECTION: Metadata & Assets preflight
    # Preflight check.
    # ------------------------------------------------------------------
    @router.post("/api/metadata-assets/preflight")
    async def metadata_assets_preflight(request: Request):
        """Validate persisted prerequisites before starting the unified manager."""
        require_auth(request)
        data = await request.json()
        raw_channels = data.get("channel_ids")
        if raw_channels is None:
            raw_channels = [data.get("channel_id")]
        channels = []
        for value in raw_channels or []:
            channel = str(value or "").strip()
            if channel and channel not in channels:
                channels.append(channel)
        if not channels:
            raise HTTPException(400, "Select at least one channel")

        aliases = {
            "inventory": "inventory", "metadata": "metadata", "metadata_match": "metadata",
            "performers": "performers", "performer_data": "performers",
            "channel_data": "channel_data", "related_entities": "channel_data",
            "snapshots": "snapshots", "playback_snapshots": "snapshots",
        }
        stages = []
        for value in data.get("stages") or []:
            canonical = aliases.get(str(value or "").strip().casefold())
            if canonical and canonical not in stages:
                stages.append(canonical)
        if not stages:
            raise HTTPException(400, "Select at least one processing stage")
        return metadata_channel_pipeline.dependency_plan(db, channels, stages)
