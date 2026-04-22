"""
simemu — simulator allocation CLI for multi-agent development.

Agents acquire named simulator slots (slugs) and all simulator operations
are proxied through this CLI, preventing conflicts between concurrent agents.

Set SIMEMU_AGENT in each agent's environment to identify it.
Set SIMEMU_OUTPUT_DIR to override the default output directory (~/.simemu/).
"""

import argparse
import datetime
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from . import state, ios, android, device
from .discover import (
    list_ios, list_android, list_watchos, list_tvos, list_visionos,
    list_real_ios, list_real_android,
    find_simulator, NoSimulatorAvailable,
)
from . import session as session_module
from . import window as window_mgr
from .session import ClaimSpec, SessionError

# Apple platforms all use xcrun simctl — same as iOS
_APPLE_PLATFORMS = {"ios", "watchos", "tvos", "visionos"}


def _resolve_port() -> int:
    """Resolve simemu port: SIMEMU_PORT env var > ~/.fed/config.json > 7803."""
    env_val = os.environ.get("SIMEMU_PORT", "")
    try:
        port = int(env_val)
        if 1 <= port <= 65535:
            return port
    except (ValueError, TypeError):
        pass
    try:
        import json as _json
        cfg_path = Path.home() / ".fed" / "config.json"
        cfg = _json.loads(cfg_path.read_text())
        dash = cfg.get("tools", {}).get("simemu", {}).get("dash")
        if isinstance(dash, int) and dash > 0:
            return dash
    except Exception:
        pass
    return 7803


_SIMEMU_PORT = _resolve_port()


def _agent() -> str:
    return os.environ.get("SIMEMU_AGENT") or f"pid-{os.getpid()}"


def _output_dir() -> Path:
    d = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _auto_path(slug: str, ext: str) -> str:
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(_output_dir() / f"{slug}_{ts}.{ext}")


def _print_json(data):
    print(json.dumps(data, indent=2))


def _project_name(alloc: state.Allocation) -> str:
    if alloc.agent and not alloc.agent.startswith("pid-"):
        return alloc.agent
    if "-" in alloc.slug:
        return alloc.slug.split("-", 1)[0]
    return alloc.slug


def _scouty_base_url() -> str:
    return (os.environ.get("SCOUTY_BASE_URL") or "http://127.0.0.1:7331").rstrip("/")


