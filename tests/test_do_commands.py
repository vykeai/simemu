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
    @patch("simemu.discover.list_real_ios")
    def test_do_install_real_ios(self, mock_list_real_ios, mock_serial, mock_install) -> None:
        self._seed("s-real01", platform="ios", sim_id="UDID-REAL",
                    device_name="iPhone 15 (real)", real_device=True)
        mock_device = MagicMock()
        mock_device.sim_id = "UDID-REAL"
        mock_list_real_ios.return_value = [mock_device]
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
    @patch("simemu.session.android.foreground_app", return_value="com.example.app")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_launch_android(self, mock_serial, mock_fg, mock_launch) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "launch", ["com.example.app/.MainActivity"])
        mock_launch.assert_called_once_with("Pixel_7", "com.example.app/.MainActivity", [])
        self.assertEqual(result["status"], "launched")

    @patch("simemu.session.android.launch")
    @patch("simemu.session.android.foreground_app", return_value="com.other.app")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_launch_android_warns_on_foreground_mismatch(self, mock_serial, mock_fg, mock_launch) -> None:
        """Launch succeeds but emits diagnostic when wrong app is foreground."""
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "launch", ["com.example.app"])
        self.assertEqual(result["status"], "launched")
        # Diagnostic emitted to stderr — we just verify launch still completes

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
        mock_screenshot.assert_called_once_with("AAA-111", "/tmp/test.png", fmt=None, max_size=None)
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
        mock_screenshot.assert_called_once_with("Pixel_7", "/tmp/droid.png", max_size=None)


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
    @patch("simemu.session.ios.complete_open_url_handoff", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url(self, mock_serial, mock_complete, mock_url) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        result = do_command("s-test01", "url", ["https://example.com"])
        mock_url.assert_called_once_with("AAA-111", "https://example.com")
        mock_complete.assert_called_once_with("AAA-111", "app.fitkind.dev")
        self.assertEqual(result["status"], "opened")

    @patch("simemu.session.android.open_url")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_android(self, mock_serial, mock_url) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        result = do_command("s-droid1", "url", ["https://example.com"])
        mock_url.assert_called_once_with("Pixel_7", "https://example.com", expected_package=None)

    @patch("simemu.session.android.open_url")
    @patch("simemu.session.android.foreground_app", return_value="app.fitkind.dev")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_android_uses_last_launched_app_for_verification(self, mock_serial, mock_fg, mock_url) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7",
                    device_name="Pixel 7")
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-droid1"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        result = do_command("s-droid1", "url", ["fitkind://debug/route"])
        mock_url.assert_called_once_with("Pixel_7", "fitkind://debug/route", expected_package="app.fitkind.dev")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_missing_arg(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "url", [])

    @patch("simemu.session.android.open_url")
    @patch("simemu.session.android.foreground_app", return_value="com.chrome.browser")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_android_raises_when_wrong_app_foreground(self, mock_serial, mock_fg, mock_url) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-droid1"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError, "not foreground on Android"):
            do_command("s-droid1", "url", ["fitkind://debug/route"])

    @patch("simemu.session.android.open_url")
    @patch("simemu.session.android.foreground_app", return_value="app.fitkind.dev")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_android_passes_when_correct_app_foreground(self, mock_serial, mock_fg, mock_url) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-droid1"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        result = do_command("s-droid1", "url", ["fitkind://debug/route"])
        self.assertEqual(result["status"], "opened")

    @patch("simemu.session.ios.open_url")
    @patch("simemu.session.ios.complete_open_url_handoff", return_value=False)
    @patch("simemu.session.ios.foreground_app", return_value=None)
    @patch("simemu.session.ios.is_app_running", return_value=False)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_ios_raises_springboard_diagnostic(
        self, mock_serial, mock_running, mock_fg, mock_complete, mock_url
    ) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError, "never launched"):
            do_command("s-test01", "url", ["fitkind://debug/route"])

    @patch("simemu.session.ios.open_url")
    @patch("simemu.session.ios.complete_open_url_handoff", return_value=False)
    @patch("simemu.session.ios.foreground_app", return_value=None)
    @patch("simemu.session.ios.is_app_running", return_value=True)
    @patch("simemu.session.ios.accept_open_app_alert", return_value=False)
    @patch("simemu.session.ios.wait_for_foreground_app", return_value=False)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_url_ios_raises_stuck_behind_sheet_diagnostic(
        self, mock_serial, mock_wait, mock_accept, mock_running, mock_fg, mock_complete, mock_url
    ) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError, "stuck behind"):
            do_command("s-test01", "url", ["fitkind://debug/route"])


