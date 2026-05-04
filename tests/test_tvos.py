"""Tests for tvOS support — Siri Remote, focus navigation, screenshot, discovery."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_tmpdir = tempfile.mkdtemp(prefix="simemu-tvos-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu import ios
from simemu.discover import _classify_form_factor, SimulatorInfo
from simemu.claim_policy import resolve_alias
from simemu.schema import validate, SESSION_SCHEMA
from simemu.session import do_command, _write_sessions_raw, _now_iso, _compute_expires_at


class TestTvOSKeyMappings(unittest.TestCase):
    def test_all_remote_keys_exist(self) -> None:
        for key_name in ("remote-up", "remote-down", "remote-left", "remote-right",
                         "remote-select", "remote-menu", "remote-play-pause"):
            self.assertIn(key_name, ios._IOS_KEYS, f"Missing key: {key_name}")

    def test_remote_keys_have_valid_vk(self) -> None:
        for key_name in ("remote-up", "remote-down", "remote-left", "remote-right",
                         "remote-select", "remote-menu", "remote-play-pause"):
            vk, modifiers, desc = ios._IOS_KEYS[key_name]
            self.assertIsInstance(vk, int)
            self.assertIsInstance(modifiers, tuple)
            self.assertTrue(desc)


class TestTvOSDiscovery(unittest.TestCase):
    def test_classify_apple_tv(self) -> None:
        sim = SimulatorInfo("A", "tvos", "Apple TV 4K (3rd generation)", True, "tvOS 18.2")
        self.assertEqual(_classify_form_factor(sim), "tv")

    def test_classify_apple_tv_lower(self) -> None:
        sim = SimulatorInfo("B", "tvos", "appletv HD", True, "tvOS 17.0")
        self.assertEqual(_classify_form_factor(sim), "tv")

    def test_classify_apple_watch(self) -> None:
        sim = SimulatorInfo("C", "watchos", "Apple Watch Ultra 2", True, "watchOS 11.0")
        self.assertEqual(_classify_form_factor(sim), "watch")

    def test_classify_vision_pro(self) -> None:
        sim = SimulatorInfo("D", "visionos", "Apple Vision Pro", True, "visionOS 2.0")
        self.assertEqual(_classify_form_factor(sim), "vision")


class TestTvOSAliases(unittest.TestCase):
    def test_tv_alias(self) -> None:
        result = resolve_alias("tv")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["form_factor"], "tv")

    def test_appletv_alias(self) -> None:
        result = resolve_alias("appletv")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["form_factor"], "tv")

    def test_apple_tv_alias(self) -> None:
        result = resolve_alias("apple-tv")
        self.assertEqual(result["platform"], "ios")
        self.assertEqual(result["form_factor"], "tv")


class TestTvOSSchema(unittest.TestCase):
    def test_tvos_session_validates(self) -> None:
        data = {
            "session": "s-tv0001",
            "platform": "tvos",
            "form_factor": "tv",
            "status": "active",
        }
        errors = validate(data, SESSION_SCHEMA)
        self.assertEqual(errors, [])


class TestTvOSCommands(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-tvos-cmd-")
        self._old = os.environ.get("SIMEMU_STATE_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old:
            os.environ["SIMEMU_STATE_DIR"] = self._old
        else:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        self.tmpdir.cleanup()

    def _seed_tvos(self, sid: str = "s-tv0001") -> None:
        now = _now_iso()
        _write_sessions_raw({"sessions": {
            sid: {
                "session_id": sid, "platform": "tvos", "form_factor": "tv",
                "os_version": None, "real_device": False, "label": "",
                "status": "active", "sim_id": "TV-UUID-001",
                "device_name": "Apple TV 4K (3rd generation)",
                "agent": "test", "created_at": now, "heartbeat_at": now,
                "expires_at": _compute_expires_at("active", now),
                "resolved_os_version": "tvOS 18.2", "pinned_serial": None,
                "claim_platform": "tvos", "claim_form_factor": "tv",
                "claim_os_version": None, "claim_real_device": False, "claim_label": "",
            }
        }})

    @patch("simemu.session.ios.focus_move")
    @patch("simemu.session.ios._ensure_booted")
    def test_focus_move_calls_ios(self, mock_boot, mock_focus) -> None:
        self._seed_tvos()
        result = do_command("s-tv0001", "focus-move", ["down"])
        self.assertEqual(result["status"], "moved")
        self.assertEqual(result["direction"], "down")
        mock_focus.assert_called_once_with("TV-UUID-001", "down")

    @patch("simemu.session.ios.focus_select")
    @patch("simemu.session.ios._ensure_booted")
    def test_focus_select_calls_ios(self, mock_boot, mock_select) -> None:
        self._seed_tvos()
        result = do_command("s-tv0001", "focus-select", [])
        self.assertEqual(result["status"], "selected")
        mock_select.assert_called_once_with("TV-UUID-001")

    @patch("simemu.session.ios.remote_button")
    @patch("simemu.session.ios._ensure_booted")
    def test_remote_button(self, mock_boot, mock_remote) -> None:
        self._seed_tvos()
        result = do_command("s-tv0001", "remote", ["menu"])
        self.assertEqual(result["status"], "pressed")
        self.assertEqual(result["button"], "menu")
        mock_remote.assert_called_once_with("TV-UUID-001", "menu")

    @patch("simemu.session.ios._ensure_booted")
    def test_tap_rejected_on_tvos(self, mock_boot) -> None:
        self._seed_tvos()
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-tv0001", "tap", ["100", "200"])
        self.assertIn("focus navigation", str(ctx.exception))

    @patch("simemu.session.ios.remote_button")
    @patch("simemu.session.ios._ensure_booted")
    def test_back_sends_menu(self, mock_boot, mock_remote) -> None:
        self._seed_tvos()
        result = do_command("s-tv0001", "back", [])
        self.assertEqual(result["method"], "remote_menu")

    @patch("simemu.session.ios.tvos_screenshot")
    @patch("simemu.session.ios._ensure_booted")
    def test_screenshot_uses_tvos_function(self, mock_boot, mock_ss) -> None:
        self._seed_tvos()
        result = do_command("s-tv0001", "screenshot", ["-o", "/tmp/tv.png"])
        mock_ss.assert_called_once()
        self.assertEqual(result["status"], "captured")


class TestTvOSDeviceSize(unittest.TestCase):
    def test_apple_tv_logical_size(self) -> None:
        size = ios._get_device_logical_size("Apple TV 4K (3rd generation)")
        # Should match AppleTV entry, not fall back to iPhone
        self.assertEqual(size, (1920, 1080))


if __name__ == "__main__":
    unittest.main()
