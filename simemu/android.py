"""
Android emulator operations via adb and emulator CLI.
Functions take an AVD name (sim_id) and resolve the adb serial as needed.
"""

import os
import re
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .discover import get_android_serial
from . import device as real_device

# Android screenrecord hard cap (3 minutes); warn agents approaching this
SCREENRECORD_MAX_SECONDS = 180


def _window_info(avd_name: str) -> Optional[dict]:
    try:
        import importlib as _il
        Quartz = _il.import_module("Quartz")
        windows = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll,
            Quartz.kCGNullWindowID,
        )
    except Exception:
        return None

    avd_name_lower = avd_name.lower()
    candidates = []
    for window in windows:
        owner = str(window.get("kCGWindowOwnerName") or "")
        name = str(window.get("kCGWindowName") or "")
        owner_lower = owner.lower()
        name_lower = name.lower()
        if avd_name_lower not in name_lower and avd_name_lower not in owner_lower:
            if "android emulator" not in owner_lower and "qemu-system" not in owner_lower and "emulator" not in name_lower:
                continue
        bounds = window.get("kCGWindowBounds") or {}
        width = float(bounds.get("Width", 0))
        height = float(bounds.get("Height", 0))
        if width <= 0 or height <= 0:
            continue
        candidates.append(
            {
                "owner": owner,
                "name": name,
                "bounds": {
                    "x": float(bounds.get("X", 0)),
                    "y": float(bounds.get("Y", 0)),
                    "width": width,
                    "height": height,
                },
                "onscreen": bool(window.get("kCGWindowIsOnscreen", 0)),
                "layer": int(window.get("kCGWindowLayer", 0)),
            }
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (not item["onscreen"], item["layer"], -(item["bounds"]["width"] * item["bounds"]["height"])))
    return candidates[0]


def current_window_frame(avd_name: str) -> Optional[dict]:
    info = _window_info(avd_name)
    if not info:
        return None
    return info["bounds"]


def set_window_frame(avd_name: str, x: float, y: float, width: float, height: float) -> bool:
    info = _window_info(avd_name)
    if not info:
        return False
    owner = info["owner"].replace('"', '\\"')
    name = info["name"].replace('"', '\\"')
    if name:
        window_ref = f'(first window whose name contains "{name}")'
    else:
        window_ref = "window 1"
    script = f'''tell application "System Events"
    tell process "{owner}"
        set w to {window_ref}
        set position of w to {{{int(x)}, {int(y)}}}
        set size of w to {{{int(width)}, {int(height)}}}
        perform action "AXRaise" of w
    end tell
end tell'''
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)
    return True


def _sidecar_dir() -> Path:
    """
    Use a per-user writable temp directory for recording metadata.
    Shared `/tmp/simemu` can be owned by another user on multi-agent Macs.
    """
    return Path(tempfile.gettempdir()) / f"simemu-{os.getuid()}"


def _resolve_serial(avd_name: str, retries: int = 6, delay: float = 0.5) -> Optional[str]:
    serial = get_android_serial(avd_name, retries=retries, delay=delay)
    if serial is not None:
        return serial
    connected_real_ids = {device.device_id for device in real_device.list_android_devices()}
    if avd_name in connected_real_ids:
        return avd_name
    return None


def _serial(avd_name: str) -> str:
    serial = _resolve_serial(avd_name, retries=6, delay=0.5)
    if serial is None:
        raise RuntimeError(
            f"Android device '{avd_name}' is not connected or adb-ready. "
            f"Re-claim the device or reconnect it, then retry."
        )
    return serial


def get_serial(avd_name: str) -> str:
    """Compatibility helper used by the newer session-based simemu flow."""
    return _serial(avd_name)


def _ensure_booted(avd_name: str) -> None:
    """Check emulator/device is adb-reachable. Raises instead of auto-booting runaway spawns."""
    from . import state
    state.check_maintenance()
    if _resolve_serial(avd_name, retries=6, delay=0.5) is None:
        raise RuntimeError(
            f"Android device '{avd_name}' is not connected or adb-ready.\n"
            f"Re-claim the device or reconnect it, then retry."
        )


