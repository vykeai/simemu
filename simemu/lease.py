"""Codeuctor-facing device lease API.

Leases are a machine-readable facade over v2 sessions. The session layer still
owns allocation, booting, duplicate-device protection, and release semantics.
"""

from __future__ import annotations

import os
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from . import session as session_module
from .session import ClaimSpec, Session, SessionError

DEFAULT_LEASE_TTL_SECONDS = 60 * 60


@dataclass
class LeaseClaimSpec:
    platform: str
    host: str = ""
    run_id: str = ""
    device: str | None = None
    form_factor: str = "phone"
    os_version: str | None = None
    real_device: bool = False
    visible: bool = False
    label: str = ""
    expires_in_seconds: int = DEFAULT_LEASE_TTL_SECONDS


@dataclass
class DeviceLease:
    lease_id: str
    session: str
    host: str
    run_id: str
    platform: str
    form_factor: str
    requested_device: str | None
    device: dict
    boot: dict
    connection: dict
    status: str
    created_at: str
    expires_at: str
    release_command: str

    def to_json(self) -> dict:
        return asdict(self)


def claim_device_lease(spec: LeaseClaimSpec) -> DeviceLease:
    """Claim a simulator/device lease and persist Codeuctor metadata."""
    host = spec.host or socket.gethostname()
    run_id = spec.run_id or os.environ.get("SIMEMU_RUN_ID", "")
    ttl = max(1, spec.expires_in_seconds or DEFAULT_LEASE_TTL_SECONDS)
    lease_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()

    claim_spec = ClaimSpec(
        platform=spec.platform,
        form_factor=spec.form_factor,
        os_version=spec.os_version,
        real_device=spec.real_device,
        device_selector=spec.device,
        label=spec.label,
        visible=spec.visible,
    )
    claimed = session_module.claim(claim_spec)

    with session_module._locked_sessions() as (data, save):
        raw = data["sessions"].get(claimed.session_id)
        if raw is not None:
            raw["lease"] = {
                "lease_id": claimed.session_id,
                "host": host,
                "run_id": run_id,
                "requested_device": spec.device,
                "expires_at": lease_expires_at,
                "created_at": claimed.created_at,
            }
            raw["expires_at"] = lease_expires_at
            save(data)

    claimed.expires_at = lease_expires_at
    return lease_from_session(claimed, host=host, run_id=run_id, requested_device=spec.device)


def release_device_lease(lease_id: str) -> DeviceLease:
    """Release a lease by its lease/session id."""
    session = session_module.get_session(lease_id)
    if session is None:
        raise SessionError(
            error="lease_not_found",
            session=lease_id,
            hint=f"No lease with ID '{lease_id}'. Check `simemu lease list`.",
        )
    raw_lease = session_module._read_sessions_raw().get("sessions", {}).get(lease_id, {}).get("lease")
    released = session_module.release(lease_id)
    return lease_from_session(released, raw_lease=raw_lease)


def list_device_leases() -> list[DeviceLease]:
    """Return active/idle/parked leases visible to Codeuctor."""
    sessions = session_module.get_active_sessions()
    raw_sessions = session_module._read_sessions_raw().get("sessions", {})
    return [
        lease_from_session(session, raw_lease=raw_sessions.get(session_id, {}).get("lease"))
        for session_id, session in sessions.items()
    ]


def lease_from_session(
    session: Session,
    *,
    host: str | None = None,
    run_id: str | None = None,
    requested_device: str | None = None,
    raw_lease: dict | None = None,
) -> DeviceLease:
    raw_lease = raw_lease or {}
    lease_host = host or raw_lease.get("host") or socket.gethostname()
    lease_run_id = run_id if run_id is not None else raw_lease.get("run_id", "")
    lease_requested_device = requested_device if requested_device is not None else raw_lease.get("requested_device")
    expires_at = raw_lease.get("expires_at") or session.expires_at or session.heartbeat_at

    return DeviceLease(
        lease_id=session.session_id,
        session=session.session_id,
        host=lease_host,
        run_id=lease_run_id,
        platform=session.platform,
        form_factor=session.form_factor,
        requested_device=lease_requested_device,
        device={
            "id": session.sim_id,
            "name": session.device_name,
            "os_version": session.resolved_os_version or session.os_version or "latest",
            "real_device": session.real_device,
        },
        boot={
            "state": boot_state(session),
            "session_status": session.status,
        },
        connection=connection_details(session, lease_host),
        status=session.status,
        created_at=session.created_at,
        expires_at=expires_at,
        release_command=f"simemu lease release {session.session_id}",
    )


def boot_state(session: Session) -> str:
    if session.status == "parked":
        return "parked"
    if session.status in {"expired", "released"}:
        return session.status
    if session.real_device or session.platform == "macos":
        return "connected"
    return "booted"


def connection_details(session: Session, host: str) -> dict:
    if session.platform == "android" and session.pinned_serial:
        identifier_kind = "adb_serial"
        identifier = session.pinned_serial
    elif session.platform == "macos":
        identifier_kind = "native"
        identifier = "macos-native"
    elif session.real_device:
        identifier_kind = "device_id"
        identifier = session.sim_id
    else:
        identifier_kind = "simulator_id"
        identifier = session.sim_id

    return {
        "kind": "local",
        "host": host,
        "identifier_kind": identifier_kind,
        "identifier": identifier,
        "server_url": os.environ.get("SIMEMU_SERVER_URL", "http://127.0.0.1:8765"),
    }
