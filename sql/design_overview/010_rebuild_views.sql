-- Design Overview replacement, agreed 4 November 2026. PostgreSQL only; no P6 writes.
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

-- Separate ordinary projects classified BL/Baseline are the only baseline source.
-- Resolve code identity once; no latest-created-ID or assigned-baseline fallback.
CREATE OR REPLACE VIEW design_overview.project_catalog AS
WITH links AS MATERIALIZED (
 SELECT DISTINCT a.proj_id,lower(btrim(t.proj_catg_type)) AS kind,v.proj_catg_id,
   coding_check.clean_text(v.proj_catg_short_name) AS short_value,
   coalesce(coding_check.clean_text(v.proj_catg_name),coding_check.clean_text(v.proj_catg_short_name)) AS description
 FROM reporting.projpcat a JOIN reporting.pcattype t USING(_source_system,proj_catg_type_id)
 JOIN reporting.pcatval v USING(_source_system,proj_catg_type_id,proj_catg_id)
 WHERE a._source_system='p6_22' AND a.delete_date IS NULL AND a.delete_session_id IS NULL
 AND t.delete_date IS NULL AND t.delete_session_id IS NULL
 AND v.delete_date IS NULL AND v.delete_session_id IS NULL
 AND lower(btrim(t.proj_catg_type)) IN('project id','period','project status','projects status','project state','account','lead planner')
), single AS (
 SELECT proj_id,CASE WHEN kind='projects status' THEN 'project status' ELSE kind END AS kind,
   CASE WHEN count(*)=1 THEN min(proj_catg_id) END AS value_id,
   CASE WHEN count(*)=1 THEN min(short_value) END AS short_value,
   CASE WHEN count(*)=1 THEN min(description) END AS description
 FROM links GROUP BY proj_id,CASE WHEN kind='projects status' THEN 'project status' ELSE kind END
), codes AS (
 SELECT proj_id,
   max(value_id) FILTER(WHERE kind='project id') AS project_code_id,
   max(short_value) FILTER(WHERE kind='project id') AS project_code,
   max(description) FILTER(WHERE kind='project id') AS project_name,
   max(value_id) FILTER(WHERE kind='period') AS period_code_id,
   max(description) FILTER(WHERE kind='period') AS period_description,
   max(short_value) FILTER(WHERE kind='project status') AS project_status_code,
   max(description) FILTER(WHERE kind='project status') AS project_status,
   max(description) FILTER(WHERE kind='project state') AS project_state,
   max(description) FILTER(WHERE kind='account') AS account,
   max(description) FILTER(WHERE kind='lead planner') AS lead_planner
 FROM single GROUP BY proj_id
)
SELECT p.proj_id,p.proj_short_name,c.project_code_id,c.project_code,c.project_name AS "Project ID",
 c.account AS "Account",c.lead_planner AS "Lead Planner",c.period_description AS "Period",
 c.project_status AS "Project Status",c.project_state AS "Project State",c.project_status_code,
 c.period_code_id,pc.full_period_code,pc.financial_year,pc.financial_year_start,pc.period_number,pc.period_serial,
 p.last_recalc_date AS data_date,p.last_recalc_date::date AS cut_date,p._snapshot_at
FROM reporting.project p JOIN codes c USING(proj_id)
LEFT JOIN design_overview.period_codes pc ON pc.proj_catg_id=c.period_code_id
WHERE p._source_system='p6_22' AND p.orig_proj_id IS NULL AND p.project_flag='Y'
 AND p.delete_date IS NULL AND p.delete_session_id IS NULL;

CREATE OR REPLACE VIEW design_overview.baseline_candidates AS
SELECT * FROM design_overview.project_catalog
WHERE upper(project_status_code)='BL' AND lower("Project Status")='baseline';

CREATE OR REPLACE VIEW design_overview.baseline_selection AS
WITH candidates AS (
 SELECT b.*,max(period_serial) OVER(PARTITION BY project_code_id) AS latest_serial,
   count(*) FILTER(WHERE period_serial IS NULL) OVER(PARTITION BY project_code_id) AS invalid_period_count
 FROM design_overview.baseline_candidates b WHERE project_code_id IS NOT NULL
), grouped AS (
 SELECT project_code_id,max(latest_serial) AS latest_period_serial,max(invalid_period_count) AS invalid_period_count,
   count(*) FILTER(WHERE period_serial=latest_serial) AS latest_candidate_count,
   min(proj_id) FILTER(WHERE period_serial=latest_serial) AS candidate_proj_id,
   min(full_period_code) FILTER(WHERE period_serial=latest_serial) AS baseline_period_code
 FROM candidates GROUP BY project_code_id
)
SELECT *,CASE WHEN invalid_period_count=0 AND latest_candidate_count=1 THEN candidate_proj_id END AS baseline_proj_id,
 CASE WHEN invalid_period_count>0 THEN 'invalid_baseline_period'
 WHEN latest_candidate_count<>1 THEN 'ambiguous_latest_baseline' ELSE 'ready' END AS baseline_status
FROM grouped;

