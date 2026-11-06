"""Archive X Windows/cross-platform launcher.

The launcher has one responsibility: ensure the process uses the installation's
project interpreter, persist launcher diagnostics, and then enter the canonical
``telegram_archive.main`` entry point. It does not create a second console
process and does not contain application startup policy.
"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent
# Compatibility-visible Windows project interpreter path; kept explicit so diagnostics/tests and launcher tooling agree.
app_root = APP_ROOT
VENV_DIR = APP_ROOT / ".venv"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
WINDOWS_VENV_PYTHON = app_root / ".venv" / "Scripts" / "python.exe"
CONSOLE_CHILD_ENV = "TELEGRAM_ARCHIVE_CONSOLE_CHILD"


def _fallback_cli_log_path() -> Path:
    if os.name == "nt":
        root = Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "Archive X"
    else:
        root = Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "Archive X"
    return root / "logs" / "launcher-cli.log"


def _rotate_cli_log(path: Path) -> None:
    try:
        limit = 10 * 1024 * 1024
        backups = 5
        if not path.exists() or path.stat().st_size < limit:
            return
        for i in range(backups, 0, -1):
            dst = path.with_name(f"{path.name}.{i}")
            if dst.exists(): dst.unlink()
            src = path if i == 1 else path.with_name(f"{path.name}.{i-1}")
            if src.exists(): src.replace(dst)
    except Exception:
        pass


def _cli_log(message: str) -> None:
    """Persist launcher events without importing the application first."""
    try:
        path = _fallback_cli_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_cli_log(path)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} [launcher] {message}\n")
    except Exception:
        pass


def _bootstrap_project_venv() -> None:
    """Create the project venv when absent; never install application packages here."""
    if VENV_PYTHON.exists():
        return
    _cli_log(f"launch_id={os.getenv('ARCHIVE_X_LAUNCH_ID', '-')} phase=venv_create path={VENV_DIR}")
    VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)


def _invalidate_bytecode_on_version_change(package_root: Path | None = None, marker_path: Path | None = None) -> int:
    """Drop cached app bytecode after an in-place version update.

    Windows users often extract a new ZIP over an older folder. Timestamp/size
    collisions can leave a stale .pyc exporting an obsolete module API. This
    cache is app code only; user data and the venv are never touched.
    """
    package_root = package_root or (APP_ROOT / "telegram_archive")
    marker_path = marker_path or (APP_ROOT / ".archive-x-bytecode-version")
    try:
        config_source = (package_root / "config.py").read_text(encoding="utf-8")
        import re
        match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', config_source, re.MULTILINE)
        version = match.group(1) if match else ""
    except Exception:
        return 0
    if not version:
        return 0
    try:
        if marker_path.read_text(encoding="utf-8").strip() == version:
            return 0
    except Exception:
        pass
    removed = 0
    if package_root.exists():
        for cache_dir in package_root.rglob("__pycache__"):
            try:
                for pyc in cache_dir.glob("*.pyc"):
                    try:
                        pyc.unlink()
                        removed += 1
                    except OSError:
                        pass
                try:
                    cache_dir.rmdir()
                except OSError:
                    pass
            except OSError:
                continue
    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(version, encoding="utf-8")
    except OSError:
        # The app remains usable in read-only installs; the next startup can
        # retry invalidation if the marker could not be written.
        pass
    return removed


def _run() -> None:
    launch_id = os.getenv("ARCHIVE_X_LAUNCH_ID") or uuid.uuid4().hex[:16]
    os.environ["ARCHIVE_X_LAUNCH_ID"] = launch_id
    _cli_log(f"launch_id={launch_id} phase=start pid={os.getpid()} parent_pid={os.getppid()} python={sys.executable} root={APP_ROOT}")
    _bootstrap_project_venv()

    # Re-enter exactly once under the installation-owned interpreter. exec* is
    # used rather than spawning a child console, so the launcher keeps one
    # process and one console throughout startup.
    if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        os.environ[CONSOLE_CHILD_ENV] = "1"
        _cli_log(f"launch_id={launch_id} phase=interpreter_switch target={VENV_PYTHON}")
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
        return

    removed_bytecode = _invalidate_bytecode_on_version_change()
    _cli_log(f"launch_id={launch_id} phase=bytecode_cache_invalidation removed={removed_bytecode}")
    try:
        from telegram_archive.main import main
        _cli_log(f"launch_id={launch_id} phase=application_import status=success")
        main()
    except BaseException as exc:
        _cli_log(f"launch_id={launch_id} phase=application_start status=error type={type(exc).__name__} message={exc}")
        raise
    finally:
        _cli_log(f"launch_id={launch_id} phase=shutdown pid={os.getpid()}")


if __name__ == "__main__":
    _run()
