"""
iOS simulator operations via xcrun simctl.
All functions take a UDID and operate on that specific simulator.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Optional


_LAST_BUSY_NOTIFICATION_AT = 0.0
_HUD_PROCESS: subprocess.Popen | None = None
_CONTROL_HANDLERS_INSTALLED = False
_PAUSE_REQUESTED = False
_STOP_REQUESTED = False


def _simctl(*args, capture: bool = False, check: bool = True) -> Optional[str]:
    cmd = ["xcrun", "simctl"] + list(args)
    if capture:
        result = subprocess.run(cmd, capture_output=True, text=True, check=check)
        return result.stdout.strip()
    else:
        subprocess.run(cmd, check=check)
        return None


def _is_booted(udid: str) -> bool:
    out = subprocess.check_output(
        ["xcrun", "simctl", "list", "devices", "--json"],
        stderr=subprocess.DEVNULL,
    )
    data = json.loads(out)
    for devices in data["devices"].values():
        for dev in devices:
            if dev["udid"] == udid:
                return dev["state"] == "Booted"
    return False


def boot(udid: str, minimize: bool = False) -> None:
    """Boot simulator if not already booted.

    minimize is accepted for API compatibility but ignored — moving the iOS
    Simulator window does not save memory, so windows stay where they are.
    All simemu operations (screenshots, gestures) work without window focus.
    """
    if _is_booted(udid):
        return
    _simctl("boot", udid)
    _simctl("bootstatus", udid, "-b")


def _ensure_booted(udid: str) -> None:
    """Auto-boot if needed. Called transparently by all proxy commands."""
    if not _is_booted(udid):
        print(f"Simulator not running, booting...", flush=True)
        boot(udid)


def shutdown(udid: str) -> None:
    _simctl("shutdown", udid, check=False)


def install(udid: str, app_path: str, timeout: int = 120) -> None:
    _ensure_booted(udid)
    """
    Install an app onto the simulator.
    Accepts .app directories or .ipa files (auto-extracts IPA → .app).
    timeout: seconds before giving up (default 120).
    """
    path = Path(app_path)
    if not path.exists():
        raise RuntimeError(f"App path not found: {app_path}")

    def _install_path(p: str) -> None:
        cmd = ["xcrun", "simctl", "install", udid, p]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"Install timed out after {timeout}s. The simulator may be unresponsive. "
                f"Try: simemu reboot <slug>"
            )
        if result.returncode != 0:
            raise RuntimeError(f"Install failed: {result.stderr.strip() or result.stdout.strip()}")

    if path.suffix == ".ipa":
        extracted = _extract_ipa(path)
        try:
            _install_path(extracted)
        finally:
            shutil.rmtree(Path(extracted).parent.parent, ignore_errors=True)
    elif path.suffix == ".app" or path.is_dir():
        _install_path(str(path))
    else:
        raise RuntimeError(f"Unsupported app format: {path.suffix}. Use .app or .ipa")


def _extract_ipa(ipa_path: Path) -> str:
    """Extract .ipa to a temp directory, return path to the .app bundle."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="simemu_ipa_"))
    with zipfile.ZipFile(ipa_path) as z:
        z.extractall(tmp_dir)

    payload = tmp_dir / "Payload"
    if not payload.exists():
        raise RuntimeError(f"Invalid .ipa: no Payload directory in {ipa_path}")

    apps = list(payload.glob("*.app"))
    if not apps:
        raise RuntimeError(f"No .app bundle found in {ipa_path}/Payload/")

    return str(apps[0])


def launch(udid: str, bundle_id: str, args: list[str] | None = None) -> None:
    _ensure_booted(udid)
    cmd_args = ["launch", udid, bundle_id] + (args or [])
    _simctl(*cmd_args)


def terminate(udid: str, bundle_id: str) -> None:
    _ensure_booted(udid)
    _simctl("terminate", udid, bundle_id, check=False)


def uninstall(udid: str, bundle_id: str) -> None:
    _ensure_booted(udid)
    _simctl("uninstall", udid, bundle_id)


