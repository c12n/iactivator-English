"""System tray icon on Windows and a menu-bar menu on macOS.

Windows has no native tray in pywebview, so we use pystray in a background thread;
there, closing the window (X) hides it to the tray instead of quitting. macOS already
shows an app menu bar, so we just add our own items to it via pywebview's menu.
"""
from __future__ import annotations

import platform
import threading

from . import __version__

_IS_WIN = platform.system() == "Windows"
_quitting = threading.Event()
_icon = None


def _tray_image():
    """A small blue rounded icon with a lightning bolt — no asset file needed."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((6, 6, 58, 58), radius=14, fill=(10, 132, 255, 255))
    d.polygon([(37, 11), (21, 37), (31, 37), (27, 53), (45, 25), (34, 25)], fill="white")
    return img


def _show(window):
    window.show()
    window.restore()


def start_windows_tray(window):
    """Run the tray icon in a daemon thread (Windows only)."""
    import pystray
    global _icon
    _icon = pystray.Icon(
        "iActivator", _tray_image(), f"iActivator {__version__}",
        menu=pystray.Menu(
            pystray.MenuItem("Show window", lambda icon, item: _show(window), default=True),
            pystray.MenuItem("Quit", lambda icon, item: _quit(window)),
        ),
    )
    threading.Thread(target=_icon.run, daemon=True).start()


def _quit(window):
    _quitting.set()
    if _icon is not None:
        _icon.stop()
    window.destroy()


def on_closing(window) -> bool:
    """Window-close handler. On Windows, X hides to the tray instead of quitting
    (return False cancels the close). Elsewhere the close goes through."""
    if _IS_WIN and not _quitting.is_set():
        window.hide()
        return False
    return True


def mac_menu(window):
    """Top menu-bar items for macOS; harmless (ignored) on other systems."""
    from webview.menu import Menu, MenuAction, MenuSeparator
    return [
        Menu("iActivator", [
            MenuAction("Show window", lambda: _show(window)),
            MenuSeparator(),
            MenuAction("Quit", window.destroy),
        ]),
    ]
