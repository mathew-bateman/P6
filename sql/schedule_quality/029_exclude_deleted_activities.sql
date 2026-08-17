/*
    Add a configuration-owned switch that excludes activities carrying the
    structured P6 Activity Status code DEL from every schedule-quality family.

    The option defaults to enabled so deployment preserves the existing open-
    end treatment of deleted activities while extending it consistently to the
    remaining activity, relationship, out-of-sequence, and logical-loop checks.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51680, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_option]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]', N'P') IS NULL
    THROW 51681, 'Deploy the versioned schedule-quality configuration first.', 1;

BEGIN TRANSACTION;

INSERT INTO [powerbitables].[xertoolkit_schedule_quality_option]
(
    config_version_id, option_code, display_name, data_type,
    bit_value, numeric_value, text_value, unit_code, sort_order
)
SELECT
    version.config_version_id,
    'exclude_deleted_activities',
    N'Exclude activities marked as deleted',
    'bit',
    CAST(1 AS bit),
    NULL,
    NULL,
    NULL,
    5
FROM [powerbitables].[xertoolkit_schedule_quality_config_version] AS version
WHERE NOT EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_schedule_quality_option] AS existing
    WHERE existing.config_version_id = version.config_version_id
      AND existing.option_code = 'exclude_deleted_activities'
);

DECLARE @config_version_id bigint;
DECLARE config_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT config_version_id
    FROM [powerbitables].[xertoolkit_schedule_quality_config_version];

OPEN config_cursor;
FETCH NEXT FROM config_cursor INTO @config_version_id;
WHILE @@FETCH_STATUS = 0
BEGIN
    DECLARE @checks_json nvarchar(max) =
    (
        SELECT check_code, is_enabled, include_loe, include_wbs_summary, include_milestones, exclude_complete
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = @config_version_id
        ORDER BY sort_order, check_code
        FOR JSON PATH, INCLUDE_NULL_VALUES
    );
    DECLARE @options_json nvarchar(max) =
    (
        SELECT option_code, data_type, bit_value, numeric_value, text_value
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
        ORDER BY sort_order, option_code
        FOR JSON PATH, INCLUDE_NULL_VALUES
    );
    DECLARE @constraints_json nvarchar(max) =
    (
        SELECT constraint_type_code, is_checked
        FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
        WHERE config_version_id = @config_version_id
        ORDER BY sort_order, constraint_type_code
        FOR JSON PATH
    );

    UPDATE [powerbitables].[xertoolkit_schedule_quality_config_version]
    SET settings_hash = CONVERT
    (
        char(64),
        HASHBYTES
        (
            'SHA2_256',
            CONVERT(varbinary(max), CONCAT(@checks_json, N'|', @options_json, N'|', @constraints_json))
        ),
        2
    )
    WHERE config_version_id = @config_version_id;

    FETCH NEXT FROM config_cursor INTO @config_version_id;
END;
CLOSE config_cursor;
DEALLOCATE config_cursor;

GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_Activities]
AS
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
)
SELECT
    t.proj_id, t.task_id, t.wbs_id, t.task_code, t.task_name, t.task_type,
    t.status_code, t.complete_pct_type, t.target_drtn_hr_cnt,
    CAST(t.target_drtn_hr_cnt / 8.0 AS decimal(18,2)) AS target_duration_days,
    t.remain_drtn_hr_cnt,
    CAST(t.remain_drtn_hr_cnt / 8.0 AS decimal(18,2)) AS remaining_duration_days,
    t.phys_complete_pct, t.total_float_hr_cnt,
    CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2)) AS total_float_days,
    t.free_float_hr_cnt,
    CAST(t.free_float_hr_cnt / 8.0 AS decimal(18,2)) AS free_float_days,
    t.act_start_date, t.act_end_date, t.early_start_date, t.early_end_date,
    t.late_start_date, t.late_end_date, t.target_start_date, t.target_end_date,
    t.expect_end_date, t.cstr_type, t.cstr_date, t.cstr_type2, t.cstr_date2,
    t.driving_path_flag,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_LOE' THEN 1 ELSE 0 END) AS is_loe,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_WBS' THEN 1 ELSE 0 END) AS is_wbs_summary,
    CONVERT(bit, CASE WHEN t.task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END) AS is_milestone,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_FinMile' THEN 1 ELSE 0 END) AS is_finish_milestone,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_Mile' THEN 1 ELSE 0 END) AS is_start_milestone,
    CONVERT(bit, CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS is_complete,
    CONVERT(bit, CASE WHEN deleted.task_id IS NULL THEN 0 ELSE 1 END) AS is_deleted,
    CONVERT
    (
        bit,
        CASE
            WHEN t.task_type NOT IN ('TT_LOE', 'TT_WBS')
             AND t.status_code <> 'TK_Complete'
            THEN 1 ELSE 0
        END
    ) AS is_dcma_activity
FROM dbo.TASK AS t
LEFT JOIN deleted_activities AS deleted
  ON deleted.proj_id = t.proj_id
 AND deleted.task_id = t.task_id;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_Relationships]
AS
SELECT
    tp.proj_id, tp.task_pred_id AS relationship_id,
    tp.pred_task_id AS predecessor_task_id, tp.task_id AS successor_task_id,
    pred.task_code AS predecessor_code, pred.task_name AS predecessor_name,
    succ.task_code AS successor_code, succ.task_name AS successor_name,
    tp.pred_type AS relationship_type, tp.lag_hr_cnt AS lag_hours,
    CAST(tp.lag_hr_cnt / 8.0 AS decimal(18,2)) AS lag_days,
    pred.target_duration_days AS predecessor_duration_days,
    succ.target_duration_days AS successor_duration_days,
    pred.target_drtn_hr_cnt AS predecessor_duration_hours,
    succ.target_drtn_hr_cnt AS successor_duration_hours,
    pred.status_code AS predecessor_status_code,
    succ.status_code AS successor_status_code,
    pred.is_loe AS predecessor_is_loe, succ.is_loe AS successor_is_loe,
    pred.is_wbs_summary AS predecessor_is_wbs_summary,
    succ.is_wbs_summary AS successor_is_wbs_summary,
    pred.is_milestone AS predecessor_is_milestone,
    succ.is_milestone AS successor_is_milestone,
    pred.is_complete AS predecessor_is_complete,
    succ.is_complete AS successor_is_complete,
    pred.is_deleted AS predecessor_is_deleted,
    succ.is_deleted AS successor_is_deleted
FROM dbo.TASKPRED AS tp
LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS pred
  ON pred.proj_id = tp.proj_id AND pred.task_id = tp.pred_task_id
LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS succ
  ON succ.proj_id = tp.proj_id AND succ.task_id = tp.task_id;
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_activity_in_scope]
(
    @config_version_id bigint,
    @check_code varchar(50)
)
RETURNS TABLE
AS
RETURN
(
    SELECT a.proj_id, a.task_id
    FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
    JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS s
      ON s.config_version_id = @config_version_id
     AND s.check_code = @check_code
     AND s.is_enabled = 1
    LEFT JOIN [powerbitables].[xertoolkit_schedule_quality_option] AS deleted_option
      ON deleted_option.config_version_id = @config_version_id
     AND deleted_option.option_code = 'exclude_deleted_activities'
    WHERE (ISNULL(s.include_loe, 0) = 1 OR a.is_loe = 0)
      AND (ISNULL(s.include_wbs_summary, 0) = 1 OR a.is_wbs_summary = 0)
      AND (s.include_milestones IS NULL OR s.include_milestones = 1 OR a.is_milestone = 0)
      AND (ISNULL(s.exclude_complete, 0) = 0 OR a.is_complete = 0)
      AND (ISNULL(deleted_option.bit_value, 1) = 0 OR a.is_deleted = 0)
);
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_relationship_in_scope]
(
    @config_version_id bigint,
    @check_code varchar(50)
)
RETURNS TABLE
AS
RETURN
(
    SELECT r.proj_id, r.relationship_id
    FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
    JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS s
      ON s.config_version_id = @config_version_id
     AND s.check_code = @check_code
     AND s.is_enabled = 1
    LEFT JOIN [powerbitables].[xertoolkit_schedule_quality_option] AS deleted_option
      ON deleted_option.config_version_id = @config_version_id
     AND deleted_option.option_code = 'exclude_deleted_activities'
    WHERE r.predecessor_is_loe IS NOT NULL
      AND r.successor_is_loe IS NOT NULL
      AND (ISNULL(s.include_loe, 0) = 1 OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0))
      AND (ISNULL(s.include_wbs_summary, 0) = 1 OR (r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0))
      AND (s.include_milestones IS NULL OR s.include_milestones = 1 OR (r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0))
      AND (ISNULL(s.exclude_complete, 0) = 0 OR (r.predecessor_is_complete = 0 AND r.successor_is_complete = 0))
      AND (ISNULL(deleted_option.bit_value, 1) = 0 OR (r.predecessor_is_deleted = 0 AND r.successor_is_deleted = 0))
);
GO

/* Patch the three optimized calculation functions in place. This preserves the
   deployed performance bodies while adding one predicate to each family. */
