-- Version 1, 2026-11-16. Additive: no existing reporting views are replaced.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='8s';
SET LOCAL statement_timeout='180s';
DO $$ BEGIN
 IF current_database()<>'p6_reporting' AND current_database() NOT LIKE 'coding_check_test_%'
 THEN RAISE EXCEPTION 'Unexpected database'; END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS p6_reporting;
CREATE VIEW p6_reporting.construction_gantt_detail AS
WITH RECURSIVE wbs AS (
 SELECT w.proj_id,w.wbs_id,w.parent_wbs_id,
 ARRAY[coalesce(w.seq_num,2147483647),w.wbs_id] AS wbs_sort_path,
 ARRAY[w.wbs_id] AS visited
 FROM reporting.projwbs w
 WHERE w._source_system='p6_22' AND NOT EXISTS (
 SELECT FROM reporting.projwbs p WHERE p._source_system='p6_22'
 AND p.proj_id=w.proj_id AND p.wbs_id=w.parent_wbs_id)
 UNION ALL
 SELECT c.proj_id,c.wbs_id,c.parent_wbs_id,
 p.wbs_sort_path||ARRAY[coalesce(c.seq_num,2147483647),c.wbs_id],p.visited||c.wbs_id
 FROM wbs p JOIN reporting.projwbs c ON c._source_system='p6_22'
 AND c.proj_id=p.proj_id AND c.parent_wbs_id=p.wbs_id
 WHERE NOT c.wbs_id=ANY(p.visited)
), records AS (
 SELECT a.proj_id,a.proj_short_name,a.project_code_id,a.project_code,a."Project ID",
 a.period_code_id,a.full_period_code,a.period_serial,a.data_date,a.cut_date,a._snapshot_at,
 a.submission_index,a.global_submission_index,a.submission_label,
 a.baseline_proj_id,a.baseline_status,a.baseline_match_status,a.baseline_task_id,
 a.task_id,a.task_code,a.task_name,a.task_type,a.status_code,a.task_code_count,
 a.activity_row_key,a.wbs_id,a.wbs_short_name,a.wbs_name,a.codes,
 a.discipline_status,a.discipline_assignments,
 a.baseline_start,a.baseline_finish,a.current_start,a.current_finish,
 NULL::timestamp AS baseline_date,NULL::timestamp AS current_date,
 NULL::text AS classification_source,
 coalesce((a.codes#>>'{milestone_type,valid_value_count}')::int,0) AS milestone_type_count,
 a.codes#>'{milestone_type,assignments}' AS milestone_assignments,
 'activity'::text AS record_type,1 AS record_type_order
 FROM reporting.construction_activity_detail a
 UNION ALL
 SELECT m.proj_id,m.proj_short_name,m.project_code_id,m.project_code,m."Project ID",
 m.period_code_id,m.full_period_code,m.period_serial,m.data_date,m.cut_date,m._snapshot_at,
 m.submission_index,m.global_submission_index,m.submission_label,
 m.baseline_proj_id,m.baseline_status,m.baseline_match_status,m.baseline_task_id,
 m.task_id,m.task_code,m.task_name,m.task_type,m.status_code,m.task_code_count,
 m.activity_row_key,m.wbs_id,m.wbs_short_name,m.wbs_name,m.codes,
 m.codes#>>'{discipline,bucket_status}',m.codes#>'{discipline,assignments}',
 m.baseline_start,m.baseline_finish,m.scheduled_start_date,m.scheduled_finish_date,
 m.baseline_date,m.current_date,m.classification_source,m.milestone_type_count,m.milestone_assignments,
 'milestone'::text,2
 FROM reporting.construction_milestone_detail m
), placements AS (
 SELECT r.*,d.discipline_value_id,d.discipline_short_name,d.discipline_description,
 coalesce(d.discipline_value_id::text,'unassigned') AS discipline_assignment_key,
 coalesce(d.discipline_description,'Unassigned Discipline') AS discipline_label,
 coalesce(jsonb_array_length(r.discipline_assignments),0) AS discipline_assignment_count,
 w.wbs_sort_path,
 CASE WHEN w.wbs_id IS NULL THEN 'missing_or_cyclic_wbs' ELSE 'ready' END AS wbs_order_status
 FROM records r
 -- The assignments are already validated against Global Discipline by the shared helper.
 -- DISTINCT additionally protects placement grain from identical assignment links.
 LEFT JOIN LATERAL (
 SELECT DISTINCT (x->>'value_id')::integer AS discipline_value_id,
 x->>'short_name' AS discipline_short_name,x->>'value' AS discipline_description
 FROM jsonb_array_elements(coalesce(r.discipline_assignments,'[]'::jsonb)) x
 ) d ON true
 LEFT JOIN wbs w ON w.proj_id=r.proj_id AND w.wbs_id=r.wbs_id
), versions AS (
 SELECT p.*,'Baseline'::text AS programme_version,1 AS programme_version_order,
 CASE WHEN record_type='milestone' THEN baseline_date ELSE baseline_start END AS gantt_start,
 CASE WHEN record_type='activity' THEN baseline_finish END AS gantt_end
 FROM placements p
 UNION ALL
 SELECT p.*,'Current Period'::text,2,
 CASE WHEN record_type='milestone' THEN p.current_date ELSE current_start END,
 CASE WHEN record_type='activity' THEN current_finish END
 FROM placements p
), ordered AS (
 SELECT v.*,
 activity_row_key||':'||record_type||':'||programme_version_order||':'||discipline_assignment_key AS gantt_row_key,
 row_number() OVER (PARTITION BY proj_id,discipline_assignment_key
 ORDER BY wbs_sort_path NULLS LAST,task_code COLLATE "C" NULLS LAST,task_id,
 record_type_order,programme_version_order) AS programme_sort_order,
 CASE WHEN gantt_start IS NULL AND record_type='activity' AND gantt_end IS NULL THEN 'missing_start_and_finish'
 WHEN gantt_start IS NULL THEN 'missing_start'
 WHEN record_type='activity' AND gantt_end IS NULL THEN 'missing_finish'
 WHEN record_type='activity' AND gantt_end<gantt_start THEN 'finish_before_start'
 ELSE 'ready' END AS date_status
 FROM versions v
)
SELECT o.*,
 date_status='ready' AS is_renderable,
 CASE WHEN record_type='activity' THEN extract(epoch FROM (gantt_end-gantt_start))/86400 END AS duration_calendar_days,
 record_type='milestone' AS is_milestone,
 coalesce(task_code,'[No Activity ID]')||' — '||coalesce(task_name,'')||' ['||record_type||'] — '||programme_version AS gantt_task_label,
 project_code_id::text||':'||discipline_assignment_key AS project_discipline_key
FROM ordered o;
COMMENT ON VIEW p6_reporting.construction_gantt_detail IS
 'All reporting cuts, activity/milestone representations, explicit discipline placements and two programme versions. Missing-date rows retained; filter is_renderable for drawing. No KPI changes.';
GRANT USAGE ON SCHEMA p6_reporting TO p6_reporting_reader,p6_reporting_sync;
GRANT SELECT ON p6_reporting.construction_gantt_detail TO p6_reporting_reader;
GRANT SELECT ON p6_reporting.construction_gantt_detail TO p6_reporting_sync WITH GRANT OPTION;
COMMIT;
