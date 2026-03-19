"""
Simulator window management — controls where simulator windows appear.

Modes:
  hidden   — minimize/hide all simulator windows immediately after boot
  space    — move all simulator windows to a dedicated macOS Space (requires yabai)
  corner   — tile all simulator windows in a screen corner
  display  — move all simulator windows to a specific display index
  default  — leave windows wherever macOS puts them

Config: ~/.simemu/config.json  { "window_mode": "hidden", ... }
"""

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from . import state


def _config_path() -> Path:
    return state.config_dir() / "config.json"


def _read_config() -> dict:
    path = _config_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _write_config(config: dict) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2))
    tmp.replace(path)


def get_window_mode() -> str:
    """Return configured window mode (default: 'default')."""
    return _read_config().get("window_mode", "default")


def set_window_mode(mode: str, **kwargs) -> dict:
    """Set the window management mode. Returns the updated config."""
    valid = ("hidden", "space", "corner", "display", "default")
    if mode not in valid:
        raise ValueError(f"Invalid window mode '{mode}'. Use: {', '.join(valid)}")

    config = _read_config()
    config["window_mode"] = mode
    if "display" in kwargs and kwargs["display"] is not None:
        config["window_display"] = int(kwargs["display"])
    if "corner" in kwargs and kwargs["corner"] is not None:
        config["window_corner"] = kwargs["corner"]
    _write_config(config)
    return config


def apply_window_mode(sim_id: str, platform: str, device_name: str) -> None:
    """Apply the configured window mode to a simulator window.

    Called automatically after boot during claim.
    """
    mode = get_window_mode()

    if mode == "default":
        return

    # Give the window time to appear — Simulator.app can take 2-3s after boot
    time.sleep(3.0)

    if mode == "hidden":
        _hide_window(sim_id, platform, device_name)
    elif mode == "space":
        _move_to_space(sim_id, platform, device_name)
    elif mode == "corner":
        config = _read_config()
        corner = config.get("window_corner", "bottom-right")
        _move_to_corner(sim_id, platform, device_name, corner)
    elif mode == "display":
        config = _read_config()
        display_index = config.get("window_display", 2)
        _move_to_display(sim_id, platform, device_name, display_index)


def apply_to_all(platform_filter: str | None = None) -> int:
    """Apply window mode to all currently booted simulators. Returns count."""
    from .session import get_active_sessions
    count = 0
    for sid, session in get_active_sessions().items():
        if platform_filter and session.platform != platform_filter:
            continue
        if session.status == "parked":
            continue
        try:
            apply_window_mode(session.sim_id, session.platform, session.device_name)
            count += 1
        except Exception:
            pass
    return count


# ── mode implementations ─────────────────────────────────────────────────────

def _hide_window(sim_id: str, platform: str, device_name: str) -> None:
    """Minimize/hide the simulator window. Retries once if window isn't found."""
    if platform in ("ios", "watchos", "tvos", "visionos"):
        for attempt in range(2):
            result = subprocess.run([
                "osascript", "-e",
                f'''tell application "System Events"
    tell process "Simulator"
        try
            set miniaturized of (first window whose name contains "{device_name}") to true
            return "ok"
        end try
        return "not found"
    end tell
end tell'''
            ], capture_output=True, text=True, check=False)
            if "ok" in result.stdout:
                return
            if attempt == 0:
                time.sleep(2.0)  # Window might not exist yet
    else:
        # Android emulators booted headless already have no window
        # For Genymotion/windowed, try to minimize
        subprocess.run([
            "osascript", "-e",
            f'''tell application "System Events"
    repeat with proc in (application processes whose name contains "Genymotion" or name contains "qemu")
        try
            set miniaturized of (first window of proc) to true
        end try
    end repeat
end tell'''
        ], capture_output=True, check=False)


def _move_to_space(sim_id: str, platform: str, device_name: str) -> None:
    """Move simulator window to a dedicated Space.

    Uses yabai if available, otherwise falls back to hiding.
    """
    # Check if yabai is available
    result = subprocess.run(["which", "yabai"], capture_output=True)
    if result.returncode != 0:
        # No yabai — fall back to hiding
        _hide_window(sim_id, platform, device_name)
        return

    if platform in ("ios", "watchos", "tvos", "visionos"):
        # Get the window ID via yabai
        try:
            out = subprocess.check_output(
                ["yabai", "-m", "query", "--windows"],
                stderr=subprocess.DEVNULL,
            )
            windows = json.loads(out)
            for w in windows:
                if w.get("app") == "Simulator" and device_name in w.get("title", ""):
                    wid = w["id"]
                    # Move to last space (dedicated sim space)
                    spaces_out = subprocess.check_output(
                        ["yabai", "-m", "query", "--spaces"],
                        stderr=subprocess.DEVNULL,
                    )
                    spaces = json.loads(spaces_out)
                    last_space = max(s["index"] for s in spaces)
                    subprocess.run(
                        ["yabai", "-m", "window", str(wid), "--space", str(last_space)],
                        capture_output=True, check=False,
                    )
                    break
        except Exception:
            _hide_window(sim_id, platform, device_name)


def _move_to_corner(sim_id: str, platform: str, device_name: str, corner: str) -> None:
    """Tile the simulator window in a screen corner."""
    if platform not in ("ios", "watchos", "tvos", "visionos"):
        return  # Android is headless

    # Get screen size
    try:
        out = subprocess.check_output(
            ["osascript", "-e", 'tell application "Finder" to get bounds of window of desktop'],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        parts = [int(x.strip()) for x in out.split(",")]
        screen_w, screen_h = parts[2], parts[3]
    except Exception:
        screen_w, screen_h = 2560, 1440

    # Simulator window size (compact)
    win_w, win_h = 280, 560

    positions = {
        "top-left": (0, 25),
        "top-right": (screen_w - win_w, 25),
        "bottom-left": (0, screen_h - win_h),
        "bottom-right": (screen_w - win_w, screen_h - win_h),
    }
    x, y = positions.get(corner, positions["bottom-right"])

    subprocess.run([
        "osascript", "-e",
        f'''tell application "System Events"
    tell process "Simulator"
        try
            set w to first window whose name contains "{device_name}"
            set position of w to {{{x}, {y}}}
            set size of w to {{{win_w}, {win_h}}}
        end try
    end tell
end tell'''
    ], capture_output=True, check=False)


def _move_to_display(sim_id: str, platform: str, device_name: str, display_index: int) -> None:
    """Move simulator window to a specific display."""
    if platform not in ("ios", "watchos", "tvos", "visionos"):
        return

    # Get display bounds
    try:
        script = f'''
        tell application "Finder"
            set displayCount to count of desktops
            if displayCount >= {display_index} then
                set targetBounds to bounds of window of desktop {display_index}
                return (item 1 of targetBounds) & "," & (item 2 of targetBounds)
            end if
        end tell
        return "0,0"
        '''
        out = subprocess.check_output(
            ["osascript", "-e", script],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        parts = [int(x.strip()) for x in out.split(",")]
        target_x, target_y = parts[0], parts[1]
    except Exception:
        target_x, target_y = 0, 0

    subprocess.run([
        "osascript", "-e",
        f'''tell application "System Events"
    tell process "Simulator"
        try
            set w to first window whose name contains "{device_name}"
            set position of w to {{{target_x + 20}, {target_y + 40}}}
        end try
    end tell
end tell'''
    ], capture_output=True, check=False)
