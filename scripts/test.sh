#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TEST_PYTHON="${TGA_TEST_PYTHON:-python3}"
if ! "$TEST_PYTHON" -c 'import telethon' >/dev/null 2>&1; then
    "$TEST_PYTHON" -m venv .venv
    .venv/bin/python -m pip install -r macos/requirements-build.txt
    TEST_PYTHON="$PWD/.venv/bin/python"
fi
PYTHONPATH=macos:. "$TEST_PYTHON" -m unittest discover -s tests -q
PYTHONPATH=macos:. "$TEST_PYTHON" -m unittest discover -s macos/tests -q
"$TEST_PYTHON" scripts/check_source.py
"$TEST_PYTHON" scripts/check_site.py