def _scouty_json(method: str, path: str, payload: dict | None = None, timeout: float = 2.0) -> dict:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{_scouty_base_url()}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def _autostart_disabled() -> bool:
    value = (os.environ.get("SIMEMU_AUTOSTART") or "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return True
    no_value = (os.environ.get("SIMEMU_NO_AUTOSTART") or "").strip().lower()
    return no_value in {"1", "true", "yes", "on"}


def _server_reachable(host: str = "127.0.0.1", port: int | None = None, timeout: float = 0.5) -> bool:
    if port is None:
        port = _SIMEMU_PORT
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _autostart_server_if_needed() -> None:
    import subprocess

    if _autostart_disabled() or _server_reachable():
        return

    log_path = _output_dir() / "autostart.log"
    with log_path.open("ab") as log_file:
        subprocess.Popen(
            [sys.executable, "-m", "simemu.cli", "serve"],
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            cwd=str(Path.cwd()),
        )

    deadline = time.time() + 3
    while time.time() < deadline:
        if _server_reachable():
            return
        time.sleep(0.1)


# ── v2 session-based command handlers ────────────────────────────────────────

def cmd_claim(args):
    """Claim a device session."""
    visible = getattr(args, "visible", False)
    spec = ClaimSpec(
        platform=args.platform,
        form_factor=getattr(args, "form_factor", None) or "phone",
        os_version=getattr(args, "version", None),
        real_device=getattr(args, "real", False),
        label=getattr(args, "label", None) or "",
        visible=visible,
    )
    try:
        session = session_module.claim(spec)
        _print_json(session.to_agent_json())
    except SessionError as e:
        _print_json(e.to_json())
        sys.exit(1)


def cmd_do(args):
    """Execute a command on a session."""
    try:
        result = session_module.do_command(args.session, args.do_command, args.extra or [])
        if result is not None:
            _print_json(result)
    except SessionError as e:
        _print_json(e.to_json())
        sys.exit(1)


def cmd_config(args):
    """Configure simemu settings."""
    if args.config_command == "window-mode":
        if args.mode is None:
            # Show current mode
            mode = window_mgr.get_window_mode()
            print(f"Current window mode: {mode}")
            print()
            print("Available modes:")
            print("  hidden   — minimize all simulator windows on boot")
            print("  space    — move to a dedicated macOS Space (requires yabai)")
            print("  corner   — tile in a screen corner (--corner top-left|top-right|bottom-left|bottom-right)")
            print("  display  — move to a specific display (--display N)")
            print("  default  — leave windows wherever macOS puts them")
        else:
            config = window_mgr.set_window_mode(
                args.mode,
                display=getattr(args, "display", None),
                corner=getattr(args, "corner", None),
            )
            print(f"Window mode set to: {args.mode}")
            if args.mode == "corner":
                print(f"  Corner: {config.get('window_corner', 'bottom-right')}")
            elif args.mode == "display":
                print(f"  Display: {config.get('window_display', 2)}")

            # Apply to all currently booted simulators
            count = window_mgr.apply_to_all()
            if count:
                print(f"  Applied to {count} running simulator(s)")

    elif args.config_command == "displays":
        displays = window_mgr.list_displays()
        if getattr(args, "json", False):
            _print_json(displays)
        else:
            print(f"{'#':<4} {'NAME':<30} {'RESOLUTION':<16} {'POSITION'}")
            print("─" * 70)
            for d in displays:
                main = " (main)" if d["is_main"] else ""
                print(f"{d['index']:<4} {d['name']}{main:<30} {d['width']}x{d['height']:<16} {d['x']},{d['y']}")
            print()
            print(f"Set display:  simemu config window-mode display --display <#>")

    elif args.config_command == "show":
        config = window_mgr._read_config()
        if config:
            _print_json(config)
        else:
            print("No config set (using defaults)")


def cmd_sessions(args):
    """List all v2 sessions."""
    sessions = session_module.get_active_sessions()
    if not sessions:
        if getattr(args, "json", False):
            _print_json([])
        else:
            print("No active sessions.")
        return

    if getattr(args, "json", False):
        _print_json([s.to_agent_json() for s in sessions.values()])
        return

    print(f"{'SESSION':<12} {'PLATFORM':<10} {'FORM':<8} {'STATUS':<8} {'OS':<12} {'LABEL':<20} {'IDLE'}")
    print("─" * 90)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for sid, s in sessions.items():
        hb = datetime.fromisoformat(s.heartbeat_at)
        idle_min = int((now - hb).total_seconds() / 60)
        os_ver = s.resolved_os_version or s.os_version or "latest"
        label = (s.label or "")[:20]
        print(f"{sid:<12} {s.platform:<10} {s.form_factor:<8} {s.status:<8} {os_ver:<12} {label:<20} {idle_min}m")


# ── status overview ──────────────────────────────────────────────────────────

def cmd_status_overview(args):
    """Show a comprehensive system status overview."""
    import platform as _plat
    import subprocess

    output_json = getattr(args, "json", False)
    data: dict = {}

    # ── System info ──────────────────────────────────────────────────────
    mac_ver = _plat.mac_ver()[0] or "unknown"
    machine = _plat.machine()
    node = _plat.node().split(".")[0]

    try:
        phys_pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        ram_gb = round(phys_pages * page_size / (1024 ** 3))
    except (ValueError, OSError):
        ram_gb = 0

    # Hardware model
    hw_model = node
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.model"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            hw_model = result.stdout.strip()
    except FileNotFoundError:
        pass

    # Displays
    try:
        displays = window_mgr.list_displays()
    except Exception:
        displays = []

    # Window mode
    try:
        win_mode = window_mgr.get_window_mode()
    except Exception:
        win_mode = "unknown"

    # Memory budget
    budget_mb = session_module.DEFAULT_MEMORY_BUDGET_MB
    env_budget = os.environ.get("SIMEMU_MEMORY_BUDGET_MB")
    if env_budget:
        try:
            budget_mb = int(env_budget)
        except ValueError:
            pass

    data["system"] = {
        "macos_version": mac_ver,
        "machine": hw_model,
        "ram_gb": ram_gb,
        "displays": displays,
        "window_mode": win_mode,
        "memory_budget_mb": budget_mb,
    }

    # ── Sessions ─────────────────────────────────────────────────────────
    all_sessions = session_module.get_all_sessions()
    active_sessions = {sid: s for sid, s in all_sessions.items() if s.status == "active"}
    idle_sessions = {sid: s for sid, s in all_sessions.items() if s.status == "idle"}
    parked_sessions = {sid: s for sid, s in all_sessions.items() if s.status == "parked"}
    live_sessions = {sid: s for sid, s in all_sessions.items()
                     if s.status in ("active", "idle", "parked")}

    # Per-platform breakdown
    platform_breakdown: dict[str, dict[str, int]] = {}
    for sid, s in live_sessions.items():
        pb = platform_breakdown.setdefault(s.platform, {})
        pb[s.form_factor] = pb.get(s.form_factor, 0) + 1

    data["sessions"] = {
        "active": len(active_sessions),
        "idle": len(idle_sessions),
        "parked": len(parked_sessions),
        "by_platform": {
            plat: {"total": sum(ffs.values()), "form_factors": ffs}
            for plat, ffs in platform_breakdown.items()
        },
    }

    # ── Available simulators ─────────────────────────────────────────────
    try:
        ios_sims = list_ios()
        ios_booted = sum(1 for s in ios_sims if s.booted)
    except Exception:
        ios_sims = []
        ios_booted = 0

    try:
        android_sims = list_android()
        android_booted = sum(1 for s in android_sims if s.booted)
    except Exception:
        android_sims = []
        android_booted = 0

    data["simulators"] = {
        "ios": {"total": len(ios_sims), "booted": ios_booted},
        "android": {"total": len(android_sims), "booted": android_booted},
    }

    # ── Services ─────────────────────────────────────────────────────────
    # Monitor
    monitor_status = "unknown"
    monitor_last_tick = None
    monitor_log = Path.home() / ".simemu" / "monitor.log"
    try:
        if monitor_log.exists():
            # Read last line to get timestamp
            lines = monitor_log.read_text().strip().splitlines()
            if lines:
                last_line = lines[-1]
                # Try to extract timestamp (ISO format at start of line)
                for part in last_line.split():
                    try:
                        from datetime import datetime as _dt, timezone as _tz
                        ts = _dt.fromisoformat(part)
                        age = (_dt.now(_tz.utc) - ts.replace(tzinfo=_tz.utc)).total_seconds()
                        monitor_last_tick = f"{int(age)}s ago"
                        monitor_status = "running" if age < 120 else "stale"
                        break
                    except (ValueError, TypeError):
                        continue
        if monitor_status == "unknown":
            # Check if the launchd job is loaded
            result = subprocess.run(
                ["launchctl", "list", "com.simemu.monitor"],
                capture_output=True, text=True, check=False,
            )
            if result.returncode == 0:
                monitor_status = "loaded"
            else:
                monitor_status = "not loaded"
    except Exception:
        pass

    # Server
    server_status = "stopped"
    try:
        with socket.create_connection(("127.0.0.1", _SIMEMU_PORT), timeout=0.5):
            server_status = "running"
    except OSError:
        pass

    # Menubar app
    menubar_status = "not running"
    menubar_pid = None
    try:
        result = subprocess.run(
            ["pgrep", "-fl", "SimEmuBar"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            menubar_pid = result.stdout.strip().split()[0]
            menubar_status = "running"
    except FileNotFoundError:
        pass

    data["services"] = {
        "monitor": {"status": monitor_status, "last_tick": monitor_last_tick},
        "server": {"status": server_status, "port": _SIMEMU_PORT},
        "menubar": {"status": menubar_status, "pid": menubar_pid},
    }

    data["version"] = "0.3.0"

    # ── Output ───────────────────────────────────────────────────────────
    if output_json:
        _print_json(data)
        return

    # Human-readable output
    print(f"simemu v{data['version']}")
    print()

    # System
    print("System:")
    display_info = ""
    if displays:
        names = []
        for d in displays:
            n = d.get("name", "Unknown")
            w, h = d.get("width", 0), d.get("height", 0)
            names.append(f"{n} {w}x{h}")
        display_info = f"  Displays: {len(displays)} ({', '.join(names)})"
    else:
        display_info = "  Displays: unknown"

    ram_str = f"{ram_gb} GB RAM" if ram_gb else "unknown RAM"
    print(f"  macOS {mac_ver} \u00b7 {hw_model} \u00b7 {ram_str}")
    print(display_info)
    print(f"  Window mode: {win_mode}")
    print(f"  Memory budget: {budget_mb // 1024} GB")
    print()

    # Sessions
    total_live = len(live_sessions)
    print(f"Sessions: {len(active_sessions)} active \u00b7 {len(idle_sessions)} idle \u00b7 {len(parked_sessions)} parked")
    for plat, info in platform_breakdown.items():
        total = sum(info.values())
        ff_parts = [f"{count} {ff}" for ff, count in sorted(info.items())]
        print(f"  {plat}: {total} sessions ({', '.join(ff_parts)})")
    if not platform_breakdown:
        print("  (none)")
    print()

    # Simulators
    print("Simulators available:")
    print(f"  iOS: {len(ios_sims)} simulators ({ios_booted} booted)")
    print(f"  Android: {len(android_sims)} AVDs ({android_booted} booted)")
    print()

    # Services
    monitor_detail = monitor_status
    if monitor_last_tick:
        monitor_detail = f"{monitor_status} (last tick {monitor_last_tick})"
    menubar_detail = menubar_status
    if menubar_pid:
        menubar_detail = f"{menubar_status} (pid {menubar_pid})"

    print(f"Monitor: {monitor_detail}")
    print(f"Server: {server_status}" + (f" on :{_SIMEMU_PORT}" if server_status == "running" else ""))
    print(f"Menubar: {menubar_detail}")


# ── legacy command handlers (DISCONTINUED) ──────────────────────────────────

def _reject_legacy(args):
    """Block all legacy slug-based commands."""
    cmd = getattr(args, "command", "?")
    print(f"Error: '{cmd}' is not a recognized command. Read the docs: docs/AGENT_README.md", file=sys.stderr)
    print(f"\nUsage: simemu claim <platform>  |  simemu do <session> <command>  |  simemu sessions", file=sys.stderr)
    sys.exit(1)


def cmd_acquire(args):
    pass

def cmd_release(args):
    pass

def cmd_list_devices(args):
    """List connected real devices (not simulators/emulators)."""
    allocated_ids = {a.sim_id for a in state.get_all().values()}
    platform = getattr(args, "platform", None)

    rows = []
    if not platform or platform == "ios":
        rows += list_real_ios(allocated_ids)
    if not platform or platform == "android":
        rows += list_real_android(allocated_ids)

    if not rows:
        if args.json:
            _print_json([])
        else:
            print("No real devices connected.")
        return

    if args.json:
        _print_json([r.__dict__ for r in rows])
        return

    print(f"{'PLATFORM':<10} {'STATE':<8} {'DEVICE':<30} {'RUNTIME':<16} {'ID'}")
    print("─" * 96)
    for s in rows:
        print(f"{s.platform:<10} {'On' if s.booted else 'Off':<8} {s.device_name:<30} {s.runtime:<16} {s.sim_id}")


def cmd_list(args):
    allocated_ids = {a.sim_id for a in state.get_all().values()}
    platform = getattr(args, "platform", None)

    rows = []
    if not platform or platform == "ios":
        rows += list_ios(allocated_ids)
    if not platform or platform == "android":
        rows += list_android(allocated_ids)

    if not rows:
        if args.json:
            _print_json([])
        else:
            print("No available simulators.")
        return

    if args.json:
        _print_json([r.__dict__ for r in rows])
        return

    print(f"{'PLATFORM':<10} {'STATE':<8} {'DEVICE':<30} {'RUNTIME':<16} {'ID'}")
    print("─" * 96)
    for s in rows:
        print(f"{s.platform:<10} {'Booted' if s.booted else 'Off':<8} {s.device_name:<30} {s.runtime:<16} {s.sim_id}")


def cmd_boot(args):
    pass

def cmd_shutdown(args):
    pass

def cmd_animations(args):
    pass

def cmd_clipboard(args):
    pass

def cmd_focus(args):
    pass

def cmd_present(args):
    pass

def cmd_stabilize(args):
    pass

def cmd_ready(args):
    pass

def cmd_workspace_set(args):
    pass

def cmd_workspace_show(args):
    pass

def cmd_workspace_clear(args):
    pass

def cmd_workspace_apply(args):
    pass

def cmd_install(args):
    pass

def cmd_apps(args):
    pass

def cmd_launch(args):
    pass

def cmd_terminate(args):
    pass

def cmd_uninstall(args):
    pass

def cmd_screenshot(args):
    pass

def cmd_record(args):
    pass

def cmd_log(args):
    pass

def cmd_url(args):
    pass

def cmd_push(args):
    pass

def cmd_pull(args):
    pass

def cmd_add_media(args):
    pass

def cmd_push_notification(args):
    pass

def cmd_rename(args):
    pass

def cmd_delete(args):
    pass

def cmd_erase(args):
    pass

def cmd_env(args):
    pass

def cmd_check(args):
    pass

@contextmanager
def cmd_maestro(args):
    pass

def cmd_tap(args):
    pass

def cmd_swipe(args):
    pass

def cmd_appearance(args):
    pass

def cmd_shake(args):
    pass

def cmd_input(args):
    pass

def cmd_privacy(args):
    pass

def cmd_rotate(args):
    pass

def cmd_key(args):
    pass

def cmd_long_press(args):
    pass

def cmd_clear_data(args):
    pass

def cmd_status_bar(args):
    pass

def cmd_biometrics(args):
    pass

def cmd_reboot(args):
    pass

def cmd_network(args):
    pass

def cmd_battery(args):
    pass

def cmd_location(args):
    pass

def cmd_reset_app(args):
    pass

def cmd_crash_log(args):
    pass

def cmd_compare(args):
    pass

def cmd_create(args):
    from . import create as c

    if args.platform == "ios":
        if args.list_devices:
            devices = c.list_ios_device_types()
            if args.json:
                _print_json([{"name": d.name, "identifier": d.identifier} for d in devices])
            else:
                for d in devices:
                    print(f"{d.name:<40} {d.identifier}")
            return
        if args.list_runtimes:
            runtimes = c.list_ios_runtimes()
            if args.json:
                _print_json([{"name": r.name, "identifier": r.identifier} for r in runtimes])
            else:
                for r in runtimes:
                    print(f"{r.name:<20} {r.identifier}")
            return
        if not args.name or not args.device or not args.os:
            print("Usage: simemu create ios <name> --device <type> --os <runtime>", file=sys.stderr)
            print("       simemu create ios --list-devices", file=sys.stderr)
            print("       simemu create ios --list-runtimes", file=sys.stderr)
            sys.exit(1)
        udid = c.create_ios(args.name, args.device, args.os)
        if args.json:
            _print_json({"name": args.name, "udid": udid, "platform": "ios"})
        else:
            print(f"Created iOS simulator '{args.name}': {udid}")

    elif args.platform == "genymotion":
        from . import genymotion as gen
        if not gen.is_available():
            raise RuntimeError(
                "Genymotion is not installed. Download from genymotion.com\n"
                "  Expected: /Applications/Genymotion.app/Contents/MacOS/gmtool"
            )
        if args.list_hwprofiles:
            profiles = gen.list_hwprofiles()
            if args.json:
                _print_json(profiles)
            else:
                for p in profiles:
                    print(p.get("name", p))
            return
        if args.list_osimages:
            images = gen.list_osimages()
            if args.json:
                _print_json(images)
            else:
                for img in images:
                    print(img.get("name", img))
            return
        if not args.name or not args.hwprofile or not args.osimage:
            print("Usage: simemu create genymotion <name> --hwprofile <profile> --osimage <image>",
                  file=sys.stderr)
            print("       simemu create genymotion --list-hwprofiles", file=sys.stderr)
            print("       simemu create genymotion --list-osimages", file=sys.stderr)
            print("Note:  Listing profiles/images and CLI creation require a Genymotion license.",
                  file=sys.stderr)
            print("       Without a license, create VMs in the Genymotion UI instead.", file=sys.stderr)
            sys.exit(1)
        uuid = gen.create(args.hwprofile, args.osimage, args.name)
        if args.json:
            _print_json({"name": args.name, "uuid": uuid, "platform": "android", "backend": "genymotion"})
        else:
            print(f"Created Genymotion VM '{args.name}': {uuid}")
            print(f"Acquire with: simemu acquire android <slug> --device \"{args.name}\"")

    elif args.platform == "android":
        if args.list_images:
            images = c.list_android_system_images()
            if args.json:
                _print_json([i.__dict__ for i in images])
            else:
                for img in images:
                    print(f"API {img.api_level:<4} {img.tag:<25} {img.abi:<12} {img.package}")
            return
        if args.list_devices:
            devices = c.list_android_devices()
            if args.json:
                _print_json([d.__dict__ for d in devices])
            else:
                for d in devices:
                    print(f"{d.id:<30} {d.name}")
            return
        if not args.name or not args.api:
            print("Usage: simemu create android <avd-name> --api <level> [--device <profile>]", file=sys.stderr)
            print("       simemu create android --list-images", file=sys.stderr)
            print("       simemu create android --list-devices", file=sys.stderr)
            sys.exit(1)
        avd = c.create_android(
            avd_name=args.name,
            api_level=args.api,
            device_query=args.device or "medium_phone",
            tag=args.tag or "google_apis",
            abi=args.abi or "x86_64",
            force=args.force,
        )
        if args.json:
            _print_json({"name": avd, "platform": "android"})
        else:
            print(f"Created Android AVD: {avd}")


# ── argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="simemu",
        description="Simulator allocation manager for multi-agent development.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--version", action="version", version="simemu 0.3.0")
    p.add_argument("--no-autostart", action="store_true",
                   help="Do not auto-start the simemu API server for this invocation")
    sub = p.add_subparsers(dest="command", required=True)

    # ── v2 session-based commands ────────────────────────────────────────────

    # claim
    claim_p = sub.add_parser("claim", help="Claim a device session (v2 API)")
    claim_p.add_argument("platform", choices=["ios", "android", "macos"])
    claim_p.add_argument("--version", help="OS version (e.g. 26, 18, 15)")
    claim_p.add_argument("--form-factor", choices=["phone", "tablet", "watch", "tv", "vision"],
                         default="phone", help="Device form factor (default: phone)")
    claim_p.add_argument("--real", action="store_true",
                         help="Prefer real device over simulator")
    claim_p.add_argument("--show", action="store_true", dest="visible",
                         help="Keep simulator window visible (default: hidden)")
    claim_p.add_argument("--label", help="Human label for display (e.g. 'proof capture')")
    claim_p.set_defaults(func=cmd_claim)

    # do
    do_p = sub.add_parser("do", help="Execute a command on a claimed session (v2 API)")
    do_p.add_argument("session", help="Session ID (e.g. s-a7f3b2)")
    do_p.add_argument("do_command",
                      help="Command: build, install, launch, tap, swipe, screenshot, maestro, "
                           "url, done, renew, env, terminate, uninstall, input, long-press, "
                           "key, appearance, rotate, location, push, pull, add-media, "
                           "shake, status-bar")
    do_p.add_argument("extra", nargs=argparse.REMAINDER,
                      help="Arguments for the command")
    do_p.set_defaults(func=cmd_do)

    # config
    config_p = sub.add_parser("config", help="Configure simemu settings")
    config_sub = config_p.add_subparsers(dest="config_command", required=True)

    wm_p = config_sub.add_parser("window-mode", help="Set simulator window management mode")
    wm_p.add_argument("mode", nargs="?", choices=["hidden", "space", "corner", "display", "default"],
                       help="Window mode (omit to show current)")
    wm_p.add_argument("--display", type=int, help="Display index for 'display' mode")
    wm_p.add_argument("--corner", choices=["top-left", "top-right", "bottom-left", "bottom-right"],
                       help="Corner for 'corner' mode")
    wm_p.set_defaults(func=cmd_config)

    config_show_p = config_sub.add_parser("show", help="Show all config")
    config_show_p.set_defaults(func=cmd_config)

    config_disp_p = config_sub.add_parser("displays", help="List connected displays")
    config_disp_p.add_argument("--json", action="store_true")
    config_disp_p.set_defaults(func=cmd_config)

    # sessions
    sess_p = sub.add_parser("sessions", help="List all active v2 sessions")
    sess_p.add_argument("--json", action="store_true", help="Output as JSON")
    sess_p.set_defaults(func=cmd_sessions)

    # status (v2 — system overview)
    st = sub.add_parser("status", help="Show system overview: sessions, simulators, services")
    st.add_argument("--json", action="store_true", help="Output as JSON")
    st.set_defaults(func=cmd_status_overview)

    # ── legacy commands (backward compat) ────────────────────────────────────

    # acquire
    acq = sub.add_parser("acquire", help="Reserve a simulator or real device")
    acq.add_argument("platform", choices=["ios", "android", "watchos", "tvos", "visionos"])
    acq.add_argument("slug", help="Slug name, e.g. fitkind-app")
    acq.add_argument("--device", help="Partial device name filter, e.g. 'iPhone 16 Pro'")
    acq.add_argument("--real", action="store_true",
                     help="Acquire a connected real device instead of a simulator/emulator")
    acq.add_argument("--no-boot", action="store_true", help="Don't boot after acquiring")
    acq.add_argument("--window", action="store_true",
                     help="Android: show emulator window (default is headless/no-window)")
    acq.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                     help="Wait up to SECONDS for a simulator to become free (default: fail immediately)")
    acq.add_argument("--json", action="store_true", help="Output as JSON")
    acq.set_defaults(func=cmd_acquire)

    # release
    rel = sub.add_parser("release", help="Release a reserved simulator")
    rel.add_argument("slug")
    rel.set_defaults(func=cmd_release)

    # list
    ls = sub.add_parser("list", help="Show available (unreserved) simulators")
    ls.add_argument("platform", nargs="?", choices=["ios", "android", "watchos", "tvos", "visionos"])
    ls.add_argument("--json", action="store_true", help="Output as JSON")
    ls.set_defaults(func=cmd_list)

    # list-devices
    ld = sub.add_parser("list-devices", help="Show connected real devices (not simulators)")
    ld.add_argument("platform", nargs="?", choices=["ios", "android", "watchos", "tvos", "visionos"])
    ld.add_argument("--json", action="store_true", help="Output as JSON")
    ld.set_defaults(func=cmd_list_devices)

    # boot
    boot_p = sub.add_parser("boot", help="Boot the reserved simulator")
    boot_p.add_argument("slug")
    boot_p.add_argument("--window", action="store_true",
                        help="Android: show emulator window (default is headless/no-window)")
    boot_p.set_defaults(func=cmd_boot)

    # shutdown
    sd = sub.add_parser("shutdown", help="Shut down the reserved simulator")
    sd.add_argument("slug")
    sd.set_defaults(func=cmd_shutdown)

    # focus
    focus_p = sub.add_parser("focus", help="Bring the simulator window to front (iOS)")
    focus_p.add_argument("slug")
    focus_p.set_defaults(func=cmd_focus)

    # present
    present_p = sub.add_parser("present", help="Restore a simulator window into a known visible state (iOS)")
    present_p.add_argument("slug")
    present_p.add_argument("--save-layout", action="store_true",
                           help="Save the current iOS simulator window frame for this slug")
    present_p.add_argument("--clear-layout", action="store_true",
                           help="Clear any saved presentation layout for this slug")
    present_p.add_argument("--json", action="store_true", help="Output as JSON")
    present_p.set_defaults(func=cmd_present)

    # stabilize
    stabilize_p = sub.add_parser("stabilize", help="Preflight simulator readiness for interactive work")
    stabilize_p.add_argument("slug")
    stabilize_p.add_argument("--heal", action="store_true",
                             help="For iOS, restore the saved presentation layout before reporting readiness")
    stabilize_p.add_argument("--json", action="store_true", help="Output as JSON")
    stabilize_p.set_defaults(func=cmd_stabilize)

    # ready
    ready_p = sub.add_parser("ready", help="Run the recommended interactive preflight for a reserved simulator")
    ready_p.add_argument("slug")
    ready_p.add_argument("--json", action="store_true", help="Output as JSON")
    ready_p.set_defaults(func=cmd_ready)

    # workspace
    workspace_p = sub.add_parser("workspace", help="Manage a per-agent simulator workspace/display target")
    workspace_sub = workspace_p.add_subparsers(dest="workspace_command", required=True)

    workspace_set_p = workspace_sub.add_parser("set", help="Save the current desktop/display as this agent's workspace")
    workspace_set_p.add_argument("--json", action="store_true", help="Output as JSON")
    workspace_set_p.set_defaults(func=cmd_workspace_set)

    workspace_show_p = workspace_sub.add_parser("show", help="Show the saved workspace for this agent")
    workspace_show_p.add_argument("--json", action="store_true", help="Output as JSON")
    workspace_show_p.set_defaults(func=cmd_workspace_show)

    workspace_clear_p = workspace_sub.add_parser("clear", help="Clear the saved workspace for this agent")
    workspace_clear_p.add_argument("--json", action="store_true", help="Output as JSON")
    workspace_clear_p.set_defaults(func=cmd_workspace_clear)

    workspace_apply_p = workspace_sub.add_parser("apply", help="Move this agent's simulator windows into the saved workspace")
    workspace_apply_p.add_argument("slugs", nargs="*", help="Optional subset of reserved slugs to place")
    workspace_apply_p.add_argument("--json", action="store_true", help="Output as JSON")
    workspace_apply_p.set_defaults(func=cmd_workspace_apply)

    # animations
    anim_p = sub.add_parser("animations",
                            help="Enable or disable UI animations (off = stable Maestro flows)")
    anim_p.add_argument("slug")
    anim_p.add_argument("mode", choices=["on", "off"],
                        help="off: disable/slow animations for test stability; on: restore normal")
    anim_p.set_defaults(func=cmd_animations)

    # clipboard
    clip_p = sub.add_parser("clipboard", help="Read the simulator pasteboard (iOS only)")
    clip_p.add_argument("slug")
    clip_p.add_argument("--json", action="store_true", help="Output as JSON")
    clip_p.set_defaults(func=cmd_clipboard)

    # install
    inst = sub.add_parser("install", help="Install app (.app/.ipa for iOS, .apk for Android)")
    inst.add_argument("slug")
    inst.add_argument("app", help="Path to .app, .ipa, or .apk")
    inst.add_argument("--timeout", type=int, default=120, metavar="SECONDS",
                      help="Abort if install takes longer than SECONDS (default: 120). "
                           "Raises an error with reboot suggestion instead of hanging.")
    inst.set_defaults(func=cmd_install)

    # apps
    apps_p = sub.add_parser("apps", help="List installed apps on the simulator")
    apps_p.add_argument("slug")
    apps_p.add_argument("--json", action="store_true", help="Output as JSON")
    apps_p.set_defaults(func=cmd_apps)

    # launch
    launch_p = sub.add_parser("launch", help="Launch app by bundle ID or package name")
    launch_p.add_argument("slug")
    launch_p.add_argument("bundle_or_package")
    launch_p.add_argument("extra", nargs="*", help="Extra launch arguments")
    launch_p.set_defaults(func=cmd_launch)

    # terminate
    term = sub.add_parser("terminate", help="Force-stop a running app")
    term.add_argument("slug")
    term.add_argument("bundle_or_package")
    term.set_defaults(func=cmd_terminate)

    # uninstall
    uninst = sub.add_parser("uninstall", help="Remove an installed app")
    uninst.add_argument("slug")
    uninst.add_argument("bundle_or_package")
    uninst.set_defaults(func=cmd_uninstall)

    # screenshot
    ss = sub.add_parser("screenshot", help="Take a screenshot")
    ss.add_argument("slug")
    ss.add_argument("--output", "-o", help="Output path (default: ~/.simemu/<slug>_<timestamp>.png)")
    ss.add_argument("--format", "-f", choices=["png", "jpeg", "jpg", "tiff", "bmp", "gif"],
                    help="Image format (iOS only; default: png)")
    ss.add_argument("--max-size", type=int, metavar="PX",
                    help="Resize so longest dimension ≤ PX (e.g. 1000). "
                         "iOS screenshots are ~2600px; use 1000 for Claude's vision API. "
                         "Also set via SIMEMU_SCREENSHOT_MAX_SIZE env var.")
    ss.add_argument("--json", action="store_true", help="Output path as JSON")
    ss.set_defaults(func=cmd_screenshot)

    # record
    rec = sub.add_parser("record", help="Record video (start / stop)")
    rec.add_argument("action", choices=["start", "stop"])
    rec.add_argument("slug")
    rec.add_argument("--output", "-o", help="Output path for 'start' (default: auto)")
    rec.add_argument("--codec", choices=["hevc", "h264", "hevc-alpha"],
                     help="Video codec for 'start' (iOS only; default: hevc)")
    rec.add_argument("--json", action="store_true", help="Output as JSON")
    rec.set_defaults(func=cmd_record)

    # log
    log_p = sub.add_parser("log", help="Stream simulator logs (Ctrl-C to stop)")
    log_p.add_argument("slug")
    log_p.add_argument("--predicate", help="iOS: log predicate filter")
    log_p.add_argument("--tag", help="Android: logcat tag filter")
    log_p.add_argument("--level", help="iOS: debug/info/error  Android: V/D/I/W/E")
    log_p.set_defaults(func=cmd_log)

    # url
    url_p = sub.add_parser("url", help="Open a URL in the simulator")
    url_p.add_argument("slug")
    url_p.add_argument("url")
    url_p.set_defaults(func=cmd_url)

    # push (Android)
    push_p = sub.add_parser("push", help="Push a file to Android emulator (Android only)")
    push_p.add_argument("slug")
    push_p.add_argument("local", help="Local file path")
    push_p.add_argument("remote", help="Remote path on device")
    push_p.set_defaults(func=cmd_push)

    # pull (Android)
    pull_p = sub.add_parser("pull", help="Pull a file from Android emulator (Android only)")
    pull_p.add_argument("slug")
    pull_p.add_argument("remote", help="Remote path on device")
    pull_p.add_argument("local", help="Local destination path")
    pull_p.set_defaults(func=cmd_pull)

    # add-media (iOS + Android)
    media = sub.add_parser("add-media", help="Add a photo/video to the device Photos/Gallery library")
    media.add_argument("slug")
    media.add_argument("file", help="Path to image or video file")
    media.set_defaults(func=cmd_add_media)

    # push-notification (iOS)
    pn = sub.add_parser("push-notification", help="Send a push notification (iOS only)")
    pn.add_argument("slug")
    pn.add_argument("bundle_id")
    pn.add_argument("payload", help="Path to JSON payload file")
    pn.set_defaults(func=cmd_push_notification)

    # reset-app
    ra_p = sub.add_parser("reset-app",
                          help="Force-stop + clear app data + relaunch in one command")
    ra_p.add_argument("slug")
    ra_p.add_argument("bundle_or_package", help="Bundle ID (iOS) or package name (Android)")
    ra_p.add_argument("--no-launch", action="store_true",
                      help="Clear data but don't relaunch the app")
    ra_p.set_defaults(func=cmd_reset_app)

    # crash-log
    cl_p = sub.add_parser("crash-log",
                          help="Show the most recent crash log for the simulator or a specific app")
    cl_p.add_argument("slug")
    cl_p.add_argument("--bundle", metavar="ID",
                      help="Filter to crashes from this bundle ID / package name")
    cl_p.add_argument("--since", type=int, default=60, metavar="MINUTES",
                      help="Look back this many minutes (default: 60)")
    cl_p.add_argument("--json", action="store_true", help="Output as JSON")
    cl_p.set_defaults(func=cmd_crash_log)

    # compare
    cmp_p = sub.add_parser("compare",
                           help="Screenshot two slugs and composite them side by side")
    cmp_p.add_argument("slug_a", help="First simulator slug")
    cmp_p.add_argument("slug_b", help="Second simulator slug")
    cmp_p.add_argument("--output", "-o", help="Output path (default: auto)")
    cmp_p.add_argument("--max-size", type=int, metavar="PX", default=1000,
                       help="Resize each screenshot so longest dimension ≤ PX before compositing (default: 1000)")
    cmp_p.add_argument("--json", action="store_true", help="Output path(s) as JSON")
    cmp_p.set_defaults(func=cmd_compare)

    # erase
    erase = sub.add_parser("erase", help="Factory reset a simulator (keeps the simulator)")
    erase.add_argument("slug")
    erase.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    erase.set_defaults(func=cmd_erase)

    # rename
    rename_p = sub.add_parser("rename", help="Rename a simulator or Android AVD")
    rename_p.add_argument("slug")
    rename_p.add_argument("name", help="New display name")
    rename_p.set_defaults(func=cmd_rename)

    # delete
    delete_p = sub.add_parser("delete", help="Permanently remove a simulator or Android AVD")
    delete_p.add_argument("slug")
    delete_p.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    delete_p.set_defaults(func=cmd_delete)

    # env
    env_p = sub.add_parser("env", help="Show device info for a reserved simulator (JSON)")
    env_p.add_argument("slug")
    env_p.set_defaults(func=cmd_env)

    # check
    check_p = sub.add_parser("check", help="Verify a simulator is booted and ready (run before adb/install flows)")
    check_p.add_argument("slug")
    check_p.add_argument("--bundle", metavar="ID",
                         help="Also verify this app bundle/package is in the foreground")
    check_p.add_argument("--json", action="store_true")
    check_p.set_defaults(func=cmd_check)

    # maestro
    maestro_p = sub.add_parser(
        "maestro",
        help="Run a Maestro flow with the correct --device flag resolved automatically",
    )
    maestro_p.add_argument("slug")
    maestro_p.add_argument("flow", nargs="+", help="Path(s) to Maestro YAML flow file(s)")
    maestro_p.add_argument("extra", nargs=argparse.REMAINDER,
                           help="Extra args passed through to maestro test")
    maestro_p.set_defaults(func=cmd_maestro)

    # tap
    tap_p = sub.add_parser("tap", help="Tap a coordinate on a reserved simulator screen")
    tap_p.add_argument("slug")
    tap_p.add_argument("x", type=float, help="X coordinate (pixels) or fraction 0-1 with --pct")
    tap_p.add_argument("y", type=float, help="Y coordinate (pixels) or fraction 0-1 with --pct")
    tap_p.add_argument("--pct", action="store_true",
                       help="Treat x/y as fractions of screen size (0.0–1.0) instead of pixels. "
                            "Eliminates coordinate scaling errors across devices.")
    tap_p.set_defaults(func=cmd_tap)

    # swipe
    swipe_p = sub.add_parser("swipe", help="Swipe gesture on a reserved simulator screen")
    swipe_p.add_argument("slug")
    swipe_p.add_argument("x1", type=float, help="Start X coordinate (pixels or fraction with --pct)")
    swipe_p.add_argument("y1", type=float, help="Start Y coordinate (pixels or fraction with --pct)")
    swipe_p.add_argument("x2", type=float, help="End X coordinate (pixels or fraction with --pct)")
    swipe_p.add_argument("y2", type=float, help="End Y coordinate (pixels or fraction with --pct)")
    swipe_p.add_argument("--duration", type=int, default=300, metavar="MS",
                         help="Gesture duration in milliseconds (default: 300)")
    swipe_p.add_argument("--pct", action="store_true",
                         help="Treat coordinates as fractions of screen size (0.0–1.0).")
    swipe_p.set_defaults(func=cmd_swipe)

    # rotate
    rot_p = sub.add_parser("rotate", help="Set device orientation")
    rot_p.add_argument("slug")
    rot_p.add_argument("orientation", choices=["portrait", "landscape", "left", "right"],
                       help="portrait | landscape | left | right")
    rot_p.set_defaults(func=cmd_rotate)

    # key
    key_p = sub.add_parser("key", help="Press a hardware key (home, back, lock, etc.)")
    key_p.add_argument("slug")
    key_p.add_argument("key", help="iOS: home|lock|siri|screenshot|paste  Android: home|back|menu|volume_up|volume_down|…")
    key_p.set_defaults(func=cmd_key)

    # long-press
    lp_p = sub.add_parser("long-press", help="Long-press a coordinate on the simulator screen")
    lp_p.add_argument("slug")
    lp_p.add_argument("x", type=float, help="X coordinate (pixels or fraction with --pct)")
    lp_p.add_argument("y", type=float, help="Y coordinate (pixels or fraction with --pct)")
    lp_p.add_argument("--duration", type=int, default=1000, metavar="MS",
                      help="Hold duration in milliseconds (default: 1000)")
    lp_p.add_argument("--pct", action="store_true",
                      help="Treat x/y as fractions of screen size (0.0–1.0).")
    lp_p.set_defaults(func=cmd_long_press)

    # clear-data
    cd_p = sub.add_parser("clear-data", help="Clear all app data (Android only)")
    cd_p.add_argument("slug")
    cd_p.add_argument("package", help="Package name, e.g. com.example.app")
    cd_p.set_defaults(func=cmd_clear_data)

    # status-bar
    sb_p = sub.add_parser("status-bar", help="Override status bar for clean screenshots")
    sb_p.add_argument("slug")
    sb_p.add_argument("--time", metavar="HH:MM", help="Clock display, e.g. 9:41")
    sb_p.add_argument("--battery", type=int, metavar="0-100")
    sb_p.add_argument("--wifi", type=int, metavar="0-3", help="WiFi bars (0-3 iOS, 0-4 Android)")
    sb_p.add_argument("--network", help="iOS: wifi|5g|4g|lte|3g|2g|edge|none")
    sb_p.add_argument("--clear", action="store_true", help="Restore the real status bar")
    sb_p.set_defaults(func=cmd_status_bar)

    # biometrics
    bio_p = sub.add_parser("biometrics", help="Simulate Face ID / Touch ID / fingerprint")
    bio_p.add_argument("slug")
    bio_p.add_argument("result", choices=["match", "fail"],
                       help="match = successful auth, fail = rejected")
    bio_p.set_defaults(func=cmd_biometrics)

    # reboot
    reboot_p = sub.add_parser("reboot", help="Reboot a reserved simulator (faster than release+acquire)")
    reboot_p.add_argument("slug")
    reboot_p.set_defaults(func=cmd_reboot)

    # network (Android only)
    net_p = sub.add_parser("network", help="Set network connectivity mode (Android only)")
    net_p.add_argument("slug")
    net_p.add_argument("mode", choices=["airplane", "all", "wifi", "data", "none"],
                       help="airplane=all off, all=restore, wifi=wifi only, data=data only, none=both off")
    net_p.set_defaults(func=cmd_network)

    # battery (Android only)
    bat_p = sub.add_parser("battery", help="Override battery level for screenshots (Android only)")
    bat_p.add_argument("slug")
    bat_p.add_argument("--level", type=int, metavar="0-100", help="Battery percentage to display")
    bat_p.add_argument("--reset", action="store_true", help="Restore real battery level")
    bat_p.set_defaults(func=cmd_battery)

    # appearance
    app_p = sub.add_parser("appearance", help="Set light or dark mode on a reserved simulator")
    app_p.add_argument("slug")
    app_p.add_argument("mode", choices=["light", "dark"], help="Appearance mode")
    app_p.set_defaults(func=cmd_appearance)

    # shake
    shake_p = sub.add_parser("shake", help="Send shake gesture (triggers React Native dev menu)")
    shake_p.add_argument("slug")
    shake_p.set_defaults(func=cmd_shake)

    # input
    input_p = sub.add_parser("input", help="Type text into focused field (Android) or set pasteboard (iOS)")
    input_p.add_argument("slug")
    input_p.add_argument("text", help="Text to type / paste")
    input_p.set_defaults(func=cmd_input)

    # privacy
    priv_p = sub.add_parser("privacy", help="Grant or revoke app permission")
    priv_p.add_argument("slug")
    priv_p.add_argument("action", choices=["grant", "revoke", "reset"],
                        help="grant / revoke / reset (iOS only)")
    priv_p.add_argument("bundle_or_package", help="Bundle ID (iOS) or package name (Android)")
    priv_p.add_argument("permission",
                        help="iOS: photos/camera/microphone/location/contacts/…  "
                             "Android: CAMERA / android.permission.CAMERA / …")
    priv_p.set_defaults(func=cmd_privacy)

    # location
    loc_p = sub.add_parser("location", help="Set or clear GPS location override")
    loc_p.add_argument("slug")
    loc_p.add_argument("lat", type=float, nargs="?", help="Latitude")
    loc_p.add_argument("lng", type=float, nargs="?", help="Longitude")
    loc_p.add_argument("--clear", action="store_true", help="Clear the location override (iOS only)")
    loc_p.set_defaults(func=cmd_location)

    # create
    cr = sub.add_parser("create", help="Create a new simulator or emulator")
    cr_sub = cr.add_subparsers(dest="platform", required=True)

    cr_ios = cr_sub.add_parser("ios", help="Create iOS simulator")
    cr_ios.add_argument("name", nargs="?", help="Name for the new simulator")
    cr_ios.add_argument("--device", help="Device type, e.g. 'iPhone 16 Pro'")
    cr_ios.add_argument("--os", help="Runtime, e.g. 'iOS 18' or '18.0'")
    cr_ios.add_argument("--list-devices", action="store_true", help="List available device types")
    cr_ios.add_argument("--list-runtimes", action="store_true", help="List installed runtimes")
    cr_ios.add_argument("--json", action="store_true", help="Output as JSON")
    cr_ios.set_defaults(func=cmd_create)

    cr_gen = cr_sub.add_parser("genymotion", help="Create Genymotion VM (requires Genymotion Desktop)")
    cr_gen.add_argument("name", nargs="?", help="Name for the new VM")
    cr_gen.add_argument("--hwprofile", help="Hardware profile name or UUID (e.g. 'Samsung Galaxy S24')")
    cr_gen.add_argument("--osimage", help="OS image name or Android version (e.g. '14.0')")
    cr_gen.add_argument("--list-hwprofiles", action="store_true", help="List available hardware profiles (requires license)")
    cr_gen.add_argument("--list-osimages", action="store_true", help="List available OS images (requires license)")
    cr_gen.add_argument("--json", action="store_true", help="Output as JSON")
    cr_gen.set_defaults(func=cmd_create)

    cr_and = cr_sub.add_parser("android", help="Create Android AVD")
    cr_and.add_argument("name", nargs="?", help="AVD name")
    cr_and.add_argument("--api", type=int, help="Android API level, e.g. 35")
    cr_and.add_argument("--device", help="Hardware profile, e.g. 'medium_phone', 'pixel_6'")
    cr_and.add_argument("--tag", help="System image tag (default: google_apis)")
    cr_and.add_argument("--abi", help="CPU ABI (default: x86_64)")
    cr_and.add_argument("--force", action="store_true", help="Overwrite existing AVD")
    cr_and.add_argument("--list-images", action="store_true", help="List installed system images")
    cr_and.add_argument("--list-devices", action="store_true", help="List hardware profiles")
    cr_and.add_argument("--json", action="store_true", help="Output as JSON")
    cr_and.set_defaults(func=cmd_create)

    # serve
    serve_p = sub.add_parser("serve", help="Start the HTTP API server (with idle-shutdown)")
    serve_p.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=_SIMEMU_PORT, help=f"Port (default: {_SIMEMU_PORT})")
    serve_p.add_argument("--idle-timeout", type=int, default=None, metavar="MINUTES",
                         help="Shut down idle simulators after N minutes (default: 20, env: SIMEMU_IDLE_TIMEOUT)")
    serve_p.set_defaults(func=cmd_serve)

    # idle-shutdown (one-shot, no daemon needed)
    idle_p = sub.add_parser("idle-shutdown",
                             help="Shut down simulators idle longer than N minutes (one-shot)")
    idle_p.add_argument("--after", type=int, default=20, metavar="MINUTES",
                        help="Idle threshold in minutes (default: 20)")
    idle_p.set_defaults(func=cmd_idle_shutdown)

    # daemon
    daemon_p = sub.add_parser("daemon", help="Manage the simemu background daemon (macOS launchd)")
    daemon_p.add_argument("action", choices=["install", "uninstall", "status"])
    daemon_p.add_argument("--idle-timeout", type=int, default=20, metavar="MINUTES",
                          help="Idle-shutdown timeout in minutes for 'install' (default: 20)")
    daemon_p.set_defaults(func=cmd_daemon)

    # maintenance
    maint_p = sub.add_parser("maintenance",
                              help="Enter/exit maintenance mode (blocks acquire/release during migration)")
    maint_p.add_argument("action", choices=["on", "off", "status"])
    maint_p.add_argument("--message", "-m", help="Message shown to blocked callers")
    maint_p.add_argument("--eta", type=int, metavar="MINUTES",
                         help="Estimated time until maintenance is done (default: 5)")
    maint_p.set_defaults(func=cmd_maintenance)

    # menubar
    mb_p = sub.add_parser("menubar", help="Launch or manage the macOS menu bar status app")
    mb_p.add_argument(
        "action",
        nargs="?",
        choices=["install", "uninstall", "status"],
        default=None,
        help="install/uninstall/status — manage the LaunchAgent that auto-starts the menu bar on login",
    )
    mb_p.set_defaults(func=cmd_menubar)

    return p


