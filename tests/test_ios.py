import sys
import time
import unittest
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

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

    def test_start_hud_overlay_launches_cute_hud_binary(self) -> None:
        captured = {}

        class FakeProc:
            stdin = None
            def poll(self):
                return None

        def fake_popen(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            proc = FakeProc()
            proc.stdin = Mock()
            proc.stdin.write = Mock()
            proc.stdin.flush = Mock()
            return proc

        ios._HUD_PROCESS = None
        with patch("simemu.ios._hud_enabled", return_value=True):
            with patch("simemu.ios._find_cute_hud", return_value="/usr/local/bin/cute-hud"):
                with patch("simemu.ios.subprocess.Popen", side_effect=fake_popen):
                    ios._start_hud_overlay()

        self.assertEqual(["/usr/local/bin/cute-hud"], captured["args"])

    def test_start_hud_overlay_skips_when_binary_not_found(self) -> None:
        ios._HUD_PROCESS = None
        with patch("simemu.ios._hud_enabled", return_value=True):
            with patch("simemu.ios._find_cute_hud", return_value=None):
                ios._start_hud_overlay()
        self.assertIsNone(ios._HUD_PROCESS)

    @patch("simemu.ios._simctl")
    @patch("simemu.ios._is_booted")
    def test_boot_tolerates_already_booted_error(self, mock_is_booted, mock_simctl) -> None:
        mock_is_booted.side_effect = [False, True]
        mock_simctl.side_effect = [
            subprocess.CalledProcessError(
                1,
                ["xcrun", "simctl", "boot", "SIM-001"],
                stderr="Unable to boot device in current state: Booted",
            ),
            None,
        ]
        ios.boot("SIM-001")
        self.assertEqual(("bootstatus", "SIM-001", "-b"), mock_simctl.call_args_list[1][0])

    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_foreground_app_prefers_non_system_bundle(self, mock_run, mock_booted) -> None:
        mock_run.return_value = Mock(
            stdout="\n".join([
                "111 UIKitApplication:com.apple.mobilecal[0x111]",
                "222 UIKitApplication:app.fitkind.dev[0x222]",
            ])
        )
        self.assertEqual("app.fitkind.dev", ios.foreground_app("SIM-001"))

    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_foreground_app_returns_none_when_only_system_bundles_present(self, mock_run, mock_booted) -> None:
        mock_run.return_value = Mock(
            stdout="\n".join([
                "111 UIKitApplication:com.apple.Preferences[0x111]",
                "222 UIKitApplication:com.apple.mobilecal[0x222]",
            ])
        )
        self.assertIsNone(ios.foreground_app("SIM-001"))

    @patch("simemu.ios._wait_for_app_running")
    @patch("simemu.ios._simctl")
    @patch("simemu.ios._ensure_booted")
    def test_launch_terminates_existing_process_and_verifies_running(self, mock_booted, mock_simctl, mock_wait) -> None:
        ios.launch("SIM-001", "app.fitkind.dev", ["--debug-route=foo"])
        mock_simctl.assert_called_once_with(
            "launch", "--terminate-running-process", "SIM-001", "app.fitkind.dev", "--debug-route=foo"
        )
        mock_wait.assert_called_once_with("SIM-001", "app.fitkind.dev")

    @patch("simemu.ios.is_app_running")
    def test_wait_for_app_running_raises_when_bundle_never_appears(self, mock_running) -> None:
        mock_running.return_value = False
        with patch("simemu.ios.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "never became a live iOS process"):
                ios._wait_for_app_running("SIM-001", "app.fitkind.dev", timeout=0.2, delay=0.01)

    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_accept_open_app_alert_returns_true_on_first_success(self, mock_run, mock_booted) -> None:
        mock_run.return_value = Mock(returncode=0)
        with patch("simemu.ios._click_open_app_alert_button", return_value=False) as mock_click:
            with patch("simemu.ios.time.sleep"):
                accepted = ios.accept_open_app_alert("SIM-001", attempts=3, delay=0.01)
        self.assertTrue(accepted)
        # Early exit — should only call once since simctl succeeded
        self.assertEqual(1, mock_run.call_count)

    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_accept_open_app_alert_uses_button_fallback(self, mock_run, mock_booted) -> None:
        mock_run.return_value = Mock(returncode=1)
        with patch("simemu.ios._click_open_app_alert_button", return_value=True) as mock_click:
            with patch("simemu.ios.time.sleep"):
                accepted = ios.accept_open_app_alert("SIM-001", attempts=3, delay=0.01)
        self.assertTrue(accepted)
        # Early exit on first button click success
        self.assertEqual(1, mock_run.call_count)
        self.assertEqual(1, mock_click.call_count)

    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.wait_for_foreground_app", side_effect=[False, True])
    @patch("simemu.ios.accept_open_app_alert", return_value=True)
    def test_complete_open_url_handoff_waits_then_accepts_and_verifies(
        self, mock_accept, mock_wait_foreground, mock_booted
    ) -> None:
        completed = ios.complete_open_url_handoff(
            "SIM-001",
            "app.fitkind.dev",
            attempts=1,
            accept_delay=0.01,
            foreground_timeout=0.05,
        )
        self.assertTrue(completed)
        mock_accept.assert_called_once_with("SIM-001", attempts=1, delay=0.01)


class BriefFocusTests(unittest.TestCase):
    """Tests for _with_brief_focus — shared-desktop focus acquisition/restoration."""

    @patch("simemu.ios._get_device_name", return_value="iPhone 17 Pro")
    @patch("simemu.ios._raise_sim_window")
    @patch("simemu.ios._activate_app")
    @patch("simemu.ios._frontmost_app_name", return_value="Terminal")
    def test_restores_previous_app_after_interaction(
        self, mock_front, mock_activate, mock_raise, mock_name
    ) -> None:
        with ios._with_brief_focus("SIM-001", action="tap"):
            pass  # interaction happens here
        mock_activate.assert_called_once_with("Terminal")

    @patch("simemu.ios._get_device_name", return_value="iPhone 17 Pro")
    @patch("simemu.ios._raise_sim_window")
    @patch("simemu.ios._activate_app")
    @patch("simemu.ios._frontmost_app_name", return_value="Simulator")
    def test_skips_restore_when_simulator_was_frontmost(
        self, mock_front, mock_activate, mock_raise, mock_name
    ) -> None:
        with ios._with_brief_focus("SIM-001", action="tap"):
            pass
        mock_activate.assert_not_called()

    @patch("simemu.ios._get_device_name", return_value="iPhone 17 Pro")
    @patch("simemu.ios._raise_sim_window")
    @patch("simemu.ios._activate_app")
    @patch("simemu.ios._frontmost_app_name", return_value=None)
    def test_skips_restore_when_no_previous_app(
        self, mock_front, mock_activate, mock_raise, mock_name
    ) -> None:
        with ios._with_brief_focus("SIM-001", action="tap"):
            pass
        mock_activate.assert_not_called()

    @patch("simemu.ios._get_device_name", return_value="iPhone 17 Pro")
    @patch("simemu.ios._raise_sim_window")
    @patch("simemu.ios._activate_app")
    @patch("simemu.ios._frontmost_app_name", return_value="Finder")
    def test_restores_even_when_interaction_raises(
        self, mock_front, mock_activate, mock_raise, mock_name
    ) -> None:
        with self.assertRaises(ValueError):
            with ios._with_brief_focus("SIM-001", action="tap"):
                raise ValueError("simulated failure")
        mock_activate.assert_called_once_with("Finder")

    @patch("simemu.ios._get_device_name", return_value="iPhone 17 Pro")
    @patch("simemu.ios._raise_sim_window")
    def test_raises_sim_window_on_entry(
        self, mock_raise, mock_name
    ) -> None:
        with patch("simemu.ios._frontmost_app_name", return_value=None), \
             patch("simemu.ios._activate_app"):
            with ios._with_brief_focus("SIM-001", action="tap"):
                mock_raise.assert_called_once_with("iPhone 17 Pro")


if __name__ == "__main__":
    unittest.main()
