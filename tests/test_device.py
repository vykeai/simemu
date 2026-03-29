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
        devicectl_devices = [
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

        with patch("simemu.device._list_xcdevice_devices_json", return_value=None):
            with patch("simemu.device._list_devicectl_devices_json", return_value=devicectl_devices):
                devices = device.list_ios_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual("00008030-001A2B3C4D5E6F78", devices[0].device_id)
        self.assertEqual("ios", devices[0].platform)
        self.assertEqual("Luke's iPhone", devices[0].device_name)
        self.assertEqual("usb", devices[0].connection)
        self.assertEqual("18.2", devices[0].os_version)

    def test_skips_simulators_in_devicectl_output(self) -> None:
        devicectl_devices = [
            {
                "identifier": "SIM-UUID-001",
                "connectionProperties": {},
                "hardwareProperties": {"platform": "com.apple.platform.appletvsimulator"},
                "deviceProperties": {"name": "Apple TV"},
                "simulator": True,
            }
        ]

        with patch("simemu.device._list_xcdevice_devices_json", return_value=None):
            with patch("simemu.device._list_devicectl_devices_json", return_value=devicectl_devices):
                devices = device.list_ios_devices()

        self.assertEqual(0, len(devices))

    def test_returns_empty_when_devicectl_not_available(self) -> None:
        with patch("simemu.device._list_devicectl_devices_json", return_value=None):
            with patch("simemu.device._list_xcdevice_devices_json", return_value=None):
                devices = device.list_ios_devices()
        self.assertEqual([], devices)

    def test_returns_empty_when_devicectl_fails(self) -> None:
        with patch("simemu.device._list_devicectl_devices_json", return_value=None):
            with patch("simemu.device._list_xcdevice_devices_json", return_value=None):
                devices = device.list_ios_devices()
        self.assertEqual([], devices)

    def test_detects_wifi_connection(self) -> None:
        devicectl_devices = [
            {
                "identifier": "WIFI-UDID-001",
                "connectionProperties": {"transportType": "wifi"},
                "hardwareProperties": {"marketingName": "iPhone 16", "platform": "com.apple.platform.iphoneos"},
                "deviceProperties": {"name": "Test iPhone", "osVersionNumber": "18.0"},
            }
        ]

        with patch("simemu.device._list_xcdevice_devices_json", return_value=None):
            with patch("simemu.device._list_devicectl_devices_json", return_value=devicectl_devices):
                devices = device.list_ios_devices()

        self.assertEqual("wifi", devices[0].connection)

    def test_skips_watch_devices_in_devicectl_output(self) -> None:
        devicectl_devices = [
            {
                "identifier": "WATCH-UDID-001",
                "connectionProperties": {"transportType": "usb"},
                "hardwareProperties": {
                    "marketingName": "Apple Watch Ultra",
                    "platform": "watchOS",
                },
                "deviceProperties": {
                    "name": "Luke Apple Watch Ultra",
                    "osVersionNumber": "26.4",
                },
                "simulator": False,
            },
            {
                "identifier": "PHONE-UDID-001",
                "connectionProperties": {"transportType": "usb"},
                "hardwareProperties": {
                    "marketingName": "iPhone 17 Pro Max",
                    "platform": "iOS",
                },
                "deviceProperties": {
                    "name": "Luke iPhone 17 Pro Max",
                    "osVersionNumber": "26.4",
                },
                "simulator": False,
            },
        ]

        with patch("simemu.device._list_xcdevice_devices_json", return_value=None):
            with patch("simemu.device._list_devicectl_devices_json", return_value=devicectl_devices):
                devices = device.list_ios_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual("Luke iPhone 17 Pro Max", devices[0].device_name)

    def test_falls_back_to_xcdevice_when_devicectl_returns_none(self) -> None:
        xcdevice_devices = [
            {
                "ignored": False,
                "modelCode": "iPhone18,2",
                "simulator": False,
                "modelName": "iPhone 17 Pro Max",
                "operatingSystemVersion": "26.4 (23E246)",
                "identifier": "00008150-001622E63638401C",
                "platform": "com.apple.platform.iphoneos",
                "available": True,
                "name": "Luke iPhone 17 Pro Max",
                "interface": "usb",
            }
        ]

        with patch("simemu.device._list_devicectl_devices_json", return_value=None):
            with patch("simemu.device._list_xcdevice_devices_json", return_value=xcdevice_devices):
                devices = device.list_ios_devices()

        self.assertEqual(1, len(devices))
        self.assertEqual("00008150-001622E63638401C", devices[0].device_id)
        self.assertEqual("Luke iPhone 17 Pro Max", devices[0].device_name)
        self.assertEqual("26.4", devices[0].os_version)

    def test_includes_persistent_alias_when_present(self) -> None:
        xcdevice_devices = [
            {
                "simulator": False,
                "identifier": "00008150-001622E63638401C",
                "platform": "com.apple.platform.iphoneos",
                "available": True,
                "name": "Luke iPhone 17 Pro Max",
                "operatingSystemVersion": "26.4 (23E246)",
                "interface": "usb",
            }
        ]

        with patch("simemu.device._list_devicectl_devices_json", return_value=None):
            with patch("simemu.device._list_xcdevice_devices_json", return_value=xcdevice_devices):
                with patch("simemu.device.find_alias_for_device", return_value="luke-iphone"):
                    devices = device.list_ios_devices()

        self.assertEqual("luke-iphone", devices[0].alias)

    def test_devicectl_uses_temp_file_instead_of_stdout(self) -> None:
        class _Tmp:
            name = "/tmp/simemu-devicectl-test.json"

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        run_result = Mock(returncode=0)

        with patch("simemu.device.tempfile.NamedTemporaryFile", return_value=_Tmp()):
            with patch("simemu.device.subprocess.run", return_value=run_result) as mock_run:
                with patch("simemu.device.Path.read_text", return_value=json.dumps({"result": {"devices": []}})):
                    with patch("simemu.device.Path.unlink", return_value=None):
                        device._list_devicectl_devices_json()

        self.assertEqual(
            "/tmp/simemu-devicectl-test.json",
            mock_run.call_args[0][0][-1],
        )
        self.assertNotEqual("/dev/stdout", mock_run.call_args[0][0][-1])


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
            with patch("simemu.discover._get_claimed_sim_ids", return_value=set()):
                sim = find_simulator("ios", real_device=True)

        self.assertEqual("UDID-001", sim.sim_id)
        self.assertTrue(sim.real_device)

    def test_find_simulator_real_device_raises_when_none_connected(self) -> None:
        with patch("simemu.device.list_ios_devices", return_value=[]):
            with patch("simemu.discover._get_claimed_sim_ids", return_value=set()):
                with self.assertRaises(NoSimulatorAvailable) as ctx:
                    find_simulator("ios", real_device=True)

        self.assertIn("real devices", str(ctx.exception))
        self.assertIn("USB connection", str(ctx.exception))

    def test_find_simulator_real_device_filters_by_name(self) -> None:
        dev1 = device.RealDevice("UDID-1", "ios", "iPhone 15", True, "18", "usb")
        dev2 = device.RealDevice("UDID-2", "ios", "iPad Pro", True, "18", "usb")

        with patch("simemu.device.list_ios_devices", return_value=[dev1, dev2]):
            with patch("simemu.discover._get_claimed_sim_ids", return_value=set()):
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


class IOSScreenshotTests(unittest.TestCase):
    def test_uses_idevicescreenshot_when_available(self) -> None:
        with patch("simemu.device.shutil.which", return_value="/opt/homebrew/bin/idevicescreenshot"):
            with patch("simemu.device.subprocess.run") as mock_run:
                device.ios_screenshot("UDID-001", "/tmp/out.png")

        mock_run.assert_called_once_with(
            ["idevicescreenshot", "-u", "UDID-001", "/tmp/out.png"],
            check=True,
        )

    def test_raises_clear_error_when_idevicescreenshot_missing(self) -> None:
        with patch("simemu.device.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "brew install libimobiledevice"):
                device.ios_screenshot("UDID-001", "/tmp/out.png")


class IsRealDeviceSerialTests(unittest.TestCase):
    def test_emulator_serial_returns_false(self) -> None:
        self.assertFalse(device.is_real_device_serial("emulator-5554"))

    def test_usb_serial_returns_true(self) -> None:
        self.assertTrue(device.is_real_device_serial("R5CR1234567"))

    def test_wifi_serial_returns_true(self) -> None:
        self.assertTrue(device.is_real_device_serial("192.168.1.100:5555"))


if __name__ == "__main__":
    unittest.main()
