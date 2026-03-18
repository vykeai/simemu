import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import android


def _mock_serial(avd_name: str) -> str:
    """Stub _serial that always returns a fixed serial."""
    return "emulator-5554"


def _mock_get_android_serial(avd_name: str) -> str:
    return "emulator-5554"


class TestSerial(unittest.TestCase):

    @patch("simemu.android.get_android_serial", return_value=None)
    def test_raises_when_not_running(self, mock_gas: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android._serial("MyAVD")
        self.assertIn("not running", str(ctx.exception))
        self.assertIn("MyAVD", str(ctx.exception))

    @patch("simemu.android.get_android_serial", return_value="emulator-5554")
    def test_returns_serial_when_running(self, mock_gas: MagicMock) -> None:
        serial = android._serial("MyAVD")
        self.assertEqual(serial, "emulator-5554")


class TestInstall(unittest.TestCase):

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_validates_apk_extension(self, mock_ready: MagicMock, mock_run: MagicMock) -> None:
        with tempfile.NamedTemporaryFile(suffix=".zip") as f:
            with self.assertRaises(RuntimeError) as ctx:
                android.install("MyAVD", f.name)
            self.assertIn(".apk", str(ctx.exception))

    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_raises_on_missing_file(self, mock_ready: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.install("MyAVD", "/nonexistent/app.apk")
        self.assertIn("APK not found", str(ctx.exception))

    @patch("simemu.android.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="adb", timeout=5))
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_raises_on_timeout(self, mock_ready: MagicMock, mock_run: MagicMock) -> None:
        with tempfile.NamedTemporaryFile(suffix=".apk") as f:
            with self.assertRaises(RuntimeError) as ctx:
                android.install("MyAVD", f.name, timeout=5)
            self.assertIn("timed out", str(ctx.exception))


class TestKey(unittest.TestCase):

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_maps_named_keys(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.key("MyAVD", "home")
        mock_adb.assert_called_once_with("MyAVD", "shell", "input", "keyevent", "3")

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_maps_back_key(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.key("MyAVD", "back")
        mock_adb.assert_called_once_with("MyAVD", "shell", "input", "keyevent", "4")

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_accepts_raw_integer_keycodes(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.key("MyAVD", "42")
        mock_adb.assert_called_once_with("MyAVD", "shell", "input", "keyevent", "42")

    @patch("simemu.android._ensure_booted")
    def test_raises_for_unknown_key(self, mock_boot: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.key("MyAVD", "turbo_button")
        self.assertIn("Unknown Android key", str(ctx.exception))
        self.assertIn("turbo_button", str(ctx.exception))


class TestGetScreenSize(unittest.TestCase):

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android._serial", side_effect=_mock_serial)
    def test_parses_physical_size(self, mock_serial: MagicMock, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="Physical size: 1080x2400\n", returncode=0)
        w, h = android.get_screen_size("MyAVD")
        self.assertEqual(w, 1080)
        self.assertEqual(h, 2400)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android._serial", side_effect=_mock_serial)
    def test_parses_override_size(self, mock_serial: MagicMock, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(
            stdout="Physical size: 1080x2400\nOverride size: 540x1200\n", returncode=0
        )
        w, h = android.get_screen_size("MyAVD")
        # Returns first match (physical)
        self.assertEqual(w, 1080)
        self.assertEqual(h, 2400)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android._serial", side_effect=_mock_serial)
    def test_raises_on_unparseable_output(self, mock_serial: MagicMock, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="no size info here\n", returncode=0)
        with self.assertRaises(RuntimeError) as ctx:
            android.get_screen_size("MyAVD")
        self.assertIn("Could not determine screen size", str(ctx.exception))


class TestNetwork(unittest.TestCase):

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_airplane_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.network("MyAVD", "airplane")
        mock_adb.assert_called_once_with(
            "MyAVD", "shell", "cmd", "connectivity", "airplane-mode", "enable"
        )

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_all_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.network("MyAVD", "all")
        mock_adb.assert_called_once_with(
            "MyAVD", "shell", "cmd", "connectivity", "airplane-mode", "disable"
        )

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_wifi_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.network("MyAVD", "wifi")
        calls = mock_adb.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0], call("MyAVD", "shell", "cmd", "connectivity", "airplane-mode", "disable"))
        self.assertEqual(calls[1], call("MyAVD", "shell", "svc", "data", "disable"))
        self.assertEqual(calls[2], call("MyAVD", "shell", "svc", "wifi", "enable"))

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_data_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.network("MyAVD", "data")
        calls = mock_adb.call_args_list
        self.assertEqual(len(calls), 3)

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_none_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.network("MyAVD", "none")
        calls = mock_adb.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], call("MyAVD", "shell", "svc", "wifi", "disable"))
        self.assertEqual(calls[1], call("MyAVD", "shell", "svc", "data", "disable"))

    @patch("simemu.android._ensure_booted")
    def test_raises_for_unknown_mode(self, mock_boot: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.network("MyAVD", "satellite")
        self.assertIn("Unknown network mode", str(ctx.exception))


class TestBattery(unittest.TestCase):

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_clamps_level_high(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.battery("MyAVD", level=150)
        # Should clamp to 100
        set_level_call = mock_adb.call_args_list[0]
        self.assertIn("100", set_level_call.args)

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_clamps_level_low(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.battery("MyAVD", level=-10)
        set_level_call = mock_adb.call_args_list[0]
        self.assertIn("0", set_level_call.args)

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_sets_valid_level(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.battery("MyAVD", level=75)
        set_level_call = mock_adb.call_args_list[0]
        self.assertIn("75", set_level_call.args)
        # Should also set status to charging
        self.assertEqual(len(mock_adb.call_args_list), 2)

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_reset_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.battery("MyAVD", reset=True)
        mock_adb.assert_called_once_with("MyAVD", "shell", "dumpsys", "battery", "reset")

    @patch("simemu.android._ensure_booted")
    def test_raises_when_no_level_and_no_reset(self, mock_boot: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.battery("MyAVD")
        self.assertIn("Specify a battery level", str(ctx.exception))


class TestRotate(unittest.TestCase):

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_portrait(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.rotate("MyAVD", "portrait")
        # Should disable auto-rotate and set rotation to 0
        calls = mock_adb.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("0", calls[1].args)  # user_rotation = 0

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_landscape(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.rotate("MyAVD", "landscape")
        calls = mock_adb.call_args_list
        self.assertIn("1", calls[1].args)  # user_rotation = 1

    @patch("simemu.android._ensure_booted")
    def test_validates_orientation(self, mock_boot: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.rotate("MyAVD", "upside_down")
        self.assertIn("portrait", str(ctx.exception))
        self.assertIn("landscape", str(ctx.exception))


class TestRename(unittest.TestCase):

    @patch("simemu.genymotion.is_genymotion_id", return_value=True)
    def test_raises_for_genymotion(self, mock_geny: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.rename("a1b2c3d4-5678-9abc-def0-111111111111", "NewName")
        self.assertIn("Genymotion", str(ctx.exception))

    @patch("simemu.android.get_android_serial", return_value="emulator-5554")
    @patch("simemu.genymotion.is_genymotion_id", return_value=False)
    def test_raises_when_running(self, mock_geny: MagicMock, mock_serial: MagicMock) -> None:
        # Create a fake .ini file so rename() gets past the existence check
        avd_base = Path.home() / ".android" / "avd"
        ini_file = avd_base / "MyAVD.ini"
        avd_base.mkdir(parents=True, exist_ok=True)
        created = not ini_file.exists()
        try:
            if created:
                ini_file.write_text("path=/tmp/fake\n")
            with self.assertRaises(RuntimeError) as ctx:
                android.rename("MyAVD", "NewAVD")
            self.assertIn("running", str(ctx.exception))
            self.assertIn("Shut it down", str(ctx.exception))
        finally:
            if created:
                ini_file.unlink(missing_ok=True)


class TestStatusBar(unittest.TestCase):

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_enters_demo_mode(self, mock_boot: MagicMock, mock_adb: MagicMock) -> None:
        android.status_bar("MyAVD", time_str="9:41", battery=100)
        calls = mock_adb.call_args_list
        # First call: enable demo mode
        self.assertIn("sysui_demo_allowed", calls[0].args)
        # Second call: enter demo
        self.assertIn("enter", calls[1].args)
        # Third call: clock
        self.assertIn("clock", calls[2].args)
        # Fourth call: battery
        self.assertIn("battery", calls[3].args)


class TestCrashLog(unittest.TestCase):

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android._serial", side_effect=_mock_serial)
    @patch("simemu.android._ensure_booted")
    def test_returns_none_when_no_crashes(self, mock_boot: MagicMock, mock_serial: MagicMock,
                                           mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = android.crash_log("MyAVD")
        self.assertIsNone(result)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android._serial", side_effect=_mock_serial)
    @patch("simemu.android._ensure_booted")
    def test_returns_crash_text(self, mock_boot: MagicMock, mock_serial: MagicMock,
                                 mock_run: MagicMock) -> None:
        crash_output = (
            "E AndroidRuntime: FATAL EXCEPTION: main\n"
            "E AndroidRuntime: Caused by: java.lang.NullPointerException\n"
            "E AndroidRuntime:     at com.example.App.onCreate(App.java:42)\n"
        )
        mock_run.return_value = MagicMock(stdout=crash_output, returncode=0)
        result = android.crash_log("MyAVD")
        self.assertIsNotNone(result)
        self.assertIn("FATAL EXCEPTION", result)
        self.assertIn("NullPointerException", result)


class TestBiometrics(unittest.TestCase):

    @patch("simemu.genymotion.is_genymotion_id", return_value=True)
    def test_raises_for_genymotion(self, mock_geny: MagicMock) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.biometrics("a1b2c3d4-5678-9abc-def0-111111111111", match=True)
        self.assertIn("Genymotion", str(ctx.exception))

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    @patch("simemu.genymotion.is_genymotion_id", return_value=False)
    def test_sends_fingerprint_match(self, mock_geny: MagicMock, mock_boot: MagicMock,
                                      mock_adb: MagicMock) -> None:
        android.biometrics("MyAVD", match=True)
        mock_adb.assert_called_once_with("MyAVD", "emu", "finger", "touch", "1")

    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    @patch("simemu.genymotion.is_genymotion_id", return_value=False)
    def test_sends_fingerprint_no_match(self, mock_geny: MagicMock, mock_boot: MagicMock,
                                         mock_adb: MagicMock) -> None:
        android.biometrics("MyAVD", match=False)
        mock_adb.assert_called_once_with("MyAVD", "emu", "finger", "touch", "2")


if __name__ == "__main__":
    unittest.main()
