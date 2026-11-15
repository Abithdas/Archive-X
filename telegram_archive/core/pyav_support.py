"""Robust Windows PyAV bootstrap.

PyAV wheels bundle FFmpeg DLLs. On Windows, Python's DLL search rules can still
fail to locate those dependent DLLs, producing errors such as:

    ImportError: DLL load failed while importing bitstream

This module prepares DLL search directories *before* importing PyAV and retries
once after clearing a partially-loaded PyAV package. It never builds PyAV from
source and never silently swaps to an incompatible system FFmpeg build.
"""
from __future__ import annotations

from .swallowed import note as _swallowed_note
import ctypes
import importlib
import importlib.util
import os
import shutil
import site
import sys
from pathlib import Path
from typing import Iterable

_DLL_HANDLES: list[object] = []


def _windows() -> bool:
    return os.name == "nt"


def _unique_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for p in paths:
        try:
            if not p or not p.exists() or not p.is_dir():
                continue
            key = os.path.normcase(os.path.abspath(str(p)))
        except Exception:
            _swallowed_note("pyav_support._unique_paths")
            continue
        if key not in seen:
            seen.add(key)
            result.append(p)
    return result


def _site_package_dirs() -> list[Path]:
    paths: list[Path] = []
    try:
        paths.extend(Path(x) for x in site.getsitepackages())
    except Exception:
        _swallowed_note("pyav_support._site_package_dirs")
        pass
    try:
        user = site.getusersitepackages()
        if user:
            paths.append(Path(user))
    except Exception:
        _swallowed_note("pyav_support._site_package_dirs")
        pass
    return _unique_paths(paths)


def _av_dirs() -> list[Path]:
    dirs: list[Path] = []
    for base in _site_package_dirs():
        av_dir = base / "av"
        if av_dir.is_dir():
            dirs.append(av_dir)
            # Modern wheels place bundled DLLs here.
            dirs.append(av_dir / "libs")
            # Some packaging layouts put them beside av or one level below.
            dirs.append(base / "av.libs")
    try:
        spec = importlib.util.find_spec("av")
        if spec and spec.origin:
            av_dir = Path(spec.origin).resolve().parent
            dirs.extend([av_dir, av_dir / "libs", av_dir.parent / "av.libs"])
    except Exception:
        _swallowed_note("pyav_support._av_dirs")
        pass
    return _unique_paths(dirs)


def prepare_dll_search_paths() -> list[str]:
    """Register likely DLL directories before importing PyAV.

    Returns the directories actually registered. On non-Windows systems this is
    a no-op.
    """
    if not _windows():
        return []

    # PyAV must load the FFmpeg DLLs shipped with its own wheel. Do not add
    # system/WinGet/Chocolatey FFmpeg directories here: those are for the
    # external fallback engine and can cause native DLL version conflicts.
    paths = _unique_paths(_av_dirs())
    registered: list[str] = []

    add_dll = getattr(os, "add_dll_directory", None)
    if add_dll:
        for path in paths:
            try:
                handle = add_dll(str(path))
                _DLL_HANDLES.append(handle)
                registered.append(str(path))
            except OSError:
                pass

    # Do not mutate PATH with external FFmpeg directories. PyAV should remain
    # isolated from whatever FFmpeg a user has installed in Windows.

    return registered


def _purge_av_modules() -> None:
    for name in list(sys.modules):
        if name == "av" or name.startswith("av."):
            sys.modules.pop(name, None)


def _windows_runtime_hint(error: BaseException) -> str:
    text = str(error)
    if _windows() and "DLL load failed" in text:
        return (
            "; Windows could not load one of PyAV's native DLL dependencies. "
            "Archive X requires the official PyAV binary wheel and the Microsoft Visual C++ v14 x64 runtime."
        )
    return ""


def import_pyav():
    """Import PyAV after preparing Windows DLL search paths.

    The retry is deliberately limited to one clean re-import so startup cannot
    loop forever if the machine is missing a native dependency.
    """
    prepare_dll_search_paths()
    try:
        return importlib.import_module("av")
    except (ImportError, OSError) as first_error:
        if not _windows():
            raise
        _purge_av_modules()
        prepare_dll_search_paths()
        try:
            return importlib.import_module("av")
        except (ImportError, OSError) as second_error:
            hint = _windows_runtime_hint(second_error)
            raise ImportError(f"PyAV native DLL import failed: {second_error}{hint}") from first_error


_DIAG_MEMO: dict | None = None


