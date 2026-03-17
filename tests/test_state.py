import os
import sys
import tempfile
import time
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

    def test_acquire_persists_and_release_removes_slug(self) -> None:
        alloc = state.acquire(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )

        self.assertEqual(alloc.slug, "fitkind-ios")
        self.assertTrue(Path(self.tmpdir.name, "state.json").exists())
        self.assertEqual(state.get("fitkind-ios").sim_id, "SIM-001")

        released = state.release("fitkind-ios", agent="fitkind")

        self.assertEqual(released.device_name, "iPhone 16 Pro")
        self.assertIsNone(state.get("fitkind-ios"))

    def test_duplicate_slug_is_rejected(self) -> None:
        state.acquire("fitkind-ios", "SIM-001", "ios", "iPhone 16 Pro", "fitkind")

        with self.assertRaisesRegex(RuntimeError, "already reserved by agent 'fitkind'"):
            state.acquire("fitkind-ios", "SIM-002", "ios", "iPhone 16", "other-agent")

    def test_duplicate_simulator_id_is_rejected(self) -> None:
        state.acquire("fitkind-ios", "SIM-001", "ios", "iPhone 16 Pro", "fitkind")

        with self.assertRaisesRegex(RuntimeError, "already reserved as 'fitkind-ios'"):
            state.acquire("healthapp-ios", "SIM-001", "ios", "iPhone 16 Pro", "healthapp")

    def test_release_requires_matching_agent_identity(self) -> None:
        state.acquire("fitkind-ios", "SIM-001", "ios", "iPhone 16 Pro", "fitkind")

        with self.assertRaisesRegex(RuntimeError, "SIMEMU_AGENT=fitkind simemu release fitkind-ios"):
            state.release("fitkind-ios", agent="wrong-agent")

    def test_touch_updates_heartbeat(self) -> None:
        state.acquire("fitkind-ios", "SIM-001", "ios", "iPhone 16 Pro", "fitkind")
        before = state.get("fitkind-ios").heartbeat_at

        time.sleep(0.01)
        state.touch("fitkind-ios")
        after = state.get("fitkind-ios").heartbeat_at

        self.assertNotEqual(before, after)

    def test_set_recording_updates_recording_fields(self) -> None:
        state.acquire("fitkind-ios", "SIM-001", "ios", "iPhone 16 Pro", "fitkind")

        state.set_recording("fitkind-ios", pid=4242, output="/tmp/fitkind.mov")
        alloc = state.get("fitkind-ios")

        self.assertEqual(alloc.recording_pid, 4242)
        self.assertEqual(alloc.recording_output, "/tmp/fitkind.mov")

        state.set_recording("fitkind-ios", pid=None, output=None)
        alloc = state.get("fitkind-ios")
        self.assertIsNone(alloc.recording_pid)
        self.assertIsNone(alloc.recording_output)

    def test_require_raises_for_missing_slug(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "No reservation for 'missing-ios'"):
            state.require("missing-ios")

    def test_presentation_layout_round_trip(self) -> None:
        layout = {"x": 10, "y": 20, "width": 300, "height": 600}

        state.set_presentation("fitkind-ios", layout)
        self.assertEqual(layout, state.get_presentation("fitkind-ios"))
        self.assertTrue(state.clear_presentation("fitkind-ios"))
        self.assertIsNone(state.get_presentation("fitkind-ios"))


if __name__ == "__main__":
    unittest.main()
