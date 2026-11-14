#!/usr/bin/env bash
# Fetch LAV Filters (the decoding parts MPC-HC uses).
#   fetch-lav.sh            the official x64 binaries -> desktop/src-tauri/resources/lav/  (bundled by Tauri)
#   fetch-lav.sh --headers  the LAV source's include folder -> desktop/axplayer/third_party/lavfilters (to build axplayer against)
# LAV_TAG picks the release (default below, with its checksum). The downloaded zip must match the checksum, so a changed upstream file cannot slip in.
# Needs the gh command (GH_TOKEN) for the download.
set -euo pipefail
# The release this app is built and tested with, and the SHA-256 of its x64 zip (the same in every CI run). To move to another release, set LAV_TAG and
# LAV_SHA256 together (the first run without a checksum prints it); an unlisted download is refused when a checksum is known.
DEFAULT_TAG="0.83"
DEFAULT_SHA256="0126982f47157bb86a6dbb43c4f332f7f98beba9ad552c19b65f9db2e7d4f186"
DEFAULT_COMMIT="fefb6987994ed56e4525e8a125f5fbb53707bc52"   # the commit tag 0.83 points to (a tag can be moved, a commit cannot)
TAG="${LAV_TAG:-$DEFAULT_TAG}"
if [ -z "${LAV_SHA256:-}" ] && [ "$TAG" = "$DEFAULT_TAG" ]; then LAV_SHA256="$DEFAULT_SHA256"; fi
REPO="Nevcairiel/LAVFilters"
here="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = "--headers" ]; then
  dest="$here/../axplayer/third_party/lavfilters"
  rm -rf "$dest"
  git clone --quiet --depth 1 --branch "$TAG" "https://github.com/$REPO" "$dest"
  if [ "$TAG" = "$DEFAULT_TAG" ] && [ "$(git -C "$dest" rev-parse HEAD)" != "$DEFAULT_COMMIT" ]; then
    echo "LAV tag $TAG no longer points at $DEFAULT_COMMIT: refusing to build against changed headers" >&2
    exit 1
  fi
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
cp "$src"/*.manifest "$out"/   # LAVFilters.Dependencies.manifest: Windows needs it beside the filters to find their helper DLLs (error 0x800736B1 without it)
for f in $(find "$tmp/x" -maxdepth 2 -iname 'COPYING*' -o -maxdepth 2 -iname 'LICENSE*' | head -n 3); do cp "$f" "$out"/; done
echo "LAV $TAG ready in $out:"; ls -la "$out"
