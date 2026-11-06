"""Entry point: `python -m telegram_archive` or `python run.py` at the
repo root (see run.py, which just calls main() here).
"""
from __future__ import annotations
import asyncio
import os
import json
import threading
import time

from . import config
from .app import create_app
from .core.auth import ensure_web_credentials
from .core.certs import ensure_certificate
from .core.database import Database
from .core.runtime_cleanup import cleanup_runtime_artifacts
from .core.process_topology import startup_message
from .core import desktop_mode


def write_default_config():
    if config.CONFIG_PATH.exists():
        return
    config.CONFIG_PATH.write_text(json.dumps({
        "web_username": "admin",
        "notes": "Web UI binds to all local interfaces for same-LAN access. Keep Windows Firewall restricted to trusted/private networks.",
    }, indent=2), encoding="utf-8")


def print_startup():
    """Print the startup banner with local addresses and preflight results."""
    ip = config.local_ip()
    print()
    print("=" * 68)
    print("              ARCHIVE X WEB APPLICATION")
    print("=" * 68)
    print(f"  Local:       https://127.0.0.1:{config.PORT}/login")
    print(f"  Network:     https://{ip}:{config.PORT}")
    print(f"  Bind:        {config.HOST}:{config.PORT}")
    print(f"  Web transport: HTTP/3/QUIC mode={config.HTTP3_MODE}")
    print(f"  User data:   {config.USER_DATA_DIR}")
    print(f"  Database:    {config.DB_PATH}")
    print(f"  Auth/API:    {config.AUTH_DIR}")
    print(f"  Assets:      {config.ASSET_DIR}")
    print(f"  Metadata:    {config.METADATA_DIR}")
    print(f"  Session:     {config.SESSION_PATH}")
    print(f"  Certificate: {config.CERT_PATH}")
    print("-" * 68)
    try:
        from .telegram.service import TELETHON_AVAILABLE
    except ImportError:
        TELETHON_AVAILABLE = False
    print("  Telegram:    " + ("Telethon available" if TELETHON_AVAILABLE else "Telethon not installed"))
    from .core.prereqs import check
    pr = check()
    for name in ("ffmpeg", "ffprobe", "vlc", "mpc_hc", "cryptg"):
        print(f"  {name:<12} " + ("found" if pr[name]["found"] else "MISSING — " + pr[name]["needed_for"]))
    from .core.media_deps import preflight
    deps = preflight()
    for key, label in (("av", "PyAV"), ("pillow", "Pillow")):
        print(f"  {label:<12} " + ("found" if deps[key]["ok"] else "NOT WORKING — " + deps[key]["error"][:120]))
    if deps.get("thumbnail_backend")=="pyav":
        print("  Thumbnails:   PyAV + Pillow")
    elif deps.get("thumbnail_backend")=="ffmpeg" and not deps["av"]["ok"]:
        print("  Thumbnails:   FFmpeg fallback ACTIVE — PyAV repair required")
    elif deps.get("thumbnail_backend")=="unavailable":
        print("  Thumbnails:   unavailable — Telethon/FFmpeg required")
    print(f"  Phone setup: open https://{ip}:{config.PORT}/ca.crt on the phone once (see dashboard).")
    print("  LAN access: https://<this-PC-LAN-IP>:8443")
    print("  Do not expose this port to the public internet.")
    print("  Open the Network URL from another device on the same LAN.")
    print("=" * 68)
    print()


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
            pass
    video = config.PACKAGE_DIR / "video"
    if video.exists() and not any(p.suffix == ".py" for p in video.iterdir()):
        shutil.rmtree(video, ignore_errors=True)


def _http3_capability():
    """Return (available, reason) without importing Hypercorn unless requested."""
    try:
        import hypercorn  # noqa: F401
    except Exception as exc:
        return False, f"Hypercorn unavailable: {type(exc).__name__}: {exc}"
    try:
        import aioquic  # noqa: F401
    except Exception as exc:
        return False, f"aioquic unavailable: {type(exc).__name__}: {exc}"
    try:
        from hypercorn.config import Config as HyperConfig  # noqa: F401
        from hypercorn.asyncio import serve  # noqa: F401
    except Exception as exc:
        return False, f"Hypercorn HTTP/3 support unavailable: {type(exc).__name__}: {exc}"
    return True, "Hypercorn + aioquic available"


async def _serve_hypercorn(serve, app, hcfg):
    """Serve until Ctrl+C, console close (SIGBREAK) or SIGTERM, then shut down cleanly.

    An explicit shutdown trigger guarantees the application lifespan shutdown
    runs (jobs stopped, clean-shutdown marker written). Without a clean marker
    every next start treats all persisted jobs as interrupted.
    """
    import signal
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop(*_args):
        loop.call_soon_threadsafe(stop.set)

    # The authenticated Exit app endpoint uses the same stop event as OS
    # signals, so the normal ASGI lifespan performs a clean application shutdown.
    app.state.request_shutdown = _request_stop

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                loop.add_signal_handler(getattr(signal, name), stop.set)
            except (NotImplementedError, RuntimeError):
                signal.signal(getattr(signal, name), _request_stop)
    await serve(app, hcfg, shutdown_trigger=stop.wait)


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
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))

        def _request_stop():
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        app.state.request_shutdown = _request_stop
        desktop_mode.watch_parent(_request_stop)

        async def _announce_when_started():
            while not server.started:
                await asyncio.sleep(0.05)
            desktop_mode.announce_ready(port)

        asyncio.create_task(_announce_when_started())
        await server.serve(sockets=[sock])

    asyncio.run(_serve())


