"""Tests for simemu.window — window management modes and configuration."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_tmpdir = tempfile.mkdtemp(prefix="simemu-window-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu import window


class TestGetWindowMode(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-win-")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        self.tmpdir.cleanup()

    def test_get_window_mode_default(self) -> None:
        # No config file exists, should return "default"
        mode = window.get_window_mode()
        self.assertEqual(mode, "default")

    def test_get_window_mode_from_config(self) -> None:
        config_path = Path(self.tmpdir.name) / "config.json"
        config_path.write_text(json.dumps({"window_mode": "hidden"}))
        mode = window.get_window_mode()
        self.assertEqual(mode, "hidden")


class TestSetWindowMode(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-win-set-")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        self.tmpdir.cleanup()

    def test_set_window_mode_hidden(self) -> None:
        config = window.set_window_mode("hidden")
        self.assertEqual(config["window_mode"], "hidden")
        # Verify it persists
        self.assertEqual(window.get_window_mode(), "hidden")

    def test_set_window_mode_invalid_raises(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            window.set_window_mode("floating")
        self.assertIn("Invalid window mode", str(ctx.exception))
        self.assertIn("floating", str(ctx.exception))

    def test_set_window_mode_with_display(self) -> None:
        config = window.set_window_mode("display", display=2)
        self.assertEqual(config["window_mode"], "display")
        self.assertEqual(config["window_display"], 2)

    def test_set_window_mode_with_corner(self) -> None:
        config = window.set_window_mode("corner", corner="top-left")
        self.assertEqual(config["window_mode"], "corner")
        self.assertEqual(config["window_corner"], "top-left")

    def test_all_valid_modes_accepted(self) -> None:
        for mode in ("hidden", "space", "corner", "display", "default"):
            config = window.set_window_mode(mode)
            self.assertEqual(config["window_mode"], mode)


class TestConfigPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-win-persist-")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        self.tmpdir.cleanup()

    def test_config_persists_to_file(self) -> None:
        window.set_window_mode("space")
        config_path = Path(self.tmpdir.name) / "config.json"
        self.assertTrue(config_path.exists())
        data = json.loads(config_path.read_text())
        self.assertEqual(data["window_mode"], "space")

    def test_corrupt_config_returns_default(self) -> None:
        config_path = Path(self.tmpdir.name) / "config.json"
        config_path.write_text("not-valid-json{{{")
        mode = window.get_window_mode()
        self.assertEqual(mode, "default")


class TestListDisplays(unittest.TestCase):
    @patch("simemu.window.subprocess.check_output")
    def test_list_displays_returns_at_least_one(self, mock_co: MagicMock) -> None:
        mock_co.return_value = b"Built-in Retina,0,0,2560,1440\n"
        displays = window.list_displays()
        self.assertGreaterEqual(len(displays), 1)
        self.assertTrue(displays[0]["is_main"])
        self.assertEqual(displays[0]["index"], 1)

    @patch("simemu.window.subprocess.check_output", side_effect=Exception("no osascript"))
    def test_list_displays_fallback(self, mock_co: MagicMock) -> None:
        displays = window.list_displays()
        self.assertEqual(len(displays), 1)
        self.assertEqual(displays[0]["name"], "Main")
        self.assertTrue(displays[0]["is_main"])

    @patch("simemu.window.subprocess.check_output")
    def test_list_displays_multiple(self, mock_co: MagicMock) -> None:
        mock_co.return_value = b"Built-in,0,0,2560,1440\nExternal,2560,0,3840,2160\n"
        displays = window.list_displays()
        self.assertEqual(len(displays), 2)
        self.assertTrue(displays[0]["is_main"])
        self.assertFalse(displays[1]["is_main"])


class TestApplyWindowMode(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-win-apply-")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        self.tmpdir.cleanup()

    @patch("simemu.window.time.sleep")
    def test_apply_window_mode_default_is_noop(self, mock_sleep) -> None:
        # Default mode should return without doing anything
        window.apply_window_mode("AAA-111", "ios", "iPhone 16 Pro")
        mock_sleep.assert_not_called()

    @patch("simemu.window.subprocess.run")
    @patch("simemu.window.time.sleep")
    def test_apply_window_mode_hidden(self, mock_sleep, mock_run) -> None:
        window.set_window_mode("hidden")
        mock_run.return_value = MagicMock(stdout="ok", returncode=0)
        window.apply_window_mode("AAA-111", "ios", "iPhone 16 Pro")
        # Should have called osascript to minimize
        self.assertTrue(mock_run.called)

    def test_apply_window_mode_android_hidden_no_crash(self) -> None:
        window.set_window_mode("hidden")
        # Should not crash for android platform
        with patch("simemu.window.time.sleep"):
            with patch("simemu.window.subprocess.run"):
                window.apply_window_mode("Pixel_7", "android", "Pixel 7")


if __name__ == "__main__":
    unittest.main()
