"""Out-of-process enforcement for Archive X watchdog liveness.

This module intentionally uses only the standard library so it remains able to
run when the application event loop, task supervisor, or application imports are
unresponsive. The parent process publishes a small, secret-free JSON heartbeat;
this process may terminate/restart the app only after a sustained liveness breach.
"""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#   - Manifest assessment
#   - Process identity and termination
#   - Restart budget and incidents
#   - Guardian entry point
# ------------------------------------------------------------------
from __future__ import annotations

from .swallowed import note as _swallowed_note
import argparse
import base64
import json
import math
import os
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any



# ==============================================================================
# SECTION: Manifest assessment
# Reads and evaluates the watchdog manifest.
# ==============================================================================

def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically publish guardian state despite brief Windows file locks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    temp_ready = False
    delays = (0.04, 0.08, 0.16, 0.32, 0.64)
    try:
        for attempt in range(len(delays) + 1):
            try:
                if not temp_ready:
                    temp.write_text(serialized, encoding="utf-8")
                    temp_ready = True
                os.replace(temp, path)
                return
            except PermissionError:
                if attempt >= len(delays):
                    raise
                time.sleep(delays[attempt])
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def assess_manifest(manifest: dict[str, Any], *, now: float | None = None,
                    startup_grace_seconds: float = 20.0,
                    loop_stale_seconds: float = 6.0,
                    watchdog_stale_seconds: float = 12.0,
                    publisher_stale_seconds: float = 8.0) -> str | None:
    """Pure health decision used by guardian and regression tests.

    Monotonic timestamps are preferred so wall-clock changes cannot make a
    blocked process look healthy. Unix timestamps remain a compatibility
    fallback for diagnostics or older test manifests.
    """
    uses_monotonic = "started_at_monotonic" in manifest
    now = (time.monotonic() if uses_monotonic else time.time()) if now is None else float(now)
    if not manifest:
        return "watchdog_manifest_missing"
    started_key = "started_at_monotonic" if uses_monotonic else "started_at_unix"
    if started_key not in manifest:
        return "watchdog_manifest_missing"

    def finite_value(key: str, fallback_key: str | None = None) -> float:
        raw = manifest.get(key)
        if raw is None and fallback_key:
            raw = manifest.get(fallback_key)
        value = float(raw or 0.0)
        if not math.isfinite(value):
            raise ValueError("non-finite timestamp")
        return value

    if not math.isfinite(now):
        return "watchdog_manifest_invalid"
    try:
        started = finite_value(started_key)
    except (TypeError, ValueError, OverflowError):
        return "watchdog_manifest_invalid"
    if started > now + 2.0:
        return "watchdog_manifest_timestamp_invalid"
    if now - started < max(0.0, startup_grace_seconds):
        return None
    loop_key = "loop_heartbeat_monotonic" if uses_monotonic else "loop_heartbeat_unix"
    watchdog_key = "watchdog_success_monotonic" if uses_monotonic else "watchdog_success_unix"
    watchdog_fallback = "watchdog_heartbeat_monotonic" if uses_monotonic else "watchdog_heartbeat_unix"
    publisher_key = "publisher_heartbeat_monotonic" if uses_monotonic else "publisher_heartbeat_unix"
    try:
        loop_beat = finite_value(loop_key)
        watchdog_beat = finite_value(watchdog_key, watchdog_fallback)
        publisher_beat = finite_value(publisher_key)
    except (TypeError, ValueError, OverflowError):
        return "watchdog_manifest_invalid"
    # A timestamp far in the future usually indicates a corrupt manifest or a
    # clock-domain mismatch. Do not accept it as fresh health evidence.
    if any(stamp > now + 2.0 for stamp in (loop_beat, watchdog_beat, publisher_beat)):
        return "watchdog_manifest_timestamp_invalid"
    # Attempted-loop heartbeats alone are not healthy evidence: a task that
    # repeatedly catches exceptions can tick forever without completing a
    # successful monitoring cycle.
    if loop_beat <= 0 or now - loop_beat >= max(1.0, loop_stale_seconds):
        return "event_loop_unresponsive"
    if watchdog_beat <= 0 or now - watchdog_beat >= max(loop_stale_seconds, watchdog_stale_seconds):
        return "watchdog_task_unresponsive"
    if publisher_beat <= 0 or now - publisher_beat >= max(loop_stale_seconds, publisher_stale_seconds):
        return "watchdog_publisher_unresponsive"
    return None




# ==============================================================================
# SECTION: Process identity and termination
# Identifies and terminates the parent and owned workers.
# ==============================================================================

