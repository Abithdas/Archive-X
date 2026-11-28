# Archive X: architecture

Archive X is a **Windows desktop app** that turns Telegram channels and local folders into one private media library. Everything runs on the user's PC: the library, caches and settings stay local, and Telegram is used only to read and move channel media. Desktop only is a decision (ADR 0005): there is no hosted mode, LAN access, HTTPS certificate or web login.

Sizes (18 November 2026): about 106,000 lines of Python in `telegram_archive/` (218 files), 456 TypeScript files in `apps/web/src/`, about 11,900 lines of Rust in `crates/` and `apps/desktop/`. How the Rust side is growing is in [plan.md](plan.md) and [adr/](adr/README.md).

## 1. Processes

```
 Tauri window (apps/desktop)  ──►  ax-server (Rust front door)  ──►  Python backend (telegram_archive)
   WebView2, splash, tray            serves apps/web, answers            FastAPI on uvicorn, Telethon,
   starts and stops the rest         native routes, proxies the rest     jobs, metadata, playback, SQLite
                                                │
                                                └──► SQLite file (read-only; Rust writes only the tables it owns)
```

| Part | What it does |
|---|---|
| `apps/desktop` (Tauri 2) | Splash window, starts the backend and front door through `crates/launcher-core`, opens the window on a one-time session URL, quits cleanly. On Windows every child process is held by a kill-on-close job object, so quitting leaves nothing behind. |
| `crates/ax-server` | The front door. Serves the built web app, answers the routes listed in `native.rs`, and forwards everything else to Python. It also sends plain video files itself (ADR 0010). |
| `telegram_archive/` | The backend: one process, one Telethon session, one SQLite database, durable jobs. Binds `127.0.0.1` on a free port with a per-launch secret; other programs cannot use its API. |

Python is deliberately **one process**: Telegram, jobs, playback and watchers are process-local singletons.

## 2. Backend layers (`telegram_archive/`)

- **HTTP (`web/`).** `routes.py` builds one router from the `routes_*.py` modules (library, entities, playback, local folders, content, screen identification, telegram, settings, jobs, automation, AI, and others). Handlers call `require_auth()` (or `require_app_auth` / `require_bridge`); the exceptions are the public pages and assets, `/health`, the login routes and two `/internal/*` routes gated by a per-launch key. The router itself has no default dependency, so a new route must call it. Middleware, outermost first: security headers, gzip/ETag, browser mutation guard (Origin check), API rate limit, request correlation, content filter (section 5).
- **Telegram (`telegram/`).** One shared `TelegramService` on Telethon, with the session stored encrypted. `core/governor.py` (`TransferGovernor`) is the only gate for FloodWait and concurrency; no engine may do its own.
- **Jobs (`jobs/`).** `JobManager` and `JobOrchestrator` handle admission, dependencies, retries and leases. After an unclean exit every persisted job is marked INTERRUPTED; nothing resumes by itself.
- **Engines (`engines/`).** Long-running workers: uploader, sender, transfer manager, thumbnail and preview caches, metadata sync, face clustering, delivery ledger, local folder scanning (`local_scanner`, `local_videos`, `local_upload_bridge`) and SFW identification (`screen_identify`).
- **Media manager (`media_manager/`).** The Telegram-to-Telegram toolkit: router, mover, archiver, duplicate finder, organizer.
- **Metadata (`metadata/`).** Filename parsing, fingerprints, provider matching. Metadata is collected from external APIs: the NSFW metadata APIs (`providers.py`) and the movie and TV APIs (TMDB, OpenSubtitles, OMDb, TVmaze in `screen_ids.py`) share one review flow.
- **Playback (`playback/`, with its session controller in `playback/controller/`).** The range-streaming engine (`stream_server.py`): 512 KiB segments over raw `upload.getFile`, a sparse disk cache, read-ahead that follows the playhead, a priority scheduler that cancels stale reads on seek, and the external player launchers.
- **Operations (`operations/`).** Content automation: RSS, a qBittorrent supervisor, ingest.
- **Core (`core/`).** Auth and sessions, database, resource manager, diagnostics, backup, and the content-mode rules. **AI (`ai/`).** The assistant, its tools and safety rules, the local engine and the local model downloads.

**Persistence.** `core/database.py` wraps one SQLite connection (guarded by `db.lock`) plus read-only handles (`db.reading()`), WAL mode, schema upgrades by in-place migrations. Secrets live in an encrypted credentials file, not in `settings`. Rust opens the same file read-only through `ax-db` (and writes only the tables it owns, ADR 0002 and 0007).

