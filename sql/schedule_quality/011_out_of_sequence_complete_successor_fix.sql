/* Make Exclude complete apply to the qualifying successor activity. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51850, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Relationships]', N'V') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]', N'V') IS NULL
    THROW 51851, 'Deploy the versioned schedule-quality SQL before this fix.', 1;
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
        r.proj_id,
        r.relationship_id,
        r.predecessor_task_id,
        r.successor_task_id,
        r.relationship_type,
        pred.status_code AS predecessor_status,
        succ.status_code AS successor_status,
        CONVERT
        (
            int,
            CASE
                WHEN r.relationship_type = 'PR_FS'
                 AND succ.act_start_date IS NOT NULL
                 AND (pred.act_end_date IS NULL OR succ.act_start_date < pred.act_end_date)
                THEN 1
                WHEN r.relationship_type = 'PR_SS'
                 AND succ.act_start_date IS NOT NULL
                 AND (pred.act_start_date IS NULL OR succ.act_start_date < pred.act_start_date)
                THEN 1
                WHEN r.relationship_type = 'PR_FF'
                 AND succ.act_end_date IS NOT NULL
                 AND (pred.act_end_date IS NULL OR succ.act_end_date < pred.act_end_date)
                THEN 1
                WHEN r.relationship_type = 'PR_SF'
                 AND succ.act_end_date IS NOT NULL
                 AND (pred.act_start_date IS NULL OR succ.act_end_date < pred.act_start_date)
                THEN 1
                ELSE 0
            END
        ) AS is_out_of_sequence,
        CASE
            WHEN r.relationship_type = 'PR_FS'
             AND succ.act_start_date IS NOT NULL
             AND (pred.act_end_date IS NULL OR succ.act_start_date < pred.act_end_date)
                THEN 'FS successor started before predecessor finished, or predecessor finish is missing'
            WHEN r.relationship_type = 'PR_SS'
             AND succ.act_start_date IS NOT NULL
             AND (pred.act_start_date IS NULL OR succ.act_start_date < pred.act_start_date)
                THEN 'SS successor started before predecessor started, or predecessor start is missing'
            WHEN r.relationship_type = 'PR_FF'
             AND succ.act_end_date IS NOT NULL
             AND (pred.act_end_date IS NULL OR succ.act_end_date < pred.act_end_date)
                THEN 'FF successor finished before predecessor finished, or predecessor finish is missing'
            WHEN r.relationship_type = 'PR_SF'
             AND succ.act_end_date IS NOT NULL
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
      ON pred.proj_id = r.proj_id
     AND pred.task_id = r.predecessor_task_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS succ
      ON succ.proj_id = r.proj_id
     AND succ.task_id = r.successor_task_id
    WHERE ISNULL(scope.exclude_complete, 0) = 0
       OR succ.is_complete = 0
);
GO

SELECT OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]')) AS corrected_function;
