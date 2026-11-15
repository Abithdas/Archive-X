# Changelog

One entry per release, newest first. Releases are published from the Desktop workflow; the installer and update files are on the GitHub release page. Entries before 30.3.0 are written from the commit history, and describe what was built; some of it was first tried on a real PC only afterwards.

## Unreleased (after 30.2.6)

- **StashDB fingerprint lookups work again:** StashDB removed the query Archive X used for file fingerprints (`findScenesByFullFingerprints`), so every OSHASH lookup failed with HTTP 422 (21 of 21 in the last log: "0 matched, 21 failed"). The lookup now uses the current query.
- **Freezes, part 3:** the channel scan held the database lock for the whole batch, so the app waited for all of it (a 95 s freeze, after which the watchdog stopped the job); it now holds the lock for one record at a time. Two queries over every linked performer moved off the thread that serves the app (13 s).
- **Faster start:** the clean-up of old cache leftovers (11 s of an 18 s start) runs beside the start instead of before it.

- **Fewer freezes, part 2:** the similar-name search in the performer identity check skips names that share too few letters to be similar (about 6 times faster again, the same answers); the diagnostics snapshot no longer asks Windows for the python processes on the thread that serves the app (it froze the app for 8 s).

## 30.2.6 (2026-11-15)

- **Fewer freezes (from a real log):** the log of 30.2.4 showed the whole app freezing for 8 to 15 seconds, 13 times in about 5 hours, once for 202 seconds. Two causes found and fixed: the performer identity check read every performer (biography included) and compared names against all of them on every call, on the thread that serves the app (now an index kept until the performers change; the answers are the same, tested against the old algorithm; about 6 times faster on the first call and far more after, measured on 30,000 performers); and the channel scan wrote thousands of rows, with a commit each, on that same thread (now on a worker thread). A swallowed error that appeared 22 times (`DirectTelegramRangeSource` had no `close`) is fixed.

- **Removed (backend):** the second way to run the Python server, which the desktop app never used: HTTPS with a local certificate authority, access from other devices on the network, HTTP/3, the phone certificate download (`/ca.crt`) and the `hypercorn` dependency. `python -m telegram_archive` now always runs the loopback desktop mode and prints the hand-off link when started by hand. The installer's backend is a little smaller.

## 30.2.5 (2026-11-15)

- **Fix (the Rust routes in 30.2.4 did not start in the real desktop app):** the front door's own question to the backend ("where is the database?") was refused by the backend's desktop guard, so in 30.2.4 every route that reads the database stayed with Python (nothing broke, nothing was faster). The front door now sends the launch secret with its own calls, and routes it answers itself require the launch secret or cookie, as the backend does (found by running the front door in front of a backend in desktop mode, which the earlier tests did not).

- **Sign-in check:** the performer name list and face learning check the session in the database (as the other Rust routes do) instead of asking Python on every request.

## 30.2.4 (2026-11-15)

- **Native player:** the video's own window is the default layer (the one that showed the picture on a real PC); Back no longer freezes the app; less lag from the on-screen check; a quicker start on network streams; jumps show where they are going.
- **Playback:** jumping into the middle of an MP4 that the browser plays through the in-browser remux now works; the player offers the native player when the browser cannot decode a file; leaving a video releases its server job.
- **Performers and studios:** missing pictures are fetched in the background (and with a "Fetch now" button); SVG logos with a standard editor doctype are accepted; a button restores genders that editing a picture used to wipe; searching accepts part of a name in any order.
- **People:** the name search can be cancelled and only one is open at a time.
- **Performance and stability:** the performer page no longer freezes the whole app; the AI page no longer looks empty while it loads; the watchdog guardian, which could not start in the installed app, is off there; reopening the app straight after closing it works.
- **Rust front door (all on in the desktop app):** the Videos list, tags, photos list, studio details, favourites, scene markers and watch progress are answered by the Rust front door instead of Python (the front door finds the database through the backend; `ARCHIVE_X_NATIVE_ROUTES=none` switches them off). Rust now writes watch history, performer and studio favourites and scene markers (ADR 0007). A race that could stop the first performer-name refresh on Windows ("database is locked") is fixed.
- **Licence:** GPL-3.0-or-later (ADR 0008); `LICENSE` and `THIRD-PARTY-NOTICES.md` are in the repository and the installer.
- **Housekeeping:** one command runs every test; one version number; unused frontend code removed; a counter records which API routes are used (for the clean-up); documents reorganised under `docs/`.

## 30.2.2 (2026-11-14)

- Native player: a switch between three video layers (in front, behind, own window) and a self-diagnosing report (frames drawn, what is on screen, window order).
- Performer name list kept on the PC (StashDB and ThePornDB), refreshed in the background, searched instantly (Rust).
- Face learning (Rust): remembers faces taken out of a person, shows the faces that look out of place, suggests look-alikes in other groups.
- Fixes: editing a performer's picture no longer clears their gender; Filters "Reset" applies at once; the "Advanced: merge, split and review groups" section is gone; the Windows reopen race; security updates (Pillow 13, cryptography 50, rusqlite 0.42, vitest 5.1, GitHub Actions pinned to commits).

## 30.2.1 (2026-11-14)

- Native player: the video window in front of the page by default, with a switch; the window's real state shown on the page.
- Download logs: one zip of every log and diagnostic, named with the date and time (Operations, Recent activity).

## 30.2.0 (2026-11-14)

- Native video player built from MPC-HC's decoding parts (LAV Filters and the Enhanced Video Renderer) with Windows hardware decoding: audio worked; the picture did not show on the first real-PC test.
- Fixes: live-caption lag after skipping; seek preview drift on a zoomed page; face-review tiles no longer overlap; the Videos page loads every channel on a fresh start; the Transfers page layout.

## 30.1.1 and 30.1.0 (2026-11-14)

- The Windows installer builds on GitHub Actions; the Rust front door is on by default; in-app updates through GitHub Releases (they work once the repository is public); the library list query is about 4.7 times faster.
