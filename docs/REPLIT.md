# Running Archive X on Replit

Hosted mode serves the backend and both web UIs (classic at `/`, React at `/app-next/`) from a Repl. The Tauri desktop shell and the Windows .exe are not part of this.

1. In Replit choose **Import from GitHub** and pick this repository and branch.
2. Open **Secrets** and add `ARCHIVE_X_ADMIN_PASSWORD` (12+ characters). Optionally add `ARCHIVE_X_ADMIN_USERNAME` (default `admin`).
3. Press **Run**. `scripts/replit-start.sh` installs `requirements.txt`, builds `frontend/` if `frontend/dist` is missing, and starts the server.
4. Open the Repl URL and sign in with the secret login. Then add your Telegram API credentials under Settings > Telegram.

## How hosted mode differs
- `ARCHIVE_X_HOSTED=1`: plain HTTP on `$PORT` (default 8443). Replit's proxy provides HTTPS; no certificate is generated and the browser is not opened.
- The admin login is created from the secrets at first start. The usual "finish setup on the PC" step is not possible on a hosted URL, and leaving setup open would let the first visitor claim the account. The server refuses to start without the secret.
- Session cookies are `Secure; SameSite=None; Partitioned` so sign-in works inside Replit's embedded preview.
- Data lives in `.archive-x-data/` (set `ARCHIVE_X_DATA_DIR` to change). It is git-ignored. Back it up from Settings > Storage & Backup.

## Limits
- Replit disk is limited; keep the video byte-cache budget small (Settings > Storage, default 50 GiB is too large).
- LAN/phone certificate setup and local-player features do not apply.
- For always-on use, publish as a Reserved VM deployment; an unpublished Repl sleeps when idle.

Tested in this repo by `tools/tests/test_hosted_mode.py`. A real Replit run has not been done yet.
