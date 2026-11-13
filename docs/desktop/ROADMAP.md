# Roadmap: UI/UX improvements and new features

Built on `UI-IMPROVEMENT-PLAN.md` (motion polish, finished). Sizes: S = under a day, M = 1 to 3 days, L = a week or more.

## Sources checked
- smoothui.dev and github.com/educlopez/smoothui (228 animated components, Motion + Tailwind 4, shadcn registry, MCP/llms.txt for agents)
- ui.unlumen.com and github.com/leovvx/unlumen-ui-docs (docs and public registry; the site itself shows only navigation, no component list)
- transitions.dev (43+ transition recipes, motion scanner `transitions-agent`)
- Popular apps for comparison: Immich (119k stars: people, CLIP search, map, albums, sharing, memories), PhotoPrism (40k: combinable filters, labels, places, faces), Jellyfin (59k: media server, transcoding, many clients), Stash (13k: scrapers, tags, stash-box, statistics)
- Popular UI kits: Magic UI (23k), Motion-Primitives (7k)
Their component pages (Magic UI, Motion-Primitives) could not be listed from the README; only the headline facts above were readable.

## A. Improve what exists

### A1. Photos (biggest gap against Immich / Google Photos)
1. **Date headings and a timeline scrubber**: group tiles under month/day headings with a draggable year-month rail on the right. M
2. **Select many photos**: tick tiles, then favourite, download, move to a collection or delete in one go (today favourites and delete are one photo at a time). M
3. **Slideshow** with a timer and fade, from the viewer. S
4. **Filters you can combine**: orientation, size, channel, date range, has-people (PhotoPrism style) as chips above the grid. M
5. **Virtualised grid** (render only what is on screen) so very large libraries stay smooth. M

### A2. People
6. **Merge two people** from their tiles ("Same person as…"), not only through Advanced. S
7. **Hide a person** (strangers, extras) so they never show in People, with an "Hidden" list to restore. S
8. **Cover photo choice** for a person tile. S
9. **Face boxes in the photo viewer** with the name, click to name or correct. M

### A3. Videos and watch page
10. **Continue watching row** at the top of Videos (resume data already exists). S
11. **Ratings (1 to 5 stars)** and a **saved filters / smart playlists** feature (Stash style). M
12. **Keyboard shortcut sheet** (press `?`), theatre mode, A-B loop, frame capture. S
13. **Queue and playlists** for the player's Up next. M
14. **Chapters bar from scene markers** drawn on the timeline (markers exist, show them as ticks with names). S

### A4. App-wide UX
15. **First-run checklist** on the Photos home when empty: connect Telegram, add API keys, scan a channel, Find people. Each step ticks itself. M
16. **Undo toasts** for deletes, moves and merges instead of confirm-only. M
17. **One notifications and jobs tray** that shows running work (Find people, scans, transfers) from any page. M
18. **Keyboard focus and screen-reader pass** across dialogs, menus and the player. M
19. **Install as an app (PWA)** with an offline shell for the web build. S
20. **Remaining plan items**: none open; the motion plan is complete.

## B. New features

### B1. Worth building soon
21. **Collections** (manual albums) for videos and photos, with a cover and sort order. M
22. **Photo duplicate finder** (perceptual hash) with a keep-best review, like the video duplicate tools. M
23. ~~On this day / Memories~~ (not required, dropped). 
24. **Tag manager page** (rename, merge, counts, colours) with tag suggestions. M
25. **Library statistics page** (counts by channel, performer, studio, resolution, growth over time). S
26. **Subtitle search and download** for videos with no subtitles, saved beside the file (OpenSubtitles-style provider setting). M
27. **Export and backup** of the app's own data (people names, favourites, ratings, collections) to one file, and restore. S

### B2. Bigger, optional
28. **Smart search by content** (type "beach" or "red dress"): optional local CLIP model like Immich, with a download step like the face models. L
29. **Scrapers for more sources** and a stash-box style link for performers (existing StashDB/ThePornDB support extended with ids, aliases and merge hints). L
30. **Multiple users** with their own favourites and hidden items, and a PIN-locked private area. L
31. ~~Place and map view~~ (dropped by decision).

