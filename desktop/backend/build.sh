#!/usr/bin/env bash
# Freeze the backend into desktop/backend/dist/archive-x-backend/ (Linux/macOS; use build.ps1 on Windows).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
"$PY" -m pip install -q pyinstaller -r requirements.txt
"$PY" -m PyInstaller --noconfirm --clean --distpath desktop/backend/dist --workpath desktop/backend/build desktop/backend/archive_x_backend.spec
echo "built: desktop/backend/dist/archive-x-backend/"
du -sh desktop/backend/dist/archive-x-backend
