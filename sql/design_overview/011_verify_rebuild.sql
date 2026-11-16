\timing on
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL statement_timeout='90s';
SET LOCAL lock_timeout='10s';
CREATE TEMP TABLE verify_cuts ON COMMIT DROP AS SELECT * FROM reporting.design_overview_submissions;
CREATE TEMP TABLE verify_metrics ON COMMIT DROP AS SELECT * FROM reporting.design_overview_performance_detail;
CREATE TEMP TABLE verify_summary ON COMMIT DROP AS SELECT * FROM reporting.design_overview_checkpoint_summary;
CREATE TEMP TABLE verify_future ON COMMIT DROP AS SELECT * FROM reporting.design_overview_future_activity_detail;
CREATE TEMP TABLE verify_future_counts ON COMMIT DROP AS SELECT * FROM reporting.design_overview_future_code_counts;
CREATE TEMP TABLE verify_code_counts ON COMMIT DROP AS SELECT * FROM reporting.design_overview_performance_code_counts;
DO $$ DECLARE n bigint; BEGIN
 SELECT count(*) INTO n FROM verify_summary WHERE planned_count IS NOT NULL
 AND planned_count<>achieved_count+missed_count+unknown_planned_count;
 IF n>0 THEN RAISE EXCEPTION 'Period accounting mismatch'; END IF;
 SELECT count(*) INTO n FROM verify_summary WHERE baseline_planned_count IS NOT NULL
 AND baseline_planned_count<>baseline_achieved_count+baseline_missed_count+unknown_baseline_planned_count;
 IF n>0 THEN RAISE EXCEPTION 'Baseline accounting mismatch'; END IF;
 SELECT count(*) INTO n FROM verify_summary WHERE remaining_count<>
 earlier_over_30_count+earlier_1_to_30_count+unchanged_count+later_1_to_30_count+later_over_30_count+new_or_no_prior_count;
 IF n>0 THEN RAISE EXCEPTION 'Movement band mismatch'; END IF;

 WITH expected AS (
 SELECT f.proj_id,k.key,count(*) AS detail_count,count(*) FILTER(WHERE is_planned) AS planned,
 count(*) FILTER(WHERE is_achieved) AS achieved,count(*) FILTER(WHERE is_missed) AS missed,
 count(*) FILTER(WHERE is_unplanned) AS unplanned,count(*) FILTER(WHERE baseline_planned) AS base_planned,
 count(*) FILTER(WHERE baseline_achieved) AS base_achieved,avg(previous_movement_days) AS movement
 FROM verify_metrics f CROSS JOIN LATERAL (VALUES(f.checkpoint_key),('all')) k(key) GROUP BY 1,2
 ) SELECT count(*) INTO n FROM verify_summary s LEFT JOIN expected e ON e.proj_id=s.proj_id AND e.key=s.checkpoint_key
 WHERE s.detail_activity_count<>coalesce(e.detail_count,0)
 OR (s.is_comparable AND (s.planned_count<>coalesce(e.planned,0) OR s.achieved_count<>coalesce(e.achieved,0)
 OR s.missed_count<>coalesce(e.missed,0) OR s.unplanned_count<>coalesce(e.unplanned,0)))
 OR (s.baseline_status='ready' AND (s.baseline_planned_count<>coalesce(e.base_planned,0) OR s.baseline_achieved_count<>coalesce(e.base_achieved,0)))
 OR s.previous_movement_days IS DISTINCT FROM e.movement;
 IF n>0 THEN RAISE EXCEPTION 'Headline/evidence mismatch'; END IF;

 WITH expected AS (
 SELECT proj_id,count(*) FILTER(WHERE is_planned) AS planned,count(*) FILTER(WHERE is_achieved) AS achieved
 FROM verify_metrics GROUP BY proj_id
 ), observed AS (
 SELECT proj_id,code_dimension,code_basis,sum(planned_count) AS planned,sum(achieved_count) AS achieved
 FROM verify_code_counts GROUP BY 1,2,3
 ) SELECT count(*) INTO n FROM observed o JOIN expected e USING(proj_id) JOIN verify_cuts s USING(proj_id)
 WHERE (s.is_comparable AND (o.planned IS DISTINCT FROM e.planned OR o.achieved IS DISTINCT FROM e.achieved))
 OR (NOT s.is_comparable AND (o.planned IS NOT NULL OR o.achieved IS NOT NULL));
 IF n>0 THEN RAISE EXCEPTION 'Dimension summary/evidence mismatch'; END IF;

 WITH expected AS (
 SELECT f.proj_id,f.future_period_offset,k.key,count(*) AS activities
 FROM verify_future f CROSS JOIN LATERAL(VALUES(f.checkpoint_key),('all')) k(key) GROUP BY 1,2,3
 ) SELECT count(*) INTO n FROM verify_future_counts c LEFT JOIN expected e
 ON e.proj_id=c.proj_id AND e.future_period_offset=c.future_period_offset AND e.key=c.checkpoint_key
 WHERE (c.is_available AND c.planned_count<>coalesce(e.activities,0)) OR (NOT c.is_available AND c.planned_count IS NOT NULL);
 IF n>0 THEN RAISE EXCEPTION 'Future headline/evidence mismatch'; END IF;
 SELECT count(*) INTO n FROM verify_future WHERE current_event_date<window_start OR current_event_date>=window_end;
 IF n>0 THEN RAISE EXCEPTION 'Future period boundary mismatch'; END IF;

 -- Independently check raw P6 Start and the selected baseline's raw Activity ID.
 SELECT count(*) INTO n FROM verify_metrics f JOIN reporting.task t
 ON t._source_system='p6_22' AND t.proj_id=f.proj_id AND t.task_id=f.current_task_id
 WHERE f.current_start IS DISTINCT FROM CASE WHEN t.status_code IN('TK_Active','TK_Complete') OR t.act_start_date IS NOT NULL
 THEN t.act_start_date ELSE coalesce(t.restart_date,t.target_start_date) END;
 IF n>0 THEN RAISE EXCEPTION 'P6 Start display mismatch'; END IF;
 SELECT count(*) INTO n FROM verify_metrics f JOIN reporting.task t
 ON t._source_system='p6_22' AND t.task_id=f.baseline_task_id AND t.proj_id=f.baseline_proj_id
 WHERE t.task_code IS DISTINCT FROM f.task_code;
 IF n>0 THEN RAISE EXCEPTION 'Baseline Activity ID mismatch'; END IF;
 SELECT count(*) INTO n FROM verify_metrics WHERE lower(checkpoint_key)='d-start'
 AND current_task_id IS NOT NULL AND match_status NOT IN('missing_activity_id','ambiguous_activity_id')
 AND checkpoint_reached IS DISTINCT FROM (actual_start IS NOT NULL);
 IF n>0 THEN RAISE EXCEPTION 'Design Start achievement mismatch'; END IF;
END $$;
SELECT comparison_status,baseline_status,count(*) AS submissions FROM verify_cuts GROUP BY 1,2 ORDER BY 1,2;
SELECT count(*) AS metric_rows,count(*) FILTER(WHERE lower(checkpoint_key)='d-start') AS design_start_rows,
 count(*) FILTER(WHERE is_planned) AS planned,count(*) FILTER(WHERE is_achieved) AS achieved,
 count(*) FILTER(WHERE is_missed) AS missed,count(*) FILTER(WHERE is_unplanned) AS unplanned,
 count(*) FILTER(WHERE baseline_task_id IS NOT NULL) AS matched_baseline_activities FROM verify_metrics;
SET LOCAL ROLE p6_reporting_external;
SELECT task_code,current_start,checkpoint_reached FROM reporting.design_overview_performance_detail LIMIT 1;
SELECT checkpoint_key,completion_percent FROM reporting.design_overview_checkpoint_summary LIMIT 1;
SELECT future_period_offset,planned_count FROM reporting.design_overview_future_code_counts LIMIT 1;
RESET ROLE;
COMMIT;
SELECT 'Replacement live reconciliation passed' AS result;
