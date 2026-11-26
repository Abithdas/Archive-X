"""Explicit registration for settings, controls, identity, and live operations."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Schedule and cache settings
#       - Metadata settings
#       - Global controls and WebSocket
#       - Web login and job read
#       - Telegram login aliases
#       - Library listing
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .video_lists import name_lists_sql


def _without_ages(value):
    """A copy of a status part without the counters that only say how long ago a heartbeat was (keys ending in `_age`)."""
    if isinstance(value, dict):
        return {k: _without_ages(v) for k, v in value.items() if not str(k).endswith("_age")}
    if isinstance(value, list):
        return [_without_ages(v) for v in value]
    return value


def steady_status_json(data) -> str:
    """The status as JSON with the watchdog heartbeat ages left out: equal for two snapshots when nothing but those ages moved."""
    if isinstance(data, dict) and "watchdogs" in data:
        data = {**data, "watchdogs": _without_ages(data["watchdogs"])}
    return json.dumps(data, sort_keys=True, default=str, separators=(",", ":"))


def register_settings_routes(
    router: APIRouter,
    *,
    db,
    jobs,
    telegram,
    config,
    ps,
    require_auth,
    login_limiter,
    sessions,
    client_key,
    session_from_ws,
    status_snapshot,
    fts_prefix_query,
    SETUP_COMPLETE_FLAG,
    SETUP_REQUIRED_FLAG,
    is_setup_required,
    set_session_cookie,
    verify_password,
    telegram_send_code,
    telegram_verify_code,
    telegram_verify_2fa,
) -> None:

    # ------------------------------------------------------------------
    # SUBSECTION: Schedule and cache settings
    # Scan schedule and playback cache policy.
    # ------------------------------------------------------------------
    """Register settings, global control, WebSocket and library routes."""
    @router.post("/api/settings/scan-schedule")
    async def save_scan_schedule(request: Request):
        require_auth(request)
        data = await request.json()
        def valid_clock(value, fallback):
            value = str(value or fallback).strip()
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise HTTPException(400, f"Invalid time: {value}")
            return value
        start = valid_clock(data.get("start"), "01:00")
        end = valid_clock(data.get("end"), "07:00")
        db.set_setting("metadata_scan_window_enabled", "1" if bool(data.get("enabled")) else "0")
        db.set_setting("metadata_scan_window_start", start)
        db.set_setting("metadata_scan_window_end", end)
        db.log(f"Metadata & Assets daily window saved: {'enabled' if data.get('enabled') else 'disabled'} {start}-{end}")
        return {"ok": True, "enabled": bool(data.get("enabled")), "start": start, "end": end}

    # Return cache policy and disk usage.
    @router.get("/api/settings/cache-usage")
    async def get_cache_usage(request: Request):
        require_auth(request)
        from ..playback.stream_server import configure_range_cache_policy
        cache_policy = configure_range_cache_policy(
            budget_gib=db.setting("playback_range_cache_budget_gib", 50),
            retention=db.setting("playback_range_cache_retention", "idle_30m"),
        )
        from ..playback.stream_server import configure_playback_tuning, kept_range_cache_summary
        tuning = configure_playback_tuning(
            readahead_seconds=db.setting("playback_readahead_seconds", 180),
            ram_mb=db.setting("playback_ram_cache_mb", 128),
        )
        def scan_usage():
            """Walk the cache folders and total their sizes off the event loop."""
            from .. import config as app_config
            from ..core.playback_index_cache import cleanup_legacy_startup_cache
            import os
            cleanup_legacy_startup_cache()
            def tree_bytes(root):
                total = 0
                if not root.exists(): return 0
                for base, _, files in os.walk(root):
                    for name in files:
                        try: total += (Path(base) / name).stat().st_size
                        except OSError: pass
                return total
            snapshots = Path(app_config.PLAYER_CACHE_DIR) / "preview_frames"
            index_root = Path(app_config.PLAYER_CACHE_DIR) / "mp4_index"
            mp4_bytes = tree_bytes(index_root)
            keyframe_bytes = 0
            manifests = 0
            if index_root.exists():
                for base, _, files in os.walk(index_root):
                    for name in files:
                        path = Path(base) / name
                        if name != "index.json": continue
                        try:
                            item = json.loads(path.read_text(encoding="utf-8"))
                            manifests += 1
                            keys = item.get("keyframes") or []
                            keyframe_bytes += len(json.dumps(keys, separators=(",", ":")).encode("utf-8"))
                        except (OSError, ValueError, TypeError): pass
            from ..playback.stream_server import range_cache_usage
            byte_cache = range_cache_usage()
            return {"snapshots_bytes": tree_bytes(snapshots), "mp4_index_bytes": mp4_bytes,
                    "keyframe_index_bytes": keyframe_bytes,
                    "manifests": manifests, "range_cache_bytes": byte_cache["bytes"],
                    "range_cache_entries": byte_cache["entries"], "range_cache_complete": byte_cache["complete"],
                    "range_cache_budget_bytes": cache_policy["budget_bytes"],
                    "range_cache_budget_gib": cache_policy["budget_gib"],
                    "range_cache_retention": cache_policy["retention"],
                    **kept_range_cache_summary(),
                    "playback_connection_profile": str(db.setting("playback_connection_profile", "balanced") or "balanced"),
                    "playback_readahead_seconds": tuning["readahead_seconds"],
                    "playback_ram_cache_mb": tuning["ram_mb"],
                    "note": "Keyframe data is included in the MP4 index cache; do not add it twice."}
        return await asyncio.to_thread(scan_usage)

    # Validate, save and apply the playback cache policy.
    @router.post("/api/settings/cache-policy")
    async def save_playback_cache_policy(request: Request):
        require_auth(request)
        data = await request.json()
        if not isinstance(data, dict):
            raise HTTPException(422, "Cache settings must be sent as an object")
        try:
            budget_gib = int(data.get("budget_gib", 50))
        except (TypeError, ValueError):
            raise HTTPException(422, "Cache limit must be a whole number of GiB")
        if not 1 <= budget_gib <= 500:
            raise HTTPException(422, "Cache limit must be between 1 and 500 GiB")
        retention = str(data.get("retention") or "idle_30m").strip().lower()
        if retention not in {"manual", "idle_10m", "idle_30m"}:
            raise HTTPException(422, "Choose manual, 10-minute, or 30-minute playback-cache retention")

        try:
            readahead = int(data.get("readahead_seconds", db.setting("playback_readahead_seconds", 180)))
            ram_mb = int(data.get("ram_mb", db.setting("playback_ram_cache_mb", 128)))
        except (TypeError, ValueError):
            raise HTTPException(422, "Look-ahead and memory buffer must be whole numbers")
        if not 30 <= readahead <= 600:
            raise HTTPException(422, "Look-ahead must be between 30 and 600 seconds")
        if not 64 <= ram_mb <= 1024:
            raise HTTPException(422, "Memory buffer must be between 64 and 1024 MiB")
        profile_name = str(data.get("connection_profile") or db.setting("playback_connection_profile", "balanced")).strip().lower()
        if profile_name not in {"gentle", "balanced", "fast"}:
            raise HTTPException(422, "Choose a gentle, balanced or fast connection profile")
        db.set_setting("playback_connection_profile", profile_name)
        from ..playback.stream_server import configure_connection_profile, apply_connection_profile
        configure_connection_profile(profile_name)
        _eng = ps.get("engine")
        if _eng is not None and getattr(_eng, "running", False):
            apply_connection_profile(_eng)
        db.set_setting("playback_readahead_seconds", str(readahead))
        db.set_setting("playback_ram_cache_mb", str(ram_mb))
        from ..playback.stream_server import configure_playback_tuning
        configure_playback_tuning(readahead_seconds=readahead, ram_mb=ram_mb)
        db.set_setting("playback_range_cache_budget_gib", str(budget_gib))
        db.set_setting("playback_range_cache_retention", retention)
        from ..playback.stream_server import (
            configure_range_cache_policy,
            prune_expired_range_cache,
            prune_range_cache_to_budget,
        )
        policy = configure_range_cache_policy(
            budget_gib=budget_gib,
            retention=retention,
        )
        engine = ps.get("engine")
        keep_paths = []
        keep_keys = ()
        if engine is not None and getattr(engine, "running", False):
            keep_paths = [cache.path for cache in engine.disk_caches.values()]
            keep_keys = engine._protected_videos()
        expiry = await asyncio.to_thread(prune_expired_range_cache, keep_paths=keep_paths)
        budget = await asyncio.to_thread(
            prune_range_cache_to_budget,
            budget_bytes=policy["budget_bytes"],
            keep_paths=keep_paths,
            keep_keys=keep_keys,
        )
        retention_label = {
            "manual": "playback cache retained until cleared; prefetch expires after 10 minutes",
            "idle_10m": "10-minute playback idle expiry; prefetch expires after 10 minutes",
            "idle_30m": "30-minute playback idle expiry; prefetch expires after 10 minutes",
        }[retention]
        db.log(
            f"Playback range cache policy saved: {budget_gib} GiB, {retention_label}; "
            f"evicted {budget.get('bytes_removed', 0)} bytes for budget."
        )
        return {
            "ok": True,
            **policy,
            "evicted_bytes": budget.get("bytes_removed", 0),
            "evicted_entries": budget.get("removed", 0),
            "expired_entries": expiry.get("removed", 0),
            "range_cache_bytes": budget.get("bytes_after", 0),
        }

    @router.post("/api/settings/release-kept-video-buffers")
    async def release_kept_video_buffers(request: Request):
        """Return every kept video to the normal retention policy."""
        require_auth(request)
        from ..playback.stream_server import release_all_kept_range_caches
        eng = ps.get("engine")
        if eng is not None:
            for cache in list(getattr(eng, "disk_caches", {}).values()) + list(getattr(eng, "_pending_cleanup", ())):
                cache.kept = False
        return await asyncio.to_thread(release_all_kept_range_caches)

    @router.post("/api/settings/clear-video-byte-cache")
    async def clear_video_byte_cache(request: Request):
        require_auth(request)
        engine = ps.get("engine")
        if engine is not None and getattr(engine, "running", False):
            try:
                result = await engine.clear_persistent_cache()
            except RuntimeError as exc:
                raise HTTPException(409, str(exc)) from exc
        else:
            from ..playback.stream_server import clear_persistent_range_cache
            result = await asyncio.to_thread(clear_persistent_range_cache)
        db.log(f"Persistent video byte cache cleared: {result.get('bytes_cleared', 0)} bytes, {result.get('entries_cleared', 0)} entries")
        return {"ok": True, **result}


    # ------------------------------------------------------------------
    # SUBSECTION: Metadata settings
    # Provider settings and checks.
    # ------------------------------------------------------------------
    @router.get("/api/settings/metadata")
    def get_metadata_settings(request: Request):
        """GET /api/settings/metadata: Get metadata settings."""
        require_auth(request)
        stash = db.setting("stashdb_api_key") or ""
        tpdb = db.setting("tpdb_api_token") or db.setting("tpdb_token") or ""
        def mask(k): return (k[:4]+"…"+k[-4:]) if len(k)>8 else ("set" if k else "")
        return {
            "stashdb_api_key": mask(stash), "stashdb_configured": bool(stash),
            "tpdb_token": mask(tpdb), "tpdb_configured": bool(tpdb),
        }

    @router.post("/api/settings/metadata")
    async def save_metadata_settings(request: Request):
        """POST /api/settings/metadata: Save metadata settings."""
        require_auth(request)
        data = await request.json()
        mapping = {"stashdb_api_key": "stashdb_api_key", "tpdb_token": "tpdb_api_token",
                   "tpdb_api_token": "tpdb_api_token"}
        saved = []
        for field, key in mapping.items():
            v = str(data.get(field) or "").strip()
            if v: db.set_setting(key, v); saved.append(field)
        db.log(f"Metadata keys updated: {saved}")
        return {"ok": True, "saved": saved}

    @router.post("/api/settings/check-tpdb")
    async def check_tpdb(request: Request):
        """POST /api/settings/check-tpdb: Check tpdb."""
        require_auth(request)
        import aiohttp
        token = db.setting("tpdb_api_token") or db.setting("tpdb_token") or ""
        if not token: return {"ok": False, "error": "No token saved"}
        try:
            async with aiohttp.ClientSession() as sess:
                # ThePornDB has no /me or whoami endpoint, and it 404s (not
                # a plain 401) on unknown paths, which made an earlier /me
                # probe look like the token was rejected when the real
                # problem was the endpoint didn't exist. Reuse the exact
                # /scenes/hash/{oshash} call the Identify feature already
                # makes successfully, with a harmless placeholder hash:
                # a bad/expired token still 401s before the hash is looked
                # up, and a good token just returns an empty match list.
                async with sess.get("https://api.theporndb.net/scenes/hash/0000000000000000",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 401:
                        return {"ok": False, "status": r.status, "error": "Invalid or expired token"}
                    if r.status in (200, 404):
                        # 200 = normal response (possibly empty); 404 here
                        # means "no scene with that hash", which still
                        # proves the token passed authentication.
                        return {"ok": True, "status": r.status}
                    text = await r.text()
                    return {"ok": False, "status": r.status, "error": text[:200]}
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}

    @router.post("/api/settings/check-stashdb")
    async def check_stashdb(request: Request):
        """POST /api/settings/check-stashdb: Check stashdb."""
        require_auth(request)
        import aiohttp
        key = db.setting("stashdb_api_key") or ""
        if not key: return {"ok": False, "error": "No API key saved"}
        query = '{"query":"{ me { name } }"}' 
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post("https://stashdb.org/graphql",
                    data=query, headers={"Content-Type":"application/json","ApiKey": key},
                    timeout=aiohttp.ClientTimeout(total=8)) as r:
                    j = await r.json()
                    ok = "data" in j and j["data"] is not None
                    return {"ok": ok, "user": j.get("data",{}).get("me",{}).get("name","") if ok else "",
                            "error": str(j.get("errors",[""])[0]) if not ok else ""}
        except Exception as e:
            return {"ok": False, "error": type(e).__name__}

    @router.post("/api/identify")
    async def identify_media(request: Request):
        """Fingerprint a Telegram video via range reads and look it up in StashDB/ThePornDB."""
        require_auth(request)
        data = await request.json()
        c, m = int(data["channel_id"]), int(data["message_id"])
        from ..core.telegram_range_transport import DirectTelegramRangeSource
        from ..metadata.fingerprint import oshash_from_source
        from ..metadata.pipeline import identify_video
        if not telegram.connected:
            raise HTTPException(503, "Telegram is not connected")
        try:
            message = await telegram.client.get_messages(c, ids=m)
            if message is None:
                raise FileNotFoundError(f"message {m} not found")
        except Exception as e:
            raise HTTPException(400, str(e))
        filename = getattr(message.file, "name", None) or f"telegram_{m}.mp4"
        from ..metadata.filename_matching import extract_filename_parameters
        hints = extract_filename_parameters(db, filename)
        try:
            loop = asyncio.get_running_loop()
            source = DirectTelegramRangeSource(telegram.client, message, loop)
            oshash = await asyncio.to_thread(oshash_from_source, source)
        except Exception:
            oshash = None
        # Use the same confidence gate and performer-identity safeguards as
        # Metadata & Assets. Never blindly apply matches[0] from raw provider
        # results: doubtful cast identities remain pending suggestions.
        result = await identify_video(db, channel_id=c, message_id=m, filename=filename,
                                      oshash=oshash, force=True)
        matches = list(result.get("matches") or [])
        best = result.get("best")
        if not matches and isinstance(best, dict):
            matches = [best]
        saved = str(result.get("action") or "") == "applied"
        if saved and best:
            db.log(f"Identify: saved {best.get('source') or result.get('source') or 'reference'} match for msg {m} ({len(best.get('performers') or [])} performer(s))")
        return {"oshash": oshash, "filename_hints": hints, "saved_to_catalogue": saved,
                "matches": matches, **result}


    # ------------------------------------------------------------------
    # SUBSECTION: Global controls and WebSocket
    # Pause, resume and live updates.
    # ------------------------------------------------------------------
    @router.post("/api/control/pause")
    async def pause(request: Request):
        """POST /api/control/pause: Pause."""
        require_auth(request)
        jobs.pause()
        return {"ok": True}

    @router.post("/api/control/resume")
    async def resume(request: Request):
        """POST /api/control/resume: Resume."""
        require_auth(request)
        jobs.resume()
        return {"ok": True}

    @router.websocket("/ws")
    async def websocket(ws: WebSocket):
        """Authenticated operational event stream with change coalescing."""
        if not session_from_ws(ws):
            await ws.close(code=4401)
            return
        _origin = ws.headers.get("origin") or ""
        if _origin:
            from urllib.parse import urlsplit
            if urlsplit(_origin).netloc.lower() != (ws.headers.get("host") or "").lower():
                await ws.close(code=4403)
                return
        await ws.accept()
        last_data = None
        last_steady = None
        last_sent = 0.0
        try:
            while True:
                if not session_from_ws(ws):
                    await ws.close(code=4401)
                    return
                from .. import config
                interval = max(0.1, float(getattr(config, "OPERATIONS_WEBSOCKET_INTERVAL_SECONDS", 0.25)))
                data, payload_json = await status_snapshot(max_age=max(0.1, float(getattr(config, "OPERATIONS_STATUS_CACHE_SECONDS", 0.25))))
                # The watchdog "age" counters (seconds since a heartbeat) differ in every snapshot, so a snapshot was never "unchanged" and the
                # whole payload (11 KB) went out four times a second to an idle page. Compare without them; they still arrive with every real change
                # and with the heartbeat below.
                steady = last_steady
                if data is not last_data:
                    last_data = data
                    steady = steady_status_json(data)
                # Send an unchanged snapshot as a heartbeat too, so an idle
                # library is distinguishable from a half-open proxy socket.
                if steady != last_steady or time.monotonic() - last_sent >= 10:
                    await ws.send_text(payload_json)
                    last_steady = steady
                    last_sent = time.monotonic()
                await asyncio.sleep(interval)
        except WebSocketDisconnect:
            pass


    # ------------------------------------------------------------------
    # SUBSECTION: Web login and job read
    # Dashboard login and job read.
    # ------------------------------------------------------------------
    @router.get("/api/settings/weblogin")
    def get_weblogin(request: Request):
        """GET /api/settings/weblogin: Get weblogin."""
        require_auth(request)
        from ..core.auth import is_default_login
        return {"username": db.setting("web_username", "admin"), "default_login": is_default_login(db), "login_required": _login_required()}

    def _login_required() -> bool:
        """The app asks for the login unless the person switched that off (Settings, Security, App login)."""
        return str(db.setting("app_login_required", "1") or "1") != "0"

    @router.get("/api/login-hint")
    async def login_hint():
        """GET /api/login-hint: Login hint."""
        # Public: whether setup is still to do, and whether the sign-in screen is wanted at all.
        return {"setup_required": bool(is_setup_required(db)), "default_login": False, "login_required": _login_required()}

    @router.post("/api/auth/open")
    async def open_without_login(request: Request):
        """With the login switched off, a request from this PC gets a normal session without typing anything. Any other caller still needs the login."""
        host = request.client.host if request.client else ""
        if _login_required() or is_setup_required(db) or host not in ("127.0.0.1", "::1"):
            raise HTTPException(403, "Sign in with your username and password")
        resp = JSONResponse({"ok": True})
        set_session_cookie(resp, request, sessions.create(db.setting("web_username", "admin")))
        return resp

    @router.post("/api/settings/weblogin")
    async def save_weblogin(request: Request):
        """POST /api/settings/weblogin: Save weblogin."""
        require_auth(request)
        from ..core.auth import set_web_login
        data = await request.json()
        u = str(data.get("username") or "").strip()
        pw = str(data.get("password") or "")
        if pw and len(pw) < 12:
            raise HTTPException(400, "Password must be at least 12 characters")
        required = data.get("required") if isinstance(data.get("required"), bool) else None
        if not u and not pw and required is None:
            raise HTTPException(400, "Nothing to change")
        # Re-authenticate: a stolen/left-open session must not be able to take
        # over the account by silently replacing the credential.
        current = str(data.get("current_password") or "")
        stored_hash = db.setting("web_password_hash")
        if not stored_hash or not verify_password(current, stored_hash):
            key = client_key(request)
            login_limiter.check(key)
            login_limiter.record(key)
            raise HTTPException(403, "Current password is incorrect")
        if required is not None:
            db.set_setting("app_login_required", "1" if required else "0")
            db.log("App login switched " + ("on" if required else "off"))
        if u or pw:
            set_web_login(db, u or str(db.setting("web_username", "admin")), pw or None)
        if pw:
            db.set_setting(SETUP_COMPLETE_FLAG, config.now_iso())
            db.set_setting(SETUP_REQUIRED_FLAG, "")
        db.log("Web login settings updated")
        resp = JSONResponse({"ok": True, "username": db.setting("web_username", "admin")})
        if pw or u:
            # Credential change invalidates every other device; keep this one signed in.
            sessions.destroy_all()
            set_session_cookie(resp, request, sessions.create(db.setting("web_username", "admin")))
        return resp

    @router.get("/api/jobs/{job_id}")
    def get_job(job_id: str, request: Request, activity_before_id: int | None = None, activity_limit: int = 500):
        """GET /api/jobs/{job_id}: Get job."""
        require_auth(request)
        job = db.get_job(job_id)
        if not job: raise HTTPException(404, "Job not found")
        job = dict(job)
        if hasattr(jobs, "watchdogs"):
            wd = next((x for x in jobs.watchdogs.snapshot().get("jobs", []) if str(x.get("name")) == str(job_id)), None)
            if wd:
                job.update({
                    "watchdog_state": wd.get("state", "UNKNOWN"),
                    "watchdog_reason": wd.get("reason", ""),
                    "watchdog_heartbeat_age": wd.get("heartbeat_age"),
                    "watchdog_progress_age": wd.get("progress_age"),
                    "watchdog_activity_age": wd.get("activity_age"),
                    "watchdog_recovery_generation": wd.get("recovery_generation"),
                })
        # Content Automation runs Metadata & Assets and Telegram Transfer as
        # child jobs. Fold their durable per-job events into the parent timeline
        # so the operator can see frame names/metadata and every delivery stage
        # without opening each child job separately.
        activity_ids = {str(job_id)}
        if str(job.get("job_type") or "").upper() == "CONTENT_AUTOMATION":
            try:
                all_jobs = db.jobs(limit=500)
                for _depth in range(4):
                    added = False
                    for child in all_jobs:
                        child_id = str(child.get("id") or "")
                        if not child_id or child_id in activity_ids:
                            continue
                        raw = child.get("detail")
                        try:
                            parsed = json.loads(raw or "{}") if isinstance(raw, str) else (raw or {})
                        except Exception:
                            parsed = {}
                        parent_id = str(parsed.get("parent_job_id") or "") if isinstance(parsed, dict) else ""
                        if parent_id in activity_ids:
                            activity_ids.add(child_id)
                            added = True
                    if not added:
                        break
                job["activity_job_ids"] = sorted(activity_ids)
            except Exception:
                _swallowed_note("routes_settings.register_settings_routes.get_job")
                pass
        try:
            activity_limit = max(1, min(int(activity_limit or 500), 500))
            activity = db.recent_job_activity(sorted(activity_ids), limit=activity_limit, before_id=activity_before_id)
            job["activity"] = activity
            job["activity_oldest_id"] = int(activity[0]["id"]) if activity else None
            # A full page may have older events; requesting that page can still return
            # empty if the total happens to equal the page size, which is harmless.
            job["activity_has_older"] = len(activity) >= activity_limit and bool(activity)
        except Exception:
            job["activity"] = []
            job["activity_oldest_id"] = None
            job["activity_has_older"] = False
        return job


    # ------------------------------------------------------------------
    # SUBSECTION: Telegram login aliases
    # Compatibility aliases.
    # ------------------------------------------------------------------
    @router.post("/api/telegram/send_code")
    async def telegram_send_code_alias(request: Request):
        """POST /api/telegram/send_code: Compatibility alias."""
        return await telegram_send_code(request)

    @router.post("/api/telegram/verify_code")
    async def telegram_verify_code_alias(request: Request):
        """POST /api/telegram/verify_code: Compatibility alias."""
        return await telegram_verify_code(request)

    @router.post("/api/telegram/verify_2fa")
    async def telegram_verify_2fa_alias(request: Request):
        """POST /api/telegram/verify_2fa: Compatibility alias."""
        return await telegram_verify_2fa(request)


    # ------------------------------------------------------------------
    # SUBSECTION: Library listing
    # Main library route.
    # ------------------------------------------------------------------
    @router.get("/api/library")
    def library(request: Request):
        """GET /api/library: Library."""
        require_auth(request)
        q = request.query_params
        page = max(0, int(q.get("page", 0)))
        per = max(1, min(48, int(q.get("per", 48))))
        search = q.get("q", "").strip()
        channel_ids = [x.strip() for x in q.get("channels", "").split(",") if x.strip()]
        sort = q.get("sort", "date_desc")
        res_filter = q.get("resolution", "")
        studio_filter = q.get("studio", "").strip()
        performer_filter = q.get("performer", "").strip()
        tag_filter = q.get("tag", "").strip()
        date_from = q.get("date_from", "")
        date_to = q.get("date_to", "")
        fav_only = q.get("favorites", "") == "1"
        message_id = q.get("message_id", "").strip()  # one video by its Telegram message id (watch-page deep links)

        with db.reading() as _rdb:
            where, params = ["1=1"], []
            if channel_ids:
                ph = ",".join(["?"]*len(channel_ids))
                where.append(f"m.source_channel IN ({ph})"); params.extend(channel_ids)
            if search:
                like = f"%{search}%"
                if getattr(db, "fts_available", False):
                    match = fts_prefix_query(search)
                    if match:
                        # Search the media's own text AND the metadata relationships.
                        # Previously media_fts only contained title/filename/caption,
                        # so searching a known performer such as "Tia Cyrus" could
                        # return the performer entity but zero of her videos.
                        where.append("("
                            "m.id IN (SELECT rowid FROM media_fts WHERE media_fts MATCH ?) "
                            "OR m.id IN (SELECT vp.video_id FROM video_performers vp WHERE vp.performer_id IN (SELECT rowid FROM performer_fts WHERE performer_fts MATCH ?)) "
                            "OR m.id IN (SELECT lvp.video_id FROM local_video_performers lvp WHERE lvp.performer_id IN (SELECT rowid FROM performer_fts WHERE performer_fts MATCH ?)) "
                            "OR m.id IN (SELECT vs.video_id FROM video_studios vs WHERE vs.studio_id IN (SELECT rowid FROM studio_fts WHERE studio_fts MATCH ?)) "
                            "OR m.id IN (SELECT vt.video_id FROM video_tags vt WHERE vt.tag_id IN (SELECT rowid FROM tag_fts WHERE tag_fts MATCH ?))"
                            ")")
                        params.extend([match, match, match, match, match])
                    else:
                        where.append("(m.title LIKE ? OR m.filename LIKE ? OR m.caption LIKE ? OR EXISTS (SELECT 1 FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=m.id AND p.name LIKE ?) OR EXISTS (SELECT 1 FROM local_video_performers lvp JOIN performers p ON p.id=lvp.performer_id WHERE lvp.video_id=m.id AND p.name LIKE ?) OR EXISTS (SELECT 1 FROM video_studios vs JOIN studios ss ON ss.id=vs.studio_id WHERE vs.video_id=m.id AND ss.name LIKE ?) OR EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=m.id AND t.name LIKE ?))")
                        params.extend([like]*7)
                else:
                    where.append("(m.title LIKE ? OR m.filename LIKE ? OR m.caption LIKE ? OR EXISTS (SELECT 1 FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=m.id AND p.name LIKE ?) OR EXISTS (SELECT 1 FROM local_video_performers lvp JOIN performers p ON p.id=lvp.performer_id WHERE lvp.video_id=m.id AND p.name LIKE ?) OR EXISTS (SELECT 1 FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=m.id AND s.name LIKE ?) OR EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=m.id AND t.name LIKE ?))")
                    params.extend([like]*7)
            if res_filter:
                rmap={"2160":"m.height>=2160","1440":"m.height>=1440 AND m.height<2160",
                      "1080":"m.height>=1080 AND m.height<1440","720":"m.height>=720 AND m.height<1080",
                      "480":"m.height>0 AND m.height<720"}
                if res_filter in rmap: where.append(rmap[res_filter])
            if studio_filter:
                where.append("m.id IN (SELECT vs.video_id FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE s.name LIKE ?)")
                params.append(f"%{studio_filter}%")
            if performer_filter:
                # Performer assignments can live in either the catalogue relationship
                # table or the app's local/manual relationship table. Filtering only
                # video_performers hides videos whose performer was assigned locally.
                where.append("(m.id IN (SELECT vp.video_id FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE p.name LIKE ?) "
                             "OR m.id IN (SELECT lvp.video_id FROM local_video_performers lvp JOIN performers p ON p.id=lvp.performer_id WHERE p.name LIKE ?))")
                params.extend([f"%{performer_filter}%", f"%{performer_filter}%"])
            if tag_filter:
                where.append("m.id IN (SELECT vt.video_id FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE t.name LIKE ?)")
                params.append(f"%{tag_filter}%")
            effective_date = "COALESCE(NULLIF(m.upload_date,''),m.created_at)"
            if date_from:
                where.append(f"({effective_date} IS NULL OR {effective_date} >= ?)"); params.append(date_from)
            if date_to:
                where.append(f"({effective_date} IS NULL OR {effective_date} <= ?)"); params.append(date_to+"T99")
            if fav_only:  where.append("m.favorite = 1")
            if message_id.lstrip("-").isdigit():
                where.append("m.source_message_id = ?"); params.append(int(message_id))

            # Relationship sorts are correlated against indexed link tables and
            # evaluated while paging media IDs; they do not fan out a 9k-video
            # catalogue into joined rows before LIMIT/OFFSET.
            sort_map={"date_desc":"COALESCE(NULLIF(m.upload_date,''),m.created_at) DESC, m.id DESC","date_asc":"COALESCE(NULLIF(m.upload_date,''),m.created_at) ASC, m.id ASC",
                      "size_desc":"m.size DESC, m.id DESC","dur_desc":"m.duration DESC, m.id DESC","name_asc":"m.filename COLLATE NOCASE ASC, m.id ASC",
                      "studio_asc":"(SELECT MIN(s.name) FROM video_studios vs JOIN studios s ON s.id=vs.studio_id WHERE vs.video_id=m.id) COLLATE NOCASE ASC, m.upload_date DESC, m.id DESC",
                      "performer_asc":"(SELECT MIN(p.name) FROM video_performers vp JOIN performers p ON p.id=vp.performer_id WHERE vp.video_id=m.id) COLLATE NOCASE ASC, m.upload_date DESC, m.id DESC",
                      "tag_asc":"(SELECT MIN(t.name) FROM video_tags vt JOIN tags t ON t.id=vt.tag_id WHERE vt.video_id=m.id) COLLATE NOCASE ASC, m.upload_date DESC, m.id DESC"}
            if sort not in sort_map:
                sort = "date_desc"
            order = sort_map[sort]
            w = " AND ".join(where)
            total = _rdb.execute(f"SELECT COUNT(*) n FROM archive_x_library_media m WHERE {w}", params).fetchone()["n"]
            if sort in {"date_desc", "date_asc", "size_desc", "dur_desc", "name_asc", "studio_asc", "performer_asc", "tag_asc"}:
                # Page the media IDs before relationship enrichment. The previous plan
                # expanded performer/studio/tag joins for the entire filtered catalogue
                # and only then applied LIMIT/OFFSET, which made common library reads
                # scale with relationship cardinality instead of page size.
                page_ids = _rdb.execute(
                    f"SELECT m.id FROM archive_x_library_media m WHERE {w} ORDER BY {order} LIMIT ? OFFSET ?",
                    params + [per, page*per]).fetchall()
                ids = [int(row["id"]) for row in page_ids]
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    rows = _rdb.execute(
                        f"SELECT m.*, c.title AS channel_title, " + name_lists_sql() +
                        f" FROM archive_x_library_media m LEFT JOIN channels c ON c.telegram_id=m.source_channel"
                        f" WHERE m.id IN ({placeholders}) GROUP BY m.id ORDER BY {order}",
                        ids).fetchall()
                else:
                    rows = []
            else:
                rows = _rdb.execute(
                    f"SELECT m.*, c.title AS channel_title, " + name_lists_sql() +
                    f" FROM archive_x_library_media m LEFT JOIN channels c ON c.telegram_id=m.source_channel"
                    f" WHERE {w} GROUP BY m.id ORDER BY {order} LIMIT ? OFFSET ?",
                    params + [per, page*per]).fetchall()

        def fmt_row(r):
            d = dict(r)
            d["performers"] = list(dict.fromkeys([x for x in (d.get("performers") or "").split(",") if x] + [x for x in (d.get("local_performers") or "").split(",") if x]))
            d["local_performers"] = [x for x in (d.get("local_performers") or "").split(",") if x]
            d["studios"] = [x for x in (d.get("studios") or "").split(",") if x]
            d["tags"] = [x for x in (d.get("tags") or "").split(",") if x]
            d["studio"] = d["studios"][0] if d["studios"] else ""
            d["telegram_filename"] = d.get("filename") or ""
            d["api_title"] = (d.get("title") or "").strip()
            d["metadata_cached"] = bool(d.get("metadata_source") and d.get("metadata_source") not in ("none", ""))
            # The UI uses Telegram's message ID and source channel as its stable
            # playback identity. Retain the SQLite media ID separately for DB mutations.
            d["media_id"] = int(d.get("id") or 0)
            d["_cid"] = str(d.get("source_channel") or "")
            d["date"] = d.get("upload_date") or d.get("created_at") or ""
            d["id"] = int(d.get("source_message_id") or d.get("id") or 0)
            d["name"] = d.get("title") or d.get("filename") or f"video {d['id']}"
            d["thumbnail_id"] = d.get("thumbnail_message_id") or d.get("source_message_id") or d["id"]
            return d
        items = [fmt_row(r) for r in rows]
        ready_count = sum(1 for item in items if bool(item.get("library_ready")))
        return {"total": total, "ready_count": ready_count, "page": page, "per": per, "items": items,
                "catalogued_only": True, "published_only": False, "snapshot_ready_only": False}
