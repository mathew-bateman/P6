/*
    Workbook-parity scorecard contract.

    The published schedule-quality configuration remains the authority for
    scope, exclusions and scoring.  This migration adds the score policy to
    that versioned configuration, corrects the two calculation rules that
    diverged from the supplied integrity workbook, and exposes one normalised
    row per project/check for all downstream reports.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51700, 'This migration must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
    THROW 51701, 'The versioned schedule-quality deployment is required.', 1;
GO

IF COL_LENGTH(N'powerbitables.xertoolkit_schedule_quality_check_scope', N'limit_type') IS NULL
    ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_check_scope]
        ADD limit_type varchar(10) NULL,
            green_limit decimal(9,2) NULL,
            amber_limit decimal(9,2) NULL,
            green_points int NULL,
            amber_points int NULL,
            records_metric varchar(50) NULL,
            qualifying_metric varchar(50) NULL;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    ;WITH workbook_rules AS
    (
        SELECT *
        FROM
        (
            VALUES
            ('missing_predecessor', 'Percent', CONVERT(decimal(9,2), 3), CONVERT(decimal(9,2), 7), 40, 32, 'dcma_activity', 'missing_predecessor'),
            ('missing_successor',   'Percent', 3, 7, 40, 32, 'dcma_activity', 'missing_successor'),
            ('open_finish',         'Percent', 3, 7, 20, 16, 'open_finish_eligible', 'open_finish'),
            ('open_start',          'Percent', 3, 7, 10,  8, 'open_start_eligible', 'open_start'),
            ('relationship_leads',  'Percent', 3, 7, 15, 12, 'relationship', 'relationship_leads'),
            ('relationship_lags',   'Percent', 3, 7, 10,  8, 'relationship', 'relationship_lags'),
            ('relationship_ratio',  'Percent', 3, 7, 10,  8, 'relationship', 'non_fs'),
            ('constraints',         'Percent', 3, 7, 15, 12, 'dcma_activity', 'constraints'),
            ('high_float',          'Percent', 3, 7, 10,  8, 'dcma_activity', 'high_float'),
            ('negative_float',      'Percent', 3, 7, 20, 16, 'dcma_activity', 'negative_float'),
            ('high_duration',       'Percent', 3, 7, 10,  8, 'dcma_activity', 'high_duration'),
            ('invalid_dates',       'Percent', 3, 7, 15, 12, 'dcma_activity', 'invalid_dates'),
            ('in_progress_errors',  'Percent', 3, 7, 15, 12, 'dcma_activity', 'in_progress_errors'),
            ('logical_loops',       'Number',  0, 1, 50, 40, 'none', 'logical_loops'),
            ('out_of_sequence',     'Number',  0, 0, 10,  8, 'none', 'out_of_sequence'),
            ('critical_tasks',      'Percent', 30, 50, 15, 12, 'dcma_activity', 'critical_tasks'),
            ('near_critical_tasks', 'Percent', 45, 66, 15, 12, 'dcma_activity', 'near_critical_tasks'),
            ('riding_progress_date','Percent', 3, 7, 15, 12, 'dcma_activity', 'riding_progress_date'),
            ('excessive_ss_lag',    'Percent', 3, 7,  0,  0, 'excessive_ss_eligible', 'excessive_ss_lag'),
            ('excessive_ff_lag',    'Percent', 3, 7,  0,  0, 'excessive_ff_eligible', 'excessive_ff_lag')
        ) AS policy_values
        (check_code, limit_type, green_limit, amber_limit, green_points, amber_points, records_metric, qualifying_metric)
    )
    UPDATE scope
    SET limit_type = COALESCE(scope.limit_type, policy.limit_type),
        green_limit = COALESCE(scope.green_limit, policy.green_limit),
        amber_limit = COALESCE(scope.amber_limit, policy.amber_limit),
        green_points = COALESCE(scope.green_points, policy.green_points),
        amber_points = COALESCE(scope.amber_points, policy.amber_points),
        records_metric = COALESCE(scope.records_metric, policy.records_metric),
        qualifying_metric = COALESCE(scope.qualifying_metric, policy.qualifying_metric)
    FROM [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
    JOIN workbook_rules AS policy ON policy.check_code = scope.check_code;

    IF EXISTS
    (
        SELECT 1
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE limit_type IS NULL OR green_limit IS NULL OR amber_limit IS NULL
           OR green_points IS NULL OR amber_points IS NULL
           OR records_metric IS NULL OR qualifying_metric IS NULL
    )
        THROW 51702, 'Every schedule-quality check must have a complete score policy.', 1;

    IF NOT EXISTS
    (
        SELECT 1 FROM sys.check_constraints
        WHERE name = N'CK_xertoolkit_sq_check_score_policy'
          AND parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]')
    )
        ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_check_scope]
            ADD CONSTRAINT [CK_xertoolkit_sq_check_score_policy]
            CHECK
            (
                limit_type IS NOT NULL
                AND green_limit IS NOT NULL
                AND amber_limit IS NOT NULL
                AND green_points IS NOT NULL
                AND amber_points IS NOT NULL
                AND records_metric IN
                    ('dcma_activity', 'relationship', 'open_start_eligible',
                     'open_finish_eligible', 'excessive_ss_eligible',
                     'excessive_ff_eligible', 'none')
                AND qualifying_metric IN
                    ('missing_predecessor', 'missing_successor', 'open_start',
                     'open_finish', 'relationship_leads', 'relationship_lags',
                     'non_fs', 'constraints', 'high_float', 'negative_float',
                     'high_duration', 'invalid_dates', 'in_progress_errors',
                     'logical_loops', 'out_of_sequence', 'critical_tasks',
                     'near_critical_tasks', 'riding_progress_date',
                     'excessive_ss_lag', 'excessive_ff_lag')
                AND limit_type IN ('Percent', 'Number')
                AND green_limit >= 0
                AND amber_limit >= green_limit
                AND green_points >= 0
                AND amber_points >= 0
            );

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO

/* Preserve score policy when the editor creates a draft from the active version. */
DECLARE @draft_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_get_or_create_schedule_quality_draft]'));
IF @draft_definition IS NULL
    THROW 51703, 'The schedule-quality draft procedure is missing.', 1;

