\set ON_ERROR_STOP on
BEGIN;
SET LOCAL lock_timeout='8s';
DROP VIEW p6_reporting.construction_gantt_detail;
-- Retain the schema: it may contain independently owned objects or grants.
-- Original four views, their permissions and source helpers are unchanged.
COMMIT;
