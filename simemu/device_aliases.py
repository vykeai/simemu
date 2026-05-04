"""Persistent labels for real devices, stored in ~/.simemu/config.json.

These labels are human-friendly, slug-like aliases for physical devices.
They support discovery, claim-by-alias, and UI display without relying on
volatile transport identifiers or whatever the device is currently named.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state


_CONFIG_KEY = "device_aliases"
_PLATFORM_NAMES = {
    "ios", "android", "watchos", "tvos", "visionos", "macos",
    "iphone", "ipad", "pixel", "watch", "tv", "appletv", "apple-tv", "vision", "mac",
}
_ALIAS_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?$")


def _config_path() -> Path:
    return state.config_dir() / "config.json"


def _read_config() -> dict[str, Any]:
    path = _config_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_config(config: dict[str, Any]) -> None:
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, indent=2, sort_keys=True))
    tmp.replace(path)


def normalize_alias(alias: str) -> str:
    """Normalize a human-entered label into a stable slug-like alias."""
    normalized = re.sub(r"[^a-z0-9_-]+", "-", alias.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    if not normalized:
        raise ValueError("Alias cannot be empty.")
    if normalized in _PLATFORM_NAMES:
        raise ValueError(
            f"Alias '{normalized}' is reserved. Use a more specific label like 'luke-iphone'."
        )
    if not _ALIAS_RE.fullmatch(normalized):
        raise ValueError(
            "Alias must be slug-like: lowercase letters, digits, hyphens, or underscores."
        )
    return normalized


def get_aliases() -> dict[str, dict[str, Any]]:
    """Return the configured alias registry keyed by alias."""
    config = _read_config()
    aliases = config.get(_CONFIG_KEY, {})
    return aliases if isinstance(aliases, dict) else {}


def resolve_device_alias(alias: str) -> dict[str, Any] | None:
    """Resolve a configured device alias to its stored record."""
    if not alias:
        return None
    return get_aliases().get(alias.strip().lower())


def find_alias_for_device(platform: str, device_id: str) -> str | None:
    """Return the alias assigned to a real device, if any."""
    for alias, record in get_aliases().items():
        if record.get("platform") == platform and record.get("device_id") == device_id:
            return alias
    return None


def set_device_alias(platform: str, device_id: str, device_name: str, alias: str) -> str:
    """Persist or update an alias for a real device.

    The alias is unique; reassigning the same device removes its previous alias.
    """
    normalized = normalize_alias(alias)
    config = _read_config()
    aliases = config.setdefault(_CONFIG_KEY, {})
    if not isinstance(aliases, dict):
        aliases = {}
        config[_CONFIG_KEY] = aliases

    # Remove any previous alias bound to this device.
    for existing_alias, record in list(aliases.items()):
        if record.get("platform") == platform and record.get("device_id") == device_id:
            if existing_alias != normalized:
                del aliases[existing_alias]

    aliases[normalized] = {
        "platform": platform,
        "device_id": device_id,
        "device_name": device_name,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_config(config)
    return normalized
