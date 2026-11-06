"""Check GitHub Releases for a newer version.

Read-only: this module never downloads or replaces anything — it only reports
whether a newer release exists and the page to get it from. The program file and
the data folder are separate, so updating the program never touches history or
settings. Fails quietly when offline.
"""
from __future__ import annotations

import json
import ssl
import sys
import threading
import urllib.request

from . import __version__, applog

REPO = "c12n/iactivator-English"
_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
_lock = threading.Lock()
_cache: dict = {}


def _ssl_context() -> ssl.SSLContext:
    """Verify HTTPS with the CA bundle shipped inside the app.

    A packaged build (PyInstaller) has no system OpenSSL CA path, so the default
    context fails to verify github.com and the check looks like "no internet".
    Using certifi's bundled cacert.pem fixes that on every machine.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _ver_tuple(s: str) -> tuple:
    out = []
    for part in (s or "").lstrip("vV").split("."):
        if part.isdigit():
            out.append(int(part))
        else:
            break
    return tuple(out)


def _asset_name() -> str:
    return "iActivator.exe" if sys.platform == "win32" else "iActivator-mac.zip"


def check(force: bool = False) -> dict:
    with _lock:
        if _cache and not force:
            return dict(_cache)
    info = {"current": __version__, "available": False, "page": RELEASES_PAGE}
    try:
        req = urllib.request.Request(_API, headers={
            "User-Agent": "iActivator", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8, context=_ssl_context()) as r:
            data = json.load(r)
        info["latest"] = (data.get("tag_name", "") or "").lstrip("vV")
        info["page"] = data.get("html_url") or RELEASES_PAGE
        want = _asset_name()
        for a in data.get("assets", []):
            if a.get("name") == want:
                info["download"] = a.get("browser_download_url")
        info["available"] = _ver_tuple(info.get("latest", "")) > _ver_tuple(__version__)
    except Exception as e:
        info["error"] = True  # offline, no releases yet, or TLS/cert trouble
        try:
            applog.add(f"Update check failed: {type(e).__name__}: {e}", level="debug")
        except Exception:
            pass
    with _lock:
        _cache.clear()
        _cache.update(info)
    return dict(info)


def open_page() -> bool:
    """Open the download page in the default browser."""
    import webbrowser
    url = check().get("page") or RELEASES_PAGE
    webbrowser.open(url)
    return True
