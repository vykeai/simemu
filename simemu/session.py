"""
Session-based resource manager for simemu v2.

Agents interact with sessions (opaque IDs) instead of device slugs/UDIDs.
Sessions manage the full device lifecycle: claim → active → idle → parked → expired.

State file: ~/.simemu/sessions.json (separate from legacy state.json)
"""

import fcntl
import json
import os
import platform as _platform_mod
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import state, ios, android, device
from .discover import find_best_device
from . import window as window_mgr


# ── timeouts (seconds) ───────────────────────────────────────────────────────

IDLE_TIMEOUT = 20 * 60        # active → idle after 20min
PARK_TIMEOUT = 40 * 60        # idle → parked after 40min more (60min total)
EXPIRE_TIMEOUT = 2 * 60 * 60  # parked → expired after 2hr total idle

# Default memory budget in MB
DEFAULT_MEMORY_BUDGET_MB = 16 * 1024  # 16GB

# Per-device estimated memory usage in MB
_DEVICE_MEMORY_MB = {
    "ios": 2048,
    "android": 3072,
    "macos": 0,  # native — no VM overhead
}


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class ClaimSpec:
    platform: str                        # "ios" | "android" | "macos"
    form_factor: str = "phone"           # "phone" | "tablet" | "watch" | "tv" | "vision"
    os_version: str | None = None        # requested version or None (any)
    real_device: bool = False
    label: str = ""
    visible: bool = False                # if True, keep window visible (default: headless)

    def to_claim_command(self) -> str:
        """Reconstruct the CLI command to re-claim with identical parameters."""
        parts = ["simemu", "claim", self.platform]
        if self.os_version:
            parts += ["--version", self.os_version]
        if self.form_factor != "phone":
            parts += ["--form-factor", self.form_factor]
        if self.real_device:
            parts.append("--real")
        if self.visible:
            parts.append("--visible")
        if self.label:
            parts += ["--label", f"'{self.label}'"]
        return " ".join(parts)


@dataclass
class Session:
    session_id: str                      # "s-" + 6 hex chars
    platform: str                        # "ios" | "android" | "macos"
    form_factor: str                     # "phone" | "tablet" | "watch" | "tv" | "vision"
    os_version: str | None               # requested version or None
    real_device: bool
    label: str
    status: str                          # "active" | "idle" | "parked" | "expired" | "released"
    sim_id: str                          # internal device ID (opaque to agent)
    device_name: str                     # internal device name
    agent: str                           # from SIMEMU_AGENT env
    created_at: str                      # ISO timestamp
    heartbeat_at: str                    # ISO timestamp — last activity
    expires_at: str | None = None        # ISO timestamp (computed)
    resolved_os_version: str | None = None  # actual OS version of assigned device

    # Stored claim spec for error recovery messages
    claim_platform: str = ""
    claim_form_factor: str = "phone"
    claim_os_version: str | None = None
    claim_real_device: bool = False
    claim_label: str = ""

    def to_agent_json(self) -> dict:
        """Return the JSON visible to agents (no internal device details)."""
        return {
            "session": self.session_id,
            "platform": self.platform,
            "form_factor": self.form_factor,
            "os_version": self.resolved_os_version or self.os_version or "latest",
            "status": self.status,
            "label": self.label,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }

    def claim_spec(self) -> ClaimSpec:
        """Reconstruct the ClaimSpec used to create this session."""
        return ClaimSpec(
            platform=self.claim_platform or self.platform,
            form_factor=self.claim_form_factor or self.form_factor,
            os_version=self.claim_os_version or self.os_version,
            real_device=self.claim_real_device or self.real_device,
            label=self.claim_label or self.label,
        )

    def reclaim_command(self) -> str:
        """Return the exact CLI command to re-claim this session."""
        return self.claim_spec().to_claim_command()


def _gen_session_id() -> str:
    return f"s-{secrets.token_hex(3)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_expires_at(status: str, heartbeat_at: str) -> str:
    """Compute when a session will next transition based on current status."""
    from datetime import timedelta
    hb = datetime.fromisoformat(heartbeat_at)
    if status == "active":
        return (hb + timedelta(seconds=IDLE_TIMEOUT)).isoformat()
    elif status == "idle":
        return (hb + timedelta(seconds=IDLE_TIMEOUT + PARK_TIMEOUT)).isoformat()
    elif status == "parked":
        return (hb + timedelta(seconds=EXPIRE_TIMEOUT)).isoformat()
    return heartbeat_at


# ── state persistence ────────────────────────────────────────────────────────

def _sessions_file() -> Path:
    return state.state_dir() / "sessions.json"


def _sessions_lock_file() -> Path:
    return state.state_dir() / "sessions.lock"


