from __future__ import annotations

import io
import json
from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from backups.services.capacity_review import build_capacity_report, capacity_card_rows, collect_capacity_report


def sql_row(target=None, **changes):
    return {
        "target": target or SimpleNamespace(name="AxialP6", slug="axialp6", sql_database="Axial", enabled=True),
        "status": "ok", "edition": "Express Edition", "version": "17.0.1000.7",
        "data_used_gb": 12.5, "data_allocated_gb": 20, "limit_gb": 50,
        "remaining_gb": 37.5, "used_percent": 25,
        "log_status": "ok", "log_used_gb": .1, "log_total_gb": 2,
        "log_allocated_gb": 2, "log_used_percent": 5,
        **changes,
    }


def postgres_row(**changes):
    return {
        "engine": "postgresql", "name": "PostgreSQL reporting", "slug": "postgresql-reporting",
        "database_name": "p6_reporting", "version": "16.13", "status": "ok",
        "used_gb": 11.74, "tables_gb": 9, "indexes_gb": 2,
        "storage_status": "ok", "storage_total_gb": 1000, "storage_used_gb": 200,
        "storage_available_gb": 700, "storage_reserved_gb": 100, "storage_usable_gb": 900,
        "storage_used_percent": 200 / 900 * 100,
        **changes,
    }


