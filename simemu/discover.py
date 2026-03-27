"""
Simulator and device discovery — lists available iOS simulators, Android AVDs,
and connected real devices, filtering out any already claimed in simemu sessions.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class SimulatorInfo:
    sim_id: str       # UDID (iOS) or AVD name (Android) or device serial
    platform: str     # "ios" | "android"
    device_name: str
    booted: bool
    runtime: str      # e.g. "iOS 26.2" or "API 35"
    real_device: bool = False   # True for physical devices, False for simulators/emulators
    genymotion: bool = False    # True for Genymotion VMs (preferred over standard AVDs)


def _list_apple_simulators(
    platform_filter: str,
    allocated_ids: set[str] | None = None,
) -> list[SimulatorInfo]:
    """Return available Apple simulators for a given platform (iOS, watchOS, tvOS, visionOS)."""
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
        if platform_filter not in runtime_key:
            continue
        # runtime_key like "com.apple.CoreSimulator.SimRuntime.iOS-26-2"
        runtime_label = runtime_key.split(".")[-1].replace("-", " ")
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
                platform={"iOS": "ios", "watchOS": "watchos", "tvOS": "tvos", "xrOS": "visionos"}.get(platform_filter, platform_filter.lower()),
                device_name=dev["name"],
                booted=dev.get("state") == "Booted",
                runtime=runtime_label,
            ))

    results.sort(key=lambda s: (not s.booted, s.device_name))
    return results


def list_ios(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available iOS simulators, excluding already-allocated ones."""
    return _list_apple_simulators("iOS", allocated_ids)


