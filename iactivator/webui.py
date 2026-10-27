"""Browser UI: a small local HTTP server (127.0.0.1 only) + one HTML page.

Used instead of Tkinter because the system Tk shipped with macOS is deprecated and
renders blank windows on recent versions; a browser works the same everywhere.
"""
from __future__ import annotations

import asyncio
import json
import platform
import socket
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, core, settings

LANGUAGES = [
    ("Украинский", "uk"), ("Русский", "ru"), ("Английский (США)", "en"), ("Английский (Великобритания)", "en-GB"),
    ("Польский", "pl"), ("Немецкий", "de"), ("Французский", "fr"), ("Испанский", "es"), ("Итальянский", "it"),
    ("Португальский (Бразилия)", "pt-BR"), ("Турецкий", "tr"), ("Румынский", "ro"), ("Чешский", "cs"),
    ("Словацкий", "sk"), ("Венгерский", "hu"), ("Казахский", "kk"), ("Иврит", "he"), ("Арабский", "ar"),
    ("Китайский (упрощ.)", "zh-Hans"), ("Китайский (традиц.)", "zh-Hant"), ("Японский", "ja"), ("Корейский", "ko"),
]

REGIONS = [
    ("Украина", "UA", "Europe/Kyiv"), ("Польша", "PL", "Europe/Warsaw"),
    ("Германия", "DE", "Europe/Berlin"), ("Чехия", "CZ", "Europe/Prague"),
    ("Молдова", "MD", "Europe/Chisinau"), ("Румыния", "RO", "Europe/Bucharest"),
    ("Казахстан", "KZ", "Asia/Almaty"), ("Грузия", "GE", "Asia/Tbilisi"),
    ("Турция", "TR", "Europe/Istanbul"), ("Великобритания", "GB", "Europe/London"),
    ("Франция", "FR", "Europe/Paris"), ("Италия", "IT", "Europe/Rome"),
    ("Испания", "ES", "Europe/Madrid"), ("Израиль", "IL", "Asia/Jerusalem"),
    ("ОАЭ", "AE", "Asia/Dubai"), ("США", "US", "America/New_York"),
    ("Канада", "CA", "America/Toronto"), ("Китай", "CN", "Asia/Shanghai"),
]

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
        self.lines: list[str] = []
        self.busy = False
        self.labels: dict[str, str] = {}

    def log(self, msg: str) -> None:
        with self.lock:
            self.lines.append(msg)

    def run(self, coro):
        """Run a coroutine on the device loop and wait for its result."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def run_job(self, make_coro) -> bool:
        """Start a long job in the background; its progress goes to the log."""
        with self.lock:
            if self.busy:
                return False
            self.busy = True

        async def job():
            try:
                await make_coro()
            except core.UserFacingError as e:
                self.log(f"❌ {e}")
            except Exception as e:
                self.log("❌ Ошибка: " + core.short_error(e))
            finally:
                self.busy = False
        asyncio.run_coroutine_threadsafe(job(), self.loop)
        return True


STATE = State()


async def _with_activator(serial, fn):
    act = core.Activator(serial, STATE.log)
    try:
        await fn(act)
    finally:
        await act.close()


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
        language=d.get("language", "uk").strip(),
        region=d.get("region", "UA").strip().upper(),
        timezone=d.get("timezone", "Europe/Kyiv").strip(),
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

    def do_GET(self):
        if self.path == "/":
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path == "/api/meta":
            return self._send(200, {
                "languages": LANGUAGES, "regions": REGIONS,
                "security": [[k, v] for k, v in core.WIFI_SECURITY.items()],
                "about": _about(),
            })
        if self.path.startswith("/api/log"):
            since = int(self.path.partition("since=")[2] or 0)
            with STATE.lock:
                lines = STATE.lines[since:]
                total = len(STATE.lines)
            return self._send(200, {"lines": lines, "next": total, "busy": STATE.busy})
        if self.path.startswith("/api/devices"):
            # Polled every few seconds: cheap usbmux listing, full labels only for new devices
            # or when the page asks for a refresh.
            try:
                serials = STATE.run(core.connected_serials())
                if "full=1" in self.path or any(s not in STATE.labels for s in serials):
                    STATE.labels = {d.serial: d.label() for d in STATE.run(core.list_devices())}
                return self._send(200, [{"serial": s, "label": STATE.labels.get(s, s)} for s in serials])
            except Exception as e:
                return self._send(200, {"error": f"Не удалось получить список устройств: {core.short_error(e)}"})
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
                return self._send(400, {"error": "Введите имя сети"})
            settings.save_network(d["ssid"], d.get("password", ""), d.get("security", "Any"),
                                  bool(d.get("hidden")), bool(d.get("nomac", True)))
            return self._send(200, settings.load())
        if self.path == "/api/network/delete":
            settings.delete_network(d.get("ssid", ""))
            return self._send(200, settings.load())

        if self.path == "/api/open-data":
            _open_folder(str(core.app_data_dir()))
            return self._send(200, {"ok": True})

        if self.path == "/api/settings/last":
            settings.save_last(d)
            return self._send(200, {"ok": True})

        serial = d.get("serial")
        if not serial:
            return self._send(400, {"error": "Не выбрано устройство"})

        if self.path == "/api/locks":
            try:
                return self._send(200, STATE.run(core.get_lock_checks(serial, check_activation=not STATE.busy)))
            except Exception as e:
                return self._send(200, {"error": core.short_error(e)})

        if self.path == "/api/check":
            try:
                return self._send(200, STATE.run(core.run_health_check(serial, check_activation=not STATE.busy)))
            except Exception as e:
                return self._send(200, {"error": core.short_error(e)})

        if self.path in ("/api/run", "/api/wifi"):
            # Remember the form (and the network, if asked) before the job starts.
            try:
                settings.save_last(d)
                if d.get("ssid", "").strip():
                    settings.save_network(d["ssid"], d.get("password", ""), d.get("security", "Any"),
                                          bool(d.get("hidden")), bool(d.get("nomac", True)))
            except Exception as e:
                STATE.log(f"⚠ Не удалось сохранить сеть: {e}")

        if self.path == "/api/languages":
            try:
                langs, _ = STATE.run(core.get_supported_languages(serial))
                return self._send(200, langs)
            except Exception as e:
                return self._send(200, {"error": core.short_error(e)})

        if self.path == "/api/info":
            try:
                return self._send(200, STATE.run(core.get_device_details(serial)))
            except Exception as e:
                return self._send(200, {"error": core.short_error(e)})

        if self.path == "/api/run":
            opts = _options(d)
            job = lambda: _with_activator(serial, lambda a: a.run(opts))
        elif self.path == "/api/wifi":
            wifi = _wifi(d)
            if wifi is None:
                return self._send(400, {"error": "Введите имя сети"})
            job = lambda: _with_activator(serial, lambda a: a.install_wifi_only(wifi))
        elif self.path == "/api/erase":
            job = lambda: _with_activator(serial, lambda a: a.erase())
        else:
            return self._send(404, {"error": "not found"})
        if not STATE.run_job(job):
            return self._send(409, {"error": "Уже выполняется другая операция"})
        self._send(200, {"ok": True})


def _free_port(preferred: int = 8765) -> int:
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # same as the server, so a restart keeps the port
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def main():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # native window: WebKit on macOS, Edge WebView2 on Windows
    except ImportError:
        webview = None

    if webview is not None:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        webview.create_window(f"iActivator {__version__}", url, width=1120, height=780, min_size=(640, 560))
        webview.start()
        return

    # Fallback without pywebview: open the page in the default browser.
    print(f"iActivator открыт в браузере: {url}")
    print("Не закрывайте это окно, пока работаете с программой. Выход: Ctrl+C.")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
