from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from backups.management.commands.check_database_capacity import build_capacity_report
from backups.models import BackupTarget


class CapacityProbeSummaryTests(SimpleTestCase):
    def row(self, **changes):
        row = {
            "target": SimpleNamespace(name="AxialP6", slug="axialp6"),
            "status": "ok", "edition": "Express Edition", "limit_gb": 50,
            "data_used_gb": 5, "data_allocated_gb": 50, "used_percent": 10,
            "log_status": "ok", "log_used_gb": 1, "log_allocated_gb": 8, "log_total_gb": 8,
            "log_used_percent": 12.5,
        }
        row.update(changes)
        if "used_percent" in changes and "data_used_gb" not in changes:
            percent = changes["used_percent"]
            row["data_used_gb"] = percent / 2 if percent is not None else 5
        if "log_used_percent" in changes and "log_used_gb" not in changes:
            percent = changes["log_used_percent"]
            row["log_used_gb"] = percent / 100 * row["log_total_gb"] if percent is not None else None
        return row

    def test_healthy_readings_are_green(self):
        report = build_capacity_report([self.row()])
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["level"], "green")
        self.assertIn("data 10.0% (5.00/50 GB)", report["reason"])
        self.assertIn("log 12.5%", report["reason"])
        self.assertEqual(report["databases"][0]["used_gb"], 5)
        self.assertEqual(report["databases"][0]["log_total_gb"], 8)
        self.assertNotIn("target", report["databases"][0])

    def test_data_and_log_thresholds_apply_independently(self):
        for field in ("used_percent", "log_used_percent"):
            for percent, expected in ((79.99, "green"), (80, "amber"), (89.99, "amber"), (90, "red"), (100, "red")):
                with self.subTest(field=field, percent=percent):
                    report = build_capacity_report([self.row(**{field: percent})])
                    self.assertEqual(report["level"], expected)

    def test_partial_failure_is_red_and_prioritised_over_healthy_targets(self):
        unavailable = self.row(
            target=SimpleNamespace(name="Other database", slug="other"),
            log_status="unavailable", log_used_percent=None, log_used_gb=None,
        )
        report = build_capacity_report([self.row(), unavailable])
        self.assertEqual(report["level"], "red")
        self.assertTrue(report["reason"].startswith("Other database:"))
        self.assertIn("log usage unavailable", report["reason"])
        self.assertEqual(len(report["databases"]), 2)

    def test_missing_data_or_unconfirmed_express_limit_is_red(self):
        for changes in (
            {"status": "unavailable", "data_used_gb": None, "used_percent": None},
            {"limit_gb": None, "used_percent": None},
            {"used_percent": None},
            {"log_used_percent": float("nan")},
        ):
            with self.subTest(changes=changes):
                report = build_capacity_report([self.row(**changes)])
                self.assertEqual(report["level"], "red")
                json.dumps(report, allow_nan=False)

    def test_non_express_has_no_fabricated_cap(self):
        report = build_capacity_report([self.row(edition="Standard Edition", limit_gb=None, used_percent=None)])
        self.assertEqual(report["level"], "green")
        self.assertIsNone(report["databases"][0]["limit_gb"])
        self.assertIn("data 5.00 GB", report["reason"])

    def test_no_targets_is_red(self):
        report = build_capacity_report([])
        self.assertEqual(report["level"], "red")
        self.assertEqual(report["databases"], [])

    def test_reason_is_bounded_and_uses_names_without_connections(self):
        rows = [self.row(target=SimpleNamespace(name="Example\n database " + "x" * 120, slug=f"db-{i}")) for i in range(20)]
        report = build_capacity_report(rows)
        self.assertLessEqual(len(report["reason"]), 500)
        self.assertNotIn("\n", report["reason"])
        self.assertEqual(len(report["databases"]), 20)


class CapacityProbeCommandTests(TestCase):
    def create_target(self, *, name, slug, enabled):
        return BackupTarget.objects.create(
            name=name, slug=slug, enabled=enabled, sql_database="Axial",
            sql_host="private-sql-host", sql_password_env="private-password", app_backup_dir="/tmp/backups",
        )

    @patch("backups.services.capacity_review.collect_postgres_capacity", return_value=None)
    @patch("backups.services.capacity_review.collect_database_capacity")
    def test_command_reads_all_targets_without_writes_and_outputs_one_json_object(self, collect, postgres):
        enabled = self.create_target(name="Enabled", slug="enabled", enabled=True)
        disabled = self.create_target(name="Disabled", slug="disabled", enabled=False)
        observed = []

        def collect_targets(targets, *, include_log_usage):
            self.assertTrue(include_log_usage)
            observed.extend(targets)
            return [CapacityProbeSummaryTests().row(target=target) for target in observed]

        collect.side_effect = collect_targets
        output = io.StringIO()
        with self.assertNumQueries(1):
            call_command("check_database_capacity", stdout=output)
        report = json.loads(output.getvalue())
        self.assertEqual({target.pk for target in observed}, {enabled.pk, disabled.pk})
        self.assertEqual(report["level"], "green")
        self.assertEqual(len(report["databases"]), 2)
        self.assertNotIn("private-password", output.getvalue())
        self.assertNotIn("private-sql-host", output.getvalue())

    @patch("backups.services.capacity_review.collect_database_capacity")
    def test_collection_exception_outputs_safe_red_json_instead_of_raising(self, collect):
        collect.side_effect = RuntimeError("PWD=private-password; private-sql-host")
        output = io.StringIO()
        with self.assertLogs("backups.management.commands.check_database_capacity", level="WARNING") as logs:
            result = call_command("check_database_capacity", stdout=output)
        report = json.loads(output.getvalue())
        self.assertIsNone(result)
        self.assertEqual(report["level"], "red")
        self.assertEqual(report["databases"], [])
        self.assertNotIn("private-password", output.getvalue() + " ".join(logs.output))
        self.assertNotIn("private-sql-host", output.getvalue())
