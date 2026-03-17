import sys
import time
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
