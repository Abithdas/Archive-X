# Plan: migrate the Archive X backend to Rust

> History, not a guide. Paths here (`rust/`, `desktop/`, `frontend/`) are from before the workspace move (ADR 0006): they are now `crates/`, `apps/desktop/` and `apps/web/`.

Status: a plan only. Nothing has been ported. Sizes are relative (S, M, L, XL); I have not estimated calendar time because it depends on who does the work.

> **The current working plan is `plan.md`** (clean-up, reorganisation, and the phase order from here). This file is kept as the record of what was done and measured.

## 0. Progress

| Phase | Status |
|-------|--------|
| 0. Prove the risky parts | **Not done.** The `grammers` Telegram trial needs a real account; the `ort` face/tag checks and the Whisper speed check were not run here. SQLite shared between Python and Rust is exercised by the front door tests only indirectly (the front door does not open the database yet) |
| 1. Safety net | **Done:** frozen API (`docs/api/`), parity harness (`tools/parity/compare.py`, 0 differences against itself and against the Rust front door on 85 routes). Not done: more backend tests, the Python clean-ups |
| 2. Rust front door | **Done and verified here** (details below), then hardened: timeouts, connection cap, graceful shutdown, pooled connections, HTTPS/HTTP2 test, CI jobs (`front-door`, `front-door-windows`, `python-lint` in `.github/workflows/ci.yml`, not yet run on GitHub). Not done: bundling the binary in the installer and a Windows run |
| 3. Database and read-only routes | **Started:** `ax-db` (read-only pool), the session lookup, the cross-site guard and the 401 behaviour exist and are tested, but no route is answered by Rust yet. See the round-3 results below |
| 3 (first route) | **Done for one route:** `GET /api/photos/channels` is answered by Rust when switched on (off by default); see "Round 4" below |
| 3 (second route) | **Done:** `GET /api/performers`, with the pattern for routes that decline unusual requests; see "Round 5" |
| 3 (third route) | **Done:** `GET /api/photos/faces/groups?suggestions=0` (the People row of the Photos page); see "Round 6" |
| 3 (routes 4 to 6) | **Done:** the channel, studio and tag video lists; see "Round 7" |
| 4 to 9 | Not started |

Phase 2 results (`rust/crates/ax-server`, 12 Rust tests, clippy clean; Python side `TrustedProxyMiddleware`, 2 tests):

- Parity (latest run): **248 of 248** requests identical through the front door: the 85 parameter-free GET routes, the routes with path parameters filled from real ids (`--discover`), and 38 list-route query variants (`--queries`). The status mix of that run: 205 answers of 200, 13 each of 400 and 404, 11 of 503 (Telegram not connected), the rest 307, 403, 409. Earlier: 85 of 85 GET routes identical through the front door (status, content type, JSON body); 14 static paths identical (status, cache and security headers, bytes), including the path-traversal attempts.
- The real React app works through it: the sign-in form, all eight pages, the live WebSocket, and a 300 MB response arrives byte for byte while the front door stays at about 19 MB of memory (it streams).
- Security: a request arriving from a non-loopback address is still treated as remote by the "only from this PC" checks, and forged `X-Forwarded-For` / peer / key headers change nothing.
- Speed (release build, one connection, loopback): app bundle 5.7 ms to 0.9 ms and 580 KB to 186 KB on the wire (gzip); app shell 3.5 ms to 0.4 ms; a proxied API call costs about 0.55 ms more than going to Python directly (it was 1.4 ms before backend connections were reused).
- The release binary is 4.1 MB.
- Desktop launcher: `LaunchOptions.front_door` (opt-in); a real end-to-end test (`cargo test -p archive-x-launcher-core real_backend_with_front_door -- --ignored`) passes with the Python backend in desktop mode: health, app shell, the backend's 403 guard, the session hand-off and a graceful shutdown all work through the front door.
- Known differences: Python counts a plain page load of `/login` against its login rate limit and the front door does not; Python advertises HTTP/3 (`alt-svc`) and the front door removes that header because it does not speak HTTP/3.

### Round 7: the video lists, and a query that multiplies rows

- **`/api/channels/{id}/videos`, `/api/studios/{id}/videos`, `/api/tags/{id}/videos`** answered by Rust (`channel_videos`, `studio_videos`, `tag_videos`); the performer list and the studio list with `performer_id` stay in Python.
- **22 requests byte-identical to Python** on the big library (found, not found, odd forms), 6 new Rust tests (58 in all), full read parity run: see the result in the README.
- **The real finding is a Python query problem:** the four-way join multiplies rows (136,965 intermediate rows for 18 videos); separate sub-queries give the same lists in 2.4 ms instead of 125 ms, and 32 times faster end to end for the channel list (191 ms to 6 ms; 54 times at 8 clients). The same rewrite can be applied in Python.
- **A measurement caveat found on the way:** the server re-creates the `archive_x_library_media` view itself, and the cost of reading through it changed between runs (Python 175 ms, later 2 ms). The tag and studio list speed-ups are therefore reported as a range; parity (the thing that matters for correctness) held in both states.

