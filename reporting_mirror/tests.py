from datetime import datetime, timezone as datetime_timezone
from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch
from contextlib import ExitStack

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from backups.models import BackupTarget
from reporting_mirror.forms import ReportingSettingsForm
from reporting_mirror.models import ReportingConfig, ReportingRun, ReportingSource
from reporting_mirror.reader import compile_query
from reporting_mirror.sync import ContentHash, pg_type, object_kind, ui_identifier_offset
from reporting_mirror.sql_staging import safe_stage_name, assert_staging
from reporting_mirror.tasks import sync_schedule


class QuerySafetyTests(SimpleTestCase):
    def test_sql_server_padded_object_types_are_classified_correctly(self):
        self.assertEqual(object_kind("V "), "view")
        self.assertEqual(object_kind("U "), "table")

    def test_combined_reports_use_a_separate_identifier_namespace(self):
        query, bound = compile_query("SELECT proj_id FROM powerbitables.Projects WHERE proj_id=?", "combined", (8589934600,))
        self.assertIn('"reporting_ui".Projects'.lower(), query.lower())
        self.assertEqual(bound["p0"], 8589934600)
        self.assertLess(ui_identifier_offset("p6_22") + 2147483647, ui_identifier_offset("p6_23") - 2147483648)

    @patch("reporting_mirror.reader.active_config", return_value=None)
    @patch("reporting_mirror.reader.sql_fetch_rows")
    def test_disabled_combined_report_never_silently_reads_p6_22(self, live_sql, config):
        from reporting_mirror.reader import report_source, fetch_rows
        token = report_source.set("combined")
        try:
            with self.assertRaises(RuntimeError):
                fetch_rows(None, "SELECT 1")
        finally:
            report_source.reset(token)
        live_sql.assert_not_called()
    def test_pagination_parameters_keep_lexical_order(self):
        query, parameters = compile_query("SELECT task_id FROM [powerbitables].[Tasks] WHERE proj_id=? ORDER BY task_id OFFSET ? ROWS FETCH NEXT ? ROWS ONLY", "p6_23", (101, 25, 50))
        self.assertIn('"p6_23"."tasks"', query)
        self.assertIn("OFFSET %(p1)s", query)
        self.assertIn("FETCH NEXT %(p2)s", query)
        self.assertEqual(parameters, {"p0": 101, "p1": 25, "p2": 50})

    def test_literal_question_marks_are_not_bound(self):
        query, parameters = compile_query("SELECT 'Does it?' label FROM dbo.TASK WHERE task_id=?", "p6_22", (5,))
        self.assertIn("Does it?", query)
        self.assertEqual(parameters, {"p0": 5})

    def test_user_values_never_interpolate_into_sql(self):
        attack = "'; DELETE FROM task; --"
        query, parameters = compile_query("SELECT * FROM dbo.TASK WHERE task_code=?", "p6_22", (attack,))
        self.assertNotIn(attack, query)
        self.assertEqual(parameters["p0"], attack)

    def test_quoted_labels_and_date_conversion(self):
        query, _ = compile_query("SELECT CONVERT(date,p.[Updated Date]) updated_date FROM [powerbitables].[Projects] p", "p6_22")
        self.assertIn('p."Updated Date"', query)
        self.assertIn("AS DATE", query)

    def test_writes_and_multiple_statements_are_rejected(self):
        for statement in ("DELETE FROM dbo.TASK", "SELECT * INTO dbo.x FROM dbo.TASK", "SELECT 1; DELETE FROM dbo.TASK", "CREATE TABLE x(a int)"):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                compile_query(statement, "p6_22")

    def test_external_schema_and_source_are_rejected(self):
        for statement, source in (("SELECT * FROM master.dbo.syslogins", "p6_22"), ("SELECT * FROM private.keys", "p6_22"), ("SELECT 1", "p6_99")):
            with self.subTest(source=source), self.assertRaises(ValueError):
                compile_query(statement, source)

    def test_bad_parameter_count_is_rejected(self):
        with self.assertRaises(ValueError):
            compile_query("SELECT * FROM dbo.TASK WHERE task_id=?", "p6_22")

    def test_digest_is_order_independent_but_detects_changes(self):
        a, b, c = ContentHash(), ContentHash(), ContentHash()
        for row in ((1, Decimal("2.00")), (2, None)):
            a.add(row)
        for row in ((2, None), (1, Decimal("2.0"))):
            b.add(row)
        c.add((1, Decimal("3.0"))); c.add((2, None))
        self.assertEqual((a.count, a.value()), (b.count, b.value()))
        self.assertNotEqual(a.value(), c.value())

    def test_unknown_types_and_restore_targets_fail_closed(self):
        with self.assertRaises(RuntimeError):
            pg_type({"type_name": "sql_variant"})
        with self.assertRaises(ValueError):
            safe_stage_name("Axial")
        with patch("reporting_mirror.sql_staging.query", return_value=[{"machine": "xps4-axialp6"}]), patch("reporting_mirror.sql_staging.secrets_config", return_value={"staging": {"hostname": "xps4-reporting-stage"}}):
            with self.assertRaises(RuntimeError):
                assert_staging(object())


class ReportingControlsTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="report_admin", is_staff=True)
        self.normal = get_user_model().objects.create_user(username="report_reader")
        self.config = ReportingConfig.current()

    def test_anonymous_redirected_and_unprivileged_user_denied(self):
        self.assertEqual(self.client.get(reverse("reporting_mirror_dashboard")).status_code, 302)
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get(reverse("reporting_mirror_dashboard")).status_code, 403)

    def test_report_group_can_view_but_cannot_change_settings_or_refresh(self):
        from django.conf import settings
        group = Group.objects.create(name=settings.P6_PORTFOLIO_REPORTING_GROUP)
        self.normal.groups.add(group)
        self.client.force_login(self.normal)
        self.assertEqual(self.client.get(reverse("reporting_mirror_dashboard")).status_code, 200)
        self.assertEqual(self.client.post(reverse("reporting_mirror_settings"), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse("reporting_mirror_refresh"), {}).status_code, 403)

    @patch("reporting_mirror.views.reporting_available", return_value=True)
    @patch("reporting_mirror.views.refresh_reporting")
    def test_refresh_permission_exposes_only_refresh_and_queues_manual_request(self, task, available):
        from django.contrib.auth.models import Permission
        self.normal.user_permissions.add(Permission.objects.get(content_type__app_label="reporting_mirror", codename="refresh_reporting"))
        self.client.force_login(self.normal)
        response = self.client.get(reverse("reporting_mirror_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'action="{reverse("reporting_mirror_refresh")}"')
        self.assertContains(response, 'Reporting Database')
        self.assertEqual(self.client.post(reverse("reporting_mirror_settings"), {}).status_code, 403)
        self.assertEqual(self.client.post(reverse("reporting_mirror_validate_engine"), {}).status_code, 403)
        response = self.client.post(reverse("reporting_mirror_refresh"))
        self.assertEqual(response.status_code, 302)
        task.apply_async.assert_called_once_with(kwargs={"trigger": "manual", "requested_by": "report_reader", "force": True}, queue="p6_reporting")

    @patch("reporting_mirror.views.reporting_available", return_value=True)
    @patch("reporting_mirror.views.refresh_reporting")
    def test_report_viewer_without_refresh_permission_has_no_button_and_post_is_denied(self, task, available):
        from django.conf import settings
        group = Group.objects.create(name=settings.P6_PORTFOLIO_REPORTING_GROUP)
        self.normal.groups.add(group)
        self.client.force_login(self.normal)
        response = self.client.get(reverse("reporting_mirror_dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, f'action="{reverse("reporting_mirror_refresh")}"')
        self.assertEqual(self.client.post(reverse("reporting_mirror_refresh")).status_code, 403)
        task.apply_async.assert_not_called()

    @patch("reporting_mirror.views.reporting_available", return_value=True)
    def test_staff_keeps_refresh_button(self, available):
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(reverse("reporting_mirror_dashboard")), f'action="{reverse("reporting_mirror_refresh")}"')

    @patch("reporting_mirror.views.reporting_available", return_value=True)
    def test_invalid_settings_keeps_staff_refresh_button(self, available):
        self.client.force_login(self.staff)
        response = self.client.post(reverse("reporting_mirror_settings"), {"interval_minutes": "not a number"})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, f'action="{reverse("reporting_mirror_refresh")}"', status_code=400)

    def test_cannot_enable_unverified_reports(self):
        form = ReportingSettingsForm({"enabled": True, "serve_reports": True, "interval_minutes": 60, "stale_after_minutes": 120, "query_timeout_seconds": 60, "retained_generations": 1}, instance=self.config)
        self.assertFalse(form.is_valid())
        self.assertIn("serve_reports", form.errors)

    def test_freshness_must_allow_a_whole_refresh_interval(self):
        form = ReportingSettingsForm({"interval_minutes": 60, "stale_after_minutes": 30, "query_timeout_seconds": 60, "retained_generations": 1}, instance=self.config)
        self.assertFalse(form.is_valid())
        self.assertIn("stale_after_minutes", form.errors)

    def test_schedule_uses_isolated_queue_and_pause_is_persistent(self):
        self.config.enabled = True; self.config.interval_minutes = 90; self.config.save()
        task = sync_schedule(self.config)
        self.assertEqual(task.queue, "p6_reporting")
        self.assertEqual(task.interval.every, 90)
        self.config.enabled = False; self.config.save()
        self.assertFalse(sync_schedule(self.config).enabled)

    def test_initial_schedule_is_anchored_to_the_successful_refresh(self):
        run = ReportingRun.objects.create(status="success", published_at=timezone.now())
        started = timezone.now() - timedelta(minutes=30)
        ReportingRun.objects.filter(pk=run.pk).update(started_at=started)
        self.config.enabled = True; self.config.save()
        task = sync_schedule(self.config)
        self.assertEqual(task.last_run_at, started)
        self.config.interval_minutes = 90; self.config.save()
        self.assertEqual(sync_schedule(self.config).last_run_at, started)

    def test_dashboard_shows_last_good_data_after_a_failed_refresh(self):
        self.client.force_login(self.staff)
        target = BackupTarget.objects.create(name="22", slug="p6_22", sql_database="P62212_1", app_backup_dir="/tmp")
        ReportingSource.objects.create(code="p6_22", target=target)
        old = ReportingRun.objects.create(status="success", published_at=timezone.now(), sources={"p6_22": {"snapshot_at": timezone.now().isoformat(), "counts": {"projects": 781, "activities": 279212}}})
        ReportingRun.objects.create(status="failed", error_message="Simulated refresh failure")
        response = self.client.get(reverse("reporting_mirror_dashboard"))
        self.assertEqual(response.context["latest_publication"].id, old.id)
        self.assertEqual(response.context["total_projects"], 781)

    @patch("reporting_mirror.views.reporting_available", return_value=True)
    @patch("reporting_mirror.views.refresh_reporting")
    def test_manual_refresh_is_post_only_and_uses_reporting_queue(self, task, available):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("reporting_mirror_refresh")).status_code, 405)
        response = self.client.post(reverse("reporting_mirror_refresh"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(task.apply_async.call_args.kwargs["queue"], "p6_reporting")

    @patch("reporting_mirror.tasks.queue_after_settings_publish", side_effect=RuntimeError("Queue unavailable"))
    @patch("backups.tasks.publish_schedule_quality_config")
    def test_reporting_queue_failure_does_not_retry_committed_settings(self, publish, queue):
        from backups.tasks import publish_schedule_quality_config_task
        with self.assertLogs("backups.tasks", level="ERROR"):
            publish_schedule_quality_config_task.run(config_version_id=11, expected_settings_hash="a" * 64, published_by="admin")
        self.assertEqual(publish.call_count, 1)

    @patch("reporting_mirror.views.reporting_available", return_value=False)
    @patch("reporting_mirror.views.refresh_reporting")
    def test_recovery_host_cannot_start_postgres_refresh(self, task, available):
        self.client.force_login(self.staff)
        self.client.post(reverse("reporting_mirror_refresh"))
        task.apply_async.assert_not_called()

    def test_preview_cannot_address_unlisted_tables(self):
        self.client.force_login(self.staff)
        ReportingRun.objects.create(status="success", published_at=timezone.now())
        self.assertEqual(self.client.get(reverse("reporting_mirror_preview", args=["users"])).status_code, 404)

    @patch("reporting_mirror.middleware.postgres_connection")
    def test_postgres_outage_has_a_rendered_recovery_page(self, connection):
        import psycopg
        from django.conf import settings
        self.normal.groups.add(Group.objects.get_or_create(name=settings.P6_SCHEDULE_QUALITY_REPORT_GROUP)[0])
        self.client.force_login(self.normal)
        self.config.serve_reports = True; self.config.save()
        connection.side_effect = psycopg.OperationalError("Database unavailable")
        with self.assertLogs("reporting_mirror.middleware", level="ERROR"):
            response = self.client.get(reverse("schedule_quality_overview"))
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Reporting is temporarily unavailable", status_code=503)

    @patch("reporting_mirror.middleware.postgres_connection")
    def test_unprivileged_report_request_never_connects_to_postgres(self, connection):
        self.client.force_login(self.normal)
        self.config.serve_reports = True; self.config.save()
        self.assertEqual(self.client.get(reverse("schedule_quality_overview")).status_code, 403)
        connection.assert_not_called()


class RefreshFailureTests(TestCase):
    def setUp(self):
        config = ReportingConfig.current()
        config.enabled = True
        config.save()

    @patch("reporting_mirror.sync.postgres_connection", side_effect=RuntimeError("PostgreSQL unavailable"))
    def test_connect_failure_is_recorded_without_hiding_the_last_good_run(self, connection):
        from reporting_mirror.sync import run_sync
        old = ReportingRun.objects.create(status="success", published_at=timezone.now())
        with self.assertRaisesMessage(RuntimeError, "PostgreSQL unavailable"):
            run_sync()
        failed = ReportingRun.objects.get(status='failed')
        self.assertEqual(failed.status, "failed")
        self.assertIsNotNone(failed.finished_at)
        self.assertEqual(ReportingRun.objects.filter(status="success").first().pk, old.pk)

    def test_cleanup_failure_does_not_mark_a_committed_publication_failed(self):
        from reporting_mirror.sync import run_sync
        for code in ("p6_22", "p6_23"):
            target = BackupTarget.objects.create(name=code, slug=code, app_backup_dir="/tmp")
            ReportingSource.objects.create(code=code, target=target)
        lock, pg = MagicMock(), MagicMock()
        lock.execute.return_value.fetchone.return_value = {"acquired": True}
        pg.__enter__.return_value = pg
        pg.execute.return_value.fetchone.return_value = {"n": 0}
        objects = [{"name": name} for name in ("project", "task", "taskpred")]
        snapshot = {"snapshot_at": timezone.now().isoformat(), "counts": {"projects": 0, "activities": 0, "relationships": 0}}
        published = timezone.now()
        with ExitStack() as stack:
            stack.enter_context(patch("reporting_mirror.sync.postgres_connection", side_effect=[lock, pg]))
            stack.enter_context(patch("reporting_mirror.sync.restore_snapshot", return_value=snapshot.copy()))
            for name in ("install_reporting_definitions", "assert_staging", "staging_target", "connect"):
                stack.enter_context(patch("reporting_mirror.sync." + name))
            stack.enter_context(patch("reporting_mirror.sync.rebuild_quality", return_value={}))
            stack.enter_context(patch("reporting_mirror.sync.reporting_objects", return_value=objects))
            stack.enter_context(patch("reporting_mirror.sync.copy_object", side_effect=lambda pg, stage, schema, obj, code: {"source": code, "name": obj["name"], "rows": 0}))
            stack.enter_context(patch("reporting_mirror.sync.publish_batch", return_value=published))
            stack.enter_context(patch("reporting_mirror.sync.prune_generations", side_effect=RuntimeError("Cleanup unavailable")))
            with self.assertLogs("reporting_mirror.sync", level="ERROR"):
                run_sync()
        run = ReportingRun.objects.first()
        self.assertEqual(run.status, "success")
        self.assertEqual(run.published_at, published)
        self.assertFalse(run.error_message)
