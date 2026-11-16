\set ON_ERROR_STOP on
\pset pager off
\timing on
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL statement_timeout='600s';
-- A single snapshot prevents publication changes during cross-view reconciliation.
-- No permanent writes. Temp materialisations avoid repeatedly evaluating shared helpers.
CREATE TEMP TABLE gantt_check ON COMMIT DROP AS SELECT * FROM p6_reporting.construction_gantt_detail;
CREATE TEMP TABLE activity_check ON COMMIT DROP AS SELECT * FROM reporting.construction_activity_detail;
CREATE TEMP TABLE milestone_check ON COMMIT DROP AS SELECT * FROM reporting.construction_milestone_detail;
CREATE TEMP TABLE bridge_check ON COMMIT DROP AS SELECT * FROM reporting.construction_milestone_type_bridge;
CREATE TEMP TABLE movement_check ON COMMIT DROP AS
 SELECT proj_id,task_id,baseline_match_status,current_start,current_finish,baseline_start,baseline_finish
 FROM reporting.construction_activity_movement;

-- 1-6, 9: requested four cuts; counts below use distinct activities, never bridge rows.
SELECT "Project ID",full_period_code,proj_id,
 count(DISTINCT task_id) FILTER(WHERE record_type='activity') AS construction_activities,
 count(DISTINCT task_id) FILTER(WHERE record_type='milestone') AS milestones,
 count(*) FILTER(WHERE programme_version='Baseline') AS baseline_rows,
 count(*) FILTER(WHERE programme_version='Current Period') AS current_rows,
 count(*) FILTER(WHERE programme_version='Baseline' AND is_renderable) AS baseline_drawable,
 count(*) FILTER(WHERE programme_version='Current Period' AND is_renderable) AS current_drawable,
 count(DISTINCT task_id) FILTER(WHERE record_type='activity' AND programme_version='Baseline' AND NOT is_renderable) AS activities_missing_baseline,
 count(DISTINCT task_id) FILTER(WHERE record_type='milestone' AND programme_version='Baseline' AND gantt_start IS NULL) AS milestones_missing_baseline,
 count(DISTINCT task_id) FILTER(WHERE record_type='milestone' AND programme_version='Current Period' AND gantt_start IS NULL) AS milestones_missing_current,
 count(DISTINCT task_id) FILTER(WHERE discipline_assignment_count>1) AS multiple_disciplines,
 count(DISTINCT task_id) FILTER(WHERE discipline_assignment_count=0) AS unassigned_disciplines,
 count(DISTINCT task_id) FILTER(WHERE record_type='milestone' AND milestone_type_count>1) AS multiple_milestone_types
FROM gantt_check
WHERE project_code_id IN (218,567) AND full_period_code IN ('2026/27.P6','2026/27.P7')
GROUP BY 1,2,3 ORDER BY 1,2,3;

-- 7,8: global uniqueness, with intentional discipline placements explicitly included.
SELECT 'duplicate_row_keys' AS check_name,count(*) AS failures FROM
 (SELECT gantt_row_key FROM gantt_check GROUP BY 1 HAVING count(*)>1) q
UNION ALL
SELECT 'duplicate_activity_version_representation_placement',count(*) FROM
 (SELECT proj_id,task_id,record_type,programme_version,discipline_assignment_key FROM gantt_check
 GROUP BY 1,2,3,4,5 HAVING count(*)>1) q
UNION ALL
SELECT 'incorrect_version_pair',count(*) FROM
 (SELECT proj_id,task_id,record_type,discipline_assignment_key FROM gantt_check
 GROUP BY 1,2,3,4 HAVING count(*)<>2 OR count(DISTINCT programme_version)<>2) q
UNION ALL
SELECT 'non_adjacent_programme_rows',count(*) FROM
 (SELECT proj_id,task_id,record_type,discipline_assignment_key FROM gantt_check
 GROUP BY 1,2,3,4 HAVING max(programme_sort_order)-min(programme_sort_order)<>1) q;

-- 1,2: bidirectional population comparisons across ALL cuts, including source-less extras.
WITH expected AS (
 SELECT proj_id,task_id,'activity'::text record_type FROM activity_check
 UNION ALL SELECT proj_id,task_id,'milestone' FROM milestone_check
), actual AS (SELECT DISTINCT proj_id,task_id,record_type FROM gantt_check)
SELECT 'source_missing_from_gantt' AS check_name,count(*) failures FROM
 (SELECT * FROM expected EXCEPT SELECT * FROM actual) q