def list_apps(udid: str) -> list[dict]:
    _ensure_booted(udid)
    """Return list of installed apps with bundle ID and display name."""
    out = _simctl("listapps", udid, capture=True)
    # listapps outputs a plist; parse with plutil
    result = subprocess.run(
        ["plutil", "-convert", "json", "-o", "-", "-"],
        input=out.encode() if out else b"",
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return []
    data = json.loads(result.stdout)
    apps = []
    for bundle_id, info in data.items():
        apps.append({
            "bundle_id": bundle_id,
            "name": info.get("CFBundleDisplayName") or info.get("CFBundleName", ""),
            "version": info.get("CFBundleShortVersionString", ""),
            "path": info.get("Path", ""),
        })
    return sorted(apps, key=lambda x: x["name"].lower())


def screenshot(udid: str, output_path: str, fmt: Optional[str] = None,
               max_size: Optional[int] = None) -> None:
    """Take a screenshot. fmt: png (default), jpeg, tiff, bmp, gif.
    max_size: if set, resize so the longest dimension is ≤ max_size px (uses sips).
    """
    _ensure_booted(udid)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["io", udid, "screenshot"]
    if fmt:
        cmd += ["--type", fmt]
    cmd.append(output_path)
    _simctl(*cmd)
    if max_size:
        subprocess.run(["sips", "-Z", str(max_size), output_path],
                       capture_output=True, check=False)


def record_start(udid: str, output_path: str, codec: Optional[str] = None) -> int:
    _ensure_booted(udid)
    """
    Start video recording in the background. Returns PID.
    codec: hevc (default), h264, hevc-alpha
    """
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    cmd = ["xcrun", "simctl", "io", udid, "recordVideo", "--force"]
    if codec:
        cmd += ["--codec", codec]
    cmd.append(output_path)
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.pid


def record_stop(pid: int) -> None:
    """Stop a background recording process (SIGINT triggers graceful finalize)."""
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        pass


def log_stream(udid: str, predicate: Optional[str] = None, level: str = "debug") -> None:
    _ensure_booted(udid)
    """Stream logs from the simulator (blocking, Ctrl-C to stop)."""
    cmd = ["xcrun", "simctl", "spawn", udid, "log", "stream", "--level", level]
    if predicate:
        cmd += ["--predicate", predicate]
    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        pass


def open_url(udid: str, url: str) -> None:
    _ensure_booted(udid)
    _simctl("openurl", udid, url)


def erase(udid: str) -> None:
    """Factory reset the simulator (must be shut down first)."""
    shutdown(udid)
    _simctl("erase", udid)


def delete(udid: str) -> None:
    """Permanently remove a simulator."""
    shutdown(udid)
    _simctl("delete", udid)


def rename(udid: str, new_name: str) -> None:
    """Rename a simulator."""
    _simctl("rename", udid, new_name)


def push_notification(udid: str, bundle_id: str, payload_path: str) -> None:
    _ensure_booted(udid)
    path = Path(payload_path)
    if not path.exists():
        raise RuntimeError(f"Payload file not found: {payload_path}")
    _simctl("push", udid, bundle_id, str(path))


def add_media(udid: str, file_path: str) -> None:
    _ensure_booted(udid)
    path = Path(file_path)
    if not path.exists():
        raise RuntimeError(f"File not found: {file_path}")
    _simctl("addmedia", udid, str(path))


def get_env(udid: str) -> dict:
    out = subprocess.check_output(
        ["xcrun", "simctl", "list", "devices", "--json"],
        stderr=subprocess.DEVNULL,
    )
    data = json.loads(out)
    for runtime, devices in data["devices"].items():
        for dev in devices:
            if dev["udid"] == udid:
                device_w, device_h = _get_device_logical_size(dev["name"])
                return {
                    "udid": udid,
                    "name": dev["name"],
                    "state": dev["state"],
                    "runtime": runtime.split(".")[-1].replace("-", "."),
                    "platform": "ios",
                    "screen_width_pt": device_w,
                    "screen_height_pt": device_h,
                }
    return {"udid": udid, "platform": "ios"}


def set_appearance(udid: str, mode: str) -> None:
    _ensure_booted(udid)
    """Set light or dark mode. mode must be 'light' or 'dark'."""
    _simctl("ui", udid, "appearance", mode)


def shake(udid: str) -> None:
    _ensure_booted(udid)
    """Send a shake gesture (triggers React Native dev menu)."""
    _simctl("io", udid, "shake")


def clipboard_get(udid: str) -> str:
    """Return the current contents of the simulator's pasteboard as a string.

    Useful for verifying that 'copy' buttons actually copied the expected value.
    Returns empty string if the pasteboard is empty or contains non-text data.
    """
    _ensure_booted(udid)
    import base64 as _b64
    result = subprocess.run(
        ["xcrun", "simctl", "pasteboard", "get", udid],
        capture_output=True, check=False,
    )
    if result.returncode != 0:
        return ""
    raw = result.stdout.strip()
    if not raw:
        return ""
    try:
        return _b64.b64decode(raw).decode("utf-8", errors="replace")
    except Exception:
        return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw


def input_text(udid: str, text: str) -> None:
    _ensure_booted(udid)
    """Paste text into the simulator via the pasteboard (works in any focused text field)."""
    import subprocess as _sp
    # Newer Xcode builds expose simulator pasteboard via pbcopy/pbpaste instead of
    # the older `simctl pasteboard set` subcommand.
    proc = _sp.run(
        ["xcrun", "simctl", "pbcopy", udid],
        input=text.encode(),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Failed to set pasteboard: {proc.stderr.decode().strip()}")


def privacy(udid: str, bundle_id: str, action: str, service: str) -> None:
    _ensure_booted(udid)
    """
    Grant or revoke a privacy permission for an app.
    action: 'grant' | 'revoke' | 'reset'
    service: photos, camera, microphone, contacts, location, notifications, etc.
    """
    _simctl("privacy", udid, action, bundle_id, service)


def location(udid: str, lat: float, lng: float) -> None:
    _ensure_booted(udid)
    """Set a fixed GPS location on the simulator (requires Xcode 14.3+)."""
    _simctl("location", udid, "set", f"{lat},{lng}")


def location_clear(udid: str) -> None:
    """Clear the fixed GPS location override."""
    _simctl("location", udid, "clear")


# Canonical logical point dimensions (portrait) for known iOS devices.
# These are the coordinate space agents use when calling tap/swipe.
# Keyed by the device-type token in the simemu naming convention.
# Fallback: (390, 844) — standard iPhone 14/15/16 size.
_IOS_DEVICE_LOGICAL_SIZE: dict[str, tuple[int, int]] = {
    # iPhone 17 family
    "iPhone17ProMax":  (440, 956),
    "iPhone17Pro":     (402, 874),
    "iPhone17":        (393, 852),
    # iPhone 16 family
    "iPhone16ProMax":  (440, 956),
    "iPhone16Plus":    (430, 932),
    "iPhone16Pro":     (402, 874),
    "iPhone16e":       (390, 844),
    "iPhone16":        (390, 844),
    # iPhone 15 family
    "iPhone15ProMax":  (430, 932),
    "iPhone15Plus":    (430, 932),
    "iPhone15Pro":     (393, 852),
    "iPhone15":        (393, 852),
    # iPhone 14 family
    "iPhone14ProMax":  (430, 932),
    "iPhone14Plus":    (428, 926),
    "iPhone14Pro":     (393, 852),
    "iPhone14":        (390, 844),
    # iPhone SE
    "iPhoneSE":        (375, 667),
    # iPad (common sizes — landscape has these swapped, portrait is canonical)
    "iPadPro13":       (1024, 1366),
    "iPadPro12":       (1024, 1366),
    "iPadPro11":       (834, 1194),
    "iPadAir13":       (1024, 1366),
    "iPadAir11":       (820, 1180),
    "iPadMini":        (744, 1133),
    "iPad":            (810, 1080),
}


def _get_device_logical_size(device_name: str) -> tuple[int, int]:
    """Return the canonical (width_pt, height_pt) for a device given its display name.

    Matches against _IOS_DEVICE_LOGICAL_SIZE by looking for known tokens in the
    device name (e.g. "Mochi iPhone16Pro 6.3in iOS18" → "iPhone16Pro" → (402, 874)).
    Falls back to (390, 844) for unknown devices.
    """
    for token, size in _IOS_DEVICE_LOGICAL_SIZE.items():
        if token.lower() in device_name.lower().replace(" ", "").replace("-", ""):
            return size
    return (390, 844)


def _get_device_name(udid: str) -> str:
    """Return the display name of a simulator by UDID."""
    import subprocess as _sp
    import json as _json
    out = _sp.run(
        ["xcrun", "simctl", "list", "devices", "--json"],
        capture_output=True, text=True, check=True,
    )
    data = _json.loads(out.stdout)
    for _runtime, devices in data["devices"].items():
        for dev in devices:
            if dev["udid"] == udid:
                return dev["name"]
    raise RuntimeError(f"Simulator {udid} not found in simctl list")


def _raise_sim_window(device_name: str) -> None:
    """Activate Simulator and raise the target window for reliable text focus.

    Buttons can often be clicked with the window merely raised, but SwiftUI
    text fields frequently need Simulator to be the active app to receive first
    responder status. We still keep the user's cursor hidden/restored around
    the click itself, but we intentionally activate Simulator here so field
    focus and paste work consistently.
    """
    import subprocess as _sp
    _sp.run(["osascript", "-e", f'''tell application "System Events"
    tell application "Simulator" to activate
    delay 0.1
    tell process "Simulator"
        perform action "AXRaise" of (first window whose name contains "{device_name}")
    end tell
end tell'''], capture_output=True, check=False)


def _frontmost_app_name() -> Optional[str]:
    result = subprocess.run(
        [
            "osascript",
            "-e",
            '''tell application "System Events"
    set frontApps to (application processes whose frontmost is true)
    if (count of frontApps) is 0 then return ""
    return name of item 1 of frontApps
end tell''',
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    name = result.stdout.strip()
    return name or None


def _activate_app(app_name: str) -> None:
    escaped = app_name.replace('"', '\\"')
    subprocess.run(
        ["osascript", "-e", f'tell application "{escaped}" to activate'],
        capture_output=True,
        check=False,
    )


def _notify_shared_desktop_wait() -> None:
    global _LAST_BUSY_NOTIFICATION_AT
    now = time.time()
    if now - _LAST_BUSY_NOTIFICATION_AT < 10:
        return
    _LAST_BUSY_NOTIFICATION_AT = now
    subprocess.run(
        [
            "osascript",
            "-e",
            'display notification "Simemu is using a simulator for a moment. Please pause keyboard and mouse input." with title "simemu"',
        ],
        capture_output=True,
        check=False,
    )


def _handle_pause_signal(_signum, _frame) -> None:
    global _PAUSE_REQUESTED
    _PAUSE_REQUESTED = not _PAUSE_REQUESTED


def _handle_stop_signal(_signum, _frame) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _install_control_signal_handlers() -> None:
    global _CONTROL_HANDLERS_INSTALLED
    if _CONTROL_HANDLERS_INSTALLED:
        return
    signal.signal(signal.SIGUSR1, _handle_pause_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)
    _CONTROL_HANDLERS_INSTALLED = True


def _reset_interaction_control() -> None:
    global _PAUSE_REQUESTED, _STOP_REQUESTED
    _PAUSE_REQUESTED = False
    _STOP_REQUESTED = False


def _check_interaction_control() -> None:
    while _PAUSE_REQUESTED and not _STOP_REQUESTED:
        time.sleep(0.2)
    if _STOP_REQUESTED:
        raise RuntimeError("simemu interaction stopped by user")


def _hud_enabled() -> bool:
    value = (os.environ.get("SIMEMU_HUD", "1")).strip().lower()
    return value not in {"0", "false", "no", "off"}


def _start_hud_overlay() -> None:
    global _HUD_PROCESS
    if not _hud_enabled():
        return
    if _HUD_PROCESS and _HUD_PROCESS.poll() is None:
        return
    parent_pid = os.getpid()
    script = rf"""
import os
import signal
import threading
import tkinter as tk

PARENT_PID = {parent_pid}

root = tk.Tk()
root.withdraw()
windows = []

def _displays():
    try:
        import Quartz
        max_displays = 16
        active, count = Quartz.CGGetActiveDisplayList(max_displays, None, None)
        result = []
        for display_id in active[:count]:
            bounds = Quartz.CGDisplayBounds(display_id)
            result.append((int(bounds.origin.x), int(bounds.origin.y), int(bounds.size.width), int(bounds.size.height)))
        return result or [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]
    except Exception:
        return [(0, 0, root.winfo_screenwidth(), root.winfo_screenheight())]

def _build_window(screen_x, screen_y, screen_w, _screen_h):
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.attributes("-alpha", 0.94)
    win.configure(bg="#0f0a14")

    frame = tk.Frame(win, bg="#201326", highlightthickness=1, highlightbackground="#6c3b62")
    frame.pack(fill="both", expand=True)

    title = tk.Label(
        frame,
        text="simemu using Simulator / Emulator",
        bg="#201326",
        fg="#f4e9f2",
        font=("SF Pro Display", 14, "bold"),
        padx=18,
        pady=(10, 4),
    )
    title.pack()

    subtitle = tk.Label(
        frame,
        text="Give me a sec. Please pause keyboard and mouse input.",
        bg="#201326",
        fg="#c9b6c8",
        font=("SF Pro Text", 11),
        padx=18,
        pady=(0, 4),
    )
    subtitle.pack()

    shortcuts = tk.Label(
        frame,
        text="Stop: Cmd+.    Pause: Cmd+Shift+.",
        bg="#201326",
        fg="#9e889c",
        font=("SF Pro Text", 10),
        padx=18,
        pady=(0, 10),
    )
    shortcuts.pack()

    width = 420
    height = 106
    x = max(screen_x + 20, screen_x + screen_w - width - 24)
    y = screen_y + 26
    win.geometry(f"{width}x{height}+{x}+{y}")
    win.deiconify()
    windows.append(win)

for display in _displays():
    _build_window(*display)

def _close(*_args):
    for win in windows:
        try:
            win.destroy()
        except Exception:
            pass
    try:
        root.destroy()
    except Exception:
        pass

signal.signal(signal.SIGTERM, _close)
signal.signal(signal.SIGINT, _close)

def _send(sig):
    try:
        os.kill(PARENT_PID, sig)
    except Exception:
        pass

def _install_shortcuts():
    try:
        import Quartz

        state = {{"cmd": False, "shift": False}}

        def callback(proxy, event_type, event, refcon):
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            cmd = bool(flags & Quartz.kCGEventFlagMaskCommand)
            shift = bool(flags & Quartz.kCGEventFlagMaskShift)
            if event_type == Quartz.kCGEventKeyDown and keycode == 47 and cmd:
                if shift:
                    _send(signal.SIGUSR1)
                else:
                    _send(signal.SIGINT)
            return event

        mask = Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            mask,
            callback,
            None,
        )
        if tap is None:
            return
        run_loop_source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), run_loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(tap, True)
    except Exception:
        return

threading.Thread(target=_install_shortcuts, daemon=True).start()
root.mainloop()
"""
    try:
        _HUD_PROCESS = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        _HUD_PROCESS = None


def _stop_hud_overlay() -> None:
    global _HUD_PROCESS
    proc = _HUD_PROCESS
    _HUD_PROCESS = None
    if not proc:
        return
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        return


@contextmanager
def _interactive_overlay():
    _start_hud_overlay()
    try:
        yield
    finally:
        _stop_hud_overlay()


@contextmanager
def _restore_frontmost_app():
    previous = _frontmost_app_name()
    try:
        yield
    finally:
        if previous and previous != "Simulator":
            _activate_app(previous)


def _open_sim_window(udid: str) -> None:
    """Ask Simulator.app to show the target device window."""
    subprocess.run(
        ["open", "-a", "Simulator", "--args", "-CurrentDeviceUDID", udid],
        capture_output=True,
        check=False,
    )


def _get_sim_bounds(udid: str) -> tuple[float, float, float, float]:
    """Return (px, py, sw, sh) — screen origin and pixel size of the simulator content area."""
    import subprocess as _sp

    device_name = _get_device_name(udid)

    r = _sp.run([
        "osascript", "-e",
        f'''tell application "System Events"
    tell process "Simulator"
        set w to first window whose name contains "{device_name}"
        set grp to first UI element of w whose role is "AXGroup"
        set p to position of grp
        set s to size of grp
        return ((item 1 of p) as string) & "," & ((item 2 of p) as string) & "," & ((item 1 of s) as string) & "," & ((item 2 of s) as string)
    end tell
end tell''',
    ], capture_output=True, text=True, check=False)

    bounds_str = r.stdout.strip()
    if not bounds_str or "," not in bounds_str:
        raise RuntimeError(f"Could not get device content bounds for simulator {udid}")

    parts = [float(v.strip()) for v in bounds_str.split(",")]
    return parts[0], parts[1], parts[2], parts[3]


def _desktop_idle_seconds() -> float:
    try:
        import importlib as _il
        Quartz = _il.import_module("Quartz")
        events = [
            Quartz.kCGAnyInputEventType,
        ]
        ages = [
            Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateCombinedSessionState,
                event_type,
            )
            for event_type in events
        ]
        return min(ages)
    except Exception:
        return 999.0


def _wait_for_desktop_idle(min_idle_seconds: float = 1.0, max_wait_seconds: float = 5.0) -> float:
    deadline = time.time() + max_wait_seconds
    idle = _desktop_idle_seconds()
    warned = False
    while idle < min_idle_seconds and time.time() < deadline:
        _check_interaction_control()
        if not warned:
            _notify_shared_desktop_wait()
            warned = True
        time.sleep(0.2)
        idle = _desktop_idle_seconds()
    return idle


def _stabilized_bounds(udid: str, retries: int = 5, delay: float = 0.2) -> tuple[str, tuple[float, float, float, float]]:
    _ensure_booted(udid)
    _open_sim_window(udid)
    device_name = _get_device_name(udid)
    last_error: Exception | None = None
    for _ in range(retries):
        _raise_sim_window(device_name)
        try:
            return device_name, _get_sim_bounds(udid)
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
    if last_error:
        raise last_error
    raise RuntimeError(f"Could not stabilize simulator window for {udid}")


def stabilize(udid: str) -> dict:
    idle_seconds = _wait_for_desktop_idle()
    device_name, bounds = _stabilized_bounds(udid)
    return {
        "stable": True,
        "udid": udid,
        "device_name": device_name,
        "content_bounds": {
            "x": bounds[0],
            "y": bounds[1],
            "width": bounds[2],
            "height": bounds[3],
        },
        "desktop_idle_seconds": idle_seconds,
        "frontmost_app": _frontmost_app_name(),
    }


def present(udid: str) -> dict:
    _ensure_booted(udid)
    _open_sim_window(udid)
    focus(udid)
    return stabilize(udid)


def _logical_to_screen(
    lx: int, ly: int,
    px: float, py: float,
    sw: float, sh: float,
    device_w: int = 390, device_h: int = 844,
) -> tuple[int, int]:
    """Convert logical points to Mac screen coordinates.

    lx, ly      — input in the device's logical point space (e.g. 402×874 for iPhone 16 Pro)
    px, py      — top-left of the simulator content area on the Mac display
    sw, sh      — displayed size of the simulator content area in Mac display points
    device_w/h  — canonical logical size of the device (from _get_device_logical_size)

    At 100% zoom sw == device_w, so the scale factor is 1.0.
    At other zoom levels sw/device_w gives the correct scale.
    """
    scale_x = sw / device_w
    scale_y = sh / device_h
    return int(px + lx * scale_x), int(py + ly * scale_y)


def _get_simulator_pid() -> int:
    """Return the PID of the running Simulator.app process."""
    result = subprocess.run(
        ["pgrep", "-f", "Simulator.app/Contents/MacOS/Simulator"],
        capture_output=True, text=True,
    )
    pids = [p.strip() for p in result.stdout.splitlines() if p.strip()]
    if not pids:
        raise RuntimeError(
            "Simulator.app is not running. Boot a simulator first with: simemu boot <slug>"
        )
    return int(pids[0])


# macOS virtual key codes used by Simulator.app shortcuts
_VK_H = 4    # h
_VK_L = 37   # l
_VK_S = 1    # s
_VK_LEFT  = 123
_VK_RIGHT = 124

def _post_key(sim_pid: int, vk: int, modifiers: int = 0) -> None:
    """Send a key-down + key-up event directly to Simulator.app without stealing focus."""
    import importlib as _il
    Quartz = _il.import_module("Quartz")
    import time as _t
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    e_dn = Quartz.CGEventCreateKeyboardEvent(src, vk, True)
    e_up = Quartz.CGEventCreateKeyboardEvent(src, vk, False)
    if modifiers:
        Quartz.CGEventSetFlags(e_dn, modifiers)
        Quartz.CGEventSetFlags(e_up, modifiers)
    Quartz.CGEventPostToPid(sim_pid, e_dn)
    _t.sleep(0.05)
    Quartz.CGEventPostToPid(sim_pid, e_up)


def _post_mouse_hidden(events_fn) -> None:
    """Run events_fn(Quartz) with the system cursor hidden.

    Saves the cursor position, hides the cursor, calls events_fn (which may
    move the cursor via CGEventPost), warps back to the original position, then
    shows the cursor. The user never sees the cursor move.
    """
    import importlib as _il
    Quartz = _il.import_module("Quartz")
    display_id = getattr(Quartz, "kCGDirectMainDisplay", None)
    if display_id is None:
        display_id = Quartz.CGMainDisplayID()

    loc = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    orig = Quartz.CGPoint(x=loc.x, y=loc.y)
    Quartz.CGDisplayHideCursor(display_id)
    try:
        events_fn(Quartz)
        Quartz.CGWarpMouseCursorPosition(orig)
    finally:
        Quartz.CGDisplayShowCursor(display_id)


def tap(udid: str, x: int, y: int) -> None:
    """Tap at logical-point coordinates on the simulator screen.

    Raises the Simulator window to receive the click, hides the system cursor
    during the event so the pointer does not visibly jump to the tap location.
    """
    import importlib as _il
    Quartz = _il.import_module("Quartz")
    import time as _t

    _install_control_signal_handlers()
    _reset_interaction_control()
    with _interactive_overlay():
        _wait_for_desktop_idle()
        _ensure_booted(udid)
        device_name, (px, py, sw, sh) = _stabilized_bounds(udid)
        device_w, device_h = _get_device_logical_size(device_name)
        cx, cy = _logical_to_screen(x, y, px, py, sw, sh, device_w, device_h)
        with _restore_frontmost_app():
            _check_interaction_control()
            _raise_sim_window(device_name)
            sim_pid = _get_simulator_pid()

            def _click(Q):
                src = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
                pos = Q.CGPoint(x=cx, y=cy)
                e_mv = Q.CGEventCreateMouseEvent(src, Q.kCGEventMouseMoved,   pos, Q.kCGMouseButtonLeft)
                e_dn = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseDown, pos, Q.kCGMouseButtonLeft)
                e_up = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseUp,   pos, Q.kCGMouseButtonLeft)
                _check_interaction_control()
                Q.CGEventPostToPid(sim_pid, e_mv)
                _t.sleep(0.03)
                _check_interaction_control()
                Q.CGEventPostToPid(sim_pid, e_dn)
                _t.sleep(0.05)
                _check_interaction_control()
                Q.CGEventPostToPid(sim_pid, e_up)

            _post_mouse_hidden(_click)


def swipe(udid: str, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
    """Swipe from (x1,y1) to (x2,y2) in logical points over duration seconds.

    Uses ~60fps drag events so iOS physics (sheet dismiss, scroll momentum) triggers correctly.
    Cursor is hidden during the swipe so it does not visibly track across the screen.
    """
    import importlib as _il
    Quartz = _il.import_module("Quartz")
    import time as _t

    _install_control_signal_handlers()
    _reset_interaction_control()
    with _interactive_overlay():
        _wait_for_desktop_idle()
        _ensure_booted(udid)
        device_name, (px, py, sw, sh) = _stabilized_bounds(udid)
        device_w, device_h = _get_device_logical_size(device_name)

        sx1, sy1 = _logical_to_screen(x1, y1, px, py, sw, sh, device_w, device_h)
        sx2, sy2 = _logical_to_screen(x2, y2, px, py, sw, sh, device_w, device_h)
        steps = max(10, int(duration * 60))
        step_delay = duration / steps

        def _drag(Q):
            src = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
            start = Q.CGPoint(x=sx1, y=sy1)
            e_dn = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseDown, start, Q.kCGMouseButtonLeft)
            _check_interaction_control()
            Q.CGEventPost(Q.kCGSessionEventTap, e_dn)
            _t.sleep(step_delay)
            for i in range(1, steps + 1):
                _check_interaction_control()
                t = i / steps
                pos = Q.CGPoint(x=int(sx1 + (sx2 - sx1) * t), y=int(sy1 + (sy2 - sy1) * t))
                e_drag = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseDragged, pos, Q.kCGMouseButtonLeft)
                Q.CGEventPost(Q.kCGSessionEventTap, e_drag)
                _t.sleep(step_delay)
            end = Q.CGPoint(x=sx2, y=sy2)
            e_up = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseUp, end, Q.kCGMouseButtonLeft)
            _check_interaction_control()
            Q.CGEventPost(Q.kCGSessionEventTap, e_up)

        with _restore_frontmost_app():
            _check_interaction_control()
            _raise_sim_window(device_name)
            _post_mouse_hidden(_drag)


