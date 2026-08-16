from __future__ import annotations

from django.test import SimpleTestCase

from backups.services.orchestration import _non_negative_int
from backups.services.retention import plan_backup_retention_deletions


def item(item_id: str, name: str) -> dict[str, object]:
    return {"id": item_id, "name": name, "file": {}}


class RetentionTests(SimpleTestCase):
    def test_retention_deletes_old_target_files_and_matching_manifests_only(self):
        deletions = plan_backup_retention_deletions(
            sharepoint_items=[
                item("new", "axialp6_backup_20260428_120000.bak.enc"),
                item("new-manifest", "axialp6_backup_20260428_120000.bak.enc.manifest.json"),
                item("old", "axialp6_backup_20260427_120000.bak.enc"),
                item("old-manifest", "axialp6_backup_20260427_120000.bak.enc.manifest.json"),
                item("other", "p62212_backup_20260427_120000.bak.enc"),
            ],
            target_slug="axialp6",
            keep_daily=1,
            keep_weekly=0,
            keep_monthly=0,
        )
        self.assertEqual(deletions, ["old", "old-manifest"])

    def test_non_negative_int_allows_zero_retention(self):
        self.assertEqual(_non_negative_int(0, 8), 0)
        self.assertEqual(_non_negative_int("0", 8), 0)
        self.assertEqual(_non_negative_int(-1, 8), 8)
        self.assertEqual(_non_negative_int("bad", 8), 8)
