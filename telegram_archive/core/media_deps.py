"""One dependency check for everything that makes thumbnails or reads video
(Telegram Media Manager, Upload Local Files, player helpers).

The Media Manager used to report "PyAV/Pillow is not installed" as one lump
and stop completely. This module checks each library separately with the
real import error (a DLL problem looks very different from "not installed"),
always from the Python interpreter that runs the app, and gives the exact
install command for that interpreter.
"""
from __future__ import annotations

import importlib
import sys
from typing import Optional

from .. import config
from . import pyav_support


def _try_import(name: str) -> tuple[bool, str, Optional[str]]:
    try:
        mod = importlib.import_module(name)
        return True, "", str(getattr(mod, "__version__", "") or getattr(mod, "VERSION", "") or "")
    except BaseException as exc:          # ImportError, OSError (DLL), etc.
        return False, f"{type(exc).__name__}: {exc}", None


def preflight() -> dict:
    from ..playback import media_tools
    telethon_ok, telethon_err, telethon_v = _try_import("telethon")
    av_diag = pyav_support.diagnose()
    av_ok, av_err, av_v = av_diag["ok"], av_diag["error"], av_diag["version"] or None
    pil_ok, pil_err, pil_v = _try_import("PIL.Image")
    cryptg_ok, cryptg_err, cryptg_v = _try_import("cryptg")
    ffmpeg = media_tools.find_ffmpeg()
    ffprobe = media_tools.find_ffprobe()
    req = config.PACKAGE_DIR.parent / "requirements.txt"
    install = f'"{sys.executable}" -m pip install -r "{req}"'
    return {
        "python": sys.executable,
        "telethon": {"ok": telethon_ok, "error": telethon_err, "version": telethon_v},
        "av": {"ok": av_ok, "error": av_err, "version": av_v, "dll_paths": av_diag.get("dll_paths", [])},
        "pillow": {"ok": pil_ok, "error": pil_err, "version": pil_v},
        "cryptg": {"ok": cryptg_ok, "error": cryptg_err, "version": cryptg_v, "optional": True},
        "ffmpeg": {"ok": bool(ffmpeg), "path": ffmpeg or ""},
        "ffprobe": {"ok": bool(ffprobe), "path": ffprobe or ""},
        "thumbnails_ok": bool(telethon_ok and pil_ok and (av_ok or ffmpeg)),
        "pyav_optional": True,
        "thumbnail_backend": "pyav" if (telethon_ok and av_ok and pil_ok) else ("ffmpeg-fallback" if telethon_ok and pil_ok and ffmpeg else "unavailable"),
        "install_command": install,
        "ffmpeg_hint": "" if ffmpeg else "Install FFmpeg (winget install Gyan.FFmpeg) or run Setup-And-Run.ps1",
    }


def summary_lines(p: dict) -> list[str]:
    def row(label, item, extra=""):
        state = ("OK " + (item.get("version") or item.get("path") or "")).strip() if item["ok"] else "MISSING"
        return f"  {label:<9} {state}{extra}"
    lines = [
        "Dependency check (Python: " + p["python"] + ")",
        row("Telethon", p["telethon"]), row("PyAV", p["av"]), row("Pillow", p["pillow"]), row("cryptg", p.get("cryptg", {"ok": False})),
        row("FFmpeg", p["ffmpeg"]), row("FFprobe", p["ffprobe"]),
    ]
    for key, label in (("telethon", "Telethon"), ("av", "PyAV"), ("pillow", "Pillow")):
        if not p[key]["ok"]:
            lines.append(f"  {label} import error: {p[key]['error']}")
    if p.get("cryptg", {}).get("ok") is False:
        lines.append("  cryptg is optional; installing it can reduce Telethon crypto CPU overhead during heavy media transfers.")
    if not (p["telethon"]["ok"] and p["av"]["ok"] and p["pillow"]["ok"]):
        lines.append("  Fix: close the app and run Setup-And-Run.ps1; it installs only missing packages and does not reinstall healthy PyAV.")
    if not p["av"]["ok"]:
        lines.append("  PyAV is unavailable; internal media features will use external FFmpeg only for operations PyAV cannot perform.")
    if not p["ffmpeg"]["ok"]:
        lines.append("  External FFmpeg fallback is unavailable; core PyAV media operations remain available when PyAV is healthy.")
    return lines
