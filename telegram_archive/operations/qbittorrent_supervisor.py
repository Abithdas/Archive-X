"""Find, configure, launch and health-check a local qBittorrent instance."""

# ------------------------------------------------------------------
# FILE MAP (search for "SECTION:" to jump between sections)
#       - Location and detection
#       - WebUI configuration
#       - Readiness and launch
# ------------------------------------------------------------------
from __future__ import annotations

from ..core.swallowed import note as _swallowed_note
import asyncio
import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .. import config


class QBittorrentSupervisor:
    """Discover, start and health-check a local qBittorrent instance.

    The supervisor deliberately manages only a localhost WebUI by default. It
    does not alter an already-running remote/externally-managed instance.
    When qBittorrent is not running and local auto-management is enabled, it
    prepares the normal Windows preferences file with localhost-only WebUI
    access, starts qBittorrent, and waits for its API to become reachable.
    """

    DEFAULT_URL = "http://127.0.0.1:8080"
    INSTALL_ID = "qBittorrent.qBittorrent"
    INSTALL_TIMEOUT = 180
    START_TIMEOUT = 60


    # ------------------------------------------------------------------
    # SUBSECTION: Location and detection
    # Local checks and executable discovery.
    # ------------------------------------------------------------------
    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        *,
        auto_start: bool = True,
        auto_install: bool = True,
        auto_configure_local_webui: bool = True,
    ):
        self.base_url = str(base_url or self.DEFAULT_URL).rstrip("/")
        self.auto_start = bool(auto_start)
        self.auto_install = bool(auto_install)
        self.auto_configure_local_webui = bool(auto_configure_local_webui)
        self.process: subprocess.Popen | None = None
        self.last_action = ""
        self.last_error = ""
        self.last_check = 0.0

    @property
    def is_local(self) -> bool:
        try:
            host = (urlparse(self.base_url).hostname or "").lower()
        except Exception:
            host = ""
        return host in {"127.0.0.1", "localhost", "::1"}

    @property
    def port(self) -> int:
        try:
            parsed = urlparse(self.base_url)
            if parsed.port:
                return int(parsed.port)
        except Exception:
            _swallowed_note("qbittorrent_supervisor.QBittorrentSupervisor.port")
            pass
        return 8080

    @staticmethod
    def _windows() -> bool:
        return os.name == "nt"

    @staticmethod
    def _process_running() -> bool:
        if os.name != "nt":
            return False
        try:
            result = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq qbittorrent.exe"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "qbittorrent.exe" in (result.stdout or "").lower()
        except Exception:
            return False

    @staticmethod
    def _candidate_paths() -> list[Path]:
        values = [
            os.getenv("ProgramFiles"),
            os.getenv("ProgramW6432"),
            os.getenv("ProgramFiles(x86)"),
            os.getenv("LOCALAPPDATA"),
        ]
        candidates: list[Path] = []
        for root in values:
            if root:
                base = Path(root)
                candidates.extend([
                    base / "qBittorrent" / "qbittorrent.exe",
                    base / "qBittorrent" / "qbittorrent.exe",
                    base / "Programs" / "qBittorrent" / "qbittorrent.exe",
                ])
        try:
            from pathlib import PureWindowsPath
            _ = PureWindowsPath
            # Common per-machine install location.
            candidates.append(Path(r"C:\Program Files\qBittorrent\qbittorrent.exe"))
            candidates.append(Path(r"C:\Program Files (x86)\qBittorrent\qbittorrent.exe"))
        except Exception:
            _swallowed_note("qbittorrent_supervisor.QBittorrentSupervisor._candidate_paths")
            pass
        return list(dict.fromkeys(candidates))

    @classmethod
    def find_executable(cls) -> Path | None:
        for path in cls._candidate_paths():
            try:
                if path.is_file():
                    return path
            except OSError:
                continue
        if os.name == "nt":
            try:
                found = shutil.which("qbittorrent.exe") or shutil.which("qbittorrent")
                if found:
                    return Path(found)
            except Exception:
                _swallowed_note("qbittorrent_supervisor.QBittorrentSupervisor.find_executable")
                pass
            # Registry lookup catches custom install folders.
            try:
                import winreg
                for hive, subkey in (
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\qBittorrent"),
                    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\qBittorrent"),
                ):
                    with winreg.OpenKey(hive, subkey) as key:
                        location, _ = winreg.QueryValueEx(key, "InstallLocation")
                        candidate = Path(str(location)) / "qbittorrent.exe"
                        if candidate.is_file():
                            return candidate
            except Exception:
                _swallowed_note("qbittorrent_supervisor.QBittorrentSupervisor.find_executable")
                pass
        return None


    # ------------------------------------------------------------------
    # SUBSECTION: WebUI configuration
    # qBittorrent ini file edits.
    # ------------------------------------------------------------------
    @staticmethod
    def _preference_files() -> list[Path]:
        roaming = Path(os.getenv("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        folder = roaming / "qBittorrent"
        return [folder / "qbittorrent.ini", folder / "qBittorrent.conf"]

    @classmethod
    def _select_preferences_file(cls) -> Path:
        paths = cls._preference_files()
        for path in paths:
            if path.exists():
                return path
        return paths[0]

    @staticmethod
    def _ini_has_value(text: str, key: str) -> bool:
        wanted = f"WebUI\\{key}="
        return any(line.strip().lower().startswith(wanted.lower()) for line in text.splitlines())

    @staticmethod
    def _set_ini_value(text: str, key: str, value: str) -> str:
        """Set Preferences/WebUI\\<key> in qBittorrent's ini text.

        A missing key is inserted INSIDE the [Preferences] section. It used to
        be appended to the end of the file, i.e. under whatever section came
        last (usually [RSS]), where qBittorrent ignores it - so the WebUI was
        never enabled and startup timed out."""
        lines = text.splitlines()
        wanted = f"WebUI\\{key}"
        section = ""
        pref_start = pref_end = None
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if section.lower() == "preferences" and pref_end is None:
                    pref_end = idx
                section = stripped[1:-1]
                if section.lower() == "preferences":
                    pref_start = idx
                    pref_end = None
                continue
            if section.lower() == "preferences" and stripped.lower().startswith(wanted.lower() + "="):
                lines[idx] = f"{wanted}={value}"
                return "\n".join(lines) + "\n"
        if pref_start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(["[Preferences]", f"{wanted}={value}"])
            return "\n".join(lines) + "\n"
        if pref_end is None:
            pref_end = len(lines)
        insert_at = pref_end
        while insert_at > pref_start + 1 and not lines[insert_at - 1].strip():
            insert_at -= 1          # keep the blank line before the next section
        lines.insert(insert_at, f"{wanted}={value}")
        return "\n".join(lines) + "\n"

    @classmethod
    def configure_local_webui(cls, port: int) -> tuple[bool, str]:
        """Enable a local WebUI before first start without disabling authentication or overwriting an existing address.

        qBittorrent exposes a specific LocalHostAuth setting. Only the local
        WebUI binding/auth behavior is touched here; user download/session
        settings are left intact. A backup is created before the first edit.
        """
        if os.name != "nt":
            return False, "automatic qBittorrent profile management is Windows-only"
        path = cls._select_preferences_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
        except UnicodeDecodeError:
            original = path.read_text(encoding="utf-8", errors="replace")
        backup = path.with_suffix(path.suffix + ".telegram-archive.bak")
        if path.exists() and not backup.exists():
            try:
                shutil.copy2(path, backup)
            except Exception:
                _swallowed_note("qbittorrent_supervisor.QBittorrentSupervisor.configure_local_webui")
                pass
        text = original
        # Never overwrite an existing WebUI address/auth policy. Only fill in
        # missing settings for a brand-new local profile. Authentication stays
        # enabled by default so other local Windows accounts cannot silently
        # control qBittorrent.
        if not cls._ini_has_value(text, "Enabled"):
            text = cls._set_ini_value(text, "Enabled", "true")
        if not cls._ini_has_value(text, "Address"):
            text = cls._set_ini_value(text, "Address", "127.0.0.1")
        if not cls._ini_has_value(text, "Port"):
            text = cls._set_ini_value(text, "Port", str(int(port)))
        if not cls._ini_has_value(text, "LocalHostAuth"):
            text = cls._set_ini_value(text, "LocalHostAuth", "true")
        if not cls._ini_has_value(text, "UseUPnP"):
            text = cls._set_ini_value(text, "UseUPnP", "false")
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            return False, f"could not write qBittorrent preferences: {exc}"
        return True, str(path)

    @classmethod
    def webui_profile_summary(cls, port: int) -> dict[str, Any]:
        """Read non-secret WebUI settings for diagnostics without modifying the profile."""
        path = cls._select_preferences_file()
        result: dict[str, Any] = {"path": str(path), "enabled": None, "port": None, "address": "", "https": None}
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
        except OSError:
            return result
        values: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("[") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip().lower()
            if key.startswith("webui\\"):
                values[key[6:]] = value.strip()
        if "enabled" in values:
            result["enabled"] = values["enabled"].lower() in {"1", "true", "yes", "on"}
        if "port" in values:
            try:
                result["port"] = int(values["port"])
            except ValueError:
                pass
        if "address" in values:
            result["address"] = values["address"]
        if "https\\enabled" in values:
            result["https"] = values["https\\enabled"].lower() in {"1", "true", "yes", "on"}
        return result


    # ------------------------------------------------------------------
    # SUBSECTION: Readiness and launch
    # Reachability, install, launch and status.
    # ------------------------------------------------------------------
    @staticmethod
    def _probe_sync(url: str, timeout: float = 2.0) -> tuple[bool, int, str]:
        endpoint = url.rstrip("/") + "/api/v2/app/version"
        try:
            with urlopen(Request(endpoint, headers={"User-Agent": "Telegram-Archive-qBittorrent-Supervisor"}), timeout=timeout) as resp:
                body = resp.read(512).decode("utf-8", "replace").strip()
                return True, int(getattr(resp, "status", 200) or 200), body
        except Exception as exc:
            status = int(getattr(exc, "code", 0) or 0)
            return False, status, str(exc)

    async def reachable(self) -> bool:
        ok, _, _ = await asyncio.to_thread(self._probe_sync, self.base_url)
        self.last_check = time.time()
        return ok

    async def ensure_ready(self) -> dict[str, Any]:
        """Make the WebUI reachable, launching or installing qBittorrent if allowed."""
        ok, status, body = await asyncio.to_thread(self._probe_sync, self.base_url)
        self.last_check = time.time()
        if ok:
            self.last_action = "already-running"
            self.last_error = ""
            return {"ok": True, "action": self.last_action, "version": body, "executable": str(await asyncio.to_thread(self.find_executable) or "")}
        # A 401/403 means the WebUI process is alive; the caller can perform
        # normal username/password or API-key authentication itself. Do not
        # try to replace an externally managed qBittorrent instance.
        if status in {401, 403} and await asyncio.to_thread(self._process_running):
            self.last_action = "already-running-auth-required"
            return {"ok": True, "action": self.last_action, "version": "", "executable": str(await asyncio.to_thread(self.find_executable) or "")}

        if not self.auto_start:
            raise RuntimeError(f"qBittorrent is not reachable at {self.base_url} and automatic start is disabled")
        if not self._windows():
            raise RuntimeError("automatic qBittorrent startup is currently supported on Windows only")
        if not self.is_local:
            raise RuntimeError(f"qBittorrent is not reachable at {self.base_url}; automatic process management is limited to localhost")

        exe = await asyncio.to_thread(self.find_executable)
        if exe is None and self.auto_install:
            self.last_action = "installing"
            result = await asyncio.to_thread(self._install_with_winget)
            if result and result.returncode == 0:
                exe = await asyncio.to_thread(self.find_executable)
        if exe is None:
            raise RuntimeError("qBittorrent was not found. Install qBittorrent or enable WinGet, then restart Archive X.")

        running = await asyncio.to_thread(self._process_running)
        if not running and self.auto_configure_local_webui and self.is_local:
            ok, detail = await asyncio.to_thread(self.configure_local_webui, self.port)
            if not ok:
                self.last_error = detail
                # Continue; an existing configured password/API key can still work.

        if running:
            self.last_action = "waiting-for-webui"
        else:
            args = [str(exe), "--no-splash", "--confirm-legal-notice", f"--webui-port={self.port}"]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            try:
                self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                creationflags=creationflags)
                self.last_action = "started"
            except Exception as exc:
                self.last_error = str(exc)
                raise RuntimeError(f"Could not start qBittorrent: {exc}") from exc

        deadline = time.monotonic() + self.START_TIMEOUT
        last_status = 0
        last_error = ""
        while time.monotonic() < deadline:
            ok, status, body = await asyncio.to_thread(self._probe_sync, self.base_url, 2.0)
            last_status = status
            last_error = body
            if ok:
                self.last_error = ""
                return {"ok": True, "action": self.last_action, "version": body, "executable": str(exe)}
            # A WebUI returning 401/403 is still a healthy listener. The API
            # client will perform the configured authentication afterwards.
            if status in {401, 403}:
                self.last_error = ""
                return {"ok": True, "action": self.last_action, "version": "", "executable": str(exe), "auth_required": True}
            await asyncio.sleep(0.5)

        process_alive = await asyncio.to_thread(self._process_running)
        profile = await asyncio.to_thread(self.webui_profile_summary, self.port)
        self.last_error = last_error or "WebUI probe timed out"
        if not process_alive:
            raise RuntimeError(
                f"qBittorrent exited during startup before its WebUI became reachable at {self.base_url}. "
                f"Profile: {profile.get('path') or 'unknown'}"
            )
        configured_port = profile.get("port")
        enabled = profile.get("enabled")
        if configured_port and int(configured_port) != int(self.port):
            raise RuntimeError(
                f"qBittorrent is running, but its WebUI is configured for port {configured_port}, "
                f"not {self.port}. Set Archive X WebUI to http://127.0.0.1:{configured_port} or change qBittorrent WebUI to {self.port} and restart qBittorrent."
            )
        if enabled is False:
            raise RuntimeError(
                f"qBittorrent is running, but its WebUI is disabled. Enable WebUI in qBittorrent Preferences → WebUI, "
                f"then retry validation at {self.base_url}."
            )
        status_note = f" HTTP status {last_status}." if last_status else ""
        raise RuntimeError(
            f"qBittorrent is running, but WebUI did not become reachable at {self.base_url} within {self.START_TIMEOUT}s."
            f" Check qBittorrent WebUI address/port and HTTPS settings.{status_note}"
        )

    @classmethod
    def _install_with_winget(cls) -> subprocess.CompletedProcess[str] | None:
        winget = shutil.which("winget.exe") or shutil.which("winget")
        if not winget:
            return None
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run(
                [winget, "install", "--id", cls.INSTALL_ID, "-e", "--scope", "user",
                 "--accept-source-agreements", "--accept-package-agreements", "--disable-interactivity"],
                capture_output=True, text=True, timeout=cls.INSTALL_TIMEOUT,
                creationflags=flags,
            )
        except Exception:
            return None

    async def launch_gui(self) -> dict[str, Any]:
        # tasklist / PowerShell calls take seconds: run them off the web
        # server's event loop.
        return await asyncio.to_thread(self._launch_gui_sync)

    def _launch_gui_sync(self) -> dict[str, Any]:
        """Start/foreground the qBittorrent desktop application when possible.

        This is deliberately separate from WebUI readiness: the Web API may be
        reachable while the GUI is minimized or hidden, and a connectivity test
        should still give the operator a visible qBittorrent window.
        """
        if not self._windows():
            return {"ok": False, "action": "unsupported", "message": "Desktop qBittorrent GUI launch is supported on Windows only."}
        exe = self.find_executable()
        if exe is None:
            raise RuntimeError("qBittorrent executable was not found.")
        try:
            # Launching the executable is also the safest way to foreground an
            # already-running single-instance qBittorrent installation: the
            # existing process decides whether to activate its main window.
            if self._process_running():
                subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                # qBittorrent is single-instance; launching the executable may
                # only signal the existing process. Use the native Windows shell
                # activation path as a second attempt so Test/start makes the
                # desktop client visible when it is minimized or behind another
                # window.
                if self._windows():
                    try:
                        ps = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                              "$w=New-Object -ComObject WScript.Shell; [void]$w.AppActivate('qBittorrent')"]
                        subprocess.run(ps, capture_output=True, text=True, timeout=5,
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    except Exception:
                        _swallowed_note("qbittorrent_supervisor.QBittorrentSupervisor._launch_gui_sync")
                        pass
                action = "activated"
            else:
                subprocess.Popen([str(exe), "--no-splash", "--confirm-legal-notice", f"--webui-port={self.port}"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                action = "started"
            self.last_action = f"gui-{action}"
            return {"ok": True, "action": self.last_action, "executable": str(exe)}
        except Exception as exc:
            self.last_error = str(exc)
            raise RuntimeError(f"Could not launch qBittorrent GUI: {exc}") from exc

    async def status(self) -> dict[str, Any]:
        reachable = await self.reachable()
        running = await asyncio.to_thread(self._process_running) if self._windows() else reachable
        executable = await asyncio.to_thread(self.find_executable)
        return {
            "reachable": reachable,
            "url": self.base_url,
            "running": running,
            "executable": str(executable or ""),
            "action": self.last_action,
            "error": self.last_error,
        }
