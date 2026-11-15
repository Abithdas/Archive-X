# ax-server: the front door

An HTTP server (hyper, rustls) that sits in front of the Python backend. It serves the web app's files, answers some routes itself, and forwards everything else to Python.

**Owns:** request routing; the cross-site guard and the desktop launch guard for routes it answers (`guard.rs`, `desktop_guard.rs`); the session check (`auth.rs`); the proxy to the backend with a pool of kept connections and a retry (`proxy.rs`); static files and response compression (`static_files.rs`, `optimize.rs`).

**Routes answered here** (switched on by name with `--native-routes`; the desktop app passes the list, `ARCHIVE_X_NATIVE_ROUTES=none` turns them off):
- reads: `native.rs`, `native_library.rs` (Videos list, tags, photos list, studio details, lists by channel/studio/tag, performers, face groups);
- writes: `native_play.rs` (watch progress, favourites, scene markers; docs/adr/0007);
- `names.rs` (performer name list, uses `ax-names`), `learning.rs` (face learning, uses `ax-faces`).

**Rule:** a route that cannot answer a particular request returns `None` and the request goes on to Python unchanged, so a route can be partial. Every answer carries `X-Served-By`.

**Tests:** `tests/` (one file per area, each with its own fake backend); the Python-versus-Rust comparison tools are in `tools/parity/`.

**Direction (docs/adr/0009):** handlers stay thin; business logic moves into the crates below this one as they are written.