## 3. Media sources

| Source | Where the bytes come from | Playback |
|---|---|---|
| Telegram message | MTProto `upload.getFile` through the governor | Python's segment cache and read-ahead |
| Local folder | The file on disk, read in place and never copied. Channel id is `-9,000,000,000,000 - folder id`, so the same routes accept it | Sent from disk by the front door with byte ranges |
| Uploaded local file | Both | After a verified upload, identity, cast and snapshots are carried to the Telegram copy (`local_upload_bridge`), so nothing is identified or snapshotted twice |

The Transfer Manager uploads a scanned folder by its own route (`/api/local-folders/{id}/upload`), and the Metadata & Assets Manager scans folders by `/api/local-folders/{id}/identify`.

A video appears in the Library only when its snapshots (36 frames), thumbnail and playback representative are ready.

## 4. Metadata

- **NSFW:** filename and fingerprint matching against the NSFW metadata APIs, suggestions confirmed in the scene-match review; the front door keeps a local copy of the performer name list (`ax-names`) and learns faces (`ax-faces`).
- **SFW:** `screen_identify` reads file names, resolves each show once per run and fetches whole seasons, keeps answers in `metadata_cache` (30 days for hits, 7 for misses; a refused key is never cached), compares titles by words, lets an explicit id in the name (`{tmdb-603}`, `tt…`) settle a match, and sends doubtful ones to a review list. Movie copies are grouped under one card and ranked by quality.

## 5. Content modes (SFW and NSFW)

The whole app is in one mode, stored in `settings` and reset to **SFW on every desktop start**. Switching to NSFW needs a PIN.

- **Where it is enforced:** the data layer. Read connections get SQLite TEMP views that shadow the content tables (`media`, `channels`, `photo_library_items`, performers, studios, tags, link tables), enabled per request by the `FILTER_ACTIVE` context variable. `ContentFilterMiddleware` switches it on for content paths and runs `core/content_guard.py`, which answers 404 for a direct link to an item of the other mode. The Rust side installs the same views from `crates/ax-db/src/visibility.sql`, generated from the Python text by `tools/gen_visibility_sql.py`, so both sides cannot drift.
- **The rule:** a channel is wholly SFW or NSFW; a video, photo, performer, studio, tag or local folder belongs to the mode it was marked with. Adult provider settings, searches and name lists, Telegram channel lists, logs and diagnostics are closed in SFW.
- **New paths must not bypass it.** Anything that reads content (a route, a protocol handler, a job message) goes through the filtered connection or the guard. `tools/tests/test_sfw_leaks.py` seeds adult data and calls the routes known to carry names (channels, provider search, logs, reports, performers, studios, tags, status and the transfer console) in SFW; it fails on any leak. It does not cover every GET route.

## 6. Web app (`apps/web`)

React 19, TypeScript (strict), Vite, Tailwind 4, TanStack Query and Router, Zustand, Motion. Features are folders under `src/features/` (library, photos, entities, watch, series, content, channels, operations, transfers, settings, ai, auth, profile). Words change with the mode through `features/content/labels.ts`. Live status arrives over an authenticated `/ws` socket into the query cache.

## 7. Key flows

1. **Start.** The shell starts the backend with `ARCHIVE_X_DESKTOP=1` and a fresh launch secret, then the front door pointed at it, waits for both ready lines, and opens `/desktop/session?token=…`. Quitting calls the backend's graceful shutdown; the backend also exits by itself if the shell dies.
2. **Browse.** The page asks `/api/...`. The front door answers the routes it owns (lists, favourites, watch history, markers) from the database; the rest go to Python.
3. **Play.** Telegram video: `/stream/{channel}/{message}` in Python (cache, read-ahead). Local video and finished copies: the front door reads the file. External players get signed URLs from the player feed.
4. **Ingest and transfer.** Jobs pass admission, run on an engine, write every delivery to the delivery ledger, then metadata fills in the item.

## 8. Known weak spots

- One Python process with global singletons: no scaling out, and tests depend on module state.
- Very large files: `stream_server.py` (5.5k lines), `automation.py` (4.3k), `providers.py` (3.2k), `database.py` (3.2k+).
- The `mm_*.py` media manager fragments are joined with `exec()` into one namespace, so they cannot be linted alone.
- Many broad `except Exception` blocks that end in `pass`, so some failures are silent (`core/swallowed.py` counts them).
- Operations, Transfers and job text can still show adult file names in SFW.
- Not verified on real hardware: folder upload to Telegram, the real metadata providers, and anything Windows-specific beyond a type check.
