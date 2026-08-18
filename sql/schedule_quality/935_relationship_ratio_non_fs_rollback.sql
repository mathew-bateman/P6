/* Restore the pre-035 relationship-ratio calculation and insert trigger. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
    THROW 51850, 'Required project-metrics result table is missing.', 1;
GO

UPDATE [powerbitables].[xertoolkit_result_project_metrics]
SET relationship_ratio = CAST
(
    ISNULL(relationship_count, 0) * 1.0
    / NULLIF(ISNULL(dcma_activity_count, 0), 0)
    AS decimal(18,2)
);
GO

CREATE OR ALTER TRIGGER [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert]
ON [powerbitables].[xertoolkit_result_project_metrics]
AFTER INSERT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF EXISTS
    (
        SELECT 1
        FROM inserted
        WHERE check_run_id IS NULL
           OR config_version_id IS NULL
           OR refreshed_at IS NULL
    )
        THROW 51802, 'Task evidence requires run-stamped project metrics.', 1;

    DELETE evidence
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
    JOIN inserted
      ON inserted.proj_id = evidence.proj_id;

    ;WITH refresh_configs AS
    (
        SELECT DISTINCT config_version_id
        FROM inserted
    )
    INSERT INTO [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    (
        proj_id,
        check_code,
        task_id,
        task_code,
        task_name,
        evidence_basis,
        check_run_id,
        refreshed_at,
        config_version_id
    )
    SELECT
        result.proj_id,
        evidence.check_code,
        evidence.task_id,
        activity.task_code,
        activity.task_name,
        evidence.evidence_basis,
        result.check_run_id,
        result.refreshed_at,
        result.config_version_id
    FROM refresh_configs AS config
    CROSS APPLY [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]
        (config.config_version_id) AS evidence
    JOIN inserted AS result
      ON result.proj_id = evidence.proj_id
     AND result.config_version_id = config.config_version_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
      ON activity.proj_id = evidence.proj_id
     AND activity.task_id = evidence.task_id;
END;
GO
