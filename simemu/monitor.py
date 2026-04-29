#!/usr/bin/env python3
"""
simemu monitor — runs every 60s via launchd.

Manages session lifecycle, reconnects offline adb, ensures server is running,
recovers stale sessions after reboot, prunes expired sessions, and writes
a heartbeat for the watchdog.

Logs to ~/.simemu/monitor.log
"""

import json
import os
import subprocess
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def _resolve_port() -> int:
    env_val = os.environ.get("SIMEMU_PORT", "")
    try:
        port = int(env_val)
        if 1 <= port <= 65535:
            return port
    except (ValueError, TypeError):
        pass
    try:
        cfg = json.loads((Path.home() / ".fed" / "config.json").read_text())
        dash = cfg.get("tools", {}).get("simemu", {}).get("dash")
        if isinstance(dash, int) and dash > 0:
            return dash
    except Exception:
        pass
    return 7803


_SIMEMU_PORT = _resolve_port()

LOG = Path.home() / ".simemu" / "monitor.log"
HEARTBEAT = Path.home() / ".simemu" / "monitor.heartbeat"
LOG.parent.mkdir(parents=True, exist_ok=True)

# T-21: Max expired/released sessions to keep before pruning
MAX_HISTORY = 50

# UI-driving tools should never survive without an owning simemu session. A
# stale Maestro process can keep tapping the desktop for hours after its parent
# agent is gone, so the monitor reaps orphaned/overlong helpers every tick.
UI_PROCESS_MAX_AGE_SECONDS = int(os.environ.get("SIMEMU_UI_PROCESS_MAX_AGE_SECONDS", "1800"))

# Runaway emulator CPU thresholds
# Sustained: if a qemu/emulator process is above this CPU% for longer than
# RUNAWAY_GRACE_SECONDS, it gets killed. Covers slow-burn runaways.
RUNAWAY_CPU_THRESHOLD = int(os.environ.get("SIMEMU_RUNAWAY_CPU_THRESHOLD", "800"))
RUNAWAY_GRACE_SECONDS = int(os.environ.get("SIMEMU_RUNAWAY_GRACE_SECONDS", "300"))
# Immediate: if a qemu/emulator process is above this CPU%, kill it right away
# regardless of age. Covers "entire machine is locked up" scenarios.
RUNAWAY_CPU_CRITICAL = int(os.environ.get("SIMEMU_RUNAWAY_CPU_CRITICAL", "1200"))


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"{ts} {msg}\n"
    with LOG.open("a") as f:
        f.write(line)


