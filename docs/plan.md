# Archive X: clean-up, reorganisation and Rust migration plan

Written 14 November 2026, at commit `21182e0`. This is the working plan from here on. It replaces the phase list in `RUST-MIGRATION-PLAN.md` (which stays as the history of what was done and measured) and it adds the clean-up and the reorganisation that plan did not cover.

How to read it: Part 1 says where the code stands, with numbers measured for this plan. Part 2 is the rules. Part 3 is the clean-up, Part 4 the reorganisation, Part 5 the migration. Part 6 is the order to do it all in. Part 7 lists the decisions only you can make. Every step says **what** to do, **why**, **how**, and **done when**.

## Progress

| Step | State |
|------|-------|
| C1 one command runs every test, in CI | **Done** (`pytest tools/tests`, `justfile`, `backend-tests` job; first run in CI pending) |
| C2 remove the old start-up path | **Done** on 15 November 2026 (you answered: desktop app only): launcher scripts, `run.py`, Replit files, hosted mode, browser auto-open, the build output in version control. The generic server mode (HTTPS certificate, LAN address, web login) stays for now: tests, the parity harness and the front door still use it, and it goes with the Python backend |
| C3 part 1 route counter | **Done**, ships in the next release; part 2 (delete routes) waits for a week of its data |
| C4 remove unused Python | **Started** (also: the generic server mode with HTTPS, LAN access, HTTP/3 and certificates is gone): the watchdog guardian process (about 1,000 lines, never ran in the installed app) is removed together with its settings and the guardian row on the Operations page; the rest waits for C3 part 2 |
| C5 unused frontend code | **Done** (`knip` in CI) |
| C6 one version number | **Done** (`tools/release/write_version.py`, written by the release workflow) |
| C7 prune the documents | **Done** (`docs/README.md` is the index) |
| R2 one Cargo workspace | **Done** (root `Cargo.toml`, one `Cargo.lock`, one `target/`); the Windows installer build under it is checked by the next Desktop run |
| R1 folder layout | **Done in a reduced form**: `apps/web`, `apps/desktop`, `crates/` (all Rust libraries and the front door), `native/axplayer`, `packaging/`. The Python tree (`telegram_archive/`, `run.py`, `requirements.txt`) was **not** moved: it is being deleted, and moving it would touch every test for no lasting benefit |
| R4 standards (part) | **Rust done**: `rustfmt.toml` (wide lines, matching the existing style), the whole Rust code formatted in one mechanical commit, `cargo fmt --check` in CI and the `justfile`, `.editorconfig`. **Not done, on purpose**: Python formatting (the code is being deleted) and web formatting (no formatter or eslint config exists; adding one would reformat 29,000 lines for little gain) |
| R6 one CI pipeline | Skipped: the four workflows (CI, Desktop installer, native player engine, dependency scan) each have a different trigger and cost, so merging them would only make every push slower |
| R5 licence | Done: GPL-3.0-or-later (ADR 0008), LICENSE, THIRD-PARTY-NOTICES.md, shipped in the installer |
| R3 | Decided (ADR 0009) and every crate has a README; no code moved yet, crates are created when their first content arrives |
| Phase 0 onward | Not started |

---

## 1. Where the code stands

### 1.1 Size

| Area | Files | Lines | Language | Fate |
|------|------:|------:|----------|------|
| `telegram_archive/` (backend) | 282 | 101,100 | Python | Rewritten in Rust, then deleted |
| `frontend/src/` | 347 | 29,000 | TypeScript, React 19 | Kept. It only talks to the HTTP API |
| `rust/` (front door and new features) | 35 | 6,000 | Rust | Grows into the whole backend |
| `desktop/` (Tauri shell, launcher, native player) | 56 | 3,600 | Rust, C++ | Kept; merged into one workspace |
| `tools/` (tests, parity, dependency scan) | 48 | 3,500 | Python | Tests move next to what they test |
| `docs/` | 25 | 2,100 | Markdown | Pruned and indexed |

Backend by package (Python lines): core 21,900; engines 21,900; web 16,700; media_manager 10,200; metadata 8,200; playback 7,900; operations 7,300; jobs 3,000; telegram 2,000.

