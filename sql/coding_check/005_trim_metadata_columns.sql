-- Remove project_code_conflict_count, _synced_at and _batch_id from public Coding Check views.
-- Retain _snapshot_at, all other columns, P6 22 scope, grouping and reader access.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';

DROP VIEW IF EXISTS reporting.coding_check_path_counts;
DROP VIEW IF EXISTS reporting.coding_check_path_summary;
DROP VIEW IF EXISTS reporting.coding_check_project_stage;
DROP VIEW IF EXISTS reporting.coding_check_discipline;
DROP VIEW IF EXISTS reporting.coding_check_design_key;
DROP VIEW IF EXISTS reporting.coding_check_milestone_type;
DROP VIEW IF EXISTS reporting.coding_check_responsible_company;
DROP VIEW IF EXISTS reporting.coding_check_activity_code_counts;

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
