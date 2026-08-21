from __future__ import annotations

import json

from django_celery_beat.models import CrontabSchedule, PeriodicTask

SCHEDULE_QUALITY_TASK_NAME = "P6 Schedule Quality Refresh"


def parse_crontab(crontab_string: str) -> tuple[str, str, str, str, str]:
    parts = (crontab_string or "").strip().split()
    if len(parts) != 5:
        return "0", "2", "*", "*", "0"
    minute, hour, day_of_month, month_of_year, day_of_week = parts
    return minute, hour, day_of_month, month_of_year, day_of_week


def sync_target_schedule(target) -> PeriodicTask:
    minute, hour, day_of_month, month_of_year, day_of_week = parse_crontab(target.backup_schedule)
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )
    task, _ = PeriodicTask.objects.get_or_create(
        name=f"P6 MSSQL Backup: {target.slug}",
        defaults={
            "task": "backups.tasks.execute_target_backup",
            "crontab": schedule,
            "enabled": target.enabled,
            "kwargs": json.dumps({"target_slug": target.slug, "force": False, "trigger": "scheduled"}),
            "description": f"Runs SQL Server backup for {target.name}.",
        },
    )
    task.task = "backups.tasks.execute_target_backup"
    task.crontab = schedule
    task.enabled = target.enabled
    task.kwargs = json.dumps({"target_slug": target.slug, "force": False, "trigger": "scheduled"})
    task.description = f"Runs SQL Server backup for {target.name}."
    task.save(update_fields=["task", "crontab", "enabled", "kwargs", "description"])
    return task


def sync_all_target_schedules(targets) -> int:
    count = 0
    for target in targets:
        sync_target_schedule(target)
        count += 1
    return count


def sync_schedule_quality_refresh_schedule(
    *,
    enabled: bool,
    crontab_string: str,
    proj_id: int | None = None,
) -> PeriodicTask:
    minute, hour, day_of_month, month_of_year, day_of_week = parse_crontab(crontab_string)
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )
    kwargs = {} if proj_id is None else {"proj_id": int(proj_id)}
    task, _ = PeriodicTask.objects.get_or_create(
        name=SCHEDULE_QUALITY_TASK_NAME,
        defaults={
            "task": "backups.tasks.execute_schedule_quality_refresh",
            "crontab": schedule,
            "enabled": enabled,
            "kwargs": json.dumps(kwargs),
            "description": "Refreshes materialized Power BI schedule-quality tables.",
        },
    )
    task.task = "backups.tasks.execute_schedule_quality_refresh"
    task.crontab = schedule
    task.enabled = enabled
    task.kwargs = json.dumps(kwargs)
    task.description = "Refreshes materialized Power BI schedule-quality tables."
    task.save(update_fields=["task", "crontab", "enabled", "kwargs", "description"])
    return task


def get_schedule_quality_refresh_schedule(
    *,
    default_crontab: str,
    default_enabled: bool,
) -> dict[str, object]:
    task = (
        PeriodicTask.objects.select_related("crontab")
        .filter(name=SCHEDULE_QUALITY_TASK_NAME)
        .first()
    )
    if task and task.crontab:
        crontab_string = (
            f"{task.crontab.minute} {task.crontab.hour} "
            f"{task.crontab.day_of_month} {task.crontab.month_of_year} {task.crontab.day_of_week}"
        )
        return {
            "enabled": task.enabled,
            "crontab": crontab_string,
            "task": task,
        }
    return {
        "enabled": default_enabled,
        "crontab": default_crontab,
        "task": None,
    }


USERDATA_PRUNE_TASK_NAME = "P6 USERDATA Bloat Cleanup"


def sync_userdata_prune_schedule(
    *,
    enabled: bool = True,
    crontab_string: str = "0 2 * * *",
    threshold_mb: int = 5,
) -> PeriodicTask:
    minute, hour, day_of_month, month_of_year, day_of_week = parse_crontab(crontab_string)
    schedule, _ = CrontabSchedule.objects.get_or_create(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )
    kwargs = {"threshold_mb": threshold_mb, "trigger": "scheduled"}
    task, _ = PeriodicTask.objects.get_or_create(
        name=USERDATA_PRUNE_TASK_NAME,
        defaults={
            "task": "backups.tasks.execute_userdata_bloat_prune",
            "crontab": schedule,
            "enabled": enabled,
            "kwargs": json.dumps(kwargs),
            "description": "Prunes bloated Primavera P6 USERDATA pm_settings BLOBs across all database targets.",
        },
    )
    task.task = "backups.tasks.execute_userdata_bloat_prune"
    task.crontab = schedule
    task.enabled = enabled
    task.kwargs = json.dumps(kwargs)
    task.description = "Prunes bloated Primavera P6 USERDATA pm_settings BLOBs across all database targets."
    task.save(update_fields=["task", "crontab", "enabled", "kwargs", "description"])
    return task


