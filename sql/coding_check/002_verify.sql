-- Read-only verification of P6 22 scope through the external reporting login.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '120s';
-- Helpers retain source fields; validate their source restriction before using
-- the external role, which needs access only to public reporting views.
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM coding_check.projects WHERE _source_system IS DISTINCT FROM 'p6_22')
    OR EXISTS (SELECT 1 FROM coding_check.code_types WHERE _source_system IS DISTINCT FROM 'p6_22') THEN
    RAISE EXCEPTION 'Coding Check internal scope includes an unexpected source';
  END IF;
END $$;
SET LOCAL ROLE p6_reporting_external;

DO $$
DECLARE problems bigint; view_name text;
BEGIN
  -- Independent raw-source population; no coding_check helper views used here.
  WITH classifications AS (
    SELECT DISTINCT a._source_system, a.proj_id, lower(btrim(t.proj_catg_type)) AS kind,
      v.proj_catg_id,
      lower(coalesce(nullif(btrim(v.proj_catg_name), ''), nullif(btrim(v.proj_catg_short_name), ''))) AS value
    FROM reporting.projpcat a
    JOIN reporting.pcattype t ON t._source_system = a._source_system AND t.proj_catg_type_id = a.proj_catg_type_id
    JOIN reporting.pcatval v ON v._source_system = a._source_system
      AND v.proj_catg_type_id = a.proj_catg_type_id AND v.proj_catg_id = a.proj_catg_id
    WHERE a.delete_date IS NULL AND a.delete_session_id IS NULL
      AND t.delete_date IS NULL AND t.delete_session_id IS NULL
      AND v.delete_date IS NULL AND v.delete_session_id IS NULL
      AND lower(btrim(t.proj_catg_type)) IN ('project state', 'project status')
  ), eligible AS (
    SELECT p._source_system, p.proj_id
    FROM reporting.project p
    JOIN classifications c ON c._source_system = p._source_system AND c.proj_id = p.proj_id
    WHERE p._source_system = 'p6_22' AND p.project_flag = 'Y'
      AND p.delete_date IS NULL AND p.delete_session_id IS NULL
    GROUP BY p._source_system, p.proj_id
    HAVING count(*) FILTER (WHERE kind = 'project state') = 1
      AND count(*) FILTER (WHERE kind = 'project status') = 1
      AND bool_or(kind = 'project state' AND value = 'live')
      AND bool_or(kind = 'project status' AND value = 'client submitted')
  ), retired AS (
    SELECT DISTINCT a._source_system, a.proj_id, a.task_id
    FROM reporting.taskactv a
    JOIN reporting.actvtype t ON t._source_system = a._source_system AND t.actv_code_type_id = a.actv_code_type_id
    JOIN reporting.actvcode v ON v._source_system = a._source_system
      AND v.actv_code_type_id = a.actv_code_type_id AND v.actv_code_id = a.actv_code_id
    WHERE a.delete_date IS NULL AND a.delete_session_id IS NULL
      AND t.delete_date IS NULL AND t.delete_session_id IS NULL
      AND v.delete_date IS NULL AND v.delete_session_id IS NULL
      AND lower(btrim(t.actv_code_type)) = 'activity status' AND upper(btrim(v.short_name)) = 'DEL'
  ), expected AS (
    SELECT e._source_system, e.proj_id, count(t.task_id) AS activity_count,
      count(*) FILTER (WHERE t.total_float_hr_cnt = 0) AS critical_path_count,
      count(*) FILTER (WHERE t.total_float_hr_cnt > 0 AND t.total_float_hr_cnt < 160) AS project_critical_count,
      count(*) FILTER (WHERE t.total_float_hr_cnt < 0) AS negative_float_count,
      count(*) FILTER (WHERE upper(regexp_replace(t.driving_path_flag, '^[[:space:]]+|[[:space:]]+$', '', 'g')) = 'Y') AS longest_path_count
    FROM eligible e LEFT JOIN reporting.task t ON t._source_system = e._source_system AND t.proj_id = e.proj_id
      AND t.delete_date IS NULL AND t.delete_session_id IS NULL
      AND NOT EXISTS (SELECT 1 FROM retired r WHERE r._source_system = t._source_system
        AND r.proj_id = t.proj_id AND r.task_id = t.task_id)
    GROUP BY e._source_system, e.proj_id
  )
  SELECT count(*) INTO problems FROM expected e
  FULL JOIN reporting.coding_check_path_summary a USING (proj_id)
  WHERE a.activity_count IS DISTINCT FROM e.activity_count
    OR a.critical_path_count IS DISTINCT FROM e.critical_path_count
    OR a.project_critical_count IS DISTINCT FROM e.project_critical_count
    OR a.negative_float_count IS DISTINCT FROM e.negative_float_count
    OR a.longest_path_count IS DISTINCT FROM e.longest_path_count;
  IF problems <> 0 THEN RAISE EXCEPTION 'Raw population/path reconciliation failed for % programmes', problems; END IF;

  WITH observed AS (
    SELECT proj_id, code_dimension, sum(activity_count) AS activity_count,
      min(project_activity_count) AS smallest_total, max(project_activity_count) AS largest_total
    FROM reporting.coding_check_activity_code_counts GROUP BY 1,2
  ), expected AS (
    SELECT p.proj_id, d.code_dimension, p.activity_count
    FROM reporting.coding_check_path_summary p
    CROSS JOIN (VALUES ('project_stage'), ('discipline'), ('design_key'), ('milestone_type'), ('responsible_company')) d(code_dimension)
  )
  SELECT count(*) INTO problems FROM expected e
  FULL JOIN observed o USING (proj_id, code_dimension)
  WHERE o.activity_count IS DISTINCT FROM e.activity_count
    OR o.smallest_total IS DISTINCT FROM e.activity_count OR o.largest_total IS DISTINCT FROM e.activity_count;
  IF problems <> 0 THEN RAISE EXCEPTION 'Code-count reconciliation failed for % programme/dimensions', problems; END IF;

  SELECT count(*) INTO problems FROM (
    SELECT proj_id, code_dimension, bucket_status, actv_code_type_id, actv_code_id
    FROM reporting.coding_check_activity_code_counts GROUP BY 1,2,3,4,5 HAVING count(*) <> 1
  ) q;
  IF problems <> 0 THEN RAISE EXCEPTION 'Duplicate coding result keys: %', problems; END IF;

  SELECT count(*) INTO problems FROM reporting.coding_check_path_summary
  WHERE activity_count <> critical_path_count + project_critical_count + negative_float_count + other_float_count + missing_float_count;
  IF problems <> 0 THEN RAISE EXCEPTION 'Exclusive float groups do not reconcile: %', problems; END IF;

  -- Check every published entry point, including the convenience views.
  FOREACH view_name IN ARRAY ARRAY[
      'coding_check_activity_code_counts', 'coding_check_project_stage',
      'coding_check_discipline', 'coding_check_design_key',
      'coding_check_milestone_type', 'coding_check_responsible_company',
      'coding_check_path_summary', 'coding_check_path_counts'
  ] LOOP
    SELECT count(*) INTO problems FROM information_schema.columns
    WHERE table_schema = 'reporting' AND table_name = view_name
      AND column_name IN ('_source_system', '_source_database', '_project_key',
          'project_code_conflict_count', '_synced_at', '_batch_id');
    IF problems <> 0 THEN RAISE EXCEPTION 'Removed public columns remain in %: %', view_name, problems; END IF;
    SELECT count(*) INTO problems FROM information_schema.columns
    WHERE table_schema = 'reporting' AND table_name = view_name
      AND column_name IN ('proj_id', 'proj_short_name', 'Project ID', 'Account',
        'Lead Planner', 'Period', 'activity_count', '_snapshot_at');
    IF problems <> 8 THEN RAISE EXCEPTION 'Required public columns missing in %', view_name; END IF;
    SELECT count(*) INTO problems FROM information_schema.columns
    WHERE table_schema = 'reporting' AND table_name = view_name
      AND column_name IN ('is_stored_baseline', 'original_proj_id', 'original_proj_short_name');
    IF problems <> 3 THEN RAISE EXCEPTION 'Stored baseline metadata columns missing in %', view_name; END IF;
    EXECUTE format($check$
      SELECT count(*) FROM reporting.%I v
      JOIN reporting.project p ON p._source_system = 'p6_22' AND p.proj_id = v.proj_id
      LEFT JOIN reporting.project original
        ON original._source_system = p._source_system AND original.proj_id = p.orig_proj_id
      WHERE v.is_stored_baseline IS DISTINCT FROM (p.orig_proj_id IS NOT NULL)
         OR v.original_proj_id IS DISTINCT FROM p.orig_proj_id
         OR v.original_proj_short_name IS DISTINCT FROM original.proj_short_name
    $check$, view_name) INTO problems;
    IF problems <> 0 THEN RAISE EXCEPTION 'Stored baseline metadata differs from raw P6 project in %', view_name; END IF;
    -- EXECUTE also proves that this login can still query every view.
    EXECUTE format('SELECT count(*) FROM reporting.%I v WHERE NOT EXISTS (SELECT 1 FROM reporting.project p WHERE p._source_system = %L AND p.proj_id = v.proj_id)', view_name, 'p6_22') INTO problems;
    IF problems <> 0 THEN RAISE EXCEPTION 'Unexpected programme rows in %: %', view_name, problems; END IF;
  END LOOP;
END $$;

SELECT count(*) AS programme_count, sum(activity_count) AS activity_count,
  count(*) FILTER (WHERE "Project ID" IS NULL) AS missing_project_id,
  count(*) FILTER (WHERE "Account" IS NULL) AS missing_account,
  count(*) FILTER (WHERE "Lead Planner" IS NULL) AS missing_lead_planner,
  count(*) FILTER (WHERE "Period" IS NULL) AS missing_period
FROM reporting.coding_check_path_summary;

SELECT code_dimension, bool_and(code_type_exists) AS code_type_exists,
  sum(activity_count) FILTER (WHERE is_uncoded) AS uncoded_activities,
  sum(activity_count) FILTER (WHERE bucket_status = 'conflict') AS conflicting_activities
FROM reporting.coding_check_activity_code_counts GROUP BY 1 ORDER BY 1;

SELECT 'P6 22-only Coding Check live verification passed' AS result;
ROLLBACK;
