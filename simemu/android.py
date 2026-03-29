"""
Android emulator operations via adb and emulator CLI.
Functions take an AVD name (sim_id) and resolve the adb serial as needed.
"""

import os
import re
import shutil
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


def _read_log_excerpt(log_path: Path, max_chars: int = 4000) -> str:
    try:
        content = log_path.read_text(errors="ignore")
    except OSError:
        return ""
    if len(content) <= max_chars:
        return content
    return content[-max_chars:]


def _capture_is_black(path: str, threshold: int = 98) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    candidate = Path(path)
    if not ffmpeg or not candidate.exists():
        return False
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "info", "-i", str(candidate), "-vf", f"blackframe={threshold}:32", "-f", "null", "-"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    output = f"{proc.stdout}\n{proc.stderr}"
    scores = [int(match.group(1)) for match in re.finditer(r"pblack:(\d+)", output)]
    return bool(scores) and max(scores) >= threshold


def _finalize_capture(candidate_path: str, output_path: str) -> bool:
    candidate = Path(candidate_path)
    try:
        size = candidate.stat().st_size
    except OSError:
        return False
    if size <= 100 or _capture_is_black(str(candidate)):
        return False
    candidate.replace(output_path)
    return True


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
                "window_id": int(window.get("kCGWindowNumber", 0) or 0),
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


def _capture_window_fallback(avd_name: str, output_path: str) -> bool:
    info = _window_info(avd_name)
    if not info or not info.get("onscreen"):
        return False

    tmp_path = output_path + ".windowtmp.png"
    try:
        window_id = int(info.get("window_id") or 0)
        if window_id > 0:
            proc = subprocess.run(
                ["screencapture", "-x", "-l", str(window_id), tmp_path],
                capture_output=True,
                check=False,
                timeout=10,
            )
        else:
            bounds = info["bounds"]
            rect = ",".join(
                str(int(bounds[key]))
                for key in ("x", "y", "width", "height")
            )
            proc = subprocess.run(
                ["screencapture", "-x", "-R", rect, tmp_path],
                capture_output=True,
                check=False,
                timeout=10,
            )
        if proc.returncode != 0:
            return False
        return _finalize_capture(tmp_path, output_path)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