DECLARE @definition nvarchar(max);
DECLARE @function_id int;

SET @function_id = OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]');
SET @definition = OBJECT_DEFINITION(@function_id);
IF @definition NOT LIKE N'%exclude_deleted_activities%'
BEGIN
    SET @definition = REPLACE
    (
        @definition,
        N'WITH deleted_activities AS',
        N'WITH options AS
    (
        SELECT MAX(CASE WHEN option_code = ''exclude_deleted_activities'' THEN CONVERT(int, bit_value) END) AS exclude_deleted_activities
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
    ),
    deleted_activities AS'
    );
    SET @definition = REPLACE
    (
        @definition,
        N'CROSS JOIN scope_settings AS scope',
        N'CROSS JOIN scope_settings AS scope
        CROSS JOIN options AS o
        WHERE ISNULL(o.exclude_deleted_activities, 1) = 0
           OR deleted.task_id IS NULL'
    );
    SET @definition = REPLACE
    (
        @definition,
        N'AND deleted.task_id IS NULL',
        N'AND (ISNULL(o.exclude_deleted_activities, 1) = 0 OR deleted.task_id IS NULL)'
    );
    IF @definition NOT LIKE N'%CROSS JOIN options AS o%'
        THROW 51682, 'The open-end function did not match the expected deployment shape.', 1;
    SET @definition = STUFF(@definition, 1, CHARINDEX(N'FUNCTION', UPPER(@definition)) + 7, N'ALTER FUNCTION');
    EXEC sys.sp_executesql @definition;
