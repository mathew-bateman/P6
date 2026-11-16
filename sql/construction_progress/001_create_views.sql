-- Additive P6 22 construction reporting. Existing helpers and source objects are unchanged.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='8s';
SET LOCAL statement_timeout='120s';
DO $$ BEGIN IF current_database()<>'p6_reporting' AND current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'Unexpected database'; END IF; END $$;
CREATE SCHEMA IF NOT EXISTS construction_progress;
REVOKE ALL ON SCHEMA construction_progress FROM PUBLIC;

-- Chronological predecessor is independent of the Design Overview exact-period rule.
CREATE OR REPLACE VIEW construction_progress.cuts AS
WITH cuts AS MATERIALIZED (SELECT * FROM reporting.design_overview_submissions),
ranked AS (
 SELECT s.*, count(*) OVER (PARTITION BY s.project_code_id,s.data_date::date) AS same_date_count,
 count(*) FILTER (WHERE s.data_date IS NULL) OVER (PARTITION BY s.project_code_id) AS undated_cut_count
 FROM cuts s
), selected AS (
 SELECT c.*,p.previous_date,p.previous_count,p.previous_id
 FROM ranked c LEFT JOIN LATERAL (
   SELECT max(x.data_date) AS previous_date,
     count(*) FILTER (WHERE x.data_date::date=z.max_date) AS previous_count,
     CASE WHEN count(*)=1 THEN min(x.proj_id) END AS previous_id
   FROM ranked x CROSS JOIN LATERAL (
     SELECT max(y.data_date::date) AS max_date FROM ranked y
     WHERE y.project_code_id=c.project_code_id AND y.data_date::date<c.data_date::date
   ) z WHERE x.project_code_id=c.project_code_id AND x.data_date::date=z.max_date
 ) p ON true
), paired AS (
 SELECT c.*, CASE WHEN c.project_code_id IS NOT NULL AND c.data_date IS NOT NULL
     AND c.same_date_count=1 AND c.previous_count=1 THEN c.previous_id END AS chronological_previous_proj_id,
 CASE WHEN c.project_code_id IS NULL THEN 'missing_project_id'
      WHEN c.data_date IS NULL THEN 'missing_data_date'
      WHEN c.same_date_count<>1 THEN 'ambiguous_current_data_date'
      WHEN c.previous_count=0 THEN 'no_previous_cut'
      WHEN c.previous_count>1 THEN 'ambiguous_previous_data_date'
      ELSE 'ready' END AS chronological_previous_status
 FROM selected c
)
SELECT c.*,p.full_period_code AS chronological_previous_period_code,p.data_date AS chronological_previous_data_date,
 CASE WHEN c.period_serial IS NOT NULL AND p.period_serial IS NOT NULL
      THEN c.period_serial-p.period_serial END AS period_distance,
 CASE WHEN c.period_serial IS NOT NULL AND p.period_serial IS NOT NULL
      THEN c.period_serial-p.period_serial<>1 END AS period_gap
FROM paired c LEFT JOIN cuts p ON p.proj_id=c.chronological_previous_proj_id;

