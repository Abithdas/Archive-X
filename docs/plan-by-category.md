# Archive X: the plan, by category

The same items as [plan.md](plan.md) (ids are unchanged: say "do B5 and H1a"), grouped by what they are about instead of by when they were written. Status: **Done** (built and released or on main), **Partly** (a first slice exists), **Open** (not started), **Needs you** (only you can do or decide it), **Parked** (not wanted now). Done items from this list were checked against the 30.8.0 release notes; anything not named there is shown as Open.

Within each category the rows run from least to most effort. **Effort** is my estimate of building it, S: under an hour; M: a few hours, one small release item; L: a day or more; XL: several releases. For "Needs you" rows it is your time, not mine. Done rows come first.

## 1. Playing and watching

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| A4 | Native player on a file the web view cannot play (AVI, DTS/AC3, 10-bit HEVC) | Needs you (real PC) | S |
| A2 | Live captions on a Telegram video | Needs you (real PC) | S |
| B6 | Remembered speed, quality cap, "why native" chip | Open | M |
| H1c | Picture-in-picture (button exists) and Chromecast | Partly | M |
| H1d | External player hands the position back to watch history | Open | M |
| I1a | Continue Watching (SFW Home has it); Next episode button with countdown | Partly | M |
| F4 / F6 | Subtitle shift, night mode, ASS subtitles | Open | M |
| Native clicks | Click and double-click on the native video window (tap to pause, double-tap for full screen) | Open | M |
| Subtitles | A "Find subtitles" download (OpenSubtitles is used for identification only today) | Open | M |
| B5 / H1b | Skip intro and recap, marked once per season | Open | L |
| B7 / F1 | Captions that cannot get stuck on a GPU problem: whisper.cpp, Parakeet as a second engine | Open | L |
| H1a | Touch gestures in the web player (double-tap, slide, hold for 2x) | Open | L |
| I1e / I1f | Prefer the file that plays directly; Picture and sound panel; Anime4K upscale | Open | L |

## 2. Home, discovery and pages

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| H1h | Trailer link on a movie page | Done (in Discover details) | Done |
| I2 | Stremio catalog addons | Partly (Cinemeta only; pasting other addons not built) | M |
| G1f / H1i | Command palette (Ctrl K exists) with instant suggestions | Partly | M |
| G1a-e, G1g | Small interactions: dock, glow cards, number counters, shimmer, sliding tabs | Open | M |
| B10 | Cast photos, posters on Videos cards, TVDB | Partly (cast and studio pictures from TMDB done in 30.8.0) | M |
| H1g / I1b | Home like a streaming app: billboard, rows, Discover | Partly (SFW Home and Discover done; rows are not yet rearrangeable; NSFW home is not redone) | L |
| I1d / I1k | Title page extras: scores, "ends at", hover preview, New episode badges | Partly (Airing this week done) | L |
| I1h / I1i | Per-profile history; NSFW-only ideas (similar scenes, shuffle, watch later) | Open | L |

## 3. Identification and metadata

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| A3 / A8 | Real provider keys and a real identification run | Needs you (real PC) | S |
| E13 | TMDB logo file and attribution rules | Open (host blocked here) | S |
| B1 | Explainable matching and "not this one" | Open | M |
| B4 | Check the name parser (specials, multi-episode, anime) | Open | M |
| B3 | One interface for all metadata providers | Open | L |
| I1c | Fill the gaps: Get button for missing episodes and scenes | Open | L |

## 4. Library keeping

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| A5 / A6 | Upload a folder to real Telegram; Windows paths and names | Needs you (real PC) | S |
| B11 | Folder upload: subfolders, split uploads | Open | M |
| B8 | Better-copy rule | Open | L |
| B9 | Rename and organise a local folder, with preview | Open | L |
| B12 / C4 | Faces and duplicates: compare tools, fingerprints on the 36 snapshots | Open | L |
| F2 / F3 | Durable transfer queue; library as a drive (WebDAV), collections, saved searches | Open | XL |
| F8 | Client-side encryption for private channels | Parked | XL |

