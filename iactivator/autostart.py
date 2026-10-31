"""Start iActivator together with Windows, per-user and without admin rights.

Uses the HKCU \\...\\Run registry key. A no-op on other systems, so the rest of the
program can call it unconditionally.
"""
from __future__ import annotations

import os
import sys

_NAME = "iActivator"
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def supported() -> bool:
    return sys.platform == "win32"


def _command() -> str:
    if getattr(sys, "frozen", False):  # packaged .exe: the executable starts the app itself
        return f'"{sys.executable}"'
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    return f'"{sys.executable}" "{main}"'  # running from source (python main.py)


def is_enabled() -> bool:
    if not supported():
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _NAME)
            return bool(value)
    except OSError:
        return False


def set_enabled(on: bool) -> bool:
    """Add or remove the startup entry; returns the resulting state."""
    if not supported():
        return False
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
        if on:
            winreg.SetValueEx(key, _NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, _NAME)
            except OSError:
                pass
    return is_enabled()
