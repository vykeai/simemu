"""Tests for simemu.monitor — health monitor tick."""

import os
import json
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

    def _seed_android_session(self, session_id: str = "s-android", **overrides) -> None:
        from simemu.session import _compute_expires_at, _now_iso

        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": "android",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "status": "active",
            "sim_id": "fitkind-phone-proof",
            "device_name": "FitKind Phone Proof",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "expires_at": _compute_expires_at("active", now),
            "resolved_os_version": "API 35",
            "claim_platform": "android",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
            "pinned_serial": "emulator-5554",
        }
        session_data.update(overrides)
        sessions_path = os.path.join(self.tmpdir.name, "sessions.json")
        with open(sessions_path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"sessions": {session_id: session_data}}))

    @patch("simemu.android.get_android_serial")
    @patch("simemu.android.validate_serial", return_value=True)
    def test_recover_stale_android_keeps_running_avd_when_pinned_serial_valid(
        self,
        mock_validate,
        mock_get_serial,
    ) -> None:
        from simemu.session import get_session

        self._seed_android_session()

        monitor._recover_stale_sessions()

        self.assertEqual(get_session("s-android").status, "active")
        mock_validate.assert_any_call("emulator-5554", "fitkind-phone-proof")
        mock_get_serial.assert_not_called()

    @patch("simemu.android.get_android_serial", return_value=None)
    @patch("simemu.android.validate_serial", return_value=False)
    def test_recover_stale_android_parks_when_avd_does_not_resolve(
        self,
        mock_validate,
        mock_get_serial,
    ) -> None:
        from simemu.session import get_session

        self._seed_android_session()

        monitor._recover_stale_sessions()

        self.assertEqual(get_session("s-android").status, "parked")
        mock_validate.assert_any_call("emulator-5554", "fitkind-phone-proof")
        mock_get_serial.assert_any_call("fitkind-phone-proof", retries=2, delay=0.5)

    def test_parse_etime_seconds(self) -> None:
        self.assertEqual(monitor._parse_etime_seconds("04:05"), 245)
        self.assertEqual(monitor._parse_etime_seconds("01:02:03"), 3723)
        self.assertEqual(monitor._parse_etime_seconds("2-01:02:03"), 176523)

    @patch("simemu.monitor.os.kill")
    @patch("simemu.monitor._iter_process_rows")
    @patch("simemu.session.get_active_sessions", return_value={})
    def test_reaps_orphaned_maestro_processes(
        self,
        mock_sessions,
        mock_rows,
        mock_kill,
    ) -> None:
        mock_rows.return_value = [
            (
                123,
                60,
                "Python -m simemu.cli do s-e21a5b maestro apple/e2e/flow.yaml",
            ),
            (
                124,
                60,
                "java maestro.cli.AppKt --device AAA test flow.yaml "
                "--debug-output /Users/luke/.simemu/maestro-debug/s-e21a5b_20260428",
            ),
        ]

        killed = monitor._reap_orphan_ui_processes()

        self.assertEqual(killed, ["123:maestro:s-e21a5b", "124:maestro:s-e21a5b"])
        mock_kill.assert_any_call(123, signal.SIGTERM)
        mock_kill.assert_any_call(124, signal.SIGTERM)

    @patch("simemu.monitor.os.kill")
    @patch("simemu.monitor._iter_process_rows")
    @patch("simemu.session.get_active_sessions")
    def test_keeps_maestro_process_for_active_young_session(
        self,
        mock_sessions,
        mock_rows,
        mock_kill,
    ) -> None:
        mock_sessions.return_value = {"s-e21a5b": object()}
        mock_rows.return_value = [
            (
                123,
                60,
                "Python -m simemu.cli do s-e21a5b maestro apple/e2e/flow.yaml",
            )
        ]

        killed = monitor._reap_orphan_ui_processes(max_age_seconds=1800)

        self.assertEqual(killed, [])
        mock_kill.assert_not_called()

    @patch("simemu.monitor.os.kill")
    @patch("simemu.monitor._iter_process_rows")
    @patch("simemu.session.get_active_sessions", return_value={})
    def test_reaps_long_lived_screencaptureui(
        self,
        mock_sessions,
        mock_rows,
        mock_kill,
    ) -> None:
        mock_rows.return_value = [
            (
                93503,
                3600,
                "/System/Library/CoreServices/screencaptureui.app/Contents/MacOS/screencaptureui",
            )
        ]

        killed = monitor._reap_orphan_ui_processes()

        self.assertEqual(killed, ["93503:screencaptureui"])
        mock_kill.assert_called_once_with(93503, signal.SIGTERM)

    @patch("simemu.monitor.os.kill")
    @patch("simemu.monitor._iter_process_rows")
    @patch("simemu.session.get_active_sessions", return_value={})
    def test_reaps_stale_system_events_osascript_loop(
        self,
        mock_sessions,
        mock_rows,
        mock_kill,
    ) -> None:
        mock_rows.return_value = [
            (
                78109,
                120,
                'zsh -c osascript -e "tell application \\"System Events\\" '
                'to get name of (process where frontmost is true)"',
            )
        ]

        killed = monitor._reap_orphan_ui_processes()

        self.assertEqual(killed, ["78109:osascript-ui"])
        mock_kill.assert_called_once_with(78109, signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
