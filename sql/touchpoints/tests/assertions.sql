\set ON_ERROR_STOP on
DO $$ BEGIN IF current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'Test database required'; END IF; END $$;
BEGIN;
SET LOCAL statement_timeout='120s';
CREATE OR REPLACE FUNCTION pg_temp.require(ok boolean,label text) RETURNS void LANGUAGE plpgsql AS $$
BEGIN IF ok IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'Touchpoints regression: %',label; END IF; END $$;
CREATE TEMP TABLE movement ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_finish_movement;
CREATE TEMP TABLE activity ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_activity_detail;
CREATE TEMP TABLE summary ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_movement_summary;
CREATE TEMP TABLE history ON COMMIT DROP AS SELECT * FROM reporting.touchpoint_finish_history;
SELECT pg_temp.require((SELECT previous_movement_days=10 AND baseline_movement_days=10 AND movement_status='Delayed'
 AND total_float_days_8h=10 AND "Responsible Company"=responsible_company AND notebook_count=2
 AND commentary LIKE '%Planner comment%' AND commentary LIKE '%Review note%' AND commentary LIKE '%Notebook A%'
 AND commentary NOT LIKE '%Deleted note%' AND jsonb_array_length(udf_notes)=2
 FROM movement WHERE proj_id=701 AND task_code='DELAY'),'dates, owner aliases, float and non-multiplying commentary');
SELECT pg_temp.require((SELECT previous_movement_days=-5 AND movement_status='Improved' AND delay_band IS NULL
 FROM movement WHERE proj_id=701 AND task_code='IMPROVE'),'negative movement is improvement, not delay');
SELECT pg_temp.require((SELECT previous_movement_days=0 AND movement_status='No movement' AND delay_band IS NULL
 FROM movement WHERE proj_id=701 AND task_code='ZERO'),'zero is not in the delayed-only first band');
SELECT pg_temp.require((SELECT previous_match_status='no_previous_activity' AND previous_movement_days IS NULL
 AND movement_status='No comparison' AND membership_change='not_in_previous_cut'
 FROM movement WHERE proj_id=701 AND task_code='NEW'),'unmatched additions stay unknown');
SELECT pg_temp.require((SELECT membership_change='removed_activity' AND current_task_id IS NULL AND previous_task_id IS NOT NULL
 AND is_remaining IS NULL FROM movement WHERE proj_id=701 AND task_code='REMOVED'),'removed previous work stays visible');
SELECT pg_temp.require((SELECT NOT is_current_touchpoint AND is_previous_touchpoint AND membership_change='removed_touchpoint_coding'
 FROM movement WHERE proj_id=701 AND task_code='REMOVED-CODE'),'removing coding cannot erase previous membership');
SELECT pg_temp.require((SELECT previous_match_status='ready' AND previous_movement_days=0 AND membership_change='added_touchpoint_coding'
 FROM movement WHERE proj_id=701 AND task_code='ADDED-CODE'),'uncoded previous task remains a valid comparator');
SELECT pg_temp.require((SELECT baseline_match_status='ready' AND baseline_codes#>>'{milestone_type,code_short_name}'='INTERFACE'
 FROM movement WHERE proj_id=701 AND task_code='DELAY'),'legacy baseline codes remain comparable');
SELECT pg_temp.require((SELECT movement_status='Completed' AND NOT is_remaining AND current_finish=timestamp '2026-10-12'
 FROM movement WHERE proj_id=701 AND task_code='COMPLETE'),'completed tasks use actual finish and leave remaining population');
SELECT pg_temp.require((SELECT previous_movement_days=30 FROM movement WHERE proj_id=701 AND task_code='START-MILE'),
 'start milestone movement is explicitly finish-based');
SELECT pg_temp.require((SELECT current_finish=timestamp '2026-10-08' AND finish_date_source='target_end_date' AND previous_movement_days=-2
 FROM movement WHERE proj_id=701 AND task_code='TARGET-ONLY'),'planned finish fallback follows canonical helper');
SELECT pg_temp.require((SELECT previous_match_status='missing_date' AND previous_movement_days IS NULL
 FROM movement WHERE proj_id=701 AND task_code='NO-DATE'),'missing dates are not zero');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM movement WHERE proj_id=701 AND task_code IN('DUP-CURRENT','DUP-PREVIOUS')
 AND (previous_match_status<>'ambiguous_activity_id' OR previous_movement_days IS NOT NULL)),
 'duplicate current or previous IDs never produce arbitrary comparisons');
SELECT pg_temp.require((SELECT baseline_match_status='ambiguous_activity_id' AND baseline_movement_days IS NULL
 FROM movement WHERE proj_id=701 AND task_code='DUP-BASELINE'),'duplicate baseline ID remains unknown');
SELECT pg_temp.require((SELECT is_remaining IS NULL AND movement_status='Unknown status'
 FROM movement WHERE proj_id=701 AND task_code='UNKNOWN-STATUS'),'unknown status is not assumed remaining');
SELECT pg_temp.require((SELECT count(*)=2 AND bool_and(previous_match_status='missing_activity_id')
 FROM movement WHERE proj_id=701 AND task_code IS NULL),'missing Activity IDs do not match each other');
