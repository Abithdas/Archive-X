#!/usr/bin/env bash
# Download a pinned static ffmpeg build into desktop/src-tauri/resources/ffmpeg/ (bundled by Tauri).
# Fails closed: a download URL and its SHA-256 must both be given, so a changed upstream file cannot slip in.
#   FFMPEG_URL=https://.../ffmpeg-n7.1-latest-win64-gpl-7.1.zip FFMPEG_SHA256=<hex> ./fetch-ffmpeg.sh
set -euo pipefail
: "${FFMPEG_URL:?set FFMPEG_URL to a pinned release asset}" "${FFMPEG_SHA256:?set FFMPEG_SHA256}"
out="$(dirname "$0")/../src-tauri/resources/ffmpeg"; tmp="$(mktemp -d)"; mkdir -p "$out"
curl -fsSL "$FFMPEG_URL" -o "$tmp/ffmpeg.zip"
echo "$FFMPEG_SHA256  $tmp/ffmpeg.zip" | sha256sum -c -
unzip -q -j "$tmp/ffmpeg.zip" '*/bin/ffmpeg*' '*/bin/ffprobe*' -d "$out"
ls -la "$out"