CREATE OR REPLACE VIEW design_overview.submissions AS
WITH candidates AS (
 SELECT * FROM design_overview.project_catalog WHERE lower("Project State")='live'
   AND lower("Project Status")='client submitted'
), counted AS (
 SELECT *,count(*) OVER(PARTITION BY project_code_id,period_serial) AS submission_count,
 max(period_serial) OVER(PARTITION BY project_code_id)-period_serial+1 AS submission_index,
 max(period_serial) OVER()-period_serial+1 AS global_submission_index,
 dense_rank() OVER(PARTITION BY project_code_id ORDER BY period_serial DESC NULLS LAST) AS history_rank
 FROM candidates
), paired AS (
 SELECT c.*,p.proj_id AS previous_proj_id,p.proj_short_name AS previous_proj_short_name,
 p.full_period_code AS previous_full_period_code,p.cut_date AS period_start,c.cut_date AS period_end,
 b.baseline_proj_id,b.baseline_period_code,coalesce(b.baseline_status,'missing_baseline') AS baseline_status,
 b.latest_candidate_count AS baseline_candidate_count,
 CASE WHEN c.project_code_id IS NULL THEN 'missing_or_conflicting_project_id'
 WHEN c.period_code_id IS NULL THEN 'missing_or_conflicting_period'
 WHEN c.period_serial IS NULL THEN 'invalid_period_code'
 WHEN c.submission_count<>1 THEN 'ambiguous_submission'
 WHEN p.proj_id IS NULL THEN 'missing_or_ambiguous_previous_submission'
 WHEN c.cut_date IS NULL OR p.cut_date IS NULL THEN 'missing_data_date'
 WHEN c.cut_date<=p.cut_date THEN 'non_increasing_data_date' ELSE 'ready' END AS comparison_status
 FROM counted c LEFT JOIN counted p ON p.project_code_id=c.project_code_id
 AND p.period_serial=c.period_serial-1 AND p.submission_count=1 AND c.submission_count=1
 LEFT JOIN design_overview.baseline_selection b ON b.project_code_id=c.project_code_id
)
SELECT *,comparison_status='ready' AS is_comparable,
 CASE submission_index WHEN 1 THEN 'Current' WHEN 2 THEN 'Previous' ELSE full_period_code END AS submission_label
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
), projects AS MATERIALIZED (SELECT proj_id FROM design_overview.submissions UNION SELECT baseline_proj_id FROM design_overview.baseline_selection WHERE baseline_proj_id IS NOT NULL)
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
  CASE WHEN (a.status_code IN ('TK_Active','TK_Complete') OR a.act_start_date IS NOT NULL) THEN a.act_start_date
    ELSE coalesce(a.restart_date,a.target_start_date) END AS scheduled_start_date,
  CASE WHEN a.status_code='TK_Complete' THEN a.act_end_date ELSE coalesce(a.reend_date,a.target_end_date) END AS scheduled_finish_date,
  CASE WHEN (lower(a.codes->'design_key'->>'code_short_name')='d-start' OR a.task_type='TT_Mile') THEN
    CASE WHEN (a.status_code IN ('TK_Active','TK_Complete') OR a.act_start_date IS NOT NULL) THEN a.act_start_date ELSE coalesce(a.restart_date,a.target_start_date) END
    WHEN a.status_code='TK_Complete' THEN a.act_end_date ELSE coalesce(a.reend_date,a.target_end_date) END AS scheduled_event_date,
  CASE WHEN (lower(a.codes->'design_key'->>'code_short_name')='d-start' OR a.task_type='TT_Mile') THEN a.act_start_date ELSE a.act_end_date END AS actual_event_date,
  CASE WHEN (lower(a.codes->'design_key'->>'code_short_name')='d-start' OR a.task_type='TT_Mile') AND (a.status_code IN ('TK_Active','TK_Complete') OR a.act_start_date IS NOT NULL) THEN 'act_start_date'
    WHEN (lower(a.codes->'design_key'->>'code_short_name')='d-start' OR a.task_type='TT_Mile') THEN CASE WHEN a.restart_date IS NOT NULL THEN 'restart_date' ELSE 'target_start_date' END
    WHEN a.status_code='TK_Complete' THEN 'act_end_date' ELSE CASE WHEN a.reend_date IS NOT NULL THEN 'reend_date' ELSE 'target_end_date' END END AS scheduled_event_source,
  a.status_code='TK_Complete' AS is_complete,a.has_design_key,a.codes,
  a.codes->'project_stage'->>'code_value' AS project_stage,
  a.codes->'discipline'->>'code_value' AS discipline,
  a.codes->'design_key'->>'code_value' AS design_key,
  a.codes->'design_key'->>'bucket_status' AS design_key_bucket_status,
  a.codes->'milestone_type'->>'code_value' AS milestone_type,
  a.codes->'responsible_company'->>'code_value' AS responsible_company,
  count(*) OVER(PARTITION BY a.proj_id,a.task_code) AS task_code_count,
  CASE WHEN (lower(a.codes->'design_key'->>'code_short_name')='d-start' OR a.task_type='TT_Mile') THEN 'start' ELSE 'finish' END AS event_basis,
  CASE WHEN (lower(a.codes->'design_key'->>'code_short_name')='d-start' OR a.task_type='TT_Mile') THEN a.act_start_date IS NOT NULL ELSE a.status_code='TK_Complete' END AS checkpoint_reached,
  CASE WHEN (a.codes->'design_key'->>'valid_value_count')::integer>1 THEN 'conflict'
    ELSE coalesce(a.codes->'design_key'->>'code_short_name','uncoded') END AS checkpoint_key,
  CASE WHEN a.act_start_date IS NOT NULL OR a.status_code IN ('TK_Active','TK_Complete') THEN 'act_start_date'
    WHEN a.restart_date IS NOT NULL THEN 'restart_date' ELSE 'target_start_date' END AS start_date_source
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
pairs AS MATERIALIZED (SELECT * FROM design_overview.submissions),
previous AS MATERIALIZED (
 SELECT s.proj_id AS comparison_proj_id,a.* FROM pairs s JOIN facts a ON a.proj_id=s.previous_proj_id
), current AS MATERIALIZED (
 SELECT s.proj_id AS comparison_proj_id,a.* FROM pairs s JOIN facts a ON a.proj_id=s.proj_id
), baselines AS MATERIALIZED (
 SELECT s.proj_id AS comparison_proj_id,a.* FROM pairs s JOIN facts a ON a.proj_id=s.baseline_proj_id
), keys AS MATERIALIZED (
 SELECT comparison_proj_id,task_code,max(task_code_count) AS max_code_count
 FROM (SELECT comparison_proj_id,task_code,task_code_count FROM previous
 UNION ALL SELECT comparison_proj_id,task_code,task_code_count FROM current) k GROUP BY 1,2
), baseline_keys AS MATERIALIZED (
 SELECT comparison_proj_id,task_code,max(task_code_count) AS baseline_code_count
 FROM baselines GROUP BY 1,2
), matched AS (
 SELECT coalesce(p.comparison_proj_id,c.comparison_proj_id) AS proj_id,
 coalesce(p.task_code,c.task_code) AS task_code,
 p.task_id AS previous_task_id,c.task_id AS current_task_id,
 coalesce(c.task_name,p.task_name) AS task_name,p.task_name AS previous_task_name,
 p.codes AS previous_codes,c.codes AS current_codes,
 coalesce(p.has_design_key,false) AS previous_has_design_key,coalesce(c.has_design_key,false) AS current_has_design_key,
 CASE WHEN c.has_design_key THEN c.checkpoint_key ELSE p.checkpoint_key END AS checkpoint_key,
 p.status_code AS previous_status,c.status_code AS current_status,
 c.is_complete AS is_activity_complete,
 p.scheduled_start_date AS previous_start,p.scheduled_finish_date AS previous_finish,
 c.scheduled_start_date AS current_start,c.scheduled_finish_date AS current_finish,
 p.act_start_date AS previous_actual_start,p.act_end_date AS previous_actual_finish,
 c.act_start_date AS actual_start,c.act_end_date AS actual_finish,
 c.start_date_source,c.task_type AS current_task_type,p.task_type AS previous_task_type
 FROM previous p FULL JOIN current c ON c.comparison_proj_id=p.comparison_proj_id
 AND c.task_code=p.task_code AND coding_check.clean_text(p.task_code) IS NOT NULL
 AND p.task_code_count=1 AND c.task_code_count=1
 WHERE coalesce(p.has_design_key,false) OR coalesce(c.has_design_key,false)
), joined AS (
 SELECT m.*,s.proj_short_name,s."Project ID",s."Account",s."Lead Planner",s.project_code_id,s.project_code,
 s.previous_proj_id,s.previous_full_period_code,s.full_period_code,s.period_serial,s.financial_year,s.period_number,
 s.cut_date,s.data_date,s.period_start,s.period_end,s.submission_index,s.global_submission_index,s.history_rank,
 s.submission_label,s.comparison_status,s.is_comparable,s.submission_count,s._snapshot_at,
 s.baseline_proj_id,s.baseline_period_code,s.baseline_status,b.task_id AS baseline_task_id,
 b.scheduled_start_date AS baseline_start,b.scheduled_finish_date AS baseline_finish,
 bk.baseline_code_count,
 CASE WHEN coding_check.clean_text(m.task_code) IS NULL THEN 'missing_activity_id'
 WHEN k.max_code_count>1 THEN 'ambiguous_activity_id'
 WHEN previous_task_id IS NULL THEN 'current_only'
 WHEN current_task_id IS NULL THEN 'previous_only' ELSE 'matched' END AS match_status,
 CASE WHEN lower(m.checkpoint_key)='d-start' OR coalesce(m.current_task_type,m.previous_task_type)='TT_Mile'
 THEN 'start' ELSE 'finish' END AS event_basis
 FROM matched m JOIN pairs s ON s.proj_id=m.proj_id
 LEFT JOIN keys k ON k.comparison_proj_id=m.proj_id AND k.task_code IS NOT DISTINCT FROM m.task_code
 LEFT JOIN baseline_keys bk ON bk.comparison_proj_id=m.proj_id AND bk.task_code=m.task_code
 LEFT JOIN baselines b ON b.comparison_proj_id=m.proj_id AND b.task_code=m.task_code AND b.task_code_count=1
), events AS (
 SELECT *,CASE WHEN checkpoint_key='conflict' THEN NULL
   WHEN event_basis='start' THEN previous_start::date ELSE previous_finish::date END AS planned_event_date,
 CASE WHEN checkpoint_key='conflict' THEN NULL
   WHEN event_basis='start' THEN current_start::date ELSE current_finish::date END AS current_event_date,
 CASE WHEN checkpoint_key='conflict' THEN NULL
   WHEN event_basis='start' THEN actual_start::date ELSE actual_finish::date END AS actual_event_date,
 CASE WHEN checkpoint_key='conflict' THEN NULL
   WHEN event_basis='start' THEN baseline_start::date ELSE baseline_finish::date END AS baseline_event_date,
 CASE WHEN current_task_id IS NULL OR match_status IN('missing_activity_id','ambiguous_activity_id') OR checkpoint_key='conflict'
   THEN NULL WHEN event_basis='start' THEN actual_start IS NOT NULL
   ELSE current_status='TK_Complete' END AS checkpoint_reached,
 CASE WHEN previous_task_id IS NULL THEN NULL WHEN event_basis='start' THEN previous_actual_start IS NOT NULL
   ELSE previous_status='TK_Complete' END AS previous_checkpoint_reached
 FROM joined
), populations AS (
 SELECT *,CASE WHEN NOT is_comparable OR match_status IN('missing_activity_id','ambiguous_activity_id')
   OR previous_task_id IS NULL OR checkpoint_key='conflict' THEN NULL
 WHEN NOT previous_has_design_key THEN false WHEN planned_event_date IS NULL THEN NULL
 ELSE planned_event_date>=period_start AND planned_event_date<period_end END AS is_planned,
 CASE WHEN baseline_status<>'ready' OR baseline_task_id IS NULL OR baseline_code_count<>1
   OR baseline_event_date IS NULL OR cut_date IS NULL OR checkpoint_key='conflict'
   OR match_status IN('missing_activity_id','ambiguous_activity_id') THEN NULL
 ELSE baseline_event_date<cut_date END AS baseline_planned
 FROM events
), outcomes AS (
 SELECT *,is_planned AND checkpoint_reached AS is_achieved,
 is_planned AND NOT checkpoint_reached AS is_missed,
 CASE WHEN NOT is_comparable OR match_status IN('missing_activity_id','ambiguous_activity_id')
   OR checkpoint_key='conflict' THEN NULL
 WHEN checkpoint_reached IS FALSE THEN false
 WHEN checkpoint_reached IS NULL OR actual_event_date IS NULL THEN NULL
 WHEN actual_event_date<period_start OR actual_event_date>=period_end THEN false
 WHEN previous_task_id IS NULL OR previous_checkpoint_reached IS NULL OR planned_event_date IS NULL THEN NULL
 ELSE NOT previous_checkpoint_reached AND planned_event_date>=period_end END AS is_unplanned,
 baseline_planned AND checkpoint_reached AS baseline_achieved,
 baseline_planned AND NOT checkpoint_reached AS baseline_missed,
 CASE WHEN baseline_status<>'ready' OR baseline_code_count>1 OR cut_date IS NULL
   OR match_status IN('missing_activity_id','ambiguous_activity_id') OR checkpoint_key='conflict' THEN NULL
 WHEN checkpoint_reached IS FALSE THEN false
 WHEN checkpoint_reached IS NULL OR actual_event_date IS NULL THEN NULL
 WHEN actual_event_date>=cut_date THEN false
 WHEN baseline_task_id IS NULL THEN true
 WHEN baseline_event_date IS NULL THEN NULL ELSE baseline_event_date>=cut_date END AS baseline_unplanned,
 NOT checkpoint_reached AS is_remaining
 FROM populations
)
SELECT *,is_planned IS TRUE AND checkpoint_reached IS NULL AS unknown_planned,
 baseline_planned IS TRUE AND checkpoint_reached IS NULL AS unknown_baseline_planned,
 CASE WHEN is_remaining AND is_comparable AND match_status='matched' AND checkpoint_key<>'conflict'
 THEN (current_event_date-planned_event_date)::numeric END AS previous_movement_days,
 CASE WHEN is_remaining AND baseline_status='ready' AND baseline_task_id IS NOT NULL AND baseline_code_count=1
   AND match_status NOT IN('missing_activity_id','ambiguous_activity_id') AND checkpoint_key<>'conflict'
 THEN (current_event_date-baseline_event_date)::numeric END AS baseline_movement_days,
 CASE WHEN is_activity_complete IS FALSE AND is_comparable AND match_status='matched'
 THEN (current_finish::date-previous_finish::date)::numeric END AS finish_movement_days,
 CASE WHEN current_task_id IS NULL THEN 'missing_current_activity'
 WHEN match_status IN('missing_activity_id','ambiguous_activity_id') THEN match_status
 WHEN checkpoint_key='conflict' THEN 'conflicting_design_key'
 WHEN current_event_date IS NULL THEN 'missing_current_event_date'
 WHEN is_comparable AND previous_task_id IS NULL THEN 'missing_previous_activity'
 WHEN is_comparable AND planned_event_date IS NULL THEN 'missing_previous_event_date'
 ELSE 'ok' END AS activity_issue
