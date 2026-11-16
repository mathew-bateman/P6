-- Production-safe checks: source data is read only; verification tables are temporary.
\set ON_ERROR_STOP on
\timing on
BEGIN ISOLATION LEVEL REPEATABLE READ;
SET LOCAL statement_timeout='120s';
SET LOCAL lock_timeout='8s';
CREATE TEMP TABLE tp_activity ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_activity_detail;
CREATE TEMP TABLE tp_routes ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_interface_routes;
CREATE TEMP TABLE tp_movement ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_finish_movement;
CREATE TEMP TABLE tp_history ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_finish_history;
CREATE TEMP TABLE tp_summary ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_movement_summary;
DO $$ DECLARE n bigint; BEGIN
 SELECT count(*) INTO n FROM(SELECT proj_id,task_id FROM tp_activity GROUP BY 1,2 HAVING count(*)<>1)q;
 IF n<>0 THEN RAISE EXCEPTION 'Touchpoint activity rows were multiplied'; END IF;
 SELECT count(*) INTO n FROM(SELECT proj_id,activity_row_key FROM tp_movement GROUP BY 1,2 HAVING count(*)<>1)q;
 IF n<>0 THEN RAISE EXCEPTION 'Movement row key is not unique'; END IF;
 IF (SELECT count(*) FROM tp_activity)<>(SELECT count(*) FROM tp_routes) THEN RAISE EXCEPTION 'Route/activity grain mismatch'; END IF;
 SELECT count(*) INTO n FROM tp_movement WHERE
  (previous_match_status<>'ready' AND previous_movement_days IS NOT NULL)
  OR (baseline_match_status<>'ready' AND baseline_movement_days IS NOT NULL)
  OR (previous_match_status='ready' AND previous_movement_days IS DISTINCT FROM current_finish::date-previous_finish::date)
  OR (baseline_match_status='ready' AND baseline_movement_days IS DISTINCT FROM current_finish::date-baseline_finish::date);
 IF n<>0 THEN RAISE EXCEPTION 'Movement/date or unknown-state mismatch'; END IF;
 SELECT count(*) INTO n FROM tp_summary WHERE remaining_count<>delayed_count+improved_count+no_movement_count+no_comparison_count;
 IF n<>0 THEN RAISE EXCEPTION 'Donut category reconciliation failed'; END IF;
 WITH expected AS(SELECT proj_id,count(*) AS n,count(*) FILTER(WHERE is_remaining) AS remaining FROM tp_movement WHERE is_current_touchpoint GROUP BY proj_id),
 observed AS(SELECT proj_id,sum(activity_count) AS n,sum(remaining_count) AS remaining FROM tp_summary GROUP BY proj_id)
 SELECT count(*) INTO n FROM expected e FULL JOIN observed o USING(proj_id)
 WHERE e.n IS DISTINCT FROM o.n OR e.remaining IS DISTINCT FROM o.remaining;
 IF n<>0 THEN RAISE EXCEPTION 'Summary and activity evidence differ'; END IF;
 WITH d AS(SELECT proj_id,responsible_company,project_stage,discipline,give_get,from_company,to_company,route_status,is_remaining,movement_status,delay_band,
 count(*) n FROM tp_movement WHERE is_current_touchpoint GROUP BY 1,2,3,4,5,6,7,8,9,10,11),
 g AS(SELECT proj_id,responsible_company,project_stage,discipline,give_get,from_company,to_company,route_status,is_remaining,movement_status,delay_band,
 sum(activity_count) n FROM tp_summary GROUP BY 1,2,3,4,5,6,7,8,9,10,11)
 SELECT count(*) INTO n FROM((SELECT * FROM d EXCEPT SELECT * FROM g) UNION ALL(SELECT * FROM g EXCEPT SELECT * FROM d))q;
 IF n<>0 THEN RAISE EXCEPTION 'Company/route/code filter reconciliation failed'; END IF;
 SELECT count(*) INTO n FROM tp_movement WHERE delay_band IS NOT NULL AND
  (previous_movement_days<=0 OR is_remaining IS NOT TRUE OR NOT is_current_touchpoint);
 IF n<>0 THEN RAISE EXCEPTION 'Delay bands contain non-delayed work'; END IF;
 SELECT count(*) INTO n FROM tp_history WHERE historical_period_serial>period_serial OR recent_cut_rank>5
 OR (history_match_status<>'ready' AND historical_finish IS NOT NULL);
 IF n<>0 THEN RAISE EXCEPTION 'History leaked a later cut or ambiguous finish'; END IF;
 SELECT count(*) INTO n FROM information_schema.columns WHERE table_schema='reporting' AND table_name LIKE 'touchpoint_%'
 AND column_name IN('Project ID','Account','Lead Planner','Responsible Company','Period','Period Code','Project State','Project Status');
 IF n<>40 THEN RAISE EXCEPTION 'Common filter columns missing: expected 40, found %',n; END IF;
END $$;
SELECT proj_short_name,full_period_code,count(*) AS activities,count(*) FILTER(WHERE is_touchpoint) AS counted_touchpoints,
 count(*) FILTER(WHERE is_remaining AND is_touchpoint) AS remaining,count(*) FILTER(WHERE route_status='coded_direction') AS route_coded
FROM tp_activity WHERE submission_index=1 GROUP BY 1,2 ORDER BY 1;
SELECT movement_status,count(*) AS remaining FROM tp_movement
 WHERE submission_index=1 AND is_current_touchpoint AND is_remaining GROUP BY 1 ORDER BY 1;
SELECT delay_band,count(*) AS delayed FROM tp_movement
 WHERE submission_index=1 AND movement_status='Delayed' GROUP BY delay_band,delay_band_order ORDER BY delay_band_order;
SELECT count(*) AS latest_activities,count(*) FILTER(WHERE previous_match_status='ready') AS previous_matches,
 count(*) FILTER(WHERE baseline_match_status='ready') AS baseline_matches,
 count(*) FILTER(WHERE name_direction_conflict) AS name_code_conflicts
FROM tp_movement WHERE submission_index=1 AND is_current_touchpoint;
SET LOCAL ROLE p6_reporting_external;
SELECT "Project ID","Account","Lead Planner","Responsible Company","Period","Period Code"
 FROM reporting.touchpoint_activity_detail LIMIT 1;
SELECT task_code,route_status FROM reporting.touchpoint_interface_routes LIMIT 1;
SELECT task_code,previous_movement_days,baseline_movement_days FROM reporting.touchpoint_finish_movement LIMIT 1;
SELECT task_code,historical_full_period_code,historical_finish FROM reporting.touchpoint_finish_history LIMIT 1;
SELECT "Responsible Company",movement_status,remaining_count FROM reporting.touchpoint_movement_summary LIMIT 1;
RESET ROLE;
COMMIT;
SELECT 'Touchpoints live reconciliation and reader checks passed' AS result;
