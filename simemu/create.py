"""
Simulator/emulator creation.

iOS:  uses `xcrun simctl create` with device type + runtime
Android: uses `avdmanager create avd` with system image + device profile
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class DeviceType:
    identifier: str   # e.g. "com.apple.CoreSimulator.SimDeviceType.iPhone-16-Pro"
    name: str         # e.g. "iPhone 16 Pro"


@dataclass
class Runtime:
    identifier: str   # e.g. "com.apple.CoreSimulator.SimRuntime.iOS-18-0"
    name: str         # e.g. "iOS 18.0"
    platform: str     # "ios"


# ── Apple simulators ─────────────────────────────────────────────────────────

def _apple_platform_name(platform: str) -> str:
    return {
        "ios": "iOS",
        "watchos": "watchOS",
        "tvos": "tvOS",
        "visionos": "xrOS",
    }.get(platform, platform)


def _apple_device_matches_platform(name: str, platform: str) -> bool:
    lower_name = name.lower()
    if platform == "ios":
        return "iphone" in lower_name or "ipad" in lower_name
    if platform == "watchos":
        return "watch" in lower_name
    if platform == "tvos":
        return "tv" in lower_name
    if platform == "visionos":
        return "vision" in lower_name
    return False


def list_apple_device_types(platform: str) -> list[DeviceType]:
    out = subprocess.check_output(
        ["xcrun", "simctl", "list", "devicetypes", "--json"],
        stderr=subprocess.DEVNULL,
    )
    data = json.loads(out)
    return [
        DeviceType(identifier=d["identifier"], name=d["name"])
        for d in data["devicetypes"]
        if _apple_device_matches_platform(d["name"], platform)
    ]


def list_apple_runtimes(platform: str) -> list[Runtime]:
    platform_name = _apple_platform_name(platform)
    out = subprocess.check_output(
        ["xcrun", "simctl", "list", "runtimes", "--json"],
        stderr=subprocess.DEVNULL,
    )
    data = json.loads(out)
    return [
        Runtime(
            identifier=r["identifier"],
            name=r["name"],
            platform=platform,
        )
        for r in data["runtimes"]
        if r.get("isAvailable") and platform_name in r["name"]
    ]


def list_ios_device_types() -> list[DeviceType]:
    return list_apple_device_types("ios")


def list_watchos_device_types() -> list[DeviceType]:
    return list_apple_device_types("watchos")


def list_ios_runtimes() -> list[Runtime]:
    return list_apple_runtimes("ios")


def list_watchos_runtimes() -> list[Runtime]:
    return list_apple_runtimes("watchos")


def create_apple(platform: str, device_name: str, device_type_query: str, runtime_query: str) -> str:
    """
    Create a new Apple simulator. Returns the new UDID.

    Args:
        device_name:       name for the new simulator (e.g. "My iPhone 16")
        device_type_query: partial match for device type (e.g. "iPhone 16 Pro")
        runtime_query:     partial match for runtime (e.g. "iOS 18" or "18.0")
    """
    device_types = list_apple_device_types(platform)
    runtimes = list_apple_runtimes(platform)

    matched_dt = _fuzzy_match(device_type_query, device_types, key=lambda x: x.name)
    if not matched_dt:
        available = ", ".join(d.name for d in device_types)
        raise RuntimeError(
            f"No device type matching '{device_type_query}'.\nAvailable: {available}"
        )

    matched_rt = _fuzzy_match(runtime_query, runtimes, key=lambda x: x.name)
    if not matched_rt:
        available = ", ".join(r.name for r in runtimes)
        raise RuntimeError(
            f"No runtime matching '{runtime_query}'.\nAvailable: {available}"
        )

    result = subprocess.check_output(
        ["xcrun", "simctl", "create", device_name,
         matched_dt.identifier, matched_rt.identifier],
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()

    return result  # returns new UDID


def create_ios(device_name: str, device_type_query: str, runtime_query: str) -> str:
    return create_apple("ios", device_name, device_type_query, runtime_query)


def create_watchos(device_name: str, device_type_query: str, runtime_query: str) -> str:
    return create_apple("watchos", device_name, device_type_query, runtime_query)


# ── Android ───────────────────────────────────────────────────────────────────

@dataclass
class AndroidSystemImage:
    package: str      # e.g. "system-images;android-35;google_apis;x86_64"
    api_level: int
    tag: str          # e.g. "google_apis", "google_apis_playstore"
    abi: str          # e.g. "x86_64"


@dataclass
class AndroidDevice:
    id: str           # e.g. "medium_phone"
    name: str         # e.g. "Medium Phone"


def list_android_system_images() -> list[AndroidSystemImage]:
    """List installed Android system images via sdkmanager."""
    try:
        out = subprocess.check_output(
            ["sdkmanager", "--list_installed"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    images = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("system-images;"):
            continue
        parts = line.split(";")
        if len(parts) >= 4:
            try:
                api = int(parts[1].replace("android-", ""))
            except ValueError:
                continue
            images.append(AndroidSystemImage(
                package=";".join(parts[:4]),
                api_level=api,
                tag=parts[2],
                abi=parts[3].split()[0],
            ))

    return sorted(images, key=lambda x: x.api_level, reverse=True)


def list_android_devices() -> list[AndroidDevice]:
    """List available hardware profiles via avdmanager."""
    try:
        out = subprocess.check_output(
            ["avdmanager", "list", "device", "-c"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    devices = []
    for line in out.splitlines():
        dev_id = line.strip()
        if dev_id:
            devices.append(AndroidDevice(
                id=dev_id,
                name=dev_id.replace("_", " ").title(),
            ))
    return devices


def create_android(
    avd_name: str,
    api_level: int,
    device_query: str = "medium_phone",
    tag: str = "google_apis",
    abi: str = "x86_64",
    force: bool = False,
) -> str:
    """
    Create a new Android AVD. Returns the AVD name.

    Args:
        avd_name:     name for the AVD (becomes the sim_id)
        api_level:    Android API level (e.g. 35)
        device_query: partial match for hardware profile (e.g. "medium_phone", "pixel_6")
        tag:          system image tag (e.g. "google_apis", "google_apis_playstore")
        abi:          CPU architecture (e.g. "x86_64", "arm64-v8a")
        force:        overwrite existing AVD with same name
    """
    package = f"system-images;android-{api_level};{tag};{abi}"

    # Verify the system image is installed
    images = list_android_system_images()
    installed = [i for i in images if i.api_level == api_level and i.tag == tag and i.abi == abi]
    if not installed:
        raise RuntimeError(
            f"System image not installed: {package}\n"
            f"Install it with:  sdkmanager '{package}'"
        )

    # Resolve device profile
    devices = list_android_devices()
    matched = _fuzzy_match(device_query, devices, key=lambda x: x.id)
    if not matched:
        matched = _fuzzy_match(device_query, devices, key=lambda x: x.name)
    if not matched:
        available = ", ".join(d.id for d in devices[:10])
        raise RuntimeError(
            f"No device profile matching '{device_query}'.\nTry: {available}"
        )

    cmd = [
        "avdmanager", "create", "avd",
        "--name", avd_name,
        "--package", package,
        "--device", matched.id,
    ]
    if force:
        cmd.append("--force")

    # avdmanager prompts for custom hardware; pipe 'no' to accept defaults
    proc = subprocess.run(
        cmd,
        input="no\n",
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"avdmanager failed:\n{proc.stderr}")

    return avd_name


# ── helpers ───────────────────────────────────────────────────────────────────

def _fuzzy_match(query: str, items, key):
    query_lower = query.lower()
    exact = [i for i in items if key(i).lower() == query_lower]
    if exact:
        return exact[0]
    partial = [i for i in items if query_lower in key(i).lower()]
    return partial[0] if partial else None