FROM outcomes;

CREATE OR REPLACE VIEW reporting.design_overview_baselines AS
SELECT b.*,s.baseline_proj_id AS selected_baseline_proj_id,s.baseline_status,
 b.proj_id=s.baseline_proj_id AS is_current_baseline
FROM design_overview.baseline_candidates b
LEFT JOIN design_overview.baseline_selection s USING(project_code_id);

CREATE OR REPLACE VIEW reporting.design_overview_performance_detail AS
WITH facts AS MATERIALIZED (SELECT * FROM design_overview.performance_detail)
SELECT *,previous_codes->'design_key'->>'code_value' AS previous_design_key,
 current_codes->'design_key'->>'code_value' AS current_design_key,
 current_codes->'discipline'->>'code_value' AS discipline,
 current_codes->'responsible_company'->>'code_value' AS responsible_company,
 current_codes->'project_stage'->>'code_value' AS project_stage,
 current_codes->'milestone_type'->>'code_value' AS milestone_type,
 CASE WHEN is_remaining IS NOT TRUE THEN NULL
 WHEN previous_movement_days IS NULL THEN 'new_or_no_prior'
 WHEN previous_movement_days < -30 THEN 'earlier_over_30'
 WHEN previous_movement_days < 0 THEN 'earlier_1_to_30'
 WHEN previous_movement_days=0 THEN 'unchanged'
 WHEN previous_movement_days<=30 THEN 'later_1_to_30' ELSE 'later_over_30' END AS movement_band
