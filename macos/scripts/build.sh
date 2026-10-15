#!/bin/bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD_ROOT="$PROJECT_ROOT/macos/build"
OUTPUT_ROOT="${TGA_OUTPUT_ROOT:-$PROJECT_ROOT/output/macos}"
APP="$OUTPUT_ROOT/通讯助手.app"
PRODUCT_VERSION="$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$PROJECT_ROOT/macos/Info.plist")"
PYTHON="${TGA_BUILD_PYTHON:-python3}"
mkdir -p "$BUILD_ROOT" "$OUTPUT_ROOT"
STAGING="$BUILD_ROOT/通讯助手.app"
# 这些目录只保存生成的应用包；完整重建，避免旧资源混入已签名的新包。
rm -rf "$STAGING"
API_ARGS=(--destination "$STAGING/Contents/Resources/TelegramAPI.json")
if [ -n "${TGA_API_CONFIG:-}" ]; then
    API_ARGS+=(--source "$TGA_API_CONFIG")
elif [ -f "$PROJECT_ROOT/macos/Private/TelegramAPI.json" ]; then
    API_ARGS+=(--source "$PROJECT_ROOT/macos/Private/TelegramAPI.json")
fi
if [ "${TGA_RELEASE:-0}" = "1" ]; then API_ARGS+=(--release); fi
"$PYTHON" "$PROJECT_ROOT/macos/scripts/configure_api.py" "${API_ARGS[@]}"
xcrun swift "$PROJECT_ROOT/macos/scripts/icon.swift" "$BUILD_ROOT/AppIcon.iconset"
iconutil -c icns "$BUILD_ROOT/AppIcon.iconset" -o "$BUILD_ROOT/AppIcon.icns"
if [ ! -x "$BUILD_ROOT/venv/bin/python" ]; then
    "$PYTHON" -m venv "$BUILD_ROOT/venv"
fi
"$BUILD_ROOT/venv/bin/python" -m pip install -r "$PROJECT_ROOT/macos/requirements-build.txt"
"$BUILD_ROOT/venv/bin/python" -m PyInstaller --noconfirm --clean --onedir --name tgengine \
    --paths "$PROJECT_ROOT" --paths "$PROJECT_ROOT/macos" \
    --distpath "$BUILD_ROOT/dist" --workpath "$BUILD_ROOT/work" --specpath "$BUILD_ROOT" \
    "$PROJECT_ROOT/macos/engine_entry.py"
mkdir -p "$STAGING/Contents/MacOS" "$STAGING/Contents/Resources"
ditto "$BUILD_ROOT/dist/tgengine" "$STAGING/Contents/Resources/Engine"
SDK="$(xcrun --sdk macosx --show-sdk-path)"
xcrun swiftc -swift-version 5 -O -target arm64-apple-macos14.0 -sdk "$SDK" \
    "$PROJECT_ROOT/macos/Sources/APIConfiguration.swift" "$PROJECT_ROOT/macos/Sources/Model.swift" "$PROJECT_ROOT/macos/Sources/Views.swift" "$PROJECT_ROOT/macos/Sources/TaskViews.swift" "$PROJECT_ROOT/macos/Sources/App.swift" \
    -o "$STAGING/Contents/MacOS/CommunicationsAssistant"
cp "$PROJECT_ROOT/macos/Info.plist" "$STAGING/Contents/Info.plist"
cp "$PROJECT_ROOT/macos/README.md" "$STAGING/Contents/Resources/使用说明.md"
"$BUILD_ROOT/venv/bin/python" "$PROJECT_ROOT/macos/scripts/licenses.py" "$STAGING/Contents/Resources/第三方许可.txt"
if [ -f "$BUILD_ROOT/AppIcon.icns" ]; then cp "$BUILD_ROOT/AppIcon.icns" "$STAGING/Contents/Resources/AppIcon.icns"; fi
codesign --force --deep --sign - "$STAGING"
codesign --verify --deep --strict "$STAGING"
rm -rf "$APP"
ditto "$STAGING" "$APP"
codesign --verify --deep --strict "$APP"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$OUTPUT_ROOT/通讯助手-$PRODUCT_VERSION-arm64.zip"
INSTALLER="$BUILD_ROOT/installer"
mkdir -p "$INSTALLER"
rm -rf "$INSTALLER/通讯助手.app"
ditto "$APP" "$INSTALLER/通讯助手.app"
if [ ! -L "$INSTALLER/Applications" ]; then ln -s /Applications "$INSTALLER/Applications"; fi
hdiutil create -ov -volname "通讯助手" -srcfolder "$INSTALLER" -format UDZO "$OUTPUT_ROOT/通讯助手-$PRODUCT_VERSION-arm64.dmg"
bash "$PROJECT_ROOT/macos/scripts/package_installer.sh" "$APP"
python3 "$PROJECT_ROOT/macos/scripts/check_installer.py" "$OUTPUT_ROOT/CommunicationsAssistant-$PRODUCT_VERSION-macOS-arm64.pkg" "$APP"
printf '%s\n' "$APP"
