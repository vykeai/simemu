"""
simemu menu bar app — live overview of simulator allocations and memory.

Shows total simulator RAM in the menu bar, lists each allocation with
status and memory, and provides quick actions (kill all, maintenance).

Usage:
    python3 -m simemu.ui.menubar
    # or
    simemu menubar
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import threading
import time
from pathlib import Path

import rumps

from simemu import state
from simemu.discover import get_android_serial
from simemu.genymotion import is_genymotion_id


# ── Memory helpers ────────────────────────────────────────────────────────────

def _qemu_memory_mb() -> dict[int, float]:
    """Return {pid: resident_mb} for all qemu-system processes."""
    result = {}
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,rss,comm"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return result
    for line in out.splitlines():
        if "qemu-system" not in line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                pid = int(parts[0])
                rss_kb = int(parts[1])
                result[pid] = rss_kb / 1024
            except ValueError:
                pass
    return result


def _simulator_memory_mb() -> dict[str, float]:
    """Return {process_name: resident_mb} for Simulator.app processes."""
    result = {}
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,rss,comm"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return result
    for line in out.splitlines():
        if "Simulator" not in line and "SimulatorKit" not in line:
            continue
        parts = line.split()
        if len(parts) >= 3:
            try:
                rss_kb = int(parts[1])
                name = parts[2].split("/")[-1]
                result[name] = result.get(name, 0) + rss_kb / 1024
            except ValueError:
                pass
    return result


def _emulator_pid_for_avd(avd_name: str) -> int | None:
    """Find the qemu PID running a specific AVD."""
    try:
        out = subprocess.check_output(
            ["ps", "-eo", "pid,args"],
            stderr=subprocess.DEVNULL,
        ).decode()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        if "qemu-system" in line and f"-avd {avd_name}" in line:
            try:
                return int(line.split()[0])
            except (ValueError, IndexError):
                pass
    return None


def _ios_sim_booted(udid: str) -> bool:
    """Check if an iOS simulator is booted."""
    try:
        out = subprocess.check_output(
            ["xcrun", "simctl", "list", "devices", "--json"],
            stderr=subprocess.DEVNULL, timeout=5,
        )
        data = json.loads(out)
        for devices in data["devices"].values():
            for d in devices:
                if d["udid"] == udid:
                    return d.get("state") == "Booted"
    except Exception:
        pass
    return False


def _format_mb(mb: float) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f}GB"
    return f"{mb:.0f}MB"


# ── Menu bar app ──────────────────────────────────────────────────────────────

class SimEmuMenuBar(rumps.App):
    def __init__(self):
        super().__init__("📱", quit_button=None)
        self._refresh_interval = 10  # seconds
        self._warning_threshold_mb = 8192  # 8GB
        self._critical_threshold_mb = 16384  # 16GB
        self._lock = threading.Lock()
        # Start background refresh
        self._timer = rumps.Timer(self._refresh, self._refresh_interval)
        self._timer.start()
        # Initial refresh
        self._refresh(None)

    def _refresh(self, _sender) -> None:
        try:
            self._update_menu()
        except Exception as e:
            self.title = "📱 ?"

    def _update_menu(self) -> None:
        allocs = state.get_all()
        qemu_mem = _qemu_memory_mb()
        sim_mem = _simulator_memory_mb()

        # Calculate total emulator memory
        total_mb = sum(qemu_mem.values()) + sum(sim_mem.values())

        # Menu bar title
        if total_mb < 100:
            self.title = "📱 idle"
        elif total_mb >= self._critical_threshold_mb:
            self.title = f"🔴 {_format_mb(total_mb)}"
        elif total_mb >= self._warning_threshold_mb:
            self.title = f"🟠 {_format_mb(total_mb)}"
        else:
            self.title = f"📱 {_format_mb(total_mb)}"

        # Build menu
        items = []

        # Header
        booted_count = 0
        for slug, alloc in sorted(allocs.items()):
            is_booted = False
            mem_mb = 0.0

            if alloc.platform in ("ios", "watchos", "tvos", "visionos"):
                is_booted = _ios_sim_booted(alloc.sim_id)
                # iOS sim memory is shared in Simulator.app — rough split
                if is_booted and sim_mem:
                    mem_mb = sum(sim_mem.values()) / max(1, sum(
                        1 for a in allocs.values()
                        if a.platform in ("ios", "watchos", "tvos", "visionos")
                        and _ios_sim_booted(a.sim_id)
                    ))
            else:
                # Android
                pid = _emulator_pid_for_avd(alloc.sim_id)
                if pid and pid in qemu_mem:
                    is_booted = True
                    mem_mb = qemu_mem[pid]
                elif is_genymotion_id(alloc.sim_id):
                    serial = get_android_serial(alloc.sim_id)
                    is_booted = serial is not None

            if is_booted:
                booted_count += 1

            # Status indicator
            if is_booted:
                dot = "🟢" if mem_mb < 2048 else "🟠" if mem_mb < 4096 else "🔴"
                mem_str = f"  ({_format_mb(mem_mb)})" if mem_mb > 0 else ""
            else:
                dot = "⚫"
                mem_str = ""

            title = f"{dot} {slug}  —  {alloc.device_name}{mem_str}"
            item = rumps.MenuItem(title)
            item.set_callback(None)  # non-interactive for now
            items.append(item)

        # Summary header
        header = rumps.MenuItem(
            f"{booted_count} booted  ·  {len(allocs)} allocated  ·  {_format_mb(total_mb)} RAM"
        )
        header.set_callback(None)

        # Maintenance status
        maint_active = state.maintenance_file().exists()
        maint_label = "🔒 Maintenance: ON" if maint_active else "🔓 Maintenance: OFF"

        # Build final menu
        self.menu.clear()
        self.menu = [
            header,
            None,  # separator
            *items,
            None,  # separator
            rumps.MenuItem(maint_label, callback=self._toggle_maintenance),
            rumps.MenuItem("🔄 Refresh", callback=self._refresh),
            rumps.MenuItem("💀 Kill All Emulators", callback=self._kill_all),
            None,
            rumps.MenuItem("Quit simemu menubar", callback=self._quit),
        ]

    def _toggle_maintenance(self, sender) -> None:
        if state.maintenance_file().exists():
            state.exit_maintenance()
            rumps.notification("simemu", "", "Maintenance mode OFF")
        else:
            state.enter_maintenance("Maintenance enabled from menu bar", 10)
            rumps.notification("simemu", "", "Maintenance mode ON")
        self._refresh(None)

    def _kill_all(self, sender) -> None:
        rumps.notification("simemu", "", "Killing all emulators...")
        subprocess.run(["pkill", "-9", "-f", "qemu-system"], capture_output=True)
        subprocess.run(["pkill", "-9", "-f", "Genymotion.app"], capture_output=True)
        time.sleep(1)
        self._refresh(None)
        rumps.notification("simemu", "", "All emulators killed.")

    def _quit(self, _sender) -> None:
        rumps.quit_application()


def main():
    SimEmuMenuBar().run()


if __name__ == "__main__":
    main()
