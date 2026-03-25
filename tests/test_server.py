import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Set SIMEMU_STATE_DIR before importing server (state module reads it at import)
_tmpdir = tempfile.mkdtemp()
os.environ["SIMEMU_STATE_DIR"] = _tmpdir

from simemu.session import ClaimSpec, Session, SessionError

# Patch lifespan to avoid starting background tasks during tests
_lifespan_patch = patch("simemu.server.lifespan")
_lifespan_patch.start()

from fastapi.testclient import TestClient
from simemu.server import app

client = TestClient(app)


def _make_session(**overrides) -> Session:
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


class ServerHealthTests(unittest.TestCase):
    """Tests for basic server endpoints."""

    def test_health(self) -> None:
        resp = client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"status": "ok"})


class ServerV2SessionTests(unittest.TestCase):
    """Tests for v2 session-based API endpoints."""

    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"SIMEMU_STATE_DIR": _tmpdir})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def test_v2_sessions_empty(self) -> None:
        with patch("simemu.server.session_module.get_active_sessions", return_value={}):
            resp = client.get("/v2/sessions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), [])

    def test_v2_claim_ios(self) -> None:
        session = _make_session()
        with patch("simemu.server.state.check_maintenance"):
            with patch("simemu.server.session_module.claim", return_value=session) as claim_mock:
                resp = client.post("/v2/claim", json={
                    "platform": "ios",
                    "form_factor": "phone",
                })
        self.assertEqual(resp.status_code, 200)
        claim_mock.assert_called_once()
        data = resp.json()
        self.assertEqual(data["session"], "s-abc123")
        self.assertEqual(data["platform"], "ios")

    def test_v2_claim_missing_platform(self) -> None:
        resp = client.post("/v2/claim", json={})
        self.assertEqual(resp.status_code, 422)

    def test_v2_do_screenshot(self) -> None:
        result = {"path": "/tmp/screenshot.png"}
        with patch("simemu.server.session_module.do_command", return_value=result) as do_mock:
            resp = client.post("/v2/do", json={
                "session": "s-abc123",
                "command": "screenshot",
            })
        self.assertEqual(resp.status_code, 200)
        do_mock.assert_called_once_with("s-abc123", "screenshot", [])
        self.assertEqual(resp.json()["path"], "/tmp/screenshot.png")

    def test_v2_do_unknown_session(self) -> None:
        err = SessionError(
            error="session_not_found",
            session="s-bad999",
            hint="No active session with ID 's-bad999'.",
        )
        with patch("simemu.server.session_module.do_command", side_effect=err):
            resp = client.post("/v2/do", json={
                "session": "s-bad999",
                "command": "screenshot",
            })
        self.assertEqual(resp.status_code, 409)


class ServerDiscoveryTests(unittest.TestCase):
    """Tests for discovery/listing endpoints."""

    def setUp(self) -> None:
        self._env = patch.dict(os.environ, {"SIMEMU_STATE_DIR": _tmpdir})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()

    def test_simulators_list(self) -> None:
        sim = MagicMock()
        sim.__dict__ = {
            "sim_id": "SIM-001",
            "platform": "ios",
            "device_name": "iPhone 16 Pro",
            "runtime": "iOS 18.0",
            "booted": False,
            "real_device": False,
        }
        with patch("simemu.server.state.get_all", return_value={}):
            with patch("simemu.server.list_ios", return_value=[sim]):
                with patch("simemu.server.list_android", return_value=[]):
                    resp = client.get("/simulators")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["device_name"], "iPhone 16 Pro")

    def test_devices_list(self) -> None:
        dev = MagicMock()
        dev.__dict__ = {
            "sim_id": "USB-001",
            "platform": "ios",
            "device_name": "Luke's iPhone (real)",
            "runtime": "iOS 18.4",
            "booted": True,
            "real_device": True,
        }
        with patch("simemu.server.state.get_all", return_value={}):
            with patch("simemu.server.list_real_ios", return_value=[dev]):
                with patch("simemu.server.list_real_android", return_value=[]):
                    resp = client.get("/devices")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["real_device"], True)


class TestV2ConvenienceRoutes(unittest.TestCase):
    """Tests for v2 convenience routes: present, stabilize, verify-install, repair-install, proof."""

    @patch("simemu.server.session_module.do_command", return_value={"status": "presented"})
    def test_v2_present(self, mock_do) -> None:
        resp = client.post("/v2/present/s-abc123")
        self.assertEqual(resp.status_code, 200)
        mock_do.assert_called_once_with("s-abc123", "present", [])

    @patch("simemu.server.session_module.do_command", return_value={"status": "stabilized"})
    def test_v2_stabilize(self, mock_do) -> None:
        resp = client.post("/v2/stabilize/s-abc123")
        self.assertEqual(resp.status_code, 200)
        mock_do.assert_called_once_with("s-abc123", "stabilize", [])

    @patch("simemu.server.session_module.do_command", return_value={"status": "verified"})
    def test_v2_verify_install(self, mock_do) -> None:
        resp = client.post("/v2/verify-install/s-abc123", json={"package": "com.example.app"})
        self.assertEqual(resp.status_code, 200)
        mock_do.assert_called_once_with("s-abc123", "verify-install", ["com.example.app"])

    @patch("simemu.server.session_module.do_command", return_value={"status": "repaired"})
    def test_v2_repair_install(self, mock_do) -> None:
        resp = client.post("/v2/repair-install/s-abc123",
                           json={"package": "com.example.app", "apk_path": "/tmp/app.apk"})
        self.assertEqual(resp.status_code, 200)
        mock_do.assert_called_once_with("s-abc123", "repair-install", ["com.example.app", "/tmp/app.apk"])

    @patch("simemu.server.session_module.do_command", return_value={"status": "proved", "path": "/tmp/p.png"})
    def test_v2_proof(self, mock_do) -> None:
        resp = client.post("/v2/proof/s-abc123",
                           json={"output": "/tmp/p.png", "url": "app://screen", "appearance": "dark", "wait": 1.5})
        self.assertEqual(resp.status_code, 200)
        args = mock_do.call_args[0]
        self.assertEqual(args[0], "s-abc123")
        self.assertEqual(args[1], "proof")
        self.assertIn("-o", args[2])
        self.assertIn("--url", args[2])
        self.assertIn("--appearance", args[2])

    @patch("simemu.server.session_module.do_command", side_effect=SessionError("session_expired", "s-abc123", "expired"))
    def test_v2_convenience_returns_409_on_session_error(self, mock_do) -> None:
        resp = client.post("/v2/present/s-abc123")
        self.assertEqual(resp.status_code, 409)

    @patch("simemu.server.session_module.do_command", side_effect=RuntimeError("boom"))
    def test_v2_convenience_returns_500_on_runtime_error(self, mock_do) -> None:
        resp = client.post("/v2/stabilize/s-abc123")
        self.assertEqual(resp.status_code, 500)


if __name__ == "__main__":
    unittest.main()