@contextmanager
def _locked_sessions():
    base = state.state_dir()
    base.mkdir(parents=True, exist_ok=True)
    lock_fd = open(_sessions_lock_file(), "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        data = _read_sessions_raw()
        pending: list[dict] = []

        def save(new_data: dict):
            pending.append(new_data)

        yield data, save

        if pending:
            _write_sessions_raw(pending[-1])
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _read_sessions_raw() -> dict:
    sf = _sessions_file()
    if sf.exists():
        try:
            return json.loads(sf.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"sessions": {}}


def _write_sessions_raw(data: dict):
    sf = _sessions_file()
    tmp = sf.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(sf)


def _session_from_dict(d: dict) -> Session:
    # Handle any extra keys gracefully
    known_fields = {f.name for f in Session.__dataclass_fields__.values()}
    filtered = {k: v for k, v in d.items() if k in known_fields}
    return Session(**filtered)


# ── public API ───────────────────────────────────────────────────────────────

def claim(spec: ClaimSpec) -> Session:
    """Find the best available device matching spec, boot it, and return a session."""
    state.check_maintenance()

    agent = os.environ.get("SIMEMU_AGENT") or f"pid-{os.getpid()}"
    now = _now_iso()

    # T-31: Rate limiting
    _check_rate_limit(agent)

    # T-26: Progress feedback
    import sys as _sys
    print(f"Claiming {spec.platform} {spec.form_factor}...", file=_sys.stderr, flush=True)

    # Generate session ID
    session_id = _gen_session_id()

    if spec.platform == "macos":
        # macOS apps run natively — no simulator/emulator needed
        mac_ver = _platform_mod.mac_ver()[0] or "unknown"
        session = Session(
            session_id=session_id,
            platform="macos",
            form_factor="desktop",
            os_version=spec.os_version,
            real_device=True,
            label=spec.label,
            status="active",
            sim_id="macos-native",
            device_name=mac_ver,
            agent=agent,
            created_at=now,
            heartbeat_at=now,
            resolved_os_version=mac_ver,
            claim_platform="macos",
            claim_form_factor="desktop",
            claim_os_version=spec.os_version,
            claim_real_device=True,
            claim_label=spec.label,
        )
        session.expires_at = _compute_expires_at("active", now)

        with _locked_sessions() as (data, save):
            # Allow multiple macOS sessions (different apps on same machine)
            data["sessions"][session_id] = asdict(session)
            save(data)

        return session

    # Check memory budget before claiming
    _enforce_memory_budget_if_needed(spec.platform)

    # Find best matching device
    sim = find_best_device(spec)

    # Boot the device if not already booted and not a real device
    if not sim.real_device and not sim.booted:
        print(f"Booting {sim.device_name}...", file=_sys.stderr, flush=True)
        if sim.platform in ("ios", "watchos", "tvos", "visionos"):
            ios.boot(sim.sim_id)
        else:
            android.boot(sim.sim_id, headless=True)
        print(f"Ready.", file=_sys.stderr, flush=True)

    # Apply window management — headless by default unless --visible
    if not sim.real_device and not spec.visible:
        try:
            window_mgr.apply_window_mode(sim.sim_id, sim.platform, sim.device_name)
        except Exception:
            pass  # window management is best-effort

    # Create the session
    session = Session(
        session_id=session_id,
        platform=spec.platform,
        form_factor=spec.form_factor,
        os_version=spec.os_version,
        real_device=sim.real_device,
        label=spec.label,
        status="active",
        sim_id=sim.sim_id,
        device_name=sim.device_name,
        agent=agent,
        created_at=now,
        heartbeat_at=now,
        resolved_os_version=sim.runtime,
        claim_platform=spec.platform,
        claim_form_factor=spec.form_factor,
        claim_os_version=spec.os_version,
        claim_real_device=spec.real_device,
        claim_label=spec.label,
    )
    session.expires_at = _compute_expires_at("active", now)

    # Persist session — check for duplicate sim_id under lock
    with _locked_sessions() as (data, save):
        # Reject if another active session already has this device
        for existing_id, existing in data["sessions"].items():
            if (existing.get("sim_id") == sim.sim_id
                    and existing.get("status") in ("active", "idle", "parked")):
                raise SessionError(
                    error="device_already_claimed",
                    session=existing_id,
                    hint=f"Device '{sim.device_name}' is already claimed by session {existing_id}. "
                         f"Release it first: simemu do {existing_id} done",
                )
        data["sessions"][session_id] = asdict(session)
        save(data)

    return session


def get_session(session_id: str) -> Session | None:
    """Return a session by ID, or None if not found."""
    data = _read_sessions_raw()
    raw = data["sessions"].get(session_id)
    if raw is None:
        return None
    return _session_from_dict(raw)


def require_session(session_id: str) -> Session:
    """Return a session by ID, or raise with actionable error."""
    session = get_session(session_id)
    if session is None:
        raise SessionError(
            error="session_not_found",
            session=session_id,
            hint=f"Session '{session_id}' does not exist. Claim a new device with: simemu claim <platform>",
        )
    if session.status == "expired":
        raise SessionError(
            error="session_expired",
            session=session_id,
            hint=f"Session expired after inactivity. Re-claim with: {session.reclaim_command()}",
            expired_at=session.expires_at,
        )
    if session.status == "released":
        raise SessionError(
            error="session_released",
            session=session_id,
            hint=f"Session was released. Re-claim with: {session.reclaim_command()}",
        )
    return session


def touch(session_id: str) -> Session:
    """Update heartbeat and ensure session is active. Re-boots parked devices."""
    session = require_session(session_id)
    now = _now_iso()

    reboot_needed = session.status == "parked"

    # macOS sessions are native — never need rebooting
    if session.platform == "macos":
        reboot_needed = False

    # Android session claims can occasionally outlive the underlying emulator
    # process. If the session is still marked active/idle but the VM is gone,
    # heal by booting it again before dispatching the next command.
    if (
        not reboot_needed
        and not session.real_device
        and session.platform == "android"
        and android.get_android_serial(session.sim_id, retries=2, delay=0.5) is None
    ):
        reboot_needed = True

    # Re-boot if parked
    if reboot_needed:
        if not session.real_device:
            if session.platform in ("ios", "watchos", "tvos", "visionos"):
                ios.boot(session.sim_id)
            else:
                android.boot(session.sim_id, headless=True)

    # Update state
    with _locked_sessions() as (data, save):
        if session_id in data["sessions"]:
            data["sessions"][session_id]["heartbeat_at"] = now
            data["sessions"][session_id]["status"] = "active"
            data["sessions"][session_id]["expires_at"] = _compute_expires_at("active", now)
            save(data)

    session.heartbeat_at = now
    session.status = "active"
    session.expires_at = _compute_expires_at("active", now)
    return session


def renew(session_id: str, hours: float | None = None) -> Session:
    """Proactively extend a session before it expires."""
    session = require_session(session_id)
    now = _now_iso()

    with _locked_sessions() as (data, save):
        if session_id in data["sessions"]:
            data["sessions"][session_id]["heartbeat_at"] = now
            data["sessions"][session_id]["status"] = "active"
            data["sessions"][session_id]["expires_at"] = _compute_expires_at("active", now)
            save(data)

    session.heartbeat_at = now
    session.status = "active"
    session.expires_at = _compute_expires_at("active", now)
    return session


def release(session_id: str) -> Session:
    """Immediately release a session and free the device."""
    session = require_session(session_id)

    if not session.real_device:
        try:
            if session.platform in ("ios", "watchos", "tvos", "visionos"):
                ios.erase(session.sim_id)
            else:
                android.erase(session.sim_id)
        except Exception:
            try:
                if session.platform in ("ios", "watchos", "tvos", "visionos"):
                    ios.shutdown(session.sim_id)
                else:
                    android.shutdown(session.sim_id)
            except Exception:
                pass

        try:
            window_mgr.apply_window_mode(session.sim_id, session.platform, session.device_name)
        except Exception:
            pass

    with _locked_sessions() as (data, save):
        if session_id in data["sessions"]:
            data["sessions"][session_id]["status"] = "released"
            data["sessions"][session_id]["visible"] = False
            data["sessions"][session_id].pop("last_build_artifact", None)
            save(data)

    session.status = "released"
    return session


def get_all_sessions() -> dict[str, Session]:
    """Return all sessions (including expired/released for history)."""
    data = _read_sessions_raw()
    return {
        sid: _session_from_dict(raw)
        for sid, raw in data["sessions"].items()
    }


def get_active_sessions() -> dict[str, Session]:
    """Return only active/idle/parked sessions."""
    return {
        sid: s for sid, s in get_all_sessions().items()
        if s.status in ("active", "idle", "parked")
    }


# ── do command dispatch ──────────────────────────────────────────────────────

_COMMAND_HELP: dict[str, str] = {
    # Session lifecycle
    "boot":             "Wake a parked session and re-boot the device",
    "show":             "Make the simulator window visible",
    "hide":             "Hide the simulator window (headless)",
    "renew":            "Extend session before it expires",
    "done":             "Release the session and free the device",
    "reboot":           "Restart the simulator",
    "present":          "Present the iOS simulator window in a canonical position",
    "stabilize":        "Stabilize the iOS simulator window for reliable interaction",
    # App management
    "install":          "Install .app/.ipa (iOS) or .apk (Android)",
    "launch":           "Launch app by bundle ID or package name",
    "terminate":        "Force-stop a running app",
    "uninstall":        "Remove an installed app",
    "reset-app":        "Terminate + uninstall + reinstall + launch",
    "clear-data":       "Clear app data (Android) or hint to reinstall (iOS)",
    "clean-retry":      "Clear Android app data and relaunch from a clean state",
    "grant-all":        "Grant ALL permissions preemptively",
    "app-info":         "Show app version, data size, container path",
    "verify-install":   "Verify Android package registration after install",
    "repair-install":   "Cold-repair Android package-manager state and reinstall",
    "app-container":    "Get the app's data container path",
    "is-running":       "Check if an app is running (returns bool + pid)",
    "foreground-app":   "Return which app is currently in foreground",
    # UI interaction
    "a11y-tap":         "Tap element by accessibility label — HEADLESS via Maestro",
    "tap":              "Tap at x y coordinates (needs visible window on iOS)",
    "swipe":            "Swipe from x1 y1 to x2 y2",
    "long-press":       "Long-press at x y coordinates",
    "scroll":           "Scroll up/down/left/right",
    "back":             "Go back (edge swipe iOS, back button Android)",
    "home":             "Go to home screen",
    "key":              "Press a hardware key (home, lock, volume, etc.)",
    "input":            "Type text into focused field",
    "type-submit":      "Type text and press Enter",
    "shake":            "Shake gesture (opens React Native dev menu)",
    # Capture & proof
    "screenshot":       "Take a screenshot (-o path, --max-size px)",
    "deeplink-proof":   "Open URL + wait 3s + screenshot in one command",
    "wait-for-render":  "Wait N seconds then screenshot",
    "video-start":      "Start screen recording (-o path)",
    "video-stop":       "Stop screen recording (pass pid from video-start)",
    "log-crash":        "Get recent crash logs",
    # Navigation
    "url":              "Open a URL or deep link",
    "maestro":          "Run a Maestro flow YAML",
    # Alerts & permissions
    "dismiss-alert":    "Dismiss any visible system alert",
    "accept-alert":     "Tap Allow/OK on a system alert",
    "deny-alert":       "Tap Don't Allow/Cancel on a system alert",
    "auto-dismiss":     "Accept pending alerts + disable animation + reset privacy",
    # Device state
    "appearance":       "Set light or dark mode",
    "rotate":           "Set orientation (portrait, landscape, left, right)",
    "location":         "Set GPS coordinates (lat lng)",
    "status-bar":       "Override status bar (--time, --battery, --wifi, --clear)",
    "biometrics":       "Simulate Face ID / fingerprint (match or fail)",
    "network":          "Set network mode: offline/slow/normal (Android)",
    # Clipboard
    "clipboard-set":    "Copy text to device clipboard",
    "clipboard-get":    "Read device clipboard (iOS only)",
    # Files
    "push":             "Push file to Android emulator",
    "pull":             "Pull file from Android emulator",
    "add-media":        "Add photo/video to device library",
    "contacts-import":  "Import contacts from VCF file",
    # System
    "keychain-reset":   "Clear iOS keychain",
    "icloud-sync":      "Trigger iCloud sync (iOS only)",
    "clone":            "Clone an iOS simulator",
    "font-size":        "Set accessibility font size (Android)",
    "reduce-motion":    "Toggle reduce motion / animations (Android)",
    "notifications-clear": "Clear notification center (Android)",
    "a11y-tree":        "Dump accessibility hierarchy (Android)",
    # Info
    "env":              "Show device info (UDID, serial, OS version)",
    "help":             "Show this help",
}


# ── macOS native command dispatch ────────────────────────────────────────────

def _do_macos_command(session: Session, command: str, args: list[str]) -> dict:
    """Handle commands for macOS native sessions (no simulator)."""
    import subprocess as _sp

    if command == "install":
        if not args:
            raise RuntimeError("Usage: simemu do <session> install <path-to-.app>")
        app_path = args[0]
        # Copy .app bundle to staging area, or open it directly
        if app_path.endswith(".app"):
            staging = Path("/tmp/simemu-apps")
            staging.mkdir(parents=True, exist_ok=True)
            _sp.run(["cp", "-R", app_path, str(staging)], check=True)
            return {"status": "installed", "app": app_path, "location": str(staging / Path(app_path).name)}
        else:
            _sp.run(["open", app_path], check=True)
            return {"status": "installed", "app": app_path}

    elif command == "launch":
        if not args:
            raise RuntimeError("Usage: simemu do <session> launch <bundle-id>")
        bundle_id = args[0]
        _sp.run(["open", "-b", bundle_id], check=True)
        return {"status": "launched", "app": bundle_id}

    elif command == "screenshot":
        output = None
        i = 0
        while i < len(args):
            if args[i] in ("-o", "--output") and i + 1 < len(args):
                output = args[i + 1]
                i += 2
            else:
                i += 1
        if not output:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
            out_dir.mkdir(parents=True, exist_ok=True)
            output = str(out_dir / f"{session.session_id}_{ts}.png")

        # Try to capture a specific window by bundle ID, fall back to full screen
        if args and not args[0].startswith("-"):
            bundle_id = args[0]
            # Get window ID via osascript
            result = _sp.run([
                "osascript", "-e",
                f'tell application "System Events" to tell process (name of first application process '
                f'whose bundle identifier is "{bundle_id}") to set wid to id of first window\n'
                f'return wid',
            ], capture_output=True, text=True, check=False)
            window_id = result.stdout.strip()
            if window_id and window_id.isdigit():
                _sp.run(["screencapture", "-l", window_id, output], check=True)
                return {"status": "captured", "path": output, "window_id": int(window_id)}

        # Fallback: capture entire screen
        _sp.run(["screencapture", "-x", output], check=True)
        return {"status": "captured", "path": output}

    elif command == "terminate":
        if not args:
            raise RuntimeError("Usage: simemu do <session> terminate <bundle-id>")
        bundle_id = args[0]
        # Graceful quit via osascript, fall back to pkill
        result = _sp.run([
            "osascript", "-e",
            f'tell application id "{bundle_id}" to quit',
        ], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            _sp.run(["pkill", "-f", bundle_id], capture_output=True, check=False)
        return {"status": "terminated", "app": bundle_id}

    elif command == "url":
        if not args:
            raise RuntimeError("Usage: simemu do <session> url <url>")
        url = args[0]
        _sp.run(["open", url], check=True)
        return {"status": "opened", "url": url}

    elif command == "tap":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> tap <x> <y>")
        x, y = float(args[0]), float(args[1])
        try:
            from Quartz import (  # type: ignore[import-untyped]
                CGEventCreateMouseEvent, CGEventPost,
                kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
                kCGHIDEventTap,
            )
            from Quartz import CGPointMake  # type: ignore[import-untyped]

            point = CGPointMake(x, y)
            move = CGEventCreateMouseEvent(None, kCGEventMouseMoved, point, 0)
            CGEventPost(kCGHIDEventTap, move)
            down = CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, point, 0)
            CGEventPost(kCGHIDEventTap, down)
            up = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, 0)
            CGEventPost(kCGHIDEventTap, up)
            return {"status": "tapped", "x": x, "y": y}
        except ImportError:
            return {"status": "unsupported", "platform": "macos",
                    "hint": "Tap requires pyobjc-framework-Quartz: pip install pyobjc-framework-Quartz"}

    elif command == "a11y-tap":
        return {"status": "unsupported", "platform": "macos",
                "hint": "Maestro does not support macOS. Use 'tap' with coordinates, "
                        "or AppleScript: 'tell application \"System Events\" to click button \"Name\" ...'."}

    elif command in ("boot", "show", "hide", "renew", "done"):
        # These are handled by the main do_command dispatcher before reaching here.
        # If we get here somehow, they are no-ops for macOS.
        return {"status": "ok", "platform": "macos", "command": command}

    else:
        _MACOS_SUPPORTED = {"install", "launch", "screenshot", "terminate", "url", "tap"}
        return {
            "status": "unsupported",
            "platform": "macos",
            "command": command,
            "hint": f"'{command}' is not available for macOS native sessions. "
                    f"Supported commands: {', '.join(sorted(_MACOS_SUPPORTED))}. "
                    f"macOS apps run natively — most simulator/emulator commands do not apply.",
        }


