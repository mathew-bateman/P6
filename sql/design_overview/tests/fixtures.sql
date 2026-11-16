-- Additive, synthetic Design Overview fixtures. Run AFTER coding_check/tests/fixtures.sql.
-- The coding fixture supplies raw tables and reader/sync roles; no production data is used.
DO $$ BEGIN
  IF current_database() NOT LIKE 'coding_check_test_%' THEN
    RAISE EXCEPTION 'Design Overview fixtures require a coding_check_test_* database';
  END IF;
END $$;

ALTER TABLE reporting.project ADD COLUMN IF NOT EXISTS orig_proj_id integer;
ALTER TABLE reporting.project ADD COLUMN IF NOT EXISTS source_proj_id integer;
ALTER TABLE reporting.project ADD COLUMN IF NOT EXISTS last_recalc_date timestamp;
ALTER TABLE reporting.pcatval ADD COLUMN IF NOT EXISTS parent_proj_catg_id integer;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS early_start_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS early_end_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS restart_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS reend_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS target_start_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS target_end_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS act_start_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS act_end_date timestamp;
ALTER TABLE reporting.task ADD COLUMN IF NOT EXISTS guid text;

INSERT INTO reporting.pcatval
(_source_system,proj_catg_id,proj_catg_type_id,proj_catg_short_name,proj_catg_name,parent_proj_catg_id) VALUES
('p6_22',31000,3,'DESIGN-A','Design Comparison A',NULL),
('p6_22',31001,3,'DESIGN-GAP','Missing Predecessor',NULL),
('p6_22',31002,3,'DESIGN-DUP','Duplicate Submission',NULL),
('p6_22',31003,3,'DESIGN-DATE','Invalid Data Dates',NULL),
('p6_22',31004,3,'DESIGN-YEAR','Financial Year Boundary',NULL),
('p6_22',32026,6,'2026/27','Year',NULL),
('p6_22',32027,6,'2027/28','Year',NULL),
('p6_22',32605,6,'P5','Period 05',32026),
('p6_22',32606,6,'P6','Period 06',32026),
('p6_22',32607,6,'P7','Period 07',32026),
('p6_22',32608,6,'P8','Period 08',32026),
('p6_22',32613,6,'P13','Period 13',32026),
('p6_22',32701,6,'P1','Period 01',32027);

INSERT INTO reporting.project
(_source_system,proj_id,proj_short_name,_project_key,orig_proj_id,last_recalc_date) VALUES
('p6_22',500,'DESIGN-A-P6','p6_22:500',NULL,'2026-09-20 00:01'),
('p6_22',501,'DESIGN-A-P7','p6_22:501',NULL,'2026-10-18 00:01'),
('p6_22',502,'DESIGN-A-STORED-BASELINE','p6_22:502',501,'2026-10-18 00:01'),
('p6_22',510,'DESIGN-GAP-P5','p6_22:510',NULL,'2026-08-23 00:01'),
('p6_22',511,'DESIGN-GAP-P7','p6_22:511',NULL,'2026-10-18 00:01'),
('p6_22',520,'DESIGN-DUP-P6','p6_22:520',NULL,'2026-09-20 00:01'),
('p6_22',521,'DESIGN-DUP-P7-A','p6_22:521',NULL,'2026-10-18 00:01'),
('p6_22',522,'DESIGN-DUP-P7-B','p6_22:522',NULL,'2026-10-18 00:01'),
('p6_22',530,'DESIGN-DATE-P6','p6_22:530',NULL,'2026-09-20 00:01'),
('p6_22',531,'DESIGN-DATE-P7','p6_22:531',NULL,'2026-09-19 00:01'),
('p6_22',540,'DESIGN-YEAR-P13','p6_22:540',NULL,'2027-03-28 00:01'),
('p6_22',541,'DESIGN-YEAR-P1','p6_22:541',NULL,'2027-04-25 00:01'),
('p6_22',550,'DESIGN-NO-PERIOD','p6_22:550',NULL,'2026-10-18 00:01');

INSERT INTO reporting.projpcat(_source_system,proj_id,proj_catg_type_id,proj_catg_id)
SELECT 'p6_22',p,type_id,value_id
FROM (VALUES(500),(501),(502),(510),(511),(520),(521),(522),(530),(531),(540),(541),(550)) projects(p)
CROSS JOIN (VALUES(1,1001),(2,2001),(4,4001),(5,5001)) codes(type_id,value_id);
INSERT INTO reporting.projpcat(_source_system,proj_id,proj_catg_type_id,proj_catg_id)
SELECT 'p6_22',p,3,project_code_id FROM (VALUES
(500,31000),(501,31000),(502,31000),(510,31001),(511,31001),
(520,31002),(521,31002),(522,31002),(530,31003),(531,31003),
(540,31004),(541,31004),(550,31000)) x(p,project_code_id);
INSERT INTO reporting.projpcat(_source_system,proj_id,proj_catg_type_id,proj_catg_id)
SELECT 'p6_22',p,6,period_code_id FROM (VALUES
(500,32606),(501,32607),(502,32607),(510,32605),(511,32607),
(520,32606),(521,32607),(522,32607),(530,32606),(531,32607),
(540,32613),(541,32701)) x(p,period_code_id);

