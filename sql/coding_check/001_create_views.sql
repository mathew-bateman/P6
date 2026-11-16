-- Coding Check Dashboard Spec, 29 October 2026.
-- Additive PostgreSQL views over the stable, atomically published P6 snapshots.
-- Coding Check scope is P6 22 only (user request, 29 October 2026).
-- Run with psql -X -v ON_ERROR_STOP=1. No live P6 SQL objects are changed.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';

CREATE SCHEMA IF NOT EXISTS coding_check;

CREATE OR REPLACE FUNCTION coding_check.clean_text(value text) RETURNS text
LANGUAGE sql IMMUTABLE PARALLEL SAFE RETURNS NULL ON NULL INPUT AS $$
  SELECT nullif(regexp_replace(value, '^[[:space:]]+|[[:space:]]+$', '', 'g'), '')
$$;

CREATE OR REPLACE VIEW coding_check.dimensions AS
SELECT * FROM (VALUES
  ('project_stage', 'Project Stage', 1),
  ('discipline', 'Discipline', 2),
  ('design_key', 'Design Key', 3),
  ('milestone_type', 'Milestone Type', 4),
  ('responsible_company', 'Responsible Company', 5)
) AS d(code_dimension, code_type_name, dimension_order);

-- Keep project-code filters separate from the actual P6 programme identifier.
-- Ambiguous filter values remain NULL and are explicitly flagged, never MAX'd
-- into an arbitrary classification. Ambiguous state/status is not eligible.
CREATE OR REPLACE VIEW coding_check.projects AS
WITH assignments AS (
  SELECT DISTINCT a._source_system, a.proj_id,
    lower(btrim(t.proj_catg_type)) AS code_type,
    v.proj_catg_id,
    coalesce(coding_check.clean_text(v.proj_catg_name),
             coding_check.clean_text(v.proj_catg_short_name)) AS code_value,
    coding_check.clean_text(v.proj_catg_short_name) AS short_value
  FROM reporting.projpcat a
  JOIN reporting.pcattype t ON t._source_system = a._source_system
    AND t.proj_catg_type_id = a.proj_catg_type_id
  JOIN reporting.pcatval v ON v._source_system = a._source_system
    AND v.proj_catg_type_id = a.proj_catg_type_id
    AND v.proj_catg_id = a.proj_catg_id
  WHERE a._source_system = 'p6_22'
    AND a.delete_date IS NULL AND a.delete_session_id IS NULL
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
    AND v.delete_date IS NULL AND v.delete_session_id IS NULL
    AND lower(btrim(t.proj_catg_type)) IN
      ('project id', 'account', 'lead planner', 'period', 'project state', 'project status')
), per_type AS (
  SELECT _source_system, proj_id, code_type, count(*) AS value_count,
    CASE WHEN count(*) = 1 THEN min(code_value) END AS code_value,
    CASE WHEN count(*) = 1 THEN min(short_value) END AS short_value
  FROM assignments GROUP BY _source_system, proj_id, code_type
), filters AS (
  SELECT _source_system, proj_id,
    max(code_value) FILTER (WHERE code_type = 'project id') AS project_id,
    max(code_value) FILTER (WHERE code_type = 'account') AS account,
    max(code_value) FILTER (WHERE code_type = 'lead planner') AS lead_planner,
    max(code_value) FILTER (WHERE code_type = 'period') AS period,
    max(short_value) FILTER (WHERE code_type = 'period') AS period_code,
    max(code_value) FILTER (WHERE code_type = 'project state') AS project_state,
    max(code_value) FILTER (WHERE code_type = 'project status') AS project_status,
    count(*) FILTER (WHERE value_count > 1) AS project_code_conflict_count
  FROM per_type GROUP BY _source_system, proj_id
)
SELECT p._source_system, p._source_database, p.proj_id, p.proj_short_name,
  f.project_id AS "Project ID", f.account AS "Account",
  f.lead_planner AS "Lead Planner", f.period AS "Period",
  f.period_code AS "Period Code", f.project_state AS "Project State",
  f.project_status AS "Project Status", f.project_code_conflict_count,
  p._project_key, p._snapshot_at, p._synced_at, p._batch_id
FROM reporting.project p
JOIN filters f ON f._source_system = p._source_system AND f.proj_id = p.proj_id
WHERE p._source_system = 'p6_22'
  AND p.project_flag = 'Y'
  AND p.delete_date IS NULL AND p.delete_session_id IS NULL
  AND lower(f.project_state) = 'live'
  AND lower(f.project_status) = 'client submitted';

