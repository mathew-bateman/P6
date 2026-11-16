-- Activity-level drill-through for the existing Coding Check counts.
-- One row per eligible activity and code dimension; P6 22 only.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';
CREATE OR REPLACE VIEW reporting.coding_check_activity_detail AS
WITH coding AS MATERIALIZED (SELECT * FROM coding_check.activity_coding)
SELECT p.proj_id, p.proj_short_name, p."Project ID", p."Account", p."Lead Planner",
  p."Period", p."Period Code", p."Project State", p."Project Status",
  t.task_id, t.task_code, t.task_name, t.task_type, t.status_code,
  t.restart_date, t.reend_date, t.target_start_date, t.target_end_date,
  t.act_start_date, t.act_end_date,
  CASE WHEN t.status_code IN ('TK_Active','TK_Complete') THEN t.act_start_date ELSE t.restart_date END AS scheduled_start_date,
  CASE WHEN t.status_code='TK_Complete' THEN t.act_end_date ELSE t.reend_date END AS scheduled_finish_date,
  t.total_float_hr_cnt, t.total_float_hr_cnt / 8.0 AS total_float_days, t.driving_path_flag,
  c.code_dimension, c.code_type_name, c.actv_code_type_id, c.actv_code_id,
  v.code_short_name, v.code_raw_value,
  CASE WHEN c.bucket_status='conflict' THEN 'Multiple code assignments'
       WHEN c.bucket_status='uncoded' THEN 'No codes / Blank' ELSE v.code_value END AS code_value,
  c.bucket_status, c.bucket_status='uncoded' AS is_uncoded, c.valid_value_count,
  b.is_stored_baseline, b.original_proj_id, b.original_proj_short_name, p._snapshot_at
FROM coding c
JOIN coding_check.projects p ON p._source_system=c._source_system AND p.proj_id=c.proj_id
JOIN reporting.task t ON t._source_system=c._source_system AND t.proj_id=c.proj_id AND t.task_id=c.task_id
JOIN coding_check.project_baseline_flags b ON b.proj_id=c.proj_id
LEFT JOIN coding_check.code_values v ON v._source_system=c._source_system AND v.code_dimension=c.code_dimension
  AND v.actv_code_type_id=c.actv_code_type_id AND v.actv_code_id=c.actv_code_id;
CREATE OR REPLACE VIEW reporting.coding_check_project_stage_detail AS SELECT * FROM reporting.coding_check_activity_detail WHERE code_dimension='project_stage';
CREATE OR REPLACE VIEW reporting.coding_check_discipline_detail AS SELECT * FROM reporting.coding_check_activity_detail WHERE code_dimension='discipline';
CREATE OR REPLACE VIEW reporting.coding_check_design_key_detail AS SELECT * FROM reporting.coding_check_activity_detail WHERE code_dimension='design_key';
CREATE OR REPLACE VIEW reporting.coding_check_milestone_type_detail AS SELECT * FROM reporting.coding_check_activity_detail WHERE code_dimension='milestone_type';
CREATE OR REPLACE VIEW reporting.coding_check_responsible_company_detail AS SELECT * FROM reporting.coding_check_activity_detail WHERE code_dimension='responsible_company';
COMMENT ON VIEW reporting.coding_check_activity_detail IS 'One eligible activity per coding dimension, matching Coding Check count buckets. Lead Planner is the project-level code. Select one dimension when counting activities.';
GRANT SELECT ON reporting.coding_check_activity_detail, reporting.coding_check_project_stage_detail,
 reporting.coding_check_discipline_detail, reporting.coding_check_design_key_detail,
 reporting.coding_check_milestone_type_detail, reporting.coding_check_responsible_company_detail TO p6_reporting_reader;
GRANT SELECT ON reporting.coding_check_activity_detail, reporting.coding_check_project_stage_detail,
 reporting.coding_check_discipline_detail, reporting.coding_check_design_key_detail,
 reporting.coding_check_milestone_type_detail, reporting.coding_check_responsible_company_detail TO p6_reporting_sync WITH GRANT OPTION;
COMMIT;
