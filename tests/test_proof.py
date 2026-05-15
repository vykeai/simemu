import hashlib
import tempfile
import unittest
from pathlib import Path

from simemu.proof import artifact_file, mobile_proof_artifact, proof_failure_class
from simemu.session import Session


def _session() -> Session:
    return Session(
        session_id="s-proof1",
        platform="ios",
        form_factor="phone",
        os_version=None,
        real_device=False,
        label="",
        status="active",
        sim_id="UDID-001",
        device_name="iPhone 16 Pro",
        agent="test",
        created_at="2026-05-15T10:00:00+00:00",
        heartbeat_at="2026-05-15T10:00:00+00:00",
        expires_at="2026-05-15T11:00:00+00:00",
        resolved_os_version="iOS 26.2",
    )


class ProofArtifactTests(unittest.TestCase):
    def test_artifact_file_hashes_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "proof.png"
            path.write_bytes(b"proof-bytes")
            artifact = artifact_file(str(path))

        self.assertTrue(artifact.exists)
        self.assertEqual(artifact.sha256, hashlib.sha256(b"proof-bytes").hexdigest())
        self.assertEqual(artifact.size_bytes, len(b"proof-bytes"))

    def test_mobile_proof_artifact_is_consumer_ready(self) -> None:
        artifact = mobile_proof_artifact(
            kind="screenshot-proof",
            session=_session(),
            output_path="/tmp/missing-proof.png",
            status="proved",
            build_path=None,
            app="com.example.app",
            metadata={"label": "checkout"},
        )

        self.assertEqual(artifact["schema_version"], "simemu.mobile-proof.v1")
        self.assertEqual(artifact["producer"], "simemu")
        self.assertEqual(artifact["lease"]["session"], "s-proof1")
        self.assertEqual(artifact["device"]["id"], "UDID-001")
        self.assertEqual(artifact["boot"]["state"], "booted")
        self.assertEqual(artifact["screenshot"]["exists"], False)
        self.assertEqual(artifact["build"]["bound"], False)
        self.assertEqual(artifact["consumers"], ["atlas", "sentinel", "proofy"])

    def test_failure_class_is_stable(self) -> None:
        self.assertEqual(proof_failure_class("boot", RuntimeError("adb-ready failed")), "device-boot-failed")
        self.assertEqual(proof_failure_class("capture", RuntimeError("screenshot failed")), "capture-failed")


if __name__ == "__main__":
    unittest.main()
