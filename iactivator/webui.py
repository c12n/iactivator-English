"""Browser UI: a small local HTTP server (127.0.0.1 only) + one HTML page.

Used instead of Tkinter because the system Tk shipped with macOS is deprecated and
renders blank windows on recent versions; a browser works the same everywhere.
"""
from __future__ import annotations

import asyncio
import json
import platform
import re
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, applog, autostart, core, history, report, settings, tray, update

LANGUAGES = [
    ("English (US)", "en"), ("English (UK)", "en-GB"), ("Ukrainian", "uk"), ("Russian", "ru"),
    ("Polish", "pl"), ("German", "de"), ("French", "fr"), ("Spanish", "es"), ("Italian", "it"),
    ("Portuguese (Brazil)", "pt-BR"), ("Turkish", "tr"), ("Romanian", "ro"), ("Czech", "cs"),
    ("Slovak", "sk"), ("Hungarian", "hu"), ("Kazakh", "kk"), ("Hebrew", "he"), ("Arabic", "ar"),
    ("Chinese (Simplified)", "zh-Hans"), ("Chinese (Traditional)", "zh-Hant"), ("Japanese", "ja"), ("Korean", "ko"),
]

REGIONS = [
    ("United States", "US", "America/New_York"), ("Canada", "CA", "America/Toronto"),
    ("United Kingdom", "GB", "Europe/London"), ("Ukraine", "UA", "Europe/Kyiv"),
    ("Poland", "PL", "Europe/Warsaw"), ("Germany", "DE", "Europe/Berlin"),
    ("Czechia", "CZ", "Europe/Prague"), ("Moldova", "MD", "Europe/Chisinau"),
    ("Romania", "RO", "Europe/Bucharest"), ("Kazakhstan", "KZ", "Asia/Almaty"),
    ("Georgia", "GE", "Asia/Tbilisi"), ("Turkey", "TR", "Europe/Istanbul"),
    ("France", "FR", "Europe/Paris"), ("Italy", "IT", "Europe/Rome"),
    ("Spain", "ES", "Europe/Madrid"), ("Israel", "IL", "Asia/Jerusalem"),
    ("UAE", "AE", "Asia/Dubai"), ("China", "CN", "Asia/Shanghai"),
]

ACTIVATION = {"Activated": "activated", "Unactivated": "not activated"}

# Shown on the About page, credited to the original author of iActivator.
# Wallets: (coin and network, address).
INSTAGRAM = "https://www.instagram.com/perminov.george"
WALLETS: list[tuple[str, str]] = [("USDT (TRC20)", "TQKtZpsdz7pm9cuXWPE3ZQEboPHva4Uuas")]

PAGE = (Path(__file__).parent / "page.html").read_text(encoding="utf-8")


def _about() -> dict:
    try:
        from importlib.metadata import version
        pmd3 = version("pymobiledevice3")
    except Exception:
        pmd3 = "—"
    return {
        "version": __version__,
        "system": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "pymobiledevice3": pmd3,
        "data_dir": str(core.app_data_dir()),
        "instagram": INSTAGRAM,
        "wallets": WALLETS,
        "autostart_supported": autostart.supported(),
        "autostart": autostart.is_enabled(),
    }


def _open_folder(path: str) -> None:
    if sys.platform == "win32":
        import os
        os.startfile(path)
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