def wait_until_ready(avd_name: str, timeout: int = 180) -> str:
    """
    Wait until adb is online and the package manager responds.

    Genymotion-backed devices can briefly report a serial while still being
    offline for installs and package queries. "ready" should mean adb commands
    will actually work, not just that the VM exists.
    """
    _ensure_booted(avd_name)
    deadline = time.time() + timeout
    last_detail = "device did not become adb-ready"
    while time.time() < deadline:
        try:
            serial = _serial(avd_name)
        except RuntimeError as exc:
            last_detail = str(exc)
            time.sleep(2)
            continue

        try:
            wait_result = subprocess.run(
                ["adb", "-s", serial, "wait-for-device"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_detail = "adb wait-for-device timed out"
            time.sleep(2)
            continue
        if wait_result.returncode != 0:
            last_detail = wait_result.stderr.strip() or wait_result.stdout.strip() or "adb wait-for-device failed"
            time.sleep(2)
            continue

        try:
            state_result = subprocess.run(
                ["adb", "-s", serial, "get-state"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_detail = "adb get-state timed out"
            time.sleep(2)
            continue
        if state_result.stdout.strip() != "device":
            last_detail = state_result.stderr.strip() or state_result.stdout.strip() or "adb device state unavailable"
            time.sleep(2)
            continue

        try:
            boot_result = subprocess.run(
                ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_detail = "adb getprop sys.boot_completed timed out"
            time.sleep(2)
            continue
        if boot_result.stdout.strip() != "1":
            last_detail = boot_result.stderr.strip() or boot_result.stdout.strip() or "Android system not boot-complete"
            time.sleep(2)
            continue

        try:
            pm_result = subprocess.run(
                ["adb", "-s", serial, "shell", "pm", "path", "android"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except subprocess.TimeoutExpired:
            last_detail = "adb shell pm path android timed out"
            time.sleep(2)
            continue
        if pm_result.returncode == 0 and "package:" in pm_result.stdout:
            return serial

        last_detail = pm_result.stderr.strip() or pm_result.stdout.strip() or "package manager not ready"
        time.sleep(2)

    raise RuntimeError(
        f"Android emulator '{avd_name}' reported as booted but never became adb-ready within {timeout}s: {last_detail}"
    )


def _adb(avd_name: str, *args, capture: bool = False, check: bool = True) -> Optional[str]:
    serial = _serial(avd_name)
    cmd = ["adb", "-s", serial] + list(args)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    else:
        subprocess.run(cmd, check=check)
        return None


def foreground_app(avd_name: str) -> Optional[str]:
    """Return the currently resumed Android package, if detectable."""
    serial = wait_until_ready(avd_name)
    result = subprocess.run(
        ["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    for line in result.stdout.splitlines():
        if "mResumedActivity" not in line and "ResumedActivity" not in line and "topResumedActivity" not in line:
            continue
        for part in line.split():
            if "/" in part and "." in part:
                return part.split("/")[0]
    return None


def _wait_for_foreground_package(
    avd_name: str,
    package: str,
    timeout: float = 5.0,
    delay: float = 0.25,
) -> None:
    """Wait briefly until the expected package is resumed."""
    deadline = time.time() + timeout
    last_foreground = None
    while time.time() < deadline:
        last_foreground = foreground_app(avd_name)
        if last_foreground == package:
            return
        time.sleep(delay)
    raise RuntimeError(
        f"Android command did not foreground '{package}'. "
        f"Foreground app was {last_foreground or 'unknown'} instead."
    )


def _apk_application_id(apk_path: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ["apkanalyzer", "manifest", "application-id", apk_path],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    package_name = result.stdout.strip()
    return package_name or None


@dataclass
class PackageVerification:
    package: str
    pm_path: str
    resolve_activity: str
    dumpsys: str
    pm_path_ok: bool
    resolve_activity_ok: bool
    dumpsys_ok: bool

    @property
    def ok(self) -> bool:
        return self.pm_path_ok and self.resolve_activity_ok and self.dumpsys_ok

    def format_report(self) -> str:
        sections = [
            f"pm path:\n{self.pm_path or '(no output)'}",
            f"resolve-activity --brief:\n{self.resolve_activity or '(no output)'}",
            f"dumpsys package:\n{self.dumpsys or '(no output)'}",
        ]
        return "\n\n".join(sections)


def verify_install(avd_name: str, package: str, timeout: int = 30) -> PackageVerification:
    """Verify Android package-manager state is coherent after install."""
    serial = wait_until_ready(avd_name)
    deadline = time.time() + timeout
    last_probe: PackageVerification | None = None

    while time.time() < deadline:
        last_probe = _probe_package_state(serial, package)
        if last_probe.ok:
            return last_probe
        time.sleep(1)

    if last_probe is None:
        last_probe = _probe_package_state(serial, package)
    if last_probe.ok:
        return last_probe
    raise RuntimeError(_format_install_verification_error(last_probe))


def repair_install(avd_name: str, package: str, apk_path: str, timeout: int = 120) -> PackageVerification:
    """Attempt escalating recovery for a package that installed into a bad PM state."""
    timeout = max(timeout, 120)
    serial = wait_until_ready(avd_name, timeout=max(timeout, 180))
    subprocess.run(["adb", "-s", serial, "uninstall", package], capture_output=True, text=True, check=False)

    recovery_errors: list[str] = []
    recovery_steps = [
        ("reboot", _repair_reboot_cycle),
        ("cold-boot", _repair_cold_boot_cycle),
        ("wipe-data", _repair_wipe_data_cycle),
    ]

    for label, action in recovery_steps:
        try:
            action(avd_name)
            install(avd_name, apk_path, timeout=timeout, repair_on_failure=False)
            probe = verify_install(avd_name, package)
            # Double-check after a brief delay — PM state can drift on slow emulators
            time.sleep(3)
            probe2 = verify_install(avd_name, package, timeout=10)
            return probe2
        except RuntimeError as exc:
            recovery_errors.append(f"{label}: {exc}")

    joined = "\n".join(recovery_errors) or "unknown repair failure"
    raise RuntimeError(
        f"repair-install could not recover coherent package-manager state for '{package}'.\n"
        f"Recovery attempts:\n{joined}"
    )


def _repair_reboot_cycle(avd_name: str) -> None:
    reboot(avd_name)


def _repair_cold_boot_cycle(avd_name: str) -> None:
    shutdown(avd_name)
    time.sleep(3)
    boot(avd_name, headless=True)
    wait_until_ready(avd_name, timeout=180)


def _repair_wipe_data_cycle(avd_name: str) -> None:
    erase(avd_name)
    time.sleep(5)
    boot(avd_name, headless=True)
    wait_until_ready(avd_name, timeout=240)


def _probe_package_state(serial: str, package: str) -> PackageVerification:
    pm_path = subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "path", package],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    resolved_launcher = subprocess.run(
        [
            "adb", "-s", serial, "shell", "cmd", "package", "resolve-activity",
            "--brief", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package,
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    dumpsys = subprocess.run(
        ["adb", "-s", serial, "shell", "dumpsys", "package", package],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    pm_path_text = pm_path.stdout.strip() or pm_path.stderr.strip()
    resolved_text = resolved_launcher.stdout.strip() or resolved_launcher.stderr.strip()
    dumpsys_text = dumpsys.stdout.strip() or dumpsys.stderr.strip()
    dumpsys_lines = "\n".join(dumpsys_text.splitlines()[:200])

    return PackageVerification(
        package=package,
        pm_path=pm_path_text,
        resolve_activity=resolved_text,
        dumpsys=dumpsys_lines,
        pm_path_ok=pm_path.returncode == 0 and "package:" in pm_path_text,
        resolve_activity_ok="/" in resolved_text and "No activity found" not in resolved_text,
        dumpsys_ok=_dumpsys_has_real_package(dumpsys_lines, package),
    )


def _dumpsys_has_real_package(text: str, package: str) -> bool:
    if not text or "pkg=null" in text:
        return False
    return (
        f"Package [{package}]" in text or
        f"pkg=Package{{" in text and package in text or
        f"PackageSetting{{" in text and package in text
    )


def _format_install_verification_error(probe: PackageVerification) -> str:
    return (
        f"Android install verification failed for '{probe.package}': emulator package-manager state is inconsistent.\n"
        f"{probe.format_report()}\n\n"
        "Try: simemu do <session> repair-install <package> <apk-path>"
    )


def boot(avd_name: str, headless: bool = False) -> None:
    """Start the AVD if not already running. Waits until fully booted."""
    from . import state, genymotion
    state.check_maintenance()
    if genymotion.is_genymotion_id(avd_name):
        if get_android_serial(avd_name) is None:
            genymotion.boot(avd_name)
        return

    if get_android_serial(avd_name) is not None:
        return

    # Memory cap to prevent runaway qemu processes
    memory_mb = int(os.environ.get("SIMEMU_ANDROID_MEMORY_MB", "2048"))

    cmd = ["emulator", "-avd", avd_name, "-memory", str(memory_mb)]
    if headless:
        cmd += ["-no-window", "-no-audio", "-no-boot-anim", "-gpu", "swiftshader_indirect"]

    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print(f"Waiting for '{avd_name}' to boot...", flush=True)
    deadline = time.time() + 300
    serial = None
    while time.time() < deadline:
        serial = get_android_serial(avd_name)
        if serial:
            break
        time.sleep(2)

    if not serial:
        raise RuntimeError(f"Emulator '{avd_name}' did not appear within 300s")

    deadline = time.time() + 180
    while time.time() < deadline:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "1":
            return
        time.sleep(2)

    raise RuntimeError(f"Emulator '{avd_name}' booted but system never became ready")


def shutdown(avd_name: str) -> None:
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        genymotion.shutdown(avd_name)
        return
    _adb(avd_name, "emu", "kill", check=False)


def install(avd_name: str, apk_path: str, timeout: int = 120, repair_on_failure: bool = True) -> None:
    serial = wait_until_ready(avd_name, timeout=max(timeout, 180))
    path = Path(apk_path)
    if not path.exists():
        raise RuntimeError(f"APK not found: {apk_path}")
    if path.suffix != ".apk":
        raise RuntimeError(f"Android requires a .apk file, got: {path.suffix}")
    def _run_install(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Install timed out after {timeout}s. The emulator may be unresponsive. "
                f"Try: simemu do <session> reboot"
            )

    cmd = ["adb", "-s", serial, "install", "-r", str(path)]
    result = _run_install(cmd)
    install_failed = result.returncode != 0 or "Failure" in result.stdout
    install_output = result.stdout or ""
    if install_failed and (
        "Performing Streamed Install" in install_output or
        "Performing Push Install" in install_output
    ):
        result = _run_install(
            ["adb", "-s", serial, "install", "--no-streaming", "-r", str(path)]
        )
        install_failed = result.returncode != 0 or "Failure" in result.stdout

    if install_failed:
        detail = result.stdout.strip() or result.stderr.strip()
        raise RuntimeError(f"Install failed: {detail}")

    package_name = _apk_application_id(str(path))
    if not package_name:
        return

    try:
        verify_install(avd_name, package_name)
        return
    except RuntimeError:
        if not repair_on_failure:
            raise
        repair_install(avd_name, package_name, str(path), timeout=timeout)


def list_apps(avd_name: str) -> list[dict]:
    wait_until_ready(avd_name)
    """Return installed packages as a list of dicts with package name."""
    output = _adb(avd_name, "shell", "pm", "list", "packages", "-f", capture=True) or ""
    apps = []
    for line in output.splitlines():
        # format: package:/path/to/base.apk=com.example.app
        if "=" not in line:
            continue
        path_part, pkg = line.split("=", 1)
        apk_path = path_part.replace("package:", "")
        apps.append({"package": pkg.strip(), "path": apk_path.strip()})
    return sorted(apps, key=lambda x: x["package"])


def stop_other_apps(avd_name: str, keep: str | list[str] | None = None) -> list[str]:
    """Force-stop all third-party packages except those in keep.

    Used to isolate the device for proof capture — prevents other agents'
    apps from intercepting deep links or appearing in foreground.
    Returns the list of packages that were stopped.
    """
    serial = wait_until_ready(avd_name)
    keep_set = set()
    if isinstance(keep, str):
        keep_set.add(keep)
    elif keep:
        keep_set.update(keep)

    # List third-party packages
    result = subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "list", "packages", "-3"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    stopped = []
    for line in result.stdout.splitlines():
        pkg = line.replace("package:", "").strip()
        if not pkg or pkg in keep_set:
            continue
        subprocess.run(
            ["adb", "-s", serial, "shell", "am", "force-stop", pkg],
            capture_output=True, check=False, timeout=5,
        )
        stopped.append(pkg)
    return stopped


def launch(avd_name: str, package_activity: str, args: list[str] | None = None) -> None:
    wait_until_ready(avd_name)
    """
    Launch an app. package_activity can be:
      - "com.example.app"           → resolves main launcher activity
      - "com.example.app/.MainActivity"  → explicit activity
    """
    expected_package = package_activity.split("/", 1)[0]
    if "/" not in package_activity:
        try:
            verify_install(avd_name, expected_package, timeout=15)
        except RuntimeError:
            pass

        # Strategy 1: monkey launch — most reliable for standard launcher activities
        try:
            _adb(
                avd_name,
                "shell",
                "monkey",
                "-p", expected_package,
                "-c", "android.intent.category.LAUNCHER",
                "1",
            )
            _wait_for_foreground_package(avd_name, expected_package)
            return
        except (subprocess.CalledProcessError, RuntimeError):
            pass

        # Strategy 2: resolve the real launcher component via package manager
        serial = wait_until_ready(avd_name)
        probe = _probe_package_state(serial, expected_package)
        resolved_lines = [line.strip() for line in probe.resolve_activity.splitlines() if line.strip()]
        for component in reversed(resolved_lines):
            if "/" not in component or component == "No activity found":
                continue
            try:
                _adb(avd_name, "shell", "am", "start", "-n", component, *(args or []))
                _wait_for_foreground_package(avd_name, expected_package)
                return
            except (subprocess.CalledProcessError, RuntimeError):
                pass

        # Strategy 3: explicit am start with package name
        try:
            _adb(
                avd_name, "shell", "am", "start",
                "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.LAUNCHER",
                "-n", f"{expected_package}/.MainActivity",
                *(args or []),
            )
            _wait_for_foreground_package(avd_name, expected_package)
            return
        except (subprocess.CalledProcessError, RuntimeError):
            pass

        # Strategy 4: common activity name patterns
        base_package = expected_package
        for suffix in (".dev", ".staging", ".prod", ".debug", ".release"):
            if base_package.endswith(suffix):
                base_package = base_package[: -len(suffix)]
                break

        candidates = (
            f"{expected_package}/.app.MainActivity",
            f"{expected_package}/{base_package}.app.MainActivity",
        )
        last_error: subprocess.CalledProcessError | None = None
        for component in candidates:
            try:
                _adb(avd_name, "shell", "am", "start", "-n", component, *(args or []))
                _wait_for_foreground_package(avd_name, expected_package)
                return
            except subprocess.CalledProcessError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
    else:
        cmd = ["shell", "am", "start", "-n", package_activity] + (args or [])
        _adb(avd_name, *cmd)
        _wait_for_foreground_package(avd_name, expected_package)


def terminate(avd_name: str, package: str) -> None:
    wait_until_ready(avd_name)
    _adb(avd_name, "shell", "am", "force-stop", package)


def uninstall(avd_name: str, package: str) -> None:
    wait_until_ready(avd_name)
    _adb(avd_name, "uninstall", package)


def dismiss_system_dialogs(avd_name: str) -> bool:
    """Dismiss any Android system dialog (ANR, crash, app not responding).

    Returns True if a dialog was detected and dismissed.
    """
    serial = wait_until_ready(avd_name)
    # Check for system dialog via dumpsys window
    result = subprocess.run(
        ["adb", "-s", serial, "shell", "dumpsys", "window", "windows"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    has_dialog = any(
        marker in result.stdout
        for marker in ("Application Not Responding", "has crashed", "isn't responding",
                       "ANR", "Application Error", "mIsAnrDialog=true")
    )
    if has_dialog:
        # Press Enter to dismiss the dialog, then back as fallback
        subprocess.run(["adb", "-s", serial, "shell", "input", "keyevent", "66"],
                       capture_output=True, check=False, timeout=5)
        time.sleep(0.3)
        subprocess.run(["adb", "-s", serial, "shell", "input", "keyevent", "4"],
                       capture_output=True, check=False, timeout=5)
        time.sleep(0.5)
        # Broadcast to dismiss any remaining system dialogs
        subprocess.run(
            ["adb", "-s", serial, "shell", "am", "broadcast",
             "-a", "android.intent.action.CLOSE_SYSTEM_DIALOGS"],
            capture_output=True, check=False, timeout=5,
        )
        return True
    return False


def screenshot(avd_name: str, output_path: str, max_size: Optional[int] = None) -> None:
    """Capture screenshot via adb exec-out screencap.
    max_size: if set, resize so the longest dimension is ≤ max_size px (uses sips).
    Automatically dismisses ANR/system dialogs before capture.
    """
    serial = wait_until_ready(avd_name)
    # Dismiss any blocking system dialogs before capturing
    dismiss_system_dialogs(avd_name)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        subprocess.run(
            ["adb", "-s", serial, "exec-out", "screencap", "-p"],
            stdout=f,
            check=True,
        )
    if max_size:
        subprocess.run(["sips", "-Z", str(max_size), output_path],
                       capture_output=True, check=False)


def record_start(avd_name: str, output_path: str) -> int:
    serial = wait_until_ready(avd_name)
    """
    Start screenrecord in background. Returns PID.
    Note: Android screenrecord has a hard 3-minute limit.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    remote = "/sdcard/simemu_record.mp4"
    proc = subprocess.Popen(
        ["adb", "-s", serial, "shell", "screenrecord",
         f"--time-limit={SCREENRECORD_MAX_SECONDS}", remote],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Store metadata in a sidecar for record_stop
    sidecar = _sidecar_dir() / f"rec_{proc.pid}.path"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(f"{serial}\n{remote}\n{output_path}")
    return proc.pid


def record_stop(pid: int) -> Optional[str]:
    """Stop recording and pull the video file. Returns local output path."""
    sidecar = _sidecar_dir() / f"rec_{pid}.path"
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        pass

    time.sleep(1)  # give screenrecord time to flush

    if sidecar.exists():
        serial, remote, local = sidecar.read_text().splitlines()
        subprocess.run(["adb", "-s", serial, "pull", remote, local], check=False)
        subprocess.run(["adb", "-s", serial, "shell", "rm", remote], check=False)
        sidecar.unlink()
        return local
    return None


def log_stream(avd_name: str, tag: Optional[str] = None, level: Optional[str] = None) -> None:
    _ensure_booted(avd_name)
    """Stream logcat (blocking, Ctrl-C to stop). level: V, D, I, W, E, F, S"""
    serial = _serial(avd_name)
    cmd = ["adb", "-s", serial, "logcat"]
    if level:
        cmd += [f"*:{level}"]
    if tag:
        # tag filter format: TAG:level or TAG:* — prepend before *:S to suppress others
        cmd = ["adb", "-s", serial, "logcat", f"{tag}:D", "*:S"]
        if level:
            cmd = ["adb", "-s", serial, "logcat", f"{tag}:{level}", "*:S"]
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


def open_url(avd_name: str, url: str, expected_package: Optional[str] = None) -> None:
    _ensure_booted(avd_name)
    _adb(
        avd_name,
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-c",
        "android.intent.category.DEFAULT",
        "-c",
        "android.intent.category.BROWSABLE",
        "-d",
        url,
    )
    if expected_package:
        _wait_for_foreground_package(avd_name, expected_package)


def push(avd_name: str, local_path: str, remote_path: str) -> None:
    if not Path(local_path).exists():
        raise RuntimeError(f"Local file not found: {local_path}")
    _adb(avd_name, "push", local_path, remote_path)


def pull(avd_name: str, remote_path: str, local_path: str) -> None:
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    _adb(avd_name, "pull", remote_path, local_path)


def erase(avd_name: str) -> None:
    """Wipe user data (factory reset). Stops emulator first if running."""
    from . import state, genymotion
    state.check_maintenance()
    if genymotion.is_genymotion_id(avd_name):
        if get_android_serial(avd_name) is not None:
            genymotion.shutdown(avd_name)
            time.sleep(2)
        genymotion.erase(avd_name)
        return
    if get_android_serial(avd_name) is not None:
        shutdown(avd_name)
        time.sleep(3)
    subprocess.run(
        ["emulator", "-avd", avd_name, "-wipe-data", "-no-window",
         "-no-audio", "-quit-after-boot", "1"],
        check=False,
    )


def rename(avd_name: str, new_name: str) -> None:
    """Rename an AVD or raise for Genymotion (unsupported via gmtool).

    Android AVD convention: filesystem names (AvdId, directory, .ini) use
    underscores; avd.ini.displayname uses the human-readable form with spaces.
    """
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        raise RuntimeError(
            "Genymotion VMs cannot be renamed via simemu. Use the Genymotion UI."
        )
    import shutil
    avd_base = Path.home() / ".android" / "avd"
    # Filesystem-safe id: spaces → underscores (matches emulator's own convention)
    avd_id = new_name.replace(" ", "_")
    old_ini = avd_base / f"{avd_name}.ini"
    new_ini = avd_base / f"{avd_id}.ini"

    if not old_ini.exists():
        raise RuntimeError(f"AVD not found: {avd_name} (no .ini at {old_ini})")

    if get_android_serial(avd_name) is not None:
        raise RuntimeError(f"Cannot rename '{avd_name}' while it is running. Shut it down first.")

    # Read actual AVD directory path from the .ini (it may differ from the AVD name)
    old_dir = None
    for line in old_ini.read_text().splitlines():
        if line.startswith("path="):
            old_dir = Path(line.split("=", 1)[1].strip())
            break
    if not old_dir or not old_dir.exists():
        raise RuntimeError(f"AVD directory not found (from .ini): {old_dir}")

    new_dir = avd_base / f"{avd_id}.avd"
    shutil.move(str(old_dir), str(new_dir))

    # Update AvdId (underscore form) and displayname (human-readable) inside config.ini
    config = new_dir / "config.ini"
    if config.exists():
        text = config.read_text()
        text = re.sub(r"(?m)^AvdId=.*$", f"AvdId={avd_id}", text)
        text = re.sub(r"(?m)^avd\.ini\.displayname=.*$",
                      f"avd.ini.displayname={new_name}", text)
        config.write_text(text)

    # Rewrite the .ini pointer file with new paths
    new_ini.write_text(
        f"avd.ini.encoding=UTF-8\n"
        f"path={new_dir}\n"
        f"path.rel=avd/{avd_id}.avd\n"
        f"target=android-35\n"
    )
    old_ini.unlink()


def delete(avd_name: str) -> None:
    """Permanently remove an AVD or Genymotion VM."""
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        genymotion.shutdown(avd_name)
        genymotion.delete(avd_name)
        return
    if get_android_serial(avd_name) is not None:
        shutdown(avd_name)
        time.sleep(2)
    import shutil
    avd_dir = Path.home() / ".android" / "avd" / f"{avd_name}.avd"
    ini_file = Path.home() / ".android" / "avd" / f"{avd_name}.ini"
    if avd_dir.exists():
        shutil.rmtree(avd_dir)
    if ini_file.exists():
        ini_file.unlink()
    if not avd_dir.exists() and not ini_file.exists():
        return
    raise RuntimeError(f"Could not fully remove AVD '{avd_name}'")


def get_env(avd_name: str) -> dict:
    from . import genymotion
    serial = get_android_serial(avd_name)
    if not serial:
        key = "uuid" if genymotion.is_genymotion_id(avd_name) else "avd"
        return {key: avd_name, "state": "Shutdown", "platform": "android"}

    props = {}
    for prop in ["ro.product.model", "ro.build.version.release", "ro.build.version.sdk"]:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", prop],
            capture_output=True, text=True,
        )
        props[prop] = result.stdout.strip()

    width, height = get_screen_size(avd_name)
    return {
        "avd": avd_name,
        "serial": serial,
        "state": "Booted",
        "platform": "android",
        "model": props.get("ro.product.model"),
        "android_version": props.get("ro.build.version.release"),
        "api_level": props.get("ro.build.version.sdk"),
        "screen_width_px": width,
        "screen_height_px": height,
    }


def set_appearance(avd_name: str, mode: str) -> None:
    """Set light or dark mode. mode must be 'light' or 'dark'."""
    value = "yes" if mode == "dark" else "no"
    _adb(avd_name, "shell", "cmd", "uimode", "night", value)


def get_screen_size(avd_name: str) -> tuple[int, int]:
    """Return physical screen dimensions (width, height) in pixels."""
    serial = _serial(avd_name)
    result = subprocess.run(
        ["adb", "-s", serial, "shell", "wm", "size"],
        capture_output=True, text=True,
    )
    # Output: "Physical size: 1080x2400" (override line may also appear)
    for line in result.stdout.splitlines():
        m = re.search(r"(\d+)x(\d+)", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise RuntimeError(f"Could not determine screen size for '{avd_name}'")


def tap(avd_name: str, x: int, y: int) -> None:
    """Tap a coordinate on the emulator screen."""
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "input", "tap", str(x), str(y))


def swipe(avd_name: str, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> None:
    """Swipe from (x1,y1) to (x2,y2). duration in milliseconds (default 300)."""
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(duration))


def shake(avd_name: str) -> None:
    _ensure_booted(avd_name)
    """Send Menu key (triggers React Native dev menu)."""
    _adb(avd_name, "shell", "input", "keyevent", "82")


def input_text(avd_name: str, text: str) -> None:
    _ensure_booted(avd_name)
    """Type text into the currently focused field.
    Note: spaces must be escaped as %s; use clipboard for complex strings."""
    # adb input text handles most printable chars; spaces become %s automatically
    safe = text.replace(" ", "%s").replace("'", "")
    _adb(avd_name, "shell", "input", "text", safe)


def privacy(avd_name: str, package: str, action: str, permission: str) -> None:
    _ensure_booted(avd_name)
    """
    Grant or revoke a runtime permission for an app.
    action: 'grant' | 'revoke'
    permission: full Android permission string, e.g. android.permission.CAMERA
                or short form: CAMERA, RECORD_AUDIO, ACCESS_FINE_LOCATION, etc.
    """
    if not permission.startswith("android.permission."):
        permission = f"android.permission.{permission}"
    _adb(avd_name, "shell", "pm", action, package, permission)


def location(avd_name: str, lat: float, lng: float) -> None:
    """Set a mock GPS location via adb shell geo fix."""
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        raise RuntimeError(
            "GPS location is not supported for Genymotion VMs via simemu. "
            "Use the Genymotion UI (GPS widget) to set location."
        )
    # adb emu geo fix <longitude> <latitude> (note: lng comes first)
    _adb(avd_name, "emu", "geo", "fix", str(lng), str(lat))


_ANDROID_KEYCODES: dict[str, int] = {
    "home":       3,
    "back":       4,
    "menu":       82,
    "power":      26,
    "lock":       26,
    "volume_up":  24,
    "volume_down": 25,
    "mute":       164,
    "enter":      66,
    "delete":     67,
    "backspace":  67,
    "search":     84,
    "app_switch": 187,
    "camera":     27,
    "screenshot": 120,
}


def key(avd_name: str, key_name: str) -> None:
    """Press a hardware key on the emulator.

    Accepts named keys (home, back, menu, power/lock, volume_up, volume_down,
    mute, enter, delete/backspace, search, app_switch, camera, screenshot)
    or a raw integer keycode.
    """
    _ensure_booted(avd_name)
    k = key_name.lower()
    if k in _ANDROID_KEYCODES:
        code = str(_ANDROID_KEYCODES[k])
    elif k.isdigit():
        code = k
    else:
        raise RuntimeError(
            f"Unknown Android key '{key_name}'. "
            f"Use a named key ({', '.join(_ANDROID_KEYCODES)}) or a numeric keycode."
        )
    _adb(avd_name, "shell", "input", "keyevent", code)


def long_press(avd_name: str, x: int, y: int, duration: int = 1000) -> None:
    """Long-press at a coordinate. duration in milliseconds (default 1000)."""
    _ensure_booted(avd_name)
    # adb input swipe at the same start/end coords with a long duration = long press
    _adb(avd_name, "shell", "input", "swipe",
         str(x), str(y), str(x), str(y), str(duration))


def rotate(avd_name: str, orientation: str) -> None:
    """Set device orientation: 'portrait' or 'landscape'."""
    _ensure_booted(avd_name)
    o = orientation.lower()
    if o not in ("portrait", "landscape"):
        raise RuntimeError(f"orientation must be 'portrait' or 'landscape' — got '{o}'")
    rotation = "0" if o == "portrait" else "1"
    # Disable auto-rotate then set fixed rotation
    _adb(avd_name, "shell", "settings", "put", "system", "accelerometer_rotation", "0")
    _adb(avd_name, "shell", "settings", "put", "system", "user_rotation", rotation)


def clear_data(avd_name: str, package: str) -> None:
    """Clear all app data (equivalent to uninstall + reinstall). Android only."""
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "pm", "clear", package)


def status_bar(avd_name: str, time_str: Optional[str] = None, battery: Optional[int] = None,
               wifi: Optional[int] = None) -> None:
    """Override the Android status bar via demo mode for clean screenshots.

    time_str: clock in HH:MM format, e.g. "9:41"
    battery:  0-100
    wifi:     0-4 bars
    """
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "settings", "put", "global", "sysui_demo_allowed", "1")
    _adb(avd_name, "shell", "am", "broadcast",
         "-a", "com.android.systemui.demo", "-e", "command", "enter", check=False)
    if time_str:
        hhmm = time_str.replace(":", "").zfill(4)
        _adb(avd_name, "shell", "am", "broadcast",
             "-a", "com.android.systemui.demo",
             "-e", "command", "clock", "-e", "hhmm", hhmm, check=False)
    if battery is not None:
        _adb(avd_name, "shell", "am", "broadcast",
             "-a", "com.android.systemui.demo",
             "-e", "command", "battery",
             "-e", "level", str(battery), "-e", "plugged", "false", check=False)
    if wifi is not None:
        bars = str(min(4, max(0, wifi)))
        _adb(avd_name, "shell", "am", "broadcast",
             "-a", "com.android.systemui.demo",
             "-e", "command", "network",
             "-e", "wifi", "show", "-e", "level", bars, check=False)


def status_bar_clear(avd_name: str) -> None:
    """Exit demo mode and restore the real status bar."""
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "am", "broadcast",
         "-a", "com.android.systemui.demo", "-e", "command", "exit", check=False)


def reboot(avd_name: str) -> None:
    """Reboot the emulator and wait until it's fully back up."""
    _ensure_booted(avd_name)
    serial = _serial(avd_name)
    subprocess.run(["adb", "-s", serial, "reboot"], check=False)
    print("Rebooting...", flush=True)
    time.sleep(5)  # allow device to go offline before polling
    deadline = time.time() + 120
    while time.time() < deadline:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
            capture_output=True, text=True,
        )
        if result.stdout.strip() == "1":
            return
        time.sleep(3)
    raise RuntimeError(f"Emulator '{avd_name}' did not complete reboot within 120s")


def network(avd_name: str, mode: str) -> None:
    """Set network connectivity mode (Android only).

    mode:
      airplane  — enable airplane mode (all radios off)
      all       — restore all connectivity (wifi + data, airplane off)
      wifi      — wifi only (disable mobile data)
      data      — mobile data only (disable wifi)
      none      — disable wifi and mobile data (but not airplane mode)
    """
    _ensure_booted(avd_name)
    m = mode.lower()
    if m == "airplane":
        _adb(avd_name, "shell", "cmd", "connectivity", "airplane-mode", "enable")
    elif m in ("all", "normal", "restore"):
        _adb(avd_name, "shell", "cmd", "connectivity", "airplane-mode", "disable")
    elif m == "wifi":
        _adb(avd_name, "shell", "cmd", "connectivity", "airplane-mode", "disable")
        _adb(avd_name, "shell", "svc", "data", "disable")
        _adb(avd_name, "shell", "svc", "wifi", "enable")
    elif m == "data":
        _adb(avd_name, "shell", "cmd", "connectivity", "airplane-mode", "disable")
        _adb(avd_name, "shell", "svc", "wifi", "disable")
        _adb(avd_name, "shell", "svc", "data", "enable")
    elif m == "none":
        _adb(avd_name, "shell", "svc", "wifi", "disable")
        _adb(avd_name, "shell", "svc", "data", "disable")
    else:
        raise RuntimeError(
            f"Unknown network mode '{mode}'. Use: airplane | all | wifi | data | none"
        )


def battery(avd_name: str, level: Optional[int] = None, reset: bool = False) -> None:
    """Override battery level for clean screenshots, or reset to real level.

    level: 0-100 (sets a fake battery level shown in status bar)
    reset: restore real battery state
    """
    _ensure_booted(avd_name)
    if reset:
        _adb(avd_name, "shell", "dumpsys", "battery", "reset")
    elif level is not None:
        clamped = max(0, min(100, level))
        _adb(avd_name, "shell", "dumpsys", "battery", "set", "level", str(clamped))
        _adb(avd_name, "shell", "dumpsys", "battery", "set", "status", "2")  # 2 = charging
    else:
        raise RuntimeError("Specify a battery level (0-100) or use --reset")


def set_animations(avd_name: str, enabled: bool) -> None:
    """Enable or disable UI animations.

    disabled (enabled=False): sets all animation scales to 0 — Maestro flows run
    without waiting for transitions, making tests faster and more stable.
    enabled (enabled=True): restores scale=1 for normal development use.
    """
    _ensure_booted(avd_name)
    scale = "1" if enabled else "0"
    _adb(avd_name, "shell", "settings", "put", "global", "window_animation_scale", scale)
    _adb(avd_name, "shell", "settings", "put", "global", "transition_animation_scale", scale)
    _adb(avd_name, "shell", "settings", "put", "global", "animator_duration_scale", scale)


def add_media(avd_name: str, file_path: str) -> None:
    """Add a photo or video file to the emulator's media library (Photos/Gallery).

    Pushes the file to /sdcard/DCIM/Camera/ and triggers the media scanner so it
    appears in the Photos app immediately — equivalent to iOS simctl addmedia.
    """
    _ensure_booted(avd_name)
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError(f"File not found: {file_path}")
    remote = f"/sdcard/DCIM/Camera/{path.name}"
    _adb(avd_name, "push", str(path), remote)
    _adb(avd_name, "shell", "am", "broadcast",
         "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
         "-d", f"file://{remote}", check=False)


def reset_app(avd_name: str, package: str, launch: bool = True) -> None:
    """Force-stop, clear all app data, then relaunch.

    Equivalent to uninstall+reinstall for data purposes, without removing the APK.
    """
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "am", "force-stop", package)
    time.sleep(0.3)
    _adb(avd_name, "shell", "pm", "clear", package)
    if launch:
        _adb(avd_name, "shell", "monkey", "-p", package,
             "-c", "android.intent.category.LAUNCHER", "1")


def crash_log(avd_name: str, package: Optional[str] = None, since_minutes: int = 60) -> Optional[str]:
    """Return recent crash/fatal log lines from logcat.

    Pulls logcat since since_minutes ago, filtering for fatal exceptions and ANRs.
    If package is given, only returns crashes from that process.
    Returns formatted crash text, or None if no crashes found.
    """
    _ensure_booted(avd_name)
    serial = _serial(avd_name)

    # logcat -t <seconds>s dumps logs from last N seconds
    seconds = since_minutes * 60
    cmd = ["adb", "-s", serial, "logcat", "-d", "-t", f"{seconds}s",
           "AndroidRuntime:E", "ActivityManager:E", "*:F"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    lines = result.stdout.splitlines()

    if package:
        # filter to lines mentioning the package
        lines = [l for l in lines if package in l or "FATAL EXCEPTION" in l or "ANR" in l]

    # collapse to only the non-empty lines around crashes
    crash_lines = [l for l in lines if any(k in l for k in (
        "FATAL EXCEPTION", "AndroidRuntime", "Caused by:", "ANR in", "Process:", "java.", "kotlin.", "at "
    ))]

    if not crash_lines:
        return None
    return "\n".join(crash_lines)


def biometrics(avd_name: str, match: bool) -> None:
    """Simulate a fingerprint touch on the emulator.

    match=True  → fingerprint ID 1 (enrolled, successful auth)
    match=False → fingerprint ID 2 (not enrolled, failed auth)

    The fingerprint must be enrolled first via Settings > Security > Fingerprint.
    """
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        raise RuntimeError(
            "Biometrics simulation is not supported for Genymotion VMs via simemu."
        )
    _ensure_booted(avd_name)
    finger_id = "1" if match else "2"
    _adb(avd_name, "emu", "finger", "touch", finger_id)
