\set ON_ERROR_STOP on
BEGIN READ ONLY;
SET LOCAL statement_timeout='120s';
-- These four representative cuts cover two logical projects and two periods.
SELECT "Project ID",full_period_code,proj_id,chronological_previous_proj_id,
 chronological_previous_period_code,period_gap,chronological_previous_status,
 count(*) AS activities,
 count(*) FILTER (WHERE actual_complete) AS actual_complete,
 count(*) FILTER (WHERE baseline_planned_complete) AS baseline_planned,
 count(*) FILTER (WHERE baseline_planned_complete IS NULL) AS baseline_unknown,
 count(*) FILTER (WHERE previous_planned_complete) AS previous_planned,
 count(*) FILTER (WHERE previous_planned_complete IS NULL) AS previous_unknown,
 count(*) FILTER (WHERE previous_match_status='missing_previous_activity') AS missing_previous_activity,
 count(*) FILTER (WHERE previous_match_status='missing_previous_forecast_finish') AS missing_previous_forecast
FROM reporting.construction_activity_movement
WHERE project_code_id IN (218,567) AND full_period_code IN ('2026/27.P6','2026/27.P7')
GROUP BY 1,2,3,4,5,6,7 ORDER BY 1,2;

SELECT s."Project ID",s.full_period_code,f.task_type,count(*) AS excluded
FROM design_overview.activity_facts f JOIN reporting.design_overview_submissions s USING(proj_id)
WHERE s.project_code_id IN (218,567) AND s.full_period_code IN ('2026/27.P6','2026/27.P7')
 AND f.task_type IN ('TT_Mile','TT_FinMile','TT_LOE')
 AND EXISTS (SELECT FROM jsonb_array_elements(coalesce(f.codes#>'{project_stage,assignments}','[]'::jsonb)) x
 WHERE (x->>'value_id')::integer=4328 AND (x->>'type_id')::integer=379)
GROUP BY 1,2,3 ORDER BY 1,2,3;

SELECT "Project ID",full_period_code,count(*) AS milestone_rows,
 count(*) FILTER (WHERE classification_source='code') AS code_only,
 count(*) FILTER (WHERE classification_source='p6_type') AS p6_type_only,
 count(*) FILTER (WHERE classification_source='p6_type_and_code') AS both,
 count(*) FILTER (WHERE milestone_type_count>1) AS multiple_codes
FROM reporting.construction_milestone_detail
WHERE project_code_id IN (218,567) AND full_period_code IN ('2026/27.P6','2026/27.P7')
GROUP BY 1,2 ORDER BY 1,2;
ROLLBACK;
