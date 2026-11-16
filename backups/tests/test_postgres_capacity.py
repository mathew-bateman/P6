from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from django.test import SimpleTestCase, override_settings

from backups.services.postgres_capacity import (
    BYTES_PER_GB,
    POSTGRES_CAPACITY_SQL,
    collect_postgres_capacity,
    reporting_capacity_enabled,
)


@override_settings(P6_REPORTING_CAPACITY_ENABLED=True, P6_REPORTING_CAPACITY_STORAGE_PATH="/stage")
class PostgresCapacityTests(SimpleTestCase):
    def postgres(self, *, result=None):
        module = MagicMock()
        module.reporting_available.return_value = True
        connection = module.postgres_connection.return_value.__enter__.return_value
        connection.execute.return_value.fetchone.return_value = result if result is not None else {
            "database_name": "p6_reporting", "version": "16.13",
            "database_bytes": int(11.75 * BYTES_PER_GB),
            "tables_bytes": int(9.4 * BYTES_PER_GB),
            "indexes_bytes": int(2.3 * BYTES_PER_GB),
        }
        return module, connection

    def disk(self, *, blocks=100, free=40, available=35, fragment=BYTES_PER_GB):
        return SimpleNamespace(f_blocks=blocks, f_bfree=free, f_bavail=available, f_frsize=fragment)

    def collect(self, *, module=None, disk=None):
        module = module if module is not None else self.postgres()[0]
        disk = disk if disk is not None else self.disk()
        with patch("backups.services.postgres_capacity.import_module", return_value=module), patch("backups.services.postgres_capacity.os.statvfs", return_value=disk, create=True):
            return collect_postgres_capacity()

    def test_reads_database_and_reserved_volume_capacity_independently(self):
        row = self.collect()
        self.assertEqual(row["engine"], "postgresql")
        self.assertEqual(row["slug"], "postgresql-reporting")
        self.assertEqual(row["database_name"], "p6_reporting")
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["used_gb"], 11.75)
        self.assertAlmostEqual(row["tables_gb"], 9.4)
        self.assertAlmostEqual(row["indexes_gb"], 2.3)
        self.assertEqual(row["version"], "16.13")
        self.assertEqual(row["storage_status"], "ok")
        self.assertEqual(row["storage_total_gb"], 100)
        self.assertEqual(row["storage_used_gb"], 60)
        self.assertEqual(row["storage_available_gb"], 35)
        self.assertEqual(row["storage_reserved_gb"], 5)
        self.assertEqual(row["storage_usable_gb"], 95)
        self.assertAlmostEqual(row["storage_used_percent"], 60 / 95 * 100)
        self.assertEqual(datetime.fromisoformat(row["checked_at"]).utcoffset(), timedelta(0))

    def test_reader_context_sets_local_timeout_and_read_only_before_query(self):
        module, connection = self.postgres()
        self.collect(module=module)
        module.postgres_connection.assert_called_once_with(reader=True)
        self.assertEqual(connection.execute.call_args_list, [
            call("SET TRANSACTION READ ONLY"),
            call("SET LOCAL statement_timeout = '5s'"),
            call(POSTGRES_CAPACITY_SQL),
        ])
        module.postgres_connection.return_value.__exit__.assert_called_once()
        self.assertNotIn("wal", POSTGRES_CAPACITY_SQL.lower())
        self.assertNotIn("load_", POSTGRES_CAPACITY_SQL.lower())

    def test_tuple_row_adapter_matches_dict_metrics(self):
        module, _ = self.postgres(result=("p6_reporting", "16.13", 12 * BYTES_PER_GB, 9 * BYTES_PER_GB, 2 * BYTES_PER_GB))
        row = self.collect(module=module)
        self.assertEqual(row["status"], "ok")
        self.assertEqual((row["used_gb"], row["tables_gb"], row["indexes_gb"]), (12, 9, 2))

    def test_postgresql_has_no_express_limit_or_transaction_log_claim(self):
        row = self.collect()
        self.assertFalse(any(key.startswith("log_") for key in row))
        self.assertNotIn("limit_gb", row)
        self.assertNotIn("remaining_gb", row)

    def test_reserved_blocks_are_excluded_from_usable_capacity(self):
        for blocks, free, available, used, reserved, usable, percent in (
            (100, 40, 35, 60, 5, 95, 60 / 95 * 100),
            (100, 100, 95, 0, 5, 95, 0),
            (100, 0, 0, 100, 0, 100, 100),
            (100, 40, 40, 60, 0, 100, 60),
        ):
            with self.subTest(blocks=blocks, free=free, available=available):
                row = self.collect(disk=self.disk(blocks=blocks, free=free, available=available))
                self.assertEqual(row["storage_status"], "ok")
                self.assertEqual(row["storage_used_gb"], used)
                self.assertEqual(row["storage_reserved_gb"], reserved)
                self.assertEqual(row["storage_usable_gb"], usable)
                self.assertAlmostEqual(row["storage_used_percent"], percent)

    def test_database_failure_preserves_volume_reading_and_sanitizes_logs(self):
        module, connection = self.postgres()
        connection.execute.side_effect = RuntimeError("host=private-db password=private-password")
        with self.assertLogs("backups.services.postgres_capacity", level="WARNING") as logs:
            row = self.collect(module=module)
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row["used_gb"])
        self.assertEqual(row["storage_status"], "ok")
        self.assertEqual(row["storage_used_gb"], 60)
        self.assertNotIn("private-password", repr(row) + " ".join(logs.output))
        module.postgres_connection.return_value.__exit__.assert_called_once()

    def test_volume_failure_preserves_database_reading(self):
        module, _ = self.postgres()
        with patch("backups.services.postgres_capacity.import_module", return_value=module), patch("backups.services.postgres_capacity.os.statvfs", side_effect=OSError("private mount error"), create=True), self.assertLogs("backups.services.postgres_capacity", level="WARNING") as logs:
            row = collect_postgres_capacity()
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["used_gb"], 11.75)
        self.assertEqual(row["storage_status"], "unavailable")
        self.assertIsNone(row["storage_used_percent"])
        self.assertNotIn("private mount error", repr(row) + " ".join(logs.output))

    @override_settings(P6_REPORTING_CAPACITY_STORAGE_PATH="")
    def test_unconfigured_volume_does_not_probe_the_web_filesystem(self):
        module, _ = self.postgres()
        with patch("backups.services.postgres_capacity.import_module", return_value=module), patch("backups.services.postgres_capacity.os.statvfs", create=True) as statvfs:
            row = collect_postgres_capacity()
        statvfs.assert_not_called()
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["storage_status"], "unavailable")

    def test_invalid_or_unusable_volumes_are_unavailable(self):
        for disk in (
            self.disk(blocks=0, free=0, available=0), self.disk(fragment=0),
            self.disk(free=110), self.disk(available=41), self.disk(available=-1),
            self.disk(free=100, available=0),
        ):
            with self.subTest(disk=disk), self.assertLogs("backups.services.postgres_capacity", level="WARNING"):
                row = self.collect(disk=disk)
                self.assertEqual(row["status"], "ok")
                self.assertEqual(row["storage_status"], "unavailable")
                self.assertIsNone(row["storage_total_gb"])

    def test_unavailable_reporting_configuration_does_not_open_connection(self):
        module, _ = self.postgres()
        module.reporting_available.return_value = False
        row = self.collect(module=module)
        module.postgres_connection.assert_not_called()
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["storage_status"], "ok")

    def test_missing_optional_module_preserves_available_volume(self):
        with patch("backups.services.postgres_capacity.import_module", side_effect=ModuleNotFoundError("No module named reporting_mirror")), patch("backups.services.postgres_capacity.os.statvfs", return_value=self.disk(), create=True), self.assertLogs("backups.services.postgres_capacity", level="WARNING"):
            row = collect_postgres_capacity()
        self.assertEqual(row["status"], "unavailable")
        self.assertEqual(row["storage_status"], "ok")

    def test_invalid_database_counters_are_not_reported_as_zero(self):
        for result in (
            ("", "16", 10, 5, 1), ("reporting", "16", -1, 5, 1),
            ("reporting", "16", 10, None, 1), ("reporting", "16", 10, 5, -1),
        ):
            with self.subTest(result=result):
                module, _ = self.postgres(result=result)
                with self.assertLogs("backups.services.postgres_capacity", level="WARNING"):
                    row = self.collect(module=module)
                self.assertEqual(row["status"], "unavailable")
                self.assertEqual(row["storage_status"], "ok")

    def test_inconsistent_component_sizes_preserve_disk_and_mark_database_unavailable(self):
        module, _ = self.postgres(result=("p6_reporting", "16.13", 10 * BYTES_PER_GB, 9 * BYTES_PER_GB, 2 * BYTES_PER_GB))
        with self.assertLogs("backups.services.postgres_capacity", level="WARNING") as logs:
            row = self.collect(module=module)
        self.assertEqual(row["status"], "unavailable")
        self.assertIsNone(row["used_gb"])
        self.assertIsNone(row["tables_gb"])
        self.assertIsNone(row["indexes_gb"])
        self.assertEqual(row["storage_status"], "ok")
        self.assertEqual(row["storage_used_gb"], 60)
        self.assertNotIn("p6_reporting", " ".join(logs.output))

    def test_zero_database_reading_remains_a_valid_reading(self):
        module, _ = self.postgres(result=("reporting", "16", 0, 0, 0))
        row = self.collect(module=module)
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["used_gb"], 0)


