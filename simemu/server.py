"""
simemu HTTP API server.

Start with:  simemu serve [--port 8765] [--host 127.0.0.1]

All simulator state (allocations, locking) is shared with the CLI —
you can mix CLI and API calls freely.

OpenAPI docs available at http://host:port/docs
"""

from __future__ import annotations

try:
    from fastapi import FastAPI, HTTPException, BackgroundTasks
    from fastapi.responses import FileResponse, JSONResponse
    import uvicorn
except ImportError:
    raise ImportError(
        "API server requires extra dependencies.\n"
        "Install with:  pip install 'simemu[api]'"
    )

import asyncio
import json
import os
import pathlib
import socket
import subprocess
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from . import state, ios, android, device
from .discover import (
    list_ios, list_android, list_real_ios, list_real_android,
    find_simulator, NoSimulatorAvailable,
)
from . import create as _create
from . import session as session_module
from .session import ClaimSpec, SessionError


# ── idle-shutdown background task ─────────────────────────────────────────────

def _shutdown_idle_simulators(timeout_minutes: int) -> list[str]:
    """Shut down (not release) simulators idle longer than timeout_minutes.

    Returns list of slugs that were shut down.
    """
    now = datetime.now(timezone.utc)
    shut_down = []
    for slug, alloc in state.get_all().items():
        if not alloc.heartbeat_at:
            continue
        last = datetime.fromisoformat(alloc.heartbeat_at)
        idle_min = (now - last).total_seconds() / 60
        if idle_min >= timeout_minutes:
            # Re-read fresh state to avoid TOCTOU race: an agent may have
            # sent a command (updating the heartbeat) between our initial
            # snapshot and now.  Without this re-check the daemon could kill
            # an emulator that a command is actively using.
            fresh_alloc = state.get(slug)
            if fresh_alloc and fresh_alloc.heartbeat_at:
                fresh_last = datetime.fromisoformat(fresh_alloc.heartbeat_at)
                fresh_idle = (datetime.now(timezone.utc) - fresh_last).total_seconds() / 60
                if fresh_idle < timeout_minutes:
                    continue  # heartbeat was refreshed — skip shutdown

            print(
                f"[simemu-daemon] '{slug}' ({alloc.device_name}) "
                f"idle {idle_min:.0f}m → shutting down",
                flush=True,
            )
            try:
                if alloc.platform == "ios":
                    ios.shutdown(alloc.sim_id)
                else:
                    android.shutdown(alloc.sim_id)
                shut_down.append(slug)
                print(f"[simemu-daemon] '{slug}' shut down.", flush=True)
            except Exception as e:
                print(f"[simemu-daemon] '{slug}' shutdown failed: {e}", flush=True)
    return shut_down


def _kill_rogue_emulators() -> list[int]:
    """Kill qemu/emulator processes that are not tracked in simemu state.

    Android emulators that crash or are abandoned outside of simemu can run
    indefinitely at 100% CPU. This scans for any qemu-system-aarch64 / emulator
    processes and kills those whose AVD name does not match a tracked allocation.
    """
    import re
    import signal as _signal

    tracked_avds: set[str] = set()
    for alloc in state.get_all().values():
        if alloc.platform == "android" and alloc.device_name:
            tracked_avds.add(alloc.device_name.lower())

    try:
        out = subprocess.run(
            ["ps", "-Ao", "pid,command"], capture_output=True, text=True
        ).stdout
    except Exception:
        return []

    killed: list[int] = []
    for line in out.splitlines():
        if not any(x in line for x in ("qemu-system-aarch64", "emulator -avd")):
            continue
        parts = line.strip().split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        avd_match = re.search(r"-avd\s+(\S+)", parts[1])
        avd_name = avd_match.group(1).lower() if avd_match else None
        if avd_name and avd_name in tracked_avds:
            continue
        try:
            os.kill(pid, _signal.SIGKILL)
            killed.append(pid)
            print(
                f"[simemu-daemon] killed rogue emulator pid={pid} avd={avd_name or '?'}",
                flush=True,
            )
        except (ProcessLookupError, PermissionError):
            pass
    return killed


