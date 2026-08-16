/* Restore the immediate pre-parity relationship-level rule from deployment 011. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51880, 'This rollback must be run against P62212_1.', 1;
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_out_of_sequence]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    SELECT
        @config_version_id AS config_version_id,
        r.proj_id, r.relationship_id, r.predecessor_task_id, r.successor_task_id,
        r.relationship_type,
        pred.status_code AS predecessor_status,
        succ.status_code AS successor_status,
        CONVERT
        (
            int,
            CASE
                WHEN r.relationship_type = 'PR_FS' AND succ.act_start_date IS NOT NULL
                 AND (pred.act_end_date IS NULL OR succ.act_start_date < pred.act_end_date) THEN 1
                WHEN r.relationship_type = 'PR_SS' AND succ.act_start_date IS NOT NULL
                 AND (pred.act_start_date IS NULL OR succ.act_start_date < pred.act_start_date) THEN 1
                WHEN r.relationship_type = 'PR_FF' AND succ.act_end_date IS NOT NULL
                 AND (pred.act_end_date IS NULL OR succ.act_end_date < pred.act_end_date) THEN 1
                WHEN r.relationship_type = 'PR_SF' AND succ.act_end_date IS NOT NULL
                 AND (pred.act_start_date IS NULL OR succ.act_end_date < pred.act_start_date) THEN 1
                ELSE 0
            END
        ) AS is_out_of_sequence,
        CASE
            WHEN r.relationship_type = 'PR_FS' AND succ.act_start_date IS NOT NULL
             AND (pred.act_end_date IS NULL OR succ.act_start_date < pred.act_end_date)
                THEN 'FS successor started before predecessor finished, or predecessor finish is missing'
            WHEN r.relationship_type = 'PR_SS' AND succ.act_start_date IS NOT NULL
             AND (pred.act_start_date IS NULL OR succ.act_start_date < pred.act_start_date)
                THEN 'SS successor started before predecessor started, or predecessor start is missing'
            WHEN r.relationship_type = 'PR_FF' AND succ.act_end_date IS NOT NULL
             AND (pred.act_end_date IS NULL OR succ.act_end_date < pred.act_end_date)
                THEN 'FF successor finished before predecessor finished, or predecessor finish is missing'
            WHEN r.relationship_type = 'PR_SF' AND succ.act_end_date IS NOT NULL
             AND (pred.act_start_date IS NULL OR succ.act_end_date < pred.act_start_date)
                THEN 'SF successor finished before predecessor started, or predecessor start is missing'
            ELSE NULL
        END AS out_of_sequence_reason
    FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
    JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
      ON scope.config_version_id = @config_version_id
     AND scope.check_code = 'out_of_sequence'
     AND scope.is_enabled = 1
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS pred
      ON pred.proj_id = r.proj_id AND pred.task_id = r.predecessor_task_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS succ
      ON succ.proj_id = r.proj_id AND succ.task_id = r.successor_task_id
    WHERE ISNULL(scope.exclude_complete, 0) = 0
       OR succ.is_complete = 0
);
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
        SELECT 1 FROM inserted
        WHERE check_run_id IS NULL OR config_version_id IS NULL OR refreshed_at IS NULL
    )
        THROW 51881, 'Out-of-sequence detail requires run-stamped project metrics.', 1;

    DELETE exception
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
    JOIN inserted ON inserted.proj_id = exception.proj_id;

    ;WITH refresh_configs AS
    (
        SELECT DISTINCT config_version_id FROM inserted
    )
    INSERT INTO [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
    (
        proj_id, project_name, project_data_date, relationship_id,
        predecessor_task_id, predecessor_code, predecessor_name, predecessor_status,
        predecessor_actual_start, predecessor_actual_finish,
        successor_task_id, successor_code, successor_name, successor_status,
        successor_actual_start, successor_actual_finish,
        relationship_type, lag_hours, lag_days, out_of_sequence_reason,
        is_out_of_sequence, check_run_id, refreshed_at, config_version_id
    )
    SELECT
        result.proj_id, result.project_name, result.updated_date, oos.relationship_id,
        oos.predecessor_task_id, predecessor.task_code, predecessor.task_name,
        oos.predecessor_status, predecessor.act_start_date, predecessor.act_end_date,
        oos.successor_task_id, successor.task_code, successor.task_name,
        oos.successor_status, successor.act_start_date, successor.act_end_date,
        oos.relationship_type, relationship.lag_hours, relationship.lag_days,
        oos.out_of_sequence_reason, CONVERT(bit, oos.is_out_of_sequence),
        result.check_run_id, result.refreshed_at, result.config_version_id
    FROM refresh_configs AS config
    CROSS APPLY [powerbitables].[xertoolkit_fn_out_of_sequence](config.config_version_id) AS oos
    JOIN inserted AS result
      ON result.proj_id = oos.proj_id
     AND result.config_version_id = config.config_version_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS predecessor
      ON predecessor.proj_id = oos.proj_id AND predecessor.task_id = oos.predecessor_task_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS successor
      ON successor.proj_id = oos.proj_id AND successor.task_id = oos.successor_task_id
    LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Relationships] AS relationship
      ON relationship.proj_id = oos.proj_id AND relationship.relationship_id = oos.relationship_id
    WHERE oos.is_out_of_sequence = 1;

    ;WITH exception_counts AS
    (
        SELECT inserted.proj_id, COUNT(exception.relationship_id) AS out_of_sequence_count
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
    JOIN exception_counts AS counts ON counts.proj_id = metrics.proj_id;
END;
GO

SELECT
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]')) AS restored_function,
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]')) AS restored_trigger;
