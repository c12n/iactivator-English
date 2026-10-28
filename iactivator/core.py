"""Device operations: activation, language/region, skipping Setup Assistant, Wi-Fi.

All functions are async and built on pymobiledevice3, so they work the same on
Windows (needs Apple Devices / iTunes drivers) and macOS.
"""
from __future__ import annotations

import asyncio
import json
import plistlib
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from pymobiledevice3 import usbmux
from pymobiledevice3.ca import create_keybag_file

from pymobiledevice3.exceptions import (
    CloudConfigurationAlreadyPresentError,
    MobileActivationException,
    ProfileError,
)
from pymobiledevice3.lockdown import LockdownClient, create_using_usbmux
from pymobiledevice3.services.diagnostics import DiagnosticsService
from pymobiledevice3.services.mobile_activation import (
    ACTIVATION_DEFAULT_URL as ACTIVATION_URL,
    ACTIVATION_DRM_HANDSHAKE_DEFAULT_URL as ACTIVATION_DRM_HANDSHAKE_URL,
    DEFAULT_HEADERS as ACTIVATION_HEADERS,
    MobileActivationService,
)
from pymobiledevice3.services.mobile_config import MobileConfigService

from .models import capacity_label, model_name

Log = Callable[[str], None]

# Setup Assistant panes skipped when "skip setup" is on. Unknown keys are ignored
# by older iOS versions, so the list can be generous.
SKIP_SETUP_PANES = [
    "Location", "Restore", "SIMSetup", "Android", "AppleID", "IntendedUser", "TOS",
    "Siri", "ScreenTime", "Diagnostics", "SoftwareUpdate", "Passcode", "Biometric",
    "Payment", "Zoom", "DisplayTone", "MessagingActivationUsingPhoneNumber",
    "HomeButtonSensitivity", "CloudStorage", "ScreenSaver", "TapToSetup", "Keyboard",
    "PreferredLanguage", "SpokenLanguage", "WatchMigration", "OnBoarding", "Privacy",
    "iMessageAndFaceTime", "AppStore", "Safety", "Multitasking", "ActionButton",
    "TermsOfAddress", "AccessibilityAppearance", "Welcome", "Appearance",
    "RestoreCompleted", "UpdateCompleted", "WiFi", "Display", "Tone",
    "LanguageAndLocale", "TouchID", "TrueToneDisplay", "iCloudStorage",
    "iCloudDiagnostics", "Registration", "DeviceToDeviceMigration", "UnlockWithWatch",
    "Accessibility", "ExpressLanguage", "Language", "Region", "Avatar",
    "DeviceProtection", "Key", "LockdownMode", "Wallpaper", "PreferredLang",
    "Intelligence", "WebContentFiltering", "CameraButton", "AdditionalPrivacySettings",
    "EnableLockdownMode", "OSShowcase", "SafetyAndHandling", "Tips",
    "AgeBasedSafetySettings", "LiquidGlass",
]

# Wi-Fi security as shown in the UI -> EncryptionType in com.apple.wifi.managed.
# "Any" lets iOS negotiate WPA2/WPA3 itself, which avoids the most common
# "profile installed but Wi-Fi does not connect" failure (WPA3 or mixed-mode routers).
WIFI_SECURITY = {
    "Авто (WPA2/WPA3)": "Any",
    "WPA/WPA2": "WPA",
    "WPA2": "WPA2",
    "WPA3": "WPA3",
    "WEP": "WEP",
    "Без пароля": "None",
}


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path.home() / ".config"
    d = base / "iActivator"
    d.mkdir(parents=True, exist_ok=True)
    return d


def keybag_path(organization: str) -> Path:
    """Supervision identity (certificate + key). It is kept so the same computer can
    keep managing devices it supervised; losing it means the device must be erased
    before it can be supervised again."""
    path = app_data_dir() / "supervision_keybag.pem"
    if not path.exists():
        create_keybag_file(path, organization)
    return path


@dataclass
class WifiOptions:
    ssid: str
    password: str = ""
    security: str = "Any"
    hidden: bool = False
    disable_mac_randomization: bool = True
    auto_join: bool = True


