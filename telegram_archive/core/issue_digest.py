"""A short, ranked list of what is going wrong, made from the logs, the failed jobs and the errors the code ignores, by plain rules (no model).

Why it exists: finding a problem should not need a large model reading thousands of log lines. This turns them into a few issues, each with how often it
happened, when, two real lines as evidence, the usual cause and where to look. A small local model (or a person) can read the whole digest at once, explain it
and say what to check next. Nothing here changes anything: it reads, groups and reports.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

from .ai_safety import redact_text
from . import swallowed

# (id, area, severity, pattern, title, usual cause, where to look)
SIGNATURES: list[tuple[str, str, str, str, str, str, str]] = [
    ("telegram-flood", "Telegram", "medium", r"floodwait|flood wait|a wait of \d+ seconds", "Telegram asked the app to slow down", "Too many requests in a short time; Telegram makes the app wait.", "Transfers: lanes show the wait. It ends by itself; fewer parallel transfers help."),
    ("telegram-session", "Telegram", "high", r"auth(?:orization)? key|session (?:revoked|expired)|sessionpassword|unauthorized|authkeyunregistered|not logged in", "Telegram sign-in was lost", "The saved session was revoked or expired (logged out from another device, or the account asked for a password).", "Settings, Telegram: sign in again."),
    ("telegram-connect", "Telegram", "medium", r"connection (?:lost|reset|refused|timed out)|connecterror|timeout(?:error)? .*telegram|telegram.*timed out|network is unreachable", "Telegram could not be reached", "No internet, a VPN or firewall in the way, or Telegram is slow.", "Check the internet; Settings, Telegram, connection."),
    ("disk-full", "System", "high", r"no space left|disk full|errno 28|not enough space", "The disk is full", "The data or cache folder has no free space.", "Settings, Storage & Backup: clear the cache; free space on the drive."),
    ("db-locked", "Database", "high", r"database is locked|sqlite3?\.operationalerror.*busy|database table is locked", "The database was busy", "Two things wrote at once, or a backup or an antivirus scan holds the file.", "Close other copies of Archive X; exclude the data folder from antivirus scans."),
    ("permission", "System", "medium", r"permission denied|winerror 5\b|access is denied|errno 13", "A file could not be opened (permission)", "Another program holds the file, or the folder is protected.", "Check the file named in the evidence; close the program using it."),
    ("ffmpeg", "Media tools", "high", r"ffmpeg(?:\.exe)? (?:not found|is missing)|ffprobe (?:not found|is missing)|no such file.*ffmpeg|ffmpeg.*(?:not found|failed|exited with)", "ffmpeg did not run", "ffmpeg is missing from the install, was blocked, or the file is damaged.", "Settings, Overview, Dependencies: is ffmpeg found?"),
    ("captions-gpu", "Live captions", "medium", r"cublas|cudnn|cudart|nvrtc|cuda.*(?:not|fail|error)", "The graphics card could not be used for captions", "The NVIDIA libraries are missing or too old. Captions then run on the processor (slower).", "Nothing to do: it falls back by itself. Settings, Experience shows the speed."),
    ("captions-failed", "Live captions", "medium", r"live captions failed|caption engine failed|speech model", "Live captions failed", "The speech model did not download or load, or ffmpeg could not read the audio.", "Settings, Experience, Live caption model; the line under the player says why."),
    ("provider-key", "Metadata", "medium", r"invalid api key|api key.*(?:refused|invalid|not accepted)|http (?:401|403)|does not recognise this key|unauthorized.*(?:tmdb|omdb|opensubtitles|stashdb|theporndb)", "A metadata provider refused the key", "The key is wrong, not activated (OMDb emails a link) or was revoked.", "Settings, Metadata: Test the key."),
    ("provider-rate", "Metadata", "low", r"http 429|rate limit|too many requests", "A metadata provider is limiting requests", "More requests than the free plan allows.", "It retries later; scan fewer videos at once."),
    ("provider-down", "Metadata", "low", r"(?:tmdb|omdb|tvmaze|stashdb|theporndb|opensubtitles).*(?:timed out|unreachable|http 5\d\d|connection)", "A metadata provider did not answer", "The service is slow or down, or there is no internet.", "Try later; Settings, Metadata, Test."),
    ("local-scan", "Local folders", "medium", r"local folder (?:scan|re-check) failed|folder identification failed|movie and show identification failed", "A local folder scan or naming run failed", "The folder was unplugged or renamed, or a file could not be read.", "Settings, Local files: is the folder there? The lane in Transfers shows the last step."),
    ("update", "App update", "low", r"updates: (?:check failed|install failed|unavailable)", "The app update did not work", "No internet, or the release is not reachable.", "Settings, Overview, App update."),
    ("native-player", "Video player", "medium", r"native player|axplayer|lav(?:video|audio)|decoder (?:failed|error)", "The native player had a problem", "A codec or hardware decoder failed on this PC.", "Settings, Experience, Native player; try the other setting."),
    ("port", "System", "high", r"address already in use|only one usage of each socket|port \d+ .*in use", "A network port is already in use", "Another copy of Archive X (or another program) holds it.", "Close the other copy and start again."),
    ("memory", "System", "high", r"memoryerror|out of memory|cannot allocate memory", "The app ran out of memory", "A very large file or too many tasks at once.", "Close other programs; lower the parallel work in Settings."),
]
_SEV = {"high": 0, "medium": 1, "low": 2}
_COMPILED = [(i, a, s, re.compile(p, re.I), t, c, w) for i, a, s, p, t, c, w in SIGNATURES]
_LEVELS_BAD = {"ERROR", "CRITICAL", "FATAL", "WARN", "WARNING"}


def _norm(text: str) -> str:
    """The line with what changes from one occurrence to the next (numbers, ids, paths, quoted names) removed, so that the same problem groups together."""
    t = re.sub(r"[A-Za-z]:\\[^\s'\"]+|/(?:[\w.\-]+/)+[\w.\-]+", "<path>", text)
    t = re.sub(r"0x[0-9a-f]+|[0-9a-f]{8,}", "<id>", t, flags=re.I)
    t = re.sub(r"\d+", "#", t)
    return re.sub(r"\s+", " ", t).strip()[:140]


def _ts(value: Any) -> float:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp() if value else 0.0
    except (TypeError, ValueError):
        return 0.0


def _evidence(line: str) -> str:
    return redact_text(line).replace("\n", " ")[:170]


def build(db, *, limit: int = 8, now: float | None = None, events: list[dict] | None = None) -> dict[str, Any]:
    """The digest. `events` can be given (tests); otherwise the newest 2,000 log events and the failed jobs are read."""
    now = now or time.time()
    rows = events if events is not None else db.recent_events(2000)
    groups: dict[str, dict[str, Any]] = {}

    def add(key: str, base: dict[str, Any], line: str, when: float) -> None:
        g = groups.get(key)
        if g is None:
            g = groups[key] = {**base, "count": 0, "first": when or now, "last": when or now, "evidence": []}
        g["count"] += 1
        if when:
            g["first"] = min(g["first"], when)
            g["last"] = max(g["last"], when)
        ev = _evidence(line)
        if ev and ev not in g["evidence"] and len(g["evidence"]) < 2:
            g["evidence"].append(ev)

    errors = warnings = 0
    for e in rows:
        level = str(e.get("level") or "").upper()
        msg = str(e.get("message") or "")
        if level not in _LEVELS_BAD and not any(sig[3].search(msg) for sig in _COMPILED if sig[2] == "high"):
            continue
        errors += level in {"ERROR", "CRITICAL", "FATAL"}
        warnings += level in {"WARN", "WARNING"}
        when = _ts(e.get("created_at") or e.get("ts"))
        hit = next((s for s in _COMPILED if s[3].search(msg)), None)
        if hit:
            add(hit[0], {"id": hit[0], "area": hit[1], "severity": hit[2], "title": hit[4], "cause": hit[5], "check": hit[6]}, msg, when)
        else:
            key = "log:" + _norm(msg)
            add(key, {"id": "log-" + re.sub(r"\W+", "-", _norm(msg).lower())[:36].strip("-"), "area": "Log", "severity": "medium" if level in {"ERROR", "CRITICAL", "FATAL"} else "low", "title": _norm(msg)[:90], "cause": "", "check": "Read the two lines in the evidence; they are the real log lines."}, msg, when)

    try:   # jobs that failed: the reason the job itself recorded
        if events is None:
            with db.reading() as conn:
                failed = conn.execute("SELECT job_type, error, updated_at FROM jobs WHERE status='FAILED' ORDER BY updated_at DESC LIMIT 40").fetchall()
            for j in failed:
                text = str(j["error"] or "no reason recorded")
                hit = next((s for s in _COMPILED if s[3].search(text)), None)
                base = {"id": hit[0], "area": hit[1], "severity": hit[2], "title": hit[4], "cause": hit[5], "check": hit[6]} if hit else {"id": "job-" + str(j["job_type"]).lower(), "area": "Jobs", "severity": "medium", "title": f"{j['job_type']} jobs failed", "cause": "", "check": "Operations: open the failed job; its error is in the evidence."}
                add(base["id"] if hit else "jobfail:" + str(j["job_type"]), base, f"{j['job_type']}: {text}", _ts(j["updated_at"]))
    except Exception:
        swallowed.note("issue_digest.failed_jobs")

    for s in swallowed.snapshot(20):   # errors the code deliberately ignores, with the place in the code
        if s["count"] < 3:
            continue
        key = f"ignored:{s['where']}:{s['error']}"
        add(key, {"id": "ignored-" + re.sub(r"\W+", "-", s["where"].lower())[:36], "area": "Ignored errors", "severity": "low" if s["count"] < 25 else "medium", "title": f"{s['error']} in {s['where']}", "cause": "The code catches this error and carries on, so nothing shows on screen; it keeps happening.", "check": f"Look at the code named '{s['where']}'."}, f"{s['error']}: {s['message']}", s["last"])
        groups[key]["count"] = max(groups[key]["count"], int(s["count"]))

    issues = sorted(groups.values(), key=lambda g: (_SEV[g["severity"]], -g["count"], -g["last"]))[:limit]
    for g in issues:
        g["first_seen"] = _iso(g.pop("first"))
        g["last_seen"] = _iso(g.pop("last"))
        g["ago"] = _ago(now - (_ts(g["last_seen"]) or now))
    return {"generated_at": _iso(now), "looked_at": {"log_events": len(rows), "errors": errors, "warnings": warnings}, "ok": not issues, "issues": issues}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ago(sec: float) -> str:
    sec = max(0, int(sec))
    return "just now" if sec < 60 else f"{sec // 60} min ago" if sec < 3600 else f"{sec // 3600} h ago" if sec < 86400 else f"{sec // 86400} d ago"


def as_text(digest: dict[str, Any]) -> str:
    """The digest as plain text of a few hundred words: what a small model can read whole, and a person can paste anywhere. Secrets are already hidden."""
    seen = digest["looked_at"]
    head = f"Archive X issue digest, {digest['generated_at']}. Looked at {seen['log_events']} log events ({seen['errors']} errors, {seen['warnings']} warnings)."
    if not digest["issues"]:
        return head + "\nNo problems found in the recent log, failed jobs or ignored errors."
    out = [head, f"{len(digest['issues'])} issue(s), worst first:"]
    for n, g in enumerate(digest["issues"], 1):
        out.append(f"{n}. [{g['severity'].upper()}] {g['area']}: {g['title']} (x{g['count']}, last {g['ago']}) id={g['id']}")
        if g["cause"]:
            out.append(f"   usual cause: {g['cause']}")
        out.append(f"   check: {g['check']}")
        for ev in g["evidence"]:
            out.append(f"   log: {ev}")
    return "\n".join(out)
