/* Remove only the additive materialised schedule-quality task evidence. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51980, 'This rollback must be run against P62212_1.', 1;
GO

DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert];
DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete];
DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence];
DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence];
DROP TABLE IF EXISTS [powerbitables].[xertoolkit_result_schedule_quality_task_evidence];
GO

SELECT
    OBJECT_ID
    (
        N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]',
        N'U'
    ) AS task_evidence_table_object_id,
    OBJECT_ID
    (
        N'[powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]',
        N'IF'
    ) AS task_evidence_function_object_id,
    OBJECT_ID
    (
        N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]',
        N'V'
    ) AS task_evidence_view_object_id,
    OBJECT_ID
    (
        N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete]',
        N'TR'
    ) AS delete_trigger_object_id,
    OBJECT_ID
    (
        N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert]',
        N'TR'
    ) AS insert_trigger_object_id;
GO