class TestDoDeepLinkProof(DoCommandBase):
    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.ios.complete_open_url_handoff", return_value=True)
    @patch("simemu.session.ios.open_url")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_deeplink_proof_ios_accepts_alert(
        self, mock_serial, mock_open_url, mock_complete_handoff, mock_screenshot
    ) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        with patch("time.sleep"):
            result = do_command(
                "s-test01",
                "deeplink-proof",
                ["fitkind://debug/route", "-o", "/tmp/proof.png"],
            )
        mock_open_url.assert_called_once_with("AAA-111", "fitkind://debug/route")
        mock_complete_handoff.assert_called_once_with("AAA-111", "app.fitkind.dev")
        mock_screenshot.assert_called_once_with("AAA-111", "/tmp/proof.png")
        self.assertEqual("captured", result["status"])


class TestDoForegroundApp(DoCommandBase):
    @patch("simemu.session.ios.foreground_app", return_value="app.fitkind.dev")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_foreground_app_ios_uses_ios_helper(self, mock_serial, mock_foreground) -> None:
        result = do_command("s-test01", "foreground-app", [])
        mock_foreground.assert_called_once_with("AAA-111")
        self.assertEqual("app.fitkind.dev", result["foreground_app"])

    @patch("simemu.session.android.foreground_app", return_value="app.fitkind.dev")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_foreground_app_android_uses_android_helper(self, mock_serial, mock_foreground) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        result = do_command("s-droid1", "foreground-app", [])
        mock_foreground.assert_called_once_with("Pixel_7")
        self.assertEqual("app.fitkind.dev", result["foreground_app"])


class TestDoPresentAndStabilize(DoCommandBase):
    @patch("simemu.session.ios.present", return_value={"stable": True})
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_present_ios_marks_session_visible(self, mock_serial, mock_present) -> None:
        result = do_command("s-test01", "present", [])
        mock_present.assert_called_once_with("AAA-111")
        self.assertEqual(True, result["stable"])
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        self.assertTrue(data["sessions"]["s-test01"]["visible"])

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_present_android_is_unsupported(self, mock_serial) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        result = do_command("s-droid1", "present", [])
        self.assertEqual("unsupported", result["status"])

    @patch("simemu.session.ios.stabilize", return_value={"stable": True, "udid": "AAA-111"})
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_stabilize_ios(self, mock_serial, mock_stabilize) -> None:
        result = do_command("s-test01", "stabilize", [])
        mock_stabilize.assert_called_once_with("AAA-111")
        self.assertTrue(result["stable"])


class TestDoVerifyInstall(DoCommandBase):
    @patch("simemu.session.android.verify_install")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_verify_install_android(self, mock_serial, mock_verify) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        mock_verify.return_value = MagicMock(format_report=lambda: "pm path:\npackage:/data/app")
        result = do_command("s-droid1", "verify-install", ["app.sitches.dev"])
        mock_verify.assert_called_once_with("Pixel_7", "app.sitches.dev")
        self.assertEqual("verified", result["status"])

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_verify_install_ios_rejected(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError):
            do_command("s-test01", "verify-install", ["com.example.App"])


