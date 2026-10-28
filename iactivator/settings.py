"""Saved Wi-Fi networks, last-used form values and program preferences.

Network names and options live in settings.json; passwords go to the OS password
store (Keychain on macOS, Credential Manager on Windows) via keyring.
export_data()/import_data() move all of it to another computer in one JSON file.
"""
from __future__ import annotations

import base64
import json
import threading
from datetime import datetime

import keyring

from . import __version__, history
from .core import app_data_dir

SERVICE = "iActivator Wi-Fi"
THEMES = ("auto", "light", "dark")
EXPORT_KIND = "iActivator settings"
_lock = threading.Lock()


def _path():
    return app_data_dir() / "settings.json"


def load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.setdefault("networks", [])
    data.setdefault("last", {})
    data.setdefault("ui", {})
    data["ui"].setdefault("theme", "auto")
    return data


def _save(data: dict) -> None:
    tmp = _path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_path())


def save_network(ssid: str, password: str, security: str, hidden: bool, nomac: bool) -> None:
    ssid = ssid.strip()
    with _lock:
        data = load()
        nets = [n for n in data["networks"] if n["ssid"] != ssid]
        nets.insert(0, {"ssid": ssid, "security": security, "hidden": hidden, "nomac": nomac})
        data["networks"] = nets
        _save(data)
    if password:
        keyring.set_password(SERVICE, ssid, password)


def get_password(ssid: str) -> str:
    return keyring.get_password(SERVICE, ssid) or ""


def delete_network(ssid: str) -> None:
    with _lock:
        data = load()
        data["networks"] = [n for n in data["networks"] if n["ssid"] != ssid]
        _save(data)
    try:
        keyring.delete_password(SERVICE, ssid)
    except keyring.errors.PasswordDeleteError:
        pass


def save_last(values: dict) -> None:
    """Remember non-secret form values (language, region, …) for the next launch."""
    keep = ("language", "region", "timezone", "h24", "activate", "skip_setup", "supervise", "organization", "ssid", "auto")
    with _lock:
        data = load()
        data["last"] = {k: values[k] for k in keep if k in values}
        _save(data)


def save_ui(values: dict) -> None:
    """Program preferences (theme, …)."""
    with _lock:
        data = load()
        if values.get("theme") in THEMES:
            data["ui"]["theme"] = values["theme"]
        _save(data)


def _keybag():
    return app_data_dir() / "supervision_keybag.pem"


def export_data(secrets: bool, include_history: bool) -> dict:
    data = load()
    nets = []
    for n in data["networks"]:
        n = dict(n)
        if secrets:
            n["password"] = get_password(n["ssid"])
        nets.append(n)
    out = {"kind": EXPORT_KIND, "version": __version__, "exported": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "networks": nets, "last": data["last"], "ui": data["ui"]}
    if secrets and _keybag().exists():
        out["supervision_keybag"] = base64.b64encode(_keybag().read_bytes()).decode()
    if include_history:
        out["history"] = history.export()
    return out


def import_data(src: dict) -> list[str]:
    """Merge an exported file into this computer's settings; returns what was done, for the journal."""
    if not isinstance(src, dict) or src.get("kind") != EXPORT_KIND:
        raise ValueError("Это не файл настроек iActivator")
    done = []
    nets = [n for n in src.get("networks", []) if isinstance(n, dict) and str(n.get("ssid", "")).strip()]
    for n in reversed(nets):  # save_network puts each at the top; keep the file's order
        save_network(n["ssid"], n.get("password", ""), n.get("security", "Any"), bool(n.get("hidden")), bool(n.get("nomac", True)))
    if nets:
        with_pass = sum(1 for n in nets if n.get("password"))
        done.append(f"Сети Wi-Fi: {len(nets)}" + (f" (с паролями: {with_pass})" if with_pass else " (без паролей)"))
    with _lock:
        data = load()
        if isinstance(src.get("last"), dict):
            data["last"] = src["last"]
            done.append("Язык, регион и режимы")
        if isinstance(src.get("ui"), dict) and src["ui"].get("theme") in THEMES:
            data["ui"]["theme"] = src["ui"]["theme"]
            done.append("Тема оформления")
        _save(data)
    if src.get("supervision_keybag"):
        key = base64.b64decode(src["supervision_keybag"])
        if not _keybag().exists():
            _keybag().write_bytes(key)
            done.append("Ключ контролируемого режима")
        elif _keybag().read_bytes() != key:
            done.append("⚠ Ключ контролируемого режима не заменён: на этом компьютере уже есть свой")
    if isinstance(src.get("history"), dict):
        done.append(f"История: устройств {history.merge(src['history'])}")
    return done
