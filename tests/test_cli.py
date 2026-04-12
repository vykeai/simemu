import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simemu import cli
from simemu.session import ClaimSpec, Session, SessionError


class CliParserTests(unittest.TestCase):
    """Tests for v2 CLI argument parsing via build_parser()."""

    def setUp(self) -> None:
        self.parser = cli.build_parser()

    def test_claim_parser_accepts_platform(self) -> None:
        args = self.parser.parse_args(["claim", "ios"])
        self.assertEqual(args.platform, "ios")
        self.assertEqual(args.func, cli.cmd_claim)

    def test_claim_parser_accepts_all_options(self) -> None:
        args = self.parser.parse_args([
            "claim", "ios",
            "--version", "26",
            "--form-factor", "tablet",
            "--device", "luke-iphone",
            "--show",
            "--label", "test",
        ])
        self.assertEqual(args.platform, "ios")
        self.assertEqual(args.version, "26")
        self.assertEqual(args.form_factor, "tablet")
        self.assertEqual(args.device, "luke-iphone")
        self.assertTrue(args.visible)
        self.assertEqual(args.label, "test")

    def test_relabel_parser(self) -> None:
        args = self.parser.parse_args(["relabel", "s-abc123", "luke-iphone", "--platform", "ios"])
        self.assertEqual(args.target, "s-abc123")
        self.assertEqual(args.label, "luke-iphone")
        self.assertEqual(args.platform, "ios")
        self.assertEqual(args.func, cli.cmd_relabel)

    def test_do_parser_accepts_session_and_command(self) -> None:
        args = self.parser.parse_args(["do", "s-abc123", "screenshot"])
        self.assertEqual(args.session, "s-abc123")
        self.assertEqual(args.do_command, "screenshot")
        self.assertEqual(args.func, cli.cmd_do)

    def test_do_parser_accepts_extra_args(self) -> None:
        args = self.parser.parse_args(["do", "s-abc123", "tap", "100", "200"])
        self.assertEqual(args.session, "s-abc123")
        self.assertEqual(args.do_command, "tap")
        self.assertEqual(args.extra, ["100", "200"])

    def test_sessions_parser(self) -> None:
        args = self.parser.parse_args(["sessions", "--json"])
        self.assertTrue(args.json)
        self.assertEqual(args.func, cli.cmd_sessions)

    def test_config_window_mode_parser(self) -> None:
        args = self.parser.parse_args(["config", "window-mode", "hidden"])
        self.assertEqual(args.config_command, "window-mode")
        self.assertEqual(args.mode, "hidden")
        self.assertEqual(args.func, cli.cmd_config)

    def test_config_displays_parser(self) -> None:
        args = self.parser.parse_args(["config", "displays"])
        self.assertEqual(args.config_command, "displays")
        self.assertEqual(args.func, cli.cmd_config)

    def test_config_show_parser(self) -> None:
        args = self.parser.parse_args(["config", "show"])
        self.assertEqual(args.config_command, "show")
        self.assertEqual(args.func, cli.cmd_config)


class CliLegacyRejectionTests(unittest.TestCase):
    """Tests that legacy slug-based commands are hard-rejected."""

    def setUp(self) -> None:
        self.parser = cli.build_parser()
        self._tmpdir = tempfile.mkdtemp()
        self._env = {
            "SIMEMU_STATE_DIR": self._tmpdir,
            "SIMEMU_NO_AUTOSTART": "1",
        }

    def _run_legacy_command(self, argv: list[str]) -> int:
        """Parse argv and run through the same logic as main(), return exit code."""
        args = self.parser.parse_args(argv)
        func_name = getattr(args.func, "__name__", "")
        if func_name not in cli._V2_COMMANDS and func_name not in cli._MAINTENANCE_EXEMPT:
            # Legacy command — should be rejected
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                try:
                    cli._reject_legacy(args)
                except SystemExit as e:
                    return e.code
            return 0
        return 0

    def test_legacy_command_rejected(self) -> None:
        with patch.dict(os.environ, self._env):
            code = self._run_legacy_command(["acquire", "ios", "test"])
        self.assertEqual(code, 1)

    def test_legacy_install_rejected(self) -> None:
        with patch.dict(os.environ, self._env):
            code = self._run_legacy_command(["install", "slug", "app.ipa"])
        self.assertEqual(code, 1)

    def test_legacy_tap_rejected(self) -> None:
        with patch.dict(os.environ, self._env):
            code = self._run_legacy_command(["tap", "slug", "100", "200"])
        self.assertEqual(code, 1)