-- Design membership is Global only. Values must exist, be active, and be nonblank.
INSERT INTO reporting.actvtype
(_source_system,actv_code_type_id,actv_code_type,actv_code_type_scope,proj_id) VALUES
('p6_22',350,'Design Key','AS_Global',NULL),
('p6_22',351,'Design Key','AS_Project',500);
INSERT INTO fixture_generation_a.actvcode
(_source_system,actv_code_id,actv_code_type_id,short_name,actv_code_name,seq_num) VALUES
('p6_22',3501,350,'D-Start','Design Start',1),
('p6_22',3502,350,'IDC','IDC Meeting',2),
('p6_22',3503,350,'EMPTY','   ',3),
('p6_22',3504,350,' ','   ',4),
('p6_22',3511,351,'LOCAL','Project scoped Design Key',1);

-- Prior P6 schedules. Task IDs intentionally differ from current IDs.
INSERT INTO reporting.task
(_source_system,proj_id,task_id,task_code,task_name,task_type,status_code,
 early_start_date,early_end_date,restart_date,reend_date,target_start_date,target_end_date,
 _project_key,_activity_key,guid)
SELECT 'p6_22',500,50000+n,code,code,kind,'TK_NotStart',start_date,finish_date,
 start_date,finish_date,start_date,finish_date,'p6_22:500','p6_22:500:'||(50000+n),'PRIOR-'||n
FROM (VALUES
(1,'ACHIEVED','TT_Task',timestamp '2026-09-01',timestamp '2026-10-01'),
(2,'MISSED','TT_Task',timestamp '2026-09-01',timestamp '2026-10-02'),
(3,'UNPLANNED-EXISTING','TT_Task',timestamp '2026-09-01',timestamp '2026-10-20'),
(4,'START-MILESTONE','TT_Mile',timestamp '2026-09-20 00:01',NULL::timestamp),
(5,'FINISH-END-BOUNDARY','TT_FinMile',NULL::timestamp,timestamp '2026-10-18 00:01'),
(6,'SPAN-PERIODS','TT_Task',timestamp '2026-07-01',timestamp '2026-10-03'),
(7,'REMOVED-ACTIVITY','TT_Task',timestamp '2026-09-01',timestamp '2026-10-04'),
(8,'REMOVED-DESIGN-KEY','TT_Task',timestamp '2026-09-01',timestamp '2026-10-05'),
(9,'RECODED-DESIGN','TT_Task',timestamp '2026-09-01',timestamp '2026-10-06'),
(10,'ADDED-DESIGN-KEY','TT_Task',timestamp '2026-09-01',timestamp '2026-10-07'),
(11,'NON-DESIGN','TT_Task',timestamp '2026-09-01',timestamp '2026-10-08'),
(12,'CONFLICT-DESIGN','TT_Task',timestamp '2026-09-01',timestamp '2026-10-09'),
(13,'DUPLICATE-CODE','TT_Task',timestamp '2026-09-01',timestamp '2026-10-10'),
(14,'DUPLICATE-CODE','TT_Task',timestamp '2026-09-01',timestamp '2026-10-10'),
(15,'LOCAL-DESIGN-ONLY','TT_Task',timestamp '2026-09-01',timestamp '2026-10-11'),
(16,'BLANK-DESIGN-ONLY','TT_Task',timestamp '2026-09-01',timestamp '2026-10-12'),
(17,'MISSING-SCHEDULE-DATE','TT_Task',timestamp '2026-09-01',NULL::timestamp),
(18,'PHYSICALLY-DELETED','TT_Task',timestamp '2026-09-01',timestamp '2026-10-13'),
(19,'RETIRED-DEL','TT_Task',timestamp '2026-09-01',timestamp '2026-10-14'),
(20,'BEFORE-START','TT_Task',timestamp '2026-09-01',timestamp '2026-09-20 00:00'),
(21,'AT-END','TT_Task',timestamp '2026-09-01',timestamp '2026-10-18 00:01')
) x(n,code,kind,start_date,finish_date);

-- Current rows, except REMOVED-ACTIVITY; NON-DESIGN keeps no design membership.
INSERT INTO reporting.task
(_source_system,proj_id,task_id,task_code,task_name,task_type,status_code,
 early_start_date,early_end_date,restart_date,reend_date,target_start_date,target_end_date,
 act_start_date,act_end_date,_project_key,_activity_key,guid,delete_date)
SELECT 'p6_22',501,51000+n,code,code,kind,state,start_date,finish_date,
 start_date,finish_date,start_date,finish_date,actual_start,actual_finish,
 'p6_22:501','p6_22:501:'||(51000+n),'CURRENT-'||n,deleted
