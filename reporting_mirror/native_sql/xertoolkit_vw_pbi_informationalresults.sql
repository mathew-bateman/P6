SELECT
  metrics.proj_id,
  metrics.project_name,
  metrics.updated_date,
  metrics.check_run_id,
  metrics.refreshed_at,
  metrics.config_version_id,
  scope.check_code,
  scope.display_name,
  metrics.soft_constraint_activity_count AS records_checked,
  metrics.soft_constraint_count AS qualifying_results,
  CAST(1 AS SMALLINT) AS is_informational
FROM "__schema__".xertoolkit_result_project_metrics AS metrics
JOIN "__schema__".xertoolkit_schedule_quality_profile AS profile
  ON profile.active_config_version_id = metrics.config_version_id
  AND profile.profile_code = 'default'
JOIN "__schema__".xertoolkit_schedule_quality_check_scope AS scope
  ON scope.config_version_id = metrics.config_version_id
  AND scope.check_code = 'soft_constraints'
  AND scope.is_enabled = 1;
