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
  - `tools/` was deleted in recent commits. `docs/architecture.md` still depends on `tools/gen_visibility_sql.py`, `tools/parity/compare.py` and `tools/tests/test_sfw_leaks.py`. These are the existing parity and SFW-leak safety nets. **Restored in this PR** from `2f1a06f^`.
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

Each phase ends with: replay at 100% for the routes moved, SFW leak test green, `cargo test` green, then the route group is switched on by default on the development branch (no intermediate public release; Python is deleted only in the final release).

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
6. Delete the Python code for the group only in Phase 6, once everything passes together.

## 7. Done criteria
- `app replay` 100% passed, fallback 0.
- No Python in the installer; one `ax-server` binary plus the Tauri shell and player.
- SFW leak test covers every GET route and passes.
- An upgrade from the last Python release keeps the library, favourites, resume points, settings, settings. Telegram needs a single re-login.

## 8. Open questions for you
1. ~~Is a one-time Telegram re-login acceptable?~~ Yes (confirmed).
2. ~~Intermediate release (Phases 0 to 2 plus playback)?~~ No. Decided: no intermediate release; the Python backend is removed in a single release after Phase 6. Route groups are still switched on one at a time behind `--native-routes` for testing, but are not shipped as a half-migrated product.
3. ~~Restore `tools/`?~~ Done in this PR (the parity, SFW-leak and visibility tools CI depends on). The deleted `docs/plan.md`, `docs/adr/` and `docs/security/` are not restored; this plan replaces plan.md and the ADRs should be rewritten in Phase 0.

## 9. Progress log

Update this table as units move. A unit is "done" when its Rust tests pass and its Python original has been compared on the same inputs.

