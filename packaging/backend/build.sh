#!/usr/bin/env bash
# Freeze the backend into packaging/backend/dist/archive-x-backend/ (Linux/macOS; use build.ps1 on Windows).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${PYTHON:-python3}"
"$PY" -m pip install -q pyinstaller -r requirements.txt
"$PY" -m PyInstaller --noconfirm --clean --distpath packaging/backend/dist --workpath packaging/backend/build packaging/backend/archive_x_backend.spec
echo "built: packaging/backend/dist/archive-x-backend/"
du -sh packaging/backend/dist/archive-x-backend
