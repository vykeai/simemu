import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import state


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-state-test-")
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

    def test_legacy_acquire_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "discontinued"):
            state.acquire("slug", "SIM-001", "ios", "iPhone", "agent")

    def test_legacy_release_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "discontinued"):
            state.release("slug")

    def test_legacy_touch_raises(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "discontinued"):
            state.touch("slug")

    def test_presentation_layout_round_trip(self) -> None:
        layout = {"x": 10, "y": 20, "width": 300, "height": 600}
        state.set_presentation("fitkind-ios", layout)
        self.assertEqual(layout, state.get_presentation("fitkind-ios"))
        self.assertTrue(state.clear_presentation("fitkind-ios"))
        self.assertIsNone(state.get_presentation("fitkind-ios"))

    def test_maintenance_mode(self) -> None:
        state.enter_maintenance("test maintenance", 5)
        with self.assertRaisesRegex(RuntimeError, "test maintenance"):
            state.check_maintenance()
        state.exit_maintenance()
        state.check_maintenance()  # should not raise

    def test_state_dir(self) -> None:
        self.assertEqual(str(state.state_dir()), self.tmpdir.name)

    def test_config_dir(self) -> None:
        self.assertEqual(str(state.config_dir()), self.tmpdir.name)


if __name__ == "__main__":
    unittest.main()