Largest files: `playback/stream_server.py` 5,486; `operations/automation.py` 4,312; `metadata/providers.py` 3,193; `core/database.py` 3,166; `media_manager/mm_vertical_mover.py` 2,907.

### 1.2 What Rust already does

| Crate | What it does | State |
|-------|--------------|-------|
| `ax-server` | Front door: serves the web app, forwards everything else to Python, answers the routes below | Shipped, on by default in the desktop app |
| `ax-db` | Read-only access to the Python database, session lookup | Shipped |
| `ax-names` | Local copy of StashDB and ThePornDB performer names, own database file, background refresh | Shipped in 30.2.2; never run against the real providers by me |
| `ax-faces` | Face learning: profiles, look-alikes, faces that look out of place | Shipped in 30.2.2; tuned on synthetic faces only |
| `launcher-core`, `src-tauri`, `native-player` | Desktop shell, backend launcher, native video player wrapper | Shipped |

Routes Rust can answer: `photos/channels`, `performers` (list), `photos/faces/groups` (fast form), channel/studio/tag video lists, the name search and name list, face learning. Six of these are still switched off by default; the desktop app turns on only the name and face-learning routes.

### 1.3 Problems measured for this plan

These are counts from tools, so they are **candidates to check, not a delete list**. The tools cannot see every use (a route called only by a script, a function looked up by name).

| Finding | Number | How it was measured |
|---------|-------:|---------------------|
| API routes with no caller in the React app or in Rust | 106 of 325 | Route paths searched in `frontend/src` and `rust/` |
| Python functions, methods and classes that look unused (outside `web/`) | 211 | `vulture`, 60% confidence |
| Frontend exports nobody imports | 40, plus 10 types and 1 dependency | `knip` |
| Broad `except Exception` handlers | 1,756 | text search |
| Errors swallowed and only counted | 661 call sites | `_swallowed_note` |
| Version numbers that disagree | 3 | Backend says `v29.5.400`, `tauri.conf.json` says `30.0.0`, `package.json` says `0.1.0`; the release is 30.2.2 |
| Cargo workspaces | 2 | `rust/` and `desktop/`, separate lock files and build folders |
| Python tests that one command can run | not all | Two files in `tools/tests` are scripts that need arguments and stop `pytest` from collecting the folder |
| Start-up paths | 2 | The desktop app, and the old `Archive-X.bat` / `Setup-And-Run.ps1` / `run.py` chain plus Replit files |
| Comments carrying old version tags (`V29.4.x`) | 122 | text search |

Where the unused routes are: AI assistant 16, playback 14, automation 12, Telegram 9, jobs 8, system 7, tags and logs 7, the test bridge 7, entity pages 6.

Two findings from the field logs matter for the plan: the watchdog guardian never ran in the installed app (1,700 lines with its supervisor), and the in-browser remux path for MKV, AVI and WMV is an unfinished stub.

---

## 2. Rules

These hold for every step. They are what keeps a long migration safe.

1. **The app works after every commit.** No step leaves a half-moved feature. Anything risky sits behind a switch that can be turned off without a new build (`--native-routes`, a setting, an environment variable).
2. **Three things stay compatible until the end:** the HTTP API the React app uses, the SQLite database file, and the data folder. That is what lets any step be rolled back.
3. **One writer per table group.** Rust may read any table. A table is written by Python or by Rust, never both. A table group changes owner in one step.
4. **New features are written in Rust.** Python gets bug fixes only. (The missing-pictures fetcher written on 14 November is the last exception; it moves in phase 8.)
5. **Delete, do not refactor, in Python.** Tidying Python that will be rewritten is wasted work. The only Python clean-up worth doing is removal, and the few changes that make porting easier.
6. **A route moves only when the parity harness shows the same answers** as Python on the seeded database (`tools/parity/compare.py`), and it ships switched off first.
7. **Nothing is called done without evidence.** Each step ends with a named check: a test, a parity run, a measured number, or a result from your PC. What could not be checked is written down as not checked.
8. **Secrets and logins stay in one place.** Until phase 4 moves them, Python owns the session and the provider keys, and Rust asks for them over `/internal/` routes that need the launch secret.

---

## 3. Clean-up: remove what is unused

Do this before more porting. Everything deleted here is code that will not have to be ported, read or tested again. Each step is its own commit so it can be reverted alone.

