#!/usr/bin/env bash
# Fetch libmpv (the mpv player as a library) -> apps/desktop/src-tauri/resources/mpv/libmpv-2.dll (bundled by Tauri), for the native player's libmpv engine
# (native/axplayer/src/axmpv.cpp). It comes from the mpv Windows builds on SourceForge (the project the mpv website points to).
# MPV_BUILD picks the file (default below, with its SHA-256): the download must match the checksum, so a changed upstream file cannot slip in.
# To move to another build, set MPV_BUILD and MPV_SHA256 together (the first run without a checksum prints it). Needs 7z.
set -euo pipefail
DEFAULT_BUILD="mpv-dev-x86_64-20261108-git-55d3feb45c.7z"
DEFAULT_SHA256="7c147cb7ceaba717414e782ef4398de728f679db6e86daaa8aeb175670cb7994"
BUILD="${MPV_BUILD:-$DEFAULT_BUILD}"
if [ -z "${MPV_SHA256:-}" ] && [ "$BUILD" = "$DEFAULT_BUILD" ]; then MPV_SHA256="$DEFAULT_SHA256"; fi
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/../../apps/desktop/src-tauri/resources/mpv}"
tmp="$(mktemp -d)"
curl -fsSL --retry 3 -o "$tmp/mpv.7z" "https://sourceforge.net/projects/mpv-player-windows/files/libmpv/$BUILD/download"
sha="$(sha256sum "$tmp/mpv.7z" | cut -d' ' -f1)"
echo "libmpv build: $BUILD  sha256=$sha"
if [ -n "${MPV_SHA256:-}" ] && [ "$sha" != "$MPV_SHA256" ]; then
  echo "libmpv checksum mismatch: expected $MPV_SHA256" >&2
  exit 1
fi
mkdir -p "$out"
7z x -y "-o$tmp/x" "$tmp/mpv.7z" libmpv-2.dll >/dev/null
cp "$tmp/x/libmpv-2.dll" "$out/libmpv-2.dll"
ls -la "$out"
