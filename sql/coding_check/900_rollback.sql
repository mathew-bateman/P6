-- Removes only this additive Coding Check release. RESTRICT prevents removing
-- unexpected dependent user objects. No P6 source/settings/results are changed.
BEGIN;
SET LOCAL lock_timeout = '10s';
DROP VIEW IF EXISTS reporting.coding_check_path_counts;
DROP VIEW IF EXISTS reporting.coding_check_path_summary;
DROP VIEW IF EXISTS reporting.coding_check_project_stage;
DROP VIEW IF EXISTS reporting.coding_check_discipline;
DROP VIEW IF EXISTS reporting.coding_check_design_key;
DROP VIEW IF EXISTS reporting.coding_check_milestone_type;
DROP VIEW IF EXISTS reporting.coding_check_responsible_company;
DROP VIEW IF EXISTS reporting.coding_check_activity_code_counts;
DROP VIEW IF EXISTS coding_check.code_counts;
DROP VIEW IF EXISTS coding_check.project_baseline_flags;
DROP VIEW IF EXISTS coding_check.activity_coding;
DROP VIEW IF EXISTS coding_check.code_values;
DROP VIEW IF EXISTS coding_check.code_types;
DROP VIEW IF EXISTS coding_check.activities;
DROP VIEW IF EXISTS coding_check.projects;
DROP VIEW IF EXISTS coding_check.dimensions;
DROP FUNCTION IF EXISTS coding_check.clean_text(text);
DROP SCHEMA IF EXISTS coding_check;
COMMIT;