def long_press(udid: str, x: int, y: int, duration: float = 1.0) -> None:
    """Long-press at a logical-point coordinate. duration in seconds (default 1.0).

    Cursor is hidden during the press so it does not visibly jump to the target.
    """
    import importlib as _il
    Quartz = _il.import_module("Quartz")
    import time as _t

    _install_control_signal_handlers()
    _reset_interaction_control()
    with _interactive_overlay():
        _wait_for_desktop_idle()
        _ensure_booted(udid)
        device_name, (px, py, sw, sh) = _stabilized_bounds(udid)
        device_w, device_h = _get_device_logical_size(device_name)
        cx, cy = _logical_to_screen(x, y, px, py, sw, sh, device_w, device_h)

        def _press(Q):
            src = Q.CGEventSourceCreate(Q.kCGEventSourceStateHIDSystemState)
            pos = Q.CGPoint(x=cx, y=cy)
            e_dn = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseDown, pos, Q.kCGMouseButtonLeft)
            e_up = Q.CGEventCreateMouseEvent(src, Q.kCGEventLeftMouseUp,   pos, Q.kCGMouseButtonLeft)
            _check_interaction_control()
            Q.CGEventPost(Q.kCGSessionEventTap, e_dn)
            start = _t.time()
            while _t.time() - start < duration:
                _check_interaction_control()
                _t.sleep(0.05)
            _check_interaction_control()
            Q.CGEventPost(Q.kCGSessionEventTap, e_up)

        with _restore_frontmost_app():
            _check_interaction_control()
            _raise_sim_window(device_name)
            _post_mouse_hidden(_press)


