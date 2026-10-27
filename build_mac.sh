#!/bin/bash
# Build iActivator.app for macOS (run on a Mac).
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/pyinstaller --noconfirm --windowed --name iActivator \
  --collect-all pymobiledevice3 --collect-all webview \
  --add-data "iactivator/page.html:iactivator" main.py
echo "Done: dist/iActivator.app"
