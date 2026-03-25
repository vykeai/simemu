"""Tests for simemu.schema — JSON contract validation."""

import unittest

from simemu.schema import (
    validate,
    SESSION_SCHEMA,
    ERROR_SCHEMA,
    SCREENSHOT_RESULT,
    PROOF_RESULT,
    LAUNCH_RESULT,
    URL_RESULT,
    BUILD_RESULT,
    STATUS_RESULT,
    HELP_RESULT,
    get_schema_for_command,
    all_schemas,
)


class TestValidate(unittest.TestCase):
    def test_valid_session(self) -> None:
        data = {
            "session": "s-abc123",
            "platform": "ios",
            "form_factor": "phone",
            "os_version": "26.1",
            "status": "active",
            "label": "",
            "created_at": "2026-01-01T00:00:00Z",
            "expires_at": "2026-01-01T01:00:00Z",
        }
        errors = validate(data, SESSION_SCHEMA)
        self.assertEqual(errors, [])

    def test_missing_required_key(self) -> None:
        data = {"session": "s-abc123", "platform": "ios"}
        errors = validate(data, SESSION_SCHEMA)
        self.assertTrue(any("form_factor" in e for e in errors))
        self.assertTrue(any("status" in e for e in errors))

    def test_wrong_type(self) -> None:
        data = {
            "session": 123,
            "platform": "ios",
            "form_factor": "phone",
            "status": "active",
        }
        errors = validate(data, SESSION_SCHEMA)
        self.assertTrue(any("session" in e and "string" in e for e in errors))

    def test_invalid_enum(self) -> None:
        data = {
            "session": "s-abc123",
            "platform": "windows",
            "form_factor": "phone",
            "status": "active",
        }
        errors = validate(data, SESSION_SCHEMA)
        self.assertTrue(any("platform" in e and "windows" in e for e in errors))

    def test_valid_error(self) -> None:
        data = {
            "error": "session_expired",
            "session": "s-abc123",
            "hint": "Re-claim with: simemu claim ios",
        }
        self.assertEqual(validate(data, ERROR_SCHEMA), [])

    def test_valid_screenshot(self) -> None:
        data = {"status": "captured", "path": "/tmp/proof.png"}
        self.assertEqual(validate(data, SCREENSHOT_RESULT), [])

    def test_valid_proof(self) -> None:
        data = {
            "status": "proved",
            "path": "/tmp/proof.png",
            "steps": ["dismiss_alerts", "status_bar:9:41"],
            "warnings": [],
            "metadata": {},
        }
        self.assertEqual(validate(data, PROOF_RESULT), [])

    def test_valid_launch(self) -> None:
        data = {"status": "launched", "app": "com.example.app"}
        self.assertEqual(validate(data, LAUNCH_RESULT), [])

    def test_valid_url(self) -> None:
        data = {"status": "opened", "url": "myapp://screen"}
        self.assertEqual(validate(data, URL_RESULT), [])

    def test_valid_build(self) -> None:
        data = {"status": "built", "platform": "ios", "variant": "mock"}
        self.assertEqual(validate(data, BUILD_RESULT), [])

    def test_valid_status_result(self) -> None:
        data = {"status": "tapped", "x": 100, "y": 200}
        self.assertEqual(validate(data, STATUS_RESULT), [])

    def test_null_allowed_for_expires_at(self) -> None:
        data = {
            "session": "s-abc123",
            "platform": "ios",
            "form_factor": "phone",
            "status": "active",
            "expires_at": None,
        }
        errors = validate(data, SESSION_SCHEMA)
        self.assertFalse(any("expires_at" in e for e in errors))

    def test_non_dict_input(self) -> None:
        errors = validate("not a dict", SESSION_SCHEMA)
        self.assertTrue(len(errors) > 0)


class TestRegistry(unittest.TestCase):
    def test_get_schema_for_known_command(self) -> None:
        self.assertIsNotNone(get_schema_for_command("claim"))
        self.assertIsNotNone(get_schema_for_command("screenshot"))
        self.assertIsNotNone(get_schema_for_command("proof"))

    def test_get_schema_for_unknown_command(self) -> None:
        self.assertIsNone(get_schema_for_command("nonexistent"))

    def test_all_schemas_nonempty(self) -> None:
        schemas = all_schemas()
        self.assertGreater(len(schemas), 10)


if __name__ == "__main__":
    unittest.main()
