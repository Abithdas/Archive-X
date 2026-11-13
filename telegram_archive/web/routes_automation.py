"""Explicit registration for Content Automation (qBittorrent) routes."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Status and setup
#       - Run controls
#       - Upload policy
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request


def register_automation_routes(
    router: APIRouter,
    *,
    db,
    jobs,
    telegram,
    require_auth,
    content_automation,
    config,
    validate_automation_root,
    QBittorrentEngine,
    TorrentEngineError,
    get_upload_policy,
    upload_policy_dict,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Status and setup
    # Status, clear-all and engine connection.
    # ------------------------------------------------------------------
    """Register Content Automation status and control routes."""
    @router.get("/api/operations/automation/status")
    def automation_status(request: Request):
        """GET /api/operations/automation/status: Automation status."""
        require_auth(request)
        return content_automation.list_status(db)

    @router.post("/api/operations/automation/clear-all")
    async def automation_clear_all(request: Request):
        """Stop Content Automation and clear its queue/cache/activity state."""
        require_auth(request)

        # Do not clear an Automation folder while Local Upload is processing an
        # overlapping path. The uploader can still be reading/deleting a file.
        try:
            validate_automation_root(db, content_automation.get_config(db, masked=False).get("download_path") or config.AUTOMATION_DOWNLOAD_DIR)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

        # Ask the live job to stop first. Do not delete persisted state while a
        # worker can still write a new queue item or touch a download file.
        active = db.active_jobs(content_automation.AUTOMATION_JOB_TYPE)
        for job in active:
            jobs.cancel(str(job["id"]))

        deadline = time.monotonic() + 60.0
        while db.active_jobs(content_automation.AUTOMATION_JOB_TYPE) and time.monotonic() < deadline:
            await asyncio.sleep(0.25)

        if db.active_jobs(content_automation.AUTOMATION_JOB_TYPE):
            raise HTTPException(409, "Content Automation is still stopping. Clear all again after it reaches Stopped.")

        try:
            result = await content_automation.clear_all_state(db)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            db.log(f"Content Automation clear-all failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(500, f"Content Automation clear-all failed: {type(exc).__name__}")

        return {"ok": True, **result}

    @router.post("/api/operations/automation/test-engine")
    async def automation_test_engine(request: Request):
        """Validate the configured qBittorrent Web API connection."""
        require_auth(request)
        cfg = content_automation.get_config(db, masked=False)
        engine = None
        try:
            supervisor = content_automation.QBittorrentSupervisor(
                cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
                auto_start=bool(cfg.get("qbittorrent_auto_start", True)),
                auto_install=bool(cfg.get("qbittorrent_auto_install", True)),
                auto_configure_local_webui=bool(cfg.get("qbittorrent_auto_configure_local_webui", True)),
            )
            await supervisor.ensure_ready()
            await supervisor.launch_gui()
            engine = QBittorrentEngine(
                base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
                username=cfg.get("qbittorrent_username") or "",
                password=cfg.get("qbittorrent_password") or "",
                api_key=cfg.get("qbittorrent_api_key") or "",
            )
            info = await engine.version()
            result = {"ok": True, **info, "python": sys.executable}
            result["session_notes"] = str(info.get("notes") or "")
            result["url"] = cfg.get("qbittorrent_url") or "http://127.0.0.1:8080"
            return result
        except TorrentEngineError as exc:
            detail = f"{exc} | Python: {sys.executable}"
            db.log(f"Content Automation download engine test failed: {detail}", "WARN")
            raise HTTPException(503, detail)
        except Exception as exc:
            db.log(f"Content Automation download engine test failed: {type(exc).__name__}: {exc}", "WARN")
            raise HTTPException(503, str(exc))
        finally:
            if engine is not None:
                try:
                    await engine.close()
                except Exception:
                    pass

    @router.post("/api/operations/automation/connect-execute")
    async def automation_connect_execute(request: Request):
        """Establish and verify Archive X ↔ qBittorrent communication, then start automation."""
        require_auth(request)
        cfg = content_automation.get_config(db, masked=False)
        try:
            validate_automation_root(db, cfg.get("download_path") or config.AUTOMATION_DOWNLOAD_DIR)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        if str(cfg.get("torrent_backend") or "qbittorrent").lower() != "qbittorrent":
            raise HTTPException(400, "Connect & Execute is available for qBittorrent only")
        for key in ("rss_url", "destination_channel"):
            if not str(cfg.get(key) or "").strip():
                raise HTTPException(400, f"Configure {key} before executing Content Automation")
        active_ca = db.active_jobs(content_automation.AUTOMATION_JOB_TYPE)
        if active_ca:
            restart_job = next(
                (j for j in active_ca
                 if j.get("status") == "PAUSED" and "interrupted by restart" in str(j.get("current_operation") or "").lower()),
                None,
            )
            if restart_job is not None:
                raise HTTPException(409, "A previous Content Automation task is paused. Use Resume or Cancel in Queue & activity before starting a new task.")
            raise HTTPException(409, "Content Automation is already running")
        try:
            ready = await content_automation.ensure_qbittorrent(cfg, db=db)
            engine = QBittorrentEngine(
                base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
                username=cfg.get("qbittorrent_username") or "",
                password=cfg.get("qbittorrent_password") or "",
                api_key=cfg.get("qbittorrent_api_key") or "",
                auto_start=bool(cfg.get("qbittorrent_auto_start", True)),
                auto_install=bool(cfg.get("qbittorrent_auto_install", True)),
                auto_configure_local_webui=bool(cfg.get("qbittorrent_auto_configure_local_webui", True)),
            )
            try:
                info = await engine.version()
            finally:
                await engine.close()
            cfg["enabled"] = True
            content_automation.save_config(db, cfg, allow_enable=True)
            content_automation.set_runtime(db, operator_started=True, paused=False)
            jid = db.create_job(content_automation.AUTOMATION_JOB_TYPE, cfg["rss_url"], cfg["destination_channel"], False, detail=json.dumps({
                "connection": "verified", "qbit_url": cfg.get("qbittorrent_url"), "qbit_version": info.get("version", "unknown")
            }))
            jobs.submit(jid)
            db.log(f"Content Automation executed: Archive X ↔ qBittorrent connected · {info.get('version', 'unknown')}")
            return {"ok": True, "id": jid, "connection": "verified", "action": ready.get("action", "ready"), "version": info.get("version", "unknown"), "url": cfg.get("qbittorrent_url") or "http://127.0.0.1:8080"}
        except TorrentEngineError as exc:
            db.log(f"Content Automation connect/execute failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(503, f"qBittorrent communication failed: {exc}")
        except Exception as exc:
            db.log(f"Content Automation connect/execute failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(503, str(exc))


    # ------------------------------------------------------------------
    # SUBSECTION: Run controls
    # Start, stop, pause, resume and skip.
    # ------------------------------------------------------------------
    @router.post("/api/operations/automation/start")
    async def automation_start(request: Request):
        """POST /api/operations/automation/start: Verify the download engine, then start automation."""
        require_auth(request)
        cfg = content_automation.get_config(db, masked=False)
        try:
            validate_automation_root(db, cfg.get("download_path") or config.AUTOMATION_DOWNLOAD_DIR)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        for key in ("rss_url", "destination_channel"):
            if not str(cfg.get(key) or "").strip():
                raise HTTPException(400, f"Configure {key} before starting Content Automation")
        active_ca = db.active_jobs(content_automation.AUTOMATION_JOB_TYPE)
        if active_ca:
            restart_job = next(
                (j for j in active_ca
                 if j.get("status") == "PAUSED" and "interrupted by restart" in str(j.get("current_operation") or "").lower()),
                None,
            )
            if restart_job is not None:
                raise HTTPException(409, "A previous Content Automation task is paused. Use Resume or Cancel in Queue & activity before starting a new task.")
            raise HTTPException(409, "Content Automation is already running")

        backend = "qbittorrent"
        try:
            download_path = Path(str(cfg.get("download_path") or "").strip()).expanduser()
            if not str(download_path):
                raise RuntimeError("Configure a Torrent download folder before starting Content Automation.")
            download_path.mkdir(parents=True, exist_ok=True)
            probe_file = download_path / ".telegram_archive_write_test"
            probe_file.write_text("ok", encoding="utf-8")
            probe_file.unlink(missing_ok=True)
            if backend == "qbittorrent":
                # Start/verify qBittorrent before creating the automation job.
                # Previously the Start Automation route only queued the job; the
                # worker then failed later if qBittorrent was not already running.
                ready = await content_automation.ensure_qbittorrent(cfg, db=db)
                engine = QBittorrentEngine(
                    base_url=cfg.get("qbittorrent_url") or "http://127.0.0.1:8080",
                    username=cfg.get("qbittorrent_username") or "",
                    password=cfg.get("qbittorrent_password") or "",
                    api_key=cfg.get("qbittorrent_api_key") or "",
                    auto_start=bool(cfg.get("qbittorrent_auto_start", True)),
                    auto_install=bool(cfg.get("qbittorrent_auto_install", True)),
                    auto_configure_local_webui=bool(cfg.get("qbittorrent_auto_configure_local_webui", True)),
                )
                try:
                    info = await engine.version()
                finally:
                    await engine.close()
            cfg["enabled"] = True
            content_automation.save_config(db, cfg, allow_enable=True)
            content_automation.set_runtime(db, operator_started=True, paused=False)
            jid = db.create_job(
                content_automation.AUTOMATION_JOB_TYPE,
                cfg["rss_url"],
                cfg["destination_channel"],
                False,
                detail=json.dumps({
                    "connection": "verified",
                    "backend": backend,
                    "qbit_url": cfg.get("qbittorrent_url") if backend == "qbittorrent" else None,
                    "version": info.get("version", "unknown"),
                }),
            )
            jobs.submit(jid)
            db.log(
                f"Content Automation started: {backend} ready · {info.get('version', 'unknown')}"
                + (f" · {ready.get('action', 'ready')}" if backend == "qbittorrent" else "")
            )
            return {
                "ok": True,
                "id": jid,
                "connection": "verified",
                "action": ready.get("action", "ready"),
                "version": info.get("version", "unknown"),
                "url": cfg.get("qbittorrent_url") if backend == "qbittorrent" else "",
            }
        except TorrentEngineError as exc:
            db.log(f"Content Automation start failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(503, f"Download engine unavailable: {exc}")
        except Exception as exc:
            db.log(f"Content Automation start failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(503, f"Download engine unavailable: {exc}")

    @router.post("/api/operations/automation/stop")
    async def automation_stop(request: Request):
        """POST /api/operations/automation/stop: Automation stop."""
        require_auth(request)
        jobs_found = db.active_jobs(content_automation.AUTOMATION_JOB_TYPE)
        for job in jobs_found:
            jobs.cancel(job["id"])
        cfg = content_automation.get_config(db, masked=False)
        cfg["enabled"] = False
        content_automation.save_config(db, cfg, allow_enable=True)
        content_automation.set_runtime(db, operator_started=False, paused=False)
        db.log("Content Automation stopped")
        return {"ok": True, "count": len(jobs_found)}

    @router.post("/api/operations/automation/pause")
    async def automation_pause(request: Request):
        require_auth(request)
        if not jobs.is_active(content_automation.AUTOMATION_JOB_TYPE):
            raise HTTPException(409, "Content Automation is not running")
        content_automation.set_runtime(db, paused=True)
        try:
            result = await content_automation.pause_active_torrent(db)
        except Exception as exc:
            content_automation.set_runtime(db, paused=False)
            raise HTTPException(503, f"Could not pause current torrent: {exc}")
        db.log("Content Automation paused by operator")
        return {"ok": True, "runtime": content_automation.get_runtime(db), **result}

    @router.post("/api/operations/automation/resume")
    async def automation_resume(request: Request):
        require_auth(request)
        active = db.active_jobs(content_automation.AUTOMATION_JOB_TYPE)
        if not active:
            raise HTTPException(409, "No previous Content Automation task is available to resume")

        restart_job = next(
            (j for j in active
             if j.get("status") == "PAUSED" and "interrupted by restart" in str(j.get("current_operation") or "").lower()),
            None,
        )
        if restart_job is not None:
            cfg = content_automation.get_config(db, masked=False)
            try:
                await content_automation.ensure_qbittorrent(cfg, db=db)
            except Exception as exc:
                raise HTTPException(503, f"Could not reconnect qBittorrent: {exc}")
            content_automation.set_runtime(db, operator_started=True, paused=False)
            jobs.resume_job(str(restart_job["id"]))
            db.log("Content Automation resumed after restart by operator")
            return {"ok": True, "id": str(restart_job["id"]), "resumed_after_restart": True, "runtime": content_automation.get_runtime(db)}

        # Normal operator pause/resume keeps the existing worker alive.
        if not jobs.is_active(content_automation.AUTOMATION_JOB_TYPE):
            raise HTTPException(409, "Content Automation is not running")
        content_automation.set_runtime(db, operator_started=True, paused=False)
        db.log("Content Automation resumed by operator")
        return {"ok": True, "runtime": content_automation.get_runtime(db)}

    async def _automation_control_current(request: Request, *, target_status: str, action_label: str):
        require_auth(request)
        content_automation._ensure_schema(db)
        with db.lock:
            placeholders = ",".join("?" for _ in content_automation.ACTIVE_STATUSES)
            found = db.conn.execute(
                f"SELECT * FROM automation_items WHERE status IN ({placeholders}) ORDER BY updated_at DESC LIMIT 1",
                list(content_automation.ACTIVE_STATUSES),
            ).fetchone()
        if not found:
            raise HTTPException(409, "There is no active queue item")
        row = dict(found)
        cleaned = await content_automation._cleanup_controlled_item(db, row)
        if cleaned is False:
            raise HTTPException(503, "Could not remove the torrent safely. It was not marked cancelled or skipped; check qBittorrent and retry.")
        content_automation._upsert(
            db, row["item_key"], status=target_status,
            error=f"{action_label} by operator", retry_stage="", next_retry_at="",
            failure_kind="", download_progress=0.0, upload_progress=0.0,
            download_state=content_automation.TASK_LABELS[target_status],
        )
        db.log(f"Content Automation: {action_label.lower()} current item {row.get('title') or row.get('item_key')}")
        return {"ok": True, "item_key": row["item_key"], "title": row.get("title"), "action": action_label}

    @router.post("/api/operations/automation/cancel-current")
    async def automation_cancel_current(request: Request):
        return await _automation_control_current(request, target_status="CANCELLED", action_label="Cancelled")

    @router.post("/api/operations/automation/skip-current")
    async def automation_skip_current(request: Request):
        return await _automation_control_current(request, target_status="SKIPPED", action_label="Skipped")

    @router.post("/api/operations/automation/skip-item")
    async def automation_skip_item(request: Request):
        """Skip one downloading torrent selected from the Queue & Activity UI."""
        require_auth(request)
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        item_key = str((payload or {}).get("item_key") or "").strip()
        if not item_key:
            raise HTTPException(400, "Missing automation item key")

        content_automation._ensure_schema(db)
        # Claim the transition atomically before awaiting torrent cleanup. The
        # running poller re-reads this state after engine awaits, so it cannot
        # hand this release to validation/upload after the operator skips it.
        with db.lock:
            found = db.conn.execute(
                "SELECT * FROM automation_items WHERE item_key=?", (item_key,)
            ).fetchone()
            if not found:
                raise HTTPException(404, "Queue item was not found")
            row = dict(found)
            if str(row.get("status") or "").upper() != "DOWNLOADING":
                raise HTTPException(409, "Only an actively downloading torrent can be skipped here")
            now = content_automation.config.now_iso()
            updated = db.conn.execute(
                "UPDATE automation_items SET status='SKIPPING',download_state=?,error='Skip requested by operator',updated_at=? "
                "WHERE item_key=? AND status='DOWNLOADING'",
                (content_automation.TASK_LABELS["SKIPPING"], now, item_key),
            )
            db.conn.commit()
            if updated.rowcount != 1:
                raise HTTPException(409, "Queue item changed state; refresh Queue & Activity and retry")

        try:
            cleaned = await content_automation._cleanup_controlled_item(db, row)
        except Exception as exc:
            cleaned = False
            cleanup_error = f"{type(exc).__name__}: {exc}"
        else:
            cleanup_error = "The torrent is still present in the download engine"

        if cleaned is False:
            # If the torrent could not be removed, restore a pollable state so
            # the row doesn't become permanently stuck in SKIPPING. Never claim
            # success while a managed torrent may still be running.
            with db.lock:
                db.conn.execute(
                    "UPDATE automation_items SET status='DOWNLOADING',download_state=?,error=?,updated_at=? "
                    "WHERE item_key=? AND status='SKIPPING'",
                    (content_automation.TASK_LABELS["DOWNLOADING"],
                     f"Skip failed: {cleanup_error}", content_automation.config.now_iso(), item_key),
                )
                db.conn.commit()
            db.log(
                f"Content Automation: item skip failed for {row.get('title') or item_key}: {cleanup_error}",
                "WARN",
            )
            raise HTTPException(503, f"Could not skip this torrent safely: {cleanup_error}")

        content_automation._upsert(
            db, item_key, status="SKIPPED", retry_stage="", next_retry_at=None,
            failure_kind="", error="Skipped by operator", download_progress=0.0,
            download_rate=0, eta_seconds=-1,
            download_state=content_automation.TASK_LABELS["SKIPPED"],
        )
        db.log(
            f"Content Automation: skipped selected torrent {row.get('title') or item_key}",
            "INFO", event="content_automation.item_skipped",
            details={"automation_item": item_key, "torrent_hash": row.get("torrent_hash") or ""},
        )
        return {"ok": True, "item_key": item_key, "title": row.get("title"), "action": "Skipped"}


    # ------------------------------------------------------------------
    # SUBSECTION: Upload policy
    # Upload policy route.
    # ------------------------------------------------------------------
    @router.get("/api/upload-policy")
    async def upload_policy(request: Request):
        require_auth(request)
        if not telegram.client or not telegram.connected:
            return {"ok": False, "detail": "Telegram is not connected"}
        policy = await get_upload_policy(telegram.client)
        return {"ok": True, **upload_policy_dict(policy)}
