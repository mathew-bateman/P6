/*
    Materialise the exact out-of-sequence relationships behind each project count.

    This is an additive deployment for databases that already have the versioned
    schedule-quality objects from 001_versioned_settings_forward.sql.

    The triggers run inside the existing project-metrics delete/insert transaction:
    - deleting a project metric removes its old exception snapshot;
    - inserting a project metric evaluates the existing out-of-sequence function,
      stores the qualifying relationships, then derives the metric count from them.

    A refresh therefore cannot commit a project count without the matching detail.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51500, 'This deployment must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_run_history]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_config_version]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]', N'IF') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]', N'V') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Relationships]', N'V') IS NULL
    THROW 51501, 'Deploy the versioned schedule-quality SQL before this extension.', 1;
GO

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
    (
        proj_id int NOT NULL,
        project_name nvarchar(255) NULL,
        project_data_date datetime2(7) NULL,
        relationship_id int NOT NULL,
        predecessor_task_id int NOT NULL,
        predecessor_code nvarchar(255) NULL,
        predecessor_name nvarchar(500) NULL,
        predecessor_status varchar(30) NULL,
        predecessor_actual_start datetime2(7) NULL,
        predecessor_actual_finish datetime2(7) NULL,
        successor_task_id int NOT NULL,
        successor_code nvarchar(255) NULL,
        successor_name nvarchar(500) NULL,
        successor_status varchar(30) NULL,
        successor_actual_start datetime2(7) NULL,
        successor_actual_finish datetime2(7) NULL,
        relationship_type varchar(12) NOT NULL,
        lag_hours decimal(18,4) NULL,
        lag_days decimal(18,2) NULL,
        out_of_sequence_reason nvarchar(300) NOT NULL,
        is_out_of_sequence bit NOT NULL,
        check_run_id bigint NOT NULL,
        refreshed_at datetime2(7) NOT NULL,
        config_version_id bigint NOT NULL,
        CONSTRAINT [PK_xertoolkit_result_out_of_sequence_exceptions]
            PRIMARY KEY CLUSTERED (proj_id, relationship_id),
        CONSTRAINT [CK_xertoolkit_oos_exception_is_qualifying]
            CHECK (is_out_of_sequence = 1),
        CONSTRAINT [FK_xertoolkit_oos_exception_run]
            FOREIGN KEY (check_run_id)
            REFERENCES [powerbitables].[xertoolkit_refresh_run_history] (check_run_id),
        CONSTRAINT [FK_xertoolkit_oos_exception_config]
            FOREIGN KEY (config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
    );

    CREATE INDEX [IX_xertoolkit_oos_exception_snapshot]
        ON [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
        (config_version_id, check_run_id, proj_id);

    CREATE INDEX [IX_xertoolkit_oos_exception_successor]
        ON [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
        (proj_id, successor_task_id);
END;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]
AS
SELECT
    exception.proj_id,
    exception.project_name,
    exception.project_data_date,
    exception.relationship_id,
    exception.predecessor_task_id,
    exception.predecessor_code,
    exception.predecessor_name,
    exception.predecessor_status,
    exception.predecessor_actual_start,
    exception.predecessor_actual_finish,
    exception.successor_task_id,
    exception.successor_code,
    exception.successor_name,
    exception.successor_status,
    exception.successor_actual_start,
    exception.successor_actual_finish,
    exception.relationship_type,
    exception.lag_hours,
    exception.lag_days,
    exception.out_of_sequence_reason,
    exception.is_out_of_sequence,
    exception.check_run_id,
    exception.refreshed_at,
    exception.config_version_id
FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception;
GO

CREATE OR ALTER TRIGGER [powerbitables].[xertoolkit_trg_project_metrics_oos_delete]
ON [powerbitables].[xertoolkit_result_project_metrics]
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;

    DELETE exception
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
    JOIN deleted
      ON deleted.proj_id = exception.proj_id;
END;
GO

CREATE OR ALTER TRIGGER [powerbitables].[xertoolkit_trg_project_metrics_oos_insert]
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
        THROW 51502, 'Out-of-sequence detail requires run-stamped project metrics.', 1;

    /* Defensive for direct inserts that were not preceded by the normal delete. */
    DELETE exception
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
    JOIN inserted
      ON inserted.proj_id = exception.proj_id;

    ;WITH refresh_configs AS
    (
        SELECT DISTINCT config_version_id
        FROM inserted
    )
    INSERT INTO [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
    (
        proj_id,
        project_name,
        project_data_date,
        relationship_id,
        predecessor_task_id,
        predecessor_code,
        predecessor_name,
        predecessor_status,
        predecessor_actual_start,
        predecessor_actual_finish,
        successor_task_id,
        successor_code,
        successor_name,
        successor_status,
        successor_actual_start,
        successor_actual_finish,
        relationship_type,
        lag_hours,
        lag_days,
        out_of_sequence_reason,
        is_out_of_sequence,
        check_run_id,
        refreshed_at,
        config_version_id
    )
    SELECT
        result.proj_id,
        result.project_name,
        result.updated_date,
        oos.relationship_id,
        oos.predecessor_task_id,
        predecessor.task_code,
        predecessor.task_name,
        oos.predecessor_status,
        predecessor.act_start_date,
        predecessor.act_end_date,
        oos.successor_task_id,
        successor.task_code,
        successor.task_name,
        oos.successor_status,
        successor.act_start_date,
        successor.act_end_date,
        oos.relationship_type,
        relationship.lag_hours,
        relationship.lag_days,
        oos.out_of_sequence_reason,
        CONVERT(bit, oos.is_out_of_sequence),
        result.check_run_id,
        result.refreshed_at,
        result.config_version_id
    FROM refresh_configs AS config
    CROSS APPLY [powerbitables].[xertoolkit_fn_out_of_sequence](config.config_version_id) AS oos
    JOIN inserted AS result
      ON result.proj_id = oos.proj_id
     AND result.config_version_id = config.config_version_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS predecessor
      ON predecessor.proj_id = oos.proj_id
     AND predecessor.task_id = oos.predecessor_task_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS successor
      ON successor.proj_id = oos.proj_id
     AND successor.task_id = oos.successor_task_id
    LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Relationships] AS relationship
      ON relationship.proj_id = oos.proj_id
     AND relationship.relationship_id = oos.relationship_id
    WHERE oos.is_out_of_sequence = 1;

    /* The committed headline count is always derived from the committed detail. */
    ;WITH exception_counts AS
    (
        SELECT
            inserted.proj_id,
            COUNT(DISTINCT exception.successor_task_id) AS out_of_sequence_count
        FROM inserted
        LEFT JOIN [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
          ON exception.proj_id = inserted.proj_id
         AND exception.check_run_id = inserted.check_run_id
         AND exception.config_version_id = inserted.config_version_id
        GROUP BY inserted.proj_id
    )
    UPDATE metrics
    SET out_of_sequence_count = counts.out_of_sequence_count
    FROM [powerbitables].[xertoolkit_result_project_metrics] AS metrics
    JOIN exception_counts AS counts
      ON counts.proj_id = metrics.proj_id;
END;
GO

SELECT
    OBJECT_SCHEMA_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]')) AS result_schema,
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]')) AS result_table,
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]')) AS power_bi_view,
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_delete]')) AS delete_trigger,
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]')) AS insert_trigger;
