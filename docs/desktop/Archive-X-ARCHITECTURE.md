# Archive X v29.5.400 — Architecture

Archive X is a **single-process, self-hosted Telegram media archive**. It runs a FastAPI web app
(HTTPS, optional HTTP/3) with a bundled vanilla-JS single-page UI. Behind that sits a durable job
system that talks to Telegram through one shared Telethon session. All state lives in one SQLite
database plus on-disk caches. It is built for a Windows PC on a home LAN, with phones and other
devices as clients.

Size: about 165k lines (182 Python files, 58 JS, 178 CSS). Source is under `telegram_archive/`.
The diagram is in `Archive-X-architecture.svg`.

## 1. Runtime shape

| Concern | Where | Notes |
|---|---|---|
| Launch | `Archive-X.bat` → `scripts/launcher/*.ps1` → `run.py` | `run.py` creates `.venv`, re-execs under it, clears stale `.pyc` on version change, then calls `telegram_archive.main:main` |
| Startup | `main.py` | Instance lock → cleanup → data migration → open DB → credentials and TLS cert → `create_app()` → serve |
| Web server | Hypercorn (HTTPS/TCP plus HTTP/3 QUIC) or Uvicorn fallback | Binds `0.0.0.0:8443` by default |
| Player feed | aiohttp, plain HTTP on `0.0.0.0:8444+` | Serves signed-token streams to VLC, MPC-HC and FFmpeg |
| App factory | `app.py: create_app()` | Single place that builds and wires every singleton (see §3) |
| Process model | `core/process_topology.py` | Deliberately **one process**. Telegram, jobs, playback and watchers are process-local singletons, so multi-worker is not supported |

## 2. Layers

**Presentation (`web/ui/`).** Static HTML shell plus about 58 numbered JS files and 178 CSS files, with no framework or
bundler. `09-app.js` alone is 10.6k lines. The server bundles and ETags the CSS and serves JS with a long cache lifetime.
Live updates come from an authenticated `/ws` WebSocket that pushes a coalesced status snapshot.

**HTTP layer (`web/`).** `routes.py` builds one router and registers 13 `routes_*.py` modules by domain: system (login and setup), telegram,
automation, jobs, metadata_assets, playback, playback_compat, settings, library, entities, media, entity_pages and tags_logs.
`routes_ai`, `routes_experience`, `routes_frontend` and `routes_test_bridge` are wired separately. Every handler calls
`require_auth()`, which checks a cookie session. ASGI middleware, outermost first: HTTP/3 `Alt-Svc` → security headers →
gzip/ETag optimizer → browser mutation guard (Origin check) → API rate limit → request correlation.

**Domain and engines.**
- `telegram/service.py` holds the one shared `TelegramService`, built on Telethon. It handles code, 2FA and QR login, with the session stored encrypted (`encrypted_session.py`).
- `core/governor.py` (`TransferGovernor`) is the single gate for FloodWait handling and concurrency. No engine may implement its own.
- `jobs/` has the `JobManager` (worker owner) and `JobOrchestrator` (admission, dependencies, retries, leases). `RecoveryCoordinator` marks every persisted job INTERRUPTED after an unclean restart. Nothing auto-resumes.
- `engines/` holds the long-running workers: uploader, telegram_sender, transfer_manager, thumbnail and preview caches, metadata sync, face clustering, delivery ledger and others.
- `media_manager/` is the Telegram-to-Telegram toolkit: router, vertical mover and archiver, duplicate finder, channel organizer. `telegram_media_manager.py` `exec()`s the six `mm_*.py` fragments into one shared namespace.
- `metadata/` runs the identification pipeline: filename parser → fingerprint and perceptual hash → provider matching (ThePornDB, StashDB) via `api_broker` → suggestions and review.
- `operations/` covers Content Automation: RSS, a qBittorrent supervisor and torrent engine, then ingest.
- `playback/` and `systems/playback/` hold the range-streaming engine (`stream_server.py`, 5.5k lines): 512 KiB segments over raw `upload.getFile`, a sparse disk cache, prefetch, and QoS. They also contain the VLC/MPC launchers and a session/controller layer.
- `core/` supplies cross-cutting services: auth and sessions, resource manager, hardware adaptation, watchdogs and guardian, diagnostics and observability, backup (`.axbackup`, optionally encrypted), the local AI runtime (loopback llama.cpp) and its tool layer.

**Persistence.**
- `core/database.py` wraps one SQLite connection (`check_same_thread=False`, guarded by `db.lock`) plus read-only handles (`db.reading()`). About 59 `CREATE TABLE` statements. Schema upgrades are in-place `ALTER TABLE` migrations.
- Secrets (Telegram `api_hash`, provider keys) are moved out of the `settings` table into an encrypted credentials file via `config.get_secret` / `set_secret`.
- A per-user data directory holds the DB, certificates, the Telegram session, thumbnail and preview caches, and the player cache.

