from __future__ import annotations

import logging
from django.utils import timezone

from backups.models import BackupTarget, DatabaseMaintenanceItem, DatabaseMaintenanceRun
from backups.services.mssql import connect

logger = logging.getLogger(__name__)


def run_log_retention_pruning(*, retention_days: int = 90, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED) -> str:
    run = DatabaseMaintenanceRun.objects.create(
        run_type=DatabaseMaintenanceRun.TYPE_LOG_PRUNING,
        trigger=trigger,
        status=DatabaseMaintenanceRun.STATUS_RUNNING,
    )

    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])

    processed_count = 0
    log_lines = [f"Started Audit Log & Job History Pruning (Retention > {retention_days} days)"]

    try:
        for target in targets:
            log_lines.append(f"Scanning target: {target.name} ({target.sql_database})...")
            conn = connect(target, database=target.sql_database, autocommit=False)
            try:
                cur = conn.cursor()
                
                # Check USESSAUD audit log count older than retention_days
                cur.execute("SELECT COUNT(*) FROM USESSAUD WHERE DATEDIFF(day, login_date, GETDATE()) > ?", (retention_days,))
                audit_count = cur.fetchone()[0] or 0

                if audit_count > 0:
                    cur.execute("DELETE FROM USESSAUD WHERE DATEDIFF(day, login_date, GETDATE()) > ?", (retention_days,))
                    conn.commit()
                    DatabaseMaintenanceItem.objects.create(
                        run=run,
                        target_name=target.name,
                        item_label="Table [USESSAUD] (Session Audit)",
                        metric_before=f"{audit_count} old rows",
                        metric_after="Purged",
                        detail_note=f"Deleted session logs older than {retention_days} days",
                    )
                    processed_count += audit_count
                    log_lines.append(f"  - Purged {audit_count} old USESSAUD audit records from {target.name}")

                # Check JOBLOG count older than retention_days
                cur.execute("SELECT COUNT(*) FROM JOBLOG WHERE DATEDIFF(day, create_date, GETDATE()) > ?", (retention_days,))
                joblog_count = cur.fetchone()[0] or 0

                if joblog_count > 0:
                    cur.execute("DELETE FROM JOBLOG WHERE DATEDIFF(day, create_date, GETDATE()) > ?", (retention_days,))
                    conn.commit()
                    DatabaseMaintenanceItem.objects.create(
                        run=run,
                        target_name=target.name,
                        item_label="Table [JOBLOG] (Job Executions)",
                        metric_before=f"{joblog_count} old rows",
                        metric_after="Purged",
                        detail_note=f"Deleted job log entries older than {retention_days} days",
                    )
                    processed_count += joblog_count
                    log_lines.append(f"  - Purged {joblog_count} old JOBLOG entries from {target.name}")

                if audit_count == 0 and joblog_count == 0:
                    log_lines.append(f"Target {target.name}: Zero audit/job log entries older than {retention_days} days.")

            finally:
                conn.close()

        run.status = DatabaseMaintenanceRun.STATUS_SUCCESS
        run.items_processed_count = processed_count
        run.finished_at = timezone.now()
        summary = f"Purged {processed_count} historical audit/job log records older than {retention_days} days across {run.targets_scanned} targets."
        run.metrics_summary = summary
        log_lines.append(summary)
        run.log_output = "\n".join(log_lines)
        run.save()
        return summary

    except Exception as error:
        logger.exception("Failed Audit Log Pruning task")
        run.status = DatabaseMaintenanceRun.STATUS_FAILED
        run.finished_at = timezone.now()
        log_lines.append(f"ERROR: {error}")
        run.log_output = "\n".join(log_lines)
        run.save()
        raise
