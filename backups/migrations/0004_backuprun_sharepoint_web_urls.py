# Generated for P6BackupAdmin.
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("backups", "0003_alter_backuptarget_sql_password_env"),
    ]

    operations = [
        migrations.AddField(
            model_name="backuprun",
            name="sharepoint_backup_web_url",
            field=models.URLField(blank=True, max_length=1000),
        ),
        migrations.AddField(
            model_name="backuprun",
            name="sharepoint_manifest_web_url",
            field=models.URLField(blank=True, max_length=1000),
        ),
    ]