IF @draft_definition NOT LIKE N'%records_metric%'
BEGIN
    DECLARE @draft_before nvarchar(max) = @draft_definition;
    SET @draft_definition = REPLACE
    (
        @draft_definition,
        N'                include_milestones,
                exclude_complete
            )
            SELECT
                @config_version_id,
                check_code,
                display_name,
                sort_order,
                is_enabled,
                include_loe,
                include_wbs_summary,
                include_milestones,
                exclude_complete',
        N'                include_milestones,
                exclude_complete,
                limit_type, green_limit, amber_limit, green_points, amber_points,
                records_metric, qualifying_metric
            )
            SELECT
                @config_version_id,
                check_code,
                display_name,
                sort_order,
                is_enabled,
                include_loe,
                include_wbs_summary,
                include_milestones,
                exclude_complete,
                limit_type, green_limit, amber_limit, green_points, amber_points,
                records_metric, qualifying_metric'
    );
    IF @draft_definition = @draft_before
        THROW 51704, 'The draft procedure no longer matches the expected check-copy contract.', 1;
    SET @draft_definition = REPLACE(@draft_definition, N'CREATE OR ALTER PROCEDURE', N'ALTER PROCEDURE');
    SET @draft_definition = REPLACE(@draft_definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @draft_definition;
END;
GO

/* Persist score policy with the existing optimistic-lock save contract. */
DECLARE @save_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_save_schedule_quality_draft]'));
IF @save_definition IS NULL
    THROW 51705, 'The schedule-quality save procedure is missing.', 1;

