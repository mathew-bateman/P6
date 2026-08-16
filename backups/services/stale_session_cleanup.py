from __future__ import annotations

import logging
from django.utils import timezone

from backups.models import BackupTarget, DatabaseMaintenanceItem, DatabaseMaintenanceRun
from backups.services.mssql import connect

logger = logging.getLogger(__name__)


def run_stale_session_cleanup(*, max_age_hours: int = 24, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED) -> str:
    run = DatabaseMaintenanceRun.objects.create(
        run_type=DatabaseMaintenanceRun.TYPE_STALE_SESSION,
        trigger=trigger,
        status=DatabaseMaintenanceRun.STATUS_RUNNING,
    )

    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])

    processed_count = 0
    log_lines = [f"Started Stale Session & Project Lock Purge (Age > {max_age_hours} hours)"]

    try:
        for target in targets:
            log_lines.append(f"Scanning target: {target.name} ({target.sql_database})...")
            conn = connect(target, database=target.sql_database, autocommit=False)
            try:
                cur = conn.cursor()
                
                # Fetch stale sessions (> max_age_hours inactive)
                cur.execute(
                    """
                    SELECT s.session_id, s.user_id, u.user_name, u.actual_name, s.host_name, s.last_active_time,
                           DATEDIFF(hour, s.last_active_time, GETDATE()) as inactive_hours
                    FROM USESSION s
                    JOIN USERS u ON s.user_id = u.user_id
                    WHERE DATEDIFF(hour, s.last_active_time, GETDATE()) > ?
                    """,
                    (max_age_hours,),
                )
                stale_sessions = cur.fetchall()

                # Also clear orphaned locks in USEROPEN and OPENING_USERS
                cur.execute("DELETE FROM USEROPEN;")
                cur.execute("DELETE FROM OPENING_USERS;")
                conn.commit()

                if not stale_sessions:
                    log_lines.append(f"Target {target.name}: Zero stale sessions (> {max_age_hours}h) found.")
                    continue

                for session_id, user_id, user_name, actual_name, host_name, last_active, inactive_hours in stale_sessions:
                    cur.execute("DELETE FROM USESSION WHERE session_id = ?", (session_id,))
                    conn.commit()

                    metric_before = f"Inactive {inactive_hours}h"
                    metric_after = "Session Purged"
                    note = f"Host: {host_name or 'Unknown'} (Last Active: {last_active})"
                    DatabaseMaintenanceItem.objects.create(
                        run=run,
                        target_name=target.name,
                        item_label=f"Session #{session_id} ({user_name})",
                        metric_before=metric_before,
                        metric_after=metric_after,
                        detail_note=note,
                    )
                    processed_count += 1
                    log_lines.append(f"  - Purged stale session #{session_id} for {user_name} ({note})")

            finally:
                conn.close()

        run.status = DatabaseMaintenanceRun.STATUS_SUCCESS
        run.items_processed_count = processed_count
        run.finished_at = timezone.now()
        summary = f"Purged {processed_count} stale sessions across {run.targets_scanned} database targets."
        run.metrics_summary = summary
        log_lines.append(summary)
        run.log_output = "\n".join(log_lines)
        run.save()
        return summary

    except Exception as error:
        logger.exception("Failed Stale Session Cleanup task")
        run.status = DatabaseMaintenanceRun.STATUS_FAILED
        run.finished_at = timezone.now()
        log_lines.append(f"ERROR: {error}")
        run.log_output = "\n".join(log_lines)
        run.save()
        raise
