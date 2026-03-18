#!/usr/bin/env python3
"""
simemu monitor — runs every 60s via launchd.

Manages session lifecycle, reconnects offline adb, ensures server is running.
Logs to ~/.simemu/monitor.log
"""

import json
import subprocess
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LOG = Path.home() / ".simemu" / "monitor.log"
LOG.parent.mkdir(parents=True, exist_ok=True)


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

    # 1. Lifecycle tick
    try:
        from simemu.session import lifecycle_tick, get_active_sessions
        from simemu.state import get_all
        changed = lifecycle_tick()
        if changed:
            log(f"lifecycle: transitioned {changed}")
    except Exception as e:
        log(f"lifecycle error: {e}")

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
        with socket.create_connection(("127.0.0.1", 8765), timeout=1):
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

    # 4. Summary
    try:
        from simemu.session import get_active_sessions
        from simemu.state import get_all
        sessions = get_active_sessions()
        legacy = get_all()
        active = sum(1 for s in sessions.values() if s.status == "active")
        idle = sum(1 for s in sessions.values() if s.status == "idle")
        parked = sum(1 for s in sessions.values() if s.status == "parked")
        log(f"ok: {len(sessions)} sessions (a={active} i={idle} p={parked}) {len(legacy)} legacy")
    except Exception as e:
        log(f"summary error: {e}")

    if issues:
        log(f"fixed: {issues}")

    signal.alarm(0)


if __name__ == "__main__":
    run()
