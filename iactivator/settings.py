"""Saved Wi-Fi networks and last-used form values.

Network names and options live in settings.json; passwords go to the OS password
store (Keychain on macOS, Credential Manager on Windows) via keyring.
"""
from __future__ import annotations

import json
import threading

import keyring

from .core import app_data_dir

SERVICE = "iActivator Wi-Fi"
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
