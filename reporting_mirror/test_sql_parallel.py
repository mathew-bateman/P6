from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from reporting_mirror.models import ReportingConfig, ReportingRun, SQLParallelRun
from reporting_mirror.native_settings import TABLES
from reporting_mirror.sql_parallel import mirror_and_refresh, queue_sql_parallel, run_sql_parallel


def rules():
    header = dict(config_version_id=99, state="published", settings_hash="A" * 64,
                  created_at=datetime(2026, 10, 25), created_by="operator",
                  updated_at=datetime(2026, 10, 25), updated_by="operator",
                  published_at=datetime(2026, 10, 25), published_by="operator", change_note="New thresholds")
    children = {table: [{"config_version_id": 99, "check_code": "high_float", "detail_field_id": 8, "value": 123}] for table in TABLES[2:]}
    children[TABLES[3]] = [{"config_version_id": 99, "option_code": code, "numeric_value": value} for code, value in (("high_duration_days", 20), ("high_float_days", 30), ("near_critical_upper_days", 5))]
    return header, children


class SQLMirrorSafetyTests(SimpleTestCase):
    def fixture(self, checksum="A" * 64, *, machine="xps4-p62212"):
        statements = []
        def fake_query(connection, statement, parameters=()):
            statements.append((statement, parameters))
            if statement.startswith("SELECT DB_NAME"):
                return [dict(database_name="P62212_1", machine_name=machine, project_object=1, refresh_object=2)]
            if statement.startswith("SELECT p.profile_id"):
                return [dict(profile_id=1, active_config_version_id=37, settings_hash=checksum, state="active")]
            if "MAX(version_number)" in statement:
                return [dict(number=39)]
            if statement.startswith("INSERT") and "OUTPUT INSERTED" in statement:
                return [dict(config_version_id=39)]
            if statement.startswith("SELECT TOP(1)"):
                return [dict(check_run_id=10307, status="success", config_version_id=37 if checksum == "A" * 64 else 39, processed_project_count=781)]
            return []
        return statements, fake_query

    def test_same_rules_refresh_without_rewriting_settings_or_drafts(self):
        statements, fake = self.fixture()
        with patch("reporting_mirror.sql_parallel.query", side_effect=fake):
            result = mirror_and_refresh(object(), *rules())
        self.assertEqual(result["processed_project_count"], 781)
        self.assertFalse(any(s.startswith(("INSERT", "UPDATE", "DELETE")) for s, _ in statements))
        self.assertIn("@config_version_id=?,@trigger_type=?", next(s for s, _ in statements if s.startswith("EXEC")))

    def test_new_rules_append_active_sql_version_without_pg_identity_or_draft_changes(self):
        statements, fake = self.fixture("B" * 64)
        original = rules(); before = deepcopy(original)
        with patch("reporting_mirror.sql_parallel.query", side_effect=fake):
            result = mirror_and_refresh(object(), *original, trigger="settings_publish")
        self.assertEqual(original, before)
        self.assertEqual(result["config_version_id"], 39)
        header_statement, values = next((s, p) for s, p in statements if s.startswith("INSERT") and "OUTPUT INSERTED" in s)
        columns = header_statement.split(" (")[1].split(")")[0].replace("[", "").replace("]", "").split(",")
        header = dict(zip(columns, values))
        self.assertEqual(header["state"], "active")
        self.assertEqual(header["based_on_config_version_id"], 37)
        self.assertNotIn("config_version_id", header)
        self.assertEqual(header["version_number"], 39)
        for statement, parameters in statements:
            self.assertNotIn("DELETE", statement)
            self.assertNotIn("IDENTITY_INSERT", statement)
            self.assertNotIn("TRUNCATE", statement)
            self.assertNotIn("CREATE", statement)
            self.assertNotIn("DROP", statement)
            if statement.startswith("UPDATE"):
                self.assertNotIn("draft", statement)
                self.assertNotIn(38, parameters)
            if statement.startswith("INSERT") and "OUTPUT INSERTED" not in statement:
                self.assertNotIn("[detail_field_id]", statement)
                self.assertEqual(parameters[-1], 39)
        self.assertEqual(next(p for s, p in statements if s.startswith("EXEC")), (39, "publish"))
        thresholds = [p for s, p in statements if s.startswith("UPDATE powerbitables.xertoolkit_settings_thresholds")]
        self.assertEqual(thresholds, [(20, "High Duration", "High Duration", 20), (30, "High Float", "High Float", 30), (0, "Critical Float", "Critical Float", 0), (5, "Near Critical", "Near Critical", 5)])

    def test_wrong_machine_fails_before_any_reporting_write(self):
        statements, fake = self.fixture(machine="some-other-server")
        with patch("reporting_mirror.sql_parallel.query", side_effect=fake), self.assertRaisesMessage(RuntimeError, "identity"):
            mirror_and_refresh(object(), *rules())
        self.assertEqual(len(statements), 1)

    def test_failed_sql_result_is_not_reported_success(self):
        statements, fake = self.fixture()
        def failed(connection, statement, parameters=()):
            value = fake(connection, statement, parameters)
            if statement.startswith("SELECT TOP(1)"):
                value[0]["status"] = "failed"
            return value
        with patch("reporting_mirror.sql_parallel.query", side_effect=failed), self.assertRaisesMessage(RuntimeError, "did not finish"):
            mirror_and_refresh(object(), *rules())


