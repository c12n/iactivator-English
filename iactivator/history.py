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
RESULT = {"ok": "Success", "warn": "Warnings", "error": "Error", "info": ""}


def json_path():
    return app_data_dir() / "history.json"


def csv_path():
    return app_data_dir() / "history.csv"


def _load() -> dict:
    try:
        return _migrate(json.loads(json_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def _is_udid(s: str) -> bool:
    """A connection identifier (UDID/ECID), not a human serial number like 'L4VKQY7W10'."""
    return bool(s) and (len(s) >= 20 or "-" in s or "~" in s)


def _window(d: dict) -> tuple[str, str]:
    times = [e["time"] for e in d.get("events", []) if e.get("time")]
    times += [t for t in (d.get("first_seen"), d.get("last_seen")) if t]
    return (min(times), max(times)) if times else ("", "")


def _migrate(data: dict) -> dict:
    """Older versions stored one physical phone under two keys — its connection id
    (holding the events) and its serial number (holding the model, IMEI, iOS). Give
    every record an explicit id and glue such split pairs back into one row."""
    for key, d in data.items():
        d.setdefault("id", key)
    # info-only record (serial key, has model, no events) -> merge into the overlapping
    # events-only record (connection-id key, has events, no model).
    info_only = [d for d in data.values()
                 if (d.get("model_name") or d.get("model")) and not d.get("events") and not _is_udid(d["id"])]
    event_only = [d for d in data.values()
                  if d.get("events") and not (d.get("model_name") or d.get("model")) and _is_udid(d["id"])]
    drop = []
    for b in info_only:
        bs, be = _window(b)
        hits = [a for a in event_only if (aw := _window(a)) and aw[0] <= be and bs <= aw[1]]
        if len(hits) != 1:
            continue  # ambiguous: leave both records untouched
        a = hits[0]
        a["serial"] = b.get("serial") or b["id"]  # show the human serial number
        for k, v in b.items():
            if k not in ("id", "serial", "events") and v and not a.get(k):
                a[k] = v
        drop.append(b["id"])
        event_only.remove(a)
    for key in drop:
        data.pop(key, None)
    return data


def _save(data: dict) -> None:
    tmp = json_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(json_path())
    _write_csv(data)


def _write_csv(data: dict) -> None:
    # One row per event; a BOM is included so Excel opens the UTF-8 file correctly.
    try:
        with open(csv_path(), "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=",")
            w.writerow(["Date and time", "Serial No.", "IMEI", "IMEI 2", "Model", "Storage", "iOS", "Action", "Result", "Details"])
            rows = [(e, d) for d in data.values() for e in d.get("events", [])]
            for e, d in sorted(rows, key=lambda r: r[0]["time"]):
                w.writerow([e["time"], d.get("serial") or d.get("id") or "", d.get("imei") or "", d.get("imei2") or "", d.get("model_name") or d.get("model") or "",
                            d.get("capacity") or "", d.get("ios") or "", e["action"], RESULT.get(e["result"], e["result"]), e.get("detail", "")])
    except OSError:
        pass  # the file is open in Excel; it will be rewritten on the next change


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _entry(data: dict, device_id: str) -> dict:
    # Keyed by the connection id (UDID) so events and device facts land in one row;
    # "serial" holds the human serial number for display and stays empty until it is read.
    return data.setdefault(device_id, {"id": device_id, "serial": "", "first_seen": _now(), "events": []})


def update_device(info: dict) -> None:
    """Store the latest known facts about a device (model, IMEI, battery…)."""
    device_id = info.get("conn_id") or info.get("serial")
    if not device_id:
        return
    with _lock:
        data = _load()
        d = _entry(data, device_id)
        if info.get("serial"):
            d["serial"] = info["serial"]
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
            "activated": sum(1 for d in devices if did(d, "Activation")),
            "faulty": sum(1 for d in devices if did(d, "Diagnostics", "error") or did(d, "Find My & MDM", "error")),
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
