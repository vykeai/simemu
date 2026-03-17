"""
State management for simemu — tracks which simulators are allocated to which agents.

Agents work with semantic slugs (e.g. "fitkind-app"), not raw simulator IDs.
State is persisted in /tmp/simemu/state.json, protected by an exclusive file lock.

Schema:
  allocations[slug] = {
    slug:             "fitkind-app"
    sim_id:           UDID (iOS) or AVD name (Android)
    platform:         "ios" | "android"
    device_name:      "iPhone 17 Pro"
    agent:            agent identifier string
    acquired_at:      ISO timestamp
    pid:              PID of acquiring process
    heartbeat_at:     ISO timestamp, updated on every proxy command (informational only)
    recording_pid:    PID of background video recording process (or null)
    recording_output: local output path for active recording (or null)
  }
"""

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def state_dir() -> Path:
    return Path(os.environ.get("SIMEMU_STATE_DIR", "/tmp/simemu"))


def config_dir() -> Path:
    return Path(os.environ.get("SIMEMU_CONFIG_DIR", Path.home() / ".simemu"))


def state_file() -> Path:
    return state_dir() / "state.json"


def lock_file() -> Path:
    return state_dir() / "state.lock"


def presentation_file() -> Path:
    return config_dir() / "presentation.json"


@dataclass
class Allocation:
    slug: str
    sim_id: str
    platform: str        # "ios" | "android"
    device_name: str
    agent: str
    acquired_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    pid: Optional[int] = None
    heartbeat_at: Optional[str] = None
    recording_pid: Optional[int] = None
    recording_output: Optional[str] = None  # local path for active recording


@contextmanager
def _locked_state():
    base_dir = state_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    lock_fd = open(lock_file(), "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _read_raw()
        pending = []

        def save(new_state):
            pending.append(new_state)

        yield state, save

        if pending:
            _write_raw(pending[-1])
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _read_raw() -> dict:
    current_state_file = state_file()
    if current_state_file.exists():
        try:
            return json.loads(current_state_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"allocations": {}}


def _write_raw(state: dict):
    current_state_file = state_file()
    tmp = current_state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(current_state_file)


@contextmanager
def _locked_presentation():
    base_dir = config_dir()
    base_dir.mkdir(parents=True, exist_ok=True)
    lock_path = base_dir / "presentation.lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        current = _read_presentation_raw()
        pending = []

        def save(new_state):
            pending.append(new_state)

        yield current, save

        if pending:
            _write_presentation_raw(pending[-1])
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


def _read_presentation_raw() -> dict:
    current_file = presentation_file()
    if current_file.exists():
        try:
            return json.loads(current_file.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"layouts": {}, "workspaces": {}}


def _write_presentation_raw(state: dict):
    current_file = presentation_file()
    tmp = current_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(current_file)


def acquire(slug: str, sim_id: str, platform: str, device_name: str, agent: str) -> "Allocation":
    """Reserve sim_id under slug. Raises if already in use."""
    with _locked_state() as (state, save):
        allocations = state["allocations"]

        if slug in allocations:
            existing = Allocation(**allocations[slug])
            raise RuntimeError(
                f"Slug '{slug}' is already reserved by agent '{existing.agent}' "
                f"on {existing.device_name} (since {existing.acquired_at})"
            )

        for other_slug, raw in allocations.items():
            other = Allocation(**raw)
            if other.sim_id == sim_id:
                raise RuntimeError(
                    f"Simulator '{device_name}' is already reserved as "
                    f"'{other_slug}' by agent '{other.agent}'"
                )

        alloc = Allocation(
            slug=slug,
            sim_id=sim_id,
            platform=platform,
            device_name=device_name,
            agent=agent,
            pid=os.getpid(),
            heartbeat_at=datetime.now(timezone.utc).isoformat(),
        )
        allocations[slug] = asdict(alloc)
        save(state)
        return alloc


def release(slug: str, agent: Optional[str] = None) -> "Allocation":
    """Release reservation for slug."""
    with _locked_state() as (state, save):
        allocations = state["allocations"]
        if slug not in allocations:
            raise RuntimeError(f"No reservation found for slug '{slug}'")
        existing = Allocation(**allocations[slug])
        if agent is not None and existing.agent != agent:
            raise RuntimeError(
                f"'{slug}' is reserved by agent '{existing.agent}', not '{agent}'.\n"
                f"To release it, run with the correct identity:\n"
                f"  SIMEMU_AGENT={existing.agent} simemu release {slug}\n"
                f"If this was your slug but SIMEMU_AGENT wasn't set, use the agent shown above."
            )
        del allocations[slug]
        save(state)
        return existing


def touch(slug: str) -> None:
    """Update heartbeat. Called automatically by every proxy command."""
    with _locked_state() as (state, save):
        allocations = state["allocations"]
        if slug in allocations:
            allocations[slug]["heartbeat_at"] = datetime.now(timezone.utc).isoformat()
            save(state)


def set_recording(slug: str, pid: Optional[int], output: Optional[str]) -> None:
    """Store or clear active recording state."""
    with _locked_state() as (state, save):
        allocations = state["allocations"]
        if slug in allocations:
            allocations[slug]["recording_pid"] = pid
            allocations[slug]["recording_output"] = output
            save(state)


def get_all() -> dict[str, "Allocation"]:
    state = _read_raw()
    return {k: Allocation(**v) for k, v in state["allocations"].items()}


def get(slug: str) -> Optional["Allocation"]:
    return get_all().get(slug)


def require(slug: str) -> "Allocation":
    alloc = get(slug)
    if alloc is None:
        raise RuntimeError(
            f"No reservation for '{slug}'. Check `simemu status` and ask the project owner to assign a slug."
        )
    return alloc


def get_presentation(slug: str) -> Optional[dict]:
    state = _read_presentation_raw()
    return state["layouts"].get(slug)


def set_presentation(slug: str, layout: dict) -> None:
    with _locked_presentation() as (state, save):
        state["layouts"][slug] = layout
        save(state)


def clear_presentation(slug: str) -> bool:
    with _locked_presentation() as (state, save):
        existed = slug in state["layouts"]
        if existed:
            del state["layouts"][slug]
            save(state)
        return existed


def get_workspace(agent: str) -> Optional[dict]:
    state = _read_presentation_raw()
    return state.get("workspaces", {}).get(agent)


def set_workspace(agent: str, workspace: dict) -> None:
    with _locked_presentation() as (state, save):
        state.setdefault("workspaces", {})[agent] = workspace
        save(state)


def clear_workspace(agent: str) -> bool:
    with _locked_presentation() as (state, save):
        workspaces = state.setdefault("workspaces", {})
        existed = agent in workspaces
        if existed:
            del workspaces[agent]
            save(state)
        return existed
