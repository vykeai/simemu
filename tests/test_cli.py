import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import cli
from simemu.state import Allocation


class CliTests(unittest.TestCase):
    def test_autostart_server_spawns_background_serve_when_missing(self) -> None:
        proc = Mock()
        with patch("simemu.cli._autostart_disabled", return_value=False):
            with patch("simemu.cli._server_reachable", side_effect=[False, True]):
                with patch("subprocess.Popen", return_value=proc) as popen_mock:
                    cli._autostart_server_if_needed()

        args = popen_mock.call_args.args[0]
        self.assertEqual([sys.executable, "-m", "simemu.cli", "serve"], args)

    def test_autostart_server_respects_disable_flag(self) -> None:
        with patch("simemu.cli._autostart_disabled", return_value=True):
            with patch("subprocess.Popen") as popen_mock:
                cli._autostart_server_if_needed()

        popen_mock.assert_not_called()

    def test_main_skips_autostart_for_serve(self) -> None:
        with patch("simemu.cli.build_parser") as build_parser_mock:
            serve_func = Mock()
            parser = Mock()
            parser.parse_args.return_value = SimpleNamespace(
                func=serve_func,
                no_autostart=False,
            )
            build_parser_mock.return_value = parser
            serve_func.__name__ = "cmd_serve"

            with patch("simemu.cli._autostart_server_if_needed") as autostart_mock:
                cli.main()

        autostart_mock.assert_not_called()
        serve_func.assert_called_once()

    def test_status_json_prints_allocations_array(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        buf = io.StringIO()

        with patch("simemu.cli.state.get_all", return_value={"fitkind-ios": alloc}):
            with redirect_stdout(buf):
                cli.cmd_status(SimpleNamespace(json=True))

        output = buf.getvalue()
        self.assertIn('"slug": "fitkind-ios"', output)
        self.assertIn('"platform": "ios"', output)

    def test_status_text_handles_empty_allocations(self) -> None:
        buf = io.StringIO()

        with patch("simemu.cli.state.get_all", return_value={}):
            with redirect_stdout(buf):
                cli.cmd_status(SimpleNamespace(json=False))

        self.assertIn("No simulators currently reserved.", buf.getvalue())

    def test_release_stops_active_ios_recording_before_release(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
            recording_pid=4242,
        )
        buf = io.StringIO()
        stop_mock = Mock()

        with patch("simemu.cli.state.release", return_value=alloc) as release_mock:
            with patch("simemu.cli.ios.record_stop", stop_mock):
                with patch("simemu.cli._agent", return_value="fitkind"):
                    with redirect_stdout(buf):
                        cli.cmd_release(SimpleNamespace(slug="fitkind-ios"))

        release_mock.assert_called_once_with("fitkind-ios", agent="fitkind")
        stop_mock.assert_called_once_with(4242)
        self.assertIn("Released 'fitkind-ios' (iPhone 16 Pro)", buf.getvalue())

    def test_record_start_tracks_recording_state_and_prints_stop_hint(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        buf = io.StringIO()

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.ios.record_start", return_value=555) as record_start_mock:
                    with patch("simemu.cli.state.set_recording") as set_recording_mock:
                        with redirect_stdout(buf):
                            cli.cmd_record(SimpleNamespace(
                                slug="fitkind-ios",
                                action="start",
                                output="/tmp/demo.mp4",
                                codec="h264",
                                json=False,
                            ))

        record_start_mock.assert_called_once_with("SIM-001", "/tmp/demo.mp4", codec="h264")
        set_recording_mock.assert_called_once_with("fitkind-ios", 555, "/tmp/demo.mp4")
        self.assertIn("Recording started → /tmp/demo.mp4", buf.getvalue())
        self.assertIn("simemu record stop fitkind-ios", buf.getvalue())

    def test_record_stop_clears_recording_state(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
            recording_pid=555,
            recording_output="/tmp/demo.mp4",
        )
        buf = io.StringIO()

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.ios.record_stop") as record_stop_mock:
                    with patch("simemu.cli.state.set_recording") as set_recording_mock:
                        with redirect_stdout(buf):
                            cli.cmd_record(SimpleNamespace(
                                slug="fitkind-ios",
                                action="stop",
                                json=False,
                            ))

        record_stop_mock.assert_called_once_with(555)
        set_recording_mock.assert_called_once_with("fitkind-ios", None, None)
        self.assertIn("Recording stopped → /tmp/demo.mp4", buf.getvalue())

    def test_screenshot_uses_env_max_size_for_ios_and_reports_saved_path(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch") as touch_mock:
                with patch("simemu.cli._auto_path", return_value="/tmp/fitkind-ios.png"):
                    with patch("simemu.cli.ios.screenshot") as screenshot_mock:
                        with patch.dict(os.environ, {"SIMEMU_SCREENSHOT_MAX_SIZE": "1000"}, clear=False):
                            with redirect_stdout(stdout), redirect_stderr(stderr):
                                cli.cmd_screenshot(SimpleNamespace(
                                    slug="fitkind-ios",
                                    output=None,
                                    format="png",
                                    max_size=None,
                                    json=False,
                                ))

        touch_mock.assert_called_once_with("fitkind-ios")
        screenshot_mock.assert_called_once_with("SIM-001", "/tmp/fitkind-ios.png", fmt="png", max_size=1000)
        self.assertIn("Screenshot saved: /tmp/fitkind-ios.png", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_screenshot_warns_and_ignores_non_png_format_on_android(self) -> None:
        alloc = Allocation(
            slug="fitkind-android",
            sim_id="EMU-001",
            platform="android",
            device_name="Pixel 9",
            agent="fitkind",
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch") as touch_mock:
                with patch("simemu.cli.android.screenshot") as screenshot_mock:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        cli.cmd_screenshot(SimpleNamespace(
                            slug="fitkind-android",
                            output="/tmp/android-shot.png",
                            format="jpeg",
                            max_size=720,
                            json=True,
                        ))

        touch_mock.assert_called_once_with("fitkind-android")
        screenshot_mock.assert_called_once_with("EMU-001", "/tmp/android-shot.png", max_size=720)
        self.assertIn("Warning: Android only supports PNG screenshots; ignoring --format.", stderr.getvalue())
        self.assertIn('"path": "/tmp/android-shot.png"', stdout.getvalue())

    def test_install_passes_timeout_to_ios_adapter(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch") as touch_mock:
                with patch("simemu.cli.ios.install") as install_mock:
                    with redirect_stdout(stdout):
                        cli.cmd_install(SimpleNamespace(
                            slug="fitkind-ios",
                            app="/tmp/Fitkind.app",
                            timeout=180,
                        ))

        touch_mock.assert_called_once_with("fitkind-ios")
        install_mock.assert_called_once_with("SIM-001", "/tmp/Fitkind.app", timeout=180)
        self.assertIn("Installing /tmp/Fitkind.app on 'fitkind-ios' (iPhone 16 Pro)...", stdout.getvalue())
        self.assertIn("Done.", stdout.getvalue())

    def test_present_ios_prints_confirmation(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch") as touch_mock:
                with patch("simemu.cli.ios.present", return_value={"stable": True}) as present_mock:
                    with redirect_stdout(stdout):
                        cli.cmd_present(SimpleNamespace(slug="fitkind-ios", json=False))

        touch_mock.assert_called_once_with("fitkind-ios")
        present_mock.assert_called_once_with("SIM-001", layout=None)
        self.assertIn("Presented 'fitkind-ios' (iPhone 16 Pro).", stdout.getvalue())

    def test_stabilize_ios_json_prints_payload(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        payload = {"stable": True, "device_name": "iPhone 16 Pro"}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch") as touch_mock:
                with patch("simemu.cli.ios.stabilize", return_value=payload) as stabilize_mock:
                    with redirect_stdout(stdout):
                        cli.cmd_stabilize(SimpleNamespace(slug="fitkind-ios", json=True))

        touch_mock.assert_called_once_with("fitkind-ios")
        stabilize_mock.assert_called_once_with("SIM-001")
        self.assertIn('"stable": true', stdout.getvalue())

    def test_stabilize_ios_reports_drift_without_healing(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        payload = {"stable": True, "device_name": "iPhone 16 Pro", "window_visible_on_active_desktop": True}
        layout = {"x": 10, "y": 20, "width": 300, "height": 600, "display_id": 1}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.current_presentation_layout", return_value={"x": 100, "y": 20, "width": 300, "height": 600, "display_id": 2}):
                        with patch("simemu.cli.ios.stabilize", return_value=payload):
                            with redirect_stdout(stdout):
                                cli.cmd_stabilize(SimpleNamespace(slug="fitkind-ios", json=False, heal=False))

        self.assertIn("layout drifted from saved presentation", stdout.getvalue())

    def test_stabilize_ios_can_heal_saved_layout(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        payload = {"stable": True, "device_name": "iPhone 16 Pro", "window_visible_on_active_desktop": True}
        layout = {"x": 10, "y": 20, "width": 300, "height": 600, "display_id": 1}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.current_presentation_layout", side_effect=[
                        {"x": 100, "y": 20, "width": 300, "height": 600, "display_id": 2},
                        layout,
                    ]):
                        with patch("simemu.cli.ios.present") as present_mock:
                            with patch("simemu.cli.ios.stabilize", return_value=payload):
                                with redirect_stdout(stdout):
                                    cli.cmd_stabilize(SimpleNamespace(slug="fitkind-ios", json=False, heal=True))

        present_mock.assert_called_once_with("SIM-001", layout=layout)
        self.assertIn("healed to saved layout", stdout.getvalue())

    def test_stabilize_ios_json_reports_display_drift_fields(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        payload = {"stable": True, "device_name": "iPhone 16 Pro", "window_visible_on_active_desktop": True}
        layout = {"x": 10, "y": 20, "width": 300, "height": 600, "display_id": 1}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.current_presentation_layout", return_value={"x": 10, "y": 20, "width": 300, "height": 600, "display_id": 2}):
                        with patch("simemu.cli.ios.stabilize", return_value=payload):
                            with redirect_stdout(stdout):
                                cli.cmd_stabilize(SimpleNamespace(slug="fitkind-ios", json=True, heal=False))

        self.assertIn('"display_drifted": true', stdout.getvalue())
        self.assertIn('"display_matches_saved": false', stdout.getvalue())

    def test_stabilize_ios_reports_window_not_visible_on_active_desktop(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        payload = {
            "stable": True,
            "device_name": "iPhone 16 Pro",
            "window_visible_on_active_desktop": False,
        }

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=None):
                    with patch("simemu.cli.ios.stabilize", return_value=payload):
                        with redirect_stdout(stdout):
                            cli.cmd_stabilize(SimpleNamespace(slug="fitkind-ios", json=False, heal=False))

        self.assertIn("window not visible on active desktop", stdout.getvalue())

    def test_present_ios_save_layout_persists_current_frame(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        layout = {"x": 1, "y": 2, "width": 300, "height": 600}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.ios.current_presentation_layout", return_value=layout) as layout_mock:
                    with patch("simemu.cli.state.set_presentation") as save_mock:
                        with redirect_stdout(stdout):
                            cli.cmd_present(SimpleNamespace(slug="fitkind-ios", json=False, save_layout=True, clear_layout=False))

        layout_mock.assert_called_once_with("SIM-001")
        save_mock.assert_called_once_with("fitkind-ios", layout)
        self.assertIn("Saved current layout for 'fitkind-ios'.", stdout.getvalue())

    def test_present_ios_uses_saved_layout_when_restoring(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        stdout = io.StringIO()
        layout = {"x": 1, "y": 2, "width": 300, "height": 600}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.present", return_value={"stable": True}) as present_mock:
                        with redirect_stdout(stdout):
                            cli.cmd_present(SimpleNamespace(slug="fitkind-ios", json=False, save_layout=False, clear_layout=False))

        present_mock.assert_called_once_with("SIM-001", layout=layout)
        self.assertIn("using saved layout", stdout.getvalue())

    def test_tap_ios_auto_restores_saved_layout_before_gesture(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        layout = {"x": 10, "y": 20, "width": 300, "height": 600}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.current_presentation_layout", return_value={"x": 100, "y": 200, "width": 300, "height": 600}):
                        with patch("simemu.cli.ios.present") as present_mock:
                            with patch("simemu.cli.ios.tap") as tap_mock:
                                cli.cmd_tap(SimpleNamespace(slug="fitkind-ios", x=12, y=34, pct=False))

        present_mock.assert_called_once_with("SIM-001", layout=layout)
        tap_mock.assert_called_once_with("SIM-001", 12, 34)

    def test_tap_ios_skips_heal_when_layout_is_already_aligned(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        layout = {"x": 10, "y": 20, "width": 300, "height": 600}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.current_presentation_layout", return_value=layout):
                        with patch("simemu.cli.ios.present") as present_mock:
                            with patch("simemu.cli.ios.tap") as tap_mock:
                                cli.cmd_tap(SimpleNamespace(slug="fitkind-ios", x=12, y=34, pct=False))

        present_mock.assert_not_called()
        tap_mock.assert_called_once_with("SIM-001", 12, 34)

    def test_tap_ios_heals_when_current_layout_cannot_be_read(self) -> None:
        alloc = Allocation(
            slug="fitkind-ios",
            sim_id="SIM-001",
            platform="ios",
            device_name="iPhone 16 Pro",
            agent="fitkind",
        )
        layout = {"x": 10, "y": 20, "width": 300, "height": 600}

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch"):
                with patch("simemu.cli.state.get_presentation", return_value=layout):
                    with patch("simemu.cli.ios.current_presentation_layout", side_effect=RuntimeError("no frame")):
                        with patch("simemu.cli.ios.present") as present_mock:
                            with patch("simemu.cli.ios.tap") as tap_mock:
                                cli.cmd_tap(SimpleNamespace(slug="fitkind-ios", x=12, y=34, pct=False))

        present_mock.assert_called_once_with("SIM-001", layout=layout)
        tap_mock.assert_called_once_with("SIM-001", 12, 34)

    def test_launch_passes_extra_arguments_to_android_adapter(self) -> None:
        alloc = Allocation(
            slug="fitkind-android",
            sim_id="EMU-001",
            platform="android",
            device_name="Pixel 9",
            agent="fitkind",
        )

        with patch("simemu.cli.state.require", return_value=alloc):
            with patch("simemu.cli.state.touch") as touch_mock:
                with patch("simemu.cli.android.launch") as launch_mock:
                    cli.cmd_launch(SimpleNamespace(
                        slug="fitkind-android",
                        bundle_or_package="com.fitkind.app",
                        extra=["--es", "route", "paywall"],
                    ))

        touch_mock.assert_called_once_with("fitkind-android")
        launch_mock.assert_called_once_with("EMU-001", "com.fitkind.app", ["--es", "route", "paywall"])


if __name__ == "__main__":
    unittest.main()
