from __future__ import annotations

import logging
from django.utils import timezone

from backups.models import BackupTarget, DatabaseMaintenanceItem, DatabaseMaintenanceRun
from backups.services.mssql import connect

logger = logging.getLogger(__name__)


def run_index_defragmentation(*, trigger: str = DatabaseMaintenanceRun.TRIGGER_SCHEDULED) -> str:
    run = DatabaseMaintenanceRun.objects.create(
        run_type=DatabaseMaintenanceRun.TYPE_INDEX_DEFRAG,
        trigger=trigger,
        status=DatabaseMaintenanceRun.STATUS_RUNNING,
    )

    targets = BackupTarget.objects.filter(enabled=True)
    run.targets_scanned = targets.count()
    run.save(update_fields=["targets_scanned"])

    processed_count = 0
    log_lines = [f"Started P6 Index Maintenance & Defragmentation ({run.get_trigger_display()})"]

    try:
        for target in targets:
            log_lines.append(f"Scanning target: {target.name} ({target.sql_database})...")
            conn = connect(target, database=target.sql_database, autocommit=True)
            try:
                cur = conn.cursor()
                
                # Fetch fragmented tables (avg_fragmentation > 15% and page_count > 50)
                cur.execute(
                    """
                    SELECT 
                        object_name(object_id) as table_name,
                        MAX(avg_fragmentation_in_percent) as orig_frag,
                        SUM(page_count) as total_pages
                    FROM sys.dm_db_index_physical_stats(DB_ID(), NULL, NULL, NULL, 'LIMITED')
                    WHERE object_name(object_id) IS NOT NULL AND page_count > 50
                    GROUP BY object_name(object_id)
                    HAVING MAX(avg_fragmentation_in_percent) > 15.0
                    ORDER BY MAX(avg_fragmentation_in_percent) DESC;
                    """
                )
                fragmented_tables = cur.fetchall()

                if not fragmented_tables:
                    log_lines.append(f"Target {target.name}: All table indexes are healthy (< 15% fragmentation).")
                    continue

                for table_name, orig_frag, total_pages in fragmented_tables:
                    if not table_name or table_name.startswith("sys") or table_name.startswith("spt_"):
                        continue

                    # Execute REORGANIZE and UPDATE STATISTICS
                    try:
                        cur.execute(f"ALTER INDEX ALL ON [{table_name}] REORGANIZE;")
                        cur.execute(f"UPDATE STATISTICS [{table_name}];")

                        # Measure updated fragmentation
                        cur.execute(
                            f"""
                            SELECT MAX(avg_fragmentation_in_percent)
                            FROM sys.dm_db_index_physical_stats(DB_ID(), OBJECT_ID(?), NULL, NULL, 'LIMITED')
                            """,
                            (table_name,),
                        )
                        new_row = cur.fetchone()
                        new_frag = new_row[0] if new_row and new_row[0] is not None else 0.0

                        orig_frag_str = f"{orig_frag:.1f}% frag"
                        new_frag_str = f"{new_frag:.1f}% frag"
                        DatabaseMaintenanceItem.objects.create(
                            run=run,
                            target_name=target.name,
                            item_label=f"Table [{table_name}]",
                            metric_before=orig_frag_str,
                            metric_after=new_frag_str,
                            detail_note=f"Reorganized & Updated Stats ({total_pages} pages)",
                        )
                        processed_count += 1
                        log_lines.append(
                            f"  - Defragmented [{table_name}]: {orig_frag_str} -> {new_frag_str} ({total_pages} pages)"
                        )
                    except Exception as t_err:
                        log_lines.append(f"  - Failed to defragment [{table_name}]: {t_err}")

            finally:
                conn.close()

        run.status = DatabaseMaintenanceRun.STATUS_SUCCESS
        run.items_processed_count = processed_count
        run.finished_at = timezone.now()
        summary = f"Defragmented {processed_count} tables across {run.targets_scanned} database targets."
        run.metrics_summary = summary
        log_lines.append(summary)
        run.log_output = "\n".join(log_lines)
        run.save()
        return summary

    except Exception as error:
        logger.exception("Failed Index Defragmentation task")
        run.status = DatabaseMaintenanceRun.STATUS_FAILED
        run.finished_at = timezone.now()
        log_lines.append(f"ERROR: {error}")
        run.log_output = "\n".join(log_lines)
        run.save()
        raise
