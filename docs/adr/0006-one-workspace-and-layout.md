# 0006. One Cargo workspace, and an apps / crates / native / packaging layout

**Status:** accepted and done 15 November 2026 (the Windows installer builds on it).

**Decision.** One Cargo workspace at the repository root (one lock file, one `target/`). `apps/web` is the web app, `apps/desktop` the Tauri shell, `crates/` every Rust library and the front door, `native/axplayer` the C++ video engine, `packaging/` the installer scripts and the freezing of the Python backend. The Python tree stays where it is until it is deleted.

**Consequences.** Dependency versions are shared and scanned once. Moves were done as moves only, one commit each, with the paths that pointed at the old places.
