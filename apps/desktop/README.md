# Archive X desktop app

A native desktop window (Tauri 2) around a frozen copy of the Archive X Python backend. No Python, no `.bat`, no
browser tab. See `docs/archive/Archive-X-MIGRATION-PLAN.md` section 9 for the full plan.

```
desktop/
  backend/       freezes telegram_archive into archive-x-backend (PyInstaller, one-folder)
  launcher-core/ Rust library: start the backend, read its ready line, health, graceful shutdown (tested)
  src-tauri/     the Tauri app: splash window, starts the backend, opens the one-time session URL, quits cleanly
  ui/splash.html shown while the backend starts
  scripts/       fetch-ffmpeg.sh (pinned, SHA-256 checked)
```

## How it runs
1. The shell shows `splash.html` and starts the backend with `ARCHIVE_X_DESKTOP=1`, a fresh 256-bit
   `ARCHIVE_X_LAUNCH_SECRET`, its own PID as `ARCHIVE_X_PARENT_PID`, and the bundled ffmpeg folder.
2. The backend binds `127.0.0.1` on a free port (plain HTTP, no certificate, no LAN) and prints
   `ARCHIVE_X_READY {"port":N,...}`.
3. The shell navigates the window to `http://127.0.0.1:N/desktop/session?token=<secret>`. The backend creates the
   normal web session (no login screen in local mode) and a guard cookie, then the page opens `/`.
4. Every request needs the secret (header or cookie), including the WebSocket. Other programs cannot use the API.
5. On quit the shell calls `POST /desktop/shutdown` (jobs are checkpointed); if the shell dies, the backend notices
   and exits by itself.

## Build and test (Linux shown; Windows uses `build.ps1`)
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
Linux build prerequisites: `libwebkit2gtk-4.1-dev libgtk-3-dev libayatana-appindicator3-dev librsvg2-dev patchelf`.
Windows needs the WebView2 runtime (the installer embeds its bootstrapper) and the MSVC build tools.

## Packaging notes (found by building a real bundle)
- Tauri `bundle.resources` must map the backend as a **directory** (`"../backend/dist/archive-x-backend": "backend"`).
  The `**/*` glob form flattens the folder and drops `_internal/`, so an installed app could not start its backend,
  while a development run (which uses `ARCHIVE_X_BACKEND_EXE`) would still work. Always test the installed layout:
  `npx @tauri-apps/cli@2 build --debug --bundles deb`, extract the `.deb`, run `usr/bin/archive-x-desktop` with no
  environment override.

## Status
Verified on Linux: frozen backend passes the desktop-mode tests, the Rust launcher passes against it, the Tauri shell
compiles and opens the real UI in a WebKitGTK window. **Not yet verified on Windows:** WebView2, NSIS/MSI installers,
Defender/SmartScreen behaviour, code signing, auto-update. Those are phases D1-D3 in the plan.