def do_command(session_id: str, command: str, args: list[str]) -> dict | None:
    """Execute a command on a session. Auto-extends heartbeat."""

    # T-12: Help command
    if command == "help":
        categories = {
            "Session": ["boot", "show", "hide", "renew", "done", "reboot", "present", "stabilize"],
            "App": ["install", "launch", "terminate", "uninstall", "reset-app", "clear-data", "clean-retry",
                     "grant-all", "app-info", "verify-install", "repair-install", "app-container",
                     "is-running", "foreground-app"],
            "UI": ["a11y-tap", "tap", "swipe", "long-press", "scroll", "back", "home",
                   "key", "input", "type-submit", "shake"],
            "Capture": ["screenshot", "deeplink-proof", "wait-for-render", "video-start",
                        "video-stop", "log-crash"],
            "Navigate": ["url", "maestro"],
            "Alerts": ["dismiss-alert", "accept-alert", "deny-alert", "auto-dismiss"],
            "Device": ["appearance", "rotate", "location", "status-bar", "biometrics",
                       "network", "clipboard-set", "clipboard-get"],
            "Files": ["push", "pull", "add-media", "contacts-import"],
            "System": ["keychain-reset", "icloud-sync", "clone", "font-size",
                       "reduce-motion", "notifications-clear", "a11y-tree", "env"],
        }
        result: dict = {"commands": {}}
        for cat, cmds in categories.items():
            result["commands"][cat] = {c: _COMMAND_HELP.get(c, "") for c in cmds}
        return result

    if command == "done":
        session = release(session_id)
        return {"session": session_id, "status": "released"}

    if command == "boot":
        # Explicitly wake a parked session — touch() already does this,
        # but agents expect an explicit boot command
        session = touch(session_id)
        return session.to_agent_json()

    if command == "present":
        session = touch(session_id)
        if session.real_device or session.platform not in ("ios", "watchos", "tvos", "visionos"):
            return {
                "status": "unsupported",
                "platform": session.platform,
                "hint": "present is currently available for iOS-family simulators only.",
            }
        result = ios.present(session.sim_id)
        with _locked_sessions() as (data, save):
            if session_id in data["sessions"]:
                data["sessions"][session_id]["visible"] = True
                save(data)
        return result

    if command == "stabilize":
        session = touch(session_id)
        if session.real_device or session.platform not in ("ios", "watchos", "tvos", "visionos"):
            return {
                "status": "unsupported",
                "platform": session.platform,
                "hint": "stabilize is currently available for iOS-family simulators only.",
            }
        return ios.stabilize(session.sim_id)

    if command in ("visible", "show"):
        session = touch(session_id)
        if not session.real_device and session.platform in ("ios", "watchos", "tvos", "visionos"):
            import subprocess
            subprocess.run([
                "osascript", "-e",
                f'''tell application "Simulator" to activate
tell application "System Events"
    tell process "Simulator"
        try
            set w to first window whose name contains "{session.device_name}"
            set miniaturized of w to false
            perform action "AXRaise" of w
        end try
    end tell
end tell'''
            ], capture_output=True, check=False)
        # Persist visibility state
        with _locked_sessions() as (data, save):
            if session_id in data["sessions"]:
                data["sessions"][session_id]["visible"] = True
                save(data)
        return {"session": session_id, "status": "visible", "device": session.device_name}

    if command in ("invisible", "hide"):
        session = touch(session_id)
        if not session.real_device:
            window_mgr.apply_window_mode(session.sim_id, session.platform, session.device_name)
        with _locked_sessions() as (data, save):
            if session_id in data["sessions"]:
                data["sessions"][session_id]["visible"] = False
                save(data)
        return {"session": session_id, "status": "invisible", "device": session.device_name}

    if command == "renew":
        hours = None
        if "--hours" in args:
            idx = args.index("--hours")
            if idx + 1 < len(args):
                hours = float(args[idx + 1])
        session = renew(session_id, hours=hours)
        return session.to_agent_json()

    # All other commands: touch heartbeat, then dispatch
    session = touch(session_id)
    sim_id = session.sim_id
    platform = session.platform
    is_real = session.real_device

    # Update HUD — only for visible sessions (hidden = no overlay needed)
    _is_visible = False
    with _locked_sessions() as (data, save):
        sess_data = data["sessions"].get(session_id, {})
        _is_visible = sess_data.get("visible", False)

    if _is_visible and platform in ("ios", "watchos", "tvos", "visionos"):
        ios._hud_send({
            "mode": "critical",
            "title": "SIMEMU",
            "badge": command.upper(),
            "action": f"{command} on {session.device_name}",
            "detail": f"{session.label}" if session.label else f"Session {session_id}",
            "task": f"simemu do {session_id} {command} {' '.join(args[:2])}".strip(),
            "platform": platform,
            "screen": session.device_name,
            "scenario": command,
        })

    # ── macOS native command dispatch ──────────────────────────────────────
    if platform == "macos":
        return _do_macos_command(session, command, args)

    if command == "install":
        if not args:
            # Auto-install: use the last build artifact
            app_path = _get_build_artifact(session.session_id)
            if not app_path:
                raise RuntimeError(
                    "Usage: simemu do <session> install <path-to-app>\n"
                    "Or run 'simemu do <session> build' first for auto-install."
                )
        else:
            app_path = args[0]
        if is_real and platform == "ios":
            device.ios_install(sim_id, app_path)
        elif platform in ("ios", "watchos", "tvos", "visionos"):
            ios.install(sim_id, app_path)
        else:
            android.install(sim_id, app_path)
        return {"status": "installed", "app": app_path}

    elif command == "launch":
        if not args:
            raise RuntimeError("Usage: simemu do <session> launch <bundle-or-package>")
        bundle = args[0]
        extra = args[1:]
        if is_real and platform == "ios":
            device.ios_launch(sim_id, bundle)
        elif platform in ("ios", "watchos", "tvos", "visionos"):
            ios.launch(sim_id, bundle, extra)
        else:
            android.launch(sim_id, bundle, extra)
            # Verify the right package is foregrounded
            actual_fg = android.foreground_app(sim_id)
            expected_pkg = bundle.split("/", 1)[0]
            if actual_fg and actual_fg != expected_pkg:
                import sys as _sys
                print(json.dumps({
                    "diagnostic": "android_launch_foreground_mismatch",
                    "expected": expected_pkg,
                    "actual": actual_fg,
                }), file=_sys.stderr, flush=True)
        launched_pkg = bundle.split("/", 1)[0]
        with _locked_sessions() as (data, save):
            if session_id in data["sessions"]:
                data["sessions"][session_id]["last_app"] = launched_pkg
                save(data)
        update_provenance(session_id, last_app=launched_pkg, last_launch_args=extra[:3])
        return {"status": "launched", "app": bundle}

    elif command == "tap":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> tap <x> <y>")
        x, y = float(args[0]), float(args[1])
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.tap(sim_id, x, y)
        else:
            android.tap(sim_id, x, y)
        return {"status": "tapped", "x": x, "y": y}

    elif command == "swipe":
        if len(args) < 4:
            raise RuntimeError("Usage: simemu do <session> swipe <x1> <y1> <x2> <y2> [--duration ms]")
        x1, y1, x2, y2 = float(args[0]), float(args[1]), float(args[2]), float(args[3])
        duration = 300
        if "--duration" in args:
            idx = args.index("--duration")
            if idx + 1 < len(args):
                duration = int(args[idx + 1])
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.swipe(sim_id, x1, y1, x2, y2, duration=duration / 1000.0)
        else:
            android.swipe(sim_id, x1, y1, x2, y2, duration=duration)
        return {"status": "swiped", "from": [x1, y1], "to": [x2, y2]}

    elif command == "screenshot":
        output = None
        fmt = "png"
        max_size = None
        i = 0
        while i < len(args):
            if args[i] in ("-o", "--output") and i + 1 < len(args):
                output = args[i + 1]
                i += 2
            elif args[i] in ("-f", "--format") and i + 1 < len(args):
                fmt = args[i + 1]
                i += 2
            elif args[i] == "--max-size" and i + 1 < len(args):
                max_size = int(args[i + 1])
                i += 2
            else:
                i += 1

        # T-25: Default max-size from env
        if max_size is None and "SIMEMU_SCREENSHOT_MAX_SIZE" in os.environ:
            max_size = int(os.environ["SIMEMU_SCREENSHOT_MAX_SIZE"])

        if not output:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
            out_dir.mkdir(parents=True, exist_ok=True)
            output = str(out_dir / f"{session_id}_{ts}.{fmt}")

        if is_real and platform == "ios":
            device.ios_screenshot(sim_id, output, max_size=max_size)
        elif platform in ("ios", "watchos", "tvos", "visionos"):
            ios.screenshot(sim_id, output, fmt=fmt if fmt != "png" else None, max_size=max_size)
        else:
            android.screenshot(sim_id, output)
        update_provenance(session_id, last_screenshot=output)
        return {"status": "captured", "path": output}

    elif command == "maestro":
        if not args:
            raise RuntimeError("Usage: simemu do <session> maestro <flow.yaml> [extra...]")
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            device_id = sim_id
        else:
            from .discover import get_android_serial
            device_id = get_android_serial(sim_id)
            if not device_id:
                raise RuntimeError(
                    f"Android emulator is not running. "
                    f"Re-claim with: {session.reclaim_command()}"
                )
        flow_files = []
        extra_args = []
        for a in args:
            if a.endswith(".yaml") or a.endswith(".yml"):
                flow_files.append(a)
            else:
                extra_args.append(a)
        if not flow_files:
            flow_files = [args[0]]
            extra_args = args[1:]

        cmd = ["maestro", "--device", device_id, "test"] + flow_files + extra_args
        result = _sp.run(cmd)
        if result.returncode != 0:
            return {"status": "failed", "exit_code": result.returncode}
        return {"status": "passed"}

    elif command == "url":
        if not args:
            raise RuntimeError("Usage: simemu do <session> url <url>")
        url = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.open_url(sim_id, url)
            expected_bundle = None
            with _locked_sessions() as (data, save):
                expected_bundle = data["sessions"].get(session_id, {}).get("last_app")
            if expected_bundle:
                handoff_ok = ios.complete_open_url_handoff(sim_id, expected_bundle)
                if not handoff_ok:
                    # Diagnose: what IS in the foreground?
                    actual_fg = ios.foreground_app(sim_id)
                    app_running = ios.is_app_running(sim_id, expected_bundle)
                    if app_running and actual_fg != expected_bundle:
                        # App launched but sheet on top — try one more aggressive dismiss
                        ios.accept_open_app_alert(sim_id, attempts=6, delay=0.3)
                        handoff_ok = ios.wait_for_foreground_app(sim_id, expected_bundle, timeout=3.0)
                    if not handoff_ok:
                        diag = {
                            "expected": expected_bundle,
                            "actual_foreground": actual_fg,
                            "app_running": app_running,
                            "url": url,
                        }
                        hint = (
                            f"App '{expected_bundle}' is running but stuck behind a confirmation sheet"
                            if app_running
                            else f"App '{expected_bundle}' never launched — still on SpringBoard or system UI"
                        )
                        raise RuntimeError(
                            f"URL handoff failed: {hint}.\n"
                            f"Diagnostics: {json.dumps(diag)}"
                        )
        else:
            expected_package = None
            with _locked_sessions() as (data, save):
                expected_package = data["sessions"].get(session_id, {}).get("last_app")
            android.open_url(sim_id, url, expected_package=expected_package)
            # Verify foreground after URL open
            if expected_package:
                actual_fg = android.foreground_app(sim_id)
                if actual_fg and actual_fg != expected_package:
                    diag = {
                        "expected": expected_package,
                        "actual_foreground": actual_fg,
                        "url": url,
                    }
                    raise RuntimeError(
                        f"URL opened but '{expected_package}' is not foreground on Android. "
                        f"Foreground: '{actual_fg}'. Another app may have intercepted the URL.\n"
                        f"Diagnostics: {json.dumps(diag)}"
                    )
        update_provenance(session_id, last_url=url, last_deep_link=url)
        return {"status": "opened", "url": url}

    elif command == "terminate":
        if not args:
            raise RuntimeError("Usage: simemu do <session> terminate <bundle-or-package>")
        bundle = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.terminate(sim_id, bundle)
        else:
            android.terminate(sim_id, bundle)
        return {"status": "terminated", "app": bundle}

    elif command == "uninstall":
        if not args:
            raise RuntimeError("Usage: simemu do <session> uninstall <bundle-or-package>")
        bundle = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.uninstall(sim_id, bundle)
        else:
            android.uninstall(sim_id, bundle)
        return {"status": "uninstalled", "app": bundle}

    elif command == "input":
        if not args:
            raise RuntimeError("Usage: simemu do <session> input <text>")
        text = " ".join(args)
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.input_text(sim_id, text)
        else:
            android.input_text(sim_id, text)
        return {"status": "input", "text": text}

    elif command == "long-press":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> long-press <x> <y> [--duration ms]")
        x, y = float(args[0]), float(args[1])
        duration = 1000
        if "--duration" in args:
            idx = args.index("--duration")
            if idx + 1 < len(args):
                duration = int(args[idx + 1])
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.long_press(sim_id, x, y, duration=duration / 1000.0)
        else:
            android.long_press(sim_id, x, y, duration=duration)
        return {"status": "long-pressed", "x": x, "y": y}

    elif command == "key":
        if not args:
            raise RuntimeError("Usage: simemu do <session> key <key-name>")
        key_name = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.key(sim_id, key_name)
        else:
            android.key(sim_id, key_name)
        return {"status": "key_pressed", "key": key_name}

    elif command == "appearance":
        if not args:
            raise RuntimeError("Usage: simemu do <session> appearance <light|dark>")
        mode = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.set_appearance(sim_id, mode)
        else:
            android.set_appearance(sim_id, mode)
        return {"status": "set", "appearance": mode}

    elif command == "rotate":
        if not args:
            raise RuntimeError("Usage: simemu do <session> rotate <portrait|landscape|left|right>")
        orientation = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.rotate(sim_id, orientation)
        else:
            android.rotate(sim_id, orientation)
        return {"status": "rotated", "orientation": orientation}

    elif command == "location":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> location <lat> <lng>")
        lat, lng = float(args[0]), float(args[1])
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.location(sim_id, lat, lng)
        else:
            android.location(sim_id, lat, lng)
        return {"status": "set", "lat": lat, "lng": lng}

    elif command == "push":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> push <local> <remote>")
        if platform != "android":
            raise RuntimeError("push is Android only")
        android.push(sim_id, args[0], args[1])
        return {"status": "pushed", "remote": args[1]}

    elif command == "pull":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> pull <remote> <local>")
        if platform != "android":
            raise RuntimeError("pull is Android only")
        android.pull(sim_id, args[0], args[1])
        return {"status": "pulled", "local": args[1]}

    elif command == "add-media":
        if not args:
            raise RuntimeError("Usage: simemu do <session> add-media <file>")
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.add_media(sim_id, args[0])
        else:
            android.add_media(sim_id, args[0])
        return {"status": "added", "file": args[0]}

    elif command == "shake":
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.shake(sim_id)
        else:
            android.shake(sim_id)
        return {"status": "shaken"}

    elif command == "status-bar":
        # Parse status-bar flags
        time_str = None
        battery = None
        wifi = None
        network = None
        clear = False
        i = 0
        while i < len(args):
            if args[i] == "--time" and i + 1 < len(args):
                time_str = args[i + 1]
                i += 2
            elif args[i] == "--battery" and i + 1 < len(args):
                battery = int(args[i + 1])
                i += 2
            elif args[i] == "--wifi" and i + 1 < len(args):
                wifi = int(args[i + 1])
                i += 2
            elif args[i] == "--network" and i + 1 < len(args):
                network = args[i + 1]
                i += 2
            elif args[i] == "--clear":
                clear = True
                i += 1
            else:
                i += 1
        if clear:
            if platform in ("ios", "watchos", "tvos", "visionos"):
                ios.status_bar_clear(sim_id)
            else:
                android.status_bar_clear(sim_id)
            return {"status": "cleared"}
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.status_bar(sim_id, time_str=time_str, battery=battery,
                           wifi=wifi, network=network)
        else:
            android.status_bar(sim_id, time_str=time_str, battery=battery,
                               wifi=wifi, network=network)
        return {"status": "set"}

    elif command == "dismiss-alert":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            # Try simctl ui alert dismiss, fall back to Maestro
            _sp.run(["xcrun", "simctl", "ui", sim_id, "alert", "accept"],
                     capture_output=True, check=False)
            ios.click_system_alert_button(sim_id, ["Cancel", "Not Now", "Close", "Don’t Allow"])
        else:
            # Android: press Enter key to dismiss
            _sp.run(["adb", "-s", android.get_serial(sim_id),
                      "shell", "input", "keyevent", "KEYCODE_ENTER"],
                     capture_output=True, check=False)
        return {"status": "dismissed"}

    elif command == "accept-alert":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.accept_open_app_alert(sim_id, attempts=2, delay=0.35)
            expected_bundle = None
            with _locked_sessions() as (data, save):
                expected_bundle = data["sessions"].get(session_id, {}).get("last_app")
            if expected_bundle:
                ios.complete_open_url_handoff(sim_id, expected_bundle, attempts=3, foreground_timeout=1.0)
        else:
            _sp.run(["adb", "-s", android.get_serial(sim_id),
                      "shell", "input", "keyevent", "KEYCODE_ENTER"],
                     capture_output=True, check=False)
        return {"status": "accepted"}

    elif command == "deny-alert":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            _sp.run(["xcrun", "simctl", "ui", sim_id, "alert", "deny"],
                     capture_output=True, check=False)
            ios.click_system_alert_button(sim_id, ["Don’t Allow", "Cancel", "Not Now", "Close"])
        else:
            _sp.run(["adb", "-s", android.get_serial(sim_id),
                      "shell", "input", "keyevent", "KEYCODE_BACK"],
                     capture_output=True, check=False)
        return {"status": "denied"}

    elif command == "grant-all":
        if not args:
            raise RuntimeError("Usage: simemu do <session> grant-all <bundle-or-package>")
        bundle = args[0]
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            services = ["all"]
            for svc in services:
                _sp.run(["xcrun", "simctl", "privacy", sim_id, "grant", svc, bundle],
                         capture_output=True, check=False)
        else:
            serial = android.get_serial(sim_id)
            permissions = [
                "android.permission.CAMERA",
                "android.permission.RECORD_AUDIO",
                "android.permission.ACCESS_FINE_LOCATION",
                "android.permission.ACCESS_COARSE_LOCATION",
                "android.permission.READ_CONTACTS",
                "android.permission.WRITE_CONTACTS",
                "android.permission.READ_EXTERNAL_STORAGE",
                "android.permission.WRITE_EXTERNAL_STORAGE",
                "android.permission.READ_MEDIA_IMAGES",
                "android.permission.READ_MEDIA_VIDEO",
                "android.permission.POST_NOTIFICATIONS",
            ]
            for perm in permissions:
                _sp.run(["adb", "-s", serial, "shell", "pm", "grant", bundle, perm],
                         capture_output=True, check=False)
        return {"status": "granted", "app": bundle}

    elif command == "clear-data":
        if not args:
            raise RuntimeError("Usage: simemu do <session> clear-data <bundle-or-package>")
        bundle = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            # iOS: terminate + uninstall + reinstall is the only reliable way
            ios.terminate(sim_id, bundle)
            # Can't easily clear data on iOS sim without uninstall
            return {"status": "terminated", "hint": "iOS: uninstall and reinstall to clear data"}
        else:
            android.clear_data(sim_id, bundle)
        return {"status": "cleared", "app": bundle}

    elif command == "clean-retry":
        if not args:
            raise RuntimeError("Usage: simemu do <session> clean-retry <bundle-or-package>")
        bundle = args[0]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            raise RuntimeError(
                "'clean-retry' is Android only. On iOS use "
                "`simemu do <session> reset-app <bundle> <app-path>`."
            )
        android.clear_data(sim_id, bundle)
        android.launch(sim_id, bundle, [])
        with _locked_sessions() as (data, save):
            if session_id in data["sessions"]:
                data["sessions"][session_id]["last_app"] = bundle.split("/", 1)[0]
                save(data)
        return {
            "status": "clean_retried",
            "app": bundle,
            "hint": "App data cleared and relaunched from a clean state.",
        }

    elif command == "clipboard-set":
        if not args:
            raise RuntimeError("Usage: simemu do <session> clipboard-set <text>")
        text = " ".join(args)
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            _sp.run(["xcrun", "simctl", "pbcopy", sim_id],
                     input=text.encode(), capture_output=True, check=False)
        else:
            android.input_text(sim_id, text)
        return {"status": "set", "text": text}

    elif command == "clipboard-get":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            result = _sp.run(["xcrun", "simctl", "pbpaste", sim_id],
                              capture_output=True, text=True, check=False)
            return {"status": "ok", "text": result.stdout.strip()}
        else:
            return {"status": "unsupported", "hint": "Android clipboard-get not supported"}

    elif command == "build":
        return _do_build(session, sim_id, platform, is_real, args)

    elif command == "env":
        result = {"session": session_id, "platform": platform, "form_factor": session.form_factor}
        if platform in ("ios", "watchos", "tvos", "visionos"):
            result["udid"] = sim_id
        else:
            serial = android.get_serial(sim_id) if not is_real else sim_id
            result["serial"] = serial
        result["device_name"] = session.device_name
        result["os_version"] = session.resolved_os_version or session.os_version
        return result

    # ── high-impact commands ─────────────────────────────────────────────────

    elif command == "auto-dismiss":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            # Grant common permissions preemptively + reset privacy warnings
            _sp.run(["xcrun", "simctl", "ui", sim_id, "alert", "accept"],
                     capture_output=True, check=False)
            # Reset privacy warnings so they don't re-appear
            _sp.run(["xcrun", "simctl", "privacy", sim_id, "reset", "all"],
                     capture_output=True, check=False)
            return {"status": "dismissed", "platform": "ios",
                    "hint": "Accepted pending alert and reset privacy warnings"}
        else:
            serial = android.get_serial(sim_id)
            # Disable window animation scale
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "window_animation_scale", "0"],
                     capture_output=True, check=False)
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "transition_animation_scale", "0"],
                     capture_output=True, check=False)
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "animator_duration_scale", "0"],
                     capture_output=True, check=False)
            # Disable package verifier
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "package_verifier_enable", "0"],
                     capture_output=True, check=False)
            return {"status": "dismissed", "platform": "android",
                    "hint": "Disabled animations and package verifier"}

    elif command == "wait-for-render":
        import subprocess as _sp
        import time as _time
        seconds = 3
        output = None
        i = 0
        while i < len(args):
            if args[i] in ("-o", "--output") and i + 1 < len(args):
                output = args[i + 1]
                i += 2
            else:
                try:
                    seconds = float(args[i])
                except ValueError:
                    pass
                i += 1
        _time.sleep(seconds)
        # Take screenshot after waiting
        if not output:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
            out_dir.mkdir(parents=True, exist_ok=True)
            output = str(out_dir / f"{session_id}_{ts}.png")
        if is_real and platform == "ios":
            device.ios_screenshot(sim_id, output)
        elif platform in ("ios", "watchos", "tvos", "visionos"):
            ios.screenshot(sim_id, output)
        else:
            android.screenshot(sim_id, output)
        return {"status": "captured", "waited": seconds, "path": output}

    elif command == "deeplink-proof":
        import time as _time
        if not args:
            raise RuntimeError("Usage: simemu do <session> deeplink-proof <url> [-o output]")
        url = args[0]
        output = None
        if "-o" in args:
            idx = args.index("-o")
            if idx + 1 < len(args):
                output = args[idx + 1]
        # Open the URL
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.open_url(sim_id, url)
            expected_bundle = None
            with _locked_sessions() as (data, save):
                expected_bundle = data["sessions"].get(session_id, {}).get("last_app")
            if expected_bundle and not ios.complete_open_url_handoff(sim_id, expected_bundle):
                raise RuntimeError(
                    f"Opened deep link but '{expected_bundle}' never became foreground on iOS."
                )
            if not expected_bundle:
                ios.accept_open_app_alert(sim_id)
        else:
            android.open_url(sim_id, url)
        # Wait for render
        _time.sleep(3)
        # Screenshot
        if not output:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
            out_dir.mkdir(parents=True, exist_ok=True)
            output = str(out_dir / f"{session_id}_{ts}.png")
        if is_real and platform == "ios":
            device.ios_screenshot(sim_id, output)
        elif platform in ("ios", "watchos", "tvos", "visionos"):
            ios.screenshot(sim_id, output)
        else:
            android.screenshot(sim_id, output)
        return {"status": "captured", "url": url, "path": output}

    elif command == "reset-app":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> reset-app <bundle-or-package> <app-path>")
        bundle = args[0]
        app_path = args[1]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.terminate(sim_id, bundle)
            ios.uninstall(sim_id, bundle)
            ios.install(sim_id, app_path)
            ios.launch(sim_id, bundle, [])
        else:
            android.terminate(sim_id, bundle)
            android.uninstall(sim_id, bundle)
            android.install(sim_id, app_path)
            android.launch(sim_id, bundle, [])
        with _locked_sessions() as (data, save):
            if session_id in data["sessions"]:
                data["sessions"][session_id]["last_app"] = bundle.split("/", 1)[0]
                save(data)
        return {"status": "reset", "app": bundle, "reinstalled_from": app_path}

    elif command == "foreground-app":
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "ok", "foreground_app": ios.foreground_app(sim_id)}
        else:
            return {"status": "ok", "foreground_app": android.foreground_app(sim_id)}

    elif command == "is-running":
        if not args:
            raise RuntimeError("Usage: simemu do <session> is-running <bundle-or-package>")
        bundle = args[0]
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            result = _sp.run(
                ["xcrun", "simctl", "spawn", sim_id, "launchctl", "list"],
                capture_output=True, text=True, check=False,
            )
            running = bundle in result.stdout
            return {"status": "ok", "app": bundle, "running": running}
        else:
            serial = android.get_serial(sim_id)
            result = _sp.run(
                ["adb", "-s", serial, "shell", "pidof", bundle],
                capture_output=True, text=True, check=False,
            )
            running = bool(result.stdout.strip())
            pid = result.stdout.strip() if running else None
            return {"status": "ok", "app": bundle, "running": running, "pid": pid}

    # ── medium-impact commands ───────────────────────────────────────────────

    elif command == "network":
        if not args:
            raise RuntimeError("Usage: simemu do <session> network <offline|slow|normal>")
        mode = args[0]
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "unsupported", "platform": "ios",
                    "hint": "iOS Simulator does not support network simulation via simctl. "
                            "Use Network Link Conditioner in System Preferences or "
                            "Apple Configurator profiles."}
        else:
            serial = android.get_serial(sim_id)
            if mode == "offline":
                _sp.run(["adb", "-s", serial, "shell", "svc", "wifi", "disable"],
                         capture_output=True, check=False)
                _sp.run(["adb", "-s", serial, "shell", "svc", "data", "disable"],
                         capture_output=True, check=False)
            elif mode == "slow":
                # Re-enable networking but throttle via emulator console
                _sp.run(["adb", "-s", serial, "shell", "svc", "wifi", "enable"],
                         capture_output=True, check=False)
                _sp.run(["adb", "-s", serial, "shell", "svc", "data", "enable"],
                         capture_output=True, check=False)
                return {"status": "partial", "network": mode,
                        "hint": "WiFi/data re-enabled. For true throttling use "
                                "emulator -netdelay or -netspeed flags at boot."}
            elif mode == "normal":
                _sp.run(["adb", "-s", serial, "shell", "svc", "wifi", "enable"],
                         capture_output=True, check=False)
                _sp.run(["adb", "-s", serial, "shell", "svc", "data", "enable"],
                         capture_output=True, check=False)
            else:
                raise RuntimeError(f"Unknown network mode '{mode}'. Use: offline, slow, normal")
            return {"status": "set", "network": mode}

    elif command == "keychain-reset":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            _sp.run(["xcrun", "simctl", "keychain", sim_id, "reset"],
                     capture_output=True, check=False)
            return {"status": "reset", "platform": "ios"}
        else:
            return {"status": "unsupported", "platform": "android",
                    "hint": "Android does not have a keychain equivalent. "
                            "Use clear-data to reset app credentials."}

    elif command == "icloud-sync":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            _sp.run(["xcrun", "simctl", "icloud_sync", sim_id],
                     capture_output=True, check=False)
            return {"status": "synced", "platform": "ios"}
        else:
            return {"status": "unsupported", "platform": "android",
                    "hint": "iCloud sync is iOS only."}

    elif command == "app-info":
        if not args:
            raise RuntimeError("Usage: simemu do <session> app-info <bundle-or-package>")
        bundle = args[0]
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            result = _sp.run(
                ["xcrun", "simctl", "appinfo", sim_id, bundle],
                capture_output=True, text=True, check=False,
            )
            return {"status": "ok", "app": bundle, "info": result.stdout.strip()}
        else:
            try:
                probe = android.verify_install(sim_id, bundle, timeout=15)
            except RuntimeError:
                serial = android.wait_until_ready(sim_id)
                probe = android._probe_package_state(serial, bundle)
            return {"status": "ok", "app": bundle, "info": probe.format_report()}

    elif command == "verify-install":
        if not args:
            raise RuntimeError("Usage: simemu do <session> verify-install <package>")
        if platform in ("ios", "watchos", "tvos", "visionos"):
            raise RuntimeError("'verify-install' is Android only.")
        bundle = args[0]
        probe = android.verify_install(sim_id, bundle)
        return {"status": "verified", "app": bundle, "info": probe.format_report()}

    elif command == "repair-install":
        if len(args) < 2:
            raise RuntimeError("Usage: simemu do <session> repair-install <package> <apk-path>")
        if platform in ("ios", "watchos", "tvos", "visionos"):
            raise RuntimeError("'repair-install' is Android only.")
        bundle = args[0]
        app_path = args[1]
        probe = android.repair_install(sim_id, bundle, app_path)
        return {"status": "repaired", "app": bundle, "reinstalled_from": app_path, "info": probe.format_report()}

    elif command == "a11y-tree":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "unsupported", "platform": "ios",
                    "hint": "iOS accessibility hierarchy is not available via simctl. "
                            "Use XCUITest or Maestro for accessibility inspection."}
        else:
            serial = android.get_serial(sim_id)
            result = _sp.run(
                ["adb", "-s", serial, "shell", "uiautomator", "dump", "/dev/tty"],
                capture_output=True, text=True, check=False,
            )
            return {"status": "ok", "tree": result.stdout.strip()}

    elif command == "a11y-tap":
        if not args:
            raise RuntimeError("Usage: simemu do <session> a11y-tap <label-text>")
        label_text = " ".join(args)
        import subprocess as _sp
        import tempfile as _tmp
        # Use a single-step Maestro flow — works headless
        flow_content = f"appId: \"\"\n---\n- tapOn: \"{label_text}\"\n"
        with _tmp.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(flow_content)
            flow_path = f.name
        try:
            if platform in ("ios", "watchos", "tvos", "visionos"):
                device_id = sim_id
            else:
                from .discover import get_android_serial
                device_id = get_android_serial(sim_id)
                if not device_id:
                    raise RuntimeError("Android emulator is not running.")
            result = _sp.run(["maestro", "--device", device_id, "test", flow_path],
                              capture_output=True, text=True, check=False)
            success = result.returncode == 0
            return {"status": "tapped" if success else "failed",
                    "label": label_text, "exit_code": result.returncode}
        finally:
            Path(flow_path).unlink(missing_ok=True)

    elif command == "type-submit":
        if not args:
            raise RuntimeError("Usage: simemu do <session> type-submit <text...>")
        text = " ".join(args)
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            # Copy text to pasteboard, paste, then press Return
            _sp.run(["xcrun", "simctl", "pbcopy", sim_id],
                     input=text.encode(), capture_output=True, check=False)
            # Paste via Cmd+V keypress
            ios.input_text(sim_id, text)
            _sp.run(["xcrun", "simctl", "io", sim_id, "sendkey", "return"],
                     capture_output=True, check=False)
        else:
            serial = android.get_serial(sim_id)
            android.input_text(sim_id, text)
            _sp.run(["adb", "-s", serial, "shell", "input", "keyevent", "KEYCODE_ENTER"],
                     capture_output=True, check=False)
        return {"status": "typed_and_submitted", "text": text}

    elif command == "scroll":
        if not args:
            raise RuntimeError("Usage: simemu do <session> scroll <up|down|left|right>")
        direction = args[0].lower()
        import subprocess as _sp
        # Define swipe coordinates for each direction (center-based, 1080x1920 logical)
        swipe_map = {
            "up":    (540, 1400, 540, 600),
            "down":  (540, 600, 540, 1400),
            "left":  (900, 960, 180, 960),
            "right": (180, 960, 900, 960),
        }
        if direction not in swipe_map:
            raise RuntimeError(f"Unknown scroll direction '{direction}'. Use: up, down, left, right")
        x1, y1, x2, y2 = swipe_map[direction]
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.swipe(sim_id, x1, y1, x2, y2, duration=0.3)
        else:
            android.swipe(sim_id, x1, y1, x2, y2, duration=300)
        return {"status": "scrolled", "direction": direction}

    elif command == "back":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            # iOS: swipe from left edge to go back
            ios.swipe(sim_id, 5, 400, 300, 400, duration=0.3)
            return {"status": "back", "method": "edge_swipe"}
        else:
            serial = android.get_serial(sim_id)
            _sp.run(["adb", "-s", serial, "shell", "input", "keyevent", "KEYCODE_BACK"],
                     capture_output=True, check=False)
            return {"status": "back", "method": "keyevent"}

    elif command == "home":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            _sp.run(["xcrun", "simctl", "io", sim_id, "sendkey", "home"],
                     capture_output=True, check=False)
        else:
            serial = android.get_serial(sim_id)
            _sp.run(["adb", "-s", serial, "shell", "input", "keyevent", "KEYCODE_HOME"],
                     capture_output=True, check=False)
        return {"status": "home"}

    elif command == "notifications-clear":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "unsupported", "platform": "ios",
                    "hint": "iOS Simulator does not expose notification clearing via simctl."}
        else:
            serial = android.get_serial(sim_id)
            _sp.run(["adb", "-s", serial, "shell", "service", "call",
                      "notification", "1"],
                     capture_output=True, check=False)
            return {"status": "cleared", "platform": "android"}

    elif command == "app-container":
        if not args:
            raise RuntimeError("Usage: simemu do <session> app-container <bundle-or-package>")
        bundle = args[0]
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            result = _sp.run(
                ["xcrun", "simctl", "get_app_container", sim_id, bundle, "data"],
                capture_output=True, text=True, check=False,
            )
            container = result.stdout.strip() if result.returncode == 0 else None
            return {"status": "ok", "app": bundle, "container": container}
        else:
            serial = android.get_serial(sim_id)
            result = _sp.run(
                ["adb", "-s", serial, "shell", "run-as", bundle, "pwd"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                return {"status": "ok", "app": bundle, "container": result.stdout.strip()}
            else:
                return {"status": "ok", "app": bundle, "container": None,
                        "hint": "App may not be debuggable (run-as requires debuggable flag)"}

    # ── low-impact commands ──────────────────────────────────────────────────

    elif command == "clone":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            new_name = args[0] if args else f"{session.device_name}-clone"
            result = _sp.run(
                ["xcrun", "simctl", "clone", sim_id, new_name],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                new_udid = result.stdout.strip()
                return {"status": "cloned", "new_udid": new_udid, "new_name": new_name}
            else:
                return {"status": "failed",
                        "error": result.stderr.strip() or "Clone failed"}
        else:
            return {"status": "unsupported", "platform": "android",
                    "hint": "Android emulator cloning is not supported."}

    elif command == "siri":
        if not args:
            raise RuntimeError("Usage: simemu do <session> siri <query...>")
        query = " ".join(args)
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "unsupported", "platform": "ios",
                    "hint": f"Siri invocation is not reliably supported via simctl. "
                            f"Query was: '{query}'. Consider using xcrun simctl spawn "
                            f"with notifyutil or Shortcuts automation instead."}
        else:
            return {"status": "unsupported", "platform": "android",
                    "hint": "Siri is iOS only. For Google Assistant use adb broadcast."}

    elif command == "contacts-import":
        if not args:
            raise RuntimeError("Usage: simemu do <session> contacts-import <vcf-file>")
        vcf_path = args[0]
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            _sp.run(["xcrun", "simctl", "addmedia", sim_id, vcf_path],
                     capture_output=True, check=False)
            return {"status": "imported", "file": vcf_path}
        else:
            serial = android.get_serial(sim_id)
            remote_path = "/sdcard/import_contacts.vcf"
            _sp.run(["adb", "-s", serial, "push", vcf_path, remote_path],
                     capture_output=True, check=False)
            _sp.run(["adb", "-s", serial, "shell", "am", "start",
                      "-a", "android.intent.action.VIEW",
                      "-d", f"file://{remote_path}",
                      "-t", "text/x-vcard"],
                     capture_output=True, check=False)
            return {"status": "imported", "file": vcf_path,
                    "hint": "VCF pushed and import intent launched"}

    elif command == "font-size":
        if not args:
            raise RuntimeError("Usage: simemu do <session> font-size <small|default|large|xlarge>")
        size = args[0].lower()
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "unsupported", "platform": "ios",
                    "hint": "Font size cannot be changed via simctl. "
                            "Use Accessibility settings in the Simulator UI "
                            "or defaults write on the sim plist."}
        else:
            serial = android.get_serial(sim_id)
            scale_map = {"small": "0.85", "default": "1.0", "large": "1.15", "xlarge": "1.3"}
            scale = scale_map.get(size, "1.0")
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "system",
                      "font_scale", scale],
                     capture_output=True, check=False)
            return {"status": "set", "font_size": size, "scale": scale}

    elif command == "reduce-motion":
        if not args:
            raise RuntimeError("Usage: simemu do <session> reduce-motion <on|off>")
        mode = args[0].lower()
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            return {"status": "unsupported", "platform": "ios",
                    "hint": "Reduce motion cannot be toggled via simctl. "
                            "Use Accessibility settings in the Simulator UI."}
        else:
            serial = android.get_serial(sim_id)
            scale = "0" if mode == "on" else "1"
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "animator_duration_scale", scale],
                     capture_output=True, check=False)
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "window_animation_scale", scale],
                     capture_output=True, check=False)
            _sp.run(["adb", "-s", serial, "shell", "settings", "put", "global",
                      "transition_animation_scale", scale],
                     capture_output=True, check=False)
            return {"status": "set", "reduce_motion": mode}

    elif command == "log-crash":
        bundle = args[0] if args else None
        if platform in ("ios", "watchos", "tvos", "visionos"):
            log = ios.crash_log(sim_id, bundle_id=bundle)
        else:
            log = android.crash_log(sim_id, package=bundle)
        return {"status": "ok", "crash_log": log}

    elif command == "video-start":
        output = None
        if "-o" in args:
            idx = args.index("-o")
            if idx + 1 < len(args):
                output = args[idx + 1]
        if not output:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
            out_dir.mkdir(parents=True, exist_ok=True)
            output = str(out_dir / f"{session_id}_{ts}.mp4")
        if platform in ("ios", "watchos", "tvos", "visionos"):
            pid = ios.record_start(sim_id, output)
        else:
            pid = android.record_start(sim_id, output)
        return {"status": "recording", "pid": pid, "path": output}

    elif command == "video-stop":
        if not args:
            raise RuntimeError("Usage: simemu do <session> video-stop <pid>")
        pid = int(args[0])
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.record_stop(pid)
        else:
            android.record_stop(pid)
        return {"status": "stopped", "pid": pid}

    elif command == "reboot":
        import subprocess as _sp
        if platform in ("ios", "watchos", "tvos", "visionos"):
            ios.shutdown(sim_id)
            ios.boot(sim_id)
            return {"status": "rebooted", "platform": "ios"}
        else:
            serial = android.get_serial(sim_id)
            _sp.run(["adb", "-s", serial, "reboot"],
                     capture_output=True, check=False)
            return {"status": "rebooted", "platform": "android"}

    else:
        raise RuntimeError(
            f"Unknown command '{command}'. Available: boot, show, hide, install, launch, tap, swipe, "
            f"screenshot, maestro, url, done, renew, terminate, uninstall, input, "
            f"long-press, key, appearance, rotate, location, push, pull, add-media, "
            f"dismiss-alert, accept-alert, deny-alert, grant-all, clear-data, clean-retry, "
            f"clipboard-set, clipboard-get, shake, status-bar, build, env, "
            f"auto-dismiss, wait-for-render, deeplink-proof, reset-app, "
            f"foreground-app, is-running, network, keychain-reset, icloud-sync, "
            f"app-info, a11y-tree, a11y-tap, type-submit, scroll, back, home, "
            f"notifications-clear, app-container, clone, siri, contacts-import, "
            f"font-size, reduce-motion, log-crash, video-start, video-stop, reboot"
        )


