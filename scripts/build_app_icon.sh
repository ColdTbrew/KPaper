#!/usr/bin/env sh
# Rebuild the macOS icon sizes from the tracked high-resolution artwork.
set -eu
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
SOURCE="$ROOT_DIR/macos-app/Resources/AppIcon.png"
ICON_WORK="$(mktemp -d "${TMPDIR:-/tmp}/kpaper-icon.XXXXXX")"
trap 'rm -rf "$ICON_WORK"' EXIT HUP INT TERM
ICONSET="$ICON_WORK/AppIcon.iconset"
mkdir -p "$ICONSET"
for SIZE in 16 32 128 256 512; do
  DOUBLE_SIZE=$((SIZE * 2))
  sips -z "$SIZE" "$SIZE" "$SOURCE" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null
  sips -z "$DOUBLE_SIZE" "$DOUBLE_SIZE" "$SOURCE" --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$ROOT_DIR/macos-app/Resources/AppIcon.icns"
echo "Rebuilt macos-app/Resources/AppIcon.icns"
