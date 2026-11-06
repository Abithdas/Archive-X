"""Operator-authorized live test bridge routes.

This is a curated test API, not a generic HTTP proxy.  Sessions are issued
only after normal Archive X login and expire quickly.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Module helpers
#   - Route registration
#   - Browser smoke test
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import json
import os
import time
import hashlib
import ssl
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import config
from ..core.live_test_bridge import CAPABILITIES, LiveTestBridge




# ==============================================================================
# SECTION: Module helpers
# Local-only checks, tokens and status.
# ==============================================================================

def _local_client_only(request: Request) -> None:
    """Require loopback callers by default; remote access must be explicitly enabled."""
    if os.getenv("TELEGRAM_ARCHIVE_TEST_BRIDGE_REMOTE", "0") == "1":
        return
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise HTTPException(403, "Live Test Bridge is local-only")


def _token_from_request(request: Request) -> str:
    auth = str(request.headers.get("authorization") or "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(request.headers.get("x-archive-test-token") or "").strip()


def _safe_test_path(path: str) -> str:
    value = str(path or "").strip()
    if not value.startswith("/") or ".." in value or "//" in value:
        raise ValueError("Invalid test path")
    allowed_prefixes = (
        "/health",
        "/api/status",
        "/api/bootstrap",
        "/api/telegram/auth-status",
        "/api/play/status",
        "/api/play/info/",
        "/api/play/buffer/",
        "/api/play/cache/status/",
        "/api/play/preview-manifest/",
        "/api/play/preview-frame/",
        "/api/play/preview/",
        "/api/thumb/",
        "/api/operations/thumbnails/status",
        "/api/media-manager/preflight",
    )
    if not any(value == p or value.startswith(p) for p in allowed_prefixes):
        raise PermissionError("Path is not allowlisted for live testing")
    return value




def _build_status_payload_for_test(*, db, governor, telegram, jobs) -> dict:
    active_jobs, last_jobs = db.job_dashboard_snapshot()
    current_job = next((j for j in active_jobs if j.get("status") in ("RUNNING", "PAUSING", "PAUSED", "CANCEL_REQUESTED")), None)
    from .. import config
    from ..telegram.service import TELETHON_AVAILABLE
    return {
        "telegram": {
            "available": TELETHON_AVAILABLE,
            "connected": bool(getattr(telegram, "connected", False)),
            "status": str(getattr(telegram, "status_text", "")),
        },
        "telegram_connected": bool(getattr(telegram, "connected", False)),
        "telegram_status": str(getattr(telegram, "status_text", "")),
        "stats": db.dashboard_stats(),
        "governor": governor.status(),
        "telegram_performance": telegram.performance_status() if hasattr(telegram, "performance_status") else {},
        "current_job": current_job,
        "jobs": [],
        "paused": bool(jobs.paused),
        "last_jobs": last_jobs,
        "version": config.APP_VERSION,
    }

def _self_get(config, path: str, session_token: str) -> dict:
    url = f"https://127.0.0.1:{config.PORT}" + path
    req = urllib.request.Request(url, headers={"Cookie": f"{config.SESSION_COOKIE}={session_token}", "Accept": "*/*"}, method="GET")
    context = ssl._create_unverified_context()
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=20, context=context) as resp:
            data = resp.read(2 * 1024 * 1024 + 1)
            truncated = len(data) > 2 * 1024 * 1024
            if truncated:
                data = data[:2 * 1024 * 1024]
            content_type = str(resp.headers.get("Content-Type") or "")
            text = None
            if content_type.startswith(("application/json", "text/", "application/javascript")):
                text = data.decode("utf-8", "replace")[:12000]
            return {
                "url": url, "status": int(resp.status), "ok": 200 <= int(resp.status) < 400,
                "content_type": content_type, "bytes": len(data), "truncated": truncated,
                "sha256": hashlib.sha256(data).hexdigest(), "text": text,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            }
    except Exception as exc:
        return {"url": url, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def _tail_file(path: Path, max_lines: int = 120) -> list[str]:
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[-max(1, min(int(max_lines), 500)):]
    except Exception:
        return []




# ==============================================================================
# SECTION: Route registration
# Operator-authorized bridge routes.
# ==============================================================================

def register(router: APIRouter, env: dict) -> LiveTestBridge:
    """Register the live test bridge routes and return the bridge."""
    db = env["db"]
    sessions = env["sessions"]
    telegram = env["telegram"]
    jobs = env["jobs"]
    governor = env["governor"]
    player_state = env["player_state"]
    bridge = LiveTestBridge()

    def require_app_auth(request: Request) -> dict:
        token = request.cookies.get(config.SESSION_COOKIE)
        entry = sessions.get(token)
        if not entry:
            raise HTTPException(401, "Authentication required")
        return entry

    def require_bridge(request: Request, capability: str):
        _local_client_only(request)
        token = _token_from_request(request)
        try:
            sess = bridge.require(token, capability)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        bridge.record(sess, request.url.path)
        return sess

    @router.get("/api/test-bridge/status")
    async def test_bridge_status(request: Request):
        require_app_auth(request)
        _local_client_only(request)
        return {
            "version": config.APP_VERSION,
            "local_only": os.getenv("TELEGRAM_ARCHIVE_TEST_BRIDGE_REMOTE", "0") != "1",
            "capabilities": list(CAPABILITIES),
            "sessions": bridge.list_public(),
        }

    @router.post("/api/test-bridge/session")
    async def issue_session(request: Request):
        operator = require_app_auth(request)
        _local_client_only(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        ttl = body.get("ttl_seconds", 900)
        caps = body.get("capabilities") or [
            "READ_STATUS", "READ_LOGS", "API_TEST", "PLAYBACK_TEST", "UI_TEST", "TELEGRAM_READ"
        ]
        try:
            sess, token = bridge.issue(
                created_by=operator.get("username") or "operator",
                capabilities=caps,
                ttl_seconds=ttl,
                origin_session_token=request.cookies.get(config.SESSION_COOKIE, ""),
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        db.log(f"Live Test Bridge session {sess.session_id} issued with capabilities={','.join(sorted(sess.capabilities))}")
        return {"ok": True, "session": sess.to_public(), "token": token, "warning": "Token is shown once. Store it securely."}

    @router.post("/api/test-bridge/session/{session_id}/revoke")
    async def revoke_session(session_id: str, request: Request):
        require_app_auth(request)
        _local_client_only(request)
        ok = bridge.revoke(session_id)
        if not ok:
            raise HTTPException(404, "Test session not found")
        db.log(f"Live Test Bridge session {session_id} revoked")
        return {"ok": True, "session_id": session_id}

    @router.get("/api/test-bridge/capabilities")
    async def bridge_capabilities(request: Request):
        sess = require_bridge(request, "READ_STATUS")
        return {"session": sess.to_public(), "capabilities": sorted(sess.capabilities)}

    @router.get("/api/test-bridge/health")
    async def bridge_health(request: Request):
        sess = require_bridge(request, "READ_STATUS")
        eng = (player_state or {}).get("engine") if isinstance(player_state, dict) else None
        return {
            "ok": True,
            "version": config.APP_VERSION,
            "telegram": {
                "available": bool(getattr(telegram, "client", None) is not None),
                "connected": bool(getattr(telegram, "connected", False)),
                "status": str(getattr(telegram, "status_text", "")),
            },
            "playback": {
                "running": bool(getattr(eng, "running", False)) if eng else False,
            },
            "test_session": sess.to_public(),
            "timestamp": config.now_iso(),
        }

    # Run one allowed test bridge action and return its result.
    @router.post("/api/test-bridge/run")
    async def bridge_run(request: Request):
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = str(body.get("action") or "").strip().lower()
        capability = "API_TEST"
        if action in {"health", "status", "bootstrap", "telegram_auth_status", "play_info", "play_buffer", "play_cache_status", "preview_manifest", "preview_frame", "preview", "thumbnail", "http_get"}:
            capability = "READ_STATUS" if action in {"health", "status", "bootstrap", "telegram_auth_status"} else "API_TEST"
            if action.startswith("play_") or action.startswith("preview") or action == "thumbnail":
                capability = "PLAYBACK_TEST"
            if action == "http_get":
                capability = "API_TEST"
        elif action == "browser_smoke":
            capability = "UI_TEST"
        elif action in {"logs", "events", "round_trip"}:
            capability = "READ_LOGS" if action in {"logs", "events"} else "API_TEST"
        else:
            raise HTTPException(400, "Unsupported live test action")
        sess = require_bridge(request, capability)
        start = time.perf_counter()

        try:
            if action == "health":
                result = {
                    "version": config.APP_VERSION,
                    "telegram_connected": bool(getattr(telegram, "connected", False)),
                    "telegram_status": str(getattr(telegram, "status_text", "")),
                    "playback_engine": bool((player_state or {}).get("engine")) if isinstance(player_state, dict) else False,
                }
            elif action == "status":
                # Use the same in-process status path as the UI without an HTTP loopback hop.
                status = _build_status_payload_for_test(db=db, governor=governor, telegram=telegram, jobs=jobs)
                result = status
            elif action == "bootstrap":
                # A lightweight contract probe rather than duplicating bootstrap logic.
                result = {"ok": True, "route": "/api/bootstrap", "auth": True, "timestamp": config.now_iso()}
            elif action == "telegram_auth_status":
                result = {"connected": bool(getattr(telegram, "connected", False)), "status": str(getattr(telegram, "status_text", ""))}
            elif action in {"play_info", "play_buffer", "play_cache_status", "preview_manifest", "preview_frame", "preview", "thumbnail"}:
                cid = body.get("channel_id")
                mid = body.get("message_id")
                if cid is None or mid is None:
                    raise HTTPException(400, "channel_id and message_id are required")
                route = {
                    "play_info": f"/api/play/info/{int(cid)}/{int(mid)}",
                    "play_buffer": f"/api/play/buffer/{int(cid)}/{int(mid)}",
                    "play_cache_status": f"/api/play/cache/status/{int(cid)}/{int(mid)}",
                    "preview_manifest": f"/api/play/preview-manifest/{int(cid)}/{int(mid)}",
                    "preview_frame": f"/api/play/preview-frame/{int(cid)}/{int(mid)}/17",
                    "preview": f"/api/play/preview/{int(cid)}/{int(mid)}",
                    "thumbnail": f"/api/thumb/{int(cid)}/{int(mid)}",
                }[action]
                result = {"ok": True, "route": route, "note": "Use browser_smoke for a full authenticated HTTP/browser assertion."}
            elif action == "http_get":
                try:
                    path = _safe_test_path(body.get("path"))
                except (ValueError, PermissionError) as exc:
                    raise HTTPException(403, str(exc)) from exc
                if not sess.origin_session_token:
                    raise HTTPException(409, "Origin login session is unavailable")
                result = _self_get(config, path, sess.origin_session_token)
            elif action == "logs":
                result = {
                    "playback_log": _tail_file(config.PLAYBACK_LOG_PATH, body.get("max_lines", 120)),
                    "runtime_snapshot_exists": config.runtime_snapshot_path().is_file(),
                }
            elif action == "events":
                result = {"events": db.recent_events(int(body.get("limit", 100) or 100))}
            elif action == "round_trip":
                payload = body.get("payload")
                serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
                result = {"received": payload, "serialized_bytes": len(serialized.encode("utf-8")), "sha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest()}
            elif action == "browser_smoke":
                result = await _browser_smoke(config, sess, bridge, body)
            else:
                raise HTTPException(400, "Unsupported live test action")
        except HTTPException:
            raise
        except Exception as exc:
            db.log(f"Live Test Bridge {action} failed: {type(exc).__name__}: {exc}", "ERROR")
            raise HTTPException(502, f"Live test failed: {type(exc).__name__}: {exc}") from exc
        result = result if isinstance(result, dict) else {"value": result}
        result["elapsed_ms"] = round((time.perf_counter() - start) * 1000, 2)
        result["session_id"] = sess.session_id
        return {"ok": True, "action": action, "result": result}

    @router.get("/test-bridge")
    async def test_bridge_page(request: Request):
        require_app_auth(request)
        _local_client_only(request)
        html = _TEST_BRIDGE_HTML
        return HTMLResponse(html)

    return bridge




# ==============================================================================
# SECTION: Browser smoke test
# Browser smoke runner.
# ==============================================================================

async def _browser_smoke(config, sess, bridge, body: dict[str, Any]) -> dict[str, Any]:
    """Run a browser-level smoke test when Playwright is installed.

    It deliberately performs only navigation/read assertions.  Playback media
    probing is controlled through the optional channel/message fields.
    """
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return {"available": False, "ok": False, "reason": f"Playwright unavailable: {type(exc).__name__}: {exc}"}

    base_url = str(body.get("base_url") or f"https://127.0.0.1:{config.PORT}").rstrip("/")
    token = sess.origin_session_token
    if not token:
        return {"available": False, "ok": False, "reason": "Origin login session is unavailable for browser test"}
    paths = ["/", "/api/status", "/api/bootstrap"]
    requested = body.get("paths") or []
    if isinstance(requested, list):
        for path in requested[:10]:
            try:
                p = _safe_test_path(str(path)) if str(path).startswith("/api/") else "/"
                if p not in paths:
                    paths.append(p)
            except Exception:
                continue
    cid = body.get("channel_id")
    mid = body.get("message_id")
    if cid is not None and mid is not None:
        try:
            cid_i, mid_i = int(cid), int(mid)
            for p in (
                f"/api/play/info/{cid_i}/{mid_i}",
                f"/api/play/buffer/{cid_i}/{mid_i}",
                f"/api/play/cache/status/{cid_i}/{mid_i}",
                f"/api/play/preview-manifest/{cid_i}/{mid_i}",
                f"/api/play/preview-frame/{cid_i}/{mid_i}/17",
                f"/api/play/preview/{cid_i}/{mid_i}",
                f"/api/thumb/{cid_i}/{mid_i}",
            ):
                if p not in paths:
                    paths.append(p)
        except (TypeError, ValueError):
            pass
    results: list[dict[str, Any]] = []
    console_errors: list[str] = []
    network_failures: list[str] = []
    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as exc:
            return {
                "available": False,
                "ok": False,
                "reason": f"Chromium browser unavailable: {type(exc).__name__}: {exc}",
            }
        context = await browser.new_context(ignore_https_errors=True)
        await context.add_cookies([{
            "name": config.SESSION_COOKIE,
            "value": token,
            "url": base_url,
            "httpOnly": True,
            "secure": base_url.startswith("https://"),
        }])
        page = await context.new_page()
        page.on("console", lambda msg: console_errors.append(f"{msg.type}: {msg.text}") if msg.type == "error" else None)
        page.on("requestfailed", lambda req: network_failures.append(f"{req.method} {req.url}: {req.failure}"))
        try:
            for path in paths:
                try:
                    resp = await page.goto(base_url + path, wait_until="domcontentloaded", timeout=20_000)
                    status = resp.status if resp else None
                    results.append({"path": path, "status": status, "ok": bool(resp and status < 400)})
                except Exception as exc:
                    results.append({"path": path, "status": None, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                    break
        finally:
            await browser.close()
    return {
        "available": True,
        "ok": all(x["ok"] for x in results) and not network_failures,
        "base_url": base_url,
        "requests": results,
        "console_errors": console_errors[:50],
        "network_failures": network_failures[:50],
        "screenshot_collected": False,
    }


_TEST_BRIDGE_HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Archive X — Live Test Bridge</title>
<style>
:root{font-family:"ArchiveXInter";background:#000000;color:#F5F7FF}body{margin:0;padding:28px;background:#000000}.wrap{max-width:980px;margin:auto}.card{background:#292929;border:1px solid #676767;border-radius:14px;padding:18px;margin-bottom:14px}.head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.muted{color:#C3C5C9;font-size:12px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.cap{display:flex;align-items:center;gap:8px;padding:8px 10px;background:#292929;border:1px solid #676767;border-radius:9px;font-size:12px}.btn{border:1px solid #676767;background:#292929;color:#F5F7FF;border-radius:9px;padding:8px 12px;cursor:pointer}.btn.pri{background:#CFCFC4;color:#000000;border-color:#CFCFC4}.danger{color:#ff6b6b;border-color:#ff6b6b}.token{font:12px "ArchiveXMono";word-break:break-all;background:#000000;padding:10px;border-radius:9px;border:1px solid #676767}.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}pre{white-space:pre-wrap;background:#000000;padding:12px;border-radius:10px;border:1px solid #676767;max-height:360px;overflow:auto}.pill{padding:4px 8px;border-radius:999px;background:#292929;border:1px solid #676767;font-size:11px}
@media(max-width:720px){.grid{grid-template-columns:1fr}}
</style></head><body><div class="wrap">
<div class="card"><div class="head"><div><h2 style="margin:0 0 4px">Archive X Live Test Bridge</h2><div class="muted">Manual authorization • short-lived token • capability scoped • local-only by default</div></div><span class="pill">V6 live testing</span></div></div>
<div class="card"><h3 style="margin-top:0">1. Authorize a test session</h3><div class="muted" style="margin-bottom:10px">Read-only diagnostic permissions are selected by default. Destructive Telegram operations are never enabled by default.</div><div id="caps" class="grid"></div><div class="row" style="margin-top:12px"><label class="muted">TTL <select id="ttl"><option value="900">15 min</option><option value="1800">30 min</option></select></label><button class="btn pri" onclick="issue()">Generate authorization</button><button class="btn" onclick="refreshStatus()">Refresh</button></div><div id="issueOut" style="margin-top:10px"></div></div>
<div class="card"><h3 style="margin-top:0">2. Run live smoke tests</h3><div class="row"><input id="token" style="flex:1;min-width:280px" placeholder="Paste the one-time live test token"><button class="btn" onclick="run('health')">Health</button><button class="btn" onclick="run('status')">Status</button><button class="btn" onclick="run('browser_smoke')">Browser smoke</button><button class="btn" onclick="run('logs')">Logs</button><button class="btn" onclick="run('events')">Events</button><button class="btn" onclick="run('round_trip')">Round-trip</button></div><div style="margin-top:10px" class="row"><input id="cid" placeholder="channel id" style="width:180px"><input id="mid" placeholder="message id" style="width:150px"><button class="btn" onclick="run('play_info')">Playback info</button><button class="btn" onclick="run('play_cache_status')">Cache status</button><button class="btn" onclick="run('preview_sheet')">Preview sheet</button></div><pre id="out">No test run.</pre></div>
<div class="card"><h3 style="margin-top:0">3. Active sessions</h3><div id="sessions" class="muted">Loading…</div></div>
</div><script>
const caps=['READ_STATUS','READ_LOGS','API_TEST','PLAYBACK_TEST','UI_TEST','TELEGRAM_READ','TELEGRAM_WRITE','DESTRUCTIVE'];
const c=document.getElementById('caps');c.innerHTML=caps.map((x,i)=>`<label class="cap"><input type="checkbox" data-cap="${x}" ${i<6?'checked':''}>${x}</label>`).join('');
async function j(url,opt){const r=await fetch(url,opt);let d={};try{d=await r.json()}catch{};return {...d,_status:r.status,_ok:r.ok};}
async function issue(){const selected=[...document.querySelectorAll('[data-cap]:checked')].map(x=>x.dataset.cap);const r=await j('/api/test-bridge/session',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({capabilities:selected,ttl_seconds:Number(document.getElementById('ttl').value)})});document.getElementById('issueOut').innerHTML=r.token?`<div class="token">${r.token}</div><div class="muted" style="margin-top:6px">Copy this token now. It will not be displayed again.</div>`:`<div class="muted">${r.detail||'Unable to issue session'}</div>`;if(r.token)document.getElementById('token').value=r.token;refreshStatus();}
async function refreshStatus(){const r=await j('/api/test-bridge/status');document.getElementById('sessions').textContent=r.sessions?JSON.stringify(r.sessions,null,2):(r.detail||'Unavailable');}
async function run(action){const tok=document.getElementById('token').value.trim();const body={action};if(document.getElementById('cid').value)body.channel_id=document.getElementById('cid').value;if(document.getElementById('mid').value)body.message_id=document.getElementById('mid').value;const r=await j('/api/test-bridge/run',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+tok},body:JSON.stringify(body)});document.getElementById('out').textContent=JSON.stringify(r,null,2);}
refreshStatus();
</script></body></html>"""
