import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("reporting_mirror", "0003_reportingconfig_pending_engine")]
    operations = [
        migrations.AddField(model_name="reportingconfig", name="sql_parallel_enabled", field=models.BooleanField(default=False)),
        migrations.CreateModel(
            name="SQLParallelRun",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(default="queued", max_length=12)),
                ("trigger", models.CharField(default="scheduled", max_length=20)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("postgres_config_version_id", models.BigIntegerField(blank=True, null=True)),
                ("settings_hash", models.CharField(blank=True, max_length=64)),
                ("sql_config_version_id", models.BigIntegerField(blank=True, null=True)),
                ("sql_check_run_id", models.BigIntegerField(blank=True, null=True)),
                ("processed_project_count", models.PositiveIntegerField(blank=True, null=True)),
                ("progress", models.CharField(default="Queued for SQL reporting refresh", max_length=250)),
                ("error_message", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("publication", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="reporting_mirror.reportingrun")),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