def _do_build(session, sim_id: str, platform: str, is_real: bool, args: list[str]) -> dict:
    """Build an app and store the artifact path in session state for auto-install."""
    import json as _json
    import subprocess
    from pathlib import Path

    # Parse flags
    variant = None
    clean = False
    test = False
    raw_cmd = None
    verbose = False
    i = 0
    while i < len(args):
        if args[i] == "--variant" and i + 1 < len(args):
            variant = args[i + 1]; i += 2
        elif args[i] == "--clean":
            clean = True; i += 1
        elif args[i] == "--test":
            test = True; i += 1
        elif args[i] == "--raw" and i + 1 < len(args):
            raw_cmd = args[i + 1]; i += 2
        elif args[i] == "--verbose":
            verbose = True; i += 1
        else:
            i += 1

    # Raw mode — escape hatch
    if raw_cmd:
        result = subprocess.run(raw_cmd, shell=True, capture_output=not verbose, text=True)
        if result.returncode != 0:
            err = result.stderr[:2000] if result.stderr else ""
            raise RuntimeError(f"Build failed (exit {result.returncode}):\n{raw_cmd}\n{err}")
        return {"status": "built", "mode": "raw", "command": raw_cmd}

    # Find execution.yaml in the project
    cwd = Path.cwd()
    exec_yaml = cwd / "keel" / "execution.yaml"
    build_config = None

    if exec_yaml.exists():
        content = exec_yaml.read_text()
        build_config = _parse_build_variants(content)

    if not build_config:
        raise RuntimeError(
            "No buildVariants in keel/execution.yaml. Either:\n"
            "  1. Add buildVariants config to keel/execution.yaml\n"
            "  2. Use --raw \"xcodebuild -scheme MyApp build\" as escape hatch"
        )

    # Resolve variant — default to first one
    variant_names = list(build_config.keys())
    if not variant:
        variant = variant_names[0]

    if variant not in build_config:
        raise RuntimeError(
            f"Unknown variant '{variant}'. Available: {', '.join(variant_names)}"
        )

    variant_cfg = build_config[variant]

    if platform in ("ios", "watchos", "tvos", "visionos"):
        ios_cfg = variant_cfg.get("ios", {})
        scheme = ios_cfg.get("scheme", variant)
        project = ios_cfg.get("project")
        workspace = ios_cfg.get("workspace")
        configuration = ios_cfg.get("configuration", "Debug")

        cmd_parts = ["xcodebuild"]
        if workspace:
            cmd_parts += ["-workspace", workspace]
        elif project:
            cmd_parts += ["-project", project]
        cmd_parts += ["-scheme", scheme]
        cmd_parts += ["-destination", f"id={sim_id}"]
        cmd_parts += ["-configuration", configuration]
        cmd_parts += ["CODE_SIGNING_ALLOWED=NO"]
        if clean:
            cmd_parts.append("clean")
        cmd_parts.append("test" if test else "build")

        print(f"[simemu] building iOS: {scheme} ({configuration}) → {sim_id[:8]}...")
        result = subprocess.run(cmd_parts, capture_output=not verbose, text=True)
        if result.returncode != 0:
            err = result.stderr[:3000] if result.stderr else result.stdout[-3000:] if result.stdout else ""
            raise RuntimeError(
                f"iOS build failed (exit {result.returncode}):\n"
                f"  {' '.join(cmd_parts)}\n{err}"
            )

        # Find the built .app
        app_path = _find_ios_artifact(scheme, configuration)
        if app_path:
            _store_build_artifact(session.session_id, str(app_path))

        return {
            "status": "built",
            "platform": "ios",
            "variant": variant,
            "scheme": scheme,
            "configuration": configuration,
            "app": str(app_path) if app_path else None,
        }

    else:
        android_cfg = variant_cfg.get("android", {})
        task = android_cfg.get("task", f"assemble{variant.capitalize()}")

        cmd_parts = ["./gradlew"]
        if clean:
            cmd_parts.append("clean")
        cmd_parts.append(task)

        print(f"[simemu] building Android: {task}...")
        result = subprocess.run(cmd_parts, capture_output=not verbose, text=True)
        if result.returncode != 0:
            err = result.stderr[:3000] if result.stderr else result.stdout[-3000:] if result.stdout else ""
            raise RuntimeError(
                f"Android build failed (exit {result.returncode}):\n"
                f"  {' '.join(cmd_parts)}\n{err}"
            )

        # Find the built .apk
        apk_path = _find_android_artifact(task)
        if apk_path:
            _store_build_artifact(session.session_id, str(apk_path))

        return {
            "status": "built",
            "platform": "android",
            "variant": variant,
            "task": task,
            "apk": str(apk_path) if apk_path else None,
        }


