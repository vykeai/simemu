"""
Simulator and device discovery — lists available iOS simulators, Android AVDs,
and connected real devices, filtering out any already allocated in simemu state.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Optional

from . import state


@dataclass
class SimulatorInfo:
    sim_id: str       # UDID (iOS) or AVD name (Android) or device serial
    platform: str     # "ios" | "android"
    device_name: str
    booted: bool
    runtime: str      # e.g. "iOS 26.2" or "API 35"
    real_device: bool = False   # True for physical devices, False for simulators/emulators
    genymotion: bool = False    # True for Genymotion VMs (preferred over standard AVDs)


def list_ios(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available iOS simulators, excluding already-allocated ones."""
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "--json"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    data = json.loads(out)
    allocated_ids = allocated_ids or set()
    results = []

    for runtime_key, devices in data["devices"].items():
        if "iOS" not in runtime_key:
            continue
        # runtime_key like "com.apple.CoreSimulator.SimRuntime.iOS-26-2"
        runtime_label = runtime_key.split(".")[-1].replace("-", " ")  # "iOS 26 2" → clean up
        parts = runtime_label.split()
        if len(parts) >= 3:
            runtime_label = f"{parts[0]} {parts[1]}.{parts[2]}"

        for dev in devices:
            if not dev.get("isAvailable"):
                continue
            udid = dev["udid"]
            if udid in allocated_ids:
                continue
            results.append(SimulatorInfo(
                sim_id=udid,
                platform="ios",
                device_name=dev["name"],
                booted=dev.get("state") == "Booted",
                runtime=runtime_label,
            ))

    # Prefer booted devices first, then alphabetical by name
    results.sort(key=lambda s: (not s.booted, s.device_name))
    return results