def _process_identity_token(pid: int) -> str | None:
    """Return an OS process-start token to defend against PID reuse.

    Linux exposes start ticks in /proc; Windows exposes creation FILETIME. An
    optional psutil fallback covers other platforms without becoming required.
    """
    if pid <= 0:
        return None
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            class _FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME),
                                                 ctypes.POINTER(_FILETIME), ctypes.POINTER(_FILETIME)]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return None
            try:
                creation, exit_time, kernel_time, user_time = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
                ok = kernel32.GetProcessTimes(handle, ctypes.byref(creation), ctypes.byref(exit_time),
                                             ctypes.byref(kernel_time), ctypes.byref(user_time))
                if not ok:
                    return None
                stamp = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
                return f"win:{stamp}"
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            _swallowed_note("watchdog_guardian._process_identity_token")
            pass
    proc_stat = Path(f"/proc/{int(pid)}/stat")
    if proc_stat.exists():
        try:
            raw = proc_stat.read_text(encoding="utf-8")
            fields = raw[raw.rfind(")") + 1:].strip().split()
            if len(fields) > 19:
                return f"proc:{fields[19]}"
        except (OSError, ValueError, IndexError):
            pass
    try:
        import psutil  # optional, used only where the OS lacks a native fallback above
        return f"psutil:{psutil.Process(int(pid)).create_time():.6f}"
    except Exception:
        return None


def _windows_pid_alive(pid: int) -> bool | None:
    """Return liveness on Windows via OpenProcess/GetExitCodeProcess.

    ``os.kill(pid, 0)`` must never be used on Windows: signal 0 is
    CTRL_C_EVENT there, so it either raises (the guardian then wrongly
    concluded the app had exited and quit every ~2 s, v29.4.220 restart loop)
    or delivers Ctrl+C to the process group. Returns None if the check is
    unavailable so the caller can fall back conservatively.
    """
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # ERROR_ACCESS_DENIED means the process exists but is protected.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return None
            return int(code.value) == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        alive = _windows_pid_alive(int(pid))
        if alive is not None:
            return alive
        try:
            import psutil  # optional
            return bool(psutil.pid_exists(int(pid)))
        except Exception:
            # Unknown: keep monitoring rather than exiting (an exit caused the
            # old restart loop). Parent shutdown still writes a "stop" control.
            return True
    # On POSIX an exited process can remain briefly as a zombie. It is no longer
    # executing work and must not block the guardian from starting a replacement.
    if os.name != "nt":
        stat_path = Path(f"/proc/{int(pid)}/stat")
        if stat_path.exists():
            try:
                tail = stat_path.read_text(encoding="utf-8")
                fields = tail[tail.rfind(")") + 1:].strip().split()
                if fields and fields[0] == "Z":
                    return False
            except (OSError, ValueError):
                pass
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _same_process(pid: int, expected_token: str | None) -> bool:
    if not _pid_alive(pid):
        return False
    if expected_token is None:
        return True
    return _process_identity_token(pid) == expected_token


def _terminate_parent(pid: int, *, wait_seconds: float = 4.0,
                      expected_start_token: str | None = None) -> bool:
    """Stop only the stalled parent instance, escalating on POSIX if needed."""
    if not _same_process(pid, expected_start_token):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return not _same_process(pid, expected_start_token)
    deadline = time.monotonic() + max(0.5, wait_seconds)
    while time.monotonic() < deadline:
        if not _same_process(pid, expected_start_token):
            return True
        time.sleep(0.1)
    if os.name != "nt" and hasattr(signal, "SIGKILL"):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not _same_process(pid, expected_start_token):
                return True
            time.sleep(0.1)
    # Windows SIGTERM is a hard process termination request; if it remains alive,
    # avoid launching a second server on top of it.
    return not _same_process(pid, expected_start_token)


