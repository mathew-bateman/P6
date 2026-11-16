-- Behavioral Design Overview assertions. Run AFTER coding base fixtures, design
-- fixtures, coding view installation and design_overview/001_create_views.sql.
-- No live project is changed; the guard rejects production databases.
DO $$ BEGIN
  IF current_database() NOT LIKE 'coding_check_test_%' THEN
    RAISE EXCEPTION 'Design Overview assertions require a coding_check_test_* database';
  END IF;
END $$;
CREATE OR REPLACE FUNCTION pg_temp.design_check(actual boolean,label text)
RETURNS void LANGUAGE plpgsql AS $$ BEGIN
  IF actual IS DISTINCT FROM TRUE THEN
    RAISE EXCEPTION 'Design Overview regression failed: %',label;
  END IF;
END $$;

SELECT pg_temp.design_check(
 (SELECT full_period_code='2026/27.P7' AND previous_proj_id=500
     AND period_start=timestamp '2026-09-20 00:01'
     AND period_end=timestamp '2026-10-18 00:01' AND is_comparable
  FROM reporting.design_overview_submissions WHERE proj_id=501),
 'financial year hierarchy and consecutive submission data dates define the comparison');
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_submissions WHERE proj_id=502),
 'stored baseline inheriting the same project/period codes is not another submission');
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_activity_detail WHERE proj_id=502),
 'stored baseline activity does not inflate design activity detail');
SELECT pg_temp.design_check(
 (SELECT previous_proj_id=540 AND full_period_code='2027/28.P1' AND is_comparable
  FROM reporting.design_overview_submissions WHERE proj_id=541),
 'P13 to next financial year P1 selects the correct previous period');
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_submissions
            WHERE proj_id IN(511,521,522,531,550) AND is_comparable),
 'missing predecessor, ambiguous submission, non-increasing dates and missing period never fabricate comparisons');
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_performance_detail
            WHERE proj_id IN(502,511,521,522,531,550)),
 'invalid comparisons do not produce performance facts');

-- The same activity code, rather than native task ID or GUID, identifies the pair.
SELECT pg_temp.design_check(
 (SELECT previous_task_id=50001 AND current_task_id=51001
    AND is_planned AND is_achieved AND NOT is_missed AND NOT is_unplanned
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='ACHIEVED'),
 'planned and achieved activity matches across changed native IDs/GUIDs');
SELECT pg_temp.design_check(
 (SELECT is_planned AND NOT is_achieved AND is_missed AND NOT is_unplanned
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='MISSED'),
 'an incomplete original-plan activity is missed even when the current forecast moved outside the period');
SELECT pg_temp.design_check(
 (SELECT NOT is_planned AND is_achieved AND is_unplanned
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='UNPLANNED-EXISTING'),
 'an existing activity completed earlier than its original planned period is unplanned');
SELECT pg_temp.design_check(
 (SELECT previous_task_id IS NULL AND is_achieved AND is_unplanned
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='UNPLANNED-NEW'),
 'new completed activity is unplanned without requiring a prior task row');
SELECT pg_temp.design_check(
 (SELECT is_planned AND is_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='SPAN-PERIODS'),
 'activity spanning multiple periods counts in its planned finish period');

-- The period is a half-open interval of P6 data-date timestamps.
SELECT pg_temp.design_check(
 (SELECT is_planned AND is_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='START-MILESTONE'),
 'start milestone uses its sole start event and includes the exact period start');
SELECT pg_temp.design_check(
 (SELECT scheduled_event_date=timestamp '2026-09-20 00:01'
     AND actual_event_date=timestamp '2026-09-20 00:01'
  FROM reporting.design_overview_activity_detail
  WHERE proj_id=501 AND task_code='START-MILESTONE'),
 'start milestone needs no finish date');
SELECT pg_temp.design_check(
 (SELECT NOT is_planned AND NOT is_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='FINISH-END-BOUNDARY'),
 'finish milestone at exact period end belongs to the next period');
SELECT pg_temp.design_check(
 (SELECT NOT is_planned AND NOT is_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='BEFORE-START'),
 'events just before the period start are excluded');
SELECT pg_temp.design_check(
 (SELECT NOT is_planned AND NOT is_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='AT-END'),
 'ordinary finish at exact period end is excluded');