-- Match unique exact Activity IDs across all eligible activities, including uncoded comparators.
CREATE OR REPLACE VIEW construction_progress.matched AS
WITH facts AS MATERIALIZED (SELECT * FROM design_overview.activity_facts),
ids AS MATERIALIZED (SELECT proj_id,task_code,count(*) AS id_count FROM facts GROUP BY proj_id,task_code)
SELECT s.*, f.task_id,f.task_code,f.task_name,f.task_type,f.status_code,f.codes,
 f.task_code_count,f.restart_date,f.reend_date,f.target_start_date,f.target_end_date,
 f.act_start_date,f.act_end_date,f.scheduled_start_date,f.scheduled_finish_date,
 b.task_id AS baseline_task_id,b.task_code_count AS baseline_task_code_count,
 b.scheduled_start_date AS baseline_start,b.scheduled_finish_date AS baseline_finish,
 b.act_start_date AS baseline_actual_start,b.act_end_date AS baseline_actual_finish,
 b.task_type AS baseline_task_type,
 p.task_id AS previous_task_id,p.task_code_count AS previous_task_code_count,
 p.reend_date AS previous_forecast_finish,p.act_end_date AS previous_actual_finish,
 p.status_code AS previous_status_code,p.codes AS previous_codes,
 CASE WHEN f.task_code IS NULL OR btrim(f.task_code)='' THEN 'missing_activity_id'
      WHEN f.task_code_count<>1 THEN 'ambiguous_current_activity_id'
      WHEN s.baseline_status<>'ready' THEN s.baseline_status
      WHEN bi.id_count>1 THEN 'ambiguous_baseline_activity_id'
      WHEN b.task_id IS NULL THEN 'missing_baseline_activity'
      WHEN b.scheduled_finish_date IS NULL THEN 'missing_baseline_finish' ELSE 'ready' END AS baseline_match_status,
 CASE WHEN f.task_code IS NULL OR btrim(f.task_code)='' THEN 'missing_activity_id'
      WHEN f.task_code_count<>1 THEN 'ambiguous_current_activity_id'
      WHEN s.chronological_previous_status<>'ready' THEN s.chronological_previous_status
      WHEN pi.id_count>1 THEN 'ambiguous_previous_activity_id'
      WHEN p.task_id IS NULL THEN 'missing_previous_activity'
      WHEN p.reend_date IS NULL THEN 'missing_previous_forecast_finish' ELSE 'ready' END AS previous_match_status
FROM construction_progress.cuts s JOIN facts f ON f.proj_id=s.proj_id
LEFT JOIN ids bi ON bi.proj_id=s.baseline_proj_id AND bi.task_code=f.task_code
LEFT JOIN ids pi ON pi.proj_id=s.chronological_previous_proj_id AND pi.task_code=f.task_code
LEFT JOIN facts b ON b.proj_id=s.baseline_proj_id AND b.task_code=f.task_code AND b.task_code_count=1 AND f.task_code_count=1 AND nullif(btrim(f.task_code),'') IS NOT NULL
LEFT JOIN facts p ON p.proj_id=s.chronological_previous_proj_id AND p.task_code=f.task_code AND p.task_code_count=1 AND f.task_code_count=1 AND nullif(btrim(f.task_code),'') IS NOT NULL;

