"""
Scouty desktop lease integration for shared-desktop focus coordination.

When multiple tools (simemu, scouty, Claude agents) share a Mac desktop,
this module coordinates focus acquisition through scouty's lease API.
Tools request a lease before taking focus, scouty shows a countdown overlay,
and the lease is released when the operation completes.

Degrades gracefully: if scouty is not running, all operations are no-ops.
"""

import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any


_ACTION_EMOJI = {
    "tap": "\U0001f446",       # 👆
    "swipe": "\u2194\ufe0f",   # ↔️
    "key": "\u2328\ufe0f",     # ⌨️
    "input": "\U0001f4dd",     # 📝
    "long-press": "\U0001f447",# 👇
    "focus": "\U0001f50d",     # 🔍
    "screenshot": "\U0001f4f7",# 📷
    "install": "\U0001f4e6",   # 📦
    "launch": "\U0001f680",    # 🚀
    "maestro": "\U0001f3ac",   # 🎬
}


def _base_url() -> str:
    return (os.environ.get("SCOUTY_BASE_URL") or "http://127.0.0.1:7331").rstrip("/")


def _json_request(method: str, path: str, payload: dict | None = None, timeout: float = 2.0) -> dict:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        f"{_base_url()}{path}",
        data=body,
        method=method,
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw else {}


def is_available() -> bool:
    """Check if scouty is reachable."""
    try:
        _json_request("GET", "/health", timeout=1.0)
        return True
    except Exception:
        return False


class DesktopLease:
    """Context manager for scouty desktop lease coordination.

    Works with both v1 (slug-based) and v2 (session-based) simemu.
    Degrades gracefully if scouty is not running.
    """

    def __init__(
        self,
        action: str,
        device_name: str,
        platform: str,
        *,
        session_id: str = "",
        project: str = "",
        reason: str = "",
        estimated_seconds: int = 5,
        real_device: bool = False,
        **extra_metadata: Any,
    ):
        self.action = action
        self.device_name = device_name
        self.platform = platform
        self.session_id = session_id
        self.project = project
        self.reason = reason or f"{action} on {device_name}"
        self.estimated_seconds = estimated_seconds
        self.real_device = real_device
        self.extra_metadata = extra_metadata
        self.lease_id: str | None = None
        self.enabled = False
        try:
            self.countdown_seconds = int(os.environ.get("SIMEMU_DESKTOP_LEASE_COUNTDOWN", "3"))
        except ValueError:
            self.countdown_seconds = 3

    def __enter__(self):
        try:
            payload = {
                "tool": "simemu",
                "project": self.project,
                "session": self.session_id,
                "platform": self.platform,
                "action": self.action,
                "action_emoji": _ACTION_EMOJI.get(self.action, "\U0001f5a5\ufe0f"),
                "reason": self.reason,
                "estimated_seconds": self.estimated_seconds,
                "countdown_seconds": self.countdown_seconds,
                "stage": f"Preparing {self.action}",
                "screen": self.device_name,
                "device_type": "real" if self.real_device else "simulator",
                **self.extra_metadata,
            }
            lease = _json_request("POST", "/desktop/lease/request", payload)
            self.lease_id = lease.get("lease_id")
            if self.lease_id:
                self.enabled = True
                remaining = lease.get("countdown_remaining_seconds")
                delay = self.countdown_seconds if remaining is None else max(0.0, float(remaining))
                if delay > 0:
                    time.sleep(delay)
                _json_request("POST", "/desktop/lease/activate", {"lease_id": self.lease_id})
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ValueError, OSError, ConnectionRefusedError):
            self.enabled = False
            self.lease_id = None
        return self

    def update(self, **metadata: Any) -> None:
        if not self.lease_id:
            return
        try:
            _json_request("POST", "/desktop/lease/update", {
                "lease_id": self.lease_id,
                "metadata": metadata,
            })
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ValueError, OSError):
            pass

    def __exit__(self, exc_type, exc, tb):
        if self.lease_id:
            try:
                _json_request("POST", "/desktop/lease/release", {"lease_id": self.lease_id})
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    ValueError, OSError):
                pass
        return False


@contextmanager
def desktop_lease(
    action: str,
    device_name: str,
    platform: str,
    **kwargs,
):
    """Convenience context manager for desktop lease coordination."""
    with DesktopLease(action, device_name, platform, **kwargs) as lease:
        yield lease