INDEX_DEFRAG_TASK_NAME = "P6 Index Maintenance & Defrag"
STALE_SESSION_TASK_NAME = "P6 Stale Session & Lock Purge"
LOG_PRUNING_TASK_NAME = "P6 Log & Job History Pruning"
STATISTICS_TASK_NAME = "P6 SQL Statistics Refresh"
PHYSICAL_INTEGRITY_TASK_NAME = "P6 Database Physical Integrity Check"
BACKGROUND_HEALTH_TASK_NAME = "P6 Native Background Health Check"
BACKGROUND_RUNNER_TASK_NAME = "P6 Native Background Runner"


def sync_all_maintenance_schedules() -> list[PeriodicTask]:
    tasks = []

    def sync(name: str, task_name: str, schedule: CrontabSchedule, *, kwargs: dict | None = None, description: str) -> PeriodicTask:
        task, _ = PeriodicTask.objects.get_or_create(
            name=name,
            defaults={"task": task_name, "crontab": schedule, "enabled": True, "kwargs": json.dumps(kwargs or {}), "description": description},
        )
        task.task = task_name
        task.crontab = schedule
        task.enabled = True
        task.kwargs = json.dumps(kwargs or {})
        task.description = description
        task.save(update_fields=["task", "crontab", "enabled", "kwargs", "description"])
        return task

    sch_defrag, _ = CrontabSchedule.objects.get_or_create(minute="0", hour="3", day_of_month="*", month_of_year="*", day_of_week="0")
    t1 = sync(
        INDEX_DEFRAG_TASK_NAME, "backups.tasks.execute_index_defragmentation", sch_defrag,
        description="Defragments table indexes (>15% frag) and updates SQL statistics across all targets.",
    )
    tasks.append(t1)

    sch_sess, _ = CrontabSchedule.objects.get_or_create(minute="30", hour="1", day_of_month="*", month_of_year="*", day_of_week="*")
    t2 = sync(
        STALE_SESSION_TASK_NAME, "backups.tasks.execute_stale_session_cleanup", sch_sess,
        kwargs={"max_age_hours": 24}, description="Purges orphaned USESSION sessions (>24h inactive) and clears project locks.",
    )
    tasks.append(t2)

    sch_log, _ = CrontabSchedule.objects.get_or_create(minute="0", hour="4", day_of_month="1", month_of_year="*", day_of_week="*")
    t3 = sync(
        LOG_PRUNING_TASK_NAME, "backups.tasks.execute_log_retention_pruning", sch_log,
        kwargs={"retention_days": 90}, description="Prunes historical USESSAUD audit logs and JOBLOG entries older than 90 days.",
    )
    tasks.append(t3)

    sch_stats, _ = CrontabSchedule.objects.get_or_create(minute="30", hour="4", day_of_month="*", month_of_year="*", day_of_week="0")
    tasks.append(sync(STATISTICS_TASK_NAME, "backups.tasks.execute_statistics_refresh", sch_stats, description="Runs SQL Server sp_updatestats across all P6 database targets."))
    sch_checkdb, _ = CrontabSchedule.objects.get_or_create(minute="0", hour="5", day_of_month="*", month_of_year="*", day_of_week="0")
    tasks.append(sync(PHYSICAL_INTEGRITY_TASK_NAME, "backups.tasks.execute_physical_integrity_check", sch_checkdb, description="Runs DBCC CHECKDB WITH PHYSICAL_ONLY across all P6 database targets."))
    sch_background, _ = CrontabSchedule.objects.get_or_create(minute="15", hour="6", day_of_month="*", month_of_year="*", day_of_week="*")
    tasks.append(sync(BACKGROUND_HEALTH_TASK_NAME, "backups.tasks.execute_p6_background_health_check", sch_background, description="Checks the P6 DAMON heartbeat and records stale native background maintenance."))
    sch_background_runner, _ = CrontabSchedule.objects.get_or_create(minute="*/5", hour="*", day_of_month="*", month_of_year="*", day_of_week="*")
    tasks.append(sync(BACKGROUND_RUNNER_TASK_NAME, "backups.tasks.execute_p6_native_background_jobs", sch_background_runner, kwargs={"target_names": ["Axial Training", "AxialP6", "P62212"]}, description="Runs P6 SYMON and DAMON every five minutes for SQL Server Express targets without SQL Agent."))

    return tasks
