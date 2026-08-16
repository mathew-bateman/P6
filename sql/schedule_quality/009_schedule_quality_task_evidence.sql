/*
    Materialise the task IDs behind the Power BI schedule-quality checks.

    This is an additive deployment for databases that already have the
    versioned schedule-quality objects from 001_versioned_settings_forward.sql.

    The triggers run inside the existing project-metrics delete/insert
    transaction. A committed project metric therefore carries task evidence
    from the same refresh run and configuration version. Logical Loops and
    Out of Sequence retain their existing dedicated result contracts.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51800, 'This deployment must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_run_history]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_config_version]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]', N'IF') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_fn_relationship_quality]', N'IF') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]', N'IF') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]', N'V') IS NULL
    THROW 51801, 'Deploy the versioned schedule-quality SQL before this extension.', 1;
GO

BEGIN TRANSACTION;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    (
        proj_id int NOT NULL,
        check_code varchar(50) NOT NULL,
        task_id int NOT NULL,
        task_code varchar(40) NOT NULL,
        task_name varchar(120) NOT NULL,
        evidence_basis varchar(40) NOT NULL,
        check_run_id bigint NOT NULL,
        refreshed_at datetime2(7) NOT NULL,
        config_version_id bigint NOT NULL,
        CONSTRAINT [PK_xertoolkit_result_schedule_quality_task_evidence]
            PRIMARY KEY CLUSTERED (proj_id, check_code, task_id),
        CONSTRAINT [FK_xertoolkit_task_evidence_run]
            FOREIGN KEY (check_run_id)
            REFERENCES [powerbitables].[xertoolkit_refresh_run_history] (check_run_id),
        CONSTRAINT [FK_xertoolkit_task_evidence_config]
            FOREIGN KEY (config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
    );

    CREATE INDEX [IX_xertoolkit_task_evidence_check]
        ON [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        (check_code, proj_id, task_id)
        INCLUDE (task_code, task_name, evidence_basis);

    CREATE INDEX [IX_xertoolkit_task_evidence_snapshot]
        ON [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        (config_version_id, check_run_id, proj_id);
END;
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH task_evidence AS
    (
        SELECT
            oe.proj_id,
            mapped.check_code,
            oe.task_id,
            mapped.evidence_basis
        FROM [powerbitables].[xertoolkit_fn_open_ends](@config_version_id) AS oe
        CROSS APPLY
        (
            VALUES
            ('missing_predecessor', oe.is_missing_predecessor, 'activity_exception'),
            ('missing_successor', oe.is_missing_successor, 'activity_exception'),
            ('open_start', oe.is_open_start, 'activity_exception'),
            ('open_finish', oe.is_open_finish, 'activity_exception')
        ) AS mapped (check_code, is_match, evidence_basis)
        WHERE mapped.is_match = 1

        UNION ALL

        SELECT
            aq.proj_id,
            mapped.check_code,
            aq.task_id,
            mapped.evidence_basis
        FROM [powerbitables].[xertoolkit_fn_activity_quality](@config_version_id) AS aq
        CROSS APPLY
        (
            VALUES
            ('high_duration', aq.is_high_duration, 'activity_exception'),
            ('high_float', aq.is_high_float, 'activity_exception'),
            ('negative_float', aq.is_negative_float, 'activity_exception'),
            ('constraints', aq.is_constraint, 'activity_exception'),
            ('critical_tasks', aq.is_critical_task, 'activity_exception'),
            ('near_critical_tasks', aq.is_near_critical_task, 'activity_exception'),
            ('invalid_dates', aq.is_invalid_date, 'activity_exception'),
            ('in_progress_errors', aq.is_in_progress_error, 'activity_exception'),
            ('riding_progress_date', aq.is_riding_progress_date, 'activity_exception')
        ) AS mapped (check_code, is_match, evidence_basis)
        WHERE mapped.is_match = 1

        UNION ALL

        SELECT
            rq.proj_id,
            mapped.check_code,
            endpoint.task_id,
            mapped.evidence_basis
        FROM [powerbitables].[xertoolkit_fn_relationship_quality](@config_version_id) AS rq
        CROSS APPLY
        (
            VALUES
            ('relationship_leads', rq.is_lead, 'relationship_endpoint'),
            ('relationship_lags', rq.is_lag, 'relationship_endpoint'),
            /*
               The headline relationship ratio is project-level and has no
               individually failing activity. The existing task-level evidence
               governed by that check is the set of non-FS relationship endpoints.
            */
            ('relationship_ratio', rq.is_non_fs, 'non_fs_relationship_endpoint'),
            ('excessive_ss_lag', rq.is_excessive_ss_lag, 'relationship_endpoint'),
            ('excessive_ff_lag', rq.is_excessive_ff_lag, 'relationship_endpoint')
        ) AS mapped (check_code, is_match, evidence_basis)
        CROSS APPLY
        (
            VALUES
            (rq.predecessor_task_id),
            (rq.successor_task_id)
        ) AS endpoint (task_id)
        WHERE mapped.is_match = 1
          AND endpoint.task_id IS NOT NULL
    )
    SELECT DISTINCT
        @config_version_id AS config_version_id,
        proj_id,
        check_code,
        task_id,
        evidence_basis
    FROM task_evidence
);
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

CREATE OR ALTER TRIGGER [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete]
ON [powerbitables].[xertoolkit_result_project_metrics]
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;

    DELETE evidence
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
    JOIN deleted
      ON deleted.proj_id = evidence.proj_id;
END;
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

COMMIT TRANSACTION;
GO

SELECT
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]')
    ) AS task_evidence_table,
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]')
    ) AS task_evidence_function,
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
    ) AS task_evidence_view,
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete]')
    ) AS delete_trigger,
    OBJECT_NAME
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert]')
    ) AS insert_trigger,
    COUNT_BIG(*) AS task_evidence_rows
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence];
GO
