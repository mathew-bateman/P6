-- Design Overview, agreed 3 November 2026. PostgreSQL only; no P6 writes.
-- Requires the Coding Check helpers. Public views are additive.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';
CREATE SCHEMA IF NOT EXISTS design_overview;

-- Recover the full hierarchical Period Code instead of sorting short labels.
CREATE OR REPLACE VIEW design_overview.period_codes AS
WITH RECURSIVE nodes AS (
  SELECT v.* FROM reporting.pcatval v
  JOIN reporting.pcattype t USING (_source_system, proj_catg_type_id)
  WHERE v._source_system = 'p6_22' AND lower(btrim(t.proj_catg_type)) = 'period'
    AND v.delete_date IS NULL AND v.delete_session_id IS NULL
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
), paths AS (
  SELECT n.proj_catg_id, n.proj_catg_type_id,
    coding_check.clean_text(n.proj_catg_short_name) AS full_period_code,
    coding_check.clean_text(n.proj_catg_short_name) AS financial_year,
    coding_check.clean_text(n.proj_catg_short_name) AS leaf_code,
    ARRAY[n.proj_catg_id] AS visited
  FROM nodes n WHERE n.parent_proj_catg_id IS NULL
  UNION ALL
  SELECT n.proj_catg_id, n.proj_catg_type_id,
    p.full_period_code || '.' || coding_check.clean_text(n.proj_catg_short_name),
    p.financial_year, coding_check.clean_text(n.proj_catg_short_name),
    p.visited || n.proj_catg_id
  FROM paths p JOIN nodes n ON n.parent_proj_catg_id = p.proj_catg_id
    AND n.proj_catg_type_id = p.proj_catg_type_id
  WHERE NOT n.proj_catg_id = ANY(p.visited)
), parsed AS (
  SELECT *, CASE WHEN financial_year ~ '^[0-9]{4}/[0-9]{2}$'
    THEN left(financial_year,4)::integer END AS financial_year_start,
    CASE WHEN leaf_code ~ '^P(0?[1-9]|1[0-3])$'
    THEN substring(leaf_code FROM 2)::integer END AS period_number
  FROM paths
)
SELECT proj_catg_id, full_period_code, financial_year, financial_year_start,
  period_number, CASE WHEN financial_year_start IS NOT NULL AND period_number IS NOT NULL
    THEN CASE WHEN right(financial_year,2)::integer = (financial_year_start + 1) % 100
      THEN financial_year_start * 13 + period_number END END AS period_serial
FROM parsed;