### Step C1. Make the safety net whole

- **What:** one command runs every test: Python, Rust, frontend. CI runs the same command.
- **Why:** deleting code without a full test run is guessing. Today the Python tests are run file by file because two script-style files break collection.
- **How:**
  1. Turn `tools/tests/test_duplicate_delete_thumbnail.py` and the other script-style file into real `pytest` tests (they take the app root from the repository, not from an argument).
  2. Make each test use its own data folder (several share one and fail depending on order; I hit this twice).
  3. Add a `python-tests` job to `ci.yml` that runs `pytest tools/tests`.
  4. Add a top-level task file (see R5) with `test`, `lint`, `build`.
- **Done when:** `pytest tools/tests` passes from a clean checkout, twice in a row, in CI.

### Step C2. Decide the old start-up path, then remove it

- **What:** `Archive-X.bat`, `Setup-And-Run.ps1`, `Ensure-Archive-X-LAN.ps1`, `README-START-HERE.txt`, `scripts/launcher/`, `scripts/setup/`, `.replit`, `scripts/replit-start.sh`, `docs/REPLIT.md`, `.archive-x-bytecode-version`, and the venv handling in `run.py`.
- **Why:** two ways to start the app means two sets of bugs. The guardian crash loop came from exactly this: code written for the script launcher running inside the installed app.
- **How:** needs your answer to question 1 in Part 7. If the desktop app is the only way you run it, delete the list above and the code that serves it (hosted mode, the LAN helper, the browser auto-open).
- **Done when:** the installed app starts and stops as before, and a search for `Setup-And-Run`, `replit` and `ARCHIVE_X_AUTO_OPEN_BROWSER` finds nothing.

### Step C3. Remove the routes nobody calls

- **What:** the 106 candidate routes.
- **Why:** each one is a route to port, secure and test. A third of the API may not be needed.
- **How:**
  1. Add a counter to the Python router that records which routes are hit (path template and count, no data). Ship it in a release.
  2. Use the app normally for a week, then send a logs zip. The counter is in it.
  3. A route with no caller in the code **and** no hits is deleted, with its handler and anything only it used. A route with hits stays and gets a note saying who calls it.
  4. Likely whole groups to go: the test bridge (`routes_test_bridge.py`), the AI "evidence" and "investigations" routes the React page does not use, the old-prefix redirects in `routes_frontend.py`.
- **Done when:** every remaining route has a known caller, the frozen API file in `docs/api/` is regenerated, and the parity run still passes.

### Step C4. Remove unused Python

- **What:** the 211 candidates, then whatever C2 and C3 leave orphaned.
- **How:** work package by package, smallest first. For each candidate: search for the name as text (it may be looked up by string), delete, run the tests. Run `vulture` again after C3, because removing routes orphans more code. Add `vulture` to CI with an allow-list so new dead code is caught.
- **Also remove:** the watchdog guardian and its supervisor if question 1 says desktop only (the desktop shell already watches the backend); the unfinished in-browser remux pipeline for MKV, AVI and WMV (the native player covers those files); `core/swallowed.py` call sites that guard nothing.
- **Done when:** `vulture` at 80% confidence reports nothing, and at 60% only the allow-list.

### Step C5. Remove unused frontend code

- **What:** the 40 exports, 10 types, the unused dependency, and the hidden `FaceGroups` component with its test.
- **How:** `npx knip`, delete, `tsc`, `vitest`, `npm run build`. Add `knip` to CI.
- **Done when:** `knip` reports nothing outside `dist/`.

### Step C6. One version number

- **What:** one source for the version, read by the backend, the desktop shell and the web app.
- **Why:** your logs say `v29.5.400` while the installed build is 30.2.2, which made the log useless for telling which build you ran.
- **How:** the release workflow already patches `tauri.conf.json`. Make it write a single `VERSION` file that the backend's `APP_VERSION`, `package.json` and the Cargo workspace read. Put the version and the commit in the log header and the About box.
- **Done when:** the log zip from a release shows that release's number.

### Step C7. Prune the documents