async def _idle_shutdown_loop(timeout_minutes: int) -> None:
    """Background coroutine: check and shut down idle simulators every minute."""
    print(
        f"[simemu-daemon] Idle-shutdown active — timeout: {timeout_minutes}m, "
        f"checking every 60s",
        flush=True,
    )
    while True:
        await asyncio.sleep(60)
        _shutdown_idle_simulators(timeout_minutes)
        # Kill any rogue Android emulator processes not tracked by simemu
        try:
            killed = _kill_rogue_emulators()
            if killed:
                print(
                    f"[simemu-daemon] rogue watchdog killed {len(killed)} process(es): {killed}",
                    flush=True,
                )
        except Exception as e:
            print(f"[simemu-daemon] rogue watchdog error: {e}", flush=True)
        # v2 session lifecycle tick
        try:
            session_module.lifecycle_tick()
        except Exception as e:
            print(f"[simemu-daemon] session lifecycle_tick error: {e}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = int(os.environ.get("SIMEMU_IDLE_TIMEOUT", "20"))
    task = asyncio.create_task(_idle_shutdown_loop(timeout))

    # Federation mDNS advertising
    _fed_port = int(os.environ.get("SIMEMU_FED_PORT", "8766"))
    _fed_config_path = pathlib.Path.home() / ".fed" / "config.json"
    try:
        _fed_cfg = json.loads(_fed_config_path.read_text())
        _identity = _fed_cfg.get("identity") or os.environ.get("SIMEMU_IDENTITY", socket.gethostname())
    except Exception:
        _identity = os.environ.get("SIMEMU_IDENTITY", socket.gethostname())
    _fed_started = False
    try:
        from .fed import start_federation
        start_federation(_identity, _fed_port)
        _fed_started = True
    except ImportError:
        print("[simemu] zeroconf not installed — skipping federation mDNS", flush=True)
    except Exception as e:
        print(f"[simemu] federation mDNS unavailable ({e}) — continuing without it", flush=True)

    yield

    if _fed_started:
        from .fed import stop_federation
        stop_federation()

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="simemu",
    description="Simulator allocation manager for multi-agent iOS/Android development.",
    version="0.1.0",
    lifespan=lifespan,
)

from .dashboard import register_dashboard
register_dashboard(app, state.get_all)


# ── request / response models ─────────────────────────────────────────────────

class CreateIosRequest(BaseModel):
    name: str               # display name for the new simulator
    device: str             # partial match e.g. "iPhone 16 Pro"
    os: str                 # partial match e.g. "iOS 18" or "18.0"

class CreateAndroidRequest(BaseModel):
    name: str               # AVD name
    api: int                # Android API level e.g. 35
    device: str = "medium_phone"
    tag: str = "google_apis"
    abi: str = "x86_64"
    force: bool = False


def _output_dir() -> Path:
    d = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── federation endpoints ──────────────────────────────────────────────────────

@app.get("/fed/info", summary="Federation identity and capabilities")
async def fed_info():
    return {
        "machine": socket.gethostname(),
        "service": "simemu",
        "version": app.version,
        "capabilities": ["simulators"],
    }


@app.get("/fed/runs", summary="Active simulator allocations")
async def fed_runs():
    allocations = state.get_all()
    return [
        {"slug": slug, "simulator": alloc.device_name, "agent": alloc.agent, "platform": alloc.platform}
        for slug, alloc in allocations.items()
    ]


# ── status & discovery ────────────────────────────────────────────────────────

@app.get("/health", summary="Server health")
def health():
    return {"status": "ok"}

@app.get("/status", summary="All current reservations")
def get_status():
    allocations = state.get_all()
    return [
        a.__dict__
        for a in allocations.values()
    ]