### Round 6: the People row, and a performance trap found by measuring

- **`GET /api/photos/faces/groups?suggestions=0[&top=N][&channels=...]`** is answered by Rust (`--native-routes face_groups_fast`); the full form that guesses who an unnamed group might be (`suggestions` missing or not `0`) stays in Python, as do odd `top` values.
- **Byte-identical to Python** on the big library (602 groups) for 17 request forms (cut sizes, channel lists with repeats and blanks, an unknown channel, odd numbers); 5 new Rust tests (52 in all) with a hand-checkable dataset (two clusters of one performer shown as one person with the negative performer id, scope counts that ignore the channel filter, SUM of nothing being 0).
- **The real Photos page** on the big library loads both its channel list and its people row from Rust (`200 rust` for both), no errors.
- **The trap:** the first measurement showed Rust **slower** than Python (0.8 times with one client and 0.1 times with 4 to 8). Cause: Rust's read connections used SQLite's default 2 MB cache; Python's use a 32 MB cache and a 256 MB memory map. With the same three pragmas in `ax-db`: 1.2 times (1 client), 1.0 (4), 1.3 (8). This route is dominated by four COUNT queries over 40,000 photos that SQLite runs identically for both, so there is little to win except not blocking Python. Performer list re-measured with the pragmas: still 15 to 32 times with 4 to 8 clients.
- A possible later optimisation (not done, it changes behaviour): cache those four counts until the database changes (`PRAGMA data_version`).

### Round 5: measuring on a big library, and the second route

- **Method:** the fixture is tiny, so every route looked fast. `tools/parity/seed_load.py` builds a big library (4,013 performers, 1,207 studios, 40,032 photos, 603 face groups, 8,409 faces) and `compare.py --timing` ranks the routes by Python's cost: see the table in `rust/README.md`. The ranking found `/api/performers/duplicate-audit` at 33 seconds (a maintenance route, not on the normal page loads) and `/api/photos/faces/groups` at 1.2 s (the People page).
- **`GET /api/performers`** (the Performers page and the pickers) is now answerable by Rust (`--native-routes performers_list`). Byte-identical to Python on the 4,013-performer library for 10 request forms, including the 4.4 MB full list, the brief list, every kind of `limit`, and the cases Rust passes back to Python. 6 new Rust tests (47 in all).
- **Speed, one client:** full list 162 ms to 58 ms (2.8 times), brief list 105 to 56 ms (1.9 times), `limit=50` 14.7 to 13.7 ms (no change). Most of the time is SQLite running the same query, so the single-request gain is modest.
- **Speed, concurrent clients (the real result):** with 8 parallel clients asking for the brief list, Python managed 1.1 requests per second with a median of 7.6 seconds per request; the Rust route managed 35 requests per second with a median of 217 ms: about 32 times the throughput. Python's requests queue behind its single process and database lock; the Rust readers do not. (The 16-client run was stopped because Python was too slow to finish it in reasonable time.)
- Corollary for the plan: the case for porting read routes rests on concurrency and on keeping the app responsive while Python is busy (a running upload, a scan), more than on single-request speed.

### Round 4: the first route answered by Rust (results)

- **`GET /api/photos/channels`**, the channel list the Photos page loads on every visit. Off by default (`--native-routes photos_channels` turns it on); every answer has `X-Served-By`.
- **Identical to Python:** same body byte for byte (1,617 bytes in the check, including an orphan channel), same ETag, same gzip and `Vary`, same `304`, same `401`; 41 Rust tests (8 new for this route: exact body, login rules, switch, caching, session keep-alive, unreadable database, empty library, unknown route name).
- **The real Photos page** loads its channel list from the Rust route with no console errors.
- **Speed:** 0.63 ms against 2.08 ms (3.3 times faster) on the small fixture, one connection; the gap should widen under load because Python's single process and database lock are not involved.
- **Resilience:** with the Python backend stopped, this route and the app shell still answer; other routes give a clear `502`.
- **Session keep-alive** (the open item from round 3): implemented and tested against a fake backend (the call carries the cookie and the signed address, at most once per interval). Not yet watched over a real 10-minute window against Python's slow database write.
- A test setup mistake was caught on the way and is worth remembering: SQLite's `COLLATE NOCASE` folds only ASCII letters, so a title starting with an accented capital sorts after `Z`; Rust and Python agree because both run the same SQL on the same engine.
- Small known imperfection: when the front door itself answers `502` for a stopped backend, it is labelled `X-Served-By: python`.

### Round 3: database, sessions, guard, write parity (results)

