"""
Watchdog — detect dead daemons, stale sessions, and unhealthy state.

Used by the status command, menubar, and monitor to surface issues
with actionable recovery hints.
"""

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def check_api_server(host: str = "127.0.0.1", port: int = 8765, timeout: float = 2.0) -> dict:
    """Check if the simemu API server is reachable."""
    import urllib.request
    try:
        url = f"http://{host}:{port}/health"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return {"status": "healthy", "url": f"http://{host}:{port}"}
    except Exception as e:
        return {
            "status": "unreachable",
            "hint": f"Start with: simemu serve\nOr run install.sh to set up the launchd agent.",
            "error": str(e)[:100],
        }


def check_monitor_agent() -> dict:
    """Check if the monitor launchd agent is loaded and running."""
    label = "com.simemu.monitor"
    try:
        result = subprocess.run(
            ["launchctl", "list", label],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0:
            return {"status": "running", "label": label}
        return {
            "status": "not_loaded",
            "hint": "Install with: bash install.sh",
        }
    except FileNotFoundError:
        return {"status": "unknown", "hint": "launchctl not available"}


def check_menubar_app() -> dict:
    """Check if SimEmuBar is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-fl", "SimEmuBar"],
            capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split()[0]
            return {"status": "running", "pid": int(pid)}
        return {
            "status": "not_running",
            "hint": "Launch with: open /Applications/SimEmuBar.app\nOr run install.sh",
        }
    except FileNotFoundError:
        return {"status": "unknown"}


def check_stale_sessions() -> dict:
    """Find sessions with heartbeats older than the expiry threshold."""
    from . import state
    sf = state.state_dir() / "sessions.json"
    if not sf.exists():
        return {"status": "ok", "stale_count": 0}

    try:
        data = json.loads(sf.read_text())
    except (json.JSONDecodeError, OSError):
        return {"status": "corrupted", "hint": "sessions.json is corrupted. Will auto-recover on next read."}

    now = datetime.now(timezone.utc)
    stale = []
    for sid, session in data.get("sessions", {}).items():
        status = session.get("status", "")
        if status in ("expired", "released"):
            continue
        hb = session.get("heartbeat_at")
        if not hb:
            continue
        try:
            last = datetime.fromisoformat(hb)
            idle_hours = (now - last).total_seconds() / 3600
            if idle_hours > 2 and status not in ("parked",):
                stale.append({
                    "session": sid,
                    "status": status,
                    "idle_hours": round(idle_hours, 1),
                })
        except (ValueError, TypeError):
            pass

    return {
        "status": "stale" if stale else "ok",
        "stale_count": len(stale),
        "stale_sessions": stale[:5],
    }


def check_state_file_health() -> dict:
    """Check sessions.json and state.json integrity."""
    from . import state
    issues = []

    sf = state.state_dir() / "sessions.json"
    if sf.exists():
        try:
            data = json.loads(sf.read_text())
            if not isinstance(data, dict) or "sessions" not in data:
                issues.append("sessions.json: missing 'sessions' key")
        except json.JSONDecodeError:
            issues.append("sessions.json: invalid JSON (will auto-recover from backup)")

    legacy = state.state_file()
    if legacy.exists():
        try:
            data = json.loads(legacy.read_text())
            if not isinstance(data, dict) or "allocations" not in data:
                issues.append("state.json: missing 'allocations' key")
        except json.JSONDecodeError:
            issues.append("state.json: invalid JSON (will auto-recover from backup)")

    return {
        "status": "issues" if issues else "ok",
        "issues": issues,
    }


def check_memory_pressure() -> dict:
    """Check for runaway qemu/emulator processes consuming excessive memory."""
    threshold_mb = int(os.environ.get("SIMEMU_MEMORY_ALERT_MB", "4096"))
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,rss,comm"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"status": "unknown"}

    offenders = []
    total_emu_mb = 0
    for line in result.stdout.splitlines():
        if "qemu-system" not in line and "emulator" not in line.lower():
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            rss_mb = int(parts[1]) / 1024
            total_emu_mb += rss_mb
            if rss_mb > threshold_mb:
                offenders.append({
                    "pid": pid,
                    "memory_mb": round(rss_mb),
                    "process": parts[2][:40],
                })
        except ValueError:
            continue

    return {
        "status": "alert" if offenders else "ok",
        "total_emulator_mb": round(total_emu_mb),
        "threshold_mb": threshold_mb,
        "offenders": offenders,
        "hint": f"Kill with: kill -9 {' '.join(str(o['pid']) for o in offenders)}" if offenders else None,
    }


def full_health_check() -> dict:
    """Run all watchdog checks and return a comprehensive health report."""
    return {
        "api_server": check_api_server(),
        "monitor": check_monitor_agent(),
        "menubar": check_menubar_app(),
        "sessions": check_stale_sessions(),
        "state_files": check_state_file_health(),
        "memory": check_memory_pressure(),
    }


def is_healthy() -> bool:
    """Quick boolean: is everything looking OK?"""
    report = full_health_check()
    return (
        report["api_server"]["status"] == "healthy"
        and report["monitor"]["status"] == "running"
        and report["sessions"]["status"] == "ok"
        and report["state_files"]["status"] == "ok"
    )
