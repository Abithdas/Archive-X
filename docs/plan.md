# Archive X: the plan

One plan for everything that is not finished. It replaces the earlier working plan (clean-up, layout and Rust migration), the Rust migration history, the streaming and architecture v3 proposal, and the lists of ideas and open items from the 30.4.x work. Written 18 November 2026, at release 30.4.2 (30.4.3 building).

A version of this plan grouped by category, with the state of each item, is in [plan-by-category.md](plan-by-category.md).

How to read it: section 1 says what is released and what is only on the branch. Section 2 is the rules. Section 3 is what must be checked on a real PC before anything else is trusted. Sections 4 to 6 are the work, in the order to do it. Section 7 is the clean-up left over. Section 8 is the decisions only you can make. Each item has an id (A1, B2, ...), so you can say "do B1 to B3". Every item says what it is, why, and what "done" means; what could not be checked is written as not checked.

## 1. Where things stand

| State | What |
|-------|------|
| Released (30.4.2) | SFW and NSFW modes enforced in the data layer; channel marks; local folders (scan, metadata scan, snapshots, upload to Telegram); movie and TV identification (TMDB, OpenSubtitles, OMDb, TVmaze); Movies and Series pages; fast and clean exit; movie and TV lookups that work in the installed app; key check on save; scanned folders in Transfers and Metadata; Library channel list without phantom "Unknown channel"; adult performers no longer shown in an empty SFW; live captions for local files with a CPU fallback |
| Building (30.4.3) | The native player starts at once for videos the web player cannot play; the in-page conversion (client-side remux and HLS) is removed |
| On the branch, not released | Nothing else |
| Not verified on a real PC | Everything Windows-specific, native-player fallback, Telegram live captions, folder upload to real Telegram, valid provider keys (section 3) |

Sizes on 18 November: about 106,000 lines of Python (218 files), 456 TypeScript files, about 11,900 lines of Rust, a C++ video engine. How it is built: [architecture.md](architecture.md). Why: [adr/](adr/README.md).

## 2. Rules

1. **The app works after every commit.** Anything risky sits behind a switch that can be turned off without a new build (a native-route name, a setting, an environment variable).
2. **Three things stay compatible until the Python backend is gone:** the HTTP API the web app uses, the SQLite database file, and the data folder.
3. **One writer per table group.** Rust may read any table; a table is written by Python or by Rust, never both (ADR 0002 and 0007).
4. **New features are written in Rust** unless they extend Python code that is about to be replaced; Python gets fixes and deletions. Delete, do not refactor, Python.
5. **A route moves to Rust only when the parity check passes** (`tools/parity/compare.py`) and it ships switched off first.
6. **Nothing is done without evidence:** a test, a parity run, a measured number, or a result from a real PC. A call to a provider or service that has only run against stand-in answers has not been run: try it once against the real service.
7. **The SFW rule has no bypass.** Anything that reads content goes through the filtered connection or the guard (ADR 0011); `tools/tests/test_sfw_leaks.py` stays green, and new lists are also tried with an empty SFW library.
8. **Outbound HTTP from Python uses the standard library or `core/http_client.py`.** `requests` is not part of the installed app.
9. **Licences, and what we take from other projects.** We take on no legal obligations. New dependencies must be, in this order of preference: Unlicense, CC0 or 0BSD (no conditions at all); MIT, BSD-2/3, ISC or Zlib (only keep their copyright line in `THIRD-PARTY-NOTICES.md`); Apache-2.0 only when nothing else works. A crate licensed "X OR Y" is taken under the better one. Not used: GPL, LGPL and AGPL code as a dependency (it brings rules of its own), MPL, non-commercial or research-only terms, "non-standard" or missing licences; the weights of a model count too. We never paste a project's code or files in: we read what a project does and why, then write our own. The licence of each candidate is checked on crates.io (or its repository) before it is added.
10. **Releases are built by the Desktop workflow only**, and published only when you say so.

## 3. Verify on a real PC (nothing else is trusted until these are done)

All of these are built and tested with stand-ins or on Linux. The only way to close them is to use the installed app. Each needs you; tell me what you see and I fix it.

| Id | Check | Done when |
|----|-------|-----------|
| A1 | Install 30.4.3 over the old build; start, switch modes, exit | The app starts in SFW, the PIN switches to NSFW, Exit leaves no process behind (the Windows job object is only type-checked) |
| A2 | **Live captions on a Telegram video** | Captions appear. If not, the line under the player ("Live captions failed: …") is the lead. The engine now falls back to the CPU when the graphics-card path fails; this is untested on real hardware |
| A3 | **Provider keys: OMDb, TMDB, OpenSubtitles** with real keys | Each key's check says it works. OMDb refuses a new key until its activation link is opened. Only wrong keys were ever tried against the live services |
| A4 | **Native player for a file the web view cannot play** (an AVI, a DTS or AC3 film, a 10-bit HEVC) | It opens in the native player without trying anything else first, plays, seeks, shows captions. Never verified on a real PC |
| A5 | **Upload a scanned folder to a Telegram channel** from Transfers, with one short video first | It arrives with its snapshots and names, and nothing is identified twice. Untested against real Telegram |
| A6 | Local-folder playback through the front door (Windows paths, long names, non-English names) | Play and seek work; a file moved out of its folder is refused |
| A7 | Your real library: the Library channel list, the Cast page in SFW, Operations | No "Unknown channel" that is not a real unnamed channel; Cast is empty in SFW until a movie is identified; no adult name anywhere in SFW |
| A8 | A movie-and-TV identification run on a real folder | Matches fill in; doubtful ones wait in the review list; a second run asks the providers nothing |

