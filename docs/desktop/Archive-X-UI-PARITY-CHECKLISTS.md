# Archive X: UI parity checklists for the React migration

Companion to `Archive-X-MIGRATION-PLAN.md`. Each checklist is the **definition of done** for one phase: a page is migrated when every `[ ]` item works in the React app and is covered by a test.

**How these were built.** Extracted from the legacy UI in `telegram_archive/web/ui/` (v29.5.400 plus this session's fixes): 572 interactive controls in 12 HTML templates, 23 dialogs and overlays, 786 JavaScript functions across 60 files, and 128 API endpoint prefixes called from 185 functions. Each control was traced to its handler, the UI it generates and the endpoints it calls. Item names quote the current labels so you can find them in the old UI.

**Markers**
- `[ ]` must reach parity
- `[ ] ⚠` parity item with a known bug or quirk to fix during the port (do not copy the bug)
- `[ ] ?` needs a product decision before porting (keep, merge or drop)
- `✕` dead or unused in the legacy UI: do not port

**Done means, for every page**
1. Every item below works against the real backend.
2. Loading (skeleton matching the content), empty and error states exist for every data region.
3. Works at phone, tablet and desktop widths and in phone landscape; passes the responsive checker (no overflow, text ≥ 11 px, touch targets ≥ 36 px).
4. Keyboard operable; dialogs trap focus and close on Escape; reduced motion respected.
5. Playwright E2E for the page's main flows passes; visual snapshot approved.

---

## Cross-cutting (Phases 0-1)

### Authentication and session
- [ ] Login form (username, password, Enter submits) → `/login`; error and rate-limit (429) messages
- [ ] First-run administrator setup (password + confirmation, 12+ chars) → `/api/auth/setup`; loopback-only message when opened from another device
- [ ] Login hint → `/api/login-hint`
- [ ] Session expiry anywhere returns to login and back to the same URL afterwards
- [ ] Sign out → `/logout`
- [ ] Exit app (graceful server shutdown, offline confirmation screen) → `/api/app/exit`, `/api/app/ping`; hidden in the mobile app (`ArchiveXMobile/` user agent)
- [ ] ⚠ Non-ASCII usernames: server-side bug B1 (login always fails); fix in the backend before or with this phase

### App shell
- [ ] Sidebar (desktop): Photos, Videos, Performers, Studios, Operations (running count badge), Transfers, Settings, AI Assistance; collapse toggle; storage summary card (used %, app data, free space) → `focusSettings`
- [ ] Icon rail (tablet) and bottom navigation (phone: Videos, Photos, Performers, Studios, Transfers, More)
- [ ] "More" sheet (phone): Operations with live counts, Settings, AI Assistance, Notifications, Command palette
- [ ] Header: global search (context-aware: videos / performers / studios / photos), Ctrl K command palette button, notifications button with count, theme toggle, Telegram status pill (opens Settings), avatar menu (profile, storage, command palette, settings, sign out, exit)
- [ ] Command palette (Ctrl K): Browse videos, Search library, Open performers, Open studios, Open Operations, Open Settings, Open Notifications, Clear library filters, Refresh library; arrow keys, Enter, Esc
- [ ] Notifications / activity panel
- [ ] Connection banner: "Lost connection … reconnecting (attempt n)" with Retry now and dismiss
- [ ] Account profile: display name + photo shown in the avatar; "Use Telegram photo" → `/api/account/profile`, `/api/account/profile/image`, `/api/telegram/me/photo`
- [ ] Browser Back/Forward stay inside the app (legacy `_navPush`/`applyNav`); in React these become real routes

### Data and live updates
- [ ] Bootstrap → `/api/bootstrap`; status → `/api/status`
- [ ] `/ws` live snapshot every 250 ms, heartbeat every 10 s, socket health check and retry (`startOpsSocket`, `checkOpsSocketHealth`, `retryOpsSocket`); updates TanStack Query caches instead of re-rendering lists
- [ ] Data-version change detection refreshes only the visible view (`_noteDataVersions`, `_refreshVisibleView`) and never while a dialog is open (`_dataRefreshBlocked`)

### Feedback and overlays
- [ ] Toast stack (tone inferred from text, action button, dismiss, pause on hover/touch, max 3), shown above open dialogs and clickable
- [ ] Confirm and prompt dialogs (`axConfirm`, `axPrompt`): title, danger style, OK/Cancel labels, Enter/Escape
- [ ] Busy-state buttons (spinner + label)
- [ ] Shimmer skeletons per content type (video card, channel, performer, studio, profile page, review card, rows, tiles, inline values)

### Appearance (ports the theme system into Tailwind tokens)
- [ ] Theme mode System / Light / Dark, persisted (`onThemeModeChange`); browser `theme-color` follows the theme
- [ ] Built-in presets (Nord, Nero Gold, Black Essence, Pastel Lavender, Pastel Mint, Minimal Slate, Minimal Sand), each with light and dark variants; "Restore default themes"
- [ ] Custom themes: create (name, dark/light, colours), save and apply, delete; stored themes still load after the migration
- [ ] Interface scale 70-130 % (slider, Dense/Default/Spacious presets, Ctrl − / + / 0 shortcuts, reset); automatic extra scale on large displays
- [ ] Grid density preference

### Mobile app shell
- [ ] Capacitor user agent detection, "Change server" link, safe-area insets, Exit hidden

### Keyboard (global)
- [ ] Ctrl K palette; Escape closes the topmost layer; arrow-key navigation in custom selects, tabs (Home/End), carousels and menus

### Not to port
- `✕` `178-photo-upload-toolbar.js` (not loaded anywhere)
- `✕` Presentation-only patch layers (`*-parity`, `*-reference-exact`, `*-final`, `*-regression-hardening`, `27-large-screen-zoom.js`, `159-modal-viewport-centering.js`): their intent is covered by the design system
- `✕` Duplicate global helpers (`$`, `esc`, `fmt`, `fdur`, `sl`, `jsa`) become typed utilities

---

## Phase 2: Library (Videos)

### Channels strip
- [ ] Channel avatars (photo, initials fallback, title, video count), selected state, "All" with total count, Favourites chip → `/api/channels`, `/api/source-channels`
- [ ] Click toggles a channel; "Select all channels" / "Clear selection"; selection persists and drives the grid
- [ ] Selected-channel actions: Shuffle (choose VLC or MPC-HC dialog → `startChannelShuffle`), Edit
- [ ] Channel edit dialog: rename, choose photo / "Use Telegram photo", preview, save → `/api/channels/…`
- [ ] Refresh channel list → `/api/telegram/refresh`
- [ ] "Index / resume" selected channels (`rebuildSelectedIndexes`) with progress feedback
- [ ] "Reload selected" (`loadLib` forced)

### Toolbar, search and filters
- [ ] Resolution select (All, 4K, 1440p, 1080p, 720p, Below 720p)
- [ ] Sort: Newest first, Oldest first, Largest first, Longest first, Name A–Z, Studio A–Z, Actress A–Z, Tag A–Z
- [ ] ? The sort says "Actress A–Z" while the rest of the UI says "Performers"; pick one term
- [ ] Filters drawer: studio, performer, tag (with suggestions from `/api/studios`, `/api/performers`, `/api/tags`), upload date from/to, quick ranges 7 days / 30 days / 1 year, resolution chips, Reset, Apply; filter count badge
- [ ] Active filter chips with remove (×)
- [ ] Favourites-only toggle
- [ ] Global search box filters the library
- [ ] ? Two filter UIs exist (inline `#fp` panel, hidden, plus the drawer). Port **one** (the drawer)
- [ ] All filters, sort, page and page size live in the URL (deep links, Back button)

### Grid and pagination
- [ ] Server pagination → `/api/library?page&per&channels&q&studio&performer&tag&date_from&date_to&resolution&sort&favorites`
- [ ] Page size selector (saved), page input, first/prev/next/last, numbered pages
- [ ] Empty states: no channels selected ("All channels" button), no matches, empty page
- [ ] Error state with Retry, keeping previous results visible
- [ ] Library summary line ("72 videos · 4 channels")

### Video card
- [ ] Thumbnail (cached, retried, placeholder) → `/api/thumb/{cid}/{mid}`
- [ ] Hover / touch-scrub preview frames (36-frame manifest, sprite, scrub feedback) → `/api/play/preview-manifest`, preview frames
- [ ] Resolution badge, duration, watch-progress bar → `/api/play/history`
- [ ] Favourite star → `/api/library/bulk-favorite`
- [ ] Title (2 lines), performer avatars + "+n" (click opens performer), studio · channel line, tag chips ("+n")
- [ ] ⋯ action menu: Move to another channel (verify, then delete source), Send to Telegram, Edit metadata, Open in VLC, Delete (danger, confirm) → `/api/library/delete`, `/api/library/move-delete`
- [ ] Click opens the watch page; prefetch on hover → `/api/play/preload`, `/api/play/info`
- [ ] Animations: enter, reflow on filter or sort change, menu open/close

### Bulk edit / mapping mode
- [ ] Enter "Edit multiple videos"; checkbox per card; Select all / Deselect / Done; selection count bar
- [ ] Performer / Studio menu: Set Performer, Set Studio, Set Performer + Studio, Clear Performer, Clear Studio → `/api/mapping/bulk`
- [ ] Metadata menu: Recheck Metadata (`/api/mapping/recheck`), Edit Title, Apply All Metadata
- [ ] More: Move All to Unknown, Unlock (`/api/mapping/unlock`), Send to Telegram (bulk)
- [ ] Performer picker (`pk`), studio picker (`sk`), combined performer + studio dialog (`psmap`, optional title), correction dialog (`vmr`) with search-as-you-type and "create new" → `/api/performers`, `/api/performers/provider-search`, `/api/studios`, `/api/local/performer-map`, `/api/local/performer-unmap`

### Quick edit dialog (from card or watch page)
- [ ] Title, performers (search, add, remove), studio, "Find video data" lookup, matched metadata preview, Save → `/api/media/quick-edit`, `/api/media/quick-edit/lookup`

### External players and shuffle
- [ ] Shuffle in VLC / MPC-HC (library, channel, performer, studio) → `/api/play/external`, `/api/play/device-playlist`, `/api/play/device-playlist-link`
- [ ] Remote devices (iOS/Android) open a VLC playlist link

### Send to Telegram
- [ ] Single and bulk send dialog: destination channel, caption template preview, "Always send to this channel" → `/api/send`, `/api/send/bulk`, `/api/send/bulk/status`, `/api/send/preview`, `/api/settings/send`

---

## Phase 3a: Performers and Studios

### Performers directory
- [ ] Grid of performer cards (portrait with fallback artwork, name, video count, edit button) → `/api/performers?limit=5000`
- [ ] Gender filter All / Female / Male; sort ("Default order" and others); search; pagination with page size; sticky toolbar
- [ ] Merge mode: pick performers, commit merge → `/api/performers/merge-batch`
- [ ] "Merge duplicates" → duplicate performer audit dialog: groups with evidence, auto-merge high-confidence, junk cleanup, run again → `/api/performers/duplicate-audit`, `.../auto-cleanup`, `/api/performers/junk-cleanup`

### Performer profile page
- [ ] Back bar with breadcrumb; hero: portrait (adjust framing dialog), eyebrow, name, pills (gender, video count, provider badges StashDB / ThePornDB, local profile), bio with expand/collapse, actions (Shuffle in MPC-HC, Add to favourites, Edit profile), social links
- [ ] Details card (videos in Archive X, career scene counts, birth date with age, measurements and other provider fields) → `/api/performers/{id}/metadata`, `/api/performers/{id}/career-counts`
- [ ] Tabs: Videos (grid + "Edit multiple videos"), Studios, Similar, Suggestions (with counts)
- [ ] Suggestions: preview dialog with four verification frames, "Yes, link performer" / "No, don't link" → `/api/suggestions/{id}/confirm|reject`
- [ ] Random play in MPC-HC (`perfRandomMPC`)
- [ ] Entity editor: profile picture (current, provider candidates with load more, upload from PC, "Use provider image again"), name, profile URL, bio → `/api/performers/{id}/image-candidates`, `/api/entities/…`
- [ ] Portrait framing editor (per performer)
- [ ] Transition: card → hero shared-element animation

### Studios directory and profile
- [ ] Studio cards (logo, name, video count), search, pagination and page size → `/api/studios`
- [ ] Studio page: logo, name, pills, bio, actions (Shuffle in MPC-HC, favourites, Edit studio), website link; details (videos, linked performers, metadata source); tabs Videos / Performers / Similar Studios / Suggestions; suggestion preview confirm/reject; entity editor → `/api/studios/{id}/videos`, `/api/studios/{id}/…`

### Tags
- [ ] Tag page (`vtg`) and tag taxonomy → `/api/tags`, `/api/tags/…`, `/api/tag-taxonomy`

---

## Phase 3b: Photos

- [ ] Channel picker with counts, All, selected-channel Edit
- [ ] Tabs: Photo Library / Image Analysis (with counts)
- [ ] Sort (Newest, Oldest, Name A-Z, Largest), Favorites filter, Refresh
- [ ] Virtualized masonry grid with real aspect ratios, infinite loading, placeholders → `/api/photos`, `/api/photos/channels`
- [ ] Card action menu (`181-photo-actions.js`)
- [ ] Lightbox viewer: previous/next, slideshow play, zoom in/out, fit, rotate left/right, favourite, fullscreen, filmstrip (with optional sound), preview → original handoff, decoded-image cache
- [ ] Viewer keyboard: arrows, Home/End, + / − / 0 zoom, R rotate, F fullscreen, Space, Escape
- [ ] Face groups: "Analyze images for faces", merge selected clusters, clear selection, group filter → `/api/photos/faces/analyze`, `/api/photos/faces/detections/…`
- [ ] Review performer images dialog: search performer, saved profile picture, provider images (load more), "Map group to selected performer"
- [ ] Photo scan / reconcile triggers (from Transfers) → `/api/photos/scan`, `/api/photos/reconcile`

---

## Phase 4: Watch page and player

### Page
- [ ] Back (universal back), Telegram link button (`openVideoTelegram`)
- [ ] Title, filename, metadata chips (resolution, duration, size, codec), studio / source chip (wraps), edit button → quick edit
- [ ] Performers & Studio card with avatars and links
- [ ] Scene tags (collapsible), scene markers (collapsible, count) → markers endpoint
- [ ] Up next rail (current queue) with arrows and scroll buttons; recommendation carousels (Up next, Similar) → `/api/play/upcoming`
- [ ] Favourite (`togFavD`), move dialog, delete
- [ ] ✕ In-player side nav items "Downloads" and "Trash" have no handlers (hidden placeholders)

### Player
- [ ] Playback start pipeline: direct range stream, startup prebuffer, first-frame watch, smart-stream fallbacks (MP4Box → MSE, mpegts → MSE, mediabunny), compatibility (HLS/FFmpeg) failure screen with "Retry original" and "Open in VLC" → `/api/play/…`
- [ ] Controls: play/pause (big button and bar), previous/next video, skip ±30 s, playback rate, mute, volume slider, picture-in-picture, fullscreen, phone orientation handling
- [ ] Seek bar with buffered ranges, live seek preview (cached frames), snapshot grid (pre-play and on demand)
- [ ] A-B repeat
- [ ] Resume prompt ("Continue video": Resume / Start over / Cancel) and preference; save/clear resume position → `/api/play/progress`
- [ ] Auto-next (modes: current queue, collection, same performer, same studio, recommended), autoplay setting
- [ ] Multi-part split files: seamless part advance and seeking across parts
- [ ] NoBuf-style buffer cache: playback drawer, cache timeline with interaction, speed badge, range-cache badges, keep-or-discard on close, download from cache, download tray → `/api/play/range-cache…`, `/api/play/download/…`
- [ ] Release server resources on close and on tab close → `/api/play/release`, `/api/play/stop`
- [ ] Telemetry, activity and incident reporting → `/api/play/telemetry`, `/api/play/activity`, `/api/diagnostics/playback-incident`
- [ ] External playback: Play in VLC / MPC-HC, device link for phones, now-playing status and stop → `/api/play/external`, `/api/play/external/status`, `/api/play/external/stop`, `/api/play/device-link`
- [ ] Shortcuts: Space/K play-pause, ←/→ ±5 s, J/L ±30 s, ↑/↓ volume (with toast), F fullscreen, M mute, N next, Shift+P previous, Esc close (when not fullscreen)
- [ ] Parity proof: start time and stall rate measured against the legacy player on Chromium, iOS Safari and Android WebView

---

## Phase 5a: Operations

- [ ] Summary tiles (Running, Queued, Failed, Completed, With errors) from `/ws`
- [ ] Jobs list with filter (All, Running, Queued, Failed, Completed); job cards with live progress, pipeline stage, ETA/age, error summary
- [ ] Job actions: pause, resume, skip, cancel, refresh, recover, stop playback job → `/api/jobs/{id}/…`, `/api/jobs/recovery`
- [ ] Job detail drawer: run information, activity timeline, "Load older activity", action footer, live polling
- [ ] System health (Telegram, workers, database, rate control, playback, automation), Attention list, Transfer telemetry (active ranges, RPC slots, FloodWait, governor)
- [ ] Diagnostics strip; Export / Reset HTTP window → `/api/diagnostics/summary`, `/api/diagnostics/metrics/export|reset`
- [ ] Library Maintenance: Run full check, last checked, Health & Previews (missing snapshots, artwork cache; Open library; Repair artwork cache; details) → `/api/operations/library-health`, `.../repair-artwork`, `/api/operations/library-summary`
- [ ] Duplicates: "Open performer review" (performer audit dialog); Duplicate video review (channel scope select, Scan for duplicates)
- [ ] Duplicate videos dialog: groups, keeper selection, Delete selected (deletes the thumbnail too), Scan again → `/api/channel-maintenance/duplicates…`, `/api/channel-maintenance/delete-duplicate`
- [ ] Metadata & scene matches: Open review, Retry & review → `/api/metadata/scene-match-review/retry-unmatched`
- [ ] Resource Controller: mode (Automatic, Playback Priority, Balanced, Background Priority, Manual), per-lane limits (metadata, thumbnail, preview, conversion, upload), pause background during playback, expiry (until changed, 15/30/60 min), Save & use Manual, Cancel changes, Return to Automatic, Pause background controller → `/api/operations/resources`
- [ ] Recent activity log: level filter (All, Info, Warning, Error, Success), module filter, outcome filter, search, older entries
- [ ] Downloads popover / sheet: Full log, Diagnostic JSON, Human report, Playback diagnostic (`downloadSupportArtifact`)

## Phase 5b: Transfers

- [ ] Module tabs: Transfer / Metadata / Automation (segmented on phone); live lanes panel; shared resource usage line → `/api/telegram-transfer/lanes`
- [ ] **Telegram Transfer Manager** (stepper: Source, Options & Checks, Review, Run)
  - [ ] Source Local files: folder path, Browse (native picker on the PC, typed path on other devices), Save folder, upload images toggle, include subfolders, destination channel, orientation, resolution, delete local source after verified delivery, oversized-file policy (optimize / skip / split)
  - [ ] Source Telegram media: channel list with search, All / Clear, destination, advanced (source/destination media type, orientation, resolution, duplicate checks source/destination/DB, cleanup broken/orphaned with trash channel)
  - [ ] "Inspect & Prepare →" → `/api/telegram-transfer/start`, `/api/telegram-transfer/scan-photos`
  - [ ] Interactive Telegram Job Manager console (`mm`): structured prompts, send value, stop → `/api/telegram-transfer`, `.../input`, `.../stop`
- [ ] **Metadata & Assets Manager** (stepper: Channels, Processing Stages, Review, Run)
  - [ ] Channel list with search, Select all / Clear, scope preset (entire channel, first 25/50/…)
  - [ ] Stages: inventory, metadata & entity data, snapshots, image scan, reconcile deleted images; force metadata / entity recheck; full-history reconcile; process limit
  - [ ] Run, Run Pending (no scan) → `/api/metadata-assets/preflight`, `.../process-channel`, `.../process-pending`
- [ ] **Content Automation** (6 steps: Source, Runtime & connectivity, Release rules, Queue & activity, Review, Run)
  - [ ] RSS URL, destination channel, download folder (Browse, Save), backend qBittorrent (URL, user, password, API key), poll interval
  - [ ] Release rules: must-contain text, resolution, min/max size, regex, remove torrent and local file after verified delivery, oversized policy, DHT/UPnP, incoming folder
  - [ ] Validate configuration, Save, Start, Stop, Clear all, skip item, resume/cancel previous run, queue and activity status → `/api/operations/automation/…`
- [ ] Playback snapshot builder status → `/api/operations/thumbnails/status`
- [ ] Media manager preflight → `/api/media-manager/preflight`

## Phase 5c: Review tools

- [ ] **Scene Match Review dialog**: counts, bulk approve ≥ threshold %, channel and missing-field filters, clear filters; ranked suggestion cards (provider image, four cached frames, site, record ID, cross-site variant warning, missing tags, "Why this match?" details); Approve / Reject with slide-out and next card sliding up; select + Reject selected (max 500); Videos needing metadata with "Edit video"; Load more → `/api/metadata/scene-match-review`, `.../approve-above`, `.../reject-selected`, `/api/suggestions/{id}/confirm|reject`
- [ ] **Earlier automatic matches** dialog (`amr`): rows with thumbnail, applied metadata, reason; Keep / Undo; select first 100; bulk Keep / Undo → `/api/metadata/auto-match-review…`
- [ ] **Suggested video preview** (performer/studio suggestions): four verification frames, confirm/reject, busy state

---

## Phase 6: Settings and AI

### Settings overview
- [ ] Status banner with pills (Telegram, metadata, web login, …); area cards (Telegram, Metadata, Experience, Storage & Backup, Security) → `focusSettings`
- [ ] Settings navigation (sections, keyboard Home/End/arrows); Validate configuration; Refresh status → `/api/settings/telegram`, `/api/settings/metadata`, `/api/settings/send`, `/api/settings/weblogin`, `/api/telegram/auth-status`
- [ ] Browser compatibility panel
- [ ] System & Network: hardware profile (Refresh), internet speed test → `/api/system/hardware`, `/api/system/internet-test`

### Telegram
- [ ] API ID / API hash ("Login with Telegram" link to my.telegram.org), Save credentials, Test API, Log out of Telegram → `/api/settings/telegram`, `/api/telegram/self-test`, `/api/telegram/logout-account`
- [ ] Sign-in methods: QR code (generate, refresh, cancel, live status), phone number (country picker with search, code entry, verify), cloud password (2FA), passkey explanation, back to methods → `/api/telegram/qr-login/…`, `/api/telegram/send_code`, `/api/telegram/verify_code`, `/api/telegram/verify_2fa`
- [ ] Proxy: Direct / SOCKS4 / SOCKS5 / HTTP / MTProto, host, port, username/password (keep or clear), MTProto secret (keep or clear), Save → `/api/settings/telegram/proxy`
- [ ] Session recovery ("Archive saved session and sign in again")

### Metadata
- [ ] ThePornDB token and StashDB key (saved-key placeholders, Test each, Save all) → `/api/settings/metadata`, check endpoints
- [ ] Scan window (enable, start, end, Save) → `/api/settings/scan-schedule`
- [ ] Provider status tiles (Refresh) → `/api/settings/providers`

### Security
- [ ] Dashboard login: username, current password, new password + confirm, Save → `/api/settings/weblogin`; default-login warning
- [ ] ? In the desktop app (local mode) this section only applies when LAN access is enabled

### Experience
- [ ] Autoplay, auto-next, auto-next mode, resume prompt, live seek preview, partial-cache prompt; Reload; Save experience settings → `/api/settings/experience`

### Appearance
- [ ] (see Cross-cutting: theme mode, presets, custom themes, interface scale)

### Storage & Backup
- [ ] Cache usage; video byte-cache limit, retention, read-ahead, stream profile, RAM buffer; Save cache settings; Refresh usage; Release kept buffers; Clear downloaded video-byte cache → `/api/settings/cache-policy`, `/api/settings/cache-usage`, `/api/settings/release-kept-video-buffers`, `/api/settings/clear-video-byte-cache`
- [ ] Data location: current path, choose new folder (Browse), Move data on next restart, Cancel pending move → `/api/settings/storage…`
- [ ] Backups: repository folder (Browse, Save), scheduled policy (enable, time, daily/weekly/monthly retention), type (full / configuration + database), optional encryption password, Create backup, history (verify, delete), Refresh → `/api/backup/create`, `/api/backup/verify`, `/api/backup/delete`, `/api/settings/backup`
- [ ] Restore: path, password, Validate / Preview, Stage restore (restart required) → `/api/backup/restore/preview`, `/api/backup/restore`
- [ ] ⚠ Restore accepts any server path (security review S7); in the desktop app use a native file picker

### AI Assistance
- [ ] ? The legacy code says "AI Assistance page is intentionally dormant", yet it is in the navigation. Decide: port, hide behind a flag, or drop
- [ ] If ported: tabs Assistant, Library Intelligence, Diagnostics, Jobs, Recommendations, Models; chat with history (Ctrl/Cmd + Enter); quick actions; library search; explain diagnostics / jobs / recommendations; local model settings (runtime path, model path, model id, endpoint, context, GPU layers), Save, Start, Stop, Test → `/api/ai/…`

---

## Appendix A: Keyboard shortcuts to preserve

| Area | Keys |
|---|---|
| Global | Ctrl K command palette; Escape closes the topmost layer; Ctrl − / + / 0 interface scale |
| Player | Space or K play/pause; ← / → ±5 s; J / L ±30 s; ↑ / ↓ volume; F fullscreen; M mute; N next; Shift+P previous; Esc close |
| Photo viewer | ← → ↑ ↓ navigate; Home / End; + / − / = / 0 zoom; R rotate; F fullscreen; Space slideshow; Esc close |
| Lists and menus | Arrow keys, Home / End, Enter / Space, Tab in custom selects, tabs, carousels, settings navigation |

## Appendix B: Questions to settle before porting

1. Filters: keep only the drawer (drop the hidden inline panel `#fp`)?
2. AI Assistance: port, flag, or drop?
3. Player side navigation "Downloads" / "Trash": drop (no behaviour today) or build real features?
4. Security settings in the desktop app: only when LAN access is on?
5. Restore from backup: native file picker only in the desktop app?
6. Which legacy-only behaviours are bugs to fix rather than copy (see `Archive-X-REVIEW.md`: B1, B2, S1-S9)?

## Appendix C: API endpoints called by the legacy UI

Generated from the code (prefixes; path parameters omitted). Every endpoint must be called by the React app or consciously retired; use this list to add Pydantic response models phase by phase.

| Endpoint (prefix) | Called from |
|---|---|
| `/api/ai/chat` | aiPageSend |
| `/api/ai/config` | aiSaveModelSettings |
| `/api/ai/history` | aiPageLoad |
| `/api/ai/runtime/start` | aiStartRuntime |
| `/api/ai/runtime/stop` | aiStopRuntime |
| `/api/ai/status` | aiPageLoad, aiRefreshModel |
| `/api/ai/test` | aiTestRuntime |
| `/api/ai/tools` | aiPageLoad |
| `/api/backup/create` | createArchiveBackup |
| `/api/backup/delete` | deleteArchiveBackup |
| `/api/backup/restore` | restoreArchiveBackup |
| `/api/backup/restore/preview` | previewArchiveRestore |
| `/api/backup/verify` | verifyArchiveBackup |
| `/api/bootstrap` | loadBootstrap |
| `/api/channel-maintenance/delete-duplicate` | deleteSelectedChannelDuplicates |
| `/api/channels` | loadChannels, openChannelEditModal, rebuildSelectedIndexes, saveChannelCardEdit |
| `/api/diagnostics/metrics/export` | exportHttpTelemetry |
| `/api/diagnostics/metrics/reset` | resetHttpTelemetry |
| `/api/diagnostics/playback-incident` | _capturePlaybackIncident |
| `/api/diagnostics/summary` | refreshDiagnosticsStrip |
| `/api/entities` | toggleEntityPageFavorite |
| `/api/icons/status` | loadSettingsStatus, validateSettingsV54 |
| `/api/identify` | identV |
| `/api/jobs` | _jobControl, _startJobDetailPolling, jobCancel, jobRecover, jobRefresh, jobSkip, loadEntityProviderMetadata, loadOlderJobActivity, openJobDetails |
| `/api/jobs/recovery` | loadRecoveryStatus |
| `/api/library` | loadLib, nextVSmart, startChannelShuffle, togFavD |
| `/api/library/bulk-favorite` | togFav |
| `/api/library/delete` | deleteLibraryVideo |
| `/api/library/move-delete` | confirmVideoMoveDelete |
| `/api/local/performer-map` | pkPick |
| `/api/local/performer-unmap` | pkRemoveImmediate |
| `/api/mapping/bulk` | mapSelected, pkPick, psMapSave, skPick, vmrSave |
| `/api/mapping/recheck` | recheckSelected |
| `/api/mapping/unlock` | unlockSelected |
| `/api/media` | deleteV55Marker, loadDetailMarkers, loadPlayerSceneChapters, nextVSmart, syncV55ProviderMarkers |
| `/api/media-manager/preflight` | loadMMPreflight, loadSettingsStatus, validateSettingsV54 |
| `/api/media/quick-edit` | saveVideoQuickEdit |
| `/api/media/quick-edit/lookup` | quickEditLookup |
| `/api/metadata-assets/preflight` | runUnifiedMetadataAssets |
| `/api/metadata-assets/process-channel` | runUnifiedMetadataAssets |
| `/api/metadata-assets/process-pending` | processPendingLibraryItems |
| `/api/metadata/auto-match-review` | amrAct, amrRefreshCount |
| `/api/metadata/auto-match-review/bulk` | amrBulkAct |
| `/api/metadata/scene-match-review` | loadMoreSceneMatchReview, loadSceneMatchReview |
| `/api/metadata/scene-match-review/approve-above` | approveSceneMatchesAbove |
| `/api/metadata/scene-match-review/reject-selected` | rejectSelectedSceneMatches |
| `/api/metadata/scene-match-review/retry-unmatched` | retryUnmatchedSceneMatches |
| `/api/operations/automation/clear-all` | clearAutomationAll |
| `/api/operations/automation/config` | loadAutomationSettings, saveAutomationDownloadFolder, saveAutomationSettings |
| `/api/operations/automation/pick-folder` | pickAutomationFolder |
| `/api/operations/automation/resume` | resumePreviousAutomation |
| `/api/operations/automation/skip-item` | skipAutomationItem |
| `/api/operations/automation/start` | startAutomation |
| `/api/operations/automation/stop` | cancelPreviousAutomation, stopAutomation |
| `/api/operations/automation/test-engine` | testAutomationEngine |
| `/api/operations/library-health` | runLibraryHealth |
| `/api/operations/library-health/repair-artwork` | repairLibraryArtwork |
| `/api/operations/library-summary` | loadLibraryMaintenanceSummary |
| `/api/operations/resources` | applyOpsResourceControls, refreshOpsResourceDashboard, setOpsResourceMode, toggleOpsBackgroundPause |
| `/api/operations/thumbnails/status` | thumbPoll |
| `/api/performers` | hydratePerformerOverview, loadFilterLists, loadPerfSuggested, openPerf, openPerformerByName, pkDoSearch, refreshPerformerAfterMapping, renderPerfTab |
| `/api/performers/duplicate-audit` | openPerformerDuplicateAudit |
| `/api/performers/duplicate-audit/auto-cleanup` | performerAuditAutoCleanup |
| `/api/performers/junk-cleanup` | performerAuditJunkAction |
| `/api/performers/merge-batch` | performerAuditMergeGroup, performerMergeCommit |
| `/api/performers/provider-search` | pkDoSearch, psMapDoSearch |
| `/api/photos/reconcile` | runUnifiedMetadataAssets |
| `/api/photos/scan` | runUnifiedMetadataAssets |
| `/api/play/activity` | _playbackActivity |
| `/api/play/device-link` | primeDeviceLink |
| `/api/play/device-playlist` | playRandom, startChannelShuffle |
| `/api/play/device-playlist-link` | _openRemoteVLCPlaylist |
| `/api/play/external` | _startLibraryExternalPlaylist, perfRandomMPC, sendPD, siteRandomMPC |
| `/api/play/external/status` | pollExternalPlayer |
| `/api/play/external/stop` | closeDBtn |
| `/api/play/history` | _axCardWatchHistory |
| `/api/play/info` | fetchInfo |
| `/api/play/preload` | _axPreloadVideoBytes |
| `/api/play/progress` | pInit, pSaveResume |
| `/api/play/release` | pReleaseServer |
| `/api/play/stop` | stopPlaybackJob |
| `/api/play/telemetry` | _playbackActivity, pSendTelemetry |
| `/api/play/upcoming` | playB |
| `/api/send` | doSend |
| `/api/send/bulk` | bulkSendToTelegram |
| `/api/send/bulk/status` | bulkSendToTelegram |
| `/api/send/preview` | openBulkSendTG, sendTG |
| `/api/settings/backup` | loadBackupStatus, saveBackupSettings |
| `/api/settings/cache-policy` | savePlaybackCachePolicy |
| `/api/settings/cache-usage` | loadCacheUsage |
| `/api/settings/clear-video-byte-cache` | clearVideoByteCache |
| `/api/settings/experience` | _axPersistAppearance, loadExperienceSettings, saveExperienceSettings, setResumePromptPreference |
| `/api/settings/folder-picker` | browseFolderInto |
| `/api/settings/folder-preference` | loadFolderPreference, saveFolderPreference |
| `/api/settings/metadata` | loadMetaSettings, loadSettingsStatus, saveMeta, testKey, validateSettingsV54 |
| `/api/settings/providers` | loadProviderDashboard |
| `/api/settings/release-kept-video-buffers` | releaseKeptBuffers |
| `/api/settings/scan-schedule` | loadScanSchedule, saveScanSchedule |
| `/api/settings/send` | loadSendSettings, validateSettingsV54 |
| `/api/settings/storage` | loadSidebarStorageSummary, loadStorageSettings |
| `/api/settings/storage/browse` | browseStorageFolder |
| `/api/settings/storage/cancel-move` | cancelStorageMove |
| `/api/settings/storage/move` | moveArchiveData |
| `/api/settings/telegram` | loadTgSettings, saveTg, validateSettingsV54 |
| `/api/settings/weblogin` | loadSettingsStatus, loadWL, saveWL, validateSettingsV54 |
| `/api/source-channels` | _loadVideoMoveChannels, loadChannels, loadOpsChannelMaintenanceChannels, retryVideoMoveChannels |
| `/api/status` | refresh |
| `/api/studios` | _softRefreshSiteList, hydrateStudioOverview, loadFilterLists, loadSiteSuggested, loadSites, openSite, skDoSearch, vmrSiteDoSearch |
| `/api/suggestions` | perfConfirmSuggested, perfRejectSuggested, siteConfirmSuggested, siteRejectSuggested |
| `/api/system/hardware` | refreshHardwareProfile |
| `/api/system/internet-test` | runInternetSpeedTest |
| `/api/tag-taxonomy` | loadSmartTagTaxonomy |
| `/api/tags` | loadFilterLists, openTagByName |
| `/api/telegram-transfer` | mmPoll |
| `/api/telegram-transfer/input` | mmSendValue |
| `/api/telegram-transfer/lanes` | refreshTransferLanes |
| `/api/telegram-transfer/scan-photos` | startUnifiedTransfer |
| `/api/telegram-transfer/start` | startUnifiedTransfer |
| `/api/telegram-transfer/stop` | mmStop |
| `/api/telegram/auth-status` | loadSettingsStatus |
| `/api/telegram/logout-account` | logoutTelegramAccount |
| `/api/telegram/refresh` | doRefreshChannels, retryVideoMoveChannels |
| `/api/telegram/self-test` | testTelegramApi |
| `/api/telegram/send_code` | sendCode |
| `/api/telegram/verify_2fa` | verify2fa |
| `/api/telegram/verify_code` | verifyCode |
| `/api/videos/lookup` | loadDetailMarkers, loadPlayerSceneChapters, nextVSmart, togFav, togFavD |