-- Preserve the original-plan denominator when the current population changes.
SELECT pg_temp.design_check(
 (SELECT previous_task_id=50007 AND current_task_id IS NULL
     AND is_planned AND is_missed IS NULL AND unknown_planned
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='REMOVED-ACTIVITY'),
 'removed planned activity stays visible as unknown rather than a guessed missed/completed result');
SELECT pg_temp.design_check(
 (SELECT previous_has_design_key AND NOT current_has_design_key
     AND is_planned AND is_missed
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='REMOVED-DESIGN-KEY'),
 'removed Design Key cannot erase the originally planned design activity');
SELECT pg_temp.design_check(
 (SELECT NOT previous_has_design_key AND current_has_design_key
     AND NOT is_planned AND is_achieved AND is_unplanned
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='ADDED-DESIGN-KEY'),
 'new design membership is retained separately from the original design scope');
SELECT pg_temp.design_check(
 (SELECT previous_codes IS DISTINCT FROM current_codes AND is_planned AND is_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='RECODED-DESIGN'),
 'both versions of changed Design Key codes remain available');
SELECT pg_temp.design_check(
 (SELECT is_planned IS NULL AND is_achieved AND is_unplanned IS NULL
      AND unknown_plan_achieved
  FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code='MISSING-SCHEDULE-DATE'),
 'a missing original schedule date cannot be mistaken for known unplanned work');
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code IN('PHYSICALLY-DELETED','RETIRED-DEL')
    AND (is_missed IS TRUE OR is_achieved IS TRUE)),
 'current deletion/retirement is not proof of missed or achieved work');
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_performance_detail
   WHERE proj_id=501 AND task_code='DUPLICATE-CODE'
     AND (is_planned IS TRUE OR is_achieved IS TRUE OR is_missed IS TRUE OR is_unplanned IS TRUE)),
 'ambiguous duplicate activity IDs never make an arbitrary many-to-many performance match');
SELECT pg_temp.design_check(
 EXISTS(SELECT 1 FROM reporting.design_overview_performance_detail
        WHERE proj_id=501 AND task_code='DUPLICATE-CODE'),
 'ambiguous activity IDs remain visible for diagnosis');

SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_performance_detail
  WHERE proj_id=501 AND task_code IN('NON-DESIGN','LOCAL-DESIGN-ONLY','BLANK-DESIGN-ONLY')),
 'only valid Global Design Key membership in either submission enters the design comparison');
SELECT pg_temp.design_check(
 (SELECT has_design_key AND design_key_bucket_status='conflict'
  FROM reporting.design_overview_activity_detail
  WHERE proj_id=501 AND task_code='CONFLICT-DESIGN'),
 'conflicting valid Global Design Key assignments stay in scope with a visible conflict bucket');
SELECT pg_temp.design_check(
 (SELECT count(*)=1 FROM reporting.design_overview_activity_detail
  WHERE proj_id=501 AND task_code='ACHIEVED'),
 'duplicate identical assignments do not duplicate activity detail');