def run():
    """Single monitor tick — must complete in <30s."""
    # Hard timeout so we never hang
    signal.signal(signal.SIGALRM, lambda *_: sys.exit(1))
    signal.alarm(30)

    issues = []

    # T-28: Write heartbeat for watchdog
    try:
        HEARTBEAT.write_text(datetime.now(timezone.utc).isoformat())
    except Exception:
        pass

    # 1. Lifecycle tick
    try:
        from simemu.session import lifecycle_tick
        changed = lifecycle_tick()
        if changed:
            log(f"lifecycle: transitioned {changed}")
    except Exception as e:
        log(f"lifecycle error: {e}")

    # T-14: Recover stale sessions after reboot
    try:
        _recover_stale_sessions()
    except Exception as e:
        log(f"stale recovery error: {e}")

    # 2. Fix offline adb
    try:
        out = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=5,
        )
        for line in out.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "offline":
                serial = parts[0]
                log(f"adb: {serial} offline, reconnecting")
                subprocess.run(["adb", "disconnect", serial], capture_output=True, timeout=5)
                time.sleep(1)
                subprocess.run(["adb", "connect", serial], capture_output=True, timeout=10)
                issues.append(f"reconnected {serial}")
    except Exception as e:
        log(f"adb error: {e}")

    # 3. Ensure server
    try:
        with socket.create_connection(("127.0.0.1", _SIMEMU_PORT), timeout=1):
            pass
    except OSError:
        log("server: not running, starting")
        try:
            subprocess.Popen(
                [sys.executable, "-m", "simemu.cli", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        except Exception as e:
            log(f"server start failed: {e}")

    # 3b. Reap orphaned UI automation/screenshot helpers.
    try:
        killed = _reap_orphan_ui_processes()
        if killed:
            issues.extend(f"reaped {item}" for item in killed)
    except Exception as e:
        log(f"ui reaper error: {e}")

    # 3c. Kill runaway emulator processes (CPU starvation guard).
    try:
        killed = _check_runaway_emulators()
        if killed:
            issues.extend(f"killed {item}" for item in killed)
    except Exception as e:
        log(f"runaway check error: {e}")

    # T-21: Prune old expired/released sessions
    try:
        _prune_old_sessions()
    except Exception as e:
        log(f"prune error: {e}")

    # 4. Summary
    try:
        from simemu.session import get_active_sessions
        sessions = get_active_sessions()
        active = sum(1 for s in sessions.values() if s.status == "active")
        idle = sum(1 for s in sessions.values() if s.status == "idle")
        parked = sum(1 for s in sessions.values() if s.status == "parked")
        log(f"ok: {len(sessions)} sessions (a={active} i={idle} p={parked})")
    except Exception as e:
        log(f"summary error: {e}")

    if issues:
        log(f"fixed: {issues}")

    signal.alarm(0)


def _recover_stale_sessions() -> None:
    """T-14: Detect sessions marked active/idle whose simulator is no longer running.

    After a reboot, all simulators are gone but sessions stay in sessions.json.
    Park these sessions so they don't confuse agents.
    """
    from simemu.session import get_active_sessions, _locked_sessions

    sessions = get_active_sessions()
    stale = []

    for sid, session in sessions.items():
        if session.status == "parked":
            continue  # already parked

        # Check if the simulator is actually running
        if session.platform in ("ios", "watchos", "tvos", "visionos"):
            try:
                out = subprocess.run(
                    ["xcrun", "simctl", "list", "devices", "booted", "--json"],
                    capture_output=True, text=True, timeout=5,
                )
                data = json.loads(out.stdout)
                booted_udids = set()
                for runtime_devs in data.get("devices", {}).values():
                    for dev in runtime_devs:
                        if dev.get("state") == "Booted":
                            booted_udids.add(dev["udid"])
                if session.sim_id not in booted_udids:
                    stale.append(sid)
            except Exception:
                pass  # can't verify — leave it
        else:
            # Android: check if emulator process is running
            try:
                out = subprocess.run(
                    ["adb", "devices"], capture_output=True, text=True, timeout=5,
                )
                if session.sim_id not in out.stdout:
                    stale.append(sid)
            except Exception:
                pass

    if stale:
        with _locked_sessions() as (data, save):
            for sid in stale:
                if sid in data["sessions"] and data["sessions"][sid].get("status") in ("active", "idle"):
                    data["sessions"][sid]["status"] = "parked"
                    log(f"stale: parked {sid} (simulator not running)")
            save(data)


def _parse_etime_seconds(value: str) -> int:
    """Parse ps etime values like [[dd-]hh:]mm:ss into seconds."""
    value = value.strip()
    days = 0
    if "-" in value:
        day_part, value = value.split("-", 1)
        days = int(day_part)
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        return 0
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _iter_process_rows() -> list[tuple[int, int, str]]:
    """Return process rows as (pid, age_seconds, command)."""
    out = subprocess.run(
        ["ps", "axww", "-o", "pid=", "-o", "etime=", "-o", "command="],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    rows: list[tuple[int, int, str]] = []
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        try:
            rows.append((int(parts[0]), _parse_etime_seconds(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def _session_id_from_command(command: str) -> str | None:
    import re

    match = re.search(r"\bsimemu\.cli\s+do\s+(s-[0-9a-fA-F]{6})\s+maestro\b", command)
    if match:
        return match.group(1)

    match = re.search(r"/maestro-debug/(s-[0-9a-fA-F]{6})_", command)
    if match:
        return match.group(1)

    return None


def _kill_process(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def _check_runaway_emulators() -> list[str]:
    """Kill qemu/emulator processes consuming excessive CPU.

    Two tiers:
    - CRITICAL (>1200%): killed immediately — these pin all cores and lock up
      the machine. A normal Android emulator boot peaks around 200-400%.
    - SUSTAINED (>800% for >5 min): killed after a grace period. Covers slow
      burns that degrade the machine over time.

    Returns a list of kill descriptions for logging.
    """
    try:
        result = subprocess.run(
            ["ps", "axww", "-o", "pid=", "-o", "pcpu=", "-o", "etime=", "-o", "comm="],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    killed: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) != 4:
            continue
        pid_str, cpu_str, etime_str, comm = parts

        # Only target emulator processes
        if "qemu-system" not in comm and "emulator" not in comm.lower():
            continue

        try:
            pid = int(pid_str)
            cpu_pct = float(cpu_str)
            age_seconds = _parse_etime_seconds(etime_str)
        except ValueError:
            continue

        if pid == os.getpid():
            continue

        reason = None
        if cpu_pct >= RUNAWAY_CPU_CRITICAL:
            reason = f"cpu={cpu_pct:.0f}% >= critical {RUNAWAY_CPU_CRITICAL}%"
        elif cpu_pct >= RUNAWAY_CPU_THRESHOLD and age_seconds >= RUNAWAY_GRACE_SECONDS:
            reason = f"cpu={cpu_pct:.0f}% >= {RUNAWAY_CPU_THRESHOLD}% for {age_seconds}s"

        if not reason:
            continue

        log(f"runaway: killing {pid} ({comm}): {reason}")
        # SIGTERM first, then SIGKILL if still alive after 1s
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            continue

        time.sleep(1)
        try:
            os.kill(pid, 0)
            # Still alive — escalate to SIGKILL
            os.kill(pid, signal.SIGKILL)
            killed.append(f"{pid}:{comm} ({reason}, SIGKILL)")
        except ProcessLookupError:
            killed.append(f"{pid}:{comm} ({reason}, SIGTERM)")

    return killed


def _reap_orphan_ui_processes(max_age_seconds: int = UI_PROCESS_MAX_AGE_SECONDS) -> list[str]:
    """Kill stale UI-driving helpers when no live session owns them.

    This intentionally targets narrow process signatures:
    - `simemu do <session> maestro ...`
    - Maestro's Java runner carrying simemu's debug-output session id
    - long-lived `screencaptureui`, which can keep stealing focus after a proof
      capture is abandoned
    - stale AppleScript/System Events UI loops that query/click the frontmost
      app every few seconds after an agent is interrupted
    It does not kill normal app daemons, Metro, or passive simemu services.
    """
    from simemu.session import get_active_sessions

    active_sessions = get_active_sessions()
    active_ids = set(active_sessions.keys())
    killed: list[str] = []

    for pid, age_seconds, command in _iter_process_rows():
        if pid == os.getpid():
            continue

        session_id = _session_id_from_command(command)
        is_session_maestro = session_id is not None and (
            "simemu.cli do" in command or "maestro.cli.AppKt" in command
        )
        if is_session_maestro and (session_id not in active_ids or age_seconds > max_age_seconds):
            _kill_process(pid)
            killed.append(f"{pid}:maestro:{session_id}")
            continue

        if "screencaptureui.app/Contents/MacOS/screencaptureui" in command and age_seconds > 300:
            _kill_process(pid)
            killed.append(f"{pid}:screencaptureui")
            continue

        apple_ui_script = (
            "osascript" in command
            and "System Events" in command
            and ("frontmost" in command or " click " in command or "AXRaise" in command)
        )
        if apple_ui_script and age_seconds > 60:
            _kill_process(pid)
            killed.append(f"{pid}:osascript-ui")

    return killed


def _prune_old_sessions() -> None:
    """T-21: Remove old expired/released sessions to keep sessions.json small."""
    from simemu.session import _locked_sessions

    with _locked_sessions() as (data, save):
        sessions = data.get("sessions", {})
        # Find expired/released sessions sorted by oldest first
        dead = [
            (sid, s.get("heartbeat_at", ""))
            for sid, s in sessions.items()
            if s.get("status") in ("expired", "released")
        ]
        dead.sort(key=lambda x: x[1])

        # Keep only the most recent MAX_HISTORY dead sessions
        if len(dead) > MAX_HISTORY:
            to_remove = dead[:-MAX_HISTORY]
            for sid, _ in to_remove:
                del sessions[sid]
            log(f"pruned: removed {len(to_remove)} old sessions")
            save(data)


def check_watchdog() -> dict:
    """T-28: Check if the monitor is running by reading its heartbeat file."""
    if not HEARTBEAT.exists():
        return {"running": False, "hint": "No heartbeat file — monitor may not be installed"}

    try:
        last = datetime.fromisoformat(HEARTBEAT.read_text().strip())
        age_seconds = (datetime.now(timezone.utc) - last).total_seconds()
        return {
            "running": age_seconds < 120,  # healthy if heartbeat < 2 minutes old
            "last_heartbeat": last.isoformat(),
            "age_seconds": int(age_seconds),
        }
    except Exception as e:
        return {"running": False, "error": str(e)}


if __name__ == "__main__":
    run()
