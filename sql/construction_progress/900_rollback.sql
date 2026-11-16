\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='8s';
DROP VIEW IF EXISTS reporting.construction_milestone_type_bridge;
DROP VIEW IF EXISTS reporting.construction_milestone_detail;
DROP VIEW IF EXISTS reporting.construction_activity_movement;
DROP VIEW IF EXISTS reporting.construction_activity_detail;
DROP VIEW IF EXISTS construction_progress.matched;
DROP VIEW IF EXISTS construction_progress.cuts;
DROP SCHEMA IF EXISTS construction_progress;
COMMIT;
