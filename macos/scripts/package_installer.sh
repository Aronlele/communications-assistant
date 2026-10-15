#!/usr/bin/env bash
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
APP="${1:-${TGA_OUTPUT_ROOT:-$PROJECT_ROOT/output/macos}/通讯助手.app}"
mkdir -p "$PROJECT_ROOT/macos/build"
STAGE=$(mktemp -d "$PROJECT_ROOT/macos/build/pkg.XXXXXX")
trap 'rm -rf "$STAGE"' EXIT
VERSION=$(python3 "$PROJECT_ROOT/macos/scripts/installer_metadata.py" "$APP" "$STAGE")
codesign --verify --deep --strict "$APP"
[[ "$(lipo -archs "$APP/Contents/MacOS/CommunicationsAssistant")" == "arm64" ]]
mkdir -p "$STAGE/root"
ditto "$APP" "$STAGE/root/通讯助手.app"
pkgbuild --root "$STAGE/root" --component-plist "$STAGE/components.plist" \
  --identifier local.communications.assistant.pkg --version "$VERSION" \
  --install-location /Applications --ownership recommended "$STAGE/assistant.pkg"
OUTPUT="$(dirname "$APP")/CommunicationsAssistant-$VERSION-macOS-arm64.pkg"
productbuild --distribution "$STAGE/Distribution.xml" --package-path "$STAGE" \
  --resources "$PROJECT_ROOT/macos/packaging/installer" --version "$VERSION" "$OUTPUT"
printf '已生成 PKG 测试安装向导（未签名、未公证）：%s\n' "$OUTPUT"