CREATE OR REPLACE VIEW design_overview.submissions AS
WITH assignments AS MATERIALIZED (
  SELECT DISTINCT a.proj_id, lower(btrim(t.proj_catg_type)) AS code_type,
    v.proj_catg_id, coding_check.clean_text(v.proj_catg_short_name) AS short_value
  FROM reporting.projpcat a
  JOIN reporting.pcattype t USING (_source_system, proj_catg_type_id)
  JOIN reporting.pcatval v USING (_source_system, proj_catg_type_id, proj_catg_id)
  WHERE a._source_system = 'p6_22'
    AND a.delete_date IS NULL AND a.delete_session_id IS NULL
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
    AND v.delete_date IS NULL AND v.delete_session_id IS NULL
    AND lower(btrim(t.proj_catg_type)) IN ('project id','period')
), resolved AS (
  SELECT proj_id,
    CASE WHEN count(*) FILTER (WHERE code_type='project id')=1
      THEN min(proj_catg_id) FILTER (WHERE code_type='project id') END AS project_code_id,
    CASE WHEN count(*) FILTER (WHERE code_type='project id')=1
      THEN min(short_value) FILTER (WHERE code_type='project id') END AS project_code,
    CASE WHEN count(*) FILTER (WHERE code_type='period')=1
      THEN min(proj_catg_id) FILTER (WHERE code_type='period') END AS period_code_id
  FROM assignments GROUP BY proj_id
), candidates AS (
  SELECT cp.proj_id, cp.proj_short_name, cp."Project ID", cp."Account",
    cp."Lead Planner", cp."Period", cp."Project State", cp."Project Status",
    r.project_code_id, r.project_code, r.period_code_id,
    pc.full_period_code, pc.financial_year, pc.financial_year_start,
    pc.period_number, pc.period_serial, p.last_recalc_date AS data_date, cp._snapshot_at
  FROM coding_check.projects cp
  JOIN reporting.project p ON p._source_system = cp._source_system AND p.proj_id = cp.proj_id
  LEFT JOIN resolved r ON r.proj_id = cp.proj_id
  LEFT JOIN design_overview.period_codes pc ON pc.proj_catg_id = r.period_code_id
  WHERE p.orig_proj_id IS NULL
), counted AS (
  SELECT *, count(*) OVER (PARTITION BY project_code_id,period_serial) AS submission_count,
    max(period_serial) OVER (PARTITION BY project_code_id) - period_serial + 1 AS submission_index,
    max(period_serial) OVER () - period_serial + 1 AS global_submission_index
  FROM candidates
), paired AS (
  SELECT c.*, prev.proj_id AS previous_proj_id, prev.proj_short_name AS previous_proj_short_name,
    prev.full_period_code AS previous_full_period_code,
    prev.data_date AS period_start, c.data_date AS period_end,
    CASE WHEN c.project_code_id IS NULL THEN 'missing_or_conflicting_project_id'
      WHEN c.period_code_id IS NULL THEN 'missing_or_conflicting_period'
      WHEN c.period_serial IS NULL THEN 'invalid_period_code'
      WHEN c.submission_count <> 1 THEN 'ambiguous_submission'
      WHEN prev.proj_id IS NULL THEN 'missing_or_ambiguous_previous_submission'
      WHEN c.data_date IS NULL OR prev.data_date IS NULL THEN 'missing_data_date'
      WHEN c.data_date <= prev.data_date THEN 'non_increasing_data_date'
      ELSE 'ready' END AS comparison_status
  FROM counted c
  LEFT JOIN counted prev ON prev.project_code_id = c.project_code_id
    AND prev.period_serial = c.period_serial - 1
    AND prev.submission_count = 1 AND c.submission_count = 1
)
SELECT *, comparison_status='ready' AS is_comparable,
  CASE submission_index WHEN 1 THEN 'Current' WHEN 2 THEN 'Previous'
    ELSE full_period_code END AS submission_label
FROM paired;

-- Match the coding-check buckets, but restrict the expensive assignment scan
-- to ordinary design submission programmes. Duplicate links count once.
CREATE OR REPLACE VIEW design_overview.eligible_activities AS
WITH raw_tasks AS MATERIALIZED (
  SELECT t._source_system,t.proj_id,t.task_id,t.task_code,t.task_name,t.task_type,t.status_code,
    t.restart_date,t.reend_date,t.target_start_date,t.target_end_date,t.act_start_date,t.act_end_date
  FROM reporting.task t WHERE t._source_system='p6_22'
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
), retired AS MATERIALIZED (
  SELECT DISTINCT a.proj_id,a.task_id FROM reporting.taskactv a
  JOIN reporting.actvtype t USING(_source_system,actv_code_type_id)
  JOIN reporting.actvcode v USING(_source_system,actv_code_type_id,actv_code_id)
  WHERE a._source_system='p6_22' AND t._source_system='p6_22' AND v._source_system='p6_22'
    AND a.delete_date IS NULL AND a.delete_session_id IS NULL
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
    AND v.delete_date IS NULL AND v.delete_session_id IS NULL
    AND lower(btrim(t.actv_code_type))='activity status' AND upper(btrim(v.short_name))='DEL'
), projects AS MATERIALIZED (SELECT proj_id FROM design_overview.submissions)
SELECT a.* FROM raw_tasks a JOIN projects p USING(proj_id)
LEFT JOIN retired r USING(proj_id,task_id) WHERE r.task_id IS NULL;

