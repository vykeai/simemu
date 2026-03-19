"""Tests for simemu.monitor — health monitor tick."""

import os
import signal
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_tmpdir = tempfile.mkdtemp(prefix="simemu-monitor-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu import monitor


class TestMonitor(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-mon-")
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name
        os.environ["SIMEMU_CONFIG_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_state is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self._old_state
        if self._old_config is None:
            os.environ.pop("SIMEMU_CONFIG_DIR", None)
        else:
            os.environ["SIMEMU_CONFIG_DIR"] = self._old_config
        self.tmpdir.cleanup()

    @patch("simemu.monitor.subprocess.Popen")
    @patch("simemu.monitor.socket.create_connection")
    @patch("simemu.monitor.subprocess.run")
    @patch("simemu.monitor.signal.alarm")
    @patch("simemu.monitor.signal.signal")
    def test_monitor_runs_without_crash(
        self, mock_signal, mock_alarm, mock_run, mock_socket, mock_popen
    ) -> None:
        # Mock adb devices returning nothing
        mock_run.return_value = MagicMock(stdout="List of devices attached\n", returncode=0)
        # Mock server is running (socket connects)
        mock_socket.return_value.__enter__ = MagicMock()
        mock_socket.return_value.__exit__ = MagicMock()

        # Should not raise
        monitor.run()
        # Verify alarm was set and cleared
        self.assertEqual(mock_alarm.call_count, 2)
        mock_alarm.assert_any_call(30)
        mock_alarm.assert_any_call(0)

    @patch("simemu.monitor.subprocess.Popen")
    @patch("simemu.monitor.socket.create_connection", side_effect=OSError("refused"))
    @patch("simemu.monitor.subprocess.run")
    @patch("simemu.monitor.signal.alarm")
    @patch("simemu.monitor.signal.signal")
    def test_monitor_starts_server_when_not_running(
        self, mock_signal, mock_alarm, mock_run, mock_socket, mock_popen
    ) -> None:
        mock_run.return_value = MagicMock(stdout="List of devices attached\n", returncode=0)
        monitor.run()
        # Should have attempted to start the server
        mock_popen.assert_called_once()

    @patch("simemu.monitor.signal.alarm")
    @patch("simemu.monitor.signal.signal")
    def test_monitor_respects_timeout(self, mock_signal, mock_alarm) -> None:
        # Verify SIGALRM is configured at the start
        with patch("simemu.monitor.subprocess.run") as mock_run, \
             patch("simemu.monitor.socket.create_connection") as mock_socket:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            mock_socket.return_value.__enter__ = MagicMock()
            mock_socket.return_value.__exit__ = MagicMock()
            monitor.run()

        # First call should set SIGALRM handler
        mock_signal.assert_called_once_with(signal.SIGALRM, unittest.mock.ANY)
        # alarm(30) set at start, alarm(0) at end
        calls = mock_alarm.call_args_list
        self.assertEqual(calls[0][0][0], 30)
        self.assertEqual(calls[-1][0][0], 0)

    @patch("simemu.monitor.subprocess.Popen")
    @patch("simemu.monitor.socket.create_connection")
    @patch("simemu.monitor.subprocess.run", side_effect=Exception("adb not found"))
    @patch("simemu.monitor.signal.alarm")
    @patch("simemu.monitor.signal.signal")
    def test_monitor_handles_adb_failure(
        self, mock_signal, mock_alarm, mock_run, mock_socket, mock_popen
    ) -> None:
        mock_socket.return_value.__enter__ = MagicMock()
        mock_socket.return_value.__exit__ = MagicMock()
        # Should not crash even if adb fails
        monitor.run()


if __name__ == "__main__":
    unittest.main()
