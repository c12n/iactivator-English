"""History of processed devices: who was connected and what was done with them.

Kept in history.json (app data folder); history.csv next to it is rewritten on every
change so the list can be opened in Excel without the program.
"""
from __future__ import annotations

import csv
import json
import threading
from datetime import datetime

from .core import app_data_dir

_lock = threading.Lock()
RESULT = {"ok": "Успешно", "warn": "Замечания", "error": "Ошибка", "info": ""}


def json_path():
    return app_data_dir() / "history.json"


def csv_path():
    return app_data_dir() / "history.csv"


def _load() -> dict:
    try:
        return json.loads(json_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    tmp = json_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(json_path())
    _write_csv(data)


def _write_csv(data: dict) -> None:
    # One row per event; ";" and a BOM so Excel with a Russian locale opens it correctly.
    try:
        with open(csv_path(), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Дата и время", "Серийный №", "IMEI", "IMEI 2", "Модель", "Память", "iOS", "Действие", "Результат", "Подробности"])
            rows = [(e, d) for d in data.values() for e in d.get("events", [])]
            for e, d in sorted(rows, key=lambda r: r[0]["time"]):
                w.writerow([e["time"], d["serial"], d.get("imei") or "", d.get("imei2") or "", d.get("model_name") or d.get("model") or "",
                            d.get("capacity") or "", d.get("ios") or "", e["action"], RESULT.get(e["result"], e["result"]), e.get("detail", "")])
    except OSError:
        pass  # the file is open in Excel; it will be rewritten on the next change


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _entry(data: dict, serial: str) -> dict:
    return data.setdefault(serial, {"serial": serial, "first_seen": _now(), "events": []})


def update_device(info: dict) -> None:
    """Store the latest known facts about a device (model, IMEI, battery…)."""
    serial = info.get("serial")
    if not serial:
        return
    with _lock:
        data = _load()
        d = _entry(data, serial)
        for k in ("imei", "imei2", "model", "model_name", "capacity", "ios", "name"):
            if info.get(k):
                d[k] = info[k]
        b = info.get("battery") or {}
        if b.get("health") is not None:
            d["battery"] = b["health"]
        d["activation"] = info.get("activation") or d.get("activation")
        d["last_seen"] = _now()
        _save(data)


def add_event(serial: str, action: str, result: str, detail: str = "") -> None:
    """result: ok | warn | error | info."""
    if not serial:
        return
    with _lock:
        data = _load()
        d = _entry(data, serial)
        d["events"].append({"time": _now(), "action": action, "result": result, "detail": detail})
        d["last_seen"] = _now()
        _save(data)


def summary() -> dict:
    with _lock:
        data = _load()
    today = datetime.now().strftime("%Y-%m-%d")
    devices = sorted(data.values(), key=lambda d: d.get("last_seen", ""), reverse=True)
    def did(d, action, result="ok"):
        return any(e["action"] == action and e["result"] == result for e in d.get("events", []))
    return {
        "devices": devices,
        "stats": {
            "total": len(devices),
            "today": sum(1 for d in devices if d.get("last_seen", "").startswith(today)),
            "activated": sum(1 for d in devices if did(d, "Активация")),
            "faulty": sum(1 for d in devices if did(d, "Диагностика", "error") or did(d, "Локатор и MDM", "error")),
        },
        "csv": str(csv_path()),
    }


def delete(serial: str) -> None:
    with _lock:
        data = _load()
        data.pop(serial, None)
        _save(data)


def export() -> dict:
    with _lock:
        return _load()


def merge(other: dict) -> int:
    """Add devices and events from another computer's history; returns how many devices it had."""
    with _lock:
        data = _load()
        for serial, d in other.items():
            if not isinstance(d, dict) or not d.get("events") and not d.get("model"):
                continue
            mine = data.setdefault(serial, {k: v for k, v in d.items() if k != "events"} | {"events": []})
            seen = {(e["time"], e["action"]) for e in mine["events"]}
            mine["events"] += [e for e in d.get("events", []) if (e.get("time"), e.get("action")) not in seen]
            mine["events"].sort(key=lambda e: e["time"])
            for k, v in d.items():
                if k != "events" and v and not mine.get(k):
                    mine[k] = v
            if d.get("last_seen", "") > mine.get("last_seen", ""):
                mine["last_seen"] = d["last_seen"]
        _save(data)
    return len(other)