@dataclass
class SetupOptions:
    language: str = "uk"
    region: str = "UA"
    timezone: str = "Europe/Kyiv"
    use_24h: bool = True
    activate: bool = True
    skip_setup: bool = True
    # Supervision lets Wi-Fi install without taps, but iOS then shows "This iPhone is
    # supervised and managed by <organization>" at the top of Settings until erased.
    supervise: bool = False
    organization: str = "iActivator"
    wifi: Optional[WifiOptions] = None
    skip_panes: list = field(default_factory=lambda: list(SKIP_SETUP_PANES))


@dataclass
class DeviceInfo:
    serial: str
    name: str
    product_type: str
    ios: str
    activation_state: str

    def label(self) -> str:
        state = ACTIVATION_RU.get(self.activation_state, self.activation_state)
        return f"{model_name(self.product_type)} · iOS {self.ios} · {state} · {self.serial}"


ACTIVATION_RU = {"Activated": "активирован", "Unactivated": "не активирован"}


class UserFacingError(Exception):
    """An error whose message is already written for the user."""


def short_error(e: Optional[BaseException]) -> str:
    """Device errors carry a whole response dict; keep only the human-readable part."""
    text = str(e)
    if "LocalizedDescription" in text:
        import re
        m = re.search(r"'LocalizedDescription': '([^']*)'", text)
        if m:
            return m.group(1)
    return text[:300]


async def connected_serials() -> list[str]:
    return [d.serial for d in await usbmux.list_devices() if d.connection_type == "USB"]


async def list_devices() -> list[DeviceInfo]:
    result = []
    for dev in await usbmux.list_devices():
        if dev.connection_type != "USB":
            continue
        try:
            lockdown = await create_using_usbmux(serial=dev.serial, autopair=False)
            v = lockdown.all_values
            result.append(DeviceInfo(
                serial=dev.serial,
                name=v.get("DeviceName", "iPhone"),
                product_type=v.get("ProductType", "?"),
                ios=v.get("ProductVersion", "?"),
                activation_state=v.get("ActivationState", "?"),
            ))
            await lockdown.close()
        except Exception as e:  # device busy / not trusted yet
            result.append(DeviceInfo(dev.serial, "Устройство", "?", "?", "нет доступа — разблокируйте iPhone"))
    return result


async def get_supported_languages(serial: str) -> tuple[list[str], list[str]]:
    lockdown = await create_using_usbmux(serial=serial, autopair=True, pair_timeout=60)
    try:
        langs = await lockdown.get_value("com.apple.international", "SupportedLanguages") or []
        locales = await lockdown.get_value("com.apple.international", "SupportedLocales") or []
        return list(langs), list(locales)
    finally:
        await lockdown.close()


def _battery_info(raw: dict) -> dict:
    """Pick capacity numbers out of the IOPMPowerSource entry. Key names differ
    between iOS versions, so several spellings are tried."""
    data = raw.get("BatteryData") or {}

    def first(*keys, allow_zero=False):
        for k in keys:
            for src in (raw, data):
                val = src.get(k)
                if isinstance(val, (int, float)) and not isinstance(val, bool) and (val > 0 or (allow_zero and val == 0)):
                    return val
        return None

    design = first("DesignCapacity")
    full = first("AppleRawMaxCapacity", "NominalChargeCapacity", "MaxCapacity")
    if full is not None and full <= 100 and design and design > 100:
        full = None  # MaxCapacity is a percentage on some versions, not mAh
    level = first("CurrentCapacity", allow_zero=True)
    result = {
        "level": level if level is not None and level <= 100 else None,
        "charging": bool(raw.get("IsCharging")),
        "design_mah": design,
        "full_mah": full,
        "cycles": first("CycleCount", allow_zero=True),  # a new battery has 0 cycles
        "health": round(full * 100 / design) if full and design else None,
    }
    temp = first("Temperature")
    if temp:
        result["temperature"] = round(temp / 100, 1)
    return result


