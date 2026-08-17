/*
    Deployment-safe schedule-quality performance hotfix.

    Replaces only the three calculation functions whose nested inline-TVF
    expansion caused repeated TASK/TASKPRED scans. Function signatures and
    result columns are unchanged; configuration and materialised rows are not
    modified by this script.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51500, 'This hotfix must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_option]', N'U') IS NULL
    THROW 51501, 'Versioned schedule-quality configuration is not deployed.', 1;

BEGIN TRANSACTION;
GO
CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_open_ends]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH options AS
    (
        SELECT MAX(CASE WHEN option_code = 'exclude_deleted_activities' THEN CONVERT(int, bit_value) END) AS exclude_deleted_activities
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
    ),
    deleted_activities AS
    (
        SELECT DISTINCT
            assignment.proj_id,
            assignment.task_id
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
    pred_logic AS
    (
        SELECT
            r.proj_id,
            r.task_id,
            COUNT_BIG(pred_source.task_id) AS predecessor_count,
            SUM
            (
                CONVERT
                (
                    bigint,
                    CASE
                        WHEN r.pred_type = 'PR_FS'
                         AND pred.task_id IS NOT NULL
                         AND pred_source.task_id IS NOT NULL
                         AND pred.is_loe = 0
                         AND pred.is_wbs_summary = 0
                        THEN 1
                        WHEN r.pred_type = 'PR_SS'
                         AND pred.task_id IS NOT NULL
                         AND pred_source.task_id IS NOT NULL
                         AND pred.is_loe = 0
                         AND pred.is_wbs_summary = 0
                         AND paired_relationship.task_id IS NOT NULL
                        THEN 1 ELSE 0
                    END
                )
            ) AS start_logic_count
        FROM dbo.TASKPRED AS r
        LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS pred
          ON pred.proj_id = r.proj_id
         AND pred.task_id = r.pred_task_id
        LEFT JOIN dbo.TASK AS pred_source
          ON pred_source.proj_id = r.proj_id
         AND pred_source.task_id = r.pred_task_id
         AND pred_source.delete_session_id IS NULL
        LEFT JOIN
        (
            SELECT DISTINCT proj_id, pred_task_id, task_id
            FROM dbo.TASKPRED
            WHERE pred_type = 'PR_FF'
              AND delete_session_id IS NULL
        ) AS paired_relationship
          ON paired_relationship.proj_id = r.proj_id
         AND paired_relationship.pred_task_id = r.pred_task_id
         AND paired_relationship.task_id = r.task_id
        WHERE r.delete_session_id IS NULL
        GROUP BY r.proj_id, r.task_id
    ),
    succ_logic AS
    (
        SELECT
            r.proj_id,
            r.pred_task_id AS task_id,
            COUNT_BIG(succ_source.task_id) AS successor_count,
            SUM
            (
                CONVERT
                (
                    bigint,
                    CASE
                        WHEN r.pred_type = 'PR_FS'
                         AND succ.task_id IS NOT NULL
                         AND succ_source.task_id IS NOT NULL
                         AND succ.is_loe = 0
                         AND succ.is_wbs_summary = 0
                        THEN 1
                        WHEN r.pred_type = 'PR_FF'
                         AND succ.task_id IS NOT NULL
                         AND succ_source.task_id IS NOT NULL
                         AND succ.is_loe = 0
                         AND succ.is_wbs_summary = 0
                         AND paired_relationship.task_id IS NOT NULL
                        THEN 1 ELSE 0
                    END
                )
            ) AS finish_logic_count
        FROM dbo.TASKPRED AS r
        LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS succ
          ON succ.proj_id = r.proj_id
         AND succ.task_id = r.task_id
        LEFT JOIN dbo.TASK AS succ_source
          ON succ_source.proj_id = r.proj_id
         AND succ_source.task_id = r.task_id
         AND succ_source.delete_session_id IS NULL
        LEFT JOIN
        (
            SELECT DISTINCT proj_id, pred_task_id, task_id
            FROM dbo.TASKPRED
            WHERE pred_type = 'PR_SS'
              AND delete_session_id IS NULL
        ) AS paired_relationship
          ON paired_relationship.proj_id = r.proj_id
         AND paired_relationship.pred_task_id = r.pred_task_id
         AND paired_relationship.task_id = r.task_id
        WHERE r.delete_session_id IS NULL
        GROUP BY r.proj_id, r.pred_task_id
    ),
    scope_rows AS
    (
        SELECT
            check_code,
            CASE WHEN is_enabled = 1 THEN 1 ELSE 0 END
              + CASE WHEN ISNULL(include_loe, 0) = 1 THEN 2 ELSE 0 END
              + CASE WHEN ISNULL(include_wbs_summary, 0) = 1 THEN 4 ELSE 0 END
              + CASE WHEN include_milestones IS NULL OR include_milestones = 1 THEN 8 ELSE 0 END
              + CASE WHEN ISNULL(exclude_complete, 0) = 1 THEN 16 ELSE 0 END AS scope_mask
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = @config_version_id
    ),
    scope_settings AS
    (
        SELECT
            MAX(CASE WHEN check_code = 'missing_predecessor' THEN scope_mask END) AS missing_predecessor_scope,
            MAX(CASE WHEN check_code = 'missing_successor' THEN scope_mask END) AS missing_successor_scope,
            MAX(CASE WHEN check_code = 'open_start' THEN scope_mask END) AS open_start_scope,
            MAX(CASE WHEN check_code = 'open_finish' THEN scope_mask END) AS open_finish_scope
        FROM scope_rows
    ),
    scoped_activities AS
    (
        SELECT
            a.proj_id,
            a.task_id,
            a.is_milestone,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.missing_predecessor_scope & 1) = 1
                     AND ((scope.missing_predecessor_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.missing_predecessor_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.missing_predecessor_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.missing_predecessor_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_missing_predecessor_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.missing_successor_scope & 1) = 1
                     AND ((scope.missing_successor_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.missing_successor_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.missing_successor_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.missing_successor_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_missing_successor_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.open_start_scope & 1) = 1
                     AND ((scope.open_start_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.open_start_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.open_start_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.open_start_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_open_start_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.open_finish_scope & 1) = 1
                     AND ((scope.open_finish_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.open_finish_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.open_finish_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.open_finish_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_open_finish_scope
        FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
        JOIN dbo.TASK AS source_activity
          ON source_activity.proj_id = a.proj_id
         AND source_activity.task_id = a.task_id
         AND source_activity.delete_session_id IS NULL
        LEFT JOIN deleted_activities AS deleted
          ON deleted.proj_id = a.proj_id
         AND deleted.task_id = a.task_id
        CROSS JOIN scope_settings AS scope
        CROSS JOIN options AS o
        WHERE ISNULL(o.exclude_deleted_activities, 1) = 0
           OR deleted.task_id IS NULL
    )
    SELECT
        @config_version_id AS config_version_id,
        a.proj_id,
        a.task_id,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_missing_predecessor_scope = 1
                 AND ISNULL(pred_logic.predecessor_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_missing_predecessor,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_missing_successor_scope = 1
                 AND ISNULL(succ_logic.successor_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_missing_successor,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_open_start_scope = 1
                 AND a.is_milestone = 0
                 AND ISNULL(pred_logic.start_logic_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_open_start,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_open_finish_scope = 1
                 AND a.is_milestone = 0
                 AND ISNULL(succ_logic.finish_logic_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_open_finish
    FROM scoped_activities AS a
    LEFT JOIN pred_logic
      ON pred_logic.proj_id = a.proj_id
     AND pred_logic.task_id = a.task_id
    LEFT JOIN succ_logic
      ON succ_logic.proj_id = a.proj_id
     AND succ_logic.task_id = a.task_id
    WHERE a.in_missing_predecessor_scope = 1
       OR a.in_missing_successor_scope = 1
       OR a.in_open_start_scope = 1
       OR a.in_open_finish_scope = 1
);
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_relationship_quality]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH options AS
    (
        SELECT
            MAX(CASE WHEN option_code = 'excessive_ss_percent' THEN numeric_value END) AS excessive_ss_percent,
            MAX(CASE WHEN option_code = 'excessive_ff_percent' THEN numeric_value END) AS excessive_ff_percent,
            MAX(CASE WHEN option_code = 'exclude_deleted_activities' THEN CONVERT(int, bit_value) END) AS exclude_deleted_activities
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
    ),
    scope_rows AS
    (
        SELECT
            check_code,
            CASE WHEN is_enabled = 1 THEN 1 ELSE 0 END
              + CASE WHEN ISNULL(include_loe, 0) = 1 THEN 2 ELSE 0 END
              + CASE WHEN ISNULL(include_wbs_summary, 0) = 1 THEN 4 ELSE 0 END
              + CASE WHEN include_milestones IS NULL OR include_milestones = 1 THEN 8 ELSE 0 END
              + CASE WHEN ISNULL(exclude_complete, 0) = 1 THEN 16 ELSE 0 END AS scope_mask
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = @config_version_id
    ),
    scope_settings AS
    (
        SELECT
            MAX(CASE WHEN check_code = 'relationship_leads' THEN scope_mask END) AS leads_scope,
            MAX(CASE WHEN check_code = 'relationship_lags' THEN scope_mask END) AS lags_scope,
            MAX(CASE WHEN check_code = 'relationship_ratio' THEN scope_mask END) AS ratio_scope,
            MAX(CASE WHEN check_code = 'excessive_ss_lag' THEN scope_mask END) AS excessive_ss_scope,
            MAX(CASE WHEN check_code = 'excessive_ff_lag' THEN scope_mask END) AS excessive_ff_scope
        FROM scope_rows
    ),
    scoped_relationships AS
    (
        SELECT
            r.*,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.leads_scope & 1) = 1
                     AND ((scope.leads_scope & 2) = 2 OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0))
                     AND ((scope.leads_scope & 4) = 4 OR (r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0))
                     AND ((scope.leads_scope & 8) = 8 OR (r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0))
                     AND ((scope.leads_scope & 16) = 0 OR (r.predecessor_is_complete = 0 AND r.successor_is_complete = 0))
                    THEN 1 ELSE 0
                END
            ) AS in_leads_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.lags_scope & 1) = 1
                     AND ((scope.lags_scope & 2) = 2 OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0))
                     AND ((scope.lags_scope & 4) = 4 OR (r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0))
                     AND ((scope.lags_scope & 8) = 8 OR (r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0))
                     AND ((scope.lags_scope & 16) = 0 OR (r.predecessor_is_complete = 0 AND r.successor_is_complete = 0))
                    THEN 1 ELSE 0
                END
            ) AS in_lags_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.ratio_scope & 1) = 1
                     AND ((scope.ratio_scope & 2) = 2 OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0))
                     AND ((scope.ratio_scope & 4) = 4 OR (r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0))
                     AND ((scope.ratio_scope & 8) = 8 OR (r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0))
                     AND ((scope.ratio_scope & 16) = 0 OR (r.predecessor_is_complete = 0 AND r.successor_is_complete = 0))
                    THEN 1 ELSE 0
                END
            ) AS in_ratio_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.excessive_ss_scope & 1) = 1
                     AND ((scope.excessive_ss_scope & 2) = 2 OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0))
                     AND ((scope.excessive_ss_scope & 4) = 4 OR (r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0))
                     AND ((scope.excessive_ss_scope & 8) = 8 OR (r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0))
                     AND ((scope.excessive_ss_scope & 16) = 0 OR (r.predecessor_is_complete = 0 AND r.successor_is_complete = 0))
                    THEN 1 ELSE 0
                END
            ) AS in_excessive_ss_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.excessive_ff_scope & 1) = 1
                     AND ((scope.excessive_ff_scope & 2) = 2 OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0))
                     AND ((scope.excessive_ff_scope & 4) = 4 OR (r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0))
                     AND ((scope.excessive_ff_scope & 8) = 8 OR (r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0))
                     AND ((scope.excessive_ff_scope & 16) = 0 OR (r.predecessor_is_complete = 0 AND r.successor_is_complete = 0))
                    THEN 1 ELSE 0
                END
            ) AS in_excessive_ff_scope
        FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
        CROSS JOIN scope_settings AS scope
        CROSS JOIN options AS o
        WHERE r.predecessor_is_loe IS NOT NULL
          AND r.successor_is_loe IS NOT NULL
          AND
          (
              ISNULL(o.exclude_deleted_activities, 1) = 0
              OR
              (
                  r.predecessor_is_deleted = 0
                  AND r.successor_is_deleted = 0
              )
          )
    )
    SELECT
        @config_version_id AS config_version_id,
        r.proj_id,
        r.relationship_id,
        r.predecessor_task_id,
        r.successor_task_id,
        r.predecessor_code,
        r.successor_code,
        r.relationship_type,
        r.lag_hours,
        r.lag_days,
        CONVERT(int, CASE WHEN r.in_leads_scope = 1 AND r.lag_hours < 0 THEN 1 ELSE 0 END) AS is_lead,
        CONVERT(int, CASE WHEN r.in_lags_scope = 1 AND r.lag_hours > 0 THEN 1 ELSE 0 END) AS is_lag,
        CONVERT(int, CASE WHEN r.in_ratio_scope = 1 AND r.relationship_type <> 'PR_FS' THEN 1 ELSE 0 END) AS is_non_fs,
        CONVERT
        (
            int,
            CASE
                WHEN r.in_excessive_ss_scope = 1
                 AND r.relationship_type = 'PR_SS'
                 AND r.predecessor_duration_hours IS NOT NULL
                 AND r.lag_hours > r.predecessor_duration_hours * o.excessive_ss_percent / 100.0
                THEN 1 ELSE 0
            END
        ) AS is_excessive_ss_lag,
        CONVERT
        (
            int,
            CASE
                WHEN r.in_excessive_ff_scope = 1
                 AND r.relationship_type = 'PR_FF'
                 AND r.successor_duration_hours IS NOT NULL
                 AND r.lag_hours > r.successor_duration_hours * o.excessive_ff_percent / 100.0
                THEN 1 ELSE 0
            END
        ) AS is_excessive_ff_lag
    FROM scoped_relationships AS r
    CROSS JOIN options AS o
    WHERE r.in_leads_scope = 1
       OR r.in_lags_scope = 1
       OR r.in_ratio_scope = 1
       OR r.in_excessive_ss_scope = 1
       OR r.in_excessive_ff_scope = 1
);
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_activity_quality]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH options AS
    (
        SELECT
            MAX(CASE WHEN option_code = 'high_float_days' THEN numeric_value END) AS high_float_days,
            MAX(CASE WHEN option_code = 'negative_float_days' THEN numeric_value END) AS negative_float_days,
            MAX(CASE WHEN option_code = 'high_duration_days' THEN numeric_value END) AS high_duration_days,
            MAX(CASE WHEN option_code = 'near_critical_upper_days' THEN numeric_value END) AS near_critical_upper_days,
            MAX(CASE WHEN option_code = 'riding_days_after_data_date' THEN numeric_value END) AS riding_days_after_data_date,
            MAX(CASE WHEN option_code = 'invalid_early_before_progress' THEN CONVERT(int, bit_value) END) AS invalid_early_before_progress,
            MAX(CASE WHEN option_code = 'invalid_actual_after_progress' THEN CONVERT(int, bit_value) END) AS invalid_actual_after_progress,
            MAX(CASE WHEN option_code = 'progress_started_zero_percent' THEN CONVERT(int, bit_value) END) AS progress_started_zero_percent,
            MAX(CASE WHEN option_code = 'progress_finished_below_100' THEN CONVERT(int, bit_value) END) AS progress_finished_below_100,
            MAX(CASE WHEN option_code = 'progress_percent_without_start' THEN CONVERT(int, bit_value) END) AS progress_percent_without_start,
            MAX(CASE WHEN option_code = 'exclude_deleted_activities' THEN CONVERT(int, bit_value) END) AS exclude_deleted_activities
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
    ),
    scope_rows AS
    (
        SELECT
            check_code,
            CASE WHEN is_enabled = 1 THEN 1 ELSE 0 END
              + CASE WHEN ISNULL(include_loe, 0) = 1 THEN 2 ELSE 0 END
              + CASE WHEN ISNULL(include_wbs_summary, 0) = 1 THEN 4 ELSE 0 END
              + CASE WHEN include_milestones IS NULL OR include_milestones = 1 THEN 8 ELSE 0 END
              + CASE WHEN ISNULL(exclude_complete, 0) = 1 THEN 16 ELSE 0 END AS scope_mask
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = @config_version_id
    ),
    scope_settings AS
    (
        SELECT
            MAX(CASE WHEN check_code = 'high_float' THEN scope_mask END) AS high_float_scope,
            MAX(CASE WHEN check_code = 'negative_float' THEN scope_mask END) AS negative_float_scope,
            MAX(CASE WHEN check_code = 'high_duration' THEN scope_mask END) AS high_duration_scope,
            MAX(CASE WHEN check_code = 'constraints' THEN scope_mask END) AS constraints_scope,
            MAX(CASE WHEN check_code = 'invalid_dates' THEN scope_mask END) AS invalid_dates_scope,
            MAX(CASE WHEN check_code = 'in_progress_errors' THEN scope_mask END) AS in_progress_errors_scope,
            MAX(CASE WHEN check_code = 'riding_progress_date' THEN scope_mask END) AS riding_progress_date_scope,
            MAX(CASE WHEN check_code = 'critical_tasks' THEN scope_mask END) AS critical_tasks_scope,
            MAX(CASE WHEN check_code = 'near_critical_tasks' THEN scope_mask END) AS near_critical_tasks_scope
        FROM scope_rows
    ),
    scoped_activities AS
    (
        SELECT
            a.*,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.high_float_scope & 1) = 1
                     AND ((scope.high_float_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.high_float_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.high_float_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.high_float_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_high_float_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.negative_float_scope & 1) = 1
                     AND ((scope.negative_float_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.negative_float_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.negative_float_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.negative_float_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_negative_float_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.high_duration_scope & 1) = 1
                     AND ((scope.high_duration_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.high_duration_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.high_duration_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.high_duration_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_high_duration_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.constraints_scope & 1) = 1
                     AND ((scope.constraints_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.constraints_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.constraints_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.constraints_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_constraints_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.invalid_dates_scope & 1) = 1
                     AND ((scope.invalid_dates_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.invalid_dates_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.invalid_dates_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.invalid_dates_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_invalid_dates_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.in_progress_errors_scope & 1) = 1
                     AND ((scope.in_progress_errors_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.in_progress_errors_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.in_progress_errors_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.in_progress_errors_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_progress_errors_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.riding_progress_date_scope & 1) = 1
                     AND ((scope.riding_progress_date_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.riding_progress_date_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.riding_progress_date_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.riding_progress_date_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_riding_progress_date_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.critical_tasks_scope & 1) = 1
                     AND ((scope.critical_tasks_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.critical_tasks_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.critical_tasks_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.critical_tasks_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_critical_tasks_scope,
            CONVERT
            (
                bit,
                CASE
                    WHEN (scope.near_critical_tasks_scope & 1) = 1
                     AND ((scope.near_critical_tasks_scope & 2) = 2 OR a.is_loe = 0)
                     AND ((scope.near_critical_tasks_scope & 4) = 4 OR a.is_wbs_summary = 0)
                     AND ((scope.near_critical_tasks_scope & 8) = 8 OR a.is_milestone = 0)
                     AND ((scope.near_critical_tasks_scope & 16) = 0 OR a.is_complete = 0)
                    THEN 1 ELSE 0
                END
            ) AS in_near_critical_tasks_scope
        FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
        CROSS JOIN scope_settings AS scope
        CROSS JOIN options AS o
        WHERE ISNULL(o.exclude_deleted_activities, 1) = 0
           OR a.is_deleted = 0
    )
    SELECT
        @config_version_id AS config_version_id,
        a.proj_id,
        a.task_id,
        a.task_code,
        a.task_name,
        w.wbs_name,
        CONVERT(int, CASE WHEN a.in_high_float_scope = 1 AND a.total_float_hr_cnt >= o.high_float_days * 8.0 THEN 1 ELSE 0 END) AS is_high_float,
        CONVERT(int, CASE WHEN a.in_negative_float_scope = 1 AND a.total_float_hr_cnt < o.negative_float_days * 8.0 THEN 1 ELSE 0 END) AS is_negative_float,
        CONVERT(int, CASE WHEN a.in_high_duration_scope = 1 AND a.remain_drtn_hr_cnt > o.high_duration_days * 8.0 THEN 1 ELSE 0 END) AS is_high_duration,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_constraints_scope = 1
                 AND EXISTS
                 (
                    SELECT 1
                    FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type] AS ct
                    WHERE ct.config_version_id = @config_version_id
                      AND ct.is_checked = 1
                      AND
                      (
                          ct.constraint_type_code = a.cstr_type
                          OR ct.constraint_type_code = a.cstr_type2
                          OR (ct.constraint_type_code = 'CS_EXPECTED' AND a.expect_end_date IS NOT NULL)
                      )
                 )
                THEN 1 ELSE 0
            END
        ) AS is_constraint,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_invalid_dates_scope = 1
                 AND p.last_recalc_date IS NOT NULL
                 AND
                 (
                     (
                         o.invalid_early_before_progress = 1
                         AND a.is_complete = 0
                         AND
                         (
                             CONVERT(date, a.early_start_date) < CONVERT(date, p.last_recalc_date)
                             OR CONVERT(date, a.early_end_date) < CONVERT(date, p.last_recalc_date)
                         )
                     )
                     OR
                     (
                         o.invalid_actual_after_progress = 1
                         AND
                         (
                             CONVERT(date, a.act_start_date) > CONVERT(date, p.last_recalc_date)
                             OR CONVERT(date, a.act_end_date) > CONVERT(date, p.last_recalc_date)
                         )
                     )
                 )
                THEN 1 ELSE 0
            END
        ) AS is_invalid_date,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_progress_errors_scope = 1
                 AND
                 (
                     (o.progress_started_zero_percent = 1 AND a.act_start_date IS NOT NULL AND a.phys_complete_pct = 0)
                     OR (o.progress_finished_below_100 = 1 AND a.act_end_date IS NOT NULL AND a.phys_complete_pct < 100)
                     OR (o.progress_percent_without_start = 1 AND a.act_start_date IS NULL AND a.phys_complete_pct > 0)
                 )
                THEN 1 ELSE 0
            END
        ) AS is_in_progress_error,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_riding_progress_date_scope = 1
                 AND p.last_recalc_date IS NOT NULL
                 AND a.status_code = 'TK_NotStart'
                 AND CONVERT(date, a.early_start_date) >= CONVERT(date, p.last_recalc_date)
                 AND CONVERT(date, a.early_start_date)
                      <= DATEADD(day, CONVERT(int, o.riding_days_after_data_date), CONVERT(date, p.last_recalc_date))
                THEN 1 ELSE 0
            END
        ) AS is_riding_progress_date,
        CONVERT(int, CASE WHEN a.in_critical_tasks_scope = 1 AND a.total_float_hr_cnt < 4.0 THEN 1 ELSE 0 END) AS is_critical_task,
        CONVERT
        (
            int,
            CASE
                WHEN a.in_near_critical_tasks_scope = 1
                 AND a.total_float_hr_cnt >= 4.0
                 AND a.total_float_hr_cnt <= o.near_critical_upper_days * 8.0
                THEN 1 ELSE 0
            END
        ) AS is_near_critical_task
    FROM scoped_activities AS a
    JOIN dbo.PROJECT AS p
      ON p.proj_id = a.proj_id
    CROSS JOIN options AS o
    LEFT JOIN dbo.PROJWBS AS w
      ON w.proj_id = a.proj_id
     AND w.wbs_id = a.wbs_id
    WHERE a.in_high_float_scope = 1
       OR a.in_negative_float_scope = 1
       OR a.in_high_duration_scope = 1
       OR a.in_constraints_scope = 1
       OR a.in_invalid_dates_scope = 1
       OR a.in_progress_errors_scope = 1
       OR a.in_riding_progress_date_scope = 1
       OR a.in_critical_tasks_scope = 1
       OR a.in_near_critical_tasks_scope = 1
);
GO

IF
(
    SELECT COUNT(*)
    FROM sys.objects
    WHERE object_id IN
    (
        OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'),
        OBJECT_ID(N'[powerbitables].[xertoolkit_fn_relationship_quality]'),
        OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]')
    )
      AND type = 'IF'
      AND OBJECT_DEFINITION(object_id) LIKE N'%scope_settings AS%'
      AND OBJECT_DEFINITION(object_id) LIKE N'%scope_mask%'
) <> 3
    THROW 51502, 'One or more optimized schedule-quality functions are missing or incomplete.', 1;

COMMIT TRANSACTION;

SELECT
    name AS optimized_function,
    modify_date
FROM sys.objects
WHERE object_id IN
(
    OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'),
    OBJECT_ID(N'[powerbitables].[xertoolkit_fn_relationship_quality]'),
    OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]')
)
ORDER BY name;
GO
