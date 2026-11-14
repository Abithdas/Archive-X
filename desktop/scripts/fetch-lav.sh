#!/usr/bin/env bash
# Fetch LAV Filters (the decoding parts MPC-HC uses).
#   fetch-lav.sh            the official x64 binaries -> desktop/src-tauri/resources/lav/  (bundled by Tauri)
#   fetch-lav.sh --headers  the LAV source's include folder -> desktop/axplayer/third_party/lavfilters (to build axplayer against)
# LAV_TAG picks the release (default below). LAV_SHA256, when set, must match the downloaded zip, so a changed upstream file cannot slip in;
# without it the checksum is only printed (copy it into the workflow to pin it). Needs the gh command (GH_TOKEN) for the download.
set -euo pipefail
TAG="${LAV_TAG:-0.83}"
REPO="Nevcairiel/LAVFilters"
here="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "--headers" ]; then
  dest="$here/../axplayer/third_party/lavfilters"
  rm -rf "$dest"
  git clone --quiet --depth 1 --branch "$TAG" "https://github.com/$REPO" "$dest"
  echo "LAV headers $TAG: $(ls "$dest/include" | wc -l) files in $dest/include"
  exit 0
fi

out="$here/../src-tauri/resources/lav"
tmp="$(mktemp -d)"
gh release download "$TAG" --repo "$REPO" --pattern '*.zip' --dir "$tmp"
echo "downloaded:"; ls -la "$tmp"
zip="$(ls "$tmp"/*.zip | grep -vi 'x86\|win32' | head -n 1 || true)"
[ -n "$zip" ] || zip="$(ls "$tmp"/*.zip | head -n 1)"
sha="$(sha256sum "$zip" | cut -d' ' -f1)"
echo "LAV zip: $(basename "$zip")  sha256=$sha"
if [ -n "${LAV_SHA256:-}" ] && [ "$sha" != "$LAV_SHA256" ]; then
  echo "LAV checksum mismatch: expected $LAV_SHA256" >&2
  exit 1
fi
unzip -q -o "$zip" -d "$tmp/x"
echo "layout:"; (cd "$tmp/x" && find . -maxdepth 3 | head -n 40)
video="$(find "$tmp/x" -ipath '*x64*' -name LAVVideo.ax | head -n 1)"
[ -n "$video" ] || video="$(find "$tmp/x" -name LAVVideo.ax | head -n 1)"
[ -n "$video" ] || { echo "LAVVideo.ax not found in the release" >&2; exit 1; }
src="$(dirname "$video")"
rm -rf "$out"; mkdir -p "$out"
cp "$src"/*.ax "$out"/
cp "$src"/*.dll "$out"/ 2>/dev/null || true
for f in $(find "$tmp/x" -maxdepth 2 -iname 'COPYING*' -o -maxdepth 2 -iname 'LICENSE*' | head -n 3); do cp "$f" "$out"/; done
echo "LAV $TAG ready in $out:"; ls -la "$out"
