"""
Live visibility reconciliation for simulator/emulator windows.

Derives visible/headless state from actual macOS desktop window state
instead of trusting stale session metadata. Used by the menubar, monitor,
and proof commands to know what's really happening on screen.
"""

from typing import Optional


def _get_all_windows() -> list[dict]:
    """Query macOS for all on-screen windows with owner and bounds."""
    try:
        import importlib
        Quartz = importlib.import_module("Quartz")
        raw = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionAll,
            Quartz.kCGNullWindowID,
        )
    except Exception:
        return []

    results = []
    for w in raw:
        owner = str(w.get("kCGWindowOwnerName") or "")
        name = str(w.get("kCGWindowName") or "")
        bounds = w.get("kCGWindowBounds") or {}
        width = float(bounds.get("Width", 0))
        height = float(bounds.get("Height", 0))
        if width <= 0 or height <= 0:
            continue
        results.append({
            "owner": owner,
            "name": name,
            "onscreen": bool(w.get("kCGWindowIsOnscreen", 0)),
            "layer": int(w.get("kCGWindowLayer", 0)),
            "width": width,
            "height": height,
            "x": float(bounds.get("X", 0)),
            "y": float(bounds.get("Y", 0)),
            "alpha": float(w.get("kCGWindowAlpha", 1.0)),
        })
    return results


def is_simulator_window_visible(device_name: str) -> Optional[bool]:
    """Check if an iOS Simulator window is visible on screen.

    Returns True if visible, False if hidden/minimized, None if no window found.
    """
    windows = _get_all_windows()
    for w in windows:
        if w["owner"] != "Simulator":
            continue
        if device_name.lower() in w["name"].lower():
            return w["onscreen"] and w["alpha"] > 0.01
    return None


def is_emulator_window_visible(avd_name: str) -> Optional[bool]:
    """Check if an Android emulator window is visible on screen.

    Returns True if visible, False if hidden/headless, None if no window found.
    Handles both standard AVDs and Genymotion VMs.
    """
    windows = _get_all_windows()
    avd_lower = avd_name.lower()
    for w in windows:
        owner_lower = w["owner"].lower()
        name_lower = w["name"].lower()
        if avd_lower in name_lower or avd_lower in owner_lower:
            return w["onscreen"] and w["alpha"] > 0.01
        if "android emulator" in owner_lower or "qemu-system" in owner_lower:
            if avd_lower in name_lower:
                return w["onscreen"] and w["alpha"] > 0.01
    return None


def get_session_visibility(session) -> str:
    """Derive visibility state for a session from live desktop state.

    Returns: "visible", "hidden", "no_window", or "parked"
    """
    if session.status == "parked":
        return "parked"
    if session.status in ("expired", "released"):
        return "no_window"
    if session.platform == "macos":
        return "visible"  # macOS apps are always "visible" (native)

    device_name = session.device_name
    if session.platform in ("ios", "watchos", "tvos", "visionos"):
        visible = is_simulator_window_visible(device_name)
    else:
        visible = is_emulator_window_visible(session.sim_id)

    if visible is None:
        return "no_window"
    return "visible" if visible else "hidden"


def reconcile_all_sessions() -> dict[str, str]:
    """Reconcile visibility for all active sessions.

    Returns {session_id: "visible"|"hidden"|"no_window"|"parked"}
    """
    from .session import get_active_sessions, get_all_sessions

    all_sessions = get_all_sessions()
    result = {}
    for sid, session in all_sessions.items():
        if session.status in ("expired", "released"):
            continue
        result[sid] = get_session_visibility(session)
    return result


def get_visibility_summary() -> dict:
    """Return a summary of visibility across all sessions.

    Used by the menubar and status command.
    """
    states = reconcile_all_sessions()
    return {
        "visible": sum(1 for v in states.values() if v == "visible"),
        "hidden": sum(1 for v in states.values() if v == "hidden"),
        "no_window": sum(1 for v in states.values() if v == "no_window"),
        "parked": sum(1 for v in states.values() if v == "parked"),
        "sessions": states,
    }
