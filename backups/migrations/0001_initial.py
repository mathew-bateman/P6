# Generated for P6BackupAdmin.
from __future__ import annotations

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BackupTarget",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("description", models.TextField(blank=True)),
                ("enabled", models.BooleanField(default=False)),
                ("sql_host", models.CharField(default="host.docker.internal", max_length=255)),
                ("sql_port", models.PositiveIntegerField(default=1433)),
                ("sql_database", models.CharField(max_length=128)),
                ("sql_username", models.CharField(default="sa", max_length=128)),
                ("sql_password_env", models.CharField(max_length=128)),
                ("odbc_driver", models.CharField(default="ODBC Driver 18 for SQL Server", max_length=120)),
                ("encrypt_connection", models.BooleanField(default=True)),
                ("trust_server_certificate", models.BooleanField(default=True)),
                ("sql_backup_dir", models.CharField(default="/var/backups", max_length=255)),
                ("app_backup_dir", models.CharField(max_length=500)),
                ("sql_data_dir", models.CharField(default="/var/database", max_length=255)),
                ("data_file_path", models.CharField(blank=True, max_length=500)),
                ("log_file_path", models.CharField(blank=True, max_length=500)),
                ("backup_schedule", models.CharField(default="0 2 * * 0", max_length=80)),
                ("sharepoint_site", models.CharField(default="root", max_length=255)),
                ("sharepoint_folder", models.CharField(default="P6 Backups", max_length=500)),
                ("notification_emails", models.TextField(blank=True)),
                ("retention_daily", models.PositiveIntegerField(default=14)),
                ("retention_weekly", models.PositiveIntegerField(default=8)),
                ("retention_monthly", models.PositiveIntegerField(default=12)),
                ("use_compression", models.BooleanField(default=False)),
                ("verify_sharepoint_upload", models.BooleanField(default=False)),
                ("keep_local_files", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="BackupRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("operation", models.CharField(choices=[("backup", "Backup"), ("restore", "Restore")], default="backup", max_length=16)),
                ("status", models.CharField(choices=[("running", "Running"), ("success", "Success"), ("failed", "Failed"), ("disabled", "Disabled")], default="running", max_length=16)),
                ("trigger", models.CharField(choices=[("scheduled", "Scheduled"), ("manual", "Manual"), ("command", "Command")], default="scheduled", max_length=16)),
                ("backup_name", models.CharField(blank=True, max_length=180)),
                ("plain_filename", models.CharField(blank=True, max_length=220)),
                ("encrypted_filename", models.CharField(blank=True, max_length=240)),
                ("manifest_filename", models.CharField(blank=True, max_length=260)),
                ("plaintext_size_bytes", models.BigIntegerField(default=0)),
                ("encrypted_size_bytes", models.BigIntegerField(default=0)),
                ("plaintext_sha256", models.CharField(blank=True, max_length=64)),
                ("encrypted_sha256", models.CharField(blank=True, max_length=64)),
                ("sharepoint_folder", models.CharField(blank=True, max_length=500)),
                ("sharepoint_backup_item_id", models.CharField(blank=True, max_length=255)),
                ("sharepoint_manifest_item_id", models.CharField(blank=True, max_length=255)),
                ("restore_source_filename", models.CharField(blank=True, max_length=260)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("target", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="runs", to="backups.backuptarget")),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddIndex(
            model_name="backuprun",
            index=models.Index(fields=["target", "-started_at"], name="backups_bac_target__c26f2d_idx"),
        ),
        migrations.AddIndex(
            model_name="backuprun",
            index=models.Index(fields=["status", "-started_at"], name="backups_bac_status_43fe41_idx"),
        ),
    ]
