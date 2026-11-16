-- User-requested removal, 4 November 2026. Coding Check is retained.
-- RESTRICT is intentional: unexpected new dependencies must stop the transaction.
BEGIN;
SET LOCAL lock_timeout='10s';
DROP VIEW IF EXISTS reporting.design_overview_issues;
DROP VIEW IF EXISTS reporting.design_overview_performance_code_counts;
DROP VIEW IF EXISTS reporting.design_overview_performance_detail;
DROP VIEW IF EXISTS reporting.design_overview_activity_code_counts;
DROP VIEW IF EXISTS reporting.design_overview_activity_detail;
DROP VIEW IF EXISTS reporting.design_overview_submissions;
DROP VIEW IF EXISTS design_overview.performance_detail;
DROP VIEW IF EXISTS design_overview.activity_facts;
DROP VIEW IF EXISTS design_overview.activity_codes;
DROP VIEW IF EXISTS design_overview.eligible_activities;
DROP VIEW IF EXISTS design_overview.code_buckets;
DROP VIEW IF EXISTS design_overview.submissions;
DROP VIEW IF EXISTS design_overview.period_codes;
DROP SCHEMA IF EXISTS design_overview;
COMMIT;
