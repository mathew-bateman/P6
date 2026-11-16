-- Independent transformation fixtures; deliberately minimal authoritative-source stubs.
\set ON_ERROR_STOP on
DO $$ BEGIN IF current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'isolated test database required'; END IF; END $$;
CREATE SCHEMA reporting;
CREATE TABLE reporting.projwbs (_source_system text,proj_id int,wbs_id int,parent_wbs_id int,seq_num int);
INSERT INTO reporting.projwbs VALUES ('p6_22',1,10,NULL,1),('p6_22',1,11,10,1),
 ('p6_22',1,20,21,2),('p6_22',1,21,20,3);
CREATE TABLE reporting.construction_activity_detail (
 proj_id int,proj_short_name text,project_code_id int,project_code text,"Project ID" text,
 period_code_id int,full_period_code text,period_serial int,data_date timestamp,cut_date date,_snapshot_at timestamptz,
 submission_index int,global_submission_index int,submission_label text,
 baseline_proj_id int,baseline_status text,baseline_match_status text,baseline_task_id int,
 task_id int,task_code text,task_name text,task_type text,status_code text,task_code_count bigint,
 activity_row_key text,wbs_id int,wbs_short_name text,wbs_name text,codes jsonb,
 discipline_status text,discipline_assignments jsonb,
 baseline_start timestamp,baseline_finish timestamp,current_start timestamp,current_finish timestamp);
INSERT INTO reporting.construction_activity_detail
 (proj_id,project_code_id,task_id,task_code,task_name,task_type,activity_row_key,wbs_id,baseline_match_status,
 discipline_status,discipline_assignments,baseline_start,baseline_finish,current_start,current_finish,codes)
 VALUES (1,7,100,'A','Dual representation','TT_Task','1:100',11,'ready','conflict',
 '[{"type_id":383,"value_id":12022,"short_name":"C","value":"Civils"},{"type_id":383,"value_id":12023,"short_name":"C","value":"Telecoms"}]',
 '2026-01-01','2026-01-05','2026-02-01','2026-02-05',
 '{"milestone_type":{"valid_value_count":2,"assignments":[{"value_id":1},{"value_id":2}]}}');
CREATE TABLE reporting.construction_milestone_detail (LIKE reporting.construction_activity_detail,
 scheduled_start_date timestamp,scheduled_finish_date timestamp,baseline_date timestamp,"current_date" timestamp,
 classification_source text,milestone_type_count int,milestone_assignments jsonb);
INSERT INTO reporting.construction_milestone_detail
 SELECT a.*,current_start,current_finish,baseline_finish,current_finish,'code',2,
 '[{"value_id":1},{"value_id":2}]' FROM reporting.construction_activity_detail a;
UPDATE reporting.construction_milestone_detail SET codes=codes||jsonb_build_object('discipline',
 jsonb_build_object('bucket_status',discipline_status,'assignments',discipline_assignments));
INSERT INTO reporting.construction_milestone_detail
 (proj_id,project_code_id,task_id,task_code,task_type,activity_row_key,wbs_id,baseline_match_status,codes,
 baseline_date,"current_date",classification_source,milestone_type_count)
 VALUES (1,7,101,'B','TT_Mile','1:101',20,'missing_baseline_activity',
 '{"discipline":{"bucket_status":"uncoded","assignments":[]}}',NULL,'2026-03-01','p6_type',0);
CREATE TABLE reporting.construction_milestone_type_bridge(activity_row_key text,milestone_value_id int);
-- Duplicate raw assignment deliberately: EXISTS filtering must not multiply rows.
INSERT INTO reporting.construction_milestone_type_bridge VALUES ('1:100',1),('1:100',1),('1:100',2);
