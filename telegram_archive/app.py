"""FastAPI app factory. One place that wires every module's singleton
together — nothing else in the package should reach for a bare module-
level `db`/`jobs`/etc. the way the original single-file v14 did.
"""
from __future__ import annotations

from .core.swallowed import note as _swallowed_note
from contextlib import asynccontextmanager
from pathlib import Path
import os
import json

from fastapi import FastAPI

from .core.auth import SessionStore, LoginRateLimiter
from .core.database import Database
from .core.governor import TransferGovernor
from .core.resource_manager import InteractiveResourceManager, restore_persisted_resource_controls
from .core.hardware_adaptive import detect_hardware, detect_hardware_cached
from .core.diagnostics import write_runtime_snapshot
from .core.request_context import new_request_id, set_trace_context
from .core.observability import parse_traceparent, set_context
from .core.process_topology import snapshot as process_topology_snapshot
from .core.http_middleware import TrustedProxyMiddleware, SecurityHeadersMiddleware, BrowserMutationGuardMiddleware, ResponseOptimizationMiddleware, APIRateLimitMiddleware
from .telegram.service import TelegramService
from .jobs.manager import JobManager
from .web.routes import build_router
from .engines.page_enricher import PageEnricher
from .playback.stream_server import StreamTokens, serve_local_player_feed
from . import config
from .operations import automation as content_automation
from .web.routes_test_bridge import register as register_test_bridge_routes
import asyncio
import time





# ==============================================================================
# SECTION: ASGI middleware
# Per-request correlation IDs and the HTTP/3 advertisement header.
# ==============================================================================