CREATE OR REPLACE VIEW design_overview.activity_codes AS
WITH activities AS MATERIALIZED (
  SELECT * FROM design_overview.eligible_activities
), valid AS MATERIALIZED (
  SELECT DISTINCT a.proj_id, a.task_id, v.code_dimension, v.actv_code_type_id,
    v.actv_code_id, v.code_short_name, v.code_raw_value, v.code_value, v.code_sort_order
  FROM reporting.taskactv a
  JOIN activities t ON t._source_system=a._source_system AND t.proj_id=a.proj_id AND t.task_id=a.task_id
  JOIN coding_check.code_values v ON v._source_system=a._source_system
    AND v.actv_code_type_id=a.actv_code_type_id AND v.actv_code_id=a.actv_code_id
  WHERE a._source_system='p6_22' AND v._source_system='p6_22'
    AND a.delete_date IS NULL AND a.delete_session_id IS NULL
), grouped AS (
  SELECT proj_id,task_id,code_dimension,count(*) AS valid_value_count,
    CASE WHEN count(*)=1 THEN min(actv_code_type_id) END AS actv_code_type_id,
    CASE WHEN count(*)=1 THEN min(actv_code_id) END AS actv_code_id,
    CASE WHEN count(*)=1 THEN min(code_short_name) END AS code_short_name,
    CASE WHEN count(*)=1 THEN min(code_raw_value) END AS code_raw_value,
    CASE WHEN count(*)=1 THEN min(code_value) END AS code_value,
    jsonb_agg(jsonb_build_object('type_id',actv_code_type_id,'value_id',actv_code_id,
      'short_name',code_short_name,'raw_value',code_raw_value,'value',code_value)
      ORDER BY actv_code_type_id,actv_code_id) AS assignments
  FROM valid GROUP BY proj_id,task_id,code_dimension
)
SELECT a.proj_id,a.task_id,d.code_dimension,d.code_type_name,
  coalesce(g.valid_value_count,0) AS valid_value_count,g.actv_code_type_id,g.actv_code_id,
  g.code_short_name,g.code_raw_value,
  CASE WHEN g.valid_value_count>1 THEN 'Multiple code assignments'
    WHEN g.valid_value_count=1 THEN g.code_value ELSE 'No codes / Blank' END AS code_value,
  CASE WHEN g.valid_value_count>1 THEN 'conflict'
    WHEN g.valid_value_count=1 THEN 'coded' ELSE 'uncoded' END AS bucket_status,
  coalesce(g.assignments,'[]'::jsonb) AS assignments,
  a.task_code,a.task_name,a.task_type,a.status_code,a.restart_date,a.reend_date,
  a.target_start_date,a.target_end_date,a.act_start_date,a.act_end_date
FROM activities a CROSS JOIN coding_check.dimensions d
LEFT JOIN grouped g ON g.proj_id=a.proj_id AND g.task_id=a.task_id AND g.code_dimension=d.code_dimension;

CREATE OR REPLACE VIEW design_overview.activity_facts AS
WITH codes AS (
  SELECT proj_id,task_id,
    max(task_code) AS task_code,max(task_name) AS task_name,max(task_type) AS task_type,
    max(status_code) AS status_code,max(restart_date) AS restart_date,max(reend_date) AS reend_date,
    max(target_start_date) AS target_start_date,max(target_end_date) AS target_end_date,
    max(act_start_date) AS act_start_date,max(act_end_date) AS act_end_date,
    jsonb_object_agg(code_dimension,jsonb_build_object('actv_code_type_id',actv_code_type_id,
      'actv_code_id',actv_code_id,'code_value',code_value,'code_short_name',code_short_name,
      'code_raw_value',code_raw_value,'bucket_status',bucket_status,
      'valid_value_count',valid_value_count,'assignments',assignments)) AS codes,
    max(valid_value_count) FILTER(WHERE code_dimension='design_key')>0 AS has_design_key
  FROM design_overview.activity_codes GROUP BY proj_id,task_id
)
SELECT a.proj_id,a.task_id,a.task_code,a.task_name,a.task_type,a.status_code,
  a.restart_date,a.reend_date,a.target_start_date,a.target_end_date,
  a.act_start_date,a.act_end_date,
  CASE WHEN a.status_code IN ('TK_Active','TK_Complete') THEN a.act_start_date
    ELSE a.restart_date END AS scheduled_start_date,
  CASE WHEN a.status_code='TK_Complete' THEN a.act_end_date ELSE a.reend_date END AS scheduled_finish_date,
  CASE WHEN a.task_type='TT_Mile' THEN
    CASE WHEN a.status_code IN ('TK_Active','TK_Complete') THEN a.act_start_date ELSE a.restart_date END
    WHEN a.status_code='TK_Complete' THEN a.act_end_date ELSE a.reend_date END AS scheduled_event_date,
  CASE WHEN a.task_type='TT_Mile' THEN a.act_start_date ELSE a.act_end_date END AS actual_event_date,
  CASE WHEN a.task_type='TT_Mile' AND a.status_code IN ('TK_Active','TK_Complete') THEN 'act_start_date'
    WHEN a.task_type='TT_Mile' THEN 'restart_date'
    WHEN a.status_code='TK_Complete' THEN 'act_end_date' ELSE 'reend_date' END AS scheduled_event_source,
  a.status_code='TK_Complete' AS is_complete,a.has_design_key,a.codes,
  a.codes->'project_stage'->>'code_value' AS project_stage,
  a.codes->'discipline'->>'code_value' AS discipline,
  a.codes->'design_key'->>'code_value' AS design_key,
  a.codes->'design_key'->>'bucket_status' AS design_key_bucket_status,
  a.codes->'milestone_type'->>'code_value' AS milestone_type,
  a.codes->'responsible_company'->>'code_value' AS responsible_company,
  count(*) OVER(PARTITION BY a.proj_id,a.task_code) AS task_code_count
