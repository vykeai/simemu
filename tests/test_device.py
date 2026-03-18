import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import device
from simemu.discover import (
    SimulatorInfo, list_real_ios, list_real_android, find_simulator,
    NoSimulatorAvailable,
)


class ListIOSDevicesTests(unittest.TestCase):
    def test_returns_connected_devices_from_devicectl(self) -> None:
        devicectl_output = json.dumps({
            "result": {
                "devices": [
                    {
                        "identifier": "00008030-001A2B3C4D5E6F78",
                        "connectionProperties": {"transportType": "usb"},
                        "hardwareProperties": {
                            "marketingName": "iPhone 15 Pro",
                            "platform": "com.apple.platform.iphoneos",
                        },
                        "deviceProperties": {
                            "name": "Luke's iPhone",
                            "osVersionNumber": "18.2",
                        },
                        "simulator": False,
                    }
                ]
            }
        })

        result = Mock()
        result.returncode = 0
        result.stdout = devicectl_output

        with patch("simemu.device.subprocess.run", return_value=result):
            with patch("simemu.device._has_devicectl", return_value=True):
                devices = device.list_ios_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual("00008030-001A2B3C4D5E6F78", devices[0].device_id)
        self.assertEqual("ios", devices[0].platform)
        self.assertEqual("Luke's iPhone", devices[0].device_name)
        self.assertEqual("usb", devices[0].connection)
        self.assertEqual("18.2", devices[0].os_version)

    def test_skips_simulators_in_devicectl_output(self) -> None:
        devicectl_output = json.dumps({
            "result": {
                "devices": [
                    {
                        "identifier": "SIM-UUID-001",
                        "connectionProperties": {},
                        "hardwareProperties": {"platform": "com.apple.platform.appletvsimulator"},
                        "deviceProperties": {"name": "Apple TV"},
                        "simulator": True,
                    }
                ]
            }
        })

        result = Mock()
        result.returncode = 0
        result.stdout = devicectl_output

        with patch("simemu.device.subprocess.run", return_value=result):
            with patch("simemu.device._has_devicectl", return_value=True):
                devices = device.list_ios_devices()

        self.assertEqual(0, len(devices))

    def test_returns_empty_when_devicectl_not_available(self) -> None:
        with patch("simemu.device._has_devicectl", return_value=False):
            devices = device.list_ios_devices()
        self.assertEqual([], devices)

    def test_returns_empty_when_devicectl_fails(self) -> None:
        result = Mock()
        result.returncode = 1
        result.stdout = ""

        with patch("simemu.device.subprocess.run", return_value=result):
            with patch("simemu.device._has_devicectl", return_value=True):
                devices = device.list_ios_devices()

        self.assertEqual([], devices)

    def test_detects_wifi_connection(self) -> None:
        devicectl_output = json.dumps({
            "result": {
                "devices": [
                    {
                        "identifier": "WIFI-UDID-001",
                        "connectionProperties": {"transportType": "wifi"},
                        "hardwareProperties": {"marketingName": "iPhone 16"},
                        "deviceProperties": {"name": "Test iPhone", "osVersionNumber": "18.0"},
                    }
                ]
            }
        })

        result = Mock()
        result.returncode = 0
        result.stdout = devicectl_output

        with patch("simemu.device.subprocess.run", return_value=result):
            with patch("simemu.device._has_devicectl", return_value=True):
                devices = device.list_ios_devices()

        self.assertEqual("wifi", devices[0].connection)


class ListAndroidDevicesTests(unittest.TestCase):
    def test_returns_real_devices_excludes_emulators(self) -> None:
        adb_output = (
            "List of devices attached\n"
            "R5CR1234567     device usb:1-1 product:dm3q model:SM_S911B transport_id:1\n"
            "emulator-5554   device product:sdk_gphone64_x86_64 model:sdk transport_id:2\n"
        ).encode()

        getprop_result = Mock()
        getprop_result.stdout = "15\n"

        with patch("simemu.device.subprocess.check_output", return_value=adb_output):
            with patch("simemu.device.subprocess.run", return_value=getprop_result):
                devices = device.list_android_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual("R5CR1234567", devices[0].device_id)
        self.assertEqual("android", devices[0].platform)
        self.assertEqual("SM S911B", devices[0].device_name)
        self.assertEqual("usb", devices[0].connection)

    def test_returns_empty_when_adb_not_installed(self) -> None:
        with patch("simemu.device.subprocess.check_output", side_effect=FileNotFoundError):
            devices = device.list_android_devices()
        self.assertEqual([], devices)

    def test_wifi_connected_device_detected(self) -> None:
        adb_output = (
            "List of devices attached\n"
            "192.168.1.100:5555   device model:Pixel_8 transport_id:3\n"
        ).encode()

        getprop_result = Mock()
        getprop_result.stdout = "14\n"

        with patch("simemu.device.subprocess.check_output", return_value=adb_output):
            with patch("simemu.device.subprocess.run", return_value=getprop_result):
                devices = device.list_android_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual("wifi", devices[0].connection)


