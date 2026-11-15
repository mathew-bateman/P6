from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve, reverse
from django.utils import timezone

from backups.models import BackupTarget
from backups.navigation import build_workspace_navigation, section_for_route


@override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class DatabaseCapacityViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.staff = User.objects.create_user(username="capacity.staff", is_staff=True)
        cls.viewer = User.objects.create_user(username="capacity.viewer")
        cls.target = BackupTarget.objects.create(
            name="Production P6", slug="production-p6", sql_database="P6_live",
            sql_host="p6-sql", app_backup_dir="/tmp/p6-backups", enabled=True,
        )
        cls.disabled = BackupTarget.objects.create(
            name="Training", slug="training", sql_database="P6_training",
            sql_host="training-sql", app_backup_dir="/tmp/training-backups", enabled=False,
        )

    def row(self, target=None, **overrides):
        row = {
            "target": target or self.target, "status": "ok", "edition": "Express Edition",
            "version": "17.0.1000.7", "limit_gb": 50, "data_used_gb": 12.5,
            "data_allocated_gb": 20, "log_allocated_gb": 2, "remaining_gb": 37.5,
            "used_percent": 25, "allocated_percent": 40, "used_bar_percent": 25,
            "reserved_bar_percent": 15, "severity": "healthy", "checked_at": timezone.now(),
            "unavailable_message": "", "capacity_note": "Transaction logs are shown separately.",
        }
        return {**row, **overrides}

    def page(self, rows):
        self.client.force_login(self.staff)
        with patch("backups.capacity_views.collect_database_capacity", return_value=rows) as collect:
            response = self.client.get(reverse("database_capacity"))
            targets = list(collect.call_args.args[0])
        self.assertEqual(response.status_code, 200)
        return response, targets

    @patch("backups.capacity_views.collect_database_capacity")
    def test_anonymous_redirects_without_reading_database(self, collect):
        response = self.client.get(reverse("database_capacity"))
        self.assertRedirects(
            response, "/accounts/login/?next=/database-capacity/", fetch_redirect_response=False,
        )
        collect.assert_not_called()

    @patch("backups.capacity_views.collect_database_capacity")
    def test_non_staff_is_forbidden_without_reading_database(self, collect):
        self.client.force_login(self.viewer)
        response = self.client.get(reverse("database_capacity"))
        self.assertEqual(response.status_code, 403)
        collect.assert_not_called()

    def test_staff_sees_usage_allocated_space_log_and_accessible_limit(self):
        response, targets = self.page([self.row()])
        self.assertTemplateUsed(response, "backups/database_capacity.html")
        self.assertContains(response, "12.50")
        self.assertContains(response, "GB used / 50 GB")
        self.assertContains(response, "25.0% used")
        self.assertContains(response, "37.50 GB remaining")
        self.assertContains(response, "20.00 GB")
        self.assertContains(response, "2.00 GB")
        self.assertContains(response, 'role="meter"', count=1)
        self.assertContains(response, 'aria-valuemax="100"')
        self.assertContains(response, 'aria-valuenow="25"')
        self.assertContains(response, 'aria-label="Production P6 data capacity"')
        self.assertContains(response, "Allocated, unused")
        self.assertContains(response, "Transaction log allocated")
        self.assertContains(response, "1 of 1 configured targets checked successfully.")
        self.assertEqual([target.pk for target in targets], [self.target.pk, self.disabled.pk])

    def test_disabled_targets_still_show_usage_with_backup_status(self):
        response, _ = self.page([self.row(self.disabled)])
        self.assertContains(response, "Training")
        self.assertContains(response, "Backups disabled")
        self.assertContains(response, "12.50")

    def test_warning_and_critical_have_text_labels(self):
        response, _ = self.page([
            self.row(severity="warning", data_used_gb=42, used_percent=84),
            self.row(self.disabled, severity="critical", data_used_gb=47, used_percent=94),
        ])
        self.assertContains(response, "Watch capacity")
        self.assertContains(response, "Near limit")
        self.assertContains(response, "84.0% used")
        self.assertContains(response, "94.0% used")

    def test_failed_target_does_not_hide_successful_target_or_render_zero_usage(self):
        unavailable = self.row(
            self.disabled, status="unavailable", severity="unavailable", data_used_gb=None,
            limit_gb=None, unavailable_message="Usage could not be read.",
        )
        response, _ = self.page([self.row(), unavailable])
        self.assertContains(response, "Unavailable")
        self.assertContains(response, "Usage could not be read.")
        self.assertContains(response, "Use Refresh usage to try again.")
        self.assertContains(response, 'role="meter"', count=1)
        self.assertContains(response, "1 of 2 configured targets checked successfully.")

    def test_unknown_limit_shows_usage_without_inventing_limit_or_meter(self):
        response, _ = self.page([self.row(
            edition="Express Edition", version="Unknown version", limit_gb=None,
            remaining_gb=None, used_percent=None, allocated_percent=None,
            capacity_note="Express limit could not be confirmed because the SQL Server version is unavailable.",
        )])
        self.assertContains(response, "12.50")
        self.assertNotContains(response, 'role="meter"')
        self.assertNotContains(response, "GB remaining")
        self.assertNotContains(response, "GB used / 50 GB")
        self.assertContains(response, "Express limit could not be confirmed")
        self.assertContains(response, "Usage available")

    def test_empty_configuration_has_actionable_empty_state(self):
        response, _ = self.page([])
        self.assertContains(response, "No databases configured")
        self.assertContains(response, 'href="/backup-targets/"')
        self.assertNotContains(response, 'role="meter"')

    def test_target_labels_and_errors_are_html_escaped(self):
        target = BackupTarget(
            name='<script>alert("target")</script>', slug="unsafe", sql_database="<db>",
            sql_host="<host>", app_backup_dir="/tmp/unsafe",
        )
        response, _ = self.page([self.row(
            target, status="unavailable", unavailable_message='<img src=x onerror="alert(1)">',
        )])
        self.assertContains(response, "&lt;script&gt;")
        self.assertContains(response, "&lt;db&gt;")
        self.assertContains(response, "&lt;img")
        self.assertNotContains(response, '<script>alert("target")</script>')
        self.assertNotContains(response, '<img src=x onerror="alert(1)">')

    def navigation(self, user):
        request = RequestFactory().get(reverse("database_capacity"))
        request.user = user
        request.session = {}
        request.resolver_match = resolve(request.path)
        return build_workspace_navigation(request)

    def test_staff_navigation_selects_capacity_under_operations(self):
        navigation = self.navigation(self.staff)
        self.assertEqual(section_for_route("database_capacity"), "operations")
        self.assertEqual(navigation["active_section"]["id"], "operations")
        links = [link for group in navigation["active_section"]["groups"] for link in group["links"]]
        capacity = next(link for link in links if link["id"] == "database_capacity")
        self.assertTrue(capacity["active"])
        self.assertEqual(capacity["url"], reverse("database_capacity"))

    def test_non_staff_navigation_does_not_expose_capacity(self):
        navigation = self.navigation(self.viewer)
        links = [link for section in navigation["sections"] for group in section["groups"] for link in group["links"]]
        self.assertNotIn("database_capacity", [link["id"] for link in links])
