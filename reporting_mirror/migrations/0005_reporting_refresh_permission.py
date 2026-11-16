from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("reporting_mirror", "0004_sqlparallelrun_reportingconfig_sql_parallel_enabled")]

    operations = [
        migrations.AlterModelOptions(
            name="reportingconfig",
            options={"permissions": [("refresh_reporting", "Can start a reporting refresh")]},
        ),
    ]
