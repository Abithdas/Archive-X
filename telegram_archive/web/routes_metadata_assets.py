"""Explicit registration for Metadata & Assets and channel-index routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Metadata & Assets jobs
#       - Playback helpers
#       - Channel catalogue helpers
#       - Channel index
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import io
import json
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request


def _channel_ids(raw, *, strict: bool = True) -> list[str]:
    """Telegram channel ids from a request: a list, or one id sent as a bare string or number. Duplicates and blanks are dropped.

    A bare string used to be walked character by character ("-100123" became the channels "-", "1", "0", ...), and a name that is not a
    number got as far as the job, which then failed with an unhelpful error. `strict` refuses non-numbers; the lenient form skips them.
    """
    if raw is None:
        return []
    if isinstance(raw, (str, int)) and not isinstance(raw, bool):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise HTTPException(400, "Channel ids must be a list of numbers")
    out: list[str] = []
    for value in raw:
        text = str(value if value is not None else "").strip()
        if not text:
            continue
        try:
            number = int(text)
        except (TypeError, ValueError):
            if strict:
                raise HTTPException(400, f"Channel ids must be numbers, not {text[:40]!r}")
            continue
        if number and str(number) not in out:
            out.append(str(number))
    return out


def register_metadata_assets_routes(
    router: APIRouter,
    *,
    db,
    jobs,
    config,
    metadata_channel_pipeline,
    ps,
    require_auth,
    resources,
    telegram,
    tokens,
) -> dict[str, object]:

    # ------------------------------------------------------------------
    # SUBSECTION: Metadata & Assets jobs
    # Progress and start routes.
    # ------------------------------------------------------------------
    """Register Metadata & Assets and channel index routes."""
    @router.get("/api/metadata-assets/job/{job_id}")
    def metadata_assets_job_progress(job_id: str, request: Request):
        """Return the hierarchical channel/stage progress for one Metadata & Assets job."""
        require_auth(request)
        job = db.get_job(job_id)
        if not job or job.get("job_type") != "METADATA_CHANNEL_PIPELINE":
            raise HTTPException(404, "Metadata & Assets job not found")
        snapshot = metadata_channel_pipeline.progress_snapshot(job.get("detail"))
        return {
            "ok": True,
            "job_id": str(job_id),
            "status": job.get("status") or "",
            "error": job.get("error") or "",
            "operation": job.get("current_operation") or "",
            "current_item": job.get("current_item") or "",
            "updated_at": job.get("updated_at") or "",
            "progress": snapshot,
        }

    @router.post("/api/metadata-assets/process-channel")
    async def metadata_assets_process_channel(request: Request):
        """Run the unified channel-phased Metadata & Assets workflow.

        One job owns the selected channels and selected stages. Channels are the
        outer queue; stages are the inner ordered phases. The explicit combined
        older-message/caption reconciliation option also enables Inventory because
        that option cannot reconcile old Telegram history without it.
        """
        require_auth(request)
        data = await request.json()
        raw_channels = data.get("channel_ids")
        if raw_channels is None:
            raw_channels = [data.get("channel_id")]
        channels = _channel_ids(raw_channels)
        if not channels:
            raise HTTPException(400, "Select at least one channel")

        stage_aliases = {
            "inventory": "inventory",
            "metadata": "metadata",
            "metadata_match": "metadata",
            "performers": "performers",
            "performer_data": "performers",
            "channel_data": "channel_data",
            "related_entities": "channel_data",
            "snapshots": "snapshots",
            "playback_snapshots": "snapshots",
        }
        stages = []
        for value in data.get("stages") or []:
            canonical = stage_aliases.get(str(value or "").strip().casefold())
            if canonical and canonical not in stages:
                stages.append(canonical)
        if not stages:
            raise HTTPException(400, "Select at least one processing stage")

        # The merged full-history option also reconciles known uploaded captions
        # in this same Metadata & Assets job. It must include Inventory; otherwise
        # the option could appear selected while no older Telegram history is read.
        # The two request keys are aliases for the one combined user action.
        full_inventory = bool(data.get("full_inventory", False) or data.get("reconcile_uploaded_captions", False))
        reconcile_uploaded_captions = full_inventory
        if full_inventory and "inventory" not in stages:
            stages.insert(0, "inventory")

        dependency_plan = metadata_channel_pipeline.dependency_plan(db, channels, stages)
        if not dependency_plan["ready"]:
            labels = {"inventory": "Inventory", "metadata": "Metadata Match"}
            names = ", ".join(labels[x] for x in dependency_plan["required_stages"])
            raise HTTPException(409, detail={
                "code": "METADATA_ASSETS_PREREQUISITES_REQUIRED",
                "message": f"{names} must be selected before the requested stages can run for all selected channels.",
                "required_stages": dependency_plan["required_stages"],
                "missing_by_channel": dependency_plan["missing_by_channel"],
            })

        requested_channel_set = {str(x) for x in channels}
        active = db.active_jobs("METADATA_CHANNEL_PIPELINE")
        for existing in active:
            try:
                detail_existing = json.loads(existing.get("detail") or "{}")
            except Exception:
                detail_existing = {}
            current_channels = {str(x) for x in (detail_existing.get("channel_ids") or [existing.get("source_channel")]) if str(x or "")}
            overlap = requested_channel_set & current_channels
            if overlap:
                return {"ok": True, "already_running": True, "id": existing.get("id"),
                        "message": f"A Metadata & Assets job already covers channel(s): {', '.join(sorted(overlap))}."}
        try:
            limit = max(0, min(5000, int(data.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        unbounded = bool(data.get("unbounded", False))
        if not unbounded and limit == 0:
            limit = 50
        detail = {
            "channel_ids": channels,
            "channel_id": channels[0],
            "stages": stages,
            "auto_added_prerequisites": {
                str(k): [str(v) for v in vals if str(v).strip()]
                for k, vals in (data.get("auto_added_prerequisites") if isinstance(data.get("auto_added_prerequisites"), dict) else {}).items()
                if str(k) in {"inventory", "metadata"} and isinstance(vals, list)
            },
            "limit": limit,
            "unbounded": unbounded,
            "force_metadata": bool(data.get("force_metadata", False)),
            "force_entity": bool(data.get("force_entity", False)),
            "visual_match": bool(data.get("visual_match", True)),   # the optional pHash scan: off skips the visual fingerprint and its provider lookups
            "full_inventory": full_inventory,
            "reconcile_uploaded_captions": reconcile_uploaded_captions,
            "pending_only": False,
            "workflow": "channels_sequential_selected_stages",
            "current_channel_index": 0,
            "current_phase": "pending",
            "channel_queue": [
                {"channel_id": channel, "state": "PENDING", "videos": [], "phases": {}}
                for channel in channels
            ],
        }
        jid = db.create_job("METADATA_CHANNEL_PIPELINE", channels[0], "", False, detail=json.dumps(detail))
        jobs.submit(jid)
        return {"ok": True, "id": jid, "channel_ids": channels, "stages": stages,
                "workflow": "channels_sequential_selected_stages", "queued_channels": len(channels)}

    @router.post("/api/metadata-assets/process-pending")
    async def metadata_assets_process_pending(request: Request):
        """Resume catalogue items that are not yet Library-ready without a channel scan."""
        require_auth(request)
        data = await request.json()
        channels = _channel_ids(data.get("channel_ids") or data.get("channels") or [], strict=False)
        if not channels:
            raise HTTPException(400, "Select at least one Telegram channel")
        stage_aliases = {
            "metadata": "metadata", "metadata_match": "metadata",
            "performers": "performers", "performer_data": "performers",
            "channel_data": "channel_data", "related_entities": "channel_data",
            "snapshots": "snapshots", "playback_snapshots": "snapshots",
        }
        stages = []
        for value in data.get("stages") or []:
            phase = stage_aliases.get(str(value or "").strip().casefold())
            if phase and phase not in stages:
                stages.append(phase)
        if not stages:
            stages = ["metadata", "performers", "channel_data", "snapshots"]
        from ..core.resource_registry import ensure_resource_tables
        ensure_resource_tables(db)
        with db.reading() as conn:
            counts = {}
            for channel in channels:
                counts[channel] = int(conn.execute(
                    "SELECT COUNT(*) FROM archive_x_catalogued_media cm "
                    "WHERE cm.source_channel=? AND NOT EXISTS "
                    "(SELECT 1 FROM archive_x_published_media pm WHERE pm.id=cm.id)",
                    (channel,),
                ).fetchone()[0] or 0)
        active = db.active_jobs("METADATA_CHANNEL_PIPELINE")
        requested_channel_set = {str(x) for x in channels}
        for existing in active:
            try:
                detail_existing = json.loads(existing.get("detail") or "{}")
            except Exception:
                detail_existing = {}
            current_channels = {str(x) for x in (detail_existing.get("channel_ids") or [existing.get("source_channel")]) if str(x or "")}
            overlap = requested_channel_set & current_channels
            if overlap:
                return {"ok": True, "already_running": True, "id": existing.get("id"),
                        "message": f"A Metadata & Assets job already covers channel(s): {', '.join(sorted(overlap))}."}
        try:
            limit = max(0, min(5000, int(data.get("limit", 50))))
        except (TypeError, ValueError):
            limit = 50
        unbounded = bool(data.get("unbounded", False))
        if not unbounded and limit == 0:
            limit = 50
        # Snapshots-only is a filesystem integrity audit as well as a readiness
        # repair. Database-ready records still need inspection because their
        # representative/frame files may have been deleted or corrupted while
        # resource_state stayed READY. Queue selected channels even at 0 DB-pending.
        snapshot_audit = len(stages) == 1 and stages[0] == "snapshots"
        pending_channels = channels if snapshot_audit else [channel for channel in channels if counts.get(channel, 0) > 0]
        if not pending_channels:
            return {"ok": True, "no_pending": True,
                    "message": "All catalogued videos in the selected channels are Library-ready; no inventory scan was run.",
                    "pending_counts": counts}
        detail = {
            "channel_ids": pending_channels,
            "channel_id": pending_channels[0],
            "stages": stages,
            "limit": limit,
            "unbounded": unbounded,
            "force_metadata": bool(data.get("force_metadata", False)),
            "force_entity": bool(data.get("force_entity", False)),
            "visual_match": bool(data.get("visual_match", True)),   # the optional pHash scan: off skips the visual fingerprint and its provider lookups
            "pending_only": True,
            "snapshot_audit": snapshot_audit,
            "full_inventory": False,
            "workflow": "pending_items_no_inventory",
            "current_channel_index": 0,
            "current_phase": "pending",
            "channel_queue": [
                {"channel_id": channel, "state": "PENDING", "videos": [], "phases": {},
                 "counts": {"pending_at_start": counts[channel]}}
                for channel in pending_channels
            ],
        }
        jid = db.create_job("METADATA_CHANNEL_PIPELINE", pending_channels[0], "", False, detail=json.dumps(detail))
        jobs.submit(jid)
        return {"ok": True, "id": jid, "channel_ids": pending_channels, "stages": stages,
                "pending_counts": counts, "snapshot_audit": snapshot_audit,
                "message": ("Queued a local snapshot integrity audit; database-ready videos will also be checked for missing/corrupt files." if snapshot_audit else "Queued pending Library items."),
                "workflow": "pending_items_no_inventory", "queued_channels": len(pending_channels)}

    @router.post("/api/metadata-assets/start")
    async def metadata_assets_start(request: Request):
        """Start each selected Metadata & Assets stage as an independent job.

        The manager remains one user-facing surface, but selected stages use
        their own persisted execution lanes. This lets metadata, suggestions,
        entity data, and playback snapshots make progress concurrently while
        the resource scheduler still bounds each workload and playback keeps
        the highest transfer priority.
        """
        require_auth(request)
        data = await request.json()
        operations = []
        for value in data.get("operations") or []:
            op = str(value or "").strip().casefold()
            if op in {"metadata", "suggestions", "entity_data", "entity_images", "thumbnails"} and op not in operations:
                operations.append("entity_data" if op in {"entity_data", "entity_images"} else op)
        if not operations:
            raise HTTPException(400, "Select at least one Metadata & Assets operation")

        operation_job_types = {
            "metadata": "METADATA_SYNC",
            "suggestions": "SUGGESTION_SCAN",
            "entity_data": "ENTITY_IMAGE_SYNC",
            "thumbnails": "THUMBNAIL_INDEXER",
        }
        conflicts = [op for op in operations if jobs.is_active(operation_job_types[op])]
        startable_operations = [op for op in operations if op not in conflicts]
        if not startable_operations:
            labels = {
                "metadata": "Metadata match",
                "suggestions": "Suggestions",
                "entity_data": "Entity data",
                "thumbnails": "Playback snapshots",
            }
            names = ", ".join(labels[op] for op in conflicts)
            active = [
                j for t in ("METADATA_ASSETS", "METADATA_SYNC", "SUGGESTION_SCAN", "ENTITY_IMAGE_SYNC", "THUMBNAIL_INDEXER")
                for j in db.active_jobs(t)
            ]
            return {
                "ok": True,
                "already_running": True,
                "conflicts": conflicts,
                "message": f"Already running: {names}",
                "id": active[0]["id"] if active else None,
                "ids": [],
            }

        # Build each persisted job independently. Legacy workers already accept
        # these canonical single-operation payloads through unified_detail().
        job_details: dict[str, dict] = {}
        if "metadata" in operations:
            job_details["metadata"] = {
                "limit": max(25, min(5000, int(data.get("metadata_limit", 50)))),
                "unbounded": bool(data.get("metadata_unbounded", False)),
                "force": bool(data.get("metadata_force", False)),
                "visual_match": bool(data.get("visual_match", True)),
                "source_channels": [str(x) for x in (data.get("metadata_channels") or []) if str(x).strip()],
            }
            if not job_details["metadata"]["source_channels"]:
                raise HTTPException(400, "Select at least one source channel for metadata matching")

        if "suggestions" in operations:
            suggestion_scopes = [str(x) for x in (data.get("suggestion_scopes") or []) if str(x) in {"channel", "performer", "site"}]
            suggestion_channels = [str(x) for x in (data.get("suggestion_channels") or []) if str(x).strip()]
            performer_raw = list(data.get("suggestion_performer_ids") or [])
            site_raw = list(data.get("suggestion_site_ids") or [])

            def _resolve_entity_selection(raw_values, table):
                values = [str(x).strip() for x in raw_values if str(x).strip()]
                if "all" not in {v.casefold() for v in values}:
                    return [int(v) for v in values if v.isdigit() and int(v) > 0]
                with db.lock:
                    rows = db.conn.execute(f"SELECT id FROM {table} ORDER BY id").fetchall()
                return [int(r[0]) for r in rows]

            job_details["suggestions"] = {
                "scopes": suggestion_scopes,
                "channel_ids": suggestion_channels,
                "performer_ids": _resolve_entity_selection(performer_raw, "performers"),
                "site_ids": _resolve_entity_selection(site_raw, "studios"),
                "limit": max(25, min(5000, int(data.get("suggestion_limit", 50)))),
                "unbounded": bool(data.get("suggestion_unbounded", False)),
                "force_recheck": bool(data.get("suggestion_force", False)),
            }
            if not job_details["suggestions"]["scopes"]:
                raise HTTPException(400, "Select at least one suggestion scan scope")
            if "channel" in suggestion_scopes and not suggestion_channels:
                raise HTTPException(400, "Select a suggestion channel")
            if "performer" in suggestion_scopes and not job_details["suggestions"]["performer_ids"]:
                raise HTTPException(400, "No performers are available for this scan")
            if "site" in suggestion_scopes and not job_details["suggestions"]["site_ids"]:
                raise HTTPException(400, "No studios are available for this scan")

        if "entity_data" in operations:
            job_details["entity_data"] = {
                "scope": str(data.get("entity_scope") or "all"),
                "limit": max(25, min(5000, int(data.get("entity_limit", 50)))),
                "unbounded": bool(data.get("entity_unbounded", False)),
                "force_recheck": bool(data.get("entity_force", False)),
                "manual": True,
            }

        if "thumbnails" in operations:
            thumbnail_scope = str(data.get("thumbnail_scope") or "missing").strip().casefold()
            if thumbnail_scope not in {"missing", "all"}:
                thumbnail_scope = "missing"
            job_details["thumbnails"] = {
                "channels": [int(x) for x in (data.get("thumbnail_channels") or []) if str(x).strip()],
                "scope": thumbnail_scope,
                "limit": max(25, min(5000, int(data.get("thumbnail_limit", 50)))),
                "unbounded": bool(data.get("thumbnail_unbounded", False)),
            }
            if not job_details["thumbnails"]["channels"]:
                raise HTTPException(400, "Select at least one source channel for playback snapshots")

        full_run = any(bool(job_details.get(op, {}).get("unbounded")) for op in operations)
        job_ids = []
        for op in startable_operations:
            job_type = operation_job_types[op]
            jid = db.create_job(job_type, "", "", False, detail=json.dumps(job_details[op]))
            jobs.submit(jid)
            job_ids.append(jid)

        return {
            "ok": True,
            "ids": job_ids,
            "id": job_ids[0] if job_ids else None,
            "operations": startable_operations,
            "requested_operations": operations,
            "conflicts": conflicts,
            "parallel": len(job_ids) > 1,
            "full_run": full_run,
        }

    # ---------------- playback ----------------
    # One shared PlaybackEngine serves every channel for the browser, VLC,
    # MPC-HC and the ffmpeg compatibility stream (see playback/stream_server.py).

    engine_lock = asyncio.Lock()


    # ------------------------------------------------------------------
    # SUBSECTION: Playback helpers
    # Engine lookup, auth and message resolution.
    # ------------------------------------------------------------------
    async def _wait_for_previous_playback_stop():
        task = ps.get("_playback_stop_task")
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=12.0)
        except asyncio.TimeoutError as exc:
            raise HTTPException(503, "The previous playback session is still releasing resources. Wait a moment and retry Play.") from exc
        except Exception as exc:
            # Cleanup reports its own exception; a finished task must not block
            # creation of the next engine forever.
            db.log(f"Playback teardown task ended with {type(exc).__name__}: {exc}", "WARN")

    async def _get_engine():
        """Return the running playback engine, creating it when needed."""
        from ..playback.stream_server import PlaybackEngine
        await _wait_for_previous_playback_stop()
        eng = ps.get("engine")
        if eng is not None and eng.running and eng.client is telegram.client:
            return eng
        async with engine_lock:
            await _wait_for_previous_playback_stop()
            eng = ps.get("engine")
            if eng is not None and eng.running and eng.client is telegram.client:
                return eng
            if eng is not None:
                try:
                    await eng.stop()
                except Exception:
                    _swallowed_note("routes_metadata_assets.register_metadata_assets_routes._get_engine")
                    pass
                ps["engine"] = None
            if not telegram.connected or telegram.client is None:
                raise HTTPException(503, "Telegram is not connected")
            profile = getattr(telegram, "account_profile", None)
            if profile is None:
                try:
                    me = await telegram.client.get_me()
                    premium = bool(getattr(me, "premium", False))
                except Exception:
                    premium = False
            else:
                premium = bool(profile.premium)
            from ..playback.stream_server import (
                configure_range_cache_policy,
                prune_expired_range_cache,
                prune_range_cache_to_budget,
            )
            cache_policy = configure_range_cache_policy(
                budget_gib=db.setting("playback_range_cache_budget_gib", 50),
                retention=db.setting("playback_range_cache_retention", "idle_30m"),
            )
            from ..playback.stream_server import configure_playback_tuning, configure_connection_profile
            configure_playback_tuning(
                readahead_seconds=db.setting("playback_readahead_seconds", 180),
                ram_mb=db.setting("playback_ram_cache_mb", 128),
            )
            configure_connection_profile(db.setting("playback_connection_profile", "balanced"))
            await asyncio.to_thread(prune_expired_range_cache)
            await asyncio.to_thread(
                prune_range_cache_to_budget,
                budget_bytes=cache_policy["budget_bytes"],
            )
            import inspect
            signature = inspect.signature(PlaybackEngine.__init__)
            kwargs = {"premium": premium}
            if "account_profile" in signature.parameters:
                kwargs["account_profile"] = profile
            if "resources" in signature.parameters:
                kwargs["resources"] = resources
            if "job_manager" in signature.parameters:
                kwargs["job_manager"] = jobs
            else:
                db.log("PlaybackEngine loaded without resources parameter; using compatibility mode for this runtime", "WARN")
            db.log(f"Playback modules: routes={__file__}; engine={inspect.getsourcefile(PlaybackEngine) or '<unknown>'}", "INFO")
            eng = PlaybackEngine(telegram.client, **kwargs)
            await eng.start()
            ps["engine"] = eng
            db.log(f"Playback engine started ({'Premium' if premium else 'Free'} account, "
                   f"{len(eng.transfer_clients)} transfer connections, "
                   f"fetch={eng.fetch_concurrency}/{eng.max_fetch_concurrency})")
            return eng

    ps["get_engine"] = _get_engine

    def _player_port() -> int:
        try:
            return int(ps.get("player_port") or config.LOCAL_PLAYER_PORT)
        except Exception:
            return int(config.LOCAL_PLAYER_PORT)

    def require_playback_allowed(channel_id: int, message_id: int) -> None:
        """Validate a playback target before generating warm-up/order URLs.

        Playback admission itself remains owned by the shared PlaybackEngine.
        These lightweight route checks prevent malformed targets from entering
        the playback queue without duplicating Telegram/media authorization.
        """
        try:
            cid, mid = int(channel_id), int(message_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid playback target")
        if cid == 0 or mid <= 0:
            raise HTTPException(400, "Invalid playback target")

    def _local_url(channel_id: int, message_id: int, ttl: int = 7 * 24 * 3600) -> str:
        return (f"http://127.0.0.1:{_player_port()}/video/{int(message_id)}"
                f"?c={int(channel_id)}&t={tokens.make(int(channel_id), int(message_id), ttl=ttl)}")

    def _playback_claim_from(source):
        if source is None:
            return None
        try:
            client_id = str(source.get("axclient") or source.get("playback_client_id") or "").strip()
            token = str(source.get("axtoken") or source.get("playback_token") or "").strip()
            generation = int(source.get("axgen") or source.get("playback_generation") or 0)
        except (AttributeError, TypeError, ValueError, OverflowError):
            return None
        if not client_id or len(client_id) > 80 or not token or len(token) > 100 or generation < 1:
            return None
        return {"client_id": client_id, "generation": generation, "token": token}

    async def _engine_message(channel_id: int, message_id: int, *, playback_claim=None):
        try:
            engine = await _get_engine()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(503, f"Playback engine could not start: {e}")
        if playback_claim:
            try:
                claimed = engine.claim_playback(
                    (int(channel_id), int(message_id)), **playback_claim,
                )
            except Exception as e:
                raise HTTPException(503, f"Playback handoff could not be registered: {type(e).__name__}")
            if not claimed:
                raise HTTPException(409, "This playback request was superseded by a newer open")
        try:
            message = await engine.get_message(channel_id, message_id)
        except FileNotFoundError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(503, f"Could not read video from Telegram: {e}")
        return engine, message


    # ------------------------------------------------------------------
    # SUBSECTION: Channel catalogue helpers
    # Catalogue queries and records.
    # ------------------------------------------------------------------
    def _cat_query(channel_id: int, ids=None):
        """Catalogue rows (+ local performer mappings) for a channel, or for
        just some of its messages."""
        extra, params = "", [str(channel_id)]
        if ids:
            ids = [int(x) for x in ids]
            extra = f" AND m.source_message_id IN ({','.join('?' for _ in ids)})"
            params += ids
        with db.lock:
            rows = db.conn.execute(
                "SELECT m.source_message_id, m.thumbnail_message_id, m.width, m.height, m.codec,"
                "       m.resolution_category, m.streamable, m.title, m.filename, m.metadata_source,"
                "       m.provider_scene_id, m.provider_updated_at, m.metadata_synced_at, m.scene_type, m.site_name, m.site_url, m.provider_url, m.provider_payload, m.release_date, m.updated_at,"
                "       GROUP_CONCAT(DISTINCT p.name) performers,"
                "       GROUP_CONCAT(DISTINCT p.id || char(31) || p.name || char(31) || COALESCE(p.image,'')) performer_meta,"
                "       GROUP_CONCAT(DISTINCT s.name) studios,"
                "       GROUP_CONCAT(DISTINCT t.name) tags"
                " FROM archive_x_published_media m"
                " LEFT JOIN video_performers vp ON vp.video_id=m.id LEFT JOIN performers p ON p.id=vp.performer_id"
                " LEFT JOIN video_studios vs ON vs.video_id=m.id LEFT JOIN studios s ON s.id=vs.studio_id"
                " LEFT JOIN video_tags vt ON vt.video_id=m.id LEFT JOIN tags t ON t.id=vt.tag_id"
                " WHERE m.source_channel=?" + extra + " GROUP BY m.id",
                params
            ).fetchall()
            local_rows = db.conn.execute(
                "SELECT m.source_message_id, GROUP_CONCAT(DISTINCT p.name) local_performers, "
                "GROUP_CONCAT(DISTINCT p.id || char(31) || p.name || char(31) || COALESCE(p.image,'')) local_performer_meta "
                "FROM archive_x_published_media m JOIN local_video_performers lvp ON lvp.video_id=m.id "
                "JOIN performers p ON p.id=lvp.performer_id WHERE m.source_channel=?" + extra +
                " GROUP BY m.source_message_id",
                params
            ).fetchall()
        catalogue = {int(r["source_message_id"]): r for r in rows}
        local_perfs = {}
        local_perf_meta = {}
        for r in local_rows:
            mid = int(r["source_message_id"])
            local_perfs[mid] = [x for x in (r["local_performers"] or "").split(",") if x]
            local_perf_meta[mid] = [x for x in (r["local_performer_meta"] or "").split(",") if x]
        return catalogue, local_perfs, local_perf_meta

    def _provider_page_url(cat) -> str:
        raw = cat["provider_url"] if cat and "provider_url" in cat.keys() else ""
        if raw:
            return str(raw).strip()
        payload = cat["provider_payload"] if cat and "provider_payload" in cat.keys() else ""
        if not payload:
            return ""
        try:
            data = json.loads(payload) if isinstance(payload, str) else payload
        except Exception:
            return ""
        if not isinstance(data, dict):
            return ""
        for key in ("url", "profile", "scene_url", "sceneUrl"):
            value = str(data.get(key) or "").strip()
            if value.startswith(("https://", "http://")):
                return value
        return ""

    def _apply_cat(rec: dict, cat, lp: list, lp_meta: list | None = None) -> dict:
        """Merge catalogue data into a video record for the UI."""
        if cat:
            rec["thumbnail_id"] = cat["thumbnail_message_id"]
            rec["telegram_filename"] = rec.get("name") or cat["filename"] or f"video_{rec['id']}.mp4"
            rec["api_title"] = (cat["title"] or "").strip()
            rec["title"] = cat["title"] or rec.get("name")
            rec["metadata_cached"] = bool(cat["metadata_source"] and cat["metadata_source"] not in ("none", ""))
            rec["metadata_checked"] = bool(cat["metadata_source"] or cat["metadata_synced_at"])
            rec["catalogued"] = True
            rec["width"] = cat["width"] or 0
            rec["height"] = cat["height"] or 0
            rec["codec"] = cat["codec"] or ""
            rec["resolution_category"] = cat["resolution_category"] or ""
            rec["streamable"] = bool(cat["streamable"])
            rec["performers"] = list(dict.fromkeys([x for x in (cat["performers"] or "").split(",") if x] + lp))
            performer_meta = []
            for raw in (cat["performer_meta"] or "").split(","):
                parts = raw.split(chr(31), 2)
                if len(parts) >= 2 and parts[0] and parts[1]:
                    performer_meta.append({"id": int(parts[0]), "name": parts[1], "image": parts[2] if len(parts) > 2 else ""})
            for raw in (lp_meta or []):
                parts = raw.split(chr(31), 2)
                if len(parts) >= 2 and parts[0] and parts[1] and not any(str(x.get("id")) == parts[0] for x in performer_meta):
                    performer_meta.append({"id": int(parts[0]), "name": parts[1], "image": parts[2] if len(parts) > 2 else ""})
            rec["performer_meta"] = performer_meta
            rec["local_performers"] = lp
            rec["studios"] = [x for x in (cat["studios"] or "").split(",") if x]
            rec["studio"] = rec["studios"][0] if rec["studios"] else ""
            rec["tags"] = [x for x in (cat["tags"] or "").split(",") if x]
            for k in ("metadata_source", "provider_scene_id", "provider_updated_at", "metadata_synced_at",
                      "scene_type", "site_name", "site_url", "release_date"):
                rec[k] = cat[k] or ""
            rec["provider_url"] = _provider_page_url(cat)
            rec["thumbnail_version"] = cat["updated_at"] or cat["provider_updated_at"] or cat["metadata_synced_at"] or ""
        else:
            rec.setdefault("thumbnail_id", int(rec["id"]))
            rec.setdefault("telegram_filename", rec.get("name") or f"video_{rec['id']}.mp4")
            rec.setdefault("api_title", "")
            rec.setdefault("metadata_cached", False)
            rec.setdefault("performers", lp)
            rec.setdefault("performer_meta", [])
            rec["local_performers"] = lp
            rec["catalogued"] = False
            rec.setdefault("studio", "")
            rec.setdefault("site_name", "")
            rec.setdefault("tags", [])
        if not rec.get("thumbnail_id"):
            rec["thumbnail_id"] = int(rec["id"])
        return rec

    def _channel_catalogue(channel_id: int, records: dict):
        """Blocking catalogue join (runs in a worker thread)."""
        catalogue, local_perfs, local_perf_meta = _cat_query(channel_id)
        result = []
        for rec in records.values():
            rid = int(rec["id"])
            result.append(_apply_cat(dict(rec), catalogue.get(rid), local_perfs.get(rid, []), local_perf_meta.get(rid, [])))
        result.sort(key=lambda r: r.get("date", ""), reverse=True)
        uncatalogued = sum(1 for r in result if not catalogue.get(int(r["id"])))
        return result, uncatalogued

    def _records_for(keys):
        """Fresh card data for (channel, message) pairs (enrichment updates)."""
        by_chan = {}
        for c, m in keys:
            by_chan.setdefault(int(c), []).append(int(m))
        out = []
        for c, ids in by_chan.items():
            raw = db.setting(f"video_index_v2_{c}") or db.setting(f"video_index_{c}")
            index = {}
            if raw:
                try:
                    index = json.loads(raw) if isinstance(raw, str) else dict(raw)
                except Exception:
                    index = {}
            catalogue, local_perfs, local_perf_meta = _cat_query(c, ids)
            for m in ids:
                base = dict(index.get(str(m)) or {"id": m, "name": ""})
                base["id"] = m
                rec = _apply_cat(base, catalogue.get(m), local_perfs.get(m, []), local_perf_meta.get(m, []))
                rec["_cid"] = str(c)
                out.append(rec)
        return out

    # Channel video index. Building it for a big channel can take a while,
    # so it ALWAYS runs as a background task; requests never wait for a full
    # Telegram scan. v2 indexes also contain videos sent as files.
    index_tasks: dict = {}
    uncat_logged: set = set()


    # ------------------------------------------------------------------
    # SUBSECTION: Channel index
    # Index state, checkpoints and start.
    # ------------------------------------------------------------------
    def _index_key(cid, version=2):
        return f"video_index_v2_{cid}" if version == 2 else f"video_index_{cid}"

    def _load_index(cid):
        for version in (2, 1):
            raw = db.setting(_index_key(cid, version))
            if raw:
                try:
                    recs = json.loads(raw) if isinstance(raw, str) else dict(raw)
                    if isinstance(recs, dict):
                        return recs, version
                except Exception:
                    _swallowed_note("routes_metadata_assets.register_metadata_assets_routes._load_index")
                    pass
        return None, 0

    def _index_state(cid):
        from ..core.channel_index_state import load_state, status
        state = load_state(db, cid)
        return state, status(
            state,
            max_age=getattr(config, "CHANNEL_INDEX_FRESHNESS_SECONDS", 300),
        )

    # Validation is cached only while all individual frame files are unchanged.
    # DB status alone is not proof that assets still exist or remain decodable.
    _snapshot_validation_cache: OrderedDict[tuple, bool] = OrderedDict()

    def _validated_snapshot_counts_sync(channel_id: int) -> tuple[int, int]:
        """Count validated snapshot sets in one channel."""
        from PIL import Image
        from ..engines.preview_prefetch import PREVIEW_VERSION, FRAME_COUNT, MIN_FRAME_BYTES, frame_quality_sync

        with db.lock:
            rows = db.conn.execute(
                "SELECT m.source_message_id,c.status,c.version,c.frame_count,c.frames_path,c.path,"
                "t.data AS thumb_data,t.source AS thumb_source "
                "FROM media m LEFT JOIN timeline_preview_cache c "
                "ON c.source_channel=m.source_channel AND c.video_message_id=m.source_message_id "
                "LEFT JOIN thumbnail_cache t ON t.source_channel=m.source_channel "
                "AND t.video_message_id=m.source_message_id "
                "WHERE CAST(m.source_channel AS INTEGER)=? AND COALESCE(m.status,'')<>'SOURCE_DELETED'",
                (int(channel_id),),
            ).fetchall()
        total = len(rows)
        ready = 0
        for row in rows:
            try:
                mid = int(row["source_message_id"])
                frames_dir = Path(str(row["frames_path"] or ""))
                if not str(row["frames_path"] or ""):
                    from ..engines.preview_prefetch import preview_frames_path
                    frames_dir = preview_frames_path(int(channel_id), mid)
                files = sorted(frames_dir.glob("frame_*.jpg")) if frames_dir.is_dir() else []
                signature_parts = []
                for f in files:
                    try:
                        st = f.stat()
                        signature_parts.append((f.name, st.st_mtime_ns, st.st_size))
                    except OSError:
                        signature_parts.append((f.name, -1, -1))
                key = (int(channel_id), mid, str(frames_dir), int(row["version"] or 0),
                       int(row["frame_count"] or 0), str(row["status"] or ""), tuple(signature_parts))
                cached = _snapshot_validation_cache.get(key)
                if cached is None:
                    quality = frame_quality_sync(frames_dir)
                    representative_ok = False
                    rep = Path(str(row["path"] or ""))
                    try:
                        if rep.is_file() and rep.stat().st_size >= MIN_FRAME_BYTES:
                            with Image.open(rep) as im:
                                im.verify()
                            representative_ok = True
                    except Exception:
                        representative_ok = False
                    if not representative_ok and str(row["thumb_source"] or "") == "playback-snapshot":
                        try:
                            data = bytes(row["thumb_data"] or b"")
                            if len(data) >= MIN_FRAME_BYTES:
                                with Image.open(io.BytesIO(data)) as im:
                                    im.verify()
                                representative_ok = True
                        except Exception:
                            representative_ok = False
                    cached = bool(
                        row["status"] == "READY"
                        and int(row["version"] or 0) == PREVIEW_VERSION
                        and int(row["frame_count"] or 0) == FRAME_COUNT
                        and quality.get("ready") and representative_ok
                    )
                    _snapshot_validation_cache[key] = cached
                    while len(_snapshot_validation_cache) > 5000:
                        _snapshot_validation_cache.popitem(last=False)
                else:
                    _snapshot_validation_cache.move_to_end(key)
                if cached:
                    ready += 1
            except Exception:
                _swallowed_note("routes_metadata_assets.register_metadata_assets_routes._validated_snapshot_counts_sync")
                continue
        return total, ready

    def _index_checkpoint_key(cid):
        return f"video_index_checkpoint_v1_{int(cid)}"

    def _start_index(cid, entity, existing=None, *, force_full: bool = False):
        """Start a channel index task unless one is already running."""
        st = index_tasks.get(cid)
        if st and not st["task"].done():
            return st
        if force_full:
            # Edits/deletes can invalidate records outside the newest cursor.
            # Preserve the currently published index until the rebuild succeeds,
            # but do not resume an append-only checkpoint for a full reconciliation.
            db.set_setting(_index_checkpoint_key(cid), {})
            existing = None
        raw_checkpoint = db.setting(_index_checkpoint_key(cid), {})
        if isinstance(raw_checkpoint, str):
            try:
                raw_checkpoint = json.loads(raw_checkpoint)
            except Exception:
                raw_checkpoint = {}
        checkpoint = raw_checkpoint if isinstance(raw_checkpoint, dict) and raw_checkpoint.get("version") == 1 else None
        checkpoint_min_id = 0
        if checkpoint:
            try:
                checkpoint_min_id = max(0, int(checkpoint.get("base_min_id") or 0))
                phase_index = int(checkpoint.get("phase_index") or 0)
                if phase_index not in (0, 1, 2) or not isinstance(checkpoint.get("records"), dict):
                    checkpoint = None
                    checkpoint_min_id = 0
            except (TypeError, ValueError, OverflowError):
                checkpoint = None
                checkpoint_min_id = 0
        # A valid saved cursor takes precedence over an incremental refresh. Its
        # records already include the prior index and every durable partial batch.
        if checkpoint and isinstance(checkpoint.get("records"), dict):
            existing = None
        st = {"stats": {"messages": 0, "videos": 0, "passes_completed": 0,
                         "enumeration_complete": False, "full_history": False},
              "error": "", "done": False, "started": time.time(),
              "incremental": bool(existing) or bool(checkpoint and checkpoint_min_id > 0),
              "resumed": bool(checkpoint), "force_full": bool(force_full)}
        from ..playback.stream_server import build_channel_index, update_channel_index

        async def _save_index_checkpoint(value):
            # Serialize and commit off-loop: a large channel checkpoint must not
            # block web requests or the browser's range delivery loop.
            await asyncio.to_thread(db.set_setting, _index_checkpoint_key(cid), value)

        async def _progress(value):
            st["stats"] = dict(value)

        async def run():
            """Index task body: build or update the index under a background slot."""
            acquired = False
            try:
                if resources is not None:
                    await resources.acquire_background("metadata")
                    acquired = True
                if checkpoint:
                    recs = await build_channel_index(
                        telegram.client, entity, progress=lambda x: st.__setitem__("stats", x),
                        checkpoint=checkpoint, checkpoint_callback=_save_index_checkpoint,
                    )
                elif existing:
                    recs = await update_channel_index(
                        telegram.client, entity, dict(existing),
                        checkpoint_callback=_save_index_checkpoint,
                        progress=lambda value: st.__setitem__("stats", value),
                    )
                else:
                    recs = await build_channel_index(
                        telegram.client, entity, progress=lambda x: st.__setitem__("stats", x),
                        checkpoint_callback=_save_index_checkpoint,
                    )
                scan_stats = dict(st.get("stats") or {})
                if not bool(scan_stats.get("enumeration_complete")):
                    raise RuntimeError(
                        "Telegram enumeration did not complete both video passes; "
                        "published index and checkpoint were left unchanged"
                    )
                # An incremental pass is complete only for its bounded new-message
                # window. The whole library remains complete iff its prior full
                # history scan was complete; a brand-new partial index cannot be.
                from ..core.channel_index_state import save_state, load_state
                previous_state = await asyncio.to_thread(load_state, db, cid)
                complete_index = bool(scan_stats.get("full_history") or previous_state.get("complete"))
                payload = await asyncio.to_thread(json.dumps, recs)
                await asyncio.to_thread(db.set_setting, _index_key(cid), payload)
                highest = max((int(x) for x in recs), default=0)
                await asyncio.to_thread(
                    save_state, db, cid, record_count=len(recs),
                    highest_message_id=highest, complete=complete_index, dirty=not complete_index,
                )
                # Published index and freshness state are committed before the
                # resume cursor is cleared; a crash before this line simply
                # repeats the idempotent final publication on restart.
                await asyncio.to_thread(db.set_setting, _index_checkpoint_key(cid), {})
                st["stats"] = dict(scan_stats, videos=len(recs), complete=complete_index,
                                   enumeration_complete=True, overall_complete=False)
                st["added"] = len(recs) - len(existing or {})
                if not existing:
                    db.log(f"Channel {getattr(entity, 'title', cid)}: {len(recs)} videos found in Telegram "
                           f"({st['stats'].get('native_videos', 0)} native, {st['stats'].get('document_videos', 0)} sent as files)")
            except Exception as e:
                st["error"] = f"{type(e).__name__}: {e}"
                try:
                    from ..core.channel_index_state import load_state, save_state
                    previous = load_state(db, cid)
                    await asyncio.to_thread(
                        save_state, db, cid,
                        record_count=int(previous.get("record_count") or len(existing or {})),
                        highest_message_id=int(previous.get("highest_message_id") or 0),
                        complete=False, dirty=True,
                    )
                except Exception:
                    _swallowed_note("routes_metadata_assets.register_metadata_assets_routes._start_index.run")
                    pass
                db.log(f"Indexing channel {getattr(entity, 'title', cid)} failed: {st['error']}; checkpoint retained for resume", "ERROR")
            finally:
                if acquired and resources is not None:
                    try:
                        await resources.release_background("metadata")
                    except Exception:
                        _swallowed_note("routes_metadata_assets.register_metadata_assets_routes._start_index.run")
                        pass
                st["done"] = True
        st["task"] = asyncio.create_task(run(), name=f"index-{cid}")
        index_tasks[cid] = st
        return st

    def _index_status(st):
        if not st:
            return {"running": False}
        return {"running": not st["done"], "done": st["done"], "error": st["error"],
                "stats": st["stats"], "added": st.get("added", 0), "incremental": st["incremental"],
                "resumed": bool(st.get("resumed")),
                "seconds": int(time.time() - st["started"])}

    @router.post("/api/channels/{channel_id}/index")
    async def channel_index_start(channel_id: int, request: Request):
        """Explicitly start/resume a Telegram video index in the background."""
        require_auth(request)
        client = getattr(telegram, "client", None)
        if client is None:
            raise HTTPException(503, "Telegram is not connected")
        connected = getattr(client, "is_connected", None)
        if callable(connected) and not connected():
            raise HTTPException(503, "Telegram is not connected")
        try:
            entity = await client.get_entity(int(channel_id))
        except Exception as exc:
            raise HTTPException(502, f"Could not resolve Telegram channel: {type(exc).__name__}")
        existing, version = _load_index(channel_id)
        state, _freshness = await asyncio.to_thread(_index_state, channel_id)
        raw_checkpoint = db.setting(_index_checkpoint_key(channel_id), {})
        if isinstance(raw_checkpoint, str):
            try:
                raw_checkpoint = json.loads(raw_checkpoint)
            except Exception:
                raw_checkpoint = {}
        try:
            checkpoint_phase = int(raw_checkpoint.get("phase_index", -1)) if isinstance(raw_checkpoint, dict) else -1
        except (TypeError, ValueError, OverflowError):
            checkpoint_phase = -1
        resumable_checkpoint = bool(
            isinstance(raw_checkpoint, dict)
            and raw_checkpoint.get("version") == 1
            and isinstance(raw_checkpoint.get("records"), dict)
            and checkpoint_phase in (0, 1, 2)
        )
        if version != 2:
            existing = None
        # An incomplete scan without a valid durable cursor cannot safely
        # continue from a published partial index; restart both history passes.
        force_full = bool((not state or not state.get("complete")) and not resumable_checkpoint)
        if force_full:
            existing = None
        task = _start_index(int(channel_id), entity, existing=existing,
                            force_full=force_full)
        return {"ok": True, "channel_id": int(channel_id), "running": not task["task"].done(),
                "resumed": bool(task.get("resumed")), "incremental": bool(task.get("incremental")),
                "status_url": f"/api/channels/{int(channel_id)}/index-status"}

    return {
        "_engine_message": _engine_message,
        "_get_engine": _get_engine,
        "_index_state": _index_state,
        "_index_status": _index_status,
        "_local_url": _local_url,
        "_playback_claim_from": _playback_claim_from,
        "_player_port": _player_port,
        "_provider_page_url": _provider_page_url,
        "_validated_snapshot_counts_sync": _validated_snapshot_counts_sync,
        "engine_lock": engine_lock,
        "index_tasks": index_tasks,
        "require_playback_allowed": require_playback_allowed,
    }