def rotate(udid: str, orientation: str) -> None:
    """Set device orientation: 'portrait', 'landscape', 'left', or 'right'.

    'portrait' and 'landscape' check the current state and only rotate if needed.
    'left' / 'right' always send one rotation in that direction.
    Does not steal focus from the user's active application.
    """
    _ensure_booted(udid)
    orientation = orientation.lower()
    sim_pid = _get_simulator_pid()

    if orientation in ("left", "right"):
        vk = _VK_LEFT if orientation == "left" else _VK_RIGHT
        import importlib as _il
        Quartz = _il.import_module("Quartz")
        _post_key(sim_pid, vk, Quartz.kCGEventFlagMaskCommand)
        return

    if orientation not in ("portrait", "landscape"):
        raise RuntimeError(f"orientation must be portrait, landscape, left, or right — got '{orientation}'")

    # Check current orientation from window dimensions and rotate once if needed
    px, py, sw, sh = _get_sim_bounds(udid)
    current = "landscape" if sw > sh else "portrait"
    if current != orientation:
        import importlib as _il
        Quartz = _il.import_module("Quartz")
        _post_key(sim_pid, _VK_RIGHT, Quartz.kCGEventFlagMaskCommand)


# Named key → (virtual key code, modifier flags mask, description)
# Modifier flags: kCGEventFlagMaskCommand=0x100000, kCGEventFlagMaskShift=0x20000
_VK_V = 9   # v

