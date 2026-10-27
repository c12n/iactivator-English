"""Device operations: activation, language/region, skipping Setup Assistant, Wi-Fi.

All functions are async and built on pymobiledevice3, so they work the same on
Windows (needs Apple Devices / iTunes drivers) and macOS.
"""
from __future__ import annotations

import asyncio
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
from pymobiledevice3.services.mobile_activation import MobileActivationService
from pymobiledevice3.services.mobile_config import MobileConfigService

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
        return f"{self.name} ({self.product_type}, iOS {self.ios}) — {self.activation_state} — {self.serial}"


class UserFacingError(Exception):
    """An error whose message is already written for the user."""


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
            result.append(DeviceInfo(dev.serial, "Устройство", "?", "?", f"нет доступа: {e}"))
    return result


async def get_supported_languages(serial: str) -> tuple[list[str], list[str]]:
    lockdown = await create_using_usbmux(serial=serial, autopair=True, pair_timeout=60)
    try:
        langs = await lockdown.get_value("com.apple.international", "SupportedLanguages") or []
        locales = await lockdown.get_value("com.apple.international", "SupportedLocales") or []
        return list(langs), list(locales)
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

        if opts.activate:
            await self.activate()
        await self.set_language_region(opts)

        keybag = None
        if opts.skip_setup:
            keybag = keybag_path(opts.organization)
            await self.apply_cloud_configuration(opts, keybag)

        if opts.wifi:
            await self.install_wifi(opts.wifi, keybag)

        self.log("✅ Готово. Если iPhone ещё на экране «Привет» — проведите вверх / нажмите «Домой».")

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

    async def apply_cloud_configuration(self, opts: SetupOptions, keybag: Path) -> None:
        cert = _cert_der(keybag)
        async with MobileConfigService(self.lockdown) as mc:
            current = await mc.get_cloud_configuration() or {}
            if current.get("IsSupervised") and cert in current.get("SupervisorHostCertificates", []):
                self.log("Конфигурация уже применена этим компьютером — пропускаю.")
                return
            self.log("Применяю конфигурацию: пропуск экранов настройки + контролируемый режим…")
            config = {
                "AllowPairing": True,
                "CloudConfigurationUIComplete": True,
                "ConfigurationSource": 2,
                "ConfigurationWasApplied": True,
                "IsMDMUnremovable": False,
                "IsMandatory": True,
                "IsMultiUser": False,
                "IsSupervised": True,
                "OrganizationMagic": str(uuid.uuid4()),
                "OrganizationName": opts.organization,
                "PostSetupProfileWasInstalled": True,
                "SkipSetup": opts.skip_panes,
                "SupervisorHostCertificates": [cert],
            }
            try:
                await mc.set_cloud_configuration(config)
            except CloudConfigurationAlreadyPresentError as e:
                raise UserFacingError(
                    "На устройстве уже есть конфигурация от другой программы/компьютера "
                    "(например, 3uTools или Apple Configurator). Сотрите iPhone "
                    "(кнопка «Стереть устройство») и повторите."
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
                    # The radio is often left off after a skipped setup: the profile is
                    # installed but nothing connects. Force it on first.
                    try:
                        await mc.set_wifi_power_state(True)
                    except ProfileError as e:
                        self.log(f"  ⚠ не удалось включить Wi-Fi модуль: {e}")
                    if keybag is not None:
                        await mc.install_profile_silent(keybag, profile)
                    else:
                        await mc.install_profile(profile)
                        self.log("  На iPhone откройте Настройки → Профиль загружен и установите его.")
                        return
                    installed = (await mc.get_profile_list()).get("ProfileMetadata", {})
                if profile_id in installed:
                    self.log("  Профиль Wi-Fi установлен и подтверждён устройством.")
                    return
                last_error = UserFacingError("устройство не показало профиль после установки")
            except (ProfileError, ConnectionError, OSError) as e:
                last_error = e
            self.log(f"  попытка {attempt} не удалась: {last_error}; повтор…")
            await asyncio.sleep(2)
            await self.connect()
        raise UserFacingError(f"Не удалось установить профиль Wi-Fi: {last_error}")

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
