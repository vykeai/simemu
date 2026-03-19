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
            "--show",
            "--label", "test",
        ])
        self.assertEqual(args.platform, "ios")
        self.assertEqual(args.version, "26")
        self.assertEqual(args.form_factor, "tablet")
        self.assertTrue(args.visible)
        self.assertEqual(args.label, "test")

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
        output = json.loads(stdout.getvalue())
        self.assertEqual(output["session"], "s-abc123")

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


if __name__ == "__main__":
    unittest.main()