END;

SET @function_id = OBJECT_ID(N'[powerbitables].[xertoolkit_fn_relationship_quality]');
SET @definition = OBJECT_DEFINITION(@function_id);
IF @definition NOT LIKE N'%exclude_deleted_activities%'
BEGIN
    SET @definition = REPLACE
    (
        @definition,
        N'MAX(CASE WHEN option_code = ''excessive_ff_percent'' THEN numeric_value END) AS excessive_ff_percent',
        N'MAX(CASE WHEN option_code = ''excessive_ff_percent'' THEN numeric_value END) AS excessive_ff_percent,
            MAX(CASE WHEN option_code = ''exclude_deleted_activities'' THEN CONVERT(int, bit_value) END) AS exclude_deleted_activities'
    );
    SET @definition = REPLACE(@definition, N'CROSS JOIN scope_settings AS scope', N'CROSS JOIN scope_settings AS scope
        CROSS JOIN options AS o');
    SET @definition = REPLACE
    (
        @definition,
        N'AND r.successor_is_loe IS NOT NULL',
        N'AND r.successor_is_loe IS NOT NULL
          AND (ISNULL(o.exclude_deleted_activities, 1) = 0 OR (r.predecessor_is_deleted = 0 AND r.successor_is_deleted = 0))'
    );
    IF @definition NOT LIKE N'%r.predecessor_is_deleted = 0%'
        THROW 51683, 'The relationship function did not match the expected deployment shape.', 1;
    SET @definition = STUFF(@definition, 1, CHARINDEX(N'FUNCTION', UPPER(@definition)) + 7, N'ALTER FUNCTION');
    EXEC sys.sp_executesql @definition;
END;

SET @function_id = OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]');
SET @definition = OBJECT_DEFINITION(@function_id);
IF @definition NOT LIKE N'%exclude_deleted_activities%'
BEGIN
    SET @definition = REPLACE
    (
        @definition,
        N'MAX(CASE WHEN option_code = ''progress_percent_without_start'' THEN CONVERT(int, bit_value) END) AS progress_percent_without_start',
        N'MAX(CASE WHEN option_code = ''progress_percent_without_start'' THEN CONVERT(int, bit_value) END) AS progress_percent_without_start,
            MAX(CASE WHEN option_code = ''exclude_deleted_activities'' THEN CONVERT(int, bit_value) END) AS exclude_deleted_activities'
    );
    SET @definition = REPLACE
    (
        @definition,
        N'CROSS JOIN scope_settings AS scope',
        N'CROSS JOIN scope_settings AS scope
        CROSS JOIN options AS o
        WHERE ISNULL(o.exclude_deleted_activities, 1) = 0
           OR a.is_deleted = 0'
    );
    IF @definition NOT LIKE N'%OR a.is_deleted = 0%'
        THROW 51684, 'The activity function did not match the expected deployment shape.', 1;
    SET @definition = STUFF(@definition, 1, CHARINDEX(N'FUNCTION', UPPER(@definition)) + 7, N'ALTER FUNCTION');
    EXEC sys.sp_executesql @definition;
