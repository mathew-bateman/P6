from __future__ import annotations

import logging
from django.utils import timezone

from backups.models import BackupTarget, UserdataPruneItem, UserdataPruneRun
from backups.services.mssql import connect

logger = logging.getLogger(__name__)


def prune_all_userdata_bloat(*, threshold_mb: int = 5, trigger: str = UserdataPruneRun.TRIGGER_SCHEDULED) -> str:
    threshold_bytes = int(threshold_mb) * 1024 * 1024
    run = UserdataPruneRun.objects.create(
        trigger=trigger,
        status=UserdataPruneRun.STATUS_RUNNING,
        threshold_mb=threshold_mb,
    )

    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])

    total_pruned_users = 0
    total_freed_bytes = 0
    log_lines = [f"Started USERDATA bloat cleanup (Threshold: {threshold_mb} MB)"]

    try:
        for target in targets:
            log_lines.append(f"Scanning target: {target.name} ({target.sql_database})...")
            conn = connect(target, database=target.sql_database, autocommit=False)
            try:
                cur = conn.cursor()
                
                # Fetch baseline clean user_data blob (user_id=56 or smallest blob under 300KB)
                cur.execute(
                    "SELECT TOP 1 user_data FROM USERDATA WHERE topic_name = 'pm_settings' AND DATALENGTH(user_data) > 0 AND DATALENGTH(user_data) < 307200 ORDER BY DATALENGTH(user_data) ASC"
                )
                baseline_row = cur.fetchone()
                if not baseline_row or not baseline_row[0]:
                    log_lines.append(f"Skipping target {target.name}: No clean baseline pm_settings BLOB found.")
                    continue

                baseline_blob = baseline_row[0]
                baseline_bytes = len(baseline_blob)

                # Query bloated users
                cur.execute(
                    """
                    SELECT u.user_id, u.user_name, u.actual_name, DATALENGTH(ud.user_data) as blob_bytes
                    FROM USERS u
                    JOIN USERDATA ud ON u.user_id = ud.user_id
                    WHERE ud.topic_name = 'pm_settings' AND DATALENGTH(ud.user_data) > ?
                    """,
                    (threshold_bytes,),
                )
                bloated_rows = cur.fetchall()

                if not bloated_rows:
                    log_lines.append(f"Target {target.name}: All users within healthy size threshold.")
                    continue

                for user_id, user_name, actual_name, original_bytes in bloated_rows:
                    cur.execute(
                        "UPDATE USERDATA SET user_data = ? WHERE user_id = ? AND topic_name = 'pm_settings'",
                        (baseline_blob, user_id),
                    )
                    conn.commit()

                    bytes_freed = max(0, original_bytes - baseline_bytes)
                    UserdataPruneItem.objects.create(
                        run=run,
                        target_name=target.name,
                        user_name=user_name or f"User #{user_id}",
                        actual_name=actual_name or "",
                        original_bytes=original_bytes,
                        pruned_bytes=baseline_bytes,
                        bytes_freed=bytes_freed,
                    )
                    total_pruned_users += 1
                    total_freed_bytes += bytes_freed
                    freed_mb = round(bytes_freed / 1024.0 / 1024.0, 2)
                    log_lines.append(
                        f"  - Pruned {user_name} ({actual_name or ''}): {round(original_bytes/1024.0/1024.0, 2)} MB -> {round(baseline_bytes/1024.0/1024.0, 2)} MB (Freed {freed_mb} MB)"
                    )
            finally:
                conn.close()

        run.status = UserdataPruneRun.STATUS_SUCCESS
        run.users_pruned_count = total_pruned_users
        run.total_bytes_freed = total_freed_bytes
        run.finished_at = timezone.now()
        total_freed_mb = round(total_freed_bytes / 1024.0 / 1024.0, 2)
        summary = f"Finished successfully. Cleaned {total_pruned_users} users across {run.targets_scanned} targets. Total space freed: {total_freed_mb} MB."
        log_lines.append(summary)
        run.log_output = "\n".join(log_lines)
        run.save()
        return summary

    except Exception as error:
        logger.exception("Failed USERDATA bloat cleanup")
        run.status = UserdataPruneRun.STATUS_FAILED
        run.finished_at = timezone.now()
        log_lines.append(f"ERROR: {error}")
        run.log_output = "\n".join(log_lines)
        run.save()
        raise
