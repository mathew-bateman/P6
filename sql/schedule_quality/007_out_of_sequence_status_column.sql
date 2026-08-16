/* Add a Power BI-friendly text label without changing the existing Boolean flag. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51700, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]', N'IF') IS NULL
    THROW 51701, 'The out-of-sequence calculation function is missing.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]', N'U') IS NULL
    THROW 51702, 'The out-of-sequence exception snapshot is missing.', 1;
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
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequence]')) AS all_relationships_view,
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]')) AS exceptions_view;
