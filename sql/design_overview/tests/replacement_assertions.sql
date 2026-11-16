DO $$ BEGIN IF current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'Test database required'; END IF; END $$;
CREATE OR REPLACE FUNCTION pg_temp.require(ok boolean,label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN IF ok IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Replacement regression: %',label; END IF; END $$;
SELECT pg_temp.require((SELECT baseline_proj_id=611 AND baseline_period_code='2026/27.P6' AND is_comparable
 FROM reporting.design_overview_submissions WHERE proj_id=601),'latest coded separate BL project is selected');
SELECT pg_temp.require((SELECT baseline_status='ambiguous_latest_baseline' AND baseline_proj_id IS NULL
 FROM reporting.design_overview_submissions WHERE proj_id=531),'tied baseline period is not arbitrarily resolved');
SELECT pg_temp.require((SELECT baseline_status='invalid_baseline_period' AND baseline_proj_id IS NULL
 FROM reporting.design_overview_submissions WHERE proj_id=511),'uncoded baseline cannot silently lose latest status');
SELECT pg_temp.require((SELECT baseline_proj_id=641 FROM reporting.design_overview_submissions WHERE proj_id=541),'baseline year rollover is numeric');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM reporting.design_overview_submissions WHERE proj_id IN(502,610,611)),
 'stored baselines and ordinary BL projects are not submitted cuts');

CREATE TEMP TABLE actual_metrics AS SELECT * FROM reporting.design_overview_performance_detail;
SELECT pg_temp.require((SELECT event_basis='start' AND current_start=timestamp '2026-10-01 09:00'
 AND start_date_source='act_start_date' AND is_activity_complete IS FALSE AND checkpoint_reached
 AND is_planned AND is_achieved AND NOT is_missed AND NOT is_remaining AND baseline_achieved
 FROM actual_metrics WHERE proj_id=601 AND task_code='START-ACTIVE'),
 'D-Start ordinary in-progress activity achieves by actual start, not remaining start or finish');
SELECT pg_temp.require((SELECT baseline_task_id IS NOT NULL AND baseline_start=timestamp '2026-09-28'
 AND baseline_finish=timestamp '2026-10-05' FROM actual_metrics WHERE proj_id=601 AND task_code='FINISH-COMPLETE'),
 'baseline activity matches Activity ID despite changed native task ID and absent baseline Design Key');
SELECT pg_temp.require((SELECT is_achieved AND is_planned AND NOT is_unplanned
 FROM actual_metrics WHERE proj_id=601 AND task_code='FINISH-COMPLETE'),'ordinary checkpoint achieves by completion');
SELECT pg_temp.require((SELECT is_planned AND is_missed AND NOT is_achieved
 FROM actual_metrics WHERE proj_id=601 AND task_code='FINISH-MISSED'),'started ordinary finish checkpoint remains missed until complete');
SELECT pg_temp.require((SELECT NOT is_planned AND NOT is_achieved AND is_unplanned
 FROM actual_metrics WHERE proj_id=601 AND task_code='PULLED-FORWARD'),'unplanned completion does not inflate planned achieved');
SELECT pg_temp.require((SELECT is_planned AND is_achieved FROM actual_metrics WHERE proj_id=601 AND task_code='START-LOWER'),
 'lower calendar date includes midnight even when raw data date was 00:01');
SELECT pg_temp.require((SELECT NOT is_planned AND NOT is_unplanned FROM actual_metrics WHERE proj_id=601 AND task_code='START-UPPER'),
 'upper calendar date is excluded for both plan and actual');
SELECT pg_temp.require((SELECT is_planned IS NULL AND is_unplanned IS NULL AND baseline_planned IS NULL
 FROM actual_metrics WHERE proj_id=601 AND task_code='MISSING-PRIOR'),'missing activity comparisons retain unknowns');
SELECT pg_temp.require((SELECT is_planned AND is_missed IS NULL AND unknown_planned
 FROM actual_metrics WHERE proj_id=601 AND task_code='REMOVED-ROW'),'removed planned work remains an unknown outcome');
