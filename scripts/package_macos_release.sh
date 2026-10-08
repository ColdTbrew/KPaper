#!/usr/bin/env sh
# Build and package the macOS app. Python runtime stays in the user's checkout.
set -eu
ROOT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
"$ROOT_DIR/scripts/build_macos_app.sh"
APP="$ROOT_DIR/dist/KPaper.app"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP/Contents/Info.plist")"
RELEASE_WORK="$(mktemp -d "${TMPDIR:-/tmp}/kpaper-release.XXXXXX")"
trap 'rm -rf "$RELEASE_WORK"' EXIT HUP INT TERM
mkdir -p "$RELEASE_WORK/image"
ditto "$APP" "$RELEASE_WORK/image/KPaper.app"
ln -s /Applications "$RELEASE_WORK/image/Applications"
cat > "$RELEASE_WORK/image/설치 안내.txt" <<'TEXT'
KPaper — Apple Silicon Mac용 논문 리더

KPaper.app을 Applications 폴더로 복사하세요.
이 앱은 Python 번역·OCR 엔진을 내장하지 않습니다. 저장소 체크아웃이 필요합니다.

1. https://github.com/ColdTbrew/KPaper 에서 저장소를 내려받습니다.
2. uv를 설치한 뒤 저장소 폴더에서 uv sync를 실행합니다.
3. 앱 설정의 프로젝트 경로를 해당 저장소 폴더로 지정합니다.
4. ChatGPT 로그인, Codex CLI 또는 OpenAI 호환 API를 설정합니다.

Unlimited-OCR 모델은 처음 사용할 때 내려받을 수 있습니다.
질문 기능은 ChatGPT 로그인 또는 Codex CLI 연결로 사용할 수 있습니다.
앱은 로컬 실행용 ad hoc 서명이며 Apple 공증은 적용하지 않았습니다.
지원·설치 설명: https://github.com/ColdTbrew/KPaper#readme
TEXT
DMG="$ROOT_DIR/dist/KPaper-$VERSION-macOS-arm64.dmg"
ZIP="$ROOT_DIR/dist/KPaper-$VERSION-macOS-arm64.zip"
hdiutil create -volname "KPaper $VERSION" -srcfolder "$RELEASE_WORK/image" -ov -format UDZO "$DMG"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
(cd "$ROOT_DIR/dist" && shasum -a 256 "$(basename "$DMG")" "$(basename "$ZIP")" > "KPaper-$VERSION-SHA256SUMS.txt")
echo "Release files saved under dist/"
