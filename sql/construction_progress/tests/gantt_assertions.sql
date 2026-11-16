\set ON_ERROR_STOP on
DO $$ BEGIN
 IF (SELECT count(*) FROM p6_reporting.construction_gantt_detail)<>10 THEN RAISE EXCEPTION 'placement/version grain'; END IF;
 IF (SELECT count(*) FROM p6_reporting.construction_gantt_detail WHERE task_id=100 AND record_type='activity')<>4 THEN RAISE EXCEPTION 'bar representation'; END IF;
 IF (SELECT count(*) FROM p6_reporting.construction_gantt_detail WHERE task_id=100 AND record_type='milestone')<>4 THEN RAISE EXCEPTION 'marker representation'; END IF;
 IF EXISTS (SELECT FROM p6_reporting.construction_gantt_detail WHERE record_type='milestone' AND gantt_end IS NOT NULL) THEN RAISE EXCEPTION 'milestone end'; END IF;
 IF EXISTS (SELECT FROM p6_reporting.construction_gantt_detail WHERE task_id=100 AND record_type='milestone' AND programme_version='Current Period' AND gantt_start IS DISTINCT FROM timestamp '2026-02-05') THEN RAISE EXCEPTION 'source current date (not SQL CURRENT_DATE)'; END IF;
 IF NOT EXISTS (SELECT FROM p6_reporting.construction_gantt_detail WHERE task_id=101 AND programme_version='Baseline' AND gantt_start IS NULL AND NOT is_renderable AND discipline_label='Unassigned Discipline' AND discipline_status='uncoded' AND wbs_order_status='missing_or_cyclic_wbs') THEN RAISE EXCEPTION 'missing dates/discipline/cyclic WBS'; END IF;
 IF (SELECT count(DISTINCT discipline_value_id) FROM p6_reporting.construction_gantt_detail WHERE task_id=100)<>2 THEN RAISE EXCEPTION 'same short name merged discipline IDs'; END IF;
 IF EXISTS (SELECT gantt_row_key FROM p6_reporting.construction_gantt_detail GROUP BY 1 HAVING count(*)>1) THEN RAISE EXCEPTION 'duplicate key'; END IF;
 IF EXISTS (SELECT task_id,record_type,discipline_assignment_key FROM p6_reporting.construction_gantt_detail GROUP BY 1,2,3 HAVING max(programme_sort_order)-min(programme_sort_order)<>1) THEN RAISE EXCEPTION 'version adjacency'; END IF;
 IF (SELECT count(*) FROM p6_reporting.construction_gantt_detail g WHERE record_type='activity' OR EXISTS
 (SELECT FROM reporting.construction_milestone_type_bridge b WHERE b.activity_row_key=g.activity_row_key AND milestone_value_id IN(1,2)))<>8 THEN RAISE EXCEPTION 'OR selection/duplicate assignment'; END IF;
 IF (SELECT count(*) FROM p6_reporting.construction_gantt_detail g WHERE record_type='activity' OR EXISTS
 (SELECT FROM reporting.construction_milestone_type_bridge b WHERE b.activity_row_key=g.activity_row_key AND milestone_value_id IN(999)))<>4 THEN RAISE EXCEPTION 'classification filter must retain bars'; END IF;
END $$;
SET ROLE p6_reporting_reader;
SELECT count(*) AS reader_rows FROM p6_reporting.construction_gantt_detail;
RESET ROLE;
