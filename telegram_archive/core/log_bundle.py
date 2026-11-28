"""A support bundle: every log and diagnostic the app has, redacted, in one zip whose name carries the date and time.

    archive-x-logs-2026-11-14_08-12-45.zip
        README.txt                 what is inside and what was left out
        manifest.json              every file, its size, whether it was cut, and everything skipped
        logs/...                   the application's log files (the last part of each, secrets blanked)
        activity/events.txt        the "Recent activity" list
        diagnostics/...            the diagnostic report, playback snapshot, errors the app ignored
        system/...                 versions, operating system, tools, free disk space, settings of the run (no secrets)

Nothing leaves the PC by itself: the bundle is made on demand and saved or downloaded for the person to send.

Safety: text is passed through the app's redaction (keys, passwords, tokens, phone numbers, cookies) and through patterns for the signed
stream addresses (`?t=...`). Files that can hold credentials (sessions, certificates and keys, databases, cookies) are never included.
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, IO, Mapping

MAX_FILE_BYTES = 8 * 1024 * 1024        # the last 8 MB of any one log
MAX_TOTAL_BYTES = 80 * 1024 * 1024      # everything in the zip, before compression
MAX_LOG_FILES = 200
LOG_NAME_PARTS = (".log", ".txt", ".jsonl", ".out", ".err")

# Never included, whatever the folder holds.
_NEVER = re.compile(r"(session|secret|credential|password|cookie|private|\.pem$|\.key$|\.pfx$|\.p12$|\.db(-wal|-shm)?$|\.sqlite3?$|\.lock$)", re.I)

_EXTRA_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # the signed stream and device links: .../video/5?c=-100&t=<hex>
    (re.compile(r"(?i)([?&](?:t|token|key|sig|signature|secret|hash|auth)=)[^&\s\"'<>]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(set-cookie\s*:\s*)[^\n]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(x-archive-x-(?:proxy-key|launch-secret)\s*[:=]\s*)[^\s,]+"), r"\1<REDACTED>"),
    (re.compile(r"(?i)(ARCHIVE_X_(?:PROXY_KEY|LAUNCH_SECRET)\s*=\s*)\S+"), r"\1<REDACTED>"),
    # JSON: "api_hash": "..."
    (re.compile(r'(?i)("(?:api_hash|api_id_hash|session_string|string_session|password|secret|token|auth_key)"\s*:\s*")[^"]*(")'), r"\1<REDACTED>\2"),
]


def bundle_name(now: datetime | None = None) -> str:
    """archive-x-logs-2026-11-14_08-12-45.zip (the PC's local time)."""
    return f"archive-x-logs-{(now or datetime.now()):%Y-%m-%d_%H-%M-%S}.zip"


def redact(text: str) -> str:
    try:
        from ..ai.ai_evidence import redact_text
        text = redact_text(text)
    except Exception:   # the bundle must still be made; the patterns below still apply
        pass
    for pattern, repl in _EXTRA_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def list_logs(log_dir: Path, limit: int = MAX_LOG_FILES) -> tuple[list[Path], list[dict[str, str]]]:
    """Log files under log_dir, newest first, and the files left out (with the reason)."""
    found: list[Path] = []
    skipped: list[dict[str, str]] = []
    root = Path(log_dir)
    if not root.is_dir():
        return found, skipped
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if depth >= 3:
            dirnames[:] = []
        for name in filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if _NEVER.search(name) or _NEVER.search(rel):
                skipped.append({"name": rel, "reason": "may hold credentials"})
            elif not any(part in name.lower() for part in LOG_NAME_PARTS):
                skipped.append({"name": rel, "reason": "not a text log"})
            else:
                found.append(path)
    def modified(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    found.sort(key=modified, reverse=True)
    for extra in found[limit:]:
        skipped.append({"name": extra.relative_to(root).as_posix(), "reason": f"more than {limit} log files (older ones left out)"})
    return found[:limit], skipped


def read_tail(path: Path, max_bytes: int) -> tuple[str, int, bool]:
    """The last max_bytes of a text file as text (starting at a line start when cut): (text, original size, was cut)."""
    size = path.stat().st_size
    with path.open("rb") as f:
        cut = size > max_bytes
        if cut:
            f.seek(size - max_bytes)
        data = f.read()
    if cut and b"\n" in data:
        data = data.split(b"\n", 1)[1]
    return data.decode("utf-8", errors="replace"), size, cut


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


README = """Archive X logs and diagnostics
==============================

Made on {created}  (Archive X {version})

Send this file when you report a problem. It stays on your PC until you send it; Archive X does not upload it.

What is inside
  logs/          the application's log files (up to the last {file_mb} MB of each)
  activity/      the "Recent activity" list
  diagnostics/   the diagnostic report, playback snapshot and the errors the app ignored
  system/        versions, operating system, tools and free disk space
  manifest.json  every file with its size, whether it was cut, and everything left out

What was blanked or left out
  Passwords, API keys, tokens, cookies, phone numbers and the signed parts of stream addresses are replaced by <REDACTED>.
  Telegram sessions, certificates, keys, databases and cookie files are never included.
  File and folder names, channel names and video titles are NOT removed: they are often what shows the problem.
"""


def build_bundle(dest: IO[bytes] | Path, *, log_dir: Path, extras: Mapping[str, Any] | None = None, now: datetime | None = None,
                 app_version: str = "", max_file_bytes: int = MAX_FILE_BYTES, max_total_bytes: int = MAX_TOTAL_BYTES) -> dict[str, Any]:
    """Write the zip to `dest`; returns the manifest that is also inside it. `extras` maps a path inside the zip to its text (or JSON-able value)."""
    now = now or datetime.now().astimezone()
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total = 0

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        def add(name: str, text: str, original: int | None = None, cut: bool = False) -> bool:
            nonlocal total
            data = redact(text).encode("utf-8", errors="replace")
            if total + len(data) > max_total_bytes:
                skipped.append({"name": name, "reason": "bundle size limit reached"})
                return False
            z.writestr(zipfile.ZipInfo(name, date_time=now.timetuple()[:6]), data, zipfile.ZIP_DEFLATED, 6)
            total += len(data)
            files.append({"name": name, "bytes": len(data), "original_bytes": original if original is not None else len(data), "cut": cut})
            return True

        for name, value in (extras or {}).items():
            try:
                add(name, _text(value))
            except Exception as exc:   # one broken part must not lose the others
                skipped.append({"name": name, "reason": f"{type(exc).__name__}: {exc}"})

        logs, left_out = list_logs(log_dir)
        skipped.extend(left_out)
        for path in logs:
            rel = "logs/" + path.relative_to(log_dir).as_posix()
            try:
                text, size, cut = read_tail(path, max_file_bytes)
            except OSError as exc:
                skipped.append({"name": rel, "reason": f"unreadable: {exc.__class__.__name__}"})
                continue
            if cut:
                text = f"[... earlier {size - max_file_bytes:,} bytes of this log are left out ...]\n" + text
            add(rel, text, original=size, cut=cut)

        manifest = {
            "created": now.isoformat(timespec="seconds"),
            "app_version": app_version,
            "redacted": True,
            "files": files,
            "skipped": skipped,
            "total_bytes": total,
        }
        readme = README.format(created=now.strftime("%Y-%m-%d %H:%M:%S"), version=app_version or "?", file_mb=max_file_bytes // (1024 * 1024))
        z.writestr("README.txt", readme)
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return manifest


# ---------------------------------------------------------------------------------------------------------------------
# What goes into `extras`. Each part is collected on its own: a part that fails becomes a line in diagnostics/collection-errors.txt.

_SAFE_ENV = re.compile(r"^(ARCHIVE_X_|TELEGRAM_ARCHIVE_|PROCESSOR_|NUMBER_OF_PROCESSORS|OS$|COMPUTERNAME$)", re.I)
_SECRET_ENV = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|HASH|COOKIE|SESSION)", re.I)


def system_text(config: Any, ffmpeg: str | None) -> str:
    lines = [
        f"app version : {getattr(config, 'APP_VERSION', '?')}" + (f" ({config.APP_COMMIT})" if getattr(config, 'APP_COMMIT', '') else ""),
        f"time        : {datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"os          : {platform.platform()}  ({platform.machine()})",
        f"python      : {sys.version.split()[0]}  {sys.executable}",
        f"cpu cores   : {os.cpu_count()}",
        f"data folder : {getattr(config, 'USER_DATA_DIR', '?')}",
        f"log folder  : {getattr(config, 'LOG_DIR', '?')}",
    ]
    try:
        total, used, free = shutil.disk_usage(str(getattr(config, "USER_DATA_DIR", ".")))
        lines.append(f"disk (data) : {free / 1e9:.1f} GB free of {total / 1e9:.1f} GB")
    except OSError:
        pass
    try:
        import psutil
        vm = psutil.virtual_memory()
        lines.append(f"memory      : {vm.available / 1e9:.1f} GB free of {vm.total / 1e9:.1f} GB")
    except Exception:
        pass
    lines.append(f"ffmpeg      : {ffmpeg or 'not found'}")
    if ffmpeg:
        try:
            out = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            lines.append(f"ffmpeg ver  : {out.splitlines()[0] if out else '?'}")
        except Exception as exc:
            lines.append(f"ffmpeg ver  : could not run ({exc.__class__.__name__})")
    return "\n".join(lines) + "\n"


def environment_text(environ: Mapping[str, str] | None = None) -> str:
    """The settings of this run (ARCHIVE_X_*, TELEGRAM_ARCHIVE_*, a few system ones); values that look like secrets are blanked."""
    environ = os.environ if environ is None else environ
    lines = []
    for key in sorted(environ):
        if _SAFE_ENV.search(key):
            lines.append(f"{key}={'<REDACTED>' if _SECRET_ENV.search(key) else environ[key]}")
    return "\n".join(lines) + "\n"


def collect_extras(*, config: Any, db: Any, report: Callable[[], str] | None = None, ffmpeg: str | None = None) -> dict[str, Any]:
    """The diagnostic parts of the bundle. `report` returns the canonical diagnostic report text (it may be slow; the caller bounds it)."""
    extras: dict[str, Any] = {}
    errors: list[str] = []

    def part(name: str, fn: Callable[[], Any]) -> None:
        try:
            extras[name] = fn()
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")

    part("system/system.txt", lambda: system_text(config, ffmpeg))
    part("system/environment.txt", lambda: environment_text())

    def ignored() -> Any:
        from . import swallowed
        return swallowed.snapshot(200)
    part("diagnostics/ignored-errors.json", ignored)

    def playback() -> Any:
        from .diagnostics import browser_diagnostics_snapshot, recent_structured_events
        return {
            "captured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "browser": browser_diagnostics_snapshot(),
            "recent_events": recent_structured_events(300),
        }
    part("diagnostics/playback-and-events.json", playback)

    def route_usage() -> Any:
        from .route_usage import RouteUsage
        return RouteUsage(config.LOG_DIR / "route-usage.json").snapshot()
    part("diagnostics/route-usage.json", route_usage)

    if report is not None:
        part("diagnostics/report.txt", report)

    def events() -> str:
        with db.reading() as rdb:
            rows = rdb.execute("SELECT level, message, created_at FROM events ORDER BY id DESC LIMIT 5000").fetchall()
        return "\n".join(f"[{r['created_at']}] [{r['level']}] {r['message']}" for r in reversed(rows)) + "\n"
    part("activity/events.txt", events)

    if errors:
        extras["diagnostics/collection-errors.txt"] = "\n".join(errors) + "\n"
    return extras


def downloads_dir(config: Any) -> Path:
    """Where a bundle saved for the person goes: their Downloads folder, else the app's exports folder."""
    downloads = Path.home() / "Downloads"
    if downloads.is_dir():
        return downloads
    folder = Path(getattr(config, "USER_DATA_DIR", ".")) / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def unique_path(folder: Path, name: str) -> Path:
    path = folder / name
    n = 2
    while path.exists():
        path = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return path


def build_to_file(folder: Path, *, log_dir: Path, extras: Mapping[str, Any], app_version: str = "", now: datetime | None = None) -> tuple[Path, dict[str, Any]]:
    """Writes the bundle into `folder` under a name with the date and time; returns its path and manifest."""
    now = now or datetime.now().astimezone()
    path = unique_path(folder, bundle_name(now))
    tmp = path.with_suffix(".zip.part")
    try:
        with tmp.open("wb") as f:
            manifest = build_bundle(f, log_dir=log_dir, extras=extras, now=now, app_version=app_version)
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
    return path, manifest

