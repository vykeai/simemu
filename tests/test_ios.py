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
    @patch("simemu.ios.subprocess.run")
    def test_boot_tolerates_already_booted_error(self, mock_run, mock_is_booted, mock_simctl) -> None:
        mock_is_booted.side_effect = [False, True]
        mock_simctl.side_effect = [
            subprocess.CalledProcessError(
                1,
                ["xcrun", "simctl", "boot", "SIM-001"],
                stderr="Unable to boot device in current state: Booted",
            ),
        ]
        # subprocess.run is called for bootstatus (redirected to stderr)
        mock_run.return_value = Mock(returncode=0)
        ios.boot("SIM-001")
        # Verify boot was attempted via _simctl
        mock_simctl.assert_called_once_with("boot", "SIM-001")

    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["xcrun"], timeout=15))
    def test_location_timeout_raises_actionable_error(self, mock_run, mock_booted) -> None:
        with self.assertRaisesRegex(RuntimeError, "simctl command timed out after 15s"):
            ios.location("SIM-001", 51.5074, -0.1278)

        mock_booted.assert_called_once_with("SIM-001")

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

    # T-LU-042: activate_app tests
    @patch("simemu.ios.subprocess.run")
    @patch("simemu.ios.is_app_running", return_value=True)
    @patch("simemu.ios._ensure_booted")
    def test_activate_app_foregrounds_running_app(self, mock_booted, mock_running, mock_run) -> None:
        mock_run.return_value = Mock(returncode=0, stdout="app.fitkind.dev: 12345\n")
        result = ios.activate_app("SIM-001", "app.fitkind.dev")
        self.assertTrue(result)
        mock_run.assert_called_once_with(
            ["xcrun", "simctl", "launch", "SIM-001", "app.fitkind.dev"],
            capture_output=True, text=True, check=False,
        )

    @patch("simemu.ios.subprocess.run")
    @patch("simemu.ios.is_app_running", return_value=True)
    @patch("simemu.ios._ensure_booted")
    def test_activate_app_returns_false_on_launch_failure(self, mock_booted, mock_running, mock_run) -> None:
        mock_run.return_value = Mock(returncode=1, stdout="", stderr="error")
        result = ios.activate_app("SIM-001", "app.fitkind.dev")
        self.assertFalse(result)

    @patch("simemu.ios._post_key")
    @patch("simemu.ios._with_brief_focus")
    @patch("simemu.ios._ensure_booted")
    def test_software_keyboard_toggle_uses_simulator_shortcut(self, mock_booted, mock_focus, mock_post_key) -> None:
        mock_focus.return_value.__enter__.return_value = None
        mock_focus.return_value.__exit__.return_value = None

        ios.software_keyboard("SIM-001", "toggle")

        mock_booted.assert_called_once_with("SIM-001")
        mock_focus.assert_called_once_with("SIM-001", action="software-keyboard")
        mock_post_key.assert_called_once_with(40, ("command down",))

    @patch("simemu.ios._ensure_booted")
    def test_software_keyboard_rejects_unknown_action(self, mock_booted) -> None:
        with self.assertRaisesRegex(RuntimeError, "Supported: toggle"):
            ios.software_keyboard("SIM-001", "show")

    @patch("simemu.ios._type_text")
    @patch("simemu.ios._tap_software_keyboard_text", return_value=False)
    @patch("simemu.ios._input_text_maestro", return_value=False)
    @patch("simemu.ios._connect_hardware_keyboard")
    @patch("simemu.ios._with_brief_focus")
    @patch("simemu.ios.subprocess.run")
    @patch("simemu.ios._ensure_booted")
    def test_input_text_copies_then_types(
        self, mock_booted, mock_run, mock_focus, mock_keyboard, mock_maestro, mock_tap_keyboard, mock_type_text
    ) -> None:
        mock_run.return_value = Mock(returncode=0, stderr=b"")
        mock_focus.return_value.__enter__.return_value = None
        mock_focus.return_value.__exit__.return_value = None

        ios.input_text("SIM-001", "review@sitches.app")

        mock_booted.assert_called_once_with("SIM-001")
        mock_run.assert_called_once_with(
            ["xcrun", "simctl", "pbcopy", "SIM-001"],
            input=b"review@sitches.app",
            capture_output=True,
        )
        mock_focus.assert_called_once_with("SIM-001", action="input")
        mock_maestro.assert_called_once_with("SIM-001", "review@sitches.app")
        mock_tap_keyboard.assert_called_once_with("SIM-001", "review@sitches.app")
        mock_keyboard.assert_called_once()
        mock_type_text.assert_called_once_with("review@sitches.app")

    @patch("simemu.ios._type_text")
    @patch("simemu.ios._tap_software_keyboard_text", return_value=True)
    @patch("simemu.ios._input_text_maestro", return_value=False)
    @patch("simemu.ios._connect_hardware_keyboard")
    @patch("simemu.ios._with_brief_focus")
    @patch("simemu.ios.subprocess.run")
    @patch("simemu.ios._ensure_booted")
    def test_input_text_prefers_visible_software_keyboard(
        self, mock_booted, mock_run, mock_focus, mock_keyboard, mock_maestro, mock_tap_keyboard, mock_type_text
    ) -> None:
        mock_run.return_value = Mock(returncode=0, stderr=b"")
        mock_focus.return_value.__enter__.return_value = None
        mock_focus.return_value.__exit__.return_value = None

        ios.input_text("SIM-001", "review@sitches.app")

        mock_maestro.assert_called_once_with("SIM-001", "review@sitches.app")
        mock_tap_keyboard.assert_called_once_with("SIM-001", "review@sitches.app")
        mock_keyboard.assert_not_called()
        mock_type_text.assert_not_called()

    @patch("simemu.ios._type_text")
    @patch("simemu.ios._tap_software_keyboard_text")
    @patch("simemu.ios._input_text_maestro", return_value=True)
    @patch("simemu.ios._connect_hardware_keyboard")
    @patch("simemu.ios._with_brief_focus")
    @patch("simemu.ios.subprocess.run")
    @patch("simemu.ios._ensure_booted")
    def test_input_text_prefers_maestro_when_available(
        self, mock_booted, mock_run, mock_focus, mock_keyboard, mock_maestro, mock_tap_keyboard, mock_type_text
    ) -> None:
        mock_run.return_value = Mock(returncode=0, stderr=b"")
        mock_focus.return_value.__enter__.return_value = None
        mock_focus.return_value.__exit__.return_value = None

        ios.input_text("SIM-001", "review@sitches.app")

        mock_maestro.assert_called_once_with("SIM-001", "review@sitches.app")
        mock_tap_keyboard.assert_not_called()
        mock_keyboard.assert_not_called()
        mock_type_text.assert_not_called()

    @patch("simemu.ios._run_system_events")
    def test_connect_hardware_keyboard_clicks_simulator_menu_item(self, mock_events) -> None:
        ios._connect_hardware_keyboard()

        script = mock_events.call_args.args[0]
        self.assertIn('menu item "Connect Hardware Keyboard"', script)
        mock_events.assert_called_once()

    @patch("simemu.ios._run_system_events")
    def test_type_text_sends_characters_individually(self, mock_events) -> None:
        ios._type_text('a@"')

        script = mock_events.call_args.args[0]
        self.assertIn('keystroke "a"', script)
        self.assertIn('keystroke "@"', script)
        self.assertIn('keystroke "\\""', script)
        self.assertEqual(3, script.count("delay 0.03"))
        mock_events.assert_called_once()
        self.assertTrue(mock_events.call_args.kwargs["check"])

    @patch("simemu.ios.subprocess.run")
    def test_run_system_events_can_raise_on_applescript_failure(self, mock_run) -> None:
        mock_run.return_value = Mock(returncode=1, stderr="not allowed", stdout="")

        with self.assertRaisesRegex(RuntimeError, "not allowed"):
            ios._run_system_events("bad script", check=True)

    def test_normalize_tap_coordinates_keeps_logical_points(self) -> None:
        logical_x, logical_y, space, logical_size, pixel_size = ios._normalize_tap_coordinates(
            201,
            437,
            "Sitches iPhone16Pro",
            402,
            874,
        )

        self.assertEqual((201, 437), (logical_x, logical_y))
        self.assertEqual("logical-points", space)
        self.assertEqual((402, 874), logical_size)
        self.assertEqual((1206, 2622), pixel_size)

    def test_normalize_tap_coordinates_accepts_screenshot_pixels(self) -> None:
        logical_x, logical_y, space, _, _ = ios._normalize_tap_coordinates(
            603,
            1311,
            "Sitches iPhone16Pro",
            402,
            874,
        )

        self.assertAlmostEqual(201, logical_x)
        self.assertAlmostEqual(437, logical_y)
        self.assertEqual("screenshot-pixels", space)

    def test_normalize_tap_coordinates_rejects_unknown_coordinate_space(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Accepted coordinate spaces"):
            ios._normalize_tap_coordinates(
                1400,
                2900,
                "Sitches iPhone16Pro",
                402,
                874,
            )

    @patch("simemu.ios.is_app_running", return_value=False)
    @patch("simemu.ios._ensure_booted")
    def test_activate_app_returns_false_when_not_running(self, mock_booted, mock_running) -> None:
        result = ios.activate_app("SIM-001", "app.fitkind.dev")
        self.assertFalse(result)

    @patch("simemu.ios.is_app_running")
    def test_wait_for_app_running_raises_when_bundle_never_appears(self, mock_running) -> None:
        mock_running.return_value = False
        with patch("simemu.ios.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "never became a live iOS process"):
                ios._wait_for_app_running("SIM-001", "app.fitkind.dev", timeout=0.2, delay=0.01)

    @patch("simemu.ios._simctl_ui_alert_available", return_value=True)
    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_accept_open_app_alert_returns_true_on_first_success(self, mock_run, mock_booted, mock_available) -> None:
        mock_run.return_value = Mock(returncode=0)
        with patch("simemu.ios._click_open_app_alert_button", return_value=False) as mock_click:
            with patch("simemu.ios.time.sleep"):
                accepted = ios.accept_open_app_alert("SIM-001", attempts=3, delay=0.01)
        self.assertTrue(accepted)
        # Early exit — should only call once since simctl succeeded
        self.assertEqual(1, mock_run.call_count)

    @patch("simemu.ios._simctl_ui_alert_available", return_value=True)
    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_accept_open_app_alert_uses_button_fallback(self, mock_run, mock_booted, mock_available) -> None:
        mock_run.return_value = Mock(returncode=1)
        with patch("simemu.ios._click_open_app_alert_button", return_value=True) as mock_click:
            with patch("simemu.ios.time.sleep"):
                accepted = ios.accept_open_app_alert("SIM-001", attempts=3, delay=0.01)
        self.assertTrue(accepted)
        # Early exit on first button click success
        self.assertEqual(1, mock_run.call_count)
        self.assertEqual(1, mock_click.call_count)

    @patch("simemu.ios._simctl_ui_alert_available", return_value=False)
    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.subprocess.run")
    def test_accept_open_app_alert_skips_simctl_on_xcode_26_5(self, mock_run, mock_booted, mock_available) -> None:
        # Code-review HIGH (T-LU-262 follow-up): when simctl ui alert is not
        # available, accept_open_app_alert MUST NOT shell out to the missing
        # subcommand — that's the same waste fixed for dismiss_system_alert.
        with patch("simemu.ios._click_open_app_alert_button", return_value=True) as mock_click:
            with patch("simemu.ios.time.sleep"):
                accepted = ios.accept_open_app_alert("SIM-001", attempts=3, delay=0.01)
        self.assertTrue(accepted)
        self.assertEqual(0, mock_run.call_count)
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

    # T-LU-043: complete_open_url_handoff uses activate_app as final fallback
    @patch("simemu.ios._ensure_booted")
    @patch("simemu.ios.is_app_running", return_value=True)
    @patch("simemu.ios.activate_app", return_value=True)
    @patch("simemu.ios.accept_open_app_alert", return_value=False)
    @patch("simemu.ios.wait_for_foreground_app", return_value=False)
    def test_complete_open_url_handoff_falls_back_to_activate(
        self, mock_wait, mock_accept, mock_activate, mock_running, mock_booted
    ) -> None:
        with patch("simemu.ios.time.sleep"):
            completed = ios.complete_open_url_handoff(
                "SIM-001", "app.fitkind.dev", attempts=1,
                accept_delay=0.01, foreground_timeout=0.01,
            )
        self.assertTrue(completed)
        mock_activate.assert_called_once_with("SIM-001", "app.fitkind.dev")

    # T-LU-043: _click_open_app_alert_button tries sheet fallback
    @patch("simemu.ios._click_alert_button", return_value=False)
    @patch("simemu.ios._click_sheet_button", return_value=True)
    def test_click_open_app_alert_uses_sheet_fallback(self, mock_sheet, mock_alert) -> None:
        result = ios._click_open_app_alert_button("SIM-001")
        self.assertTrue(result)
        mock_sheet.assert_called_once()


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


class SimWindowMatchTests(unittest.TestCase):
    """T-LU-263: AppleScript predicate must not greedy-match sibling device names."""

    def test_predicate_is_exact_or_starts_with_paren(self) -> None:
        clause = ios._sim_window_match("iPhone 17")
        # Must anchor on equality or the parenthesised runtime suffix.
        self.assertIn('name is "iPhone 17"', clause)
        self.assertIn('name starts with "iPhone 17 ("', clause)
        # Must NOT use the greedy `contains` operator.
        self.assertNotIn("contains", clause)

    def test_predicate_escapes_quotes(self) -> None:
        clause = ios._sim_window_match('Weird "Sim"')
        # Quotes get backslash-escaped for safe AppleScript interpolation.
        self.assertIn(r'\"Sim\"', clause)


class DismissSystemAlertTests(unittest.TestCase):
    """T-LU-262: raise loudly when no automation path can dismiss an iOS 26 alert."""

    def setUp(self) -> None:
        ios._reset_simctl_ui_alert_cache()

    def tearDown(self) -> None:
        ios._reset_simctl_ui_alert_cache()

    def _fake_simctl_run_factory(self, alert_available: bool, simctl_alert_returncode: int = 0):
        """Build a fake subprocess.run that simulates the simctl ui surface."""
        def fake_run(cmd, *args, **kwargs):
            result = Mock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0
            if cmd[:3] == ["xcrun", "simctl", "ui"] and len(cmd) >= 4 and cmd[3] == "--help":
                if alert_available:
                    result.stdout = "USAGE: simctl ui <device> alert | appearance | ...\n"
                else:
                    # Xcode 26.5 help no longer lists `alert`.
                    result.stdout = "USAGE: simctl ui <device> appearance | content_size\n"
                return result
            if cmd[:3] == ["xcrun", "simctl", "ui"] and "alert" in cmd:
                if not alert_available:
                    result.returncode = 64
                    result.stderr = "error: unknown subcommand 'alert'\n"
                else:
                    result.returncode = simctl_alert_returncode
                return result
            return result
        return fake_run

    def test_subcommand_unavailable_detected(self) -> None:
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=False)):
            self.assertFalse(ios._simctl_ui_alert_available())

    def test_subcommand_available_detected(self) -> None:
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=True)):
            self.assertTrue(ios._simctl_ui_alert_available())

    def test_availability_is_cached_per_process(self) -> None:
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=False)) as run:
            ios._simctl_ui_alert_available()
            ios._simctl_ui_alert_available()
            ios._simctl_ui_alert_available()
            # Probe runs exactly once even if queried repeatedly.
            help_calls = [c for c in run.call_args_list
                          if c.args[0][:4] == ["xcrun", "simctl", "ui", "--help"]]
            self.assertEqual(len(help_calls), 1)

    def test_raises_when_simctl_missing_and_applescript_fails(self) -> None:
        """Xcode 26.5 + iOS 26 + every fallback failing => RuntimeError, not silent denial."""
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=False)), \
             patch("simemu.ios._ensure_booted"), \
             patch("simemu.ios.click_system_alert_button", return_value=False):
            with self.assertRaises(RuntimeError) as ctx:
                ios.dismiss_system_alert("SIM-26", "deny")
            msg = str(ctx.exception)
            self.assertIn("T-LU-262", msg)
            self.assertIn("simctl ui alert", msg)

    def test_raises_for_accept_when_no_path_works(self) -> None:
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=False)), \
             patch("simemu.ios._ensure_booted"), \
             patch("simemu.ios.click_system_alert_button", return_value=False):
            with self.assertRaises(RuntimeError):
                ios.dismiss_system_alert("SIM-26", "accept")

    def test_succeeds_via_simctl_when_available(self) -> None:
        """iOS <26 / Xcode <26.5: keep working through the simctl path."""
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=True)), \
             patch("simemu.ios._ensure_booted"), \
             patch("simemu.ios.click_system_alert_button", return_value=False) as click:
            result = ios.dismiss_system_alert("SIM-OLD", "deny")
            self.assertEqual(result["status"], "denied")
            self.assertEqual(result["method"], "simctl")
            click.assert_not_called()

    def test_succeeds_via_applescript_fallback(self) -> None:
        """If simctl is missing but AppleScript clicks the button, no error."""
        with patch("simemu.ios.subprocess.run",
                   side_effect=self._fake_simctl_run_factory(alert_available=False)), \
             patch("simemu.ios._ensure_booted"), \
             patch("simemu.ios.click_system_alert_button", return_value=True) as click:
            result = ios.dismiss_system_alert("SIM-26", "deny")
            self.assertEqual(result["status"], "denied")
            self.assertEqual(result["method"], "applescript")
            click.assert_called_once()

    def test_rejects_invalid_verdict(self) -> None:
        with self.assertRaises(ValueError):
            ios.dismiss_system_alert("SIM-X", "maybe")


if __name__ == "__main__":
    unittest.main()
