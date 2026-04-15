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
    if env_val.isdigit():
        return int(env_val)
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
