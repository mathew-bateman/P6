from django.core.management.base import BaseCommand

from backups.models import BackupTarget
from reporting_mirror.models import ReportingConfig, ReportingSource
from reporting_mirror.sync import run_sync


class Command(BaseCommand):
    help = "Publish a verified PostgreSQL reporting snapshot from isolated P6 restores."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--bootstrap", action="store_true")

    def handle(self, *args, **options):
        if options["bootstrap"]:
            for code, database in (("p6_22", "P62212_1"), ("p6_23", "Axial")):
                target = BackupTarget.objects.get(sql_database=database, enabled=True)
                ReportingSource.objects.update_or_create(code=code, defaults={"target": target, "enabled": True})
            ReportingConfig.current()
        self.stdout.write(run_sync(force=options["force"], trigger="command"))