class CapacityReviewTests(SimpleTestCase):
    def test_combined_report_keeps_database_usage_distinct_from_shared_disk_capacity(self):
        report = build_capacity_report([sql_row(), postgres_row()])
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["level"], "green")
        sql, postgres = report["databases"]
        self.assertEqual((sql["engine"], sql["limit_gb"], sql["used_percent"]), ("sqlserver", 50, 25))
        self.assertEqual((postgres["used_gb"], postgres["tables_gb"], postgres["indexes_gb"]), (11.74, 9, 2))
        self.assertEqual(postgres["storage_used_gb"] + postgres["storage_available_gb"], postgres["storage_usable_gb"])
        self.assertEqual(postgres["storage_usable_gb"] + postgres["storage_reserved_gb"], postgres["storage_total_gb"])
        self.assertAlmostEqual(postgres["storage_used_percent"], 200 / 900 * 100)
        self.assertIn("database 11.74 GB", report["reason"])
        self.assertIn("shared disk 22.2% (700.00 GB available)", report["reason"])

    def test_postgresql_does_not_receive_sql_server_quota_or_log_metrics(self):
        report = build_capacity_report([postgres_row()])
        postgres, = report["databases"]
        self.assertEqual(postgres["limit_status"], "not_applicable")
        self.assertEqual(postgres["log_status"], "not_applicable")
        for key in ("limit_gb", "used_percent", "remaining_gb", "allocated_gb", "log_used_gb", "log_used_percent", "log_allocated_gb", "log_total_gb"):
            self.assertIsNone(postgres[key], key)
        self.assertNotIn("/50", report["reason"])
        self.assertNotIn("log ", report["reason"])

    def test_thresholds_use_each_engines_actual_capacity_measure(self):
        for percent, expected in ((0, "green"), (79.99, "green"), (80, "amber"), (89.99, "amber"), (90, "red"), (100, "red")):
            readings = (
                sql_row(data_used_gb=percent / 2, data_allocated_gb=50, used_percent=percent, remaining_gb=50 - percent / 2),
                sql_row(log_used_gb=2 * percent / 100, log_used_percent=percent),
                postgres_row(storage_used_gb=900 * percent / 100, storage_available_gb=900 * (100 - percent) / 100, storage_used_percent=percent),
            )
            for reading in readings:
                with self.subTest(engine=reading.get("engine", "sqlserver"), percent=percent, log=reading.get("log_used_percent")):
                    report = build_capacity_report([reading])
                    self.assertEqual(report["level"], expected)
                    self.assertEqual(report["databases"][0]["level"], expected)

    def test_postgresql_database_failure_retains_disk_and_healthy_sql_evidence(self):
        report = build_capacity_report([sql_row(), postgres_row(status="unavailable", used_gb=None, tables_gb=None, indexes_gb=None)])
        self.assertEqual(report["level"], "red")
        sql, postgres = report["databases"]
        self.assertEqual(sql["level"], "green")
        self.assertEqual(postgres["storage_available_gb"], 700)
        self.assertEqual(postgres["storage_status"], "ok")
        self.assertTrue(report["reason"].startswith("PostgreSQL reporting:"))
        self.assertIn("database usage unavailable", report["reason"])
        self.assertIn("700.00 GB available", report["reason"])

    def test_postgresql_storage_failure_retains_database_and_healthy_sql_evidence(self):
        report = build_capacity_report([sql_row(), postgres_row(
            storage_status="unavailable", storage_total_gb=None, storage_used_gb=None,
            storage_available_gb=None, storage_reserved_gb=None, storage_usable_gb=None, storage_used_percent=None,
        )])
        self.assertEqual(report["level"], "red")
        sql, postgres = report["databases"]
        self.assertEqual(sql["level"], "green")
        self.assertEqual((postgres["used_gb"], postgres["tables_gb"], postgres["indexes_gb"]), (11.74, 9, 2))
        self.assertIn("database 11.74 GB, shared disk usage unavailable", report["reason"])

    def test_sql_failure_or_missing_limit_and_log_cannot_be_hidden_by_healthy_postgresql(self):
        for changes in (
            {"status": "unavailable", "data_used_gb": None},
            {"limit_gb": None, "used_percent": None},
            {"log_status": "unavailable", "log_used_percent": None, "log_used_gb": None},
        ):
            with self.subTest(changes=changes):
                report = build_capacity_report([postgres_row(), sql_row(**changes)])
                self.assertEqual(report["level"], "red")
                self.assertEqual(report["databases"][0]["level"], "green")
                self.assertTrue(report["reason"].startswith("AxialP6:"))
                self.assertEqual(len(report["databases"]), 2)

    def test_non_finite_or_boolean_sql_measurements_are_unavailable(self):
        for changes in (
            {"data_used_gb": True}, {"data_used_gb": float("inf")},
            {"used_percent": float("nan")}, {"log_used_percent": True},
        ):
            with self.subTest(changes=changes):
                report = build_capacity_report([sql_row(**changes)])
                self.assertEqual(report["level"], "red")
                json.dumps(report, allow_nan=False)

    def test_clamped_visual_bars_preserve_actual_over_limit_measurement(self):
        report = build_capacity_report([sql_row(data_used_gb=55, data_allocated_gb=56, used_percent=110, remaining_gb=0)])
        card, = capacity_card_rows(report)
        self.assertEqual((card["used_gb"], card["used_percent"], card["level"]), (55, 110, "red"))
        self.assertEqual(card["used_bar_percent"], 100)
        self.assertEqual(card["reserved_bar_percent"], 0)
        self.assertEqual(report["databases"][0]["used_percent"], 110)
        self.assertNotIn("used_bar_percent", report["databases"][0])

    def test_regular_visual_bar_distinguishes_used_allocated_unused_and_remaining(self):
        card, = capacity_card_rows(build_capacity_report([sql_row()]))
        self.assertEqual(card["used_bar_percent"], 25)
        self.assertEqual(card["reserved_bar_percent"], 15)
        self.assertEqual(100 - card["used_bar_percent"] - card["reserved_bar_percent"], 60)

    def test_empty_configuration_is_unavailable_instead_of_green(self):
        report = build_capacity_report([])
        self.assertEqual(report["level"], "red")
        self.assertEqual(report["databases"], [])
        self.assertEqual(capacity_card_rows(report), [])


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class CapacityReviewCollectionTests(TestCase):
    @patch("backups.services.capacity_review.collect_postgres_capacity", return_value=None)
    @patch("backups.services.capacity_review.collect_database_capacity", return_value=[])
    def test_optional_postgresql_disabled_keeps_an_empty_configuration_explicit(self, sql, postgres):
        report = collect_capacity_report()
        self.assertEqual(report["databases"], [])
        self.assertEqual(report["level"], "red")
        sql.assert_called_once()
        self.assertTrue(sql.call_args.kwargs["include_log_usage"])
        postgres.assert_called_once_with()

    def test_page_and_command_use_the_same_sql_and_postgresql_collection_and_classification(self):
        staff = get_user_model().objects.create_user(username="shared.capacity", is_staff=True)
        self.client.force_login(staff)
        observed = datetime(2026, 11, 16, 9, 30, tzinfo=datetime_timezone.utc)
        rows = [sql_row()]
        output = io.StringIO()
        with patch("backups.services.capacity_review.collect_database_capacity", side_effect=lambda *args, **kwargs: list(rows)) as sql, patch("backups.services.capacity_review.collect_postgres_capacity", return_value=postgres_row()) as postgres, patch("backups.services.capacity_review.timezone.now", return_value=observed):
            call_command("check_database_capacity", stdout=output)
            response = self.client.get(reverse("database_capacity"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["capacity_report"], json.loads(output.getvalue()))
        self.assertEqual(sql.call_count, 2)
        self.assertEqual(postgres.call_count, 2)
        self.assertEqual(response.context["databases"], capacity_card_rows(json.loads(output.getvalue())))