UNION ALL SELECT 'gantt_extra_to_source',count(*) FROM
 (SELECT * FROM actual EXCEPT SELECT * FROM expected) q;

-- Placement and every date must match the authoritative sources, including NULLs.
WITH expected AS (
 SELECT a.proj_id,a.task_id,'activity'::text record_type,a.discipline_assignments,
 a.baseline_start,a.baseline_finish,a.current_start,a.current_finish,a.baseline_match_status
 FROM activity_check a
 UNION ALL
 SELECT m.proj_id,m.task_id,'milestone',m.codes#>'{discipline,assignments}',
 m.baseline_date,NULL::timestamp,m.current_date,NULL::timestamp,m.baseline_match_status FROM milestone_check m
), placed AS (
 SELECT e.*,coalesce((d.x->>'value_id'),'unassigned') discipline_assignment_key
 FROM expected e LEFT JOIN LATERAL jsonb_array_elements(coalesce(e.discipline_assignments,'[]'::jsonb)) d(x) ON true
), versioned AS (
 SELECT p.*,'Baseline'::text programme_version,baseline_start expected_start,baseline_finish expected_end FROM placed p
 UNION ALL
 SELECT p.*,'Current Period',current_start,current_finish FROM placed p
)
SELECT 'date_status_and_placement_alignment' AS check_name,count(*) AS failures
FROM versioned e FULL JOIN gantt_check g USING(proj_id,task_id,record_type,discipline_assignment_key,programme_version)
WHERE e.proj_id IS NULL OR g.proj_id IS NULL OR e.expected_start IS DISTINCT FROM g.gantt_start
 OR e.expected_end IS DISTINCT FROM g.gantt_end OR e.baseline_match_status IS DISTINCT FROM g.baseline_match_status;

SELECT 'movement_source_alignment' AS check_name,count(*) AS failures
FROM activity_check a FULL JOIN movement_check m USING(proj_id,task_id)
WHERE a.proj_id IS NULL OR m.proj_id IS NULL OR
 ROW(a.current_start,a.current_finish,a.baseline_start,a.baseline_finish,a.baseline_match_status)
 IS DISTINCT FROM ROW(m.current_start,m.current_finish,m.baseline_start,m.baseline_finish,m.baseline_match_status);

-- Match diagnostics and source ID ambiguity are retained rather than repaired by matching.
SELECT record_type,baseline_match_status,count(DISTINCT activity_row_key) activities
FROM gantt_check WHERE proj_id IN (1753,1797,1792,1843)
GROUP BY 1,2 ORDER BY 1,2;
SELECT record_type,date_status,count(*) FROM gantt_check GROUP BY 1,2 ORDER BY 1,2;
SELECT 'ambiguous_logical_ids' AS check_name,count(*) AS groups FROM
 (SELECT proj_id,task_code,record_type,programme_version,discipline_assignment_key
 FROM gantt_check GROUP BY 1,2,3,4,5 HAVING count(DISTINCT task_id)>1) q;
SELECT 'classification_duplicate_links' AS check_name,count(*) failures FROM
 (SELECT activity_row_key,milestone_value_id FROM bridge_check GROUP BY 1,2 HAVING count(*)>1) q;
SELECT 'classification_multi_value_activities' AS check_name,count(*) activities FROM
 (SELECT activity_row_key FROM bridge_check GROUP BY 1 HAVING count(DISTINCT milestone_value_id)>1) q;
-- OR/EXISTS semijoin never expands facts; duration bars bypass classification selection.
WITH selected AS (SELECT DISTINCT milestone_value_id FROM bridge_check ORDER BY 1 LIMIT 2)
SELECT 'two_value_OR_selection' AS check_name,
 count(*) FILTER(WHERE g.record_type='activity') bars_kept,
 count(*) FILTER(WHERE g.record_type='milestone') marker_rows_kept,
 count(*)-count(DISTINCT gantt_row_key) duplicate_keys
FROM gantt_check g WHERE g.record_type='activity' OR EXISTS
 (SELECT FROM bridge_check b JOIN selected s USING(milestone_value_id) WHERE b.activity_row_key=g.activity_row_key);
SELECT count(*) AS all_cut_rows,count(DISTINCT proj_id) AS cuts,count(DISTINCT project_code_id) AS logical_projects FROM gantt_check;
ROLLBACK;