-- All activity types and completion states are included. Physical deletions and
-- structured Activity Status = DEL assignments are excluded in every code scope.
CREATE OR REPLACE VIEW coding_check.activities AS
WITH retired AS (
  SELECT DISTINCT a._source_system, a.proj_id, a.task_id
  FROM reporting.taskactv a
  JOIN reporting.actvtype t ON t._source_system = a._source_system
    AND t.actv_code_type_id = a.actv_code_type_id
  JOIN reporting.actvcode v ON v._source_system = a._source_system
    AND v.actv_code_type_id = a.actv_code_type_id AND v.actv_code_id = a.actv_code_id
  WHERE a.delete_date IS NULL AND a.delete_session_id IS NULL
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
    AND v.delete_date IS NULL AND v.delete_session_id IS NULL
    AND lower(btrim(t.actv_code_type)) = 'activity status'
    AND upper(btrim(v.short_name)) = 'DEL'
)
SELECT t._source_system, t.proj_id, t.task_id, t.task_code, t.task_name,
  t.total_float_hr_cnt, t.total_float_hr_cnt / 8.0 AS total_float_days,
  t.driving_path_flag, t._project_key, t._activity_key
FROM reporting.task t
JOIN coding_check.projects p ON p._source_system = t._source_system AND p.proj_id = t.proj_id
LEFT JOIN retired r ON r._source_system = t._source_system
  AND r.proj_id = t.proj_id AND r.task_id = t.task_id
WHERE t.delete_date IS NULL AND t.delete_session_id IS NULL AND r.task_id IS NULL;

-- Resolve by name on every query, so future types/values require no SQL edits.
-- Only P6 22 dictionaries are included in the Coding Check scope.
CREATE OR REPLACE VIEW coding_check.code_types AS
SELECT s._source_system, d.code_dimension, d.code_type_name, d.dimension_order,
  t.actv_code_type_id, t.actv_code_type AS source_code_type_name
FROM (SELECT DISTINCT _source_system FROM reporting.project WHERE _source_system = 'p6_22') s
CROSS JOIN coding_check.dimensions d
LEFT JOIN reporting.actvtype t ON t._source_system = s._source_system
  AND t.actv_code_type_scope = 'AS_Global'
  AND t.delete_date IS NULL AND t.delete_session_id IS NULL
  AND lower(btrim(t.actv_code_type)) = lower(d.code_type_name);

CREATE OR REPLACE VIEW coding_check.code_values AS
WITH named_values AS (
  SELECT t._source_system, t.code_dimension, t.code_type_name,
    t.actv_code_type_id, t.source_code_type_name, v.actv_code_id,
    coding_check.clean_text(v.short_name) AS code_short_name,
    coalesce(coding_check.clean_text(v.actv_code_name),
             coding_check.clean_text(v.short_name)) AS code_raw_value,
    v.seq_num AS code_sort_order
  FROM coding_check.code_types t
  JOIN reporting.actvcode v ON v._source_system = t._source_system
    AND v.actv_code_type_id = t.actv_code_type_id
  WHERE v.delete_date IS NULL AND v.delete_session_id IS NULL
)
SELECT v.*,
  CASE WHEN code_dimension = 'project_stage' THEN
    coalesce(coding_check.clean_text(regexp_replace(code_raw_value,
      '^ES[0-9]+[[:alpha:]]*([[:space:]]*/[[:space:]]*ES[0-9]+[[:alpha:]]*)*[[:space:]]*[-–—][[:space:]]*', '', 'i')), code_raw_value)
    ELSE code_raw_value END AS code_value
FROM named_values v WHERE code_raw_value IS NOT NULL;

