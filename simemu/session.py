"""
Session-based resource manager for simemu v2.

Agents interact with sessions (opaque IDs) instead of device slugs/UDIDs.
Sessions manage the full device lifecycle: claim → active → idle → parked → expired.

State file: ~/.simemu/sessions.json (separate from legacy state.json)
"""

import fcntl
import json
import os
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
}


# ── data model ───────────────────────────────────────────────────────────────

@dataclass
class ClaimSpec:
    platform: str                        # "ios" | "android"
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
    platform: str                        # "ios" | "android"
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

    # Check memory budget before claiming
    _enforce_memory_budget_if_needed(spec.platform)

    # Find best matching device
    sim = find_best_device(spec)

    # Generate session ID
    session_id = _gen_session_id()

    # Boot the device if not already booted and not a real device
    if not sim.real_device and not sim.booted:
        if sim.platform in ("ios", "watchos", "tvos", "visionos"):
            ios.boot(sim.sim_id)
        else:
            android.boot(sim.sim_id, headless=True)

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

    with _locked_sessions() as (data, save):
        if session_id in data["sessions"]:
            data["sessions"][session_id]["status"] = "released"
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

def do_command(session_id: str, command: str, args: list[str]) -> dict | None:
    """Execute a command on a session. Auto-extends heartbeat."""
    if command == "done":
        session = release(session_id)
        return {"session": session_id, "status": "released"}

    if command == "boot":
        # Explicitly wake a parked session — touch() already does this,
        # but agents expect an explicit boot command
        session = touch(session_id)
        return session.to_agent_json()

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

    # Update HUD with rich context
    if platform in ("ios", "watchos", "tvos", "visionos"):
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
        i = 0
        while i < len(args):
            if args[i] in ("-o", "--output") and i + 1 < len(args):
                output = args[i + 1]
                i += 2
            elif args[i] in ("-f", "--format") and i + 1 < len(args):
                fmt = args[i + 1]
                i += 2
            else:
                i += 1

        if not output:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
            out_dir.mkdir(parents=True, exist_ok=True)
            output = str(out_dir / f"{session_id}_{ts}.{fmt}")

        if is_real and platform == "ios":
            device.ios_screenshot(sim_id, output)
        elif platform in ("ios", "watchos", "tvos", "visionos"):
            ios.screenshot(sim_id, output, fmt=fmt if fmt != "png" else None)
        else:
            android.screenshot(sim_id, output)
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
        else:
            android.open_url(sim_id, url)
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

    else:
        raise RuntimeError(
            f"Unknown command '{command}'. Available: boot, visible, invisible, install, launch, tap, swipe, "
            f"screenshot, maestro, url, done, renew, terminate, uninstall, input, "
            f"long-press, key, appearance, rotate, location, push, pull, add-media, "
            f"shake, status-bar, build, env"
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
    with _locked():
        data = _read_sessions_raw()
        if session_id in data.get("sessions", {}):
            data["sessions"][session_id]["last_build_artifact"] = artifact_path
            _write_sessions_raw(data)


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
