"""
Simulator discovery — lists available iOS simulators and Android AVDs,
filtering out any already allocated in simemu state.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Optional

from . import state


@dataclass
class SimulatorInfo:
    sim_id: str       # UDID (iOS) or AVD name (Android)
    platform: str     # "ios" | "android"
    device_name: str
    booted: bool
    runtime: str      # e.g. "iOS 26.2" or "API 35"


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

    # Genymotion VMs (if Genymotion is installed)
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
            ))

    results.sort(key=lambda s: (not s.booted, s.device_name))
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


def find_simulator(platform: str, device_name: Optional[str] = None) -> SimulatorInfo:
    """
    Find an available (unallocated) simulator, optionally filtered by device_name.
    Raises RuntimeError if none available.
    """
    allocated_ids = {a.sim_id for a in state.get_all().values()}

    if platform == "ios":
        sims = list_ios(allocated_ids)
    elif platform == "android":
        sims = list_android(allocated_ids)
    else:
        raise RuntimeError(f"Unknown platform '{platform}'. Use 'ios' or 'android'.")

    if not sims:
        all_allocs = state.get_all()
        held_by = [f"  '{a.slug}' → {a.device_name} (agent: {a.agent})"
                   for a in all_allocs.values() if a.platform == platform]
        hint = "\n".join(held_by) if held_by else "  (none reserved)"
        raise NoSimulatorAvailable(
            f"No available {platform} simulators — all are reserved:\n{hint}\n\n"
            f"Options:\n"
            f"  simemu acquire {platform} <slug> --wait 120   # wait up to 2 min\n"
            f"  simemu create {'ios --device \"iPhone 16\" --os \"iOS 18\"' if platform == 'ios' else 'android --api 35'}  # create a new one"
        )

    if device_name:
        matches = [s for s in sims if device_name.lower() in s.device_name.lower()]
        if not matches:
            available = ", ".join(s.device_name for s in sims)
            raise NoSimulatorAvailable(
                f"No available {platform} simulator matching '{device_name}'.\n"
                f"Available: {available}"
            )
        return matches[0]

    return sims[0]
