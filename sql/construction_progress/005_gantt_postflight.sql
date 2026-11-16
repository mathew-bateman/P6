\set ON_ERROR_STOP on
-- Compare these four hashes with the preflight receipt before installing.
SELECT c.oid::regclass::text || '|' || md5(pg_get_viewdef(c.oid,true))
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='reporting' AND c.relname IN
 ('construction_activity_detail','construction_activity_movement','construction_milestone_detail','construction_milestone_type_bridge')
ORDER BY c.relname;
SET ROLE p6_reporting_external;
SELECT record_type,count(DISTINCT task_id) activities,count(*) rows
FROM p6_reporting.construction_gantt_detail WHERE proj_id=1797 GROUP BY record_type;
RESET ROLE;
WITH milestones AS MATERIALIZED (
 SELECT * FROM reporting.construction_milestone_detail WHERE proj_id IN (1753,1797,1792,1843)
), raw AS (
 SELECT DISTINCT t.proj_id,t.task_id,t.actv_code_id FROM reporting.taskactv t
 JOIN coding_check.code_values v ON v._source_system=t._source_system
 AND v.actv_code_type_id=t.actv_code_type_id AND v.actv_code_id=t.actv_code_id
 JOIN milestones m USING(proj_id,task_id)
 WHERE t._source_system='p6_22' AND t.actv_code_type_id=383 AND v.code_dimension='discipline'
 AND t.delete_date IS NULL AND t.delete_session_id IS NULL
), extracted AS (
 SELECT m.proj_id,m.task_id,(x->>'value_id')::int actv_code_id
 FROM milestones m CROSS JOIN LATERAL jsonb_array_elements(m.codes#>'{discipline,assignments}') x
)
SELECT 'milestone_discipline_raw_vs_extracted' check_name,count(*) differences FROM
 ((SELECT * FROM raw EXCEPT SELECT * FROM extracted)
 UNION ALL (SELECT * FROM extracted EXCEPT SELECT * FROM raw)) differences;
