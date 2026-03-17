import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import ios


class IOSControlTests(unittest.TestCase):
    def setUp(self) -> None:
        ios._reset_interaction_control()

    def test_stop_signal_sets_stop_flag(self) -> None:
        ios._handle_stop_signal(None, None)
        with self.assertRaisesRegex(RuntimeError, "stopped by user"):
            ios._check_interaction_control()

    def test_pause_signal_toggles_pause_flag(self) -> None:
        ios._handle_pause_signal(None, None)
        self.assertTrue(ios._PAUSE_REQUESTED)
        ios._handle_pause_signal(None, None)
        self.assertFalse(ios._PAUSE_REQUESTED)

    def test_check_interaction_control_waits_until_pause_cleared(self) -> None:
        ios._handle_pause_signal(None, None)

        def clear_pause():
            time.sleep(0.1)
            ios._handle_pause_signal(None, None)

        import threading
        thread = threading.Thread(target=clear_pause)
        thread.start()
        ios._check_interaction_control()
        thread.join()
        self.assertFalse(ios._PAUSE_REQUESTED)

    def test_display_for_frame_returns_none_when_quartz_unavailable(self) -> None:
        with patch("importlib.import_module", side_effect=RuntimeError("no quartz")):
            self.assertIsNone(ios._display_for_frame(0, 0, 100, 100))

    def test_window_visibility_state_returns_onscreen_metadata(self) -> None:
        class FakeQuartz:
            kCGWindowListOptionAll = 1
            kCGNullWindowID = 0

            @staticmethod
            def CGWindowListCopyWindowInfo(_opt, _wid):
                return [
                    {
                        "kCGWindowOwnerName": "Simulator",
                        "kCGWindowName": "sitches iPhone 16 Pro Max",
                        "kCGWindowIsOnscreen": 1,
                        "kCGWindowLayer": 0,
                        "kCGWindowAlpha": 1.0,
                    }
                ]

        real_import_module = __import__("importlib").import_module

        def fake_import_module(name):
            if name == "Quartz":
                return FakeQuartz
            return real_import_module(name)

        with patch("simemu.ios._get_device_name", return_value="sitches iPhone 16 Pro Max"):
            with patch("importlib.import_module", side_effect=fake_import_module):
                state = ios._window_visibility_state("SIM-001")

        self.assertEqual(True, state["onscreen"])
        self.assertEqual(0, state["layer"])

    def test_window_visibility_state_returns_none_when_quartz_unavailable(self) -> None:
        with patch("importlib.import_module", side_effect=RuntimeError("no quartz")):
            self.assertIsNone(ios._window_visibility_state("SIM-001"))


if __name__ == "__main__":
    unittest.main()