FROM facts;


CREATE OR REPLACE VIEW reporting.design_overview_performance_code_counts AS
WITH facts AS MATERIALIZED (SELECT * FROM reporting.design_overview_performance_detail),
rows AS (
 SELECT f.*,d.code_dimension,b.code_basis,
 (j.code->>'actv_code_type_id')::integer AS actv_code_type_id,
 (j.code->>'actv_code_id')::integer AS actv_code_id,
 coalesce(j.code->>'code_value','No activity in submission') AS code_value,
 coalesce(j.code->>'bucket_status','unavailable') AS bucket_status
 FROM facts f CROSS JOIN coding_check.dimensions d CROSS JOIN (VALUES('plan'),('outcome')) b(code_basis)
 CROSS JOIN LATERAL (SELECT (CASE b.code_basis WHEN 'plan' THEN previous_codes ELSE current_codes END)->d.code_dimension AS code) j
), grouped AS (
 SELECT proj_id,code_dimension,code_basis,actv_code_type_id,actv_code_id,code_value,bucket_status,
 count(*) FILTER(WHERE is_planned) AS planned_count,
 count(*) FILTER(WHERE is_planned IS NULL) AS unknown_plan_count,
 count(*) FILTER(WHERE is_achieved) AS achieved_count,
 count(*) FILTER(WHERE is_achieved IS NULL) AS unknown_achieved_count,
 count(*) FILTER(WHERE is_missed) AS missed_count,
 count(*) FILTER(WHERE is_missed IS NULL) AS unknown_missed_count,
 count(*) FILTER(WHERE is_unplanned) AS unplanned_count,
 count(*) FILTER(WHERE is_unplanned IS NULL) AS unknown_unplanned_count,
 count(*) FILTER(WHERE baseline_planned) AS baseline_planned_count,
 count(*) FILTER(WHERE baseline_planned IS NULL) AS unknown_baseline_plan_count,
 count(*) FILTER(WHERE baseline_achieved) AS baseline_achieved_count,
 count(*) FILTER(WHERE baseline_achieved IS NULL) AS unknown_baseline_achieved_count,
 count(*) FILTER(WHERE baseline_missed) AS baseline_missed_count,
 count(*) FILTER(WHERE baseline_missed IS NULL) AS unknown_baseline_missed_count,
 count(*) FILTER(WHERE baseline_unplanned) AS baseline_unplanned_count,
 count(*) FILTER(WHERE baseline_unplanned IS NULL) AS unknown_baseline_unplanned_count,
 count(*) FILTER(WHERE is_remaining) AS remaining_count,
 count(*) FILTER(WHERE is_remaining IS NULL) AS unknown_remaining_count,
 count(*) AS detail_activity_count,
 count(*) FILTER(WHERE unknown_planned) AS unknown_planned_count,
 count(*) FILTER(WHERE unknown_baseline_planned) AS unknown_baseline_planned_count,
 avg(previous_movement_days) AS previous_movement_days,
 avg(baseline_movement_days) AS baseline_movement_days,
 count(previous_movement_days) AS previous_movement_comparable_count,
 count(baseline_movement_days) AS baseline_movement_comparable_count,
 count(*) FILTER(WHERE movement_band='earlier_over_30') AS earlier_over_30_count,
 count(*) FILTER(WHERE movement_band='earlier_1_to_30') AS earlier_1_to_30_count,
 count(*) FILTER(WHERE movement_band='unchanged') AS unchanged_count,
 count(*) FILTER(WHERE movement_band='later_1_to_30') AS later_1_to_30_count,
 count(*) FILTER(WHERE movement_band='later_over_30') AS later_over_30_count,
 count(*) FILTER(WHERE movement_band='new_or_no_prior') AS new_or_no_prior_count
 FROM rows GROUP BY 1,2,3,4,5,6,7
)
SELECT g.proj_id,
 g.code_dimension,
 g.code_basis,
 g.actv_code_type_id,
 g.actv_code_id,
 g.code_value,
 g.bucket_status,
 CASE WHEN s.is_comparable THEN g.planned_count END AS planned_count,
 g.unknown_plan_count,
 CASE WHEN s.is_comparable THEN g.achieved_count END AS achieved_count,
 g.unknown_achieved_count,
 CASE WHEN s.is_comparable THEN g.missed_count END AS missed_count,
 g.unknown_missed_count,
 CASE WHEN s.is_comparable THEN g.unplanned_count END AS unplanned_count,
 g.unknown_unplanned_count,
 CASE WHEN s.baseline_status='ready' THEN g.baseline_planned_count END AS baseline_planned_count,
 g.unknown_baseline_plan_count,
 CASE WHEN s.baseline_status='ready' THEN g.baseline_achieved_count END AS baseline_achieved_count,
 g.unknown_baseline_achieved_count,
 CASE WHEN s.baseline_status='ready' THEN g.baseline_missed_count END AS baseline_missed_count,
 g.unknown_baseline_missed_count,
 CASE WHEN s.baseline_status='ready' THEN g.baseline_unplanned_count END AS baseline_unplanned_count,
 g.unknown_baseline_unplanned_count,
 g.remaining_count,
 g.unknown_remaining_count,
 g.detail_activity_count,
 g.unknown_planned_count,
 g.unknown_baseline_planned_count,
 g.previous_movement_days,
 g.baseline_movement_days,
 g.previous_movement_comparable_count,
 g.baseline_movement_comparable_count,
 g.earlier_over_30_count,
 g.earlier_1_to_30_count,
 g.unchanged_count,
 g.later_1_to_30_count,
 g.later_over_30_count,
 g.new_or_no_prior_count,s.full_period_code,s.submission_index,s.comparison_status,s.baseline_status,s.baseline_proj_id,
 CASE WHEN s.is_comparable AND g.unknown_plan_count=0 AND g.unknown_planned_count=0 AND g.planned_count>0 AND g.unknown_achieved_count=0
 THEN g.achieved_count::numeric/g.planned_count END AS completion_percent,
 s."Project ID",s."Lead Planner",s."Account",s._snapshot_at
