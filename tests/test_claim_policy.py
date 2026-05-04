"""Tests for simemu.claim_policy — aliases, defaults, per-product preferences."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from simemu.claim_policy import resolve_alias, apply_defaults, get_all_aliases


_tmpdir = tempfile.mkdtemp(prefix="simemu-policy-test-")


class TestResolveAlias(unittest.TestCase):
    def setUp(self) -> None:
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_STATE_DIR"] = _tmpdir
        os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

    def tearDown(self) -> None:
        if self._old_state is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self._old_state

        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config

    def test_builtin_iphone(self) -> None:
        result = resolve_alias("iphone")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["form_factor"], "phone")

    def test_builtin_ipad(self) -> None:
        result = resolve_alias("ipad")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["form_factor"], "tablet")

    def test_builtin_pixel(self) -> None:
        result = resolve_alias("pixel")
        self.assertEqual(result["platform"], "android")
        self.assertEqual(result["form_factor"], "phone")

    def test_builtin_watch(self) -> None:
        result = resolve_alias("watch")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["form_factor"], "watch")

    def test_builtin_mac(self) -> None:
        result = resolve_alias("mac")
        self.assertEqual(result["platform"], "macos")

    def test_raw_platform_passes_through(self) -> None:
        result = resolve_alias("ios")
        self.assertEqual(result["platform"], "ios")
        self.assertNotIn("form_factor", result)

    def test_raw_android_passes_through(self) -> None:
        result = resolve_alias("android")
        self.assertEqual(result["platform"], "android")

    def test_unknown_passes_through(self) -> None:
        result = resolve_alias("foobar")
        self.assertEqual(result["platform"], "foobar")

    def test_device_alias_resolves_to_real_device_claim(self) -> None:
        Path(_tmpdir).mkdir(parents=True, exist_ok=True)
        (Path(_tmpdir) / "config.json").write_text(json.dumps({
            "device_aliases": {
                "luke-iphone": {
                    "platform": "ios",
                    "device_id": "00008150-001622E63638401C",
                    "device_name": "Luke iPhone 17 Pro Max",
                }
            }
        }))
        result = resolve_alias("luke-iphone")
        self.assertEqual(result["platform"], "ios")
        self.assertTrue(result["real_device"])
        self.assertEqual(result["device"], "00008150-001622E63638401C")


class TestApplyDefaults(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-policy-")
        self._old = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old
        self.tmpdir.cleanup()

    def _write_config(self, config: dict) -> None:
        Path(self.tmpdir.name).mkdir(parents=True, exist_ok=True)
        (Path(self.tmpdir.name) / "config.json").write_text(json.dumps(config))

    def test_applies_version_default(self) -> None:
        self._write_config({
            "claim_policy": {"defaults": {"ios": {"version": "26"}}}
        })
        spec = apply_defaults("ios", {"version": None, "form_factor": "phone"})
        self.assertEqual(spec["version"], "26")

    def test_does_not_override_explicit_version(self) -> None:
        self._write_config({
            "claim_policy": {"defaults": {"ios": {"version": "26"}}}
        })
        spec = apply_defaults("ios", {"version": "18", "form_factor": "phone"})
        self.assertEqual(spec["version"], "18")

    def test_no_defaults_is_noop(self) -> None:
        spec = apply_defaults("ios", {"version": None, "form_factor": "phone"})
        self.assertIsNone(spec["version"])

    def test_user_alias_overrides_builtin(self) -> None:
        self._write_config({
            "claim_policy": {
                "aliases": {"iphone": {"platform": "ios", "form_factor": "phone", "version": "18"}}
            }
        })
        result = resolve_alias("iphone")
        self.assertEqual(result["version"], "18")


class TestGetAllAliases(unittest.TestCase):
    def test_includes_builtins(self) -> None:
        aliases = get_all_aliases()
        self.assertIn("iphone", aliases)
        self.assertIn("ipad", aliases)
        self.assertIn("pixel", aliases)
        self.assertIn("watch", aliases)
        self.assertIn("mac", aliases)


if __name__ == "__main__":
    unittest.main()