END;
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
            tp.proj_id, tp.task_pred_id AS relationship_id,
            tp.pred_task_id AS predecessor_task_id, tp.task_id AS successor_task_id,
            tp.pred_type AS relationship_type, tp.create_date AS relationship_create_date,
            tp.update_date AS relationship_update_date, project.last_schedule_date
        FROM dbo.TASKPRED AS tp
        JOIN dbo.PROJECT AS project
          ON project.proj_id = tp.proj_id AND project.delete_session_id IS NULL
        WHERE tp.delete_session_id IS NULL
    )
    SELECT
        @config_version_id AS config_version_id,
        r.proj_id, r.relationship_id, r.predecessor_task_id, r.successor_task_id,
        r.relationship_type, pred.status_code AS predecessor_status,
        succ.status_code AS successor_status,
        CONVERT(int, CASE WHEN r.relationship_type = 'PR_FS'
            AND succ.act_start_date IS NOT NULL AND pred.act_end_date IS NULL
            AND r.last_schedule_date IS NOT NULL
            AND (r.relationship_create_date IS NULL OR r.relationship_create_date <= r.last_schedule_date)
            AND (r.relationship_update_date IS NULL OR r.relationship_update_date <= r.last_schedule_date)
            THEN 1 ELSE 0 END) AS is_out_of_sequence,
        CASE WHEN r.relationship_type = 'PR_FS'
            AND succ.act_start_date IS NOT NULL AND pred.act_end_date IS NULL
            AND r.last_schedule_date IS NOT NULL
            AND (r.relationship_create_date IS NULL OR r.relationship_create_date <= r.last_schedule_date)
            AND (r.relationship_update_date IS NULL OR r.relationship_update_date <= r.last_schedule_date)
            THEN 'FS successor had started while predecessor remained unfinished at the last P6 schedule'
            ELSE NULL END AS out_of_sequence_reason
    FROM relationships AS r
    JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
      ON scope.config_version_id = @config_version_id
     AND scope.check_code = 'out_of_sequence' AND scope.is_enabled = 1
    JOIN dbo.TASK AS pred
      ON pred.proj_id = r.proj_id AND pred.task_id = r.predecessor_task_id
    JOIN dbo.TASK AS succ
      ON succ.proj_id = r.proj_id AND succ.task_id = r.successor_task_id
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