def _parse_build_variants(yaml_content: str) -> dict | None:
    """Parse buildVariants from execution.yaml. Minimal YAML parser."""
    lines = yaml_content.split("\n")
    in_variants = False
    current_variant = None
    current_platform = None
    variants: dict = {}

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # Top-level key
        if not line.startswith(" ") and not line.startswith("\t"):
            if stripped == "buildVariants:":
                in_variants = True
            else:
                in_variants = False
            continue

        if not in_variants:
            continue

        indent = len(line) - len(line.lstrip())

        # Variant name (indent 2)
        if indent == 2 and stripped.endswith(":"):
            current_variant = stripped[:-1].strip()
            variants[current_variant] = {}
            current_platform = None
            continue

        # Platform (indent 4)
        if indent == 4 and stripped.endswith(":") and current_variant:
            current_platform = stripped[:-1].strip()
            variants[current_variant][current_platform] = {}
            continue

        # Key: value (indent 6)
        if indent == 6 and ": " in stripped and current_variant and current_platform:
            key, _, value = stripped.partition(": ")
            variants[current_variant][current_platform][key.strip()] = value.strip()

    return variants if variants else None


def _find_ios_artifact(scheme: str, configuration: str):
    """Find the most recently built .app in DerivedData."""
    from pathlib import Path
    import os

    derived = Path.home() / "Library" / "Developer" / "Xcode" / "DerivedData"
    if not derived.exists():
        return None

    candidates = []
    config_dir = f"{configuration}-iphonesimulator"
    for dd in derived.iterdir():
        products = dd / "Build" / "Products" / config_dir
        if products.exists():
            for app in products.glob("*.app"):
                candidates.append((app, os.path.getmtime(app)))

    if not candidates:
        return None

    # Return most recently modified
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _find_android_artifact(task: str):
    """Find the built APK from gradle output."""
    from pathlib import Path

    # Common output locations
    for pattern in [
        "app/build/outputs/apk/**/*.apk",
        "build/outputs/apk/**/*.apk",
    ]:
        apks = list(Path.cwd().glob(pattern))
        if apks:
            # Prefer debug APK matching the task
            debug = [a for a in apks if "debug" in a.name.lower()]
            return debug[0] if debug else apks[0]
    return None