class SQLParallelDispatchTests(TestCase):
    def setUp(self):
        self.config = ReportingConfig.current()
        self.config.sql_parallel_enabled = True
        self.config.manage_settings_in_postgres = True
        self.config.save()

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.tasks.refresh_sql_parallel")
    def test_successful_publication_queues_once_on_serialized_queue(self, task, available):
        publication = ReportingRun.objects.create(status="success", engine="postgres")
        first = queue_sql_parallel(publication=publication, trigger="settings_publish")
        second = queue_sql_parallel(publication=publication, trigger="settings_publish")
        self.assertEqual(first, second)
        self.assertEqual(task.apply_async.call_count, 1)
        self.assertEqual(task.apply_async.call_args.kwargs["queue"], "p6_reporting")
        self.assertEqual(SQLParallelRun.objects.count(), 1)

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.tasks.refresh_sql_parallel")
    def test_failed_or_skipped_pg_never_queues_sql_publication(self, task, available):
        for status in ("failed", "skipped", "running"):
            self.assertIsNone(queue_sql_parallel(publication=ReportingRun.objects.create(status=status, engine="postgres")))
        task.apply_async.assert_not_called()

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.tasks.refresh_sql_parallel")
    def test_queue_failure_keeps_pg_publication_successful(self, task, available):
        publication = ReportingRun.objects.create(status="success", engine="postgres")
        task.apply_async.side_effect = RuntimeError("broker unavailable")
        queue_sql_parallel(publication=publication)
        publication.refresh_from_db()
        self.assertEqual(publication.status, "success")
        self.assertEqual(SQLParallelRun.objects.get().status, "failed")

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.tasks.refresh_sql_parallel")
    def test_disabled_flag_does_not_queue(self, task, available):
        self.config.sql_parallel_enabled = False; self.config.save()
        self.assertIsNone(queue_sql_parallel())
        task.apply_async.assert_not_called()

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.sql_parallel.SQLParallelRun.objects.filter")
    @patch("reporting_mirror.sql_parallel.connect")
    def test_lost_atomic_claim_cannot_start_a_duplicate_sql_refresh(self, connection, run_filter, available):
        record = SQLParallelRun.objects.create(status="queued")
        run_filter.return_value.update.return_value = 0
        run_sql_parallel(record.pk)
        connection.assert_not_called()

    @patch("reporting_mirror.native_settings.enabled", return_value=True)
    @patch("reporting_mirror.sql_parallel.queue_sql_parallel", return_value="queued-id")
    @patch("reporting_mirror.tasks.refresh_reporting")
    def test_original_schedule_retains_sql_refresh_without_duplicate_pg_work(self, pg_task, queue, enabled):
        from backups.tasks import execute_schedule_quality_refresh
        result = execute_schedule_quality_refresh.run(trigger="scheduled")
        queue.assert_called_once_with(trigger="scheduled")
        pg_task.apply_async.assert_not_called()
        self.assertIn("SQL reporting", result)

    @patch("reporting_mirror.native_settings.enabled", return_value=True)
    @patch("reporting_mirror.sql_parallel.queue_sql_parallel")
    @patch("reporting_mirror.tasks.refresh_reporting")
    def test_manual_quality_refresh_still_fetches_new_pg_data_before_sql_mirror(self, pg_task, queue, enabled):
        from backups.tasks import execute_schedule_quality_refresh
        execute_schedule_quality_refresh.run(trigger="manual")
        queue.assert_not_called()
        pg_task.apply_async.assert_called_once()
        self.assertFalse(pg_task.apply_async.call_args.kwargs["kwargs"]["recalculate_only"])

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.sql_parallel.retained_sql_target")
    @patch("reporting_mirror.sql_parallel.current_settings", side_effect=rules)
    @patch("reporting_mirror.sql_parallel.mirror_and_refresh", side_effect=RuntimeError("forced calculation failure"))
    @patch("reporting_mirror.sql_parallel.query", return_value=[{"result": 0}])
    @patch("reporting_mirror.sql_parallel.connect")
    @patch("reporting_mirror.sql_parallel.postgres_connection")
    def test_sql_failure_rolls_back_and_retains_successful_pg_run(self, pg_connect, sql_connect, query, mirror, settings, target, available):
        pg_connect.return_value.execute.return_value.fetchone.return_value = {"acquired": True}
        connection = sql_connect.return_value
        publication = ReportingRun.objects.create(status="success", engine="postgres")
        record = SQLParallelRun.objects.create(publication=publication)
        with self.assertRaisesMessage(RuntimeError, "forced calculation failure"):
            run_sql_parallel(record.pk)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()
        record.refresh_from_db(); publication.refresh_from_db()
        self.assertEqual(record.status, "failed")
        self.assertEqual(publication.status, "success")
        self.assertIn("previous SQL results", record.progress)

    @patch("reporting_mirror.sql_parallel.reporting_available", return_value=True)
    @patch("reporting_mirror.sql_parallel.retained_sql_target")
    @patch("reporting_mirror.sql_parallel.current_settings", side_effect=rules)
    @patch("reporting_mirror.sql_parallel.mirror_and_refresh", side_effect=RuntimeError("primary SQL failure"))
    @patch("reporting_mirror.sql_parallel.query")
    @patch("reporting_mirror.sql_parallel.connect")
    @patch("reporting_mirror.sql_parallel.postgres_connection")
    def test_failed_sql_cleanup_still_releases_pg_lock_and_keeps_primary_error(self, pg_connect, sql_connect, query, mirror, settings, target, available):
        pg_connect.return_value.execute.return_value.fetchone.return_value = {"acquired": True}
        sql_connect.return_value.close.side_effect = RuntimeError("close failure")
        def statements(connection, statement, parameters=()):
            if "sp_releaseapplock" in statement:
                raise RuntimeError("release failure")
            return [{"result": 0}]
        query.side_effect = statements
        record = SQLParallelRun.objects.create()
        with self.assertRaisesMessage(RuntimeError, "primary SQL failure"):
            run_sql_parallel(record.pk)
        self.assertIn("pg_advisory_unlock", pg_connect.return_value.execute.call_args.args[0])
        pg_connect.return_value.close.assert_called_once()

    @patch("reporting_mirror.sql_parallel.queue_sql_parallel")
    @patch("reporting_mirror.sync.run_sync")
    def test_only_a_new_successful_pg_run_triggers_parallel_refresh(self, sync, queue):
        from reporting_mirror.tasks import refresh_reporting
        old = ReportingRun.objects.create(status="success", engine="postgres")
        refresh_reporting.run(trigger="manual")
        queue.assert_not_called()
        def publish(**kwargs):
            ReportingRun.objects.create(status="success", engine="postgres")
            return "published"
        sync.side_effect = publish
        refresh_reporting.run(trigger="manual")
        self.assertEqual(queue.call_count, 1)
        self.assertNotEqual(queue.call_args.kwargs["publication"].pk, old.pk)