- **Sessions are in SQLite already** (`web_sessions`, hashed tokens), so Rust can check logins without any change to Python. Verified against the real backend: the cookie Python issued is accepted (valid 30 days), a changed one is refused, and after Python's logout the same cookie is refused at once.
- **Rust reads while Python writes** (the Phase 0 SQLite item): 8 concurrent readers against a writer committing constantly, in WAL mode: no "database is locked", counts never go backwards, and after the writer stops the readers see every row.
- **The cross-site guard** agrees with Python's middleware on all 7,938 generated cases; deliberately breaking either of two rules makes the test fail (so the test is not vacuous).
- **Schema contract:** 102 tables snapshotted; Python test and CI step fail when the Python schema changes without the snapshot being reviewed.
- **Write parity harness** (`compare_writes.py`) with 22 scenarios (quick edit, bulk mapping, favourites, markers, face merge/naming, refusal cases, a body that is not JSON, a cross-site POST): two independent Python servers on fresh fixtures give identical answers and identical row changes (one quick edit alone changes 44 rows in 24 tables). This is the check every ported write route has to pass.
- `tools/parity/seed_fixture.py` makes the comparison database reproducible (two builds are identical).
- Not done: a Rust-side route (Step 6), the session "touch" for requests Rust answers alone, bundling, Windows.

### What the hardening round found (and fixed)

- **Parity found a real bug:** the front door served the app shell for `/stream-cached/...` and `/stream-compat/...` (Python routes that are not under `/stream`). It now serves a path itself only when a file exists in the web folder or it is one of the app's own pages, and sends everything else to Python.
- **A test found a real gap:** a client that connects and sends nothing was never disconnected, because the combined HTTP/1-and-2 server waits for the first bytes before applying any timeout. The protocol is now chosen up front (HTTP/2 only when TLS negotiated it).
- Still open from the review: sessions (Step 3), the database crate (Step 4), write-route parity (run the same POST against two copies of one database and diff the response and the changed rows), bundling into the installer.

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

### Round 8: the performer name list, written in Rust from the start

From here new backend features are written in Rust rather than Python.

- **`ax-names`** (new crate): keeps the id, name, aliases and picture link of every performer StashDB and ThePornDB know, in its own SQLite file (`performer_names.db` next to the Python database; Python's database is not touched). Search matches part of a name, any word order, aliases and accents, exact names first. StashDB is refreshed incrementally (`updated_at` greater than the last run, 40 per page); ThePornDB, which has no "changed since", is re-read in full about weekly. Both resume at the page they stopped on, record a failure and retry later. 11 tests with a fake provider.
- **Front door routes** (`--native-routes name_search,name_list`, on by default in the desktop app): `GET /api/photos/faces/performers/provider-search` answers from the local copy and hands unknown names to Python's live search; `GET .../name-list` reports size and last refresh; `POST .../name-list/sync` refreshes now (same-site check like Python's). A refresh runs two minutes after start and then every six hours. 5 integration tests.
- **No second login system, no secrets stored here:** who is signed in is asked of the backend (`/api/app/ping`), and the provider keys and data folder come from the backend's `/internal/provider-keys`, which needs the launch secret and a loopback caller. The front door answers `/internal/...` from clients with 404 and never forwards the secret header.
- **Not verified:** the StashDB and ThePornDB request shapes (`queryPerformers` with `updated_at`/`sort`, ThePornDB `performers?page=&per_page=`) follow the providers' documented APIs but have not been run against the live services from here.

### Round 9: face learning (Rust)

- **`ax-faces`** (new crate, pure maths on 128-number face vectors): a profile per named person from their confirmed faces with the odd ones trimmed away (twice, with a floor on the spread so ordinary different looks are kept), up to 24 varied example faces, and a "not this person" memory. It gives (1) **outliers**: the person's confirmed faces, least alike first, flagged when below a line taken from the person's own spread (kept between 0.25 and 0.45), (2) **suggestions**: faces in unnamed groups scoring at least 0.46 against the examples (strong from 0.56), at least 0.04 further from the closest removed face than from the examples, and not closer to another named person's centre. 9 tests on synthetic faces (planted mistakes ranked first, a few wrong faces do not pull the profile, removed faces and their look-alikes are not offered, a face that fits another person goes to them).
- **Routes** (`--native-routes face_learning`, on by default in the desktop app): `GET /api/photos/faces/learning/{outliers,suggestions}?performer_id=` and `.../status`, read-only on the Python database, cached until faces, groups or removal notes change. Login and database location come from the backend as for the name list. 5 integration tests on a real SQLite file.
- **Python glue (small):** taking a face out of a named person's group (Check faces, "Different person", a single split, a move to someone else) writes a row to `photo_face_negatives(detection_id, performer_id)`; moving a face to a person deletes their "not" row for it. 4 tests.
- **Interface:** Check faces uses the learned scores to order and flag faces, and shows "N faces in other groups look like this person" with tick and add. The People page shows how many confirmed and removed faces it is learning from.
- **Nothing is applied by itself.** Every suggestion waits for a click. Silent auto-assignment is left until the numbers have been checked on real data.
- **Not verified:** the thresholds (0.46, 0.56, margins) are starting values from SFace's published behaviour and the app's existing 0.42 and 0.46, tuned only on synthetic faces. They need checking against a real library; the accept/reject counts the interface could record would be the way to tune them.
