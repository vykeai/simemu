"""Tests for simemu.watchdog — daemon health checks."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

_tmpdir = tempfile.mkdtemp(prefix="simemu-watchdog-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu.watchdog import (
    check_api_server,
    check_monitor_agent,
    check_menubar_app,
    check_stale_sessions,
    check_state_file_health,
    full_health_check,
    is_healthy,
)


class TestCheckApiServer(unittest.TestCase):
    @patch("urllib.request.urlopen")
    def test_healthy(self, mock_urlopen) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"status":"ok"}'
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = lambda s, *a: None
        mock_urlopen.return_value = mock_resp
        result = check_api_server()
        self.assertEqual(result["status"], "healthy")

    @patch("urllib.request.urlopen", side_effect=ConnectionRefusedError)
    def test_unreachable(self, mock_urlopen) -> None:
        result = check_api_server()
        self.assertEqual(result["status"], "unreachable")
        self.assertIn("hint", result)


class TestCheckMonitorAgent(unittest.TestCase):
    @patch("simemu.watchdog.subprocess.run")
    def test_running(self, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=0)
        result = check_monitor_agent()
        self.assertEqual(result["status"], "running")

    @patch("simemu.watchdog.subprocess.run")
    def test_not_loaded(self, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=3)
        result = check_monitor_agent()
        self.assertEqual(result["status"], "not_loaded")


class TestCheckMenubarApp(unittest.TestCase):
    @patch("simemu.watchdog.subprocess.run")
    def test_running(self, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="123 SimEmuBar\n")
        result = check_menubar_app()
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["pid"], 123)

    @patch("simemu.watchdog._menubar_app_candidates")
    @patch("simemu.watchdog.subprocess.run")
    def test_installed_not_running(self, mock_run, mock_candidates) -> None:
        tmp = Path(_tmpdir) / "SimEmuBar.app"
        (tmp / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
        (tmp / "Contents" / "MacOS" / "SimEmuBar").write_text("")
        mock_candidates.return_value = [tmp]
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = check_menubar_app()
        self.assertEqual(result["status"], "installed_not_running")
        self.assertEqual(result["app_path"], str(tmp))

    @patch("simemu.watchdog._menubar_app_candidates", return_value=[])
    @patch("simemu.watchdog.subprocess.run")
    def test_not_installed(self, mock_run, mock_candidates) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = check_menubar_app()
        self.assertEqual(result["status"], "not_installed")


class TestCheckStaleSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-wd-")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_no_sessions_file(self) -> None:
        result = check_stale_sessions()
        self.assertEqual(result["status"], "ok")

    def test_corrupted_file(self) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        sf.write_text("NOT JSON")
        result = check_stale_sessions()
        self.assertEqual(result["status"], "corrupted")

    def test_detects_stale_session(self) -> None:
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        sf = Path(self.tmpdir.name) / "sessions.json"
        sf.write_text(json.dumps({
            "sessions": {
                "s-stale": {"status": "active", "heartbeat_at": old}
            }
        }))
        result = check_stale_sessions()
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["stale_count"], 1)

    def test_ignores_parked(self) -> None:
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        sf = Path(self.tmpdir.name) / "sessions.json"
        sf.write_text(json.dumps({
            "sessions": {
                "s-parked": {"status": "parked", "heartbeat_at": old}
            }
        }))
        result = check_stale_sessions()
        self.assertEqual(result["status"], "ok")


class TestCheckStateFileHealth(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-sfh-")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_ok_when_no_files(self) -> None:
        result = check_state_file_health()
        self.assertEqual(result["status"], "ok")

    def test_detects_corrupted_sessions(self) -> None:
        sf = Path(self.tmpdir.name) / "sessions.json"
        sf.write_text("CORRUPT")
        result = check_state_file_health()
        self.assertEqual(result["status"], "issues")
        self.assertTrue(any("sessions.json" in i for i in result["issues"]))


if __name__ == "__main__":
    unittest.main()
