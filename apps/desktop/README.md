# Archive X desktop app

A native Windows window (Tauri 2) around the Archive X backend and the Rust front door. No Python install, no browser tab.

```
apps/desktop/
  src-tauri/   the Tauri app: splash window, starts the backend and front door, opens the one-time session URL,
               quits cleanly, in-app updates
  ui/          splash.html, shown while the backend starts
crates/launcher-core/   Rust library: start the backend, read its ready line, health checks, graceful shutdown,
                        and (on Windows) the kill-on-close job object that holds every child process
packaging/backend/      freezes telegram_archive into archive-x-backend (PyInstaller, one folder)
packaging/scripts/      fetch-ffmpeg.sh (pinned, SHA-256 checked), make_latest_json.py (update manifest)
```

## How it runs

1. The shell shows `splash.html` and starts the backend with `ARCHIVE_X_DESKTOP=1`, a fresh 256-bit `ARCHIVE_X_LAUNCH_SECRET`, its own PID as `ARCHIVE_X_PARENT_PID`, and the bundled ffmpeg folder. The backend opens in **SFW** every time.
2. The backend binds `127.0.0.1` on a free port (plain HTTP) and prints `ARCHIVE_X_READY {"port":N,...}`.
3. The shell starts the front door (`ax-server`) in front of it, also on a free loopback port, and navigates the window to `/desktop/session?token=<secret>`. The backend creates the web session (no login screen) and a guard cookie, then the page opens `/`.
4. Every request needs the secret (header or cookie), including the WebSocket. Other programs on the PC cannot use the API.
5. On quit the shell calls `POST /desktop/shutdown` (jobs are checkpointed) and waits up to 12 seconds; the job object then ends anything left. If the shell dies first, the backend notices and exits by itself.

Releases are built only by the [Desktop workflow](../../.github/workflows/desktop.yml); with a version number it publishes the release itself. Updates are described in [docs/updates.md](../../docs/updates.md).

## Build and test (Linux shown; Windows uses `packaging/backend/build.ps1`)

```bash
# 1. freeze the backend
packaging/backend/build.sh                      # -> packaging/backend/dist/archive-x-backend/

# 2. test the backend (source or frozen) end to end
python tools/tests/test_desktop_mode.py
python tools/tests/test_desktop_mode.py packaging/backend/dist/archive-x-backend/archive-x-backend

# 3. test the launcher, including against the real frozen backend
cargo test -p archive-x-launcher-core
ARCHIVE_X_BACKEND_EXE=$PWD/packaging/backend/dist/archive-x-backend/archive-x-backend \
  cargo test -p archive-x-launcher-core --test launcher -- --ignored --nocapture

# 4. run the app from the build tree
ARCHIVE_X_BACKEND_EXE=$PWD/packaging/backend/dist/archive-x-backend/archive-x-backend cargo run -p archive-x-desktop
```

Linux build prerequisites: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf`. Windows needs the WebView2 runtime (the installer embeds its bootstrapper) and the MSVC build tools.

## Packaging notes (found by building a real bundle)

- Tauri `bundle.resources` must map the backend as a **directory**. The `**/*` glob form flattens the folder and drops `_internal/`, so an installed app could not start its backend while a development run (which uses `ARCHIVE_X_BACKEND_EXE`) would still work. Always test the installed layout, with no environment override.
- The installer stops `archive-x-backend.exe` and `ax-server.exe` before copying files, or Windows refuses to overwrite the runtime DLLs.

## Status

Verified on Linux: the frozen backend passes the desktop-mode tests, the launcher passes against it, the shell opens the real UI. The Windows installer is built and checked by the Desktop workflow. **Not verified by anyone on a real PC in every release:** WebView2 behaviour, SmartScreen, code signing, and the Windows job object (it is type-checked, not run).