IF @save_definition NOT LIKE N'%records_metric%'
BEGIN
    DECLARE @save_before nvarchar(max) = @save_definition;
    SET @save_definition = REPLACE
    (
        @save_definition,
        N'exclude_complete bit NULL
    );
    DECLARE @options TABLE',
        N'exclude_complete bit NULL,
        limit_type varchar(10) NULL,
        green_limit decimal(9,2) NULL,
        amber_limit decimal(9,2) NULL,
        green_points int NULL,
        amber_points int NULL,
        records_metric varchar(50) NULL,
        qualifying_metric varchar(50) NULL
    );
    DECLARE @options TABLE'
    );
    SET @save_definition = REPLACE
    (
        @save_definition,
        N'        include_milestones,
        exclude_complete
    )
    SELECT
        check_code,
        is_enabled,
        include_loe,
        include_wbs_summary,
        include_milestones,
        exclude_complete
    FROM OPENJSON(@settings_json, ''$.checks'')',
        N'        include_milestones,
        exclude_complete,
        limit_type,
        green_limit,
        amber_limit,
        green_points,
        amber_points,
        records_metric,
        qualifying_metric
    )
    SELECT
        check_code,
        is_enabled,
        include_loe,
        include_wbs_summary,
        include_milestones,
        exclude_complete,
        limit_type,
        green_limit,
        amber_limit,
        green_points,
        amber_points,
        records_metric,
        qualifying_metric
    FROM OPENJSON(@settings_json, ''$.checks'')'
    );
    SET @save_definition = REPLACE
    (
        @save_definition,
        N'exclude_complete bit ''$.exclude_complete''
    );',
        N'exclude_complete bit ''$.exclude_complete'',
        limit_type varchar(10) ''$.limit_type'',
        green_limit decimal(9,2) ''$.green_limit'',
        amber_limit decimal(9,2) ''$.amber_limit'',
        green_points int ''$.green_points'',
        amber_points int ''$.amber_points'',
        records_metric varchar(50) ''$.records_metric'',
        qualifying_metric varchar(50) ''$.qualifying_metric''
    );'
    );
    SET @save_definition = REPLACE
    (
        @save_definition,
        N'cs.exclude_complete = c.exclude_complete
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope] AS cs
        INNER JOIN @checks AS c',
        N'cs.exclude_complete = c.exclude_complete,
            cs.limit_type = c.limit_type,
            cs.green_limit = c.green_limit,
            cs.amber_limit = c.amber_limit,
            cs.green_points = c.green_points,
            cs.amber_points = c.amber_points,
            cs.records_metric = c.records_metric,
            cs.qualifying_metric = c.qualifying_metric
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope] AS cs
        INNER JOIN @checks AS c'
    );
    SET @save_definition = REPLACE
    (
        @save_definition,
        N'include_milestones, exclude_complete
                 FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]',
        N'include_milestones, exclude_complete, limit_type, green_limit, amber_limit,
                         green_points, amber_points, records_metric, qualifying_metric
                 FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]'
    );
    IF @save_definition = @save_before
       OR @save_definition NOT LIKE N'%qualifying_metric varchar(50)%'
       OR @save_definition NOT LIKE N'%cs.records_metric = c.records_metric%'
        THROW 51706, 'The save procedure no longer matches the expected score-policy contract.', 1;
    SET @save_definition = REPLACE(@save_definition, N'CREATE OR ALTER PROCEDURE', N'ALTER PROCEDURE');
    SET @save_definition = REPLACE(@save_definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @save_definition;
END;
GO

/* Workbook open-end semantics: either FS or SS is valid predecessor logic;
   either FS or FF is valid successor logic.  Return eligibility so the result
   view can use the same denominator as the calculation. */
DECLARE @open_ends_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'));
IF @open_ends_definition IS NULL
    THROW 51707, 'The open-end calculation function is missing.', 1;

IF @open_ends_definition NOT LIKE N'%WORKBOOK_OPEN_END_PARITY_V1%'
BEGIN
    DECLARE @open_ends_before nvarchar(max) = @open_ends_definition;
    SET @open_ends_definition = REPLACE
    (
        @open_ends_definition,
        N'                         AND paired_relationship.task_id IS NOT NULL
                        THEN 1 ELSE 0',
        N'                        THEN 1 ELSE 0'
    );
    SET @open_ends_definition = REPLACE
    (
        @open_ends_definition,
        N'        a.task_id,
        CONVERT',
        N'        a.task_id,
        a.is_milestone,
        a.in_open_start_scope,
        a.in_open_finish_scope,
        /* WORKBOOK_OPEN_END_PARITY_V1 */
        CONVERT'
    );
    IF @open_ends_definition = @open_ends_before
       OR @open_ends_definition NOT LIKE N'%WORKBOOK_OPEN_END_PARITY_V1%'
        THROW 51708, 'The open-end calculation function no longer matches the expected contract.', 1;
    SET @open_ends_definition = REPLACE(@open_ends_definition, N'CREATE OR ALTER FUNCTION', N'ALTER FUNCTION');
    SET @open_ends_definition = REPLACE(@open_ends_definition, N'CREATE FUNCTION', N'ALTER FUNCTION');
    EXEC sys.sp_executesql @open_ends_definition;
END;
GO

/* Workbook relationship ratio semantics: only SS relationships not starting
   at a Start Milestone and FF relationships not ending at a Finish Milestone
   qualify. */
DECLARE @relationship_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_relationship_quality]'));
IF @relationship_definition IS NULL
    THROW 51709, 'The relationship-quality calculation function is missing.', 1;