-- One row per activity and dimension. DISTINCT protects against duplicated
-- assignment rows; conflicting valid values get their own visible bucket.
CREATE OR REPLACE VIEW coding_check.activity_coding AS
WITH scoped_activities AS MATERIALIZED (
  SELECT * FROM coding_check.activities
), candidate_assignments AS MATERIALIZED (
  SELECT DISTINCT a._source_system, a.proj_id, a.task_id, v.code_dimension,
    v.actv_code_type_id, v.actv_code_id
  FROM reporting.taskactv a
  JOIN coding_check.code_values v ON v._source_system = a._source_system
    AND v.actv_code_type_id = a.actv_code_type_id AND v.actv_code_id = a.actv_code_id
  WHERE a.delete_date IS NULL AND a.delete_session_id IS NULL
), valid AS (
  SELECT a.* FROM candidate_assignments a
  JOIN scoped_activities t ON t._source_system = a._source_system
    AND t.proj_id = a.proj_id AND t.task_id = a.task_id
), per_activity AS (
  SELECT _source_system, proj_id, task_id, code_dimension, count(*) AS valid_value_count,
    CASE WHEN count(*) = 1 THEN min(actv_code_type_id) END AS actv_code_type_id,
    CASE WHEN count(*) = 1 THEN min(actv_code_id) END AS actv_code_id
  FROM valid GROUP BY _source_system, proj_id, task_id, code_dimension
)
SELECT t._source_system, t.proj_id, t.task_id, t._project_key, t._activity_key,
  d.code_dimension, d.code_type_name, a.actv_code_type_id, a.actv_code_id,
  coalesce(a.valid_value_count, 0) AS valid_value_count,
  CASE WHEN a.valid_value_count > 1 THEN 'conflict'
    WHEN a.valid_value_count = 1 THEN 'coded' ELSE 'uncoded' END AS bucket_status
FROM scoped_activities t CROSS JOIN coding_check.dimensions d
LEFT JOIN per_activity a ON a._source_system = t._source_system
  AND a.proj_id = t.proj_id AND a.task_id = t.task_id AND a.code_dimension = d.code_dimension;

CREATE OR REPLACE VIEW coding_check.code_counts AS
WITH counts AS (
  SELECT _source_system, proj_id, code_dimension, actv_code_type_id, actv_code_id,
    bucket_status, count(*) AS activity_count
  FROM coding_check.activity_coding
  GROUP BY _source_system, proj_id, code_dimension, actv_code_type_id, actv_code_id, bucket_status
), types AS (
  SELECT _source_system, code_dimension, code_type_name,
    bool_or(actv_code_type_id IS NOT NULL) AS code_type_exists,
    string_agg(DISTINCT source_code_type_name, '; ' ORDER BY source_code_type_name) AS source_code_type_names
  FROM coding_check.code_types GROUP BY _source_system, code_dimension, code_type_name
), totals AS (
  SELECT _source_system, proj_id, count(*) AS project_activity_count
  FROM coding_check.activities GROUP BY _source_system, proj_id
), buckets AS (
  SELECT t._source_system, t.code_dimension, t.code_type_name, t.code_type_exists,
    t.source_code_type_names, v.actv_code_type_id, v.actv_code_id,
    v.code_short_name, v.code_raw_value, v.code_value, v.code_sort_order, 'coded'::text AS bucket_status
  FROM types t JOIN coding_check.code_values v USING (_source_system, code_dimension, code_type_name)
  UNION ALL
  SELECT t._source_system, t.code_dimension, t.code_type_name, t.code_type_exists,
    t.source_code_type_names, NULL::integer, NULL::integer, NULL::text, NULL::text,
    b.code_value, b.code_sort_order, b.bucket_status
  FROM types t CROSS JOIN (VALUES
    ('No codes / Blank'::text, 2147483646, 'uncoded'::text),
    ('Multiple code assignments'::text, 2147483647, 'conflict'::text)
  ) b(code_value, code_sort_order, bucket_status)
)
SELECT p._source_system, p._source_database, p.proj_id, p.proj_short_name,
  p."Project ID", p."Account", p."Lead Planner", p."Period", p."Period Code",
  p."Project State", p."Project Status", p.project_code_conflict_count,
  b.code_dimension, b.code_type_name, b.source_code_type_names,
  b.actv_code_type_id, b.actv_code_id, b.code_short_name, b.code_raw_value,
  b.code_value, b.code_sort_order, b.bucket_status,
  b.bucket_status = 'uncoded' AS is_uncoded,
  coalesce(c.activity_count, 0::bigint) AS activity_count,
  coalesce(t.project_activity_count, 0::bigint) AS project_activity_count,
  b.code_type_exists, p._project_key, p._snapshot_at, p._synced_at, p._batch_id
FROM coding_check.projects p
JOIN buckets b ON b._source_system = p._source_system
LEFT JOIN totals t ON t._source_system = p._source_system AND t.proj_id = p.proj_id
LEFT JOIN counts c ON c._source_system = p._source_system AND c.proj_id = p.proj_id
  AND c.code_dimension = b.code_dimension AND c.bucket_status = b.bucket_status
  AND c.actv_code_type_id IS NOT DISTINCT FROM b.actv_code_type_id
  AND c.actv_code_id IS NOT DISTINCT FROM b.actv_code_id
