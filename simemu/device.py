"""
Real device operations — iOS via devicectl/ios-deploy, Android via adb.

Real devices connect over USB or WiFi. They are identified by:
  - iOS: UDID (40-char hex or 24-char + dash format)
  - Android: adb serial (USB serial or <ip>:<port>)

Most Android operations reuse the android module directly since adb works
the same way for real devices and emulators. iOS real devices require
devicectl (Xcode 15+) instead of simctl.
"""

import json
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .device_aliases import find_alias_for_device

# iOS UDIDs: 40-char hex (USB) or 00008XXX-XXXX... (WiFi/newer devices)
_IOS_UDID_RE = re.compile(r"^[0-9a-fA-F-]{24,40}$")


@dataclass
class RealDevice:
    device_id: str      # UDID (iOS) or serial (Android)
    platform: str       # "ios" | "android"
    device_name: str
    connected: bool
    os_version: str     # e.g. "18.2" or "15"
    connection: str     # "usb" | "wifi"
    alias: str = ""


def _has_devicectl() -> bool:
    return shutil.which("devicectl") is not None or shutil.which("xcrun") is not None


def _is_ios_family_platform(platform: str) -> bool:
    """Return True for real iPhone/iPad device platforms across Apple tool variants."""
    normalized = platform.strip().lower()
    return normalized in {
        "ios",
        "ipados",
        "iphoneos",
        "com.apple.platform.iphoneos",
        "com.apple.platform.ipados",
    }


def _devicectl(*args, capture: bool = True) -> Optional[str]:
    """Run xcrun devicectl with args."""
    cmd = ["xcrun", "devicectl"] + list(args)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout.strip()
    subprocess.run(cmd, check=True)
    return None


