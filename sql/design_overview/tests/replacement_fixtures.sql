-- Run after the original synthetic coding/design fixtures, in an isolated DB.
DO $$ BEGIN IF current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'Test database required'; END IF; END $$;
INSERT INTO reporting.pcatval(_source_system,proj_catg_type_id,proj_catg_id,proj_catg_short_name,proj_catg_name)
VALUES('p6_22',2,39000,'BL','Baseline'),('p6_22',3,31005,'DESIGN-V2','Design replacement');
INSERT INTO reporting.project(_source_system,proj_id,proj_short_name,_project_key,last_recalc_date)
SELECT 'p6_22',id,name,'p6_22:'||id,dd FROM (VALUES
 (600,'V2-P6',timestamp '2026-09-20 00:01'),(601,'V2-P7',timestamp '2026-10-18 00:01'),
 (610,'V2-BASE-P5',timestamp '2026-08-23 00:01'),(611,'V2-BASE-P6',timestamp '2026-09-20 00:01'),
 (620,'TIED-BASE-A',timestamp '2026-10-18'),(621,'TIED-BASE-B',timestamp '2026-10-18'),
 (630,'BASE-WITHOUT-PERIOD',timestamp '2026-10-18'),
 (640,'BASE-YEAR-P13',timestamp '2027-03-28'),(641,'BASE-YEAR-P1',timestamp '2027-04-25')
) p(id,name,dd);
INSERT INTO reporting.projpcat(_source_system,proj_id,proj_catg_type_id,proj_catg_id)
SELECT 'p6_22',id,kind,value FROM (VALUES
 (600,1,1001),(600,2,2001),(600,3,31005),(600,6,32606),
 (601,1,1001),(601,2,2001),(601,3,31005),(601,6,32607),
 (610,2,39000),(610,3,31005),(610,6,32605),
 (611,2,39000),(611,3,31005),(611,6,32606),
 (620,2,39000),(620,3,31003),(620,6,32607),
 (621,2,39000),(621,3,31003),(621,6,32607),
 (630,2,39000),(630,3,31001),
 (640,2,39000),(640,3,31004),(640,6,32613),
 (641,2,39000),(641,3,31004),(641,6,32701)
) c(id,kind,value);

INSERT INTO reporting.task(_source_system,proj_id,task_id,task_code,task_name,task_type,status_code,
 restart_date,reend_date,target_start_date,target_end_date,act_start_date,act_end_date)
SELECT 'p6_22',project_id,project_id*100+n,code,code,kind,state,rs,rf,rs,rf,ast,aft
FROM (VALUES
 (600,1,'START-ACTIVE','TT_Task','TK_NotStart',timestamp '2026-09-25',timestamp '2026-12-01',NULL::timestamp,NULL::timestamp),
 (601,1,'START-ACTIVE','TT_Task','TK_Active',timestamp '2026-10-12',timestamp '2026-12-20',timestamp '2026-10-01 09:00',NULL::timestamp),
 (600,2,'FINISH-COMPLETE','TT_Task','TK_NotStart',timestamp '2026-09-01',timestamp '2026-10-02',NULL::timestamp,NULL::timestamp),
 (601,2,'FINISH-COMPLETE','TT_Task','TK_Complete',NULL::timestamp,NULL::timestamp,timestamp '2026-09-01',timestamp '2026-10-04'),
 (600,3,'FINISH-MISSED','TT_Task','TK_NotStart',timestamp '2026-09-01',timestamp '2026-10-03',NULL::timestamp,NULL::timestamp),
 (601,3,'FINISH-MISSED','TT_Task','TK_Active',timestamp '2026-10-18',timestamp '2026-11-20',timestamp '2026-09-01',NULL::timestamp),
 (600,4,'PULLED-FORWARD','TT_Task','TK_NotStart',timestamp '2026-09-01',timestamp '2026-11-01',NULL::timestamp,NULL::timestamp),
 (601,4,'PULLED-FORWARD','TT_Task','TK_Complete',NULL::timestamp,NULL::timestamp,timestamp '2026-09-01',timestamp '2026-10-12'),
 (600,5,'START-LOWER','TT_Task','TK_NotStart',timestamp '2026-09-20 00:00',timestamp '2026-12-01',NULL::timestamp,NULL::timestamp),
 (601,5,'START-LOWER','TT_Task','TK_Active',timestamp '2026-10-18',timestamp '2026-12-01',timestamp '2026-09-20 00:00',NULL::timestamp),
 (600,6,'START-UPPER','TT_Task','TK_NotStart',timestamp '2026-10-18 00:00',timestamp '2026-12-01',NULL::timestamp,NULL::timestamp),
 (601,6,'START-UPPER','TT_Task','TK_Active',timestamp '2026-10-18',timestamp '2026-12-01',timestamp '2026-10-18 00:00',NULL::timestamp),
 (600,7,'FUTURE-START','TT_Task','TK_NotStart',timestamp '2026-11-15',timestamp '2027-02-01',NULL::timestamp,NULL::timestamp),
 (601,7,'FUTURE-START','TT_Task','TK_NotStart',timestamp '2026-11-20',timestamp '2027-03-01',NULL::timestamp,NULL::timestamp),
 (601,8,'MISSING-PRIOR','TT_Task','TK_Complete',NULL::timestamp,NULL::timestamp,timestamp '2026-09-01',timestamp '2026-10-10'),
 (600,9,'REMOVED-ROW','TT_Task','TK_NotStart',timestamp '2026-09-01',timestamp '2026-10-10',NULL::timestamp,NULL::timestamp),
 (600,10,'FUTURE-UPPER','TT_FinMile','TK_NotStart',NULL::timestamp,timestamp '2026-11-15',NULL::timestamp,NULL::timestamp),
 (601,10,'FUTURE-UPPER','TT_FinMile','TK_NotStart',NULL::timestamp,timestamp '2026-11-15',NULL::timestamp,NULL::timestamp),
 (600,11,'PRE-SCHEDULE','TT_Task','TK_NotStart',NULL::timestamp,timestamp '2026-12-01',NULL::timestamp,NULL::timestamp),
 (601,11,'PRE-SCHEDULE','TT_Task','TK_NotStart',NULL::timestamp,timestamp '2026-12-01',NULL::timestamp,NULL::timestamp)
) t(project_id,n,code,kind,state,rs,rf,ast,aft);
UPDATE reporting.task SET target_start_date='2026-11-02' WHERE proj_id IN(600,601) AND task_code='PRE-SCHEDULE';
INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id)
SELECT 'p6_22',proj_id,task_id,350,CASE WHEN task_code IN('START-ACTIVE','START-LOWER','START-UPPER','FUTURE-START','PRE-SCHEDULE') THEN 3501 ELSE 3502 END
FROM reporting.task WHERE proj_id IN(600,601);
-- Baseline copies have different native task IDs and no Design Key codes.
INSERT INTO reporting.task(_source_system,proj_id,task_id,task_code,task_name,task_type,status_code,restart_date,reend_date)
SELECT 'p6_22',b.id,b.id*100+row_number() OVER(PARTITION BY b.id ORDER BY t.task_code),
 t.task_code,t.task_name,t.task_type,'TK_NotStart',
 CASE WHEN b.id=610 THEN timestamp '2026-08-01' ELSE timestamp '2026-09-28' END,
 CASE WHEN b.id=610 THEN timestamp '2026-08-02' ELSE timestamp '2026-10-05' END
FROM reporting.task t CROSS JOIN (VALUES(610),(611)) b(id) WHERE t.proj_id=600;