_IOS_KEYS: dict[str, tuple[int, int, str]] = {
    "home":       (_VK_H, 0x120000, "Go to home screen (Cmd+Shift+H)"),
    "lock":       (_VK_L, 0x100000, "Lock device (Cmd+L)"),
    "siri":       (_VK_H, 0x100000, "Invoke Siri (Cmd+H)"),
    "screenshot": (_VK_S, 0x120000, "System screenshot (Cmd+Shift+S)"),
    "paste":      (_VK_V, 0x100000, "Paste clipboard (Cmd+V)"),
}


def key(udid: str, key_name: str) -> None:
    """Press a named hardware key on the simulator.

    Supported: home, lock, siri, screenshot
    Does not steal focus from the user's active application.
    """
    _ensure_booted(udid)
    k = key_name.lower()
    if k not in _IOS_KEYS:
        raise RuntimeError(
            f"Unknown iOS key '{key_name}'. Supported: {', '.join(_IOS_KEYS)}"
        )
    vk, modifiers, _ = _IOS_KEYS[k]
    _post_key(_get_simulator_pid(), vk, modifiers)


def status_bar(udid: str, time_str: Optional[str] = None, battery: Optional[int] = None,
               wifi: Optional[int] = None, network: Optional[str] = None) -> None:
    """Override the simulator status bar for clean screenshots.

    time_str: clock display, e.g. "9:41"
    battery:  0-100
    wifi:     0-3 bars
    network:  wifi | 5g | 4g | lte | 3g | 2g | edge | none
    """
    _ensure_booted(udid)
    cmd = ["status_bar", udid, "override"]
    if time_str:
        cmd += ["--time", time_str]
    if battery is not None:
        cmd += ["--batteryLevel", str(battery), "--batteryState", "charged"]
    if wifi is not None:
        cmd += ["--wifiBars", str(min(3, max(0, wifi)))]
    if network:
        cmd += ["--dataNetwork", network]
    _simctl(*cmd)