class TestDoRepairInstall(DoCommandBase):
    @patch("simemu.session.android.repair_install")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_repair_install_android(self, mock_serial, mock_repair) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        mock_repair.return_value = MagicMock(format_report=lambda: "pm path:\npackage:/data/app")
        result = do_command("s-droid1", "repair-install", ["app.sitches.dev", "/tmp/app.apk"])
        mock_repair.assert_called_once_with("Pixel_7", "app.sitches.dev", "/tmp/app.apk")
        self.assertEqual("repaired", result["status"])


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
    @patch("simemu.session.ios.click_system_alert_button")
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_dismiss_alert_ios(self, mock_serial, mock_run, mock_click) -> None:
        result = do_command("s-test01", "dismiss-alert", [])
        self.assertEqual(result["status"], "dismissed")
        # Verify xcrun simctl ui was called
        cmd_args = mock_run.call_args[0][0]
        self.assertIn("simctl", cmd_args)
        mock_click.assert_called_once()

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
    @patch("simemu.session.ios.accept_open_app_alert")
    @patch("simemu.session.ios.complete_open_url_handoff", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_accept_alert(self, mock_serial, mock_complete, mock_accept) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "app.fitkind.dev"
        sf.write_text(json.dumps(data))
        result = do_command("s-test01", "accept-alert", [])
        mock_accept.assert_called_once_with("AAA-111", attempts=2, delay=0.35)
        mock_complete.assert_called_once_with("AAA-111", "app.fitkind.dev", attempts=3, foreground_timeout=1.0)
        self.assertEqual(result["status"], "accepted")


# ── deny-alert ───────────────────────────────────────────────────────────────


class TestDoDenyAlert(DoCommandBase):
    @patch("simemu.session.ios.click_system_alert_button")
    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_deny_alert(self, mock_serial, mock_run, mock_click) -> None:
        result = do_command("s-test01", "deny-alert", [])
        self.assertEqual(result["status"], "denied")
        mock_click.assert_called_once()


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


class TestDoCleanRetry(DoCommandBase):
    @patch("simemu.session.android.launch")
    @patch("simemu.session.android.clear_data")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clean_retry_android(self, mock_serial, mock_clear, mock_launch) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        result = do_command("s-droid1", "clean-retry", ["com.example.app"])
        mock_clear.assert_called_once_with("Pixel_7", "com.example.app")
        mock_launch.assert_called_once_with("Pixel_7", "com.example.app", [])
        self.assertEqual(result["status"], "clean_retried")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_clean_retry_ios_rejected(self, mock_serial) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-test01", "clean-retry", ["com.example.App"])
        self.assertIn("Android only", str(ctx.exception))


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


# ── provenance ──────────────────────────────────────────────────────────────


class TestProvenance(DoCommandBase):
    @patch("simemu.session.ios.launch")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_launch_records_provenance(self, mock_serial, mock_launch) -> None:
        from simemu.session import get_provenance
        do_command("s-test01", "launch", ["com.example.app"])
        prov = get_provenance("s-test01")
        self.assertEqual(prov["last_app"], "com.example.app")
        self.assertIn("updated_at", prov)

    @patch("simemu.session.ios.open_url")
    @patch("simemu.session.ios.complete_open_url_handoff", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_url_records_provenance(self, mock_serial, mock_complete, mock_url) -> None:
        from simemu.session import get_provenance
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "com.example.app"
        sf.write_text(json.dumps(data))
        do_command("s-test01", "url", ["myapp://deep/link"])
        prov = get_provenance("s-test01")
        self.assertEqual(prov["last_url"], "myapp://deep/link")
        self.assertEqual(prov["last_deep_link"], "myapp://deep/link")

    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_screenshot_records_provenance(self, mock_serial, mock_ss) -> None:
        from simemu.session import get_provenance
        result = do_command("s-test01", "screenshot", ["-o", "/tmp/proof.png"])
        prov = get_provenance("s-test01")
        self.assertEqual(prov["last_screenshot"], "/tmp/proof.png")

    def test_get_provenance_empty_session(self) -> None:
        from simemu.session import get_provenance
        prov = get_provenance("s-test01")
        self.assertEqual(prov, {})

    def test_get_provenance_nonexistent_session(self) -> None:
        from simemu.session import get_provenance
        prov = get_provenance("s-nonexistent")
        self.assertEqual(prov, {})

    @patch("simemu.session.ios.launch")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_provenance_survives_multiple_updates(self, mock_serial, mock_launch) -> None:
        from simemu.session import get_provenance, update_provenance
        do_command("s-test01", "launch", ["com.first.app"])
        update_provenance("s-test01", custom_field="custom_value")
        prov = get_provenance("s-test01")
        self.assertEqual(prov["last_app"], "com.first.app")
        self.assertEqual(prov["custom_field"], "custom_value")


# ── build ───────────────────────────────────────────────────────────────────


class TestDoBuild(DoCommandBase):
    def _write_execution_yaml(self, content: str) -> None:
        keel_dir = Path.cwd() / "keel"
        keel_dir.mkdir(parents=True, exist_ok=True)
        (keel_dir / "execution.yaml").write_text(content)

    def _cleanup_execution_yaml(self) -> None:
        f = Path.cwd() / "keel" / "execution.yaml"
        if f.exists():
            f.unlink()

    def tearDown(self) -> None:
        self._cleanup_execution_yaml()
        super().tearDown()

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_raw_mode(self, mock_serial) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
            result = do_command("s-test01", "build", ["--raw", "echo hello"])
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["mode"], "raw")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_no_config_raises(self, mock_serial) -> None:
        self._cleanup_execution_yaml()
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-test01", "build", [])
        self.assertIn("buildVariants", str(ctx.exception))

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_ios_variant(self, mock_serial) -> None:
        self._write_execution_yaml("""buildVariants:
  mock:
    ios:
      scheme: TestApp-mock
      project: TestApp.xcodeproj
      configuration: Debug
""")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="BUILD SUCCEEDED", stderr="")
            with patch("simemu.session._find_ios_artifact", return_value=Path("/tmp/TestApp.app")):
                result = do_command("s-test01", "build", ["--variant", "mock"])
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["variant"], "mock")
        self.assertEqual(result["scheme"], "TestApp-mock")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_android_variant(self, mock_serial) -> None:
        self._seed("s-android01", platform="android", sim_id="Pixel_8_API35", device_name="Pixel 8")
        self._write_execution_yaml("""buildVariants:
  dev:
    android:
      task: assembleDevDebug
""")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="BUILD SUCCESSFUL", stderr="")
            with patch("simemu.session._find_android_artifact", return_value=Path("/tmp/app-dev-debug.apk")):
                result = do_command("s-android01", "build", ["--variant", "dev"])
        self.assertEqual(result["status"], "built")
        self.assertEqual(result["platform"], "android")
        self.assertEqual(result["variant"], "dev")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_default_variant(self, mock_serial) -> None:
        self._write_execution_yaml("""buildVariants:
  mock:
    ios:
      scheme: TestApp
      configuration: Debug
  release:
    ios:
      scheme: TestApp
      configuration: Release
""")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="BUILD SUCCEEDED", stderr="")
            with patch("simemu.session._find_ios_artifact", return_value=None):
                result = do_command("s-test01", "build", [])
        # Should use first variant (mock) as default
        self.assertEqual(result["variant"], "mock")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_unknown_variant_raises(self, mock_serial) -> None:
        self._write_execution_yaml("""buildVariants:
  mock:
    ios:
      scheme: TestApp
""")
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-test01", "build", ["--variant", "nonexistent"])
        self.assertIn("Unknown variant", str(ctx.exception))

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_build_failure_includes_command(self, mock_serial) -> None:
        self._write_execution_yaml("""buildVariants:
  mock:
    ios:
      scheme: TestApp
      project: TestApp.xcodeproj
      configuration: Debug
""")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=65, stdout="", stderr="Build failed: no such scheme")
            with self.assertRaises(RuntimeError) as ctx:
                do_command("s-test01", "build", ["--variant", "mock"])
        self.assertIn("iOS build failed", str(ctx.exception))
        self.assertIn("xcodebuild", str(ctx.exception))


