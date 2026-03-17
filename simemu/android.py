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
from pathlib import Path
from typing import Optional

from .discover import get_android_serial

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


def _serial(avd_name: str) -> str:
    serial = get_android_serial(avd_name)
    if serial is None:
        raise RuntimeError(
            f"Android emulator '{avd_name}' is not running. "
            f"Run: simemu boot <slug>"
        )
    return serial


def _ensure_booted(avd_name: str) -> None:
    """Auto-boot the emulator if it's not running."""
    if get_android_serial(avd_name) is None:
        print(f"Emulator not running, booting...", flush=True)
        boot(avd_name)


def _adb(avd_name: str, *args, capture: bool = False, check: bool = True) -> Optional[str]:
    serial = _serial(avd_name)
    cmd = ["adb", "-s", serial] + list(args)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    else:
        subprocess.run(cmd, check=check)
        return None


def boot(avd_name: str, headless: bool = False) -> None:
    """Start the AVD if not already running. Waits until fully booted."""
    from . import genymotion
    if genymotion.is_genymotion_id(avd_name):
        if get_android_serial(avd_name) is None:
            genymotion.boot(avd_name)
        return

    if get_android_serial(avd_name) is not None:
        return

    cmd = ["emulator", "-avd", avd_name]
    if headless:
        cmd += ["-no-window", "-no-audio", "-no-boot-anim"]

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


def install(avd_name: str, apk_path: str, timeout: int = 120) -> None:
    _ensure_booted(avd_name)
    path = Path(apk_path)
    if not path.exists():
        raise RuntimeError(f"APK not found: {apk_path}")
    if path.suffix != ".apk":
        raise RuntimeError(f"Android requires a .apk file, got: {path.suffix}")
    serial = _serial(avd_name)
    cmd = ["adb", "-s", serial, "install", "-r", str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"Install timed out after {timeout}s. The emulator may be unresponsive. "
            f"Try: simemu reboot <slug>"
        )
    if result.returncode != 0 or "Failure" in result.stdout:
        detail = result.stdout.strip() or result.stderr.strip()
        raise RuntimeError(f"Install failed: {detail}")


def list_apps(avd_name: str) -> list[dict]:
    _ensure_booted(avd_name)
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


def launch(avd_name: str, package_activity: str, args: list[str] | None = None) -> None:
    _ensure_booted(avd_name)
    """
    Launch an app. package_activity can be:
      - "com.example.app"           → resolves main launcher activity
      - "com.example.app/.MainActivity"  → explicit activity
    """
    if "/" not in package_activity:
        # Prefer the launcher-intent path, but fall back to the conventional
        # MainActivity name used by our app templates when monkey is rejected.
        try:
            _adb(avd_name, "shell", "monkey", "-p", package_activity,
                 "-c", "android.intent.category.LAUNCHER", "1")
        except subprocess.CalledProcessError:
            base_package = package_activity
            for suffix in (".dev", ".staging", ".prod", ".debug", ".release"):
                if base_package.endswith(suffix):
                    base_package = base_package[: -len(suffix)]
                    break

            candidates = (
                f"{package_activity}/.app.MainActivity",
                f"{package_activity}/{base_package}.app.MainActivity",
            )
            last_error: subprocess.CalledProcessError | None = None
            for component in candidates:
                try:
                    _adb(avd_name, "shell", "am", "start", "-n", component)
                    return
                except subprocess.CalledProcessError as exc:
                    last_error = exc
            if last_error is not None:
                raise last_error
    else:
        cmd = ["shell", "am", "start", "-n", package_activity] + (args or [])
        _adb(avd_name, *cmd)


def terminate(avd_name: str, package: str) -> None:
    _ensure_booted(avd_name)
    _adb(avd_name, "shell", "am", "force-stop", package)


def uninstall(avd_name: str, package: str) -> None:
    _ensure_booted(avd_name)
    _adb(avd_name, "uninstall", package)


def screenshot(avd_name: str, output_path: str, max_size: Optional[int] = None) -> None:
    """Capture screenshot via screencap + adb pull.
    max_size: if set, resize so the longest dimension is ≤ max_size px (uses sips).
    """
    _ensure_booted(avd_name)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    remote = "/sdcard/simemu_screenshot.png"
    _adb(avd_name, "shell", "screencap", "-p", remote)
    _adb(avd_name, "pull", remote, output_path)
    _adb(avd_name, "shell", "rm", remote, check=False)
    if max_size:
        subprocess.run(["sips", "-Z", str(max_size), output_path],
                       capture_output=True, check=False)


def record_start(avd_name: str, output_path: str) -> int:
    _ensure_booted(avd_name)
    """
    Start screenrecord in background. Returns PID.
    Note: Android screenrecord has a hard 3-minute limit.
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    serial = _serial(avd_name)
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


def open_url(avd_name: str, url: str) -> None:
    _ensure_booted(avd_name)
    escaped_url = url.replace("&", r"\&")
    _adb(avd_name, "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", escaped_url)


def push(avd_name: str, local_path: str, remote_path: str) -> None:
    if not Path(local_path).exists():
        raise RuntimeError(f"Local file not found: {local_path}")
    _adb(avd_name, "push", local_path, remote_path)


def pull(avd_name: str, remote_path: str, local_path: str) -> None:
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    _adb(avd_name, "pull", remote_path, local_path)


def erase(avd_name: str) -> None:
    """Wipe user data (factory reset). Stops emulator first if running."""
    from . import genymotion
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
