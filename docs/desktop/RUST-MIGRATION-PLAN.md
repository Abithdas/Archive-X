# Plan: migrate the Archive X backend to Rust

Status: a plan only. Nothing has been ported. Sizes are relative (S, M, L, XL); I have not estimated calendar time because it depends on who does the work.

## 0. Progress

| Phase | Status |
|-------|--------|
| 0. Prove the risky parts | **Not done.** The `grammers` Telegram trial needs a real account; the `ort` face/tag checks and the Whisper speed check were not run here. SQLite shared between Python and Rust is exercised by the front door tests only indirectly (the front door does not open the database yet) |
| 1. Safety net | **Done:** frozen API (`docs/desktop/api/`), parity harness (`tools/parity/compare.py`, 0 differences against itself and against the Rust front door on 85 routes). Not done: more backend tests, the Python clean-ups |
| 2. Rust front door | **Done and verified here** (details below). Not done: bundling the binary in the installer and the GitHub Actions build, and a Windows run |
| 3 to 9 | Not started |

Phase 2 results (`rust/crates/ax-server`, 12 Rust tests, clippy clean; Python side `TrustedProxyMiddleware`, 2 tests):

- 85 of 85 GET routes identical through the front door (status, content type, JSON body); 14 static paths identical (status, cache and security headers, bytes), including the path-traversal attempts.
- The real React app works through it: the sign-in form, all eight pages, the live WebSocket, and a 300 MB response arrives byte for byte while the front door stays at about 19 MB of memory (it streams).
- Security: a request arriving from a non-loopback address is still treated as remote by the "only from this PC" checks, and forged `X-Forwarded-For` / peer / key headers change nothing.
- Speed (release build, one connection, loopback): app bundle 5.7 ms to 0.9 ms and 580 KB to 186 KB on the wire (gzip); app shell 3.5 ms to 0.4 ms; a proxied API call costs about 0.55 ms more than going to Python directly (it was 1.4 ms before backend connections were reused).
- The release binary is 4.1 MB.
- Desktop launcher: `LaunchOptions.front_door` (opt-in); a real end-to-end test (`cargo test -p archive-x-launcher-core real_backend_with_front_door -- --ignored`) passes with the Python backend in desktop mode: health, app shell, the backend's 403 guard, the session hand-off and a graceful shutdown all work through the front door.
- Known differences: Python counts a plain page load of `/login` against its login rate limit and the front door does not; Python advertises HTTP/3 (`alt-svc`) and the front door removes that header because it does not speak HTTP/3.

## 1. What is being migrated

| Part | Today | After |
|------|-------|-------|
| Frontend | React 19 + TypeScript (about 1,150 tests) | **Unchanged.** It only talks to the HTTP API |
| Desktop shell | Tauri (already Rust), starts a PyInstaller build of the Python backend as a sidecar | Same shell; the sidecar becomes a Rust binary, later linked in-process |
| Backend | Python, about 100,000 lines in 187 files: FastAPI, Telethon, SQLite, PyAV/ffmpeg, OpenCV, faster-whisper, NudeNet, CLIP | Rust |

Backend size by area (lines): engines 21,700; core 21,600; web 16,600; media_manager 10,200; metadata 8,100; playback 7,800; operations 7,300; jobs 3,000; telegram 2,000.

**The rule for the whole migration:** the HTTP API, the SQLite file and the data folder stay compatible at every step, so the React app and existing user data keep working, and any step can be rolled back.

## 2. Approach: replace it piece by piece, never all at once

A full rewrite followed by one big switch is the highest-risk option for a 100,000-line app with only about 60 backend tests. Instead:

1. A new Rust server becomes the front door on the same port.
2. It answers the routes that have been ported and **forwards everything else to the Python backend**, which keeps running behind it.
3. Route groups move across one at a time. Each is checked against the Python version before the switch.
4. When nothing is forwarded any more, Python is removed.

Both processes open the same SQLite database (WAL mode is already on, which allows this). During the transition only one side may *write* a given table group; the plan below moves writers in whole groups to respect that.

## 3. Technology choices

