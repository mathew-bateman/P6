from __future__ import annotations

import logging

from celery import shared_task

from backups.models import BackupRun, DatabaseMaintenanceRun, UserdataPruneRun
from backups.services.database_health import (
    run_p6_background_health_check,
    run_p6_native_background_jobs,
    run_physical_integrity_check,
    run_statistics_refresh,
)
from backups.services.index_defrag import run_index_defragmentation
from backups.services.orchestration import run_backup_for_target
from backups.services.log_pruning import run_log_retention_pruning
from backups.services.restore import run_restore_for_target
from backups.services.schedule_quality import (
    publish_schedule_quality_config,
    refresh_schedule_quality,
)
from backups.services.stale_session_cleanup import run_stale_session_cleanup
from backups.services.userdata_prune import prune_all_userdata_bloat


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


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def execute_index_defragmentation(
    self,
    *,
    trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED,
) -> str:
    try:
        return run_index_defragmentation(trigger=trigger)
    except Exception as error:
        logger.exception("Index defragmentation task failed")
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def execute_stale_session_cleanup(
    self,
    *,
    max_age_hours: int = 24,
    trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED,
) -> str:
    try:
        return run_stale_session_cleanup(
            max_age_hours=max_age_hours,
            trigger=trigger,
        )
    except Exception as error:
        logger.exception("Stale session cleanup task failed")
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def execute_log_retention_pruning(
    self,
    *,
    retention_days: int = 90,
    trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED,
) -> str:
    try:
        return run_log_retention_pruning(
            retention_days=retention_days,
            trigger=trigger,
        )
    except Exception as error:
        logger.exception("Log retention pruning task failed")
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=1, default_retry_delay=120)
def execute_userdata_bloat_prune(
    self,
    *,
    threshold_mb: int = 5,
    trigger: str = UserdataPruneRun.TRIGGER_SCHEDULED,
) -> str:
    try:
        return prune_all_userdata_bloat(
            threshold_mb=threshold_mb,
            trigger=trigger,
        )
    except Exception as error:
        logger.exception("USERDATA bloat cleanup task failed")
        raise self.retry(exc=error)


@shared_task(bind=True, max_retries=1, default_retry_delay=300)
def execute_statistics_refresh(
    self, *, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED
) -> str:
    return run_statistics_refresh(trigger=trigger)


@shared_task(bind=True, max_retries=0)
def execute_physical_integrity_check(
    self, *, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED
) -> str:
    return run_physical_integrity_check(trigger=trigger)


@shared_task(bind=True, max_retries=0)
def execute_p6_background_health_check(
    self, *, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED
) -> str:
    return run_p6_background_health_check(trigger=trigger)


@shared_task(bind=True, max_retries=1, default_retry_delay=60)
def execute_p6_native_background_jobs(self, *, target_names: list[str]) -> str:
    try:
        return run_p6_native_background_jobs(target_names=target_names)
    except Exception as error:
        logger.exception("P6 native background maintenance task failed")
        raise self.retry(exc=error)
