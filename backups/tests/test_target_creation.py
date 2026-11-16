from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from backups.models import BackupTarget


class TargetCreationTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="creator", password="test", is_staff=True)
        self.client.force_login(self.user)
        self.data = {"name": "Test P6", "sql_host": "sql.example", "sql_port": "1433", "sql_database": "P6Test", "sql_username": "backup", "sql_password_env": "example", "app_backup_dir": "/tmp/p6", "sql_backup_dir": "/var/backups", "sharepoint_site": "root", "sharepoint_folder": "P6 Backups/Test"}

    def test_create_is_disabled_and_visible_in_sidebar(self):
        response = self.client.post(reverse("backup_target_create"), self.data)
        target = BackupTarget.objects.get(slug="test-p6")
        self.assertFalse(target.enabled)
        self.assertRedirects(response, target.get_absolute_url())
        response = self.client.get(reverse("backup_dashboard"))
        self.assertContains(response, "Test P6")
        self.assertContains(response, "Databases")

    def test_duplicate_target_is_rejected(self):
        self.client.post(reverse("backup_target_create"), self.data)
        response = self.client.post(reverse("backup_target_create"), self.data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "A target with this name already exists", status_code=400)
        self.assertEqual(BackupTarget.objects.filter(slug="test-p6").count(), 1)

    def test_non_staff_cannot_create(self):
        self.client.force_login(get_user_model().objects.create_user(username="viewer"))
        self.assertEqual(self.client.post(reverse("backup_target_create"), self.data).status_code, 403)
        self.assertFalse(BackupTarget.objects.exists())
