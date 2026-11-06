# Branch `archive-x-desktop-app`: what is done and what is not

> Update: the React UI now uses the classic UI's exact tokens (dark and light), the Outfit font and icon sprite served by the backend, the grouped sidebar with storage card, the top bar (search, Ctrl K, Telegram pill, theme, avatar), round channel chips and the classic video and performer cards. Checked side by side against the classic app.
> Update: Videos toolbar done (Filters with resolution, sort with 8 options, favourites-only, header search with debounce), tested against the real backend. Still to do on the toolbar: studio/performer/tag/date filters, VLC/MPC-HC shuffle, bulk edit.
> Update: Videos filters now match the classic drawer (studio, performer, tag with suggestions, upload-date range, Reset, Apply), active-filter chips, Favourites chip, refresh channels, ⋯ menu (select all, clear, reload), numbered pagination with page input and page size. Resolution chips removed because the classic UI only has a hidden resolution control. Still missing: VLC/MPC-HC shuffle, card ⋯ menu (move, send, mapping, delete), bulk edit, notifications panel, account menu, command palette, sidebar collapse, phone More sheet.
> Update: React Performers and Studios directory pages and the performer profile page are done (search, gender filter, paging, skeletons, 38 unit tests, checked against the real backend). Studio detail page, performer editing, merge mode and the other sections are still to do.

Executes the first parts of `Archive-X-MIGRATION-PLAN.md` as a separate line of work from `main`.

## Verified (run in this branch's environment: Linux, Python 3.13, Rust 1.97, Node 22)
| Area | Result |
|---|---|
| Backend desktop mode | `tools/tests/test_desktop_mode.py`: guard (HTTP and WebSocket), session hand-off, HttpOnly cookies, secret never in a cookie, landing-path sanitising, graceful shutdown, parent-death watchdog. **All pass from source and from the frozen build.** |
| Frozen backend (PyInstaller, one folder) | Builds in ~35 s, 313 MB with PyAV, OpenCV and cryptg; cold start to ready 1.7 s; 137 MB RSS; zero errors in the log |
| Rust launcher | 9 unit tests (fake backends) plus an integration test against the real frozen backend: ready in 1.5 s, graceful stop in 1.4 s |
| Tauri shell | Compiles against real WebKitGTK; opens the real UI in a native window with no login screen; closing it leaves no process behind |
| Installer layout | A real `.deb` was built; running the **installed** layout (no development override) finds the bundled backend and loads the UI. This found and fixed a flattened-resources bug that development runs hide |
| React app | 31 unit tests; type-check clean; 163 kB gzipped JS; served by the backend at `/app-next/`; sign-in form shows server errors; library skeleton within 1% of the real card at 390 and 1440 px; live status over `/ws`; runs inside the native window |
| Bugs found by running it for real | `302` hand-off lost `SameSite=Strict` cookies in the WebView (fixed); `**/*` resource glob dropped `_internal/` (fixed) |

## Not verified or not done
- **Windows.** No Windows machine here. WebView2, the NSIS and MSI installers, Defender and SmartScreen behaviour,
  `build.ps1`, and the Windows CI workflow (`desktop.yml`) have **never been run**. Expect fixes on the first run.
- **ffmpeg is not bundled yet.** `desktop/scripts/fetch-ffmpeg.sh` needs a pinned URL and SHA-256 (CI variables).
  The backend already honours `ARCHIVE_X_TOOLS_DIR`.
- **Code signing and auto-update** (plan phase D3): no Authenticode certificate, no updater key, no `latest.json`.
- **Tray / close-to-tray, LAN opt-in setting, data-folder migration for existing installs** (phases D1/D2).
- **Installed size.** 313 MB frozen (181-225 MB `.deb`); PyAV, OpenCV and numpy dominate. Making face clustering an
  optional download is the main lever; Nuitka has not been evaluated.
- **React migration beyond phase 0.** Only the shell, sign-in, and a read-only Library grid exist. Performers,
  studios, photos, player, operations, transfers, settings and all dialogs are still in the classic UI.
- Backend Pydantic response models and generated API types (the TypeScript types are hand-written for now).
- The Outfit font is not bundled in the React app yet (system font fallback).
- macOS builds.

## Try it (Linux)
See `desktop/README.md`. Short version: `desktop/backend/build.sh`, then
`ARCHIVE_X_BACKEND_EXE=$PWD/desktop/backend/dist/archive-x-backend/archive-x-backend ARCHIVE_X_LANDING=/app-next/ cargo run -p archive-x-desktop`
from `desktop/` (needs the Tauri Linux prerequisites listed there).
