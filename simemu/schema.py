"""
JSON schema contract for simemu sessions, commands, and server responses.

All agent-facing JSON follows these schemas. Use validate() to check
conformance in tests. Schemas are also exposed as documentation via
`simemu do <session> help --schema`.
"""

from typing import Any

# ── Session (returned by claim, do boot, sessions list) ──────────────────────

SESSION_SCHEMA = {
    "type": "object",
    "required": ["session", "platform", "form_factor", "status"],
    "properties": {
        "session":     {"type": "string", "pattern": "^s-[0-9a-f]{6}$"},
        "platform":    {"type": "string", "enum": ["ios", "android", "macos"]},
        "form_factor": {"type": "string", "enum": ["phone", "tablet", "watch", "tv", "vision", "desktop"]},
        "os_version":  {"type": "string"},
        "status":      {"type": "string", "enum": ["active", "idle", "parked", "expired", "released"]},
        "label":       {"type": "string"},
        "created_at":  {"type": "string"},
        "expires_at":  {"type": ["string", "null"]},
    },
}

# ── Error (returned on failure) ──────────────────────────────────────────────

ERROR_SCHEMA = {
    "type": "object",
    "required": ["error", "hint"],
    "properties": {
        "error":   {"type": "string"},
        "session": {"type": "string"},
        "hint":    {"type": "string"},
    },
}

# ── Command results ──────────────────────────────────────────────────────────

STATUS_RESULT = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string"},
    },
}

SCREENSHOT_RESULT = {
    "type": "object",
    "required": ["status", "path"],
    "properties": {
        "status": {"type": "string", "enum": ["captured"]},
        "path":   {"type": "string"},
    },
}

PROOF_RESULT = {
    "type": "object",
    "required": ["status", "path", "steps"],
    "properties": {
        "status":   {"type": "string", "enum": ["proved"]},
        "path":     {"type": "string"},
        "steps":    {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "metadata": {"type": "object"},
    },
}

LAUNCH_RESULT = {
    "type": "object",
    "required": ["status", "app"],
    "properties": {
        "status": {"type": "string", "enum": ["launched"]},
        "app":    {"type": "string"},
    },
}

INSTALL_RESULT = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status": {"type": "string", "enum": ["installed"]},
        "app":    {"type": "string"},
    },
}

URL_RESULT = {
    "type": "object",
    "required": ["status", "url"],
    "properties": {
        "status": {"type": "string", "enum": ["opened"]},
        "url":    {"type": "string"},
    },
}

BUILD_RESULT = {
    "type": "object",
    "required": ["status"],
    "properties": {
        "status":  {"type": "string", "enum": ["built"]},
        "platform": {"type": "string"},
        "variant": {"type": "string"},
    },
}

HELP_RESULT = {
    "type": "object",
    "required": ["commands"],
    "properties": {
        "commands": {"type": "object"},
    },
}

# ── Registry ─────────────────────────────────────────────────────────────────

COMMAND_SCHEMAS: dict[str, dict] = {
    "claim":      SESSION_SCHEMA,
    "boot":       SESSION_SCHEMA,
    "renew":      SESSION_SCHEMA,
    "done":       STATUS_RESULT,
    "screenshot": SCREENSHOT_RESULT,
    "proof":      PROOF_RESULT,
    "launch":     LAUNCH_RESULT,
    "install":    INSTALL_RESULT,
    "url":        URL_RESULT,
    "build":      BUILD_RESULT,
    "help":       HELP_RESULT,
    "tap":        STATUS_RESULT,
    "swipe":      STATUS_RESULT,
    "key":        STATUS_RESULT,
    "input":      STATUS_RESULT,
    "terminate":  STATUS_RESULT,
    "uninstall":  STATUS_RESULT,
    "appearance": STATUS_RESULT,
    "rotate":     STATUS_RESULT,
}


def validate(data: dict, schema: dict) -> list[str]:
    """Validate a dict against a schema. Returns list of error strings (empty = valid).

    Lightweight validator — no jsonschema dependency needed.
    Checks: required keys, type of values, enum membership.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"Expected object, got {type(data).__name__}"]

    # Check required keys
    for key in schema.get("required", []):
        if key not in data:
            errors.append(f"Missing required key: '{key}'")

    # Check property types
    props = schema.get("properties", {})
    for key, prop_schema in props.items():
        if key not in data:
            continue
        value = data[key]
        expected_type = prop_schema.get("type")

        # Handle union types (e.g. ["string", "null"])
        if isinstance(expected_type, list):
            type_names = expected_type
        elif expected_type:
            type_names = [expected_type]
        else:
            continue

        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
            "null": type(None),
        }
        allowed_types = tuple(type_map.get(t, object) for t in type_names)
        if not isinstance(value, allowed_types):
            errors.append(f"Key '{key}': expected {'/'.join(type_names)}, got {type(value).__name__}")

        # Check enum
        if "enum" in prop_schema and value is not None:
            if value not in prop_schema["enum"]:
                errors.append(f"Key '{key}': value '{value}' not in {prop_schema['enum']}")

    return errors


def get_schema_for_command(command: str) -> dict | None:
    """Return the response schema for a command, or None if unknown."""
    return COMMAND_SCHEMAS.get(command)


def all_schemas() -> dict[str, dict]:
    """Return all command schemas as a dict."""
    return dict(COMMAND_SCHEMAS)
