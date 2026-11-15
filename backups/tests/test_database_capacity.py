from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from backups.services.database_capacity import PAGES_PER_GB, collect_database_capacity


class DatabaseCapacityTests(SimpleTestCase):
    def target(self, **kwargs):
        defaults = {
            "name": "P6 22", "sql_host": "sql.example", "sql_port": 1433,
            "sql_database": "Axial", "sql_username": "reader", "sql_password": "private-password",
            "odbc_driver": "ODBC Driver 18 for SQL Server", "encrypt_connection": True,
            "trust_server_certificate": True, "enabled": True,
        }
        return SimpleNamespace(**(defaults | kwargs))

    def connection(self, *, used=12.5, allocated=20, log=8, edition="Express Edition (64-bit)", version="17.0.1000.7", major="17"):
        connection = Mock()
        connection.cursor.return_value.fetchone.return_value = (
            edition, version, major,
            int(allocated * PAGES_PER_GB), int(used * PAGES_PER_GB), int(log * PAGES_PER_GB), 0, 2,
        )
        return connection

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_express_2025_usage_excludes_log_and_includes_all_data_file_totals(self, connect):
        connection = self.connection()
        connect.return_value = connection
        target = self.target()
        row, = collect_database_capacity([target])
        self.assertEqual(row["status"], "ok")
        self.assertIs(row["target"], target)
        self.assertEqual(row["limit_gb"], 50)
        self.assertEqual(row["data_used_gb"], 12.5)
        self.assertEqual(row["data_allocated_gb"], 20)
        self.assertEqual(row["log_allocated_gb"], 8)
        self.assertEqual(row["remaining_gb"], 37.5)
        self.assertEqual(row["used_percent"], 25)
        self.assertEqual(row["allocated_percent"], 40)
        self.assertEqual(row["used_bar_percent"], 25)
        self.assertEqual(row["reserved_bar_percent"], 15)
        self.assertEqual(row["severity"], "healthy")
        connection.close.assert_called_once()
        self.assertEqual(connection.timeout, 10)
        self.assertEqual(connect.call_args.kwargs, {"autocommit": True, "timeout": 5})
        self.assertIn("DATABASE=Axial;", connect.call_args.args[0])

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_express_2022_keeps_the_10_gb_limit(self, connect):
        connect.return_value = self.connection(used=8, allocated=9, major="16", version="16.0.1000.6")
        row, = collect_database_capacity([self.target()])
        self.assertEqual(row["limit_gb"], 10)
        self.assertEqual(row["remaining_gb"], 2)
        self.assertEqual(row["used_percent"], 80)
        self.assertEqual(row["severity"], "warning")

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_status_thresholds_use_used_data_instead_of_allocated_space(self, connect):
        for used, severity in ((39.5, "healthy"), (40, "warning"), (44.5, "warning"), (45, "critical"), (50, "critical")):
            with self.subTest(used=used):
                connect.return_value = self.connection(used=used, allocated=50)
                row, = collect_database_capacity([self.target()])
                self.assertEqual(row["severity"], severity)

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_over_limit_values_remain_visible_but_chart_does_not_overflow(self, connect):
        connect.return_value = self.connection(used=55, allocated=56)
        row, = collect_database_capacity([self.target()])
        self.assertEqual(row["used_percent"], 110)
        self.assertEqual(row["used_bar_percent"], 100)
        self.assertEqual(row["reserved_bar_percent"], 0)
        self.assertEqual(row["remaining_gb"], 0)

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_non_express_does_not_receive_an_express_limit(self, connect):
        for edition in ("Standard Edition", "Enterprise Edition", "Developer Edition", "Unknown edition"):
            with self.subTest(edition=edition):
                connect.return_value = self.connection(edition=edition)
                row, = collect_database_capacity([self.target()])
                self.assertIsNone(row["limit_gb"])
                self.assertIsNone(row["remaining_gb"])
                self.assertIsNone(row["used_percent"])
                self.assertEqual(row["data_used_gb"], 12.5)
                self.assertEqual(row["used_bar_percent"], 0)

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_unknown_express_version_does_not_assume_50_gb(self, connect):
        for major in (None, "", "invalid", "0", "-1"):
            with self.subTest(major=major):
                connect.return_value = self.connection(major=major)
                row, = collect_database_capacity([self.target()])
                self.assertIsNone(row["limit_gb"])
                self.assertIn("could not be confirmed", row["capacity_note"])

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_zero_usage_is_a_successful_measurement(self, connect):
        connect.return_value = self.connection(used=0, allocated=0, log=0)
        row, = collect_database_capacity([self.target()])
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["data_used_gb"], 0)
        self.assertEqual(row["remaining_gb"], 50)
        self.assertEqual(row["used_percent"], 0)

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_error_is_sanitized_and_other_databases_still_collect(self, connect):
        connect.side_effect = [RuntimeError("PWD=private-password; something failed"), self.connection()]
        with self.assertLogs("backups.services.database_capacity", level="WARNING") as logs:
            first, second = collect_database_capacity([self.target(), self.target(sql_database="Training")])
        self.assertEqual(first["status"], "unavailable")
        self.assertIsNone(first["data_used_gb"])
        self.assertIsNone(first["remaining_gb"])
        self.assertNotIn("private-password", first["unavailable_message"])
        self.assertNotIn("private-password", " ".join(logs.output))
        self.assertEqual(second["status"], "ok")

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_query_failure_closes_the_connection(self, connect):
        connection = self.connection()
        connection.cursor.return_value.execute.side_effect = RuntimeError("SQL failure")
        connect.return_value = connection
        with self.assertLogs("backups.services.database_capacity", level="WARNING"):
            row, = collect_database_capacity([self.target()])
        self.assertEqual(row["status"], "unavailable")
        connection.close.assert_called_once()

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_partial_file_statistics_are_unavailable_instead_of_understated(self, connect):
        connection = self.connection()
        connection.cursor.return_value.fetchone.return_value = ("Express Edition", "17.0", "17", PAGES_PER_GB, 0, 0, 1, 2)
        connect.return_value = connection
        with self.assertLogs("backups.services.database_capacity", level="WARNING"):
            row, = collect_database_capacity([self.target()])
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row["data_used_gb"])

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_missing_or_invalid_counters_do_not_become_zero_usage(self, connect):
        for result in (None, ("Express", "17", "17", 5, 6, 0, 0, 1), ("Express", "17", "17", 5, 1, -1, 0, 1), ("Express", "17", "17", 0, 0, 0, 0, 0)):
            with self.subTest(result=result):
                connection = self.connection()
                connection.cursor.return_value.fetchone.return_value = result
                connect.return_value = connection
                with self.assertLogs("backups.services.database_capacity", level="WARNING"):
                    row, = collect_database_capacity([self.target()])
                self.assertEqual(row["status"], "unavailable")
                connection.close.assert_called_once()

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_duplicate_configurations_share_read_and_disabled_targets_remain_visible(self, connect):
        connect.return_value = self.connection()
        enabled = self.target()
        disabled = self.target(name="Disabled alias", enabled=False)
        rows = collect_database_capacity([enabled, disabled])
        self.assertEqual(len(rows), 2)
        self.assertIs(rows[0]["target"], enabled)
        self.assertIs(rows[1]["target"], disabled)
        self.assertEqual(rows[1]["status"], "ok")
        connect.assert_called_once()

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_different_credentials_never_share_a_result(self, connect):
        connect.side_effect = [self.connection(), RuntimeError("Access denied")]
        with self.assertLogs("backups.services.database_capacity", level="WARNING"):
            rows = collect_database_capacity([self.target(), self.target(sql_username="other-reader")])
        self.assertEqual(connect.call_count, 2)
        self.assertEqual([row["status"] for row in rows], ["ok", "unavailable"])

    @patch("backups.services.database_capacity.pyodbc.connect")
    def test_empty_target_list_does_not_connect(self, connect):
        self.assertEqual(collect_database_capacity([]), [])
        connect.assert_not_called()
