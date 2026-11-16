"""Emit the same read-only capacity report as the P6 review page."""
import json
import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

from backups.services.capacity_review import build_capacity_report, collect_capacity_report


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Read SQL Server and PostgreSQL reporting capacity and emit the shared JSON monitoring report."
    requires_system_checks = []

    def handle(self, *args, **options):
        try:
            report = collect_capacity_report()
        except Exception:
            logger.warning("The database capacity monitoring probe could not complete.")
            report = {"schema_version": 2, "checked_at": timezone.now().isoformat(), "level": "red",
                      "reason": "Database capacity collection failed. Check the service and target connections.", "databases": []}
        self.stdout.write(json.dumps(report, ensure_ascii=True, allow_nan=False))