IF @relationship_definition NOT LIKE N'%WORKBOOK_RELATIONSHIP_RATIO_PARITY_V1%'
BEGIN
    DECLARE @relationship_before nvarchar(max) = @relationship_definition;
    SET @relationship_definition = REPLACE
    (
        @relationship_definition,
        N'            r.*,
            CONVERT',
        N'            r.*,
            CONVERT(bit, ISNULL(predecessor.is_start_milestone, 0)) AS predecessor_is_start_milestone,
            CONVERT(bit, ISNULL(successor.is_finish_milestone, 0)) AS successor_is_finish_milestone,
            CONVERT'
    );
    SET @relationship_definition = REPLACE
    (
        @relationship_definition,
        N'        FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
        CROSS JOIN scope_settings AS scope',
        N'        FROM [powerbitables].[xertoolkit_vw_PBI_Relationships] AS r
        LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS predecessor
          ON predecessor.proj_id = r.proj_id
         AND predecessor.task_id = r.predecessor_task_id
        LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS successor
          ON successor.proj_id = r.proj_id
         AND successor.task_id = r.successor_task_id
        CROSS JOIN scope_settings AS scope'
    );
    SET @relationship_definition = REPLACE
    (
        @relationship_definition,
        N'CONVERT(int, CASE WHEN r.in_ratio_scope = 1 AND r.relationship_type <> ''PR_FS'' THEN 1 ELSE 0 END) AS is_non_fs,',
        N'CONVERT(int, CASE
                WHEN r.in_ratio_scope = 1
                 AND ((r.relationship_type = ''PR_SS'' AND r.predecessor_is_start_milestone = 0)
                   OR (r.relationship_type = ''PR_FF'' AND r.successor_is_finish_milestone = 0))
                THEN 1 ELSE 0 END) AS is_non_fs,
        CONVERT(int, CASE WHEN r.in_excessive_ss_scope = 1
                             AND r.relationship_type = ''PR_SS''
                             AND r.predecessor_duration_hours IS NOT NULL
                             AND r.lag_hours > 0 THEN 1 ELSE 0 END) AS is_excessive_ss_eligible,
        CONVERT(int, CASE WHEN r.in_excessive_ff_scope = 1
                             AND r.relationship_type = ''PR_FF''
                             AND r.successor_duration_hours IS NOT NULL
                             AND r.lag_hours > 0 THEN 1 ELSE 0 END) AS is_excessive_ff_eligible,
        /* WORKBOOK_RELATIONSHIP_RATIO_PARITY_V1 */'
    );
    IF @relationship_definition = @relationship_before
       OR @relationship_definition NOT LIKE N'%WORKBOOK_RELATIONSHIP_RATIO_PARITY_V1%'
       OR @relationship_definition NOT LIKE N'%predecessor_is_start_milestone%'
        THROW 51710, 'The relationship-quality function no longer matches the expected contract.', 1;
    SET @relationship_definition = REPLACE(@relationship_definition, N'CREATE OR ALTER FUNCTION', N'ALTER FUNCTION');
    SET @relationship_definition = REPLACE(@relationship_definition, N'CREATE FUNCTION', N'ALTER FUNCTION');
    EXEC sys.sp_executesql @relationship_definition;
