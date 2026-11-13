"""Launch VLC or MPC-HC pointed at the local stream server.
Ported from the existing Telegram player launch contract: find_vlc(),
find_mpc_hc(), choose_player(), and terminate_player_process_tree().

VLC gets a .m3u8 playlist (or single URL for one video).
MPC-HC gets /new /play <url>.
"""
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import os
import subprocess
import sys
import threading
import time
import re
from pathlib import Path
from typing import Optional

from ..core.prereqs import check

_procs: dict[int, subprocess.Popen] = {}
_proc_logs: dict[int, object] = {}
_sessions: dict[int, dict] = {}
_sessions_lock = threading.RLock()


def register_session(pid: int, video_keys) -> None:
    """Associate an Archive X-launched player PID with the playback videos it owns.

    Session metadata remains until ``take_session`` is called, even after the
    child process exits, so the API status poll can release queued/read-ahead
    work for the full playlist when the user closes VLC/MPC-HC.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    keys = []
    seen = set()
    for item in video_keys or []:
        try:
            c, m = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if (c, m) not in seen:
            keys.append((c, m))
            seen.add((c, m))
    with _sessions_lock:
        _sessions[pid] = {"videos": keys, "created_at": time.monotonic()}


def take_session(pid: int) -> dict | None:
    """Atomically consume an external-player session during teardown."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return None
    with _sessions_lock:
        return _sessions.pop(pid, None)