def _diagnose_cache_key() -> str:
    """Identity of the PyAV install without importing it (dist metadata + Python)."""
    try:
        from importlib import metadata as _md
        dist = _md.distribution("av")
        version = dist.version
        try:
            stamp = int(Path(str(dist.locate_file("av"))).stat().st_mtime)
        except Exception:
            stamp = 0
    except Exception:
        version, stamp = "missing", 0
    return f"{sys.executable}|{sys.version.split()[0]}|av={version}|{stamp}"


def diagnose(*, force: bool = False) -> dict:
    """PyAV health, remembered per install.

    v29.4.390: the check below starts a second Python process that loads the
    FFmpeg DLLs. It ran before the server started on every launch and again on
    every Settings/Media Manager visit, which on Windows (process start plus
    antivirus DLL scanning) costs seconds each time. The result only changes
    when PyAV or Python changes, so it is cached in memory and on disk.
    """
    global _DIAG_MEMO
    key = _diagnose_cache_key()
    if not force and _DIAG_MEMO is not None and _DIAG_MEMO.get("_key") == key:
        return {k: v for k, v in _DIAG_MEMO.items() if k != "_key"}
    cache_file = None
    try:
        from .. import config as _config
        cache_file = Path(_config.USER_DATA_DIR) / "state" / "pyav_diagnostic.json"
        if not force and cache_file.is_file():
            import json as _json
            cached = _json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("_key") == key and cached.get("ok"):
                _DIAG_MEMO = cached
                return {k: v for k, v in cached.items() if k != "_key"}
    except Exception:
        cache_file = cache_file
    result = _diagnose_uncached()
    _DIAG_MEMO = {**result, "_key": key}
    if result.get("ok") and cache_file is not None:
        try:
            import json as _json
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(_json.dumps(_DIAG_MEMO), encoding="utf-8")
        except Exception:
            _swallowed_note("pyav_support.diagnose")
            pass
    return result


def diagnostic_main() -> int:
    """What the disposable child runs: import PyAV and its bitstream module, then say so as one JSON line."""
    import json
    av = import_pyav()
    from av import bitstream  # noqa: F401  (the import itself is the check)
    print(json.dumps({"ok": True, "version": str(getattr(av, "__version__", "") or ""),
                      "architecture": "64-bit" if sys.maxsize > 2**32 else "32-bit"}))
    return 0


def _diagnose_uncached() -> dict:
    """Check PyAV in a disposable child process.

    A native DLL access violation cannot be caught by Python. Keeping the
    diagnostic in a child means a broken PyAV wheel can never terminate the
    web application during startup or from the dependency dashboard.
    """
    import json
    import subprocess

    code = "from telegram_archive.core.pyav_support import diagnostic_main; raise SystemExit(diagnostic_main())"
    # The installed backend is a frozen program: it ignores "-c" and would start the whole app (which then stops at the
    # one-instance lock, so PyAV looked missing on every install). packaging/backend/entry.py answers this flag instead.
    cmd = [sys.executable, "--ax-pyav-diagnostic"] if getattr(sys, "frozen", False) else [sys.executable, "-c", code]
    env = os.environ.copy()
    env["TELEGRAM_ARCHIVE_PYAV_DIAGNOSTIC"] = "1"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=25,
                           env=env, creationflags=creationflags)
        if r.returncode == 0:
            line = next((x.strip() for x in r.stdout.splitlines() if x.strip()), "")
            try:
                data = json.loads(line)
            except Exception:
                data = {}
            if data.get("ok"):
                return {"ok": True, "version": data.get("version", ""), "bitstream": True,
                        "architecture": data.get("architecture", ""), "error": "",
                        "dll_paths": prepare_dll_search_paths(), "python": sys.executable}
        detail = (r.stderr or r.stdout or "PyAV diagnostic process failed").strip()
        return {"ok": False, "version": "", "error": f"Native diagnostic exit {r.returncode}: {detail[-1200:]}",
                "dll_paths": prepare_dll_search_paths(), "python": sys.executable}
    except subprocess.TimeoutExpired:
        return {"ok": False, "version": "", "error": "PyAV diagnostic timed out after 25 seconds",
                "dll_paths": prepare_dll_search_paths(), "python": sys.executable}
    except BaseException as exc:
        return {"ok": False, "version": "", "error": f"{type(exc).__name__}: {exc}",
                "dll_paths": prepare_dll_search_paths(), "python": sys.executable}