| Need | Python today | Rust choice | Risk |
|------|--------------|-------------|------|
| HTTP server, WebSocket | FastAPI, Hypercorn | `axum` on `tokio`, `tower-http` (gzip, static files) | Low |
| HTTPS with the app's own certificate | cryptography | `rustls`, `rcgen` | Low |
| HTTP/3 | hypercorn[h3] | Drop at first; add with the `h3` crate later if it is really used | Low |
| Database | sqlite3, one connection + lock | `rusqlite` with a small pool: one writer, several readers | Low |
| Login, password hashing | argon2-cffi | `argon2` crate (same hash format, so existing passwords keep working) | Low |
| Outbound HTTP (metadata providers) | aiohttp | `reqwest` | Low |
| JSON, config | stdlib | `serde`, `serde_json` | Low |
| Telegram client | Telethon 1.45 | `grammers` | **High** (see 3.1) |
| Video probing, thumbnails, remux | PyAV + ffmpeg/ffprobe programs | Call the ffmpeg/ffprobe programs first; `ffmpeg-next` only where needed | Medium |
| Images | Pillow | `image`, `fast_image_resize` | Low |
| Faces (YuNet, SFace) | OpenCV | `ort` (ONNX Runtime): both models are ONNX files | Medium |
| Content tags (NudeNet 320n) | nudenet | `ort`: it is an ONNX model | Medium |
| Look suggestions (CLIP) | onnxruntime + tokenizers | `ort` + `tokenizers` (already a Rust library) | Low |
| Captions (Whisper) | faster-whisper | `whisper-rs` (whisper.cpp) | Medium: different engine, so speed and accuracy must be re-measured |
| Torrents | own engine + qBittorrent supervisor | Keep supervising qBittorrent over its web API at first | Medium |
| Logging, diagnostics | print + custom | `tracing` | Low |
| Errors | broad `except` | `thiserror` types; no silent ignores | Low |

### 3.1 The Telegram layer is the deciding risk

Everything valuable in this app depends on Telegram, and the Python code leans on Telethon in detail: a custom parallel uploader that reaches into Telethon's private sender objects, FloodWait handling, file-reference refresh, range downloads for streaming, and an encrypted session file.

`grammers` is the main Rust Telegram library. It is younger than Telethon and is not yet at version 1.0, so it must be proven before anything is committed to it. That is Phase 0 below. If it fails the checks, the fallback is to **keep a small Python process that does only Telegram**, with Rust doing everything else and talking to it over a local socket. That still delivers most of the benefit.

## 4. Phases

### Phase 0. Prove the risky parts (S, and it decides whether to go ahead)
Small throwaway programs, no app code:
1. `grammers`: sign in with the existing account by converting the Telethon session (data centre + auth key), list a channel, download a byte range of a large video, upload a 2 GB file in parts with a FloodWait in the middle, forward and delete messages, and receive updates.
2. `ort`: run YuNet + SFace on 50 photos and compare the face vectors with the Python output (they must match closely, or every saved face group becomes useless).
3. `ort`: NudeNet and CLIP give the same tags as Python on the same photos.
4. `whisper-rs`: speed and quality of the "small" model on a slow PC against faster-whisper.
5. Rust and Python reading and writing the same SQLite file at once without lock errors.

**Go/no-go:** if 1 fails, use the "Python does only Telegram" fallback. If 2 fails, plan a one-time re-analysis of faces. Write the results down before continuing.

### Phase 1. Safety net (M)
1. Freeze the API: export the OpenAPI description from FastAPI and commit it.
2. Build a **comparison harness**: it sends the same request to Python and to Rust and diffs status, headers that matter, and JSON. Record real responses from a seeded database as fixtures.
3. Raise backend tests where behaviour is subtle (deletes, moves, transfers, upload resume, face grouping). Existing tests with a fake Telegram client are the model.
4. Finish the cheap Python clean-ups that make porting easier: delete dead code, number the database migrations, and replace `print` with logging. Do not start the big Python refactors; they would be thrown away.

