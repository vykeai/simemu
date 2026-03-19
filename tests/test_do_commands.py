"""Tests for simemu.session.do_command — exhaustive dispatch coverage."""

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

_tmpdir = tempfile.mkdtemp(prefix="simemu-do-cmd-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu.session import (
    _compute_expires_at,
    _now_iso,
    do_command,
)


class DoCommandBase(unittest.TestCase):
    """Base class that seeds a session and patches touch for do_command tests."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-docmd-")
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name
        # Seed default iOS session
        self._seed("s-test01", platform="ios")

    def tearDown(self) -> None:
        if self._old_state is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self._old_state
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        self.tmpdir.cleanup()

    def _seed(
        self,
        session_id: str = "s-test01",
        platform: str = "ios",
        sim_id: str = "AAA-111",
        device_name: str = "iPhone 16 Pro",
        real_device: bool = False,
    ) -> None:
        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": platform,
            "form_factor": "phone",
            "os_version": None,
            "real_device": real_device,
            "label": "",
            "status": "active",
            "sim_id": sim_id,
            "device_name": device_name,
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "expires_at": _compute_expires_at("active", now),
            "resolved_os_version": "iOS 26.2" if platform == "ios" else "API 35",
            "claim_platform": platform,
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": real_device,
            "claim_label": "",
        }
        sf = Path(self.tmpdir.name) / "sessions.json"
        if sf.exists():
            data = json.loads(sf.read_text())
        else:
            data = {"sessions": {}}
        data["sessions"][session_id] = session_data
        sf.write_text(json.dumps(data))


# ── install ──────────────────────────────────────────────────────────────────


class TestDoInstall(DoCommandBase):
    @patch("simemu.session.ios.install")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_install_ios(self, mock_serial, mock_install) -> None:
        result = do_command("s-test01", "install", ["/path/to/App.app"])
        mock_install.assert_called_once_with("AAA-111", "/path/to/App.app")
        self.assertEqual(result["status"], "installed")

    @patch("simemu.session.android.install")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_install_android(self, mock_serial, mock_install) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "install", ["/path/to/app.apk"])
        mock_install.assert_called_once_with("Pixel_7", "/path/to/app.apk")
        self.assertEqual(result["status"], "installed")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_install_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-test01", "install", [])
        self.assertIn("install", str(ctx.exception).lower())

    @patch("simemu.session.device.ios_install")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_install_real_ios(self, mock_serial, mock_install) -> None:
        self._seed("s-real01", platform="ios", sim_id="UDID-REAL",
                    device_name="iPhone 15 (real)", real_device=True)
        result = do_command("s-real01", "install", ["/path/to/App.ipa"])
        mock_install.assert_called_once_with("UDID-REAL", "/path/to/App.ipa")
        self.assertEqual(result["status"], "installed")


# ── launch ───────────────────────────────────────────────────────────────────


class TestDoLaunch(DoCommandBase):
    @patch("simemu.session.ios.launch")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_launch_ios(self, mock_serial, mock_launch) -> None:
        result = do_command("s-test01", "launch", ["com.example.App"])
        mock_launch.assert_called_once_with("AAA-111", "com.example.App", [])
        self.assertEqual(result["status"], "launched")

    @patch("simemu.session.android.launch")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_launch_android(self, mock_serial, mock_launch) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "launch", ["com.example.app/.MainActivity"])
        mock_launch.assert_called_once_with("Pixel_7", "com.example.app/.MainActivity", [])
        self.assertEqual(result["status"], "launched")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_launch_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "launch", [])


# ── tap ──────────────────────────────────────────────────────────────────────


class TestDoTap(DoCommandBase):
    @patch("simemu.session.ios.tap")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_tap_ios(self, mock_serial, mock_tap) -> None:
        result = do_command("s-test01", "tap", ["100", "200"])
        mock_tap.assert_called_once_with("AAA-111", 100.0, 200.0)
        self.assertEqual(result["status"], "tapped")

    @patch("simemu.session.android.tap")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_tap_android(self, mock_serial, mock_tap) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "tap", ["50", "300"])
        mock_tap.assert_called_once_with("Pixel_7", 50.0, 300.0)
        self.assertEqual(result["status"], "tapped")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_tap_missing_args(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "tap", ["100"])


# ── swipe ────────────────────────────────────────────────────────────────────


class TestDoSwipe(DoCommandBase):
    @patch("simemu.session.ios.swipe")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_swipe(self, mock_serial, mock_swipe) -> None:
        result = do_command("s-test01", "swipe", ["10", "20", "30", "40"])
        mock_swipe.assert_called_once_with("AAA-111", 10.0, 20.0, 30.0, 40.0, duration=0.3)
        self.assertEqual(result["status"], "swiped")

    @patch("simemu.session.android.swipe")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_swipe_android_with_duration(self, mock_serial, mock_swipe) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "swipe", ["10", "20", "30", "40", "--duration", "500"])
        mock_swipe.assert_called_once_with("Pixel_7", 10.0, 20.0, 30.0, 40.0, duration=500)

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_swipe_missing_args(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "swipe", ["10", "20", "30"])


# ── screenshot ───────────────────────────────────────────────────────────────


class TestDoScreenshot(DoCommandBase):
    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_screenshot(self, mock_serial, mock_screenshot) -> None:
        result = do_command("s-test01", "screenshot", ["-o", "/tmp/test.png"])
        mock_screenshot.assert_called_once_with("AAA-111", "/tmp/test.png", fmt=None)
        self.assertEqual(result["status"], "captured")
        self.assertEqual(result["path"], "/tmp/test.png")

    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_screenshot_auto_path(self, mock_serial, mock_screenshot) -> None:
        result = do_command("s-test01", "screenshot", [])
        self.assertEqual(result["status"], "captured")
        self.assertIn("s-test01", result["path"])

    @patch("simemu.session.android.screenshot")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_screenshot_android(self, mock_serial, mock_screenshot) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "screenshot", ["-o", "/tmp/droid.png"])
        mock_screenshot.assert_called_once_with("Pixel_7", "/tmp/droid.png")


# ── maestro ──────────────────────────────────────────────────────────────────


class TestDoMaestro(DoCommandBase):
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_maestro(self, mock_serial, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        result = do_command("s-test01", "maestro", ["flow.yaml"])
        self.assertEqual(result["status"], "passed")
        # Verify maestro was called with the session's sim_id
        cmd_args = mock_run.call_args[0][0]
        self.assertEqual(cmd_args[0], "maestro")
        self.assertIn("flow.yaml", cmd_args)

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_maestro_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "maestro", [])


# ── url ──────────────────────────────────────────────────────────────────────


class TestDoUrl(DoCommandBase):
    @patch("simemu.session.ios.open_url")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url(self, mock_serial, mock_url) -> None:
        result = do_command("s-test01", "url", ["https://example.com"])
        mock_url.assert_called_once_with("AAA-111", "https://example.com")
        self.assertEqual(result["status"], "opened")

    @patch("simemu.session.android.open_url")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_android(self, mock_serial, mock_url) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "url", ["https://example.com"])
        mock_url.assert_called_once_with("Pixel_7", "https://example.com")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "url", [])


# ── terminate ────────────────────────────────────────────────────────────────


class TestDoTerminate(DoCommandBase):
    @patch("simemu.session.ios.terminate")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_terminate(self, mock_serial, mock_term) -> None:
        result = do_command("s-test01", "terminate", ["com.example.App"])
        mock_term.assert_called_once_with("AAA-111", "com.example.App")
        self.assertEqual(result["status"], "terminated")

    @patch("simemu.session.android.terminate")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_terminate_android(self, mock_serial, mock_term) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "terminate", ["com.example.app"])
        mock_term.assert_called_once_with("Pixel_7", "com.example.app")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_terminate_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "terminate", [])


# ── uninstall ────────────────────────────────────────────────────────────────


class TestDoUninstall(DoCommandBase):
    @patch("simemu.session.ios.uninstall")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_uninstall(self, mock_serial, mock_uninstall) -> None:
        result = do_command("s-test01", "uninstall", ["com.example.App"])
        mock_uninstall.assert_called_once_with("AAA-111", "com.example.App")
        self.assertEqual(result["status"], "uninstalled")

    @patch("simemu.session.android.uninstall")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_uninstall_android(self, mock_serial, mock_uninstall) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "uninstall", ["com.example.app"])
        mock_uninstall.assert_called_once_with("Pixel_7", "com.example.app")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_uninstall_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "uninstall", [])


# ── input ────────────────────────────────────────────────────────────────────


class TestDoInput(DoCommandBase):
    @patch("simemu.session.ios.input_text")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_input(self, mock_serial, mock_input) -> None:
        result = do_command("s-test01", "input", ["hello", "world"])
        mock_input.assert_called_once_with("AAA-111", "hello world")
        self.assertEqual(result["status"], "input")

    @patch("simemu.session.android.input_text")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_input_android(self, mock_serial, mock_input) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "input", ["test text"])
        mock_input.assert_called_once_with("Pixel_7", "test text")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_input_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "input", [])


# ── long-press ───────────────────────────────────────────────────────────────


class TestDoLongPress(DoCommandBase):
    @patch("simemu.session.ios.long_press")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_long_press(self, mock_serial, mock_lp) -> None:
        result = do_command("s-test01", "long-press", ["100", "200"])
        mock_lp.assert_called_once_with("AAA-111", 100.0, 200.0, duration=1.0)
        self.assertEqual(result["status"], "long-pressed")

    @patch("simemu.session.android.long_press")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_long_press_android_with_duration(self, mock_serial, mock_lp) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "long-press", ["100", "200", "--duration", "2000"])
        mock_lp.assert_called_once_with("Pixel_7", 100.0, 200.0, duration=2000)

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_long_press_missing_args(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "long-press", ["100"])


# ── key ──────────────────────────────────────────────────────────────────────


class TestDoKey(DoCommandBase):
    @patch("simemu.session.ios.key")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_key(self, mock_serial, mock_key) -> None:
        result = do_command("s-test01", "key", ["home"])
        mock_key.assert_called_once_with("AAA-111", "home")
        self.assertEqual(result["status"], "key_pressed")

    @patch("simemu.session.android.key")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_key_android(self, mock_serial, mock_key) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "key", ["back"])
        mock_key.assert_called_once_with("Pixel_7", "back")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_key_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "key", [])


# ── appearance ───────────────────────────────────────────────────────────────


class TestDoAppearance(DoCommandBase):
    @patch("simemu.session.ios.set_appearance")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_appearance(self, mock_serial, mock_appear) -> None:
        result = do_command("s-test01", "appearance", ["dark"])
        mock_appear.assert_called_once_with("AAA-111", "dark")
        self.assertEqual(result["status"], "set")

    @patch("simemu.session.android.set_appearance")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_appearance_android(self, mock_serial, mock_appear) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "appearance", ["light"])
        mock_appear.assert_called_once_with("Pixel_7", "light")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_appearance_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "appearance", [])


# ── rotate ───────────────────────────────────────────────────────────────────


class TestDoRotate(DoCommandBase):
    @patch("simemu.session.ios.rotate")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_rotate(self, mock_serial, mock_rotate) -> None:
        result = do_command("s-test01", "rotate", ["landscape"])
        mock_rotate.assert_called_once_with("AAA-111", "landscape")
        self.assertEqual(result["status"], "rotated")

    @patch("simemu.session.android.rotate")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_rotate_android(self, mock_serial, mock_rotate) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "rotate", ["portrait"])
        mock_rotate.assert_called_once_with("Pixel_7", "portrait")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_rotate_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "rotate", [])


# ── location ─────────────────────────────────────────────────────────────────


class TestDoLocation(DoCommandBase):
    @patch("simemu.session.ios.location")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_location(self, mock_serial, mock_loc) -> None:
        result = do_command("s-test01", "location", ["37.7749", "-122.4194"])
        mock_loc.assert_called_once_with("AAA-111", 37.7749, -122.4194)
        self.assertEqual(result["status"], "set")

    @patch("simemu.session.android.location")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_location_android(self, mock_serial, mock_loc) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "location", ["40.7128", "-74.0060"])
        mock_loc.assert_called_once_with("Pixel_7", 40.7128, -74.0060)

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_location_missing_args(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "location", ["37.7749"])


# ── push / pull (Android only) ───────────────────────────────────────────────


class TestDoPushPull(DoCommandBase):
    @patch("simemu.session.android.push")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_push_android_only(self, mock_serial, mock_push) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "push", ["/local/file.txt", "/sdcard/file.txt"])
        mock_push.assert_called_once_with("Pixel_7", "/local/file.txt", "/sdcard/file.txt")
        self.assertEqual(result["status"], "pushed")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_push_ios_raises(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-test01", "push", ["/local", "/remote"])
        self.assertIn("Android only", str(ctx.exception))

    @patch("simemu.session.android.pull")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_pull_android_only(self, mock_serial, mock_pull) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "pull", ["/sdcard/file.txt", "/local/file.txt"])
        mock_pull.assert_called_once_with("Pixel_7", "/sdcard/file.txt", "/local/file.txt")
        self.assertEqual(result["status"], "pulled")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_pull_ios_raises(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-test01", "pull", ["/remote", "/local"])
        self.assertIn("Android only", str(ctx.exception))

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_push_missing_args(self, mock_serial) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        with self.assertRaises(RuntimeError):
            do_command("s-droid1", "push", ["/local"])

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_pull_missing_args(self, mock_serial) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        with self.assertRaises(RuntimeError):
            do_command("s-droid1", "pull", ["/remote"])


# ── add-media ────────────────────────────────────────────────────────────────


class TestDoAddMedia(DoCommandBase):
    @patch("simemu.session.ios.add_media")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_add_media(self, mock_serial, mock_media) -> None:
        result = do_command("s-test01", "add-media", ["/tmp/photo.jpg"])
        mock_media.assert_called_once_with("AAA-111", "/tmp/photo.jpg")
        self.assertEqual(result["status"], "added")

    @patch("simemu.session.android.add_media")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_add_media_android(self, mock_serial, mock_media) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "add-media", ["/tmp/photo.jpg"])
        mock_media.assert_called_once_with("Pixel_7", "/tmp/photo.jpg")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_add_media_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "add-media", [])


# ── shake ────────────────────────────────────────────────────────────────────


class TestDoShake(DoCommandBase):
    @patch("simemu.session.ios.shake")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_shake(self, mock_serial, mock_shake) -> None:
        result = do_command("s-test01", "shake", [])
        mock_shake.assert_called_once_with("AAA-111")
        self.assertEqual(result["status"], "shaken")

    @patch("simemu.session.android.shake")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_shake_android(self, mock_serial, mock_shake) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "shake", [])
        mock_shake.assert_called_once_with("Pixel_7")


# ── status-bar ───────────────────────────────────────────────────────────────


class TestDoStatusBar(DoCommandBase):
    @patch("simemu.session.ios.status_bar")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_status_bar(self, mock_serial, mock_sb) -> None:
        result = do_command("s-test01", "status-bar", ["--time", "9:41", "--battery", "100"])
        mock_sb.assert_called_once_with("AAA-111", time_str="9:41", battery=100,
                                        wifi=None, network=None)
        self.assertEqual(result["status"], "set")

    @patch("simemu.session.ios.status_bar_clear")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_status_bar_clear(self, mock_serial, mock_clear) -> None:
        result = do_command("s-test01", "status-bar", ["--clear"])
        mock_clear.assert_called_once_with("AAA-111")
        self.assertEqual(result["status"], "cleared")

    @patch("simemu.session.android.status_bar")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_status_bar_android(self, mock_serial, mock_sb) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "status-bar", ["--time", "10:00"])
        mock_sb.assert_called_once_with("Pixel_7", time_str="10:00", battery=None,
                                        wifi=None, network=None)

    @patch("simemu.session.android.status_bar_clear")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_status_bar_clear_android(self, mock_serial, mock_clear) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "status-bar", ["--clear"])
        mock_clear.assert_called_once_with("Pixel_7")


# ── dismiss-alert ────────────────────────────────────────────────────────────


class TestDoDismissAlert(DoCommandBase):
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_dismiss_alert_ios(self, mock_serial, mock_run) -> None:
        result = do_command("s-test01", "dismiss-alert", [])
        self.assertEqual(result["status"], "dismissed")
        # Verify xcrun simctl ui was called
        cmd_args = mock_run.call_args[0][0]
        self.assertIn("simctl", cmd_args)

    @patch("subprocess.run")
    @patch("simemu.session.android.get_serial", return_value="emulator-5554")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_dismiss_alert_android(self, mock_gas, mock_serial, mock_run) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "dismiss-alert", [])
        self.assertEqual(result["status"], "dismissed")


# ── accept-alert ─────────────────────────────────────────────────────────────


class TestDoAcceptAlert(DoCommandBase):
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_accept_alert(self, mock_serial, mock_run) -> None:
        result = do_command("s-test01", "accept-alert", [])
        self.assertEqual(result["status"], "accepted")


# ── deny-alert ───────────────────────────────────────────────────────────────


class TestDoDenyAlert(DoCommandBase):
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_deny_alert(self, mock_serial, mock_run) -> None:
        result = do_command("s-test01", "deny-alert", [])
        self.assertEqual(result["status"], "denied")


# ── grant-all ────────────────────────────────────────────────────────────────


class TestDoGrantAll(DoCommandBase):
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_grant_all_ios(self, mock_serial, mock_run) -> None:
        result = do_command("s-test01", "grant-all", ["com.example.App"])
        self.assertEqual(result["status"], "granted")
        # Should have called xcrun simctl privacy
        cmd_args = mock_run.call_args[0][0]
        self.assertIn("simctl", cmd_args)
        self.assertIn("privacy", cmd_args)

    @patch("subprocess.run")
    @patch("simemu.session.android.get_serial", return_value="emulator-5554")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_grant_all_android(self, mock_gas, mock_serial, mock_run) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "grant-all", ["com.example.app"])
        self.assertEqual(result["status"], "granted")
        # Multiple permissions should have been granted
        self.assertTrue(mock_run.call_count > 1)

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_grant_all_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "grant-all", [])


# ── clear-data ───────────────────────────────────────────────────────────────


class TestDoClearData(DoCommandBase):
    @patch("simemu.session.ios.terminate")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clear_data_ios(self, mock_serial, mock_term) -> None:
        result = do_command("s-test01", "clear-data", ["com.example.App"])
        # iOS falls back to terminate
        mock_term.assert_called_once_with("AAA-111", "com.example.App")
        self.assertEqual(result["status"], "terminated")

    @patch("simemu.session.android.clear_data")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clear_data_android(self, mock_serial, mock_clear) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "clear-data", ["com.example.app"])
        mock_clear.assert_called_once_with("Pixel_7", "com.example.app")
        self.assertEqual(result["status"], "cleared")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clear_data_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "clear-data", [])


# ── clipboard ────────────────────────────────────────────────────────────────


class TestDoClipboard(DoCommandBase):
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clipboard_set_ios(self, mock_serial, mock_run) -> None:
        result = do_command("s-test01", "clipboard-set", ["hello", "world"])
        self.assertEqual(result["status"], "set")
        self.assertEqual(result["text"], "hello world")
        # Should call xcrun simctl pbcopy
        cmd_args = mock_run.call_args[0][0]
        self.assertIn("pbcopy", cmd_args)

    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clipboard_get_ios(self, mock_serial, mock_run) -> None:
        mock_run.return_value = MagicMock(stdout="clipboard content", returncode=0)
        result = do_command("s-test01", "clipboard-get", [])
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["text"], "clipboard content")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clipboard_set_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "clipboard-set", [])

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clipboard_get_android_unsupported(self, mock_serial) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "clipboard-get", [])
        self.assertEqual(result["status"], "unsupported")


if __name__ == "__main__":
    unittest.main()
