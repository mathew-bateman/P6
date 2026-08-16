/*
    Immutable pre-deployment snapshot for the 2026-07-14 versioned
    schedule-quality deployment.

    This script intentionally writes only rollback/audit copies. Re-running it
    does not overwrite a snapshot that already exists.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51000, 'This snapshot must be run against P62212_1.', 1;

IF SCHEMA_ID(N'xertoolkit_rollback') IS NULL
    EXEC(N'CREATE SCHEMA [xertoolkit_rollback] AUTHORIZATION [dbo];');
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_modules]', N'U') IS NULL
BEGIN
    CREATE TABLE [xertoolkit_rollback].[schedule_quality_20260714_modules]
    (
        object_name sysname NOT NULL PRIMARY KEY,
        object_type char(2) NOT NULL,
        definition nvarchar(max) NOT NULL,
        captured_at datetime2(7) NOT NULL
            CONSTRAINT [DF_sq_20260714_modules_captured_at] DEFAULT SYSUTCDATETIME()
    );

    INSERT INTO [xertoolkit_rollback].[schedule_quality_20260714_modules]
        (object_name, object_type, definition)
    SELECT
        o.name,
        o.type,
        m.definition
    FROM sys.objects AS o
    JOIN sys.schemas AS s
      ON s.schema_id = o.schema_id
    JOIN sys.sql_modules AS m
      ON m.object_id = o.object_id
    WHERE s.name = N'powerbitables'
      AND o.name IN
      (
          N'xertoolkit_vw_PBI_Projects',
          N'xertoolkit_vw_PBI_Activities',
          N'xertoolkit_vw_PBI_Relationships',
          N'xertoolkit_vw_PBI_OpenEnds',
          N'xertoolkit_vw_PBI_RelationshipQuality',
          N'xertoolkit_vw_PBI_ActivityQuality',
          N'xertoolkit_vw_PBI_ActivityQualityValidation',
          N'xertoolkit_vw_PBI_OutOfSequence',
          N'xertoolkit_vw_PBI_ProjectMetrics',
          N'xertoolkit_refresh_all_schedule_quality'
      );

    IF (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260714_modules]) <> 10
        THROW 51001, 'Snapshot did not capture all ten required module definitions.', 1;
END;
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_table_columns]', N'U') IS NULL
BEGIN
    SELECT
        o.name AS object_name,
        c.column_id,
        c.name AS column_name,
        TYPE_NAME(c.user_type_id) AS data_type,
        c.max_length,
        c.precision,
        c.scale,
        c.is_nullable,
        c.is_identity,
        dc.name AS default_name,
        dc.definition AS default_definition,
        SYSUTCDATETIME() AS captured_at
    INTO [xertoolkit_rollback].[schedule_quality_20260714_table_columns]
    FROM sys.objects AS o
    JOIN sys.schemas AS s
      ON s.schema_id = o.schema_id
    JOIN sys.columns AS c
      ON c.object_id = o.object_id
    LEFT JOIN sys.default_constraints AS dc
      ON dc.object_id = c.default_object_id
    WHERE s.name = N'powerbitables'
      AND o.name IN
      (
          N'xertoolkit_settings_thresholds',
          N'xertoolkit_result_project_metrics',
          N'xertoolkit_result_logic_loop_tasks',
          N'xertoolkit_refresh_run_history'
      );
END;
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_indexes]', N'U') IS NULL
BEGIN
    SELECT
        o.name AS object_name,
        i.name AS index_name,
        i.type_desc,
        i.is_unique,
        i.is_primary_key,
        i.filter_definition,
        ic.key_ordinal,
        ic.is_included_column,
        c.name AS column_name,
        SYSUTCDATETIME() AS captured_at
    INTO [xertoolkit_rollback].[schedule_quality_20260714_indexes]
    FROM sys.objects AS o
    JOIN sys.schemas AS s
      ON s.schema_id = o.schema_id
    JOIN sys.indexes AS i
      ON i.object_id = o.object_id
     AND i.index_id > 0
    JOIN sys.index_columns AS ic
      ON ic.object_id = i.object_id
     AND ic.index_id = i.index_id
    JOIN sys.columns AS c
      ON c.object_id = ic.object_id
     AND c.column_id = ic.column_id
    WHERE s.name = N'powerbitables'
      AND o.name IN
      (
          N'xertoolkit_settings_thresholds',
          N'xertoolkit_result_project_metrics',
          N'xertoolkit_result_logic_loop_tasks',
          N'xertoolkit_refresh_run_history'
      );
END;
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_settings_thresholds]', N'U') IS NULL
    SELECT *
    INTO [xertoolkit_rollback].[schedule_quality_20260714_settings_thresholds]
    FROM [powerbitables].[xertoolkit_settings_thresholds];
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_project_metrics]', N'U') IS NULL
    SELECT *
    INTO [xertoolkit_rollback].[schedule_quality_20260714_project_metrics]
    FROM [powerbitables].[xertoolkit_result_project_metrics];
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_logic_loop_tasks]', N'U') IS NULL
    SELECT *
    INTO [xertoolkit_rollback].[schedule_quality_20260714_logic_loop_tasks]
    FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];
GO

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_refresh_history]', N'U') IS NULL
    SELECT *
    INTO [xertoolkit_rollback].[schedule_quality_20260714_refresh_history]
    FROM [powerbitables].[xertoolkit_refresh_run_history];
GO

SELECT
    N'predeploy snapshot ready' AS snapshot_status,
    (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260714_modules]) AS module_count,
    (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260714_project_metrics]) AS project_metric_rows,
    (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260714_logic_loop_tasks]) AS logic_loop_rows,
    (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260714_refresh_history]) AS refresh_history_rows;
GO
