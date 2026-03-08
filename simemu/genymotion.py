"""
Genymotion Desktop integration via gmtool CLI.

Genymotion VMs are identified by UUID (e.g. "a1b2c3d4-5678-...") rather than
name strings like standard AVDs. This UUID is used as sim_id throughout simemu.
Genymotion devices connect over TCP: adb serial is "<ip>:5555".

CLI reference (Genymotion 3.x):
  gmtool [--format json] admin list
  gmtool [--format json] admin start <uuid|name>
  gmtool [--format json] admin stop <uuid|name>
  gmtool [--format json] admin delete <uuid|name>
  gmtool [--format json] admin factoryreset <uuid|name>
  gmtool [--format json] admin create <hwprofile> <osimage> <name>  (requires license)
  gmtool [--format json] admin hwprofiles                            (requires license)
  gmtool [--format json] admin osimages                             (requires license)
"""

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

_GMTOOL_CANDIDATES = [
    "/Applications/Genymotion.app/Contents/MacOS/gmtool",
]

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def is_genymotion_id(sim_id: str) -> bool:
    """True if sim_id is a Genymotion VM UUID (vs an AVD name string)."""
    return bool(_UUID_RE.match(str(sim_id)))


def gmtool_path() -> Optional[str]:
    """Find gmtool on PATH or at the standard Genymotion install location."""
    if found := shutil.which("gmtool"):
        return found
    for candidate in _GMTOOL_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def is_available() -> bool:
    """Return True if Genymotion Desktop (gmtool) is installed."""
    return gmtool_path() is not None


def _run(*args, check: bool = True, as_json: bool = False) -> str | dict:
    path = gmtool_path()
    if not path:
        raise RuntimeError(
            "gmtool not found. Install Genymotion Desktop from genymotion.com\n"
            "  Expected: /Applications/Genymotion.app/Contents/MacOS/gmtool\n"
            "  Or add gmtool to your PATH."
        )
    cmd = [path]
    if as_json:
        cmd += ["--format", "json"]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        msg = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"gmtool error: {msg}")
    if as_json:
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}
    return result.stdout.strip()


def list_vms() -> list[dict]:
    """List Genymotion VMs. Returns dicts with: uuid, name, state, ip."""
    try:
        data = _run("admin", "list", check=False, as_json=True)
    except RuntimeError:
        return []
    instances = data.get("instances", []) if isinstance(data, dict) else []
    result = []
    for inst in instances:
        uuid = inst.get("uuid", "")
        if not _UUID_RE.match(uuid):
            continue
        result.append({
            "uuid": uuid,
            "name": inst.get("name", ""),
            "state": inst.get("state", ""),
            "ip": inst.get("ip", ""),
        })
    return result


def list_hwprofiles() -> list[dict]:
    """List available hardware profiles. Requires Genymotion license."""
    try:
        data = _run("admin", "hwprofiles", as_json=True)
    except RuntimeError as e:
        if "license" in str(e).lower():
            raise RuntimeError(
                "Listing hardware profiles requires a Genymotion license.\n"
                "Create VMs in the Genymotion UI, then use 'simemu list android' to find them."
            ) from None
        raise
    return data.get("hw_profiles", []) if isinstance(data, dict) else []


def list_osimages() -> list[dict]:
    """List available OS images. Requires Genymotion license."""
    try:
        data = _run("admin", "osimages", as_json=True)
    except RuntimeError as e:
        if "license" in str(e).lower():
            raise RuntimeError(
                "Listing OS images requires a Genymotion license.\n"
                "Create VMs in the Genymotion UI, then use 'simemu list android' to find them."
            ) from None
        raise
    return data.get("os_images", []) if isinstance(data, dict) else []


def _ensure_adb_connected(serial: str) -> None:
    """Connect adb to a TCP serial (e.g. '192.168.56.101:5555') if not already connected.

    Genymotion VMs connect over TCP. If the VM was started outside simemu (via the
    Genymotion UI), it won't be in 'adb devices' until adb connect is called.
    """
    try:
        out = subprocess.check_output(
            ["adb", "devices"], stderr=subprocess.DEVNULL
        ).decode()
        if serial in out:
            return
        subprocess.run(["adb", "connect", serial], capture_output=True, check=False)
    except FileNotFoundError:
        pass


def get_adb_serial(vm_uuid: str) -> Optional[str]:
    """Return the adb serial (e.g. '192.168.56.101:5555') for a running Genymotion VM.

    Auto-connects adb if the VM is on but not yet in 'adb devices' (e.g. started via UI).
    """
    for vm in list_vms():
        if vm["uuid"] == vm_uuid:
            ip = vm.get("ip", "")
            state = vm.get("state", "").lower()
            if ip and state == "on":
                serial = f"{ip}:5555"
                _ensure_adb_connected(serial)
                return serial
    return None


def boot(vm_uuid: str) -> None:
    """Start a Genymotion VM and wait until it's adb-accessible and fully booted."""
    _run("admin", "start", vm_uuid, check=False)
    print("Waiting for Genymotion VM to boot...", flush=True)
    deadline = time.time() + 180
    while time.time() < deadline:
        serial = get_adb_serial(vm_uuid)
        if serial:
            subprocess.run(["adb", "connect", serial], capture_output=True)
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
                capture_output=True, text=True,
            )
            if result.stdout.strip() == "1":
                return
        time.sleep(3)
    raise RuntimeError(f"Genymotion VM {vm_uuid} did not become ready within 180s")


def shutdown(vm_uuid: str) -> None:
    """Stop a Genymotion VM."""
    _run("admin", "stop", vm_uuid, check=False)


def erase(vm_uuid: str) -> None:
    """Factory reset a Genymotion VM (must be stopped first)."""
    _run("admin", "factoryreset", vm_uuid)


def create(hwprofile: str, osimage: str, vm_name: str) -> str:
    """Create a new Genymotion VM. Returns the new VM's UUID. Requires license."""
    try:
        data = _run("admin", "create", hwprofile, osimage, vm_name, as_json=True)
    except RuntimeError as e:
        if "license" in str(e).lower():
            raise RuntimeError(
                "Creating VMs via CLI requires a Genymotion license.\n"
                "Create the VM in the Genymotion UI instead, then use:\n"
                "  simemu acquire android <slug> --device \"<vm-name>\""
            ) from None
        raise
    uuid = data.get("uuid", "") if isinstance(data, dict) else ""
    if _UUID_RE.match(uuid):
        return uuid
    # Fallback: look it up by name
    for vm in list_vms():
        if vm["name"] == vm_name:
            return vm["uuid"]
    raise RuntimeError(
        f"Genymotion VM '{vm_name}' created but UUID not found.\n"
        f"Run 'simemu list android' to find it."
    )


def delete(vm_uuid: str) -> None:
    """Permanently remove a Genymotion VM."""
    _run("admin", "delete", vm_uuid)


def parse_runtime(vm_name: str) -> str:
    """Extract a runtime label from a Genymotion VM name.

    e.g. 'Samsung Galaxy S24 - Android 14.0 - API 34' → 'Android 14'
    """
    m = re.search(r"Android\s+(\d+(?:\.\d+)?)", vm_name, re.IGNORECASE)
    if m:
        return f"Android {m.group(1).split('.')[0]}"
    m = re.search(r"API\s+(\d+)", vm_name, re.IGNORECASE)
    if m:
        return f"API {m.group(1)}"
    return "Android"