END;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityResults]
AS
WITH active_metrics AS
(
    SELECT metrics.*
    FROM [powerbitables].[xertoolkit_result_project_metrics] AS metrics
    JOIN [powerbitables].[xertoolkit_schedule_quality_profile] AS profile
      ON profile.active_config_version_id = metrics.config_version_id
    WHERE profile.profile_code = N'default'
),
open_end_denominators AS
(
    SELECT
        metrics.proj_id,
        metrics.config_version_id,
        SUM(CASE WHEN open_ends.in_open_start_scope = 1 AND open_ends.is_milestone = 0 THEN 1 ELSE 0 END) AS open_start_records,
        SUM(CASE WHEN open_ends.in_open_finish_scope = 1 AND open_ends.is_milestone = 0 THEN 1 ELSE 0 END) AS open_finish_records
    FROM active_metrics AS metrics
    OUTER APPLY
    (
        SELECT *
        FROM [powerbitables].[xertoolkit_fn_open_ends](metrics.config_version_id)
        WHERE proj_id = metrics.proj_id
    ) AS open_ends
    GROUP BY metrics.proj_id, metrics.config_version_id
),
relationship_denominators AS
(
    SELECT
        metrics.proj_id,
        metrics.config_version_id,
        SUM(relationship_quality.is_excessive_ss_eligible) AS excessive_ss_records,
        SUM(relationship_quality.is_excessive_ff_eligible) AS excessive_ff_records
    FROM active_metrics AS metrics
    OUTER APPLY
    (
        SELECT *
        FROM [powerbitables].[xertoolkit_fn_relationship_quality](metrics.config_version_id)
        WHERE proj_id = metrics.proj_id
    ) AS relationship_quality
    GROUP BY metrics.proj_id, metrics.config_version_id
)
SELECT
    metrics.proj_id,
    metrics.project_name,
    metrics.updated_date,
    metrics.check_run_id,
    metrics.refreshed_at,
    metrics.config_version_id,
    scope.check_code,
    scope.display_name,
    scope.sort_order,
    scope.limit_type,
    scope.green_limit,
    scope.amber_limit,
    scope.green_points,
    scope.amber_points,
    scope.records_metric,
    scope.qualifying_metric,
    CONVERT
    (
        bigint,
        CASE scope.records_metric
            WHEN 'dcma_activity' THEN metrics.dcma_activity_count
            WHEN 'relationship' THEN metrics.relationship_count
            WHEN 'open_start_eligible' THEN ISNULL(open_end.open_start_records, 0)
            WHEN 'open_finish_eligible' THEN ISNULL(open_end.open_finish_records, 0)
            WHEN 'excessive_ss_eligible' THEN ISNULL(relationship.excessive_ss_records, 0)
            WHEN 'excessive_ff_eligible' THEN ISNULL(relationship.excessive_ff_records, 0)
            WHEN 'none' THEN 0
            ELSE 0
        END
    ) AS records_checked,
    CONVERT
    (
        bigint,
        CASE scope.qualifying_metric
            WHEN 'missing_predecessor' THEN metrics.missing_predecessor_count
            WHEN 'missing_successor' THEN metrics.missing_successor_count
            WHEN 'open_start' THEN metrics.open_start_count
            WHEN 'open_finish' THEN metrics.open_finish_count
            WHEN 'relationship_leads' THEN metrics.lead_count
            WHEN 'relationship_lags' THEN metrics.lag_count
            WHEN 'non_fs' THEN metrics.non_fs_count
            WHEN 'constraints' THEN metrics.constraint_count
            WHEN 'high_float' THEN metrics.high_float_count
            WHEN 'negative_float' THEN metrics.negative_float_count
            WHEN 'high_duration' THEN metrics.high_duration_count
            WHEN 'invalid_dates' THEN metrics.invalid_date_count
            WHEN 'in_progress_errors' THEN metrics.in_progress_error_count
            WHEN 'logical_loops' THEN metrics.logical_loop_count
            WHEN 'out_of_sequence' THEN metrics.out_of_sequence_count
            WHEN 'critical_tasks' THEN metrics.critical_task_count
            WHEN 'near_critical_tasks' THEN metrics.near_critical_task_count
            WHEN 'riding_progress_date' THEN metrics.riding_progress_date_count
            WHEN 'excessive_ss_lag' THEN metrics.excessive_ss_lag_count
            WHEN 'excessive_ff_lag' THEN metrics.excessive_ff_lag_count
            ELSE 0
        END
    ) AS qualifying_results
FROM active_metrics AS metrics
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
  ON scope.config_version_id = metrics.config_version_id
 AND scope.is_enabled = 1
LEFT JOIN open_end_denominators AS open_end
  ON open_end.proj_id = metrics.proj_id
 AND open_end.config_version_id = metrics.config_version_id
LEFT JOIN relationship_denominators AS relationship
  ON relationship.proj_id = metrics.proj_id
 AND relationship.config_version_id = metrics.config_version_id;
GO

IF OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityResults]', N'V') IS NULL
    THROW 51711, 'The normalised schedule-quality result view was not created.', 1;
GO
