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
    list_ios, list_android, list_real_ios, list_real_android,
    find_simulator, NoSimulatorAvailable,
)


def _agent() -> str:
    return os.environ.get("SIMEMU_AGENT") or f"pid-{os.getpid()}"


def _is_real_device(alloc: state.Allocation) -> bool:
    """Check if an allocation refers to a real device (not simulator/emulator).

    Real devices have "(real)" in their device_name, set during acquire.
    """
    return "(real)" in alloc.device_name


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


_ACTION_EMOJI = {
    "tap": "\U0001f446",       # 👆
    "swipe": "\u2194\ufe0f",   # ↔️
    "key": "\u2328\ufe0f",     # ⌨️
    "input": "\U0001f4dd",     # 📝
    "long-press": "\U0001f447",# 👇
    "focus": "\U0001f50d",     # 🔍
}


class _DesktopLease:
    def __init__(self, alloc: state.Allocation, action: str, reason: str,
                 estimated_seconds: int = 5, **extra_metadata):
        self.alloc = alloc
        self.action = action
        self.reason = reason
        self.estimated_seconds = estimated_seconds
        self.extra_metadata = extra_metadata
        self.lease_id: str | None = None
        self.enabled = False
        self.countdown_seconds = int(os.environ.get("SIMEMU_DESKTOP_LEASE_COUNTDOWN", "3"))

    def __enter__(self):
        try:
            payload = {
                "tool": "simemu",
                "project": _project_name(self.alloc),
                "slug": self.alloc.slug,
                "platform": self.alloc.platform,
                "action": self.action,
                "action_emoji": _ACTION_EMOJI.get(self.action, "\U0001f5a5\ufe0f"),
                "reason": self.reason,
                "estimated_seconds": self.estimated_seconds,
                "countdown_seconds": self.countdown_seconds,
                "stage": "Preparing desktop control",
                "screen": self.alloc.device_name,
                "device_type": "real" if _is_real_device(self.alloc) else "simulator",
                **self.extra_metadata,
            }
            lease = _scouty_json("POST", "/desktop/lease/request", payload)
            self.lease_id = lease.get("lease_id")
            if self.lease_id:
                self.enabled = True
                remaining = lease.get("countdown_remaining_seconds")
                delay = self.countdown_seconds if remaining is None else max(0.0, float(remaining))
                if delay > 0:
                    time.sleep(delay)
                _scouty_json("POST", "/desktop/lease/activate", {"lease_id": self.lease_id})
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
            self.enabled = False
            self.lease_id = None
        return self

    def update(self, **metadata):
        if not self.lease_id:
            return
        try:
            _scouty_json("POST", "/desktop/lease/update", {"lease_id": self.lease_id, "metadata": metadata})
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
            pass

    def __exit__(self, exc_type, exc, tb):
        if self.lease_id:
            try:
                _scouty_json("POST", "/desktop/lease/release", {"lease_id": self.lease_id})
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
                pass
        return False


def _desktop_lease(alloc: state.Allocation, action: str, reason: str,
                   estimated_seconds: int = 5, **extra_metadata):
    return _DesktopLease(alloc, action, reason, estimated_seconds, **extra_metadata)


def _autostart_disabled() -> bool:
    value = (os.environ.get("SIMEMU_AUTOSTART") or "").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return True
    no_value = (os.environ.get("SIMEMU_NO_AUTOSTART") or "").strip().lower()
    return no_value in {"1", "true", "yes", "on"}


def _server_reachable(host: str = "127.0.0.1", port: int = 8765, timeout: float = 0.5) -> bool:
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


# ── command handlers ──────────────────────────────────────────────────────────

def cmd_acquire(args):
    wait = getattr(args, "wait", 0)
    real = getattr(args, "real", False)
    poll = 10  # seconds between retries
    deadline = time.time() + wait
    attempt = 0

    while True:
        try:
            sim = find_simulator(args.platform, args.device, real_device=real)
            break
        except NoSimulatorAvailable as e:
            if time.time() >= deadline:
                raise RuntimeError(str(e)) from None
            attempt += 1
            remaining = int(deadline - time.time())
            kind = "device" if real else "simulator"
            print(f"No {kind} available, retrying in {poll}s (up to {remaining}s remaining)...",
                  flush=True)
            time.sleep(poll)

    alloc = state.acquire(
        slug=args.slug,
        sim_id=sim.sim_id,
        platform=sim.platform,
        device_name=sim.device_name,
        agent=_agent(),
    )

    if args.json:
        _print_json({
            "slug": args.slug,
            "sim_id": sim.sim_id,
            "platform": sim.platform,
            "device_name": sim.device_name,
            "runtime": sim.runtime,
            "real_device": sim.real_device,
            "agent": alloc.agent,
            "acquired_at": alloc.acquired_at,
        })
    else:
        label = "real device" if sim.real_device else "simulator"
        print(f"Reserved '{args.slug}' → {sim.device_name} ({sim.runtime}) [{label}]")
        print(f"  sim_id:  {sim.sim_id}")
        print(f"  agent:   {alloc.agent}")

    # Real devices are already booted — skip boot step
    if sim.real_device:
        if not args.json:
            print("Ready (real device — already connected).")
        return

    if not args.no_boot:
        if not args.json:
            print("Booting...", flush=True)
        if sim.platform == "ios":
            ios.boot(sim.sim_id)
        else:
            android.boot(sim.sim_id, headless=not args.window)
        placement = _maybe_apply_agent_workspace(args.slug)
        if not args.json:
            print("Ready.")
            if placement and placement.get("applied"):
                print(f"Placed '{args.slug}' in the '{alloc.agent}' workspace.")