def _store_build_artifact(session_id: str, artifact_path: str):
    """Store the build artifact path in session state for auto-install."""
    with _locked_sessions() as (data, save):
        if session_id in data.get("sessions", {}):
            data["sessions"][session_id]["last_build_artifact"] = artifact_path
            save(data)


def _get_build_artifact(session_id: str) -> str | None:
    """Get the stored build artifact path."""
    data = _read_sessions_raw()
    session_data = data.get("sessions", {}).get(session_id, {})
    return session_data.get("last_build_artifact")


# ── lifecycle management ─────────────────────────────────────────────────────

def lifecycle_tick() -> list[str]:
    """Called periodically (every 60s). Transitions sessions through lifecycle states.

    Returns list of session IDs that changed state.
    """
    now = datetime.now(timezone.utc)
    changed = []

    with _locked_sessions() as (data, save):
        dirty = False
        for sid, raw in list(data["sessions"].items()):
            session = _session_from_dict(raw)
            if session.status in ("expired", "released"):
                continue

            hb = datetime.fromisoformat(session.heartbeat_at)
            idle_seconds = (now - hb).total_seconds()

            new_status = session.status

            if idle_seconds >= EXPIRE_TIMEOUT:
                new_status = "expired"
            elif idle_seconds >= IDLE_TIMEOUT + PARK_TIMEOUT and session.status in ("active", "idle"):
                new_status = "parked"
            elif idle_seconds >= IDLE_TIMEOUT and session.status == "active":
                new_status = "idle"

            if new_status != session.status:
                old_status = session.status
                data["sessions"][sid]["status"] = new_status
                data["sessions"][sid]["expires_at"] = _compute_expires_at(new_status, session.heartbeat_at)
                dirty = True
                changed.append(sid)

                # Shutdown device when parking
                if new_status == "parked" and not session.real_device:
                    try:
                        if session.platform in ("ios", "watchos", "tvos", "visionos"):
                            ios.shutdown(session.sim_id)
                        else:
                            android.shutdown(session.sim_id)
                    except Exception:
                        pass  # device may already be off

                print(
                    f"[simemu-session] '{sid}' {old_status} → {new_status} "
                    f"(idle {idle_seconds / 60:.0f}m)",
                    flush=True,
                )

        if dirty:
            save(data)

    return changed


