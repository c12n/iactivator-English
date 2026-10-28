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
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, applog, core, history, report, settings

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

ACTIVATION = {"Activated": "активирован", "Unactivated": "не активирован"}

# Shown on the About page. Wallets: (coin and network, address).
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

    def log(self, msg: str, serial: str = "", level: str = "") -> None:
        applog.add(msg, serial, level)

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
            except Exception as e:
                self.log("❌ Ошибка: " + core.short_error(e), serial)
                applog.exception(e, serial)
            finally:
                self.busy = False
        asyncio.run_coroutine_threadsafe(job(), self.loop)
        return True


STATE = State()


async def _with_activator(serial, fn, action, detail=""):
    act = core.Activator(serial, lambda m: STATE.log(m, serial))
    STATE.log(f"▶ {action}" + (f": {detail}" if detail else ""), serial)
    started = time.monotonic()
    try:
        await fn(act)
        history.add_event(serial, action, "ok", detail)
        STATE.log(f"✅ {action}: готово за {time.monotonic() - started:.0f} с", serial)
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
    STATE.log(f"{action}: " + (f"неисправностей {len(bad)}, замечаний {len(warn)}" if bad or warn else "без замечаний"),
              serial, "error" if bad else "warn" if warn else "ok")
    if bad or warn:
        detail = "; ".join(f"{r['name']}: {r['detail']}" for r in bad + warn)
        history.add_event(serial, action, "error" if bad else "warn", detail)
    else:
        history.add_event(serial, action, "ok", "Без замечаний")


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
        if self.path.startswith("/api/log"):
            entries, total = applog.since(int(self.path.partition("since=")[2] or 0))
            return self._send(200, {"entries": entries, "next": total, "busy": STATE.busy})
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
                msg = f"Не удалось получить список устройств: {core.short_error(e)}"
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
                return self._send(400, {"error": "Введите имя сети"})
            settings.save_network(d["ssid"], d.get("password", ""), d.get("security", "Any"),
                                  bool(d.get("hidden")), bool(d.get("nomac", True)))
            STATE.log(f"Сеть «{d['ssid'].strip()}» сохранена (защита {d.get('security', 'Any')}"
                      + (", скрытая" if d.get("hidden") else "") + ")")
            return self._send(200, settings.load())
        if self.path == "/api/network/delete":
            settings.delete_network(d.get("ssid", ""))
            STATE.log(f"Сеть «{d.get('ssid', '')}» удалена")
            return self._send(200, settings.load())
        if self.path == "/api/settings/ui":
            settings.save_ui(d)
            STATE.log(f"Тема оформления: {THEME_NAMES.get(d.get('theme'), d.get('theme'))}", level="debug")
            return self._send(200, settings.load())
        if self.path == "/api/settings/export":
            return self._send(200, _export(bool(d.get("secrets")), bool(d.get("history"))))
        if self.path == "/api/settings/import":
            try:
                done = settings.import_data(d.get("data"))
            except Exception as e:
                STATE.log(f"❌ Импорт настроек: {e}")
                return self._send(200, {"error": f"Не удалось загрузить настройки: {e}"})
            STATE.log(f"✅ Настройки загружены из «{d.get('name', 'файла')}»")
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
        if self.path == "/api/open-url":
            url = d.get("url", "")
            if url == INSTAGRAM:
                webbrowser.open(url)
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
            return self._send(400, {"error": "Не выбрано устройство"})

        if self.path == "/api/locks":
            try:
                res = STATE.run(core.get_lock_checks(serial, check_activation=not STATE.busy))
                if d.get("record"):
                    _record_checks(serial, "Локатор и MDM", res)
                return self._send(200, res)
            except Exception as e:
                return self._fail(e, serial, "Локатор и MDM")

        if self.path == "/api/check":
            STATE.log("Диагностика: проверяю…", serial)
            try:
                res = STATE.run(core.run_health_check(serial, check_activation=not STATE.busy))
                _record_checks(serial, "Диагностика", res)
                return self._send(200, res)
            except Exception as e:
                return self._fail(e, serial, "Диагностика")

        if self.path == "/api/report":
            try:
                info = STATE.run(core.get_device_details(serial))
                history.update_device(info)
                results = STATE.run(core.run_health_check(serial, check_activation=not STATE.busy))
                _record_checks(serial, "Диагностика", results)
                path = report.build_pdf(info, results)
                history.add_event(serial, "PDF-отчёт", "info", path.name)
                STATE.log(f"📄 Отчёт сохранён: {path}", serial, "ok")
                report.open_file(path)
                return self._send(200, {"path": str(path)})
            except Exception as e:
                return self._fail(e, serial, "PDF-отчёт")

        if self.path in ("/api/run", "/api/wifi"):
            # Remember the form (and the network, if asked) before the job starts.
            try:
                settings.save_last(d)
                if d.get("ssid", "").strip():
                    settings.save_network(d["ssid"], d.get("password", ""), d.get("security", "Any"),
                                          bool(d.get("hidden")), bool(d.get("nomac", True)))
            except Exception as e:
                STATE.log(f"⚠ Не удалось сохранить сеть: {e}", serial)

        if self.path == "/api/languages":
            try:
                langs, _ = STATE.run(core.get_supported_languages(serial))
                return self._send(200, langs)
            except Exception as e:
                return self._fail(e, serial, "Языки устройства")

        if self.path == "/api/info":
            try:
                info = STATE.run(core.get_device_details(serial))
                history.update_device(info)
                if d.get("record"):
                    b = info.get("battery") or {}
                    detail = (f"{ACTIVATION.get(info.get('activation'), info.get('activation'))}, iOS {info.get('ios')} ({info.get('build')})"
                              + (f", батарея {b['health']}%" if b.get("health") is not None else ""))
                    history.add_event(serial, "Подключение", "info", detail)
                    STATE.log(f"Подключён {info.get('model_name')}"
                              + (f", {info['capacity']}" if info.get("capacity") else "") + f": {detail}", serial)
                    STATE.log(f"IMEI {info.get('imei') or '—'}" + (f", IMEI 2 {info['imei2']}" if info.get("imei2") else ""),
                              serial, "debug")
                return self._send(200, info)
            except Exception as e:
                return self._fail(e, serial, "Чтение данных")

        if self.path == "/api/run":
            opts = _options(d)
            detail = f"{opts.language}, {opts.region}" + (f", Wi-Fi «{opts.wifi.ssid}»" if opts.wifi else "")
            action = "Активация" if opts.activate else "Настройка"
            job = lambda: _with_activator(serial, lambda a: a.run(opts), action, detail)
        elif self.path == "/api/wifi":
            wifi = _wifi(d)
            if wifi is None:
                return self._send(400, {"error": "Введите имя сети"})
            job = lambda: _with_activator(serial, lambda a: a.install_wifi_only(wifi), "Wi-Fi", f"«{wifi.ssid}»")
        elif self.path == "/api/erase":
            job = lambda: _with_activator(serial, lambda a: a.erase(), "Стирание")
        else:
            return self._send(404, {"error": "not found"})
        if not STATE.run_job(job, serial):
            return self._send(409, {"error": "Уже выполняется другая операция"})
        self._send(200, {"ok": True})


