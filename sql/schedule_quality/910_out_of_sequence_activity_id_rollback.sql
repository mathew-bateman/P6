/* Remove only activity_id while retaining the existing text status columns. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51840, 'This rollback must be run against P62212_1.', 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_OutOfSequence]
AS
SELECT
    q.*,
    CONVERT
    (
        nvarchar(30),
        CASE
            WHEN q.is_out_of_sequence = 1 THEN N'out_of_sequence'
            ELSE N'not_out_of_sequence'
        END
    ) AS out_of_sequence_status
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
CROSS APPLY [powerbitables].[xertoolkit_fn_out_of_sequence](p.active_config_version_id) AS q
WHERE p.profile_code = N'default';
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
    exception.config_version_id,
    CONVERT(nvarchar(30), N'out_of_sequence') AS out_of_sequence_status
FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception;
GO

SELECT
    COL_LENGTH(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequence]', N'activity_id') AS all_relationships_activity_id_length,
    COL_LENGTH(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]', N'activity_id') AS exceptions_activity_id_length;
