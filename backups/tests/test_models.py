from __future__ import annotations

import os

from django.test import SimpleTestCase

from backups.models import BackupTarget


class BackupTargetPasswordTests(SimpleTestCase):
    def test_sql_password_uses_saved_value_directly(self):
        os.environ["P6_TEST_SQL_PASSWORD"] = "from-env"
        target = BackupTarget(sql_password_env="P6_TEST_SQL_PASSWORD")

        self.assertEqual(target.sql_password, "P6_TEST_SQL_PASSWORD")

    def test_sql_password_trims_saved_value(self):
        target = BackupTarget(sql_password_env="  P@ssword!1  ")

        self.assertEqual(target.sql_password, "P@ssword!1")
