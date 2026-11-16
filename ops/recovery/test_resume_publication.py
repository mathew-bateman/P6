"""Offline checks for immutable publication resume and fail-on-conflict upload."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

from ops.recovery import resume_publication as resume


def response(status, payload):
    row = Mock(status_code=status)
    row.json.return_value = payload
    return row


class PublicationResumeTests(unittest.TestCase):
    def artifact(self, root):
        payload = b"retained database backup"
        manifest = {"recovery_id": "recovery_123", "files": [{"path": "application/sql/test.bak", "sha256": hashlib.sha256(payload).hexdigest(), "size_bytes": len(payload)}]}
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest))
        archive_path = root / "recovery.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            member = tarfile.TarInfo("application/sql/test.bak")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
            archive.add(manifest_path, arcname="manifest.json")
        encrypted = root / "recovery.tar.gz.enc"
        encrypted.write_bytes(b"opaque cipher fixture")
        encryption = {"recovery_id": "recovery_123", "capture_manifest_sha256": resume.sha256(manifest_path), "archive_sha256": resume.sha256(archive_path), "archive_bytes": archive_path.stat().st_size, "encrypted_sha256": resume.sha256(encrypted), "encrypted_bytes": encrypted.stat().st_size}
        (root / "encryption_manifest.json").write_text(json.dumps(encryption))
        return encryption

    def test_validates_archive_without_rewriting_any_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            encryption = self.artifact(root)
            before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()}
            self.assertEqual(resume.validate_artifacts(root, "recovery_123"), encryption)
            self.assertEqual(before, {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.iterdir()})

    def test_refuses_corrupted_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.artifact(root)
            with (root / "recovery.tar.gz").open("ab") as stream:
                stream.write(b"unexpected change")
            with self.assertRaises(RuntimeError):
                resume.validate_artifacts(root, "recovery_123")

    def test_refuses_transfer_sidecar_for_different_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "archive.enc"
            source.write_bytes(b"cipher")
            state = root / "state.json"
            state.write_text(json.dumps({"folder": "different"}))
            client = Mock(folder_path="expected", headers={})
            with self.assertRaises(RuntimeError):
                resume.resumable_upload(client, source, resume.sha256(source), state)
            client.session.post.assert_not_called()

    def test_completed_remote_item_is_reused_without_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "archive.enc"
            source.write_bytes(b"cipher")
            client = Mock(folder_path="expected", headers={})
            with patch.object(resume, "item_at", return_value={"id": "existing"}):
                result = resume.resumable_upload(client, source, resume.sha256(source), Path(temporary) / "state.json")
            self.assertEqual(result, {"id": "existing"})
            client.session.post.assert_not_called()
            client.session.put.assert_not_called()

    def test_creates_new_session_with_fail_on_conflict(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "archive.enc"
            source.write_bytes(b"cipher")
            client = Mock(folder_path="expected", headers={})
            client.session.post.return_value = response(200, {"uploadUrl": "https://upload.example/private"})
            client.session.put.return_value = response(201, {"id": "new"})
            state = Path(temporary) / "state.json"
            with patch.object(resume, "item_at", return_value=None), patch.object(resume, "SMALL_UPLOAD_LIMIT_BYTES", 0):
                result = resume.resumable_upload(client, source, resume.sha256(source), state)
            self.assertEqual(result["id"], "new")
            self.assertEqual(client.session.post.call_args.kwargs["json"]["item"]["@microsoft.graph.conflictBehavior"], "fail")
            self.assertNotIn("upload_url", json.loads(state.read_text()))

    def test_resumes_server_reported_offset(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "archive.enc"
            source.write_bytes(b"a" * (320 * 1024) + b"tail")
            client = Mock(folder_path="expected", headers={})
            client.session.get.return_value = response(200, {"nextExpectedRanges": ["327680-"]})
            client.session.put.return_value = response(201, {"id": "resumed"})
            state = Path(temporary) / "state.json"
            state.write_text(json.dumps({"folder": "expected", "filename": source.name, "sha256": resume.sha256(source), "size_bytes": source.stat().st_size, "upload_url": "https://upload.example/private"}))
            with patch.object(resume, "SMALL_UPLOAD_LIMIT_BYTES", 0):
                result = resume.resumable_upload(client, source, resume.sha256(source), state)
            self.assertEqual(result["id"], "resumed")
            self.assertEqual(client.session.put.call_args.kwargs["data"], b"tail")
            self.assertEqual(client.session.put.call_args.kwargs["headers"]["Content-Range"], "bytes 327680-327683/327684")
            client.session.post.assert_not_called()

    def test_small_metadata_uses_conditional_put(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "encryption_manifest.json"
            source.write_bytes(b'{"verified":true}')
            client = Mock(folder_path="expected", headers={"Authorization": "private"})
            client._content_url.return_value = "https://graph.example/metadata:/content"
            client.session.put.return_value = response(201, {"id": "metadata"})
            with patch.object(resume, "item_at", return_value=None):
                result = resume.resumable_upload(client, source, resume.sha256(source), Path(temporary) / "state.json")
            self.assertEqual(result["id"], "metadata")
            self.assertEqual(client.session.put.call_args.args[0], client._content_url.return_value)
            self.assertEqual(client.session.put.call_args.kwargs["headers"]["If-None-Match"], "*")
            client.session.post.assert_not_called()

    def test_small_metadata_conflict_preserves_existing_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "encryption_manifest.json"
            source.write_bytes(b'{"verified":true}')
            client = Mock(folder_path="expected", headers={})
            client.session.put.return_value = response(412, {})
            with patch.object(resume, "item_at", side_effect=[None, {"id": "existing-metadata"}]):
                result = resume.resumable_upload(client, source, resume.sha256(source), Path(temporary) / "state.json")
            self.assertEqual(result, {"id": "existing-metadata"})
            self.assertEqual(client.session.put.call_args.kwargs["headers"]["If-None-Match"], "*")
            client.session.post.assert_not_called()

    def proof_fixture(self):
        item = {"id": "archive", "eTag": "etag-v1", "cTag": "ctag-v1", "size": 100, "file": {}}
        encryption = {"encrypted_sha256": "cipher-hash", "archive_sha256": "plain-hash", "capture_manifest_sha256": "manifest-hash", "key_sha256": "key-hash", "encrypted_bytes": 100}
        return item, encryption, resume.archive_proof(item, encryption, "recovery_123")

    def test_download_proof_reused_only_for_unchanged_remote_validators(self):
        item, encryption, proof = self.proof_fixture()
        self.assertTrue(resume.archive_proof_matches(proof, item, encryption, "recovery_123"))
        for key in ("id", "eTag", "cTag", "size"):
            with self.subTest(key=key):
                changed = {**item, key: 101 if key == "size" else "changed"}
                self.assertFalse(resume.archive_proof_matches(proof, changed, encryption, "recovery_123"))
                missing = dict(item)
                missing.pop(key)
                self.assertFalse(resume.archive_proof_matches(proof, missing, encryption, "recovery_123"))

    def test_download_proof_rejects_changed_expected_hashes_or_identity(self):
        item, encryption, proof = self.proof_fixture()
        for key in ("encrypted_sha256", "archive_sha256", "capture_manifest_sha256", "key_sha256"):
            with self.subTest(key=key):
                self.assertFalse(resume.archive_proof_matches(proof, item, {**encryption, key: "changed"}, "recovery_123"))
        self.assertFalse(resume.archive_proof_matches(proof, item, encryption, "other_123"))
        self.assertFalse(resume.archive_proof_matches({**proof, "upload_download_decrypt_sha256": "failed"}, item, encryption, "recovery_123"))


if __name__ == "__main__":
    unittest.main()
