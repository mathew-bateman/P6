from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from backups.services.scheduling import sync_schedule_quality_refresh_schedule


def _configured_project_id() -> int | None:
    raw_value = str(settings.P6_SCHEDULE_QUALITY_REFRESH_PROJ_ID).strip()
    if not raw_value:
        return None
    return int(raw_value)


class Command(BaseCommand):
    help = "Create or update the Celery Beat schedule for Power BI schedule-quality refresh."

    def handle(self, *args, **options):
        task = sync_schedule_quality_refresh_schedule(
            enabled=settings.P6_SCHEDULE_QUALITY_REFRESH_ENABLED,
            crontab_string=settings.P6_SCHEDULE_QUALITY_REFRESH_SCHEDULE,
            proj_id=_configured_project_id(),
        )
        state = "enabled" if task.enabled else "disabled"
        self.stdout.write(
            self.style.SUCCESS(
                f"Schedule quality refresh task {state}: {task.name} ({task.crontab})"
            )
        )
