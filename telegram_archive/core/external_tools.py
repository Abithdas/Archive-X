"""Deterministic external-tool discovery.

Application-bundled tools always win. System PATH is a last-resort compatibility
fallback, so a user's unrelated FFmpeg installation cannot silently change the
archive's media behavior.
"""
from __future__ import annotations
import os, shutil, sys
from pathlib import Path
from .. import config


def _names(name: str):
    return [name + ".exe", name] if sys.platform == "win32" else [name]


def find_tool(name: str) -> str | None:
    candidates: list[Path] = []
    app = config.APP_DIR
    # Preferred application-local layouts.
    for base in (app / "tools" / "ffmpeg", app / "ffmpeg", app / "tools"):
        for n in _names(name):
            candidates.extend((base / n, base / "bin" / n))
    # Only then consider normal Windows/system installations.
    for n in _names(name):
        p = shutil.which(n)
        if p:
            candidates.append(Path(p))
    for p in candidates:
        try:
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass
    return None


def ffmpeg() -> str | None:
    return find_tool("ffmpeg")


def ffprobe() -> str | None:
    return find_tool("ffprobe")
