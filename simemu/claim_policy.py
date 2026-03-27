"""
Claim policy — aliases, defaults, and per-product device preferences.

Loaded from ~/.simemu/config.json under "claim_policy":
{
  "claim_policy": {
    "aliases": {
      "iphone": {"platform": "ios", "form_factor": "phone"},
      "ipad": {"platform": "ios", "form_factor": "tablet"},
      "pixel": {"platform": "android", "form_factor": "phone"},
      "watch": {"platform": "ios", "form_factor": "watch"}
    },
    "defaults": {
      "ios": {"version": "26", "form_factor": "phone"},
      "android": {"version": "15", "form_factor": "phone"}
    }
  }
}

Usage: `simemu claim iphone` resolves to `simemu claim ios --form-factor phone --version 26`
"""

import json
from pathlib import Path
from typing import Any


# Built-in aliases (always available, config can override)
_BUILTIN_ALIASES: dict[str, dict[str, str]] = {
    "iphone": {"platform": "ios", "form_factor": "phone"},
    "ipad": {"platform": "ios", "form_factor": "tablet"},
    "pixel": {"platform": "android", "form_factor": "phone"},
    "watch": {"platform": "ios", "form_factor": "watch"},
    "tv": {"platform": "ios", "form_factor": "tv"},
    "appletv": {"platform": "ios", "form_factor": "tv"},
    "apple-tv": {"platform": "ios", "form_factor": "tv"},
    "vision": {"platform": "ios", "form_factor": "vision"},
    "mac": {"platform": "macos", "form_factor": "desktop"},
}


def _load_policy() -> dict[str, Any]:
    from . import state
    config_path = state.config_dir() / "config.json"
    if not config_path.exists():
        return {}
    try:
        config = json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return config.get("claim_policy", {})


def resolve_alias(platform_or_alias: str) -> dict[str, str]:
    """Resolve a platform name or alias to a claim spec dict.

    Returns dict with keys: platform, form_factor, version (all optional except platform).
    If the input is a known alias, expands it. Otherwise passes through as platform.
    """
    policy = _load_policy()
    user_aliases = policy.get("aliases", {})

    # Check user aliases first, then builtins
    alias_def = user_aliases.get(platform_or_alias) or _BUILTIN_ALIASES.get(platform_or_alias)
    if alias_def:
        return dict(alias_def)

    # Not an alias — treat as raw platform
    return {"platform": platform_or_alias}


def apply_defaults(platform: str, spec: dict[str, str | None]) -> dict[str, str | None]:
    """Apply per-platform defaults from policy config.

    Fills in missing version, form_factor from the policy defaults.
    Does NOT override values already set by the user.
    """
    policy = _load_policy()
    defaults = policy.get("defaults", {}).get(platform, {})

    if not spec.get("version") and defaults.get("version"):
        spec["version"] = defaults["version"]
    if not spec.get("form_factor") and defaults.get("form_factor"):
        spec["form_factor"] = defaults["form_factor"]

    return spec


def get_all_aliases() -> dict[str, dict[str, str]]:
    """Return all available aliases (builtins + user config)."""
    policy = _load_policy()
    result = dict(_BUILTIN_ALIASES)
    result.update(policy.get("aliases", {}))
    return result