| Date | Phase | Unit | Rust | Status | Notes |
|---|---|---|---|---|---|
| 2026-11-20 | 0 | `rust-anything analyze --whole-app` | `docs/rust-analysis.md` | done | The tool maps by package (22 units), so everything under `telegram_archive/` shows as wave 0 and the `fastapi` and dynamic-code "blocked" flags are expected: we replace FastAPI with axum, not wrap it. Use the file-level order in section 5 instead of its wave numbers. The tool's own `bridge` and `diff` loop is not used for ports of whole packages. |
| 2026-11-20 | 1 | `core/retry_policy.py` | `ax-core::retry_policy` | done | The caller passes error type name, message and `seconds` instead of an exception object, plus an explicit `jitter`. Differs from Python only in the `reason` text when Telegram sends a flood wait too big for i64 (the value is capped). |
| 2026-11-20 | 1 | `core/safe_paths.py` | `ax-core::safe_paths` | done | Own non-strict `realpath`. Also accepts a filesystem-root base, which Python rejects by accident. |
| 2026-11-20 | 1 | `core/sql_batches.py` | `ax-core::sql_batches` | done | |
| 2026-11-20 | 1 | `core/duration_filter.py` | `ax-core::duration_filter` | done | `message_duration_seconds` stays with the Telegram client port. Compared against Python on 17 inputs. |
| 2026-11-20 | 1 | `core/resolution_filter.py` | `ax-core::resolution_filter` | done | Compared against Python on 8 selections and 7 sizes. |
| 2026-11-20 | 1 | `core/net_errors.py` | `ax-core::net_errors` | done | The caller names the error family (`Kind`) instead of passing an exception object; the same text patterns apply. |
| 2026-11-20 | 1 | `core/size_optimizer.py` (pure parts) | `ax-core::size_optimizer` | partial | Bitrate maths, next-pass bitrate, duration fallback and the FFmpeg command line are ported and compared with Python. The async encode loop waits for the media worker port. |
| | 1 | `core/session_cookie.py` | - | deferred | It is a thin wrapper over FastAPI's response object, so it is rewritten with the axum auth layer in Phase 2. |
| 2026-11-20 | 1 | `core/encrypted_files.py` | `ax-core::vault` | done (Windows DPAPI unrun) | Same file format both ways: a blob written by Python opens in Rust, and a blob written by Rust opens in Python, with the same derived key. Per-user key file, "no key but encrypted data exists" refusal, plaintext upgrade on first read, atomic write and the backup purpose table are ported. Differences: JSON is written compactly, and paths come from a `Vault` struct instead of `config`. The Windows DPAPI branch compiles and passes clippy for `x86_64-pc-windows-msvc` but has not been run on Windows; test it on a Windows machine before relying on it (a key written by Python's DPAPI code must open in Rust). |
| 2026-11-20 | 0 | Parity harness | `tools/parity/run_parity.py` | done for reads | One command starts two seeded Python backends (and `ax-server` in front of one) and runs `compare.py` in SFW and NSFW mode: 475 GET routes, 130 of them answered by Rust. `--self-check` compares the two Python copies with each other (0 differences), so a difference means a real one. Not yet wired in: the write routes (`compare_writes.py` needs a signed-in user). The fixture's rows are all NSFW, so SFW mode shows an empty library; adding some SFW rows to `seed_fixture.py` would test the SFW side better. |
| 2026-11-20 | 2 | `/api/performers` list | `ax-server::native` | fixed | The harness found that the Rust list lacked the `movies` and `series` counts Python added later. Now ported (`title_counts`). |
| 2026-11-20 | 2 | channel/studio video lists in SFW | `ax-server::native` | fixed | A channel or studio that exists but belongs to the other mode now answers the guard's "Not found" like Python, and odd channel-id spellings (`-001001`) go to Python. |
| 2026-11-20 | 2 | `GET /api/studios` | `ax-server::native` (`studios_list`) | done | Plain list only. A search, an odd limit, an empty list, or a video that names a studio but has no link yet (Python's `_backfill_studios_from_media` repairs that first) all go to Python. Matches Python on the parity harness (NSFW pass now has 81 routes answered by Rust). 5 new tests in `tests/native_studios.rs`. |
| 2026-11-20 | 2 | `metadata/tag_taxonomy.py` | `ax-core::tag_taxonomy` | done | Port of the whole module. Normalisation uses NFKC, full case folding (`caseless`) and a `\w` that follows Python's `isalnum` by Unicode category, not the `regex` crate's `\w` (which also accepts combining marks). Python's `round(x, 5)` is reproduced through exact decimal formatting. Pinned by 585 KiB of vectors made from the Python module (`tools/parity/gen_taxonomy_vectors.py`, checked in CI): names with accents, ß, İ, final sigma, combining marks, full-width forms, superscripts, emoji; every taxonomy field, the ranked order and the map order match. |
| 2026-11-20 | 2 | `GET /api/tag-taxonomy` | `ax-server::native` (`tag_taxonomy`) | done | Answers from the current mode's tags on every request. Python keeps its answer for 30 s and shares it between SFW and NSFW, so a mode switch can show the other mode's tags for up to 30 s; the Rust route does not copy that. The parity harness compares only the `?refresh=1` form because of that cache. 3 new tests in `tests/native_tag_taxonomy.rs`. |
| 2026-11-20 | 0 | Parity harness: writes and SFW data | `tools/parity/run_parity.py --writes` | done | The fixture now has two SFW channels (with their videos and photos), four SFW performers, two studios and two tags, so the SFW pass lists real items (Rust answers 76 routes there, 79 in NSFW, all identical). `--writes` replays the 51 state-changing requests of `writes.json` against both servers and compares the answers and the rows each one changed: identical. The harness is now a CI job (`parity`): Python against Python, then Python against the Rust front door. |
| 2026-11-20 | 2 | `GET /api/performers/{id}/videos` | `ax-server::native` (`performer_videos`) | done | The performer page, with the same-person merge, the optional `studio_id` filter, the provider page found in `provider_payload`, and the file-name-first display name (the performer page puts the file name before the title; the studio page does the opposite). Python first rewrites the "Unknown Performer" row to a local-only profile on every request; Rust only checks whether that rewrite would change anything (`unknown_performer_is_tidy`, compared with Python on 11 row states) and hands the request to Python when it would. A `studio_id` that is not a plain number also goes to Python. 6 new tests in `tests/native_performer_videos.rs`. Harness: identical in SFW and NSFW, 84 routes answered by Rust. |
| 2026-11-20 | 0 | Parity harness fixes | `tools/parity/compare.py` | done | Discovery now asks both servers (it only asked Python, whose studio list repairs data on first use, so Python ran ahead of Rust). Cache-busting `?v=<time>` stamps are normalised. Three runs in a row: 0 differences in 285 + 295 reads and 51 writes. |
| 2026-11-20 | 2 | `GET /api/performers/{id}/edit`, `/api/studios/{id}/edit`, `/api/performers/{id}/metadata` | `ax-server::native` (`entity_edit`, `performer_metadata`) | done | The edit forms are plain reads; the performer metadata page shares the Unknown Performer check with the performer video list. 5 new tests in `tests/native_entity_pages.rs`. |
| 2026-11-20 | 2 | Content guard on hidden performers and studios | `ax-server::native` (`entity_hidden`) | fixed | New harness lines request ids the fixture marks NSFW in both modes. In SFW that showed `/api/studios/{id}/metadata` answering "Studio not found" where Python's content guard answers "Not found" (a real, pre-existing difference). Fixed for it and used by the new routes. Rust now answers 100 routes in SFW and 104 in NSFW, all identical. |
| 2026-11-20 | 2 | `GET /api/photos/{channel}/{message}/thumbnail`, `/preview`, `/image` | `ax-server::native_photos` | done | The Photos grid asks for dozens of these at once. A file already on disk is sent from the front door with Starlette's `FileResponse` headers and ETag (MD5 of "<mtime as a Python float>-<size>", checked against Python's formula on a real file; headers and bytes compared with Python's over three rounds of nine requests). Left to Python: a thumbnail or preview that has to be rendered (or is older than its original), an original that has to be fetched, a row with no recorded cache path (Python writes it), a `Range` request, an image type other than JPEG, PNG, WebP or GIF, and any path outside the user-data and cache folders. A photo hidden by the content mode answers the guard's "Not found". Needs the backend's `/internal/provider-keys` to report `user_data_dir` and `cache_dir` (added; an older backend without them just means Python answers). 6 new tests in `tests/native_photos.rs`. |
| 2026-11-20 | 0 | Parity harness: shared proxy key, photo twice-requests | `tools/parity/run_parity.py` | done | Both backends now run with the same `ARCHIVE_X_PROXY_KEY` as the front door (the desktop shell does the same), so the front door's `/internal/` questions are answered; without it every native file route silently fell back to Python. Photo URLs are requested twice so the second answer comes from the cache. Background bookkeeping table `telegram_caption_sync` is ignored in the write comparison (it flaked once in ten runs). Rust answers 113 routes in SFW and 117 in NSFW, all identical, plus 51 writes. |
| 2026-11-20 | 2 | `GET /api/source-channels` | `ax-server::native` (`source_channels`) | done | A management list: it reads the real `channels` table (not the mode's view) and, only in SFW, leaves out the channels that are in the library as NSFW. 1 test. |
| 2026-11-20 | 2 | `GET /api/studios/{id}/family` | `ax-server::native` (`studio_family`) | done | Parent, child and sister labels from `entity_metadata`. The fixture now carries provider rows for the studios in the shapes the route reads (parent as object under details, at the top, as text, numeric id, upper-case provider, text that is not JSON), so the harness checks the relations, not just an empty answer; an extra Python run on 5 hand-made rows gave the same answer as the Rust test. Values that are not scalars (lists, objects, fractions) in id and name positions go to Python. `ax-core::text::casefold` added. 4 tests. Rust answers 125 routes in SFW and 130 in NSFW, all identical. |
| 2026-11-20 | 2 | `GET /api/channels` | `ax-server::native` (`library_channels`) | done | The Library's channel list: channels with ready videos, channels that only appear through their videos (a local folder named after its folder, an unnamed Telegram channel in NSFW only; a folder that is gone is not listed), and the registry's counts (present, prepared, pending, moved away, last scan). The fixture now has a registry, an orphan channel and a local folder, so the harness checks all of that (Rust and Python agree in both modes, and on `last_scan_status: null` for a channel with a registry but no scan row). A query that fails (a missing table) goes to Python so it reports the problem. 2 tests. Rust answers 131 routes in NSFW, all identical. |
