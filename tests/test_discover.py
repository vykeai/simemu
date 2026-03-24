import json
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu.discover import (
    NoSimulatorAvailable,
    SimulatorInfo,
    find_best_device,
    find_simulator,
    get_android_serial,
    get_reservation,
    list_android,
    list_ios,
)


SIMCTL_JSON = json.dumps({
    "devices": {
        "com.apple.CoreSimulator.SimRuntime.iOS-26-2": [
            {
                "udid": "AAA-111",
                "name": "iPhone 16 Pro",
                "isAvailable": True,
                "state": "Booted",
            },
            {
                "udid": "BBB-222",
                "name": "iPhone 16",
                "isAvailable": True,
                "state": "Shutdown",
            },
            {
                "udid": "CCC-333",
                "name": "iPad Air",
                "isAvailable": False,
                "state": "Shutdown",
            },
        ],
        "com.apple.CoreSimulator.SimRuntime.watchOS-11-0": [
            {
                "udid": "DDD-444",
                "name": "Apple Watch",
                "isAvailable": True,
                "state": "Shutdown",
            },
        ],
    }
})


class TestListIos(unittest.TestCase):

    @patch("simemu.discover.subprocess.check_output")
    def test_parses_json_and_filters_unavailable(self, mock_co: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        result = list_ios()

        # Should include 2 available iOS devices, skip unavailable iPad and watchOS
        self.assertEqual(len(result), 2)
        udids = {s.sim_id for s in result}
        self.assertIn("AAA-111", udids)
        self.assertIn("BBB-222", udids)
        self.assertNotIn("CCC-333", udids)  # unavailable
        self.assertNotIn("DDD-444", udids)  # watchOS

    @patch("simemu.discover.subprocess.check_output")
    def test_sorts_booted_first(self, mock_co: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        result = list_ios()
        self.assertTrue(result[0].booted)
        self.assertEqual(result[0].sim_id, "AAA-111")

    @patch("simemu.discover.subprocess.check_output")
    def test_skips_allocated_ids(self, mock_co: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        result = list_ios(allocated_ids={"AAA-111"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sim_id, "BBB-222")

    @patch("simemu.discover.subprocess.check_output")
    def test_runtime_label_formatting(self, mock_co: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        result = list_ios()
        self.assertEqual(result[0].runtime, "iOS 26.2")

    @patch("simemu.discover.subprocess.check_output", side_effect=FileNotFoundError)
    def test_simctl_not_found_returns_empty(self, mock_co: MagicMock) -> None:
        result = list_ios()
        self.assertEqual(result, [])

    @patch("simemu.discover.subprocess.check_output",
           side_effect=subprocess.CalledProcessError(1, "simctl"))
    def test_simctl_error_returns_empty(self, mock_co: MagicMock) -> None:
        result = list_ios()
        self.assertEqual(result, [])


EMULATOR_LIST = b"Pixel_7_API_35\nNexus_5X\n"

ADB_DEVICES_OUTPUT = "List of devices attached\nemulator-5554\tdevice\n"


class TestListAndroid(unittest.TestCase):

    @patch("simemu.genymotion.is_available", return_value=False)
    @patch("simemu.discover._get_booted_avds", return_value={"Pixel_7_API_35"})
    @patch("simemu.discover.subprocess.check_output", return_value=EMULATOR_LIST)
    def test_parses_avd_list(self, mock_co: MagicMock, mock_booted: MagicMock, mock_geny: MagicMock) -> None:
        result = list_android()
        self.assertEqual(len(result), 2)
        names = {s.sim_id for s in result}
        self.assertIn("Pixel_7_API_35", names)
        self.assertIn("Nexus_5X", names)

    @patch("simemu.genymotion.is_available", return_value=False)
    @patch("simemu.discover._get_booted_avds", return_value={"Pixel_7_API_35"})
    @patch("simemu.discover.subprocess.check_output", return_value=EMULATOR_LIST)
    def test_booted_sorted_first(self, mock_co: MagicMock, mock_booted: MagicMock, mock_geny: MagicMock) -> None:
        result = list_android()
        self.assertTrue(result[0].booted)
        self.assertEqual(result[0].sim_id, "Pixel_7_API_35")

    @patch("simemu.genymotion.is_available", return_value=False)
    @patch("simemu.discover._get_booted_avds", return_value=set())
    @patch("simemu.discover.subprocess.check_output", return_value=EMULATOR_LIST)
    def test_extracts_api_from_name(self, mock_co: MagicMock, mock_booted: MagicMock, mock_geny: MagicMock) -> None:
        result = list_android()
        api_sim = next(s for s in result if s.sim_id == "Pixel_7_API_35")
        self.assertEqual(api_sim.runtime, "API 35")
        nexus = next(s for s in result if s.sim_id == "Nexus_5X")
        self.assertEqual(nexus.runtime, "Android")

    @patch("simemu.genymotion.is_available", return_value=False)
    @patch("simemu.discover._get_booted_avds", return_value=set())
    @patch("simemu.discover.subprocess.check_output", return_value=EMULATOR_LIST)
    def test_skips_allocated(self, mock_co: MagicMock, mock_booted: MagicMock, mock_geny: MagicMock) -> None:
        result = list_android(allocated_ids={"Pixel_7_API_35"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].sim_id, "Nexus_5X")

    @patch("simemu.discover._get_booted_avds", return_value=set())
    @patch("simemu.discover.subprocess.check_output", return_value=b"Nexus_5X\nPixel_8\n")
    def test_lists_multiple_avds(self, mock_co: MagicMock, mock_booted: MagicMock) -> None:
        result = list_android()
        self.assertEqual(len(result), 2)
        names = [s.sim_id for s in result]
        self.assertIn("Nexus_5X", names)
        self.assertIn("Pixel_8", names)


class TestGetAndroidSerial(unittest.TestCase):

    @patch("simemu.discover.subprocess.check_output")
    def test_maps_avd_to_serial(self, mock_co: MagicMock) -> None:
        mock_co.side_effect = [
            ADB_DEVICES_OUTPUT.encode(),   # adb devices
            b"Pixel_7_API_35\nOK\n",       # adb emu avd name
        ]
        serial = get_android_serial("Pixel_7_API_35")
        self.assertEqual(serial, "emulator-5554")

    @patch("simemu.discover.subprocess.check_output")
    def test_returns_none_when_no_match(self, mock_co: MagicMock) -> None:
        mock_co.side_effect = [
            ADB_DEVICES_OUTPUT.encode(),
            b"Other_AVD\nOK\n",
        ]
        serial = get_android_serial("Pixel_7_API_35")
        self.assertIsNone(serial)

    @patch("simemu.discover.subprocess.check_output", side_effect=FileNotFoundError)
    def test_returns_none_when_adb_missing(self, mock_co: MagicMock) -> None:
        serial = get_android_serial("Pixel_7_API_35")
        self.assertIsNone(serial)

class TestFindSimulator(unittest.TestCase):

    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch("simemu.discover.subprocess.check_output")
    def test_returns_first_available(self, mock_co: MagicMock, mock_claimed: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        sim = find_simulator("ios")
        # Booted device should come first
        self.assertEqual(sim.sim_id, "AAA-111")
        self.assertTrue(sim.booted)

    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch("simemu.discover.subprocess.check_output")
    def test_filters_by_device_name(self, mock_co: MagicMock, mock_claimed: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        sim = find_simulator("ios", device_name="iPhone 16")
        # Both match "iPhone 16" substring — but booted "iPhone 16 Pro" sorts first
        self.assertIn("iPhone 16", sim.device_name)

    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch("simemu.discover.subprocess.check_output", side_effect=FileNotFoundError)
    def test_raises_no_simulator_available(self, mock_co: MagicMock, mock_claimed: MagicMock) -> None:
        with self.assertRaises(NoSimulatorAvailable) as ctx:
            find_simulator("ios")
        self.assertIn("No available ios simulators", str(ctx.exception))

    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch("simemu.discover.subprocess.check_output")
    def test_raises_when_device_name_not_found(self, mock_co: MagicMock, mock_claimed: MagicMock) -> None:
        mock_co.return_value = SIMCTL_JSON.encode()
        with self.assertRaises(NoSimulatorAvailable) as ctx:
            find_simulator("ios", device_name="Pixel 9")
        self.assertIn("No available ios simulator", str(ctx.exception))
        self.assertIn("Pixel 9", str(ctx.exception))
        self.assertIn("Available:", str(ctx.exception))

    def test_raises_for_unknown_platform(self) -> None:
        with self.assertRaises(RuntimeError):
            find_simulator("windows")


class TestFindBestDevice(unittest.TestCase):
    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch(
        "simemu.discover.list_ios",
        return_value=[
            SimulatorInfo("ipad-1", "ios", "iPad Air", True, "iOS 26.2"),
            SimulatorInfo("iphone-1", "ios", "iPhone 16 Pro", False, "iOS 26.2"),
        ],
    )
    def test_prefers_requested_phone_form_factor(self, mock_list: MagicMock, mock_claimed: MagicMock) -> None:
        spec = SimpleNamespace(
            platform="ios",
            form_factor="phone",
            os_version=None,
            real_device=False,
        )
        result = find_best_device(spec)
        self.assertEqual(result.sim_id, "iphone-1")

    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch(
        "simemu.discover.list_ios",
        return_value=[SimulatorInfo("ipad-1", "ios", "iPad Air", True, "iOS 26.2")],
    )
    def test_raises_when_requested_phone_is_unavailable(self, mock_list: MagicMock, mock_claimed: MagicMock) -> None:
        spec = SimpleNamespace(
            platform="ios",
            form_factor="phone",
            os_version=None,
            real_device=False,
        )
        with self.assertRaises(NoSimulatorAvailable) as ctx:
            find_best_device(spec)
        self.assertIn("form factor 'phone'", str(ctx.exception))
        self.assertIn("iPad Air", str(ctx.exception))


class TestReservations(unittest.TestCase):
    """Tests for permanent device reservations."""

    def setUp(self) -> None:
        import tempfile, os
        self.tmpdir = tempfile.mkdtemp(prefix="simemu-res-test-")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir

    def tearDown(self) -> None:
        import os, shutil
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_config(self, config: dict) -> None:
        config_path = Path(self.tmpdir) / "config.json"
        config_path.write_text(json.dumps(config))

    def test_get_reservation_returns_match(self) -> None:
        self._write_config({
            "reservations": {
                "sitches": {"ios": {"device": "iPhone 17 Pro Max"}}
            }
        })
        result = get_reservation("sitches", "ios")
        self.assertIsNotNone(result)
        self.assertEqual(result["device"], "iPhone 17 Pro Max")

    def test_get_reservation_returns_none_for_unknown_agent(self) -> None:
        self._write_config({"reservations": {"sitches": {"ios": {"device": "iPhone 17"}}}})
        result = get_reservation("unknown", "ios")
        self.assertIsNone(result)

    def test_get_reservation_returns_none_for_wrong_platform(self) -> None:
        self._write_config({"reservations": {"sitches": {"ios": {"device": "iPhone 17"}}}})
        result = get_reservation("sitches", "android")
        self.assertIsNone(result)

    def test_get_reservation_returns_none_when_no_config(self) -> None:
        result = get_reservation("sitches", "ios")
        self.assertIsNone(result)

    @patch("simemu.discover._get_claimed_sim_ids", return_value=set())
    @patch("simemu.discover.list_ios")
    def test_find_best_device_prefers_reserved(self, mock_list_ios, mock_claimed) -> None:
        import os
        os.environ["SIMEMU_AGENT"] = "sitches"
        self._write_config({
            "reservations": {
                "sitches": {"ios": {"device": "iPhone 17 Pro Max"}}
            }
        })
        mock_list_ios.return_value = [
            SimulatorInfo("A", "ios", "iPhone 17", False, "iOS 26.1"),
            SimulatorInfo("B", "ios", "iPhone 17 Pro Max", False, "iOS 26.1"),
            SimulatorInfo("C", "ios", "iPhone 17 Pro", False, "iOS 26.1"),
        ]
        from simemu.session import ClaimSpec
        spec = ClaimSpec(platform="ios")
        result = find_best_device(spec)
        self.assertEqual(result.device_name, "iPhone 17 Pro Max")
        os.environ.pop("SIMEMU_AGENT", None)


if __name__ == "__main__":
    unittest.main()
