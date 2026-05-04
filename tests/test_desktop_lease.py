"""Tests for simemu.desktop_lease — scouty coordination."""

import json
import unittest
from unittest.mock import MagicMock, patch

from simemu.desktop_lease import DesktopLease, desktop_lease, is_available


class TestDesktopLease(unittest.TestCase):
    @patch("simemu.desktop_lease._json_request")
    def test_lease_lifecycle(self, mock_req) -> None:
        mock_req.side_effect = [
            {"lease_id": "lease-001", "countdown_remaining_seconds": 0},  # request
            {},  # activate
            {},  # release
        ]
        with DesktopLease("tap", "iPhone 17 Pro", "ios", session_id="s-abc") as lease:
            self.assertTrue(lease.enabled)
            self.assertEqual(lease.lease_id, "lease-001")
        # Verify release was called
        self.assertEqual(mock_req.call_count, 3)
        last_call = mock_req.call_args_list[2]
        self.assertIn("/desktop/lease/release", last_call[0][1])

    @patch("simemu.desktop_lease._json_request", side_effect=ConnectionRefusedError)
    def test_degrades_gracefully_when_scouty_down(self, mock_req) -> None:
        with DesktopLease("tap", "iPhone 17 Pro", "ios") as lease:
            self.assertFalse(lease.enabled)
            self.assertIsNone(lease.lease_id)

    @patch("simemu.desktop_lease._json_request")
    def test_update_sends_metadata(self, mock_req) -> None:
        mock_req.side_effect = [
            {"lease_id": "lease-002", "countdown_remaining_seconds": 0},
            {},  # activate
            {},  # update
            {},  # release
        ]
        with DesktopLease("swipe", "Pixel 8", "android") as lease:
            lease.update(stage="Swiping", coordinates="100,200→300,400")
        update_call = mock_req.call_args_list[2]
        self.assertIn("/desktop/lease/update", update_call[0][1])

    @patch("simemu.desktop_lease._json_request")
    def test_update_noop_when_no_lease(self, mock_req) -> None:
        mock_req.side_effect = ConnectionRefusedError
        with DesktopLease("tap", "iPhone 17", "ios") as lease:
            lease.update(stage="test")  # should not raise

    @patch("simemu.desktop_lease._json_request")
    def test_context_manager_convenience(self, mock_req) -> None:
        mock_req.side_effect = [
            {"lease_id": "lease-003", "countdown_remaining_seconds": 0},
            {},  # activate
            {},  # release
        ]
        with desktop_lease("focus", "iPad Pro", "ios", session_id="s-xyz") as lease:
            self.assertTrue(lease.enabled)

    @patch("simemu.desktop_lease._json_request")
    def test_payload_includes_session_and_action_emoji(self, mock_req) -> None:
        mock_req.side_effect = [
            {"lease_id": "l-004", "countdown_remaining_seconds": 0},
            {},
            {},
        ]
        with DesktopLease("tap", "iPhone 17", "ios", session_id="s-test"):
            pass
        first_call = mock_req.call_args_list[0]
        self.assertEqual(first_call[0][0], "POST")
        self.assertIn("/desktop/lease/request", first_call[0][1])
        # Payload is the third positional arg
        payload = first_call[0][2]
        self.assertEqual(payload["session"], "s-test")
        self.assertEqual(payload["action"], "tap")
        self.assertIn("action_emoji", payload)


class TestIsAvailable(unittest.TestCase):
    @patch("simemu.desktop_lease._json_request", return_value={"status": "ok"})
    def test_returns_true_when_reachable(self, mock_req) -> None:
        self.assertTrue(is_available())

    @patch("simemu.desktop_lease._json_request", side_effect=ConnectionRefusedError)
    def test_returns_false_when_unreachable(self, mock_req) -> None:
        self.assertFalse(is_available())


if __name__ == "__main__":
    unittest.main()
