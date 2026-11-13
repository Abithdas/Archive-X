"""Structured diagnostics and AI-shareable incident reporting.

The diagnostic stream is deliberately append-only JSONL plus an atomic runtime
snapshot. It contains operational state, not credentials. Secrets and tokens
are redacted before anything is written to a shareable report.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Redaction and browser diagnostics
#   - Paths and process topology
#   - Structured event recording
#   - Feature diagnostics
#   - Runtime snapshots and reporting
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import json
import os
import platform
import socket
import sys
import time
import traceback
import re
import hashlib
import subprocess
import threading
import atexit
from pathlib import Path
from typing import Any, Optional

from .. import config
from . import observability
from .request_context import correlation_snapshot




# ==============================================================================
# SECTION: Redaction and browser diagnostics
# Removes sensitive values and ingests diagnostics sent by the browser.
# ==============================================================================

def _redact_text(value: str) -> str:
    text = str(value)
    patterns = [
        (r'(?i)(api[_-]?hash\s*[=:]\s*)[^,\s]+', r'\1<REDACTED>'),
        (r'(?i)(api[_-]?key\s*[=:]\s*)[^,\s]+', r'\1<REDACTED>'),
        (r'(?i)(password\s*[=:]\s*)[^,\s]+', r'\1<REDACTED>'),
        (r'(?i)(token\s*[=:]\s*)[^,\s]+', r'\1<REDACTED>'),
        (r'(?i)(authorization\s*:\s*bearer\s+)[^\s]+', r'\1<REDACTED>'),
        (r'(?i)(phone\s*[=:]\s*)[+\d][\d\s-]+', r'\1<REDACTED>'),
    ]
    for pattern, repl in patterns:
        text = re.sub(pattern, repl, text)
    return text


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key = str(k).lower()
            if any(x in key for x in ("password", "secret", "token", "api_hash", "api_key", "phone")):
                out[k] = "<REDACTED>"
            else:
                out[k] = _safe(v)
        return out
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    return value



_TOPOLOGY_CACHE = {"at": 0.0, "value": {}}
_TOPOLOGY_LOCK = threading.Lock()
_DIAG_LOCK = threading.RLock()
_DIAG_FH = None
_DIAG_PATH = None
_DIAG_PENDING = 0
_DIAG_LAST_FLUSH = 0.0
_DIAG_BATCH = 25
_DIAG_INTERVAL = 0.50

_BROWSER_LOCK = threading.RLock()
_BROWSER_STATE: dict[str, Any] = {
    "schema": 1,
    "captured_at": None,
    "sessions": {},
    "events_received": 0,
    "resource_samples": 0,
    "long_tasks": 0,
    "last_log_monotonic": 0.0,
}


def ingest_browser_diagnostics(*, session_id: str, snapshot: Optional[dict] = None,
                               resources: Optional[list] = None, events: Optional[list] = None) -> None:
    """Merge bounded browser/runtime telemetry into the in-process diagnostic snapshot."""
    sid = str(session_id or "-")[:80]
    if sid == "-":
        return
    snap = _safe(snapshot or {})
    res = _safe(resources or [])
    evs = _safe(events or [])
    with _BROWSER_LOCK:
        sessions = _BROWSER_STATE.setdefault("sessions", {})
        state = sessions.setdefault(sid, {"first_seen": config.now_iso(), "events": [], "resources": []})
        state["last_seen"] = config.now_iso()
        state["snapshot"] = snap
        if isinstance(res, list):
            cap = max(1, int(getattr(config, "BROWSER_DIAGNOSTICS_MAX_RESOURCES", 40)))
            state["resources"] = (state.get("resources") or [])[-99:] + res[:cap]
            state["resources"] = state["resources"][-100:]
            _BROWSER_STATE["resource_samples"] = int(_BROWSER_STATE.get("resource_samples", 0)) + len(res)
        if isinstance(evs, list):
            state["events"] = (state.get("events") or [])[-99:] + evs[:40]
            state["events"] = state["events"][-100:]
            _BROWSER_STATE["events_received"] = int(_BROWSER_STATE.get("events_received", 0)) + len(evs)
            _BROWSER_STATE["long_tasks"] = int(_BROWSER_STATE.get("long_tasks", 0)) + sum(1 for x in evs if isinstance(x, dict) and x.get("type") == "browser.longtask")
        while len(sessions) > 8:
            sessions.pop(next(iter(sessions)))
        _BROWSER_STATE["captured_at"] = config.now_iso()


def browser_diagnostics_snapshot() -> dict[str, Any]:
    with _BROWSER_LOCK:
        out = dict(_BROWSER_STATE)
        out.pop("last_log_monotonic", None)
        return _safe(out)





# ==============================================================================
# SECTION: Paths and process topology
# Hashing, process topology and log/event file locations.
# ==============================================================================

def _sha256(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _process_topology() -> dict:
    """Describe the running processes and modules; cached for 15 seconds."""
    now = time.monotonic()
    with _TOPOLOGY_LOCK:
        if now - float(_TOPOLOGY_CACHE["at"]) < 15.0:
            return _TOPOLOGY_CACHE["value"]
        rows = []
        port_owners = []
        if os.name == "nt":
            try:
                cmd = ["powershell", "-NoProfile", "-Command",
                       "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                       "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | "
                       "ConvertTo-Json -Compress"]
                raw = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip()
                value = json.loads(raw) if raw else []
                if isinstance(value, dict): value = [value]
                rows = [{k: str(v)[:500] for k, v in item.items() if v is not None} for item in value]
            except Exception as exc:
                rows = [{"error": f"{type(exc).__name__}: {exc}"}]
            try:
                cmd = ["powershell", "-NoProfile", "-Command",
                       f"Get-NetTCPConnection -LocalPort {int(config.LOCAL_PLAYER_PORT)} -ErrorAction SilentlyContinue | "
                       "Select-Object LocalAddress,LocalPort,RemoteAddress,RemotePort,State,OwningProcess | ConvertTo-Json -Compress"]
                raw = subprocess.run(cmd, capture_output=True, text=True, timeout=2).stdout.strip()
                value = json.loads(raw) if raw else []
                if isinstance(value, dict): value = [value]
                port_owners = [{k: v for k, v in item.items()} for item in value]
            except Exception as exc:
                port_owners = [{"error": f"{type(exc).__name__}: {exc}"}]
        source_files = {}
        for name, path in {
            "config": config.PACKAGE_DIR / "config.py",
            "routes": config.PACKAGE_DIR / "web" / "routes.py",
            "stream_server": config.PACKAGE_DIR / "playback" / "stream_server.py",
            "telegram_service": config.PACKAGE_DIR / "telegram" / "service.py",
            "telegram_client_policy": config.PACKAGE_DIR / "telegram" / "client_policy.py",
            "diagnostics": Path(__file__).resolve(),
        }.items():
            source_files[name] = {"path": str(path), "sha256": _sha256(path)}

        # Record the actual imported Telegram modules as well as their source
        # files. This catches stale installations/PYTHONPATH collisions where
        # the on-disk package and the module loaded by the running process
        # are different copies.
        telegram_import = {}
        try:
            import importlib
            service_mod = importlib.import_module("telegram_archive.telegram.service")
            policy_mod = importlib.import_module("telegram_archive.telegram.client_policy")
            factory = getattr(service_mod, "make_telegram_client", None)
            telegram_import = {
                "service_module": str(getattr(service_mod, "__file__", "")),
                "client_policy_module": str(getattr(policy_mod, "__file__", "")),
                "make_telegram_client_available": callable(factory),
                "service_package_matches_runtime": str(getattr(service_mod, "__file__", "")).startswith(str(config.PACKAGE_DIR)),
                "client_policy_package_matches_runtime": str(getattr(policy_mod, "__file__", "")).startswith(str(config.PACKAGE_DIR)),
            }
        except Exception as exc:
            telegram_import = {"error": f"{type(exc).__name__}: {exc}"}
        topo = {
            "app_dir": str(config.APP_DIR),
            "package_dir": str(config.PACKAGE_DIR),
            "cwd": os.getcwd(),
            "python_executable": sys.executable,
            "python_processes": rows,
            "player_port": int(config.LOCAL_PLAYER_PORT),
            "player_port_owners": port_owners,
            "source_files": source_files,
            "telegram_import": telegram_import,
        }
        _TOPOLOGY_CACHE.update({"at": now, "value": topo})
        return topo


def _tail_file(path: Path, limit: int = 300) -> list[str]:
    try:
        if not path.exists() or not path.is_file():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return [_redact_text(x) for x in lines[-max(1, min(int(limit), 1000)):]]
    except Exception as exc:
        return [f"<read error: {type(exc).__name__}: {exc}>"]


def event_path() -> Path:
    p = config.LOG_DIR / "system_events.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def runtime_snapshot_path() -> Path:
    p = config.RUNTIME_STATE_DIR / "runtime_snapshot.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p




# ==============================================================================
# SECTION: Structured event recording
# Rotating event files, record_event and flush handling.
# ==============================================================================

def _rotate_event_file_locked(path: Path) -> None:
    """Bound the persistent structured-event stream without introducing another logger."""
    try:
        limit = int(getattr(config, "DIAGNOSTICS_EVENT_MAX_BYTES", 10 * 1024 * 1024))
        backups = int(getattr(config, "DIAGNOSTICS_EVENT_BACKUPS", 5))
        if not path.exists() or path.stat().st_size < limit:
            return
        if _DIAG_FH is not None and not _DIAG_FH.closed:
            _DIAG_FH.flush()
            _DIAG_FH.close()
        for idx in range(backups, 0, -1):
            src = path.with_name(f"{path.name}.{idx}")
            dst = path.with_name(f"{path.name}.{idx + 1}")
            if src.exists():
                if idx >= backups:
                    src.unlink(missing_ok=True)
                else:
                    src.replace(dst)
        path.replace(path.with_name(f"{path.name}.1"))
    except Exception:
        # A diagnostic rotation failure must never affect application work.
        _swallowed_note("diagnostics._rotate_event_file_locked")
        pass


def record_event(*, event: str, level: str = "INFO", message: str = "",
                 job_id: Optional[str] = None, pipeline: Optional[dict] = None,
                 operation: Optional[str] = None, error: Optional[BaseException] = None,
                 details: Optional[dict] = None) -> None:
    """Write one structured diagnostic event with correlation context."""
    ctx = correlation_snapshot()
    item = {
        "ts": config.now_iso(),
        "pid": os.getpid(),
        "tid": threading.get_ident(),
        "level": str(level).upper(),
        "event": event,
        "message": str(message or ""),
        "job_id": job_id or (ctx.get("job_id") if ctx else None),
        "operation": operation or (ctx.get("operation_id") if ctx else None),
        "request_id": ctx.get("request_id") if ctx else "-",
        "trace_id": ctx.get("trace_id") if ctx else "-",
        "span_id": ctx.get("span_id") if ctx else "-",
        "component": (details or {}).get("component") or (ctx.get("component") if ctx else "-"),
        "launch_id": (details or {}).get("launch_id") or (ctx.get("launch_id") if ctx else os.getenv("ARCHIVE_X_LAUNCH_ID") or "-"),
        "pipeline": _safe(pipeline or {}),
        "details": _safe(details or {}),
        "context": _safe(ctx),
    }
    if error is not None:
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))[-12000:]
        fp = observability.error_fingerprint(event=str(event), error_type=type(error).__name__, message=str(error), traceback_text=tb)
        item["error"] = {
            "type": type(error).__name__,
            "message": str(error),
            "fingerprint": fp,
            "traceback": tb,
        }
    global _DIAG_FH, _DIAG_PATH, _DIAG_PENDING, _DIAG_LAST_FLUSH
    try:
        item = observability.ingest(_safe(item))
        should_persist = bool(item.pop("_persist", True))
        if not should_persist:
            return
        path = event_path()
        line = json.dumps(_safe(item), ensure_ascii=False, separators=(",", ":")) + "\n"
        with _DIAG_LOCK:
            _rotate_event_file_locked(path)
            if _DIAG_FH is None or _DIAG_FH.closed or _DIAG_PATH != path:
                if _DIAG_FH is not None and not _DIAG_FH.closed:
                    try: _DIAG_FH.flush()
                    except Exception:
                        _swallowed_note("diagnostics.record_event")
                        pass
                    try: _DIAG_FH.close()
                    except Exception:
                        _swallowed_note("diagnostics.record_event")
                        pass
                _DIAG_FH = path.open("a", encoding="utf-8", buffering=1)
                _DIAG_PATH = path
                _DIAG_LAST_FLUSH = time.monotonic()
            _DIAG_FH.write(line)
            _DIAG_PENDING += 1
            now = time.monotonic()
            if _DIAG_PENDING >= _DIAG_BATCH or (now - _DIAG_LAST_FLUSH) >= _DIAG_INTERVAL:
                _DIAG_FH.flush()
                _DIAG_PENDING = 0
                _DIAG_LAST_FLUSH = now
    except Exception:
        # Diagnostics must never become an application failure path.
        _swallowed_note("diagnostics.record_event")
        pass


def flush_diagnostics() -> None:
    global _DIAG_FH, _DIAG_PENDING, _DIAG_LAST_FLUSH
    try:
        with _DIAG_LOCK:
            if _DIAG_FH is not None and not _DIAG_FH.closed:
                _DIAG_FH.flush()
                _DIAG_PENDING = 0
                _DIAG_LAST_FLUSH = time.monotonic()
    except Exception:
        _swallowed_note("diagnostics.flush_diagnostics")
        pass


@atexit.register
def _flush_at_exit() -> None:
    flush_diagnostics()
    try:
        with _DIAG_LOCK:
            if _DIAG_FH is not None and not _DIAG_FH.closed:
                _DIAG_FH.close()
    except Exception:
        _swallowed_note("diagnostics._flush_at_exit")
        pass




# ==============================================================================
# SECTION: Feature diagnostics
# Per-feature diagnostic snapshots.
# ==============================================================================

def telegram_transfer_diagnostics(db, *, limit: int = 12) -> dict[str, Any]:
    """Return bounded evidence for the unified Telegram Transfer Manager.

    The durable transfer job row is authoritative for lifecycle state;
    transfer_locations is authoritative for verified source/destination pairs.
    """
    result = {"job_type": "TELEGRAM_TRANSFER", "active": [], "recent": [], "verified_transfers": 0, "issues": []}
    if db is None:
        return result
    try:
        types = {"TELEGRAM_TRANSFER", "UPLOAD_LOCAL", "MEDIA_MANAGER"}
        rows = [j for j in db.jobs(limit=max(1, min(int(limit), 25))) if str(j.get("job_type") or "") in types]
        active_ids = {str(j.get("id")) for t in types for j in db.active_jobs(t)}
        for job in rows:
            detail = {}
            try: detail = json.loads(job.get("detail") or "{}")
            except Exception:
                _swallowed_note("diagnostics.telegram_transfer_diagnostics")
                pass
            child_states = {}
            for name, child_id in (detail.get("preparation_jobs") or {}).items() if isinstance(detail.get("preparation_jobs"), dict) else []:
                child = db.get_job(str(child_id))
                child_states[str(name)] = {"job_id": str(child_id), "status": child.get("status") if child else "MISSING", "error": child.get("error") if child else ""}
            item = {"job_id": str(job.get("id") or ""), "job_type": job.get("job_type") or "", "status": job.get("status") or "", "created_at": job.get("created_at") or "", "updated_at": job.get("updated_at") or "", "completed": int(job.get("completed") or 0), "total": int(job.get("total") or 0), "current_operation": job.get("current_operation") or "", "current_item": job.get("current_item") or "", "error": job.get("error") or "", "source": detail.get("source") or "", "source_channels": detail.get("source_channels") or [], "destination_channel": detail.get("destination_channel") or "", "preparation": detail.get("preparation") or "", "preparation_jobs": child_states}
            (result["active"] if item["job_id"] in active_ids else result["recent"]).append(item)
            if item["error"]:
                result["issues"].append({"job_id": item["job_id"], "source": "job", "level": "ERROR", "message": str(item["error"])[:1000]})
        with db.lock:
            row = db.conn.execute("SELECT COUNT(*) n FROM transfer_locations WHERE status='VERIFIED'").fetchone()
            result["verified_transfers"] = int(row["n"] or 0) if row else 0
        for event in structured_event_tail(300):
            event_name = str(event.get("event") or "")
            message = str(event.get("message") or "")
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            if "transfer" in event_name.lower() or "telegram transfer" in message.lower():
                level = str(event.get("level") or "INFO").upper()
                if level in {"WARN", "ERROR", "CRITICAL"}:
                    result["issues"].append({"job_id": str(event.get("job_id") or details.get("job_id") or ""), "source": "structured_event", "level": level, "event": event_name, "message": message[:1000]})
    except Exception as exc:
        result["collection_error"] = f"{type(exc).__name__}: {exc}"
    result["active"] = result["active"][:max(1, min(int(limit), 25))]
    result["recent"] = result["recent"][:max(1, min(int(limit), 25))]
    result["issues"] = result["issues"][:max(1, min(int(limit) * 2, 50))]
    return result



def content_automation_diagnostics(db, *, limit: int = 12) -> dict[str, Any]:
    """Return bounded Content Automation acquisition/preparation/delivery evidence."""
    result = {
        "job_type": "CONTENT_AUTOMATION",
        "active": [], "recent": [], "children": [], "issues": [],
        "counts": {}, "prepared": 0, "published": 0, "cleaned": 0,
    }
    if db is None:
        return result
    try:
        from ..operations import automation as content_automation
        content_automation._ensure_schema(db)
        all_jobs = db.jobs(limit=max(25, min(int(limit) * 8, 200)))
        jobs = [j for j in all_jobs if str(j.get("job_type") or "") == "CONTENT_AUTOMATION"]
        active_ids = {str(j.get("id")) for j in db.active_jobs("CONTENT_AUTOMATION")}
        parent_ids = {str(j.get("id") or "") for j in jobs}
        for child in all_jobs:
            child_type = str(child.get("job_type") or "")
            if child_type not in {"METADATA_CHANNEL_PIPELINE", "TELEGRAM_TRANSFER"}:
                continue
            try:
                detail = json.loads(child.get("detail") or "{}")
            except Exception:
                detail = {}
            parent_id = str(detail.get("parent_job_id") or "")
            if parent_id not in parent_ids:
                continue
            result["children"].append({
                "job_id": str(child.get("id") or ""),
                "parent_job_id": parent_id,
                "job_type": child_type,
                "status": child.get("status") or "",
                "created_at": child.get("created_at") or "",
                "updated_at": child.get("updated_at") or "",
                "current_operation": child.get("current_operation") or "",
                "error": child.get("error") or "",
            })
        for job in jobs:
            item = {
                "job_id": str(job.get("id") or ""), "status": job.get("status") or "",
                "created_at": job.get("created_at") or "", "updated_at": job.get("updated_at") or "",
                "completed": int(job.get("completed") or 0), "total": int(job.get("total") or 0),
                "current_operation": job.get("current_operation") or "", "current_item": job.get("current_item") or "",
                "error": job.get("error") or "",
            }
            (result["active"] if item["job_id"] in active_ids else result["recent"]).append(item)
            if item["error"]:
                result["issues"].append({"job_id": item["job_id"], "source": "job", "level": "ERROR", "message": str(item["error"])[:1000]})
        with db.lock:
            for row in db.conn.execute("SELECT status,COUNT(*) c FROM automation_items GROUP BY status").fetchall():
                result["counts"][str(row["status"])] = int(row["c"] or 0)
            result["prepared"] = int(db.conn.execute("SELECT COUNT(*) c FROM automation_items WHERE content_plan_status IN ('READY_TO_TRANSFER','TRANSFERRING','DELIVERY_VERIFIED','ALREADY_PUBLISHED','PUBLISHED')").fetchone()["c"] or 0)
            result["published"] = int(db.conn.execute("SELECT COUNT(*) c FROM automation_items WHERE content_plan_status IN ('PUBLISHED','ALREADY_PUBLISHED')").fetchone()["c"] or 0)
            result["cleaned"] = int(db.conn.execute("SELECT COUNT(*) c FROM automation_items WHERE source_cleanup_state='CLEANED'").fetchone()["c"] or 0)
        for event in structured_event_tail(400):
            name = str(event.get("event") or "")
            message = str(event.get("message") or "")
            if "content_automation" in name.casefold() or "content automation" in message.casefold():
                level = str(event.get("level") or "INFO").upper()
                if level in {"WARN", "ERROR", "CRITICAL"}:
                    result["issues"].append({"job_id": str(event.get("job_id") or ""), "source": "structured_event", "level": level, "event": name, "message": message[:1000]})
    except Exception as exc:
        result["collection_error"] = f"{type(exc).__name__}: {exc}"
    cap = max(1, min(int(limit), 25))
    result["active"] = result["active"][:cap]
    result["recent"] = result["recent"][:cap]
    result["children"] = result["children"][:max(1, min(int(limit) * 4, 100))]
    result["issues"] = result["issues"][:max(1, min(int(limit) * 2, 50))]
    return result

def metadata_assets_diagnostics(db, *, limit: int = 12) -> dict[str, Any]:
    """Return bounded Metadata & Assets job/checkpoint evidence for diagnostics.

    The existing METADATA_CHANNEL_PIPELINE job checkpoint is authoritative for
    progress. This projection deliberately does not create another scheduler
    or telemetry store; it joins the durable job row, hierarchical checkpoint,
    and recent correlated events so a report can explain both progress and
    failures.
    """
    result = {
        "job_type": "METADATA_CHANNEL_PIPELINE",
        "active": [],
        "recent": [],
        "issues": [],
        "publication": {},
    }
    if db is None:
        return result
    try:
        from ..engines import metadata_channel_pipeline
        from .resource_registry import media_readiness_stats
        rows = [j for j in db.jobs(limit=max(1, min(int(limit), 25)))
                if str(j.get("job_type") or "") == "METADATA_CHANNEL_PIPELINE"]
        active_ids = {str(j.get("id")) for j in db.active_jobs("METADATA_CHANNEL_PIPELINE")}
        for job in rows:
            jid = str(job.get("id") or "")
            detail = job.get("detail") or "{}"
            progress = metadata_channel_pipeline.progress_snapshot(detail)
            readiness = progress.get("readiness") if isinstance(progress.get("readiness"), dict) else {}
            item = {
                "job_id": jid,
                "status": job.get("status") or "",
                "created_at": job.get("created_at") or "",
                "updated_at": job.get("updated_at") or "",
                "completed": int(job.get("completed") or 0),
                "total": int(job.get("total") or 0),
                "current_operation": job.get("current_operation") or "",
                "current_item": job.get("current_item") or "",
                "error": job.get("error") or "",
                "progress": progress,
                "readiness": readiness,
            }
            if jid in active_ids:
                result["active"].append(item)
            else:
                result["recent"].append(item)
            if item["error"]:
                result["issues"].append({"job_id": jid, "source": "job", "level": "ERROR", "message": str(item["error"])[:1000]})
            for channel in progress.get("channels") or []:
                if channel.get("error"):
                    result["issues"].append({"job_id": jid, "source": "channel", "channel_id": channel.get("channel_id"), "level": "ERROR", "message": str(channel.get("error"))[:1000]})
                for phase in channel.get("phases") or []:
                    if str(phase.get("state") or "").upper() == "FAILED":
                        result["issues"].append({"job_id": jid, "source": "phase", "channel_id": channel.get("channel_id"), "phase": phase.get("phase"), "level": "ERROR", "message": str(channel.get("error") or "Phase failed")[:1000]})
        # Publication is a per-video projection. Diagnostics expose it directly
        # so an operator can distinguish processed work from media that is actually
        # available to Library/Performer/Studio consumers.
        result["publication"] = media_readiness_stats(db)
        # Correlated event evidence is bounded and filtered to the canonical
        # pipeline so unrelated application noise is not presented as a
        # Metadata & Assets failure.
        for event in structured_event_tail(300):
            if str(event.get("job_id") or "") not in {str(x.get("job_id")) for x in result["active"] + result["recent"]}:
                continue
            if str(event.get("level") or "").upper() in {"WARN", "ERROR", "CRITICAL"}:
                result["issues"].append({
                    "job_id": event.get("job_id"),
                    "source": "event",
                    "timestamp": event.get("ts"),
                    "level": event.get("level"),
                    "event": event.get("event"),
                    "operation": event.get("operation"),
                    "message": str(event.get("message") or "")[:1000],
                    "error": _safe(event.get("error") or {}),
                    "details": _safe(event.get("details") or {}),
                })
        result["issues"] = result["issues"][-100:]
    except Exception as exc:
        result["collection_error"] = {"type": type(exc).__name__, "message": str(exc)[:1000]}
    return _safe(result)


def observability_snapshot(*, recent_limit: int = 80, errors_limit: int = 40) -> dict[str, Any]:
    return observability.snapshot(recent_limit=recent_limit, errors_limit=errors_limit)




# ==============================================================================
# SECTION: Runtime snapshots and reporting
# Event tails, correlation summaries and environment collection.
# ==============================================================================

def recent_structured_events(limit: int = 100) -> list[dict[str, Any]]:
    return observability.recent(limit)


def write_runtime_snapshot(*, db=None, jobs=None, telegram=None, governor=None,
                            extra: Optional[dict] = None) -> None:
    """Persist the latest runtime topology so it remains available after a crash."""
    try:
        payload = {
            "schema": 1,
            "captured_at": config.now_iso(),
            "pid": os.getpid(),
            "launch_id": os.getenv("ARCHIVE_X_LAUNCH_ID") or "-",
            "app_version": config.APP_VERSION,
            "host": socket.gethostname(),
            "platform": platform.platform(),
            "python": sys.version,
            "user_data_dir": str(config.USER_DATA_DIR),
            "telegram_connected": bool(getattr(telegram, "connected", False)) if telegram else None,
            "active_jobs": db.active_jobs() if db else [],
            "pipelines": jobs.pipeline_snapshot() if jobs else [],
            "watchdogs": jobs.watchdogs.snapshot() if jobs and hasattr(jobs, "watchdogs") else {},
            "governor": governor.snapshot() if governor else {},
            "extra": _safe({**(extra or {}), "runtime_topology": _process_topology(), "browser_diagnostics": browser_diagnostics_snapshot()}),
        }
        tmp = runtime_snapshot_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(_safe(payload), indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(runtime_snapshot_path())
    except Exception:
        _swallowed_note("diagnostics.write_runtime_snapshot")
        pass


def structured_event_tail(limit: int = 500) -> list[dict[str, Any]]:
    """Read a bounded tail across the current and rotated structured event files."""
    path = event_path()
    try:
        paths = [path] + [path.with_name(f"{path.name}.{i}") for i in range(1, int(getattr(config, "DIAGNOSTICS_EVENT_BACKUPS", 5)) + 1)]
        lines: list[str] = []
        for candidate in paths:
            lines.extend(_tail_file(candidate, max(20, int(limit // max(1, len(paths))))))
        out = []
        for line in lines[-max(1, min(int(limit), 1000)):]:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    out.append(value)
            except Exception:
                _swallowed_note("diagnostics.structured_event_tail")
                continue
        out.sort(key=lambda x: str(x.get("ts") or ""))
        return out[-max(1, min(int(limit), 1000)):]
    except Exception:
        return []


def correlation_summary(events: list[dict[str, Any]], cli_lines: list[str] | None = None) -> dict[str, Any]:
    """Create a human-readable correlation index from structured evidence."""
    counters: dict[str, dict[str, int]] = {
        "launch_id": {}, "request_id": {}, "trace_id": {},
        "operation_id": {}, "job_id": {}, "error_fingerprint": {}
    }
    errors = []
    timeline = []
    for item in events:
        ctx = item.get("context") or {}
        values = {
            "launch_id": item.get("launch_id") or ctx.get("launch_id"),
            "request_id": item.get("request_id") or ctx.get("request_id"),
            "trace_id": item.get("trace_id") or ctx.get("trace_id"),
            "operation_id": item.get("operation") or ctx.get("operation_id"),
            "job_id": item.get("job_id") or ctx.get("job_id"),
        }
        for key, value in values.items():
            value = str(value or "-")
            if value != "-":
                counters[key][value] = counters[key].get(value, 0) + 1
        err = item.get("error")
        if isinstance(err, dict):
            fp = str(err.get("fingerprint") or "-")
            if fp != "-":
                counters["error_fingerprint"][fp] = counters["error_fingerprint"].get(fp, 0) + 1
            errors.append({"ts": item.get("ts"), "event": item.get("event"), "level": item.get("level"),
                           "message": item.get("message"), "type": err.get("type"),
                           "fingerprint": fp, "job_id": values["job_id"],
                           "operation_id": values["operation_id"], "request_id": values["request_id"],
                           "launch_id": values["launch_id"]})
        if len(timeline) < 80:
            timeline.append({"ts": item.get("ts"), "level": item.get("level"), "event": item.get("event"),
                             "message": item.get("message"), "launch_id": values["launch_id"],
                             "job_id": values["job_id"], "operation_id": values["operation_id"],
                             "request_id": values["request_id"]})
    from . import observability as _obs
    return {
        "purpose": "Connect launcher, HTTP, jobs, operations and errors using shared identifiers.",
        "diagnostic_health": _obs.diagnostic_health(),
        "incident_analysis": _obs._incident_analysis(events),
        "launches": counters["launch_id"],
        "requests": counters["request_id"],
        "traces": counters["trace_id"],
        "operations": counters["operation_id"],
        "jobs": counters["job_id"],
        "error_fingerprints": counters["error_fingerprint"],
        "error_count": len(errors),
        "errors": errors[-50:],
        "timeline": timeline,
        "launcher_cli_lines": len(cli_lines or []),
    }


def collect_environment() -> dict:
    """Return non-secret environment/runtime facts for a diagnostic bundle."""
    return _safe({
        "app_version": config.APP_VERSION,
        "pid": os.getpid(),
        "launch_id": os.getenv("ARCHIVE_X_LAUNCH_ID") or "-",
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "hostname": socket.gethostname(),
        "os_name": os.name,
        "cwd": os.getcwd(),
        "runtime_topology": _process_topology(),
        "browser_diagnostics": browser_diagnostics_snapshot(),
    })