- **What:** `docs/desktop/` had 14 documents, several describing finished or abandoned work (`BRANCH-STATUS.md`, `Archive-X-MIGRATION-PLAN.md`, `Archive-X-UI-PARITY-CHECKLISTS.md`, `UI-IMPROVEMENT-PLAN.md`, `mockups/`).
- **How:** move finished plans to `docs/archive/`, delete what is wrong, keep what describes the app as it is. (The 122 old `V29.4.x` tags in Python comments are left alone: they are in code that will be deleted, and rule 5 says not to tidy it.)
- **Done when:** `docs/README.md` lists every document in one line each, and none describes something that no longer exists.

---

## 4. Reorganisation: a layout a new developer can read

Do this after the clean-up (less to move) and in one sitting per step (moves that drag on cause merge pain).

### Step R1. One repository layout

```
Archive-X/
  apps/
    desktop/        Tauri shell (today apps/desktop/src-tauri, apps/desktop/ui)
    web/            React app (today frontend/)
  crates/           every Rust library and the server
    ax-server/  ax-db/  ax-names/  ax-faces/
    ax-core/  ax-jobs/  ax-media/  ax-providers/  ax-telegram/  ax-ai/   (added by the migration)
    launcher-core/  native-player/
  native/
    axplayer/       the C++ DirectShow engine
  backend-py/       the Python backend (today telegram_archive/, run.py, requirements.txt); shrinks to nothing
  tests/            cross-language tests: parity harness, seeded fixtures
  tools/            release scripts, dependency scan
  docs/
  .github/
```

- **Why:** today the top level mixes launch scripts, two Rust trees and a Python package, and nothing says which parts ship.
- **How:** `git mv` only, one commit per top-level move, with the workflow paths, the Tauri resource paths and the PyInstaller spec updated in the same commit. No code changes in a move commit.
- **Done when:** the Windows release build passes on the new layout.

### Step R2. One Cargo workspace

- **What:** merge `rust/` and `desktop/` into one workspace at the root, one `Cargo.lock`, shared dependency versions under `[workspace.dependencies]`, shared lints under `[workspace.lints]`.
- **Why:** two lock files mean two versions of the same crate and two dependency scans. `ureq`, `rusqlite` and `serde_json` are already pinned separately.
- **Done when:** `cargo test --workspace` and `cargo clippy --workspace -- -D warnings` pass on Linux and Windows.

### Step R3. A shape for the Rust backend

Decide it now, before the bulk of the code arrives:

| Crate | Owns | Depends on |
|-------|------|------------|
| `ax-core` | Types shared by all: ids, errors, config, the data-folder layout | nothing of ours |
| `ax-db` | Schema, numbered migrations, typed queries, the single writer and the reader pool | `ax-core` |
| `ax-providers` | StashDB and ThePornDB clients (absorbs `ax-names`) | `ax-core`, `ax-db` |
| `ax-media` | ffmpeg and ffprobe, thumbnails, previews, image derivatives | `ax-core` |
| `ax-ai` | Faces, tags, captions (absorbs `ax-faces`) | `ax-core`, `ax-db`, `ax-media` |
| `ax-jobs` | Job table, lanes, pause, resume, cancel | `ax-core`, `ax-db` |
| `ax-telegram` | Session, download ranges, uploads, FloodWait | `ax-core`, `ax-db` |
| `ax-server` | HTTP only: routing, login, static files. No business logic | all of the above |

Rules: lower crates never depend on higher ones; `ax-server` handlers are thin (parse, call a crate, answer); every crate has a `README.md` saying what it owns; errors are typed (`thiserror`), never ignored.

### Step R4. Standards, enforced by tools

| Language | Format | Lint | In CI |
|----------|--------|------|-------|
| Rust | `rustfmt` | `clippy -D warnings` | yes (clippy already) |
| TypeScript | `prettier` | `eslint` (the config needs migrating to the flat format; it does not run today), `tsc`, `knip` | add |
| Python (while it lasts) | `ruff format` | `ruff`, `vulture` | add |
| C++ | `clang-format` | warnings as errors | add |

Add a `.editorconfig`, and a pre-commit hook that runs the formatters.

### Step R5. The files a professional repository has