SELECT pg_temp.require((SELECT name_direction_conflict FROM activity WHERE proj_id=701 AND task_code='NAME-CONFLICT'),
 'name/code disagreement is flagged without recoding');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM activity WHERE proj_id=701 AND task_code IN('LOCAL-ONLY','PHYSICAL-DELETE','RETIRED-DEL','DEAD-ASSIGNMENT','SOURCE-23-COLLISION')),
 'Global-only, physical deletion, any-scope DEL, deleted assignment and source isolation');
SELECT pg_temp.require((SELECT touchpoint_code_conflict AND NOT is_touchpoint FROM activity WHERE proj_id=701 AND task_code='CODE-CONFLICT'),
 'conflicting Milestone Type remains in review detail, excluded from counted membership');
SELECT pg_temp.require((SELECT route_status='conflicting_routes' AND from_company IS NULL AND to_company IS NULL
 FROM activity WHERE proj_id=701 AND task_code='ROUTE-CONFLICT'),'multiple directions never select an arbitrary endpoint');
SELECT pg_temp.require((SELECT route_status='unstructured_route' AND from_company IS NULL
 FROM activity WHERE proj_id=701 AND task_code='UNSTRUCTURED-ROUTE'),'unstructured route text is retained without invented parties');
SELECT pg_temp.require((SELECT from_company='Amey' AND to_company='Network Rail' AND route_status='coded_direction'
 FROM activity WHERE proj_id=701 AND task_code='DELAY') AND
 (SELECT from_company='Network Rail' AND to_company='Amey' FROM activity WHERE proj_id=700 AND task_code='DELAY'),
 'opposite meanings of the same short code use each project dictionary description');
SELECT pg_temp.require((SELECT membership_change='reclassified_touchpoint' AND is_reclassified
 FROM movement WHERE proj_id=701 AND task_code='RECLASSIFY'),'GIVE/GET recoding is visible');
SELECT pg_temp.require((SELECT previous_match_status='cut_not_comparable' AND previous_movement_days IS NULL
 FROM movement WHERE proj_id=721 AND task_code='GAP'),'missing exact previous period never falls back across a gap');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM history WHERE proj_id=701 AND historical_proj_id=702),
 'recent history is relative to selected cut, never a later cut');
SELECT pg_temp.require((SELECT historical_finish=timestamp '2026-10-10' AND history_match_status='ready' AND history_period_offset=1
 FROM history WHERE proj_id=701 AND task_code='ADDED-CODE' AND historical_proj_id=700),
 'history matches uncoded historical records by exact unique ID');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM movement GROUP BY proj_id,activity_row_key HAVING count(*)>1),
 'review detail has a stable unique row key');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM activity GROUP BY proj_id,task_id HAVING count(*)>1),
 'commentary, dictionaries and links do not multiply activities');
SELECT pg_temp.require((SELECT count(*)=5 FROM information_schema.columns WHERE table_schema='reporting'
 AND table_name LIKE 'touchpoint_%' AND column_name='Responsible Company'), 'all five views expose company filter');
SELECT pg_temp.require((SELECT count(*)=5 FROM information_schema.columns WHERE table_schema='reporting'
 AND table_name LIKE 'touchpoint_%' AND column_name='Project ID'), 'all five views expose Project ID filter');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM summary WHERE remaining_count<>delayed_count+improved_count+no_movement_count+no_comparison_count),
 'summary categories reconcile within every filter combination');
SELECT pg_temp.require((SELECT sum(activity_count) FROM summary WHERE proj_id=701)=
 (SELECT count(*) FROM activity WHERE proj_id=701 AND is_touchpoint),'summary total equals individual eligible activity records');
SELECT pg_temp.require((SELECT sum(remaining_count) FROM summary WHERE proj_id=701)=
 (SELECT count(*) FROM movement WHERE proj_id=701 AND is_current_touchpoint AND is_remaining),'remaining summary equals matching detail');
SELECT pg_temp.require(NOT EXISTS(SELECT 1 FROM movement WHERE proj_id=701 AND task_code LIKE 'BAND-%' AND
 delay_band_order IS DISTINCT FROM CASE WHEN substring(task_code FROM 6)::int<=20 THEN 1 WHEN substring(task_code FROM 6)::int<=40 THEN 2
 WHEN substring(task_code FROM 6)::int<=60 THEN 3 WHEN substring(task_code FROM 6)::int<=80 THEN 4
 WHEN substring(task_code FROM 6)::int<=100 THEN 5 ELSE 6 END),'every delay-band boundary');
SET LOCAL ROLE p6_reporting_external;
SELECT "Project ID","Account","Lead Planner","Responsible Company","Period Code" FROM reporting.touchpoint_activity_detail LIMIT 1;
SELECT count(*) FROM reporting.touchpoint_interface_routes;
SELECT count(*) FROM reporting.touchpoint_finish_movement;
SELECT count(*) FROM reporting.touchpoint_finish_history;
SELECT count(*) FROM reporting.touchpoint_movement_summary;
RESET ROLE;
COMMIT;
SELECT 'Touchpoints behavioral checks passed' AS result;
