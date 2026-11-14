# Archive X: migration plan: React + Tailwind UI and a standalone desktop app (like NoBuf)

Status: proposal · Scope: Archive X v29.5.400 · Author: generated with Claude Code from a read of the codebase

## 1. Recommendation in one paragraph

Rebuild the **frontend** in React + TypeScript + Tailwind CSS, page by page, served by the existing backend at `/app-next` (the backend already has this route), until it reaches parity and replaces the old UI. That is where the simplification and the animation gains are. Treat **Node.js as two separate decisions**: (a) Node as the frontend *toolchain* (Vite, npm) is required and costs nothing at runtime; (b) Node as the *backend* means rewriting 82k lines of Python, including the Telegram MTProto client, the range-streaming playback engine, ffmpeg/PyAV thumbnailing and face clustering. Do (a) now. Make (b) a separate, later track with a go/no-go gate (section 8), because it carries most of the risk and none of the UI benefit.

**Desktop app, modelled on NoBuf** ([Istiaq-Edu/NoBuf](https://github.com/Istiaq-Edu/NoBuf), MIT). The target is a standalone app like NoBuf: `Archive X_x64-setup.exe` (or MSI), its own window, no Python, no `.bat` script, no first-run `pip install`, one-click signed auto-updates. NoBuf is **Tauri 2 + a Rust backend + React 19 / Vite 7 / Tailwind 4 / framer-motion / TanStack Query and Virtual / Sonner**, so the frontend stack below matches it almost one to one. The packaging track (section 9) does **not** depend on the React rewrite or on a backend rewrite. Recommended: the same **Tauri 2** shell as NoBuf, starting a **frozen Python backend** as a bundled sidecar. It can ship with the current UI in roughly 6-9 weeks; the React UI drops into the same shell later.

**Backend language, revisited.** NoBuf shows the "fully native" end state is Tauri + **Rust**, not Node: its Telegram client is grammers, and its instant-streaming server is actix-web inside the app. If you later want to drop Python, Rust is the better fit for a Tauri app (one runtime, no extra sidecar, NoBuf's MIT-licensed streaming and caching code can be studied or reused with attribution). Node remains possible but would be a second bundled runtime. Section 8 compares the three.

## 2. Why the frontend should be rebuilt (measured)

| Measure (current UI) | Value | Consequence |
|---|---|---|
| JavaScript | 25,845 lines in 60 classic scripts; `09-app.js` alone 10.6k lines, 736 top-level functions | Global state, load-order coupling ("later scripts can wrap earlier globals") |
| Inline event handlers | 700 `onclick=` / `onkeydown=` | No component boundaries; hard to test |
| CSS | 37,407 lines in 181 files, **20,574 `!important`**, selectors like `#app#app#app#app#app#app` | Every fix needs a stronger override; responsive work this session needed triple-id selectors |
| Animations | Hand-written per screen (card slide-out, toasts, skeletons) | Each one fights the cascade; no shared motion system |
| API surface | 325 routes, 158 used by the UI, one WebSocket (`/ws`), 0 typed responses | A typed client can be generated, but response types must be added |

What carries over unchanged: the HTTP API, the WebSocket status stream, auth (HttpOnly cookie, same origin), the Capacitor mobile shell, and the Python engines.

## 3. Target stack

| Concern | Choice | Why |
|---|---|---|
| Build | **Vite** + **TypeScript** (strict) | Fast dev server; static `dist/` the backend already knows how to serve |
| UI | **React 19** | Requested; ecosystem for the parts below |
| Styling | **Tailwind CSS v4** (CSS-first `@theme` tokens) | Replaces 181 files and `!important` wars; theme presets become CSS variables |
| Components | **Radix UI** primitives (Dialog, Popover, Tabs, Select, Tooltip), styled with Tailwind (shadcn/ui-style, owned in repo) | Accessible focus traps, inert backgrounds and portals solved once (the toast-behind-dialog bug class goes away) |
| Animation | **Motion** for React (`motion/react`, the successor package of framer-motion, which NoBuf uses at v12): `AnimatePresence`, `layout`, shared `layoutId` | Card exit/next-slides-up, list reflow, page transitions, gestures; reduced motion respected centrally |
| Desktop shell | **Tauri 2** (same as NoBuf): WebView2 on Windows, plugins `updater`, `single-instance`, `window-state`, `store`, `dialog`, `shell`, `process` | Native window, installer, signed auto-update; see section 9 |
| Server state | **TanStack Query** | Caching, background refresh, optimistic approve/reject; WebSocket messages update the cache |
| Client state | **Zustand** (small stores: player, selection, UI prefs) | Replaces hundreds of `let _x` globals |
| Routing | **TanStack Router** (or React Router 7) with typed search params | Real URLs for library filters, entity pages, video page; working Back button |
| Lists | **TanStack Virtual** | 9k+ video library and photo masonry without DOM bloat |
| Toasts | **Sonner** (inside the Radix portal layer) | Stacked, accessible, above dialogs |
| API types | `openapi-typescript` generated from FastAPI `/openapi.json`, plus Pydantic `response_model`s added per endpoint as pages migrate | Today every response is an untyped dict |
| Tests | **Vitest** + Testing Library (units), **Playwright** (E2E, visual, the responsive/skeleton/review checkers built in this session), **MSW** for API mocks | Parity proof per page |
| Lint/format | ESLint (typescript-eslint, react-hooks), Prettier, Tailwind class sorting | |

Motion design system (built once in phase 1, used everywhere):
- Tokens: durations 120/200/320 ms, two easing curves, spring presets for drag and sheets.
- Primitives: `<FadeIn>`, `<SlideOut direction>`, `<ListReflow>` (layout animations), `<Sheet>` (mobile bottom sheet with drag-to-dismiss), `<Shimmer>` skeleton matching each card type (ported from `css/192-shimmer-skeleton.css`).
- One `useReducedMotion` gate; all motion is transform/opacity only.

## 4. Architecture during the migration (strangler pattern)

```
Browser ──► FastAPI (unchanged, same origin, same cookie)
             ├─ /            legacy UI (shrinks page by page)
             ├─ /app-next/*  new React app  (frontend/dist, already routed in web/routes_frontend.py)
             ├─ /api/*       325 routes (typed gradually)
             └─ /ws          live status (consumed by both UIs)
Python engines: Telegram (Telethon), playback range engine, jobs, metadata, ffmpeg/PyAV
```

- Same origin, so the session cookie, the Origin guard and the WebSocket check work as-is. No CORS, no token changes.
- A "Try the new interface" switch in the old UI (and back), stored per user. Each migrated page links back to the old one until parity.
- At runtime the app needs no Node and no system Python: `frontend/dist` is built in CI and bundled into the desktop app (section 9). **End users never install Node or Python.**
- The Capacitor app keeps working: point it at `/app-next/` once phase 3 lands, and at `/` after cut-over.

Repository layout:
```
frontend/                 new app (Vite)
  src/app/                router, providers, layout shell
  src/design/             Tailwind theme, tokens, motion primitives, Radix-based components
  src/features/<area>/    library, entities, photos, player, operations, transfers, settings, review
  src/api/                generated types, fetch client, WebSocket bridge to TanStack Query
  e2e/                    Playwright (ports of tools/responsive-check and tools/tests)
telegram_archive/         unchanged Python backend
```

## 5. Phases

Durations assume one experienced full-time developer (with AI assistance). They are rough planning numbers, not commitments.

### Phase 0: Foundations (1-2 weeks)
- Scaffold `frontend/` (Vite, TS strict, Tailwind v4, ESLint, Prettier, Vitest, Playwright); CI builds `dist/` and runs tests.
- Generate the API client from `/openapi.json`; add Pydantic response models for the ~15 endpoints the first pages need (`/api/library`, `/api/channels`, `/api/status`, `/api/performers`, `/api/studios`, login).
- WebSocket bridge: `/ws` snapshot → TanStack Query cache updates (coalesced; same 250 ms cadence).
- Auth flow in React (login, first-run setup, session expiry → login redirect).
- **Exit:** `/app-next/` loads behind login, shows live status from `/ws`, builds in CI, ships inside the desktop build.

### Phase 1: Design system + app shell (2-3 weeks)
- Port design tokens from the current theme system (Nord, Nero Gold and the other presets, light/dark, custom themes, interface scale) into Tailwind `@theme` variables; keep the stored preferences compatible.
- Components: Button, IconButton, Input, Select, Checkbox, Tabs, Dialog, Sheet, Popover, Menu, Tooltip, Toast, Badge/Chip, Card, Skeleton, EmptyState, Pagination.
- Shell: sidebar (desktop), icon rail (tablet), bottom nav (phone), header search, account menu; safe areas; the large-display fluid scale from `css/191`.
- Motion primitives and reduced-motion handling.
- Storybook (or Ladle) for the components; visual tests at the 17 viewports used this session.
- **Exit:** the shell plus components pass the responsive checker (no overflow, text ≥ 11 px, touch targets ≥ 36 px).

### Phase 2: Library (most used; 3-4 weeks)
- Channel strip, filters (studio, performer, tag, date, resolution, favourites) as URL search params, sort, pagination/virtualized grid, video card (hover preview frames, favourite, ⋯ menu), bulk/mapping mode, quick edit dialog.
- Animations: card enter/reflow on filter change, ⋯ menu, selection mode transitions.
- **Exit:** feature parity checklist for the library; E2E covering filters, favourites, quick edit; performance check with 10k cards (virtualized, 60 fps scroll on a mid-range phone).

### Phase 3: Performers, Studios, Photos (3-4 weeks)
- Directory grids, detail pages (hero, details, tabs, related, suggestions), entity editor with image candidates and portrait framing, merge/duplicate audit.
- Photos: channel picker, masonry (virtualized), viewer/lightbox with filmstrip, upload toolbar, face groups.
- Animations: shared-element transition from card to detail hero (`layoutId`), lightbox zoom.
- **Exit:** parity; Capacitor app pointed at `/app-next/` for beta testers.

### Phase 4: Watch page and player (hardest; 4-6 weeks)
- The player logic (range streaming, seek preview, snapshot grid, A-B repeat, auto-next, external players, resume dialog, playback QoS) is ~13k lines today across `09-app.js` and `js/player/`. Extract the engine-facing parts into framework-free TypeScript modules first (testable without React), then build React controls on top.
- Up next queue, scene markers, metadata/suggestion panel, Telegram link, move-to-channel dialog.
- **Exit:** playback parity on Chromium, Safari (iOS) and Android WebView; no regression in start time or stall rate (measure with the existing playback diagnostics).

### Phase 5: Operations, Transfers, Review tools (3-4 weeks)
- Operations dashboard (live jobs, health, resource controller, diagnostics, library maintenance), job detail drawer.
- Transfers workspace (wizards, live lanes), Content Automation.
- Scene Match Review, auto-match review, duplicate review (including the thumbnail-delete fix), backups.
- Animations: optimistic approve/reject with exit animation (already prototyped in the legacy UI), live list reconciliation without flicker.
- **Exit:** parity; live updates from `/ws` with no full-list re-renders.

### Phase 6: Settings, AI page, polish, cut-over (2-3 weeks)
- Settings (Telegram login, metadata providers, appearance, storage and backup, security, system and network), AI assistance page.
- Accessibility audit (keyboard paths, focus order, screen-reader labels), bundle-size budget, Lighthouse.
- Cut-over: `/` serves the new app; the legacy UI remains at `/legacy` for one release; then delete `web/ui/` (≈65k lines of JS/CSS/HTML) and the inline asset bundler in `web/html.py`.
- **Exit:** zero open parity gaps; one release with the legacy fallback unused.

**Frontend total: roughly 18-27 weeks for one developer, about 10-15 weeks for two working on different areas after phase 1.**

## 6. Parity and quality gates (every phase)

- A written parity checklist per page, from the legacy UI's functions and routes: see `Archive-X-UI-PARITY-CHECKLISTS.md` (one checklist per phase, extracted from the code, with every API endpoint the legacy UI calls).
- Playwright E2E for the main flows, plus the checkers from this session ported to the new app: responsive sweep (17 viewports), skeleton-matches-content, review slide-out, toast-over-dialog, duplicate delete.
- Visual regression snapshots at phone, tablet and desktop.
- Performance budgets: initial JS under 250 kB gzip for the shell, route-level code splitting, 60 fps for card animations (transform/opacity only).
- Accessibility: Radix primitives plus axe checks in CI.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Player regression (the most complex feature) | Extract the engine logic to plain TS modules with tests before any React work; run both players side by side in phase 4 |
| Hidden behaviour in 700 inline handlers and 736 globals | Build the parity checklist by reading each legacy view; keep the legacy UI reachable until cut-over |
| Untyped API responses | Add Pydantic `response_model`s per endpoint as it is consumed; generated types fail the build on drift |
| Theme and preference compatibility | Map existing presets and stored settings to Tailwind variables; keep the same storage keys |
| Two UIs to maintain for months | Freeze new features in the legacy UI after phase 2; new work lands only in React |
| Bundle and performance on phones | Virtualization, code splitting, image lazy-loading, performance budgets in CI |
| Scope creep from "while we are at it" redesigns | Phases 2-5 aim for parity plus motion; redesigns get their own tickets after cut-over |
| Antivirus / SmartScreen flags on a frozen Python app | Authenticode signing, Nuitka evaluation, one-folder build, submit builds to Microsoft for analysis |
| Orphaned backend process if the shell crashes | Sidecar exits when its parent disappears (watch the parent PID / stdin pipe); single-instance lock on the data folder stays |
| WebView2 video codec differences from Chrome (for example HEVC needs the system extension and hardware support) | Keep today's ffmpeg remux/transcode fallback; test playback matrix in the WebView2 window in D0; NoBuf documents its own HEVC fallback |
| WebView2 missing on older Windows 10 | Installer bundles the Evergreen WebView2 bootstrapper (Tauri option) |
| Breaking existing installs during the switch | Desktop build reads the same data folder; D1 exit requires upgrade without data loss; keep the zip/source release for one version |

Also worth fixing during phase 0: FastAPI's default `/docs` and `/openapi.json` are public and list every route. Keep them for development, but require login (or disable them) in production builds.

## 8. Replacing the Python backend: a separate, later track (Node.js or Rust)

NoBuf changes this section: it proves a Tauri app with a **Rust** backend (grammers MTProto client + actix-web streaming server, about 42k lines of Rust) can deliver instant Telegram streaming as a single installed app. For a Tauri desktop app, Rust is the natural backend language; Node would be a second runtime bundled next to the shell.

| | Keep Python (sidecar) | Port to Rust (NoBuf-style) | Port to Node.js |
|---|---|---|---|
| Runtimes in the installer | Tauri shell + frozen Python | Tauri only (one binary) | Tauri shell + bundled Node (SEA) |
| Telegram client | Telethon (today) | grammers (used by NoBuf) | GramJS |
| Streaming engine | today's Python engine | rewrite; NoBuf's MIT code is a reference | rewrite |
| Installer size (rough) | larger (Python + PyAV + OpenCV) | smallest | medium |
| Effort | lowest | highest (82k lines of Python), but best end state | high |
| Session migration (Telethon → new client) | none | required (auth key + DC) | required |

The detailed Node breakdown below applies equally to Rust; swap GramJS for grammers, Fastify for actix-web/axum, better-sqlite3 for `rusqlite`/`sqlite`.

### What a port would involve
| Python component today | Node.js replacement | Difficulty |
|---|---|---|
| Telethon (MTProto login, channels, uploads, message moves) | GramJS (`telegram` on npm) | High: different session format; flood-wait, file-reference refresh and upload semantics must be re-proven |
| Playback range engine (`playback/stream_server.py`, 5.5k lines: 512 KiB segments, raw `upload.getFile`, sparse cache, prefetch, QoS) | Custom on GramJS low-level API | Very high: the core of the product |
| Job system, governor, orchestrator, watchdogs | Custom (BullMQ needs Redis, so likely in-process) | High |
| ffmpeg / PyAV thumbnails, previews, splitting, optimizing | ffmpeg CLI via child processes | Medium |
| Face clustering (OpenCV) | onnxruntime-node or keep a Python worker | Medium-high |
| SQLite (59 tables, live migrations) | better-sqlite3 on the same file | Low-medium (schema stays) |
| AES-GCM credential vault, encrypted Telethon session | Node `crypto` (AES-GCM is compatible); session conversion Telethon → GramJS | Medium (must be verified on real accounts) |
| HTTP API (325 routes), auth, middleware | Fastify + zod (or NestJS) | Medium |
| Windows packaging | Node runtime must ship with the app (Node SEA or a bundled node.exe) | Medium |

Estimate: 82k lines of Python logic, plausibly **6-12+ months for one developer**, with a real risk of playback and Telegram regressions and a data-migration step for every existing user's session.

### Options
1. **Keep the Python backend as a Tauri sidecar (recommended now).** The React app talks to the same API inside the Tauri window. Node is build-time only. Lowest risk; all the UI and desktop gains.
2. **Port to Rust later (recommended end state if Python must go).** One Tauri binary like NoBuf. Port service by service behind the same HTTP API so the React app does not change: SQLite-only routes first, then jobs, then media, and Telegram plus playback last, each compared side by side against Python. Study NoBuf's `server.rs`, `stream_cache.rs` and `download_pool.rs` (MIT) before designing the playback port.
3. **Port to Node.js.** Same service-by-service approach with GramJS. It works, but in a Tauri app it means shipping and updating a Node runtime as a sidecar; if Node is a hard requirement, Electron (Node built in) is the more coherent shell than Tauri.
4. **Node web tier + Python engine.** Adds a second runtime and an internal API without removing Python. Not recommended.

### Go / no-go gate for the backend track (decide after phase 6)
- Is there a concrete problem Python is causing (hiring, performance, packaging), not just stack preference?
- Has a GramJS spike reproduced login with a converted Telethon session, channel listing, a 2 GB upload, a message move, and range streaming with seeking at today's start time and stall rate?
- Is there capacity for months of parallel maintenance of two backends?
If any answer is no, stay on option 1.

## 9. Standalone desktop app (like NoBuf)

### Target experience
- `Archive X_x64-setup.exe` (NSIS) and `Archive X_x64_en-US.msi`, plus a portable zip. Start-menu entry, uninstaller, per-user install (no admin needed).
- Double-click opens Archive X in its own window (no browser tab, no console, no `.bat`). Window size and position remembered. Second launch focuses the running window.
- Close to tray while jobs run; "Quit" from the tray performs the existing graceful shutdown (jobs checkpointed).
- One-click signed updates, as in NoBuf.
- Everything local: data stays in `%LOCALAPPDATA%\Archive X` (today's location), so existing users keep their library, Telegram session and settings.

### How NoBuf does it (verified in its repo) and what Archive X takes from it
| NoBuf | Archive X |
|---|---|
| Tauri 2 shell, `identifier`, NSIS + MSI bundles (`bundle.targets: "all"`) | Same; identifier e.g. `app.archivex.desktop` |
| React app served through `tauri-plugin-localhost` (port 14200) so the UI is same-origin with its streaming server (port 14201) | Same idea: the WebView loads the UI from the Python backend on `127.0.0.1:<port>`, so cookies, the Origin guard and video range requests work unchanged |
| Backend in-process (Rust, actix-web) | Python backend frozen as a Tauri **sidecar** (`bundle.externalBin`), started and stopped by the shell |
| `tauri-plugin-updater` with a minisign key, `latest.json` on GitHub Releases, built by `tauri-apps/tauri-action` | Same pipeline; the sidecar ships inside each update |
| `tauri-plugin-single-instance`, `window-state`, `store`, `dialog`, `shell`, `process` | Same plugins; single-instance replaces today's instance lock for the desktop build |
| ffmpeg: use one on the system, otherwise download to app data on first need | **Bundle** ffmpeg/ffprobe (Archive X needs them for thumbnails, previews and splitting from the start); keep the optional local-AI model as an in-app download |
| No Authenticode signing seen in its workflow (Windows SmartScreen will warn) | Add Authenticode signing (an OV/EV certificate or Azure Trusted Signing), especially because frozen Python executables trigger more antivirus false positives |

### Backend changes for desktop mode
- **Bind to `127.0.0.1` on a free port by default.** No LAN exposure, no Windows Firewall prompt, no self-signed certificate needed for the local window. Protect the local server with a per-launch secret the shell passes to the WebView, so other local programs cannot use the API.
- **LAN / phone access becomes an opt-in setting** ("Allow devices on my network"), which turns on today's HTTPS listener on 8443, the web login, the root CA download and the player feed. This also fixes several items from the security review by making them off by default.
- **No first-run setup inside the app:** dependencies are frozen; `Setup-And-Run.ps1`, `run_supervised.ps1`, the venv bootstrap and `requirements.txt` installs are dropped for desktop builds (the source-run path can stay for developers).
- **Startup:** the shell shows a native splash, starts the sidecar, waits for `/health`, then loads the UI (today `main.py` already measures startup phases, about 2 s).
- **Shutdown and updates:** the shell calls the existing graceful exit endpoint before quitting or applying an update.

### Freezing the Python backend
- Tool: **PyInstaller in one-folder mode** for the spike (fast to iterate), evaluate **Nuitka** (faster start, fewer antivirus false positives) before 1.0. Avoid one-file mode: it unpacks hundreds of MB to a temp folder on every launch.
- Must-test native pieces: `cryptg`, PyAV (bundled FFmpeg libraries), OpenCV (face clustering), Pillow, `aioquic`/Hypercorn (or drop HTTP/3 for the local window), `cryptography`, `argon2-cffi`.
- The module loader that `exec()`s the `mm_*.py` fragments reads source files at runtime; ship them as data files or convert them to normal imports (also recommended in the code review).
- Startup code that deletes "obsolete" files from the package folder must be disabled in frozen builds (the install folder is read-only for updates).
- Size: expect the installer to be dominated by Python, PyAV, OpenCV and ffmpeg; measure in the spike and set a budget (for example under 250 MB installed). Making face clustering an optional download is the biggest lever.

### Desktop track phases (can start immediately, with the current UI)
| Phase | Work | Rough time | Exit |
|---|---|---|---|
| D0 Spike | Tauri 2 shell + PyInstaller sidecar + bundled ffmpeg; window loads the current UI from `127.0.0.1`; measure installer size, cold start, memory, Defender scan | 1-2 weeks | Works on a clean Windows 10 and 11 VM with no Python installed |
| D1 Packaging | NSIS + MSI + portable zip, per-user install, data-folder compatibility with existing installs, splash, single instance, window state, tray, graceful quit | 2-3 weeks | Existing users upgrade without losing library, session or settings |
| D2 Desktop mode | Localhost bind + per-launch secret, LAN mode as an opt-in setting, first-run screen (Telegram login) without the web-login step in local mode | 1-2 weeks | Security review items for LAN exposure closed by default |
| D3 Release pipeline | GitHub Actions on `windows-latest`: build frontend, freeze backend, `tauri-action` bundle, Authenticode sign, minisign `latest.json`, publish; staged rollout channel (beta/stable) | 1-2 weeks | One-click update from version N to N+1 verified |
| D4 Polish | Crash and log viewer, "Open data folder", diagnostics export, uninstall keeps or removes data (asked), optional macOS/Linux builds | 1 week | |

**Desktop total: roughly 6-10 weeks**, independent of the React work. When the React app reaches cut-over, its `dist/` is bundled in the same way and the legacy UI is removed from the installer.

### How the desktop track and the React track fit together
```
Weeks →   0────4────8────12───16───20───24───28
Desktop   D0 D1 D2 D3 D4                         (ships v30 desktop with current UI)
React        P0 P1 P2──── P3──── P4────── P5──── P6 (ships inside the desktop app at cut-over)
Backend                                           decision gate → (optional Rust/Node port)
```

## 10. First two weeks (concrete)

Two tracks start in parallel; the desktop spike gives the earliest user-visible win.

**Desktop track (D0 spike)**
1. Freeze the current backend with PyInstaller (one-folder) on a Windows runner, bundling ffmpeg/ffprobe; fix what breaks (`mm_*.py` loader, obsolete-file cleanup, native wheels).
2. Create a Tauri 2 shell that starts the sidecar on `127.0.0.1:<free port>`, waits for `/health`, shows a splash, then opens the current UI; add single-instance and window-state.
3. Build NSIS + MSI with `tauri-action`; install on clean Windows 10 and 11 VMs without Python; record installer size, cold start, memory and any Defender or SmartScreen warnings.

**React track (Phase 0)**
1. Create `frontend/` with Vite, React 19, TS strict, Tailwind v4, Motion, Radix, TanStack Query/Router/Virtual, Sonner, Vitest, Playwright; pin exact versions (NoBuf's `app/package.json` is a useful reference set).
2. CI: build `frontend/dist`, run lint, unit and E2E tests; bundle `dist` into the desktop build.
3. Add Pydantic response models and generated TS types for: login/setup, `/api/status`, `/api/channels`, `/api/library`, `/api/performers`, `/api/studios`.
4. Build the `/ws` → TanStack Query bridge and the login flow.
5. Port the theme tokens (one preset, dark and light) into Tailwind and build Button, Card, Skeleton, Dialog and Toast with motion.
6. Ship `/app-next/` with the shell and a read-only library grid behind a "Try the new interface" switch.

## 11. Decisions needed from you

1. Backend now: keep Python as a Tauri sidecar (recommended)? And the long-term direction if Python should go: Rust like NoBuf (recommended), Node.js, or undecided until the gate in section 8?
2. Desktop: Windows only at first, or also macOS/Linux builds (Tauri supports them; the Python sidecar must be frozen per platform)?
3. Code signing: buy an OV/EV certificate or use Azure Trusted Signing, or accept SmartScreen warnings at first like NoBuf?
4. LAN/phone access: keep it as an opt-in setting in the desktop app (recommended), or drop it?
5. Router: TanStack Router (typed search params) or React Router 7?
6. Components: own them on Radix (shadcn/ui-style, recommended), or adopt a full kit (Mantine, Chakra)?
7. Team size and target date, so the phases can be scheduled.
