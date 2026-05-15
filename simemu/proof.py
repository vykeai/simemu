"""Machine-readable mobile proof artifact metadata."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lease import lease_from_session
from .session import Session


@dataclass
class ArtifactFile:
    path: str
    exists: bool
    sha256: str | None
    size_bytes: int | None


def artifact_file(path: str | None) -> ArtifactFile | None:
    if not path:
        return None
    file_path = Path(path)
    if not file_path.exists():
        return ArtifactFile(path=path, exists=False, sha256=None, size_bytes=None)
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return ArtifactFile(
        path=path,
        exists=True,
        sha256=digest,
        size_bytes=file_path.stat().st_size,
    )


def mobile_proof_artifact(
    *,
    kind: str,
    session: Session,
    output_path: str | None = None,
    status: str,
    build_path: str | None = None,
    app: str | None = None,
    flow_files: list[str] | None = None,
    debug_output: str | None = None,
    failure_class: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict:
    """Build the stable artifact shape consumed by Atlas, Sentinel, or Proofy."""
    screenshot = artifact_file(output_path)
    build = artifact_file(build_path)
    lease = lease_from_session(session).to_json()

    artifact = {
        "schema_version": "simemu.mobile-proof.v1",
        "producer": "simemu",
        "kind": kind,
        "status": status,
        "failure_class": failure_class,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "lease": {
            "lease_id": lease["lease_id"],
            "session": lease["session"],
            "host": lease["host"],
            "run_id": lease["run_id"],
            "expires_at": lease["expires_at"],
        },
        "device": lease["device"],
        "boot": lease["boot"],
        "connection": lease["connection"],
        "app": app,
        "build": {
            "bound": build is not None and build.exists,
            "artifact": asdict(build) if build else None,
        },
        "screenshot": asdict(screenshot) if screenshot else None,
        "flow": {
            "files": flow_files or [],
            "debug_output": debug_output,
        } if flow_files or debug_output else None,
        "consumers": ["atlas", "sentinel", "proofy"],
        "metadata": metadata or {},
    }
    return artifact


def proof_failure_class(stage: str, exc: BaseException | str | None = None) -> str:
    text = str(exc or "").lower()
    if stage == "boot" or "boot" in text or "adb-ready" in text:
        return "device-boot-failed"
    if stage == "install" or "install" in text:
        return "app-install-failed"
    if stage == "launch" or "foreground" in text or "handoff" in text:
        return "app-launch-failed"
    if stage == "capture" or "screenshot" in text:
        return "capture-failed"
    if stage == "flow" or "maestro" in text:
        return "mobile-flow-failed"
    return "mobile-proof-failed"
