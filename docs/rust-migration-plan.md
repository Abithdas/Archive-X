# Archive X: plan to move the Python backend to Rust

Status: draft for review. Written 2026-11-20. Nothing here has been run yet; Phase 0 is where the numbers below get verified.

Goal: replace `telegram_archive/` (about 109k lines of Python, 218 files) with Rust, so the app is Tauri shell + Rust backend + web app + C++ player, with no Python runtime in the installer. The Rust front door (`crates/ax-server`) and `ax-db` already exist and already answer a growing list of routes, so this is a continuation of the existing strangler migration, with [rust-anything](https://github.com/erkamyaman/rust-anything) added as the proof layer.

## 1. What rust-anything does and does not do for us

It records what the original does from the outside, then replays that recording against a rewrite and fails on any difference.

| Need | Tool support | Notes for Archive X |
|---|---|---|
| HTTP API parity | `app record --http`, `app replay`, `app front-door` (axum proxy), `app wire --route`, `app lift` | Best fit. Our backend is an HTTP server and we already have an axum front door. |
| Side effects (webhooks, mail, DB rows) | `--effects http,smtp`, `--db-reset`, `--db-dump` | The DB dump covers our SQLite state. Telegram traffic is MTProto, not HTTP, so it needs our own sink (see 4.1). |
| Per-function ports | `bridge`, `record`, `diff`, `gate` (pyo3 bridge) | Use only for pure leaf modules (parsers, retry policy, path safety). Not for the stateful engines. |
| Dependency mapping | `analyze . --whole-app` | Gives the port order, units that must move together, and crates with no equivalent. |
| Cross-session work | `rust/MAP.md`, `HANDOFF.md`, `DECISIONS.md` | Fits a migration this size done over many sessions. |
| **Not covered** | Anything the recording does not exercise | It states this itself ("What it can't catch"). Telegram, playback seeking, jobs and the AI assistant need our own recorded scenarios. |

Caveat: `app lift` assumes the end state is a single pure-Rust binary built from `rust/app`. We already have a workspace, so we use `front-door`/`wire`/replay as the safety net but keep our own crate layout (decision D1) instead of letting it scaffold `rust/app`.

## 2. Where we are today

- Rust already: `ax-server` (static files, proxy, streaming, ~15 named native routes in `native.rs`: library/photos/performers/tags lists, favourites, play progress/history, local/cached streams), `ax-db` (read-only SQLite + the tables Rust owns, SFW/NSFW views from `visibility.sql`), `ax-names`, `ax-faces`, `launcher-core`, `native-player`, the Tauri shell.
- Python still owns: all writes outside Rust's tables, Telegram (Telethon), jobs, every engine, metadata providers, playback segment cache, automation (RSS/qBittorrent), AI assistant, media manager, auth/sessions, backup, diagnostics.
- Largest Python files (migration risk): `playback/stream_server.py` 5.5k, `operations/automation.py` 4.3k, `metadata/providers.py` 3.4k, `core/database.py` 3.3k, `media_manager/mm_vertical_mover.py` 2.7k, `web/routes_library.py` 2.6k, `engines/metadata_channel_pipeline.py` 2.5k, `operations/torrent_engine.py` 2.2k.
- Gaps found while reading the repo:
  - `tools/` was deleted in recent commits. `docs/architecture.md` still depends on `tools/gen_visibility_sql.py`, `tools/parity/compare.py` and `tools/tests/test_sfw_leaks.py`. These are the existing parity and SFW-leak safety nets. **Restore them from git history before any migration work** (`git log --diff-filter=D -- tools`).
  - `docs/plan.md` and `docs/adr/` (ADRs 0002 to 0010) are also deleted but still referenced. Restore or rewrite them; decisions below should be recorded as ADRs again.

## 3. Decisions to settle first (grilling round)

rust-anything's whole-app flow starts by writing each decision as an ADR. Recommended answers are given; these need your confirmation before Phase 1.

| # | Decision | Recommendation |
|---|---|---|
| D1 | Crate layout | Keep the existing workspace. Add crates per domain (`ax-core`, `ax-telegram`, `ax-jobs`, `ax-metadata`, `ax-playback`, `ax-automation`, `ax-ai`) and make `ax-server` the single binary. Use rust-anything only for recording, replay and gating. |
| D2 | Telegram client | `grammers` (pure Rust MTProto). **Decided: a one-time re-login is acceptable**, so the Telethon session does not have to be imported. Importing it stays a nice-to-have if the spike is cheap (see 4.1). |
| D3 | Web framework | `axum` on `hyper` (already hyper-based in `ax-server`). |
| D4 | SQLite | `rusqlite` (already used). Keep the schema and in-place migrations; port `database.py` migrations verbatim. |
| D5 | Single process | Keep one process with explicit shared state (`Arc` services), not global singletons, so tests no longer depend on module state. |
| D6 | Video/image work | PyAV and Pillow replaced by `ffmpeg-next` or the existing `axplayer` C++ engine for decode, and the `image` crate for derivatives. Decide whether ffmpeg is bundled or shelled out. |
| D7 | Local AI | Keep the llama-style runtime as a child process (already is); port only the orchestration, tools and safety rules. |
| D8 | Cutover | Per-route-group switches (`--native-routes`) stay until a group is 100% in Rust; Python is removed last, behind a single release. |
| D9 | Data compatibility | Same SQLite file and encrypted credentials format, so upgrading users keep everything. |

## 4. Risks that decide the schedule

### 4.1 Telegram
- Decided: a one-time re-login on first launch of the Rust build is acceptable, so session import is optional. Phase 0 spike: try importing Telethon's `.session` (SQLite, auth key + DC) into `grammers`; if it is not quick, skip it. Either way the upgrade must show a clear sign-in prompt, keep the library, favourites, resume points and settings, and delete the old Python session file only after the new login works.
- `TransferGovernor` is the only FloodWait/concurrency gate; the Rust port must keep that invariant (one gate, no engine does its own).
- Parity cannot be recorded against live Telegram. Build a **fake Telegram sink** (a trait `TelegramApi` with a recorded-fixture implementation) used by both the Python recording harness and the Rust tests.

### 4.2 Playback (`stream_server.py`)
- 512 KiB segments, sparse disk cache, read-ahead following the playhead, priority scheduler that cancels stale reads on seek. Behaviour depends on timing, so byte-for-byte replay is not enough; add scenario tests (sequential read, seek, concurrent seeks, cache hit/miss).
- `ax-server` already streams local and cached files; the Telegram-backed path is what remains.

### 4.3 SFW/NSFW enforcement
- The rule lives in SQLite TEMP views plus `content_guard`. Any new Rust read path must use `ax_db` filtered connections. Keep `test_sfw_leaks` running against both backends at every phase, and extend it to every GET route (the docs admit it does not cover all of them).

### 4.4 Semantic traps rust-anything flags for Python sources
- Python `int` is unbounded, `str` is Unicode code points, dict order is insertion, float formatting differs, `datetime` naive/aware rules. The metadata filename parser and `screen_identify` title comparison are exposed to all of these; record with the tool's trap generators plus real file names from the library.

### 4.5 Oversized modules
- `exec()`-joined `mm_*.py` fragments cannot be analysed by static tools; flatten them in Python first (Phase 0) so they can be recorded and ported as ordinary units.

## 5. Phases

Each phase ends with: replay at 100% for the routes moved, SFW leak test green, `cargo test` green, then the route group switched on by default for one release before the Python code is deleted.

### Phase 0: foundations (about 1 to 2 weeks)
1. Install the tool (`cargo install rust-anything`) and run `rust-anything analyze . --whole-app` on this repo. Commit the output as the port map and port order; reconcile it with section 5.
2. Restore `tools/` (parity, visibility generator, SFW leak test) and the deleted docs/ADRs from git history.
3. Build the **recording harness**: a script that starts the Python backend in desktop mode with a seeded SQLite fixture (SFW and NSFW rows, local folders, fake channels), the fake Telegram sink, and `ARCHIVE_X_LAUNCH_SECRET`, then runs `rust-anything app record . --http --db-reset ... --db-dump ...`. Inputs: the route list from `routes_*.py`, plus the existing parity corpus.
4. `app replay` against Python itself must pass 100% twice in a row (proves the recording is deterministic; wildcards for ids/timestamps via `--normalize`).
5. Spikes: optional Telethon session import into `grammers` (re-login is acceptable); ffmpeg bundling; flatten `mm_*.py`.
6. Settle D1 to D9 and write them as ADRs.

### Phase 1: pure leaf logic (2 to 3 weeks)
Units with no I/O or only filesystem: `retry_policy`, `safe_paths`, `sql_batches`, `encrypted_files`, `session_cookie`, `net_errors`, `duration_filter`, `resolution_filter`, `size_optimizer`, filename/fingerprint parsing in `metadata/`, title matching in `screen_identify`.
Use `bridge` → `record` → port → `diff` → `gate` per unit (pyo3 shadow lets Python keep calling the Rust version while the rest is migrated). Output: `ax-core`, `ax-metadata` (parsers only).

### Phase 2: database and read routes (3 to 4 weeks)
1. Port `core/database.py`: connection setup, WAL, `db.lock`, `db.reading()`, all in-place migrations (one test per migration from every historical schema version).
2. Extend `ax-db` so Rust can write every table, then move ownership table by table (settings, media, channels, photo library, entities, tags, watch history, sessions, jobs state).
3. Move read-only route groups into `ax-server` native routes: `routes_library`, `routes_entities`, `routes_entity_pages`, `routes_tags_logs`, `routes_experience`, `routes_frontend`, `routes_system`. Each group: record, port, `app wire --route`, replay, switch on.
4. Port content-mode, auth, sessions, desktop-session handshake (`core/desktop_mode.py`, `core/auth.py`, `content_guard.py`).

### Phase 3: Telegram and jobs (4 to 6 weeks)
1. `ax-telegram`: `TelegramService` on grammers, encrypted session storage, the governor, the fake sink.
2. `ax-jobs`: `JobManager`/`JobOrchestrator` (admission, dependencies, retries, leases; INTERRUPTED-on-restart rule), durable in SQLite.
3. Engines in order of dependence: delivery ledger, channel sync, thumbnail/snapshot/preview caches, uploader, sender, transfer manager, face clustering, local scanner (`local_scanner`, `local_videos`, `local_upload_bridge`).
4. Routes: `routes_telegram`, `routes_jobs`, `routes_local`, `routes_media`, `routes_tracks`.

### Phase 4: metadata and playback (4 to 6 weeks)
1. `ax-metadata`: providers (TMDB, OMDb, TVmaze, Cinemeta, OpenSubtitles, NSFW providers), `metadata_cache` TTLs (30 days hits, 7 days misses, refused keys never cached), review flow, `metadata_channel_pipeline`.
2. `ax-playback`: segment fetcher, sparse cache, read-ahead, scheduler, signed URLs for external players, the playback session controller.
3. Routes: `routes_metadata_assets`, `routes_screen`, `routes_playback`, `routes_playback_compat`, `routes_content`.

### Phase 5: automation, media manager, AI (3 to 5 weeks)
1. `ax-automation`: RSS ingest, qBittorrent supervisor, torrent engine (`reqwest`, `feed-rs`).
2. Media manager (`mm_*`): router, mover, archiver, duplicate finder, organizer.
3. `ax-ai`: assistant, tools, safety rules, model downloads, local runtime supervision. Settings, backup, diagnostics, log bundle.

### Phase 6: cutover and removal (1 to 2 weeks)
1. Run `app replay` against the pure-Rust server, fallback rate 0, no route still proxied to Python.
2. Remove the Python proxy path and `--upstream`; remove Python from `packaging/backend`, `pyproject.toml`, `requirements.txt`, the PyInstaller-style frozen backend; update `launcher-core` to start only `ax-server`.
3. Installer size, startup time, memory comparison; release as a major version with an upgrade test from the last Python release (same data folder; the user signs in to Telegram once).

Rough total: 18 to 28 weeks of focused work, dominated by Phases 3 and 4. Phases are sequential on the critical path (database before everything; Telegram before jobs and playback) but routes within a phase can be ported in parallel by separate sessions, claiming one unit each in `MAP.md`.

## 6. Working method per unit

1. Claim the unit in `MAP.md`.
2. Record the original (cases saved under `rust/app-cases/` or `tools/parity/`), run twice, wildcards for volatile fields.
3. Write the Rust; for routes, add a named route in `ax-server/native.rs` or the new crate; run the routes with `--native-routes <name>` so each can be switched off.
4. `app replay` (must be 100% for the unit) plus SFW leak test plus `cargo fmt`, `clippy -D warnings`, `cargo test`.
5. Commit, update `MAP.md` and `HANDOFF.md`.
6. Only when the whole group has been on by default for a release, delete the Python code.

## 7. Done criteria
- `app replay` 100% passed, fallback 0.
- No Python in the installer; one `ax-server` binary plus the Tauri shell and player.
- SFW leak test covers every GET route and passes.
- An upgrade from the last Python release keeps the library, favourites, resume points, settings, settings. Telegram needs a single re-login.

## 8. Open questions for you
1. ~~Is a one-time Telegram re-login acceptable?~~ Yes (confirmed).
2. Is a 4 to 6 month timeline realistic, or should we stop at "Python only for Telegram + AI" (Phases 0 to 2 plus playback) as an intermediate release?
3. Should I restore `tools/` and the deleted ADRs/plan from history as the first commit?