FROM codes a;

CREATE OR REPLACE VIEW reporting.design_overview_submissions AS SELECT * FROM design_overview.submissions;

CREATE OR REPLACE VIEW reporting.design_overview_activity_detail AS
WITH facts AS MATERIALIZED (SELECT * FROM design_overview.activity_facts WHERE has_design_key),
submissions AS MATERIALIZED (SELECT * FROM design_overview.submissions)
SELECT a.*,s.proj_short_name,s."Project ID",s."Account",s."Lead Planner",
  s."Project State",s."Project Status",s."Period",s.full_period_code AS "Period Code",
  s.full_period_code,s.financial_year,s.period_number,s.data_date,s.submission_index,
  s.global_submission_index,s.submission_label,s.comparison_status,s._snapshot_at
FROM facts a JOIN submissions s ON s.proj_id=a.proj_id;

CREATE OR REPLACE VIEW design_overview.code_buckets AS
SELECT code_dimension,code_type_name,actv_code_type_id,actv_code_id,
  code_short_name,code_raw_value,code_value,code_sort_order,'coded'::text AS bucket_status
FROM coding_check.code_values WHERE _source_system='p6_22'
UNION ALL
SELECT d.code_dimension,d.code_type_name,NULL::integer,NULL::integer,NULL::text,NULL::text,
  b.code_value,b.code_sort_order,b.bucket_status
FROM coding_check.dimensions d CROSS JOIN (VALUES
  ('No codes / Blank',2147483646,'uncoded'),
  ('Multiple code assignments',2147483647,'conflict'),
  ('No activity in submission',2147483645,'unavailable')
) b(code_value,code_sort_order,bucket_status);

CREATE OR REPLACE VIEW reporting.design_overview_activity_code_counts AS
WITH facts AS MATERIALIZED (SELECT * FROM design_overview.activity_facts WHERE has_design_key),
counts AS (
  SELECT a.proj_id,d.code_dimension,
    (j.code->>'actv_code_type_id')::integer AS actv_code_type_id,
    (j.code->>'actv_code_id')::integer AS actv_code_id,
    j.code->>'bucket_status' AS bucket_status,count(*) AS activity_count
  FROM facts a CROSS JOIN coding_check.dimensions d
  CROSS JOIN LATERAL (SELECT a.codes->d.code_dimension AS code) j
  GROUP BY 1,2,3,4,5
), totals AS (SELECT proj_id,count(*) AS project_activity_count FROM facts GROUP BY proj_id)
SELECT s.proj_id,s.proj_short_name,s."Project ID",s."Account",s."Lead Planner",s."Period",
  s.full_period_code AS "Period Code",s.full_period_code,s.submission_index,s.submission_label,
  b.*,b.bucket_status='uncoded' AS is_uncoded,
  coalesce(c.activity_count,0::bigint) AS activity_count,
  coalesce(t.project_activity_count,0::bigint) AS project_activity_count,s._snapshot_at
FROM design_overview.submissions s CROSS JOIN design_overview.code_buckets b
LEFT JOIN counts c ON c.proj_id=s.proj_id AND c.code_dimension=b.code_dimension
  AND c.bucket_status=b.bucket_status AND c.actv_code_type_id IS NOT DISTINCT FROM b.actv_code_type_id
  AND c.actv_code_id IS NOT DISTINCT FROM b.actv_code_id
LEFT JOIN totals t ON t.proj_id=s.proj_id
WHERE b.bucket_status <> 'unavailable' AND (b.bucket_status <> 'conflict' OR coalesce(c.activity_count,0)>0);