class State:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.lock = threading.Lock()
        self.busy = False
        self.labels: dict[str, str] = {}
        self.last_device_error = ""
        self.steps: list[dict] = []  # live activation checklist shown in the UI

    def log(self, msg: str, serial: str = "", level: str = "") -> None:
        applog.add(msg, serial, level)

    def set_steps(self, names: list) -> None:
        self.steps = [{"name": n, "status": "pending", "detail": ""} for n in names]

    def set_step(self, name: str, status: str, detail: str = "") -> None:
        for s in self.steps:
            if s["name"] == name:
                s["status"], s["detail"] = status, detail
                return

    def fail_running_steps(self, detail: str = "") -> None:
        for s in self.steps:
            if s["status"] in ("run", "pending"):
                s["status"], s["detail"] = ("error", detail) if s["status"] == "run" else ("skipped", "")

    def run(self, coro):
        """Run a coroutine on the device loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def run_job(self, make_coro, serial: str = "") -> bool:
        """Start a long job in the background; its progress goes to the log."""
        with self.lock:
            if self.busy:
                return False
            self.busy = True

        async def job():
            try:
                await make_coro()
            except core.UserFacingError as e:
                self.log(f"❌ {e}", serial)
                self.fail_running_steps(str(e))
            except Exception as e:
                self.log("❌ Error: " + core.short_error(e), serial)
                applog.exception(e, serial)
                self.fail_running_steps(core.short_error(e))
            finally:
                self.busy = False
        asyncio.run_coroutine_threadsafe(job(), self.loop)
        return True


STATE = State()


async def _with_activator(serial, fn, action, detail=""):
    act = core.Activator(serial, lambda m: STATE.log(m, serial), step=STATE.set_step)
    STATE.log(f"▶ {action}" + (f": {detail}" if detail else ""), serial)
    started = time.monotonic()
    try:
        await fn(act)
        history.add_event(serial, action, "ok", detail)
        STATE.log(f"✅ {action}: done in {time.monotonic() - started:.0f} s", serial)
    except Exception as e:
        history.add_event(serial, action, "error", str(e) if isinstance(e, core.UserFacingError) else core.short_error(e))
        raise
    finally:
        await act.close()


def _record_checks(serial: str, action: str, results: list) -> None:
    bad = [r for r in results if r["status"] == "bad"]
    warn = [r for r in results if r["status"] == "warn"]
    for r in results:
        level = {"bad": "error", "warn": "warn", "ok": "ok"}.get(r["status"], "info")
        STATE.log(f"{action} · {r['name']}: {r['detail']}", serial, level if level != "ok" else "debug")
    STATE.log(f"{action}: " + (f"{len(bad)} faults, {len(warn)} warnings" if bad or warn else "no warnings"),
              serial, "error" if bad else "warn" if warn else "ok")
    if bad or warn:
        detail = "; ".join(f"{r['name']}: {r['detail']}" for r in bad + warn)
        history.add_event(serial, action, "error" if bad else "warn", detail)
    else:
        history.add_event(serial, action, "ok", "No warnings")


def _wifi(d: dict):
    if not d.get("ssid"):
        return None
    return core.WifiOptions(
        ssid=d["ssid"],
        password=d.get("password", ""),
        security=d.get("security", "Any"),
        hidden=bool(d.get("hidden")),
        disable_mac_randomization=bool(d.get("nomac", True)),
    )


def _options(d: dict) -> core.SetupOptions:
    return core.SetupOptions(
        language=d.get("language", "en").strip(),
        region=d.get("region", "US").strip().upper(),
        timezone=d.get("timezone", "America/New_York").strip(),
        use_24h=bool(d.get("h24", True)),
        activate=bool(d.get("activate", True)),
        skip_setup=bool(d.get("skip_setup", True)),
        supervise=bool(d.get("supervise", False)),
        organization=(d.get("organization") or "iActivator").strip(),
        wifi=_wifi(d),
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _fail(self, e: Exception, serial: str, what: str):
        STATE.log(f"❌ {what}: {core.short_error(e)}", serial)
        applog.exception(e, serial)
        return self._send(200, {"error": core.short_error(e)})

    def do_GET(self):
        if self.path == "/api/history":
            return self._send(200, history.summary())
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/meta":
            return self._send(200, {
                "languages": LANGUAGES, "regions": REGIONS,
                "security": [[k, v] for k, v in core.WIFI_SECURITY.items()],
                "about": _about(),
            })
        if self.path.startswith("/api/update"):
            return self._send(200, update.check(force="force" in self.path))
        if self.path.startswith("/api/log"):
            entries, total = applog.since(int(self.path.partition("since=")[2] or 0))
            return self._send(200, {"entries": entries, "next": total, "busy": STATE.busy, "steps": STATE.steps})
        if self.path.startswith("/api/devices"):
            # Polled every few seconds: cheap usbmux listing, full labels only for new devices
            # or when the page asks for a refresh.
            try:
                serials = STATE.run(core.connected_serials())
                if "full=1" in self.path or any(s not in STATE.labels for s in serials):
                    STATE.labels = {d.serial: d.label() for d in STATE.run(core.list_devices())}
                STATE.last_device_error = ""
                return self._send(200, [{"serial": s, "label": STATE.labels.get(s, s)} for s in serials])
            except Exception as e:
                msg = f"Could not get the device list: {core.short_error(e)}"
                if msg != STATE.last_device_error:  # polled every 3 s: log a failure once, not every time
                    STATE.log("⚠ " + msg)
                    applog.exception(e)
                STATE.last_device_error = msg
                return self._send(200, {"error": msg})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        # Only accept requests from our own page (blocks other websites from poking the API).
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{self.server.server_port}"):
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length", 0))
        d = json.loads(self.rfile.read(length) or b"{}")

        if self.path == "/api/settings":
            return self._send(200, settings.load())
        if self.path == "/api/network/password":
            return self._send(200, {"password": settings.get_password(d.get("ssid", ""))})
        if self.path == "/api/network/save":
            if not d.get("ssid", "").strip():
                return self._send(400, {"error": "Enter a network name"})
            settings.save_network(d["ssid"], d.get("password", ""), d.get("security", "Any"),
                                  bool(d.get("hidden")), bool(d.get("nomac", True)))
            STATE.log(f"Network \"{d['ssid'].strip()}\" saved (security {d.get('security', 'Any')}"
                      + (", hidden" if d.get("hidden") else "") + ")")
            return self._send(200, settings.load())
        if self.path == "/api/network/delete":
            settings.delete_network(d.get("ssid", ""))
            STATE.log(f"Network \"{d.get('ssid', '')}\" deleted")
            return self._send(200, settings.load())
        if self.path == "/api/settings/autostart":
            on = autostart.set_enabled(bool(d.get("on")))
            STATE.log("Start with Windows: " + ("on" if on else "off"))
            return self._send(200, {"autostart": on})
        if self.path == "/api/settings/ui":
            settings.save_ui(d)
            STATE.log(f"Theme: {THEME_NAMES.get(d.get('theme'), d.get('theme'))}", level="debug")
            return self._send(200, settings.load())
        if self.path == "/api/settings/export":
            return self._send(200, _export(bool(d.get("secrets")), bool(d.get("history"))))
        if self.path == "/api/settings/import":
            try:
                done = settings.import_data(d.get("data"))
            except Exception as e:
                STATE.log(f"❌ Settings import: {e}")
                return self._send(200, {"error": f"Could not load the settings: {e}"})
            STATE.log(f"✅ Settings loaded from \"{d.get('name', 'file')}\"")
            for line in done:
                STATE.log("  " + line, level="warn" if line.startswith("⚠") else "info")
            return self._send(200, {"done": done})
        if self.path == "/api/journal":
            # Messages the page writes itself (device unplugged, auto mode, …)
            STATE.log(str(d.get("text", ""))[:500], str(d.get("serial") or ""), d.get("level", ""))
            return self._send(200, {"ok": True})
        if self.path == "/api/logs/open":
            _open_folder(str(applog.logs_dir()))
            return self._send(200, {"ok": True})
        if self.path == "/api/logs/export":
            return self._send(200, _export_logs(bool(d.get("history", True)), bool(d.get("redact"))))
        if self.path == "/api/open-url":
            url = d.get("url", "")
            if url == INSTAGRAM:
                webbrowser.open(url)
            return self._send(200, {"ok": True})
        if self.path == "/api/update/open":
            update.open_page()
            return self._send(200, {"ok": True})

        if self.path == "/api/open-data":
            _open_folder(str(core.app_data_dir()))
            return self._send(200, {"ok": True})

        if self.path == "/api/history/open":
            _open_folder(str(history.csv_path() if history.csv_path().exists() else core.app_data_dir()))
            return self._send(200, {"ok": True})
        if self.path == "/api/reports/open":
            _open_folder(str(report.reports_dir()))
            return self._send(200, {"ok": True})
        if self.path == "/api/history/delete":
            history.delete(d.get("serial", ""))
            return self._send(200, {"ok": True})

        if self.path == "/api/settings/last":
            settings.save_last(d)
            return self._send(200, {"ok": True})

        serial = d.get("serial")
        if not serial:
            return self._send(400, {"error": "No device selected"})

        if self.path == "/api/locks":
            try:
                res = STATE.run(core.get_lock_checks(serial, check_activation=not STATE.busy))
                if d.get("record"):
                    _record_checks(serial, "Find My & MDM", res)
                return self._send(200, res)
            except Exception as e:
                return self._fail(e, serial, "Find My & MDM")

        if self.path == "/api/check":
            STATE.log("Diagnostics: checking…", serial)
            try:
                res = STATE.run(core.run_health_check(serial, check_activation=not STATE.busy))
                _record_checks(serial, "Diagnostics", res)
                return self._send(200, res)
            except Exception as e:
                return self._fail(e, serial, "Diagnostics")

        if self.path == "/api/report":
            try:
                info = STATE.run(core.get_device_details(serial))
                history.update_device(info)
                results = STATE.run(core.run_health_check(serial, check_activation=not STATE.busy))
                _record_checks(serial, "Diagnostics", results)
                path = report.build_pdf(info, results, STATE.steps)
                history.add_event(serial, "PDF report", "info", path.name)
                STATE.log(f"📄 Report saved: {path}", serial, "ok")
                report.open_file(path)
                return self._send(200, {"path": str(path)})
            except Exception as e:
                return self._fail(e, serial, "PDF report")

        if self.path in ("/api/run", "/api/wifi"):
            # Remember the form (and the network, if asked) before the job starts.
            try:
                settings.save_last(d)
                if d.get("ssid", "").strip():
                    settings.save_network(d["ssid"], d.get("password", ""), d.get("security", "Any"),
                                          bool(d.get("hidden")), bool(d.get("nomac", True)))
            except Exception as e:
                STATE.log(f"⚠ Could not save the network: {e}", serial)

        if self.path == "/api/languages":
            try:
                langs, _ = STATE.run(core.get_supported_languages(serial))
                return self._send(200, langs)
            except Exception as e:
                return self._fail(e, serial, "Device languages")

        if self.path == "/api/info":
            try:
                info = STATE.run(core.get_device_details(serial))
                history.update_device(info)
                if d.get("record"):
                    b = info.get("battery") or {}
                    detail = (f"{ACTIVATION.get(info.get('activation'), info.get('activation'))}, iOS {info.get('ios')} ({info.get('build')})"
                              + (f", battery {b['health']}%" if b.get("health") is not None else ""))
                    history.add_event(serial, "Connected", "info", detail)
                    STATE.log(f"Connected: {info.get('model_name')}"
                              + (f", {info['capacity']}" if info.get("capacity") else "") + f": {detail}", serial)
                    STATE.log(f"IMEI {info.get('imei') or '—'}" + (f", IMEI 2 {info['imei2']}" if info.get("imei2") else ""),
                              serial, "debug")
                return self._send(200, info)
            except Exception as e:
                return self._fail(e, serial, "Reading device data")

        if self.path == "/api/run":
            opts = _options(d)
            detail = f"{opts.language}, {opts.region}" + (f", Wi-Fi «{opts.wifi.ssid}»" if opts.wifi else "")
            action = "Activation" if opts.activate else "Setup"
            names = ([core.STEP_ACTIVATE] if opts.activate else []) + [core.STEP_LANG]
            names += [core.STEP_SKIP] if opts.skip_setup else []
            names += [core.STEP_WIFI] if opts.wifi else []
            STATE.set_steps(names)
            job = lambda: _with_activator(serial, lambda a: a.run(opts), action, detail)
        elif self.path == "/api/wifi":
            wifi = _wifi(d)
            if wifi is None:
                return self._send(400, {"error": "Enter a network name"})
            STATE.set_steps([])
            job = lambda: _with_activator(serial, lambda a: a.install_wifi_only(wifi), "Wi-Fi", f"«{wifi.ssid}»")
        elif self.path == "/api/erase":
            STATE.set_steps([])
            job = lambda: _with_activator(serial, lambda a: a.erase(), "Erase")
        else:
            return self._send(404, {"error": "not found"})
        if not STATE.run_job(job, serial):
            return self._send(409, {"error": "Another operation is already running"})
        self._send(200, {"ok": True})


THEME_NAMES = {"auto": "system", "light": "light", "dark": "dark"}
WINDOW = None  # pywebview window, for native file dialogs


def _save_path(name: str):
    """Ask where to save a file; without the native window, use Downloads."""
    if WINDOW is None:
        folder = Path.home() / "Downloads"
        return (folder if folder.is_dir() else core.app_data_dir()) / name
    import webview
    kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    res = WINDOW.create_file_dialog(kind, save_filename=name)
    if not res:
        return None
    return Path(res if isinstance(res, str) else res[0])


def _export(secrets: bool, with_history: bool) -> dict:
    path = _save_path(f"iActivator-settings-{time.strftime('%Y-%m-%d')}.json")
    if path is None:
        return {"cancelled": True}
    data = settings.export_data(secrets, with_history)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        STATE.log(f"❌ Could not save the settings: {e}")
        return {"error": f"Could not save the file: {e}"}
    STATE.log(f"✅ Settings saved to a file: {path}")
    STATE.log(f"  Wi-Fi networks: {len(data['networks'])}" + (", with passwords" if secrets else ", no passwords")
              + (", supervision key" if "supervision_keybag" in data else "")
              + (f", history: {len(data['history'])} devices" if "history" in data else ""))
    if WINDOW is None:
        _open_folder(str(path.parent))
    return {"path": str(path)}


def _device_identifiers() -> set:
    """Every serial number / IMEI / UDID the program knows, to blank out when sharing."""
    ids: set = set()
    try:
        for key, d in history.export().items():
            ids.add(str(key))
            for k in ("id", "serial", "imei", "imei2"):
                if d.get(k):
                    ids.add(str(d[k]))
    except Exception:
        pass
    return {i for i in ids if i and i != "-" and len(i) >= 5}


def _redact_ids(text: str, idents: set) -> str:
    """Replace known identifiers, then IMEI-like digit runs and UDIDs, with ***."""
    for ident in sorted(idents, key=len, reverse=True):
        text = text.replace(ident, "***")
    text = re.sub(r"\b\d{14,15}\b", "***", text)                       # IMEI
    text = re.sub(r"\b[0-9a-fA-F]{40}\b", "***", text)                 # old-style UDID
    text = re.sub(r"\b[0-9A-Fa-f]{8}-[0-9A-Fa-f]{16}\b", "***", text)  # new-style UDID
    return text


def _export_logs(with_history: bool, redact: bool = False) -> dict:
    """Bundle the daily logs (and optionally the device history) into one text file
    that can be sent for support/diagnosis. With redact=True the serial numbers and
    IMEIs are blanked out and the history is left out, so the file can be shared
    without revealing which devices were processed."""
    if redact:
        with_history = False
    suffix = "-redacted" if redact else ""
    path = _save_path(f"iActivator-log{suffix}-{time.strftime('%Y-%m-%d_%H%M')}.txt")
    if path is None:
        return {"cancelled": True}
    a = _about()
    parts = [
        "=== iActivator: diagnostic log ===",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Version: {a['version']}   System: {a['system']}   Python: {a['python']}   pymobiledevice3: {a['pymobiledevice3']}",
    ]
    if redact:
        parts.append("Serial numbers, IMEIs and UDIDs are hidden (***); device history is not included.")
    parts.append("")
    logs = sorted(applog.logs_dir().glob("*.log"))
    if logs:
        parts.append(f"=== LOG ENTRIES ({len(logs)} files) ===")
        for p in logs:
            try:
                parts.append(f"\n----- {p.name} -----\n" + p.read_text(encoding="utf-8").rstrip())
            except OSError as e:
                parts.append(f"\n----- {p.name} ----- (could not read: {e})")
    else:
        parts.append("=== NO LOG ENTRIES YET ===")
    if with_history:
        parts.append("\n\n=== DEVICE HISTORY (JSON) ===")
        parts.append(json.dumps(history.export(), ensure_ascii=False, indent=1))
    text = "\n".join(parts) + "\n"
    if redact:
        text = _redact_ids(text, _device_identifiers())
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        STATE.log(f"❌ Could not save the log: {e}")
        return {"error": f"Could not save the file: {e}"}
    STATE.log(f"✅ Log saved to a file: {path}")
    if WINDOW is None:
        _open_folder(str(path.parent))
    return {"path": str(path)}


def _free_port(preferred: int = 8765) -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # same as the server, so a restart keeps the port
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


DEFAULT_W, DEFAULT_H = 1120, 780
MIN_W, MIN_H = 640, 560


def _window_geometry(webview):
    """Size and place the window so it always fits the screen, reusing the last size
    the user left when it still fits. Without this the fixed 1120x780 window opens
    bigger than the screen on Windows laptops scaled to 125-150%, pushing the bottom
    bar and corners off-screen."""
    try:
        scr = webview.screens[0]
        sw, sh = int(scr.width), int(scr.height)
    except Exception:
        sw, sh = DEFAULT_W, DEFAULT_H  # screen size unknown: fall back to the design size
    fit_w, fit_h = min(DEFAULT_W, int(sw * 0.92)), min(DEFAULT_H, int(sh * 0.90))
    saved = settings.get_window()
    w = saved["w"] if isinstance(saved.get("w"), int) else fit_w
    h = saved["h"] if isinstance(saved.get("h"), int) else fit_h
    w = max(MIN_W, min(w, sw))  # never wider/taller than the actual screen
    h = max(MIN_H, min(h, sh))
    if isinstance(saved.get("x"), int) and isinstance(saved.get("y"), int) \
            and 0 <= saved["x"] <= sw - w and 0 <= saved["y"] <= sh - h:
        x, y = saved["x"], saved["y"]
    else:  # center it, biased slightly up so it clears the taskbar/dock
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2 - 10)
    min_size = (min(MIN_W, w), min(MIN_H, h))  # min_size must not exceed the window itself
    return w, h, x, y, min_size


def main():
    # Trim Edge WebView2 (Chromium) memory on Windows: no GPU process, smaller JS heap.
    # Must be set before the WebView2 environment starts. Harmless on macOS (WebKit).
    if sys.platform == "win32":
        import os
        os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
                              "--disable-gpu --disable-dev-shm-usage --js-flags=--max-old-space-size=128")

    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # native window: WebKit on macOS, Edge WebView2 on Windows
    except ImportError:
        webview = None

    applog.cleanup()
    STATE.log(f"iActivator {__version__} started · {platform.system()} {platform.release()} · Python {platform.python_version()}",
              level="debug")

    if webview is not None:
        global WINDOW
        threading.Thread(target=server.serve_forever, daemon=True).start()
        w, h, x, y, min_size = _window_geometry(webview)
        WINDOW = webview.create_window(f"iActivator {__version__}", url, width=w, height=h,
                                       x=x, y=y, min_size=min_size)
        # Remember where the user left the window; write once on close, not on every drag.
        geom: dict = {}
        WINDOW.events.resized += lambda width, height: geom.update(w=width, h=height)
        WINDOW.events.moved += lambda nx, ny: geom.update(x=nx, y=ny)

        def _on_closing():
            settings.save_window(geom)
            return tray.on_closing(WINDOW)  # on Windows: hide to tray instead of quitting

        WINDOW.events.closing += _on_closing

        if sys.platform == "win32":
            tray.start_windows_tray(WINDOW)
            webview.start()
        else:
            webview.start(menu=tray.mac_menu(WINDOW))  # macOS menu bar
        return

    # Fallback without pywebview: open the page in the default browser.
    print(f"iActivator is open in your browser: {url}")
    print("Do not close this window while you are using the program. To quit: Ctrl+C.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