def list_watchos(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available watchOS simulators, excluding already-allocated ones."""
    return _list_apple_simulators("watchOS", allocated_ids)


def list_tvos(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available tvOS simulators, excluding already-allocated ones."""
    return _list_apple_simulators("tvOS", allocated_ids)


def list_visionos(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available visionOS simulators, excluding already-allocated ones."""
    return _list_apple_simulators("xrOS", allocated_ids)


def list_android(allocated_ids: set[str] | None = None) -> list[SimulatorInfo]:
    """Return all available Android AVDs, excluding allocated ones."""
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

    # Genymotion support disabled — VMs are unreliable (adbd crashes, offline loops)
    # Standard AVDs on Apple Silicon are more stable and lighter.

    results.sort(key=lambda s: (not s.booted, s.device_name))
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


def get_android_serial(avd_name: str, retries: int = 1, delay: float = 0.5) -> Optional[str]:
    """Return the adb serial for a running AVD.

    For standard AVDs returns 'emulator-XXXX'.
    Genymotion support is disabled — VMs are unreliable.
    """
    attempts = max(1, retries)
    for attempt in range(attempts):
        try:
            out = subprocess.check_output(
                ["adb", "devices"],
                stderr=subprocess.DEVNULL,
            ).decode()
        except (subprocess.CalledProcessError, FileNotFoundError):
            out = ""

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

        if attempt < attempts - 1:
            import time as _time
            _time.sleep(delay)

    return None


class NoSimulatorAvailable(RuntimeError):
    pass


def _get_claimed_sim_ids() -> set[str]:
    """Return sim_ids of all active sessions (claimed devices)."""
    from .session import get_active_sessions
    return {s.sim_id for s in get_active_sessions().values()}


def _classify_form_factor(sim: SimulatorInfo) -> str | None:
    """Infer a coarse form factor from a discovered device name."""
    name = sim.device_name.lower()

    if any(hint in name for hint in ("apple tv", "appletv")):
        return "tv"
    if any(hint in name for hint in ("apple watch", "watch")):
        return "watch"
    if any(hint in name for hint in ("apple vision", "vision pro")):
        return "vision"
    if any(hint in name for hint in ("ipad", "tablet")):
        return "tablet"
    if any(hint in name for hint in ("iphone", "pixel", "galaxy", "phone", "nexus")):
        return "phone"
    return None


def get_reservation(agent: str, platform: str, form_factor: str = "phone") -> dict | None:
    """Check if an agent has a reserved device for a platform + form factor.

    Supports two config formats in ~/.simemu/config.json:

    Simple (legacy):
    {"reservations": {"sitches": {"ios": {"device": "iPhone 17 Pro Max"}}}}

    Pool (new):
    {"reservation_pools": {
      "sitches": {
        "ios-phone": ["iPhone 17 Pro Max", "iPhone 17 Pro"],
        "android-phone": ["Pixel 9 Pro"]
      }
    }}

    Returns {"device": "name"} or {"devices": ["name1", "name2"]} or None.
    """
    from . import state as _state
    config_path = _state.config_dir() / "config.json"
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    # Check pool format first (more specific)
    pools = config.get("reservation_pools", {})
    agent_pool = pools.get(agent, {})
    pool_key = f"{platform}-{form_factor}"
    if pool_key in agent_pool:
        devices = agent_pool[pool_key]
        if isinstance(devices, list) and devices:
            return {"devices": devices}

    # Fall back to simple format
    reservations = config.get("reservations", {})
    agent_res = reservations.get(agent, {})
    return agent_res.get(platform)


def find_best_device(spec: "ClaimSpec") -> SimulatorInfo:
    """Find the best available device matching a ClaimSpec.

    Scoring: booted > shutdown, exact version match > close, less memory > more.
    Maps form_factor to platform and device name filters.
    Respects permanent reservations: if the agent has a reserved device, prefer it.
    """
    from .session import ClaimSpec  # deferred to avoid circular import

    allocated_ids = _get_claimed_sim_ids()

    # Map form_factor to platform and device filter
    _FORM_FACTOR_PLATFORM = {
        "watch": "watchos",
        "tv": "tvos",
        "vision": "visionos",
    }

    platform = _FORM_FACTOR_PLATFORM.get(spec.form_factor, spec.platform)

    if spec.real_device:
        if platform == "ios":
            candidates = list_real_ios(allocated_ids)
        elif platform == "android":
            candidates = list_real_android(allocated_ids)
        else:
            raise NoSimulatorAvailable(
                f"Real device discovery not supported for platform '{platform}'."
            )
        kind = "real devices"
    else:
        _LIST_FNS = {
            "ios": list_ios,
            "watchos": list_watchos,
            "tvos": list_tvos,
            "visionos": list_visionos,
            "android": list_android,
        }
        list_fn = _LIST_FNS.get(platform)
        if not list_fn:
            raise NoSimulatorAvailable(f"Unknown platform '{platform}'.")
        candidates = list_fn(allocated_ids)
        kind = "simulators"

    if not candidates:
        raise NoSimulatorAvailable(
            f"No available {platform} {kind}. "
            f"Re-try later or create a new one."
        )

    if spec.form_factor in {"phone", "tablet"}:
        filtered = [
            sim for sim in candidates
            if _classify_form_factor(sim) == spec.form_factor
        ]
        if not filtered:
            available = ", ".join(sim.device_name for sim in candidates)
            from .session import get_active_sessions
            active = get_active_sessions()
            held_by = [
                f"{sid} → {s.device_name} (agent: {s.agent})"
                for sid, s in active.items()
                if s.platform == platform
            ]
            ownership_hint = ""
            if held_by:
                ownership_hint = f". Currently claimed: {'; '.join(held_by)}"
            raise NoSimulatorAvailable(
                f"No available {platform} {kind} matching form factor "
                f"'{spec.form_factor}'. Available unclaimed devices: {available}{ownership_hint}"
            )
        candidates = filtered

    # Check for permanent reservation (simple or pool)
    agent = os.environ.get("SIMEMU_AGENT", "")
    reservation = get_reservation(agent, platform, spec.form_factor) if agent else None
    reserved_device_names: list[str] = []
    if reservation:
        if "devices" in reservation:
            reserved_device_names = reservation["devices"]
        elif "device" in reservation:
            reserved_device_names = [reservation["device"]]

    # Score candidates
    def _score(sim: SimulatorInfo) -> tuple:
        """Lower score = better match. Returns tuple for sorting."""
        # Permanent reservation match is highest priority
        reserved_score = 1
        for rname in reserved_device_names:
            if rname in sim.device_name:
                reserved_score = 0
                break

        # Prefer booted devices (saves boot time)
        booted_score = 0 if sim.booted else 1

        # Prefer version match
        version_score = 0
        if spec.os_version:
            runtime_lower = sim.runtime.lower()
            if spec.os_version in runtime_lower:
                version_score = 0
            else:
                version_score = 1

        # Prefer non-Genymotion (lighter on Apple Silicon)
        geny_score = 1 if sim.genymotion else 0

        return (reserved_score, version_score, booted_score, geny_score, sim.device_name)

    candidates.sort(key=_score)
    return candidates[0]


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
    allocated_ids = _get_claimed_sim_ids()

    _APPLE_PLATFORMS = {"ios", "watchos", "tvos", "visionos"}
    _LIST_FNS = {
        "ios": list_ios,
        "watchos": list_watchos,
        "tvos": list_tvos,
        "visionos": list_visionos,
        "android": list_android,
    }
    _VALID = ", ".join(sorted(_LIST_FNS))

    if real_device:
        if platform == "ios":
            sims = list_real_ios(allocated_ids)
        elif platform == "android":
            sims = list_real_android(allocated_ids)
        else:
            raise RuntimeError(f"Real device discovery not supported for '{platform}'.")
        kind = "real devices"
    else:
        list_fn = _LIST_FNS.get(platform)
        if not list_fn:
            raise RuntimeError(f"Unknown platform '{platform}'. Use: {_VALID}")
        sims = list_fn(allocated_ids)
        kind = "simulators"

    if not sims:
        from .session import get_active_sessions
        active = get_active_sessions()
        held_by = [f"  {sid} → {s.device_name} (agent: {s.agent})"
                   for sid, s in active.items() if s.platform == platform]
        hint = "\n".join(held_by) if held_by else "  (none claimed)"

        if real_device:
            raise NoSimulatorAvailable(
                f"No available {platform} {kind} — check USB connection and trust dialog.\n"
                f"Currently claimed:\n{hint}"
            )
        raise NoSimulatorAvailable(
            f"No available {platform} {kind} — all are claimed:\n{hint}\n\n"
            f"Options:\n"
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