WHERE b.bucket_status <> 'conflict' OR coalesce(c.activity_count, 0) > 0;

-- Explicit public projections omit source-system/database and project-key metadata.

CREATE OR REPLACE VIEW reporting.coding_check_activity_code_counts AS
  SELECT proj_id, proj_short_name, "Project ID", "Account", "Lead Planner", "Period", "Period Code",
    "Project State", "Project Status",
    code_dimension, code_type_name, source_code_type_names,
    actv_code_type_id, actv_code_id, code_short_name, code_raw_value,
    code_value, code_sort_order, bucket_status, is_uncoded,
    activity_count, project_activity_count, code_type_exists,
    _snapshot_at
  FROM coding_check.code_counts;

CREATE OR REPLACE VIEW reporting.coding_check_project_stage AS
  SELECT proj_id, proj_short_name, "Project ID", "Account", "Lead Planner", "Period", "Period Code",
    "Project State", "Project Status",
    code_dimension, code_type_name, source_code_type_names,
    actv_code_type_id, actv_code_id, code_short_name, code_raw_value,
    code_value, code_sort_order, bucket_status, is_uncoded,
    activity_count, project_activity_count, code_type_exists,
    _snapshot_at
  FROM coding_check.code_counts WHERE code_dimension = 'project_stage';

CREATE OR REPLACE VIEW reporting.coding_check_discipline AS
  SELECT proj_id, proj_short_name, "Project ID", "Account", "Lead Planner", "Period", "Period Code",
    "Project State", "Project Status",
    code_dimension, code_type_name, source_code_type_names,
    actv_code_type_id, actv_code_id, code_short_name, code_raw_value,
    code_value, code_sort_order, bucket_status, is_uncoded,
    activity_count, project_activity_count, code_type_exists,
    _snapshot_at
  FROM coding_check.code_counts WHERE code_dimension = 'discipline';

CREATE OR REPLACE VIEW reporting.coding_check_design_key AS
  SELECT proj_id, proj_short_name, "Project ID", "Account", "Lead Planner", "Period", "Period Code",
    "Project State", "Project Status",
    code_dimension, code_type_name, source_code_type_names,
    actv_code_type_id, actv_code_id, code_short_name, code_raw_value,
    code_value, code_sort_order, bucket_status, is_uncoded,
    activity_count, project_activity_count, code_type_exists,
    _snapshot_at
  FROM coding_check.code_counts WHERE code_dimension = 'design_key';

CREATE OR REPLACE VIEW reporting.coding_check_milestone_type AS
  SELECT proj_id, proj_short_name, "Project ID", "Account", "Lead Planner", "Period", "Period Code",
    "Project State", "Project Status",
    code_dimension, code_type_name, source_code_type_names,
    actv_code_type_id, actv_code_id, code_short_name, code_raw_value,
    code_value, code_sort_order, bucket_status, is_uncoded,
    activity_count, project_activity_count, code_type_exists,
    _snapshot_at
  FROM coding_check.code_counts WHERE code_dimension = 'milestone_type';

CREATE OR REPLACE VIEW reporting.coding_check_responsible_company AS
  SELECT proj_id, proj_short_name, "Project ID", "Account", "Lead Planner", "Period", "Period Code",
    "Project State", "Project Status",
    code_dimension, code_type_name, source_code_type_names,
    actv_code_type_id, actv_code_id, code_short_name, code_raw_value,
    code_value, code_sort_order, bucket_status, is_uncoded,
    activity_count, project_activity_count, code_type_exists,
    _snapshot_at
  FROM coding_check.code_counts WHERE code_dimension = 'responsible_company';