CREATE OR REPLACE VIEW design_overview.performance_detail AS
WITH facts AS MATERIALIZED (SELECT * FROM design_overview.activity_facts),
pairs AS MATERIALIZED (SELECT * FROM design_overview.submissions WHERE is_comparable),
previous AS MATERIALIZED (
  SELECT s.proj_id AS comparison_proj_id,a.* FROM pairs s JOIN facts a ON a.proj_id=s.previous_proj_id
), current AS MATERIALIZED (
  SELECT s.proj_id AS comparison_proj_id,a.* FROM pairs s JOIN facts a ON a.proj_id=s.proj_id
), keys AS (
  SELECT comparison_proj_id,task_code,max(task_code_count) AS max_code_count
  FROM (SELECT comparison_proj_id,task_code,task_code_count FROM previous
    UNION ALL SELECT comparison_proj_id,task_code,task_code_count FROM current) k
  GROUP BY comparison_proj_id,task_code
), matched AS (
  SELECT coalesce(p.comparison_proj_id,c.comparison_proj_id) AS proj_id,
    coalesce(p.task_code,c.task_code) AS task_code,
    p.task_id AS previous_task_id,c.task_id AS current_task_id,
    p.task_name AS previous_task_name,c.task_name AS current_task_name,
    p.has_design_key AS previous_has_design_key,c.has_design_key AS current_has_design_key,
    p.codes AS previous_codes,c.codes AS current_codes,
    p.task_type AS previous_task_type,c.task_type AS current_task_type,
    p.status_code AS previous_status,c.status_code AS current_status,
    p.is_complete AS previous_complete,c.is_complete AS current_complete,
    p.scheduled_event_date AS planned_event_date,c.actual_event_date,
    p.scheduled_event_source AS planned_event_source,
    p.scheduled_start_date AS planned_start_date,p.scheduled_finish_date AS planned_finish_date,
    c.act_start_date AS actual_start_date,c.act_end_date AS actual_finish_date
  FROM previous p FULL JOIN current c ON c.comparison_proj_id=p.comparison_proj_id
    AND c.task_code=p.task_code AND coding_check.clean_text(p.task_code) IS NOT NULL
    AND p.task_code_count=1 AND c.task_code_count=1
  WHERE coalesce(p.has_design_key,false) OR coalesce(c.has_design_key,false)
), classified AS (
  SELECT m.*,s.previous_proj_id,s.proj_short_name,s.previous_proj_short_name,
    s."Project ID",s.project_code_id,s.project_code,s."Account",s."Lead Planner",
    s.full_period_code,s.previous_full_period_code,s.submission_index,s.submission_label,
    s.period_start,s.period_end,s._snapshot_at,
    CASE WHEN coding_check.clean_text(m.task_code) IS NULL THEN 'missing_activity_id'
      WHEN k.max_code_count>1 THEN 'ambiguous_activity_id'
      WHEN previous_task_id IS NULL THEN 'current_only'
      WHEN current_task_id IS NULL THEN 'previous_only' ELSE 'matched' END AS match_status,
    coalesce(previous_has_design_key,false) IS DISTINCT FROM coalesce(current_has_design_key,false)
      AS design_membership_changed
  FROM matched m JOIN pairs s ON s.proj_id=m.proj_id
  LEFT JOIN keys k ON k.comparison_proj_id=m.proj_id AND k.task_code IS NOT DISTINCT FROM m.task_code
), flags AS (
  SELECT *, CASE WHEN match_status IN ('missing_activity_id','ambiguous_activity_id') THEN NULL
    WHEN NOT coalesce(previous_has_design_key,false) THEN false
    WHEN previous_complete THEN false
    WHEN previous_complete IS NULL OR planned_event_date IS NULL THEN NULL
    ELSE planned_event_date >= period_start AND planned_event_date < period_end END AS is_planned,
    CASE WHEN match_status IN ('missing_activity_id','ambiguous_activity_id') THEN NULL
      WHEN current_task_id IS NULL THEN NULL
      WHEN current_complete IS NULL THEN NULL
      WHEN current_complete AND actual_event_date IS NULL THEN NULL
      WHEN NOT current_complete AND actual_event_date IS NOT NULL THEN NULL
      ELSE current_complete AND actual_event_date >= period_start AND actual_event_date < period_end
    END AS is_achieved
  FROM classified
)
SELECT *, CASE WHEN is_planned IS FALSE THEN false
    WHEN is_planned IS NULL OR is_achieved IS NULL THEN NULL
    ELSE NOT is_achieved END AS is_missed,
  CASE WHEN is_achieved IS FALSE THEN false
    WHEN is_achieved IS NULL OR is_planned IS NULL THEN NULL
    ELSE NOT is_planned END AS is_unplanned,
  is_planned IS TRUE AND is_achieved IS NULL AS unknown_planned,
  is_achieved IS TRUE AND is_planned IS NULL AS unknown_plan_achieved,
  previous_task_id IS NOT NULL AND current_task_id IS NULL AS missing_current_activity,
  coalesce(previous_has_design_key,false) AND NOT coalesce(current_has_design_key,false) AS removed_design_key,
  NOT coalesce(previous_has_design_key,false) AND coalesce(current_has_design_key,false) AS added_design_key,
  CASE WHEN match_status IN ('missing_activity_id','ambiguous_activity_id') THEN 'ambiguous_activity'
    WHEN current_task_id IS NULL THEN 'missing_current_activity'
    WHEN current_complete IS NULL THEN 'missing_completion_status'
    WHEN current_complete AND actual_event_date IS NULL THEN 'missing_actual_event_date'
    WHEN NOT current_complete AND actual_event_date IS NOT NULL THEN 'actual_date_status_conflict'
    WHEN previous_has_design_key AND NOT previous_complete AND planned_event_date IS NULL THEN 'missing_planned_event_date'
    ELSE 'ok' END AS activity_issue
