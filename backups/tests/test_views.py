from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from backups.services.schedule_quality import ScheduleQualitySettingsConflict
from backups.tests.test_schedule_quality import (
    SAVED_SETTINGS_HASH,
    SETTINGS_HASH,
    build_settings_snapshot,
)
from backups.views import parse_backup_schedule_for_form, parse_non_negative_int


class ViewHelperTests(SimpleTestCase):
    def test_parse_weekly_schedule_for_form(self):
        payload = parse_backup_schedule_for_form("30 1 * * 1,3,5")
        self.assertEqual(payload["schedule_type"], "weekly")
        self.assertEqual(payload["backup_time"], "01:30")
        self.assertEqual(payload["backup_days"], ["1", "3", "5"])

    def test_parse_daily_schedule_for_form(self):
        payload = parse_backup_schedule_for_form("0 2 * * *")
        self.assertEqual(payload["schedule_type"], "daily")
        self.assertEqual(payload["backup_days"], ["0", "1", "2", "3", "4", "5", "6"])

    def test_parse_non_negative_int_allows_zero(self):
        self.assertEqual(parse_non_negative_int("0", 3), 0)
        self.assertEqual(parse_non_negative_int("-1", 3), 0)
        self.assertEqual(parse_non_negative_int("bad", 3), 3)


