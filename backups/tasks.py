from __future__ import annotations

import logging

from celery import shared_task

from backups.models import BackupRun
from backups.services.orchestration import run_backup_for_target
from backups.services.restore import run_restore_for_target
from backups.services.schedule_quality import (
    publish_schedule_quality_config,
    refresh_schedule_quality,
)


logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def execute_target_backup(
    self,
    *,
    target_slug: str,
    force: bool = False,
    trigger: str = BackupRun.TRIGGER_SCHEDULED,
) -> str:
    try:
        return run_backup_for_target(target_slug=target_slug, force=force, trigger=trigger)
    except Exception as error:
        logger.exception("Backup task failed for target %s", target_slug)
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def execute_target_restore(self, **kwargs) -> str:
    try:
        return run_restore_for_target(**kwargs)
    except Exception as error:
        logger.exception("Restore task failed")
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def execute_schedule_quality_refresh(
    self,
    *,
    proj_id: int | None = None,
    trigger: str = "scheduled",
) -> str:
    try:
        return refresh_schedule_quality(proj_id=proj_id, trigger=trigger)
    except Exception as error:
        logger.exception("Schedule quality refresh task failed")
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def publish_schedule_quality_config_task(
    self,
    *,
    config_version_id: int,
    expected_settings_hash: str,
    published_by: str,
) -> None:
    """Publish a saved draft and run its all-project rebuild off the web request."""
    try:
        publish_schedule_quality_config(
            config_version_id=config_version_id,
            expected_settings_hash=expected_settings_hash,
            published_by=published_by,
            trigger_type="publish",
        )
    except Exception as error:
        logger.exception(
            "Schedule quality publish task failed for draft %s", config_version_id
        )
        raise self.retry(exc=error)