# ── memory budget ────────────────────────────────────────────────────────────

def _estimated_memory_mb() -> int:
    """Estimate total memory usage of all active/idle sessions."""
    total = 0
    for session in get_active_sessions().values():
        if session.status != "parked":  # parked devices are shutdown
            total += _DEVICE_MEMORY_MB.get(session.platform, 2048)
    return total


def _memory_budget_mb() -> int:
    """Return configured memory budget in MB."""
    env = os.environ.get("SIMEMU_MEMORY_BUDGET_MB")
    if env:
        return int(env)
    return DEFAULT_MEMORY_BUDGET_MB


def _enforce_memory_budget_if_needed(platform: str) -> None:
    """Park idle sessions if claiming a new device would exceed the memory budget."""
    needed = _DEVICE_MEMORY_MB.get(platform, 2048)
    budget = _memory_budget_mb()
    current = _estimated_memory_mb()

    if current + needed <= budget:
        return

    # Try parking idle sessions to make room, oldest first
    sessions_by_idle = sorted(
        [
            (sid, s) for sid, s in get_active_sessions().items()
            if s.status == "idle"
        ],
        key=lambda x: x[1].heartbeat_at,
    )

    for sid, session in sessions_by_idle:
        if current + needed <= budget:
            break
        _park_session(sid, session)
        current -= _DEVICE_MEMORY_MB.get(session.platform, 2048)

    # If still over budget, check if we can park active sessions (oldest first)
    if current + needed > budget:
        active_sessions = sorted(
            [
                (sid, s) for sid, s in get_active_sessions().items()
                if s.status == "active"
            ],
            key=lambda x: x[1].heartbeat_at,
        )
        # Don't park the most recently active sessions
        for sid, session in active_sessions[:-1]:  # keep at least one
            if current + needed <= budget:
                break
            _park_session(sid, session)
            current -= _DEVICE_MEMORY_MB.get(session.platform, 2048)

    if current + needed > budget:
        raise SessionError(
            error="memory_budget_exceeded",
            session="",
            hint=f"All devices busy (using {current}MB of {budget}MB budget). "
                 f"Release idle sessions first or increase SIMEMU_MEMORY_BUDGET_MB.",
        )