### B3. Technical
32. **End-to-end test suite** (Playwright) for login, Photos, People, watch, Transfers; run in CI. M
33. **Performance budget**: lazy-load People, Operations and the player; measure first paint; keep each route chunk under a set size. S
34. **Error boundary and a "report a problem" button** that copies the log lines. S

## Mockups
Open `mockups/mockups.html#tray`, `#collections`, `#duplicates`, `#tags`, `#stats` (add `?dark` for the dark theme), or see the PNGs in `mockups/`. They show: item 17 jobs tray, 21 collections, 22 photo duplicate finder, 24 tag manager, 25 statistics. Mockups only, nothing is built.

## C. Suggested order
- **Batch 1 (feel of Photos, 1 week):** 1, 2, 3, 5, 6, 7, 10.
- **Batch 2 (finishing touches, 1 week):** 4, 8, 12, 14, 15, 16, 25.
- **Batch 3 (organising, 1 to 2 weeks):** 21, 22, 23, 24, 11, 27.
- **Batch 4 (player and media, 1 to 2 weeks):** 13, 26, 9, 17.
- **Later / only if wanted:** 28, 29, 30.
- **Alongside any batch:** 32, 33, 34, 18, 19.

## D. Not recommended
- Decorative effects and installing UI kits wholesale (see the UI plan).
- A map view (dropped).

## E. Everything left, easiest to hardest
Dropped: map view (31), On this day (23). Numbers match the lists above.

| # | Item | Size | Why it sits here |
|---|------|------|------------------|
| 1 | 34 Error screen + "report a problem" | S | One boundary component and a copy-log button |
| 2 | 33 Performance budget | S | Lazy routes and a size check, no new screens |
| 3 | 19 Install as an app (PWA) | S | Manifest and a small service worker |
| 4 | 12 Shortcut sheet, theatre mode, A-B loop, frame capture | S | Small additions to the player |
| 5 | 14 Scene markers on the timeline | S | Markers already exist; only drawing them |
| 6 | 10 Continue watching row | S | Resume data already exists |
| 7 | 3 Photo slideshow | S | A timer on top of the viewer |
| 8 | 8 Choose a person's cover photo | S | One field and a picker |
| 9 | 6 Merge two people from their tiles | S | The merge call already exists |
| 10 | 7 Hide a person (+ restore list) | S | One flag and a filter |
| 11 | 27 Export and restore your data | S | Read and write one file |
| 12 | 25 Statistics page | S to M | Counting queries plus charts |
| 13 | 4 Combinable photo filters | M | Query options plus filter chips |
| 14 | 16 Undo for deletes, moves, merges | M | Needs reversible operations in several places |
| 15 | 15 First-run checklist | M | Reads state from many parts of the app |
| 16 | 17 Jobs tray | M | One panel over data the app already polls |
| 17 | 5 Grid that renders only what is on screen | M | Changes the photo layout engine, so it needs care |
| 18 | 1 Month/day headings and timeline rail | M | Date grouping plus a scrubber on top of the grid |
| 19 | 2 Select many photos, bulk actions | M | Selection state plus several bulk calls |
| 20 | 18 Keyboard and screen-reader pass | M | Many small fixes across every screen |
| 21 | 24 Tag manager | M | A new page, merge rules and suggestions |
| 22 | 21 Collections | M | New data, pages and "add to" everywhere |
| 23 | 13 Queue and playlists | M | New data plus changes to the player |
| 24 | 11 Ratings and saved filters / smart playlists | M | Two features across videos, performers and filters |
| 25 | 9 Face boxes with names in the photo viewer | M | Needs face positions and an overlay in the viewer |
| 26 | 22 Photo duplicate finder | M | Picture fingerprints, a scan job and a safe review |
| 27 | 26 Subtitle search and download | M | A new outside service and file saving |
| 28 | 32 End-to-end browser tests | M | A test suite across the whole app |
| 29 | 29 More scrapers / StashDB-style linking | L | Outside services with their own rules |
| 30 | 28 Search by content (local model) | L | Model download, indexing every photo, new search |
| 31 | 30 Multiple users and a PIN-locked area | L | Touches login, storage and every list |
