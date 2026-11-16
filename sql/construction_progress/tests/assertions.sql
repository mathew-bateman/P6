DO $$ BEGIN
 IF (SELECT count(*) FROM reporting.construction_activity_detail WHERE proj_id=701 AND task_code='CP1')<>1 THEN RAISE EXCEPTION 'duplicate assignment inflated activity'; END IF;
 IF EXISTS(SELECT FROM reporting.construction_activity_detail WHERE proj_id=701 AND task_code IN ('CP4','CP5')) THEN RAISE EXCEPTION 'excluded type counted'; END IF;
 IF NOT EXISTS(SELECT FROM reporting.construction_activity_movement WHERE proj_id=701 AND task_code='CP1' AND actual_complete AND baseline_planned_complete AND previous_planned_complete AND previous_membership_status='recoded_to_es6') THEN RAISE EXCEPTION 'comparable activity failed'; END IF;
 IF NOT EXISTS(SELECT FROM reporting.construction_activity_movement WHERE proj_id=701 AND task_code='CP2' AND previous_match_status='missing_previous_activity' AND previous_planned_complete IS NULL) THEN RAISE EXCEPTION 'missing prior activity failed'; END IF;
 IF NOT EXISTS(SELECT FROM reporting.construction_activity_movement WHERE proj_id=701 AND task_code='CP3' AND previous_match_status='missing_previous_forecast_finish' AND previous_planned_complete IS NULL) THEN RAISE EXCEPTION 'missing prior date failed'; END IF;
 IF NOT EXISTS(SELECT FROM reporting.construction_activity_movement WHERE proj_id=701 AND task_code='CPDUP' AND previous_match_status='ambiguous_previous_activity_id') THEN RAISE EXCEPTION 'duplicate previous ID not flagged'; END IF;
 IF (SELECT count(*) FROM reporting.construction_milestone_detail WHERE proj_id=701 AND task_code='CP6')<>1 OR (SELECT count(*) FROM reporting.construction_milestone_type_bridge WHERE proj_id=701 AND task_id=799009)<>2 THEN RAISE EXCEPTION 'milestone bridge cardinality failed'; END IF;
 IF NOT EXISTS(SELECT FROM construction_progress.cuts WHERE proj_id=721 AND chronological_previous_proj_id=720 AND period_gap) THEN RAISE EXCEPTION 'gap predecessor failed'; END IF;
END $$;

-- Adding a second previous cut on the same calendar day must make it ambiguous.
UPDATE reporting.project SET last_recalc_date='2026-09-20 14:00' WHERE proj_id=702;
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM construction_progress.cuts WHERE proj_id=701 AND chronological_previous_status='ambiguous_previous_data_date' AND chronological_previous_proj_id IS NULL) THEN RAISE EXCEPTION 'date tie not flagged'; END IF;
END $$;
