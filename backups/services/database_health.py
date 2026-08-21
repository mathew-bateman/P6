from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from backups.models import BackupTarget, DatabaseMaintenanceItem, DatabaseMaintenanceRun
from backups.services.mssql import connect


def _finish(run, *, status: str, processed: int, lines: list[str]) -> str:
    run.status = status
    run.items_processed_count = processed
    run.finished_at = timezone.now()
    summary = lines[-1]
    run.metrics_summary = summary
    run.log_output = "\n".join(lines)
    run.save()
    return summary


def run_statistics_refresh(*, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED) -> str:
    run = DatabaseMaintenanceRun.objects.create(
        run_type=DatabaseMaintenanceRun.TYPE_STATISTICS, trigger=trigger, status=DatabaseMaintenanceRun.STATUS_RUNNING
    )
    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])
    processed = 0
    lines = ["Started SQL Server statistics refresh (sp_updatestats)."]
    try:
        for target in targets:
            conn = connect(target, database=target.sql_database, autocommit=True)
            try:
                cursor = conn.cursor()
                cursor.execute("EXEC sp_updatestats;")
                while cursor.nextset():
                    pass
                DatabaseMaintenanceItem.objects.create(
                    run=run, target_name=target.name, item_label="All P6 table statistics",
                    metric_after="Refreshed", detail_note="Executed sp_updatestats",
                )
                processed += 1
                lines.append(f"  - Refreshed statistics for {target.name}.")
            finally:
                conn.close()
        lines.append(f"Refreshed statistics across {processed} database targets.")
        return _finish(run, status=DatabaseMaintenanceRun.STATUS_SUCCESS, processed=processed, lines=lines)
    except Exception as error:
        lines.append(f"ERROR: {error}")
        return _finish(run, status=DatabaseMaintenanceRun.STATUS_FAILED, processed=processed, lines=lines)


def run_physical_integrity_check(*, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED) -> str:
    run = DatabaseMaintenanceRun.objects.create(
        run_type=DatabaseMaintenanceRun.TYPE_HEALTH_CHECK, trigger=trigger, status=DatabaseMaintenanceRun.STATUS_RUNNING
    )
    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])
    processed = 0
    lines = ["Started SQL Server DBCC CHECKDB PHYSICAL_ONLY integrity checks."]
    try:
        for target in targets:
            conn = connect(target, database=target.sql_database, autocommit=True)
            try:
                cursor = conn.cursor()
                cursor.execute("DBCC CHECKDB WITH PHYSICAL_ONLY, NO_INFOMSGS;")
                while cursor.nextset():
                    pass
                DatabaseMaintenanceItem.objects.create(
                    run=run, target_name=target.name, item_label="DBCC CHECKDB PHYSICAL_ONLY",
                    metric_after="Passed", detail_note="No physical consistency errors returned",
                )
                processed += 1
                lines.append(f"  - Physical integrity check passed for {target.name}.")
            finally:
                conn.close()
        lines.append(f"Physical integrity checks passed across {processed} database targets.")
        return _finish(run, status=DatabaseMaintenanceRun.STATUS_SUCCESS, processed=processed, lines=lines)
    except Exception as error:
        lines.append(f"ERROR: {error}")
        return _finish(run, status=DatabaseMaintenanceRun.STATUS_FAILED, processed=processed, lines=lines)


def run_p6_background_health_check(*, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED) -> str:
    run = DatabaseMaintenanceRun.objects.create(
        run_type=DatabaseMaintenanceRun.TYPE_P6_BACKGROUND_HEALTH, trigger=trigger, status=DatabaseMaintenanceRun.STATUS_RUNNING
    )
    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])
    stale_targets: list[str] = []
    lines = ["Started P6 native background-process heartbeat check."]
    cutoff = timezone.now() - timedelta(minutes=20)
    for target in targets:
        conn = connect(target, database=target.sql_database, autocommit=True)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT setting_value FROM SETTINGS WHERE namespace = 'database.background.Damon' AND setting_name = 'HeartBeatTime'"
            )
            row = cursor.fetchone()
            heartbeat = str(row[0]).strip() if row and row[0] else ""
            # P6 stores this as an ISO-like text setting. Lexical comparison is
            # chronological for its `YYYY-MM-DD HH:MM:SS` representation.
            healthy = bool(heartbeat) and heartbeat >= cutoff.strftime("%Y-%m-%d %H:%M:%S")
            DatabaseMaintenanceItem.objects.create(
                run=run, target_name=target.name, item_label="DAMON heartbeat",
                metric_before=heartbeat or "Missing", metric_after="Healthy" if healthy else "Stale",
                detail_note="Expected within the last 20 minutes",
            )
            if healthy:
                lines.append(f"  - {target.name}: DAMON heartbeat healthy ({heartbeat}).")
            else:
                stale_targets.append(target.name)
                lines.append(f"  - {target.name}: DAMON heartbeat stale or missing ({heartbeat or 'missing'}).")
        finally:
            conn.close()
    if stale_targets:
        lines.append(f"P6 native background health requires attention on: {', '.join(stale_targets)}.")
        return _finish(run, status=DatabaseMaintenanceRun.STATUS_FAILED, processed=run.targets_scanned, lines=lines)
    lines.append(f"P6 native background health is current across {run.targets_scanned} database targets.")
    return _finish(run, status=DatabaseMaintenanceRun.STATUS_SUCCESS, processed=run.targets_scanned, lines=lines)


def run_p6_native_background_jobs(*, target_names: list[str]) -> str:
    """Run P6's native SYMON and DAMON procedures where SQL Agent is unavailable.

    SQL Server Express containers cannot host SQL Server Agent.  These calls are
    the same P6 procedures Agent would invoke, scheduled here every five minutes.
    """
    targets = BackupTarget.objects.filter(enabled=True, name__in=target_names).order_by("name")
    executed: list[str] = []
    failures: list[str] = []
    for target in targets:
        conn = connect(target, database=target.sql_database, autocommit=True)
        try:
            cursor = conn.cursor()
            for procedure in ("SYSTEM_MONITOR", "DATA_MONITOR"):
                cursor.execute(f"EXEC [{procedure}];")
                while cursor.nextset():
                    pass
            executed.append(target.name)
        except Exception as error:
            failures.append(f"{target.name}: {error}")
        finally:
            conn.close()
    if failures:
        raise RuntimeError("P6 native background maintenance failed: " + "; ".join(failures))
    return "Ran P6 SYMON and DAMON for: " + ", ".join(executed)
