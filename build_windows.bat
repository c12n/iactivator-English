@echo off
REM Build iActivator.exe for Windows (run on Windows, Python 3.9+ from python.org).
cd /d "%~dp0"
if not exist .venv python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name iActivator --icon assets\icon.ico --collect-all pymobiledevice3 --collect-all webview --collect-submodules keyring --add-data "iactivator\page.html;iactivator" main.py
echo.
echo Done: dist\iActivator.exe
pause