CREATE OR REPLACE VIEW reporting.construction_activity_detail AS
SELECT m.*,m.proj_id::text||':'||m.task_id::text AS activity_row_key,t.wbs_id,w.wbs_short_name,w.wbs_name,w.parent_wbs_id,
 (m.codes#>>'{discipline,actv_code_id}')::integer AS discipline_value_id,
 m.codes#>>'{discipline,code_short_name}' AS discipline_short_name,
 m.codes#>>'{discipline,code_value}' AS discipline_description,
 m.codes#>>'{discipline,bucket_status}' AS discipline_status,
 m.codes#>'{discipline,assignments}' AS discipline_assignments,
 m.scheduled_start_date AS current_start,m.scheduled_finish_date AS current_finish,
 m.act_start_date AS actual_start,m.act_end_date AS actual_finish,
 EXISTS (SELECT FROM jsonb_array_elements(coalesce(m.previous_codes#>'{project_stage,assignments}','[]'::jsonb)) x WHERE (x->>'value_id')::integer=4328 AND (x->>'type_id')::integer=379) AS previous_had_es6
FROM construction_progress.matched m
JOIN reporting.task t ON t._source_system='p6_22' AND t.proj_id=m.proj_id AND t.task_id=m.task_id
LEFT JOIN reporting.projwbs w ON w._source_system='p6_22' AND w.proj_id=t.proj_id AND w.wbs_id=t.wbs_id
WHERE m.task_type NOT IN ('TT_Mile','TT_FinMile','TT_LOE')
 AND EXISTS (SELECT FROM jsonb_array_elements(coalesce(m.codes#>'{project_stage,assignments}','[]'::jsonb)) x WHERE (x->>'value_id')::integer=4328 AND (x->>'type_id')::integer=379);

CREATE OR REPLACE VIEW reporting.construction_activity_movement AS
SELECT a.*,
 CASE WHEN data_date IS NULL THEN NULL ELSE coalesce(act_end_date::date<=data_date::date,false) END AS actual_complete,
 CASE WHEN data_date IS NULL OR baseline_match_status<>'ready' THEN NULL ELSE baseline_finish::date<=data_date::date END AS baseline_planned_complete,
 CASE WHEN data_date IS NULL OR previous_match_status<>'ready' THEN NULL ELSE previous_forecast_finish::date<=data_date::date END AS previous_planned_complete,
 CASE WHEN previous_task_id IS NULL THEN previous_match_status WHEN previous_had_es6 THEN 'previously_es6' ELSE 'recoded_to_es6' END AS previous_membership_status
FROM reporting.construction_activity_detail a;

-- One row per activity; multi-code membership lives in the separate bridge.
CREATE OR REPLACE VIEW reporting.construction_milestone_detail AS
SELECT m.*,m.proj_id::text||':'||m.task_id::text AS activity_row_key,t.wbs_id,w.wbs_short_name,w.wbs_name,
 CASE WHEN m.task_type IN ('TT_Mile','TT_FinMile') AND (m.codes#>>'{milestone_type,valid_value_count}')::int>0 THEN 'p6_type_and_code'
      WHEN m.task_type IN ('TT_Mile','TT_FinMile') THEN 'p6_type' ELSE 'code' END AS classification_source,
 (m.codes#>>'{milestone_type,valid_value_count}')::int AS milestone_type_count,
 m.codes#>'{milestone_type,assignments}' AS milestone_assignments,
 CASE WHEN m.task_type='TT_Mile' THEN m.baseline_start ELSE m.baseline_finish END AS baseline_date,
 CASE WHEN m.task_type='TT_Mile' THEN m.scheduled_start_date ELSE m.scheduled_finish_date END AS current_date,
 CASE WHEN m.task_type='TT_Mile' THEN m.restart_date ELSE m.reend_date END AS forecast_date,
 CASE WHEN m.task_type='TT_Mile' THEN m.act_start_date ELSE m.act_end_date END AS actual_date,
 CASE WHEN m.task_type='TT_Mile' THEN m.scheduled_start_date::date-m.baseline_start::date
      ELSE m.scheduled_finish_date::date-m.baseline_finish::date END AS baseline_movement_days
FROM construction_progress.matched m
JOIN reporting.task t ON t._source_system='p6_22' AND t.proj_id=m.proj_id AND t.task_id=m.task_id
LEFT JOIN reporting.projwbs w ON w._source_system='p6_22' AND w.proj_id=t.proj_id AND w.wbs_id=t.wbs_id
WHERE m.task_type IN ('TT_Mile','TT_FinMile') OR coalesce((m.codes#>>'{milestone_type,valid_value_count}')::int,0)>0;

CREATE OR REPLACE VIEW reporting.construction_milestone_type_bridge AS
SELECT m.proj_id,m.task_id,m.activity_row_key,(x->>'type_id')::integer AS milestone_type_id,
 (x->>'value_id')::integer AS milestone_value_id,x->>'short_name' AS milestone_short_name,
 x->>'raw_value' AS milestone_description
FROM reporting.construction_milestone_detail m
CROSS JOIN LATERAL jsonb_array_elements(coalesce(m.milestone_assignments,'[]'::jsonb)) x;

GRANT USAGE ON SCHEMA reporting TO p6_reporting_reader;
GRANT SELECT ON reporting.construction_activity_detail,reporting.construction_activity_movement,reporting.construction_milestone_detail,reporting.construction_milestone_type_bridge TO p6_reporting_reader;
GRANT SELECT ON reporting.construction_activity_detail,reporting.construction_activity_movement,reporting.construction_milestone_detail,reporting.construction_milestone_type_bridge TO p6_reporting_sync WITH GRANT OPTION;
COMMIT;
