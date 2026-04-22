"""Tests that maintenance mode blocks all boot/spawn paths."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import state, ios, android


class MaintenanceBlocksBootTests(unittest.TestCase):
    """Verify that NO code path can spawn an emulator during maintenance."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-maint-test-")
        self.old_state_dir = os.environ.get("SIMEMU_STATE_DIR")
        self.old_config_dir = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name
        # Enable maintenance mode
        state.enter_maintenance("test maintenance", 1)

    def tearDown(self) -> None:
        state.exit_maintenance()
        if self.old_state_dir is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self.old_state_dir
        if self.old_config_dir is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self.old_config_dir
        self.tmpdir.cleanup()

    def test_android_ensure_booted_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            android._ensure_booted("some-avd")

    def test_android_boot_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            android.boot("some-avd")

    def test_android_erase_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            android.erase("some-avd")

    def test_ios_ensure_booted_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            ios._ensure_booted("SIM-001")

    def test_ios_boot_blocked(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            ios.boot("SIM-001")

    def test_android_install_blocked_not_booted(self) -> None:
        """install calls _ensure_booted which should fail during maintenance."""
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            android.install("some-avd", "/tmp/test.apk")

    def test_android_screenshot_blocked_not_booted(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            android.screenshot("some-avd", "/tmp/shot.png")

    def test_android_tap_blocked_not_booted(self) -> None:
        """tap calls _adb which calls _serial — raises 'not running' (still blocked, no spawn)."""
        with self.assertRaises(RuntimeError):
            android.tap("some-avd", 100, 200)

    def test_ios_install_blocked_not_booted(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            ios.install("SIM-001", "/tmp/Test.app")

    def test_ios_screenshot_blocked_not_booted(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            ios.screenshot("SIM-001", "/tmp/shot.png")


class MaintenanceNotBlockedTests(unittest.TestCase):
    """Verify that safe commands still work during maintenance."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-maint-test-")
        self.old_state_dir = os.environ.get("SIMEMU_STATE_DIR")
        self.old_config_dir = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name
        state.enter_maintenance("test", 1)

    def tearDown(self) -> None:
        state.exit_maintenance()
        if self.old_state_dir is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self.old_state_dir
        if self.old_config_dir is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self.old_config_dir
        self.tmpdir.cleanup()

    def test_status_works_during_maintenance(self) -> None:
        """state.get_all() should work — it's read-only."""
        result = state.get_all()
        self.assertEqual({}, result)

    def test_exit_maintenance_works(self) -> None:
        state.exit_maintenance()
        # Should not raise
        state.check_maintenance()

    def test_enter_exit_roundtrip(self) -> None:
        state.exit_maintenance()
        state.check_maintenance()  # no error
        state.enter_maintenance("back on", 5)
        with self.assertRaises(RuntimeError):
            state.check_maintenance()


class NoAutoBootTests(unittest.TestCase):
    """Verify _ensure_booted never auto-spawns — even without maintenance."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-noboot-test-")
        self.old_state_dir = os.environ.get("SIMEMU_STATE_DIR")
        self.old_config_dir = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self.old_state_dir is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self.old_state_dir
        if self.old_config_dir is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self.old_config_dir
        self.tmpdir.cleanup()

    def test_android_ensure_booted_raises_not_spawns(self) -> None:
        """Without maintenance, _ensure_booted should still raise (not boot)."""
        with patch("simemu.android.get_android_serial", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "Wake it through the session API"):
                android._ensure_booted("test-avd")

    def test_ios_ensure_booted_raises_not_spawns(self) -> None:
        with patch("simemu.ios._is_booted", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "Boot it explicitly"):
                ios._ensure_booted("SIM-001")

    def test_android_ensure_booted_no_subprocess_call(self) -> None:
        """Verify no subprocess is spawned at all."""
        with patch("simemu.android.get_android_serial", return_value=None):
            with patch("subprocess.Popen") as popen_mock:
                with patch("subprocess.run") as run_mock:
                    try:
                        android._ensure_booted("test-avd")
                    except RuntimeError:
                        pass
                    popen_mock.assert_not_called()
                    run_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
