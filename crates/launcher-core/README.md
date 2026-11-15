# launcher-core: starting the backend

Starts, supervises and stops the Python backend and the front door as child processes. No GUI code, so it is tested on its own.

**Owns:** the per-launch secret and proxy key, the environment the backend gets (`ARCHIVE_X_DESKTOP`, `ARCHIVE_X_LAUNCH_SECRET`, `ARCHIVE_X_PROXY_KEY`, data folder), reading the ready line, the front door's command line and the list of routes it answers (`native_routes()`, overridable with `ARCHIVE_X_NATIVE_ROUTES`), and stopping both cleanly.

Used by `apps/desktop/src-tauri`.