## 3. Wiring in `create_app()`

```
Database ─► TransferGovernor ─► TelegramService ─► JobManager(+Orchestrator, Watchdogs)
   │                │                                   │
   │                └─► InteractiveResourceManager ◄────┤
   ├─► SessionStore / LoginRateLimiter                  ├─► PageEnricher
   ├─► StreamTokens (HMAC secret in settings) ──► aiohttp player feed
   └─► build_router(...) ─► routes_* modules ─► FastAPI app + middleware
```

The lifespan hook starts the jobs (restoring them as interrupted), runs a background preview backfill, starts a Telegram
connect supervisor and a diagnostics watcher, and launches the player feed. On shutdown it checkpoints jobs and writes a
clean-shutdown marker.

## 4. Key flows

1. **First run.** The launcher builds the venv and installs dependencies. The app creates a local root CA and a leaf certificate for the PC's LAN IP, then sets `setup_required`. `POST /api/auth/setup` is accepted only from loopback and creates the admin credentials. The phone installs the CA via `/ca.crt`.
2. **Login.** `POST /login` → rate-limit check → PBKDF2-SHA256 verify (260k iterations) → random 256-bit session token. Only a SHA-256 hash is persisted in `web_sessions`. The cookie is HttpOnly, Secure and SameSite=Strict. Sessions last 30 days and slide.
3. **Playback.** The UI asks `/api/play/*`. The shared engine pulls ranges from Telegram through the governor into a sparse cache and serves HTTP ranges to the browser. External players get a signed `?c=&t=` URL from the player feed.
4. **Ingest and transfer.** Local-folder or Telegram-source jobs go through orchestrator admission, then an engine (uploader, mover or sender). Oversized files are optimized or split, every delivery is written to the delivery ledger, and the metadata pipeline then enriches the item.
5. **Metadata.** Provider and filename matching produces suggestions, which a user confirms in the scene-match review UI. Results are mirrored to Telegram captions through caption-sync.

## 5. Architectural observations

- **Strength — clear ownership rules.** One Telegram client, one governor, one orchestrator, and no silent resume after a crash. These are enforced by convention and documented in docstrings.
- **Strength — secrets are handled with care.** The Telegram session and credentials are encrypted at rest, session tokens are stored hashed, and log redaction matches `password`, `token`, `api_hash` and similar names.
- **Risk — a single process with global singletons.** This rules out horizontal scale and makes tests depend on module state.
- **Risk — files that are too large.** `09-app.js` (10.6k lines), `stream_server.py` (5.5k), `automation.py` (4.3k), `providers.py` (3.2k) and `database.py` (3.1k).
- **Risk — namespace-fragment `exec()`.** The `mm_*.py` files cannot be linted or type-checked on their own, because they reference names defined in the host file.
- **Risk — layered CSS.** The numbered files `142-ax2-…`, `145-ax-final-parity`, `148-reference-exact` and `162-reference-fidelity-final` override earlier files. This makes the cascade order-dependent and fragile.
- **Risk — exception handling.** There are 1,716 `except Exception` blocks, and 542 of them go straight to `pass`, so failures are silent.

## 6. Structure measured from the Graphify graph
Graphify indexed 244 files into 6,739 nodes and 17,498 edges (91% extracted, 9% inferred). Full views are in `Archive-X-graphify/`.

| Measure | Result |
|---|---|
| Most-connected nodes | `Database` (109 edges), `now_iso` (105), `PlaybackEngine` (84), `TransferGovernor` (72), `JobManager` (70), `register_playback_routes` (70), `register_library_routes` (68) |
| Detected communities | 242. The largest map to the files and classes in §2: `uploader`, `telegram_sender`, `automation`, `thumbnail_cache`, `preview_prefetch`, `JobManager`, `PlaybackEngine`, `QBittorrentEngine` |
| Circular imports | 3 — `core/internal_media ↔ media_manager/telegram_media_manager` (directly and via `engines/telegram_sender`), and `core/workspace_guard → operations/automation → engines/uploader → core/workspace_guard` |
| Routes | 325 HTTP and WebSocket handlers across 17 route modules; all authenticate directly or through a helper, apart from the intentionally public ones |

**What the graph says about layering.** The dependency direction is mostly clean: `web → jobs/engines → telegram/core → database`.
The exceptions are the three cycles above. They sit where `core` (supposedly the lowest layer) imports from `engines`,
`operations` and `media_manager`. Moving the shared helpers that `internal_media` and `workspace_guard` need into a small
leaf module would remove all three cycles.
