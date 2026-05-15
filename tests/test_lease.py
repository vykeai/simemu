import unittest
from contextlib import contextmanager
from unittest.mock import patch

from simemu.lease import LeaseClaimSpec, claim_device_lease, lease_from_session
from simemu.session import Session, SessionError


def _session(**overrides) -> Session:
    defaults = {
        "session_id": "s-lease1",
        "platform": "ios",
        "form_factor": "phone",
        "os_version": None,
        "real_device": False,
        "label": "codeuctor",
        "status": "active",
        "sim_id": "UDID-001",
        "device_name": "iPhone 16 Pro",
        "agent": "test-agent",
        "created_at": "2026-05-15T10:00:00+00:00",
        "heartbeat_at": "2026-05-15T10:00:00+00:00",
        "expires_at": "2026-05-15T11:00:00+00:00",
        "resolved_os_version": "iOS 26.2",
        "claim_platform": "ios",
        "claim_form_factor": "phone",
    }
    defaults.update(overrides)
    return Session(**defaults)


class LeaseTests(unittest.TestCase):
    def test_lease_from_session_includes_connection_and_boot_state(self) -> None:
        lease = lease_from_session(_session(), host="mac-studio", run_id="run-123", requested_device="iPhone")

        self.assertEqual(lease.lease_id, "s-lease1")
        self.assertEqual(lease.host, "mac-studio")
        self.assertEqual(lease.run_id, "run-123")
        self.assertEqual(lease.requested_device, "iPhone")
        self.assertEqual(lease.device["id"], "UDID-001")
        self.assertEqual(lease.device["name"], "iPhone 16 Pro")
        self.assertEqual(lease.boot["state"], "booted")
        self.assertEqual(lease.connection["identifier_kind"], "simulator_id")
        self.assertEqual(lease.connection["identifier"], "UDID-001")
        self.assertEqual(lease.release_command, "simemu lease release s-lease1")

    def test_android_lease_uses_pinned_adb_serial(self) -> None:
        lease = lease_from_session(_session(platform="android", sim_id="Pixel_API_35", pinned_serial="emulator-5554"))

        self.assertEqual(lease.connection["identifier_kind"], "adb_serial")
        self.assertEqual(lease.connection["identifier"], "emulator-5554")

    def test_claim_device_lease_persists_metadata_without_selecting_by_terminal_state(self) -> None:
        data = {"sessions": {"s-lease1": {"session_id": "s-lease1"}}}
        saved = []

        @contextmanager
        def fake_lock():
            yield data, saved.append

        with patch("simemu.lease.session_module.claim", return_value=_session()) as claim_mock:
            with patch("simemu.lease.session_module._locked_sessions", fake_lock):
                lease = claim_device_lease(
                    LeaseClaimSpec(
                        platform="ios",
                        host="mac-studio",
                        run_id="run-123",
                        device="iPhone 16 Pro",
                        expires_in_seconds=120,
                    )
                )

        claim_mock.assert_called_once()
        claim_spec = claim_mock.call_args.args[0]
        self.assertEqual(claim_spec.device_selector, "iPhone 16 Pro")
        self.assertEqual(lease.host, "mac-studio")
        self.assertEqual(lease.run_id, "run-123")
        self.assertEqual(data["sessions"]["s-lease1"]["lease"]["run_id"], "run-123")
        self.assertEqual(saved[-1], data)

    def test_release_missing_lease_returns_session_error(self) -> None:
        from simemu.lease import release_device_lease

        with patch("simemu.lease.session_module.get_session", return_value=None):
            with self.assertRaises(SessionError) as ctx:
                release_device_lease("s-missing")

        self.assertEqual(ctx.exception.error_type, "lease_not_found")

    def test_release_device_lease_preserves_codeuctor_metadata(self) -> None:
        from simemu.lease import release_device_lease

        raw = {
            "sessions": {
                "s-lease1": {
                    "lease": {
                        "host": "mac-studio",
                        "run_id": "run-123",
                        "requested_device": "iPhone",
                        "expires_at": "2026-05-15T12:00:00+00:00",
                    }
                }
            }
        }
        with patch("simemu.lease.session_module.get_session", return_value=_session()):
            with patch("simemu.lease.session_module._read_sessions_raw", return_value=raw):
                with patch("simemu.lease.session_module.release", return_value=_session(status="released")):
                    lease = release_device_lease("s-lease1")

        self.assertEqual(lease.status, "released")
        self.assertEqual(lease.host, "mac-studio")
        self.assertEqual(lease.run_id, "run-123")


if __name__ == "__main__":
    unittest.main()
