/*
    Roll back 005_performance_hotfix.sql to the versioned function
    definitions that were live immediately before the 2026-07-15 hotfix.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51510, 'This rollback must be run against P62212_1.', 1;

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
    WITH pred_logic AS
    (
        SELECT
            r.proj_id,
            r.successor_task_id AS task_id,
            COUNT_BIG(*) AS predecessor_count,
            SUM
            (
                CASE
                    WHEN r.relationship_type IN ('PR_FS', 'PR_SS')
                     AND r.predecessor_is_loe = 0
                     AND r.predecessor_is_wbs_summary = 0
                    THEN 1 ELSE 0
                END
            ) AS start_logic_count
        FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
        GROUP BY r.proj_id, r.successor_task_id
    ),
    succ_logic AS
    (
        SELECT
            r.proj_id,
            r.predecessor_task_id AS task_id,
            COUNT_BIG(*) AS successor_count,
            SUM
            (
                CASE
                    WHEN r.relationship_type IN ('PR_FS', 'PR_FF')
                     AND r.successor_is_loe = 0
                     AND r.successor_is_wbs_summary = 0
                    THEN 1 ELSE 0
                END
            ) AS finish_logic_count
        FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
        GROUP BY r.proj_id, r.predecessor_task_id
    )
    SELECT
        @config_version_id AS config_version_id,
        a.proj_id,
        a.task_id,
        CONVERT
        (
            int,
            CASE WHEN mp.task_id IS NOT NULL AND ISNULL(p.predecessor_count, 0) = 0 THEN 1 ELSE 0 END
        ) AS is_missing_predecessor,
        CONVERT
        (
            int,
            CASE
                WHEN ms.task_id IS NOT NULL
                 AND ISNULL(s.successor_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_missing_successor,
        CONVERT
        (
            int,
            CASE
                WHEN os.task_id IS NOT NULL
                 AND a.is_milestone = 0
                 AND ISNULL(p.start_logic_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_open_start,
        CONVERT
        (
            int,
            CASE
                WHEN ofi.task_id IS NOT NULL
                 AND a.is_milestone = 0
                 AND ISNULL(s.finish_logic_count, 0) = 0
                THEN 1 ELSE 0
            END
        ) AS is_open_finish
    FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
    LEFT JOIN pred_logic AS p
      ON p.proj_id = a.proj_id
     AND p.task_id = a.task_id
    LEFT JOIN succ_logic AS s
      ON s.proj_id = a.proj_id
     AND s.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'missing_predecessor') AS mp
      ON mp.proj_id = a.proj_id
     AND mp.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'missing_successor') AS ms
      ON ms.proj_id = a.proj_id
     AND ms.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'open_start') AS os
      ON os.proj_id = a.proj_id
     AND os.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'open_finish') AS ofi
      ON ofi.proj_id = a.proj_id
     AND ofi.task_id = a.task_id
    WHERE mp.task_id IS NOT NULL
       OR ms.task_id IS NOT NULL
       OR os.task_id IS NOT NULL
       OR ofi.task_id IS NOT NULL
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
            MAX(CASE WHEN option_code = 'excessive_ff_percent' THEN numeric_value END) AS excessive_ff_percent
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
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
        CONVERT(int, CASE WHEN leads.relationship_id IS NOT NULL AND r.lag_hours < 0 THEN 1 ELSE 0 END) AS is_lead,
        CONVERT(int, CASE WHEN lags.relationship_id IS NOT NULL AND r.lag_hours > 0 THEN 1 ELSE 0 END) AS is_lag,
        CONVERT(int, CASE WHEN ratio.relationship_id IS NOT NULL AND r.relationship_type <> 'PR_FS' THEN 1 ELSE 0 END) AS is_non_fs,
        CONVERT
        (
            int,
            CASE
                WHEN ss.relationship_id IS NOT NULL
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
                WHEN ff.relationship_id IS NOT NULL
                 AND r.relationship_type = 'PR_FF'
                 AND r.successor_duration_hours IS NOT NULL
                 AND r.lag_hours > r.successor_duration_hours * o.excessive_ff_percent / 100.0
                THEN 1 ELSE 0
            END
        ) AS is_excessive_ff_lag
    FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
    CROSS JOIN options AS o
    LEFT JOIN [powerbitables].[xertoolkit_fn_relationship_in_scope](@config_version_id, 'relationship_leads') AS leads
      ON leads.proj_id = r.proj_id
     AND leads.relationship_id = r.relationship_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_relationship_in_scope](@config_version_id, 'relationship_lags') AS lags
      ON lags.proj_id = r.proj_id
     AND lags.relationship_id = r.relationship_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_relationship_in_scope](@config_version_id, 'relationship_ratio') AS ratio
      ON ratio.proj_id = r.proj_id
     AND ratio.relationship_id = r.relationship_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_relationship_in_scope](@config_version_id, 'excessive_ss_lag') AS ss
      ON ss.proj_id = r.proj_id
     AND ss.relationship_id = r.relationship_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_relationship_in_scope](@config_version_id, 'excessive_ff_lag') AS ff
      ON ff.proj_id = r.proj_id
     AND ff.relationship_id = r.relationship_id
    WHERE leads.relationship_id IS NOT NULL
       OR lags.relationship_id IS NOT NULL
       OR ratio.relationship_id IS NOT NULL
       OR ss.relationship_id IS NOT NULL
       OR ff.relationship_id IS NOT NULL
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
            MAX(CASE WHEN option_code = 'progress_percent_without_start' THEN CONVERT(int, bit_value) END) AS progress_percent_without_start
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @config_version_id
    )
    SELECT
        @config_version_id AS config_version_id,
        a.proj_id,
        a.task_id,
        a.task_code,
        a.task_name,
        w.wbs_name,
        CONVERT(int, CASE WHEN hf.task_id IS NOT NULL AND a.total_float_hr_cnt > o.high_float_days * 8.0 THEN 1 ELSE 0 END) AS is_high_float,
        CONVERT(int, CASE WHEN nf.task_id IS NOT NULL AND a.total_float_hr_cnt < o.negative_float_days * 8.0 THEN 1 ELSE 0 END) AS is_negative_float,
        CONVERT(int, CASE WHEN hd.task_id IS NOT NULL AND a.target_drtn_hr_cnt > o.high_duration_days * 8.0 THEN 1 ELSE 0 END) AS is_high_duration,
        CONVERT
        (
            int,
            CASE
                WHEN cs.task_id IS NOT NULL
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
                WHEN inv.task_id IS NOT NULL
                 AND p.data_date IS NOT NULL
                 AND
                 (
                     (
                         o.invalid_early_before_progress = 1
                         AND a.is_complete = 0
                         AND
                         (
                             CONVERT(date, a.early_start_date) < CONVERT(date, p.data_date)
                             OR CONVERT(date, a.early_end_date) < CONVERT(date, p.data_date)
                         )
                     )
                     OR
                     (
                         o.invalid_actual_after_progress = 1
                         AND
                         (
                             CONVERT(date, a.act_start_date) > CONVERT(date, p.data_date)
                             OR CONVERT(date, a.act_end_date) > CONVERT(date, p.data_date)
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
                WHEN ip.task_id IS NOT NULL
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
                WHEN ride.task_id IS NOT NULL
                 AND p.data_date IS NOT NULL
                 AND a.status_code = 'TK_NotStart'
                 AND CONVERT(date, a.early_start_date) >= CONVERT(date, p.data_date)
                 AND CONVERT(date, a.early_start_date)
                     <= DATEADD(day, CONVERT(int, o.riding_days_after_data_date), CONVERT(date, p.data_date))
                THEN 1 ELSE 0
            END
        ) AS is_riding_progress_date,
        CONVERT(int, CASE WHEN crit.task_id IS NOT NULL AND a.total_float_hr_cnt <= 0 THEN 1 ELSE 0 END) AS is_critical_task,
        CONVERT
        (
            int,
            CASE
                WHEN nearcrit.task_id IS NOT NULL
                 AND a.total_float_hr_cnt > 0
                 AND a.total_float_hr_cnt <= o.near_critical_upper_days * 8.0
                THEN 1 ELSE 0
            END
        ) AS is_near_critical_task
    FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
    JOIN [powerbitables].[xertoolkit_vw_PBI_Projects] AS p
      ON p.proj_id = a.proj_id
    CROSS JOIN options AS o
    LEFT JOIN dbo.PROJWBS AS w
      ON w.proj_id = a.proj_id
     AND w.wbs_id = a.wbs_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'high_float') AS hf
      ON hf.proj_id = a.proj_id AND hf.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'negative_float') AS nf
      ON nf.proj_id = a.proj_id AND nf.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'high_duration') AS hd
      ON hd.proj_id = a.proj_id AND hd.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'constraints') AS cs
      ON cs.proj_id = a.proj_id AND cs.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'invalid_dates') AS inv
      ON inv.proj_id = a.proj_id AND inv.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'in_progress_errors') AS ip
      ON ip.proj_id = a.proj_id AND ip.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'riding_progress_date') AS ride
      ON ride.proj_id = a.proj_id AND ride.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'critical_tasks') AS crit
      ON crit.proj_id = a.proj_id AND crit.task_id = a.task_id
    LEFT JOIN [powerbitables].[xertoolkit_fn_activity_in_scope](@config_version_id, 'near_critical_tasks') AS nearcrit
      ON nearcrit.proj_id = a.proj_id AND nearcrit.task_id = a.task_id
    WHERE hf.task_id IS NOT NULL
       OR nf.task_id IS NOT NULL
       OR hd.task_id IS NOT NULL
       OR cs.task_id IS NOT NULL
       OR inv.task_id IS NOT NULL
       OR ip.task_id IS NOT NULL
       OR ride.task_id IS NOT NULL
       OR crit.task_id IS NOT NULL
       OR nearcrit.task_id IS NOT NULL
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
      AND OBJECT_DEFINITION(object_id) NOT LIKE N'%scope_settings AS%'
      AND OBJECT_DEFINITION(object_id) NOT LIKE N'%scope_mask%'
) <> 3
    THROW 51511, 'One or more pre-hotfix function definitions were not restored.', 1;

COMMIT TRANSACTION;

SELECT
    name AS restored_function,
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
