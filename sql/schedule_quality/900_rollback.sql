/*
    Restore the pre-2026-07-14 schedule-quality SQL modules, schemas, and data.
    Requires the immutable objects created by 000_predeploy_snapshot.sql.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51900, 'This rollback must be run against P62212_1.', 1;

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_modules]', N'U') IS NULL
   OR OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_project_metrics]', N'U') IS NULL
   OR OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_logic_loop_tasks]', N'U') IS NULL
   OR OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_refresh_history]', N'U') IS NULL
   OR OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_settings_thresholds]', N'U') IS NULL
    THROW 51901, 'The required pre-deployment snapshot is incomplete.', 1;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert];
    DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence];
    DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_oos_insert];
    DROP TRIGGER IF EXISTS [powerbitables].[xertoolkit_trg_project_metrics_oos_delete];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions];
    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_result_out_of_sequence_exceptions];

    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_schedule_quality_constraint_types];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_schedule_quality_options];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_schedule_quality_settings];

    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_result_schedule_quality_task_evidence];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_ActivityQualityValidation];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_OutOfSequence];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_ActivityQuality];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_RelationshipQuality];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_OpenEnds];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_ProjectMetrics];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_Relationships];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_Activities];
    DROP VIEW IF EXISTS [powerbitables].[xertoolkit_vw_PBI_Projects];

    DROP PROCEDURE IF EXISTS [powerbitables].[xertoolkit_publish_schedule_quality_config];
    DROP PROCEDURE IF EXISTS [powerbitables].[xertoolkit_save_schedule_quality_draft];
    DROP PROCEDURE IF EXISTS [powerbitables].[xertoolkit_get_or_create_schedule_quality_draft];
    DROP PROCEDURE IF EXISTS [powerbitables].[xertoolkit_refresh_all_schedule_quality];

    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_out_of_sequence];
    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence];
    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_activity_quality];
    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_relationship_quality];
    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_open_ends];
    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_relationship_in_scope];
    DROP FUNCTION IF EXISTS [powerbitables].[xertoolkit_fn_activity_in_scope];

    IF OBJECT_ID(N'[powerbitables].[FK_xertoolkit_project_metrics_config_version]', N'F') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_result_project_metrics]
        DROP CONSTRAINT [FK_xertoolkit_project_metrics_config_version];

    IF OBJECT_ID(N'[powerbitables].[FK_xertoolkit_logic_loop_config_version]', N'F') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_result_logic_loop_tasks]
        DROP CONSTRAINT [FK_xertoolkit_logic_loop_config_version];

    IF OBJECT_ID(N'[powerbitables].[FK_xertoolkit_refresh_history_config_version]', N'F') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_refresh_run_history]
        DROP CONSTRAINT [FK_xertoolkit_refresh_history_config_version];

    IF EXISTS
    (
        SELECT 1 FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]')
          AND name = N'IX_xertoolkit_project_metrics_config_version'
    )
        DROP INDEX [IX_xertoolkit_project_metrics_config_version]
        ON [powerbitables].[xertoolkit_result_project_metrics];

    IF EXISTS
    (
        SELECT 1 FROM sys.indexes
        WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]')
          AND name = N'IX_xertoolkit_logic_loop_config_version'
    )
        DROP INDEX [IX_xertoolkit_logic_loop_config_version]
        ON [powerbitables].[xertoolkit_result_logic_loop_tasks];

    IF COL_LENGTH(N'powerbitables.xertoolkit_result_project_metrics', N'config_version_id') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_result_project_metrics]
        DROP COLUMN config_version_id;

    IF COL_LENGTH(N'powerbitables.xertoolkit_result_logic_loop_tasks', N'config_version_id') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_result_logic_loop_tasks]
        DROP COLUMN config_version_id;

    IF COL_LENGTH(N'powerbitables.xertoolkit_refresh_run_history', N'config_version_id') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_refresh_run_history]
        DROP COLUMN config_version_id;

    DELETE FROM [powerbitables].[xertoolkit_result_project_metrics];
    INSERT INTO [powerbitables].[xertoolkit_result_project_metrics]
    (
        proj_id,
        project_name,
        updated_date,
        activity_count,
        dcma_activity_count,
        relationship_count,
        relationship_ratio,
        missing_predecessor_count,
        missing_successor_count,
        open_start_count,
        open_finish_count,
        lead_count,
        lag_count,
        non_fs_count,
        high_float_count,
        negative_float_count,
        high_duration_count,
        constraint_count,
        invalid_date_count,
        in_progress_error_count,
        riding_progress_date_count,
        critical_task_count,
        near_critical_task_count,
        out_of_sequence_count,
        logical_loop_count,
        check_run_id,
        refreshed_at,
        excessive_ss_lag_count,
        excessive_ff_lag_count
    )
    SELECT
        proj_id,
        project_name,
        updated_date,
        activity_count,
        dcma_activity_count,
        relationship_count,
        relationship_ratio,
        missing_predecessor_count,
        missing_successor_count,
        open_start_count,
        open_finish_count,
        lead_count,
        lag_count,
        non_fs_count,
        high_float_count,
        negative_float_count,
        high_duration_count,
        constraint_count,
        invalid_date_count,
        in_progress_error_count,
        riding_progress_date_count,
        critical_task_count,
        near_critical_task_count,
        out_of_sequence_count,
        logical_loop_count,
        check_run_id,
        refreshed_at,
        excessive_ss_lag_count,
        excessive_ff_lag_count
    FROM [xertoolkit_rollback].[schedule_quality_20260714_project_metrics];

    DELETE FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];
    INSERT INTO [powerbitables].[xertoolkit_result_logic_loop_tasks]
    (
        proj_id,
        task_id,
        loop_path,
        loop_length,
        calculated_date,
        check_run_id,
        calculation_method
    )
    SELECT
        proj_id,
        task_id,
        loop_path,
        loop_length,
        calculated_date,
        check_run_id,
        calculation_method
    FROM [xertoolkit_rollback].[schedule_quality_20260714_logic_loop_tasks];

    DELETE FROM [powerbitables].[xertoolkit_refresh_run_history];
    SET IDENTITY_INSERT [powerbitables].[xertoolkit_refresh_run_history] ON;
    INSERT INTO [powerbitables].[xertoolkit_refresh_run_history]
    (
        check_run_id,
        requested_proj_id,
        started_at,
        completed_at,
        status,
        processed_project_count,
        logic_loop_task_count,
        error_message,
        trigger_type
    )
    SELECT
        check_run_id,
        requested_proj_id,
        started_at,
        completed_at,
        status,
        processed_project_count,
        logic_loop_task_count,
        error_message,
        trigger_type
    FROM [xertoolkit_rollback].[schedule_quality_20260714_refresh_history];
    SET IDENTITY_INSERT [powerbitables].[xertoolkit_refresh_run_history] OFF;

    DECLARE @restored_max_check_run_id bigint =
    (
        SELECT ISNULL(MAX(check_run_id), 0)
        FROM [xertoolkit_rollback].[schedule_quality_20260714_refresh_history]
    );
    DECLARE @reseed_sql nvarchar(300) =
        N'DBCC CHECKIDENT (''[powerbitables].[xertoolkit_refresh_run_history]'', RESEED, '
        + CONVERT(nvarchar(30), @restored_max_check_run_id)
        + N') WITH NO_INFOMSGS;';
    EXEC sys.sp_executesql @reseed_sql;

    DELETE FROM [powerbitables].[xertoolkit_settings_thresholds];
    INSERT INTO [powerbitables].[xertoolkit_settings_thresholds]
    SELECT *
    FROM [xertoolkit_rollback].[schedule_quality_20260714_settings_thresholds];

    IF OBJECT_ID(N'[powerbitables].[FK_xertoolkit_sq_profile_active_version]', N'F') IS NOT NULL
        ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_profile]
        DROP CONSTRAINT [FK_xertoolkit_sq_profile_active_version];

    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_schedule_quality_constraint_type];
    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_schedule_quality_option];
    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_schedule_quality_check_scope];
    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_schedule_quality_config_version];
    DROP TABLE IF EXISTS [powerbitables].[xertoolkit_schedule_quality_profile];

    DECLARE @definition nvarchar(max);

    DECLARE module_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT definition
    FROM [xertoolkit_rollback].[schedule_quality_20260714_modules]
    ORDER BY
        CASE object_name
            WHEN N'xertoolkit_vw_PBI_Projects' THEN 10
            WHEN N'xertoolkit_vw_PBI_Activities' THEN 20
            WHEN N'xertoolkit_vw_PBI_Relationships' THEN 30
            WHEN N'xertoolkit_vw_PBI_OpenEnds' THEN 40
            WHEN N'xertoolkit_vw_PBI_RelationshipQuality' THEN 50
            WHEN N'xertoolkit_vw_PBI_ActivityQuality' THEN 60
            WHEN N'xertoolkit_vw_PBI_ActivityQualityValidation' THEN 70
            WHEN N'xertoolkit_vw_PBI_OutOfSequence' THEN 80
            WHEN N'xertoolkit_vw_PBI_ProjectMetrics' THEN 90
            WHEN N'xertoolkit_refresh_all_schedule_quality' THEN 100
            ELSE 1000
        END;

    OPEN module_cursor;
    FETCH NEXT FROM module_cursor INTO @definition;

    WHILE @@FETCH_STATUS = 0
    BEGIN
        EXEC sys.sp_executesql @definition;
        FETCH NEXT FROM module_cursor INTO @definition;
    END;

    CLOSE module_cursor;
    DEALLOCATE module_cursor;

    /* This pre-existing view was not part of the original ten-module snapshot. */
    EXEC sys.sp_executesql N'
CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_LogicLoops]
AS
SELECT
    proj_id,
    task_id,
    loop_path,
    loop_length
FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];';

    COMMIT TRANSACTION;

    SELECT
        N'rollback complete' AS rollback_status,
        (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_result_project_metrics]) AS restored_project_metric_rows,
        (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_result_logic_loop_tasks]) AS restored_logic_loop_rows,
        (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_refresh_run_history]) AS restored_refresh_history_rows;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;

    BEGIN TRY
        SET IDENTITY_INSERT [powerbitables].[xertoolkit_refresh_run_history] OFF;
    END TRY
    BEGIN CATCH
        /* Ignore if IDENTITY_INSERT was not enabled. */
    END CATCH;

    THROW;
END CATCH;
GO
