-- Read-only source reconciliation with temporary, session-local results.
\timing on
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL statement_timeout='120s';
SET LOCAL lock_timeout='10s';
CREATE TEMP TABLE verify_design_submissions ON COMMIT DROP AS
  SELECT * FROM reporting.design_overview_submissions;
CREATE TEMP TABLE verify_design_activities ON COMMIT DROP AS
  SELECT * FROM reporting.design_overview_activity_detail;
CREATE TEMP TABLE verify_design_coding_counts ON COMMIT DROP AS
  SELECT * FROM reporting.design_overview_activity_code_counts;
CREATE TEMP TABLE verify_design_performance ON COMMIT DROP AS
  SELECT * FROM reporting.design_overview_performance_detail;
CREATE TEMP TABLE verify_design_performance_counts ON COMMIT DROP AS
  SELECT * FROM reporting.design_overview_performance_code_counts;

DO $$ DECLARE problems bigint; BEGIN
  SELECT count(*) INTO problems FROM verify_design_submissions s
  JOIN reporting.project p ON p._source_system='p6_22' AND p.proj_id=s.proj_id
  WHERE p.orig_proj_id IS NOT NULL;
  IF problems<>0 THEN RAISE EXCEPTION 'Stored baselines entered Design Overview'; END IF;

  SELECT count(*) INTO problems FROM (
    SELECT project_code_id,period_serial FROM verify_design_submissions
    WHERE is_comparable GROUP BY 1,2 HAVING count(*)<>1
  ) x;
  IF problems<>0 THEN RAISE EXCEPTION 'Ambiguous submissions entered a comparison'; END IF;

  -- Independent Global Design Key membership from raw assignments/dictionaries.
  WITH expected AS (
    SELECT a.proj_id,count(DISTINCT a.task_id) AS activities
    FROM coding_check.activities a JOIN verify_design_submissions s USING(proj_id)
    JOIN reporting.taskactv ta ON ta._source_system=a._source_system
      AND ta.proj_id=a.proj_id AND ta.task_id=a.task_id
    JOIN reporting.actvtype t ON t._source_system=ta._source_system
      AND t.actv_code_type_id=ta.actv_code_type_id
    JOIN reporting.actvcode v ON v._source_system=ta._source_system
      AND v.actv_code_type_id=ta.actv_code_type_id AND v.actv_code_id=ta.actv_code_id
    WHERE ta.delete_date IS NULL AND ta.delete_session_id IS NULL
      AND t.delete_date IS NULL AND t.delete_session_id IS NULL
      AND v.delete_date IS NULL AND v.delete_session_id IS NULL
      AND t.actv_code_type_scope='AS_Global' AND lower(btrim(t.actv_code_type))='design key'
      AND coalesce(coding_check.clean_text(v.actv_code_name),coding_check.clean_text(v.short_name)) IS NOT NULL
    GROUP BY a.proj_id
  ), observed AS (SELECT proj_id,count(*) AS activities FROM verify_design_activities GROUP BY proj_id)
  SELECT count(*) INTO problems FROM expected e FULL JOIN observed o USING(proj_id)
    WHERE e.activities IS DISTINCT FROM o.activities;
  IF problems<>0 THEN RAISE EXCEPTION 'Design membership differs from raw source'; END IF;

  SELECT count(*) INTO problems FROM (
    SELECT proj_id,code_dimension,sum(activity_count) AS activities
    FROM verify_design_coding_counts GROUP BY 1,2
  ) c LEFT JOIN (SELECT proj_id,count(*) AS activities FROM verify_design_activities GROUP BY proj_id) a USING(proj_id)
  WHERE c.activities IS DISTINCT FROM coalesce(a.activities,0);
  IF problems<>0 THEN RAISE EXCEPTION 'Design coding counts do not reconcile'; END IF;

  WITH expected AS (
    SELECT s.proj_id,count(*) FILTER(WHERE p.is_planned) AS planned,
      count(*) FILTER(WHERE p.is_achieved) AS achieved,
      count(*) FILTER(WHERE p.is_planned AND p.is_achieved) AS planned_achieved,
      count(*) FILTER(WHERE p.is_missed) AS missed,count(*) FILTER(WHERE p.is_unplanned) AS unplanned,
      count(*) FILTER(WHERE p.unknown_planned) AS unknown_planned,
      count(*) FILTER(WHERE p.unknown_plan_achieved) AS unknown_plan_achieved
    FROM verify_design_submissions s LEFT JOIN verify_design_performance p USING(proj_id)
    WHERE s.is_comparable GROUP BY s.proj_id
  ), observed AS (
    SELECT proj_id,code_dimension,code_basis,sum(planned_count) AS planned,
      sum(achieved_count) AS achieved,sum(planned_achieved_count) AS planned_achieved,
      sum(missed_count) AS missed,sum(unplanned_count) AS unplanned,
      sum(unknown_planned_count) AS unknown_planned,
      sum(unknown_plan_achieved_count) AS unknown_plan_achieved
    FROM verify_design_performance_counts GROUP BY 1,2,3
  ) SELECT count(*) INTO problems FROM observed o JOIN expected e USING(proj_id)
  WHERE o.planned IS DISTINCT FROM e.planned OR o.achieved IS DISTINCT FROM e.achieved
    OR o.planned_achieved IS DISTINCT FROM e.planned_achieved OR o.missed IS DISTINCT FROM e.missed
    OR o.unplanned IS DISTINCT FROM e.unplanned OR o.unknown_planned IS DISTINCT FROM e.unknown_planned
    OR o.unknown_plan_achieved IS DISTINCT FROM e.unknown_plan_achieved;
  IF problems<>0 THEN RAISE EXCEPTION 'Design performance counts do not reconcile'; END IF;

  SELECT count(*) INTO problems FROM verify_design_performance_counts
  WHERE planned_count<>planned_achieved_count+missed_count+unknown_planned_count
    OR achieved_count<>planned_achieved_count+unplanned_count+unknown_plan_achieved_count;
  IF problems<>0 THEN RAISE EXCEPTION 'Design performance accounting identities failed'; END IF;
END $$;

SELECT comparison_status,count(*) AS submissions FROM verify_design_submissions GROUP BY 1 ORDER BY 1;
SELECT count(*) AS design_activity_rows,count(DISTINCT proj_id) AS design_submissions FROM verify_design_activities;
SELECT count(*) AS comparison_activity_rows,count(DISTINCT proj_id) AS comparison_submissions,
  count(*) FILTER(WHERE is_planned) AS planned,count(*) FILTER(WHERE is_achieved) AS achieved,
  count(*) FILTER(WHERE is_missed) AS missed,count(*) FILTER(WHERE is_unplanned) AS unplanned,
  count(*) FILTER(WHERE unknown_planned) AS unknown_planned,
  count(*) FILTER(WHERE unknown_plan_achieved) AS unknown_plan_achieved
FROM verify_design_performance;

-- Exercise the actual external reader's access, not just privilege metadata.
SET LOCAL ROLE p6_reporting_external;
SELECT count(*) AS reader_submission_rows FROM reporting.design_overview_submissions;
SELECT task_code,full_period_code,design_key FROM reporting.design_overview_activity_detail LIMIT 1;
SELECT task_code,is_planned,is_achieved FROM reporting.design_overview_performance_detail LIMIT 1;
RESET ROLE;
COMMIT;
SELECT 'Design Overview deployment reconciliation passed' AS result;
