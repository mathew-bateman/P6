/*
    XER-aligned Relationship Ratio

    XER defines the check as Non-FS Relationships / Total Relationships.
    Keep the materialised metric as a percentage so it matches the configured
    3% / 7% programme thresholds and the validation report presentation.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
    THROW 51840, 'Required project-metrics result table is missing.', 1;

IF COL_LENGTH(N'powerbitables.xertoolkit_result_project_metrics', N'non_fs_count') IS NULL
   OR COL_LENGTH(N'powerbitables.xertoolkit_result_project_metrics', N'relationship_count') IS NULL
   OR COL_LENGTH(N'powerbitables.xertoolkit_result_project_metrics', N'relationship_ratio') IS NULL
    THROW 51841, 'Project-metrics relationship-ratio columns are missing.', 1;
GO

/* Correct the currently materialised rows without waiting for a full refresh. */
UPDATE [powerbitables].[xertoolkit_result_project_metrics]
SET relationship_ratio = CAST
(
    ISNULL(non_fs_count, 0) * 100.0
    / NULLIF(ISNULL(relationship_count, 0), 0)
    AS decimal(18,2)
);
GO

/*
   The existing refresh procedure writes a new result set in one INSERT.  Keep
   the metric correct on every subsequent canary or full refresh without
   changing its transactional staging and evidence-generation behaviour.
*/
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

    UPDATE result
    SET relationship_ratio = CAST
    (
        ISNULL(result.non_fs_count, 0) * 100.0
        / NULLIF(ISNULL(result.relationship_count, 0), 0)
        AS decimal(18,2)
    )
    FROM [powerbitables].[xertoolkit_result_project_metrics] AS result
    JOIN inserted
      ON inserted.proj_id = result.proj_id;

    /* Defensive for direct inserts that were not preceded by the normal delete. */
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
