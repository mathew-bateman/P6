from __future__ import annotations

from django.test import SimpleTestCase

from backups.services.restore import build_sharepoint_backup_rows
from backups.services.sharepoint import SharePointClient, parse_sharepoint_url


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = payload or {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self):
        self.get_calls = []
        self.post_calls = []
        self.delete_calls = []

    def get(self, url, **kwargs):
        self.get_calls.append((url, kwargs))
        return FakeResponse(payload={"id": "drive-id"})

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        return FakeResponse(status_code=204)

    def delete(self, url, **kwargs):
        self.delete_calls.append((url, kwargs))
        return FakeResponse(status_code=204)


class SharePointTests(SimpleTestCase):
    def test_parse_sharepoint_folder_url_extracts_site_and_folder(self):
        parsed = parse_sharepoint_url(
            "https://axialprojects.sharepoint.com/sites/P6/Shared%20Documents/Backups/P6",
            default_folder="P6 Backups",
        )
        self.assertEqual(parsed["site"], "axialprojects.sharepoint.com:/sites/P6:")
        self.assertEqual(parsed["folder"], "Backups/P6")

    def test_parse_sharepoint_all_items_url_extracts_selected_folder(self):
        parsed = parse_sharepoint_url(
            "https://axialprojects.sharepoint.com/sites/AxialApps/"
            "Shared%20Documents/Forms/AllItems.aspx"
            "?id=%2Fsites%2FAxialApps%2FShared%20Documents%2FAxialP6_Backups%2FV23%2FTraining",
            default_folder="P6 Backups",
        )

        self.assertEqual(parsed["site"], "axialprojects.sharepoint.com:/sites/AxialApps:")
        self.assertEqual(parsed["folder"], "AxialP6_Backups/V23/Training")

    def test_build_rows_only_includes_target_encrypted_backups_with_manifest_status(self):
        rows = build_sharepoint_backup_rows(
            [
                {
                    "id": "1",
                    "name": "axialp6_backup_20260428_120000.bak.enc",
                    "file": {},
                    "size": 1024,
                    "@microsoft.graph.downloadUrl": "backup-url",
                    "lastModifiedDateTime": "2026-04-28T12:00:00Z",
                },
                {
                    "id": "2",
                    "name": "axialp6_backup_20260428_120000.bak.enc.manifest.json",
                    "file": {},
                    "@microsoft.graph.downloadUrl": "manifest-url",
                },
                {
                    "id": "3",
                    "name": "p62212_backup_20260428_120000.bak.enc",
                    "file": {},
                    "@microsoft.graph.downloadUrl": "other-url",
                },
            ],
            target_slug="axialp6",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "axialp6_backup_20260428_120000.bak.enc")
        self.assertEqual(rows[0]["manifest_download_url"], "manifest-url")
        self.assertTrue(rows[0]["has_manifest"])


class SharePointClientDeleteTests(SimpleTestCase):
    def test_delete_item_permanently_deletes_drive_item(self):
        session = FakeSession()
        client = SharePointClient(
            token="token",
            site_id="site-id",
            folder_path="Backups",
            session=session,
        )

        self.assertTrue(client.delete_item("item-id"))

        self.assertEqual(
            session.get_calls[0][0],
            "https://graph.microsoft.com/v1.0/sites/site-id/drive",
        )
        self.assertEqual(
            session.post_calls[0][0],
            "https://graph.microsoft.com/v1.0/drives/drive-id/items/item-id/permanentDelete",
        )
        self.assertEqual(session.delete_calls, [])
