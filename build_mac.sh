#!/bin/bash
# Build iActivator.app for macOS (run on a Mac).
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/pyinstaller --noconfirm --windowed --name iActivator --icon assets/icon.icns \
  --collect-all pymobiledevice3 --collect-all webview --collect-submodules keyring --collect-data fpdf \
  --add-data "iactivator/page.html:iactivator" main.py
echo "Done: dist/iActivator.app"