-- Float groups are mutually exclusive, using unrounded raw hours / 8.
-- Longest Path is an independent flag, so it can overlap any float group.
CREATE OR REPLACE VIEW reporting.coding_check_path_summary AS
SELECT p.proj_id, p.proj_short_name,
  p."Project ID", p."Account", p."Lead Planner", p."Period", p."Period Code",
  p."Project State", p."Project Status",
  count(a.task_id) AS activity_count,
  count(*) FILTER (WHERE a.total_float_hr_cnt = 0) AS critical_path_count,
  count(*) FILTER (WHERE upper(coding_check.clean_text(a.driving_path_flag)) = 'Y') AS longest_path_count,
  count(*) FILTER (WHERE a.total_float_hr_cnt > 0 AND a.total_float_hr_cnt < 160) AS project_critical_count,
  count(*) FILTER (WHERE a.total_float_hr_cnt < 0) AS negative_float_count,
  count(*) FILTER (WHERE a.total_float_hr_cnt >= 160) AS other_float_count,
  count(a.task_id) FILTER (WHERE a.total_float_hr_cnt IS NULL) AS missing_float_count,
  count(a.task_id) FILTER (WHERE coding_check.clean_text(a.driving_path_flag) IS NULL) AS missing_longest_path_flag_count,
  count(a.task_id) FILTER (WHERE upper(coding_check.clean_text(a.driving_path_flag)) NOT IN ('Y', 'N')) AS invalid_longest_path_flag_count,
  8::numeric AS hours_per_day, 20::numeric AS project_critical_threshold_days,
  p._snapshot_at
FROM coding_check.projects p
LEFT JOIN coding_check.activities a ON a._source_system = p._source_system AND a.proj_id = p.proj_id
GROUP BY p._source_system, p._source_database, p.proj_id, p.proj_short_name,
  p."Project ID", p."Account", p."Lead Planner", p."Period", p."Period Code",
  p."Project State", p."Project Status",
  p._snapshot_at;

CREATE OR REPLACE VIEW reporting.coding_check_path_counts AS
SELECT p.proj_id, p.proj_short_name,
  p."Project ID", p."Account", p."Lead Planner", p."Period", p."Period Code",
  p."Project State", p."Project Status",
  m.metric_key, m.metric_label, m.metric_order, m.is_float_bucket,
  m.activity_count, p.activity_count AS project_activity_count,
  p.hours_per_day, p.project_critical_threshold_days,
  p._snapshot_at
FROM reporting.coding_check_path_summary p
CROSS JOIN LATERAL (VALUES
  ('critical_path', 'Critical Path', 1, true, p.critical_path_count),
  ('longest_path', 'Longest Path', 2, false, p.longest_path_count),
  ('project_critical', 'Project Critical (20d)', 3, true, p.project_critical_count),
  ('negative_float', 'Negative Float', 4, true, p.negative_float_count),
  ('other_float', 'Float 20 days or more', 5, true, p.other_float_count),
  ('missing_float', 'Missing Total Float', 6, true, p.missing_float_count),
  ('missing_longest_path_flag', 'Missing Longest Path Flag', 7, false, p.missing_longest_path_flag_count),
  ('invalid_longest_path_flag', 'Invalid Longest Path Flag', 8, false, p.invalid_longest_path_flag_count)
) m(metric_key, metric_label, metric_order, is_float_bucket, activity_count);

COMMENT ON VIEW reporting.coding_check_activity_code_counts IS
  'Coding checks: one row per P6 22 programme/dimension/value. Includes zero dictionary values and uncoded rows. Sum activity_count within one dimension only.';
COMMENT ON VIEW reporting.coding_check_path_counts IS
  'Float buckets are exclusive; Longest Path and missing/invalid path flags are independent. P6 days are 8 hours. Raw hours are not rounded.';
COMMENT ON VIEW reporting.coding_check_path_summary IS
  'Critical=float 0; Project Critical=float >0 and <160 hours; Negative=float <0. Longest Path=TASK.driving_path_flag Y. All active non-retired activity types/statuses.';

GRANT USAGE ON SCHEMA reporting TO p6_reporting_reader;
GRANT SELECT ON reporting.coding_check_activity_code_counts,
  reporting.coding_check_project_stage, reporting.coding_check_discipline,
  reporting.coding_check_design_key, reporting.coding_check_milestone_type,
  reporting.coding_check_responsible_company, reporting.coding_check_path_summary,
  reporting.coding_check_path_counts TO p6_reporting_reader;

-- The publication worker grants reader access schema-wide. These views may be
-- owned by the installer, so allow the worker to grant their SELECT privilege.
GRANT SELECT ON reporting.coding_check_activity_code_counts,
  reporting.coding_check_project_stage, reporting.coding_check_discipline,
  reporting.coding_check_design_key, reporting.coding_check_milestone_type,
  reporting.coding_check_responsible_company, reporting.coding_check_path_summary,
  reporting.coding_check_path_counts TO p6_reporting_sync WITH GRANT OPTION;

COMMIT;