def cmd_release(args):
    alloc = state.release(args.slug, agent=_agent())
    # If a recording was active, stop it cleanly
    if alloc.recording_pid is not None:
        if alloc.platform == "ios":
            ios.record_stop(alloc.recording_pid)
        else:
            android.record_stop(alloc.recording_pid)
    print(f"Released '{args.slug}' ({alloc.device_name})")


def cmd_status(args):
    allocations = state.get_all()
    if not allocations:
        if args.json:
            _print_json([])
        else:
            print("No simulators currently reserved.")
        return

    if args.json:
        rows = []
        for slug, alloc in allocations.items():
            d = alloc.__dict__.copy()
            rows.append(alloc.__dict__.copy())
        _print_json(rows)
        return

    print(f"{'SLUG':<22} {'PLATFORM':<10} {'DEVICE':<26} {'AGENT':<22} {'SINCE':<20} {'REC'}")
    print("─" * 100)
    for slug, alloc in allocations.items():
        since = alloc.acquired_at[:19].replace("T", " ")
        rec = "●REC" if alloc.recording_pid else ""
        print(f"{slug:<22} {alloc.platform:<10} {alloc.device_name:<26} {alloc.agent:<22} {since:<20} {rec}")


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
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if _is_real_device(alloc):
        print(f"'{args.slug}' is a real device — already connected.")
        return
    if alloc.platform == "ios":
        ios.boot(alloc.sim_id)
    else:
        android.boot(alloc.sim_id, headless=not getattr(args, "window", False))
    placement = _maybe_apply_agent_workspace(args.slug)
    print(f"'{args.slug}' is booted.")
    if placement and placement.get("applied"):
        print(f"Placed '{args.slug}' in the '{alloc.agent}' workspace.")