THEME_NAMES = {"auto": "как в системе", "light": "светлая", "dark": "тёмная"}
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
    path = _save_path(f"iActivator-настройки-{time.strftime('%Y-%m-%d')}.json")
    if path is None:
        return {"cancelled": True}
    data = settings.export_data(secrets, with_history)
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        STATE.log(f"❌ Не удалось сохранить настройки: {e}")
        return {"error": f"Не удалось сохранить файл: {e}"}
    STATE.log(f"✅ Настройки сохранены в файл: {path}")
    STATE.log(f"  Сети Wi-Fi: {len(data['networks'])}" + (", с паролями" if secrets else ", без паролей")
              + (", ключ контролируемого режима" if "supervision_keybag" in data else "")
              + (f", история: устройств {len(data['history'])}" if "history" in data else ""))
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


def main():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    try:
        import webview  # native window: WebKit on macOS, Edge WebView2 on Windows
    except ImportError:
        webview = None

    applog.cleanup()
    STATE.log(f"iActivator {__version__} запущен · {platform.system()} {platform.release()} · Python {platform.python_version()}",
              level="debug")

    if webview is not None:
        global WINDOW
        threading.Thread(target=server.serve_forever, daemon=True).start()
        WINDOW = webview.create_window(f"iActivator {__version__}", url, width=1120, height=780, min_size=(640, 560))
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