async def get_device_details(serial: str) -> dict:
    lockdown = await create_using_usbmux(serial=serial, autopair=True, pair_timeout=60)
    try:
        v = lockdown.all_values
        info = {
            "name": v.get("DeviceName"),
            "model": v.get("ProductType"),
            "model_name": model_name(v.get("ProductType")),
            "capacity": None,
            "ios": v.get("ProductVersion"),
            "build": v.get("BuildVersion"),
            "baseband": v.get("BasebandVersion"),
            "serial": v.get("SerialNumber"),
            "imei": v.get("InternationalMobileEquipmentIdentity"),
            "imei2": v.get("InternationalMobileEquipmentIdentity2"),
            "activation": v.get("ActivationState"),
            "battery": None,
        }
        try:
            info["capacity"] = capacity_label(await lockdown.get_value("com.apple.disk_usage", "TotalDiskCapacity"))
        except Exception:
            pass
        try:
            async with DiagnosticsService(lockdown) as diag:
                raw = await diag.get_battery()
            if raw:
                info["battery"] = _battery_info(raw)
        except Exception:
            # Diagnostics may refuse on locked or unactivated devices; fall back to the charge level.
            level = await lockdown.get_value("com.apple.mobile.battery", "BatteryCurrentCapacity")
            if level is not None:
                info["battery"] = {"level": level}
        return info
    finally:
        await lockdown.close()


# Substring of a panic log -> what usually is at fault. Checked in order, first match wins.
PANIC_HINTS = [
    ("missing sensor", "Не найден датчик — чаще всего шлейф зарядки, микрофон или датчик на шлейфе кнопок"),
    ("thermalmonitord", "Датчик температуры не отвечает — шлейф зарядки/кнопок, батарея или плата"),
    ("smc panic", "Контроллер питания (SMC) — батарея, шлейф зарядки или плата"),
    ("aop panic", "Сопроцессор датчиков (AOP) — датчики, шлейфы или плата"),
    ("i2c", "Ошибка шины I2C — шлейф или компонент на плате"),
    ("baseband", "Модем — проблема со связью / платой модема"),
    ("nand", "Память (NAND) — неисправность накопителя"),
    ("ans2", "Контроллер памяти (ANS) — неисправность накопителя"),
    ("agx", "Графический процессор"),
    ("watchdog", "Устройство зависло и перезагрузилось (watchdog) — ПО или плата"),
]


def _check(name, status, detail):
    return {"name": name, "status": status, "detail": detail}


async def _panic_checks(lockdown, days: int = 30) -> list[dict]:
    from datetime import datetime, timedelta
    from pymobiledevice3.services.crash_reports import CrashReportsManager

    since = datetime.now() - timedelta(days=days)
    async with CrashReportsManager(lockdown) as crm:
        files = [f for f in await crm.ls("/", depth=2) if "panic-full" in f.lower() or "panic-base" in f.lower()]
        recent = []
        seen = set()
        # One panic is often saved twice (panic-full and panic-base); prefer the full one
        for f in sorted(files, key=lambda f: "panic-base" in f.lower()):
            try:
                # panic-full-2024-05-01-123456.0002.ips
                stamp = f.rsplit("/", 1)[-1].split("panic-")[1].split("-", 1)[1][:17]
                when = datetime.strptime(stamp, "%Y-%m-%d-%H%M%S")
            except (IndexError, ValueError):
                continue
            if when >= since and when not in seen:
                seen.add(when)
                recent.append((when, f))
        recent.sort(reverse=True)
        if not recent:
            return [_check("Перезагрузки (panic)", "ok", f"Сбоев за {days} дней нет")]

        reasons: dict[str, int] = {}
        for _, f in recent[:10]:
            try:
                text = (await crm.afc.get_file_contents(f)).decode("utf-8", "replace")
            except Exception:
                continue
            body = text.split("\n", 1)[-1]
            try:
                panic = json.loads(body).get("panicString", body)
            except ValueError:
                panic = body
            low = panic.lower()
            hint = next((h for key, h in PANIC_HINTS if key in low), None)
            if hint is None:
                hint = "Прочее: " + panic.strip().splitlines()[0][:120] if panic.strip() else "Неизвестная причина"
            reasons[hint] = reasons.get(hint, 0) + 1

    status = "bad" if len(recent) >= 3 else "warn"
    detail = f"{len(recent)} сбоев за {days} дней (последний {recent[0][0]:%d.%m.%Y %H:%M}). " + "; ".join(
        f"{h} ×{n}" for h, n in sorted(reasons.items(), key=lambda x: -x[1]))
    return [_check("Перезагрузки (panic)", status, detail)]