class CliHandlerTests(unittest.TestCase):
    """Tests for v2 command handler functions."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._env_patcher = patch.dict(os.environ, {
            "SIMEMU_STATE_DIR": self._tmpdir,
        })
        self._env_patcher.start()

    def tearDown(self) -> None:
        self._env_patcher.stop()

    def _make_session(self, **overrides) -> Session:
        defaults = {
            "session_id": "s-abc123",
            "platform": "ios",
            "form_factor": "phone",
            "os_version": "latest",
            "real_device": False,
            "label": "",
            "status": "active",
            "sim_id": "SIM-001",
            "device_name": "iPhone 16 Pro",
            "agent": "test-agent",
            "heartbeat_at": "2026-03-19T10:00:00+00:00",
            "created_at": "2026-03-19T09:00:00+00:00",
            "expires_at": "2026-03-19T11:00:00+00:00",
        }
        defaults.update(overrides)
        return Session(**defaults)

    def test_claim_calls_session_claim(self) -> None:
        session = self._make_session()
        args = Namespace(
            platform="ios",
            form_factor="phone",
            version=None,
            real=False,
            device="luke-iphone",
            visible=False,
            label="",
        )
        stdout = io.StringIO()

        with patch("simemu.cli.session_module.claim", return_value=session) as claim_mock:
            with redirect_stdout(stdout):
                cli.cmd_claim(args)

        claim_mock.assert_called_once()
        spec = claim_mock.call_args[0][0]
        self.assertIsInstance(spec, ClaimSpec)
        self.assertEqual(spec.platform, "ios")
        self.assertEqual(spec.device_selector, "luke-iphone")
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["session"], "s-abc123")

    def test_claim_uses_real_device_alias(self) -> None:
        session = self._make_session(real_device=True)
        args = Namespace(
            platform="luke-iphone",
            form_factor="phone",
            version=None,
            real=False,
            device=None,
            visible=False,
            label="",
        )
        stdout = io.StringIO()

        with patch("simemu.cli.session_module.claim", return_value=session) as claim_mock:
            with patch("simemu.claim_policy.resolve_alias", return_value={
                "platform": "ios",
                "real_device": True,
                "device": "00008150-001622E63638401C",
            }):
                with patch("simemu.claim_policy.apply_defaults", side_effect=lambda platform, spec: spec):
                    with redirect_stdout(stdout):
                        cli.cmd_claim(args)

        spec = claim_mock.call_args[0][0]
        self.assertTrue(spec.real_device)
        self.assertEqual(spec.device_selector, "00008150-001622E63638401C")

    def test_do_calls_do_command(self) -> None:
        args = Namespace(
            session="s-abc123",
            do_command="screenshot",
            extra=[],
        )
        result = {"path": "/tmp/shot.png"}
        stdout = io.StringIO()

        with patch("simemu.cli.session_module.do_command", return_value=result) as do_mock:
            with redirect_stdout(stdout):
                cli.cmd_do(args)

        do_mock.assert_called_once_with("s-abc123", "screenshot", [])
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["path"], "/tmp/shot.png")

    def test_sessions_shows_active(self) -> None:
        session = self._make_session()
        args = Namespace(json=False)
        stdout = io.StringIO()

        with patch("simemu.cli.session_module.get_active_sessions",
                    return_value={"s-abc123": session}):
            with redirect_stdout(stdout):
                cli.cmd_sessions(args)

        output = stdout.getvalue()
        self.assertIn("s-abc123", output)
        self.assertIn("ios", output)

    def test_sessions_json_output(self) -> None:
        session = self._make_session()
        args = Namespace(json=True)
        stdout = io.StringIO()

        with patch("simemu.cli.session_module.get_active_sessions",
                    return_value={"s-abc123": session}):
            with redirect_stdout(stdout):
                cli.cmd_sessions(args)

        data = json.loads(stdout.getvalue())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["session"], "s-abc123")

    def test_config_window_mode_show(self) -> None:
        args = Namespace(config_command="window-mode", mode=None)
        stdout = io.StringIO()

        with patch("simemu.cli.window_mgr.get_window_mode", return_value="hidden"):
            with redirect_stdout(stdout):
                cli.cmd_config(args)

        output = stdout.getvalue()
        self.assertIn("Current window mode: hidden", output)
        self.assertIn("Available modes:", output)

    def test_config_window_mode_set(self) -> None:
        args = Namespace(
            config_command="window-mode",
            mode="hidden",
            display=None,
            corner=None,
        )
        stdout = io.StringIO()

        with patch("simemu.cli.window_mgr.set_window_mode",
                    return_value={"window_mode": "hidden"}) as set_mock:
            with patch("simemu.cli.window_mgr.apply_to_all", return_value=0):
                with redirect_stdout(stdout):
                    cli.cmd_config(args)

        set_mock.assert_called_once_with("hidden", display=None, corner=None)
        self.assertIn("Window mode set to: hidden", stdout.getvalue())

    def test_relabel_calls_alias_store(self) -> None:
        args = Namespace(target="s-live123", label="luke-iphone", platform=None)
        stdout = io.StringIO()

        with patch("simemu.cli._resolve_real_device_target", return_value=("ios", "DEVICE-1", "Luke iPhone")):
            with patch("simemu.cli.set_device_alias", return_value="luke-iphone") as set_alias:
                with redirect_stdout(stdout):
                    cli.cmd_relabel(args)

        set_alias.assert_called_once_with(
            platform="ios",
            device_id="DEVICE-1",
            device_name="Luke iPhone",
            alias="luke-iphone",
        )
        self.assertIn("luke-iphone", stdout.getvalue())

    def test_rename_updates_matching_sessions(self) -> None:
        args = Namespace(target="s-abc123", name="FitKind iPhone", platform=None)
        stdout = io.StringIO()

        with patch("simemu.cli._resolve_simulator_target", return_value=("ios", "SIM-001")):
            with patch("simemu.cli.ios.rename") as rename_mock:
                with patch("simemu.cli._update_session_device_refs") as update_refs:
                    with patch("simemu.cli.state._locked_state") as locked_state:
                        locked_state.return_value.__enter__.return_value = ({"allocations": {}}, lambda data: None)
                        locked_state.return_value.__exit__.return_value = False
                        with redirect_stdout(stdout):
                            cli.cmd_rename(args)

        rename_mock.assert_called_once_with("SIM-001", "FitKind iPhone")
        update_refs.assert_called_once_with("ios", "SIM-001", "FitKind iPhone")
        self.assertIn("FitKind iPhone", stdout.getvalue())

    @patch("subprocess.run")
    def test_daemon_install_uses_state_dir_log(self, mock_run) -> None:
        args = Namespace(action="install", idle_timeout=20)
        stdout = io.StringIO()
        fake_home = Path(self._tmpdir) / "home"
        repo_root = Path(cli.__file__).resolve().parents[1]

        with patch("simemu.cli.Path.home", return_value=fake_home):
            with redirect_stdout(stdout):
                cli.cmd_daemon(args)

        plist_path = fake_home / "Library" / "LaunchAgents" / "com.simemu.daemon.plist"
        plist = plist_path.read_text()
        expected_log = str(Path(self._tmpdir) / "daemon.log")
        self.assertIn(expected_log, plist)
        self.assertIn(sys.executable, plist)
        self.assertIn(str(repo_root), plist)
        self.assertIn(expected_log, stdout.getvalue())

    @patch("simemu.watchdog.check_menubar_app", return_value={"status": "installed_not_running", "app_path": "/Applications/SimEmuBar.app"})
    @patch("simemu.cli.socket.create_connection")
    @patch("simemu.cli.list_android", return_value=[])
    @patch("simemu.cli.list_ios", return_value=[])
    @patch("simemu.cli.session_module.get_all_sessions", return_value={})
    @patch("simemu.cli.window_mgr.list_displays", return_value=[])
    @patch("simemu.cli.window_mgr.get_window_mode", return_value="hidden")
    @patch("subprocess.run")
    def test_status_overview_flags_menubar_not_running(
        self,
        mock_run,
        mock_window_mode,
        mock_displays,
        mock_sessions,
        mock_ios,
        mock_android,
        mock_socket,
        mock_menubar,
    ) -> None:
        mock_socket.return_value.__enter__ = lambda s: s
        mock_socket.return_value.__exit__ = lambda s, *a: None
        mock_run.return_value = MagicMock(returncode=0, stdout="Mac15,14\n")
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cli.cmd_status_overview(Namespace(json=False))

        output = stdout.getvalue()
        self.assertIn("Menubar: installed, not running", output)
        self.assertIn("Menubar not running", output)
        self.assertNotIn("Health: all good", output)

    @patch("simemu.watchdog.full_health_check")
    def test_doctor_flags_installed_but_stopped_menubar(self, mock_health) -> None:
        mock_health.return_value = {
            "api_server": {"status": "healthy"},
            "monitor": {"status": "running"},
            "menubar": {"status": "installed_not_running", "app_path": "/Applications/SimEmuBar.app"},
            "state_files": {"status": "ok", "issues": []},
            "sessions": {"status": "ok", "stale_count": 0},
        }
        stdout = io.StringIO()

        with redirect_stdout(stdout):
            cli.cmd_doctor(Namespace())

        output = stdout.getvalue()
        self.assertIn("Menubar app installed but not running", output)
        self.assertIn("simemu menubar", output)


class CliInvocationWarningTests(unittest.TestCase):
    def test_warns_for_module_invocation(self) -> None:
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["cli.py"]):
            with redirect_stderr(stderr):
                cli._warn_if_module_invocation()
        self.assertIn("python -m simemu.cli", stderr.getvalue())

    def test_no_warning_for_public_cli(self) -> None:
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["simemu"]):
            with redirect_stderr(stderr):
                cli._warn_if_module_invocation()
        self.assertEqual("", stderr.getvalue())


class CliDoHelpTests(unittest.TestCase):
    def test_do_help_prints_subcommand_help(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["simemu", "do", "help"]):
            with patch("simemu.cli._autostart_server_if_needed"):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as exc:
                        cli.main()
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("usage: simemu do", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