FROM grouped g JOIN design_overview.submissions s USING(proj_id);

CREATE OR REPLACE VIEW design_overview.checkpoints AS
SELECT DISTINCT code_short_name AS checkpoint_key,code_value AS checkpoint_label
FROM coding_check.code_values WHERE _source_system='p6_22' AND code_dimension='design_key'
UNION SELECT 'all','All Design Activities'
UNION SELECT 'conflict','Multiple Design Key assignments';

CREATE OR REPLACE VIEW reporting.design_overview_checkpoint_summary AS
WITH facts AS MATERIALIZED (SELECT * FROM reporting.design_overview_performance_detail),
rows AS (
 SELECT f.*,k.key FROM facts f CROSS JOIN LATERAL (VALUES(f.checkpoint_key),('all')) k(key)
), grouped AS (
 SELECT proj_id,key,count(*) FILTER(WHERE is_planned) AS planned_count,
 count(*) FILTER(WHERE is_planned IS NULL) AS unknown_plan_count,
 count(*) FILTER(WHERE is_achieved) AS achieved_count,
 count(*) FILTER(WHERE is_achieved IS NULL) AS unknown_achieved_count,
 count(*) FILTER(WHERE is_missed) AS missed_count,
 count(*) FILTER(WHERE is_missed IS NULL) AS unknown_missed_count,
 count(*) FILTER(WHERE is_unplanned) AS unplanned_count,
 count(*) FILTER(WHERE is_unplanned IS NULL) AS unknown_unplanned_count,
 count(*) FILTER(WHERE baseline_planned) AS baseline_planned_count,
 count(*) FILTER(WHERE baseline_planned IS NULL) AS unknown_baseline_plan_count,
 count(*) FILTER(WHERE baseline_achieved) AS baseline_achieved_count,
 count(*) FILTER(WHERE baseline_achieved IS NULL) AS unknown_baseline_achieved_count,
 count(*) FILTER(WHERE baseline_missed) AS baseline_missed_count,
 count(*) FILTER(WHERE baseline_missed IS NULL) AS unknown_baseline_missed_count,
 count(*) FILTER(WHERE baseline_unplanned) AS baseline_unplanned_count,
 count(*) FILTER(WHERE baseline_unplanned IS NULL) AS unknown_baseline_unplanned_count,
 count(*) FILTER(WHERE is_remaining) AS remaining_count,
 count(*) FILTER(WHERE is_remaining IS NULL) AS unknown_remaining_count,
 count(*) AS detail_activity_count,
 count(*) FILTER(WHERE unknown_planned) AS unknown_planned_count,
 count(*) FILTER(WHERE unknown_baseline_planned) AS unknown_baseline_planned_count,
 avg(previous_movement_days) AS previous_movement_days,
 avg(baseline_movement_days) AS baseline_movement_days,
 count(previous_movement_days) AS previous_movement_comparable_count,
 count(baseline_movement_days) AS baseline_movement_comparable_count,
 count(*) FILTER(WHERE movement_band='earlier_over_30') AS earlier_over_30_count,
 count(*) FILTER(WHERE movement_band='earlier_1_to_30') AS earlier_1_to_30_count,
 count(*) FILTER(WHERE movement_band='unchanged') AS unchanged_count,
 count(*) FILTER(WHERE movement_band='later_1_to_30') AS later_1_to_30_count,
 count(*) FILTER(WHERE movement_band='later_over_30') AS later_over_30_count,
 count(*) FILTER(WHERE movement_band='new_or_no_prior') AS new_or_no_prior_count FROM rows GROUP BY 1,2
)
SELECT s.proj_id,s.proj_short_name,s.project_code,s."Project ID",s."Account",s."Lead Planner",
 s.full_period_code,s.period_start,s.period_end,s.cut_date,s.submission_index,s.global_submission_index,
 s.history_rank,s.previous_proj_id,s.baseline_proj_id,s.baseline_period_code,s.baseline_status,
 s.comparison_status,s.is_comparable,k.checkpoint_key,k.checkpoint_label,
 CASE WHEN s.is_comparable THEN coalesce(g.planned_count,0) END AS planned_count,
 CASE WHEN s.is_comparable THEN coalesce(g.achieved_count,0) END AS achieved_count,
 CASE WHEN s.is_comparable THEN coalesce(g.missed_count,0) END AS missed_count,
 CASE WHEN s.is_comparable THEN coalesce(g.unplanned_count,0) END AS unplanned_count,
 CASE WHEN s.baseline_status='ready' THEN coalesce(g.baseline_planned_count,0) END AS baseline_planned_count,
 CASE WHEN s.baseline_status='ready' THEN coalesce(g.baseline_achieved_count,0) END AS baseline_achieved_count,
 CASE WHEN s.baseline_status='ready' THEN coalesce(g.baseline_missed_count,0) END AS baseline_missed_count,
 CASE WHEN s.baseline_status='ready' THEN coalesce(g.baseline_unplanned_count,0) END AS baseline_unplanned_count,
 coalesce(g.remaining_count,0) AS remaining_count,coalesce(g.detail_activity_count,0) AS detail_activity_count,
 coalesce(g.unknown_plan_count,0) AS unknown_plan_count,
 coalesce(g.unknown_planned_count,0) AS unknown_planned_count,
 coalesce(g.unknown_achieved_count,0) AS unknown_achieved_count,
 coalesce(g.unknown_unplanned_count,0) AS unknown_unplanned_count,
 coalesce(g.unknown_baseline_plan_count,0) AS unknown_baseline_plan_count,
 coalesce(g.unknown_baseline_planned_count,0) AS unknown_baseline_planned_count,
 coalesce(g.unknown_baseline_achieved_count,0) AS unknown_baseline_achieved_count,
 CASE WHEN s.is_comparable AND coalesce(g.unknown_plan_count,0)=0 AND coalesce(g.unknown_planned_count,0)=0
   AND coalesce(g.unknown_achieved_count,0)=0 AND g.planned_count>0
 THEN g.achieved_count::numeric/g.planned_count END AS completion_percent,
 CASE WHEN s.baseline_status='ready' AND coalesce(g.unknown_baseline_plan_count,0)=0 AND coalesce(g.unknown_baseline_planned_count,0)=0
   AND coalesce(g.unknown_baseline_achieved_count,0)=0 AND g.baseline_planned_count>0
 THEN g.baseline_achieved_count::numeric/g.baseline_planned_count END AS baseline_completion_percent,
 g.previous_movement_days,g.baseline_movement_days,
 coalesce(g.previous_movement_comparable_count,0) AS previous_movement_comparable_count,
 coalesce(g.baseline_movement_comparable_count,0) AS baseline_movement_comparable_count,
 coalesce(g.earlier_over_30_count,0) AS earlier_over_30_count,
 coalesce(g.earlier_1_to_30_count,0) AS earlier_1_to_30_count,
 coalesce(g.unchanged_count,0) AS unchanged_count,
 coalesce(g.later_1_to_30_count,0) AS later_1_to_30_count,
 coalesce(g.later_over_30_count,0) AS later_over_30_count,
 coalesce(g.new_or_no_prior_count,0) AS new_or_no_prior_count,s._snapshot_at,
 coalesce(g.unknown_remaining_count,0) AS unknown_remaining_count