class ScheduleQualityRefreshViewTests(TestCase):
    def create_schedule_quality_editor(self, username="jason.mappin"):
        user = get_user_model().objects.create_user(
            username=username,
            password="password",
        )
        group = Group.objects.create(name=settings.P6_SCHEDULE_QUALITY_EDITOR_GROUP)
        user.groups.add(group)
        return user

    @staticmethod
    def settings_post_data(*, settings_hash=SETTINGS_HASH, action="publish"):
        return {
            "config_version_id": "11",
            "expected_settings_hash": settings_hash,
            "change_note": "Match User Settings",
            "action": action,
            "check__missing_predecessor__is_enabled": "on",
            "check__missing_predecessor__include_milestones": "on",
            "check__missing_predecessor__exclude_complete": "on",
            "check__invalid_dates__is_enabled": "on",
            "option__exclude_deleted_activities": "on",
            "option__high_float_days": "84",
            "option__excessive_ss_percent": "50",
            "constraint__mandatory_start": "on",
        }

    @patch("backups.views.execute_schedule_quality_refresh")
    def test_staff_user_can_queue_schedule_quality_refresh(self, refresh_task):
        user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.post(reverse("schedule_quality_refresh"))

        self.assertRedirects(
            response,
            reverse("schedule_quality_dashboard"),
            fetch_redirect_response=False,
        )
        refresh_task.delay.assert_called_once_with(trigger="manual")

    @patch("backups.views.execute_schedule_quality_refresh")
    def test_non_staff_user_can_queue_schedule_quality_refresh(self, refresh_task):
        user = get_user_model().objects.create_user(
            username="jason.mappin",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(reverse("schedule_quality_refresh"))

        self.assertRedirects(
            response,
            reverse("schedule_quality_dashboard"),
            fetch_redirect_response=False,
        )
        refresh_task.delay.assert_called_once_with(trigger="manual")

    def test_non_staff_dashboard_redirects_to_schedule_quality_page(self):
        user = get_user_model().objects.create_user(
            username="jason.mappin",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("backup_dashboard"))

        self.assertRedirects(
            response,
            reverse("schedule_quality_dashboard"),
            fetch_redirect_response=False,
        )

    def test_landing_page_lists_only_areas_available_to_user(self):
        user = get_user_model().objects.create_user(
            username="viewer",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("suite_landing"))

        self.assertContains(response, "Schedule Quality")
        self.assertNotContains(response, "Open Backup Targets")
        self.assertNotContains(response, "Open Maintenance")

    def test_staff_landing_page_lists_all_navigation_areas(self):
        user = get_user_model().objects.create_user(
            username="staff",
            password="password",
            is_staff=True,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("suite_landing"))

        self.assertContains(response, "Open Backup Targets")
        self.assertContains(response, "Open Schedule Quality")
        self.assertContains(response, "Open Maintenance")

    @patch("backups.views.count_schedule_quality_refresh_history")
    @patch("backups.views.fetch_schedule_quality_refresh_history")
    def test_schedule_quality_dashboard_shows_refresh_history(self, fetch_history, count_history):
        count_history.return_value = 12
        fetch_history.return_value = [
            {
                "check_run_id": 11,
                "config_version_id": 17,
                "trigger_type": "manual",
                "status": "success",
                "started_at": None,
                "completed_at": None,
                "duration_display": "32s",
                "processed_project_count": 554,
                "logic_loop_task_count": 139,
            }
        ]
        user = get_user_model().objects.create_user(
            username="jason.mappin",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("schedule_quality_dashboard"))

        self.assertContains(response, "Refresh all data")
        self.assertContains(response, "Manual")
        self.assertContains(response, "32s")
        self.assertContains(response, "554")
        self.assertContains(response, "17")
        self.assertContains(response, "Showing 1-10 of 12 refresh runs")
        fetch_history.assert_called_once_with(limit=10, offset=0, status="")

    @patch("backups.views.count_schedule_quality_refresh_history")
    @patch("backups.views.fetch_schedule_quality_refresh_history")
    def test_schedule_quality_dashboard_can_page_refresh_history(self, fetch_history, count_history):
        count_history.return_value = 12
        fetch_history.return_value = []
        user = get_user_model().objects.create_user(
            username="jason.mappin",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("schedule_quality_dashboard"), {"page": "2"})

        self.assertEqual(response.status_code, 200)
        fetch_history.assert_called_once_with(limit=10, offset=10, status="")

    @patch("backups.views.count_schedule_quality_refresh_history")
    @patch("backups.views.fetch_schedule_quality_refresh_history")
    def test_jason_dashboard_shows_schedule_edit_button(self, fetch_history, count_history):
        count_history.return_value = 0
        fetch_history.return_value = []
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.get(reverse("schedule_quality_dashboard"))

        self.assertContains(response, "Automatic schedule")
        self.assertContains(response, "Edit Schedule")
        self.assertContains(response, "Save Schedule")
        self.assertNotContains(response, "Cron expression")

    @patch("backups.views.sync_schedule_quality_refresh_schedule")
    def test_jason_can_update_schedule_quality_schedule(self, sync_schedule):
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.post(
            reverse("schedule_quality_schedule_update"),
            {
                "enabled": "on",
                "schedule_type": "daily",
                "backup_time": "03:30",
                "backup_schedule": "0 3 * * *",
            },
        )

        self.assertRedirects(
            response,
            reverse("schedule_quality_dashboard"),
            fetch_redirect_response=False,
        )
        sync_schedule.assert_called_once_with(
            enabled=True,
            crontab_string="30 3 * * *",
            proj_id=None,
        )

    @patch("backups.views.sync_schedule_quality_refresh_schedule")
    def test_other_non_staff_user_cannot_update_schedule_quality_schedule(self, sync_schedule):
        user = get_user_model().objects.create_user(
            username="viewer",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("schedule_quality_schedule_update"),
            {
                "enabled": "on",
                "schedule_type": "daily",
                "backup_time": "03:30",
            },
        )

        self.assertEqual(response.status_code, 403)
        sync_schedule.assert_not_called()

    @patch("backups.views.fetch_schedule_quality_settings")
    @patch("backups.views.get_or_create_schedule_quality_draft")
    def test_editor_can_open_sql_backed_quality_settings(self, get_draft, fetch_settings):
        get_draft.return_value = 11
        fetch_settings.return_value = build_settings_snapshot()
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.get(reverse("schedule_quality_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Configure Schedule Quality")
        self.assertContains(response, "Missing predecessor")
        self.assertNotContains(response, "How changes reach Power BI")
        self.assertNotContains(response, "Stores your changes in SQL Server only")
        self.assertNotContains(response, "Unpublished drafts are ignored")
        self.assertContains(
            response,
            "These published settings supply the scope, thresholds and points",
        )
        self.assertContains(response, "Save draft only")
        self.assertContains(response, "Publish and rebuild")
        self.assertContains(response, "N/A")
        self.assertContains(response, "Optional check scope and constraints")
        self.assertNotContains(response, 'class="settings-advanced"')
        self.assertContains(response, "Check scope")
        self.assertContains(response, "Exclude activities marked as deleted")
        self.assertContains(response, "Activity Status code DEL")
        self.assertContains(response, "Constraint types checked")
        self.assertNotContains(response, "Allowed constraint types")
        self.assertContains(response, SETTINGS_HASH)
        html = response.content.decode()
        advanced_start = html.index('<section class="panel settings-scope-panel">')
        advanced_end = html.index("</section>", advanced_start)
        form_start = html.rfind("<form", 0, advanced_start)
        form_end = html.index("</form>", advanced_end)
        self.assertLess(form_start, advanced_start)
        self.assertLess(advanced_start, advanced_end)
        self.assertLess(advanced_end, form_end)
        self.assertIn("Check scope", html[advanced_start:advanced_end])
        self.assertIn("Constraint types checked", html[advanced_start:advanced_end])
        get_draft.assert_called_once_with(
            changed_by="jason.mappin",
            profile_code="default",
        )
        fetch_settings.assert_called_once_with(config_version_id=11)

    @patch("backups.views.fetch_schedule_quality_settings")
    def test_viewer_cannot_open_quality_settings(self, fetch_settings):
        user = get_user_model().objects.create_user(
            username="viewer",
            password="password",
        )
        self.client.force_login(user)

        response = self.client.get(reverse("schedule_quality_settings"))

        self.assertEqual(response.status_code, 403)
        fetch_settings.assert_not_called()

    @patch("backups.views.publish_schedule_quality_config_task")
    @patch("backups.views.save_schedule_quality_draft")
    @patch("backups.views.fetch_schedule_quality_settings")
    def test_editor_can_publish_settings(
        self,
        fetch_settings,
        save_draft,
        publish_task,
    ):
        fetch_settings.return_value = build_settings_snapshot()
        save_draft.return_value = SAVED_SETTINGS_HASH
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.post(
            reverse("schedule_quality_settings"),
            self.settings_post_data(),
        )

        self.assertRedirects(
            response,
            reverse("schedule_quality_settings"),
            fetch_redirect_response=False,
        )
        save_draft.assert_called_once()
        self.assertEqual(save_draft.call_args.kwargs["config_version_id"], 11)
        self.assertEqual(
            save_draft.call_args.kwargs["expected_settings_hash"],
            SETTINGS_HASH,
        )
        self.assertEqual(save_draft.call_args.kwargs["changed_by"], "jason.mappin")
        publish_task.delay.assert_called_once_with(
            config_version_id=11,
            expected_settings_hash=SAVED_SETTINGS_HASH,
            published_by="jason.mappin",
        )

    @patch("backups.views.publish_schedule_quality_config_task")
    @patch("backups.views.save_schedule_quality_draft")
    @patch("backups.views.fetch_schedule_quality_settings")
    def test_stale_form_hash_does_not_save_or_publish(
        self,
        fetch_settings,
        save_draft,
        publish_task,
    ):
        fetch_settings.return_value = build_settings_snapshot()
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.post(
            reverse("schedule_quality_settings"),
            self.settings_post_data(settings_hash=SAVED_SETTINGS_HASH),
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "This draft changed after the page was loaded",
            status_code=400,
        )
        self.assertContains(response, "Check scope and score policy", status_code=400)
        save_draft.assert_not_called()
        publish_task.delay.assert_not_called()

    @patch("backups.views.publish_schedule_quality_config_task")
    @patch("backups.views.save_schedule_quality_draft")
    @patch("backups.views.fetch_schedule_quality_settings")
    def test_save_conflict_returns_reload_message(
        self,
        fetch_settings,
        save_draft,
        publish_task,
    ):
        fetch_settings.return_value = build_settings_snapshot()
        save_draft.side_effect = ScheduleQualitySettingsConflict("stale")
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.post(
            reverse("schedule_quality_settings"),
            self.settings_post_data(action="save"),
        )

        self.assertContains(
            response,
            "This draft changed while you were editing it",
            status_code=409,
        )
        publish_task.delay.assert_not_called()

    @patch("backups.views.publish_schedule_quality_config_task")
    @patch("backups.views.save_schedule_quality_draft")
    @patch("backups.views.fetch_schedule_quality_settings")
    def test_publish_queue_failure_leaves_active_results_unchanged(
        self,
        fetch_settings,
        save_draft,
        publish_task,
    ):
        fetch_settings.return_value = build_settings_snapshot()
        save_draft.return_value = SAVED_SETTINGS_HASH
        publish_task.delay.side_effect = RuntimeError("broker unavailable")
        user = self.create_schedule_quality_editor()
        self.client.force_login(user)

        response = self.client.post(
            reverse("schedule_quality_settings"),
            self.settings_post_data(),
        )

        self.assertContains(
            response,
            "could not be queued",
            status_code=502,
        )
        publish_task.delay.assert_called_once_with(
            config_version_id=11,
            expected_settings_hash=SAVED_SETTINGS_HASH,
            published_by="jason.mappin",
        )


class ScheduleQualityEditorCommandTests(TestCase):
    def test_command_grants_editor_group(self):
        user = get_user_model().objects.create_user(
            username="jason.mappin",
            password="password",
        )
        stdout = StringIO()

        call_command(
            "grant_schedule_quality_editor",
            "jason.mappin",
            stdout=stdout,
        )

        self.assertTrue(
            user.groups.filter(name=settings.P6_SCHEDULE_QUALITY_EDITOR_GROUP).exists()
        )
        self.assertIn("Granted schedule-quality editor access", stdout.getvalue())