def list_android(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available Android AVDs and Genymotion VMs, excluding allocated ones."""
    from . import genymotion

    allocated_ids = allocated_ids or set()
    booted_avds = _get_booted_avds()
    results = []

    # Standard AVDs
    try:
        out = subprocess.check_output(
            ["emulator", "-list-avds"],
            stderr=subprocess.DEVNULL,
        )
        for line in out.decode().splitlines():
            avd = line.strip()
            if not avd or avd in allocated_ids:
                continue
            runtime = "Android"
            if "API_" in avd:
                api = avd.split("API_")[-1].split("_")[0]
                runtime = f"API {api}"
            results.append(SimulatorInfo(
                sim_id=avd,
                platform="android",
                device_name=avd.replace("_", " "),
                booted=avd in booted_avds,
                runtime=runtime,
            ))
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Genymotion VMs (if Genymotion is installed) — preferred over standard AVDs
    if genymotion.is_available():
        for vm in genymotion.list_vms():
            if vm["uuid"] in allocated_ids:
                continue
            results.append(SimulatorInfo(
                sim_id=vm["uuid"],
                platform="android",
                device_name=vm["name"],
                booted=vm["state"].lower() == "on",
                runtime=genymotion.parse_runtime(vm["name"]),
                genymotion=True,
            ))

    # Sort: booted first, then Genymotion before standard AVDs, then by name
    results.sort(key=lambda s: (not s.booted, not s.genymotion, s.device_name))
    return results


def list_real_ios(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return connected real iOS devices, excluding already-allocated ones."""
    from . import device
    allocated_ids = allocated_ids or set()
    results = []
    for dev in device.list_ios_devices():
        if dev.device_id in allocated_ids:
            continue
        results.append(SimulatorInfo(
            sim_id=dev.device_id,
            platform="ios",
            device_name=f"{dev.device_name} (real)",
            booted=dev.connected,
            runtime=f"iOS {dev.os_version}" if dev.os_version else "iOS",
            real_device=True,
        ))
    return results


def list_real_android(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return connected real Android devices, excluding already-allocated ones."""
    from . import device
    allocated_ids = allocated_ids or set()
    results = []
    for dev in device.list_android_devices():
        if dev.device_id in allocated_ids:
            continue
        results.append(SimulatorInfo(
            sim_id=dev.device_id,
            platform="android",
            device_name=f"{dev.device_name} (real)",
            booted=dev.connected,
            runtime=f"Android {dev.os_version}" if dev.os_version else "Android",
            real_device=True,
        ))
    return results


def _get_booted_avds() -> set[str]:
    """Map running emulator serials back to their AVD names."""
    booted = set()
    try:
        out = subprocess.check_output(
            ["adb", "devices"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return booted

    for line in out.splitlines():
        if not line.startswith("emulator-"):
            continue
        serial = line.split()[0]
        try:
            name_out = subprocess.check_output(
                ["adb", "-s", serial, "emu", "avd", "name"],
                stderr=subprocess.DEVNULL,
            ).decode().splitlines()
            if name_out:
                booted.add(name_out[0].strip())
        except subprocess.CalledProcessError:
            pass

    return booted


def get_android_serial(avd_name: str) -> Optional[str]:
    """Return the adb serial for a running AVD or Genymotion VM.

    For Genymotion VMs (UUID sim_id) returns '<ip>:5555'.
    For standard AVDs returns 'emulator-XXXX'.
    """
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        return genymotion.get_adb_serial(avd_name)

    try:
        out = subprocess.check_output(
            ["adb", "devices"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None

    for line in out.splitlines():
        if not line.startswith("emulator-"):
            continue
        serial = line.split()[0]
        try:
            name_out = subprocess.check_output(
                ["adb", "-s", serial, "emu", "avd", "name"],
                stderr=subprocess.DEVNULL,
            ).decode().splitlines()
            if name_out and name_out[0].strip() == avd_name:
                return serial
        except subprocess.CalledProcessError:
            pass

    return None


class NoSimulatorAvailable(RuntimeError):
    pass


def find_simulator(
    platform: str,
    device_name: Optional[str] = None,
    real_device: bool = False,
) -> SimulatorInfo:
    """
    Find an available (unallocated) simulator or real device, optionally filtered by device_name.
    Raises RuntimeError if none available.

    real_device: if True, search only connected real devices instead of simulators.
    """
    allocated_ids = {a.sim_id for a in state.get_all().values()}

    if real_device:
        if platform == "ios":
            sims = list_real_ios(allocated_ids)
        elif platform == "android":
            sims = list_real_android(allocated_ids)
        else:
            raise RuntimeError(f"Unknown platform '{platform}'. Use 'ios' or 'android'.")
        kind = "real devices"
    else:
        if platform == "ios":
            sims = list_ios(allocated_ids)
        elif platform == "android":
            sims = list_android(allocated_ids)
        else:
            raise RuntimeError(f"Unknown platform '{platform}'. Use 'ios' or 'android'.")
        kind = "simulators"

    if not sims:
        all_allocs = state.get_all()
        held_by = [f"  '{a.slug}' → {a.device_name} (agent: {a.agent})"
                   for a in all_allocs.values() if a.platform == platform]
        hint = "\n".join(held_by) if held_by else "  (none reserved)"

        if real_device:
            raise NoSimulatorAvailable(
                f"No available {platform} {kind} — check USB connection and trust dialog.\n"
                f"Currently reserved:\n{hint}\n\n"
                f"Options:\n"
                f"  simemu list-devices {platform}   # see connected devices\n"
                f"  simemu acquire {platform} <slug> --real --wait 60  # wait for a device"
            )
        raise NoSimulatorAvailable(
            f"No available {platform} {kind} — all are reserved:\n{hint}\n\n"
            f"Options:\n"
            f"  simemu acquire {platform} <slug> --wait 120   # wait up to 2 min\n"
            f"  simemu create {'ios --device \"iPhone 16\" --os \"iOS 18\"' if platform == 'ios' else 'android --api 35'}  # create a new one"
        )

    if device_name:
        matches = [s for s in sims if device_name.lower() in s.device_name.lower()]
        if not matches:
            available = ", ".join(s.device_name for s in sims)
            raise NoSimulatorAvailable(
                f"No available {platform} {kind} matching '{device_name}'.\n"
                f"Available: {available}"
            )
        return matches[0]

    return sims[0]
