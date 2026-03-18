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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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


def _has_devicectl() -> bool:
    return shutil.which("devicectl") is not None or shutil.which("xcrun") is not None


def _devicectl(*args, capture: bool = True) -> Optional[str]:
    """Run xcrun devicectl with args."""
    cmd = ["xcrun", "devicectl"] + list(args)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return result.stdout.strip()
    subprocess.run(cmd, check=True)
    return None


def list_ios_devices() -> list[RealDevice]:
    """List connected real iOS devices via devicectl (Xcode 15+).

    Falls back to empty list if devicectl is not available or no devices
    are connected.
    """
    if not _has_devicectl():
        return []

    try:
        out = subprocess.run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", "/dev/stdout"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return []
        data = json.loads(out.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []

    devices = []
    result_devices = data.get("result", {}).get("devices", [])
    for dev in result_devices:
        conn_props = dev.get("connectionProperties", {})
        hw_props = dev.get("hardwareProperties", {})
        device_props = dev.get("deviceProperties", {})

        # Skip simulators (devicectl can list those too)
        if hw_props.get("platform") == "com.apple.platform.appletvsimulator":
            continue
        if dev.get("simulator", False):
            continue

        udid = dev.get("identifier", "")
        if not udid:
            continue

        transport = conn_props.get("transportType", "")
        connection = "wifi" if transport == "wifi" else "usb"

        name = device_props.get("name", hw_props.get("marketingName", "iOS Device"))
        os_version = device_props.get("osVersionNumber", "")

        devices.append(RealDevice(
            device_id=udid,
            platform="ios",
            device_name=name,
            connected=True,
            os_version=str(os_version),
            connection=connection,
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


def ios_screenshot(udid: str, output_path: str, max_size: int | None = None) -> None:
    """Take a screenshot of a real iOS device via devicectl or idevicescreenshot."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Try devicectl first (Xcode 15+)
    result = subprocess.run(
        ["xcrun", "devicectl", "device", "info", "screenshot",
         "--device", udid, "--output", output_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Fall back to idevicescreenshot (libimobiledevice)
        if shutil.which("idevicescreenshot"):
            subprocess.run(
                ["idevicescreenshot", "-u", udid, output_path],
                check=True,
            )
        else:
            raise RuntimeError(
                f"Screenshot failed. devicectl error: {result.stderr.strip()}\n"
                "Install libimobiledevice for fallback: brew install libimobiledevice"
            )

    if max_size:
        subprocess.run(
            ["sips", "-Z", str(max_size), output_path],
            capture_output=True, check=False,
        )


def ios_get_env(udid: str) -> dict:
    """Return device info for a real iOS device."""
    info: dict = {
        "udid": udid,
        "platform": "ios",
        "device_type": "real",
        "state": "Connected",
    }

    try:
        out = subprocess.run(
            ["xcrun", "devicectl", "list", "devices", "--json-output", "/dev/stdout"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            data = json.loads(out.stdout)
            for dev in data.get("result", {}).get("devices", []):
                if dev.get("identifier") == udid:
                    hw = dev.get("hardwareProperties", {})
                    dp = dev.get("deviceProperties", {})
                    info["model"] = hw.get("marketingName", "")
                    info["device_name"] = dp.get("name", "")
                    info["os_version"] = str(dp.get("osVersionNumber", ""))
                    break
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass

    return info


def is_real_device_serial(serial: str) -> bool:
    """Check if an adb serial looks like a real device (not emulator-XXXX)."""
    return not serial.startswith("emulator-")
