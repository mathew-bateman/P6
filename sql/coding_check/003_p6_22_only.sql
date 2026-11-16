-- Restrict existing Coding Check views to P6 22. Column contracts/grants are preserved.
-- All public coding_check views inherit this scope through their helper dependencies.
BEGIN;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '120s';

CREATE OR REPLACE VIEW coding_check.projects AS
WITH assignments AS (
  SELECT DISTINCT a._source_system, a.proj_id,
    lower(btrim(t.proj_catg_type)) AS code_type,
    v.proj_catg_id,
    coalesce(coding_check.clean_text(v.proj_catg_name),
             coding_check.clean_text(v.proj_catg_short_name)) AS code_value,
    coding_check.clean_text(v.proj_catg_short_name) AS short_value
  FROM reporting.projpcat a
  JOIN reporting.pcattype t ON t._source_system = a._source_system
    AND t.proj_catg_type_id = a.proj_catg_type_id
  JOIN reporting.pcatval v ON v._source_system = a._source_system
    AND v.proj_catg_type_id = a.proj_catg_type_id
    AND v.proj_catg_id = a.proj_catg_id
  WHERE a._source_system = 'p6_22'
    AND a.delete_date IS NULL AND a.delete_session_id IS NULL
    AND t.delete_date IS NULL AND t.delete_session_id IS NULL
    AND v.delete_date IS NULL AND v.delete_session_id IS NULL
    AND lower(btrim(t.proj_catg_type)) IN
      ('project id', 'account', 'lead planner', 'period', 'project state', 'project status')
), per_type AS (
  SELECT _source_system, proj_id, code_type, count(*) AS value_count,
    CASE WHEN count(*) = 1 THEN min(code_value) END AS code_value,
    CASE WHEN count(*) = 1 THEN min(short_value) END AS short_value
  FROM assignments GROUP BY _source_system, proj_id, code_type
), filters AS (
  SELECT _source_system, proj_id,
    max(code_value) FILTER (WHERE code_type = 'project id') AS project_id,
    max(code_value) FILTER (WHERE code_type = 'account') AS account,
    max(code_value) FILTER (WHERE code_type = 'lead planner') AS lead_planner,
    max(code_value) FILTER (WHERE code_type = 'period') AS period,
    max(short_value) FILTER (WHERE code_type = 'period') AS period_code,
    max(code_value) FILTER (WHERE code_type = 'project state') AS project_state,
    max(code_value) FILTER (WHERE code_type = 'project status') AS project_status,
    count(*) FILTER (WHERE value_count > 1) AS project_code_conflict_count
  FROM per_type GROUP BY _source_system, proj_id
)
SELECT p._source_system, p._source_database, p.proj_id, p.proj_short_name,
  f.project_id AS "Project ID", f.account AS "Account",
  f.lead_planner AS "Lead Planner", f.period AS "Period",
  f.period_code AS "Period Code", f.project_state AS "Project State",
  f.project_status AS "Project Status", f.project_code_conflict_count,
  p._project_key, p._snapshot_at, p._synced_at, p._batch_id
FROM reporting.project p
JOIN filters f ON f._source_system = p._source_system AND f.proj_id = p.proj_id
WHERE p._source_system = 'p6_22'
  AND p.project_flag = 'Y'
  AND p.delete_date IS NULL AND p.delete_session_id IS NULL
  AND lower(f.project_state) = 'live'
  AND lower(f.project_status) = 'client submitted';

CREATE OR REPLACE VIEW coding_check.code_types AS
SELECT s._source_system, d.code_dimension, d.code_type_name, d.dimension_order,
  t.actv_code_type_id, t.actv_code_type AS source_code_type_name
FROM (SELECT DISTINCT _source_system FROM reporting.project WHERE _source_system = 'p6_22') s
CROSS JOIN coding_check.dimensions d
LEFT JOIN reporting.actvtype t ON t._source_system = s._source_system
  AND t.actv_code_type_scope = 'AS_Global'
  AND t.delete_date IS NULL AND t.delete_session_id IS NULL
  AND lower(btrim(t.actv_code_type)) = lower(d.code_type_name);

COMMIT;
