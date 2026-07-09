"""Fed config port discovery — reads ~/.fed/config.json, falls back to defaults."""
from __future__ import annotations

import json
from pathlib import Path


def _fed_config() -> dict:
    try:
        return json.loads((Path.home() / ".fed" / "config.json").read_text())
    except Exception:
        return {}


def fed_tool_url(slug: str, default_port: int) -> str:
    """Return http://127.0.0.1:<port> for slug, reading ~/.fed/config.json first."""
    port = _fed_config().get("tools", {}).get(slug, {}).get("dash")
    if isinstance(port, int) and 1 <= port <= 65535:
        return f"http://127.0.0.1:{port}"
    return f"http://127.0.0.1:{default_port}"


def fed_tool_port(slug: str, default_port: int) -> int:
    """Return the dash port for slug, reading ~/.fed/config.json first."""
    port = _fed_config().get("tools", {}).get(slug, {}).get("dash")
    if isinstance(port, int) and 1 <= port <= 65535:
        return port
    return default_port
