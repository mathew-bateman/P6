from __future__ import annotations

from django.core.management.base import BaseCommand

from backups.models import BackupTarget
from backups.services.scheduling import sync_all_target_schedules


class Command(BaseCommand):
    help = "Create or update Celery Beat schedules for all P6 backup targets."

    def handle(self, *args, **options):
        count = sync_all_target_schedules(BackupTarget.objects.all())
        self.stdout.write(self.style.SUCCESS(f"Synchronized {count} backup schedule(s)."))
