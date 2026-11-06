"""Prerequisite detection: FFmpeg, FFprobe, VLC, MPC-HC.

Detection only — installation is done by Setup-And-Run.ps1 (via winget,
with your confirmation), because installing software needs a normal
Windows prompt, not a background web server.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from .external_tools import ffmpeg as find_app_ffmpeg, ffprobe as find_app_ffprobe
from typing import Optional

_WINDOWS_CANDIDATES = {
    "vlc": [
        r"C:\Program Files\VideoLAN\VLC\vlc.exe",
        r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
    ],
    "mpc-hc": [
        r"C:\Program Files\MPC-HC\mpc-hc64.exe",
        r"C:\Program Files (x86)\MPC-HC\mpc-hc.exe",
        r"C:\Program Files\K-Lite Codec Pack\MPC-HC64\mpc-hc64.exe",
        r"C:\Program Files (x86)\K-Lite Codec Pack\MPC-HC\mpc-hc.exe",
    ],
}


def _find(names: list[str], extra: list[str]) -> Optional[str]:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    for c in extra:
        if Path(c).exists():
            return c
    return None


def _version(path: str) -> str:
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=5)
        first = (out.stdout or out.stderr).splitlines()
        return first[0][:80] if first else ""
    except Exception:
        return ""


def _has_module(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def check() -> dict:
    local = os.environ.get("LOCALAPPDATA", "")
    winget_links = [str(Path(local) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe")] if local else []
    ffmpeg = find_app_ffmpeg()
    ffprobe = find_app_ffprobe()
    vlc = _find(["vlc"], _WINDOWS_CANDIDATES["vlc"])
    mpc = _find(["mpc-hc64", "mpc-hc"], _WINDOWS_CANDIDATES["mpc-hc"])
    return {
        "ffmpeg": {"found": bool(ffmpeg), "path": ffmpeg, "version": _version(ffmpeg) if ffmpeg else "",
                   "needed_for": "optional playback/conversion fallback; PyAV is primary media engine"},
        "ffprobe": {"found": bool(ffprobe), "path": ffprobe,
                    "needed_for": "optional compatibility fallback; PyAV is primary probe"},
        "vlc": {"found": bool(vlc), "path": vlc, "needed_for": "'Play in VLC'"},
        "mpc_hc": {"found": bool(mpc), "path": mpc, "needed_for": "'Play in MPC-HC' (optional if VLC exists)"},
        "cryptg": {"found": _has_module("cryptg"), "path": "python package",
                   "needed_for": "optional native Telethon crypto acceleration for lower CPU overhead during media transfers"},
        "all_required_ok": True,
    }