FROM flags;

CREATE OR REPLACE VIEW reporting.design_overview_performance_detail AS
SELECT p.*,
  previous_codes->'project_stage'->>'code_value' AS previous_project_stage,
  current_codes->'project_stage'->>'code_value' AS current_project_stage,
  previous_codes->'discipline'->>'code_value' AS previous_discipline,
  current_codes->'discipline'->>'code_value' AS current_discipline,
  previous_codes->'design_key'->>'code_value' AS previous_design_key,
  current_codes->'design_key'->>'code_value' AS current_design_key,
  previous_codes->'milestone_type'->>'code_value' AS previous_milestone_type,
  current_codes->'milestone_type'->>'code_value' AS current_milestone_type,
  previous_codes->'responsible_company'->>'code_value' AS previous_responsible_company,
  current_codes->'responsible_company'->>'code_value' AS current_responsible_company
FROM design_overview.performance_detail p;

CREATE OR REPLACE VIEW reporting.design_overview_performance_code_counts AS
WITH details AS MATERIALIZED (SELECT * FROM design_overview.performance_detail),
rows AS (
  SELECT p.*,d.code_dimension,b.code_basis,
    (j.code->>'actv_code_type_id')::integer AS actv_code_type_id,
    (j.code->>'actv_code_id')::integer AS actv_code_id,
    coalesce(j.code->>'bucket_status','unavailable') AS bucket_status
  FROM details p CROSS JOIN coding_check.dimensions d
  CROSS JOIN (VALUES('plan'),('outcome')) b(code_basis)
  CROSS JOIN LATERAL (SELECT (CASE b.code_basis WHEN 'plan' THEN p.previous_codes
    ELSE p.current_codes END)->d.code_dimension AS code) j
), counts AS (
  SELECT proj_id,code_dimension,code_basis,actv_code_type_id,actv_code_id,bucket_status,
    count(*) AS detail_activity_count,
    count(*) FILTER(WHERE is_planned) AS planned_count,
    count(*) FILTER(WHERE is_achieved) AS achieved_count,
    count(*) FILTER(WHERE is_planned AND is_achieved) AS planned_achieved_count,
    count(*) FILTER(WHERE is_missed) AS missed_count,
    count(*) FILTER(WHERE is_unplanned) AS unplanned_count,
    count(*) FILTER(WHERE unknown_planned) AS unknown_planned_count,
    count(*) FILTER(WHERE unknown_plan_achieved) AS unknown_plan_achieved_count,
    count(*) FILTER(WHERE is_planned IS NULL) AS unknown_plan_count,
    count(*) FILTER(WHERE is_achieved IS NULL) AS unknown_outcome_count
  FROM rows GROUP BY proj_id,code_dimension,code_basis,actv_code_type_id,actv_code_id,bucket_status
)
SELECT s.proj_id,s.proj_short_name,s."Project ID",s."Account",s."Lead Planner",
  s.previous_proj_id,s.full_period_code AS "Period Code",s.full_period_code,
  s.submission_index,s.submission_label,s.period_start,s.period_end,b.*,basis.code_basis,
  coalesce(c.detail_activity_count,0::bigint) AS detail_activity_count,
  coalesce(c.planned_count,0::bigint) AS planned_count,
  coalesce(c.achieved_count,0::bigint) AS achieved_count,
  coalesce(c.planned_achieved_count,0::bigint) AS planned_achieved_count,
  coalesce(c.missed_count,0::bigint) AS missed_count,
  coalesce(c.unplanned_count,0::bigint) AS unplanned_count,
  coalesce(c.unknown_planned_count,0::bigint) AS unknown_planned_count,
  coalesce(c.unknown_plan_achieved_count,0::bigint) AS unknown_plan_achieved_count,
  coalesce(c.unknown_plan_count,0::bigint) AS unknown_plan_count,
  coalesce(c.unknown_outcome_count,0::bigint) AS unknown_outcome_count,s._snapshot_at
