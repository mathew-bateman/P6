-- Run only after the repository's Coding Check, Design and Touchpoints fixtures.
DO $$ BEGIN IF current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'test database required'; END IF; END $$;
ALTER TABLE reporting.task ADD COLUMN wbs_id integer;
ALTER TABLE reporting.projwbs ADD COLUMN wbs_short_name text;
INSERT INTO reporting.actvtype(_source_system,actv_code_type_id,actv_code_type,actv_code_type_scope)
 VALUES ('p6_22',379,'Project Stage','AS_Global');
INSERT INTO fixture_generation_a.actvcode(_source_system,actv_code_type_id,actv_code_id,short_name,actv_code_name)
 VALUES ('p6_22',379,4328,'ES6','ES6 - Construction & Commisioning');
INSERT INTO reporting.task(_source_system,proj_id,task_id,task_code,task_name,task_type,status_code,reend_date,act_end_date)
VALUES
 ('p6_22',700,799001,'CP1','Comparable','TT_Task','TK_NotStart','2026-10-12',NULL),
 ('p6_22',701,799002,'CP1','Comparable','TT_Task','TK_Complete',NULL,'2026-10-17'),
 ('p6_22',710,799003,'CP1','Comparable','TT_Task','TK_NotStart','2026-10-10',NULL),
 ('p6_22',701,799004,'CP2','Added','TT_Task','TK_NotStart','2026-10-01',NULL),
 ('p6_22',700,799005,'CP3','No forecast','TT_Task','TK_NotStart',NULL,NULL),
 ('p6_22',701,799006,'CP3','No forecast','TT_Task','TK_NotStart','2026-12-01',NULL),
 ('p6_22',701,799007,'CP4','Excluded LOE','TT_LOE','TK_NotStart','2026-10-01',NULL),
 ('p6_22',701,799008,'CP5','Excluded milestone','TT_FinMile','TK_NotStart','2026-10-01',NULL),
 ('p6_22',701,799009,'CP6','Multiple codes','TT_Task','TK_NotStart','2026-10-01',NULL),
 ('p6_22',700,799010,'CPDUP','Ambiguous A','TT_Task','TK_NotStart','2026-10-01',NULL),
 ('p6_22',700,799011,'CPDUP','Ambiguous B','TT_Task','TK_NotStart','2026-10-01',NULL),
 ('p6_22',701,799012,'CPDUP','Ambiguous','TT_Task','TK_NotStart','2026-10-01',NULL);
INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id)
 SELECT 'p6_22',701,id,379,4328 FROM (VALUES(799002),(799004),(799006),(799007),(799008),(799009),(799012)) x(id);
-- Duplicate link must not duplicate the activity; two milestone values must
-- produce one milestone fact row and two bridge rows.
INSERT INTO reporting.taskactv(_source_system,proj_id,task_id,actv_code_type_id,actv_code_id) VALUES
 ('p6_22',701,799002,379,4328),
 ('p6_22',701,799009,30,30301),('p6_22',701,799009,30,30302);
