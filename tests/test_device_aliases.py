import json
import os
import tempfile
import unittest
from pathlib import Path

from simemu.device_aliases import (
    find_alias_for_device,
    normalize_alias,
    resolve_device_alias,
    set_device_alias,
)


class DeviceAliasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-device-alias-")
        self.old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self.old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self.old_config
        self.tmpdir.cleanup()

    def test_normalize_alias_slugifies(self) -> None:
        self.assertEqual("luke-iphone", normalize_alias(" Luke iPhone "))

    def test_reserved_platform_alias_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_alias("iphone")

    def test_set_and_resolve_alias(self) -> None:
        alias = set_device_alias(
            platform="ios",
            device_id="00008150-001622E63638401C",
            device_name="Luke iPhone 17 Pro Max",
            alias="Luke iPhone",
        )

        self.assertEqual("luke-iphone", alias)
        record = resolve_device_alias("luke-iphone")
        assert record is not None
        self.assertEqual("ios", record["platform"])
        self.assertEqual("00008150-001622E63638401C", record["device_id"])
        self.assertEqual("Luke iPhone 17 Pro Max", record["device_name"])

    def test_reassign_replaces_old_alias_for_same_device(self) -> None:
        set_device_alias("ios", "DEVICE-1", "Luke iPhone 17 Pro Max", "luke-iphone")
        set_device_alias("ios", "DEVICE-1", "Luke iPhone 17 Pro Max", "sleep-phone")

        self.assertIsNone(resolve_device_alias("luke-iphone"))
        self.assertEqual("sleep-phone", find_alias_for_device("ios", "DEVICE-1"))

    def test_persists_to_config_json(self) -> None:
        set_device_alias("android", "R5CR1234567", "Pixel 10 Pro", "pixel-proof")
        config = json.loads((Path(self.tmpdir.name) / "config.json").read_text())
        self.assertIn("device_aliases", config)
        self.assertIn("pixel-proof", config["device_aliases"])


if __name__ == "__main__":
    unittest.main()
