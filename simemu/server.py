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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from . import state, ios, android
from .discover import list_ios, list_android, find_simulator, NoSimulatorAvailable
from . import create as _create


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

class AcquireRequest(BaseModel):
    platform: str           # "ios" | "android"
    slug: str
    agent: str
    device: Optional[str] = None     # partial device name filter
    boot: bool = True
    headless: bool = True            # Android: headless by default; pass False for windowed

class ReleaseRequest(BaseModel):
    agent: str

class InstallRequest(BaseModel):
    app_url: str            # URL or absolute local path to .app/.ipa/.apk
    agent: str

class LaunchRequest(BaseModel):
    bundle_or_package: str
    agent: str
    args: list[str] = []

class TerminateRequest(BaseModel):
    bundle_or_package: str
    agent: str

class UninstallRequest(BaseModel):
    bundle_or_package: str
    agent: str

class UrlRequest(BaseModel):
    url: str
    agent: str

class RecordStartRequest(BaseModel):
    agent: str
    codec: Optional[str] = None     # iOS only: hevc | h264 | hevc-alpha

class RecordStopRequest(BaseModel):
    agent: str

class EraseRequest(BaseModel):
    agent: str

class PushRequest(BaseModel):
    local_path: str
    remote_path: str
    agent: str

class PullRequest(BaseModel):
    remote_path: str
    local_path: str
    agent: str

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


# ── helpers ───────────────────────────────────────────────────────────────────

def _require(slug: str) -> state.Allocation:
    alloc = state.get(slug)
    if alloc is None:
        raise HTTPException(status_code=404, detail=f"No reservation for slug '{slug}'. Acquire it first.")
    return alloc

def _check_agent(alloc: state.Allocation, agent: str):
    if alloc.agent != agent:
        raise HTTPException(
            status_code=403,
            detail=f"'{alloc.slug}' is owned by agent '{alloc.agent}', not '{agent}'."
        )

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


# ── acquire & release ─────────────────────────────────────────────────────────

@app.post("/acquire", summary="Reserve a simulator by slug")
def acquire(req: AcquireRequest):
    try:
        sim = find_simulator(req.platform, req.device)
    except NoSimulatorAvailable as e:
        raise HTTPException(status_code=409, detail=str(e))

    try:
        alloc = state.acquire(
            slug=req.slug,
            sim_id=sim.sim_id,
            platform=sim.platform,
            device_name=sim.device_name,
            agent=req.agent,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))

    if req.boot:
        try:
            if sim.platform == "ios":
                ios.boot(sim.sim_id, minimize=req.headless)
            else:
                android.boot(sim.sim_id, headless=req.headless)
        except Exception as e:
            state.release(req.slug)
            raise HTTPException(status_code=500, detail=f"Boot failed: {e}")

    return {**alloc.__dict__, "runtime": sim.runtime}


@app.delete("/release/{slug}", summary="Release a reservation")
def release(slug: str, req: ReleaseRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)

    if alloc.recording_pid is not None:
        if alloc.platform == "ios":
            ios.record_stop(alloc.recording_pid)
        else:
            android.record_stop(alloc.recording_pid)

    try:
        released = state.release(slug, agent=req.agent)
    except RuntimeError as e:
        raise HTTPException(status_code=403, detail=str(e))

    return {"released": slug, "device_name": released.device_name}


# ── simulator control ─────────────────────────────────────────────────────────

