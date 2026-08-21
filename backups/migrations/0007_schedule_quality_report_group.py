from django.db import migrations


GROUP_NAME = "ScheduleQuality"


def create_schedule_quality_report_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name=GROUP_NAME)


class Migration(migrations.Migration):
    dependencies = [
        ("backups", "0006_databasemaintenancerun_databasemaintenanceitem"),
    ]

    operations = [
        migrations.RunPython(
            create_schedule_quality_report_group,
            migrations.RunPython.noop,
        ),
    ]
