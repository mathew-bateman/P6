"""Retirement must never revive SQL reporting after configuration drift.

All database and schedule managers are mocked. These tests neither connect to
SQL Server/PostgreSQL nor write application or scheduler state.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import OperationalError, ProgrammingError
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse


class RetiredReportReaderTests(SimpleTestCase):
    @patch('reporting_mirror.reader.retired', return_value=True)
    @patch('reporting_mirror.reader.active_config', return_value=None)
    @patch('reporting_mirror.reader.sql_fetch_rows')
    @patch('reporting_mirror.reader.postgres_connection')
    def test_retired_report_cannot_fall_back_to_sql_for_any_source(self, pg, legacy, config, retired):
        from reporting_mirror.reader import fetch_rows, report_source
        for source in ('p6_22', 'p6_23', 'combined'):
            token = report_source.set(source)
            try:
                with self.subTest(source=source), self.assertRaisesMessage(RuntimeError, 'PostgreSQL reporting must remain enabled'):
                    fetch_rows(None, 'SELECT 1')
            finally:
                report_source.reset(token)
        pg.assert_not_called()
        legacy.assert_not_called()

    @patch('reporting_mirror.reader.retired', return_value=False)
    @patch('reporting_mirror.reader.active_config', return_value=None)
    @patch('reporting_mirror.reader.sql_fetch_rows', return_value=[{'value': 1}])
    def test_unretired_recovery_host_keeps_its_original_sql_reader(self, legacy, config, retired):
        from reporting_mirror.reader import fetch_rows, report_source
        token = report_source.set('p6_22')
        try:
            self.assertEqual(fetch_rows('target', 'SELECT 1', parameters=(3,), database='P6'), [{'value': 1}])
        finally:
            report_source.reset(token)
        legacy.assert_called_once_with('target', 'SELECT 1', parameters=(3,), database='P6', transactional=False)

    @patch('reporting_mirror.reader.active_config', return_value=SimpleNamespace(query_timeout_seconds=60))
    @patch('reporting_mirror.reader.sql_fetch_rows')
    def test_active_postgres_reader_uses_the_pinned_connection(self, legacy, config):
        from reporting_mirror.reader import fetch_rows, report_connection, report_source
        pinned = MagicMock()
        pinned.execute.return_value.fetchall.return_value = [{'value': 1}]
        token = report_connection.set(pinned)
        source_token = report_source.set('p6_22')
        try:
            self.assertEqual(fetch_rows(None, 'SELECT 1 AS value'), [{'value': 1}])
        finally:
            report_source.reset(source_token)
            report_connection.reset(token)
        pinned.execute.assert_called_once()
        legacy.assert_not_called()


class RetiredSettingsRoutingTests(SimpleTestCase):
    def settings_state(self, retired, available, configured=None, error=None):
        from reporting_mirror.native_settings import enabled
        with patch('reporting_mirror.native_settings.retired', return_value=retired), \
             patch('reporting_mirror.native_settings.reporting_available', return_value=available), \
             patch('reporting_mirror.models.ReportingConfig.objects.filter') as manager:
            manager.return_value.exists.return_value = configured
            manager.return_value.exists.side_effect = error
            return enabled()

    def test_retired_settings_remain_postgres_when_configured(self):
        self.assertTrue(self.settings_state(True, True, True))

    def test_retired_missing_postgres_configuration_cannot_choose_sql(self):
        with self.assertRaisesMessage(RuntimeError, 'settings are not configured'):
            self.settings_state(True, False)

    def test_retired_disabled_postgres_settings_cannot_choose_sql(self):
        with self.assertRaisesMessage(RuntimeError, 'settings must remain enabled'):
            self.settings_state(True, True, False)

    def test_retired_operational_database_error_cannot_choose_sql(self):
        with self.assertRaisesMessage(RuntimeError, 'settings are unavailable'):
            self.settings_state(True, True, error=OperationalError('unavailable'))

    def test_retired_schema_database_error_cannot_choose_sql(self):
        with self.assertRaisesMessage(RuntimeError, 'settings are unavailable'):
            self.settings_state(True, True, error=ProgrammingError('unavailable'))

    def test_unretired_recovery_host_can_have_no_postgres_configuration(self):
        self.assertFalse(self.settings_state(False, False))

    def test_unretired_recovery_host_can_keep_settings_in_sql(self):
        self.assertFalse(self.settings_state(False, True, False))

    def test_unretired_recovery_host_keeps_existing_database_error_handling(self):
        for error in (OperationalError('unavailable'), ProgrammingError('unavailable')):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(self.settings_state(False, True, error=error))


class RetiredScheduleTests(SimpleTestCase):
    def sync_schedule(self, *, retired, enabled):
        from backups.services.scheduling import sync_schedule_quality_refresh_schedule
        task = MagicMock(enabled=True)
        with patch('backups.services.scheduling.retired', return_value=retired), \
             patch('backups.services.scheduling.CrontabSchedule.objects.get_or_create', return_value=('cron', False)) as cron, \
             patch('backups.services.scheduling.PeriodicTask.objects.get_or_create', return_value=(task, False)) as manager:
            returned = sync_schedule_quality_refresh_schedule(enabled=enabled, crontab_string='*/15 * * * *')
        self.assertIs(returned, task)
        task.save.assert_called_once()
        return task, manager.call_args.kwargs['defaults']

    def test_management_or_ui_reenable_attempt_stays_disabled_after_retirement(self):
        task, defaults = self.sync_schedule(retired=True, enabled=True)
        self.assertFalse(task.enabled)
        self.assertFalse(defaults['enabled'])
        self.assertIn('Retired SQL reporting schedule', task.description)

    def test_retired_disabled_schedule_stays_disabled(self):
        task, defaults = self.sync_schedule(retired=True, enabled=False)
        self.assertFalse(task.enabled)
        self.assertFalse(defaults['enabled'])

    def test_unretired_recovery_schedule_can_still_be_enabled(self):
        task, defaults = self.sync_schedule(retired=False, enabled=True)
        self.assertTrue(task.enabled)
        self.assertTrue(defaults['enabled'])
        self.assertEqual(task.description, 'Refreshes materialized Power BI schedule-quality tables.')

    def test_retired_dashboard_does_not_show_a_stale_enabled_schedule(self):
        from backups.services.scheduling import get_schedule_quality_refresh_schedule
        task = SimpleNamespace(enabled=True, crontab=SimpleNamespace(minute='0', hour='2', day_of_month='*', month_of_year='*', day_of_week='0'))
        with patch('backups.services.scheduling.retired', return_value=True), \
             patch('backups.services.scheduling.PeriodicTask.objects.select_related') as manager:
            manager.return_value.filter.return_value.first.return_value = task
            state = get_schedule_quality_refresh_schedule(default_crontab='*/15 * * * *', default_enabled=True)
        self.assertFalse(state['enabled'])


class RetiredScheduleUiTests(SimpleTestCase):
    def staff_request(self):
        request = RequestFactory().post('/schedule-quality/schedule/', {'enabled': 'on'})
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        return request

    @patch('reporting_mirror.sql_retirement.retired', return_value=True)
    @patch('backups.views.can_edit_schedule_quality_schedule', return_value=True)
    @patch('backups.views.build_schedule_from_post')
    @patch('backups.views.sync_schedule_quality_refresh_schedule')
    @patch('backups.views.messages.warning')
    def test_retired_schedule_post_redirects_to_postgres_controls_without_saving(self, warning, sync, build, permitted, retired):
        from backups.views import ScheduleQualityScheduleUpdateView
        request = self.staff_request()
        response = ScheduleQualityScheduleUpdateView().post(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('reporting_mirror_dashboard'))
        warning.assert_called_once_with(request, 'Legacy SQL refresh schedule is retired. Manage PostgreSQL refresh in Reporting Database.')
        build.assert_not_called()
        sync.assert_not_called()

    @patch('reporting_mirror.sql_retirement.retired', return_value=True)
    @patch('backups.views.can_edit_schedule_quality_schedule', return_value=False)
    @patch('backups.views.sync_schedule_quality_refresh_schedule')
    @patch('backups.views.messages.warning')
    def test_schedule_post_preserves_permission_denial(self, warning, sync, permitted, retired):
        from backups.views import ScheduleQualityScheduleUpdateView
        with self.assertRaises(PermissionDenied):
            ScheduleQualityScheduleUpdateView().post(self.staff_request())
        warning.assert_not_called()
        sync.assert_not_called()

    @patch('reporting_mirror.sql_retirement.retired', return_value=False)
    @patch('backups.views.can_edit_schedule_quality_schedule', return_value=True)
    @patch('backups.views.build_schedule_from_post', return_value='*/15 * * * *')
    @patch('backups.views.sync_schedule_quality_refresh_schedule')
    @patch('backups.views.messages.success')
    def test_unretired_recovery_host_schedule_post_keeps_existing_behaviour(self, success, sync, build, permitted, retired):
        from backups.views import ScheduleQualityScheduleUpdateView
        response = ScheduleQualityScheduleUpdateView().post(self.staff_request())
        self.assertEqual(response.url, reverse('schedule_quality_dashboard'))
        sync.assert_called_once_with(enabled=True, crontab_string='*/15 * * * *', proj_id=None)
        success.assert_called_once()

    @patch('backups.views.can_edit_schedule_quality_schedule', return_value=True)
    @patch('backups.views.count_schedule_quality_refresh_history', return_value=0)
    @patch('backups.views.fetch_schedule_quality_refresh_history', return_value=[])
    @patch('backups.views.get_schedule_quality_refresh_schedule', return_value={'enabled': False, 'crontab': '*/15 * * * *'})
    def test_retirement_hides_old_schedule_without_revoking_postgres_settings_permission(self, schedule, history, count, permitted):
        from backups.views import ScheduleQualityDashboardView
        request = RequestFactory().get('/schedule-quality/')
        request.user = SimpleNamespace(is_authenticated=True, is_staff=True)
        for retired in (True, False):
            with self.subTest(retired=retired), patch('reporting_mirror.sql_retirement.retired', return_value=retired):
                view = ScheduleQualityDashboardView()
                view.setup(request)
                context = view.get_context_data()
                self.assertEqual(context['can_edit_schedule_quality_schedule'], not retired)
                self.assertTrue(context['can_edit_schedule_quality_settings'])

    def test_retired_dashboard_ignores_enabled_environment_default(self):
        from backups.services.scheduling import get_schedule_quality_refresh_schedule
        with patch('backups.services.scheduling.retired', return_value=True), \
             patch('backups.services.scheduling.PeriodicTask.objects.select_related') as manager:
            manager.return_value.filter.return_value.first.return_value = None
            state = get_schedule_quality_refresh_schedule(default_crontab='*/15 * * * *', default_enabled=True)
        self.assertFalse(state['enabled'])