@app.get("/simulators", summary="Available (unreserved) simulators")
def list_simulators(platform: Optional[str] = None):
    allocated_ids = {a.sim_id for a in state.get_all().values()}
    rows = []
    if not platform or platform == "ios":
        rows += list_ios(allocated_ids)
    if not platform or platform == "android":
        rows += list_android(allocated_ids)
    return [r.__dict__ for r in rows]


@app.get("/devices", summary="Connected real devices (not simulators)")
def list_devices(platform: Optional[str] = None):
    allocated_ids = {a.sim_id for a in state.get_all().values()}
    rows = []
    if not platform or platform == "ios":
        rows += list_real_ios(allocated_ids)
    if not platform or platform == "android":
        rows += list_real_android(allocated_ids)
    return [r.__dict__ for r in rows]


# ── create ────────────────────────────────────────────────────────────────────

@app.get("/create/ios/device-types",
         summary="List available iOS device types for simulator creation")
def ios_device_types():
    return [{"name": d.name, "identifier": d.identifier}
            for d in _create.list_ios_device_types()]


@app.get("/create/ios/runtimes",
         summary="List installed iOS runtimes for simulator creation")
def ios_runtimes():
    return [{"name": r.name, "identifier": r.identifier}
            for r in _create.list_ios_runtimes()]


@app.post("/create/ios",
          summary="Create a new iOS simulator",
          status_code=201)
def create_ios(req: CreateIosRequest):
    try:
        udid = _create.create_ios(req.name, req.device, req.os)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"name": req.name, "udid": udid, "platform": "ios"}


@app.get("/create/android/system-images",
         summary="List installed Android system images for AVD creation")
def android_system_images():
    return [i.__dict__ for i in _create.list_android_system_images()]


@app.get("/create/android/device-profiles",
         summary="List Android hardware profiles for AVD creation")
def android_device_profiles():
    return [d.__dict__ for d in _create.list_android_devices()]


@app.post("/create/android",
          summary="Create a new Android AVD",
          status_code=201)
def create_android(req: CreateAndroidRequest):
    try:
        avd = _create.create_android(
            avd_name=req.name,
            api_level=req.api,
            device_query=req.device,
            tag=req.tag,
            abi=req.abi,
            force=req.force,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"name": avd, "platform": "android"}


# ── v2 session-based API ──────────────────────────────────────────────────────

class V2ClaimRequest(BaseModel):
    platform: str                            # "ios" | "android"
    form_factor: str = "phone"               # "phone" | "tablet" | "watch" | "tv" | "vision"
    os_version: Optional[str] = None
    real_device: bool = False
    label: str = ""


class V2DoRequest(BaseModel):
    session: str
    command: str
    args: list[str] = []


@app.post("/v2/claim", summary="Claim a device session (v2 API)")
def v2_claim(req: V2ClaimRequest):
    try:
        state.check_maintenance()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    spec = ClaimSpec(
        platform=req.platform,
        form_factor=req.form_factor,
        os_version=req.os_version,
        real_device=req.real_device,
        label=req.label,
    )
    try:
        session = session_module.claim(spec)
    except SessionError as e:
        raise HTTPException(status_code=409, detail=e.to_json())
    except NoSimulatorAvailable as e:
        raise HTTPException(status_code=409, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return session.to_agent_json()


@app.post("/v2/do", summary="Execute a command on a session (v2 API)")
def v2_do(req: V2DoRequest):
    try:
        result = session_module.do_command(req.session, req.command, req.args)
    except SessionError as e:
        raise HTTPException(status_code=409, detail=e.to_json())
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return result or {"status": "ok"}


@app.get("/v2/sessions", summary="List all active v2 sessions")
def v2_sessions():
    sessions = session_module.get_active_sessions()
    return [s.to_agent_json() for s in sessions.values()]


# ── server entrypoint ─────────────────────────────────────────────────────────

def serve(host: str = "127.0.0.1", port: int = 8765):
    uvicorn.run(app, host=host, port=port)
