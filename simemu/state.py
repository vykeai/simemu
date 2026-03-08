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

STATE_DIR = Path("/tmp/simemu")
STATE_FILE = STATE_DIR / "state.json"
LOCK_FILE = STATE_DIR / "state.lock"


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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
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
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"allocations": {}}


def _write_raw(state: dict):
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE_FILE)


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