## 5. Settings, health and polish

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| B15 / D1 | Lock Operations and Transfers in SFW | Needs you (D1) | S |
| H1e / H1f | First-run import of old data; unsigned-installer help in the README | Open | S |
| A1 / A7 | Install over the old build; real library checks in SFW | Needs you (real PC) | S |
| B2 | Health page: what is wrong now, each with a fix link | Open | M |
| B13 | Update channel (stable or beta), changelog after an update | Open | M |
| B14 | Run-your-own-command hooks | Open | M |
| F13 | Desktop shell: autostart, remember window, `archivex://` links | Open | L |

## 6. Streaming and data layer

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| C1 | Front door serves local files and finished copies | Done | Done |
| C5 | Content-mode rules in one Rust place | Open | M |
| C2 / C3 | `ax://` thumbnails; batched scan writes and cursor paging | Open | L |
| F5 / F7 | Progressive MP4 reads; Telethon flood-wait and takeout notes | Open | L |
| C6 / C7 | Telegram client in Rust (trial); Telegram cache hits from Rust | Needs you (D2) | XL |
| C8 | Hosted product | Parked | XL |

## 7. Rust migration (section 6 of the plan)

| Phase | What | Status |
| --- | --- | --- |
| 0 | Prove Telegram (grammers) and the models | Needs you |
| 1 | Reading in Rust | Mostly done |
| 2 | Streaming | Done for local files; Telegram stays in Python until C6 |
| 3 | Writes without Telegram | Partly |
| 4 | Login and sessions | Mostly moot |
| 5-8 | Jobs, local AI, Telegram, providers and automation | Open (decision D4 first) |
| 9 | Remove Python | Open |
| F11 | Record Python answers, replay against Rust | Open |

## 8. Security and licences

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| E11 / E12 / E14 | NudeNet AGPL, fonts, transitions code | Done | Done |
| E15 | Bundled FFmpeg, LAV filters, MPL crates: notices stay | Done (nothing to change) | Done |
| E17 / E19 | CodeQL alerts (about 15) and one Dependabot alert left open on purpose | Needs you (dismiss on GitHub with the reasons in `docs/security/SCA-REPORT.md`) | S |
| E18 | Security scanners: first run of Trivy and Nuclei to tune; add `ANTHROPIC_API_KEY` for PR review | Needs you | S |
| F14 | Red-team the local assistant (PyRIT) | Open | M |

## 9. Clean-up and housekeeping

| Id | What | Status | Effort |
| --- | --- | --- | --- |
| E20 | Flaky local-video test | Done | Done |
| E3 / E4 | Unused variables; regenerate `docs/api/` | Open | S |
| E5 / D3 | Scrub adult provider names from old notes | Needs you (D3) | S |
| E6 | Delete old `deliverables/` zips | Open | S |
| E7 / D5 | GitHub description and topics | Needs you (D5) | S |
| Branches | Delete `archive-x-desktop-app` and the four Dependabot branches on GitHub | Needs you (this session cannot) | S |
| Release | The README, UI reference and "personal project" wording are on main but not in a release | Needs you ("publish") | S |
| E8 / E9 | Two timing-sensitive web tests; one order-dependent Python test | Open | M |
| E10 / E16 | Caption model on an NVIDIA card; more model sizes | Open | M |
| F9 | Faster CI builds with uv | Open | M |
| E1 / E2 | Delete conversion and unused routes (106 of 325 had no caller) | Open (wait a week of logs) | L |

## 10. Decisions that are yours

D1 lock Operations and Transfers in SFW. D2 Telegram trial on a test channel. D3 scrub provider names from old notes. D4 keep or drop automation, RSS and torrents (7,300 lines). D5 GitHub description and topics. D6 downloading from sites (a yt-dlp-style source). D7 phone as a remote. D8 decided.

## Suggested order

1. **Things only you can do, in one sitting:** A1, A4, A7 on the installed 30.8.0 (they find the real bugs), delete the five branches, set D5, dismiss the CodeQL alerts.
2. **Next release worth building:** B2 (health page), B1 (explainable matching), I1a (Next episode button), B5 (skip intro).
3. **Then:** the player extras (H1a gestures, native clicks, Find subtitles), then B3 and B4 before adding more providers.
4. **Last:** the Rust migration phases, after D4.
