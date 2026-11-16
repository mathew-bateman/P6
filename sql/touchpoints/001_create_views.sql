-- Additive Touchpoints views. Reuses LIVE Design Overview helpers; never reinstalls them.
-- P6_22 only. No writes to P6 or imported source data.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='8s';
SET LOCAL statement_timeout='120s';
DO $$ BEGIN IF current_database()<>'p6_reporting' AND current_database() NOT LIKE 'coding_check_test_%' THEN RAISE EXCEPTION 'Unexpected target database'; END IF; END $$;
CREATE SCHEMA IF NOT EXISTS touchpoints;
REVOKE ALL ON SCHEMA touchpoints FROM PUBLIC;

CREATE OR REPLACE VIEW touchpoints.activity_facts AS
SELECT f.*,
 CASE WHEN (f.codes#>>'{milestone_type,valid_value_count}')::integer=1
       AND f.codes#>>'{milestone_type,code_short_name}' IN ('GET','GIVE')
      THEN f.codes#>>'{milestone_type,code_short_name}' END AS give_get,
 EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(f.codes#>'{milestone_type,assignments}','[]'::jsonb)) x
        WHERE x->>'short_name' IN ('GET','GIVE')) AS has_touchpoint_assignment,
 coalesce((f.codes#>>'{milestone_type,valid_value_count}')::integer,0)>1
   AND EXISTS(SELECT 1 FROM jsonb_array_elements(coalesce(f.codes#>'{milestone_type,assignments}','[]'::jsonb)) x
              WHERE x->>'short_name' IN ('GET','GIVE')) AS touchpoint_code_conflict
FROM design_overview.activity_facts f;

CREATE OR REPLACE VIEW touchpoints.interface_routes AS
WITH assignments AS (
 SELECT DISTINCT a.proj_id,a.task_id,z.actv_code_type_id,v.actv_code_id,z.actv_code_type_scope,
   v.short_name,coalesce(nullif(btrim(v.actv_code_name),''),nullif(btrim(v.short_name),'')) AS route_value
 FROM reporting.taskactv a
 JOIN reporting.actvtype z USING(_source_system,actv_code_type_id)
 JOIN reporting.actvcode v USING(_source_system,actv_code_type_id,actv_code_id)
 WHERE a._source_system='p6_22' AND lower(btrim(z.actv_code_type))='interface detail'
   AND a.delete_date IS NULL AND a.delete_session_id IS NULL
   AND z.delete_date IS NULL AND z.delete_session_id IS NULL
   AND v.delete_date IS NULL AND v.delete_session_id IS NULL
), grouped AS (
 SELECT proj_id,task_id,count(*) AS route_assignment_count,
   CASE WHEN count(*)=1 THEN min(actv_code_type_id) END AS interface_code_type_id,
   CASE WHEN count(*)=1 THEN min(actv_code_id) END AS interface_code_id,
   CASE WHEN count(*)=1 THEN min(short_name) END AS interface_code_short_name,
   CASE WHEN count(*)=1 THEN min(route_value) END AS interface_detail,
   jsonb_agg(jsonb_build_object('type_id',actv_code_type_id,'value_id',actv_code_id,
     'scope',actv_code_type_scope,'short_name',short_name,'value',route_value)
     ORDER BY actv_code_type_id,actv_code_id) AS interface_assignments
 FROM assignments GROUP BY proj_id,task_id
), parsed AS (
 SELECT g.*,route_assignment_count=1 AND regexp_count(interface_detail,'>')=1
  AND nullif(btrim(split_part(interface_detail,'>',1)),'') IS NOT NULL
  AND nullif(btrim(split_part(interface_detail,'>',2)),'') IS NOT NULL AS has_direction
 FROM grouped g
)
SELECT p.*,CASE WHEN has_direction THEN btrim(split_part(interface_detail,'>',1)) END AS from_company,
 CASE WHEN has_direction THEN btrim(split_part(interface_detail,'>',2)) END AS to_company,
 CASE WHEN route_assignment_count>1 THEN 'conflicting_routes'
      WHEN has_direction THEN 'coded_direction' ELSE 'unstructured_route' END AS route_status
FROM parsed p;

CREATE OR REPLACE VIEW touchpoints.activity_commentary AS
WITH udf AS (
 SELECT DISTINCT v.proj_id,v.fk_id AS task_id,t.udf_type_id,t.udf_type_label,v.udf_text
 FROM reporting.udfvalue v JOIN reporting.udftype t USING(_source_system,udf_type_id)
 WHERE v._source_system='p6_22' AND lower(v.table_name)='task'
  AND lower(btrim(t.udf_type_label)) IN('comments','notes') AND nullif(btrim(v.udf_text),'') IS NOT NULL
  AND v.delete_date IS NULL AND v.delete_session_id IS NULL
  AND t.delete_date IS NULL AND t.delete_session_id IS NULL
), u AS (
 SELECT proj_id,task_id,string_agg(udf_type_label||': '||udf_text,E'\n' ORDER BY udf_type_id,udf_text) AS udf_commentary,
 jsonb_agg(jsonb_build_object('udf_type_id',udf_type_id,'label',udf_type_label,'text',udf_text)
 ORDER BY udf_type_id,udf_text) AS udf_notes FROM udf GROUP BY proj_id,task_id
), m AS (
 SELECT proj_id,task_id,count(*) AS notebook_count,
 string_agg(btrim(regexp_replace(replace(replace(replace(replace(task_memo,'&nbsp;',' '),'&amp;','&'),'&lt;','<'),'&gt;','>'),'<[^>]*>',' ','g')),E'\n' ORDER BY memo_type_id,memo_id) AS notebook_commentary,
 jsonb_agg(jsonb_build_object('memo_id',memo_id,'memo_type_id',memo_type_id,'raw_text',task_memo)
 ORDER BY memo_type_id,memo_id) AS notebook_entries
 FROM reporting.taskmemo WHERE _source_system='p6_22' AND delete_date IS NULL AND delete_session_id IS NULL
 AND nullif(btrim(task_memo),'') IS NOT NULL GROUP BY proj_id,task_id
)
SELECT coalesce(u.proj_id,m.proj_id) AS proj_id,coalesce(u.task_id,m.task_id) AS task_id,
 u.udf_commentary,m.notebook_commentary,
 nullif(concat_ws(E'\n',nullif(u.udf_commentary,''),nullif(m.notebook_commentary,'')),'') AS commentary,
 coalesce(m.notebook_count,0) AS notebook_count,coalesce(u.udf_notes,'[]'::jsonb) AS udf_notes,
 coalesce(m.notebook_entries,'[]'::jsonb) AS notebook_entries
FROM u FULL JOIN m USING(proj_id,task_id);

CREATE OR REPLACE VIEW touchpoints.enriched_activities AS
SELECT f.*,f.responsible_company AS "Responsible Company",
 t.total_float_hr_cnt,t.total_float_hr_cnt/8.0 AS total_float_days_8h,
 f.scheduled_finish_date AS current_finish,f.target_end_date AS planned_finish,f.act_end_date AS actual_finish,
 CASE WHEN f.status_code='TK_Complete' THEN 'act_end_date'
      WHEN f.reend_date IS NOT NULL THEN 'reend_date' ELSE 'target_end_date' END AS finish_date_source,
 CASE WHEN f.status_code IS NOT NULL THEN f.status_code<>'TK_Complete' END AS is_remaining,
 coalesce(r.route_assignment_count,0) AS route_assignment_count,r.interface_code_type_id,r.interface_code_id,
 r.interface_code_short_name,r.interface_detail,coalesce(r.interface_assignments,'[]'::jsonb) AS interface_assignments,
 r.from_company,r.to_company,r.from_company AS "From Company",r.to_company AS "To Company",
 coalesce(r.route_status,'missing_route') AS route_status,
 (f.give_get='GET' AND f.task_name~*'^[[:space:]]*(<GIVE>|GIVE[[:space:]]*:)')
 OR (f.give_get='GIVE' AND f.task_name~*'^[[:space:]]*(<GET>|GET[[:space:]]*:)') AS name_direction_conflict,
 n.commentary,n.udf_commentary,n.notebook_commentary,coalesce(n.notebook_count,0) AS notebook_count,
 coalesce(n.udf_notes,'[]'::jsonb) AS udf_notes,coalesce(n.notebook_entries,'[]'::jsonb) AS notebook_entries
FROM touchpoints.activity_facts f
JOIN reporting.task t ON t._source_system='p6_22' AND t.proj_id=f.proj_id AND t.task_id=f.task_id
LEFT JOIN touchpoints.interface_routes r ON r.proj_id=f.proj_id AND r.task_id=f.task_id
LEFT JOIN touchpoints.activity_commentary n ON n.proj_id=f.proj_id AND n.task_id=f.task_id;

CREATE OR REPLACE VIEW reporting.touchpoint_activity_detail AS
SELECT s.proj_id,
 s.proj_short_name,
 s.project_code_id,
 s.project_code,
 s."Project ID",
 s."Account",
 s."Lead Planner",
 s."Period",
 s.full_period_code AS "Period Code",
 s."Project State",
 s."Project Status",
 s.period_code_id,
 s.full_period_code,
 s.financial_year,
 s.financial_year_start,
 s.period_number,
 s.period_serial,
 s.data_date,
 s.cut_date,
 s.submission_count,
 s.submission_index,
 s.global_submission_index,
 s.history_rank,
 s.previous_proj_id,
 s.previous_proj_short_name,
 s.previous_full_period_code,
 s.period_start,
 s.period_end,
 s.baseline_proj_id,
 s.baseline_period_code,
 s.baseline_status,
 s.baseline_candidate_count,
 s.comparison_status,
 s.is_comparable,
 s.submission_label,
 s._snapshot_at,
 a.task_id,a.task_code,a.task_name,a.task_type,a.status_code,a.give_get,
 a.give_get IS NOT NULL AS is_touchpoint,a.touchpoint_code_conflict,a.task_code_count,
 a.current_finish,a.scheduled_finish_date,a.planned_finish,a.actual_finish,a.finish_date_source,
 a.reend_date,a.target_end_date,a.act_end_date,a.is_remaining,
 a.total_float_hr_cnt,a.total_float_days_8h,a.responsible_company,a."Responsible Company",
 a.project_stage,a.discipline,a.milestone_type,a.codes,
 a.interface_detail,a.interface_code_type_id,a.interface_code_id,a.interface_code_short_name,
 a.route_assignment_count,a.interface_assignments,a.from_company,a.to_company,a."From Company",a."To Company",
 a.route_status,a.name_direction_conflict,a.commentary,a.udf_commentary,a.notebook_commentary,
 a.notebook_count,a.udf_notes,a.notebook_entries
FROM touchpoints.enriched_activities a JOIN reporting.design_overview_submissions s ON s.proj_id=a.proj_id
WHERE a.has_touchpoint_assignment;

CREATE OR REPLACE VIEW reporting.touchpoint_interface_routes AS
SELECT a.proj_id,
 a.proj_short_name,
 a.project_code_id,
 a.project_code,
 a."Project ID",
 a."Account",
 a."Lead Planner",
 a."Period",
 a.full_period_code AS "Period Code",
 a."Project State",
 a."Project Status",
 a.period_code_id,
 a.full_period_code,
 a.financial_year,
 a.financial_year_start,
 a.period_number,
 a.period_serial,
 a.data_date,
 a.cut_date,
 a.submission_count,
 a.submission_index,
 a.global_submission_index,
 a.history_rank,
 a.previous_proj_id,
 a.previous_proj_short_name,
 a.previous_full_period_code,
 a.period_start,
 a.period_end,
 a.baseline_proj_id,
 a.baseline_period_code,
 a.baseline_status,
 a.baseline_candidate_count,
 a.comparison_status,
 a.is_comparable,
 a.submission_label,
 a._snapshot_at,a.task_id,a.task_code,a.task_name,a.give_get,a.is_touchpoint,
 a.responsible_company,a."Responsible Company",a.project_stage,a.discipline,a.milestone_type,
 a.interface_detail,a.interface_code_type_id,a.interface_code_id,a.interface_code_short_name,
 a.route_assignment_count,a.interface_assignments,a.from_company,a.to_company,a."From Company",a."To Company",
 a.route_status,a.name_direction_conflict,a.touchpoint_code_conflict
FROM reporting.touchpoint_activity_detail a;

-- Match ALL eligible activities first. No Design Key or comparator GIVE/GET gate.
CREATE OR REPLACE VIEW touchpoints.matched_activities AS
WITH facts AS MATERIALIZED (SELECT * FROM touchpoints.activity_facts),
 ids AS MATERIALIZED (
 SELECT proj_id,task_code,count(*) AS code_count,max(task_id) AS single_task_id,
 bool_or(has_touchpoint_assignment) AS has_touchpoint_assignment
 FROM facts GROUP BY proj_id,task_code
), current_rows AS (
 SELECT s.proj_id,c.task_code,c.task_id AS current_task_id,p.task_id AS previous_task_id,
 c.task_code_count AS current_code_count,coalesce(i.code_count,0) AS previous_code_count,
 c.has_touchpoint_assignment AS current_has_touchpoint_assignment,
 coalesce(i.has_touchpoint_assignment,false) AS previous_has_touchpoint_assignment
 FROM reporting.design_overview_submissions s JOIN facts c ON c.proj_id=s.proj_id
 LEFT JOIN ids i ON i.proj_id=s.previous_proj_id AND i.task_code=c.task_code
  AND nullif(btrim(c.task_code),'') IS NOT NULL AND s.is_comparable
 LEFT JOIN facts p ON p.proj_id=s.previous_proj_id AND p.task_id=i.single_task_id
  AND i.code_count=1 AND c.task_code_count=1
 WHERE c.has_touchpoint_assignment OR coalesce(i.has_touchpoint_assignment,false)
), removed_rows AS (
 SELECT s.proj_id,p.task_code,NULL::integer AS current_task_id,p.task_id AS previous_task_id,
 0::bigint AS current_code_count,p.task_code_count AS previous_code_count,
 false AS current_has_touchpoint_assignment,true AS previous_has_touchpoint_assignment
 FROM reporting.design_overview_submissions s JOIN facts p ON p.proj_id=s.previous_proj_id AND s.is_comparable
 WHERE p.has_touchpoint_assignment AND NOT EXISTS(
  SELECT 1 FROM facts c WHERE c.proj_id=s.proj_id AND c.task_code=p.task_code
   AND nullif(btrim(p.task_code),'') IS NOT NULL)
), pairs AS (SELECT * FROM current_rows UNION ALL SELECT * FROM removed_rows)
SELECT x.*,b.task_id AS baseline_task_id,coalesce(i.code_count,0) AS baseline_code_count,
 x.proj_id::text||':'||CASE WHEN x.current_task_id IS NOT NULL THEN 'current:'||x.current_task_id::text
 ELSE 'previous:'||x.previous_task_id::text END AS activity_row_key
FROM pairs x JOIN reporting.design_overview_submissions s USING(proj_id)
LEFT JOIN ids i ON i.proj_id=s.baseline_proj_id AND i.task_code=x.task_code
 AND nullif(btrim(x.task_code),'') IS NOT NULL AND s.baseline_status='ready'
LEFT JOIN facts b ON b.proj_id=s.baseline_proj_id AND b.task_id=i.single_task_id
 AND i.code_count=1 AND x.current_code_count=1;

CREATE OR REPLACE VIEW reporting.touchpoint_finish_movement AS
WITH pairs AS MATERIALIZED (SELECT * FROM touchpoints.matched_activities),
 facts AS MATERIALIZED (SELECT * FROM touchpoints.enriched_activities),
 details AS (
 SELECT s.proj_id,
 s.proj_short_name,
 s.project_code_id,
 s.project_code,
 s."Project ID",
 s."Account",
 s."Lead Planner",
 s."Period",
 s.full_period_code AS "Period Code",
 s."Project State",
 s."Project Status",
 s.period_code_id,
 s.full_period_code,
 s.financial_year,
 s.financial_year_start,
 s.period_number,
 s.period_serial,
 s.data_date,
 s.cut_date,
 s.submission_count,
 s.submission_index,
 s.global_submission_index,
 s.history_rank,
 s.previous_proj_id,
 s.previous_proj_short_name,
 s.previous_full_period_code,
 s.period_start,
 s.period_end,
 s.baseline_proj_id,
 s.baseline_period_code,
 s.baseline_status,
 s.baseline_candidate_count,
 s.comparison_status,
 s.is_comparable,
 s.submission_label,
 s._snapshot_at,
 x.activity_row_key,x.task_code,x.current_task_id,x.previous_task_id,x.baseline_task_id,
 x.current_code_count,x.previous_code_count,x.baseline_code_count,
 x.current_has_touchpoint_assignment,x.previous_has_touchpoint_assignment,
 c.give_get IS NOT NULL AS is_current_touchpoint,p.give_get IS NOT NULL AS is_previous_touchpoint,
 c.touchpoint_code_conflict AS current_coding_conflict,p.touchpoint_code_conflict AS previous_coding_conflict,
 coalesce(c.task_name,p.task_name) AS task_name,p.task_name AS previous_task_name,
 a.task_type,a.status_code,c.status_code AS current_status,p.status_code AS previous_status,
 c.give_get AS current_give_get,p.give_get AS previous_give_get,a.give_get,
 c.current_finish,p.current_finish AS previous_finish,b.current_finish AS baseline_finish,
 c.planned_finish,c.actual_finish,c.finish_date_source,
 c.is_remaining,c.total_float_hr_cnt,c.total_float_days_8h,
 a.responsible_company,a."Responsible Company",a.project_stage,a.discipline,a.milestone_type,
 c.codes AS current_codes,p.codes AS previous_codes,b.codes AS baseline_codes,
 a.interface_detail,p.interface_detail AS previous_interface_detail,
 a.interface_code_type_id,a.interface_code_id,a.interface_code_short_name,a.interface_assignments,
 a.from_company,a.to_company,a."From Company",a."To Company",a.route_status,a.name_direction_conflict,
 a.commentary,a.udf_commentary,a.notebook_commentary,a.notebook_count,a.udf_notes,a.notebook_entries,
 CASE WHEN x.current_task_id IS NOT NULL THEN 'current' ELSE 'previous_only' END AS classification_basis,
 CASE WHEN s.is_comparable IS NOT TRUE THEN 'cut_not_comparable'
      WHEN nullif(btrim(x.task_code),'') IS NULL THEN 'missing_activity_id'
      WHEN x.current_code_count>1 OR x.previous_code_count>1 THEN 'ambiguous_activity_id'
      WHEN x.current_task_id IS NULL THEN 'no_current_activity'
      WHEN x.previous_code_count=0 THEN 'no_previous_activity'
      WHEN c.current_finish IS NULL OR p.current_finish IS NULL THEN 'missing_date'
      ELSE 'ready' END AS previous_match_status,
 CASE WHEN s.baseline_status IS DISTINCT FROM 'ready' THEN 'baseline_not_ready'
      WHEN nullif(btrim(x.task_code),'') IS NULL THEN 'missing_activity_id'
      WHEN x.current_code_count>1 OR x.baseline_code_count>1 THEN 'ambiguous_activity_id'
      WHEN x.current_task_id IS NULL THEN 'no_current_activity'
      WHEN x.baseline_code_count=0 THEN 'no_baseline_activity'
      WHEN c.current_finish IS NULL OR b.current_finish IS NULL THEN 'missing_date'
      ELSE 'ready' END AS baseline_match_status,
 CASE WHEN x.current_task_id IS NULL THEN 'removed_activity'
      WHEN s.is_comparable IS NOT TRUE THEN 'no_comparable_cut'
      WHEN nullif(btrim(x.task_code),'') IS NULL OR x.current_code_count>1 OR x.previous_code_count>1 THEN 'unresolved_identity'
      WHEN x.previous_code_count=0 THEN 'not_in_previous_cut'
      WHEN NOT x.previous_has_touchpoint_assignment THEN 'added_touchpoint_coding'
      WHEN NOT x.current_has_touchpoint_assignment THEN 'removed_touchpoint_coding'
      WHEN c.give_get IS DISTINCT FROM p.give_get THEN 'reclassified_touchpoint'
      ELSE 'retained_touchpoint' END AS membership_change,
 CASE WHEN c.task_id IS NOT NULL AND p.task_id IS NOT NULL THEN c.task_name IS DISTINCT FROM p.task_name END AS is_renamed,
 CASE WHEN c.task_id IS NOT NULL AND p.task_id IS NOT NULL THEN c.codes->'milestone_type' IS DISTINCT FROM p.codes->'milestone_type' END AS is_reclassified
 FROM pairs x JOIN reporting.design_overview_submissions s USING(proj_id)
 LEFT JOIN facts c ON c.proj_id=x.proj_id AND c.task_id=x.current_task_id
 LEFT JOIN facts p ON p.proj_id=s.previous_proj_id AND p.task_id=x.previous_task_id
 LEFT JOIN facts b ON b.proj_id=s.baseline_proj_id AND b.task_id=x.baseline_task_id
 JOIN facts a ON a.proj_id=CASE WHEN x.current_task_id IS NOT NULL THEN x.proj_id ELSE s.previous_proj_id END
  AND a.task_id=coalesce(x.current_task_id,x.previous_task_id)
), movement AS (
 SELECT d.*,CASE WHEN previous_match_status='ready' THEN current_finish::date-previous_finish::date END AS previous_movement_days,
 CASE WHEN baseline_match_status='ready' THEN current_finish::date-baseline_finish::date END AS baseline_movement_days
 FROM details d
), classified AS (
 SELECT m.*,CASE WHEN NOT is_current_touchpoint THEN 'Not a current Touchpoint'
      WHEN is_remaining IS NULL THEN 'Unknown status' WHEN NOT is_remaining THEN 'Completed'
      WHEN previous_movement_days IS NULL THEN 'No comparison' WHEN previous_movement_days>0 THEN 'Delayed'
      WHEN previous_movement_days<0 THEN 'Improved' ELSE 'No movement' END AS movement_status,
 CASE WHEN NOT is_current_touchpoint THEN 'Not a current Touchpoint'
      WHEN is_remaining IS NULL THEN 'Unknown status' WHEN NOT is_remaining THEN 'Completed'
      WHEN baseline_movement_days IS NULL THEN 'No comparison' WHEN baseline_movement_days>0 THEN 'Delayed'
      WHEN baseline_movement_days<0 THEN 'Improved' ELSE 'No movement' END AS baseline_movement_status,
 CASE WHEN is_current_touchpoint AND is_remaining AND previous_movement_days>0 THEN
      CASE WHEN previous_movement_days<=20 THEN 1 WHEN previous_movement_days<=40 THEN 2
           WHEN previous_movement_days<=60 THEN 3 WHEN previous_movement_days<=80 THEN 4
           WHEN previous_movement_days<=100 THEN 5 ELSE 6 END END AS delay_band_order
 FROM movement m
)
SELECT c.*,CASE delay_band_order WHEN 1 THEN '0-20' WHEN 2 THEN '21-40' WHEN 3 THEN '41-60'
 WHEN 4 THEN '61-80' WHEN 5 THEN '81-100' WHEN 6 THEN '>100' END AS delay_band
FROM classified c;

CREATE OR REPLACE VIEW reporting.touchpoint_finish_history AS
WITH anchors AS MATERIALIZED (SELECT * FROM reporting.touchpoint_finish_movement WHERE current_has_touchpoint_assignment),
 facts AS MATERIALIZED (SELECT * FROM touchpoints.activity_facts),
 cuts AS MATERIALIZED (SELECT * FROM reporting.design_overview_submissions),
 periods AS (
 SELECT project_code_id,period_serial,min(full_period_code) AS historical_full_period_code,
 count(*) AS cut_count,CASE WHEN count(*)=1 THEN min(proj_id) END AS historical_proj_id
 FROM cuts WHERE project_code_id IS NOT NULL AND period_serial IS NOT NULL GROUP BY project_code_id,period_serial
), ranked AS (
 SELECT s.proj_id,h.*,row_number() OVER(PARTITION BY s.proj_id ORDER BY h.period_serial DESC) AS recent_cut_rank
 FROM cuts s JOIN periods h ON h.project_code_id=s.project_code_id AND h.period_serial<=s.period_serial
), ids AS (
 SELECT proj_id,task_code,count(*) AS code_count,max(task_id) AS single_task_id FROM facts GROUP BY proj_id,task_code
)
SELECT a.proj_id,
 a.proj_short_name,
 a.project_code_id,
 a.project_code,
 a."Project ID",
 a."Account",
 a."Lead Planner",
 a."Period",
 a.full_period_code AS "Period Code",
 a."Project State",
 a."Project Status",
 a.period_code_id,
 a.full_period_code,
 a.financial_year,
 a.financial_year_start,
 a.period_number,
 a.period_serial,
 a.data_date,
 a.cut_date,
 a.submission_count,
 a.submission_index,
 a.global_submission_index,
 a.history_rank,
 a.previous_proj_id,
 a.previous_proj_short_name,
 a.previous_full_period_code,
 a.period_start,
 a.period_end,
 a.baseline_proj_id,
 a.baseline_period_code,
 a.baseline_status,
 a.baseline_candidate_count,
 a.comparison_status,
 a.is_comparable,
 a.submission_label,
 a._snapshot_at,a.activity_row_key,a.current_task_id AS task_id,a.task_code,a.task_name,a.give_get,
 a.responsible_company,a."Responsible Company",a.project_stage,a.discipline,a.milestone_type,
 a.from_company,a.to_company,a."From Company",a."To Company",a.interface_detail,a.route_status,
 h.historical_proj_id,hs.proj_short_name AS historical_proj_short_name,h.historical_full_period_code,
 h.period_serial AS historical_period_serial,hs.cut_date AS historical_cut_date,h.recent_cut_rank,
 a.period_serial-h.period_serial AS history_period_offset,f.task_id AS historical_task_id,
 f.scheduled_finish_date AS historical_finish,f.status_code AS historical_status,
 f.give_get AS historical_give_get,coalesce(i.code_count,0) AS historical_code_count,
 CASE WHEN nullif(btrim(a.task_code),'') IS NULL THEN 'missing_activity_id'
 WHEN a.current_code_count>1 OR coalesce(i.code_count,0)>1 THEN 'ambiguous_activity_id'
 WHEN h.cut_count<>1 THEN 'ambiguous_history_cut'
 WHEN coalesce(i.code_count,0)=0 THEN 'missing_activity'
 WHEN f.scheduled_finish_date IS NULL THEN 'missing_date' ELSE 'ready' END AS history_match_status
FROM anchors a JOIN ranked h ON h.proj_id=a.proj_id AND h.recent_cut_rank<=5
LEFT JOIN cuts hs ON hs.proj_id=h.historical_proj_id
LEFT JOIN ids i ON i.proj_id=h.historical_proj_id AND i.task_code=a.task_code AND nullif(btrim(a.task_code),'') IS NOT NULL
LEFT JOIN facts f ON f.proj_id=h.historical_proj_id AND f.task_id=i.single_task_id AND i.code_count=1 AND a.current_code_count=1;

CREATE OR REPLACE VIEW reporting.touchpoint_movement_summary AS
WITH detail AS MATERIALIZED (SELECT * FROM reporting.touchpoint_finish_movement WHERE is_current_touchpoint),
 grouped AS (
 SELECT proj_id,responsible_company,project_stage,discipline,milestone_type,give_get,
 from_company,to_company,route_status,is_remaining,movement_status,delay_band,delay_band_order,
 count(*) AS activity_count,count(*) FILTER(WHERE is_remaining) AS remaining_count,
 count(*) FILTER(WHERE is_remaining IS NULL) AS unknown_remaining_count,
 count(*) FILTER(WHERE movement_status='Delayed') AS delayed_count,
 count(*) FILTER(WHERE movement_status='Improved') AS improved_count,
 count(*) FILTER(WHERE movement_status='No movement') AS no_movement_count,
 count(*) FILTER(WHERE movement_status='No comparison') AS no_comparison_count,
 count(*) FILTER(WHERE is_remaining AND previous_movement_days IS NOT NULL) AS previous_comparable_count,
 count(*) FILTER(WHERE is_remaining AND baseline_movement_days IS NOT NULL) AS baseline_comparable_count,
 sum(previous_movement_days) FILTER(WHERE is_remaining) AS previous_movement_days_sum,
 sum(baseline_movement_days) FILTER(WHERE is_remaining) AS baseline_movement_days_sum,
 avg(previous_movement_days) FILTER(WHERE is_remaining) AS average_previous_movement_days,
 avg(baseline_movement_days) FILTER(WHERE is_remaining) AS average_baseline_movement_days
 FROM detail GROUP BY proj_id,responsible_company,project_stage,discipline,milestone_type,give_get,
 from_company,to_company,route_status,is_remaining,movement_status,delay_band,delay_band_order
)
SELECT s.proj_id,
 s.proj_short_name,
 s.project_code_id,
 s.project_code,
 s."Project ID",
 s."Account",
 s."Lead Planner",
 s."Period",
 s.full_period_code AS "Period Code",
 s."Project State",
 s."Project Status",
 s.period_code_id,
 s.full_period_code,
 s.financial_year,
 s.financial_year_start,
 s.period_number,
 s.period_serial,
 s.data_date,
 s.cut_date,
 s.submission_count,
 s.submission_index,
 s.global_submission_index,
 s.history_rank,
 s.previous_proj_id,
 s.previous_proj_short_name,
 s.previous_full_period_code,
 s.period_start,
 s.period_end,
 s.baseline_proj_id,
 s.baseline_period_code,
 s.baseline_status,
 s.baseline_candidate_count,
 s.comparison_status,
 s.is_comparable,
 s.submission_label,
 s._snapshot_at,g.responsible_company,g.responsible_company AS "Responsible Company",
 g.project_stage,g.discipline,g.milestone_type,g.give_get,g.from_company,g.to_company,
 g.from_company AS "From Company",g.to_company AS "To Company",g.route_status,g.is_remaining,
 g.movement_status,g.delay_band,g.delay_band_order,g.activity_count,g.remaining_count,g.unknown_remaining_count,
 g.delayed_count,g.improved_count,g.no_movement_count,g.no_comparison_count,
 g.previous_comparable_count,g.baseline_comparable_count,g.previous_movement_days_sum,g.baseline_movement_days_sum,
 g.average_previous_movement_days,g.average_baseline_movement_days
FROM grouped g JOIN reporting.design_overview_submissions s USING(proj_id);

GRANT USAGE ON SCHEMA reporting TO p6_reporting_reader;
GRANT SELECT ON reporting.touchpoint_activity_detail,reporting.touchpoint_interface_routes,
 reporting.touchpoint_finish_movement,reporting.touchpoint_finish_history,reporting.touchpoint_movement_summary
 TO p6_reporting_reader;
GRANT SELECT ON reporting.touchpoint_activity_detail,reporting.touchpoint_interface_routes,
 reporting.touchpoint_finish_movement,reporting.touchpoint_finish_history,reporting.touchpoint_movement_summary
 TO p6_reporting_sync WITH GRANT OPTION;
COMMENT ON VIEW reporting.touchpoint_activity_detail IS 'Touchpoints v1 2026-11-12. Active P6_22 Global Milestone Type GIVE/GET assignments, including flagged conflicts. One native task per submitted cut. is_touchpoint selects valid single-code membership. No Design Key gate. Uses existing Design Overview eligibility.';
COMMENT ON VIEW reporting.touchpoint_interface_routes IS 'Touchpoints v1. One activity with source Interface Detail direction or explicit missing/conflicting status. From/To companies are source-text endpoints, not verified pair IDs or planner mappings. Never infer GIVE/GET pairs from names.';
COMMENT ON VIEW reporting.touchpoint_finish_movement IS 'Touchpoints v1. Exact unique Activity ID within logical project, all eligible comparator tasks, existing previous/baseline selection. Positive calendar-date difference=later. NULL=no comparison. Completed and previous-only/reclassified activities stay visible; summary counts only current valid Touchpoints.';
COMMENT ON VIEW reporting.touchpoint_finish_history IS 'Touchpoints v1. Selected-cut filter columns and current ownership; up to five available coded periods at/before selected cut. Historical fields are explicitly prefixed. Gaps retain period offsets; ambiguous cuts/IDs remain NULL; no historical GIVE/GET coding gate.';
COMMENT ON VIEW reporting.touchpoint_movement_summary IS 'Touchpoints v1. Additive detail-derived counts by cut, owner, route, code, remaining status and movement band. Donut: is_remaining=true. Delay bars: movement_status=Delayed, ordered delay_band_order. Label 0-20 contains positive 1-20 days; zero stays No movement. Recompute combined averages from sums/comparable counts.';
COMMIT;
