"""Tests for simemu.session — session lifecycle, claim, touch, renew, release."""

import json
import os
import tempfile
import time
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

# Set up temp state dir before importing session module
_tmpdir = tempfile.mkdtemp(prefix="simemu-session-test-")
os.environ["SIMEMU_STATE_DIR"] = _tmpdir
os.environ["SIMEMU_CONFIG_DIR"] = _tmpdir

from simemu.discover import SimulatorInfo
from simemu.session import (
    ClaimSpec,
    Session,
    SessionError,
    _compute_expires_at,
    _gen_session_id,
    _now_iso,
    claim,
    do_command,
    get_active_sessions,
    get_session,
    lifecycle_tick,
    release,
    renew,
    require_session,
    touch,
    IDLE_TIMEOUT,
    PARK_TIMEOUT,
    EXPIRE_TIMEOUT,
    DEFAULT_MEMORY_BUDGET_MB,
    _DEVICE_MEMORY_MB,
)


def _make_sim(
    sim_id: str = "AAA-111",
    platform: str = "ios",
    device_name: str = "iPhone 16 Pro",
    booted: bool = False,
    runtime: str = "iOS 26.2",
    real_device: bool = False,
) -> SimulatorInfo:
    return SimulatorInfo(
        sim_id=sim_id,
        platform=platform,
        device_name=device_name,
        booted=booted,
        runtime=runtime,
        real_device=real_device,
    )


class TestGenSessionId(unittest.TestCase):
    def test_gen_session_id(self) -> None:
        sid = _gen_session_id()
        self.assertTrue(sid.startswith("s-"))
        self.assertEqual(len(sid), 8)  # "s-" + 6 hex chars

    def test_gen_session_id_unique(self) -> None:
        ids = {_gen_session_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)


class TestClaimSpec(unittest.TestCase):
    def test_to_claim_command(self) -> None:
        spec = ClaimSpec(platform="ios")
        cmd = spec.to_claim_command()
        self.assertEqual(cmd, "simemu claim ios")

    def test_to_claim_command_with_all_options(self) -> None:
        spec = ClaimSpec(
            platform="android",
            form_factor="tablet",
            os_version="14",
            real_device=True,
            label="my-test",
        )
        cmd = spec.to_claim_command()
        self.assertIn("simemu claim android", cmd)
        self.assertIn("--version 14", cmd)
        self.assertIn("--form-factor tablet", cmd)
        self.assertIn("--real", cmd)
        self.assertIn("--label 'my-test'", cmd)

    def test_to_claim_command_with_visible(self) -> None:
        spec = ClaimSpec(platform="ios", visible=True)
        cmd = spec.to_claim_command()
        self.assertIn("--visible", cmd)


class TestSession(unittest.TestCase):
    def _make_session(self, **overrides) -> Session:
        now = _now_iso()
        defaults = {
            "session_id": "s-abc123",
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "test-label",
            "status": "active",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test-agent",
            "created_at": now,
            "heartbeat_at": now,
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
        }
        defaults.update(overrides)
        return Session(**defaults)

    def test_to_agent_json(self) -> None:
        session = self._make_session()
        j = session.to_agent_json()
        self.assertEqual(j["session"], "s-abc123")
        self.assertEqual(j["platform"], "ios")
        self.assertEqual(j["form_factor"], "phone")
        self.assertEqual(j["status"], "active")
        self.assertEqual(j["label"], "test-label")
        # Must NOT contain internal fields
        self.assertNotIn("sim_id", j)
        self.assertNotIn("device_name", j)
        self.assertNotIn("agent", j)
        self.assertNotIn("heartbeat_at", j)

    def test_to_agent_json_os_version_fallback(self) -> None:
        session = self._make_session(resolved_os_version=None, os_version=None)
        j = session.to_agent_json()
        self.assertEqual(j["os_version"], "latest")

    def test_reclaim_command(self) -> None:
        session = self._make_session(claim_platform="ios", claim_form_factor="phone")
        cmd = session.reclaim_command()
        self.assertIn("simemu claim ios", cmd)