SELECT pg_temp.require((SELECT current_start=timestamp '2026-11-02' AND start_date_source='target_start_date'
 FROM actual_metrics WHERE proj_id=601 AND task_code='PRE-SCHEDULE'),'planned start supplies the pre-schedule Start display');
SELECT pg_temp.require((SELECT previous_movement_days=5 AND movement_band='later_1_to_30'
 FROM actual_metrics WHERE proj_id=601 AND task_code='FUTURE-START'),'D-Start movement uses start dates');

CREATE TEMP TABLE actual_future AS SELECT * FROM reporting.design_overview_future_activity_detail;
SELECT pg_temp.require((SELECT count(*)=1 AND min(future_period_offset)=1 FROM actual_future
 WHERE proj_id=601 AND task_code='FUTURE-START'),'D-Start future window uses start, not later finish');
SELECT pg_temp.require((SELECT count(*)=1 AND min(future_period_offset)=1 FROM actual_future
 WHERE proj_id=601 AND task_code='FUTURE-UPPER'),'future boundary belongs to exactly one 28-day window');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM actual_future WHERE task_code='START-ACTIVE' AND proj_id=601),
 'achieved Design Start checkpoint is not future or remaining work');

CREATE TEMP TABLE actual_summary AS SELECT * FROM reporting.design_overview_checkpoint_summary;
DO $$ DECLARE n bigint; BEGIN
 SELECT count(*) INTO n FROM actual_summary s WHERE s.planned_count IS NOT NULL
 AND s.planned_count<>s.achieved_count+s.missed_count+s.unknown_planned_count;
 PERFORM pg_temp.require(n=0,'all checkpoint period accounting reconciles');
 SELECT count(*) INTO n FROM actual_summary s WHERE s.baseline_planned_count IS NOT NULL
 AND s.baseline_planned_count<>s.baseline_achieved_count+s.baseline_missed_count+s.unknown_baseline_planned_count;
 PERFORM pg_temp.require(n=0,'all checkpoint baseline accounting reconciles');
 SELECT count(*) INTO n FROM actual_summary s WHERE s.remaining_count<>
 s.earlier_over_30_count+s.earlier_1_to_30_count+s.unchanged_count+s.later_1_to_30_count+s.later_over_30_count+s.new_or_no_prior_count;
 PERFORM pg_temp.require(n=0,'six movement bands cover all remaining checkpoints');
 SELECT count(*) INTO n FROM actual_summary s JOIN (
 SELECT proj_id,count(*) FILTER(WHERE is_planned) AS planned,count(*) FILTER(WHERE is_achieved) AS achieved,
 count(*) FILTER(WHERE is_unplanned) AS unplanned,avg(previous_movement_days) AS movement
 FROM actual_metrics GROUP BY proj_id) d USING(proj_id)
 WHERE s.checkpoint_key='all' AND ((s.is_comparable AND (s.planned_count<>d.planned OR s.achieved_count<>d.achieved OR s.unplanned_count<>d.unplanned))
 OR s.previous_movement_days IS DISTINCT FROM d.movement);
 PERFORM pg_temp.require(n=0,'summary uses the same identity sets and excludes NULL movement from means');
END $$;
SELECT pg_temp.require((SELECT completion_percent IS NULL AND unknown_plan_count>0 FROM actual_summary WHERE proj_id=601 AND checkpoint_key='all'),
 'unknown comparison population is not displayed as a certain completion percentage');
SELECT pg_temp.require((SELECT planned_count IS NULL AND completion_percent IS NULL FROM actual_summary WHERE proj_id=550 AND checkpoint_key='all'),
 'missing reporting period is not silently a zero plan');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM reporting.design_overview_history WHERE history_rank>5),'history is limited to five available cuts');
BEGIN;
SET LOCAL ROLE p6_reporting_external;
SELECT task_code,current_start,baseline_proj_id FROM reporting.design_overview_performance_detail LIMIT 1;
SELECT * FROM reporting.design_overview_future_code_counts LIMIT 1;
ROLLBACK;
BEGIN;
SET LOCAL ROLE p6_reporting_sync;
GRANT SELECT ON ALL TABLES IN SCHEMA reporting TO p6_reporting_reader;
ROLLBACK;
SELECT 'Replacement behavioral checks passed' AS result;
