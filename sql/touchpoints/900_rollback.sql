-- Removes only the new Touchpoints objects. No CASCADE; downstream SQL dependencies block rollback safely.
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='8s';
DROP VIEW IF EXISTS reporting.touchpoint_movement_summary;
DROP VIEW IF EXISTS reporting.touchpoint_finish_history;
DROP VIEW IF EXISTS reporting.touchpoint_interface_routes;
DROP VIEW IF EXISTS reporting.touchpoint_activity_detail;
DROP VIEW IF EXISTS reporting.touchpoint_finish_movement;
DROP VIEW IF EXISTS touchpoints.matched_activities;
DROP VIEW IF EXISTS touchpoints.enriched_activities;
DROP VIEW IF EXISTS touchpoints.activity_commentary;
DROP VIEW IF EXISTS touchpoints.interface_routes;
DROP VIEW IF EXISTS touchpoints.activity_facts;
DROP SCHEMA IF EXISTS touchpoints;
COMMIT;
