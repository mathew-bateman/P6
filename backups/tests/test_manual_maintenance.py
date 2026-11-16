from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

class ManualMaintenanceTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="maintainer", is_staff=True)
        self.client.force_login(self.staff)

    @patch("backups.views.execute_index_defragmentation.delay")
    def test_staff_can_queue_manual_task(self, delay):
        response = self.client.post(reverse("maintenance_run"), {"task": "index"})
        self.assertRedirects(response, reverse("maintenance_dashboard"), fetch_redirect_response=False)
        delay.assert_called_once_with(trigger="manual")

    @patch("backups.views.execute_index_defragmentation.delay")
    def test_invalid_task_does_not_queue(self, delay):
        self.client.post(reverse("maintenance_run"), {"task": "unknown"})
        delay.assert_not_called()

    @patch("backups.views.execute_index_defragmentation.delay")
    def test_non_staff_cannot_queue(self, delay):
        self.client.force_login(get_user_model().objects.create_user(username="viewer"))
        self.assertEqual(self.client.post(reverse("maintenance_run"), {"task": "index"}).status_code, 403)
        delay.assert_not_called()