class PostgresCapacityAvailabilityTests(SimpleTestCase):
    @override_settings(P6_REPORTING_CAPACITY_ENABLED=None)
    def test_optional_module_controls_default_availability(self):
        for installed in (True, False):
            with self.subTest(installed=installed), patch("backups.services.postgres_capacity.apps.is_installed", return_value=installed) as is_installed:
                self.assertEqual(reporting_capacity_enabled(), installed)
                is_installed.assert_called_once_with("reporting_mirror")

    @override_settings(P6_REPORTING_CAPACITY_ENABLED=False)
    def test_explicit_disable_avoids_all_collection_even_when_installed(self):
        with patch("backups.services.postgres_capacity.apps.is_installed", return_value=True), patch("backups.services.postgres_capacity.import_module") as import_module, patch("backups.services.postgres_capacity.os.statvfs", create=True) as statvfs:
            self.assertIsNone(collect_postgres_capacity())
        import_module.assert_not_called()
        statvfs.assert_not_called()

    @override_settings(P6_REPORTING_CAPACITY_ENABLED=True)
    def test_explicit_enable_includes_unavailable_optional_module_as_missing_data(self):
        with patch("backups.services.postgres_capacity.apps.is_installed", return_value=False):
            self.assertTrue(reporting_capacity_enabled())