FROM design_overview.submissions s CROSS JOIN design_overview.checkpoints k
LEFT JOIN grouped g ON g.proj_id=s.proj_id AND g.key=k.checkpoint_key;

CREATE OR REPLACE VIEW reporting.design_overview_history AS
SELECT * FROM reporting.design_overview_checkpoint_summary
WHERE history_rank<=5 AND full_period_code IS NOT NULL;

CREATE OR REPLACE VIEW reporting.design_overview_future_activity_detail AS
WITH facts AS MATERIALIZED (SELECT * FROM reporting.design_overview_performance_detail)
SELECT f.proj_id,f.proj_short_name,f.task_code,f.current_task_id AS task_id,f.task_name,
 f.project_code,f."Project ID",f."Account",f."Lead Planner",f.full_period_code,
 f.checkpoint_key,f.current_design_key,f.event_basis,f.current_event_date,f.current_start,f.current_finish,
 f.current_status,f.current_codes,f.discipline,f.responsible_company,f.project_stage,
 w.offset_number AS future_period_offset,f.cut_date+28*w.offset_number AS window_start,
 f.cut_date+28*(w.offset_number+1) AS window_end,f._snapshot_at
FROM facts f CROSS JOIN generate_series(0,2) w(offset_number)
WHERE f.current_task_id IS NOT NULL AND f.current_has_design_key AND f.is_remaining
 AND f.match_status NOT IN('missing_activity_id','ambiguous_activity_id') AND f.checkpoint_key<>'conflict'
 AND f.period_serial IS NOT NULL AND f.submission_count=1
 AND f.current_event_date>=f.cut_date+28*w.offset_number
 AND f.current_event_date<f.cut_date+28*(w.offset_number+1);

