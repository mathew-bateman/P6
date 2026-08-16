from __future__ import annotations

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from backups.models import BackupRun, BackupTarget
from backups.services.emailing import send_backup_status_email


class BackupStatusEmailTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        GRAPH_MAIL_SENDER="",
        DEFAULT_FROM_EMAIL="p6-backups@example.local",
    )
    def test_backup_status_email_uses_axialpeople_template_shell(self) -> None:
        target = BackupTarget.objects.create(
            name="Axial Training",
            slug="axial-training",
            sql_database="Axial_Training",
            app_backup_dir="/backups",
            notification_emails="mathew@example.com",
        )
        backup_run = BackupRun.objects.create(
            target=target,
            operation=BackupRun.OPERATION_BACKUP,
            status=BackupRun.STATUS_SUCCESS,
            encrypted_filename="axial-training_backup_20260428_215520.bak.enc",
            encrypted_size_bytes=256156303,
            sharepoint_folder="P6 Backups/Axial Training",
            finished_at=timezone.now(),
        )

        send_backup_status_email(backup_run=backup_run, success=True)

        self.assertEqual(len(mail.outbox), 1)
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn('class="email-wrapper"', html_body)
        self.assertIn('class="email-container"', html_body)
        self.assertIn("Axial Projects", html_body)
        self.assertIn("Backup Success", html_body)
        self.assertIn("Axial Training", html_body)
        self.assertIn("SharePoint P6 Backups/Axial Training", html_body)