def status_bar_clear(udid: str) -> None:
    """Restore the real status bar after an override."""
    _ensure_booted(udid)
    _simctl("status_bar", udid, "clear")


def set_animations(udid: str, enabled: bool) -> None:
    """Enable or disable UI animations.

    disabled (enabled=False): turns on slow-animations mode via simctl io — makes
    animations deterministic and Maestro flows more stable.
    enabled (enabled=True): restores normal animation speed.
    """
    _ensure_booted(udid)
    flag = "off" if enabled else "on"   # simctl: 'on' = slow animations enabled
    _simctl("io", udid, "setSlowAnimations", flag)


def focus(udid: str) -> None:
    """Bring the Simulator window to the front and activate the app so the user can see it."""
    import subprocess as _sp
    _open_sim_window(udid)
    device_name = _get_device_name(udid)
    _sp.run(["osascript", "-e", f'''tell application "Simulator" to activate
tell application "System Events"
    tell process "Simulator"
        perform action "AXRaise" of (first window whose name contains "{device_name}")
    end tell
end tell'''], capture_output=True, check=False)


def reboot(udid: str) -> None:
    """Reboot the simulator (shutdown + boot)."""
    shutdown(udid)
    import time
    time.sleep(2)
    boot(udid)


def reset_app(udid: str, bundle_id: str) -> None:
    """Force-stop, clear all app data, then relaunch. iOS equivalent of Android clear-data + restart.

    Uses `xcrun simctl privacy reset` to clear permissions, then uninstalls/reinstalls
    is NOT done — instead we use the container reset approach via simctl.
    For a full data wipe, use: simemu erase <slug> (wipes entire simulator).
    This resets the app's data container (Documents, Library, tmp, UserDefaults).
    """
    _ensure_booted(udid)
    # terminate first so the app doesn't fight the container deletion
    _simctl("terminate", udid, bundle_id, check=False)
    import time as _t
    _t.sleep(0.5)
    # delete the app's data container — simctl respawns it clean on next launch
    _simctl("privacy", udid, "reset", "all", bundle_id, check=False)
    result = subprocess.run(
        ["xcrun", "simctl", "get_app_container", udid, bundle_id, "data"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode == 0:
        data_container = result.stdout.strip()
        if data_container:
            import shutil as _sh
            _sh.rmtree(data_container, ignore_errors=True)
    _simctl("launch", udid, bundle_id)


def crash_log(udid: str, bundle_id: Optional[str] = None, since_minutes: int = 60) -> Optional[str]:
    """Return the most recent crash report for the simulator (or a specific app).

    Searches ~/Library/Logs/DiagnosticReports for .crash or .ips files modified
    within the last since_minutes minutes. Returns the raw crash text, or None if
    no recent crashes found.
    """
    import glob as _glob
    import time as _t

    diag = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    if not diag.exists():
        return None

    cutoff = _t.time() - since_minutes * 60
    candidates = []
    for pattern in ("*.crash", "*.ips"):
        for p in diag.glob(pattern):
            if p.stat().st_mtime >= cutoff:
                candidates.append(p)

    if bundle_id:
        # filter to files whose name starts with the app name (bundle_id last component)
        app_name = bundle_id.split(".")[-1].lower()
        candidates = [p for p in candidates if app_name in p.name.lower()]

    if not candidates:
        return None

    newest = max(candidates, key=lambda p: p.stat().st_mtime)
    return newest.read_text(errors="replace")


def biometrics(udid: str, match: bool) -> None:
    """Simulate a Face ID / Touch ID result.

    match=True  → successful authentication
    match=False → failed authentication

    The device must have biometrics enrolled first:
    Simulator → Features → Face ID / Touch ID → Enrolled
    """
    import subprocess as _sp

    _ensure_booted(udid)
    face_action  = "Matching Face"  if match else "Non-Matching Face"
    touch_action = "Matching Touch" if match else "Non-Matching Touch"
    _sp.run(["osascript", "-e", f'''
tell application "System Events"
    tell process "Simulator"
        set frontmost to true
        try
            click menu item "{face_action}" of menu 1 of menu item "Face ID" of menu "Features" of menu bar 1
        on error
            click menu item "{touch_action}" of menu 1 of menu item "Touch ID" of menu "Features" of menu bar 1
        end try
    end tell
end tell'''], capture_output=True, check=False)