CREATE OR REPLACE VIEW reporting.design_overview_future_code_counts AS
WITH facts AS MATERIALIZED (SELECT * FROM reporting.design_overview_future_activity_detail),
counts AS (
 SELECT f.proj_id,f.future_period_offset,k.key,count(*) AS planned_count
 FROM facts f CROSS JOIN LATERAL (VALUES(f.checkpoint_key),('all')) k(key) GROUP BY 1,2,3
)
SELECT s.proj_id,s.proj_short_name,s.full_period_code,s.submission_index,k.checkpoint_key,k.checkpoint_label,
 w.offset_number AS future_period_offset,s.cut_date+28*w.offset_number AS window_start,
 s.cut_date+28*(w.offset_number+1) AS window_end,
 s.cut_date IS NOT NULL AND s.period_serial IS NOT NULL AND s.submission_count=1 AS is_available,
 CASE WHEN s.cut_date IS NOT NULL AND s.period_serial IS NOT NULL AND s.submission_count=1
 THEN coalesce(c.planned_count,0) END AS planned_count,s._snapshot_at
FROM design_overview.submissions s CROSS JOIN design_overview.checkpoints k
CROSS JOIN generate_series(0,2) w(offset_number)
LEFT JOIN counts c ON c.proj_id=s.proj_id AND c.future_period_offset=w.offset_number AND c.key=k.checkpoint_key;