class TestComputeExpiresAt(unittest.TestCase):
    def test_active(self) -> None:
        now = _now_iso()
        expires = _compute_expires_at("active", now)
        hb = datetime.fromisoformat(now)
        exp = datetime.fromisoformat(expires)
        diff = (exp - hb).total_seconds()
        self.assertAlmostEqual(diff, IDLE_TIMEOUT, delta=1)

    def test_idle(self) -> None:
        now = _now_iso()
        expires = _compute_expires_at("idle", now)
        hb = datetime.fromisoformat(now)
        exp = datetime.fromisoformat(expires)
        diff = (exp - hb).total_seconds()
        self.assertAlmostEqual(diff, IDLE_TIMEOUT + PARK_TIMEOUT, delta=1)

    def test_parked(self) -> None:
        now = _now_iso()
        expires = _compute_expires_at("parked", now)
        hb = datetime.fromisoformat(now)
        exp = datetime.fromisoformat(expires)
        diff = (exp - hb).total_seconds()
        self.assertAlmostEqual(diff, EXPIRE_TIMEOUT, delta=1)

    def test_unknown_status_returns_heartbeat(self) -> None:
        now = _now_iso()
        expires = _compute_expires_at("released", now)
        self.assertEqual(expires, now)


class TestClaim(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-claim-test-")
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

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_creates_session(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=False)
        spec = ClaimSpec(platform="ios")
        session = claim(spec)

        self.assertTrue(session.session_id.startswith("s-"))
        self.assertEqual(session.platform, "ios")
        self.assertEqual(session.status, "active")
        self.assertEqual(session.sim_id, "AAA-111")
        self.assertEqual(session.device_name, "iPhone 16 Pro")

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_boots_device(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=False)
        spec = ClaimSpec(platform="ios")
        claim(spec)
        mock_boot.assert_called_once_with("AAA-111")

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_skips_boot_for_booted_device(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=True)
        spec = ClaimSpec(platform="ios")
        claim(spec)
        mock_boot.assert_not_called()

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_skips_boot_for_real_device(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=False, real_device=True)
        spec = ClaimSpec(platform="ios")
        claim(spec)
        mock_boot.assert_not_called()

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.android.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_boots_android_headless(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(
            sim_id="Pixel_7", platform="android", device_name="Pixel 7",
            booted=False, runtime="API 35",
        )
        spec = ClaimSpec(platform="android")
        claim(spec)
        mock_boot.assert_called_once_with("Pixel_7", headless=True)

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_rejects_duplicate_sim_id(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=True)
        spec = ClaimSpec(platform="ios")
        claim(spec)

        # Second claim for same device should fail
        mock_find.return_value = _make_sim(booted=True)
        with self.assertRaises(SessionError) as ctx:
            claim(spec)
        self.assertEqual(ctx.exception.error_type, "device_already_claimed")

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_applies_window_mode(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=True)
        spec = ClaimSpec(platform="ios", visible=False)
        claim(spec)
        mock_win.assert_called_once_with("AAA-111", "ios", "iPhone 16 Pro")

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    def test_claim_skips_window_mode_when_visible(self, mock_maint, mock_find, mock_boot, mock_win) -> None:
        mock_find.return_value = _make_sim(booted=True)
        spec = ClaimSpec(platform="ios", visible=True)
        claim(spec)
        mock_win.assert_not_called()


class TestTouch(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-touch-test-")
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

    def _seed_session(self, session_id: str = "s-aaa111", **overrides) -> None:
        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "status": "active",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "expires_at": _compute_expires_at("active", now),
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        session_data.update(overrides)
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = {"sessions": {session_id: session_data}}
        sf.write_text(json.dumps(data))

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_touch_updates_heartbeat(self, mock_serial) -> None:
        self._seed_session()
        old = get_session("s-aaa111")
        time.sleep(0.01)
        updated = touch("s-aaa111")
        self.assertNotEqual(old.heartbeat_at, updated.heartbeat_at)
        self.assertEqual(updated.status, "active")

    @patch("simemu.session.ios.boot")
    def test_touch_reboots_parked_session(self, mock_boot) -> None:
        self._seed_session(status="parked")
        session = touch("s-aaa111")
        mock_boot.assert_called_once_with("AAA-111")
        self.assertEqual(session.status, "active")

    @patch("simemu.session.android.boot")
    def test_touch_reboots_parked_android_session(self, mock_boot) -> None:
        self._seed_session(status="parked", platform="android", sim_id="Pixel_7")
        session = touch("s-aaa111")
        mock_boot.assert_called_once_with("Pixel_7", headless=True)
        self.assertEqual(session.status, "active")

    def test_touch_raises_for_expired_session(self) -> None:
        self._seed_session(status="expired")
        with self.assertRaises(SessionError) as ctx:
            touch("s-aaa111")
        self.assertEqual(ctx.exception.error_type, "session_expired")

    def test_touch_raises_for_nonexistent_session(self) -> None:
        with self.assertRaises(SessionError) as ctx:
            touch("s-doesnt-exist")
        self.assertEqual(ctx.exception.error_type, "session_not_found")

    def test_touch_raises_for_released_session(self) -> None:
        self._seed_session(status="released")
        with self.assertRaises(SessionError) as ctx:
            touch("s-aaa111")
        self.assertEqual(ctx.exception.error_type, "session_released")


class TestRealDeviceRecovery(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-real-test-")
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

    def _seed_real_session(self, platform="ios") -> None:
        from simemu.session import _write_sessions_raw, _now_iso, _compute_expires_at
        now = _now_iso()
        _write_sessions_raw({"sessions": {
            "s-real01": {
                "session_id": "s-real01", "platform": platform,
                "form_factor": "phone", "os_version": None,
                "real_device": True, "label": "", "status": "active",
                "sim_id": "REAL-UDID-001", "device_name": "iPhone 15 (real)",
                "agent": "test", "created_at": now, "heartbeat_at": now,
                "expires_at": _compute_expires_at("active", now),
                "resolved_os_version": "iOS 18.2",
                "claim_platform": platform, "claim_form_factor": "phone",
                "claim_os_version": None, "claim_real_device": True, "claim_label": "",
            }
        }})

    @patch("simemu.session.device.list_android_devices", return_value=[])
    def test_touch_raises_when_real_android_disconnected(self, mock_list_devices) -> None:
        self._seed_real_session(platform="android")
        with self.assertRaises(RuntimeError) as ctx:
            touch("s-real01")
        self.assertIn("no longer connected", str(ctx.exception))

    @patch("simemu.session.device.list_android_devices", return_value=[MagicMock(device_id="REAL-UDID-001")])
    def test_touch_succeeds_when_real_android_connected(self, mock_list_devices) -> None:
        self._seed_real_session(platform="android")
        session = touch("s-real01")
        self.assertEqual(session.status, "active")

    @patch("simemu.discover.list_real_ios", return_value=[])
    def test_touch_raises_when_real_ios_disconnected(self, mock_list) -> None:
        self._seed_real_session(platform="ios")
        with self.assertRaises(RuntimeError) as ctx:
            touch("s-real01")
        self.assertIn("no longer connected", str(ctx.exception))


class TestRenew(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-renew-test-")
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

    def _seed_session(self, session_id: str = "s-aaa111", **overrides) -> None:
        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "status": "active",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "expires_at": _compute_expires_at("active", now),
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        session_data.update(overrides)
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = {"sessions": {session_id: session_data}}
        sf.write_text(json.dumps(data))

    def test_renew_extends_session(self) -> None:
        self._seed_session()
        old = get_session("s-aaa111")
        time.sleep(0.01)
        renewed = renew("s-aaa111")
        self.assertNotEqual(old.heartbeat_at, renewed.heartbeat_at)
        self.assertEqual(renewed.status, "active")


class TestRelease(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-release-test-")
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

    def _seed_session(self, session_id: str = "s-aaa111", **overrides) -> None:
        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "status": "active",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "expires_at": _compute_expires_at("active", now),
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        session_data.update(overrides)
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = {"sessions": {session_id: session_data}}
        sf.write_text(json.dumps(data))

    def test_release_sets_status_released(self) -> None:
        self._seed_session()
        session = release("s-aaa111")
        self.assertEqual(session.status, "released")
        # Verify persisted too
        persisted = get_session("s-aaa111")
        self.assertEqual(persisted.status, "released")

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.erase")
    def test_release_cleans_up_ios_simulator_state(self, mock_erase, mock_window) -> None:
        self._seed_session(visible=True, last_build_artifact="/tmp/build/App.app")
        release("s-aaa111")

        mock_erase.assert_called_once_with("AAA-111")
        mock_window.assert_called_once_with("AAA-111", "ios", "iPhone 16 Pro")

        persisted = json.loads((Path(self.tmpdir.name) / "sessions.json").read_text())
        saved = persisted["sessions"]["s-aaa111"]
        self.assertFalse(saved["visible"])
        self.assertNotIn("last_build_artifact", saved)

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.android.erase")
    def test_release_cleans_up_android_simulator_state(self, mock_erase, mock_window) -> None:
        self._seed_session(
            session_id="s-droid1",
            platform="android",
            sim_id="Pixel_7",
            device_name="Pixel 7",
        )
        release("s-droid1")

        mock_erase.assert_called_once_with("Pixel_7")
        mock_window.assert_called_once_with("Pixel_7", "android", "Pixel 7")

    def test_release_raises_for_nonexistent(self) -> None:
        with self.assertRaises(SessionError) as ctx:
            release("s-nosuch")
        self.assertEqual(ctx.exception.error_type, "session_not_found")


class TestGetSession(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-get-test-")
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_state is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self._old_state
        self.tmpdir.cleanup()

    def test_returns_none_for_missing(self) -> None:
        result = get_session("s-nonexistent")
        self.assertIsNone(result)


class TestGetActiveSessions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-active-test-")
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_state is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self._old_state
        self.tmpdir.cleanup()

    def test_filters_expired(self) -> None:
        now = _now_iso()
        base = {
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        sf = Path(self.tmpdir.name) / "sessions.json"
        fresh_expires_at = _compute_expires_at("active", now)
        data = {
            "sessions": {
                "s-active": {**base, "session_id": "s-active", "status": "active",
                             "expires_at": fresh_expires_at},
                "s-idle": {**base, "session_id": "s-idle", "status": "idle",
                           "sim_id": "BBB-222", "expires_at": _compute_expires_at("idle", now)},
                "s-expired": {**base, "session_id": "s-expired", "status": "expired",
                              "sim_id": "CCC-333", "expires_at": now},
                "s-released": {**base, "session_id": "s-released", "status": "released",
                               "sim_id": "DDD-444", "expires_at": now},
                "s-parked": {**base, "session_id": "s-parked", "status": "parked",
                             "sim_id": "EEE-555", "expires_at": _compute_expires_at("parked", now)},
            }
        }
        sf.write_text(json.dumps(data))

        active = get_active_sessions()
        self.assertIn("s-active", active)
        self.assertIn("s-idle", active)
        self.assertIn("s-parked", active)
        self.assertNotIn("s-expired", active)
        self.assertNotIn("s-released", active)

    def test_excludes_effectively_expired_active_sessions(self) -> None:
        now = _now_iso()
        expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        base = {
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        sf = Path(self.tmpdir.name) / "sessions.json"
        sf.write_text(json.dumps({
            "sessions": {
                "s-active-fresh": {
                    **base,
                    "session_id": "s-active-fresh",
                    "status": "active",
                    "expires_at": _compute_expires_at("active", now),
                },
                "s-active-stale": {
                    **base,
                    "session_id": "s-active-stale",
                    "status": "active",
                    "sim_id": "BBB-222",
                    "expires_at": expired_at,
                },
            }
        }))

        active = get_active_sessions()
        self.assertIn("s-active-fresh", active)
        self.assertNotIn("s-active-stale", active)

    def test_require_session_marks_effectively_expired_session(self) -> None:
        now = _now_iso()
        expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        sf = Path(self.tmpdir.name) / "sessions.json"
        sf.write_text(json.dumps({
            "sessions": {
                "s-expiring": {
                    "session_id": "s-expiring",
                    "platform": "ios",
                    "form_factor": "phone",
                    "os_version": None,
                    "real_device": False,
                    "label": "",
                    "status": "active",
                    "sim_id": "AAA-111",
                    "device_name": "iPhone 16 Pro",
                    "agent": "test",
                    "created_at": now,
                    "heartbeat_at": now,
                    "expires_at": expired_at,
                    "resolved_os_version": "iOS 26.2",
                    "claim_platform": "ios",
                    "claim_form_factor": "phone",
                    "claim_os_version": None,
                    "claim_real_device": False,
                    "claim_label": "",
                }
            }
        }))

        with self.assertRaises(SessionError) as ctx:
            require_session("s-expiring")
        self.assertEqual(ctx.exception.error_type, "session_expired")

        data = json.loads(sf.read_text())
        self.assertEqual(data["sessions"]["s-expiring"]["status"], "expired")


class TestLifecycleTick(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-lifecycle-test-")
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        os.environ["SIMEMU_STATE_DIR"] = self.tmpdir.name

    def tearDown(self) -> None:
        if self._old_state is None:
            os.environ.pop("SIMEMU_STATE_DIR", None)
        else:
            os.environ["SIMEMU_STATE_DIR"] = self._old_state
        self.tmpdir.cleanup()

    def _seed(self, session_id: str, status: str, idle_seconds: float) -> None:
        heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=idle_seconds)).isoformat()
        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "status": status,
            "sim_id": f"SIM-{session_id}",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": heartbeat,
            "expires_at": _compute_expires_at(status, heartbeat),
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        sf = Path(self.tmpdir.name) / "sessions.json"
        if sf.exists():
            data = json.loads(sf.read_text())
        else:
            data = {"sessions": {}}
        data["sessions"][session_id] = session_data
        sf.write_text(json.dumps(data))

    def test_transitions_active_to_idle(self) -> None:
        self._seed("s-test", "active", IDLE_TIMEOUT + 60)
        changed = lifecycle_tick()
        self.assertIn("s-test", changed)
        session = get_session("s-test")
        self.assertEqual(session.status, "idle")

    @patch("simemu.session.ios.shutdown")
    def test_transitions_idle_to_parked(self, mock_shutdown) -> None:
        self._seed("s-test", "idle", IDLE_TIMEOUT + PARK_TIMEOUT + 60)
        changed = lifecycle_tick()
        self.assertIn("s-test", changed)
        session = get_session("s-test")
        self.assertEqual(session.status, "parked")

    @patch("simemu.session.ios.shutdown")
    def test_parks_and_shuts_down(self, mock_shutdown) -> None:
        self._seed("s-test", "idle", IDLE_TIMEOUT + PARK_TIMEOUT + 60)
        lifecycle_tick()
        mock_shutdown.assert_called_once_with("SIM-s-test")

    def test_expires_old_sessions(self) -> None:
        self._seed("s-test", "active", EXPIRE_TIMEOUT + 60)
        changed = lifecycle_tick()
        self.assertIn("s-test", changed)
        session = get_session("s-test")
        self.assertEqual(session.status, "expired")

    def test_skips_already_expired(self) -> None:
        self._seed("s-test", "expired", EXPIRE_TIMEOUT + 1000)
        changed = lifecycle_tick()
        self.assertNotIn("s-test", changed)

    def test_skips_already_released(self) -> None:
        self._seed("s-test", "released", EXPIRE_TIMEOUT + 1000)
        changed = lifecycle_tick()
        self.assertNotIn("s-test", changed)

    def test_no_transition_when_recent(self) -> None:
        self._seed("s-test", "active", 10)  # 10 seconds ago
        changed = lifecycle_tick()
        self.assertEqual(changed, [])


class TestDoCommand(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-do-test-")
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

    def _seed_session(self, session_id: str = "s-aaa111", **overrides) -> None:
        now = _now_iso()
        session_data = {
            "session_id": session_id,
            "platform": "ios",
            "form_factor": "phone",
            "os_version": None,
            "real_device": False,
            "label": "",
            "status": "active",
            "sim_id": "AAA-111",
            "device_name": "iPhone 16 Pro",
            "agent": "test",
            "created_at": now,
            "heartbeat_at": now,
            "expires_at": _compute_expires_at("active", now),
            "resolved_os_version": "iOS 26.2",
            "claim_platform": "ios",
            "claim_form_factor": "phone",
            "claim_os_version": None,
            "claim_real_device": False,
            "claim_label": "",
        }
        session_data.update(overrides)
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = {"sessions": {session_id: session_data}}
        sf.write_text(json.dumps(data))

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_command_done_releases(self, mock_serial) -> None:
        self._seed_session()
        result = do_command("s-aaa111", "done", [])
        self.assertEqual(result["status"], "released")
        session = get_session("s-aaa111")
        self.assertEqual(session.status, "released")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_command_boot_touches(self, mock_serial) -> None:
        self._seed_session()
        result = do_command("s-aaa111", "boot", [])
        self.assertIn("session", result)

    @patch("subprocess.run")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_command_show_sets_visible(self, mock_serial, mock_run) -> None:
        self._seed_session()
        result = do_command("s-aaa111", "show", [])
        self.assertEqual(result["status"], "visible")

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_command_hide_sets_invisible(self, mock_serial, mock_win) -> None:
        self._seed_session()
        result = do_command("s-aaa111", "hide", [])
        self.assertEqual(result["status"], "invisible")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_command_renew(self, mock_serial) -> None:
        self._seed_session()
        result = do_command("s-aaa111", "renew", [])
        self.assertIn("session", result)
        self.assertEqual(result["status"], "active")

    @patch("simemu.session.android.get_android_serial", return_value="emulator-5554")
    def test_do_command_unknown_raises(self, mock_serial) -> None:
        self._seed_session()
        with self.assertRaises(RuntimeError) as ctx:
            do_command("s-aaa111", "fly-to-moon", [])
        self.assertIn("Unknown command", str(ctx.exception))
        self.assertIn("fly-to-moon", str(ctx.exception))


class TestSessionError(unittest.TestCase):
    def test_to_json(self) -> None:
        err = SessionError(
            error="session_not_found",
            session="s-abc123",
            hint="Session not found",
            custom_field="extra",
        )
        j = err.to_json()
        self.assertEqual(j["error"], "session_not_found")
        self.assertEqual(j["session"], "s-abc123")
        self.assertEqual(j["hint"], "Session not found")
        self.assertEqual(j["custom_field"], "extra")

    def test_is_runtime_error(self) -> None:
        err = SessionError(error="test", session="s-1", hint="test hint")
        self.assertIsInstance(err, RuntimeError)
        self.assertEqual(str(err), "test hint")


class TestMemoryBudget(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory(prefix="simemu-mem-test-")
        self._old_state = os.environ.get("SIMEMU_STATE_DIR")
        self._old_config = os.environ.get("SIMEMU_CONFIG_DIR")
        self._old_budget = os.environ.get("SIMEMU_MEMORY_BUDGET_MB")
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
        if self._old_budget is None:
            os.environ.pop("SIMEMU_MEMORY_BUDGET_MB", None)
        else:
            os.environ["SIMEMU_MEMORY_BUDGET_MB"] = self._old_budget
        self.tmpdir.cleanup()

    def _seed_sessions(self, count: int, platform: str = "ios", status: str = "active") -> None:
        now = _now_iso()
        sf = Path(self.tmpdir.name) / "sessions.json"
        data = {"sessions": {}}
        for i in range(count):
            sid = f"s-mem{i:03d}"
            heartbeat = (datetime.now(timezone.utc) - timedelta(seconds=i * 100)).isoformat()
            data["sessions"][sid] = {
                "session_id": sid,
                "platform": platform,
                "form_factor": "phone",
                "os_version": None,
                "real_device": False,
                "label": "",
                "status": status,
                "sim_id": f"SIM-{i}",
                "device_name": f"Device {i}",
                "agent": "test",
                "created_at": now,
                "heartbeat_at": heartbeat,
                "expires_at": _compute_expires_at(status, heartbeat),
                "resolved_os_version": "iOS 26.2",
                "claim_platform": platform,
                "claim_form_factor": "phone",
                "claim_os_version": None,
                "claim_real_device": False,
                "claim_label": "",
            }
        sf.write_text(json.dumps(data))

    @patch("simemu.session.window_mgr.apply_window_mode")
    @patch("simemu.session.ios.boot")
    @patch("simemu.session.find_best_device")
    @patch("simemu.session.state.check_maintenance")
    @patch("simemu.session.ios.shutdown")
    def test_memory_budget_enforcement(
        self, mock_shutdown, mock_maint, mock_find, mock_boot, mock_win
    ) -> None:
        # Set a very small budget: 4GB
        os.environ["SIMEMU_MEMORY_BUDGET_MB"] = "4096"
        # Seed 2 idle iOS sessions (2048 MB each = 4096 MB used)
        self._seed_sessions(2, platform="ios", status="idle")
        # Attempting to claim should park idle sessions to make room
        mock_find.return_value = _make_sim(sim_id="NEW-SIM", booted=True)
        spec = ClaimSpec(platform="ios")
        session = claim(spec)
        self.assertEqual(session.status, "active")
        # At least one shutdown should have been called
        self.assertTrue(mock_shutdown.called)


if __name__ == "__main__":
    unittest.main()