FROM (VALUES
(1,'ACHIEVED','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-01',timestamp '2026-09-01',timestamp '2026-10-01',NULL::timestamp),
(2,'MISSED','TT_Task','TK_Active',timestamp '2026-09-01',timestamp '2026-10-20',timestamp '2026-09-01',NULL::timestamp,NULL::timestamp),
(3,'UNPLANNED-EXISTING','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-20',timestamp '2026-09-01',timestamp '2026-10-02',NULL::timestamp),
(4,'START-MILESTONE','TT_Mile','TK_Complete',timestamp '2026-09-20 00:01',NULL::timestamp,timestamp '2026-09-20 00:01',NULL::timestamp,NULL::timestamp),
(5,'FINISH-END-BOUNDARY','TT_FinMile','TK_Complete',NULL::timestamp,timestamp '2026-10-18 00:01',NULL::timestamp,timestamp '2026-10-18 00:01',NULL::timestamp),
(6,'SPAN-PERIODS','TT_Task','TK_Complete',timestamp '2026-07-01',timestamp '2026-10-03',timestamp '2026-07-01',timestamp '2026-10-03',NULL::timestamp),
(8,'REMOVED-DESIGN-KEY','TT_Task','TK_Active',timestamp '2026-09-01',timestamp '2026-10-20',timestamp '2026-09-01',NULL::timestamp,NULL::timestamp),
(9,'RECODED-DESIGN','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-06',timestamp '2026-09-01',timestamp '2026-10-06',NULL::timestamp),
(10,'ADDED-DESIGN-KEY','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-07',timestamp '2026-09-01',timestamp '2026-10-07',NULL::timestamp),
(11,'NON-DESIGN','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-08',timestamp '2026-09-01',timestamp '2026-10-08',NULL::timestamp),
(12,'CONFLICT-DESIGN','TT_Task','TK_Active',timestamp '2026-09-01',timestamp '2026-10-20',timestamp '2026-09-01',NULL::timestamp,NULL::timestamp),
(13,'DUPLICATE-CODE','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-10',timestamp '2026-09-01',timestamp '2026-10-10',NULL::timestamp),
(15,'LOCAL-DESIGN-ONLY','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-11',timestamp '2026-09-01',timestamp '2026-10-11',NULL::timestamp),
(16,'BLANK-DESIGN-ONLY','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-12',timestamp '2026-09-01',timestamp '2026-10-12',NULL::timestamp),
(17,'MISSING-SCHEDULE-DATE','TT_Task','TK_Complete',timestamp '2026-09-01',NULL::timestamp,timestamp '2026-09-01',timestamp '2026-10-12',NULL::timestamp),
(18,'PHYSICALLY-DELETED','TT_Task','TK_NotStart',timestamp '2026-09-01',timestamp '2026-10-13',NULL::timestamp,NULL::timestamp,timestamp '2026-10-10'),
(19,'RETIRED-DEL','TT_Task','TK_NotStart',timestamp '2026-09-01',timestamp '2026-10-14',NULL::timestamp,NULL::timestamp,NULL::timestamp),
(20,'BEFORE-START','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-09-20 00:00',timestamp '2026-09-01',timestamp '2026-09-20 00:00',NULL::timestamp),
(21,'AT-END','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-18 00:01',timestamp '2026-09-01',timestamp '2026-10-18 00:01',NULL::timestamp),
(22,'UNPLANNED-NEW','TT_Task','TK_Complete',timestamp '2026-09-01',timestamp '2026-10-04',timestamp '2026-09-01',timestamp '2026-10-04',NULL::timestamp)
) x(n,code,kind,state,start_date,finish_date,actual_start,actual_finish,deleted);

INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id)
SELECT 'p6_22',500,50000+n,350,3501 FROM generate_series(1,21) n
WHERE n NOT IN(10,11,15,16);
INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id)
SELECT 'p6_22',501,51000+n,350,CASE WHEN n=9 THEN 3502 ELSE 3501 END
FROM generate_series(1,22) n WHERE n NOT IN(7,8,11,14,15,16);
INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id) VALUES
('p6_22',500,50012,350,3502),('p6_22',501,51012,350,3502),
('p6_22',500,50015,351,3511),('p6_22',501,51015,351,3511),
('p6_22',500,50016,350,3504),('p6_22',501,51016,350,3504),
('p6_22',501,51019,90,900),
-- Duplicate identical assignment must not inflate membership or counts.
('p6_22',501,51001,350,3501),
('p6_22',500,50001,20,201),('p6_22',501,51001,20,202),
('p6_22',500,50001,40,401),('p6_22',501,51001,40,402),
('p6_22',500,50001,10,101),('p6_22',501,51001,10,102);

-- Baseline shares the period and activity code but must never multiply the pair.
INSERT INTO reporting.task(_source_system,proj_id,task_id,task_code,task_name,
 early_start_date,early_end_date,restart_date,reend_date,_project_key,_activity_key)
VALUES ('p6_22',502,52001,'ACHIEVED','Stored baseline only','2026-09-01','2026-10-01',
'2026-09-01','2026-10-01','p6_22:502','p6_22:502:52001');
INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id)
VALUES ('p6_22',502,52001,350,3501);