async def _activation_lock_status(lockdown) -> Optional[bool]:
    """Ask Apple's activation server, the same way the iPhone does during setup,
    whether the device is locked to an Apple ID. Nothing is applied to the device.
    Only meaningful before activation; returns None when the answer is unknown."""
    service = MobileActivationService(lockdown)
    headers = {"Content-Type": "application/x-apple-plist", **ACTIVATION_HEADERS}
    try:
        blob = await service.create_activation_session_info()
        handshake, _ = await asyncio.to_thread(
            service.post, ACTIVATION_DRM_HANDSHAKE_URL, plistlib.dumps(blob), headers)
        info = await service.create_activation_info_with_session(handshake)
        request = {"activation-info": plistlib.dumps(info)}
    except Exception:
        # Older iOS without session activation
        request = {"activation-info": plistlib.dumps(await lockdown.get_value(key="ActivationInfo"))}
    content, resp_headers = await asyncio.to_thread(service.post, ACTIVATION_URL, request)
    ctype = resp_headers.get("Content-Type", "")
    if "buddyml" in ctype:
        # A login form (Apple ID / password fields) means Activation Lock.
        return b"editableTextRow" in content or b"secure" in content
    if "xml" in ctype:
        return False  # an activation record came back: no lock
    return None


async def lock_checks(lockdown, own_cert: Optional[bytes] = None, check_activation: bool = True) -> list[dict]:
    """Find My (Activation Lock) and MDM / remote management.

    check_activation=False skips the activation-server query, which must not run while
    the same device is being activated (both use the device's activation session)."""
    results = []
    v = lockdown.all_values

    if v.get("ActivationState") == "Unactivated" and not check_activation:
        results.append(_check("Локатор (блокировка активации)", "info", "Проверка отложена — идёт активация"))
    elif v.get("ActivationState") == "Unactivated":
        try:
            locked = await _activation_lock_status(lockdown)
            if locked:
                results.append(_check("Локатор (блокировка активации)", "bad",
                                      "Устройство привязано к Apple ID. Нужен пароль владельца или отвязка на icloud.com/find"))
            elif locked is False:
                results.append(_check("Локатор (блокировка активации)", "ok", "Блокировки нет"))
            else:
                results.append(_check("Локатор (блокировка активации)", "warn", "Сервер Apple не дал однозначного ответа"))
        except Exception as e:
            results.append(_check("Локатор (блокировка активации)", "warn",
                                  f"Не удалось проверить (нужен интернет): {short_error(e)}"))
    else:
        results.append(_check("Локатор (блокировка активации)", "info",
                              "Устройство уже активировано — по USB статус не узнать. "
                              "Проверьте на iPhone: Настройки → [имя] → Локатор. "
                              "Перед продажей/сбросом его нужно выключить"))

    try:
        async with MobileConfigService(lockdown) as mc:
            cc = await mc.get_cloud_configuration() or {}
            profiles = await mc.get_profile_list()
    except Exception as e:
        results.append(_check("MDM (удалённое управление)", "warn", f"Не удалось прочитать: {short_error(e)}"))
        return results

    ours = own_cert is not None and own_cert in cc.get("SupervisorHostCertificates", [])
    org = cc.get("OrganizationName")
    if ours:
        results.append(_check("MDM (удалённое управление)", "ok", "Под контролем этого компьютера (iActivator)"))
    elif cc.get("IsMDMUnremovable") or cc.get("ConfigurationURL"):
        results.append(_check("MDM (удалённое управление)", "bad",
                              f"Устройство в корпоративной программе (Apple Business Manager): {org or 'организация неизвестна'}. "
                              "Удалить может только владелец организации"))
    elif not cc.get("IsSupervised") and cc.get("ConfigurationSource") == 2 and cc.get("SkipSetup"):
        results.append(_check("MDM (удалённое управление)", "ok", "Не управляется (только пропуск экранов настройки)"))
    elif cc.get("IsSupervised") or org:
        results.append(_check("MDM (удалённое управление)", "warn",
                              f"Контролируемый режим: {org or 'неизвестная организация'} (снимается стиранием)"))
    else:
        results.append(_check("MDM (удалённое управление)", "ok", "Не управляется"))

    meta = profiles.get("ProfileMetadata", {})
    foreign = {pid: m for pid, m in meta.items() if not pid.startswith("com.iactivator.")}
    locked_profiles = [m.get("PayloadDisplayName", pid) for pid, m in foreign.items() if m.get("PayloadRemovalDisallowed")]
    if locked_profiles:
        results.append(_check("Профили", "bad", "Неудаляемые профили: " + ", ".join(locked_profiles)))
    elif foreign:
        results.append(_check("Профили", "warn", "Установлены профили: " +
                              ", ".join(m.get("PayloadDisplayName", pid) for pid, m in foreign.items())))

    try:
        r = await lockdown.get_value("com.apple.mobile.restriction") or {}
        if r.get("ProhibitAppInstall") or r.get("ProhibitAppDelete"):
            results.append(_check("Ограничения", "warn", "Запрещена установка/удаление приложений"))
    except Exception:
        pass
    return results


