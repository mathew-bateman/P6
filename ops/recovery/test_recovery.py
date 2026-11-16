"""Focused offline checks for recovery identity, non-overwrite and integrity controls."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ops.recovery import application_recovery as application
from ops.recovery import capture_recovery as host


def response(status, payload):
    row = Mock(status_code=status)
    row.json.return_value = payload
    return row


class RecoveryControlsTests(unittest.TestCase):
    def test_rejects_path_traversal_recovery_ids(self):
        for value in ("../escape", "/tmp/escape", "abc/def", "a b c d", "short"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                application.validate_id(value)
        self.assertEqual(application.validate_id("20261116T120000Z"), "20261116T120000Z")

    def test_metadata_cannot_overwrite_previous_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "proof.json"
            host.json_file(path, {"first": True})
            with self.assertRaises(FileExistsError):
                host.json_file(path, {"first": False})
            self.assertIn("true", path.read_text())

    def test_manifest_hashes_nested_payload_and_excludes_diagnostics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            nested = root / "application" / "sql"
            nested.mkdir(parents=True)
            (nested / "snapshot.bak").write_bytes(b"verified backup payload")
            (root / "capture.log").write_text("private diagnostics")
            (root / "FAILED.json").write_text("private failure")
            result = host.build_manifest(root, recovery_id="recovery_123", excludes=(), containers=[])
            self.assertEqual(result["files"], [{"path": "application/sql/snapshot.bak", "size_bytes": 23, "sha256": hashlib.sha256(b"verified backup payload").hexdigest()}])

    def test_existing_offsite_recovery_id_is_never_reused(self):
        client = Mock(site_id="site", headers={})
        client.session.get.side_effect = [response(200, {"id": "base", "folder": {}}), response(200, {"id": "parent", "folder": {}}), response(200, {"id": "existing", "folder": {}})]
        with self.assertRaises(FileExistsError):
            application.reserve_offsite_folder(client, "recovery_123")
        client.session.post.assert_not_called()

    def test_new_offsite_id_uses_fail_on_conflict(self):
        client = Mock(site_id="site", headers={})
        client.session.get.side_effect = [response(200, {"id": "base", "folder": {}}), response(200, {"id": "parent", "folder": {}}), response(404, {})]
        client.session.post.return_value = response(201, {"id": "new"})
        application.reserve_offsite_folder(client, "recovery_123")
        kwargs = client.session.post.call_args.kwargs
        self.assertEqual(kwargs["json"]["@microsoft.graph.conflictBehavior"], "fail")
        self.assertEqual(kwargs["json"]["name"], "recovery_123")

    def test_missing_offsite_parents_are_created_explicitly(self):
        client = Mock(site_id="site", headers={})
        client.session.get.side_effect = [response(404, {}), response(404, {}), response(404, {})]
        client.session.post.side_effect = [response(201, {"id": "base"}), response(201, {"id": "parent"}), response(201, {"id": "new"})]
        application.reserve_offsite_folder(client, "recovery_123")
        self.assertEqual(client.session.post.call_count, 3)
        self.assertTrue(client.session.post.call_args_list[0].args[0].endswith("/root/children"))
        self.assertTrue(client.session.post.call_args_list[1].args[0].endswith("/items/base/children"))

    def test_large_upload_without_download_url_fetches_completed_metadata(self):
        client = Mock(site_id="site", folder_path="Recovery/test", headers={"Authorization": "private"})
        metadata = {"id": "large-item", "name": "recovery.tar.gz.enc", "@microsoft.graph.downloadUrl": "https://download.example/private"}
        client.session.get.return_value = response(200, metadata)
        item = application.completed_download_item(client, {"id": "large-item", "name": "recovery.tar.gz.enc"}, "recovery.tar.gz.enc")
        self.assertEqual(item, metadata)
        self.assertTrue(client.session.get.call_args.args[0].endswith("/items/large-item"))
        self.assertEqual(client.session.get.call_args.kwargs["headers"], client.headers)

    def test_upload_without_item_id_fetches_completed_file_by_path(self):
        client = Mock(site_id="site", folder_path="P6 Recovery/test", headers={})
        metadata = {"name": "recovery.tar.gz.enc", "@microsoft.graph.downloadUrl": "https://download.example/private"}
        client.session.get.return_value = response(200, metadata)
        self.assertEqual(application.completed_download_item(client, {}, "recovery.tar.gz.enc"), metadata)
        self.assertTrue(client.session.get.call_args.args[0].endswith("/root:/P6%20Recovery/test/recovery.tar.gz.enc"))


if __name__ == "__main__":
    unittest.main()
