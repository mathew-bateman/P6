/*
    Performance-only correction for the structured deleted-activity filter in
    the out-of-sequence calculation.

    The previous function joined xertoolkit_vw_PBI_Activities twice. Because
    that view derives Activity Status = DEL through TASKACTV, ACTVTYPE, and
    ACTVCODE, SQL Server expanded the derivation once per relationship endpoint
    inside the materialisation trigger. This version reads the two required P6
    TASK rows directly and evaluates one deleted-activity semi-join.

    Calculation rules, source fields, configuration, and result contracts are
    unchanged. Safe to rerun. Roll back with
    930_out_of_sequence_deleted_filter_performance_rollback.sql.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51690, 'This hotfix must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]', N'IF') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_option]', N'U') IS NULL
    THROW 51691, 'Deploy the versioned schedule-quality and deleted-activity SQL first.', 1;

BEGIN TRANSACTION;
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_out_of_sequence]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    /* OOS_DELETED_FILTER_SEMIJOIN_V1 */
    WITH deleted_activities AS
    (
        SELECT DISTINCT assignment.proj_id, assignment.task_id
        FROM dbo.TASKACTV AS assignment
        JOIN dbo.ACTVTYPE AS code_type
          ON code_type.actv_code_type_id = assignment.actv_code_type_id
         AND code_type.delete_session_id IS NULL
         AND code_type.delete_date IS NULL
        JOIN dbo.ACTVCODE AS code
          ON code.actv_code_id = assignment.actv_code_id
         AND code.delete_session_id IS NULL
         AND code.delete_date IS NULL
        WHERE assignment.delete_session_id IS NULL
          AND assignment.delete_date IS NULL
          AND code_type.actv_code_type = 'Activity Status'
          AND code.short_name = 'DEL'
    ),
    relationships AS
    (
        SELECT
            tp.proj_id,
            tp.task_pred_id AS relationship_id,
            tp.pred_task_id AS predecessor_task_id,
            tp.task_id AS successor_task_id,
            tp.pred_type AS relationship_type,
            tp.create_date AS relationship_create_date,
            tp.update_date AS relationship_update_date,
            project.last_schedule_date
        FROM dbo.TASKPRED AS tp
        JOIN dbo.PROJECT AS project
          ON project.proj_id = tp.proj_id
         AND project.delete_session_id IS NULL
        WHERE tp.delete_session_id IS NULL
    )
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
                 AND pred.act_end_date IS NULL
                 AND r.last_schedule_date IS NOT NULL
                 AND (r.relationship_create_date IS NULL OR r.relationship_create_date <= r.last_schedule_date)
                 AND (r.relationship_update_date IS NULL OR r.relationship_update_date <= r.last_schedule_date)
                THEN 1
                ELSE 0
            END
        ) AS is_out_of_sequence,
        CASE
            WHEN r.relationship_type = 'PR_FS'
             AND succ.act_start_date IS NOT NULL
             AND pred.act_end_date IS NULL
             AND r.last_schedule_date IS NOT NULL
             AND (r.relationship_create_date IS NULL OR r.relationship_create_date <= r.last_schedule_date)
             AND (r.relationship_update_date IS NULL OR r.relationship_update_date <= r.last_schedule_date)
                THEN 'FS successor had started while predecessor remained unfinished at the last P6 schedule'
            ELSE NULL
        END AS out_of_sequence_reason
    FROM relationships AS r
    JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
      ON scope.config_version_id = @config_version_id
     AND scope.check_code = 'out_of_sequence'
     AND scope.is_enabled = 1
    JOIN dbo.TASK AS pred
      ON pred.proj_id = r.proj_id
     AND pred.task_id = r.predecessor_task_id
    JOIN dbo.TASK AS succ
      ON succ.proj_id = r.proj_id
     AND succ.task_id = r.successor_task_id
    LEFT JOIN [powerbitables].[xertoolkit_schedule_quality_option] AS deleted_option
      ON deleted_option.config_version_id = @config_version_id
     AND deleted_option.option_code = 'exclude_deleted_activities'
    WHERE (ISNULL(scope.exclude_complete, 0) = 0 OR succ.status_code <> 'TK_Complete')
      AND
      (
          ISNULL(deleted_option.bit_value, 1) = 0
          OR NOT EXISTS
          (
              SELECT 1
              FROM deleted_activities AS deleted
              WHERE deleted.proj_id = r.proj_id
                AND deleted.task_id IN
                    (r.predecessor_task_id, r.successor_task_id)
          )
      )
);
GO

DECLARE @definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'));

IF @definition NOT LIKE N'%OOS_DELETED_FILTER_SEMIJOIN_V1%'
   OR @definition NOT LIKE N'%JOIN dbo.TASK AS pred%'
   OR @definition NOT LIKE N'%JOIN dbo.TASK AS succ%'
   OR @definition NOT LIKE N'%FROM deleted_activities AS deleted%'
   OR @definition LIKE N'%xertoolkit_vw_PBI_Activities] AS pred%'
   OR @definition LIKE N'%xertoolkit_vw_PBI_Activities] AS succ%'
    THROW 51692, 'The optimized out-of-sequence definition was not installed.', 1;

COMMIT TRANSACTION;
GO
