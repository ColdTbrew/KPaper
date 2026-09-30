#!/usr/bin/env sh
set -eu

ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
APP_NAME="KPaper"
PACKAGE_DIR="$ROOT_DIR/macos-app"
DIST_DIR="$ROOT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS_DIR="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

BUILD_LOG="$(mktemp "${TMPDIR:-/tmp}/kpaper-build.XXXXXX")"
SDK_LIST="$(mktemp "${TMPDIR:-/tmp}/kpaper-sdks.XXXXXX")"
trap 'rm -f "$BUILD_LOG" "$SDK_LIST"' EXIT HUP INT TERM
SWIFT_SDK="${KPAPER_SWIFT_SDK:-}"

build_release() {
  if [ -n "$SWIFT_SDK" ]; then
    swift build --package-path "$PACKAGE_DIR" -c release --build-system native --sdk "$SWIFT_SDK" > "$BUILD_LOG" 2>&1
  else
    swift build --package-path "$PACKAGE_DIR" -c release --build-system native > "$BUILD_LOG" 2>&1
  fi
}

if ! build_release; then
  cat "$BUILD_LOG" >&2
  # A newer SDK can reference compiler plugins absent from the active Swift
  # toolchain. Retry an installed older SDK only for that specific mismatch.
  if [ -n "$SWIFT_SDK" ] || ! grep -Eq 'SwiftUIMacros|external macro implementation.*could not be found|compiler plugin.*not found' "$BUILD_LOG"; then
    exit 1
  fi
  DEFAULT_SDK="$(xcrun --sdk macosx --show-sdk-path)"
  DEFAULT_SDK="$(CDPATH= cd -- "$DEFAULT_SDK" && pwd -P)"
  SDK_PARENT="$(dirname -- "$DEFAULT_SDK")"
  BUILD_OK=false
  # Include Command Line Tools SDKs when Xcode is the active developer dir.
  find "$SDK_PARENT" /Library/Developer/CommandLineTools/SDKs -maxdepth 1 -name 'MacOSX[0-9]*.sdk' -print 2>/dev/null | sort -ru > "$SDK_LIST"
  while IFS= read -r candidate; do
    candidate="$(CDPATH= cd -- "$candidate" && pwd -P)"
    [ "$candidate" != "$DEFAULT_SDK" ] || continue
    SWIFT_SDK="$candidate"
    echo "Retrying release build with SDK: $SWIFT_SDK" >&2
    if build_release; then
      BUILD_OK=true
      break
    fi
    cat "$BUILD_LOG" >&2
    grep -Eq 'SwiftUIMacros|external macro implementation.*could not be found|compiler plugin.*not found' "$BUILD_LOG" || exit 1
  done < "$SDK_LIST"
  [ "$BUILD_OK" = true ] || exit 1
fi
cat "$BUILD_LOG"

if [ -n "$SWIFT_SDK" ]; then
  BIN_DIR="$(swift build --package-path "$PACKAGE_DIR" -c release --build-system native --sdk "$SWIFT_SDK" --show-bin-path)"
else
  BIN_DIR="$(swift build --package-path "$PACKAGE_DIR" -c release --build-system native --show-bin-path)"
fi

rm -rf "$APP_DIR"
mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"
cp "$BIN_DIR/KPaperMac" "$MACOS_DIR/KPaperMac"
cp "$PACKAGE_DIR/Resources/AppIcon.icns" "$RESOURCES_DIR/AppIcon.icns"
cp "$ROOT_DIR/RELEASE_NOTES.md" "$RESOURCES_DIR/ReleaseNotes.md"

cat > "$CONTENTS_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>KPaperMac</string>
  <key>CFBundleIdentifier</key>
  <string>io.codex.kpaper</string>
  <key>CFBundleName</key>
  <string>KPaper</string>
  <key>CFBundleDisplayName</key>
  <string>KPaper</string>
  <key>CFBundleIconFile</key>
  <string>AppIcon</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>0.2.0</string>
  <key>CFBundleVersion</key>
  <string>3</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

plutil -lint "$CONTENTS_DIR/Info.plist"
codesign --force --deep --sign - "$APP_DIR"
codesign --verify --deep --strict "$APP_DIR"

echo "$APP_DIR"