### Phase 2. Rust front door (M)
1. New workspace next to the Tauri one: `crates/ax-server`, `ax-db`, `ax-core`, later `ax-telegram`, `ax-media`, `ax-ai`.
2. `ax-server`: TLS with the existing certificate, the same session cookie and CSRF/Origin rules, gzip, static files for the React build (including the service worker rules), and a reverse proxy for every other path, WebSocket and streaming included.
3. Tauri starts `ax-server`, which starts Python on a private port.
4. Ship this alone first. Nothing has changed for the user except the front door; this proves packaging, certificates, logging and updates.

### Phase 3. Database and read-only routes (L)
1. `ax-db`: typed queries for the existing schema, a migration runner that understands the current schema version, one writer + reader pool.
2. Port the read routes, in this order (most used, least risk first): status and settings reads; library list, search and filters; performers and studios lists and detail pages; photos list, channels, face groups, content tags; job and lane status.
3. Each route is switched only after the comparison harness shows identical answers on the seeded database.
4. Expected visible gain here: faster lists and no more whole-server freezes on slow queries.

### Phase 4. Writes that do not touch Telegram (M)
Favourites, markers, play history, quick edit, bulk mapping, performer/studio edits, tag management, appearance and other settings, face naming/merging/splitting, content-tag filters. Move each table group's writer in one step so that only one side writes it.

### Phase 5. Jobs and background work (L)
1. `ax-jobs`: the job table, lanes, pause/resume/cancel, the resource governor and watchdogs, written on `tokio` tasks with explicit cancellation.
2. Run the Rust job manager for Rust-owned job types only; Python keeps its own until its engines move.
3. Port the pure-local engines first: thumbnails and contact sheets (ffmpeg), preview frames, image derivatives, the photo cache, duplicate fingerprinting.

### Phase 6. Local AI (M)
Faces, content tags, look suggestions with `ort`; captions with `whisper-rs`; the model catalog and checked downloads. Keep the stored vector format so existing face groups survive (or run the planned re-analysis from Phase 0).

### Phase 7. Telegram (XL, the core of the work)
In this order, each behind a setting so it can be switched back:
1. Session, sign-in (code, password, QR), connection supervision.
2. Reading: channel list, history scans, the photo and video index.
3. Streaming: range downloads, the segment cache, file-reference refresh, the compatibility/HLS paths.
4. Deleting and moving messages with verification (the handlers that now have fake-client tests).
5. Uploading: the parallel part uploader with resume, covers, split files, captions, FloodWait policy.
6. The Transfer Manager and the Media Manager workflows (about 10,000 lines of mostly procedural code; port by workflow, not file by file).

### Phase 8. Metadata providers and automation (L)
Provider clients (`reqwest`), matching and filename parsing (pure logic, port with table-driven tests), image candidates, the automation pipeline, RSS, and the qBittorrent supervisor.

### Phase 9. Remove Python (S)
Delete the proxy, the PyInstaller build and `requirements.txt`; link the server into the Tauri process; update the GitHub Actions workflow to a pure Rust build; re-measure installer size, start-up time and memory.

## 5. What you gain, and what you do not

**Gains:** one small installer instead of a bundled Python runtime; much lower memory use; no global interpreter lock, so streaming, uploads and scans stop competing; whole classes of the bugs found recently become compile errors (undefined names, a blocking call on the event loop, a handler in the wrong thread).

**Not gains:** Telegram speed limits and FloodWaits are set by Telegram, not by the language. Whisper and the image models already run in native code, so they will not get much faster.

**Costs:** it is a large project; for a long time two backends must be kept working; `grammers` may need fixes contributed upstream; anyone maintaining the app must know Rust.

## 6. A smaller alternative worth considering

If the goals are speed and stability rather than "no Python", most of the benefit comes from Phases 0 to 3 plus streaming: a Rust front door serving static files, the read routes and video streaming, with Python kept for Telegram workflows, uploads and metadata. That is perhaps a third of the work and avoids the highest risk.

## 7. Suggested first three steps
1. Phase 0 item 1 (the `grammers` trial). It decides the shape of everything else.
2. Phase 1 items 1 and 2 (frozen API + comparison harness).
3. Phase 2 (the Rust front door), shipped on its own.
