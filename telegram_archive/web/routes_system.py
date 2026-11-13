"""Explicit registration for static assets, diagnostics, login, and status."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Static assets
#       - Health and diagnostics
#       - Login and session
#       - App shell and status
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import hmac
import json
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..core.auth import (
    SETUP_COMPLETE_FLAG,
    SETUP_REQUIRED_FLAG,
    is_setup_required,
    set_web_login,
    verify_password,
)
from ..core.session_cookie import set_session_cookie
from ..operations import automation as content_automation
from ..telegram.service import TELETHON_AVAILABLE


def register_system_routes(
    router: APIRouter,
    *,
    config,
    db,
    governor,
    jobs,
    login_limiter,
    ps,
    require_auth,
    sessions,
    telegram,
    _active_jobs_view,
    client_key,
    status_snapshot,
    FloodWaitError,
) -> dict[str, object]:
    """Register static asset, diagnostics, login and status routes."""
    ICON_DIRS = (config.ICON_DIR, config.PACKAGE_DIR.parent / "icons",
                 Path(__file__).resolve().parent / "static" / "icons", config.PACKAGE_DIR.parent)
    ICON_FILES = {"Telegram": "tg.jpg", "VLC": "vlc.png", "MPC-HC": "mpc.png", "Archive X": "archive-x.png"}


    # ------------------------------------------------------------------
    # SUBSECTION: Static assets
    # Icons, UI assets and fonts.
    # ------------------------------------------------------------------
    @router.get("/api/icons/status")
    async def icons_status(request: Request):
        """GET /api/icons/status: Icons status."""
        require_auth(request)
        found = {}
        for label, fname in ICON_FILES.items():
            hit = next((str(d / fname) for d in ICON_DIRS if (d / fname).is_file()), "")
            found[label] = hit
        return {"icons": found, "folder": str(config.ICON_DIR)}
    ICON_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp", ".svg": "image/svg+xml"}

    @router.get("/static/icons/{name}")
    async def static_icon(name: str):
        """Playback button icons (data/icons/ first, then the bundled folder)."""
        from fastapi.responses import Response
        suffix = Path(name).suffix.lower()
        if "/" in name or "\\" in name or ".." in name or suffix not in ICON_TYPES:
            raise HTTPException(404)
        for folder in ICON_DIRS:
            if folder == config.PACKAGE_DIR.parent and name not in ICON_FILES.values():
                continue
            path = folder / name
            if path.is_file():
                return Response(await asyncio.to_thread(path.read_bytes), media_type=ICON_TYPES[suffix],
                                headers={"Cache-Control": "public, max-age=86400"})
        raise HTTPException(404)

    @router.get("/static/icons/ax-icons/{name}")
    async def static_icon_pack_item(name: str):
        """Serve individual supplied Archive X sprite glyphs and their license maps."""
        from fastapi.responses import Response
        suffix = Path(name).suffix.lower()
        if "/" in name or "\\" in name or ".." in name or suffix not in {".svg", ".txt"}:
            raise HTTPException(404)
        path = Path(__file__).resolve().parent / "static" / "icons" / "ax-icons" / name
        if not path.is_file():
            raise HTTPException(404)
        media = "image/svg+xml" if suffix == ".svg" else "text/plain; charset=utf-8"
        return Response(await asyncio.to_thread(path.read_bytes), media_type=media,
                        headers={"Cache-Control": "public, max-age=2592000, immutable"})

    FONT_TYPES = {".woff2": "font/woff2"}

    @router.get("/static/fonts/{name}")
    async def static_font(name: str):
        """Serve an immutable bundled application font; never fetch from the network."""
        from fastapi.responses import Response
        from ..core.font_manager import ensure_font, font_specs
        suffix = Path(name).suffix.lower()
        if "/" in name or "\\" in name or ".." in name or name not in font_specs() or suffix not in FONT_TYPES:
            raise HTTPException(404)
        path = await asyncio.to_thread(ensure_font, name)
        if not path or not path.is_file():
            raise HTTPException(404)
        return Response(await asyncio.to_thread(path.read_bytes), media_type=FONT_TYPES[suffix],
                        headers={"Cache-Control": "public, max-age=2592000, immutable"})


    # ------------------------------------------------------------------
    # SUBSECTION: Health and diagnostics
    # Health, watchdogs, library health and metrics.
    # ------------------------------------------------------------------
    @router.get("/health")
    async def health():
        """GET /health: Health."""
        return {"status": "ok", "version": config.APP_VERSION, "telegram_available": TELETHON_AVAILABLE}

    @router.get("/api/watchdogs")
    async def watchdogs_status(request: Request):
        """Return watchdog/liveness state for diagnostics and the Operations UI."""
        require_auth(request)
        return await asyncio.to_thread(jobs.watchdogs.snapshot) if hasattr(jobs, "watchdogs") else {"overall": "UNKNOWN", "jobs": []}

    @router.get("/api/diagnostics/ignored-errors")
    def diagnostics_ignored_errors(request: Request):
        """Errors that best-effort code ignored on purpose, most frequent first, so a quiet failure can be traced."""
        require_auth(request)
        from ..core import swallowed
        return {"ok": True, "items": swallowed.snapshot(100)}

    @router.get("/api/diagnostics/summary")
    def diagnostics_summary(request: Request):
        require_auth(request)
        from ..core.diagnostics import observability_snapshot, metadata_assets_diagnostics, telegram_transfer_diagnostics, content_automation_diagnostics
        payload = observability_snapshot(recent_limit=60, errors_limit=30)
        from ..core import swallowed
        payload["ignored_errors"] = swallowed.snapshot(20)
        payload["metadata_assets"] = metadata_assets_diagnostics(db, limit=8)
        payload["telegram_transfer"] = telegram_transfer_diagnostics(db, limit=8)
        payload["content_automation"] = content_automation_diagnostics(db, limit=8)
        from ..core.observability import diagnostic_health
        payload["diagnostic_health"] = diagnostic_health()
        payload["watchdogs"] = jobs.watchdogs.snapshot() if hasattr(jobs, "watchdogs") else {}
        payload["active_jobs"] = _active_jobs_view()
        payload["telegram"] = telegram.performance_status() if hasattr(telegram, "performance_status") else {}
        return payload

    @router.get("/api/operations/library-health")
    async def library_health_report(request: Request):
        """Report missing preview assets, cache issues, and stale background jobs."""
        require_auth(request)
        try:
            limit = max(1, min(1000, int(request.query_params.get("limit") or 100)))
        except ValueError:
            raise HTTPException(400, "limit must be an integer")
        try:
            stale_hours = max(1, min(720, int(request.query_params.get("stale_job_hours") or 24)))
        except ValueError:
            raise HTTPException(400, "stale_job_hours must be an integer")
        from ..core.library_health import diagnose
        return await asyncio.to_thread(diagnose, db, limit=limit, stale_job_hours=stale_hours)

    @router.get("/api/operations/library-summary")
    async def library_catalogue_summary(request: Request):
        """Return the current published-library counts for the Operations overview."""
        require_auth(request)
        from ..core.library_health import library_catalogue_counts
        return await asyncio.to_thread(library_catalogue_counts, db)

    @router.post("/api/operations/library-health/repair-artwork")
    async def library_health_repair_artwork(request: Request):
        """Repair artwork byte-cache misses from matching canonical local files only."""
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        try:
            limit = max(1, min(500, int(body.get("limit", 100) or 100)))
        except (TypeError, ValueError):
            raise HTTPException(400, "limit must be an integer")
        from ..core.library_health import repair_artwork_cache_from_disk
        result = await asyncio.to_thread(repair_artwork_cache_from_disk, db, limit=limit)
        return {"ok": True, **result}

    @router.get("/api/diagnostics/playback")
    async def diagnostics_playback_snapshot(request: Request):
        """Return a fast, bounded playback-only diagnostic report."""
        require_auth(request)
        from ..core.diagnostics import browser_diagnostics_snapshot, recent_structured_events, observability_snapshot
        from ..core.ai_evidence import _tail_lines
        eng = ps.get("engine")
        server = eng.status() if eng else {"running": False}
        recent=[x for x in recent_structured_events(100) if str(x.get("event") or "").startswith("playback.")][-40:]
        playback_log_path = Path(config.PLAYBACK_LOG_PATH)
        return {
            "schema": 1, "kind": "PLAYBACK_DIAGNOSTICS", "captured_at": config.now_iso(),
            "playback": server, "browser": browser_diagnostics_snapshot(),
            "playback_log_path": str(playback_log_path.resolve()),
            "playback_log_tail": _tail_lines(playback_log_path, 200),
            "recent_playback_events": recent,
            "diagnostic_health": (observability_snapshot(recent_limit=20, errors_limit=10).get("diagnostic_health") or {}),
        }

    @router.post("/api/diagnostics/playback-incident")
    async def diagnostics_playback_incident(request: Request):
        """Capture a small, immediate playback incident snapshot.

        This path intentionally avoids full diagnostic collection, process
        topology, filesystem scans, and large log reads so a stalled player can
        still produce evidence while the application is degraded.
        """
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        from ..core.diagnostics import record_event, recent_structured_events
        from ..core.ai_evidence import _tail_lines
        eng = ps.get("engine")
        server = eng.status() if eng else {"running": False}
        media = body.get("media") if isinstance(body.get("media"), dict) else {}
        incident = {
            "reason": str(body.get("reason") or "unknown")[:40],
            "channel_id": body.get("channel_id"),
            "message_id": body.get("message_id"),
            "position": media.get("current_time"),
            "duration": media.get("duration"),
            "ready_state": media.get("ready_state"),
            "network_state": media.get("network_state"),
            "paused": media.get("paused"),
            "seeking": media.get("seeking"),
            "buffered": media.get("buffered", [])[:12] if isinstance(media.get("buffered"), list) else [],
            "seekable": media.get("seekable", [])[:12] if isinstance(media.get("seekable"), list) else [],
            "error": media.get("error"),
            "browser_session_id": str(body.get("session_id") or "-")[:80],
            "captured_at": config.now_iso(),
        }
        playback_keys={
            "running","transfer_connections","concurrency","adaptive_max_concurrency","queued","active",
            "http_streams","cached_videos","downloaded","served","playback_sessions","playback_generations",
            "speed","flood_premium_waits","flood_premium_wait_until","completed_by_priority","metrics",
            "playback_qos","buffer_controller","range_policy"
        }
        playback={k:server.get(k) for k in playback_keys if k in server}
        recent=[x for x in recent_structured_events(80) if str(x.get("event") or "").startswith("playback.")][-30:]
        record_event(
            event="playback.incident", level="WARN",
            message=f"Playback incident: {incident['reason']}",
            details={"component":"playback","incident":incident,"server_playback":playback,"recent_playback_events":recent},
        )
        playback_log_path = Path(config.PLAYBACK_LOG_PATH)
        return {
            "ok": True, "incident": incident, "playback": playback,
            "playback_log_path": str(playback_log_path.resolve()),
            "playback_log_tail": _tail_lines(playback_log_path, 120),
            "recent_playback_events": recent,
        }

    @router.get("/api/diagnostics/metrics")
    async def diagnostics_metrics(request: Request):
        require_auth(request)
        from ..core.observability import http_metrics_snapshot
        return http_metrics_snapshot()

    @router.post("/api/diagnostics/metrics/reset")
    async def diagnostics_metrics_reset(request: Request):
        require_auth(request)
        from ..core.observability import reset_http_metrics
        return reset_http_metrics()

    @router.get("/api/diagnostics/metrics/export")
    async def diagnostics_metrics_export(request: Request):
        """Download the current bounded HTTP telemetry window without persisting it."""
        require_auth(request)
        from datetime import datetime, timezone
        from fastapi.responses import Response
        from ..core.observability import http_metrics_snapshot
        payload = http_metrics_snapshot()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="archive-x-http-telemetry-{stamp}.json"',
                "Cache-Control": "no-store",
            },
        )

    @router.get("/api/diagnostics/logs")
    async def diagnostics_logs(request: Request, limit: int = 120, level: str = ""):
        require_auth(request)
        from ..core.diagnostics import recent_structured_events
        items = recent_structured_events(limit=max(1, min(int(limit or 120), 500)))
        wanted = str(level or "").strip().upper()
        if wanted:
            items = [x for x in items if str(x.get("level") or "").upper() == wanted]
        return {"count": len(items), "items": items}

    # Take a diagnostics report from the browser.
    @router.post("/api/diagnostics/client")
    async def diagnostics_client(request: Request):
        require_auth(request)
        from ..core.diagnostics import record_event
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid diagnostics payload")
        events = body.get("events") if isinstance(body, dict) else None
        if not isinstance(events, list):
            raise HTTPException(400, "events must be an array")
        snapshot = body.get("snapshot") if isinstance(body, dict) and isinstance(body.get("snapshot"), dict) else {}
        resources = body.get("resources") if isinstance(body, dict) and isinstance(body.get("resources"), list) else []
        session_id = str(body.get("session_id") or snapshot.get("session_id") or "-")[:80]
        from ..core.diagnostics import ingest_browser_diagnostics
        ingest_browser_diagnostics(session_id=session_id, snapshot=snapshot, resources=resources, events=events)
        # Keep the durable event log useful without writing a full Resource Timing
        # sample every five seconds. Browser snapshots are persisted at a bounded
        # cadence; detailed resource samples remain in the diagnostic report ledger.
        try:
            from ..core import diagnostics as _diag_mod
            import time as _diag_time
            now_m = _diag_time.monotonic()
            with _diag_mod._BROWSER_LOCK:
                last_m = float(_diag_mod._BROWSER_STATE.get("last_log_monotonic") or 0.0)
            important = any(isinstance(x, dict) and str(x.get("level") or "").upper() in {"WARN", "ERROR"} for x in events[:40])
            if important or now_m - last_m >= 15.0:
                snap = snapshot if isinstance(snapshot, dict) else {}
                record_event(event="browser.telemetry.snapshot", level="WARN" if important else "INFO", message="Browser diagnostic snapshot", details={"component":"browser","session_id":session_id,"browser_snapshot":snap})
                # Avoid adding another mutable field to the public snapshot payload;
                # this marker only throttles durable log writes in the current process.
                from ..core import diagnostics as _diag_mod
                with _diag_mod._BROWSER_LOCK:
                    _diag_mod._BROWSER_STATE["last_log_monotonic"] = now_m
        except Exception:
            _swallowed_note("routes_system.register_system_routes.diagnostics_client")
            pass
        accepted = 0
        for raw in events[:40]:
            if not isinstance(raw, dict):
                continue
            typ = str(raw.get("type") or "client.event")[:80]
            message = str(raw.get("message") or "")[:1000]
            details = {
                "component": "browser",
                "url": str(raw.get("url") or "")[:500],
                "page": str(raw.get("page") or "")[:300],
                "status": raw.get("status"),
                "method": str(raw.get("method") or "")[:16],
                "duration_ms": raw.get("duration_ms"),
                "browser": str(raw.get("browser") or "")[:100],
                "data": raw.get("data") if isinstance(raw.get("data"), dict) else {},
            }
            level = str(raw.get("level") or ("ERROR" if typ in {"js.exception", "js.unhandled_rejection", "api.error", "api.network_error"} else ("WARN" if typ == "resource.load_error" else "INFO"))).upper()
            if level not in {"DEBUG", "INFO", "WARN", "ERROR", "SUCCESS"}:
                level = "INFO"
            record_event(event=typ, level=level, message=message, details=details)
            accepted += 1
        return {"ok": True, "accepted": accepted}


    # ------------------------------------------------------------------
    # SUBSECTION: Login and session
    # Login, setup, logout and exit.
    # ------------------------------------------------------------------
    @router.post("/api/auth/setup")
    async def setup_auth(request: Request):
        """Public one-time administrator credential provisioning."""
        if not is_setup_required(db):
            raise HTTPException(409, "Administrator setup is already complete")
        # The server listens on the LAN by default. Without this check the first
        # device on the network to reach the page could claim the admin account.
        # The launcher opens https://127.0.0.1, so normal first run is unaffected.
        if os.getenv("TELEGRAM_ARCHIVE_ALLOW_REMOTE_SETUP", "0") != "1":
            host = request.client.host if request.client else ""
            if host not in {"127.0.0.1", "::1", "localhost"}:
                raise HTTPException(403, "Finish first-run setup on the PC running Archive X (open https://127.0.0.1:%d)" % config.PORT)
        data = await request.json()
        username = str(data.get("username") or "").strip()
        password = str(data.get("password") or "")
        confirmation = str(data.get("confirmation") or "")
        if not username or len(username) > 80:
            raise HTTPException(400, "Username is required")
        if len(password) < 12:
            raise HTTPException(400, "Password must be at least 12 characters")
        if password != confirmation:
            raise HTTPException(400, "Passwords do not match")
        set_web_login(db, username, password)
        db.set_setting(SETUP_COMPLETE_FLAG, config.now_iso())
        db.set_setting(SETUP_REQUIRED_FLAG, "")
        sessions.destroy_all()
        db.log("Web dashboard administrator credential setup completed")
        return {"ok": True, "username": username}

    @router.post("/login")
    async def login_submit(request: Request):
        """POST /login: Login submit."""
        key = client_key(request)
        login_limiter.check(key)
        data = await request.json()
        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))

        if is_setup_required(db):
            raise HTTPException(428, "Administrator credential setup is required")
        stored_username = db.setting("web_username", "admin")
        stored_hash = db.setting("web_password_hash")

        ok = bool(stored_hash) and hmac.compare_digest(username, str(stored_username)) and verify_password(password, stored_hash)
        if not ok:
            login_limiter.record(key)
            db.log(f"Failed web login attempt from {key}", "WARN")
            raise HTTPException(401, "Invalid username or password")

        login_limiter.clear(key)
        token = sessions.create(username)
        db.log(f"Web login succeeded from {key}")
        resp = JSONResponse({"ok": True})
        set_session_cookie(resp, request, token)
        return resp

    @router.post("/logout")
    async def logout(request: Request):
        """POST /logout: Logout."""
        token = request.cookies.get(config.SESSION_COOKIE)
        sessions.destroy(token)
        resp = JSONResponse({"ok": True})
        set_session_cookie(resp, request)
        return resp

    @router.get("/api/app/ping")
    async def app_ping(request: Request):
        """Lightweight authenticated liveness probe used by the Exit app status screen."""
        require_auth(request)
        return JSONResponse({"ok": True, "version": config.APP_VERSION}, headers={"Cache-Control": "no-store"})

    @router.post("/api/app/exit")
    async def exit_archive_x(request: Request):
        """Request a graceful application shutdown without logging out of the web session first."""
        require_auth(request)
        request_shutdown = getattr(request.app.state, "request_shutdown", None)
        if not callable(request_shutdown):
            raise HTTPException(503, "This server does not expose a graceful shutdown controller; restart it from its launcher.")

        try:
            active = db.active_jobs()
            active_count = len(active or [])
        except Exception as exc:
            active_count = -1
            try:
                db.log(f"Could not count active jobs before graceful exit: {type(exc).__name__}: {exc}", "WARN")
            except Exception:
                _swallowed_note("routes_system.register_system_routes.exit_archive_x")
                pass
        try:
            db.log(
                f"Graceful Archive X exit requested from the authenticated UI; active_jobs={active_count}",
                "INFO", event="app.exit_requested", details={"active_jobs": active_count},
            )
        except Exception:
            _swallowed_note("routes_system.register_system_routes.exit_archive_x")
            pass

        # Register the shutdown completion marker watched by the Windows .bat
        # supervisor. The supervisor is the Python process's parent, so its
        # last-resort process-tree cleanup cannot terminate itself by accident.
        try:
            from ..core.shutdown_guard import prepare_shutdown_completion_marker
            marker_prepared = prepare_shutdown_completion_marker(request.app)
            if os.name == "nt" and os.environ.get("ARCHIVE_X_SHUTDOWN_SUPERVISED") == "1" and not marker_prepared:
                db.log(
                    "Could not prepare the Windows shutdown completion marker; normal graceful shutdown will continue",
                    "WARN", event="app.exit_marker_prepare_failed",
                )
        except Exception as exc:
            try:
                db.log(f"Shutdown completion marker setup failed: {type(exc).__name__}: {exc}", "WARN", event="app.exit_marker_prepare_failed")
            except Exception:
                _swallowed_note("routes_system.register_system_routes.exit_archive_x")
                pass

        # Let the 202 response leave the server before switching the server's
        # shutdown flag. The ASGI lifespan then checkpoints jobs and stops the
        # watchdog guardian, playback engine, Telegram client, and database.
        asyncio.get_running_loop().call_later(0.8, request_shutdown)
        return JSONResponse({
            "ok": True,
            "shutdown": "scheduled",
            "active_jobs": active_count,
            "message": "Archive X is stopping gracefully. Job recovery state will be saved where supported.",
        }, status_code=202, headers={"Cache-Control": "no-store"})


    # ------------------------------------------------------------------
    # SUBSECTION: App shell and status
    # Index page, status and bootstrap.
    # ------------------------------------------------------------------
    @router.get("/api/status")
    async def status(request: Request):
        """GET /api/status: Status."""
        require_auth(request)
        _data, payload = await status_snapshot()
        from fastapi.responses import Response
        return Response(content=payload, media_type="application/json", headers={"Cache-Control": "no-store"})

    @router.get("/api/bootstrap")
    async def bootstrap(request: Request):
        """Single authenticated startup payload for common control-plane data."""
        require_auth(request)

        def _channels_sync():
            with db.reading() as conn:
                return [dict(r) for r in conn.execute("SELECT c.*, COUNT(DISTINCT cm.id) video_count FROM channels c JOIN archive_x_library_media cm ON cm.source_channel=c.telegram_id GROUP BY c.id ORDER BY c.title").fetchall()]

        def _settings_sync():
            cfg = telegram.config()
            api_hash = str(cfg.get("api_hash") or "")
            telegram_s = {
                "configured": bool(cfg.get("api_id") and api_hash),
                "api_verified": bool(cfg.get("api_id") and api_hash and telegram.client is not None and not str(telegram.status_text or "").startswith("ERROR")),
                "api_id": cfg.get("api_id") or "",
                "api_hash_masked": (api_hash[:4] + "…" + api_hash[-4:]) if len(api_hash) > 8 else ("set" if api_hash else ""),
                "phone": cfg.get("phone") or "",
                "connected": telegram.connected, "status": telegram.status_text,
            }
            stash = db.setting("stashdb_api_key") or ""
            tpdb = db.setting("tpdb_api_token") or db.setting("tpdb_token") or ""
            def mask(v): return (v[:4] + "…" + v[-4:]) if len(v) > 8 else ("set" if v else "")
            metadata_s = {
                "stashdb_api_key": mask(stash), "stashdb_configured": bool(stash),
                "tpdb_token": mask(tpdb), "tpdb_configured": bool(tpdb),
            }
            from ..engines import telegram_sender
            send_s = telegram_sender.settings(db)
            experience_s = {
                "autoplay": bool(db.setting("ta_autoplay", "1") not in {"0", "false", "False"}),
                "autonext": bool(db.setting("ta_autonext", "1") not in {"0", "false", "False"}),
                "autonext_mode": db.setting("ta_autonext_mode", "context"),
                "theme_mode": ("system" if db.setting("ax_theme_mode", "system") == "auto-time" else db.setting("ax_theme_mode", "system")),
                "theme_preset": (db.setting("ax_theme_preset", "nord") if db.setting("ax_theme_preset", "nord") in {"nord", "nero-gold", "black-essence","pastel-lavender", "pastel-mint", "minimal-slate", "minimal-sand", "custom"} else "nord"),
                "grid_density": (db.setting("ax_grid_density", "default") if db.setting("ax_grid_density", "default") in {"compact", "default", "spacious"} else "default"),
                "live_seek_preview": bool(db.setting("ta_live_seek_preview", "1") not in {"0", "false", "False"}),
                # Include this in bootstrap as well as the dedicated settings route;
                # otherwise the seeded GET cache can temporarily default it to enabled.
                "resume_prompt": bool(db.setting("ta_resume_prompt", "1") not in {"0", "false", "False"}),
                "partial_cache_prompt": bool(db.setting("ta_partial_cache_prompt", "0") not in {"0", "false", "False"}),
            }
            from ..core.auth import is_default_login
            web_s = {"username": db.setting("web_username", "admin"), "default_login": is_default_login(db)}
            automation_s = content_automation.get_config(db, masked=True)
            return telegram_s, metadata_s, send_s, experience_s, web_s, automation_s

        from ..core.media_deps import preflight
        status_value, channels_value, preflight_value, settings_value = await asyncio.gather(
            status_snapshot(),
            asyncio.to_thread(_channels_sync),
            asyncio.to_thread(preflight),
            asyncio.to_thread(_settings_sync),
        )
        telegram_s, metadata_s, send_s, experience_s, web_s, automation_s = settings_value
        return {
            "status": status_value[0],
            "channels": channels_value,
            "preflight": preflight_value,
            "settings": {"telegram": telegram_s, "metadata": metadata_s, "send": send_s, "experience": experience_s, "weblogin": web_s},
            "automation": automation_s,
        }

    @router.post("/api/telegram/send-code")
    async def telegram_send_code(request: Request):
        """POST /api/telegram/send-code: Telegram send code."""
        require_auth(request)
        data = await request.json()
        try:
            return await telegram.send_login_code(str(data.get("phone", "")).strip())
        except FloodWaitError as e:
            seconds = getattr(e, "seconds", 60)
            governor.note_floodwait(seconds)
            raise HTTPException(429, f"Telegram FloodWait: wait {seconds} seconds")
        except Exception as e:
            db.log(f"Telegram send-code error: {e}", "ERROR")
            raise HTTPException(400, str(e))

    return {"telegram_send_code": telegram_send_code}