FROM design_overview.submissions s CROSS JOIN design_overview.code_buckets b
CROSS JOIN (VALUES('plan'),('outcome')) basis(code_basis)
LEFT JOIN counts c ON c.proj_id=s.proj_id AND c.code_dimension=b.code_dimension
  AND c.code_basis=basis.code_basis AND c.bucket_status=b.bucket_status
  AND c.actv_code_type_id IS NOT DISTINCT FROM b.actv_code_type_id
  AND c.actv_code_id IS NOT DISTINCT FROM b.actv_code_id
WHERE s.is_comparable AND (b.bucket_status NOT IN('conflict','unavailable') OR coalesce(c.detail_activity_count,0)>0);

CREATE OR REPLACE VIEW reporting.design_overview_issues AS
SELECT s.proj_id,NULL::text AS task_code,s.comparison_status AS issue_type,
  jsonb_build_object('period',s.full_period_code,'data_date',s.data_date,
    'previous_proj_id',s.previous_proj_id,'submission_count',s.submission_count) AS details
FROM design_overview.submissions s WHERE NOT s.is_comparable
UNION ALL
SELECT p.proj_id,p.task_code,p.activity_issue,
  jsonb_build_object('previous_task_id',p.previous_task_id,'current_task_id',p.current_task_id,
    'match_status',p.match_status,'planned_event_date',p.planned_event_date,
    'actual_event_date',p.actual_event_date)
FROM design_overview.performance_detail p WHERE p.activity_issue <> 'ok';

COMMENT ON VIEW reporting.design_overview_submissions IS
  'P6 22 ordinary Live Client Submitted projects. Full hierarchical Period Code. Exact preceding financial period only; interval [previous data date,current data date). submission_index is relative to latest period for this Project ID; global_submission_index aligns all projects.';
COMMENT ON VIEW reporting.design_overview_activity_detail IS
  'One activity per submission with at least one valid Global Design Key. Includes conflicting assignments as a visible bucket. Scheduled dates use Remaining Early or Actual according to status; no silent Planned/Early fallback.';
COMMENT ON VIEW reporting.design_overview_activity_code_counts IS
  'Design activities by submission/dimension/value, including zero values and uncoded buckets. Sum one code_dimension only; project_activity_count repeats the design denominator.';
COMMENT ON VIEW reporting.design_overview_performance_detail IS
  'Full outer comparison of Activity IDs in consecutive ordinary submissions. Design membership in either side retained. Achieved includes unplanned. NULL flags mean unknown, not false. Missing/deleted activities do not become confirmed misses.';
COMMENT ON VIEW reporting.design_overview_performance_code_counts IS
  'Filter exactly one code_basis (plan or outcome) and one code_dimension. All metrics use that classification basis; both classifications remain in detail. planned=planned_achieved+missed+unknown_planned; achieved=planned_achieved+unplanned+unknown_plan_achieved. Never sum bases or dimensions together.';

GRANT USAGE ON SCHEMA reporting TO p6_reporting_reader;
GRANT SELECT ON reporting.design_overview_submissions,reporting.design_overview_activity_detail,
  reporting.design_overview_activity_code_counts,reporting.design_overview_performance_detail,
  reporting.design_overview_performance_code_counts,reporting.design_overview_issues TO p6_reporting_reader;
GRANT SELECT ON reporting.design_overview_submissions,reporting.design_overview_activity_detail,
  reporting.design_overview_activity_code_counts,reporting.design_overview_performance_detail,
  reporting.design_overview_performance_code_counts,reporting.design_overview_issues TO p6_reporting_sync WITH GRANT OPTION;
COMMIT;