def _terminate_owned_workers(manifest: dict[str, Any]) -> list[int]:
    """Terminate only isolated worker PIDs explicitly owned by this app."""
    try:
        if manifest.get("publisher_heartbeat_monotonic") is not None:
            publisher_beat = float(manifest.get("publisher_heartbeat_monotonic"))
            now_clock = time.monotonic()
        else:
            publisher_beat = float(manifest.get("publisher_heartbeat_unix") or 0.0)
            now_clock = time.time()
        if not math.isfinite(publisher_beat):
            return []
        manifest_age = now_clock - publisher_beat
    except (TypeError, ValueError, OverflowError):
        return []
    # Do not trust corrupt/future timestamps or stale process IDs: PID reuse is
    # possible after a process exits, and a bad manifest must not name victims.
    if manifest_age < -2.0 or manifest_age > 3.0:
        return []
    terminated: list[int] = []
    owned = set()
    jobs = manifest.get("active_jobs", [])
    if not isinstance(jobs, list):
        return terminated
    for job in jobs:
        if not isinstance(job, dict):
            continue
        try:
            pid = int(job.get("isolated_process_pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and pid != os.getpid():
            owned.add(pid)
    for pid in sorted(owned):
        if not _pid_alive(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            terminated.append(pid)
        except OSError:
            continue
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline and _pid_alive(pid):
            time.sleep(0.05)
        if _pid_alive(pid) and os.name != "nt" and hasattr(signal, "SIGKILL"):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return terminated




# ==============================================================================
# SECTION: Restart budget and incidents
# Limits restarts and records incidents.
# ==============================================================================

def _restart_budget(history_path: Path, *, now: float, window_seconds: float, limit: int) -> tuple[bool, list[float]]:
    raw = _read_json(history_path)
    attempts = raw.get("restart_attempts", [])
    clean = []
    if isinstance(attempts, list):
        for item in attempts:
            try:
                stamp = float(item)
                if 0 <= now - stamp <= window_seconds:
                    clean.append(stamp)
            except (TypeError, ValueError):
                continue
    return len(clean) < max(0, int(limit)), clean


def _incident_age(now_wall: float, value: Any) -> float | None:
    """Safely format a timestamp age without allowing bad JSON to kill guardian."""
    try:
        stamp = float(value)
        if not math.isfinite(stamp):
            return None
        return round(max(0.0, now_wall - stamp), 3)
    except (TypeError, ValueError, OverflowError):
        return None


def _append_incident(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass




# ==============================================================================
# SECTION: Guardian entry point
# Command decoding and the guardian main loop.
# ==============================================================================

def _decode_command(value: str) -> list[str]:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        parsed = json.loads(decoded)
        if isinstance(parsed, list) and parsed and all(isinstance(x, str) for x in parsed):
            return parsed
    except Exception:
        _swallowed_note("watchdog_guardian._decode_command")
        pass
    return []


def run_guardian(*, manifest_path: Path, control_path: Path, parent_pid: int,
                 heartbeat_path: Path, incident_path: Path, restart_history_path: Path, app_root: Path,
                 restart_command: list[str], poll_seconds: float = 0.5,
                 startup_grace_seconds: float = 20.0, loop_stale_seconds: float = 6.0,
                 watchdog_stale_seconds: float = 12.0, publisher_stale_seconds: float = 8.0,
                 restart_limit: int = 2, restart_window_seconds: float = 600.0) -> int:
    """Monitor one Archive X process and restart it only after a liveness breach."""
    started_monotonic = time.monotonic()
    parent_start_token = _process_identity_token(int(parent_pid))
    interval = max(0.2, float(poll_seconds))
    manifest_missing_since: float | None = None
    while _same_process(int(parent_pid), parent_start_token):
        try:
            _atomic_json(heartbeat_path, {
                "pid": os.getpid(), "parent_pid": int(parent_pid),
                "heartbeat_unix": time.time(), "state": "MONITORING",
            })
        except Exception:
            _swallowed_note("watchdog_guardian.run_guardian")
            pass
        control = _read_json(control_path)
        if control.get("action") == "stop":
            return 0
        manifest = _read_json(manifest_path)
        reason = assess_manifest(
            manifest, now=time.monotonic() if "started_at_monotonic" in manifest else time.time(),
            startup_grace_seconds=startup_grace_seconds,
            loop_stale_seconds=loop_stale_seconds, watchdog_stale_seconds=watchdog_stale_seconds,
            publisher_stale_seconds=publisher_stale_seconds,
        )
        now_mono = time.monotonic()
        # Missing/invalid-to-parse manifests are a publisher failure, not direct
        # evidence that the application event loop is hung. Give manifest
        # publication the same longer deadline as the publisher heartbeat; the
        # shorter startup grace alone caused avoidable restarts when Windows
        # briefly denied an atomic file replacement.
        if reason == "watchdog_manifest_missing":
            if manifest_missing_since is None:
                manifest_missing_since = now_mono
            missing_manifest_grace = max(0.0, startup_grace_seconds, publisher_stale_seconds)
            if now_mono - manifest_missing_since < missing_manifest_grace:
                reason = None
        else:
            manifest_missing_since = None
        # Other health failures still receive the startup grace period.
        if reason and now_mono - started_monotonic < max(0.0, startup_grace_seconds):
            reason = None
        # If even the publisher has ceased running, the last manifest's publisher
        # clock ages naturally and eventually enters the same enforcement path.
        if reason:
            now = time.time()
            budget_ok, attempts = _restart_budget(
                restart_history_path, now=now, window_seconds=restart_window_seconds,
                limit=restart_limit,
            )
            incident = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "watchdog.guardian_enforcement",
                "reason": reason,
                "parent_pid": int(parent_pid),
                "manifest_age_seconds": _incident_age(now, manifest.get("publisher_heartbeat_unix")),
                "loop_heartbeat_age_seconds": _incident_age(now, manifest.get("loop_heartbeat_unix")),
                "watchdog_heartbeat_age_seconds": _incident_age(now, manifest.get("watchdog_heartbeat_unix")),
                "restart_allowed": budget_ok,
                "restart_limit": int(restart_limit),
                "restart_window_seconds": float(restart_window_seconds),
                "active_jobs": [
                    {"id": str(x.get("id") or ""), "status": str(x.get("status") or ""),
                     "watchdog_state": str(x.get("watchdog_state") or "UNKNOWN")}
                    for x in (manifest.get("active_jobs") if isinstance(manifest.get("active_jobs"), list) else [])
                    if isinstance(x, dict)
                ],
            }
            _append_incident(incident_path, incident)
            terminated_workers = _terminate_owned_workers(manifest)
            if not _terminate_parent(int(parent_pid), expected_start_token=parent_start_token):
                _append_incident(incident_path, {**incident, "event": "watchdog.guardian_termination_failed",
                                                 "terminated_worker_pids": terminated_workers})
                return 2
            if not budget_ok:
                _append_incident(incident_path, {**incident, "event": "watchdog.guardian_restart_suppressed",
                                                 "terminated_worker_pids": terminated_workers})
                return 3
            attempts.append(now)
            _atomic_json(restart_history_path, {"restart_attempts": attempts[-max(1, restart_limit):], "updated_at_unix": now})
            if not restart_command:
                _append_incident(incident_path, {**incident, "event": "watchdog.guardian_restart_command_missing"})
                return 4
            try:
                # V29.4.223: a guardian restart must not open another browser tab;
                # the existing tab reconnects on its own.
                child_env = dict(os.environ)
                child_env["ARCHIVE_X_AUTO_OPEN_BROWSER"] = "0"
                kwargs: dict[str, Any] = {"cwd": str(app_root), "stdin": subprocess.DEVNULL,
                                          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                                          "close_fds": True, "env": child_env}
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                else:
                    kwargs["start_new_session"] = True
                child = subprocess.Popen(restart_command, **kwargs)
                _append_incident(incident_path, {**incident, "event": "watchdog.guardian_restart_started",
                                                 "replacement_pid": child.pid,
                                                 "terminated_worker_pids": terminated_workers})
                return 0
            except Exception as exc:
                _append_incident(incident_path, {**incident, "event": "watchdog.guardian_restart_failed",
                                                 "error_type": type(exc).__name__, "error": str(exc)[:300]})
                return 5
        time.sleep(interval)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Archive X independent watchdog enforcer")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--control", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--guardian-heartbeat", required=True)
    parser.add_argument("--incident", required=True)
    parser.add_argument("--restart-history", required=True)
    parser.add_argument("--app-root", required=True)
    parser.add_argument("--restart-command", default="")
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--startup-grace-seconds", type=float, default=20.0)
    parser.add_argument("--loop-stale-seconds", type=float, default=6.0)
    parser.add_argument("--watchdog-stale-seconds", type=float, default=12.0)
    parser.add_argument("--publisher-stale-seconds", type=float, default=8.0)
    parser.add_argument("--restart-limit", type=int, default=2)
    parser.add_argument("--restart-window-seconds", type=float, default=600.0)
    args = parser.parse_args(argv)
    return run_guardian(
        manifest_path=Path(args.manifest), control_path=Path(args.control), parent_pid=args.parent_pid,
        heartbeat_path=Path(args.guardian_heartbeat),
        incident_path=Path(args.incident), restart_history_path=Path(args.restart_history),
        app_root=Path(args.app_root), restart_command=_decode_command(args.restart_command),
        poll_seconds=args.poll_seconds, startup_grace_seconds=args.startup_grace_seconds,
        loop_stale_seconds=args.loop_stale_seconds, watchdog_stale_seconds=args.watchdog_stale_seconds,
        publisher_stale_seconds=args.publisher_stale_seconds, restart_limit=args.restart_limit,
        restart_window_seconds=args.restart_window_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
