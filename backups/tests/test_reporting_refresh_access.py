"""The compatibility refresh control shares PostgreSQL permissions and audit data.

Queues and external databases are mocked; these checks never start a refresh.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from backups.tasks import execute_schedule_quality_refresh
from backups.views import (
    ScheduleQualityDashboardView,
    TriggerScheduleQualityRefreshView,
    can_view_schedule_quality_reports,
)
from reporting_mirror.views import ReportingRefreshView


def reporting_user(*, staff=False, refresh_permission=False):
    groups = MagicMock()
    groups.filter.return_value.exists.return_value = False
    return SimpleNamespace(
        is_authenticated=True,
        is_staff=staff,
        groups=groups,
        has_perm=lambda permission: refresh_permission and permission == "reporting_mirror.refresh_reporting",
        get_username=lambda: "refresh.operator",
        get_full_name=lambda: "Refresh Operator",
    )


class ReportingRefreshAccessTests(SimpleTestCase):
    def request(self, path, user):
        request = RequestFactory().post(path)
        request.user = user
        request.session = {}
        return request

    def test_both_refresh_endpoints_require_login_before_queueing(self):
        for name, view in (
            ("schedule_quality_refresh", TriggerScheduleQualityRefreshView),
            ("reporting_mirror_refresh", ReportingRefreshView),
        ):
            with self.subTest(endpoint=name), \
                 patch("backups.views.execute_schedule_quality_refresh.delay") as legacy, \
                 patch("reporting_mirror.views.refresh_reporting.apply_async") as native:
                response = view.as_view()(self.request(reverse(name), AnonymousUser()))
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse("login"), response.url)
                legacy.assert_not_called()
                native.assert_not_called()

    def test_both_endpoints_deny_authenticated_user_without_refresh_permission(self):
        for name, view in (
            ("schedule_quality_refresh", TriggerScheduleQualityRefreshView),
            ("reporting_mirror_refresh", ReportingRefreshView),
        ):
            with self.subTest(endpoint=name), \
                 patch("backups.views.execute_schedule_quality_refresh.delay") as legacy, \
                 patch("reporting_mirror.views.refresh_reporting.apply_async") as native:
                with self.assertRaises(PermissionDenied):
                    view.as_view()(self.request(reverse(name), reporting_user()))
                legacy.assert_not_called()
                native.assert_not_called()

    @patch("backups.views.execute_schedule_quality_refresh.delay")
    def test_quality_report_membership_does_not_grant_refresh_permission(self, queued):
        user = reporting_user()
        user.groups.filter.return_value.exists.return_value = True
        self.assertTrue(can_view_schedule_quality_reports(user))
        with self.assertRaises(PermissionDenied):
            TriggerScheduleQualityRefreshView.as_view()(
                self.request(reverse("schedule_quality_refresh"), user)
            )
        queued.assert_not_called()

    @patch("backups.views.messages.success")
    @patch("backups.views.execute_schedule_quality_refresh.delay")
    def test_staff_and_explicit_refresh_permission_carry_actor(self, queued, message):
        for user in (reporting_user(staff=True), reporting_user(refresh_permission=True)):
            with self.subTest(staff=user.is_staff):
                queued.reset_mock()
                response = TriggerScheduleQualityRefreshView.as_view()(
                    self.request(reverse("schedule_quality_refresh"), user)
                )
                self.assertEqual(response.status_code, 302)
                queued.assert_called_once_with(trigger="manual", requested_by="refresh.operator")

    @patch("reporting_mirror.views.reporting_available", return_value=True)
    @patch("reporting_mirror.views.messages.success")
    @patch("reporting_mirror.views.refresh_reporting.apply_async")
    def test_existing_postgres_refresh_keeps_explicit_permission_and_actor(self, queued, message, available):
        response = ReportingRefreshView.as_view()(
            self.request(reverse("reporting_mirror_refresh"), reporting_user(refresh_permission=True))
        )
        self.assertEqual(response.status_code, 302)
        queued.assert_called_once_with(
            kwargs={"trigger": "manual", "requested_by": "refresh.operator", "force": True},
            queue="p6_reporting",
        )

    @override_settings(STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    })
    @patch("backups.views.can_edit_schedule_quality_schedule", return_value=False)
    @patch("backups.views.can_edit_schedule_quality_settings", return_value=False)
    @patch("backups.views.count_schedule_quality_refresh_history", return_value=0)
    @patch("backups.views.fetch_schedule_quality_refresh_history", return_value=[])
    @patch("backups.views.get_schedule_quality_refresh_schedule", return_value={"enabled": False, "crontab": "0 2 * * *"})
    def test_dashboard_only_displays_refresh_action_to_authorized_users(self, *mocks):
        for allowed in (False, True):
            with self.subTest(allowed=allowed):
                request = self.request(reverse("schedule_quality_dashboard"), reporting_user(refresh_permission=allowed))
                view = ScheduleQualityDashboardView()
                view.setup(request)
                context = view.get_context_data()
                self.assertEqual(context["can_refresh_schedule_quality"], allowed)
                html = get_template(view.template_name).render(context, request)
                action = f'action="{reverse("schedule_quality_refresh")}"'
                self.assertEqual(action in html, allowed)


class ReportingRefreshTaskCompatibilityTests(SimpleTestCase):
    @patch("reporting_mirror.native_settings.enabled", return_value=True)
    @patch("reporting_mirror.tasks.refresh_reporting.apply_async")
    @patch("backups.tasks.refresh_schedule_quality")
    def test_manual_refresh_routes_to_postgres_with_original_actor(self, legacy, queued, enabled):
        execute_schedule_quality_refresh.run(trigger="manual", requested_by="refresh.operator")
        queued.assert_called_once_with(
            kwargs={"trigger": "manual", "requested_by": "refresh.operator", "force": True, "recalculate_only": False},
            queue="p6_reporting",
        )
        legacy.assert_not_called()

    @patch("reporting_mirror.native_settings.enabled", return_value=True)
    @patch("reporting_mirror.models.ReportingConfig.objects.filter")
    @patch("reporting_mirror.tasks.refresh_reporting.apply_async")
    def test_existing_queued_payload_without_actor_still_routes_to_postgres(self, queued, config, enabled):
        config.return_value.exists.return_value = False
        execute_schedule_quality_refresh.run()
        self.assertEqual(queued.call_args.kwargs["kwargs"]["requested_by"], "")
        self.assertEqual(queued.call_args.kwargs["kwargs"]["trigger"], "scheduled")
        self.assertEqual(queued.call_args.kwargs["queue"], "p6_reporting")

    @patch("reporting_mirror.native_settings.enabled", return_value=False)
    @patch("backups.tasks.refresh_schedule_quality", return_value="Recovered SQL refresh")
    @patch("reporting_mirror.tasks.refresh_reporting.apply_async")
    def test_unretired_recovery_host_preserves_sql_task_contract(self, queued, legacy, enabled):
        for actor in (None, "refresh.operator"):
            with self.subTest(actor=actor):
                legacy.reset_mock()
                payload = {"proj_id": 1444, "trigger": "manual"}
                if actor is not None:
                    payload["requested_by"] = actor
                self.assertEqual(execute_schedule_quality_refresh.run(**payload), "Recovered SQL refresh")
                legacy.assert_called_once_with(proj_id=1444, trigger="manual")
        queued.assert_not_called()
