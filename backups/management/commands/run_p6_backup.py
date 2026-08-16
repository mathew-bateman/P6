from __future__ import annotations

from django.core.management.base import BaseCommand

from backups.models import BackupRun
from backups.services.orchestration import run_backup_for_target


class Command(BaseCommand):
    help = "Run a P6 SQL Server backup for one target immediately."

    def add_arguments(self, parser) -> None:
        parser.add_argument("target_slug")
        parser.add_argument("--force", action="store_true", help="Run even if the target is disabled.")

    def handle(self, *args, **options):
        result = run_backup_for_target(
            target_slug=options["target_slug"],
            force=bool(options["force"]),
            trigger=BackupRun.TRIGGER_COMMAND,
        )
        self.stdout.write(self.style.SUCCESS(result))