class ContentFilterMiddleware:
    """Mark the requests that show library content, so the database connections they open hide what the SFW / NSFW mode does not allow."""

    # Pages and data of the library. Management screens (Telegram, Transfers, Operations, Settings, jobs, AI) see everything.
    CONTENT = ("/api/videos", "/api/search", "/api/performers", "/api/studios", "/api/tags", "/api/tag-taxonomy", "/api/photos", "/api/channels",
               "/api/library", "/api/bootstrap", "/api/play", "/api/thumb", "/api/entities", "/api/entity-lookup", "/api/entity-images",
               "/api/media", "/api/suggestions", "/api/metadata", "/api/mapping", "/api/channel-maintenance", "/api/identify", "/api/captions", "/api/screen",
               "/stream", "/stream-cached", "/stream-compat", "/hls")

    def __init__(self, app, db=None):
        self.app = app
        self.db = db

    @classmethod
    def applies(cls, path: str) -> bool:
        return any(path == p or path.startswith(p + "/") for p in cls.CONTENT)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not self.applies(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return
        if self.db is not None:
            from .core import content_guard
            import asyncio
            if await asyncio.to_thread(content_guard.hidden, self.db, str(scope.get("path") or "")):
                from fastapi.responses import JSONResponse
                await JSONResponse({"detail": "Not found"}, status_code=404)(scope, receive, send)
                return
        from .core.content_mode import FILTER_ACTIVE
        token = FILTER_ACTIVE.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            FILTER_ACTIVE.reset(token)


class RequestCorrelationMiddleware:
    """Attach one lightweight correlation id to every HTTP request/response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        """Attach request and trace IDs to an HTTP request and record its outcome."""
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.lower(): v.decode("latin1", "ignore") for k, v in (scope.get("headers") or [])}
        rid = new_request_id(headers.get("x-request-id"))
        parsed = parse_traceparent(headers.get("traceparent"))
        if parsed:
            parent_trace, parent_span = parsed
            set_trace_context(parent_trace, parent_span)
        else:
            import uuid
            set_trace_context(uuid.uuid4().hex, uuid.uuid4().hex[:16])
        set_context(component="http")
        started = time.perf_counter()
        response_status = 500

        async def _send(message):
            if message.get("type") == "http.response.start":
                nonlocal response_status
                response_status = int(message.get("status") or 500)
                out = list(message.get("headers") or [])
                if not any(k.lower() == b"x-request-id" for k, _ in out):
                    out.append((b"x-request-id", rid.encode("ascii")))
                ctx = observability_context = set_context()
                trace = str(ctx.get("trace_id") or "-")
                span = str(ctx.get("span_id") or "-")
                if trace != "-" and span != "-" and not any(k.lower() == b"traceparent" for k, _ in out):
                    out.append((b"traceparent", f"00-{trace}-{span}-01".encode("ascii")))
                message = dict(message)
                message["headers"] = out
            await send(message)

        try:
            await self.app(scope, receive, _send)
        except Exception as exc:
            from .core.diagnostics import record_event
            record_event(
                event="http.request.exception", level="ERROR",
                message=f"{scope.get('method','GET')} {scope.get('path','/')}",
                error=exc, details={"http.method": scope.get("method"), "url.path": scope.get("path"),
                                  "duration_ms": round((time.perf_counter()-started)*1000.0, 2)},
            )
            raise
        finally:
            from .core.observability import record_http_request
            record_http_request(method=scope.get("method", "GET"), path=scope.get("path", "/"),
                                status=response_status, duration_ms=(time.perf_counter()-started)*1000.0)
            set_context(component="-")


def create_app(db: Database | None = None, *, session_store: SessionStore | None = None) -> FastAPI:
    """Build the FastAPI app and wire the database, governor, Telegram, jobs and routes together."""
    set_context(launch_id=os.getenv("ARCHIVE_X_LAUNCH_ID") or "-", component="startup")
    db = db or Database()
    if os.environ.get("ARCHIVE_X_DESKTOP") == "1":
        # The desktop app always opens in SFW: NSFW is a choice made each time (with the PIN), never something a restart leaves switched on.
        from .core import content_mode as _cm
        try:
            _cm.set_mode(db, _cm.DEFAULT_MODE)
        except Exception as exc:
            db.log(f"Could not reset the content mode at start: {type(exc).__name__}: {exc}"[:200], "WARN")
    governor = TransferGovernor(db)
    from .playback.media_tools import find_ffmpeg
    hardware_profile = detect_hardware_cached(find_ffmpeg(), config.PACKAGE_DIR)
    resources = InteractiveResourceManager(governor=governor, hardware_profile=hardware_profile)
    # Restore only non-expiring operator selections (including Manual / Until
    # changed). Temporary overrides are persisted as Automatic by the update
    # endpoint, so a restart never resurrects an expired temporary policy.
    try:
        restored_mode = restore_persisted_resource_controls(db, resources)
        if restored_mode != "automatic":
            db.log(f"Restored saved resource control policy: {restored_mode}")
    except Exception as exc:
        db.log(f"Could not restore saved resource controls: {type(exc).__name__}: {exc}", "WARN")
    telegram = TelegramService(db, governor)
    jobs = JobManager(db, telegram, governor, resources=resources)
    page_enricher = PageEnricher(db, telegram, priority_gate=jobs.background_priority, resources=resources, metadata_blocked=lambda: jobs.is_active("METADATA_SYNC"))
    # Production keeps sessions in SQLite by default. Tests may inject an
    # in-memory store so authenticated UI fixtures never persist into user data.
    sessions = session_store if session_store is not None else SessionStore(db)
    login_limiter = LoginRateLimiter()
    tokens = StreamTokens(db)
    # One shared playback engine (all channels), created on first playback.
    app_state = {"engine": None, "get_engine": None, "player_port": config.LOCAL_PLAYER_PORT, "player_host": config.LOCAL_PLAYER_HOST, "hardware_profile": hardware_profile, "internet_test": None}

    # Startup and shutdown: starts jobs and background watchers, then stops them on exit.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Do not hold the HTTP listener hostage to Telegram's MTProto handshake or
        # a slow get_me() call. The login page and local UI must remain available
        # when Telegram is offline, credentials need setup, or the network is slow.
        content_automation.disarm_on_boot(db)
        jobs.start()          # restores interrupted jobs; Content Automation and Upload Local require explicit Resume/Cancel

        # Upgrade existing snapshot directories locally. This startup task may
        # create validation.json/grid_sprite.jpg and repair the saved DB manifest
        # from existing JPEGs only; it never connects to Telegram or rebuilds
        # missing frames. Keep it off the ASGI event loop so normal requests stay
        # available while a larger library is backfilled.
        from .engines.preview_prefetch import backfill_existing_local_preview_assets_sync
        local_preview_backfill = asyncio.create_task(
            asyncio.to_thread(backfill_existing_local_preview_assets_sync, db),
            name="local-preview-backfill",
        )

        def _local_preview_backfill_done(task):
            if task.cancelled():
                return
            error = task.exception()
            if error is not None:
                try:
                    db.log(f"Local preview backfill failed: {type(error).__name__}: {error}", "WARN",
                           event="preview.local_backfill_failed")
                except Exception:
                    _swallowed_note("app.create_app.lifespan._local_preview_backfill_done")
                    pass
        local_preview_backfill.add_done_callback(_local_preview_backfill_done)

        # Remove legacy startup-only ranges from durable MP4-index folders.
        # Keep the MP4 moov/index segments and keyframe manifests intact; normal
        # playback range caching remains the only persistent video-byte cache.
        try:
            with db.lock:
                removed_setting = db.conn.execute(
                    "DELETE FROM settings WHERE key=?",
                    ("playback_first_seconds_cache_enabled",),
                ).rowcount
                if removed_setting:
                    db.conn.commit()
        except Exception as exc:
            db.log(f"Legacy playback-setting cleanup failed: {type(exc).__name__}: {exc}", "WARN")

        from .core.playback_index_cache import cleanup_legacy_startup_cache
        legacy_index_cleanup = asyncio.create_task(
            asyncio.to_thread(cleanup_legacy_startup_cache),
            name="legacy-mp4-index-cleanup",
        )

        def _legacy_index_cleanup_done(task):
            if task.cancelled():
                return
            error = task.exception()
            if error is not None:
                db.log(f"Legacy MP4 index cleanup failed: {type(error).__name__}: {error}", "WARN")
                return
            result = task.result() or {}
            if result.get("manifests_migrated") or result.get("bytes_removed"):
                db.log(
                    f"Cleaned {int(result.get('bytes_removed') or 0)} bytes from "
                    f"{int(result.get('manifests_migrated') or 0)} legacy MP4 index entries; "
                    "MP4 index and keyframe data were preserved.",
                    event="playback.legacy_index_cleanup",
                    details=result,
                )

        legacy_index_cleanup.add_done_callback(_legacy_index_cleanup_done)

        async def _connect_telegram_background():
            try:
                connected = await asyncio.wait_for(
                    telegram.connect(), timeout=config.TELEGRAM_BACKGROUND_CONNECT_TIMEOUT_SECONDS
                )
                if not connected:
                    db.log(f"Telegram remains disconnected; web UI is available ({telegram.status_text})", "WARN")
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError:
                telegram.connected = False
                db.log(
                    f"Telegram auto-connect exceeded {config.TELEGRAM_BACKGROUND_CONNECT_TIMEOUT_SECONDS:.0f}s; "
                    "web UI remains available. Retry connection from Settings.", "WARN"
                )
                try:
                    await asyncio.wait_for(telegram.disconnect(), timeout=3.0)
                except Exception:
                    _swallowed_note("app.create_app.lifespan._connect_telegram_background")
                    pass
            except Exception as exc:
                telegram.connected = False
                db.log(f"Telegram background startup failed: {type(exc).__name__}: {exc}; web UI remains available", "WARN")

        async def _telegram_connect_supervisor():
            """Initial connect, then retry transient failures with backoff.

            Previously a boot-time network outage, timeout or FloodWait left
            Telegram disconnected until the operator pressed Retry in Settings.
            Only transient states are retried: login-required, missing API
            credentials and invalidated sessions need the operator, and an
            interactive (code/QR) login in progress is never disturbed.
            """
            await _connect_telegram_background()
            delay = 30.0
            transient = ("TELEGRAM NETWORK TIMEOUT", "FLOODWAIT", "ERROR")
            while True:
                # While connected, poll the real socket state every few
                # seconds: `telegram.connected` is only a flag set at connect
                # time, and Telethon stops reconnecting on its own after a
                # longer outage (Wi-Fi drop, PC sleep), which previously left
                # jobs and playback failing until the app was restarted.
                await asyncio.sleep(5.0 if telegram.connected else delay)
                if telegram.connected:
                    delay = 30.0
                    if not telegram.connection_alive():
                        await telegram.recover_connection()
                    continue
                status = str(getattr(telegram, "status_text", "") or "").upper()
                if status.startswith("RECONNECTING") and getattr(telegram, "client", None) is not None:
                    # Keep reviving the same client so running engines that
                    # hold it recover too.
                    if await telegram.recover_connection():
                        delay = 30.0
                    else:
                        delay = 15.0
                    continue
                if (getattr(telegram, "session_invalidated", False)
                        or getattr(telegram, "_qr_login_task", None) or getattr(telegram, "_qr_login", None)
                        or not any(status.startswith(t) for t in transient)):
                    delay = 30.0
                    continue
                if status.startswith("FLOODWAIT"):
                    try:
                        delay = max(delay, float(status.split()[1].rstrip("S")) + 5)
                    except (IndexError, ValueError):
                        pass
                old = getattr(telegram, "client", None)
                if old is not None:
                    try:
                        await asyncio.wait_for(old.disconnect(), timeout=3.0)
                    except Exception:
                        _swallowed_note("app.create_app.lifespan._telegram_connect_supervisor")
                        pass
                db.log(f"Retrying Telegram connection after: {status[:120]}", "INFO", event="telegram.reconnect_retry")
                await _connect_telegram_background()
                delay = 30.0 if telegram.connected else min(300.0, delay * 2)

        telegram_connect_task = asyncio.create_task(
            _telegram_connect_supervisor(), name="telegram-background-connect"
        )
        async def _diagnostic_snapshot_watch():
            while True:
                try:
                    eng = app_state.get("engine")
                    # On a worker thread: it asks Windows for the python processes (PowerShell, up to 2 s each) and used to freeze the app for 8 s.
                    await asyncio.to_thread(
                        write_runtime_snapshot,
                        db=db, jobs=jobs, telegram=telegram, governor=governor,
                        extra={
                            "process": {"cwd": str(config.APP_DIR), "package_dir": str(config.PACKAGE_DIR), "topology": process_topology_snapshot().snapshot()},
                            "playback": {
                                "running": bool(getattr(eng, "running", False)) if eng else False,
                                "status": eng.status() if eng and hasattr(eng, "status") else None,
                            },
                            "preview": dict(app_state.get("preview_stats") or {}),
                            "telegram_capabilities": telegram.performance_status(),
                            "hardware": app_state.get("hardware_profile"),
                            "internet": app_state.get("internet_test"),
                        },
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    _swallowed_note("app.create_app.lifespan._diagnostic_snapshot_watch")
                    pass
                await asyncio.sleep(5)
        diagnostic_watch = asyncio.create_task(_diagnostic_snapshot_watch(), name="diagnostic-snapshot")

        async def _playback_resource_reconcile_watch():
            """Release playback resource leases that no longer have an owner.

            Browser range requests are authoritative for browser playback.
            External VLC/MPC-HC launches are tracked by the launcher process
            table. A stale coordinator lease must not block every background
            job indefinitely.
            """
            from .playback import vlc_launcher
            while True:
                try:
                    eng = app_state.get("engine")
                    browser_streams = int(getattr(eng, "active_http_requests", 0) or 0) if eng else 0
                    external_active = False
                    for proc in list(getattr(vlc_launcher, "_procs", {}).values()):
                        try:
                            if proc.poll() is None:
                                external_active = True
                                break
                        except Exception:
                            _swallowed_note("app.create_app.lifespan._playback_resource_reconcile_watch")
                            continue
                    expected = browser_streams + (1 if external_active else 0)
                    stale = await resources.reconcile_playback(
                        expected_streams=expected,
                        reason="activity_reconciliation",
                    )
                    if stale:
                        db.log(
                            f"Playback resource watchdog released {stale} stale lease(s); "
                            f"browser_streams={browser_streams}, external_player={external_active}",
                            "WARN", event="playback.resource_reconciled",
                            details={"stale_leases": stale, "browser_streams": browser_streams,
                                     "external_player_active": external_active},
                        )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    db.log(f"Playback resource reconciliation failed: {type(exc).__name__}: {exc}", "WARN")
                await asyncio.sleep(3)

        playback_resource_watch = asyncio.create_task(
            _playback_resource_reconcile_watch(), name="playback-resource-reconcile"
        )

        # Content Automation is strictly operator-started: nothing here may
        # create an automation job or start qBittorrent from saved settings.


        # Do not start external metadata-provider work during application launch.
        # Legacy reference repairs remain eligible for the normal user-started
        # Metadata & Assets workflow; startup only records that repair work is
        # available so opening the application stays predictable and provider
        # outages cannot make the first session look hung.
        repaired_channels = list(getattr(db, "reference_repair_channels", []) or [])
        if repaired_channels:
            if db.setting("stashdb_api_key") or db.setting("tpdb_api_token") or db.setting("tpdb_token"):
                db.log(
                    f"Metadata repair is pending for {len(repaired_channels)} channel(s); "
                    "deferred until the operator starts Metadata & Assets"
                )
            else:
                db.log("Unsafe title-only metadata links were reset; configure a metadata provider key to re-identify them", "WARN")

        # Playback snapshots are now built only by an explicit user-started
        # Snapshot Cache / Metadata & Assets operation. The previous idle watcher
        # could contact Telegram and extract 36 frames for unrelated videos during
        # application startup. Existing snapshots remain available from disk/DB.

        async def _asset_prefetch_watch():
            # Artwork is the final automatic stage. It waits until channel
            # catalogue/snapshot/provider work is quiet, preventing external
            # artwork traffic from competing with Telegram media work.
            from .engines.asset_prefetch import needs_prefetch
            while True:
                try:
                    if (telegram.connected and not resources.playback_active and not resources.background_paused
                        and not page_enricher.pending() and not page_enricher.snapshot_pending()
                        and not jobs.transfer_active() and not jobs.metadata_assets_active()
                        and not jobs.is_active("ASSET_PREFETCH") and not jobs.is_active("PREVIEW_PREFETCH")):
                        if not jobs.maintenance_suppressed("ASSET_PREFETCH") and await asyncio.to_thread(needs_prefetch, db):
                            jid = db.create_job("ASSET_PREFETCH", "", "", False, detail="{}")
                            jobs.submit(jid)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    db.log(f"Asset prefetch watcher failed: {type(exc).__name__}: {exc}", "WARN")
                await asyncio.sleep(15 if page_enricher.pending() or page_enricher.snapshot_pending() else 60)

        prefetch_watch = asyncio.create_task(_asset_prefetch_watch(), name="asset-prefetch-watch")
        from .engines import missing_images as _missing_images
        missing_images_watch = asyncio.create_task(_missing_images.refresh_loop(db), name="missing-entity-pictures")

        async def _player_feed_task():
            # Signed-link feed for VLC/MPC-HC (this PC and LAN devices).
            try:
                await serve_local_player_feed(app_state, tokens, config.LOCAL_PLAYER_PORT,
                                              config.LOCAL_PLAYER_HOST)
            except Exception as exc:
                db.log(f"Player feed could not start on {config.LOCAL_PLAYER_HOST}:{config.LOCAL_PLAYER_PORT}: {type(exc).__name__}: {exc}", "ERROR")
                raise
        feed = asyncio.create_task(_player_feed_task(), name="player-feed")
        def _feed_done(t):
            if not t.cancelled() and t.exception():
                db.log(f"Player feed on port {config.LOCAL_PLAYER_PORT} stopped: {t.exception()}", "ERROR")
        feed.add_done_callback(_feed_done)

        async def _warm_engine():
            # Wait for the asynchronous Telegram connection task, then create the
            # playback engine and establish its transfer-only connections before
            # the first Play. This task never requests media ranges or queues data.
            last_error_log = 0.0
            while True:
                try:
                    if telegram.connected and telegram.client is not None and app_state.get("get_engine"):
                        # Refresh Telegram's small/large file-operation limits on
                        # connect and periodically, before media transfers start.
                        await telegram.refresh_download_limits()
                        engine = await app_state["get_engine"]()
                        # Reconcile dropped transfer clients without rebuilding a
                        # healthy engine or waiting for the next Play request.
                        if engine is not None and engine.running:
                            await engine._ensure_transfer_sessions(engine.initial_transfer_sessions)
                    await asyncio.sleep(5.0 if telegram.connected else 1.0)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    now = asyncio.get_running_loop().time()
                    if now - last_error_log >= 30.0:
                        try:
                            db.log(f"Playback engine background warm/reconnect failed: {type(e).__name__}: {getattr(e, 'detail', e)}", "WARN")
                        except Exception:
                            _swallowed_note("app.create_app.lifespan._warm_engine")
                            pass
                        last_error_log = now
                    await asyncio.sleep(3.0)
        warm = asyncio.create_task(_warm_engine(), name="engine-warm-reconnect")
        try:
            phases = list(getattr(app.state, "startup_phases", []) or [])
            t0 = getattr(app.state, "startup_t0", None)
            if t0 is not None:
                total = time.monotonic() - t0
                detail = ", ".join(f"{name} {secs:.1f}s" for name, secs in phases)
                print(f"  Archive X ready in {total:.1f}s ({detail})")
                db.log(f"Startup ready in {total:.1f}s ({detail})", "INFO" if total < 20 else "WARN",
                       event="startup.timing",
                       details={"total_seconds": round(total, 2), **{n: round(v, 2) for n, v in phases}})
        except Exception:
            _swallowed_note("app.create_app.lifespan")
            pass
        yield
        warm.cancel()
        feed.cancel()
        try:
            route_usage.flush()
        except Exception:
            pass
        prefetch_watch.cancel()
        missing_images_watch.cancel()
        diagnostic_watch.cancel()
        playback_resource_watch.cancel()
        telegram_connect_task.cancel()
        await asyncio.gather(
            warm, feed, prefetch_watch, missing_images_watch,
            diagnostic_watch, playback_resource_watch, telegram_connect_task,
            return_exceptions=True,
        )
        # Let the read/repair-only startup worker finish before closing SQLite.
        await asyncio.gather(local_preview_backfill, legacy_index_cleanup, return_exceptions=True)
        # Teardown is best-effort per component so one failed service cannot
        # skip job recovery markers or leave the database connection open.
        try:
            await asyncio.wait_for(page_enricher.stop(), timeout=3)
        except Exception as exc:
            db.log(f"Page enricher shutdown error: {type(exc).__name__}: {exc}", "WARN")
        try:
            # Interrupted work is persisted; Upload Local resumes only after explicit operator choice.
            await asyncio.wait_for(jobs.stop(), timeout=6)   # a slow job must not hold the window shut: its checkpoint is what the next start resumes from
        except Exception as exc:
            db.log(f"Job manager shutdown error: {type(exc).__name__}: {exc}", "ERROR", event="shutdown.jobs_failed")
            try:
                jobs.recovery.mark_clean_shutdown()
            except Exception:
                _swallowed_note("app.create_app.lifespan")
                pass
        _eng = app_state.get("engine")
        if _eng is not None:
            try:
                await asyncio.wait_for(_eng.stop(), timeout=4)
            except Exception as exc:
                db.log(f"Playback engine shutdown error: {type(exc).__name__}: {exc}", "WARN")
            app_state["engine"] = None
        ai_service = getattr(app.state, "ai_assistant", None)
        if ai_service is not None:
            try:
                ai_service.close()
            except Exception as exc:
                db.log(f"Local AI shutdown error: {type(exc).__name__}: {exc}", "WARN", event="ai.runtime.shutdown")
        try:
            await asyncio.wait_for(telegram.disconnect(), timeout=3)   # a dead connection can take far longer than this to give up
        except Exception as exc:
            db.log(f"Telegram shutdown error: {type(exc).__name__}: {exc}", "WARN")
        from .core.http_client import close_async_sessions
        try:
            await close_async_sessions()
        except Exception as exc:
            db.log(f"HTTP session shutdown error: {type(exc).__name__}: {exc}", "WARN")
        finally:
            try:
                db.close()
            except Exception:
                _swallowed_note("app.create_app.lifespan")
                pass
            # Written after all app-owned service cleanup and the DB close. The
            # Windows shutdown guard waits for this marker before using its
            # process-tree fallback, so it cannot interrupt the normal cleanup.
            try:
                from .core.shutdown_guard import write_shutdown_completion_marker
                write_shutdown_completion_marker(app)
            except Exception:
                _swallowed_note("app.create_app.lifespan")
                pass

    app = FastAPI(title="Archive X", lifespan=lifespan)

    @app.exception_handler(json.JSONDecodeError)
    async def _malformed_json_handler(request, exc):
        # Handlers call request.json() directly; a malformed body is a client
        # error (400), not an internal server error.
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "Request body must be valid JSON"}, status_code=400)
    app.state.archive_x_hardware = hardware_profile
    from .core.route_usage import RouteUsage, RouteUsageMiddleware
    route_usage = RouteUsage(config.LOG_DIR / "route-usage.json")
    app.state.route_usage = route_usage
    app.add_middleware(RouteUsageMiddleware, usage=route_usage)   # innermost, so the route is known when it answers
    app.add_middleware(ContentFilterMiddleware, db=db)
    app.add_middleware(RequestCorrelationMiddleware)
    app.add_middleware(APIRateLimitMiddleware)
    app.add_middleware(BrowserMutationGuardMiddleware)
    app.add_middleware(ResponseOptimizationMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    _proxy_key = os.getenv("ARCHIVE_X_PROXY_KEY", "").strip()
    if _proxy_key:   # the Rust front door is in front of this server; outermost, so every later layer sees the real client address
        app.add_middleware(TrustedProxyMiddleware, key=_proxy_key)
    from .core import desktop_mode
    if desktop_mode.enabled():
        desktop_mode.install(app, db, sessions)   # outermost guard + /desktop/session and /desktop/shutdown
    app.state.db = db
    app.state.governor = governor
    app.state.resources = resources
    app.state.telegram = telegram
    app.state.jobs = jobs
    app.state.sessions = sessions
    app.state.tokens = tokens
    app.state._player_state = app_state

    router = build_router(
        db=db, governor=governor, telegram=telegram,
        jobs=jobs, sessions=sessions, login_limiter=login_limiter,
        tokens=tokens, player_state=app_state, resources=resources,
        page_enricher=page_enricher,
    )
    app.include_router(router)
    # Live Test Bridge is a separate, capability-scoped surface. It is added
    # after the canonical router so it cannot alter normal application flows.
    # The route module uses only app-owned singletons and keeps test tokens
    # short-lived/in-memory.
    bridge = register_test_bridge_routes(app.router, {
        "db": db, "sessions": sessions, "telegram": telegram,
        "jobs": jobs, "governor": governor, "player_state": app_state,
    })
    app.state.live_test_bridge = bridge
    from .web.routes_ai import register_ai_routes
    ai = register_ai_routes(app.router, {
        "db": db, "sessions": sessions, "telegram": telegram,
        "jobs": jobs, "governor": governor,
    }, bridge)
    app.state.ai_assistant = ai
    # The catch-all that serves the React app must come after every other route (including the two added above),
    # or it would answer 404 to their GET requests.
    from .web.routes_frontend import register_frontend_routes
    _root = Path(__file__).resolve().parents[1]
    # The web app's build: apps/web/dist from a checkout, frontend/dist inside the frozen backend (see packaging/backend/archive_x_backend.spec).
    register_frontend_routes(app.router, dist_dir=_root / "apps" / "web" / "dist" if (_root / "apps" / "web" / "dist").is_dir() else _root / "frontend" / "dist")
    return app