class DiscoverRealDeviceIntegrationTests(unittest.TestCase):
    def test_list_real_ios_wraps_device_module(self) -> None:
        fake_device = device.RealDevice(
            device_id="UDID-001",
            platform="ios",
            device_name="iPhone 15",
            connected=True,
            os_version="18.2",
            connection="usb",
        )

        with patch("simemu.device.list_ios_devices", return_value=[fake_device]):
            results = list_real_ios()

        self.assertEqual(1, len(results))
        self.assertEqual("UDID-001", results[0].sim_id)
        self.assertEqual("iPhone 15 (real)", results[0].device_name)
        self.assertTrue(results[0].real_device)
        self.assertEqual("iOS 18.2", results[0].runtime)

    def test_list_real_ios_excludes_allocated(self) -> None:
        fake_device = device.RealDevice(
            device_id="UDID-001",
            platform="ios",
            device_name="iPhone 15",
            connected=True,
            os_version="18.2",
            connection="usb",
        )

        with patch("simemu.device.list_ios_devices", return_value=[fake_device]):
            results = list_real_ios(allocated_ids={"UDID-001"})

        self.assertEqual(0, len(results))

    def test_list_real_android_wraps_device_module(self) -> None:
        fake_device = device.RealDevice(
            device_id="R5CR123",
            platform="android",
            device_name="Galaxy S24",
            connected=True,
            os_version="15",
            connection="usb",
        )

        with patch("simemu.device.list_android_devices", return_value=[fake_device]):
            results = list_real_android()

        self.assertEqual(1, len(results))
        self.assertEqual("R5CR123", results[0].sim_id)
        self.assertTrue(results[0].real_device)

    def test_find_simulator_with_real_device_flag(self) -> None:
        fake_device = device.RealDevice(
            device_id="UDID-001",
            platform="ios",
            device_name="iPhone 15",
            connected=True,
            os_version="18.2",
            connection="usb",
        )

        with patch("simemu.device.list_ios_devices", return_value=[fake_device]):
            with patch("simemu.discover.state.get_all", return_value={}):
                sim = find_simulator("ios", real_device=True)

        self.assertEqual("UDID-001", sim.sim_id)
        self.assertTrue(sim.real_device)

    def test_find_simulator_real_device_raises_when_none_connected(self) -> None:
        with patch("simemu.device.list_ios_devices", return_value=[]):
            with patch("simemu.discover.state.get_all", return_value={}):
                with self.assertRaises(NoSimulatorAvailable) as ctx:
                    find_simulator("ios", real_device=True)

        self.assertIn("real devices", str(ctx.exception))
        self.assertIn("USB connection", str(ctx.exception))

    def test_find_simulator_real_device_filters_by_name(self) -> None:
        dev1 = device.RealDevice("UDID-1", "ios", "iPhone 15", True, "18", "usb")
        dev2 = device.RealDevice("UDID-2", "ios", "iPad Pro", True, "18", "usb")

        with patch("simemu.device.list_ios_devices", return_value=[dev1, dev2]):
            with patch("simemu.discover.state.get_all", return_value={}):
                sim = find_simulator("ios", device_name="iPad", real_device=True)

        self.assertEqual("UDID-2", sim.sim_id)


class IOSInstallTests(unittest.TestCase):
    def test_raises_for_missing_file(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "App not found"):
            device.ios_install("UDID", "/nonexistent/app.ipa")

    def test_raises_for_wrong_extension(self) -> None:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".apk") as f:
            with self.assertRaisesRegex(RuntimeError, "Real iOS devices require .ipa"):
                device.ios_install("UDID", f.name)


class IsRealDeviceSerialTests(unittest.TestCase):
    def test_emulator_serial_returns_false(self) -> None:
        self.assertFalse(device.is_real_device_serial("emulator-5554"))

    def test_usb_serial_returns_true(self) -> None:
        self.assertTrue(device.is_real_device_serial("R5CR1234567"))

    def test_wifi_serial_returns_true(self) -> None:
        self.assertTrue(device.is_real_device_serial("192.168.1.100:5555"))


if __name__ == "__main__":
    unittest.main()