@app.post("/simulators/{slug}/boot", summary="Boot the simulator")
def boot(slug: str, agent: str):
    alloc = _require(slug)
    _check_agent(alloc, agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.boot(alloc.sim_id)
    else:
        android.boot(alloc.sim_id)
    return {"status": "booted", "slug": slug}


@app.post("/simulators/{slug}/shutdown", summary="Shut down the simulator")
def shutdown(slug: str, agent: str):
    alloc = _require(slug)
    _check_agent(alloc, agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.shutdown(alloc.sim_id)
    else:
        android.shutdown(alloc.sim_id)
    return {"status": "shutdown", "slug": slug}


@app.post("/simulators/{slug}/erase", summary="Factory reset the simulator (keeps it)")
def erase(slug: str, req: EraseRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.erase(alloc.sim_id)
    else:
        android.erase(alloc.sim_id)
    return {"status": "erased", "slug": slug}


class RenameRequest(BaseModel):
    agent: str
    name: str

class DeleteRequest(BaseModel):
    agent: str

@app.patch("/simulators/{slug}/rename", summary="Rename a simulator or AVD")
def rename_simulator(slug: str, req: RenameRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.rename(alloc.sim_id, req.name)
    else:
        android.rename(alloc.sim_id, req.name)
    with state._locked_state() as (s, save):
        if slug in s["allocations"]:
            s["allocations"][slug]["device_name"] = req.name
            save(s)
    return {"slug": slug, "name": req.name}


@app.delete("/simulators/{slug}",
            summary="Permanently remove a simulator or AVD (releases reservation too)",
            status_code=200)
def delete_simulator(slug: str, req: DeleteRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    if alloc.recording_pid:
        if alloc.platform == "ios":
            ios.record_stop(alloc.recording_pid)
        else:
            android.record_stop(alloc.recording_pid)
    state.release(slug, agent=None)
    try:
        if alloc.platform == "ios":
            ios.delete(alloc.sim_id)
        else:
            android.delete(alloc.sim_id)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "deleted", "slug": slug, "device_name": alloc.device_name}


@app.get("/simulators/{slug}/env", summary="Device info for a reservation")
def env(slug: str):
    alloc = _require(slug)
    state.touch(slug)
    if alloc.platform == "ios":
        info = ios.get_env(alloc.sim_id)
    else:
        info = android.get_env(alloc.sim_id)
    info["slug"] = slug
    info["agent"] = alloc.agent
    info["acquired_at"] = alloc.acquired_at
    return info


# ── app management ────────────────────────────────────────────────────────────

@app.post("/simulators/{slug}/install", summary="Install an app (.app/.ipa/.apk)")
def install(slug: str, req: InstallRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    state.touch(slug)
    path = req.app_url
    if not Path(path).exists():
        raise HTTPException(status_code=400, detail=f"File not found: {path}")
    try:
        if alloc.platform == "ios":
            ios.install(alloc.sim_id, path)
        else:
            android.install(alloc.sim_id, path)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"status": "installed", "app": path}


@app.get("/simulators/{slug}/apps", summary="List installed apps")
def list_apps(slug: str):
    alloc = _require(slug)
    state.touch(slug)
    if alloc.platform == "ios":
        return ios.list_apps(alloc.sim_id)
    else:
        return android.list_apps(alloc.sim_id)


@app.post("/simulators/{slug}/launch", summary="Launch an app")
def launch(slug: str, req: LaunchRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.launch(alloc.sim_id, req.bundle_or_package, req.args)
    else:
        android.launch(alloc.sim_id, req.bundle_or_package, req.args)
    return {"status": "launched", "app": req.bundle_or_package}


@app.post("/simulators/{slug}/terminate", summary="Force-stop an app")
def terminate(slug: str, req: TerminateRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.terminate(alloc.sim_id, req.bundle_or_package)
    else:
        android.terminate(alloc.sim_id, req.bundle_or_package)
    return {"status": "terminated", "app": req.bundle_or_package}


@app.delete("/simulators/{slug}/apps/{bundle_or_package}", summary="Uninstall an app")
def uninstall(slug: str, bundle_or_package: str, agent: str):
    alloc = _require(slug)
    _check_agent(alloc, agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.uninstall(alloc.sim_id, bundle_or_package)
    else:
        android.uninstall(alloc.sim_id, bundle_or_package)
    return {"status": "uninstalled", "app": bundle_or_package}


# ── capture ───────────────────────────────────────────────────────────────────

@app.post("/simulators/{slug}/screenshot",
          summary="Take a screenshot — returns the image file")
def screenshot(slug: str, agent: str, fmt: str = "png"):
    alloc = _require(slug)
    _check_agent(alloc, agent)
    state.touch(slug)

    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = str(_output_dir() / f"{slug}_{ts}.{fmt}")

    if alloc.platform == "ios":
        ios.screenshot(alloc.sim_id, output, fmt=fmt if fmt != "png" else None)
    else:
        android.screenshot(alloc.sim_id, output)

    return FileResponse(output, media_type=f"image/{fmt}", filename=Path(output).name)


@app.post("/simulators/{slug}/record/start", summary="Start video recording")
def record_start(slug: str, req: RecordStartRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)

    if alloc.recording_pid is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Recording already active (pid {alloc.recording_pid}). Stop it first."
        )

    import datetime
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    output = str(_output_dir() / f"{slug}_{ts}.mp4")

    if alloc.platform == "ios":
        pid = ios.record_start(alloc.sim_id, output, codec=req.codec)
    else:
        pid = android.record_start(alloc.sim_id, output)

    state.set_recording(slug, pid, output)
    return {"status": "recording", "pid": pid, "output": output}


@app.post("/simulators/{slug}/record/stop",
          summary="Stop recording — returns the video file")
def record_stop(slug: str, req: RecordStopRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)

    if alloc.recording_pid is None:
        raise HTTPException(status_code=409, detail="No active recording.")

    output = alloc.recording_output

    if alloc.platform == "ios":
        ios.record_stop(alloc.recording_pid)
    else:
        android.record_stop(alloc.recording_pid)

    state.set_recording(slug, None, None)

    if output and Path(output).exists():
        return FileResponse(output, media_type="video/mp4", filename=Path(output).name)

    return {"status": "stopped", "output": output}


@app.post("/simulators/{slug}/url", summary="Open a URL in the simulator")
def open_url(slug: str, req: UrlRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    state.touch(slug)
    if alloc.platform == "ios":
        ios.open_url(alloc.sim_id, req.url)
    else:
        android.open_url(alloc.sim_id, req.url)
    return {"status": "opened", "url": req.url}


# ── Android file transfer ─────────────────────────────────────────────────────

@app.post("/simulators/{slug}/push", summary="Push a file to Android emulator")
def push(slug: str, req: PushRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    if alloc.platform != "android":
        raise HTTPException(status_code=400, detail="push is Android only.")
    state.touch(slug)
    android.push(alloc.sim_id, req.local_path, req.remote_path)
    return {"status": "pushed", "remote": req.remote_path}


@app.post("/simulators/{slug}/pull", summary="Pull a file from Android emulator")
def pull(slug: str, req: PullRequest):
    alloc = _require(slug)
    _check_agent(alloc, req.agent)
    if alloc.platform != "android":
        raise HTTPException(status_code=400, detail="pull is Android only.")
    state.touch(slug)
    android.pull(alloc.sim_id, req.remote_path, req.local_path)
    return FileResponse(req.local_path, filename=Path(req.local_path).name)


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


# ── server entrypoint ─────────────────────────────────────────────────────────

def serve(host: str = "127.0.0.1", port: int = 8765):
    uvicorn.run(app, host=host, port=port)