async def get_lock_checks(serial: str, check_activation: bool = True) -> list[dict]:
    lockdown = await create_using_usbmux(serial=serial, autopair=True, pair_timeout=60)
    try:
        return await lock_checks(lockdown, _own_cert(), check_activation)
    finally:
        await lockdown.close()


def _own_cert() -> Optional[bytes]:
    path = app_data_dir() / "supervision_keybag.pem"
    return _cert_der(path) if path.exists() else None


async def run_health_check(serial: str, check_activation: bool = True) -> list[dict]:
    lockdown = await create_using_usbmux(serial=serial, autopair=True, pair_timeout=60)
    results: list[dict] = []
    try:
        v = lockdown.all_values

        # Battery
        battery = None
        try:
            async with DiagnosticsService(lockdown) as diag:
                raw = await diag.get_battery()
            battery = _battery_info(raw or {}) | {"external": bool((raw or {}).get("ExternalConnected"))}
        except Exception as e:
            results.append(_check("Батарея", "warn", f"Нет доступа к диагностике (разблокируйте iPhone): {short_error(e)}"))
        if battery:
            h, cyc = battery.get("health"), battery.get("cycles")
            if h is None:
                results.append(_check("Ёмкость батареи", "warn", "Устройство не сообщило ёмкость"))
            else:
                st = "ok" if h >= 85 else "warn" if h >= 80 else "bad"
                note = {"ok": "в норме", "warn": "износ заметен", "bad": "рекомендуется замена"}[st]
                results.append(_check("Ёмкость батареи", st, f"{h}% ({battery['full_mah']} из {battery['design_mah']} мАч) — {note}"))
            if cyc is not None:
                st = "ok" if cyc < 500 else "warn" if cyc < 1000 else "bad"
                results.append(_check("Циклы зарядки", st, str(cyc)))
            t = battery.get("temperature")
            if t is not None:
                st = "ok" if t < 40 else "warn" if t < 45 else "bad"
                results.append(_check("Температура батареи", st, f"{t} °C"))
            level = battery.get("level")
            if battery["external"] and not battery["charging"] and level is not None and level < 80:
                results.append(_check("Зарядка", "warn",
                                      f"Кабель подключён, но заряд ({level}%) не идёт — проверьте кабель, порт или контроллер зарядки"))

        # Radios: an empty address / missing modem firmware usually means a chip that did not start
        for key, name in (("WiFiAddress", "Wi-Fi модуль"), ("BluetoothAddress", "Bluetooth")):
            addr = v.get(key)
            ok = bool(addr) and addr.replace(":", "").strip("0") != ""
            results.append(_check(name, "ok" if ok else "bad", addr if ok else "Адрес отсутствует — модуль не определяется"))
        if v.get("TelephonyCapability"):
            if not v.get("BasebandVersion"):
                results.append(_check("Модем", "bad", "Нет версии прошивки модема — модем не запустился"))
            elif not v.get("InternationalMobileEquipmentIdentity"):
                results.append(_check("Модем", "bad", "IMEI не читается"))
            else:
                results.append(_check("Модем", "ok", f"прошивка {v.get('BasebandVersion')}"))

        # Storage
        try:
            du = await lockdown.get_value("com.apple.disk_usage") or {}
            total, free = du.get("TotalDiskCapacity"), du.get("AmountDataAvailable")
            if total and free is not None:
                pct = free * 100 / total
                st = "ok" if pct >= 10 else "warn" if pct >= 3 else "bad"
                results.append(_check("Память", st, f"свободно {free / 1e9:.1f} из {total / 1e9:.0f} ГБ ({pct:.0f}%)"))
        except Exception:
            pass

        # Kernel panics
        try:
            results.extend(await _panic_checks(lockdown))
        except Exception as e:
            results.append(_check("Перезагрузки (panic)", "warn", f"Не удалось прочитать журналы: {short_error(e)}"))

        results = await lock_checks(lockdown, _own_cert(), check_activation) + results
        return results
    finally:
        await lockdown.close()