def _list_devicectl_devices_json() -> Optional[list[dict]]:
    """Return raw device rows from `xcrun devicectl list devices`.

    `devicectl --json-output /dev/stdout` is not portable across macOS/Xcode
    combinations; some environments reject `/dev/stdout` with NSCocoaError 512.
    Write to a temporary file instead, then load JSON from disk.
    """
    if not _has_devicectl():
        return None

    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
            tmp_path = Path(handle.name)

        result = subprocess.run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", str(tmp_path)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None

        data = json.loads(tmp_path.read_text())
        return data.get("result", {}).get("devices", [])
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError, OSError):
        return None
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _list_xcdevice_devices_json() -> Optional[list[dict]]:
    """Fallback raw rows from `xcrun xcdevice list`.

    `xcdevice` is less featureful than `devicectl`, but it reliably exposes
    connected real iPhone/iPad hardware on machines where `devicectl` JSON
    output is flaky.
    """
    try:
        result = subprocess.run(
            ["xcrun", "xcdevice", "list"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None
        data = json.loads(result.stdout)
        if isinstance(data, list):
            return data
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return None
    return None


def list_ios_devices() -> list[RealDevice]:
    """List connected real iOS devices via devicectl (Xcode 15+).

    Falls back to empty list if devicectl is not available or no devices
    are connected.
    """
    # Prefer xcdevice identifiers because they line up with xcodebuild and
    # libimobiledevice tooling, while devicectl can emit a different internal
    # CoreDevice UUID for the same physical iPhone.
    result_devices = _list_xcdevice_devices_json()
    source = "xcdevice"
    if result_devices is None:
        result_devices = _list_devicectl_devices_json()
        source = "devicectl"
    if result_devices is None:
        return []

    devices = []
    for dev in result_devices:
        if source == "devicectl":
            conn_props = dev.get("connectionProperties", {})
            hw_props = dev.get("hardwareProperties", {})
            device_props = dev.get("deviceProperties", {})
            platform = hw_props.get("platform", "")

            # Keep only real iPhone/iPad family hardware.
            if dev.get("simulator", False):
                continue
            if platform and not _is_ios_family_platform(platform):
                continue

            udid = dev.get("identifier", "")
            if not udid:
                continue

            transport = conn_props.get("transportType", "")
            connection = "wifi" if transport == "wifi" else "usb"
            name = device_props.get("name", hw_props.get("marketingName", "iOS Device"))
            os_version = device_props.get("osVersionNumber", "")
        else:
            # xcdevice emits a flatter schema.
            if dev.get("simulator", False):
                continue
            if not dev.get("available", True):
                continue
            platform = dev.get("platform", "")
            if platform and not _is_ios_family_platform(platform):
                continue

            udid = dev.get("identifier", "")
            if not udid:
                continue

            connection = "wifi" if dev.get("interface") == "network" else "usb"
            name = dev.get("name", dev.get("modelName", "iOS Device"))
            os_version = str(dev.get("operatingSystemVersion", "")).split(" ", 1)[0]

        devices.append(RealDevice(
            device_id=udid,
            platform="ios",
            device_name=name,
            connected=True,
            os_version=str(os_version),
            connection=connection,
            alias=find_alias_for_device("ios", udid) or "",
        ))

    return devices


def list_android_devices() -> list[RealDevice]:
    """List connected real Android devices (not emulators) via adb."""
    try:
        out = subprocess.check_output(
            ["adb", "devices", "-l"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).decode()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return []

    devices = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        serial = parts[0]

        # Skip emulators — they use "emulator-XXXX" serials
        if serial.startswith("emulator-"):
            continue

        # Parse the extra key=value fields
        extras = {}
        for part in parts[2:]:
            if ":" in part:
                k, v = part.split(":", 1)
                extras[k] = v

        model = extras.get("model", "Android Device").replace("_", " ")

        # Get OS version
        os_version = ""
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "getprop", "ro.build.version.release"],
                capture_output=True, text=True, timeout=5,
            )
            os_version = result.stdout.strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass

        # Determine connection type
        connection = "wifi" if ":" in serial else "usb"

        devices.append(RealDevice(
            device_id=serial,
            platform="android",
            device_name=model,
            connected=True,
            os_version=os_version,
            connection=connection,
            alias=find_alias_for_device("android", serial) or "",
        ))

    return devices


def list_all_devices(allocated_ids: set[str] | None = None) -> list[RealDevice]:
    """List all connected real devices (iOS + Android), excluding allocated ones."""
    allocated_ids = allocated_ids or set()
    devices = list_ios_devices() + list_android_devices()
    return [d for d in devices if d.device_id not in allocated_ids]


def ios_install(udid: str, app_path: str, timeout: int = 120) -> None:
    """Install an app on a real iOS device.

    Supports .ipa files via devicectl (Xcode 15+).
    """
    path = Path(app_path)
    if not path.exists():
        raise RuntimeError(f"App not found: {app_path}")
    if path.suffix not in (".ipa", ".app"):
        raise RuntimeError(
            f"Real iOS devices require .ipa (or .app for dev-signed). Got: {path.suffix}"
        )

    try:
        result = subprocess.run(
            ["xcrun", "devicectl", "device", "install", "app",
             "--device", udid, str(path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Install timed out after {timeout}s. Check device is unlocked and trusted."
        )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Install failed on device {udid}: {detail}")


def ios_launch(udid: str, bundle_id: str) -> None:
    """Launch an app on a real iOS device via devicectl."""
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "process", "launch",
         "--device", udid, bundle_id],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Launch failed: {detail}")


_TUNNELD_DEFAULT_ADDRESS = ("127.0.0.1", 49151)
_PYMOBILEDEVICE3_PYTHON = (
    "/Users/luke/.local/pipx/venvs/pymobiledevice3/bin/python"
)


def _find_rsd_addr(udid: str) -> "tuple[str, int] | None":
    """Query the tunneld HTTP API for the RSD tunnel address+port for *udid*.

    tunneld exposes a JSON HTTP endpoint at http://127.0.0.1:49151 that maps
    UDID → list[{tunnel-address, tunnel-port, interface}].  Returns the first
    matching entry, or None if tunneld is not running or has no tunnel for
    this device.
    """
    host, port = _TUNNELD_DEFAULT_ADDRESS
    url = f"http://{host}:{port}"
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            data: dict = json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None

    # data is keyed by UDID; each value is a list of tunnel detail dicts.
    tunnels = data.get(udid, [])
    if not tunnels:
        return None

    entry = tunnels[0]
    addr = entry.get("tunnel-address")
    port_num = entry.get("tunnel-port")
    if addr and port_num is not None:
        return (addr, int(port_num))
    return None


def ios_screenshot(udid: str, output_path: str, max_size: int | None = None) -> None:
    """Take a screenshot of a real iOS device.

    Strategy (in order):
    1. ``idevicescreenshot`` — fast, works on iOS ≤ 17 / older libimobiledevice.
    2. pymobiledevice3 via tunneld — required for iOS 17+ / iOS 26.x where the
       old lockdownd-based API is deprecated.  Needs ``sudo pymobiledevice3
       remote tunneld`` running in the background.

    If both fail a descriptive RuntimeError is raised with remediation steps.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # --- fast path: idevicescreenshot ---
    if shutil.which("idevicescreenshot"):
        result = subprocess.run(
            ["idevicescreenshot", "-u", udid, output_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and Path(output_path).exists():
            if max_size:
                subprocess.run(
                    ["sips", "-Z", str(max_size), output_path],
                    capture_output=True, check=False,
                )
            return
        # idevicescreenshot failed — fall through to tunneld

    # --- tunneld path: pymobiledevice3 developer screenshot --rsd ---
    rsd = _find_rsd_addr(udid)
    if rsd is not None:
        addr, rsd_port = rsd
        cmd = [
            _PYMOBILEDEVICE3_PYTHON, "-m", "pymobiledevice3",
            "developer", "screenshot",
            "--rsd", addr, str(rsd_port),
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and Path(output_path).exists():
            if max_size:
                subprocess.run(
                    ["sips", "-Z", str(max_size), output_path],
                    capture_output=True, check=False,
                )
            return
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"pymobiledevice3 screenshot failed (RSD {addr}:{rsd_port}): {detail}"
        )

    # --- tunneld not running or no tunnel for this device ---
    raise RuntimeError(
        f"Cannot take screenshot of device {udid}:\n"
        "  • idevicescreenshot failed or is not installed.\n"
        "  • tunneld is not running or has no tunnel for this device.\n"
        "\n"
        "Start tunneld (requires root):\n"
        f"  sudo {_PYMOBILEDEVICE3_PYTHON} -m pymobiledevice3 remote tunneld\n"
        "\n"
        "Then retry.  To install libimobiledevice (legacy fallback):\n"
        "  brew install libimobiledevice"
    )


def ios_get_env(udid: str) -> dict:
    """Return device info for a real iOS device."""
    info: dict = {
        "udid": udid,
        "platform": "ios",
        "device_type": "real",
        "state": "Connected",
    }

    result_devices = _list_xcdevice_devices_json()
    source = "xcdevice"
    if result_devices is None:
        result_devices = _list_devicectl_devices_json()
        source = "devicectl"

    for dev in result_devices or []:
        if dev.get("identifier") != udid:
            continue
        if source == "devicectl":
            hw = dev.get("hardwareProperties", {})
            dp = dev.get("deviceProperties", {})
            info["model"] = hw.get("marketingName", "")
            info["device_name"] = dp.get("name", "")
            info["os_version"] = str(dp.get("osVersionNumber", ""))
        else:
            info["model"] = dev.get("modelName", "")
            info["device_name"] = dev.get("name", "")
            info["os_version"] = str(dev.get("operatingSystemVersion", "")).split(" ", 1)[0]
        break

    return info


def is_real_device_serial(serial: str) -> bool:
    """Check if an adb serial looks like a real device (not emulator-XXXX)."""
    return not serial.startswith("emulator-")