def cmd_serve(args):
    from .server import serve
    timeout = getattr(args, "idle_timeout", None)
    if timeout is not None:
        os.environ["SIMEMU_IDLE_TIMEOUT"] = str(timeout)
    current = os.environ.get("SIMEMU_IDLE_TIMEOUT", "20")
    print(f"Starting simemu API server on http://{args.host}:{args.port}")
    print(f"  OpenAPI docs: http://{args.host}:{args.port}/docs")
    print(f"  Idle-shutdown: {current} minutes")
    serve(host=args.host, port=args.port)


def cmd_idle_shutdown(args):
    """One-shot idle shutdown — shuts down simulators idle longer than --after minutes."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    shut = []
    for slug, alloc in state.get_all().items():
        if not alloc.heartbeat_at:
            continue
        last = datetime.fromisoformat(alloc.heartbeat_at)
        idle_min = (now - last).total_seconds() / 60
        if idle_min >= args.after:
            print(f"Shutting down '{slug}' ({alloc.device_name}) — idle {idle_min:.0f}m", flush=True)
            try:
                if alloc.platform == "ios":
                    ios.shutdown(alloc.sim_id)
                else:
                    android.shutdown(alloc.sim_id)
                shut.append(slug)
            except Exception as e:
                print(f"Warning: could not shut down '{slug}': {e}", file=sys.stderr)
    if not shut:
        print(f"No simulators idle longer than {args.after} minutes.")
    else:
        print(f"Shut down {len(shut)} simulator(s): {', '.join(shut)}")


def cmd_daemon(args):
    """Manage the simemu background daemon (launchd agent on macOS)."""
    import shutil
    import subprocess as _sp
    import urllib.request
    label = "com.simemu.daemon"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if args.action == "install":
        simemu_bin = shutil.which("simemu")
        if not simemu_bin:
            raise RuntimeError(
                "simemu binary not found on PATH.\n"
                "Install simemu first: pip install -e ~/dev/simemu/"
            )
        timeout = args.idle_timeout
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{simemu_bin}</string>
        <string>serve</string>
        <string>--idle-timeout</string>
        <string>{timeout}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/simemu/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/simemu/daemon.log</string>
</dict>
</plist>
"""
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        _sp.run(["launchctl", "load", "-w", str(plist_path)], check=False)
        print(f"simemu daemon installed and started.")
        print(f"  Idle-shutdown timeout: {timeout} minutes")
        print(f"  Logs:  /tmp/simemu/daemon.log")
        print(f"  Plist: {plist_path}")

    elif args.action == "uninstall":
        if plist_path.exists():
            _sp.run(["launchctl", "unload", "-w", str(plist_path)], check=False)
            plist_path.unlink()
            print("simemu daemon stopped and removed.")
        else:
            print("simemu daemon is not installed.")

    elif args.action == "status":
        manual_server = None
        for url in (f"http://127.0.0.1:{_SIMEMU_PORT}/health", f"http://127.0.0.1:{_SIMEMU_PORT}/status"):
            try:
                with urllib.request.urlopen(url, timeout=1.5) as resp:
                    manual_server = {
                        "url": url.rsplit("/", 1)[0],
                        "status": resp.status,
                    }
                    break
            except Exception:
                continue

        result = _sp.run(
            ["launchctl", "list", label],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"simemu daemon is RUNNING  (launchd label: {label})")
            print(f"  Logs: /tmp/simemu/daemon.log")
            if plist_path.exists():
                print(f"  Plist: {plist_path}")
        else:
            print("simemu daemon is NOT running.")
            if plist_path.exists():
                print(f"  Plist exists ({plist_path}) — run 'simemu daemon install' to start it.")
        if manual_server:
            print(f"simemu API server is RUNNING  ({manual_server['url']})")
            print("  Note: this is a live server process, not the launchd-managed daemon.")