## 4. Product work (in this order)

### Identification you can trust

- **B1 Explainable matching and "not this one".** Show the score and reason per candidate in the review list ("title matches, year off by 1") and remember rejected candidates so a re-run never offers them again. *Why:* today the list shows candidates with no reasons, and a rejection is forgotten. *Done when:* each candidate shows its reason, and a rejected one stays rejected across runs. (Idea from Sonarr's manual search.)
- **B2 A health page.** One list of what is wrong now, each with a fix link: no TMDB key, key refused, folder gone, ffmpeg missing, Telegram disconnected, disk nearly full, native engine not installed, speech model missing. *Why:* the pieces exist across Operations and Settings, but not in one place. *Done when:* every item has a test that makes it appear and a link that fixes it.
- **B3 One interface for metadata providers.** Each provider (TMDB, OMDb, OpenSubtitles, TVmaze, the NSFW metadata APIs) answers the same questions and reports "available / key missing / rate-limited". *Why:* adding TVDB, Trakt or AniList becomes one small file, and the Metadata page shows every provider's status the same way. *Done when:* the existing providers run through it with no change in results (the identification tests stay green).
- **B4 Check the name parser.** Test specials (`S00E01`), multi-episode files (`S01E01E02`), absolute-numbered anime and release-group noise, and compare with guessit and parse-torrent-title on a sample of your real file names. *Done when:* the failures found are fixed or listed.

### Playing and watching

- **B5 Skip intro and recap for a season.** Mark an intro once; the same range is offered as a Skip button on the other episodes, adjustable per episode. Reuses scene markers.
- **B6 Remembered playback speed and a quality cap**, plus a chip saying why a video went to the native player ("native: HEVC 10-bit").
- **B7 Live captions that cannot be stuck on a GPU problem for good.** whisper.cpp as a second engine (no CUDA needed), chosen by the same warm-up test that already decides the CPU fallback.

### Library keeping

- **B8 A better-copy rule.** A per-series or per-movie target ("1080p, not a cinema recording"); a note when a better copy turns up; the old copy is kept until you confirm.
- **B9 Rename and organise a local folder, with a preview of the result first.** Opt-in, reversible, never deletes.
- **B10 Fill the gaps in what the SFW side shows:** TVDB as a provider (after B3), cast photos, posters on the Videos cards.
- **B11 Folder upload gaps.** Folders that leave out subfolders are refused today; split (multi-part) uploads are untested.
- **B12 Faces and duplicates.** Compare our face grouping and duplicate finding with Immich, czkawka and Video Duplicate Finder on a real library; take what is cheap (for example duplicate detection by fingerprints on the 36 snapshots, which belongs to C4).

### Settings and polish

- **B13 An update channel** (stable or beta) and the changelog shown after an update.
- **B14 Run-your-own-command hooks** after a transfer or an import, for power users.
- **B15 Lock Operations and Transfers in SFW** (decision D1): job text and file names there can still show adult names in SFW.

## 5. Streaming and the data layer

From the v3 architecture proposal, with the corrections made in review: integer keys stay in SQLite, blocks are 1 MB, nothing is promised under 200 ms from a cold Telegram fetch, and every claim is benchmarked.

- **C1 Done:** the front door sends local-folder videos and finished playback copies itself (ADR 0010; about 5 times the throughput on a local file in a benchmark, seeks 3.6 ms to 1.6 ms). Telegram streaming stays in Python on purpose.
- **C2 `ax://` thumbnails and preview frames**, with the SFW check done through `ax-db` (which has the views). Done when thumbnails load through it, a hidden item answers 404, and the SFW leak test covers the new path.
- **C3 Batched scan writes and paging by a combined (sort key, id) cursor** in `ax-db`. Done when a 50,000-item channel scrolls without OFFSET and a scan writes in batches of 500 to 1,000.
- **C4 Two-stage duplicate detection.** Identical Telegram documents first (document id), then fingerprints on the 36 snapshots; never on Telegram's 40-pixel thumbnail.
- **C5 Content-mode rules in one Rust place.** `visibility.sql` is generated from Python today; move the single source of truth into the Rust data layer and have Python read through it.
- **C6 The Telegram client in Rust (Phase 0, below):** a trial of `grammers`, judged by parallel block fetches across data centres, transparent `FILE_REFERENCE_EXPIRED` refresh, `FILE_MIGRATE` handling, `FLOOD_WAIT` back-off, reuse of the existing session without a new login, and time-to-first-frame no worse than today on the same files. Needs your account for a test channel (D2).
- **C7 Serve Telegram cache hits from Rust** only if measurements show Python is the bottleneck; two programs sharing one cache and one playhead is the risk.
- **C8 Hosted product** (PostgreSQL, a UUIDv7 column and sync, a message bus, OpenSearch, cells): only if there is ever a hosted product. Not before.

## 6. The Rust migration (what is left)

The recipe for moving one route: read the Python handler; write down its parameters, answers, errors and tables; seed data and record Python's answers; write the Rust version with hand-checkable tests; wire it behind a route name, off by default; run the parity check; measure on the big library with one client and eight; switch it on in one release and watch a week of logs; then delete the Python handler and what only it used, and regenerate `docs/api/`. A write route also moves every other writer of that table group in the same release, and is tested against an interrupted write.

| Phase | What | State |
|-------|------|-------|
| 0 | Prove the risky parts: Telegram with `grammers`; face, tag and caption models through ONNX Runtime and whisper.cpp | The trial program is written (`tools/phase0/telegram-trial`) and waits for you to run it on your PC; the model part is not started |
| 1 | Reading in Rust | Done for: photo channels, performer list, People row, channel, studio and tag video lists, library list, tags, studio metadata, photos. Not ported on purpose: job and lane status (live Python memory), performer metadata (Python writes first), studio family counts, searches (full-text index) |
| 2 | Streaming | Local files and finished copies in Rust (C1). The Telegram stream server (5,500 lines) stays in Python until C6 |
| 3 | Writes without Telegram | Done: watch history, entity favourites, scene markers, video favourites. Next: performer and studio edits, tags, face naming and merging, settings |
| 4 | Login and sessions | Mostly moot (the desktop app has no web login). Done: the name list and face learning check the session in the database; the front door applies the launch guard |
| 5 | Jobs | Not started: job table, lanes, pause and resume as `ax-jobs`; then the engines that need no Telegram (thumbnails, previews, derivatives, duplicate fingerprints) |
| 6 | Local AI | Not started: face detection and vectors, content and look tags, captions, assistant setup and chat loop; a better face model (SCRFD plus ArcFace) with a re-analysis job |
| 7 | Telegram | Not started: session, scans, delete and move with verification, the parallel uploader with resume, the Transfer and Media Manager workflows (port by workflow, not file by file) |
| 8 | Providers and automation | Not started: metadata matching, filename parsing, artwork, then automation, RSS and torrents if you keep them (D4) |
| 9 | Remove Python | Delete the backend, PyInstaller, `requirements.txt` and the proxy; link the server into the desktop process; re-measure installer size, start-up time and memory |

Two tracks run alongside: the **native player** (done: picture in its own window, captions strip, preview, fallback, up-front choice; open: A4, and a decision on making it the main player) and **face learning** (open: check thresholds against your real corrections, record accepted and rejected suggestions, then allow automatic adding of strong matches).

## 7. Clean-up left over

- **E1 Python conversion routes the page no longer uses** (HLS and the playback copies in `routes_playback.py` and `routes_playback_compat.py`). Check external players and other callers first; then delete.
- **E2 Unused routes and Python.** The route counter (shipped) needs a week of normal use and a logs zip; then delete the unused routes and the Python only they used (106 of 325 routes had no caller when measured on 14 November).
- **E3 Small leftovers:** eight unused variables found by vulture (`resource_manager`, `delivery_pipeline`, `telegram_sender`, `mm_smart_router`, `automation`, `stream_server`); the optional `cryptg` import stays on purpose.
- **E4 `docs/api/`** (the frozen API description the parity check compares with) has not been regenerated for the routes added since 30.4.0.
- **E5 Older notes still name the adult metadata providers** (older changelog entries, two decision pages, the crate README). Scrub them if you want them gone (D3).
- **E6 The `deliverables/` zips** (12 MB, not tracked by git) are from earlier sessions; delete them if you do not need them.
- **E7 GitHub description and topics** are empty. Set a one-line description and topics (D5).
- **E8 Two web tests are timing-sensitive** under a full run (a different one failed each time and passed alone). Make them wait for what they check instead of for time.
- **E9 A Python test depends on the order of the others.** `test_screen_identify.py::test_an_nsfw_folder_is_identified_by_file_hash...` fails when `test_sfw_leaks.py` runs before it in the same process (it also fails on the commit before the last round), and passes alone. A provider key saved by an earlier test is still there; the test should start from no keys.
- **E10 The caption speech model on a graphics card.** The model choice and the timing were measured on a processor only. Try Accurate on a PC with an NVIDIA card and see the speed Settings, Experience shows.

### Projects to learn from (written ourselves) or to depend on (licence checked 18 November)

| For | Project | Licence | Use |
|-----|---------|---------|-----|
| B4 name parser | hunch (guessit port, Rust) | MIT | May be a dependency; at least use its guessit-derived test cases as our benchmark |
| B4 | cinekey (release-name parser) | MIT | Second opinion for multi-episode and anime numbering |
| B7 captions without CUDA | whisper.cpp, whisper-cpp-plus (Rust, streaming and voice detection) | MIT, MIT | May be a dependency (`whisper-rs`, Unlicense, is also allowed) |
| Phase 6 faces | rusty_scrfd (SCRFD detector) | MIT | May be a dependency. `face_id` has a "non-standard" licence: not used, read for the idea only. Check the licence of the model weights too (some published face models are research-only) before shipping any |
| B12, C4 duplicates | vid_dup_finder_lib, czkawka_core | MIT OR Apache-2.0, MIT | Study how they hash frames; our own fingerprint on the 36 snapshots is the plan |
| Phase 5 jobs | apalis, apalis-sqlite | MIT OR Apache-2.0, MIT | Study the SQLite job table and the hooked fetch; decide after D4 |
| C6 Telegram | grammers (now on Codeberg) | MIT OR Apache-2.0 | The trial already planned |
| Issues list | Drain3 (Python), logdrain (Rust) | MIT, Apache-2.0 OR MIT | Idea: learn log templates; write our own small version |
| B5 skip intro | intro-skipper (Jellyfin plugin) | GPL-3.0 | Idea only: compare audio fingerprints of the start of each episode in a season |
| F7 | Telethon | MIT | Already a dependency; its flood-wait and takeout ideas, below |
| F2, F3, F8 | Telegram-Drive | no licence file found (all rights reserved by default) | Ideas only; no code |
| F4 | FastStream | "All rights reserved, ask permission" | Ideas only; no code |
| F5 | mediabunny | MPL-2.0 | Ideas only (MPL is not allowed as a dependency) |
| F6 | jassub (libass in WebAssembly) | MIT | Idea; libass is ISC |
| F5 | mp4box.js | BSD-3-Clause | Idea: progressive parsing and sample tables |
| F5 | mpegts.js | Apache-2.0 | Idea only |
| F9 | uv, ruff | MIT (uv: MIT OR Apache-2.0) | Dev tools, not shipped; ruff is already in CI |
| F1 | Handy | MIT | Idea: Parakeet V3 as a second speech engine; check the model weights' licence first |
| F10 | omnigraph | MIT | Idea: hybrid search (full text + vectors + rank fusion) |
| F11 | rust-anything | MIT | Idea: record what Python answers, replay it against Rust |
| F12 | pgrust | AGPL-3.0 | Idea only: one memory budget, a scheduler that protects the UI |
| F13 | tauri, Pake | MIT or Apache-2.0, GPL-3.0 | tauri is ours already; Pake only for the size idea |
| none | agent-browser (Apache-2.0), rea (MIT) | | Not a fit: browser driving for agents, and reverse engineering of other apps |

### Ideas from the projects of 19 November (F items; none started)

Read from their READMEs on 19 November; nothing was copied, and licences are as the repositories state them.
- **F1 Second speech engine, Parakeet V3 (Handy).** Handy offers Whisper sizes and Parakeet V3, "CPU-optimised, automatic language detection", behind Silero voice detection, all offline. On a processor Whisper large-v3-turbo ran at 3.7 times real time here. Try Parakeet through onnxruntime as a size in the model manager (the same table as the Whisper sizes), measure speed and word accuracy on our own test speech, and read its weights licence before shipping. Pairs with B7.
- **F2 Transfer queue that survives everything (Telegram-Drive).** Durable upload and download queues that recover after a restart; separate upload and download concurrency and bandwidth limits; states for flood wait, network lost and waiting; downloads written to a private temporary file and published atomically only after they verify; a native notification when a transfer finishes or needs attention. Check each against our jobs (the Media Manager already resumes after a restart) and add what is missing, starting with the temporary-file-then-publish rule and the separate limits.
- **F3 Library as a drive (Telegram-Drive).** An opt-in local REST API and WebDAV, so Explorer or a media player can mount the library; password-protected local share links that expire; Continue Watching, collections and saved searches; storage insight and cleanup; folder sync. Of these, collections and saved searches fit the library pages best; WebDAV is the one that opens the library to other programs, so it would need the same SFW and NSFW rule as every route.
- **F4 Player (FastStream).** Fill the buffer ahead with several ranged requests at once (it claims up to 6 times faster on slow lines) and a cap on the pre-buffer size; a subtitle sync tool that shifts timing while you watch; an equaliser, compressor and loudness boost ("night mode"); colour-blind filters; remappable keys. Our playback already prefetches ranges; measure ours first. The subtitle shift and the night-mode compressor (Web Audio) are the small, safe ones.
- **F5 Streaming from Telegram without a server transcode (mp4box.js, mediabunny, mpegts.js).** Read an MP4's sample table progressively, so a seek can ask Telegram for exactly the bytes of the frames needed (the moov box at the end of many files is the classic problem); cut fragments for the browser's Media Source API; hardware-accelerated frame extraction with WebCodecs, which could make the 36 snapshots without ffmpeg; transmuxing MPEG-TS to fragmented MP4 in the browser. We already have an MP4 sample index (`mp4_sample_index_gop` in the log); compare it with how mp4box.js names and orders the boxes, and test WebCodecs snapshots against the ffmpeg ones.
- **F6 Styled subtitles (jassub).** SSA/ASS subtitles with all their styling, drawn by libass compiled to WebAssembly and WebGL, without touching the page. Anime and fansub files carry them. First step, our own ASS-to-WebVTT conversion (loses styling, no new dependency); full styling only if that is not enough.
- **F7 Telegram client details (Telethon).** What its documentation says about flood waits, takeout (a session that is allowed higher limits for exporting) and incremental history by message id. We use Telethon 1.45 and handle flood waits; read the takeout and minimum-id parts before the C-items on streaming and sync. Not checked against its documentation yet. Its MIT licence is the one in our notices file; the Codeberg page's own licence field was empty.
- **F8 Client-side encryption for private channels (Telegram-Drive).** Optional encryption of an upload before it leaves the PC, with a per-file envelope, and a settings backup in Saved Messages encrypted with a passphrase. Large design cost; only if channels with private content are a wish (decision D-next).
- **F9 Faster builds (uv, ruff).** `uv pip install` in CI and in the release build instead of `pip`, with its cache: the backend job spends most of its time installing. ruff is already the linter; its formatter would only add churn.
- **F10 Hybrid search (omnigraph).** One query that merges full-text matches, vector matches and a graph step, ranked together by reciprocal rank fusion (each list adds 1 / (k + rank)). We have SQLite full text and the CLIP model; fusing "text in the title" with "looks like this picture" is a small piece of code of our own.
- **F11 Prove the Rust rewrite with recordings (rust-anything).** Record what the Python routes answer on generated and real inputs, replay every case against the Rust route, and let nothing move over until all cases match. Fits phases 5 to 9 of section 6 directly, and the parity checks we already wrote for names and faces would become the first recordings.
- **F12 One memory budget and a protected UI (pgrust).** One budget sized from the machine for caches and running jobs, and a scheduler that keeps one heavy query from starving the rest. Our "app-wide slowness" fix was the same lesson; the resource governor could grow a memory side.
- **F13 Desktop shell (tauri, Pake).** Pake shows installers under 10 MB by using the system WebView; ours carries a frozen Python backend, so that is not reachable. From tauri's plugin list, check autostart, window-state (remember size and place) and deep links (`archivex://` opens a video) before building them ourselves.
- **F14 Red-team the local assistant (Microsoft PyRIT, MIT).** A manual run of PyRIT's prompt-injection and jailbreak probes against the assistant endpoint, to check that a hostile channel name or caption cannot make it reveal settings or act. Not a CI gate; needs the assistant model installed.
- **G1 UI ideas from React Bits, Aceternity UI, Magic UI, 21st.dev (19 November).** Read on 19 November (home pages and Magic UI's component list). All four are copy-into-your-project libraries aimed at landing pages, so most of what they offer does not fit a media app; the useful part is the small interactions. We already ship `motion` 12, so every idea below is our own small component on it, with `prefers-reduced-motion` honoured. Licences (read from the repositories): Magic UI MIT, 21st.dev platform MIT (each community component has its own licence), React Bits **MIT with the Commons Clause** (not on rule 9's list, so we copy nothing), Aceternity has a free set and a paid All-Access set, and its terms were not found on the home page (treat as closed). Magic UI also sells a Pro set that is not MIT. Under rule 9 we write our own and paste none of theirs.
  - **G1a Dock for the video player and the main navigation (Magic UI's Dock idea):** icons grow toward the pointer. Useful for the rail on wide screens only.
  - **G1b Spotlight / glow border on hover for channel and movie cards:** a soft light that follows the pointer (Aceternity, Magic UI). Cheap with a CSS variable set on `pointermove`.
  - **G1c Animated number counters** on Overview and Jobs (React Bits' CountUp, Magic UI's Number Ticker): counts and sizes ease to their new value instead of jumping.
  - **G1d Shimmer text for "working" states** (Magic UI's Animated Shiny Text): the running job's title, instead of a spinner beside it.
  - **G1e Animated tabs with a sliding indicator** in Settings and Movies/Series (`layoutId` in `motion`).
  - **G1f Command palette (21st.dev style, Ctrl+K):** jump to a channel, a performer, a setting or a job. The biggest usability gain of the list.
  - **G1g Also seen and worth a look later:** Progressive Blur (soft fade where a long list scrolls under the header), Blur Fade (staggered reveal, which our skeletons mostly cover), Animated List (new jobs slide in), Border Beam (a light running round the border of a running job card).
- **G1h Not worth it:** aurora/particle/3D backgrounds (heavy for a media app, hurt battery and the player), scroll-driven hero effects (no landing page).
- **H1 Ideas from three Netflix-style apps (19 November).** Licences read from the repositories: `notdxbi/dxbi-stremio` GPL-3.0 (ideas only, we read none of its code into ours), `Subhampreet/Netflix-Clone-ReactJS` Apache-2.0 (a tutorial clone), `mehulsatardekar/movie-streaming-webapp` no licence file (all rights reserved, ideas only). Rule 9 applies: we write our own.
  - **H1a Touch gestures in the web player (dx.bi's phone player):** double-tap a side to skip 10 s, slide across to seek, slide up/down on the left for brightness and on the right for volume, hold for 2×. Matters for the mobile-ready build.
  - **H1b Skip intro / skip recap on series:** dx.bi ships tools to find intros. Ours could reuse the scene markers and the audio fingerprints we already compute; start with a manual "mark intro" button per show that applies to every episode.
  - **H1c Picture-in-picture and Chromecast** from the web player (dx.bi has both). PiP is one browser call; Chromecast needs the Cast SDK, which is Google-licensed, so check its terms before any work.
  - **H1d Hand a video to an external player and take the position back (dx.bi "Play videos in"):** we already feed VLC and MPC; the missing half is writing the position they stopped at into watch history.
  - **H1e First-run import:** dx.bi offers to copy sign-in, library and settings from the app it replaces. Ours could offer to import the old Telegram Archive data folder.
  - **H1f Unsigned-installer help in the README:** dx.bi explains the Windows "protected your PC, More info, Run anyway" screen step by step. Ours says nothing about it; a short note would save questions.
  - **H1g Home like a streaming app (all three):** a hero banner with a featured title, a Continue Watching row, rows by genre, rows that scroll sideways with arrows, and a top bar that turns solid on scroll. Mostly a layout of data we already have; SFW first.
  - **H1h Trailer on a movie page (Netflix clone):** TMDB lists trailers for a title; show a Trailer button that opens the YouTube link in the browser (no embedding, no tracking from our page).
  - **H1i Instant prefix suggestions in search (the movie webapp's trie):** suggestions from titles, performers and channels as you type. Pairs with the Ctrl+K palette (G1f); a sorted list with binary search is enough at our sizes.
- **I1 Make movies, series and adult videos feel like dx.bi (19 November).** dx.bi is GPL-3.0, so this is a list of features seen in its README and changelog, each to be written by us. It is an addon front-end for Stremio (it finds streams for you); ours is a library of what you own, so "does everything by itself" means: find what is missing, fetch it with the automation we already have (RSS, qBittorrent), then identify and file it. Checked in the web app on 19 November: there is no Continue Watching row, no Next episode button and no Skip intro yet.
  - **I1a Continue Watching and Up Next (both modes).** A Home row of what you started, with a progress bar; when an episode ends, a Next episode button with a countdown, absent on a finale; the credits button then just skips the credits. We already store resume positions (`useResume`).
  - **I1b Home you can arrange (both modes).** Rows named like a streaming app (Recently added, Popular films, Series you are watching), each switchable and reorderable (dx.bi: Customise Home). SFW rows come from TMDB lists; NSFW rows from performers, studios and tags.
  - **I1c Fill the gaps (series first).** Our Series page already lists missing episodes. Add a Get button on each that searches the sources already set up in Automation and queues the best match, shown in Jobs. The NSFW version is the same idea on a performer or studio page: scenes StashDB or ThePornDB knows that you do not have. How: ask the provider for the scene list of a performer, studio or tag (the keys in Settings already exist), subtract the scenes already matched in the library (each match stores the scene id), and show the rest with a Get button; a studio page shows "N of M scenes" like an episode count. Show a hint when videos are still unidentified, since those may cover some of the "missing" scenes. Cached per provider like the other lookups so a page opens fast.
  - **I1d Title page extras.** Reviews with Rotten Tomatoes and Metacritic scores (OMDb already gives both, and its key is in Settings), "Ends at" (now plus the time left), cast and crew links, trailer button, and MyAnimeList / AniList buttons for anime. Hide a title you never want to see.
  - **I1e Next-up intelligence for versions.** When a movie has several files, pick the one the device can play without conversion first, and say why (dx.bi tries Dolby Vision last). We already rank versions by quality; add "plays directly on this PC" to the ranking from the browser verdict `/api/play/info` returns.
  - **I1f Player panels.** A Picture and sound panel (brightness, contrast, saturation, audio delay, subtitle delay and size, one place), and for the native player the MIT-licensed Anime4K shaders that dx.bi bundles as an optional upscale for animation (check libmpv shader support in our native player first).
  - **I1g Watch together.** Two people, same position, shared pause (dx.bi's watch party). Large: needs a relay we would have to run, which conflicts with local-first. Park until asked.
  - **I1h Own place per person.** dx.bi keeps a separate resume point and Continue Watching per viewer. We have profiles; confirm history is per profile, and that NSFW history never shows in SFW.
  - **I1i NSFW-only ideas.** Scenes like this one (face, look and tag embeddings we already have), Surprise me / shuffle within a performer or studio, watch-later list, tag-based Because you watched rows, and a rule that nothing from NSFW feeds Discord presence, notifications or any "now watching" display.
  - **I1j Not for us.** Stream-source lists, debrid and torrent-as-streaming (we play files you have), TV remote navigation and the sideloaded iPhone build (separate products), the intro film.
  - **I1k Found by reading all of dx.bi (317 files: a 17,900-line plugin, a Tauri-free Electron-style app, an Android TV app, an iOS app and 36 test scripts).** Features worth our own version, in order of fit: *hover preview that unfolds into a trailer or our own snapshot strip*, *rotating billboard* on Home (featured title, rating, synopsis, Play), *colours from the artwork* on a title page, *New episode badges* and an *Airing this week* row with countdowns (TVmaze, already a keyless fallback of ours), *Dialogue boost and Night mode* and *second subtitle language* in the player, *remove black bars / zoom* for ultrawide, *stream stats overlay (D)* showing codec, bitrate and dropped frames for our own player, *watched titles dimmed or hidden* in lists, and a shortcut sheet on `?`. Anime only: filler-episode marks. Its test scripts (one per feature, run from CI) are a good habit to copy.
- **I2 Stremio addons as metadata sources (19 November). First slice built: Cinemeta as a keyless provider (switch in Settings, Metadata; `metadata/screen_ids.py`, ids kept as -(10^10 + IMDb number)), tested against live answers. Not built yet: other catalog addons, subtitle addons, pasting an addon link, Home rows and Airing this week from it.** Stremio addons are plain HTTP and JSON (`/manifest.json`, `/catalog/{type}/{id}.json`, `/meta/{type}/{id}.json`, `/subtitles/...`, `/stream/...`), so a small client of ours is enough; no SDK needed. Checked live on 19 November: Cinemeta (`v3-cinemeta.strem.io`, "the official addon for movie and series catalogs") answers without a key, and one series call returns poster, background, logo, cast, director, genres, IMDb rating, runtime, trailers, imdb/tmdb/tvdb ids and every episode with its release date (67 for Breaking Bad). That feeds airing this week, new-episode badges and the missing-episode list from one source when TMDB or OMDb keys are absent.
  - **Use:** catalog and meta addons (Cinemeta, TMDB-based catalogs, anime catalogs) for Home rows, "Airing this week" and episode dates; subtitle addons (OpenSubtitles) for the player; `addon_catalog` to let the person paste an addon link in Settings, shown like the other metadata providers, off by default.
  - **Do not use:** `stream` addons (Torrentio and similar). They return torrent or debrid links to copies we cannot show are licensed, which is not what Archive X is for (it plays files you have), and adding them would break rule 9's aim of taking on no legal exposure. Nothing in the plan depends on them.

- **E11 (done, unreleased) NudeNet (photo content tags) carried AGPL.** Now an optional download (the weights) run by our own code; the package is out of `requirements.txt`. Original finding: The package ships an AGPL-3.0 licence file (its metadata says MIT) and its model is made from Ultralytics YOLOv8 (AGPL). It is in `requirements.txt`, so the installer carries it. AGPL can be combined with our GPL, but it is the kind of term rule 9 says to avoid. Replace it (a model with a permissive licence) or drop the feature; decide in D8.
- **E12 Fonts: done.** Inter and the two unnamed fonts (`archive-x-display`, `archive-x-mono`, made with AI, origin unknown) were removed; only Outfit, with its licence text, is bundled.
- **E13 TMDB logo.** The credit sentence is in Settings, Metadata. TMDB's logo rules (themoviedb.org/about/logos-attribution) could not be opened from the build machine (the host is not allowed); save the logo file and the rules text, or allow the host, and the logo is added next to the sentence.
- **E14 Transitions.dev: done.** The motion code adapted from its snippets was rewritten from scratch (`design/motion.css`, `clearDissolve.ts`, `spinReel.ts`, `Reveal.tsx`): our own class names (`mo-`), numbers and techniques, and the library is no longer listed in the notices.
- **E15 What cannot be removed.** FFmpeg (GPL-3.0) and the LAV filters (GPL-2.0-or-later) are bundled and need the source offer already in `THIRD-PARTY-NOTICES.md` (we are GPL ourselves, so they fit). A few MPL-2.0 crates (cssparser, selectors, dtoa-short, option-ext) come in through Tauri; unchanged, they only need their source to stay available. The OpenCV and PyAV wheels carry LGPL FFmpeg parts.

- **E16 More sizes where they are safe.** Faces and content tags have one size. Faces: any other recogniser makes fingerprints that do not match the stored people, so a swap needs a "read all faces again" step first. Content tags: the 640-pixel NudeNet model is on GitHub only, which the build host cannot reach to pin a checksum; pin it from a PC that can. Captions could gain a whisper.cpp engine (B7) with the same sizes.
- **E17 Security alerts left open on purpose (19 November).** Dependabot #7 (`glib`, Linux-only, not in the Windows build) and 13 CodeQL alerts (5 high, 8 medium: the local folder picker, error text on status routes, the hand-start launch address). The reasons are in `docs/security/SCA-REPORT.md`. To make the CodeQL check pass: dismiss them on GitHub with those reasons, or change the code (generic error text, no secret in the printed address). Your choice, not made yet.
- **E18 Security scanners from the 12-repository list (19 November).** Added `security.yml` (gitleaks, Trivy, Nuclei, optional Claude Code Security Review), `tools/security/dast.py`, `.gitleaks.toml`. Left out TruffleHog (AGPL), Semgrep (LGPL), ClamAV (GPL) under rule 9; reasons in `docs/security/TOOLS.md`. Trivy is reporting only and the Nuclei job has not run yet (its template download is blocked from the build session): look at the first run and tune `TAGS` in the script if it is noisy. Add an `ANTHROPIC_API_KEY` repository secret to turn the pull-request review on.
- **E19 CodeQL now shows 8 high and 8 medium on PR #26 (19 November).** Three high alerts are new since the first report: "clear-text storage of sensitive information" in `core/diagnostics.py:343`, `engines/delivery_pipeline.py:176` and `engines/thumbnail_cache.py:876`. None was introduced by the scanner commit; the alert details cannot be read from the build session, so read them on GitHub (Security, Code scanning) and either dismiss with a reason or fix. The Rust configuration is still reported as not found on the PR. Decision left open with E17.
- **E20 Flaky local-video test, found and fixed (19 November).** `test_a_local_video_is_listed_probed_streamed_and_gets_its_snapshots` failed on CI even with a 2-minute wait, so it was never a slow runner. `local_videos.process` remembered "this folder is being prepared" by folder number only; a run left over from an earlier test, using another database, made the next run return at once, so nothing was probed. It is now keyed by database and folder, which also showed that two other tests had passed only because of the bug (their second scan was ignored while the first was still working). Tests now wait for scan threads to end (`conftest.py`), and a new test covers the clash. In the app this could only matter with two libraries open at once, which does not happen.

## 8. Decisions that are yours

- **D1** Lock Operations and Transfers in SFW? (B15)
- **D2** May the Telegram trial use your account, on a test channel you choose? (C6, Phase 0)
- **D3** Scrub the provider names from the older notes? (E5)
- **D4** Do you use automation, RSS and torrents? They are 7,300 lines that one page uses; dropping them removes a whole phase.
- **D5** Set the GitHub description and topics? (E7)
- **D6** Should downloading from sites (a yt-dlp-style source) ever be a feature? It fits technically and is a terms-of-service and legal choice.
- **D7** A phone as a remote control? The app listens only on this PC by design; a pairing code would need a security design first.
- **D8 (decided: on-demand download, done)** NudeNet: the weights are an optional download and every other model is a download in sizes too. Left over: a better or larger content detector with a permissive licence, if one turns up (E16).
- Answered: desktop app only (ADR 0005); licence GPL-3.0-or-later (ADR 0008); web player first, native player for what it cannot play.

## 9. Measured facts kept from the earlier migration rounds

- **Concurrency is the case for Rust reads, not single-request speed.** With 8 parallel clients on a 4,013-performer library, Python served 1.1 requests per second (median 7.6 s) and the Rust route 35 per second (median 217 ms). One client saw only 1.9 to 3.3 times.
- **A trap:** Rust read connections with SQLite's default 2 MB cache were slower than Python's. The same three settings Python uses (32 MB cache, 256 MB memory map, temporary tables in memory) fixed it; `ax-db` has them.
- **A Python query problem:** a four-way join multiplied rows (136,965 intermediate rows for 18 videos); separate sub-queries gave the same lists 32 times faster. The same rewrite can be applied in Python.
- **Parity:** 248 of 248 requests identical through the front door; the cross-site guard agrees with Python on all 7,938 generated cases; a write-parity harness (22 scenarios) is the check every ported write route has to pass. The schema snapshot (102 tables) fails CI when the Python schema changes unreviewed.
- **The front door** streams: a 300 MB response arrives byte for byte at about 19 MB of memory; the release binary is about 4 MB.
- **Measure on a big seeded library** (`tools/parity/seed_load.py`): the small fixture makes every route look fast.
- **Streaming files from the front door** (`tools/bench/stream_files.py`): local file 360 MB/s to about 1,950 MB/s, a 1 MB seek 3.6 ms to 1.6 ms, on one machine.

## 10. What this plan does not promise

- Telegram's speed limits and waits are Telegram's. Rust does not change them.
- The models already run in native code; Rust makes them easier to ship, not much faster.
- Counts of "unused" code are candidates, not a delete list: some code is used in ways a search cannot see.
- I cannot run the Windows installer, your Telegram account or your library here. Every phase ends with a release you try, and its result decides the next step.