def _park_session(session_id: str, session: Session) -> None:
    """Park a session: shut down its device but keep the session."""
    with _locked_sessions() as (data, save):
        if session_id in data["sessions"]:
            data["sessions"][session_id]["status"] = "parked"
            data["sessions"][session_id]["expires_at"] = _compute_expires_at(
                "parked", session.heartbeat_at
            )
            save(data)

    if not session.real_device:
        try:
            if session.platform in ("ios", "watchos", "tvos", "visionos"):
                ios.shutdown(session.sim_id)
            else:
                android.shutdown(session.sim_id)
        except Exception:
            pass

    print(f"[simemu-session] Parked '{session_id}' to free memory", flush=True)


# ── errors ───────────────────────────────────────────────────────────────────

class SessionError(RuntimeError):
    """Error with structured JSON output and actionable hints."""

    def __init__(self, error: str, session: str, hint: str, **extra):
        self.error_type = error
        self.session = session
        self.hint = hint
        self.extra = extra
        super().__init__(hint)

    def to_json(self) -> dict:
        result = {
            "error": self.error_type,
            "session": self.session,
            "hint": self.hint,
        }
        result.update(self.extra)
        return result


# ── T-15: Command history ────────────────────────────────────────────────────

def _log_command(session_id: str, command: str, args: list[str]) -> None:
    """Append a command to the session's history log."""
    log_dir = state.state_dir() / "history"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{session_id}.log"
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"{ts} {command} {' '.join(args[:5])}\n"
    try:
        with log_file.open("a") as f:
            f.write(line)
    except OSError:
        pass


def get_command_history(session_id: str) -> list[str]:
    """Return the command history for a session."""
    log_file = state.state_dir() / "history" / f"{session_id}.log"
    if not log_file.exists():
        return []
    return log_file.read_text().strip().splitlines()


# ── T-LU-019: Session provenance ────────────────────────────────────────────

def update_provenance(session_id: str, **fields) -> None:
    """Update proof provenance metadata for a session.

    Stored fields: last_app, last_url, last_screenshot, last_build,
    last_deep_link, render_wait_ms, proof_metadata (arbitrary dict).

    Provenance survives normal session writes and recovery.
    """
    with _locked_sessions() as (data, save):
        session_data = data["sessions"].get(session_id)
        if not session_data:
            return
        provenance = session_data.setdefault("provenance", {})
        provenance["updated_at"] = _now_iso()
        for key, value in fields.items():
            provenance[key] = value
        save(data)


def get_provenance(session_id: str) -> dict:
    """Return the current proof provenance for a session."""
    data = _read_sessions_raw()
    session_data = data["sessions"].get(session_id, {})
    return session_data.get("provenance", {})


# ── T-29: Safe android serial helper ─────────────────────────────────────────

def _android_serial(sim_id: str) -> str:
    """Get Android serial, raising a clear error if not available."""
    serial = android.get_serial(sim_id)
    if not serial:
        raise RuntimeError(
            f"Android emulator '{sim_id}' is not running or not adb-ready. "
            f"Try: simemu do <session> reboot"
        )
    return serial


# ── T-31: Rate limiting ──────────────────────────────────────────────────────

MAX_ACTIVE_PER_AGENT = 8


def _check_rate_limit(agent: str) -> None:
    """Raise if agent has too many active sessions."""
    active = get_active_sessions()
    agent_count = sum(1 for s in active.values()
                      if s.agent == agent and s.status in ("active", "idle"))
    if agent_count >= MAX_ACTIVE_PER_AGENT:
        raise SessionError(
            error="rate_limited",
            session="",
            hint=f"Agent '{agent}' has {agent_count} active sessions (max {MAX_ACTIVE_PER_AGENT}). "
                 f"Release some first: simemu do <session> done",
        )