def build_wifi_profile(wifi: WifiOptions) -> tuple[bytes, str]:
    ssid = wifi.ssid.strip()
    # Stable identifiers: re-installing the same network replaces the old profile
    # instead of piling up conflicting duplicates.
    ns = uuid.uuid5(uuid.NAMESPACE_DNS, "iactivator.wifi." + ssid)
    profile_id = f"com.iactivator.wifi.{ns}"
    payload = {
        "PayloadType": "com.apple.wifi.managed",
        "PayloadVersion": 1,
        "PayloadIdentifier": profile_id + ".payload",
        "PayloadUUID": str(uuid.uuid5(ns, "payload")).upper(),
        "PayloadDisplayName": "Wi-Fi",
        "SSID_STR": ssid,
        "HIDDEN_NETWORK": wifi.hidden,
        "AutoJoin": wifi.auto_join,
        "EncryptionType": wifi.security,
        "IsHotspot": False,
        "CaptiveBypass": False,
        "ProxyType": "None",
        # A randomized ("private") MAC breaks routers with MAC filtering or DHCP
        # reservations — another reason the network "is set but does not work".
        "DisableAssociationMACRandomization": wifi.disable_mac_randomization,
    }
    if wifi.security != "None":
        payload["Password"] = wifi.password
    profile = {
        "PayloadType": "Configuration",
        "PayloadVersion": 1,
        "PayloadIdentifier": profile_id,
        "PayloadUUID": str(ns).upper(),
        "PayloadDisplayName": f"Wi-Fi: {ssid}",
        "PayloadRemovalDisallowed": False,
        "PayloadContent": [payload],
    }
    return plistlib.dumps(profile), profile_id


def _validate_wifi(wifi: WifiOptions) -> None:
    if not wifi.ssid.strip():
        raise UserFacingError("Не указано имя Wi-Fi сети (SSID).")
    if wifi.ssid != wifi.ssid.strip():
        raise UserFacingError("В имени сети есть пробелы в начале/конце — уберите их, иначе iPhone не найдёт сеть.")
    if wifi.security in ("Any", "WPA", "WPA2", "WPA3") and not (8 <= len(wifi.password) <= 63):
        raise UserFacingError("Пароль WPA/WPA2/WPA3 должен быть от 8 до 63 символов.")


def _cert_der(keybag: Path) -> bytes:
    return x509.load_pem_x509_certificate(keybag.read_bytes()).public_bytes(Encoding.DER)


