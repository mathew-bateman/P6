WITH logic_loop_tasks AS (
  SELECT DISTINCT
    config_version_id,
    check_run_id,
    proj_id,
    task_id
  FROM "__schema__"."xertoolkit_result_logic_loop_tasks"
), out_of_sequence_successors AS (
  /* One successor can have several qualifying relationships; count it once. */
  SELECT
    exception.config_version_id,
    exception.check_run_id,
    exception.refreshed_at,
    exception.proj_id,
    exception.successor_task_id AS task_id,
    MAX(CAST(exception.successor_code AS VARCHAR(40))) AS task_code,
    MAX(CAST(exception.successor_name AS VARCHAR(120))) AS task_name
  FROM "__schema__"."xertoolkit_result_out_of_sequence_exceptions" AS exception
  GROUP BY
    exception.config_version_id,
    exception.check_run_id,
    exception.refreshed_at,
    exception.proj_id,
    exception.successor_task_id
)
SELECT
  evidence.config_version_id,
  evidence.check_run_id,
  evidence.refreshed_at,
  evidence.proj_id,
  scope.check_code,
  scope.display_name AS check_name,
  scope.sort_order AS check_sort_order,
  evidence.task_id,
  evidence.task_code,
  evidence.task_name,
  activity.total_float_days,
  evidence.evidence_basis,
  evidence.detail_json,
  COALESCE(evidence.detail_display, N'N/A') AS evidence_display
FROM "__schema__"."xertoolkit_result_schedule_quality_task_evidence" AS evidence
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS scope
  ON scope.config_version_id = evidence.config_version_id
  AND scope.check_code = evidence.check_code
LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS activity
  ON activity.proj_id = evidence.proj_id AND activity.task_id = evidence.task_id
UNION ALL
SELECT
  logic.config_version_id,
  logic.check_run_id,
  metrics.refreshed_at,
  logic.proj_id,
  scope.check_code,
  scope.display_name AS check_name,
  scope.sort_order AS check_sort_order,
  logic.task_id,
  CAST(activity.task_code AS VARCHAR(40)) AS task_code,
  CAST(activity.task_name AS VARCHAR(120)) AS task_name,
  activity.total_float_days,
  CAST('logical_loop_member' AS VARCHAR(40)) AS evidence_basis,
  CAST(NULL AS TEXT) AS detail_json,
  CAST(N'N/A' AS VARCHAR(1000)) AS evidence_display
FROM logic_loop_tasks AS logic
JOIN "__schema__"."xertoolkit_result_project_metrics" AS metrics
  ON metrics.proj_id = logic.proj_id
  AND metrics.check_run_id = logic.check_run_id
  AND metrics.config_version_id = logic.config_version_id
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS scope
  ON scope.config_version_id = logic.config_version_id
  AND scope.check_code = 'logical_loops'
LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS activity
  ON activity.proj_id = logic.proj_id AND activity.task_id = logic.task_id
UNION ALL
SELECT
  successor.config_version_id,
  successor.check_run_id,
  successor.refreshed_at,
  successor.proj_id,
  scope.check_code,
  scope.display_name AS check_name,
  scope.sort_order AS check_sort_order,
  successor.task_id,
  successor.task_code,
  successor.task_name,
  activity.total_float_days,
  CAST('out_of_sequence_successor' AS VARCHAR(40)) AS evidence_basis,
  CAST(NULL AS TEXT) AS detail_json,
  CAST(N'N/A' AS VARCHAR(1000)) AS evidence_display
FROM out_of_sequence_successors AS successor
JOIN "__schema__"."xertoolkit_result_project_metrics" AS metrics
  ON metrics.proj_id = successor.proj_id
  AND metrics.check_run_id = successor.check_run_id
  AND metrics.config_version_id = successor.config_version_id
  AND metrics.refreshed_at = successor.refreshed_at
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS scope
  ON scope.config_version_id = successor.config_version_id
  AND scope.check_code = 'out_of_sequence'
LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS activity
  ON activity.proj_id = successor.proj_id AND activity.task_id = successor.task_id;