def _capture_console_screenshot(serial: str, output_path: str) -> bool:
    """Use the emulator console screenshot path when adb screencap is broken.

    Android Emulator supports `adb emu screenrecord screenshot <dir>` and writes
    a PNG directly on the host. This works on headless emulator sessions where
    both exec-out screencap and on-device screencap can return empty files.
    """
    if not serial.startswith("emulator-"):
        return False

    with tempfile.TemporaryDirectory(prefix="simemu-console-screenshot-") as td:
        output_dir = Path(td)
        before = {p.name for p in output_dir.glob("*.png")}
        try:
            proc = subprocess.run(
                ["adb", "-s", serial, "emu", "screenrecord", "screenshot", str(output_dir)],
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False

        if proc.returncode != 0:
            return False

        candidates = [p for p in output_dir.glob("*.png") if p.name not in before]
        if not candidates:
            candidates = list(output_dir.glob("*.png"))
        candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
        for candidate in candidates:
            if _finalize_capture(str(candidate), output_path):
                return True
        return False


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


def _serial(avd_name: str, pinned: Optional[str] = None) -> str:
    """Resolve the adb serial for an AVD. If pinned is provided, validate it first.

    This prevents cross-session contamination when multiple emulators are running:
    the pinned serial ensures we always talk to the same emulator that was claimed.
    """
    if pinned:
        # Validate the pinned serial still belongs to this AVD
        if validate_serial(pinned, avd_name):
            return pinned
        # Pinned serial is stale — fall through to full resolution

    serial = _resolve_serial(avd_name, retries=6, delay=0.5)
    if serial is None:
        raise RuntimeError(
            f"Android device '{avd_name}' is not connected or adb-ready. "
            f"Re-claim the device or reconnect it, then retry."
        )
    return serial


def validate_serial(serial: str, expected_avd: str) -> bool:
    """Check that a serial (e.g. emulator-5554) belongs to the expected AVD.

    Returns False if the serial is offline, doesn't exist, or belongs to a
    different AVD. This is the core session isolation check.
    """
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "emu", "avd", "name"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0:
            return False
        actual_name = result.stdout.strip().splitlines()[0].strip() if result.stdout.strip() else ""
        return actual_name == expected_avd
    except (subprocess.TimeoutExpired, IndexError, OSError):
        return False


def get_serial(avd_name: str) -> str:
    """Compatibility helper used by the newer session-based simemu flow."""
    return _serial(avd_name)


def _ensure_booted(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    """Check emulator/device is adb-reachable. Raises instead of auto-booting runaway spawns."""
    from . import state
    state.check_maintenance()
    try:
        _serial(avd_name, pinned=pinned_serial)
        return
    except RuntimeError:
        pass
    if _resolve_serial(avd_name, retries=6, delay=0.5) is None:
        raise RuntimeError(
            f"Android device '{avd_name}' is not connected or adb-ready.\n"
            f"Re-claim the device or reconnect it, then retry."
        )


def wait_until_ready(
    avd_name: str,
    timeout: int = 180,
    pinned_serial: Optional[str] = None,
) -> str:
    """
    Wait until adb is online and the package manager responds.

    Genymotion-backed devices can briefly report a serial while still being
    offline for installs and package queries. "ready" should mean adb commands
    will actually work, not just that the VM exists.
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    deadline = time.time() + timeout
    last_detail = "device did not become adb-ready"
    while time.time() < deadline:
        try:
            serial = _serial(avd_name, pinned=pinned_serial)
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
            # Post-boot settle: wait for the launcher to be the foreground activity.
            # Without this, deep links opened immediately after boot can land on the
            # OS startup/animation screen instead of the target app.
            _wait_for_launcher_ready(serial)
            return serial

        last_detail = pm_result.stderr.strip() or pm_result.stdout.strip() or "package manager not ready"
        time.sleep(2)

    raise RuntimeError(
        f"Android emulator '{avd_name}' reported as booted but never became adb-ready within {timeout}s: {last_detail}"
    )


def _wait_for_launcher_ready(serial: str, timeout: float = 15.0) -> None:
    """Wait until the Android launcher is the foreground activity after boot.

    After sys.boot_completed=1, the home screen animation can still be playing.
    This prevents commands issued immediately after boot from capturing the
    boot animation instead of actual app content.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            for line in result.stdout.splitlines():
                if "mResumedActivity" in line or "topResumedActivity" in line:
                    lower = line.lower()
                    if "launcher" in lower or "home" in lower or "trebuchet" in lower:
                        return
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
            pass
        time.sleep(1)
    # Timeout is non-fatal — the device may still work, just not fully settled


def _adb(avd_name: str, *args, capture: bool = False, check: bool = True,
         timeout: int = 60, pinned_serial: Optional[str] = None) -> Optional[str]:
    """Run an adb command against the device. Retries once with fresh serial on connection errors."""
    return _adb_with_retry(avd_name, list(args), capture=capture, check=check,
                           timeout=timeout, pinned_serial=pinned_serial)


def _adb_with_retry(avd_name: str, args: list[str], *, capture: bool, check: bool,
                     timeout: int, pinned_serial: Optional[str] = None,
                     _retried: bool = False) -> Optional[str]:
    serial = _serial(avd_name, pinned=pinned_serial)
    cmd = ["adb", "-s", serial] + args
    try:
        if capture:
            result = subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout)
            output = result.stdout.strip()
            # Detect stale connection — adb returns but device is gone
            if result.returncode != 0 and not _retried:
                err = (result.stderr or "").lower()
                if "device" in err and ("not found" in err or "offline" in err):
                    time.sleep(1)
                    return _adb_with_retry(avd_name, args, capture=capture, check=check,
                                           timeout=timeout, pinned_serial=pinned_serial,
                                           _retried=True)
            return output
        else:
            subprocess.run(cmd, check=check, timeout=timeout)
            return None
    except subprocess.CalledProcessError as exc:
        if not _retried:
            err = str(exc).lower()
            if "device" in err and ("not found" in err or "offline" in err):
                time.sleep(1)
                return _adb_with_retry(avd_name, args, capture=capture, check=check,
                                       timeout=timeout, pinned_serial=pinned_serial,
                                       _retried=True)
        raise
    except subprocess.TimeoutExpired:
        if not _retried:
            # Serial might be stale after an activity-alias restart — retry once
            time.sleep(2)
            return _adb_with_retry(avd_name, args, capture=capture, check=check,
                                   timeout=timeout, pinned_serial=pinned_serial,
                                   _retried=True)
        raise RuntimeError(
            f"adb command timed out after {timeout}s (retried): {' '.join(cmd[:6])}...\n"
            f"The device may be unresponsive. Try: simemu do <session> reboot"
        )


def foreground_app(avd_name: str, retries: int = 2, delay: float = 1.0,
                   pinned_serial: Optional[str] = None) -> Optional[str]:
    """Return the currently resumed Android package, if detectable.

    Uses multiple detection strategies:
    1. mResumedActivity / topResumedActivity from dumpsys activity
    2. mFocusedApp from dumpsys window (catches dialogs/sheets)
    3. mCurrentFocus from dumpsys window (catches overlay fragments)

    Retries briefly to handle transient launcher-bounce after activity-alias
    switches.
    """
    for attempt in range(max(1, retries)):
        try:
            serial = _serial(avd_name, pinned=pinned_serial)

            # Strategy 1: dumpsys activity — the standard approach
            pkg = _detect_foreground_from_activity(serial)
            if pkg:
                lower = pkg.lower()
                if "launcher" not in lower and "home" not in lower:
                    return pkg
                # Got launcher — might be transient, try other methods before retrying
                pass

            # Strategy 2: dumpsys window — catches dialogs, sheets, overlays
            pkg = _detect_foreground_from_window(serial)
            if pkg:
                lower = pkg.lower()
                if "launcher" not in lower and "home" not in lower:
                    return pkg

            # If we got launcher from strategy 1, return it on the last attempt
            if attempt >= retries - 1:
                pkg = _detect_foreground_from_activity(serial) or _detect_foreground_from_window(serial)
                return pkg

        except (RuntimeError, subprocess.TimeoutExpired):
            pass
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def _detect_foreground_from_activity(serial: str) -> Optional[str]:
    """Detect foreground app from dumpsys activity activities."""
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        for line in result.stdout.splitlines():
            if "mResumedActivity" not in line and "ResumedActivity" not in line and "topResumedActivity" not in line:
                continue
            for part in line.split():
                if "/" in part and "." in part:
                    return part.split("/")[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _detect_foreground_from_window(serial: str) -> Optional[str]:
    """Detect foreground app from dumpsys window — catches dialogs and sheets."""
    try:
        result = subprocess.run(
            ["adb", "-s", serial, "shell", "dumpsys", "window"],
            capture_output=True, text=True, check=False, timeout=10,
        )
        # Check mFocusedApp and mCurrentFocus
        for line in result.stdout.splitlines():
            if "mFocusedApp" not in line and "mCurrentFocus" not in line:
                continue
            for part in line.split():
                if "/" in part and "." in part:
                    return part.split("/")[0]
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _wait_for_foreground_package(
    avd_name: str,
    package: str,
    timeout: float = 5.0,
    delay: float = 0.25,
    pinned_serial: Optional[str] = None,
) -> None:
    """Wait briefly until the expected package is resumed."""
    deadline = time.time() + timeout
    last_foreground = None
    while time.time() < deadline:
        last_foreground = foreground_app(avd_name, pinned_serial=pinned_serial)
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
        """Package state is considered OK if pm_path and resolve_activity both pass.

        dumpsys_ok is a bonus signal — it can be stale or timed out after
        activity-alias switches without the package actually being broken.
        Only require pm_path + resolve_activity for the core install-health check.
        """
        return self.pm_path_ok and self.resolve_activity_ok

    @property
    def fully_verified(self) -> bool:
        """All three probes passed — the strongest possible verification."""
        return self.pm_path_ok and self.resolve_activity_ok and self.dumpsys_ok

    def format_report(self) -> str:
        sections = [
            f"pm path:\n{self.pm_path or '(no output)'}",
            f"resolve-activity --brief:\n{self.resolve_activity or '(no output)'}",
            f"dumpsys package:\n{self.dumpsys or '(no output)'}",
        ]
        return "\n\n".join(sections)


def verify_install(
    avd_name: str,
    package: str,
    timeout: int = 30,
    pinned_serial: Optional[str] = None,
) -> PackageVerification:
    """Verify Android package-manager state is coherent after install."""
    serial = wait_until_ready(avd_name, pinned_serial=pinned_serial)
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


def repair_install(
    avd_name: str,
    package: str,
    apk_path: str,
    timeout: int = 120,
    pinned_serial: Optional[str] = None,
) -> PackageVerification:
    """Attempt escalating recovery for a package that installed into a bad PM state.

    Strategy: try the least-disruptive fix first (uninstall+reinstall), then reboot,
    then cold-boot. Each step has a hard per-step timeout to prevent long churn.
    Wipe-data is removed — it's too destructive and slow for runtime recovery.
    """
    step_timeout = min(timeout, 60)  # per-step ceiling
    serial = wait_until_ready(avd_name, timeout=step_timeout, pinned_serial=pinned_serial)

    # Step 0: simple uninstall + reinstall (no reboot) — handles most alias-switch state
    try:
        subprocess.run(["adb", "-s", serial, "uninstall", package],
                       capture_output=True, text=True, check=False, timeout=15)
        install(
            avd_name,
            apk_path,
            timeout=step_timeout,
            repair_on_failure=False,
            pinned_serial=pinned_serial,
        )
        probe = verify_install(avd_name, package, timeout=10, pinned_serial=pinned_serial)
        if probe.ok:
            return probe
    except RuntimeError:
        pass

    # Step 1: reboot + reinstall
    recovery_errors: list[str] = []
    recovery_steps = [
        ("reboot", _repair_reboot_cycle),
        ("cold-boot", _repair_cold_boot_cycle),
    ]

    for label, action in recovery_steps:
        step_start = time.time()
        try:
            action(avd_name, pinned_serial=pinned_serial)
            # Hard per-step timeout — don't let any single step churn
            elapsed = time.time() - step_start
            if elapsed > step_timeout:
                recovery_errors.append(f"{label}: exceeded {step_timeout}s step timeout")
                continue
            install(
                avd_name,
                apk_path,
                timeout=step_timeout,
                repair_on_failure=False,
                pinned_serial=pinned_serial,
            )
            probe = verify_install(avd_name, package, timeout=10, pinned_serial=pinned_serial)
            if probe.ok:
                return probe
            recovery_errors.append(f"{label}: install ok but verify failed")
        except RuntimeError as exc:
            recovery_errors.append(f"{label}: {exc}")

    joined = "\n".join(recovery_errors) or "unknown repair failure"
    raise RuntimeError(
        f"repair-install failed for '{package}' after 2 recovery attempts.\n"
        f"Recovery log:\n{joined}\n"
        f"Re-claim the session: simemu claim android"
    )


def _repair_reboot_cycle(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    reboot(avd_name, pinned_serial=pinned_serial)
    _verify_post_recovery_health(avd_name, pinned_serial=pinned_serial)


def _repair_cold_boot_cycle(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    shutdown(avd_name)
    time.sleep(3)
    boot(avd_name, headless=True)
    wait_until_ready(avd_name, timeout=90, pinned_serial=pinned_serial)
    _verify_post_recovery_health(avd_name, pinned_serial=pinned_serial)


def _verify_post_recovery_health(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    """After reboot/recovery, verify the device is actually usable before returning.

    Checks: adb stable, launcher ready, PM queryable, screenshot works.
    """
    serial = _serial(avd_name, pinned=pinned_serial)

    # 1. Launcher must be ready (not still on boot animation)
    _wait_for_launcher_ready(serial, timeout=15)

    # 2. PM must respond to a basic query
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            result = subprocess.run(
                ["adb", "-s", serial, "shell", "pm", "path", "android"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            if result.returncode == 0 and "package:" in result.stdout:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)

    # 3. Screenshot must work (validates screencap + adb pipeline)
    import tempfile
    _tmp_f = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    test_png = _tmp_f.name
    _tmp_f.close()
    try:
        screenshot(avd_name, test_png, pinned_serial=pinned_serial, settle_ms=0)
        if Path(test_png).stat().st_size < 100:
            raise RuntimeError("Post-recovery screenshot produced empty file")
    except (RuntimeError, OSError) as e:
        raise RuntimeError(f"Post-recovery health check failed: screenshot not working ({e})")
    finally:
        try:
            Path(test_png).unlink(missing_ok=True)
        except OSError:
            pass


def _probe_package_state(serial: str, package: str) -> PackageVerification:
    def _safe_run(cmd: list[str], timeout: int = 10) -> subprocess.CompletedProcess:
        """Run adb command with timeout, returning empty result on timeout instead of hanging."""
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="timed out")

    pm_path = _safe_run(["adb", "-s", serial, "shell", "pm", "path", package])
    resolved_launcher = _safe_run([
        "adb", "-s", serial, "shell", "cmd", "package", "resolve-activity",
        "--brief", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER", package,
    ])
    dumpsys = _safe_run(["adb", "-s", serial, "shell", "dumpsys", "package", package])

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
        f"Package [{package}]" in text
        or (f"pkg=Package{{" in text and package in text)
        or (f"PackageSetting{{" in text and package in text)
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
    base_cmd = ["emulator", "-avd", avd_name, "-memory", str(memory_mb)]
    if headless:
        base_cmd += ["-no-window", "-no-audio", "-no-boot-anim", "-gpu", "swiftshader_indirect"]

    launch_variants = [base_cmd]
    if "-no-snapshot-load" not in base_cmd:
        launch_variants.append(base_cmd + ["-no-snapshot-load"])

    last_error = ""
    for attempt, cmd in enumerate(launch_variants, start=1):
        log_handle = tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f"simemu-boot-{avd_name}-",
            suffix=".log",
            delete=False,
        )
        log_path = Path(log_handle.name)
        proc = subprocess.Popen(cmd, stdout=log_handle, stderr=subprocess.STDOUT)
        log_handle.close()

        print(f"Waiting for '{avd_name}' to boot...", flush=True)
        deadline = time.time() + 300
        serial = None
        while time.time() < deadline:
            serial = get_android_serial(avd_name)
            if serial:
                break
            if proc.poll() is not None:
                break
            time.sleep(2)

        if serial:
            deadline = time.time() + 180
            while time.time() < deadline:
                result = subprocess.run(
                    ["adb", "-s", serial, "shell", "getprop", "sys.boot_completed"],
                    capture_output=True, text=True, timeout=15,
                )
                if result.stdout.strip() == "1":
                    try:
                        log_path.unlink()
                    except OSError:
                        pass
                    return
                if proc.poll() is not None:
                    break
                time.sleep(2)

        exit_code = proc.poll()
        log_excerpt = _read_log_excerpt(log_path)
        try:
            if exit_code is None:
                proc.terminate()
                proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

        retryable_snapshot_failure = (
            attempt == 1 and
            "-no-snapshot-load" not in cmd and
            "snapshot" in log_excerpt.lower()
        )
        if retryable_snapshot_failure:
            last_error = (
                f"Emulator '{avd_name}' failed with snapshot state corruption; "
                f"retrying without snapshot load.\n{log_excerpt.strip()}"
            )
            try:
                log_path.unlink()
            except OSError:
                pass
            continue

        if exit_code is not None:
            last_error = (
                f"Emulator '{avd_name}' exited before adb became ready "
                f"(exit={exit_code}).\n{log_excerpt.strip()}"
            )
        elif serial:
            last_error = (
                f"Emulator '{avd_name}' got adb serial '{serial}' but never finished booting.\n"
                f"{log_excerpt.strip()}"
            )
        else:
            last_error = (
                f"Emulator '{avd_name}' did not appear within 300s.\n{log_excerpt.strip()}"
            )
        try:
            log_path.unlink()
        except OSError:
            pass
        break

    raise RuntimeError(last_error or f"Emulator '{avd_name}' failed to boot")


def shutdown(avd_name: str) -> None:
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        genymotion.shutdown(avd_name)
        return
    _adb(avd_name, "emu", "kill", check=False)


def install(
    avd_name: str,
    apk_path: str,
    timeout: int = 120,
    repair_on_failure: bool = True,
    pinned_serial: Optional[str] = None,
) -> None:
    serial = wait_until_ready(avd_name, timeout=max(timeout, 180), pinned_serial=pinned_serial)
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
        verify_install(avd_name, package_name, pinned_serial=pinned_serial)
        return
    except RuntimeError:
        if not repair_on_failure:
            raise
        repair_install(avd_name, package_name, str(path), timeout=timeout, pinned_serial=pinned_serial)


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


def stop_other_apps(
    avd_name: str,
    keep: str | list[str] | None = None,
    pinned_serial: Optional[str] = None,
) -> list[str]:
    """Force-stop all third-party packages except those in keep.

    Used to isolate the device for proof capture — prevents other agents'
    apps from intercepting deep links or appearing in foreground.
    Returns the list of packages that were stopped.
    """
    serial = wait_until_ready(avd_name, pinned_serial=pinned_serial)
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


def launch(
    avd_name: str,
    package_activity: str,
    args: list[str] | None = None,
    pinned_serial: Optional[str] = None,
) -> None:
    wait_until_ready(avd_name, pinned_serial=pinned_serial)
    """
    Launch an app. package_activity can be:
      - "com.example.app"           → resolves main launcher activity
      - "com.example.app/.MainActivity"  → explicit activity
    """
    expected_package = package_activity.split("/", 1)[0]
    if "/" not in package_activity:
        try:
            verify_install(avd_name, expected_package, timeout=15, pinned_serial=pinned_serial)
        except RuntimeError:
            pass

        # Strategy 1: resolve the real launcher component via package manager.
        # This is the most reliable approach — uses the actual registered launcher
        # activity (e.g. MainActivityPepperAlias) instead of guessing .MainActivity.
        serial = wait_until_ready(avd_name, pinned_serial=pinned_serial)
        probe = _probe_package_state(serial, expected_package)
        resolved_lines = [line.strip() for line in probe.resolve_activity.splitlines() if line.strip()]
        for component in resolved_lines:
            if "/" not in component or "No activity found" in component:
                continue
            # Validate it looks like a component: package/activity
            if not component.startswith(expected_package):
                continue
            try:
                _adb(avd_name, "shell", "am", "start", "-n", component, *(args or []), pinned_serial=pinned_serial)
                _wait_for_foreground_package(avd_name, expected_package, pinned_serial=pinned_serial)
                return
            except (subprocess.CalledProcessError, RuntimeError):
                pass

        # Strategy 2: monkey launch — reliable for standard launcher activities
        try:
            _adb(
                avd_name,
                "shell",
                "monkey",
                "-p", expected_package,
                "-c", "android.intent.category.LAUNCHER",
                "1",
                pinned_serial=pinned_serial,
            )
            _wait_for_foreground_package(avd_name, expected_package, pinned_serial=pinned_serial)
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
                pinned_serial=pinned_serial,
            )
            _wait_for_foreground_package(avd_name, expected_package, pinned_serial=pinned_serial)
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
                _adb(avd_name, "shell", "am", "start", "-n", component, *(args or []), pinned_serial=pinned_serial)
                _wait_for_foreground_package(avd_name, expected_package, pinned_serial=pinned_serial)
                return
            except subprocess.CalledProcessError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
    else:
        cmd = ["shell", "am", "start", "-n", package_activity] + (args or [])
        _adb(avd_name, *cmd, pinned_serial=pinned_serial)
        _wait_for_foreground_package(avd_name, expected_package, pinned_serial=pinned_serial)


def terminate(avd_name: str, package: str, pinned_serial: Optional[str] = None) -> None:
    wait_until_ready(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "am", "force-stop", package, pinned_serial=pinned_serial)


def uninstall(avd_name: str, package: str, pinned_serial: Optional[str] = None) -> None:
    wait_until_ready(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "uninstall", package, pinned_serial=pinned_serial)


def dismiss_system_dialogs(avd_name: str, pinned_serial: Optional[str] = None) -> bool:
    """Dismiss any Android system dialog (ANR, crash, app not responding).

    Returns True if a dialog was detected and dismissed.
    """
    serial = wait_until_ready(avd_name, pinned_serial=pinned_serial)
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


def screenshot(avd_name: str, output_path: str, max_size: Optional[int] = None,
               pinned_serial: Optional[str] = None,
               settle_ms: int = 500) -> None:
    """Capture screenshot via adb exec-out screencap.
    max_size: if set, resize so the longest dimension is ≤ max_size px (uses sips).
    settle_ms: wait this long before capturing to let the UI finish animating.
    Automatically dismisses ANR/system dialogs before capture.
    Waits for adb recovery on transient device-offline (e.g. after deep-link open
    triggers an activity-alias switch or app restart).
    Uses a temp file + rename to avoid leaving zero-byte files on timeout.
    """
    dismiss_system_dialogs(avd_name, pinned_serial=pinned_serial)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # Let the UI settle after route-open / activity transitions before capturing
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)

    max_attempts = 4
    for attempt in range(max_attempts):
        try:
            serial = _serial(avd_name, pinned=pinned_serial)
        except RuntimeError:
            if attempt < max_attempts - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(
                f"Screenshot failed: device '{avd_name}' went offline and did not recover.\n"
                f"Re-claim or reboot: simemu do <session> reboot"
            )

        captured = False
        if _capture_window_fallback(avd_name, output_path):
            break

        # Write to temp file, then rename — prevents leaving zero-byte files on timeout.
        # Use Popen for explicit process control — subprocess.run can leave adb
        # exec-out hanging when screencap stalls on a GPU buffer lock.
        tmp_path = output_path + ".tmp"
        try:
            with open(tmp_path, "wb") as f:
                proc = subprocess.Popen(
                    ["adb", "-s", serial, "exec-out", "screencap", "-p"],
                    stdout=f, stderr=subprocess.DEVNULL,
                )
                try:
                    proc.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)

            if proc.returncode == 0:
                if _finalize_capture(tmp_path, output_path):
                    captured = True
        except OSError:
            pass
        finally:
            if not captured:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except OSError:
                    pass

        if not captured:
            # Fallback for devices where adb exec-out stalls or returns an empty
            # stream under GPU load: write the screenshot on-device, then pull it.
            remote_tmp = f"/sdcard/Pictures/simemu_capture_{os.getpid()}.png"
            pulled_tmp = output_path + ".pulltmp"
            try:
                _adb(
                    avd_name,
                    "shell",
                    "rm",
                    "-f",
                    remote_tmp,
                    check=False,
                    timeout=10,
                    pinned_serial=serial,
                )
                _adb(
                    avd_name,
                    "shell",
                    "screencap",
                    "-p",
                    remote_tmp,
                    timeout=15,
                    pinned_serial=serial,
                )
                _adb(
                    avd_name,
                    "pull",
                    remote_tmp,
                    pulled_tmp,
                    timeout=20,
                    pinned_serial=serial,
                )
                if _finalize_capture(pulled_tmp, output_path):
                    captured = True
            except (OSError, RuntimeError, subprocess.CalledProcessError):
                pass
            finally:
                if not captured:
                    try:
                        Path(pulled_tmp).unlink(missing_ok=True)
                    except OSError:
                        pass
                try:
                    _adb(
                        avd_name,
                        "shell",
                        "rm",
                        "-f",
                        remote_tmp,
                        check=False,
                        timeout=10,
                        pinned_serial=serial,
                    )
                except RuntimeError:
                    pass

        if not captured:
            # Emulator console screenshots are host-side PNGs that still work on
            # some headless API 34/35 builds where screencap returns zero bytes.
            captured = _capture_console_screenshot(serial, output_path)

        if not captured:
            # Final fallback: some Android emulator builds return empty PNGs for
            # both exec-out screencap and on-device screencap. Screenrecord still
            # works there, so capture a 1-second MP4 and extract the first frame.
            ffmpeg = shutil.which("ffmpeg")
            remote_video = f"/sdcard/Movies/simemu_capture_{os.getpid()}.mp4"
            local_video = output_path + ".recordtmp.mp4"
            local_frame = output_path + ".recordtmp.png"
            try:
                if ffmpeg:
                    subprocess.run(
                        ["adb", "-s", serial, "shell", "rm", "-f", remote_video],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                    subprocess.run(
                        ["adb", "-s", serial, "shell", "screenrecord", "--time-limit=1", remote_video],
                        capture_output=True,
                        check=False,
                        timeout=12,
                    )
                    subprocess.run(
                        ["adb", "-s", serial, "pull", remote_video, local_video],
                        capture_output=True,
                        check=False,
                        timeout=20,
                    )
                    if Path(local_video).exists() and Path(local_video).stat().st_size > 1000:
                        subprocess.run(
                            [ffmpeg, "-y", "-i", local_video, "-frames:v", "1", local_frame],
                            capture_output=True,
                            check=False,
                            timeout=20,
                        )
                        if _finalize_capture(local_frame, output_path):
                            captured = True
            except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                pass
            finally:
                try:
                    subprocess.run(
                        ["adb", "-s", serial, "shell", "rm", "-f", remote_video],
                        capture_output=True,
                        check=False,
                        timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                for path in (local_video, local_frame):
                    try:
                        Path(path).unlink(missing_ok=True)
                    except OSError:
                        pass

        if not captured:
            captured = _capture_window_fallback(avd_name, output_path)

        if captured:
            break

        if attempt < max_attempts - 1:
            time.sleep(1 + attempt)
            continue

        raise RuntimeError(
            f"Screenshot failed after {max_attempts} attempts. Device '{avd_name}' may be unresponsive.\n"
            f"Try: simemu do <session> reboot"
        )

    if max_size:
        subprocess.run(["sips", "-Z", str(max_size), output_path],
                       capture_output=True, check=False)


def record_start(
    avd_name: str,
    output_path: str,
    pinned_serial: Optional[str] = None,
) -> int:
    """Start screenrecord in background. Returns PID.

    Note: Android screenrecord has a hard 3-minute limit.
    """
    serial = wait_until_ready(avd_name, pinned_serial=pinned_serial)
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
        subprocess.run(["adb", "-s", serial, "pull", remote, local], check=False, timeout=30)
        subprocess.run(["adb", "-s", serial, "shell", "rm", remote], check=False, timeout=10)
        sidecar.unlink()
        return local
    return None


def log_stream(
    avd_name: str,
    tag: Optional[str] = None,
    level: Optional[str] = None,
    pinned_serial: Optional[str] = None,
) -> None:
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    """Stream logcat (blocking, Ctrl-C to stop). level: V, D, I, W, E, F, S"""
    serial = _serial(avd_name, pinned=pinned_serial)
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


def log_tail(
    avd_name: str,
    tag: Optional[str] = None,
    level: Optional[str] = None,
    tail_lines: int = 200,
    pinned_serial: Optional[str] = None,
) -> str:
    """Return recent logcat lines as text."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    serial = _serial(avd_name, pinned=pinned_serial)
    cmd = ["adb", "-s", serial, "logcat", "-d"]
    if tag:
        tag_level = level or "V"
        cmd = ["adb", "-s", serial, "logcat", "-d", f"{tag}:{tag_level}", "*:S"]
    elif level:
        cmd = ["adb", "-s", serial, "logcat", "-d", f"*:{level}"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
    lines = result.stdout.splitlines()
    if tail_lines > 0:
        lines = lines[-tail_lines:]
    return "\n".join(lines)


def open_url(
    avd_name: str,
    url: str,
    expected_package: Optional[str] = None,
    pinned_serial: Optional[str] = None,
) -> None:
    """Open a URL/deep-link on the device.

    Tries multiple intent strategies to handle apps that register deep links
    with different category combinations (DEFAULT, BROWSABLE, or none).
    Retries on transient adb offline errors.
    """
    wait_until_ready(avd_name, pinned_serial=pinned_serial)

    # Brief settle after any prior command — prevents racing with app process restart
    time.sleep(0.3)

    last_error: Exception | None = None
    strategies = [
        # Strategy 1: VIEW + BROWSABLE (standard deep link)
        ["shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-c", "android.intent.category.BROWSABLE", "-d", url],
        # Strategy 2: VIEW only (no category — catches debug/internal routes)
        ["shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url],
        # Strategy 3: VIEW + DEFAULT (some apps register with DEFAULT only)
        ["shell", "am", "start", "-a", "android.intent.action.VIEW",
         "-c", "android.intent.category.DEFAULT", "-d", url],
    ]

    for strategy in strategies:
        for attempt in range(2):
            try:
                result = _adb(
                    avd_name,
                    *strategy,
                    capture=True,
                    check=False,
                    timeout=15,
                    pinned_serial=pinned_serial,
                )
                # Check if am start reported an error
                if result and "unable to resolve" in result.lower():
                    last_error = RuntimeError(f"Intent not resolved: {result.strip()}")
                    break  # Try next strategy
                if result and "error" in result.lower() and "type 3" in result.lower():
                    last_error = RuntimeError(f"Activity not found: {result.strip()}")
                    break  # Try next strategy
                # Intent dispatched — verify it actually landed in the right app
                time.sleep(0.5)
                if expected_package:
                    try:
                        _wait_for_foreground_package(
                            avd_name,
                            expected_package,
                            timeout=5.0,
                            pinned_serial=pinned_serial,
                        )
                    except RuntimeError:
                        # Wrong app foregrounded — this strategy didn't work
                        actual = foreground_app(avd_name, retries=1, pinned_serial=pinned_serial)
                        last_error = RuntimeError(
                            f"URL dispatched but wrong app is foreground: "
                            f"expected={expected_package}, actual={actual}"
                        )
                        break  # Try next strategy
                return
            except RuntimeError as e:
                err_str = str(e).lower()
                if "device offline" in err_str or "not found" in err_str or "timed out" in err_str:
                    if attempt == 0:
                        time.sleep(2)
                        continue
                last_error = e
                break  # Try next strategy

    # All strategies failed
    if last_error:
        raise RuntimeError(
            f"Failed to open URL '{url[:80]}' on device '{avd_name}'.\n"
            f"Last error: {last_error}\n"
            f"The app may not handle this deep link scheme, or the device is unresponsive."
        )
    raise RuntimeError(f"Failed to open URL '{url[:80]}' — all intent strategies exhausted.")


def push(
    avd_name: str,
    local_path: str,
    remote_path: str,
    pinned_serial: Optional[str] = None,
) -> None:
    if not Path(local_path).exists():
        raise RuntimeError(f"Local file not found: {local_path}")
    _adb(avd_name, "push", local_path, remote_path, pinned_serial=pinned_serial)


def pull(
    avd_name: str,
    remote_path: str,
    local_path: str,
    pinned_serial: Optional[str] = None,
) -> None:
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    _adb(avd_name, "pull", remote_path, local_path, pinned_serial=pinned_serial)


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

    # Preserve the target from the original .ini (don't hardcode android-35)
    original_target = "android-35"
    for line in old_ini.read_text().splitlines():
        if line.startswith("target="):
            original_target = line.split("=", 1)[1].strip()
            break

    # Rewrite the .ini pointer file with new paths
    new_ini.write_text(
        f"avd.ini.encoding=UTF-8\n"
        f"path={new_dir}\n"
        f"path.rel=avd/{avd_id}.avd\n"
        f"target={original_target}\n"
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


def set_appearance(avd_name: str, mode: str, pinned_serial: Optional[str] = None) -> None:
    """Set light or dark mode. mode must be 'light' or 'dark'."""
    value = "yes" if mode == "dark" else "no"
    _adb(avd_name, "shell", "cmd", "uimode", "night", value, pinned_serial=pinned_serial)


def get_screen_size(avd_name: str, pinned_serial: Optional[str] = None) -> tuple[int, int]:
    """Return physical screen dimensions (width, height) in pixels."""
    serial = _serial(avd_name, pinned=pinned_serial)
    result = subprocess.run(
        ["adb", "-s", serial, "shell", "wm", "size"],
        capture_output=True, text=True, timeout=10,
    )
    # Output: "Physical size: 1080x2400" (override line may also appear)
    for line in result.stdout.splitlines():
        m = re.search(r"(\d+)x(\d+)", line)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise RuntimeError(f"Could not determine screen size for '{avd_name}'")


def tap(avd_name: str, x: int, y: int, pinned_serial: Optional[str] = None) -> None:
    """Tap a coordinate on the emulator screen."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "input", "tap", str(x), str(y), pinned_serial=pinned_serial)


def swipe(
    avd_name: str,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    duration: int = 300,
    pinned_serial: Optional[str] = None,
) -> None:
    """Swipe from (x1,y1) to (x2,y2). duration in milliseconds (default 300)."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "input", "swipe",
         str(x1), str(y1), str(x2), str(y2), str(duration), pinned_serial=pinned_serial)


def shake(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    """Send Menu key (triggers React Native dev menu)."""
    _adb(avd_name, "shell", "input", "keyevent", "82", pinned_serial=pinned_serial)


def input_text(avd_name: str, text: str, pinned_serial: Optional[str] = None) -> None:
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    """Type text into the currently focused field.
    Note: spaces must be escaped as %s; use clipboard for complex strings."""
    # adb input text handles most printable chars; spaces become %s automatically
    safe = text.replace(" ", "%s").replace("'", "")
    _adb(avd_name, "shell", "input", "text", safe, pinned_serial=pinned_serial)


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


def location(
    avd_name: str,
    lat: float,
    lng: float,
    pinned_serial: Optional[str] = None,
) -> None:
    """Set a mock GPS location via adb shell geo fix."""
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        raise RuntimeError(
            "GPS location is not supported for Genymotion VMs via simemu. "
            "Use the Genymotion UI (GPS widget) to set location."
        )
    # adb emu geo fix <longitude> <latitude> (note: lng comes first)
    _adb(avd_name, "emu", "geo", "fix", str(lng), str(lat), pinned_serial=pinned_serial)


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


def key(avd_name: str, key_name: str, pinned_serial: Optional[str] = None) -> None:
    """Press a hardware key on the emulator.

    Accepts named keys (home, back, menu, power/lock, volume_up, volume_down,
    mute, enter, delete/backspace, search, app_switch, camera, screenshot)
    or a raw integer keycode.
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
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
    _adb(avd_name, "shell", "input", "keyevent", code, pinned_serial=pinned_serial)


def long_press(
    avd_name: str,
    x: int,
    y: int,
    duration: int = 1000,
    pinned_serial: Optional[str] = None,
) -> None:
    """Long-press at a coordinate. duration in milliseconds (default 1000)."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    # adb input swipe at the same start/end coords with a long duration = long press
    _adb(avd_name, "shell", "input", "swipe",
         str(x), str(y), str(x), str(y), str(duration), pinned_serial=pinned_serial)


def rotate(avd_name: str, orientation: str, pinned_serial: Optional[str] = None) -> None:
    """Set device orientation: 'portrait' or 'landscape'."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    o = orientation.lower()
    if o not in ("portrait", "landscape"):
        raise RuntimeError(f"orientation must be 'portrait' or 'landscape' — got '{o}'")
    rotation = "0" if o == "portrait" else "1"
    # Disable auto-rotate then set fixed rotation
    _adb(avd_name, "shell", "settings", "put", "system", "accelerometer_rotation", "0", pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "settings", "put", "system", "user_rotation", rotation, pinned_serial=pinned_serial)


def clear_data(avd_name: str, package: str, pinned_serial: Optional[str] = None) -> None:
    """Clear all app data (equivalent to uninstall + reinstall). Android only."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "pm", "clear", package, pinned_serial=pinned_serial)


def status_bar(avd_name: str, time_str: Optional[str] = None, battery: Optional[int] = None,
               wifi: Optional[int] = None, pinned_serial: Optional[str] = None) -> None:
    """Override the Android status bar via demo mode for clean screenshots.

    time_str: clock in HH:MM format, e.g. "9:41"
    battery:  0-100
    wifi:     0-4 bars
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "settings", "put", "global", "sysui_demo_allowed", "1", pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "am", "broadcast",
         "-a", "com.android.systemui.demo", "-e", "command", "enter", check=False, pinned_serial=pinned_serial)
    if time_str:
        hhmm = time_str.replace(":", "").zfill(4)
        _adb(avd_name, "shell", "am", "broadcast",
             "-a", "com.android.systemui.demo",
             "-e", "command", "clock", "-e", "hhmm", hhmm, check=False, pinned_serial=pinned_serial)
    if battery is not None:
        _adb(avd_name, "shell", "am", "broadcast",
             "-a", "com.android.systemui.demo",
             "-e", "command", "battery",
             "-e", "level", str(battery), "-e", "plugged", "false", check=False, pinned_serial=pinned_serial)
    if wifi is not None:
        bars = str(min(4, max(0, wifi)))
        _adb(avd_name, "shell", "am", "broadcast",
             "-a", "com.android.systemui.demo",
             "-e", "command", "network",
             "-e", "wifi", "show", "-e", "level", bars, check=False, pinned_serial=pinned_serial)


def status_bar_clear(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    """Exit demo mode and restore the real status bar."""
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "am", "broadcast",
         "-a", "com.android.systemui.demo", "-e", "command", "exit", check=False, pinned_serial=pinned_serial)


def reboot(avd_name: str, pinned_serial: Optional[str] = None) -> None:
    """Reboot the emulator and wait until it's fully back up.

    Re-resolves the adb serial after reboot since the emulator may come
    back on a different port.
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    serial = _serial(avd_name, pinned=pinned_serial)
    subprocess.run(["adb", "-s", serial, "reboot"], check=False, timeout=30)
    print("Rebooting...", flush=True)
    time.sleep(5)  # allow device to go offline before polling
    # Use wait_until_ready which re-resolves the serial and verifies PM
    try:
        wait_until_ready(avd_name, timeout=120, pinned_serial=pinned_serial)
    except RuntimeError:
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


def add_media(avd_name: str, file_path: str, pinned_serial: Optional[str] = None) -> None:
    """Add a photo or video file to the emulator's media library (Photos/Gallery).

    Pushes the file to /sdcard/DCIM/Camera/ and triggers the media scanner so it
    appears in the Photos app immediately — equivalent to iOS simctl addmedia.
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError(f"File not found: {file_path}")
    remote = f"/sdcard/DCIM/Camera/{path.name}"
    _adb(avd_name, "push", str(path), remote, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "am", "broadcast",
         "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
         "-d", f"file://{remote}", check=False, pinned_serial=pinned_serial)


def reset_app(
    avd_name: str,
    package: str,
    launch: bool = True,
    pinned_serial: Optional[str] = None,
) -> None:
    """Force-stop, clear all app data, then relaunch.

    Equivalent to uninstall+reinstall for data purposes, without removing the APK.
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    _adb(avd_name, "shell", "am", "force-stop", package, pinned_serial=pinned_serial)
    time.sleep(0.3)
    _adb(avd_name, "shell", "pm", "clear", package, pinned_serial=pinned_serial)
    if launch:
        _adb(avd_name, "shell", "monkey", "-p", package,
             "-c", "android.intent.category.LAUNCHER", "1", pinned_serial=pinned_serial)


def crash_log(
    avd_name: str,
    package: Optional[str] = None,
    since_minutes: int = 60,
    pinned_serial: Optional[str] = None,
) -> Optional[str]:
    """Return recent Android crash or launch-failure lines from logcat.

    Prefer the dedicated crash buffer, then fall back to recent package-related
    lines from the main log buffer. This catches:
    - normal FATAL EXCEPTION crashes
    - ANRs / force-finishing activity lines
    - startup failures where the process dies before AndroidRuntime emits a
      conventional Java stack trace
    """
    _ensure_booted(avd_name, pinned_serial=pinned_serial)
    serial = _serial(avd_name, pinned=pinned_serial)
    seconds = since_minutes * 60

    def _run(cmd: list[str]) -> list[str]:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        return result.stdout.splitlines()

    def _with_context(lines: list[str], indices: list[int], radius: int = 2) -> list[str]:
        if not indices:
            return []
        keep: list[str] = []
        seen: set[int] = set()
        for idx in indices:
            start = max(0, idx - radius)
            end = min(len(lines), idx + radius + 1)
            for pos in range(start, end):
                if pos in seen:
                    continue
                seen.add(pos)
                keep.append(lines[pos])
        return keep

    crash_markers = (
        "FATAL EXCEPTION",
        "AndroidRuntime",
        "Caused by:",
        "ANR in",
        "Process:",
        "Shutting down VM",
        "java.",
        "kotlin.",
        " at ",
    )
    launch_failure_markers = (
        "Force finishing activity",
        "Force stopping",
        "Unable to start activity",
        "Unable to resume activity",
        "Unable to instantiate activity",
        "Activity top resumed state loss timeout",
        "Scheduling restart of crashed service",
        "has crashed",
        "isn't responding",
        "ANR in",
    )

    crash_lines = _run(["adb", "-s", serial, "logcat", "-d", "-b", "crash"])
    if package:
        relevant = [i for i, line in enumerate(crash_lines) if package in line or any(marker in line for marker in crash_markers)]
    else:
        relevant = [i for i, line in enumerate(crash_lines) if any(marker in line for marker in crash_markers)]
    excerpt = _with_context(crash_lines, relevant, radius=3)
    if excerpt:
        return "\n".join(excerpt)

    main_lines = _run(["adb", "-s", serial, "logcat", "-d"])
    if seconds > 0 and len(main_lines) > 4000:
        main_lines = main_lines[-4000:]
    relevant_indices: list[int] = []
    for i, line in enumerate(main_lines):
        if any(marker in line for marker in crash_markers + launch_failure_markers):
            relevant_indices.append(i)
            continue
        if package and package in line:
            relevant_indices.append(i)
    excerpt = _with_context(main_lines, relevant_indices, radius=2)
    if excerpt:
        return "\n".join(excerpt[-120:])
    return None


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
