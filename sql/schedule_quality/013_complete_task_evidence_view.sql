/* Expose all task-level validation evidence through one Power BI view. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51890, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]', N'V') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]', N'V') IS NULL
    THROW 51891, 'Deploy the task-evidence, logic-loop, and out-of-sequence objects first.', 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
AS
WITH logic_loop_tasks AS
(
    SELECT DISTINCT
        logic.config_version_id,
        logic.check_run_id,
        logic.proj_id,
        logic.task_id
    FROM [powerbitables].[xertoolkit_result_logic_loop_tasks] AS logic
),
out_of_sequence_successors AS
(
    /* Several qualifying relationships can point to the same successor. */
    SELECT
        exception.config_version_id,
        exception.check_run_id,
        exception.refreshed_at,
        exception.proj_id,
        exception.successor_task_id AS task_id,
        MAX(CONVERT(varchar(40), exception.successor_code)) AS task_code,
        MAX(CONVERT(varchar(120), exception.successor_name)) AS task_name
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
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
    evidence.evidence_basis
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
  ON scope.config_version_id = evidence.config_version_id
 AND scope.check_code = evidence.check_code
JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
  ON activity.proj_id = evidence.proj_id
 AND activity.task_id = evidence.task_id

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
    CONVERT(varchar(40), activity.task_code) AS task_code,
    CONVERT(varchar(120), activity.task_name) AS task_name,
    activity.total_float_days,
    CONVERT(varchar(40), 'logical_loop_member') AS evidence_basis
FROM logic_loop_tasks AS logic
JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
  ON metrics.proj_id = logic.proj_id
 AND metrics.check_run_id = logic.check_run_id
 AND metrics.config_version_id = logic.config_version_id
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
  ON scope.config_version_id = logic.config_version_id
 AND scope.check_code = 'logical_loops'
JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
  ON activity.proj_id = logic.proj_id
 AND activity.task_id = logic.task_id

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
    CONVERT(varchar(40), 'out_of_sequence_successor') AS evidence_basis
FROM out_of_sequence_successors AS successor
JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
 ON metrics.proj_id = successor.proj_id
 AND metrics.check_run_id = successor.check_run_id
 AND metrics.config_version_id = successor.config_version_id
 AND metrics.refreshed_at = successor.refreshed_at
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
  ON scope.config_version_id = successor.config_version_id
 AND scope.check_code = 'out_of_sequence'
JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
  ON activity.proj_id = successor.proj_id
 AND activity.task_id = successor.task_id;
GO

SELECT
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
    ) AS unified_task_evidence_view;
GO