# ── proof ───────────────────────────────────────────────────────────────────


class TestDoProof(DoCommandBase):
    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.ios.status_bar")
    @patch("simemu.session.ios.foreground_app", return_value="com.example.app")
    @patch("simemu.session.ios.accept_open_app_alert", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_proof_ios_normalizes_and_captures(self, mock_serial, mock_alert,
                                                mock_fg, mock_status, mock_ss) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "com.example.app"
        sf.write_text(json.dumps(data))
        result = do_command("s-test01", "proof", ["-o", "/tmp/proof.png", "--wait", "0.1"])
        self.assertEqual(result["status"], "proved")
        self.assertEqual(result["path"], "/tmp/proof.png")
        self.assertIn("status_bar:9:41", result["steps"])
        self.assertIn("dismiss_alerts", result["steps"])
        mock_ss.assert_called_once()
        mock_status.assert_called_once()

    @patch("simemu.session.android.screenshot")
    @patch("simemu.session.android.foreground_app", return_value="com.example.app")
    @patch("simemu.session.android.dismiss_system_dialogs", return_value=False)
    @patch("simemu.session.android.stop_other_apps", return_value=["ai.vivii.dev"])
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_proof_android_isolates_and_captures(self, mock_serial, mock_stop,
                                                  mock_dismiss, mock_fg, mock_ss) -> None:
        self._seed("s-droid1", platform="android", sim_id="Pixel_7", device_name="Pixel 7")
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-droid1"]["last_app"] = "com.example.app"
        sf.write_text(json.dumps(data))
        result = do_command("s-droid1", "proof", ["-o", "/tmp/proof.png", "--wait", "0.1"])
        self.assertEqual(result["status"], "proved")
        mock_stop.assert_called_once()
        self.assertIn("isolate:com.example.app", result["steps"])

    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.ios.status_bar")
    @patch("simemu.session.ios.foreground_app", return_value="com.wrong.app")
    @patch("simemu.session.ios.accept_open_app_alert", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_proof_fails_on_foreground_mismatch(self, mock_serial, mock_alert,
                                                 mock_fg, mock_status, mock_ss) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = json.loads(sf.read_text())
        data["sessions"]["s-test01"]["last_app"] = "com.expected.app"
        sf.write_text(json.dumps(data))
        with self.assertRaisesRegex(RuntimeError, "not trustworthy"):
            do_command("s-test01", "proof", ["-o", "/tmp/proof.png", "--wait", "0.1"])
        mock_ss.assert_not_called()  # screenshot never taken

    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.ios.status_bar")
    @patch("simemu.session.ios.set_appearance")
    @patch("simemu.session.ios.foreground_app", return_value=None)
    @patch("simemu.session.ios.accept_open_app_alert", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_proof_with_appearance_flag(self, mock_serial, mock_alert, mock_fg,
                                        mock_appear, mock_status, mock_ss) -> None:
        result = do_command("s-test01", "proof", ["--appearance", "dark", "--wait", "0.1", "-o", "/tmp/p.png"])
        self.assertEqual(result["status"], "proved")
        mock_appear.assert_called_once_with("AAA-111", "dark")
        self.assertIn("appearance:dark", result["steps"])

    @patch("simemu.session.ios.screenshot")
    @patch("simemu.session.ios.status_bar")
    @patch("simemu.session.ios.foreground_app", return_value=None)
    @patch("simemu.session.ios.accept_open_app_alert", return_value=True)
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_proof_stores_provenance(self, mock_serial, mock_alert, mock_fg,
                                      mock_status, mock_ss) -> None:
        from simemu.session import get_provenance
        result = do_command("s-test01", "proof", ["-o", "/tmp/proof.png", "--wait", "0.1", "--label", "test"])
        prov = get_provenance("s-test01")
        self.assertEqual(prov["last_screenshot"], "/tmp/proof.png")
        self.assertIn("last_proof", prov)
        self.assertEqual(prov["last_proof"]["label"], "test")


class TestParseVariants(unittest.TestCase):
    def test_parse_simple_yaml(self) -> None:
        from simemu.session import _parse_build_variants
        yaml = """buildVariants:
  mock:
    ios:
      scheme: MyApp-mock
      configuration: Debug
    android:
      task: assembleLocalDebug
  release:
    ios:
      scheme: MyApp
      configuration: Release
"""
        result = _parse_build_variants(yaml)
        self.assertIsNotNone(result)
        self.assertIn("mock", result)
        self.assertIn("release", result)
        self.assertEqual(result["mock"]["ios"]["scheme"], "MyApp-mock")
        self.assertEqual(result["mock"]["android"]["task"], "assembleLocalDebug")
        self.assertEqual(result["release"]["ios"]["configuration"], "Release")

    def test_parse_empty_returns_none(self) -> None:
        from simemu.session import _parse_build_variants
        result = _parse_build_variants("nothing: here")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
