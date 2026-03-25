"""
Structured trace bundle export for transcript-backed debugging.

Collects session state, command history, provenance, watchdog health,
and recent logs into a single JSON bundle that can be attached to
bug reports or agent transcripts.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def export_trace(session_id: str | None = None) -> dict:
    """Export a comprehensive trace bundle for debugging.

    If session_id is provided, includes session-specific data.
    Always includes system-wide health and recent activity.
    """
    from . import state
    from .session import get_session, get_provenance, get_command_history, get_active_sessions
    from .watchdog import full_health_check

    bundle: dict = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "simemu_version": "0.3.0",
    }

    # System health
    try:
        bundle["health"] = full_health_check()
    except Exception as e:
        bundle["health"] = {"error": str(e)}

    # Active sessions summary
    try:
        active = get_active_sessions()
        bundle["active_sessions"] = {
            sid: {
                "platform": s.platform,
                "form_factor": s.form_factor,
                "status": s.status,
                "device_name": s.device_name,
                "agent": s.agent,
                "heartbeat_at": s.heartbeat_at,
            }
            for sid, s in active.items()
        }
    except Exception as e:
        bundle["active_sessions"] = {"error": str(e)}

    # Session-specific trace
    if session_id:
        try:
            session = get_session(session_id)
            if session:
                bundle["session"] = {
                    "session_id": session.session_id,
                    "platform": session.platform,
                    "form_factor": session.form_factor,
                    "status": session.status,
                    "device_name": session.device_name,
                    "sim_id": session.sim_id,
                    "agent": session.agent,
                    "created_at": session.created_at,
                    "heartbeat_at": session.heartbeat_at,
                    "expires_at": session.expires_at,
                    "label": session.label,
                    "resolved_os_version": session.resolved_os_version,
                }
            else:
                bundle["session"] = {"error": f"Session {session_id} not found"}
        except Exception as e:
            bundle["session"] = {"error": str(e)}

        # Command history
        try:
            bundle["command_history"] = get_command_history(session_id)
        except Exception as e:
            bundle["command_history"] = {"error": str(e)}

        # Provenance
        try:
            bundle["provenance"] = get_provenance(session_id)
        except Exception as e:
            bundle["provenance"] = {"error": str(e)}

    # Recent monitor log (last 20 lines)
    monitor_log = state.state_dir() / "monitor.log"
    if monitor_log.exists():
        try:
            lines = monitor_log.read_text().strip().splitlines()
            bundle["monitor_log_tail"] = lines[-20:]
        except OSError:
            bundle["monitor_log_tail"] = []
    else:
        bundle["monitor_log_tail"] = []

    # Recent monitor stderr (last 20 lines)
    stderr_log = state.state_dir() / "monitor-stderr.log"
    if stderr_log.exists():
        try:
            lines = stderr_log.read_text().strip().splitlines()
            bundle["monitor_stderr_tail"] = lines[-20:]
        except OSError:
            bundle["monitor_stderr_tail"] = []

    return bundle


def export_trace_to_file(session_id: str | None = None, output: str | None = None) -> str:
    """Export trace bundle to a file. Returns the file path."""
    bundle = export_trace(session_id)

    if not output:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(os.environ.get("SIMEMU_OUTPUT_DIR", Path.home() / ".simemu"))
        out_dir.mkdir(parents=True, exist_ok=True)
        prefix = f"{session_id}_" if session_id else ""
        output = str(out_dir / f"{prefix}trace_{ts}.json")

    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(bundle, indent=2))
    return output