CREATE OR REPLACE VIEW reporting.design_overview_issues AS
SELECT s.proj_id,NULL::text AS task_code,s.comparison_status AS issue_type,
 jsonb_build_object('period',s.full_period_code,'previous_proj_id',s.previous_proj_id) AS details
FROM design_overview.submissions s WHERE NOT s.is_comparable
UNION ALL
SELECT s.proj_id,NULL::text,s.baseline_status,jsonb_build_object('baseline_period',s.baseline_period_code)
FROM design_overview.submissions s WHERE s.baseline_status<>'ready'
UNION ALL
SELECT f.proj_id,f.task_code,f.activity_issue,jsonb_build_object('previous_task_id',f.previous_task_id,'current_task_id',f.current_task_id)
FROM reporting.design_overview_performance_detail f WHERE f.activity_issue<>'ok'
UNION ALL
SELECT f.proj_id,f.task_code,'missing_or_ambiguous_baseline_activity',jsonb_build_object('baseline_proj_id',f.baseline_proj_id,'baseline_code_count',f.baseline_code_count)
FROM reporting.design_overview_performance_detail f
WHERE f.baseline_status='ready' AND (f.baseline_task_id IS NULL OR f.baseline_code_count<>1 OR f.baseline_event_date IS NULL);

GRANT USAGE ON SCHEMA reporting TO p6_reporting_reader;
GRANT SELECT ON reporting.design_overview_submissions,reporting.design_overview_baselines,reporting.design_overview_activity_detail,reporting.design_overview_activity_code_counts,reporting.design_overview_performance_detail,reporting.design_overview_performance_code_counts,reporting.design_overview_checkpoint_summary,reporting.design_overview_history,reporting.design_overview_future_activity_detail,reporting.design_overview_future_code_counts,reporting.design_overview_issues TO p6_reporting_reader;
GRANT SELECT ON reporting.design_overview_submissions,reporting.design_overview_baselines,reporting.design_overview_activity_detail,reporting.design_overview_activity_code_counts,reporting.design_overview_performance_detail,reporting.design_overview_performance_code_counts,reporting.design_overview_checkpoint_summary,reporting.design_overview_history,reporting.design_overview_future_activity_detail,reporting.design_overview_future_code_counts,reporting.design_overview_issues TO p6_reporting_sync WITH GRANT OPTION;
COMMENT ON VIEW reporting.design_overview_performance_detail IS '2026-11-04 rules: latest separate BL/Baseline by full Period Code, Activity ID matching, D-Start Actual Start achieves checkpoint. Calendar-day [L,U). NULL flags preserve unknowns. Current latest baseline is applied to every cut.';
COMMENT ON VIEW reporting.design_overview_checkpoint_summary IS 'One cut/checkpoint plus inclusive all. Achieved is planned AND checkpoint reached. Percentages are fractions, unknown populations yield NULL. Do not sum the all row with checkpoint rows.';
COMMENT ON VIEW reporting.design_overview_performance_code_counts IS 'Filter one code_dimension and one code_basis (plan or outcome). Metrics are additive only within that selection. Unknown counts remain separate; do not sum bases or dimensions.';
COMMIT;