def cmd_shutdown(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if _is_real_device(alloc):
        raise RuntimeError(
            f"'{args.slug}' is a real device — cannot shut down via simemu.\n"
            f"Use 'simemu release {args.slug}' to release the reservation."
        )
    if alloc.platform == "ios":
        ios.shutdown(alloc.sim_id)
    else:
        android.shutdown(alloc.sim_id)
    print(f"'{args.slug}' shut down.")


def cmd_animations(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    enabled = args.mode == "on"
    if alloc.platform == "ios":
        ios.set_animations(alloc.sim_id, enabled)
    else:
        android.set_animations(alloc.sim_id, enabled)
    state_str = "restored" if enabled else "disabled (slow-mode for stable Maestro flows)"
    print(f"Animations {state_str} on '{args.slug}'.")


def cmd_clipboard(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform != "ios":
        raise RuntimeError(
            "'clipboard get' is iOS only. Android has no reliable CLI clipboard read command."
        )
    text = ios.clipboard_get(alloc.sim_id)
    if args.json:
        _print_json({"clipboard": text})
    else:
        print(text)


def cmd_focus(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        with _desktop_lease(alloc, "focus", f"Bring {args.slug} to the foreground", estimated_seconds=4) as lease:
            lease.update(stage="Booting simulator if needed", screen="Simulator shell", scenario="Desktop focus")
            _prepare_ios_interaction(args.slug, alloc.sim_id)
            lease.update(stage="Bringing simulator window to foreground", screen=alloc.device_name, scenario="Desktop focus")
            ios.focus(alloc.sim_id)
        print(f"Simulator window for '{args.slug}' brought to front.")
    else:
        print(f"'{args.slug}' is an Android emulator. Android runs headless by default — "
              f"boot with --window if you need a visible window.")


def cmd_present(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        clear_layout = getattr(args, "clear_layout", False)
        save_layout = getattr(args, "save_layout", False)

        if clear_layout:
            removed = state.clear_presentation(args.slug)
            message = f"Cleared saved layout for '{args.slug}'." if removed else f"No saved layout for '{args.slug}'."
            if args.json:
                _print_json({"cleared": removed, "slug": args.slug})
            else:
                print(message)
            return

        if save_layout:
            layout = ios.current_presentation_layout(alloc.sim_id)
            state.set_presentation(args.slug, layout)
            if args.json:
                _print_json({"saved": True, "slug": args.slug, "layout": layout})
            else:
                print(f"Saved current layout for '{args.slug}'.")
            return

        layout = state.get_presentation(args.slug)
        result = ios.present(alloc.sim_id, layout=layout)
        workspace_placement = _maybe_apply_agent_workspace(args.slug)
        if workspace_placement and workspace_placement.get("applied"):
            result["workspace_applied"] = True
        if args.json:
            _print_json(result)
        else:
            suffix = " using saved layout" if layout else ""
            print(f"Presented '{args.slug}' ({alloc.device_name}){suffix}.")
    else:
        message = (
            f"'{args.slug}' is Android — presentation is controlled at boot time "
            f"with --window."
        )
        if args.json:
            _print_json({"stable": True, "platform": "android", "message": message})
        else:
            print(message)


def cmd_stabilize(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        if getattr(args, "heal", False):
            prep = _ensure_ios_ready_or_heal(args.slug, alloc.sim_id)
            result = prep["stable"]
            healed = prep["healed"]
        else:
            result = ios.stabilize(alloc.sim_id)
            healed = False
        presentation = _ios_presentation_status(args.slug, alloc.sim_id)
        result.update(presentation)
        result["healed"] = healed
    else:
        result = {
            "stable": True,
            "slug": args.slug,
            "platform": alloc.platform,
            "device_name": alloc.device_name,
            "note": "Android presentation is already window-independent for most commands.",
        }
    if args.json:
        _print_json(result)
    else:
        suffix = ""
        if alloc.platform == "ios" and result.get("has_saved_layout"):
            if result.get("healed"):
                suffix = " (healed to saved layout)"
            elif result.get("layout_drifted"):
                suffix = " (layout drifted from saved presentation)"
            else:
                suffix = " (layout matches saved presentation)"
        visibility_suffix = ""
        if alloc.platform == "ios" and result.get("window_visible_on_active_desktop") is False:
            visibility_suffix = " [window not visible on active desktop]"
        print(f"'{args.slug}' is stable.{suffix}{visibility_suffix}")


def cmd_ready(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        prep = _ensure_ios_ready_or_heal(args.slug, alloc.sim_id)
        result = prep["stable"]
        result.update(_ios_presentation_status(args.slug, alloc.sim_id))
        result["healed"] = prep["healed"]
        result["ready"] = True
    else:
        result = {
            "ready": True,
            "stable": True,
            "slug": args.slug,
            "platform": alloc.platform,
            "device_name": alloc.device_name,
            "note": "Android presentation is already window-independent for most commands.",
        }
    if args.json:
        _print_json(result)
    else:
        suffix = ""
        if alloc.platform == "ios":
            if result.get("healed"):
                suffix = " (healed)"
            elif result.get("layout_matches_saved") is True:
                suffix = " (already aligned)"
        print(f"'{args.slug}' is ready.{suffix}")


def cmd_workspace_set(args):
    agent = _agent()
    workspace = _current_workspace_anchor()
    state.set_workspace(agent, workspace)
    if args.json:
        _print_json({"agent": agent, "workspace": workspace})
    else:
        source = workspace.get("frontmost_app") or "current desktop"
        print(
            f"Saved workspace for '{agent}' on display {workspace.get('display_id')} "
            f"from {source}."
        )


def cmd_workspace_show(args):
    agent = _agent()
    workspace = state.get_workspace(agent)
    if args.json:
        _print_json({"agent": agent, "workspace": workspace})
        return
    if not workspace:
        print(f"No workspace saved for '{agent}'.")
        return
    print(
        f"Workspace for '{agent}': display {workspace.get('display_id')} "
        f"({int(workspace.get('width', 0))}x{int(workspace.get('height', 0))} at "
        f"{int(workspace.get('origin_x', 0))},{int(workspace.get('origin_y', 0))})"
    )


def cmd_workspace_clear(args):
    agent = _agent()
    cleared = state.clear_workspace(agent)
    if args.json:
        _print_json({"agent": agent, "cleared": cleared})
    else:
        if cleared:
            print(f"Cleared workspace for '{agent}'.")
        else:
            print(f"No workspace saved for '{agent}'.")


def cmd_workspace_apply(args):
    agent = _agent()
    workspace = state.get_workspace(agent)
    if not workspace:
        raise RuntimeError(
            f"No workspace saved for '{agent}'. Run `simemu workspace set` from the desktop where you want "
            f"this agent's simulator windows to live."
        )
    if args.slugs:
        allocations = [state.require(slug) for slug in args.slugs]
    else:
        allocations = _agent_allocations(agent)
    if not allocations:
        raise RuntimeError(f"No simulators reserved for '{agent}'.")
    placements = _apply_workspace_to_allocations(workspace, allocations)
    if args.json:
        _print_json({"agent": agent, "workspace": workspace, "placements": placements})
        return
    print(f"Applied workspace for '{agent}' to {len(placements)} simulator(s).")
    for placement in placements:
        suffix = "" if placement["applied"] else f" [{placement['note']}]"
        print(
            f"  {placement['slug']}: {int(placement['layout']['x'])},{int(placement['layout']['y'])} "
            f"{int(placement['layout']['width'])}x{int(placement['layout']['height'])}{suffix}"
        )


def cmd_install(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    timeout = args.timeout
    print(f"Installing {args.app} on '{args.slug}' ({alloc.device_name})...")
    if _is_real_device(alloc) and alloc.platform == "ios":
        device.ios_install(alloc.sim_id, args.app, timeout=timeout)
    elif alloc.platform == "ios":
        ios.install(alloc.sim_id, args.app, timeout=timeout)
    else:
        # adb install works the same for real Android devices and emulators
        android.install(alloc.sim_id, args.app, timeout=timeout)
    print("Done.")


def cmd_apps(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        apps = ios.list_apps(alloc.sim_id)
    else:
        apps = android.list_apps(alloc.sim_id)

    if args.json:
        _print_json(apps)
        return

    if not apps:
        print("No apps installed.")
        return

    if alloc.platform == "ios":
        print(f"{'NAME':<35} {'BUNDLE ID':<50} {'VERSION'}")
        print("─" * 90)
        for a in apps:
            print(f"{a['name']:<35} {a['bundle_id']:<50} {a['version']}")
    else:
        print(f"{'PACKAGE':<60} {'PATH'}")
        print("─" * 90)
        for a in apps:
            print(f"{a['package']:<60} {a['path']}")


def cmd_launch(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    extra = args.extra or []
    if _is_real_device(alloc) and alloc.platform == "ios":
        device.ios_launch(alloc.sim_id, args.bundle_or_package)
    elif alloc.platform == "ios":
        ios.launch(alloc.sim_id, args.bundle_or_package, extra)
    else:
        android.launch(alloc.sim_id, args.bundle_or_package, extra)


def cmd_terminate(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.terminate(alloc.sim_id, args.bundle_or_package)
    else:
        android.terminate(alloc.sim_id, args.bundle_or_package)


def cmd_uninstall(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.uninstall(alloc.sim_id, args.bundle_or_package)
    else:
        android.uninstall(alloc.sim_id, args.bundle_or_package)


def cmd_screenshot(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)

    ext = "png"
    if args.format and args.format in ("jpeg", "jpg"):
        ext = "jpg"
    elif args.format:
        ext = args.format

    output = args.output or _auto_path(args.slug, ext)

    max_size = args.max_size or (
        int(os.environ["SIMEMU_SCREENSHOT_MAX_SIZE"])
        if "SIMEMU_SCREENSHOT_MAX_SIZE" in os.environ else None
    )

    if _is_real_device(alloc) and alloc.platform == "ios":
        device.ios_screenshot(alloc.sim_id, output, max_size=max_size)
    elif alloc.platform == "ios":
        ios.screenshot(alloc.sim_id, output, fmt=args.format, max_size=max_size)
        if not max_size:
            print("Tip: iOS screenshots are ~2600px tall. Pass --max-size 1000 (or set "
                  "SIMEMU_SCREENSHOT_MAX_SIZE=1000) to auto-resize for Claude's vision.",
                  file=sys.stderr)
    else:
        if args.format and args.format not in ("png",):
            print(f"Warning: Android only supports PNG screenshots; ignoring --format.", file=sys.stderr)
        # adb screencap works the same for real Android devices
        android.screenshot(alloc.sim_id, output, max_size=max_size)

    print(f"Screenshot saved: {output}")
    if args.json:
        _print_json({"path": output})


def cmd_record(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)

    if args.action == "start":
        if alloc.recording_pid is not None:
            raise RuntimeError(
                f"A recording is already active for '{args.slug}' (pid {alloc.recording_pid}). "
                f"Stop it first with: simemu record stop {args.slug}"
            )
        output = args.output or _auto_path(args.slug, "mp4")
        if alloc.platform == "ios":
            pid = ios.record_start(alloc.sim_id, output, codec=args.codec)
        else:
            if args.codec:
                print("Warning: --codec is not supported on Android.", file=sys.stderr)
            pid = android.record_start(alloc.sim_id, output)
            print(f"Note: Android screenrecord has a 3-minute hard limit.", file=sys.stderr)
        state.set_recording(args.slug, pid, output)
        if args.json:
            _print_json({"pid": pid, "output": output})
        else:
            print(f"Recording started → {output}")
            print(f"Stop with:  simemu record stop {args.slug}")

    elif args.action == "stop":
        if alloc.recording_pid is None:
            raise RuntimeError(f"No active recording for '{args.slug}'.")
        output = alloc.recording_output
        if alloc.platform == "ios":
            ios.record_stop(alloc.recording_pid)
        else:
            android.record_stop(alloc.recording_pid)
        state.set_recording(args.slug, None, None)
        if args.json:
            _print_json({"output": output})
        else:
            print(f"Recording stopped → {output}")


def cmd_log(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.log_stream(alloc.sim_id, predicate=args.predicate, level=args.level or "debug")
    else:
        android.log_stream(alloc.sim_id, tag=args.tag, level=args.level)


def cmd_url(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.open_url(alloc.sim_id, args.url)
    else:
        android.open_url(alloc.sim_id, args.url)


def cmd_push(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform != "android":
        raise RuntimeError("'push' is Android only. For iOS use 'simemu add-media' (photos/videos) or 'simemu push-notification'.")
    android.push(alloc.sim_id, args.local, args.remote)


def cmd_pull(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform != "android":
        raise RuntimeError("'pull' is Android only.")
    android.pull(alloc.sim_id, args.remote, args.local)


def cmd_add_media(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.add_media(alloc.sim_id, args.file)
    else:
        android.add_media(alloc.sim_id, args.file)
    print(f"Added {args.file} to Photos library on '{args.slug}'.")


def cmd_push_notification(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform != "ios":
        raise RuntimeError("'push-notification' is iOS only.")
    ios.push_notification(alloc.sim_id, args.bundle_id, args.payload)
    print("Push notification sent.")


def cmd_rename(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.rename(alloc.sim_id, args.name)
    else:
        android.rename(alloc.sim_id, args.name)
    # Update the stored device_name (and sim_id for Android, where AVD name = sim_id)
    with state._locked_state() as (s, save):
        if args.slug in s["allocations"]:
            s["allocations"][args.slug]["device_name"] = args.name
            if alloc.platform == "android":
                # AVD filesystem id uses underscores (matches android.rename() convention)
                s["allocations"][args.slug]["sim_id"] = args.name.replace(" ", "_")
            save(s)
    print(f"Renamed '{args.slug}' → {args.name}")


def cmd_delete(args):
    """Permanently remove a simulator/AVD. Releases reservation if held."""
    alloc = state.get(args.slug)
    if alloc:
        if not args.yes:
            try:
                confirm = input(
                    f"Permanently DELETE '{args.slug}' ({alloc.device_name})? "
                    f"This cannot be undone. [y/N] "
                )
            except EOFError:
                raise RuntimeError("Non-interactive: pass --yes to confirm delete.")
            if confirm.strip().lower() != "y":
                print("Aborted.")
                return
        if alloc.recording_pid:
            if alloc.platform == "ios":
                ios.record_stop(alloc.recording_pid)
            else:
                android.record_stop(alloc.recording_pid)
        state.release(args.slug, agent=None)  # admin release
        if alloc.platform == "ios":
            ios.delete(alloc.sim_id)
        else:
            android.delete(alloc.sim_id)
        print(f"Deleted '{args.slug}' ({alloc.device_name}).")
    else:
        # Not in simemu state — delete by raw sim_id/avd
        raise RuntimeError(
            f"No reservation for '{args.slug}'. "
            f"Use 'simemu acquire' first, or delete directly via the platform tools."
        )


def cmd_erase(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if not args.yes:
        try:
            confirm = input(f"Erase all data on '{args.slug}' ({alloc.device_name})? [y/N] ")
        except EOFError:
            raise RuntimeError("Non-interactive mode: pass --yes to confirm erase.")
        if confirm.strip().lower() != "y":
            print("Aborted.")
            return
    if alloc.platform == "ios":
        ios.erase(alloc.sim_id)
    else:
        android.erase(alloc.sim_id)
    print(f"'{args.slug}' erased.")


def cmd_env(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if _is_real_device(alloc) and alloc.platform == "ios":
        info = device.ios_get_env(alloc.sim_id)
        info["maestro_device"] = alloc.sim_id
    elif alloc.platform == "ios":
        info = ios.get_env(alloc.sim_id)
        info["maestro_device"] = alloc.sim_id  # UDID for maestro --device
    else:
        info = android.get_env(alloc.sim_id)
        from .discover import get_android_serial
        serial = get_android_serial(alloc.sim_id)
        info["maestro_device"] = serial or alloc.sim_id  # real Android: serial is the sim_id
    info["slug"] = args.slug
    info["agent"] = alloc.agent
    info["acquired_at"] = alloc.acquired_at
    info["real_device"] = _is_real_device(alloc)
    _print_json(info)


def cmd_check(args):
    """Verify a reserved simulator is booted and the specified app is in the foreground."""
    alloc = state.require(args.slug)
    state.touch(args.slug)
    issues = []

    if alloc.platform == "ios":
        env = ios.get_env(alloc.sim_id)
        if env.get("state") != "Booted":
            issues.append(f"Simulator is not booted (state: {env.get('state')}). Run: simemu boot {args.slug}")
    else:
        from .discover import get_android_serial
        serial = get_android_serial(alloc.sim_id)
        if serial is None:
            issues.append(f"Emulator is not running. Run: simemu boot {args.slug}")

    if args.bundle and not issues:
        if alloc.platform == "ios":
            result = ios.get_foreground_app(alloc.sim_id) if hasattr(ios, "get_foreground_app") else None
        else:
            from .discover import get_android_serial
            serial = get_android_serial(alloc.sim_id)
            import subprocess as _sp2
            r = _sp2.run(["adb", "-s", serial, "shell", "dumpsys", "activity", "activities"],
                         capture_output=True, text=True)
            if args.bundle not in r.stdout:
                issues.append(f"App '{args.bundle}' does not appear to be in foreground. Run: simemu launch {args.slug} {args.bundle}")

    if issues:
        for issue in issues:
            print(f"✗ {issue}", file=sys.stderr)
        raise SystemExit(1)
    else:
        print(f"✓ {args.slug} is ready")
        if args.json:
            _print_json({"slug": args.slug, "ready": True, "platform": alloc.platform})


def cmd_maestro(args):
    """Run a Maestro flow against a reserved simulator, with the correct --device flag resolved automatically."""
    import subprocess as _sp
    alloc = state.require(args.slug)
    state.touch(args.slug)

    if alloc.platform == "ios":
        device_id = alloc.sim_id  # UDID
    else:
        from .discover import get_android_serial
        device_id = get_android_serial(alloc.sim_id)
        if not device_id:
            raise RuntimeError(
                f"Android emulator '{args.slug}' is not running. Boot it first: simemu boot {args.slug}"
            )

    cmd = ["maestro", "--device", device_id, "test"] + args.flow + args.extra
    print(f"Running: {' '.join(cmd)}", flush=True)
    result = _sp.run(cmd)
    if result.returncode != 0:
        raise SystemExit(result.returncode)


def _resolve_coords(args, alloc, x_attr="x", y_attr="y"):
    """Resolve tap/swipe coordinates. If --pct, converts fractions to pixels."""
    x = getattr(args, x_attr)
    y = getattr(args, y_attr)
    if getattr(args, "pct", False):
        if alloc.platform == "ios":
            env = ios.get_env(alloc.sim_id)
            w, h = env["screen_width_pt"], env["screen_height_pt"]
        else:
            w, h = android.get_screen_size(alloc.sim_id)
        x = round(x * w)
        y = round(y * h)
    return x, y


def _layout_differs(current: dict, saved: dict, tolerance: float = 2.0) -> bool:
    for key in ("x", "y", "width", "height"):
        if abs(float(current[key]) - float(saved[key])) > tolerance:
            return True
    if (
        current.get("display_id") is not None
        and saved.get("display_id") is not None
        and int(current["display_id"]) != int(saved["display_id"])
    ):
        return True
    return False


def _agent_allocations(agent: str) -> list[state.Allocation]:
    return sorted(
        [alloc for alloc in state.get_all().values() if alloc.agent == agent],
        key=lambda alloc: alloc.slug,
    )


def _current_workspace_anchor() -> dict:
    anchor = ios.current_desktop_anchor()
    display = anchor.get("display")
    if not display:
        raise RuntimeError("Could not determine the current display/desktop anchor.")
    return {
        "display_id": display.get("id"),
        "origin_x": display.get("origin_x"),
        "origin_y": display.get("origin_y"),
        "width": display.get("width"),
        "height": display.get("height"),
        "frontmost_app": anchor.get("frontmost_app"),
        "captured_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _workspace_frame_for_slot(display: dict, slot: int, total: int, base_width: float, base_height: float) -> dict:
    padding = 32.0
    gap = 24.0
    columns = 1 if total <= 1 else 2
    rows = max(1, (total + columns - 1) // columns)
    usable_width = max(display["width"] - (padding * 2) - (gap * (columns - 1)), base_width)
    usable_height = max(display["height"] - (padding * 2) - (gap * (rows - 1)), base_height)
    cell_width = usable_width / columns
    cell_height = usable_height / rows
    scale = min(cell_width / base_width, cell_height / base_height, 1.0)
    width = max(320.0, round(base_width * scale))
    height = max(640.0, round(base_height * scale))
    column = slot % columns
    row = slot // columns
    x = display["origin_x"] + padding + (column * (cell_width + gap)) + max(0.0, (cell_width - width) / 2.0)
    y = display["origin_y"] + padding + (row * (cell_height + gap)) + max(0.0, (cell_height - height) / 2.0)
    return {
        "x": round(x),
        "y": round(y),
        "width": width,
        "height": height,
        "display_id": display.get("display_id"),
    }


def _current_or_saved_window_size(alloc: state.Allocation) -> tuple[float, float]:
    if alloc.platform == "ios":
        try:
            layout = ios.current_presentation_layout(alloc.sim_id)
            return float(layout["width"]), float(layout["height"])
        except Exception:
            saved = state.get_presentation(alloc.slug)
            if saved:
                return float(saved["width"]), float(saved["height"])
            return 494.0, 1054.0
    frame = android.current_window_frame(alloc.sim_id)
    if frame:
        return float(frame["width"]), float(frame["height"])
    return 411.0, 914.0


def _apply_workspace_to_allocations(workspace: dict, allocations: list[state.Allocation]) -> list[dict]:
    display = {
        "display_id": workspace.get("display_id"),
        "origin_x": float(workspace["origin_x"]),
        "origin_y": float(workspace["origin_y"]),
        "width": float(workspace["width"]),
        "height": float(workspace["height"]),
    }
    placements = []
    for idx, alloc in enumerate(allocations):
        base_width, base_height = _current_or_saved_window_size(alloc)
        layout = _workspace_frame_for_slot(display, idx, len(allocations), base_width, base_height)
        applied = False
        note = None
        if alloc.platform == "ios":
            ios.present(alloc.sim_id, layout=layout)
            state.set_presentation(alloc.slug, layout)
            applied = True
        else:
            applied = android.set_window_frame(
                alloc.sim_id,
                layout["x"],
                layout["y"],
                layout["width"],
                layout["height"],
            )
            if not applied:
                note = "Android emulator window not visible; launch with --window to place it in the workspace."
        placements.append(
            {
                "slug": alloc.slug,
                "platform": alloc.platform,
                "layout": layout,
                "applied": applied,
                "note": note,
            }
        )
    return placements


def _maybe_apply_agent_workspace(slug: str) -> Optional[dict]:
    alloc = state.require(slug)
    workspace = state.get_workspace(alloc.agent)
    if not workspace:
        return None
    placements = _apply_workspace_to_allocations(workspace, [alloc])
    return placements[0] if placements else None


def _ensure_ios_ready_or_heal(slug: str, sim_id: str) -> dict:
    saved_layout = state.get_presentation(slug)
    stable = ios.stabilize(sim_id)
    if not saved_layout:
        if stable.get("window_visible_on_active_desktop") is False:
            raise RuntimeError(
                f"Simulator window for '{slug}' is not visible on the active desktop. "
                f"Run `simemu present {slug}` or save a layout with `simemu present {slug} --save-layout`."
            )
        return {"healed": False, "stable": stable}

    if stable.get("window_visible_on_active_desktop") is False:
        ios.present(sim_id, layout=saved_layout)
        return {"healed": True, "stable": ios.stabilize(sim_id)}
    try:
        current_layout = ios.current_presentation_layout(sim_id)
    except Exception:
        ios.present(sim_id, layout=saved_layout)
        return {"healed": True, "stable": ios.stabilize(sim_id)}
    if _layout_differs(current_layout, saved_layout):
        ios.present(sim_id, layout=saved_layout)
        return {"healed": True, "stable": ios.stabilize(sim_id)}
    return {"healed": False, "stable": stable}


def _prepare_ios_interaction(slug: str, sim_id: str) -> None:
    _ensure_ios_ready_or_heal(slug, sim_id)


def _ios_presentation_status(slug: str, sim_id: str) -> dict:
    saved_layout = state.get_presentation(slug)
    if not saved_layout:
        return {
            "has_saved_layout": False,
            "layout_matches_saved": None,
            "layout_drifted": None,
            "saved_layout": None,
            "display_matches_saved": None,
            "display_drifted": None,
        }
    try:
        current_layout = ios.current_presentation_layout(sim_id)
    except Exception:
        return {
            "has_saved_layout": True,
            "layout_matches_saved": False,
            "layout_drifted": True,
            "saved_layout": saved_layout,
            "display_matches_saved": None,
            "display_drifted": None,
        }
    drifted = _layout_differs(current_layout, saved_layout)
    display_matches_saved = None
    display_drifted = None
    if saved_layout.get("display_id") is not None:
        current_display_id = current_layout.get("display_id")
        display_matches_saved = current_display_id == saved_layout["display_id"]
        display_drifted = not display_matches_saved
    return {
        "has_saved_layout": True,
        "layout_matches_saved": not drifted,
        "layout_drifted": drifted,
        "saved_layout": saved_layout,
        "display_matches_saved": display_matches_saved,
        "display_drifted": display_drifted,
    }


def cmd_tap(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    x, y = _resolve_coords(args, alloc)
    if alloc.platform == "ios":
        lease = _desktop_lease(alloc, "tap", f"Tap {x},{y} on {args.slug}",
                               estimated_seconds=5, coordinates=f"{x},{y}")
        with lease:
            lease.update(stage="Stabilizing simulator window", screen=alloc.device_name, scenario="UI interaction")
            _prepare_ios_interaction(args.slug, alloc.sim_id)
            lease.update(stage="Tapping interface", screen=f"{alloc.device_name} @ {x},{y}",
                         scenario="UI interaction", coordinates=f"{x},{y}")
            ios.tap(alloc.sim_id, x, y)
    else:
        android.tap(alloc.sim_id, x, y)


def cmd_swipe(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    x1, y1 = _resolve_coords(args, alloc, "x1", "y1")
    x2, y2 = _resolve_coords(args, alloc, "x2", "y2")
    if alloc.platform == "ios":
        lease = _desktop_lease(alloc, "swipe", f"Swipe {x1},{y1} to {x2},{y2} on {args.slug}",
                               estimated_seconds=6, coordinates=f"{x1},{y1}->{x2},{y2}",
                               duration_ms=args.duration)
        with lease:
            lease.update(stage="Stabilizing simulator window", screen=alloc.device_name, scenario="Gesture")
            _prepare_ios_interaction(args.slug, alloc.sim_id)
            lease.update(stage="Swiping interface", screen=f"{alloc.device_name} {x1},{y1}->{x2},{y2}",
                         scenario="Gesture", coordinates=f"{x1},{y1}->{x2},{y2}")
            ios.swipe(alloc.sim_id, x1, y1, x2, y2, duration=args.duration / 1000.0)
    else:
        android.swipe(alloc.sim_id, x1, y1, x2, y2, duration=args.duration)
    print(f"Swiped ({x1},{y1}) → ({x2},{y2}) on '{args.slug}'.")


def cmd_appearance(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.set_appearance(alloc.sim_id, args.mode)
    else:
        android.set_appearance(alloc.sim_id, args.mode)
    print(f"'{args.slug}' appearance set to {args.mode}.")


def cmd_shake(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.shake(alloc.sim_id)
    else:
        android.shake(alloc.sim_id)
    print(f"Shake sent to '{args.slug}'.")


def cmd_input(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        lease = _desktop_lease(alloc, "input", f"Enter text on {args.slug}",
                               estimated_seconds=4, text_preview=args.text[:40])
        with lease:
            lease.update(stage="Preparing text input", screen=alloc.device_name,
                         scenario="Keyboard input", text_preview=args.text[:40])
            ios.input_text(alloc.sim_id, args.text)
        print(f"Text copied to '{args.slug}' pasteboard (paste with Cmd+V or long-press).")
    else:
        android.input_text(alloc.sim_id, args.text)
        print(f"Text typed into '{args.slug}'.")


def cmd_privacy(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.privacy(alloc.sim_id, args.bundle_or_package, args.action, args.permission)
    else:
        android.privacy(alloc.sim_id, args.bundle_or_package, args.action, args.permission)
    print(f"Privacy '{args.action}' {args.permission} for '{args.bundle_or_package}' on '{args.slug}'.")


def cmd_rotate(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        ios.rotate(alloc.sim_id, args.orientation)
    else:
        android.rotate(alloc.sim_id, args.orientation)
    print(f"'{args.slug}' rotated to {args.orientation}.")


def cmd_key(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        lease = _desktop_lease(alloc, "key", f"Send {args.key} key to {args.slug}",
                               estimated_seconds=4, key_name=args.key)
        with lease:
            lease.update(stage="Stabilizing simulator window", screen=alloc.device_name,
                         scenario="Keyboard input", key_name=args.key)
            _prepare_ios_interaction(args.slug, alloc.sim_id)
            lease.update(stage="Sending key event", screen=f"{alloc.device_name} · {args.key}",
                         scenario="Keyboard input", key_name=args.key)
            ios.key(alloc.sim_id, args.key)
    else:
        android.key(alloc.sim_id, args.key)
    print(f"Key '{args.key}' sent to '{args.slug}'.")


def cmd_long_press(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    x, y = _resolve_coords(args, alloc)
    if alloc.platform == "ios":
        lease = _desktop_lease(alloc, "long-press", f"Long press {x},{y} on {args.slug}",
                               estimated_seconds=6, coordinates=f"{x},{y}",
                               duration_ms=getattr(args, "duration", 1000))
        with lease:
            lease.update(stage="Stabilizing simulator window", screen=alloc.device_name,
                         scenario="Gesture", coordinates=f"{x},{y}")
            _prepare_ios_interaction(args.slug, alloc.sim_id)
            lease.update(stage="Holding press", screen=f"{alloc.device_name} @ {x},{y}",
                         scenario="Gesture", coordinates=f"{x},{y}")
            ios.long_press(alloc.sim_id, x, y, duration=args.duration / 1000.0)
    else:
        android.long_press(alloc.sim_id, x, y, duration=args.duration)
    print(f"Long-pressed ({x},{y}) on '{args.slug}'.")


def cmd_clear_data(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform != "android":
        raise RuntimeError(
            "'clear-data' is Android only. "
            "For iOS, uninstall and reinstall the app to reset its data."
        )
    android.clear_data(alloc.sim_id, args.package)
    print(f"Cleared data for '{args.package}' on '{args.slug}'.")


def cmd_status_bar(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if args.clear:
        if alloc.platform == "ios":
            ios.status_bar_clear(alloc.sim_id)
        else:
            android.status_bar_clear(alloc.sim_id)
        print(f"Status bar restored on '{args.slug}'.")
    else:
        if alloc.platform == "ios":
            ios.status_bar(alloc.sim_id, time_str=args.time, battery=args.battery,
                           wifi=args.wifi, network=args.network)
        else:
            ios_only = args.network
            if ios_only:
                print("Warning: --network is iOS only, ignoring.", file=sys.stderr)
            android.status_bar(alloc.sim_id, time_str=args.time,
                               battery=args.battery, wifi=args.wifi)
        print(f"Status bar overridden on '{args.slug}'.")


def cmd_biometrics(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    match = args.result == "match"
    if alloc.platform == "ios":
        ios.biometrics(alloc.sim_id, match)
    else:
        android.biometrics(alloc.sim_id, match)
    result_str = "match" if match else "fail"
    print(f"Biometrics '{result_str}' sent to '{args.slug}'.")


def cmd_reboot(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    print(f"Rebooting '{args.slug}'...", flush=True)
    if alloc.platform == "ios":
        ios.reboot(alloc.sim_id)
    else:
        android.reboot(alloc.sim_id)
    print(f"'{args.slug}' rebooted.")


def cmd_network(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        raise RuntimeError(
            "'network' is Android only. iOS Simulator does not support runtime network "
            "toggling via CLI.\nUse Network Link Conditioner (macOS System Preferences) "
            "to simulate poor network conditions on iOS."
        )
    android.network(alloc.sim_id, args.mode)
    print(f"Network mode set to '{args.mode}' on '{args.slug}'.")


def cmd_battery(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        raise RuntimeError(
            "'battery' is Android only. iOS Simulator does not support battery level overrides via CLI."
        )
    if args.reset:
        android.battery(alloc.sim_id, reset=True)
        print(f"Battery level reset to real value on '{args.slug}'.")
    else:
        if args.level is None:
            raise RuntimeError("Specify --level 0-100 or --reset")
        android.battery(alloc.sim_id, level=args.level)
        print(f"Battery level set to {args.level}% on '{args.slug}'.")


def cmd_location(args):
    alloc = state.require(args.slug)
    state.touch(args.slug)
    if alloc.platform == "ios":
        if args.clear:
            ios.location_clear(alloc.sim_id)
            print(f"Location cleared on '{args.slug}'.")
        else:
            ios.location(alloc.sim_id, args.lat, args.lng)
            print(f"Location set to {args.lat},{args.lng} on '{args.slug}'.")
    else:
        if args.clear:
            raise RuntimeError("Location clear not supported on Android emulator.")
        android.location(alloc.sim_id, args.lat, args.lng)
        print(f"Location set to {args.lat},{args.lng} on '{args.slug}'.")


def cmd_reset_app(args):
    """Force-stop + clear app data + relaunch in one command."""
    alloc = state.require(args.slug)
    state.touch(args.slug)
    bundle = args.bundle_or_package
    print(f"Resetting '{bundle}' on '{args.slug}'...", flush=True)
    if alloc.platform == "ios":
        ios.reset_app(alloc.sim_id, bundle)
    else:
        android.reset_app(alloc.sim_id, bundle, launch=not args.no_launch)
    print("Done — app data cleared and app relaunched.")


def cmd_crash_log(args):
    """Show the most recent crash log for the simulator or a specific app."""
    alloc = state.require(args.slug)
    state.touch(args.slug)
    since = args.since or 60
    if alloc.platform == "ios":
        log = ios.crash_log(alloc.sim_id, bundle_id=args.bundle, since_minutes=since)
    else:
        log = android.crash_log(alloc.sim_id, package=args.bundle, since_minutes=since)

    if log is None:
        print(f"No crashes found in the last {since} minutes on '{args.slug}'.")
        if args.json:
            _print_json({"crash": None})
        return

    if args.json:
        _print_json({"crash": log})
    else:
        print(log)


def cmd_compare(args):
    """Take screenshots of two slugs and combine them side by side."""
    import subprocess as _sp
    alloc_a = state.require(args.slug_a)
    alloc_b = state.require(args.slug_b)
    state.touch(args.slug_a)
    state.touch(args.slug_b)

    max_size = args.max_size or int(os.environ.get("SIMEMU_SCREENSHOT_MAX_SIZE", 1000))

    path_a = _auto_path(args.slug_a, "png")
    path_b = _auto_path(args.slug_b, "png")

    print(f"Screenshotting '{args.slug_a}'...", flush=True)
    if alloc_a.platform == "ios":
        ios.screenshot(alloc_a.sim_id, path_a, max_size=max_size)
    else:
        android.screenshot(alloc_a.sim_id, path_a, max_size=max_size)

    print(f"Screenshotting '{args.slug_b}'...", flush=True)
    if alloc_b.platform == "ios":
        ios.screenshot(alloc_b.sim_id, path_b, max_size=max_size)
    else:
        android.screenshot(alloc_b.sim_id, path_b, max_size=max_size)

    output = args.output or _auto_path(f"{args.slug_a}_vs_{args.slug_b}", "png")

    # Use sips + ImageMagick convert if available, else fall back to sips tiling
    convert = _sp.run(["which", "convert"], capture_output=True, text=True)
    if convert.returncode == 0:
        _sp.run(["convert", "+append", path_a, path_b, output], check=True)
    else:
        # sips can append images horizontally via --padColor and canvas tricks;
        # simpler fallback: just report both paths separately
        print("Note: install ImageMagick ('brew install imagemagick') for side-by-side compositing.")
        print(f"  {args.slug_a}: {path_a}")
        print(f"  {args.slug_b}: {path_b}")
        if args.json:
            _print_json({"path_a": path_a, "path_b": path_b})
        return

    print(f"Comparison saved: {output}")
    if args.json:
        _print_json({"path": output, "path_a": path_a, "path_b": path_b})


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
    p.add_argument("--version", action="version", version="simemu 0.1.0")
    p.add_argument("--no-autostart", action="store_true",
                   help="Do not auto-start the simemu API server for this invocation")
    sub = p.add_subparsers(dest="command", required=True)

    # acquire
    acq = sub.add_parser("acquire", help="Reserve a simulator or real device")
    acq.add_argument("platform", choices=["ios", "android"])
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

    # status
    st = sub.add_parser("status", help="Show all current reservations")
    st.add_argument("--json", action="store_true", help="Output as JSON")
    st.set_defaults(func=cmd_status)

    # list
    ls = sub.add_parser("list", help="Show available (unreserved) simulators")
    ls.add_argument("platform", nargs="?", choices=["ios", "android"])
    ls.add_argument("--json", action="store_true", help="Output as JSON")
    ls.set_defaults(func=cmd_list)

    # list-devices
    ld = sub.add_parser("list-devices", help="Show connected real devices (not simulators)")
    ld.add_argument("platform", nargs="?", choices=["ios", "android"])
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
    serve_p.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
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
        for url in ("http://127.0.0.1:8765/health", "http://127.0.0.1:8765/status"):
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


def main():
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "no_autostart", False):
        os.environ["SIMEMU_NO_AUTOSTART"] = "1"
    if getattr(args.func, "__name__", "") not in {"cmd_serve", "cmd_daemon"}:
        _autostart_server_if_needed()
    try:
        args.func(args)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
