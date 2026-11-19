"""Explicitly approved evidence collection for the Archive X AI Assistant.

Nothing in this module is collected or sent automatically. Routes call these
helpers only after an operator explicitly approves an evidence request.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .. import config
from ..core.diagnostics import collect_environment, runtime_snapshot_path, observability_snapshot, structured_event_tail, correlation_summary, browser_diagnostics_snapshot, metadata_assets_diagnostics, telegram_transfer_diagnostics, content_automation_diagnostics

_SECRET_PATTERNS = [
    (re.compile(r"(?i)(api[_-]?hash\s*[=:]\s*)[^,\s]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(api[_-]?key\s*[=:]\s*)[^,\s]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(password\s*[=:]\s*)[^,\s]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(token\s*[=:]\s*)[^,\s]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(phone\s*[=:]\s*)[+\d][\d\s-]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(cookie\s*[=:]\s*)[^\n]+"), r"\1<REDACTED>"),
    
    (re.compile(r"(?i)(authorization\s*[=:]\s*)[^,\s]+"), r"\1<REDACTED>"),
]


def redact_text(value: str) -> str:
    text = str(value or "")
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redaction_count(before: str, after: str) -> int:
    return max(0, str(before or "").count("<REDACTED>"), str(after or "").count("<REDACTED>"))


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lk = str(key).lower()
            if any(x in lk for x in ("password", "secret", "token", "api_hash", "api_key", "phone", "credential")):
                out[key] = "<REDACTED>"
            else:
                out[key] = _safe_json(item)
        return out
    if isinstance(value, list):
        return [_safe_json(x) for x in value]
    return value


def _tail_lines(path: Path, limit: int = 250) -> list[str]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return [redact_text(x) for x in lines[-max(1, min(int(limit), 500)) :]]
    except Exception as exc:
        return [f"<read error: {type(exc).__name__}: {exc}>"]


def collect_diagnostics(*, db, requested_items: list[str] | None = None, max_chars: int = 120_000, launch_id: str | None = None, playback_status: dict[str, Any] | None = None) -> dict[str, Any]:
    """Gather the requested diagnostic items into a bounded evidence payload."""
    items = [str(x).upper() for x in (requested_items or [])]
    allowed = {
        "PLAYBACK_LOG", "PLAYBACK_STATUS", "APPLICATION_LOGS", "APPLICATION_EVENTS",
        "CLI_LAUNCHER_LOG", "RUNTIME_SNAPSHOT", "ENVIRONMENT", "PROCESS_TOPOLOGY", "CORRELATION_INDEX", "BROWSER_DIAGNOSTICS", "METADATA_ASSETS", "TELEGRAM_TRANSFER", "CONTENT_AUTOMATION",
    }
    items = [x for x in items if x in allowed]
    if not items:
        items = ["PLAYBACK_LOG", "PLAYBACK_STATUS", "APPLICATION_EVENTS", "RUNTIME_SNAPSHOT", "ENVIRONMENT", "BROWSER_DIAGNOSTICS", "METADATA_ASSETS", "TELEGRAM_TRANSFER", "CONTENT_AUTOMATION"]

    selected_launch = str(launch_id or "").strip() or None
    payload: dict[str, Any] = {"schema": 6, "items": items, "redaction": "credential/token/key/phone patterns redacted",
                              "diagnostic_scope": {"launch_id": selected_launch or "all persisted launches", "current_process_launch_id": os.getenv("ARCHIVE_X_LAUNCH_ID") or "-"},
                              "support_readme": {
                                  "start_here": "correlation_index",
                                  "correlation_order": ["launch_id", "request_id", "trace_id", "operation_id", "job_id", "error_fingerprint"],
                                  "launcher_chain": ["Archive X desktop app", "archive-x-backend", "telegram_archive"],
                                  "note": "Use launch_id to connect Windows startup/control-plane events to application events. Use request/trace IDs for HTTP, operation/job IDs for work execution, and error fingerprints for repeated failures. Browser diagnostics connect media state, Resource Timing, network information, long tasks and browser-side failures to the same report.",
                                  "diagnosis_order": ["diagnostic_health", "incident_analysis.first_failure", "incident_analysis.incidents", "correlation_index.timeline", "raw evidence"],
                                  "log_retention": "Structured JSONL is bounded by configured rotation; repeated events are aggregated within the deduplication window.",
                              },
                              "observability": observability_snapshot(recent_limit=80, errors_limit=40)}
    def add(name: str, value: Any) -> None:
        # Keep sections structurally intact here. The final budget pass trims
        # oldest entries from event/log arrays, never slices serialized JSON.
        payload[name] = value

    if "PLAYBACK_LOG" in items:
        # Keep the tail and authoritative path together so support captures make
        # it clear which Windows data-root log was read.
        add("playback_log_path", str(Path(config.PLAYBACK_LOG_PATH).resolve()))
        add("playback_log_tail", _tail_lines(config.PLAYBACK_LOG_PATH, 300))

    if "PLAYBACK_STATUS" in items:
        add("playback_status", _safe_json(playback_status or {"available": False}))

    if "APPLICATION_LOGS" in items:
        logs = {}
        for path in sorted(config.LOG_DIR.glob("*.log")):
            if path.name in {config.PLAYBACK_LOG_PATH.name, getattr(config, "CLI_LOG_PATH", config.LOG_DIR / "launcher-cli.log").name}:
                continue
            logs[path.name] = _tail_lines(path, 220)
        add("application_logs", logs)

    cli_lines = _tail_lines(getattr(config, "CLI_LOG_PATH", config.LOG_DIR / "launcher-cli.log"), 500) if "CLI_LAUNCHER_LOG" in items or "CORRELATION_INDEX" in items else []
    if "CLI_LAUNCHER_LOG" in items:
        add("cli_launcher_log", cli_lines)

    if "CORRELATION_INDEX" in items:
        events = structured_event_tail(500)
        if selected_launch:
            events = [e for e in events if str(e.get("launch_id") or (e.get("context") or {}).get("launch_id") or "") == selected_launch]
        add("correlation_index", correlation_summary(events, cli_lines))
        add("structured_event_tail", events)

    if "APPLICATION_EVENTS" in items:
        try:
            events = db.recent_events(250)
            if selected_launch:
                events = [e for e in events if str(e.get("launch_id") or "") == selected_launch]
            add("application_events", _safe_json(events))
        except Exception as exc:
            add("application_events", [f"<event read error: {type(exc).__name__}: {exc}>"])

    if "RUNTIME_SNAPSHOT" in items:
        path = runtime_snapshot_path()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"available": False}
        except Exception as exc:
            raw = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        add("runtime_snapshot", _safe_json(raw))

    if "ENVIRONMENT" in items:
        add("environment", collect_environment())

    if "PROCESS_TOPOLOGY" in items and "environment" not in payload:
        add("environment", collect_environment())

    if "BROWSER_DIAGNOSTICS" in items:
        add("browser_diagnostics", browser_diagnostics_snapshot())

    if "METADATA_ASSETS" in items:
        add("metadata_assets", metadata_assets_diagnostics(db, limit=12))
    if "TELEGRAM_TRANSFER" in items:
        add("telegram_transfer", telegram_transfer_diagnostics(db, limit=12))
    if "CONTENT_AUTOMATION" in items:
        add("content_automation", content_automation_diagnostics(db, limit=12))

    # Bound the report by trimming oldest records while preserving the complete
    # JSON structure. This replaces the former final `text[:max_chars]` slice,
    # which cut the closing braces and made exported reports invalid JSON.
    def serialize() -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2, default=str)

    def list_refs(value, path=()):
        found = []
        if isinstance(value, dict):
            for key, child in value.items():
                found.extend(list_refs(child, path + (str(key),)))
        elif isinstance(value, list):
            if value:
                encoded_size = sum(len(json.dumps(item, ensure_ascii=False, default=str)) for item in value)
                label = ".".join(path).lower()
                priority = 2 if any(token in label for token in ("event", "log", "timeline", "resource", "history", "trace", "line")) else 1
                found.append((priority, encoded_size, len(value), path, value))
            for index, child in enumerate(value):
                found.extend(list_refs(child, path + (str(index),)))
        return found

    def string_refs(value, path=()):
        found = []
        if isinstance(value, dict):
            for key, child in value.items():
                found.extend(string_refs(child, path + (str(key),)))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                found.extend(string_refs(child, path + (str(index),)))
        elif isinstance(value, str) and len(value) > 1000:
            found.append((len(value), path, value))
        return found

    def parent_at(path):
        parent = payload
        for part in path[:-1]:
            parent = parent[int(part)] if isinstance(parent, list) else parent[part]
        return parent, path[-1]

    text = serialize()
    budget = max(1000, int(max_chars or 120_000))
    while len(text) > budget:
        collections = list_refs(payload)
        if collections:
            # Prefer event/log histories over structural arrays; remove older
            # items from the start so the newest incident evidence survives.
            _priority, encoded_size, count, path, values = max(
                collections, key=lambda item: (item[0], item[1], item[2])
            )
            average = max(1, encoded_size // max(1, count))
            excess = len(text) - budget + 256
            remove_n = max(1, min(count, (excess + average - 1) // average))
            del values[:remove_n]
        else:
            strings = string_refs(payload)
            if not strings:
                break
            length, path, value = max(strings, key=lambda item: item[0])
            excess = len(text) - budget + 256
            keep = max(256, length - excess)
            parent, key = parent_at(path)
            if isinstance(parent, list):
                parent[int(key)] = "<older text trimmed>\n" + value[-keep:]
            else:
                parent[key] = "<older text trimmed>\n" + value[-keep:]
        text = serialize()

    redactions = text.count("<REDACTED>")
    return {
        "kind": "DIAGNOSTICS",
        "items": items,
        "text": text,
        "summary": {
            "items": items,
            "characters": len(text),
            "within_requested_budget": len(text) <= budget,
            "redacted": True,
            "redaction_count": redactions,
        },
    }



def render_diagnostics_html(report_text: str) -> str:
    """Render the canonical JSON diagnostic report as a bounded human report."""
    import html as _html
    try:
        payload = json.loads(report_text)
    except Exception:
        return "<html><body><h1>Archive X Diagnostic Report</h1><pre>" + _html.escape(report_text[:180000]) + "</pre></body></html>"
    obs = payload.get("observability") or {}
    health = obs.get("diagnostic_health") or {}
    incidents = (payload.get("correlation_index") or {}).get("incident_analysis") or obs.get("incident_analysis") or {}
    first = incidents.get("first_failure") or {}
    rows = []
    for item in (incidents.get("incidents") or [])[:20]:
        vals = [item.get(k) or "-" for k in ("severity","event","first_seen","occurrences","message")]
        rows.append("<tr>" + "".join(f"<td>{_html.escape(str(v))}</td>" for v in vals) + "</tr>")
    timeline = []
    for item in ((payload.get("correlation_index") or {}).get("timeline") or [])[-80:]:
        vals = [item.get(k) or "-" for k in ("ts","level","event","message")]
        timeline.append("<tr>" + "".join(f"<td>{_html.escape(str(v))}</td>" for v in vals) + "</tr>")
    css = "body{font:14px system-ui,sans-serif;background:#f6f6f6;color:#222;margin:32px}main{max-width:1100px;margin:auto;background:white;padding:28px;border-radius:14px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{border:1px solid #ddd;border-radius:10px;padding:12px}table{width:100%;border-collapse:collapse;margin:12px 0 28px}th,td{text-align:left;padding:7px;border-bottom:1px solid #eee;vertical-align:top}pre{white-space:pre-wrap;word-break:break-word}.state{font-weight:700}small{color:#666}"
    evidence = {k:v for k,v in payload.items() if k not in {"correlation_index","observability"}}
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Archive X Diagnostic Report</title><style>{css}</style></head><body><main>
<h1>Archive X Diagnostic Report</h1><p><small>Schema {payload.get('schema','-')} · Scope: {_html.escape(str((payload.get('diagnostic_scope') or {}).get('launch_id') or 'current'))}</small></p>
<div class='grid'><div class='card'><small>Health</small><div class='state'>{_html.escape(str(health.get('state') or 'UNKNOWN'))}</div></div><div class='card'><small>Recent failures</small><div class='state'>{health.get('recent_failures',0)}</div></div><div class='card'><small>Incidents</small><div class='state'>{incidents.get('active_incident_count',0)}</div></div><div class='card'><small>Duplicate events</small><div class='state'>{obs.get('duplicate_events',0)}</div></div></div>
<h2>First failure</h2><pre>{_html.escape(json.dumps(first,ensure_ascii=False,indent=2))}</pre>
<h2>Incident summary</h2><table><thead><tr><th>Severity</th><th>Event</th><th>First seen</th><th>Count</th><th>Message</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5">No incidents detected.</td></tr>'}</tbody></table>
<h2>Timeline</h2><table><thead><tr><th>Time</th><th>Level</th><th>Event</th><th>Message</th></tr></thead><tbody>{''.join(timeline) or '<tr><td colspan="4">No timeline entries.</td></tr>'}</tbody></table>
<h2>Evidence</h2><pre>{_html.escape(json.dumps(evidence,ensure_ascii=False,indent=2,default=str)[:120000])}</pre>
</main></body></html>"""

def evidence_dir() -> Path:
    p = config.AI_EVIDENCE_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_evidence_path(evidence_id: str, suffix: str = "") -> Path:
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(evidence_id or ""))[:80]
    if not clean:
        raise ValueError("Invalid evidence id")
    return evidence_dir() / f"{clean}{suffix}"