/* Preserve every later evidence-formatting patch by editing only the refresh
   procedure's configuration count, cycle edge, and activity-count blocks. */
DECLARE @refresh_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

IF @refresh_definition NOT LIKE N'%DECLARE @exclude_deleted_activities bit%'
BEGIN
    SET @refresh_definition = REPLACE
    (
        @refresh_definition,
        N'WHERE config_version_id = @config_version_id) <> 12',
        N'WHERE config_version_id = @config_version_id) <> 13'
    );
    SET @refresh_definition = REPLACE
    (
        @refresh_definition,
        N'DECLARE @logical_loops_enabled bit =',
        N'DECLARE @exclude_deleted_activities bit = ISNULL
        (
            (SELECT bit_value FROM [powerbitables].[xertoolkit_schedule_quality_option]
             WHERE config_version_id = @config_version_id AND option_code = ''exclude_deleted_activities''),
            1
        );

        DECLARE @logical_loops_enabled bit ='
    );
    SET @refresh_definition = REPLACE
    (
        @refresh_definition,
        N'            WHERE tpred.pred_task_id IS NOT NULL',
        N'            JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS predecessor
              ON predecessor.proj_id = tpred.proj_id AND predecessor.task_id = tpred.pred_task_id
            JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS successor
              ON successor.proj_id = tpred.proj_id AND successor.task_id = tpred.task_id
            WHERE tpred.pred_task_id IS NOT NULL'
    );
    SET @refresh_definition = REPLACE
    (
        @refresh_definition,
        N'              AND tpred.task_id IS NOT NULL;',
        N'              AND tpred.task_id IS NOT NULL
              AND (@exclude_deleted_activities = 0 OR (predecessor.is_deleted = 0 AND successor.is_deleted = 0));'
    );

    DECLARE @activity_counts int = CHARINDEX(N'INTO #ActivityCounts', @refresh_definition);
    DECLARE @activity_group int = CHARINDEX(N'        GROUP BY a.proj_id', @refresh_definition, @activity_counts);
    IF @activity_counts = 0 OR @activity_group = 0
        THROW 51685, 'The activity-count refresh block could not be located.', 1;
    SET @refresh_definition = STUFF
    (
        @refresh_definition,
        @activity_group,
        0,
        N'        WHERE @exclude_deleted_activities = 0 OR a.is_deleted = 0
'
    );

    IF @refresh_definition NOT LIKE N'%predecessor.is_deleted = 0%'
       OR @refresh_definition NOT LIKE N'%WHERE @exclude_deleted_activities = 0 OR a.is_deleted = 0%'
        THROW 51686, 'The deleted-activity refresh predicates were not installed.', 1;

    SET @refresh_definition = STUFF
    (
        @refresh_definition,
        1,
        CHARINDEX(N'PROCEDURE', UPPER(@refresh_definition)) + 8,
        N'ALTER PROCEDURE'
    );
    EXEC sys.sp_executesql @refresh_definition;
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_schedule_quality_option]
    WHERE option_code = 'exclude_deleted_activities'
)
    THROW 51687, 'The deleted-activity option was not created.', 1;

IF COL_LENGTH(N'powerbitables.xertoolkit_vw_PBI_Activities', N'is_deleted') IS NULL
   OR COL_LENGTH(N'powerbitables.xertoolkit_vw_PBI_Relationships', N'predecessor_is_deleted') IS NULL
    THROW 51688, 'The deleted-activity status columns were not deployed.', 1;

COMMIT TRANSACTION;

SELECT
    config_version_id,
    bit_value AS exclude_deleted_activities,
    display_name
FROM [powerbitables].[xertoolkit_schedule_quality_option]
WHERE option_code = 'exclude_deleted_activities'
ORDER BY config_version_id;
GO
