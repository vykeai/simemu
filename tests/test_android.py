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

    @patch("simemu.android.verify_install")
    @patch("simemu.android._apk_application_id", return_value="app.fitkind.dev")
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_runs_post_install_verification(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
        mock_app_id: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="Success\n", stderr="")
        with tempfile.NamedTemporaryFile(suffix=".apk") as f:
            android.install("MyAVD", f.name)
        mock_verify.assert_called_once_with("MyAVD", "app.fitkind.dev")

    @patch("simemu.android.repair_install")
    @patch("simemu.android.verify_install", side_effect=RuntimeError("broken pm"))
    @patch("simemu.android._apk_application_id", return_value="app.fitkind.dev")
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_attempts_repair_when_verification_fails(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
        mock_app_id: MagicMock,
        mock_verify: MagicMock,
        mock_repair: MagicMock,
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="Success\n", stderr="")
        with tempfile.NamedTemporaryFile(suffix=".apk") as f:
            android.install("MyAVD", f.name)
            mock_repair.assert_called_once_with("MyAVD", "app.fitkind.dev", f.name, timeout=120)


class TestPackageVerification(unittest.TestCase):
    def _result(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_verify_install_passes_with_coherent_package_state(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.side_effect = [
            self._result(stdout="package:/data/app/app.fitkind.dev/base.apk\n"),
            self._result(stdout="priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=false\napp.fitkind.dev/.MainActivity\n"),
            self._result(stdout="Package [app.fitkind.dev] (123abc):\n  pkg=Package{123abc app.fitkind.dev}\n"),
        ]
        probe = android.verify_install("MyAVD", "app.fitkind.dev", timeout=0)
        self.assertTrue(probe.ok)
        self.assertIn("pm path", probe.format_report())

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_verify_install_raises_on_pkg_null_state(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.side_effect = [
            self._result(stdout=""),
            self._result(stdout="No activity found\n"),
            self._result(stdout="Packages:\n  Package [app.sitches.dev] (abc):\n    pkg=null\n"),
        ]
        with self.assertRaises(RuntimeError) as ctx:
            android.verify_install("MyAVD", "app.sitches.dev", timeout=0)
        msg = str(ctx.exception)
        self.assertIn("package-manager state is inconsistent", msg)
        self.assertIn("pkg=null", msg)
        self.assertIn("repair-install", msg)

    @patch("simemu.android.verify_install", return_value=android.PackageVerification(
        package="app.sitches.dev",
        pm_path="package:/data/app/app.sitches.dev/base.apk",
        resolve_activity="app.sitches.dev/.MainActivity",
        dumpsys="Package [app.sitches.dev]",
        pm_path_ok=True,
        resolve_activity_ok=True,
        dumpsys_ok=True,
    ))
    @patch("simemu.android.install")
    @patch("simemu.android.reboot")
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_repair_install_reboots_and_reinstalls(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
        mock_reboot: MagicMock,
        mock_install: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        probe = android.repair_install("MyAVD", "app.sitches.dev", "/tmp/app.apk")
        self.assertTrue(probe.ok)
        mock_run.assert_called_once()
        mock_reboot.assert_called_once_with("MyAVD")
        mock_install.assert_called_once_with("MyAVD", "/tmp/app.apk", timeout=120, repair_on_failure=False)
        # verify_install called twice: initial + delayed recheck
        self.assertEqual(mock_verify.call_count, 2)

    @patch("simemu.android.verify_install", side_effect=[
        RuntimeError("soft reboot still bad"),  # reboot attempt: initial verify fails
        # cold-boot attempt: initial verify passes
        android.PackageVerification(
            package="app.sitches.dev",
            pm_path="package:/data/app/app.sitches.dev/base.apk",
            resolve_activity="app.sitches.dev/.MainActivity",
            dumpsys="Package [app.sitches.dev]",
            pm_path_ok=True, resolve_activity_ok=True, dumpsys_ok=True,
        ),
        # cold-boot attempt: delayed recheck also passes
        android.PackageVerification(
            package="app.sitches.dev",
            pm_path="package:/data/app/app.sitches.dev/base.apk",
            resolve_activity="app.sitches.dev/.MainActivity",
            dumpsys="Package [app.sitches.dev]",
            pm_path_ok=True, resolve_activity_ok=True, dumpsys_ok=True,
        ),
    ])
    @patch("simemu.android.install")
    @patch("simemu.android._repair_wipe_data_cycle")
    @patch("simemu.android._repair_cold_boot_cycle")
    @patch("simemu.android._repair_reboot_cycle")
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_repair_install_escalates_to_cold_boot(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
        mock_reboot_cycle: MagicMock,
        mock_cold_boot_cycle: MagicMock,
        mock_wipe_cycle: MagicMock,
        mock_install: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        probe = android.repair_install("MyAVD", "app.sitches.dev", "/tmp/app.apk")
        self.assertTrue(probe.ok)
        mock_reboot_cycle.assert_called_once_with("MyAVD")
        mock_cold_boot_cycle.assert_called_once_with("MyAVD")
        mock_wipe_cycle.assert_not_called()
        self.assertEqual(2, mock_install.call_count)

    @patch("simemu.android.verify_install", side_effect=RuntimeError("still broken"))
    @patch("simemu.android.install")
    @patch("simemu.android._repair_wipe_data_cycle")
    @patch("simemu.android._repair_cold_boot_cycle")
    @patch("simemu.android._repair_reboot_cycle")
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_repair_install_raises_after_all_recovery_steps_fail(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
        mock_reboot_cycle: MagicMock,
        mock_cold_boot_cycle: MagicMock,
        mock_wipe_cycle: MagicMock,
        mock_install: MagicMock,
        mock_verify: MagicMock,
    ) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            android.repair_install("MyAVD", "app.sitches.dev", "/tmp/app.apk")
        self.assertIn("repair-install could not recover", str(ctx.exception))
        self.assertIn("reboot:", str(ctx.exception))
        self.assertIn("cold-boot:", str(ctx.exception))
        self.assertIn("wipe-data:", str(ctx.exception))


class TestForegroundVerification(unittest.TestCase):
    def _result(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
        return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_foreground_app_parses_resumed_package(
        self,
        mock_ready: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mock_run.return_value = self._result(
            stdout="  mResumedActivity: ActivityRecord{abc u0 app.fitkind.dev/.MainActivity t12}\n"
        )
        self.assertEqual("app.fitkind.dev", android.foreground_app("MyAVD"))

    @patch("simemu.android.foreground_app", return_value="com.vivii.dev")
    def test_wait_for_foreground_package_raises_for_wrong_app(self, mock_foreground: MagicMock) -> None:
        with patch("simemu.android.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "Foreground app was com.vivii.dev instead"):
                android._wait_for_foreground_package("MyAVD", "app.fitkind.dev", timeout=0.2, delay=0.01)

    @patch("simemu.android._wait_for_foreground_package")
    @patch("simemu.android._adb")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_launch_verifies_foreground_package(
        self,
        mock_ready: MagicMock,
        mock_adb: MagicMock,
        mock_wait_foreground: MagicMock,
    ) -> None:
        android.launch("MyAVD", "app.fitkind.dev")
        mock_wait_foreground.assert_called_once_with("MyAVD", "app.fitkind.dev")

    @patch("simemu.android._wait_for_foreground_package")
    @patch("simemu.android._adb")
    @patch("simemu.android._ensure_booted")
    def test_open_url_verifies_expected_package_when_provided(
        self,
        mock_booted: MagicMock,
        mock_adb: MagicMock,
        mock_wait_foreground: MagicMock,
    ) -> None:
        android.open_url("MyAVD", "fitkind://debug/vault/template-detail-proof", expected_package="app.fitkind.dev")
        mock_wait_foreground.assert_called_once_with("MyAVD", "app.fitkind.dev")


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


class TestStopOtherApps(unittest.TestCase):
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_stops_third_party_apps_except_keep(self, mock_ready, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="package:app.sitches.dev\npackage:ai.vivii.dev\npackage:com.example.other\n",
            stderr="",
        )
        stopped = android.stop_other_apps("TestAVD", keep="app.sitches.dev")
        self.assertIn("ai.vivii.dev", stopped)
        self.assertIn("com.example.other", stopped)
        self.assertNotIn("app.sitches.dev", stopped)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_keeps_multiple_packages(self, mock_ready, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="package:app.a\npackage:app.b\npackage:app.c\n",
            stderr="",
        )
        stopped = android.stop_other_apps("TestAVD", keep=["app.a", "app.b"])
        self.assertEqual(stopped, ["app.c"])


class TestDismissSystemDialogs(unittest.TestCase):
    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_detects_and_dismisses_anr(self, mock_ready, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="mIsAnrDialog=true Application Not Responding",
            stderr="",
        )
        result = android.dismiss_system_dialogs("TestAVD")
        self.assertTrue(result)
        # dumpsys + keyevent 66 + keyevent 4 + broadcast = 4 calls
        self.assertGreaterEqual(mock_run.call_count, 4)

    @patch("simemu.android.subprocess.run")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    def test_returns_false_when_no_dialog(self, mock_ready, mock_run) -> None:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Window #0: com.example.app/MainActivity",
            stderr="",
        )
        result = android.dismiss_system_dialogs("TestAVD")
        self.assertFalse(result)
        mock_run.assert_called_once()


class TestRepairInstallDelayedVerify(unittest.TestCase):
    @patch("simemu.android.verify_install")
    @patch("simemu.android.install")
    @patch("simemu.android._repair_reboot_cycle")
    @patch("simemu.android.wait_until_ready", return_value="emulator-5554")
    @patch("simemu.android.subprocess.run")
    def test_repair_does_delayed_recheck(self, mock_sub, mock_ready, mock_reboot,
                                          mock_install, mock_verify) -> None:
        probe = android.PackageVerification(
            package="com.test", pm_path="package:/data/app/com.test",
            resolve_activity="com.test/.Main", dumpsys="Package [com.test]",
            pm_path_ok=True, resolve_activity_ok=True, dumpsys_ok=True,
        )
        mock_verify.return_value = probe
        result = android.repair_install("TestAVD", "com.test", "/tmp/app.apk")
        # verify_install called twice: initial + delayed recheck
        self.assertEqual(mock_verify.call_count, 2)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
