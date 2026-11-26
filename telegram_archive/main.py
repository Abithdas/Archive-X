"""Entry point: `python -m telegram_archive` (the desktop shell starts it this way)."""
from __future__ import annotations
from .core.swallowed import note as _swallowed_note
import asyncio
import os
import sys
import json
import threading
import time

from . import config
from .app import create_app
from .core.auth import ensure_web_credentials
from .core.database import Database
from .core.runtime_cleanup import cleanup_runtime_artifacts
from .core import desktop_mode


def write_default_config():
    if config.CONFIG_PATH.exists():
        return
    config.CONFIG_PATH.write_text(json.dumps({
        "web_username": "admin",
        "notes": "Web UI binds to all local interfaces for same-LAN access. Keep Windows Firewall restricted to trusted/private networks.",
    }, indent=2), encoding="utf-8")


OBSOLETE_FILES = (
    # v18.9: replaced by the unified Telegram Media Manager / removed features.
    "engines/scanner.py", "engines/router_engine.py", "engines/duplicate_engine.py",
    "engines/thumbnail_repair.py", "engines/intake_watcher.py", "engines/thumbnail_engine.py",
    "telegram/watcher.py", "telegram/scanner.py", "telegram/files.py", "core/assistant.py",
    "video/__init__.py", "video/analyzer.py", "video/duplicate.py", "video/router.py",
    "video/streamability.py", "video/thumbnail.py", "video/vertical.py",
)


def remove_obsolete_files():
    if desktop_mode.frozen():
        return  # a frozen install folder is read-only and updated as a whole by the installer
    import shutil
    for rel in OBSOLETE_FILES:
        path = config.PACKAGE_DIR / rel
        try:
            if path.exists():
                path.unlink()
                print(f"  Removed obsolete module: {rel}")
        except Exception:
            _swallowed_note("main.remove_obsolete_files")
            pass
    video = config.PACKAGE_DIR / "video"
    if video.exists() and not any(p.suffix == ".py" for p in video.iterdir()):
        shutil.rmtree(video, ignore_errors=True)


def _run_desktop_server(app):
    """Desktop mode: plain HTTP on a bound loopback socket, announced to the shell on stdout.

    The listening socket is created first and handed to uvicorn, so the port the shell learns about cannot be
    taken by another program between "pick a port" and "start serving".
    """
    import uvicorn

    sock = desktop_mode.pick_socket(desktop_mode.requested_port())
    port = sock.getsockname()[1]

    async def _serve():
        loop = asyncio.get_running_loop()
        # open streams and live-update sockets must not hold the shutdown: after 3 s uvicorn closes them
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on", timeout_graceful_shutdown=3))

        def _request_stop():
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        app.state.request_shutdown = _request_stop
        desktop_mode.watch_parent(_request_stop)

        async def _announce_when_started():
            while not server.started:
                await asyncio.sleep(0.05)
            desktop_mode.announce_ready(port)

        asyncio.create_task(_announce_when_started())
        if not (os.getenv("ARCHIVE_X_LAUNCH_SECRET") or "").strip():
            # Started by hand (no desktop shell): the window's hand-off link is the only way in. It carries the launch secret, so it is opened
            # in the browser rather than printed where a log or a terminal history would keep it.
            import webbrowser
            if not webbrowser.open(f"http://127.0.0.1:{port}/desktop/session?token={desktop_mode.launch_secret()}"):
                print("  Archive X is running, but no browser could be opened. Set ARCHIVE_X_LAUNCH_SECRET and open /desktop/session?token=<it> yourself.", flush=True)
        await server.serve(sockets=[sock])

    asyncio.run(_serve())
    # Cleanup is done (the database is closed in the lifespan). A helper thread that is still winding down must not keep the process, and
    # with it the window's "Exit", waiting.
    import logging as _logging
    _logging.shutdown()
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(0)


STARTUP_PHASES: list[tuple[str, float]] = []
STARTUP_T0 = time.monotonic()


class _phase:
    """Time one startup phase; the summary is printed and logged once the app is ready."""
    def __init__(self, name: str):
        self.name = name
    def __enter__(self):
        self.t = time.monotonic()
    def __exit__(self, *exc):
        STARTUP_PHASES.append((self.name, time.monotonic() - self.t))
        return False


def main():
    """Program entry: take the instance lock, prepare data, then run the web server."""
    # The backend runs in one way only: loopback HTTP behind the desktop shell (docs/adr/0005).
    os.environ.setdefault("ARCHIVE_X_DESKTOP", "1")
    from .core.instance_lock import AlreadyRunning, acquire as acquire_instance_lock
    try:
        acquire_instance_lock(config.USER_DATA_DIR)
    except AlreadyRunning as exc:
        print(f"  {exc}. Close the running Archive X first.")
        raise SystemExit(2)
    from .core import crash_log
    crash_log.install(config.LOG_DIR)   # a crash inside native code (decoder, image library) leaves a stack trace in the log bundle
    with _phase("cleanup"):
        remove_obsolete_files()
    # Walking the player cache for stale leftovers took 11 s of the 18 s start on a real library; nothing waits for it, so it runs beside the start.
    def _cleanup_in_background():
        try:
            cleanup_runtime_artifacts()
        except Exception as exc:
            print(f"  Runtime cleanup skipped: {type(exc).__name__}: {exc}")
    threading.Thread(target=_cleanup_in_background, name="runtime-cleanup", daemon=True).start()

    with _phase("data migration"):
        moved = config.migrate_legacy_data()
        for m in moved:
            print(f"  Migrated from previous version: {m}")
        write_default_config()
    recovered = None
    with _phase("database open + schema checks"):
        try:
            db = Database()
        except Exception as exc:
            from .core import db_recovery
            if not db_recovery.is_corruption(exc):
                raise
            # A damaged database file used to end the start here for good. Move it aside, start a new one and copy back what can be read.
            print(f"  Database is damaged ({exc}); recovering what can be read", flush=True)
            moved = db_recovery.quarantine(config.DB_PATH)
            db = Database()
            recovered = (db_recovery.salvage(moved, db), moved.parent)
    if recovered:
        from .core import db_recovery
        message = db_recovery.describe(*recovered)
        print("  " + message, flush=True)
        db.log(message, "ERROR", event="database.recovered")
    print("  database steps: " + ", ".join(f"{k} {v}s" for k, v in getattr(db, "startup_steps", {}).items()), flush=True)
    with _phase("asset store"):
        try:
            asset_result = db.hydrate_persistent_asset_store()
            if asset_result and not asset_result.get("skipped"):
                print(f"  Persistent asset export: {asset_result}")
        except Exception as exc:
            print(f"  Persistent asset export skipped: {type(exc).__name__}: {exc}")
    with _phase("credentials"):
        ensure_web_credentials(db)
        desktop_mode.prepare_credentials(db)   # no web login: a random, never-shown password
    print(f"  Archive X backend {config.APP_VERSION} (loopback only); data: {config.USER_DATA_DIR}", flush=True)

    with _phase("application setup"):
        app = create_app(db=db)
    app.state.startup_phases = STARTUP_PHASES
    app.state.startup_t0 = STARTUP_T0
    try:
        _run_desktop_server(app)
    finally:
        # main() owns the database: flush buffered events and close it last.
        db.close()


if __name__ == "__main__":
    main()
