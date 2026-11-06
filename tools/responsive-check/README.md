# Responsive check (developer tool, not used by the app)
Drives the real UI in headless Chromium at 17 viewport sizes (320x568 ... 2560x1440, plus phone landscape) across 12 screens
and reports: horizontal overflow, off-screen fixed elements, text under 10.5px, touch targets under 30px, clipped text.
It also saves screenshots and can build contact sheets.

Test-only setup (never run against real data):
1. `ARCHIVE_X_DATA_DIR=/tmp/axdata ARCHIVE_X_AUTO_OPEN_BROWSER=0 python run.py` (use a throwaway data dir; pip install websockets)
2. Open https://127.0.0.1:8443 once to create the admin `admin` / `correct-horse-battery` (the script does this through /api/auth/setup).
3. `python seed_demo_data.py` (edit the DB path inside) inserts 72 demo videos, 12 performers, 6 studios and makes the library view show them.
   The server recreates its views periodically, so sweep.js re-seeds when the library looks empty.
4. `NODE_PATH=$(npm root -g) TAG=run1 SHOT=390x844,1440x900 node sweep.js`
Needs Node 20+ with `playwright` installed, and Pillow for contact_sheets.py. Paths in the scripts assume this repository layout: adjust BASE/paths at the top.
