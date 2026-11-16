WITH active_metrics AS (
  SELECT
    metrics.*
  FROM "__schema__"."xertoolkit_result_project_metrics" AS metrics
  JOIN "__schema__"."xertoolkit_schedule_quality_profile" AS profile
    ON profile.active_config_version_id = metrics.config_version_id
  WHERE
    profile.profile_code = N'default'
), open_end_denominators AS (
  SELECT
    metrics.proj_id,
    metrics.config_version_id,
    SUM(
      CASE
        WHEN open_ends.in_open_start_scope = 1 AND open_ends.is_milestone = 0
        THEN 1
        ELSE 0
      END
    ) AS open_start_records,
    SUM(
      CASE
        WHEN open_ends.in_open_finish_scope = 1 AND open_ends.is_milestone = 0
        THEN 1
        ELSE 0
      END
    ) AS open_finish_records
  FROM active_metrics AS metrics LEFT JOIN LATERAL (
    SELECT
      *
    FROM "__schema__".xertoolkit_fn_open_ends
    WHERE
      proj_id = metrics.proj_id
  ) AS open_ends ON TRUE
  GROUP BY
    metrics.proj_id,
    metrics.config_version_id
), relationship_denominators AS (
  SELECT
    metrics.proj_id,
    metrics.config_version_id,
    SUM(relationship_quality.is_excessive_ss_eligible) AS excessive_ss_records,
    SUM(relationship_quality.is_excessive_ff_eligible) AS excessive_ff_records
  FROM active_metrics AS metrics LEFT JOIN LATERAL (
    SELECT
      *
    FROM "__schema__".xertoolkit_fn_relationship_quality
    WHERE
      proj_id = metrics.proj_id
  ) AS relationship_quality ON TRUE
  GROUP BY
    metrics.proj_id,
    metrics.config_version_id
)
SELECT
  metrics.proj_id,
  metrics.project_name,
  metrics.updated_date,
  metrics.check_run_id,
  metrics.refreshed_at,
  metrics.config_version_id,
  scope.check_code,
  scope.display_name,
  scope.sort_order,
  scope.limit_type,
  scope.green_limit,
  scope.amber_limit,
  scope.green_points,
  scope.amber_points,
  scope.records_metric,
  scope.qualifying_metric,
  CAST(CASE scope.records_metric
    WHEN 'dcma_activity'
    THEN metrics.dcma_activity_count
    WHEN 'soft_constraint_activity'
    THEN metrics.soft_constraint_activity_count
    WHEN 'relationship'
    THEN metrics.relationship_count
    WHEN 'open_start_eligible'
    THEN COALESCE(open_end.open_start_records, 0)
    WHEN 'open_finish_eligible'
    THEN COALESCE(open_end.open_finish_records, 0)
    WHEN 'excessive_ss_eligible'
    THEN COALESCE(relationship.excessive_ss_records, 0)
    WHEN 'excessive_ff_eligible'
    THEN COALESCE(relationship.excessive_ff_records, 0)
    WHEN 'none'
    THEN 0
    ELSE 0
  END AS BIGINT) AS records_checked,
  CAST(CASE scope.qualifying_metric
    WHEN 'missing_predecessor'
    THEN metrics.missing_predecessor_count
    WHEN 'missing_successor'
    THEN metrics.missing_successor_count
    WHEN 'open_start'
    THEN metrics.open_start_count
    WHEN 'open_finish'
    THEN metrics.open_finish_count
    WHEN 'relationship_leads'
    THEN metrics.lead_count
    WHEN 'relationship_lags'
    THEN metrics.lag_count
    WHEN 'non_fs'
    THEN metrics.non_fs_count
    WHEN 'constraints'
    THEN metrics.constraint_count
    WHEN 'soft_constraints'
    THEN metrics.soft_constraint_count
    WHEN 'high_float'
    THEN metrics.high_float_count
    WHEN 'negative_float'
    THEN metrics.negative_float_count
    WHEN 'high_duration'
    THEN metrics.high_duration_count
    WHEN 'invalid_dates'
    THEN metrics.invalid_date_count
    WHEN 'in_progress_errors'
    THEN metrics.in_progress_error_count
    WHEN 'logical_loops'
    THEN metrics.logical_loop_count
    WHEN 'out_of_sequence'
    THEN metrics.out_of_sequence_count
    WHEN 'critical_tasks'
    THEN metrics.critical_task_count
    WHEN 'near_critical_tasks'
    THEN metrics.near_critical_task_count
    WHEN 'riding_progress_date'
    THEN metrics.riding_progress_date_count
    WHEN 'excessive_ss_lag'
    THEN metrics.excessive_ss_lag_count
    WHEN 'excessive_ff_lag'
    THEN metrics.excessive_ff_lag_count
    ELSE 0
  END AS BIGINT) AS qualifying_results
FROM active_metrics AS metrics
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS scope
  ON scope.config_version_id = metrics.config_version_id
  AND scope.is_enabled = 1
  AND scope.check_code <> 'soft_constraints' /* FYI_EXCLUDED_FROM_DCMA_V1 */
LEFT JOIN open_end_denominators AS open_end
  ON open_end.proj_id = metrics.proj_id
  AND open_end.config_version_id = metrics.config_version_id
LEFT JOIN relationship_denominators AS relationship
  ON relationship.proj_id = metrics.proj_id
  AND relationship.config_version_id = metrics.config_version_id;
