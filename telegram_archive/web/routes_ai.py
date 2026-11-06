"""Embedded Archive X AI Assistant routes with explicit evidence consent."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Status, config and runtime
#       - History and investigations
#       - Live authorization and chat
#       - Evidence consent
#   - Module helpers
# ------------------------------------------------------------------
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import config
from ..core.ai_assistant import ArchiveXAI, LocalAIError, project_context
from ..core.ai_evidence import collect_diagnostics, safe_evidence_path
from ..core.live_test_bridge import READ_ONLY
from .routes_test_bridge import _browser_smoke, _build_status_payload_for_test


def register_ai_routes(router: APIRouter, env: dict, bridge) -> ArchiveXAI:
    """Register the AI assistant routes and return the assistant."""
    db = env["db"]
    sessions = env["sessions"]
    telegram = env["telegram"]
    jobs = env["jobs"]
    governor = env["governor"]
    ai = ArchiveXAI(db, jobs=jobs, governor=governor)
    live_sessions: dict[str, tuple[str, str]] = {}
    pending_requests: dict[str, dict[str, Any]] = {}
    pending_evidence: dict[str, dict[str, Any]] = {}

    def require_auth(request: Request) -> tuple[str, dict]:
        token = str(request.cookies.get(config.SESSION_COOKIE) or "")
        entry = sessions.get(token)
        if not entry:
            raise HTTPException(401, "Authentication required")
        return token, entry

    def current_live_session(app_token: str):
        pair = live_sessions.get(app_token)
        if not pair:
            return None
        _, test_token = pair
        sess = bridge.authenticate(test_token)
        if sess is None:
            live_sessions.pop(app_token, None)
            return None
        return sess

    def require_pending(app_token: str) -> dict[str, Any]:
        item = pending_requests.get(app_token)
        if not item or item.get("expires_at", 0) <= time.time():
            pending_requests.pop(app_token, None)
            raise HTTPException(409, "No pending evidence request. Ask the AI Assistant again.")
        return item

    def _capture_browser_screenshot(request: Request, app_token: str, page_path: str) -> dict[str, Any]:
        sess = current_live_session(app_token)
        if sess is None:
            raise HTTPException(403, "Authorize live testing before collecting UI evidence")
        if "UI_TEST" not in sess.capabilities:
            raise HTTPException(403, "UI_TEST capability is required for screenshot capture")
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise HTTPException(503, f"Playwright unavailable: {type(exc).__name__}: {exc}") from exc
        token = sess.origin_session_token
        if not token:
            raise HTTPException(409, "Origin login session is unavailable")
        base_url = f"https://127.0.0.1:{config.PORT}"
        path = str(page_path or "/").strip()
        if not path.startswith("/") or ".." in path or "//" in path:
            path = "/"
        out = safe_evidence_path("shot_" + secrets.token_hex(8), ".png")
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:
                raise HTTPException(503, f"Chromium unavailable: {type(exc).__name__}: {exc}") from exc
            context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
            context.add_cookies([{
                "name": config.SESSION_COOKIE,
                "value": token,
                "url": base_url,
                "httpOnly": True,
                "secure": True,
            }])
            page = context.new_page()
            try:
                page.goto(base_url + path, wait_until="domcontentloaded", timeout=20_000)
                page.screenshot(path=str(out), full_page=True)
            finally:
                context.close()
                browser.close()
        bridge.record(sess, "ai_evidence:screenshot")
        return {"path": str(out), "size_bytes": out.stat().st_size, "page_path": path}


    # ------------------------------------------------------------------
    # SUBSECTION: Status, config and runtime
    # Assistant status, config and runtime.
    # ------------------------------------------------------------------
    @router.get("/api/ai/status")
    async def ai_status(request: Request):
        app_token, _ = require_auth(request)
        status = ai.masked_status()
        live = current_live_session(app_token)
        status["live_authorized"] = bool(live)
        status["local_only"] = True
        status["live_session"] = live.to_public() if live else None
        active = db.ai_active_investigation()
        status["investigation"] = active
        status["history_count"] = len(db.ai_list_chat_messages(500, int(active["id"])))
        status["investigations"] = db.ai_list_investigations(50)
        req = pending_requests.get(app_token)
        status["pending_evidence_request"] = req if req and req.get("expires_at", 0) > time.time() else None
        status["pending_evidence"] = [
            {"evidence_id": k, "kind": str(v.get("kind") or ""), "created_at": v.get("created_at"), "summary": v.get("summary") or {}, "provenance": v.get("provenance") or {}}
            for k, v in pending_evidence.items() if v.get("app_token") == app_token
        ]
        status["evidence_history"] = db.ai_list_evidence_history(int(active["id"]), 30)
        return status

    @router.post("/api/ai/config")
    async def ai_config(request: Request):
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            result = ai.configure(
                runtime_path=body.get("runtime_path") if "runtime_path" in body else None,
                model_path=body.get("model_path") if "model_path" in body else None,
                model_id=body.get("model_id") if "model_id" in body else None,
                endpoint=body.get("endpoint") if "endpoint" in body else None,
                context=body.get("context") if "context" in body else None,
                gpu_layers=body.get("gpu_layers") if "gpu_layers" in body else None,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        db.log("Local AI settings updated", event="ai.configuration.updated")
        return {"ok": True, **result}

    @router.get("/api/ai/tools")
    async def ai_tools(request: Request):
        require_auth(request)
        return {"local_only": True, "tools": ai.tools.definitions()}

    @router.post("/api/ai/runtime/start")
    async def ai_runtime_start(request: Request):
        require_auth(request)
        try:
            result = await asyncio.to_thread(ai.start)
        except LocalAIError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True, "runtime": result}

    @router.post("/api/ai/runtime/stop")
    async def ai_runtime_stop(request: Request):
        require_auth(request)
        return {"ok": True, "runtime": await asyncio.to_thread(ai.stop)}

    @router.post("/api/ai/test")
    async def ai_test(request: Request):
        require_auth(request)
        try:
            result = await asyncio.to_thread(ai.test)
        except LocalAIError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True, **result}


    # ------------------------------------------------------------------
    # SUBSECTION: History and investigations
    # Conversation history and investigations.
    # ------------------------------------------------------------------
    @router.get("/api/ai/history")
    async def ai_history(request: Request):
        require_auth(request)
        active = db.ai_active_investigation()
        return {"investigation": active, "messages": db.ai_list_chat_messages(200, int(active["id"])), "observations": db.ai_list_observations(100, int(active["id"])), "evidence": db.ai_list_evidence_history(int(active["id"]), 50)}

    @router.get("/api/ai/investigations")
    async def ai_investigations(request: Request):
        require_auth(request)
        active = db.ai_active_investigation()
        return {"active_id": int(active["id"]), "investigations": db.ai_list_investigations(50)}

    @router.post("/api/ai/investigations")
    async def ai_investigation_create(request: Request):
        require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        title = str(body.get("title") or "New investigation").strip()
        if not title or len(title) > 160:
            raise HTTPException(400, "Investigation title must be 1-160 characters")
        iid = db.ai_create_investigation(title)
        active = db.ai_set_active_investigation(iid)
        return {"ok": True, "investigation": active}

    @router.post("/api/ai/investigations/select")
    async def ai_investigation_select(request: Request):
        require_auth(request)
        try:
            body = await request.json()
            iid = int(body.get("investigation_id"))
        except Exception as exc:
            raise HTTPException(400, "Valid investigation_id is required") from exc
        try:
            active = db.ai_set_active_investigation(iid)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "investigation": active}

    @router.post("/api/ai/investigations/update")
    async def ai_investigation_update(request: Request):
        require_auth(request)
        try:
            body = await request.json()
            iid = int(body.get("investigation_id"))
        except Exception as exc:
            raise HTTPException(400, "Valid investigation_id is required") from exc
        allowed_stages = {"OBSERVE", "TEST", "DIAGNOSE", "PROPOSE_FIX", "VERIFY", "DONE"}
        stage = str(body.get("stage") or "").upper() or None
        status_value = str(body.get("status") or "").upper() or None
        if stage and stage not in allowed_stages:
            raise HTTPException(400, "Invalid investigation stage")
        try:
            active = db.ai_update_investigation(iid, title=body.get("title"), stage=stage, status=status_value, summary=body.get("summary"))
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"ok": True, "investigation": active}

    @router.post("/api/ai/observation")
    async def ai_observation(request: Request):
        operator = require_auth(request)[1]
        try:
            body = await request.json()
        except Exception:
            body = {}
        kind = str(body.get("kind") or "OBSERVATION").upper()
        if kind not in {"OBSERVATION", "PROBLEM", "SUGGESTION", "DECISION", "REQUIREMENT"}:
            raise HTTPException(400, "Invalid observation type")
        content = str(body.get("content") or "").strip()
        if not content or len(content) > 20000:
            raise HTTPException(400, "Observation must be 1-20,000 characters")
        oid = db.ai_add_observation(kind, content, int(db.ai_active_investigation()["id"]))
        db.log(f"AI Assistant observation recorded by {operator.get('username') or 'operator'}: {kind}")
        return {"ok": True, "id": oid, "kind": kind}

    @router.post("/api/ai/reset")
    async def ai_reset(request: Request):
        app_token, _ = require_auth(request)
        pending_requests.pop(app_token, None)
        for eid in [x for x, v in pending_evidence.items() if v.get("app_token") == app_token]:
            pending_evidence.pop(eid, None)
        active = db.ai_active_investigation()
        try:
            result = await asyncio.to_thread(ai.reset_conversation, int(active["id"]))
        except LocalAIError as exc:
            raise HTTPException(502, str(exc)) from exc
        db.log("AI Assistant conversation reset")
        return result


    # ------------------------------------------------------------------
    # SUBSECTION: Live authorization and chat
    # Live session authorization and chat.
    # ------------------------------------------------------------------
    @router.post("/api/ai/live/authorize")
    async def ai_live_authorize(request: Request):
        app_token, operator = require_auth(request)
        ttl = 900
        try:
            body = await request.json()
            ttl = body.get("ttl_seconds", 900)
        except Exception:
            pass
        old = live_sessions.get(app_token)
        if old:
            bridge.revoke(old[0])
        try:
            sess, test_token = bridge.issue(
                created_by=operator.get("username") or "operator",
                capabilities=READ_ONLY,
                ttl_seconds=ttl,
                origin_session_token=app_token,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        live_sessions[app_token] = (sess.session_id, test_token)
        db.log(f"AI Assistant live testing authorized: session={sess.session_id}")
        return {"ok": True, "session": sess.to_public(), "capabilities": sorted(sess.capabilities)}

    @router.post("/api/ai/live/revoke")
    async def ai_live_revoke(request: Request):
        app_token, _ = require_auth(request)
        pair = live_sessions.pop(app_token, None)
        if pair:
            bridge.revoke(pair[0])
            db.log(f"AI Assistant live testing revoked: session={pair[0]}")
        return {"ok": True}

    @router.post("/api/ai/chat")
    async def ai_chat(request: Request):
        app_token, operator = require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        message = str(body.get("message") or "").strip()
        if not message:
            raise HTTPException(400, "Message is required")
        page = str(body.get("page") or "")[:400]
        selected = body.get("selected") if isinstance(body.get("selected"), dict) else None
        active = db.ai_active_investigation()
        context = project_context(db, telegram=telegram, jobs=jobs, governor=governor, page=page, selected=selected)
        try:
            result = await asyncio.to_thread(ai.chat, message, context, operator.get("username") or "operator", int(active["id"]))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except LocalAIError as exc:
            raise HTTPException(502, str(exc)) from exc
        req = result.get("evidence_request") or {"kind": "NONE"}
        if str(req.get("kind") or "NONE").upper() != "NONE":
            pending_requests[app_token] = {
                "kind": str(req.get("kind") or "NONE").upper(),
                "reason": str(req.get("reason") or "").strip(),
                "scope": str(req.get("scope") or "").strip(),
                "items": [str(x) for x in (req.get("items") or [])][:20],
                "page": page or "/",
                "investigation_id": int(active["id"]),
                "created_at": config.now_iso(),
                "expires_at": time.time() + 10 * 60,
            }
        else:
            pending_requests.pop(app_token, None)
        return {"ok": True, **result}


    # ------------------------------------------------------------------
    # SUBSECTION: Evidence consent
    # Evidence collection, preview, discard and send.
    # ------------------------------------------------------------------
    @router.post("/api/ai/evidence/decline")
    async def ai_evidence_decline(request: Request):
        app_token, _ = require_auth(request)
        pending_requests.pop(app_token, None)
        db.log("AI Assistant evidence request declined by operator")
        return {"ok": True}

    # Collect the consented diagnostics for the pending request.
    @router.post("/api/ai/evidence/collect")
    async def ai_evidence_collect(request: Request):
        app_token, _ = require_auth(request)
        pending = require_pending(app_token)
        try:
            body = await request.json()
        except Exception:
            body = {}
        requested_kind = str(body.get("kind") or pending["kind"]).upper()
        if requested_kind not in {"SCREENSHOT", "DIAGNOSTICS", "BOTH", "CLIENT_STATE"}:
            raise HTTPException(400, "Invalid evidence kind")
        if requested_kind != pending["kind"]:
            raise HTTPException(409, "Requested evidence does not match the pending AI request")
        live = current_live_session(app_token)
        if live is None:
            raise HTTPException(403, "Authorize live testing before collecting approved evidence")

        try:
            body = await request.json()
        except Exception:
            body = {}
        selected_items = body.get("items") if isinstance(body.get("items"), list) else list(pending.get("items") or [])
        selected_items = [str(x).strip() for x in selected_items if str(x).strip()][:30]
        normalized = {x.upper().replace(" ", "_").replace("-", "_") for x in selected_items}
        screenshot_requested = requested_kind in {"SCREENSHOT", "BOTH"} and (not selected_items or bool(normalized & {"SCREENSHOT", "CURRENT_UI_SCREENSHOT", "CURRENT_UI"}))
        client_state_requested = requested_kind == "CLIENT_STATE" or bool(normalized & {"CLIENT_STATE", "CURRENT_UI_STATE"})
        diagnostics_items = [x for x in selected_items if x.upper().replace(" ", "_").replace("-", "_") in {"PLAYBACK_LOG","APPLICATION_LOGS","APPLICATION_EVENTS","RUNTIME_SNAPSHOT","ENVIRONMENT","PROCESS_TOPOLOGY","BROWSER_DIAGNOSTICS"}]
        if requested_kind in {"DIAGNOSTICS", "BOTH"} and not diagnostics_items:
            diagnostics_items = [x for x in (pending.get("items") or []) if str(x).upper().replace(" ", "_").replace("-", "_") in {"PLAYBACK_LOG","APPLICATION_LOGS","APPLICATION_EVENTS","RUNTIME_SNAPSHOT","ENVIRONMENT","PROCESS_TOPOLOGY","BROWSER_DIAGNOSTICS"}]
        evidence_id = secrets.token_hex(12)
        active = db.ai_active_investigation()
        record: dict[str, Any] = {
            "evidence_id": evidence_id,
            "app_token": app_token,
            "investigation_id": int(pending.get("investigation_id") or active["id"]),
            "kind": requested_kind,
            "created_at": config.now_iso(),
            "reason": pending.get("reason", ""),
            "scope": pending.get("scope", ""),
            "page_path": pending.get("page") or "/",
            "items": selected_items,
            "summary": {},
            "provenance": {"source": "Archive X", "page": pending.get("page") or "/", "version": config.APP_VERSION, "captured_at": config.now_iso()},
        }
        if screenshot_requested:
            if "UI_TEST" not in live.capabilities:
                raise HTTPException(403, "UI_TEST capability is required for screenshot capture")
            raise HTTPException(409, "Use Capture current UI to share the live browser screenshot")
        if client_state_requested:
            raise HTTPException(409, "CLIENT_STATE evidence must use /api/ai/evidence/client-state")
        if diagnostics_items:
            if "READ_LOGS" not in live.capabilities and "READ_STATUS" not in live.capabilities:
                raise HTTPException(403, "Diagnostic read capability is required")
            diag = await asyncio.to_thread(collect_diagnostics, db=db, requested_items=diagnostics_items)
            record["diagnostics_text"] = diag["text"]
            record["summary"]["diagnostics"] = diag["summary"]
            record["provenance"]["redaction_count"] = diag["summary"].get("redaction_count", 0)
        if not diagnostics_items and not screenshot_requested and not client_state_requested:
            raise HTTPException(400, "No evidence items were selected")
        pending_evidence[evidence_id] = record
        pending_requests.pop(app_token, None)
        db.ai_record_evidence_history(evidence_id=evidence_id, investigation_id=record["investigation_id"], kind=requested_kind, reason=record["reason"], scope=record["scope"], source_page=record["page_path"], source="Archive X", status="STAGED", sha256="", summary=record["summary"])
        db.log(f"AI Assistant evidence staged with explicit operator approval: {requested_kind}")
        return {"ok": True, "evidence_id": evidence_id, "kind": requested_kind, "summary": record["summary"], "provenance": record["provenance"], "preview": _preview_evidence(record)}

    # Store a consented screenshot of the current UI.
    @router.post("/api/ai/evidence/current-ui")
    async def ai_evidence_current_ui(request: Request):
        app_token, _ = require_auth(request)
        pending = require_pending(app_token)
        if pending["kind"] not in {"SCREENSHOT", "BOTH"}:
            raise HTTPException(409, "The AI did not request a screenshot")
        live = current_live_session(app_token)
        if live is None or "UI_TEST" not in live.capabilities:
            raise HTTPException(403, "UI_TEST capability is required for screenshot capture")
        try:
            body = await request.json()
        except Exception:
            body = {}
        data_url = str(body.get("image_data_url") or "")
        if not data_url.startswith("data:image/") or ";base64," not in data_url:
            raise HTTPException(400, "A base64 image data URL is required")
        header, encoded = data_url.split(",", 1)
        if "image/png" not in header and "image/jpeg" not in header:
            raise HTTPException(400, "Only PNG or JPEG screenshots are accepted")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(400, "Invalid screenshot encoding") from exc
        if not raw or len(raw) > 8 * 1024 * 1024:
            raise HTTPException(413, "Screenshot must be <= 8 MiB")
        if not (raw.startswith(b"\x89PNG\r\n\x1a\n") or raw.startswith(b"\xff\xd8\xff")):
            raise HTTPException(400, "Screenshot content does not match PNG/JPEG")
        selected_items = body.get("items") if isinstance(body.get("items"), list) else ["SCREENSHOT"]
        selected_items = [str(x).strip() for x in selected_items if str(x).strip()][:30]
        normalized = {x.upper().replace(" ", "_").replace("-", "_") for x in selected_items}
        diag_items = [x for x in selected_items if x.upper().replace(" ", "_").replace("-", "_") in {"PLAYBACK_LOG","APPLICATION_LOGS","APPLICATION_EVENTS","RUNTIME_SNAPSHOT","ENVIRONMENT","PROCESS_TOPOLOGY","BROWSER_DIAGNOSTICS"}]
        if diag_items and ("READ_LOGS" not in live.capabilities and "READ_STATUS" not in live.capabilities):
            raise HTTPException(403, "Diagnostic read capability is required")
        evidence_id = secrets.token_hex(12)
        ext = ".png" if "image/png" in header else ".jpg"
        out = safe_evidence_path("ui_" + evidence_id, ext)
        out.write_bytes(raw)
        sha256 = hashlib.sha256(raw).hexdigest()
        active = db.ai_active_investigation()
        page = str(body.get("page") or pending.get("page") or "/")[:500]
        record = {
            "evidence_id": evidence_id, "app_token": app_token,
            "investigation_id": int(pending.get("investigation_id") or active["id"]),
            "kind": pending["kind"], "created_at": config.now_iso(),
            "reason": pending.get("reason", ""), "scope": pending.get("scope", ""),
            "page_path": page, "items": selected_items, "screenshot_path": str(out),
            "summary": {"screenshot": {"page": page, "size_bytes": len(raw), "sha256": sha256}},
            "provenance": {"source": "current browser tab", "page": page, "version": config.APP_VERSION, "captured_at": config.now_iso(), "sha256": sha256, "method": "operator-selected browser tab"},
        }
        if diag_items:
            diag = await asyncio.to_thread(collect_diagnostics, db=db, requested_items=diag_items)
            record["diagnostics_text"] = diag["text"]
            record["summary"]["diagnostics"] = diag["summary"]
            record["provenance"]["redaction_count"] = diag["summary"].get("redaction_count", 0)
        pending_evidence[evidence_id] = record
        pending_requests.pop(app_token, None)
        db.ai_record_evidence_history(evidence_id=evidence_id, investigation_id=record["investigation_id"], kind=record["kind"], reason=record["reason"], scope=record["scope"], source_page=page, source="current browser tab", status="STAGED", sha256=sha256, summary=record["summary"])
        bridge.record(live, "ai_evidence:current-ui")
        return {"ok": True, "evidence_id": evidence_id, "kind": record["kind"], "summary": record["summary"], "provenance": record["provenance"], "preview": _preview_evidence(record)}

    # Store consented browser state for the pending request.
    @router.post("/api/ai/evidence/client-state")
    async def ai_evidence_client_state(request: Request):
        app_token, _ = require_auth(request)
        pending = require_pending(app_token)
        if pending["kind"] not in {"CLIENT_STATE", "BOTH"}:
            raise HTTPException(409, "The AI did not request client UI state")
        try:
            body = await request.json()
        except Exception:
            body = {}
        state = str(body.get("text") or "").strip()
        if not state or len(state) > 30000:
            raise HTTPException(400, "Client UI state is empty or too large")
        requested = {str(x).upper().replace(" ", "_").replace("-", "_") for x in (pending.get("items") or [])}
        include_cli = bool(requested & {"CLI", "CLI_OUTPUT", "VISIBLE_CLI_OUTPUT"})
        if not include_cli:
            try:
                parsed = json.loads(state)
                if isinstance(parsed, dict):
                    parsed.pop("visible_cli_output", None)
                    state = json.dumps(parsed, ensure_ascii=False, indent=2)
            except Exception:
                pass
        evidence_id = secrets.token_hex(12)
        active = db.ai_active_investigation()
        record = {
            "evidence_id": evidence_id,
            "app_token": app_token,
            "investigation_id": int(pending.get("investigation_id") or active["id"]),
            "kind": "CLIENT_STATE",
            "created_at": config.now_iso(),
            "reason": pending.get("reason", ""),
            "scope": pending.get("scope", ""),
            "client_state": state,
            "summary": {"client_state_characters": len(state), "cli_included": include_cli},
            "provenance": {"source": "current Archive X browser state", "page": pending.get("page") or "/", "version": config.APP_VERSION, "captured_at": config.now_iso(), "cli_included": include_cli, "sha256": hashlib.sha256(state.encode("utf-8")).hexdigest()},
        }
        pending_evidence[evidence_id] = record
        db.ai_record_evidence_history(evidence_id=evidence_id, investigation_id=record["investigation_id"], kind="CLIENT_STATE", reason=record["reason"], scope=record["scope"], source_page=record.get("page_path", pending.get("page") or "/"), source="current Archive X browser state", status="STAGED", sha256=str((record.get("provenance") or {}).get("sha256") or ""), summary=record["summary"])
        pending_requests.pop(app_token, None)
        db.log("AI Assistant client UI state collected with explicit operator approval")
        return {"ok": True, "evidence_id": evidence_id, "kind": "CLIENT_STATE", "summary": record["summary"], "preview": state[:4000]}

    @router.get("/api/ai/evidence/preview/{evidence_id}")
    async def ai_evidence_preview(evidence_id: str, request: Request):
        app_token, _ = require_auth(request)
        record = pending_evidence.get(str(evidence_id))
        if not record or record.get("app_token") != app_token:
            raise HTTPException(404, "Evidence not found")
        return {"ok": True, "evidence_id": evidence_id, "kind": record.get("kind"), "summary": record.get("summary") or {}, "preview": _preview_evidence(record)}

    @router.get("/api/ai/evidence/screenshot/{evidence_id}")
    async def ai_evidence_screenshot(evidence_id: str, request: Request):
        app_token, _ = require_auth(request)
        record = pending_evidence.get(str(evidence_id))
        if not record or record.get("app_token") != app_token or not record.get("screenshot_path"):
            raise HTTPException(404, "Screenshot evidence not found")
        path = Path(record["screenshot_path"]).resolve()
        allowed = config.AI_EVIDENCE_DIR.resolve()
        if allowed not in path.parents:
            raise HTTPException(403, "Invalid evidence path")
        if not path.is_file():
            raise HTTPException(404, "Screenshot evidence is no longer available")
        return FileResponse(path, media_type="image/png", filename="ui-evidence.png")

    @router.post("/api/ai/evidence/discard")
    async def ai_evidence_discard(request: Request):
        app_token, _ = require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        evidence_id = str(body.get("evidence_id") or "")
        record = pending_evidence.get(evidence_id)
        if not record or record.get("app_token") != app_token:
            raise HTTPException(404, "Evidence not found")
        _cleanup_record(record)
        pending_evidence.pop(evidence_id, None)
        db.ai_record_evidence_history(evidence_id=evidence_id, investigation_id=record.get("investigation_id"), kind=record.get("kind", ""), reason=record.get("reason", ""), scope=record.get("scope", ""), source_page=record.get("page_path", ""), source=str((record.get("provenance") or {}).get("source") or ""), status="DISCARDED", sha256=str((record.get("provenance") or {}).get("sha256") or ""), summary=record.get("summary") or {})
        db.log("AI Assistant evidence discarded by operator")
        return {"ok": True}

    # Send previously collected evidence to the assistant.
    @router.post("/api/ai/evidence/send")
    async def ai_evidence_send(request: Request):
        app_token, operator = require_auth(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        evidence_id = str(body.get("evidence_id") or "")
        record = pending_evidence.get(evidence_id)
        if not record or record.get("app_token") != app_token:
            raise HTTPException(404, "Evidence not found")
        context = project_context(db, telegram=telegram, jobs=jobs, governor=governor, page=str(record.get("page_path") or record.get("scope") or ""), selected=None)
        screenshot_data_url = ""
        shot = record.get("screenshot_path")
        if shot:
            p = Path(shot)
            raw = p.read_bytes()
            screenshot_data_url = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        try:
            result = await asyncio.to_thread(
                ai.send_evidence,
                evidence_prompt=str(record.get("reason") or "Analyze the approved evidence and continue the investigation."),
                context=context,
                screenshot_data_url=screenshot_data_url,
                diagnostics_text=str(record.get("diagnostics_text") or ""),
                client_state=str(record.get("client_state") or ""),
                user_identity=operator.get("username") or "operator",
                investigation_id=int(record.get("investigation_id") or db.ai_active_investigation()["id"]),
            )
        except LocalAIError as exc:
            raise HTTPException(502, str(exc)) from exc
        _cleanup_record(record)
        pending_evidence.pop(evidence_id, None)
        req = result.get("evidence_request") or {"kind": "NONE"}
        if str(req.get("kind") or "NONE").upper() != "NONE":
            pending_requests[app_token] = {
                **req,
                "kind": str(req.get("kind") or "NONE").upper(),
                "page": "/",
                "investigation_id": int(record.get("investigation_id") or db.ai_active_investigation()["id"]),
                "created_at": config.now_iso(),
                "expires_at": time.time() + 10 * 60,
            }
        db.ai_record_evidence_history(evidence_id=evidence_id, investigation_id=record.get("investigation_id"), kind=record.get("kind", ""), reason=record.get("reason", ""), scope=record.get("scope", ""), source_page=record.get("page_path", ""), source=str((record.get("provenance") or {}).get("source") or ""), status="SENT", sha256=str((record.get("provenance") or {}).get("sha256") or ""), summary=record.get("summary") or {})
        db.log("Local AI operator-approved evidence supplied to the local model", event="ai.evidence.supplied")
        return {"ok": True, **result}

    return ai




# ==============================================================================
# SECTION: Module helpers
# Evidence preview and cleanup.
# ==============================================================================

def _preview_evidence(record: dict[str, Any]) -> dict[str, Any]:
    out = {"reason": record.get("reason", ""), "scope": record.get("scope", ""), "summary": record.get("summary") or {}, "provenance": record.get("provenance") or {}}
    if record.get("diagnostics_text"):
        out["diagnostics_preview"] = str(record["diagnostics_text"])[:4000]
    if record.get("client_state"):
        out["client_state_preview"] = str(record["client_state"])[:4000]
    if record.get("screenshot_path"):
        out["screenshot_ready"] = True
    return out


def _cleanup_record(record: dict[str, Any]) -> None:
    path = record.get("screenshot_path")
    if not path:
        return
    try:
        p = Path(path)
        if p.exists():
            p.unlink()
    except Exception:
        pass