class Activator:
    def __init__(self, serial: str, log: Log):
        self.serial = serial
        self.log = log
        self.lockdown: Optional[LockdownClient] = None

    async def connect(self) -> LockdownClient:
        if self.lockdown is not None:
            await self.lockdown.close()
        self.log("Подключение к устройству… (если появится «Доверять этому компьютеру?» — нажмите «Доверять»)")
        self.lockdown = await create_using_usbmux(serial=self.serial, autopair=True, pair_timeout=120)
        return self.lockdown

    async def close(self) -> None:
        if self.lockdown is not None:
            await self.lockdown.close()
            self.lockdown = None

    async def run(self, opts: SetupOptions) -> None:
        if opts.wifi:
            _validate_wifi(opts.wifi)
        lockdown = await self.connect()
        v = lockdown.all_values
        self.log(f"Устройство: {v.get('ProductType')} iOS {v.get('ProductVersion')}, "
                 f"состояние: {v.get('ActivationState')}")

        # Order matters. Once the iPhone gets online during setup, Setup Assistant fetches
        # its own (empty) configuration from Apple and overwrites ours — the screens then
        # show up again. So: activate over USB (the iPhone stays offline), apply the
        # skip-setup configuration, and give it Wi-Fi only after setup has finished.
        # (The configuration service is not available before activation.)
        if opts.activate:
            await self.activate()
        await self.set_language_region(opts)

        keybag = keybag_path(opts.organization) if opts.skip_setup and opts.supervise else None
        if opts.skip_setup:
            await self.apply_cloud_configuration(opts, keybag)
        elif await self.setup_finished() is False:
            self.log("ℹ «Пропустить все экраны настройки» выключено — экраны на iPhone нужно пройти вручную.")

        if opts.wifi:
            if opts.skip_setup and await self.setup_finished() is False:
                self.log("📱 Проведите вверх по экрану iPhone — он перейдёт на главный экран без настроек. "
                         "Wi-Fi установлю сразу после этого.")
                if not await self.wait_setup_finished(timeout=600):
                    self.log("⚠ iPhone не завершил настройку за 10 минут. Wi-Fi не установлен — "
                             "нажмите «Только Wi-Fi», когда iPhone будет на главном экране.")
                    return
            await self.install_wifi(opts.wifi, keybag)

        self.log("✅ Готово. Если iPhone ещё на экране «Привет» — проведите вверх.")

    async def setup_finished(self) -> Optional[bool]:
        try:
            return bool(await self.lockdown.get_value("com.apple.purplebuddy", "SetupDone"))
        except Exception:
            return None

    async def wait_setup_finished(self, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(3)
            done = await self.setup_finished()
            if done is None:  # connection dropped (cable, lock screen); reconnect quietly
                try:
                    self.lockdown = await create_using_usbmux(serial=self.serial, autopair=True, pair_timeout=30)
                except Exception:
                    continue
            elif done:
                self.log("iPhone завершил настройку.")
                await asyncio.sleep(3)  # let Setup Assistant close before installing profiles
                return True
        return False

    async def activate(self) -> None:
        # Activation goes through this computer's internet connection over USB,
        # so the iPhone itself does not need Wi-Fi for this step.
        service = MobileActivationService(self.lockdown)
        state = await service.state()
        if state != "Unactivated":
            self.log(f"Активация не требуется (состояние: {state}).")
            return
        self.log("Активация через серверы Apple (интернет компьютера)…")
        try:
            await service.activate(skip_apple_id_query=True)
        except MobileActivationException as e:
            if "iCloud" in str(e) or "Activation Lock" in str(e):
                raise UserFacingError(
                    "На устройстве включена Блокировка активации (Найти iPhone). "
                    "Введите Apple ID владельца на самом iPhone или снимите устройство с аккаунта "
                    "на icloud.com/find. Программа не обходит эту блокировку."
                ) from e
            raise
        self.log("Устройство активировано.")
        await self.connect()

    async def set_language_region(self, opts: SetupOptions) -> None:
        locale = f"{opts.language.split('-')[0]}_{opts.region}"
        self.log(f"Язык: {opts.language}, регион/локаль: {locale}, часовой пояс: {opts.timezone}")
        await self.lockdown.set_language(opts.language)
        await self.lockdown.set_locale(locale)
        for setter, value, what in (
            (self.lockdown.set_timezone, opts.timezone, "часовой пояс"),
            (self.lockdown.set_uses24h_clock, opts.use_24h, "24-часовой формат"),
        ):
            try:
                await setter(value)
            except Exception as e:  # not writable on some iOS versions; not critical
                self.log(f"  ⚠ не удалось установить {what}: {e}")

    async def apply_cloud_configuration(self, opts: SetupOptions, keybag: Optional[Path]) -> None:
        cert = _cert_der(keybag) if keybag is not None else None
        async with MobileConfigService(self.lockdown) as mc:
            current = await mc.get_cloud_configuration() or {}
            if cert is not None and current.get("IsSupervised") and cert in current.get("SupervisorHostCertificates", []):
                self.log("Конфигурация уже применена этим компьютером — пропускаю.")
                return
            if cert is None and current.get("ConfigurationSource") == 2 and current.get("SkipSetup"):
                self.log("Пропуск экранов уже настроен — пропускаю.")
                return
            self.log("Применяю конфигурацию: пропуск экранов настройки" +
                     (" + контролируемый режим…" if cert is not None else "…"))
            config = {
                "AllowPairing": True,
                "CloudConfigurationUIComplete": True,
                "ConfigurationSource": 2,
                "ConfigurationWasApplied": True,
                "IsMDMUnremovable": False,
                "IsMandatory": True,
                "IsMultiUser": False,
                "IsSupervised": cert is not None,
                "OrganizationMagic": str(uuid.uuid4()),
                "OrganizationName": opts.organization,
                "PostSetupProfileWasInstalled": True,
                "SkipSetup": opts.skip_panes,
            }
            if cert is not None:
                config["SupervisorHostCertificates"] = [cert]
            try:
                await mc.set_cloud_configuration(config)
            except CloudConfigurationAlreadyPresentError as e:
                raise UserFacingError(
                    "iPhone уже получил свою конфигурацию настройки (он выходил в интернет во время "
                    "настройки, или его настраивали 3uTools / Apple Configurator), и iOS не даёт её заменить. "
                    "Нажмите «Стереть…», затем «Запустить» — после стирания экраны будут пропущены."
                ) from e
        self.log("Экраны настройки будут пропущены.")
        await self.connect()

    async def install_wifi(self, wifi: WifiOptions, keybag: Optional[Path]) -> None:
        profile, profile_id = build_wifi_profile(wifi)
        self.log(f"Wi-Fi: «{wifi.ssid}», защита {wifi.security}, "
                 f"{'скрытая, ' if wifi.hidden else ''}"
                 f"{'без случайного MAC' if wifi.disable_mac_randomization else 'частный MAC'}")
        last_error: Optional[Exception] = None
        for attempt in (1, 2, 3):
            try:
                async with MobileConfigService(self.lockdown) as mc:
                    if keybag is None:
                        # Without supervision iOS only accepts the profile as "downloaded";
                        # the user has to confirm it once in Settings.
                        await mc.install_profile(profile)
                        self.log("  📱 На iPhone: Настройки → «Профиль загружен» (вверху) → Установить. "
                                 "Чтобы Wi-Fi ставился без нажатий, включите «Контролируемый режим» "
                                 "(на сброшенном iPhone).")
                        return
                    await mc.install_profile_silent(keybag, profile)
                    # The radio is often left off after a skipped setup: the profile is
                    # installed but nothing connects. Turning it on needs the escalated
                    # (supervisor) session that install_profile_silent just opened.
                    try:
                        await mc.set_wifi_power_state(True)
                    except ProfileError:
                        pass
                    installed = (await mc.get_profile_list()).get("ProfileMetadata", {})
                if profile_id in installed:
                    self.log("  Профиль Wi-Fi установлен и подтверждён устройством.")
                    return
                last_error = UserFacingError("устройство не показало профиль после установки")
            except (ProfileError, ConnectionError, OSError) as e:
                last_error = e
            self.log(f"  попытка {attempt} не удалась: {short_error(last_error)}; повтор…")
            await asyncio.sleep(2)
            await self.connect()
        raise UserFacingError(f"Не удалось установить профиль Wi-Fi: {short_error(last_error)}")

    async def install_wifi_only(self, wifi: WifiOptions) -> None:
        """Wi-Fi for an already set-up device: silent if this computer supervises it,
        otherwise the profile has to be confirmed on the iPhone."""
        _validate_wifi(wifi)
        await self.connect()
        keybag = app_data_dir() / "supervision_keybag.pem"
        ours = False
        if keybag.exists():
            async with MobileConfigService(self.lockdown) as mc:
                current = await mc.get_cloud_configuration() or {}
            ours = _cert_der(keybag) in current.get("SupervisorHostCertificates", [])
        if not ours:
            self.log("Устройство не контролируется этим компьютером — профиль потребует подтверждения на iPhone.")
        await self.install_wifi(wifi, keybag if ours else None)

    async def erase(self) -> None:
        lockdown = await self.connect()
        self.log("Стираю устройство (все данные будут удалены)…")
        async with MobileConfigService(lockdown) as mc:
            await mc.erase_device(preserve_data_plan=True, disallow_proximity_setup=False)
        try:
            await lockdown.close()
        except Exception:
            pass  # the device drops the connection while erasing
        self.lockdown = None
        self.log("Команда стирания отправлена. Дождитесь перезагрузки iPhone.")
