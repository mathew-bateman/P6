/* Roll back only the additive out-of-sequence exception materialisation. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51510, 'This rollback must be run against P62212_1.', 1;

IF OBJECT_DEFINITION
   (
       OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
   ) LIKE N'%xertoolkit_result_out_of_sequence_exceptions%'
    THROW 51511, 'Run 913_complete_task_evidence_view_rollback.sql before this rollback.', 1;
GO

DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_oos_insert];
DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_oos_delete];
DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions];
DROP TABLE IF EXISTS [powerbitables].[xertoolkit_result_out_of_sequence_exceptions];
GO

SELECT
    OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]', N'U') AS result_table_object_id,
    OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]', N'V') AS power_bi_view_object_id,
    OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]', N'TR') AS insert_trigger_object_id,
    OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_delete]', N'TR') AS delete_trigger_object_id;