def _run_web_server(app, certfile, keyfile):
    """Run HTTPS over TCP plus HTTP/3 over QUIC on the same port when enabled."""
    if desktop_mode.enabled():
        _run_desktop_server(app)
        return
    mode = str(config.HTTP3_MODE or "auto").lower()
    available, reason = _http3_capability()

    if mode not in {"auto", "on", "off"}:
        print(f"  HTTP/3:      Unknown mode '{mode}', using auto.")
        mode = "auto"

    use_http3 = mode != "off" and available
    if mode == "on" and not available:
        print(f"  HTTP/3:      requested but unavailable — falling back to HTTPS/TCP")
        print(f"               {reason}")
    elif mode == "auto" and not available:
        print(f"  HTTP/3:      unavailable — using HTTPS/TCP")
        print(f"               {reason}")

    if use_http3:
        from hypercorn.asyncio import serve
        from hypercorn.config import Config as HyperConfig

        hcfg = HyperConfig()
        hcfg.bind = [f"{config.HOST}:{config.PORT}"]
        hcfg.quic_bind = [f"{config.HOST}:{config.PORT}"]
        hcfg.certfile = str(certfile)
        hcfg.keyfile = str(keyfile)
        hcfg.alpn_protocols = ["h2", "http/1.1", "h3"]
        hcfg.loglevel = "info"
        hcfg.accesslog = "-"
        os.environ["TELEGRAM_ARCHIVE_HTTP3_ACTIVE"] = "1"
        print(f"  HTTP/3:      ACTIVE (UDP/QUIC {config.HOST}:{config.PORT})")
        print(f"  HTTP/1.1/2:  ACTIVE (TCP {config.HOST}:{config.PORT})")
        asyncio.run(_serve_hypercorn(serve, app, hcfg))
        return

    os.environ.pop("TELEGRAM_ARCHIVE_HTTP3_ACTIVE", None)
    import uvicorn

    async def _serve_uvicorn():
        loop = asyncio.get_running_loop()
        server_config = uvicorn.Config(
            app,
            host=config.HOST,
            port=config.PORT,
            log_level="info",
            ssl_certfile=certfile,
            ssl_keyfile=keyfile,
        )
        server = uvicorn.Server(server_config)

        def _request_stop():
            # Run on the server loop; Uvicorn then completes ASGI lifespan shutdown.
            loop.call_soon_threadsafe(setattr, server, "should_exit", True)

        app.state.request_shutdown = _request_stop
        await server.serve()

    asyncio.run(_serve_uvicorn())


def _open_browser_when_ready() -> None:
    """Open the login page only after HTTPS responds, not merely after TCP binds.

    A listening socket can exist while the ASGI application is still starting;
    opening the browser at that point sends requests into an unavailable app.
    The untrusted context is limited to this readiness probe for the app's own
    local self-signed certificate; the browser still handles the normal page.
    """
    if not config.AUTO_OPEN_BROWSER:
        return
    import ssl
    import urllib.error
    import urllib.request
    url = f"https://127.0.0.1:{config.PORT}/login"
    context = ssl._create_unverified_context()
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
            try:
                with urllib.request.urlopen(request, timeout=1.5, context=context) as response:
                    response.read(1)
            except urllib.error.HTTPError:
                # Any HTTP response (including an auth/error status) proves the
                # application is serving requests; the browser can display it.
                pass
            import webbrowser
            webbrowser.open(url, new=2)
            return
        except (OSError, urllib.error.URLError, TimeoutError):
            time.sleep(0.5)
    print("  Browser auto-open skipped: HTTPS app did not become ready within 90 seconds.")


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
    from .core.instance_lock import AlreadyRunning, acquire as acquire_instance_lock
    try:
        acquire_instance_lock(config.USER_DATA_DIR)
    except AlreadyRunning as exc:
        print(f"  {exc}. Open https://127.0.0.1:{config.PORT}/login instead.")
        raise SystemExit(2)
    with _phase("cleanup"):
        remove_obsolete_files()
        try:
            cleanup_runtime_artifacts()
        except Exception as exc:
            print(f"  Runtime cleanup skipped: {type(exc).__name__}: {exc}")

    with _phase("data migration"):
        moved = config.migrate_legacy_data()
        for m in moved:
            print(f"  Migrated from previous version: {m}")
        write_default_config()
    with _phase("database open + schema checks"):
        db = Database()
    with _phase("asset store"):
        try:
            asset_result = db.hydrate_persistent_asset_store()
            if asset_result and not asset_result.get("skipped"):
                print(f"  Persistent asset export: {asset_result}")
        except Exception as exc:
            print(f"  Persistent asset export skipped: {type(exc).__name__}: {exc}")
    desktop = desktop_mode.enabled()
    with _phase("credentials + certificate"):
        ensure_web_credentials(db)
        if desktop:
            desktop_mode.prepare_credentials(db)   # local mode: no web login, no LAN certificate
            certfile = keyfile = None
        else:
            certfile, keyfile = ensure_certificate()
    with _phase("dependency check"):
        if desktop:
            print(f"  Archive X desktop backend {config.APP_VERSION} (loopback only); data: {config.USER_DATA_DIR}", flush=True)
        else:
            print_startup()
            print(f"  Process model: {startup_message()}")
    if config.AUTO_OPEN_BROWSER and not desktop:
        threading.Thread(target=_open_browser_when_ready, name="archive-x-browser-open", daemon=True).start()

    with _phase("application setup"):
        app = create_app(db=db)
    app.state.startup_phases = STARTUP_PHASES
    app.state.startup_t0 = STARTUP_T0
    try:
        _run_web_server(app, certfile, keyfile)
    finally:
        # main() owns the database: flush buffered events and close it last.
        db.close()


if __name__ == "__main__":
    main()