def find_vlc() -> Optional[str]:
    """Ported verbatim from v35 find_vlc()."""
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VideoLAN" / "VLC" / "vlc.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "VideoLAN" / "VLC" / "vlc.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "VideoLAN" / "VLC" / "vlc.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    if sys.platform == "win32":
        try:
            r = subprocess.run(["where", "vlc.exe"], capture_output=True, text=True, timeout=5,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                return r.stdout.strip().splitlines()[0]
        except Exception:
            _swallowed_note("vlc_launcher.find_vlc")
            pass
    else:
        for name in ("vlc", "cvlc"):
            try:
                r = subprocess.run(["which", name], capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    return r.stdout.strip()
            except Exception:
                _swallowed_note("vlc_launcher.find_vlc")
                pass
    return None


def find_mpc_hc() -> Optional[str]:
    """Ported verbatim from v35 find_mpc_hc()."""
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MPC-HC" / "mpc-hc64.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MPC-HC" / "mpc-hc.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "MPC-HC" / "mpc-hc64.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "MPC-HC" / "mpc-hc.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "K-Lite Codec Pack" / "MPC-HC64" / "mpc-hc64.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "K-Lite Codec Pack" / "MPC-HC64" / "mpc-hc64.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "K-Lite Codec Pack" / "MPC-HC" / "mpc-hc.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    if sys.platform == "win32":
        for exe in ("mpc-hc64.exe", "mpc-hc.exe"):
            try:
                r = subprocess.run(["where", exe], capture_output=True, text=True, timeout=5,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if r.returncode == 0:
                    return r.stdout.strip().splitlines()[0]
            except Exception:
                _swallowed_note("vlc_launcher.find_mpc_hc")
                pass
    return None



def launch(stream_url: str, preferred: str = "vlc", title: str = "") -> dict:
    """Launch VLC or MPC-HC pointed at stream_url. Returns {"player": name, "pid": int}."""
    vlc = find_vlc()
    mpc = find_mpc_hc()

    preferred = (preferred or "vlc").strip().lower()
    if preferred == "vlc":
        if not vlc:
            raise FileNotFoundError("VLC was not found on this PC")
        name, path = "vlc", vlc
    elif preferred in ("mpc", "mpc-hc"):
        if not mpc:
            raise FileNotFoundError("MPC-HC was not found on this PC")
        name, path = "mpc-hc", mpc
    else:
        raise ValueError("preferred player must be vlc or mpc-hc")

    if name == "vlc":
        args = [
            path,
            # Some VLC setups start "minimized to the system tray" or hand the
            # URL to an older VLC hidden in the tray. Always open a normal,
            # visible window instead (like MPC-HC does).
            "--no-qt-start-minimized",
            "--no-one-instance",
            "--no-playlist-enqueue",
            "--playlist-autostart",
            "--network-caching=800",
            "--http-reconnect",
            "--no-video-title-show",
            stream_url,
        ]
    else:
        # MPC-HC: /new prevents an existing instance stealing the URL.
        args = [path, "/new", "/play", stream_url]

    # Capture native-player stdout/stderr instead of discarding it. External
    # players are a separate process, so their decoder/network failures must be
    # diagnosable even when the Archive X browser page is closed.
    try:
        from .. import config
        log_dir = Path(config.LOG_DIR) / "external_players"
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_dir = Path.cwd() / "logs" / "external_players"
        log_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "_", title or "video")[:80].strip("._-") or "video"
    log_path = log_dir / f"{name}_{int(time.time())}_{safe_title}.log"
    log_file = open(log_path, "ab", buffering=0)
    launch_started = time.monotonic()

    kwargs = {}
    if sys.platform == "win32":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 1          # SW_SHOWNORMAL
        kwargs["startupinfo"] = si
        _allow_foreground()
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        proc = subprocess.Popen(
            args,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        creationflags=creationflags,
        **kwargs,
    )
    except Exception:
        try:
            log_file.close()
        except Exception:
            _swallowed_note("vlc_launcher.launch")
            pass
        raise
    _procs[proc.pid] = proc
    _proc_logs[proc.pid] = log_file
    try:
        with open(log_path, "ab", buffering=0) as marker:
            marker.write((f"\nArchive X launch player={name} pid={proc.pid} "
                          f"startup_ms={(time.monotonic()-launch_started)*1000:.1f}\n").encode("utf-8", "replace"))
    except Exception:
        _swallowed_note("vlc_launcher.launch")
        pass
    if sys.platform == "win32":
        threading.Thread(target=_bring_to_front, args=(proc.pid, name), daemon=True,
                         name=f"front-{proc.pid}").start()
    return {"player": name, "pid": proc.pid, "log_path": str(log_path)}


def _allow_foreground():
    """Let the player we are about to start take the foreground."""
    try:
        import ctypes
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    except Exception:
        _swallowed_note("vlc_launcher._allow_foreground")
        pass


def _bring_to_front(pid: int, name: str, timeout: float = 20.0):
    """Windows only: when the player window appears, restore it if it is
    minimized/hidden in the tray and put it in front of the browser."""
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return
    user32 = ctypes.windll.user32
    SW_RESTORE, SW_SHOW = 9, 5
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x2, 0x1, 0x40
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def windows():
        found = []

        def cb(hwnd, _):
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if owner_pid.value != pid or user32.GetWindow(hwnd, 4):   # 4 = GW_OWNER
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or ""
            visible = bool(user32.IsWindowVisible(hwnd))
            main_vlc = name == "vlc" and "vlc" in title.lower()
            if title and (visible or main_vlc):
                found.append((hwnd, visible))
            return True
        user32.EnumWindows(EnumProc(cb), 0)
        return found

    def raise_window(hwnd, visible):
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        elif not visible:
            user32.ShowWindow(hwnd, SW_SHOW)
        # Windows only lets the foreground app activate another window; a
        # simulated ALT press satisfies that rule for this one call.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        user32.SetForegroundWindow(hwnd)

    deadline = time.monotonic() + timeout
    raised = 0
    while time.monotonic() < deadline and raised < 2:
        time.sleep(0.4)
        try:
            found = windows()
        except Exception:
            return
        if found:
            for hwnd, visible in found:
                try:
                    raise_window(hwnd, visible)
                except Exception:
                    _swallowed_note("vlc_launcher._bring_to_front")
                    pass
            raised += 1
            time.sleep(1.5)   # the window is resized once the video starts


def status(pid: int) -> dict:
    """Return whether an Archive X-launched external player process is alive.

    A missing PID is considered not running. Finished processes are removed
    from the launcher registry so the UI can reliably treat the external
    playback session as stopped when the user closes VLC/MPC-HC.
    """
    try:
        pid = int(pid)
    except Exception:
        return {"known": False, "running": False, "exit_code": None}
    proc = _procs.get(pid)
    if proc is None:
        return {"known": False, "running": False, "exit_code": None}
    code = proc.poll()
    if code is None:
        return {"known": True, "running": True, "exit_code": None}
    _procs.pop(pid, None)
    log_file = _proc_logs.pop(pid, None)
    if log_file is not None:
        try:
            log_file.close()
        except Exception:
            _swallowed_note("vlc_launcher.status")
            pass
    return {"known": True, "running": False, "exit_code": int(code)}


def stop(pid: int) -> bool:
    proc = _procs.pop(pid, None)
    log_file = _proc_logs.pop(pid, None)
    if proc is None:
        if log_file is not None:
            try: log_file.close()
            except Exception:
                _swallowed_note("vlc_launcher.stop")
                pass
        return False
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception:
            _swallowed_note("vlc_launcher.stop")
            pass
    else:
        try:
            proc.terminate()
        except Exception:
            _swallowed_note("vlc_launcher.stop")
            pass
    if log_file is not None:
        try:
            log_file.close()
        except Exception:
            _swallowed_note("vlc_launcher.stop")
            pass
    return True
