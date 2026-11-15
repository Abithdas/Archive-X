# 0005. The desktop app is the only supported way to run Archive X

**Status:** accepted 15 November 2026 (the owner confirmed they run only the desktop app).

**Context.** Two ways to start the app (the desktop app, and `.bat`/`.ps1`/`run.py` scripts with hosted Replit mode) meant two sets of bugs. The watchdog guardian crash loop came from script-launcher code running inside the installed app.

**Decision.** The script launchers, `run.py`, the Replit files, hosted mode, the browser auto-open and the watchdog guardian were removed. From a checkout the backend runs with `python -m telegram_archive`, for development and tests.

**Consequences.** There is no supported browser-only or LAN deployment. The generic server mode (HTTPS certificate, LAN address, web login) still exists in the code because tests and the parity harness use it; it is removed with the Python backend.
