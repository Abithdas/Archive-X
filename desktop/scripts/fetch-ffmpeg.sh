#!/usr/bin/env bash
# Download a pinned static ffmpeg build into desktop/src-tauri/resources/ffmpeg/ (bundled by Tauri).
# Fails closed: a download URL and its SHA-256 must both be given, so a changed upstream file cannot slip in.
#   FFMPEG_URL=https://.../ffmpeg-n7.1-latest-win64-gpl-7.1.zip FFMPEG_SHA256=<hex> ./fetch-ffmpeg.sh
set -euo pipefail
# Convenience for CI when no pin is configured: take BtbN's current release and verify it against the
# checksums.sha256 published beside it (guards against corruption, NOT against a compromised release; pin for releases).
if [ -z "${FFMPEG_URL:-}" ] && [ "${FFMPEG_FROM_BTBN:-0}" = "1" ]; then
  base=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest
  # BtbN renames these as releases move on; override with FFMPEG_ASSET (see checksums.sha256 beside the download)
  asset="${FFMPEG_ASSET:-ffmpeg-n8.1-latest-win64-gpl-8.1.zip}"
  FFMPEG_URL="$base/$asset"
  FFMPEG_SHA256="$(curl -fsSL "$base/checksums.sha256" | awk -v a="$asset" '$2==a||$2=="*"a{print $1}')"
  [ -n "$FFMPEG_SHA256" ] || { echo "ffmpeg: no checksum listed for $asset in $base/checksums.sha256 (renamed? set FFMPEG_ASSET)" >&2; exit 1; }
  echo "ffmpeg: $FFMPEG_URL sha256=$FFMPEG_SHA256"
fi
: "${FFMPEG_URL:?set FFMPEG_URL to a pinned release asset}" "${FFMPEG_SHA256:?set FFMPEG_SHA256}"
out="$(dirname "$0")/../src-tauri/resources/ffmpeg"; tmp="$(mktemp -d)"; mkdir -p "$out"
curl -fsSL "$FFMPEG_URL" -o "$tmp/ffmpeg.zip"
echo "$FFMPEG_SHA256  $tmp/ffmpeg.zip" | sha256sum -c -
unzip -q -j "$tmp/ffmpeg.zip" '*/bin/ffmpeg*' '*/bin/ffprobe*' -d "$out"
ls -la "$out"
