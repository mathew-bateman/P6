# Generated for P6BackupAdmin.
from __future__ import annotations

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("backups", "0002_rename_backups_bac_target__c26f2d_idx_backups_bac_target__a02d54_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="backuptarget",
            name="sql_password_env",
            field=models.CharField("SQL password", blank=True, max_length=128),
        ),
    ]
