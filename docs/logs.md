# Logs and diagnostics for support

**Operations → Recent activity → Download logs (.zip)** makes one zip with everything needed to look into a problem. It is named with the
PC's date and time, for example `archive-x-logs-2026-11-14_08-12-45.zip`.

- **Desktop app:** the zip is saved into your **Downloads** folder (an app window cannot always show a save dialog) and a message says where.
- **In a browser:** an ordinary download.

## What is inside

| Folder or file | Content |
|---|---|
| `logs/` | the application's log files (`playback.log`, `launcher-cli.log`, `system_events.jsonl`, the watchdog logs, ...): up to the last 8 MB of each |
| `activity/events.txt` | the Recent activity list (last 5,000 entries) |
| `diagnostics/report.txt` | the canonical diagnostic report (the same one `/api/diagnostics/report` makes) |
| `diagnostics/playback-and-events.json` | the playback snapshot and the last 300 structured events |
| `diagnostics/ignored-errors.json` | errors the app caught and carried on from, most frequent first |
| `system/system.txt` | app version, Windows version, CPU cores, free memory and disk, ffmpeg version, whether the native player is installed |
| `system/environment.txt` | the `ARCHIVE_X_*` settings of this run (anything that looks like a secret is blanked) |
| `manifest.json`, `README.txt` | every file with its size and whether it was cut, and everything that was left out, with the reason |

A part that fails or takes too long (the diagnostic report is limited to 25 seconds) is listed in `diagnostics/collection-errors.txt`
instead of losing the rest. The whole bundle is limited to 80 MB.

## What is blanked or left out

Passwords, API keys and hashes, tokens, cookies, phone numbers and the signed part of stream addresses (`?t=...`) become `<REDACTED>`.
Telegram sessions, certificates and keys, databases and cookie files are never included. File and folder names, channel names and video
titles are **not** removed: they are often what shows the problem. Nothing is sent anywhere by Archive X; you send the file.

Code: `telegram_archive/core/log_bundle.py`, the route in `web/routes_tags_logs.py` (`GET /api/diagnostics/logs.zip`, `?save=1` from this PC only),
the button in `apps/web/src/features/operations/OperationsPage.tsx`. Tests: `tools/tests/test_log_bundle.py`, `logBundle.test.ts`.
