#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TEST_PYTHON="${TGA_TEST_PYTHON:-python3}"
if [ "$(uname -s)" != Darwin ]; then
    echo '原生探针需要 macOS'
    exit 1
fi
"$TEST_PYTHON" macos/scripts/native_accounts_probe.py
"$TEST_PYTHON" macos/scripts/native_recovery_probe.py
"$TEST_PYTHON" macos/scripts/native_product_probe.py
