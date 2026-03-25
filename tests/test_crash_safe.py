"""Tests for crash-safe atomic writes and corruption recovery."""

import json
import os
import tempfile
import unittest
from pathlib import Path

# Set temp dirs before importing simemu modules
_tmpdir = tempfile.mkdtemp(prefix="simemu-crash-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu.session import _read_sessions_raw, _write_sessions_raw, _sessions_file
from simemu.state import _read_raw, _write_raw, state_file


class TestSessionsCrashSafe(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-cs-")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_write_creates_backup(self) -> None:
        data = {"sessions": {"s-1": {"status": "active"}}}
        _write_sessions_raw(data)
        _write_sessions_raw({"sessions": {"s-2": {"status": "active"}}})
        bak = _sessions_file().with_suffix(".bak")
        self.assertTrue(bak.exists())
        bak_data = json.loads(bak.read_text())
        self.assertIn("s-1", bak_data["sessions"])

    def test_read_recovers_from_corrupted_primary(self) -> None:
        # Write twice so backup exists, then corrupt primary
        _write_sessions_raw({"sessions": {"s-old": {"status": "idle"}}})
        _write_sessions_raw({"sessions": {"s-good": {"status": "active"}}})
        # Backup has s-old, primary has s-good
        sf = _sessions_file()
        sf.write_text("NOT VALID JSON {{{")
        data = _read_sessions_raw()
        # Should recover from backup (which has s-old)
        self.assertIn("s-old", data["sessions"])

    def test_read_returns_empty_when_both_corrupted(self) -> None:
        sf = _sessions_file()
        bak = sf.with_suffix(".bak")
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("CORRUPT")
        bak.write_text("ALSO CORRUPT")
        data = _read_sessions_raw()
        self.assertEqual(data, {"sessions": {}})

    def test_read_cleans_stale_tmp(self) -> None:
        sf = _sessions_file()
        sf.parent.mkdir(parents=True, exist_ok=True)
        tmp = sf.with_suffix(".tmp")
        tmp.write_text("stale tmp from crashed write")
        _read_sessions_raw()
        self.assertFalse(tmp.exists())

    def test_write_validates_json_roundtrip(self) -> None:
        data = {"sessions": {"s-1": {"status": "active"}}}
        # Should not raise — valid JSON
        _write_sessions_raw(data)
        result = _read_sessions_raw()
        self.assertEqual(result["sessions"]["s-1"]["status"], "active")

    def test_read_empty_dir(self) -> None:
        data = _read_sessions_raw()
        self.assertEqual(data, {"sessions": {}})


class TestStateCrashSafe(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-state-cs-")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_write_creates_backup(self) -> None:
        _write_raw({"allocations": {"a": {"slug": "a"}}})
        _write_raw({"allocations": {"b": {"slug": "b"}}})
        bak = state_file().with_suffix(".bak")
        self.assertTrue(bak.exists())
        bak_data = json.loads(bak.read_text())
        self.assertIn("a", bak_data["allocations"])

    def test_read_recovers_from_corrupted_primary(self) -> None:
        _write_raw({"allocations": {"old": {"slug": "old"}}})
        _write_raw({"allocations": {"good": {"slug": "good"}}})
        sf = state_file()
        sf.write_text("BROKEN JSON")
        data = _read_raw()
        # Backup has "old" from before the second write
        self.assertIn("old", data["allocations"])

    def test_read_returns_empty_when_both_corrupted(self) -> None:
        sf = state_file()
        bak = sf.with_suffix(".bak")
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text("X")
        bak.write_text("Y")
        data = _read_raw()
        self.assertEqual(data, {"allocations": {}})


if __name__ == "__main__":
    unittest.main()
