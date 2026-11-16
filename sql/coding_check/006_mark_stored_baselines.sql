-- Keep eligible stored P6 baselines in Coding Check and make them filterable.
-- Append fields only: existing column positions, eligibility and counts remain.
-- Run after 001_create_views.sql (and 005 on an older installation).
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';

CREATE OR REPLACE VIEW coding_check.project_baseline_flags AS
SELECT p.proj_id,
  p.orig_proj_id IS NOT NULL AS is_stored_baseline,
  p.orig_proj_id AS original_proj_id,
  original.proj_short_name AS original_proj_short_name
FROM reporting.project p
LEFT JOIN reporting.project original
  ON original._source_system = p._source_system AND original.proj_id = p.orig_proj_id
WHERE p._source_system = 'p6_22';

-- All six code views retain the same projection and their existing dimension.
DO $$
DECLARE entry record;
BEGIN
  FOR entry IN SELECT * FROM (VALUES
    ('coding_check_activity_code_counts', NULL::text),
    ('coding_check_project_stage', 'project_stage'),
    ('coding_check_discipline', 'discipline'),
    ('coding_check_design_key', 'design_key'),
    ('coding_check_milestone_type', 'milestone_type'),
    ('coding_check_responsible_company', 'responsible_company')
  ) AS views(view_name, dimension)
  LOOP
    EXECUTE format($view$
      CREATE OR REPLACE VIEW reporting.%I AS
      SELECT c.proj_id, c.proj_short_name, c."Project ID", c."Account", c."Lead Planner",
        c."Period", c."Period Code", c."Project State", c."Project Status",
        c.code_dimension, c.code_type_name, c.source_code_type_names,
        c.actv_code_type_id, c.actv_code_id, c.code_short_name, c.code_raw_value,
        c.code_value, c.code_sort_order, c.bucket_status, c.is_uncoded,
        c.activity_count, c.project_activity_count, c.code_type_exists, c._snapshot_at,
        b.is_stored_baseline, b.original_proj_id, b.original_proj_short_name
      FROM coding_check.code_counts c
      JOIN coding_check.project_baseline_flags b ON b.proj_id = c.proj_id
      %s
    $view$, entry.view_name,
      CASE WHEN entry.dimension IS NULL THEN ''
           ELSE format('WHERE c.code_dimension = %L', entry.dimension) END);
  END LOOP;
END $$;

CREATE OR REPLACE VIEW reporting.coding_check_path_summary AS
WITH counts AS (
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
    p."Project State", p."Project Status", p._snapshot_at
)
SELECT c.*, b.is_stored_baseline, b.original_proj_id, b.original_proj_short_name
FROM counts c
JOIN coding_check.project_baseline_flags b ON b.proj_id = c.proj_id;

CREATE OR REPLACE VIEW reporting.coding_check_path_counts AS
SELECT p.proj_id, p.proj_short_name,
  p."Project ID", p."Account", p."Lead Planner", p."Period", p."Period Code",
  p."Project State", p."Project Status",
  m.metric_key, m.metric_label, m.metric_order, m.is_float_bucket,
  m.activity_count, p.activity_count AS project_activity_count,
  p.hours_per_day, p.project_critical_threshold_days, p._snapshot_at,
  p.is_stored_baseline, p.original_proj_id, p.original_proj_short_name
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

-- CREATE OR REPLACE preserves existing owners, grants and dependent objects.
DO $$
DECLARE view_name text;
BEGIN
  FOREACH view_name IN ARRAY ARRAY[
    'coding_check_activity_code_counts', 'coding_check_project_stage',
    'coding_check_discipline', 'coding_check_design_key',
    'coding_check_milestone_type', 'coding_check_responsible_company',
    'coding_check_path_summary', 'coding_check_path_counts'
  ] LOOP
    EXECUTE format('COMMENT ON COLUMN reporting.%I.is_stored_baseline IS %L', view_name,
      'True when P6 PROJECT.orig_proj_id is populated. Stored baselines remain included; filter false for ordinary projects only. Independent of the Project Status code.');
    EXECUTE format('COMMENT ON COLUMN reporting.%I.original_proj_id IS %L', view_name,
      'P6 22 PROJECT.orig_proj_id: original project linked to this stored baseline; NULL for ordinary projects.');
    EXECUTE format('COMMENT ON COLUMN reporting.%I.original_proj_short_name IS %L', view_name,
      'P6 22 identifier of the original project. NULL if no original reference or the referenced project is absent.');
  END LOOP;
END $$;

COMMIT;
