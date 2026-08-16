/* Restore the original 18-check task-evidence Power BI view. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51890, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]', N'V') IS NULL
    THROW 51891, 'The original task-evidence dependencies are missing.', 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
AS
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
 AND activity.task_id = evidence.task_id;
GO

SELECT
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
    ) AS restored_task_evidence_view;
GO
