"""Tests for simemu.visibility — live window state reconciliation."""

import unittest
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from simemu.visibility import (
    get_session_visibility,
    is_simulator_window_visible,
    is_emulator_window_visible,
    get_visibility_summary,
)


def _mock_session(platform="ios", status="active", device_name="iPhone 17 Pro",
                  sim_id="AAA-111"):
    return SimpleNamespace(
        session_id="s-test01",
        platform=platform,
        status=status,
        device_name=device_name,
        sim_id=sim_id,
    )


class TestIsSimulatorWindowVisible(unittest.TestCase):
    @patch("simemu.visibility._get_all_windows")
    def test_visible_simulator(self, mock_windows) -> None:
        mock_windows.return_value = [
            {"owner": "Simulator", "name": "iPhone 17 Pro – iOS 26.1",
             "onscreen": True, "layer": 0, "width": 440, "height": 956,
             "x": 100, "y": 50, "alpha": 1.0},
        ]
        self.assertTrue(is_simulator_window_visible("iPhone 17 Pro"))

    @patch("simemu.visibility._get_all_windows")
    def test_hidden_simulator(self, mock_windows) -> None:
        mock_windows.return_value = [
            {"owner": "Simulator", "name": "iPhone 17 Pro – iOS 26.1",
             "onscreen": False, "layer": 0, "width": 440, "height": 956,
             "x": 100, "y": 50, "alpha": 1.0},
        ]
        self.assertFalse(is_simulator_window_visible("iPhone 17 Pro"))

    @patch("simemu.visibility._get_all_windows")
    def test_no_window_found(self, mock_windows) -> None:
        mock_windows.return_value = [
            {"owner": "Finder", "name": "Desktop", "onscreen": True,
             "layer": 0, "width": 1920, "height": 1080, "x": 0, "y": 0, "alpha": 1.0},
        ]
        self.assertIsNone(is_simulator_window_visible("iPhone 17 Pro"))

    @patch("simemu.visibility._get_all_windows")
    def test_zero_alpha_counts_as_hidden(self, mock_windows) -> None:
        mock_windows.return_value = [
            {"owner": "Simulator", "name": "iPhone 17 Pro",
             "onscreen": True, "layer": 0, "width": 440, "height": 956,
             "x": 100, "y": 50, "alpha": 0.0},
        ]
        self.assertFalse(is_simulator_window_visible("iPhone 17 Pro"))


class TestIsEmulatorWindowVisible(unittest.TestCase):
    @patch("simemu.visibility._get_all_windows")
    def test_visible_emulator(self, mock_windows) -> None:
        mock_windows.return_value = [
            {"owner": "qemu-system-aarch64", "name": "Pixel_8_API_35",
             "onscreen": True, "layer": 0, "width": 1080, "height": 2400,
             "x": 200, "y": 100, "alpha": 1.0},
        ]
        self.assertTrue(is_emulator_window_visible("Pixel_8_API_35"))

    @patch("simemu.visibility._get_all_windows")
    def test_headless_emulator_no_window(self, mock_windows) -> None:
        mock_windows.return_value = []
        self.assertIsNone(is_emulator_window_visible("Pixel_8_API_35"))


class TestGetSessionVisibility(unittest.TestCase):
    @patch("simemu.visibility.is_simulator_window_visible", return_value=True)
    def test_active_visible_ios(self, mock_vis) -> None:
        s = _mock_session(platform="ios", status="active")
        self.assertEqual(get_session_visibility(s), "visible")

    @patch("simemu.visibility.is_simulator_window_visible", return_value=False)
    def test_active_hidden_ios(self, mock_vis) -> None:
        s = _mock_session(platform="ios", status="active")
        self.assertEqual(get_session_visibility(s), "hidden")

    @patch("simemu.visibility.is_simulator_window_visible", return_value=None)
    def test_active_no_window_ios(self, mock_vis) -> None:
        s = _mock_session(platform="ios", status="active")
        self.assertEqual(get_session_visibility(s), "no_window")

    def test_parked_always_parked(self) -> None:
        s = _mock_session(status="parked")
        self.assertEqual(get_session_visibility(s), "parked")

    def test_expired_always_no_window(self) -> None:
        s = _mock_session(status="expired")
        self.assertEqual(get_session_visibility(s), "no_window")

    def test_macos_always_visible(self) -> None:
        s = _mock_session(platform="macos", status="active")
        self.assertEqual(get_session_visibility(s), "visible")

    @patch("simemu.visibility.is_emulator_window_visible", return_value=True)
    def test_android_visible(self, mock_vis) -> None:
        s = _mock_session(platform="android", status="active", sim_id="Pixel_8")
        self.assertEqual(get_session_visibility(s), "visible")

    @patch("simemu.visibility.is_emulator_window_visible", return_value=None)
    def test_android_headless(self, mock_vis) -> None:
        s = _mock_session(platform="android", status="idle", sim_id="Pixel_8")
        self.assertEqual(get_session_visibility(s), "no_window")


if __name__ == "__main__":
    unittest.main()