-- Each summary is reconciled independently to its detail population.
DO $$ DECLARE problems bigint; BEGIN
 SELECT count(*) INTO problems FROM (
   SELECT c.proj_id,c.code_dimension,sum(c.activity_count) AS counted
   FROM reporting.design_overview_activity_code_counts c
   GROUP BY c.proj_id,c.code_dimension
 ) observed
 LEFT JOIN (SELECT proj_id,count(*) AS expected
            FROM reporting.design_overview_activity_detail
            WHERE has_design_key GROUP BY proj_id) detail USING(proj_id)
 WHERE observed.counted IS DISTINCT FROM coalesce(detail.expected,0);
 PERFORM pg_temp.design_check(problems=0,'each coding-summary dimension reconciles to design activity detail');

 WITH expected AS (
   SELECT s.proj_id,
    count(*) FILTER(WHERE is_planned) AS planned,
    count(*) FILTER(WHERE is_achieved) AS achieved,
    count(*) FILTER(WHERE is_planned AND is_achieved) AS planned_achieved,
    count(*) FILTER(WHERE is_missed) AS missed,
    count(*) FILTER(WHERE is_unplanned) AS unplanned,
    count(*) FILTER(WHERE unknown_planned) AS unknown_planned,
    count(*) FILTER(WHERE unknown_plan_achieved) AS unknown_plan_achieved
   FROM reporting.design_overview_submissions s
   LEFT JOIN reporting.design_overview_performance_detail p ON p.proj_id=s.proj_id
   WHERE s.is_comparable GROUP BY s.proj_id
 ), observed AS (
   SELECT proj_id,code_dimension,code_basis,
    sum(planned_count) AS planned,sum(achieved_count) AS achieved,
    sum(planned_achieved_count) AS planned_achieved,
    sum(missed_count) AS missed,sum(unplanned_count) AS unplanned,
    sum(unknown_planned_count) AS unknown_planned,
    sum(unknown_plan_achieved_count) AS unknown_plan_achieved
   FROM reporting.design_overview_performance_code_counts GROUP BY proj_id,code_dimension,code_basis
 ) SELECT count(*) INTO problems FROM observed o LEFT JOIN expected e USING(proj_id)
 WHERE o.planned IS DISTINCT FROM e.planned OR o.achieved IS DISTINCT FROM e.achieved
    OR o.planned_achieved IS DISTINCT FROM e.planned_achieved
    OR o.missed IS DISTINCT FROM e.missed OR o.unplanned IS DISTINCT FROM e.unplanned
    OR o.unknown_planned IS DISTINCT FROM e.unknown_planned
    OR o.unknown_plan_achieved IS DISTINCT FROM e.unknown_plan_achieved;
 PERFORM pg_temp.design_check(problems=0,'every code basis/dimension performance summary reconciles to detail');

 SELECT count(*) INTO problems FROM reporting.design_overview_performance_code_counts
 WHERE code_basis NOT IN('plan','outcome');
 PERFORM pg_temp.design_check(problems=0,'plan/outcome classification attribution is explicit');
END $$;

-- Role grants matter because the publisher will continue replacing raw generations.
DO $$ DECLARE view_name text; BEGIN
 FOREACH view_name IN ARRAY ARRAY['design_overview_submissions','design_overview_activity_detail',
  'design_overview_activity_code_counts','design_overview_performance_detail',
  'design_overview_performance_code_counts'] LOOP
  PERFORM pg_temp.design_check(has_table_privilege('p6_reporting_reader','reporting.'||view_name,'SELECT'),
    view_name||': reporting reader can query the view');
  PERFORM pg_temp.design_check(has_table_privilege('p6_reporting_external','reporting.'||view_name,'SELECT'),
    view_name||': external reporting reader retains access');
  PERFORM pg_temp.design_check(has_table_privilege('p6_reporting_sync','reporting.'||view_name,'SELECT WITH GRANT OPTION'),
    view_name||': publication worker can maintain reader grants');
 END LOOP;
END $$;
SELECT 'Design Overview behavioral assertions passed' AS result;


-- Per-activity accounting must hold independently of aggregation/classification.
SELECT pg_temp.design_check(
 NOT EXISTS(SELECT 1 FROM reporting.design_overview_performance_detail
  WHERE coalesce(is_planned::integer,0) <>
    coalesce((is_planned AND is_achieved)::integer,0)
      +coalesce(is_missed::integer,0)+unknown_planned::integer
   OR coalesce(is_achieved::integer,0) <>
    coalesce((is_planned AND is_achieved)::integer,0)
      +coalesce(is_unplanned::integer,0)+unknown_plan_achieved::integer),
 'planned/achieved accounting reconciles row by row, including unknown outcomes');

-- Exercise permissions rather than relying only on catalog grants. Both roles
-- must resolve dependencies behind the public owner-rights views successfully.
BEGIN;
SET LOCAL ROLE p6_reporting_reader;
SELECT count(*) AS reader_submission_smoke FROM reporting.design_overview_submissions;
SELECT count(*) AS reader_activity_smoke FROM reporting.design_overview_activity_detail;
SELECT count(*) AS reader_performance_smoke FROM reporting.design_overview_performance_detail;
COMMIT;
BEGIN;
SET LOCAL ROLE p6_reporting_external;
SELECT count(*) AS external_submission_smoke FROM reporting.design_overview_submissions;
SELECT count(*) AS external_performance_smoke FROM reporting.design_overview_performance_detail;
COMMIT;
SELECT 'Design Overview reader and accounting checks passed' AS result;