def _find_swift_menubar_app() -> Path | None:
    """Find the SimEmuBar .app bundle or bare binary."""
    swift_dir = Path(__file__).parent / "swift"
    # Prefer .app bundle (required for menu bar rendering)
    app_candidates = [
        Path("/Applications/SimEmuBar.app"),
        swift_dir / ".build" / "SimEmuBar.app",
    ]
    for p in app_candidates:
        if p.exists() and (p / "Contents" / "MacOS" / "SimEmuBar").exists():
            return p
    return None


def cmd_menubar(args):
    """Launch the macOS menu bar status app (SwiftUI or rumps fallback)."""
    import subprocess as sp

    action = getattr(args, "action", None)
    label = "com.simemu.menubar"
    plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"

    if action in ("install", "uninstall", "status"):
        if action == "install":
            app_bundle = _find_swift_menubar_app()
            if not app_bundle:
                raise RuntimeError(
                    "SimEmuBar.app not found.\n"
                    "Build it first: cd ~/dev/simemu/simemu/swift && swift build -c release\n"
                    "Or install to /Applications/SimEmuBar.app"
                )
            binary = app_bundle / "Contents" / "MacOS" / "SimEmuBar"
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{binary}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/simemu/menubar.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/simemu/menubar.log</string>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
</dict>
</plist>
"""
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            Path("/tmp/simemu").mkdir(parents=True, exist_ok=True)
            plist_path.write_text(plist_content)
            sp.run(["launchctl", "unload", "-w", str(plist_path)], capture_output=True)
            sp.run(["launchctl", "load", "-w", str(plist_path)], check=False)
            print(f"SimEmuBar LaunchAgent installed and started.")
            print(f"  App:   {app_bundle}")
            print(f"  Logs:  /tmp/simemu/menubar.log")
            print(f"  Plist: {plist_path}")

        elif action == "uninstall":
            if plist_path.exists():
                sp.run(["launchctl", "unload", "-w", str(plist_path)], check=False)
                plist_path.unlink()
                print("SimEmuBar LaunchAgent stopped and removed.")
            else:
                print("SimEmuBar LaunchAgent is not installed.")

        elif action == "status":
            result = sp.run(
                ["launchctl", "list", label],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                print(f"SimEmuBar LaunchAgent is RUNNING  (label: {label})")
                if plist_path.exists():
                    print(f"  Plist: {plist_path}")
            else:
                print("SimEmuBar LaunchAgent is NOT running.")
                if plist_path.exists():
                    print(f"  Plist exists — run 'simemu menubar install' to start it.")
                else:
                    print("  Run 'simemu menubar install' to set it up.")
        return

    # No action — just launch it now
    app_bundle = _find_swift_menubar_app()
    if app_bundle:
        sp.run(["open", str(app_bundle)], check=False)
        return

    # Fallback to rumps-based menubar
    try:
        from .ui.menubar import main as menubar_main
    except ImportError as e:
        raise RuntimeError(
            f"Menu bar requires either the Swift build (cd simemu/swift && swift build -c release) "
            f"or rumps: pip install rumps\n({e})"
        ) from None
    menubar_main()


def cmd_maintenance(args):
    """Enter or exit maintenance mode."""
    if args.action == "on":
        msg = args.message or "simemu is temporarily unavailable — migrating emulators to Genymotion"
        eta = args.eta or 5
        state.enter_maintenance(msg, eta)
        print(f"Maintenance mode ON: {msg} (~{eta} min)")
    elif args.action == "off":
        state.exit_maintenance()
        print("Maintenance mode OFF — simemu is available again.")
    elif args.action == "status":
        mf = state.maintenance_file()
        if mf.exists():
            import json as _json
            data = _json.loads(mf.read_text())
            print(f"MAINTENANCE MODE ACTIVE")
            print(f"  Message: {data.get('message', '')}")
            print(f"  ETA: ~{data.get('eta_minutes', '?')} minutes")
            print(f"  Since: {data.get('started_at', '?')}")
        else:
            print("Maintenance mode is OFF.")


# Maintenance-exempt commands (can run during maintenance)
_MAINTENANCE_EXEMPT = {"cmd_status", "cmd_status_overview", "cmd_sessions", "cmd_config", "cmd_maintenance", "cmd_serve", "cmd_daemon", "cmd_menubar"}

# v2 + admin commands — everything else is legacy and rejected
_V2_COMMANDS = {
    "cmd_claim", "cmd_do", "cmd_sessions", "cmd_config",
    "cmd_serve", "cmd_daemon", "cmd_maintenance", "cmd_menubar",
    "cmd_create", "cmd_idle_shutdown",
    "cmd_list", "cmd_list_devices",  # discovery is still useful
    "cmd_status_overview",  # v2 system overview
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "no_autostart", False):
        os.environ["SIMEMU_NO_AUTOSTART"] = "1"
    if getattr(args.func, "__name__", "") not in {"cmd_serve", "cmd_daemon"}:
        _autostart_server_if_needed()
    try:
        func_name = getattr(args.func, "__name__", "")
        # Reject legacy slug-based commands
        if func_name not in _V2_COMMANDS and func_name not in _MAINTENANCE_EXEMPT:
            _reject_legacy(args)
        # Check maintenance mode for non-exempt commands
        if func_name not in _MAINTENANCE_EXEMPT:
            state.check_maintenance()
        args.func(args)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
