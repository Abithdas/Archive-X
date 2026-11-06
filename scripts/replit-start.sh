#!/usr/bin/env bash
# Start Archive X on Replit (hosted mode: plain HTTP behind Replit's HTTPS proxy).
# Required secret: ARCHIVE_X_ADMIN_PASSWORD (12+ characters). Optional: ARCHIVE_X_ADMIN_USERNAME (default "admin").
set -euo pipefail
cd "$(dirname "$0")/.."

export ARCHIVE_X_HOSTED=1
export ARCHIVE_X_AUTO_OPEN_BROWSER=0
# Keep data in the Repl's persistent files, not the (ephemeral) home directory.
export ARCHIVE_X_DATA_DIR="${ARCHIVE_X_DATA_DIR:-$PWD/.archive-x-data}"

if [ -z "${ARCHIVE_X_ADMIN_PASSWORD:-}" ]; then
  echo "Add a Replit Secret named ARCHIVE_X_ADMIN_PASSWORD (12+ characters), then press Run again." >&2
  exit 1
fi

PY="${PYTHON:-python3}"
if ! "$PY" -c "import fastapi, telethon, uvicorn" 2>/dev/null; then
  "$PY" -m pip install --quiet -r requirements.txt
fi

if [ ! -f frontend/dist/index.html ] && command -v npm >/dev/null 2>&1; then
  (cd frontend && npm ci --no-audit --no-fund && npm run build)
fi

exec "$PY" -c "from telegram_archive.main import main; main()"