- `README.md`: what the app is, how to build it, how to run the tests, the layout above.
- `CONTRIBUTING.md`: the rules in Part 2, the commit style, how to port a route.
- `CHANGELOG.md`: one entry per release, written from the commits (30.1.0 onward can be reconstructed).
- `LICENSE` and `THIRD-PARTY-NOTICES`: **needed before the repository goes public.** The installer ships LAV Filters and ffmpeg, which are GPL; that affects which licence the app can use. See question 3 in Part 7.
- `justfile` (or `Makefile`): `just test`, `just lint`, `just build`, `just release 30.3.0`.
- `docs/README.md`: the index of documents.
- `docs/adr/`: one short page per big decision (front door first; one writer per table; the native player's own window; new features in Rust). The reasons are in this conversation today and nowhere else.

### Step R6. One CI pipeline

Today there are four workflows that overlap. Target: `ci.yml` (lint, tests for all four languages, on every push), `release.yml` (the installer, on request), `sca.yml` (dependencies, weekly). The Windows engine test becomes a job in `ci.yml`.

---

## 5. Migration: Python to Rust

### 5.1 How one route or feature is moved

This recipe is the unit of work. It is what was done seven times already.

1. **Pick** the next item from the phase list. Read the Python handler and everything it calls.
2. **Write down its contract:** parameters, answers, error cases, which tables it reads and writes.
3. **Seed data** that exercises it in `tests/` and record Python's answers.
4. **Write the Rust version** in the right crate, with tests that can be checked by hand.
5. **Wire it** into `ax-server` behind a route name, switched off.
6. **Parity run:** same request to both, same status, headers and body. Requests the Rust version does not handle are declined and go to Python.
7. **Measure** on the big seeded library, one client and eight.
8. **Switch it on** in the desktop app in one release. Watch a week of logs.
9. **Delete the Python handler** and what only it used. Regenerate the frozen API file.

A write route adds two steps: move every writer of that table group in the same release (rule 3), and test that an interrupted write leaves the database consistent.

### 5.2 Phases

Sizes are relative (S, M, L, XL). I have not estimated calendar time; it depends on who does the work and how much review you want.

**Phase 0. Prove the risky parts (S). Still not done, and it decides the shape of the rest.**
- Telegram with `grammers`: sign in from the existing session, list a channel, download a byte range, upload a large file with a FloodWait in the middle, receive updates. Needs your account (question 2).
- Face, tag and caption models through ONNX Runtime and whisper.cpp: same results as Python on the same files.
- **If Telegram fails:** keep a small Python process that does only Telegram, and move everything else. Most of the benefit survives.

**Phase 1. Finish reading (M).** Switch on the six routes already written. Then port, in this order: `/api/library` (the Videos page, the most used route), video detail, performer and studio detail pages, photos list, search, settings reads, job and lane status. Exit: every page of the app loads with Python stopped except for actions.

**Phase 2. Streaming (L).** The stream server: range requests, the segment cache, file-reference refresh. It is 5,500 lines and the part you feel most; your logs show seconds lost here. It needs Telegram downloads, so it follows the Phase 0 result: in Rust if `grammers` works, otherwise Rust cache and HTTP in front of a Python fetcher. Exit: playback start and seek times measured before and after on your PC.

**Phase 3. Writes without Telegram (M).** One table group at a time: favourites and play history; markers; performer and studio edits; tags; face naming, merging, removal; settings. `ax-db` becomes the single writer for each group as it moves, with numbered migrations. Exit: Python writes none of these tables.

**Phase 4. Login and sessions (S).** Password check (`argon2`, same hash format), session cookie, the cross-site guard for all routes. The `/internal/` bridge goes away. Exit: the login page works with Python stopped.

**Phase 5. Jobs (L).** Job table, lanes, pause, resume, cancel, as `ax-jobs`. Then the engines that need no Telegram: thumbnails, previews, contact sheets, image derivatives, duplicate fingerprints. Exit: those jobs run and resume after a restart with Python stopped.

**Phase 6. Local AI (M).** Face detection and vectors, content tags, look tags, captions. This is where a better face model is adopted (SCRFD + ArcFace through ONNX Runtime), with a re-analysis job and retuned thresholds. Exit: Find people runs in Rust and existing named people survive.

**Phase 7. Telegram (XL).** Session and sign-in; channel scans; delete and move with verification; the parallel uploader with resume; the Transfer Manager and Media Manager workflows (about 10,000 lines of procedural code: port by workflow, not file by file). Exit: a full transfer runs in Rust and resumes after being killed.

**Phase 8. Providers and automation (L).** Metadata matching, filename parsing, artwork (including the missing-pictures fetcher), then automation, RSS and torrents if you keep them (question 4): 7,300 lines that only one page uses.

**Phase 9. Remove Python (S).** Delete `backend-py/`, PyInstaller, `requirements.txt`, the proxy. Link the server into the desktop process. Re-measure installer size, start-up time and memory.

### 5.3 Two tracks that run alongside

- **Native player.** Picture works in its own window (your test on 30.2.2). Next: captions drawn by the player in an overlay window; automatic choice of the native player for files the browser cannot decode (needs the codec read up front); then a decision on making it the main player. It is already Rust and C++, so it does not wait for the phases.
- **Face learning.** Check the thresholds against your real corrections, record accepted and rejected suggestions, then allow automatic adding of strong matches.

---

## 6. Order of work

| # | Step | Size | Needs from you |
|--:|------|:----:|----------------|
| 1 | Release 30.2.3 with the fixes since 30.2.2 | S | Your go-ahead |
| 2 | C1: one command runs every test, in CI | S | |
| 3 | C6: one version number | S | |
| 4 | C3 part 1: route counter, shipped in a release | S | A week of normal use, then a logs zip |
| 5 | C2: remove the old start-up path | M | Answer to question 1 |
| 6 | C5, C7: frontend and document clean-up | S | |
| 7 | C3 part 2 and C4: delete unused routes and Python | M | The logs zip from step 4 |
| 8 | R1, R2: new layout, one Cargo workspace | M | Nothing; one release to prove the build |
| 9 | R3 to R6: crate shape, standards, repository files, one CI | M | Answer to question 3 |
| 10 | Phase 0: the Telegram and model trials | S | Question 2: your account for the trial |
| 11 | Phase 1: reading in Rust | M | Ported, all off by default, 302/302 parity on the seeded fixture: `/api/library` (no search text), `/api/tags` (no search text), `/api/studios/{id}/metadata`, `/api/photos` (channels, sort, paging). Not ported on purpose: job and lane status (live Python memory, belongs with the job engine, Phase 4), performer metadata (Python writes first), studio family and career counts (Unicode case-folding rules, provider calls), searches (full-text index). Not switched on: no speed gain measured on the fixture and no real-library run yet |
| 12 | Phases 2 to 9, in order | L to XL | A release and a week of logs after each. Phase 4 is mostly moot since ADR 0005 (the desktop app has no web login: the window gets its session from the launch hand-off); what remained is done: the name list and face learning check the session in the database instead of asking Python, and the front door applies the desktop launch guard to everything it answers. Phase 3 started out of order (it needs no Telegram): watch history (`/api/play/progress`, `/api/play/history`) is written by Rust, ADR 0007, entity favourites and scene markers; 40/40 write scenarios identical, off by default |

Steps 2, 3, 5 and 6 do not depend on each other and can go in any order. Step 7 must follow step 4. Step 8 should follow step 7, so less code is moved.

---

## 7. Decisions that are yours

1. **Is the desktop app the only way you run Archive X?** If yes, the script launcher, Replit files, hosted mode and the guardian are deleted. If you still open it from another device on your network or run the script version, they stay and need fixing instead.
2. **May the Telegram trial use your account?** It needs a real sign-in. It would run on your PC from a small test program I prepare; nothing is uploaded except to a test channel you choose.
3. **Which licence, and when does the repository go public?** The in-app updater only works once it is public. Shipping LAV Filters and ffmpeg (GPL) with the installer limits the choice.
4. **Do you use automation, RSS and torrents?** They are 7,300 lines and 12 of their routes have no caller in the React app. Dropping them removes a whole phase.
5. **Which unused features should stay?** The route counter (step 4) gives the list; you say which of the unused ones you still want.

---

## 8. What this plan does not promise

- Telegram's speed limits and waits are Telegram's. Rust does not change them.
- The models already run in native code. Rust makes them easier to ship, not much faster.
- The candidate counts in 1.3 will shrink when checked; some "unused" code is used in ways a search cannot see.
- I cannot run the Windows installer, your Telegram account or your library here. Every phase ends with a release that you try, and its result decides the next step.
