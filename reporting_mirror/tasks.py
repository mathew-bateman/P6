from celery import shared_task


@shared_task(bind=True, soft_time_limit=7000, time_limit=7200)
def refresh_reporting(self, *, trigger="scheduled", requested_by="", force=False, **options):
    from reporting_mirror.sync import run_sync
    from reporting_mirror.models import ReportingRun
    before = ReportingRun.objects.filter(status="success", engine="postgres").first()
    result = run_sync(trigger=trigger, requested_by=requested_by, force=force, **options)
    # A failed or skipped PG calculation cannot trigger a SQL rules publication.
    latest = ReportingRun.objects.filter(status="success", engine="postgres").first()
    if latest is not None and (before is None or latest.pk != before.pk):
        from reporting_mirror.sql_parallel import queue_sql_parallel
        queue_sql_parallel(trigger=trigger, publication=latest)
    return result


@shared_task(bind=True, max_retries=2, default_retry_delay=120, soft_time_limit=5700, time_limit=6000)
def refresh_sql_parallel(self, *, run_id):
    from reporting_mirror.sql_parallel import run_sql_parallel
    try:
        return run_sql_parallel(run_id)
    except Exception as error:
        raise self.retry(exc=error)


def sync_schedule(config):
    from django_celery_beat.models import IntervalSchedule, PeriodicTask
    interval, _ = IntervalSchedule.objects.get_or_create(every=config.interval_minutes, period=IntervalSchedule.MINUTES)
    task = PeriodicTask.objects.update_or_create(
        name="P6 Combined PostgreSQL Reporting Refresh",
        defaults={"task": "reporting_mirror.tasks.refresh_reporting", "interval": interval, "crontab": None, "enabled": config.enabled, "queue": "p6_reporting", "description": "Restore consistent P6 22/23 snapshots on reporting staging and publish validated PostgreSQL reporting views."},
    )[0]
    if config.enabled and task.last_run_at is None:
        from reporting_mirror.models import ReportingRun
        latest = ReportingRun.objects.filter(status="success").first()
        if latest:
            task.last_run_at = latest.started_at
            task.save(update_fields=["last_run_at"])
    return task


def queue_after_settings_publish():
    from reporting_mirror.models import ReportingConfig
    if ReportingConfig.objects.filter(pk=1, enabled=True).exists():
        refresh_reporting.apply_async(kwargs={"trigger": "settings_publish"}, queue="p6_reporting")


@shared_task(bind=True, soft_time_limit=7000, time_limit=7200)
def validate_native_engine(self, *, validation_id):
    from reporting_mirror.sql_retirement import retired, MESSAGE
    if retired():
        from django.utils import timezone
        from reporting_mirror.models import NativeEngineValidation
        NativeEngineValidation.objects.filter(pk=validation_id, status__in=["queued", "running"]).update(status="failed", progress="SQL engine comparison is unavailable after retirement", error_message=MESSAGE, finished_at=timezone.now())
        return MESSAGE
    from reporting_mirror.native_validation import validate_native_engine as run_validation
    return run_validation(validation_id)


@shared_task(bind=True, soft_time_limit=7000, time_limit=7200)
def switch_reporting_engine(self, *, engine, requested_by=""):
    from reporting_mirror.sync import run_sync
    from reporting_mirror.models import ReportingConfig, ReportingRun
    before = ReportingRun.objects.filter(status="success", engine="postgres").first()
    try:
        result = run_sync(trigger="engine_switch", requested_by=requested_by, force=True, engine=engine, switch_engine=True)
        latest = ReportingRun.objects.filter(status="success", engine="postgres").first()
        if engine == "postgres" and latest is not None and (before is None or latest.pk != before.pk):
            from reporting_mirror.sql_parallel import queue_sql_parallel
            queue_sql_parallel(trigger="engine_switch", publication=latest)
        return result
    finally:
        ReportingConfig.objects.filter(pk=1).update(pending_engine="")


@shared_task(soft_time_limit=300, time_limit=360)
def backup_reporting_settings():
    from reporting_mirror.settings_backup import backup_settings
    return backup_settings()
