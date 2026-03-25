"""Tests for simemu.trace — structured trace bundle export."""

import json
import os
import tempfile
import unittest
from pathlib import Path

_tmpdir = tempfile.mkdtemp(prefix="simemu-trace-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu.trace import export_trace, export_trace_to_file
from simemu.session import _write_sessions_raw, _now_iso, _compute_expires_at


class TestExportTrace(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-trace-")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _seed_session(self, sid: str = "s-trace1") -> None:
        now = _now_iso()
        _write_sessions_raw({"sessions": {
            sid: {
                "session_id": sid,
                "platform": "ios",
                "form_factor": "phone",
                "os_version": "26",
                "real_device": False,
                "label": "test trace",
                "status": "active",
                "sim_id": "AAA-111",
                "device_name": "iPhone 17 Pro",
                "agent": "test",
                "created_at": now,
                "heartbeat_at": now,
                "expires_at": _compute_expires_at("active", now),
                "resolved_os_version": "iOS 26.1",
                "claim_platform": "ios",
                "claim_form_factor": "phone",
                "claim_os_version": "26",
                "claim_real_device": False,
                "claim_label": "test trace",
                "provenance": {"last_app": "com.test"},
            },
        }})

    def test_trace_without_session(self) -> None:
        bundle = export_trace()
        self.assertIn("exported_at", bundle)
        self.assertIn("health", bundle)
        self.assertIn("active_sessions", bundle)
        self.assertEqual(bundle["simemu_version"], "0.3.0")

    def test_trace_with_session(self) -> None:
        self._seed_session()
        bundle = export_trace("s-trace1")
        self.assertIn("session", bundle)
        self.assertEqual(bundle["session"]["session_id"], "s-trace1")
        self.assertIn("provenance", bundle)
        self.assertIn("command_history", bundle)

    def test_trace_missing_session(self) -> None:
        bundle = export_trace("s-nonexistent")
        self.assertIn("error", bundle["session"])

    def test_export_to_file(self) -> None:
        self._seed_session()
        output = os.path.join(self.tmpdir.name, "trace.json")
        path = export_trace_to_file("s-trace1", output)
        self.assertEqual(path, output)
        self.assertTrue(Path(output).exists())
        data = json.loads(Path(output).read_text())
        self.assertIn("session", data)

    def test_export_auto_names_file(self) -> None:
        os.environ["SIMEMU_OUTPUT_DIR"] = self.tmpdir.name
        path = export_trace_to_file()
        self.assertTrue(Path(path).exists())
        self.assertIn("trace_", path)


if __name__ == "__main__":
    unittest.main()
