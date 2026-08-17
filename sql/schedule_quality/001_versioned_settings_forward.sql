/*
    Version-controlled schedule-quality settings and SQL-owned calculations.

    Prerequisite: 000_predeploy_snapshot.sql completed successfully.
    This file is intentionally split with GO for a pyodbc GO-aware runner.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51100, 'This deployment must be run against P62212_1.', 1;

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260714_modules]', N'U') IS NULL
    THROW 51101, 'Run 000_predeploy_snapshot.sql before the forward deployment.', 1;

IF CAST(SERVERPROPERTY('ProductMajorVersion') AS int) < 13
    THROW 51102, 'SQL Server 2016 or newer is required for OPENJSON and CREATE OR ALTER.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_run_history]', N'U') IS NULL
    THROW 51103, 'Required materialised result tables are missing.', 1;
GO

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_profile]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_schedule_quality_profile]
    (
        profile_id int IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_xertoolkit_schedule_quality_profile] PRIMARY KEY,
        profile_code nvarchar(50) NOT NULL,
        profile_name nvarchar(100) NOT NULL,
        active_config_version_id bigint NULL,
        created_at datetime2(7) NOT NULL
            CONSTRAINT [DF_xertoolkit_sq_profile_created_at] DEFAULT SYSUTCDATETIME(),
        row_version rowversion NOT NULL,
        CONSTRAINT [UQ_xertoolkit_sq_profile_code] UNIQUE (profile_code)
    );
END;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_config_version]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_schedule_quality_config_version]
    (
        config_version_id bigint IDENTITY(1,1) NOT NULL
            CONSTRAINT [PK_xertoolkit_schedule_quality_config_version] PRIMARY KEY,
        profile_id int NOT NULL,
        version_number int NOT NULL,
        state varchar(12) NOT NULL,
        based_on_config_version_id bigint NULL,
        change_note nvarchar(500) NULL,
        settings_hash char(64) NULL,
        created_at datetime2(7) NOT NULL
            CONSTRAINT [DF_xertoolkit_sq_version_created_at] DEFAULT SYSUTCDATETIME(),
        created_by nvarchar(150) NOT NULL,
        updated_at datetime2(7) NOT NULL
            CONSTRAINT [DF_xertoolkit_sq_version_updated_at] DEFAULT SYSUTCDATETIME(),
        updated_by nvarchar(150) NOT NULL,
        published_at datetime2(7) NULL,
        published_by nvarchar(150) NULL,
        CONSTRAINT [UQ_xertoolkit_sq_version_number] UNIQUE (profile_id, version_number),
        CONSTRAINT [CK_xertoolkit_sq_version_state]
            CHECK (state IN ('draft', 'active', 'superseded')),
        CONSTRAINT [FK_xertoolkit_sq_version_profile]
            FOREIGN KEY (profile_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_profile] (profile_id),
        CONSTRAINT [FK_xertoolkit_sq_version_based_on]
            FOREIGN KEY (based_on_config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
    );

    CREATE UNIQUE INDEX [UX_xertoolkit_sq_version_one_active]
        ON [powerbitables].[xertoolkit_schedule_quality_config_version] (profile_id)
        WHERE state = 'active';

    CREATE UNIQUE INDEX [UX_xertoolkit_sq_version_one_draft]
        ON [powerbitables].[xertoolkit_schedule_quality_config_version] (profile_id)
        WHERE state = 'draft';
END;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_check_scope]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_schedule_quality_check_scope]
    (
        config_version_id bigint NOT NULL,
        check_code varchar(50) NOT NULL,
        display_name nvarchar(100) NOT NULL,
        sort_order smallint NOT NULL,
        is_enabled bit NOT NULL,
        include_loe bit NULL,
        include_wbs_summary bit NULL,
        include_milestones bit NULL,
        exclude_complete bit NULL,
        CONSTRAINT [PK_xertoolkit_schedule_quality_check_scope]
            PRIMARY KEY (config_version_id, check_code),
        CONSTRAINT [FK_xertoolkit_sq_check_version]
            FOREIGN KEY (config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
            ON DELETE CASCADE
    );
END;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_option]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_schedule_quality_option]
    (
        config_version_id bigint NOT NULL,
        option_code varchar(50) NOT NULL,
        display_name nvarchar(100) NOT NULL,
        data_type varchar(10) NOT NULL,
        bit_value bit NULL,
        numeric_value decimal(18,4) NULL,
        text_value nvarchar(250) NULL,
        unit_code varchar(20) NULL,
        sort_order smallint NOT NULL,
        CONSTRAINT [PK_xertoolkit_schedule_quality_option]
            PRIMARY KEY (config_version_id, option_code),
        CONSTRAINT [FK_xertoolkit_sq_option_version]
            FOREIGN KEY (config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
            ON DELETE CASCADE,
        CONSTRAINT [CK_xertoolkit_sq_option_data_type]
            CHECK (data_type IN ('bit', 'integer', 'decimal', 'text')),
        CONSTRAINT [CK_xertoolkit_sq_option_typed_value]
            CHECK
            (
                (data_type = 'bit' AND bit_value IS NOT NULL AND numeric_value IS NULL AND text_value IS NULL)
                OR (data_type IN ('integer', 'decimal') AND bit_value IS NULL AND numeric_value IS NOT NULL AND text_value IS NULL)
                OR (data_type = 'text' AND bit_value IS NULL AND numeric_value IS NULL AND text_value IS NOT NULL)
            )
    );
END;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_constraint_type]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_schedule_quality_constraint_type]
    (
        config_version_id bigint NOT NULL,
        constraint_type_code varchar(20) NOT NULL,
        display_name nvarchar(100) NOT NULL,
        is_checked bit NOT NULL,
        sort_order smallint NOT NULL,
        CONSTRAINT [PK_xertoolkit_schedule_quality_constraint_type]
            PRIMARY KEY (config_version_id, constraint_type_code),
        CONSTRAINT [FK_xertoolkit_sq_constraint_version]
            FOREIGN KEY (config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
            ON DELETE CASCADE
    );
END;
GO

IF NOT EXISTS
(
    SELECT 1
    FROM sys.foreign_keys
    WHERE parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_profile]')
      AND name = N'FK_xertoolkit_sq_profile_active_version'
)
BEGIN
    ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_profile]
    ADD CONSTRAINT [FK_xertoolkit_sq_profile_active_version]
        FOREIGN KEY (active_config_version_id)
        REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id);
END;

IF COL_LENGTH(N'powerbitables.xertoolkit_result_project_metrics', N'config_version_id') IS NULL
    ALTER TABLE [powerbitables].[xertoolkit_result_project_metrics]
    ADD config_version_id bigint NULL;

IF COL_LENGTH(N'powerbitables.xertoolkit_result_logic_loop_tasks', N'config_version_id') IS NULL
    ALTER TABLE [powerbitables].[xertoolkit_result_logic_loop_tasks]
    ADD config_version_id bigint NULL;

IF COL_LENGTH(N'powerbitables.xertoolkit_refresh_run_history', N'config_version_id') IS NULL
    ALTER TABLE [powerbitables].[xertoolkit_refresh_run_history]
    ADD config_version_id bigint NULL;
GO

IF NOT EXISTS
(
    SELECT 1 FROM sys.foreign_keys
    WHERE parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]')
      AND name = N'FK_xertoolkit_project_metrics_config_version'
)
    ALTER TABLE [powerbitables].[xertoolkit_result_project_metrics]
    ADD CONSTRAINT [FK_xertoolkit_project_metrics_config_version]
        FOREIGN KEY (config_version_id)
        REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id);

IF NOT EXISTS
(
    SELECT 1 FROM sys.foreign_keys
    WHERE parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]')
      AND name = N'FK_xertoolkit_logic_loop_config_version'
)
    ALTER TABLE [powerbitables].[xertoolkit_result_logic_loop_tasks]
    ADD CONSTRAINT [FK_xertoolkit_logic_loop_config_version]
        FOREIGN KEY (config_version_id)
        REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id);

IF NOT EXISTS
(
    SELECT 1 FROM sys.foreign_keys
    WHERE parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_run_history]')
      AND name = N'FK_xertoolkit_refresh_history_config_version'
)
    ALTER TABLE [powerbitables].[xertoolkit_refresh_run_history]
    ADD CONSTRAINT [FK_xertoolkit_refresh_history_config_version]
        FOREIGN KEY (config_version_id)
        REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id);

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]')
      AND name = N'IX_xertoolkit_project_metrics_config_version'
)
    CREATE INDEX [IX_xertoolkit_project_metrics_config_version]
        ON [powerbitables].[xertoolkit_result_project_metrics] (config_version_id, proj_id);

IF NOT EXISTS
(
    SELECT 1 FROM sys.indexes
    WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]')
      AND name = N'IX_xertoolkit_logic_loop_config_version'
)
    CREATE INDEX [IX_xertoolkit_logic_loop_config_version]
        ON [powerbitables].[xertoolkit_result_logic_loop_tasks] (config_version_id, proj_id, task_id);
GO

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]', N'U') IS NULL
BEGIN
    CREATE TABLE [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    (
        proj_id int NOT NULL,
        check_code varchar(50) NOT NULL,
        task_id int NOT NULL,
        task_code varchar(40) NOT NULL,
        task_name varchar(120) NOT NULL,
        evidence_basis varchar(40) NOT NULL,
        check_run_id bigint NOT NULL,
        refreshed_at datetime2(7) NOT NULL,
        config_version_id bigint NOT NULL,
        CONSTRAINT [PK_xertoolkit_result_schedule_quality_task_evidence]
            PRIMARY KEY CLUSTERED (proj_id, check_code, task_id),
        CONSTRAINT [FK_xertoolkit_task_evidence_run]
            FOREIGN KEY (check_run_id)
            REFERENCES [powerbitables].[xertoolkit_refresh_run_history] (check_run_id),
        CONSTRAINT [FK_xertoolkit_task_evidence_config]
            FOREIGN KEY (config_version_id)
            REFERENCES [powerbitables].[xertoolkit_schedule_quality_config_version] (config_version_id)
    );

    CREATE INDEX [IX_xertoolkit_task_evidence_check]
        ON [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        (check_code, proj_id, task_id)
        INCLUDE (task_code, task_name, evidence_basis);

    CREATE INDEX [IX_xertoolkit_task_evidence_snapshot]
        ON [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        (config_version_id, check_run_id, proj_id);
END;
GO

BEGIN TRANSACTION;

IF NOT EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_schedule_quality_profile]
    WHERE profile_code = N'default'
)
BEGIN
    INSERT INTO [powerbitables].[xertoolkit_schedule_quality_profile]
        (profile_code, profile_name)
    VALUES
        (N'default', N'Default schedule quality checks');
END;

DECLARE @profile_id int =
(
    SELECT profile_id
    FROM [powerbitables].[xertoolkit_schedule_quality_profile]
    WHERE profile_code = N'default'
);

IF NOT EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
    WHERE profile_id = @profile_id
)
BEGIN
    INSERT INTO [powerbitables].[xertoolkit_schedule_quality_config_version]
    (
        profile_id, version_number, state, change_note,
        created_by, updated_by, published_at, published_by
    )
    VALUES
    (
        @profile_id, 1, 'active',
        N'Seeded from 162528 - Hertford Loop Integrity Check (3).xlsx',
        N'system migration 2026-07-14', N'system migration 2026-07-14',
        SYSUTCDATETIME(), N'system migration 2026-07-14'
    );

    DECLARE @seed_config_version_id bigint = SCOPE_IDENTITY();

    INSERT INTO [powerbitables].[xertoolkit_schedule_quality_check_scope]
    (
        config_version_id, check_code, display_name, sort_order, is_enabled,
        include_loe, include_wbs_summary, include_milestones, exclude_complete
    )
    SELECT
        @seed_config_version_id, v.check_code, v.display_name, v.sort_order, CAST(1 AS bit),
        v.include_loe, v.include_wbs_summary, v.include_milestones, v.exclude_complete
    FROM
    (
        VALUES
        ('missing_predecessor', N'Missing Predecessors', 10, CAST(NULL AS bit), CAST(NULL AS bit), CAST(1 AS bit), CAST(1 AS bit)),
        ('missing_successor', N'Missing Successors', 20, NULL, NULL, 1, 1),
        ('open_finish', N'Open-Finish Tasks', 30, NULL, NULL, NULL, 1),
        ('open_start', N'Open-Start Tasks', 40, NULL, NULL, NULL, 1),
        ('relationship_leads', N'Relationship -ve Lags (Leads)', 50, 0, 0, 1, 1),
        ('relationship_lags', N'Relationship +ve Lags', 60, 0, 0, 1, 1),
        ('relationship_ratio', N'Relationship Ratio / Non-FS', 70, 0, 0, 1, 1),
        ('constraints', N'Constraints', 80, 0, 0, 1, 1),
        ('high_float', N'High Total Float', 90, 0, 0, 1, 1),
        ('negative_float', N'Negative Float', 100, 0, 0, 1, 1),
        ('high_duration', N'High Duration', 110, 0, 0, 1, 1),
        ('invalid_dates', N'Invalid Dates', 120, 0, 0, 1, 0),
        ('in_progress_errors', N'In Progress Errors', 130, 0, 0, 1, 0),
        ('logical_loops', N'Logical Loops', 140, NULL, NULL, NULL, NULL),
        ('out_of_sequence', N'Out of Sequence', 150, NULL, NULL, NULL, 1),
        ('critical_tasks', N'Critical Tasks', 160, 0, 0, 1, 1),
        ('near_critical_tasks', N'Near Critical Tasks', 170, 0, 0, 1, 1),
        ('riding_progress_date', N'Riding Progress Date', 180, 0, 0, 1, 1),
        ('excessive_ss_lag', N'Excessive SS Lag Duration', 190, 0, 0, 1, 1),
        ('excessive_ff_lag', N'Excessive FF Lag Duration', 200, 0, 0, 1, 1)
    ) AS v
    (
        check_code, display_name, sort_order,
        include_loe, include_wbs_summary, include_milestones, exclude_complete
    );

    INSERT INTO [powerbitables].[xertoolkit_schedule_quality_option]
    (
        config_version_id, option_code, display_name, data_type,
        bit_value, numeric_value, text_value, unit_code, sort_order
    )
    SELECT
        @seed_config_version_id, v.option_code, v.display_name, v.data_type,
        v.bit_value, v.numeric_value, CAST(NULL AS nvarchar(250)), v.unit_code, v.sort_order
    FROM
    (
        VALUES
        ('high_float_days', N'High float threshold', 'integer', CAST(NULL AS bit), CAST(84 AS decimal(18,4)), 'days', 10),
        ('negative_float_days', N'Negative float threshold', 'integer', NULL, CAST(0 AS decimal(18,4)), 'days', 20),
        ('high_duration_days', N'High duration threshold', 'integer', NULL, CAST(84 AS decimal(18,4)), 'days', 30),
        ('near_critical_upper_days', N'Near critical upper float', 'integer', NULL, CAST(20 AS decimal(18,4)), 'days', 40),
        ('riding_days_after_data_date', N'Riding date days after data date', 'integer', NULL, CAST(3 AS decimal(18,4)), 'days', 50),
        ('excessive_ss_percent', N'Excessive SS lag percentage of predecessor duration', 'decimal', NULL, CAST(50 AS decimal(18,4)), 'percent', 60),
        ('excessive_ff_percent', N'Excessive FF lag percentage of successor duration', 'decimal', NULL, CAST(50 AS decimal(18,4)), 'percent', 70),
        ('invalid_early_before_progress', N'Forecast dates before progress date', 'bit', CAST(1 AS bit), NULL, NULL, 80),
        ('invalid_actual_after_progress', N'Actual dates after progress date', 'bit', CAST(1 AS bit), NULL, NULL, 90),
        ('progress_started_zero_percent', N'Started activities at zero percent', 'bit', CAST(1 AS bit), NULL, NULL, 100),
        ('progress_finished_below_100', N'Finished activities below 100 percent', 'bit', CAST(1 AS bit), NULL, NULL, 110),
        ('progress_percent_without_start', N'Positive percent without actual start', 'bit', CAST(1 AS bit), NULL, NULL, 120)
    ) AS v
    (
        option_code, display_name, data_type,
        bit_value, numeric_value, unit_code, sort_order
    );

    INSERT INTO [powerbitables].[xertoolkit_schedule_quality_constraint_type]
    (
        config_version_id, constraint_type_code, display_name, is_checked, sort_order
    )
    SELECT @seed_config_version_id, v.constraint_type_code, v.display_name, v.is_checked, v.sort_order
    FROM
    (
        VALUES
        ('CS_MSOB', N'Start On or Before', CAST(0 AS bit), 10),
        ('CS_MSOA', N'Start On or After', CAST(0 AS bit), 20),
        ('CS_MEOB', N'Finish On or Before', CAST(0 AS bit), 30),
        ('CS_MEOA', N'Finish On or After', CAST(0 AS bit), 40),
        ('CS_MANDSTART', N'Mandatory Start', CAST(1 AS bit), 50),
        ('CS_MANDFIN', N'Mandatory Finish', CAST(1 AS bit), 60),
        ('CS_MEO', N'Finish On', CAST(1 AS bit), 70),
        ('CS_MSO', N'Start On', CAST(1 AS bit), 80),
        ('CS_ALAP', N'As Late As Possible', CAST(0 AS bit), 90),
        ('CS_EXPECTED', N'Expected Finish', CAST(0 AS bit), 100)
    ) AS v (constraint_type_code, display_name, is_checked, sort_order);

    DECLARE @checks_json nvarchar(max) =
    (
        SELECT check_code, is_enabled, include_loe, include_wbs_summary, include_milestones, exclude_complete
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = @seed_config_version_id
        ORDER BY sort_order, check_code
        FOR JSON PATH, INCLUDE_NULL_VALUES
    );
    DECLARE @options_json nvarchar(max) =
    (
        SELECT option_code, data_type, bit_value, numeric_value, text_value
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @seed_config_version_id
        ORDER BY sort_order, option_code
        FOR JSON PATH, INCLUDE_NULL_VALUES
    );
    DECLARE @constraints_json nvarchar(max) =
    (
        SELECT constraint_type_code, is_checked
        FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
        WHERE config_version_id = @seed_config_version_id
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
    WHERE config_version_id = @seed_config_version_id;

    UPDATE [powerbitables].[xertoolkit_schedule_quality_profile]
    SET active_config_version_id = @seed_config_version_id
    WHERE profile_id = @profile_id;
END;

IF EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
    WHERE profile_id = @profile_id
      AND state = 'active'
)
BEGIN
    UPDATE p
    SET active_config_version_id = v.config_version_id
    FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
    JOIN [powerbitables].[xertoolkit_schedule_quality_config_version] AS v
      ON v.profile_id = p.profile_id
     AND v.state = 'active'
    WHERE p.profile_id = @profile_id
      AND (p.active_config_version_id IS NULL OR p.active_config_version_id <> v.config_version_id);
END;

/* Keep the legacy four-row threshold table coherent for existing readers. */
DECLARE @active_seed_version_id bigint =
(
    SELECT active_config_version_id
    FROM [powerbitables].[xertoolkit_schedule_quality_profile]
    WHERE profile_id = @profile_id
);
DECLARE @legacy_thresholds TABLE
(
    setting_name nvarchar(100) NOT NULL PRIMARY KEY,
    setting_value decimal(18,4) NOT NULL
);

INSERT INTO @legacy_thresholds (setting_name, setting_value)
SELECT N'High Duration', numeric_value
FROM [powerbitables].[xertoolkit_schedule_quality_option]
WHERE config_version_id = @active_seed_version_id
  AND option_code = 'high_duration_days'
UNION ALL
SELECT N'High Float', numeric_value
FROM [powerbitables].[xertoolkit_schedule_quality_option]
WHERE config_version_id = @active_seed_version_id
  AND option_code = 'high_float_days'
UNION ALL
SELECT N'Critical Float', CONVERT(decimal(18,4), 0)
UNION ALL
SELECT N'Near Critical', numeric_value
FROM [powerbitables].[xertoolkit_schedule_quality_option]
WHERE config_version_id = @active_seed_version_id
  AND option_code = 'near_critical_upper_days';

UPDATE legacy
SET setting_value = source.setting_value
FROM [powerbitables].[xertoolkit_settings_thresholds] AS legacy
JOIN @legacy_thresholds AS source
  ON source.setting_name = legacy.setting_name;

INSERT INTO [powerbitables].[xertoolkit_settings_thresholds]
    (setting_name, setting_value)
SELECT source.setting_name, source.setting_value
FROM @legacy_thresholds AS source
WHERE NOT EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_settings_thresholds] AS legacy
    WHERE legacy.setting_name = source.setting_name
);

COMMIT TRANSACTION;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_Projects]
AS
WITH project_codes AS
(
    SELECT
        pcat.proj_id,
        MAX(CASE WHEN pct.proj_catg_type = 'Contract Type' THEN pcv.proj_catg_name END) AS [Contract Type],
        MAX(CASE WHEN pct.proj_catg_type = 'Project Type' THEN pcv.proj_catg_name END) AS [Project Type],
        MAX(CASE WHEN pct.proj_catg_type = 'Project ID' THEN pcv.proj_catg_name END) AS [Project Name],
        MAX(CASE WHEN pct.proj_catg_type = 'Industry' THEN pcv.proj_catg_name END) AS [Industry],
        MAX(CASE WHEN pct.proj_catg_type = 'Account' THEN pcv.proj_catg_name END) AS [Account],
        MAX(CASE WHEN pct.proj_catg_type = 'Project Status' THEN pcv.proj_catg_name END) AS [Project Status],
        MAX(CASE WHEN pct.proj_catg_type = 'Planner' THEN pcv.proj_catg_name END) AS [Planner],
        MAX(CASE WHEN pct.proj_catg_type = 'Project State' THEN pcv.proj_catg_name END) AS [Project State],
        MAX(CASE WHEN pct.proj_catg_type = 'Lead Planner' THEN pcv.proj_catg_name END) AS [Lead Planner],
        MAX(CASE WHEN pct.proj_catg_type = 'Client' THEN pcv.proj_catg_name END) AS [Client]
    FROM dbo.PROJPCAT AS pcat
    JOIN dbo.PCATTYPE AS pct
      ON pct.proj_catg_type_id = pcat.proj_catg_type_id
    JOIN dbo.PCATVAL AS pcv
      ON pcv.proj_catg_id = pcat.proj_catg_id
     AND pcv.proj_catg_type_id = pcat.proj_catg_type_id
    GROUP BY pcat.proj_id
),
updated_dates AS
(
    SELECT
        dedup.proj_id,
        MAX(TRY_CONVERT(date, formatted.formatted_date, 3)) AS [Updated Date]
    FROM
    (
        SELECT DISTINCT
            op.obs_id,
            op.proj_id,
            LTRIM(RTRIM
            (
                CASE
                    WHEN CHARINDEX('-', REVERSE(w.wbs_name)) > 0
                    THEN RIGHT(w.wbs_name, CHARINDEX('-', REVERSE(w.wbs_name)) - 1)
                    ELSE w.wbs_name
                END
            )) AS update_date_text
        FROM dbo.OBSPROJ AS op
        JOIN dbo.PROJECT AS p
          ON p.proj_id = op.proj_id
         AND p.project_flag = 'Y'
        LEFT JOIN dbo.PROJWBS AS w
          ON w.wbs_id = op.wbs_id
    ) AS dedup
    CROSS APPLY
    (
        SELECT
            CASE
                WHEN LEN(dedup.update_date_text) >= 8
                THEN SUBSTRING(dedup.update_date_text, 1, 2) + '/'
                   + SUBSTRING(dedup.update_date_text, 4, 2) + '/'
                   + SUBSTRING(dedup.update_date_text, 7, 2)
                ELSE NULL
            END AS formatted_date
    ) AS formatted
    GROUP BY dedup.proj_id
)
SELECT
    p.proj_id,
    p.proj_short_name,
    p.clndr_id,
    p.last_recalc_date,
    p.last_recalc_date AS data_date,
    p.plan_start_date,
    pc.[Contract Type],
    pc.[Project Type],
    pc.[Project Name],
    pc.[Industry],
    pc.[Account],
    pc.[Project Status],
    pc.[Planner],
    pc.[Project State],
    pc.[Lead Planner],
    pc.[Client],
    ud.[Updated Date]
FROM dbo.PROJECT AS p
LEFT JOIN project_codes AS pc
  ON pc.proj_id = p.proj_id
LEFT JOIN updated_dates AS ud
  ON ud.proj_id = p.proj_id;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_Activities]
AS
SELECT
    t.proj_id,
    t.task_id,
    t.wbs_id,
    t.task_code,
    t.task_name,
    t.task_type,
    t.status_code,
    t.complete_pct_type,
    t.target_drtn_hr_cnt,
    CAST(t.target_drtn_hr_cnt / 8.0 AS decimal(18,2)) AS target_duration_days,
    t.remain_drtn_hr_cnt,
    CAST(t.remain_drtn_hr_cnt / 8.0 AS decimal(18,2)) AS remaining_duration_days,
    t.phys_complete_pct,
    t.total_float_hr_cnt,
    CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2)) AS total_float_days,
    t.free_float_hr_cnt,
    CAST(t.free_float_hr_cnt / 8.0 AS decimal(18,2)) AS free_float_days,
    t.act_start_date,
    t.act_end_date,
    t.early_start_date,
    t.early_end_date,
    t.late_start_date,
    t.late_end_date,
    t.target_start_date,
    t.target_end_date,
    t.expect_end_date,
    t.cstr_type,
    t.cstr_date,
    t.cstr_type2,
    t.cstr_date2,
    t.driving_path_flag,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_LOE' THEN 1 ELSE 0 END) AS is_loe,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_WBS' THEN 1 ELSE 0 END) AS is_wbs_summary,
    CONVERT(bit, CASE WHEN t.task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END) AS is_milestone,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_FinMile' THEN 1 ELSE 0 END) AS is_finish_milestone,
    CONVERT(bit, CASE WHEN t.task_type = 'TT_Mile' THEN 1 ELSE 0 END) AS is_start_milestone,
    CONVERT(bit, CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END) AS is_complete,
    CONVERT
    (
        bit,
        CASE
            WHEN t.task_type NOT IN ('TT_LOE', 'TT_WBS')
             AND t.status_code <> 'TK_Complete'
            THEN 1 ELSE 0
        END
    ) AS is_dcma_activity
FROM dbo.TASK AS t;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_LogicLoops]
AS
WITH ranked_loops AS
(
    SELECT
        logic.proj_id,
        logic.task_id,
        logic.loop_path,
        logic.loop_length,
        logic.calculated_date,
        logic.check_run_id,
        logic.calculation_method,
        logic.config_version_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY logic.proj_id, logic.task_id
            ORDER BY
                CASE WHEN logic.calculation_method = 'self_loop' THEN 0 ELSE 1 END,
                logic.calculated_date DESC,
                logic.check_run_id DESC,
                logic.config_version_id DESC
        ) AS row_rank
    FROM [powerbitables].[xertoolkit_result_logic_loop_tasks] AS logic
)
SELECT
    logic.proj_id,
    logic.task_id,
    logic.loop_path,
    logic.loop_length,
    project.proj_short_name AS project_name,
    project.last_recalc_date AS project_data_date,
    task.task_code,
    task.task_name,
    task.task_type,
    task.status_code AS activity_status,
    task.wbs_id,
    wbs.wbs_name,
    task.act_start_date AS actual_start,
    task.act_end_date AS actual_finish,
    task.early_start_date AS early_start,
    task.early_end_date AS early_finish,
    task.late_start_date AS late_start,
    task.late_end_date AS late_finish,
    task.target_start_date AS target_start,
    task.target_end_date AS target_finish,
    logic.calculation_method,
    logic.calculated_date,
    logic.check_run_id,
    logic.config_version_id,
    CONVERT(bit, 1) AS is_logical_loop,
    CONVERT(nvarchar(30), N'logical_loop') AS logical_loop_status
FROM ranked_loops AS logic
LEFT JOIN dbo.PROJECT AS project
  ON project.proj_id = logic.proj_id
LEFT JOIN dbo.TASK AS task
  ON task.proj_id = logic.proj_id
 AND task.task_id = logic.task_id
LEFT JOIN dbo.PROJWBS AS wbs
  ON wbs.proj_id = task.proj_id
 AND wbs.wbs_id = task.wbs_id
WHERE logic.row_rank = 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_Relationships]
AS
SELECT
    tp.proj_id,
    tp.task_pred_id AS relationship_id,
    tp.pred_task_id AS predecessor_task_id,
    tp.task_id AS successor_task_id,
    pred.task_code AS predecessor_code,
    pred.task_name AS predecessor_name,
    succ.task_code AS successor_code,
    succ.task_name AS successor_name,
    tp.pred_type AS relationship_type,
    tp.lag_hr_cnt AS lag_hours,
    CAST(tp.lag_hr_cnt / 8.0 AS decimal(18,2)) AS lag_days,
    pred.target_duration_days AS predecessor_duration_days,
    succ.target_duration_days AS successor_duration_days,
    pred.target_drtn_hr_cnt AS predecessor_duration_hours,
    succ.target_drtn_hr_cnt AS successor_duration_hours,
    pred.status_code AS predecessor_status_code,
    succ.status_code AS successor_status_code,
    pred.is_loe AS predecessor_is_loe,
    succ.is_loe AS successor_is_loe,
    pred.is_wbs_summary AS predecessor_is_wbs_summary,
    succ.is_wbs_summary AS successor_is_wbs_summary,
    pred.is_milestone AS predecessor_is_milestone,
    succ.is_milestone AS successor_is_milestone,
    pred.is_complete AS predecessor_is_complete,
    succ.is_complete AS successor_is_complete
FROM dbo.TASKPRED AS tp
LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS pred
  ON pred.proj_id = tp.proj_id
 AND pred.task_id = tp.pred_task_id
LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS succ
  ON succ.proj_id = tp.proj_id
 AND succ.task_id = tp.task_id;
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
    WHERE (ISNULL(s.include_loe, 0) = 1 OR a.is_loe = 0)
      AND (ISNULL(s.include_wbs_summary, 0) = 1 OR a.is_wbs_summary = 0)
      AND (s.include_milestones IS NULL OR s.include_milestones = 1 OR a.is_milestone = 0)
      AND (ISNULL(s.exclude_complete, 0) = 0 OR a.is_complete = 0)
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
    WHERE r.predecessor_is_loe IS NOT NULL
      AND r.successor_is_loe IS NOT NULL
      AND
      (
          ISNULL(s.include_loe, 0) = 1
          OR (r.predecessor_is_loe = 0 AND r.successor_is_loe = 0)
      )
      AND
      (
          ISNULL(s.include_wbs_summary, 0) = 1
          OR
          (
              r.predecessor_is_wbs_summary = 0
              AND r.successor_is_wbs_summary = 0
          )
      )
      AND
      (
          s.include_milestones IS NULL
          OR s.include_milestones = 1
          OR
          (
              r.predecessor_is_milestone = 0
              AND r.successor_is_milestone = 0
          )
      )
      AND
      (
          ISNULL(s.exclude_complete, 0) = 0
          OR
          (
              r.predecessor_is_complete = 0
              AND r.successor_is_complete = 0
          )
      )
);
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_open_ends]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH deleted_activities AS
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
                     AND deleted.task_id IS NULL
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
                     AND deleted.task_id IS NULL
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
            MAX(CASE WHEN option_code = 'excessive_ff_percent' THEN numeric_value END) AS excessive_ff_percent
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
        WHERE r.predecessor_is_loe IS NOT NULL
          AND r.successor_is_loe IS NOT NULL
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
            MAX(CASE WHEN option_code = 'progress_percent_without_start' THEN CONVERT(int, bit_value) END) AS progress_percent_without_start
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

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH task_evidence AS
    (
        SELECT
            oe.proj_id,
            mapped.check_code,
            oe.task_id,
            mapped.evidence_basis
        FROM [powerbitables].[xertoolkit_fn_open_ends](@config_version_id) AS oe
        CROSS APPLY
        (
            VALUES
            ('missing_predecessor', oe.is_missing_predecessor, 'activity_exception'),
            ('missing_successor', oe.is_missing_successor, 'activity_exception'),
            ('open_start', oe.is_open_start, 'activity_exception'),
            ('open_finish', oe.is_open_finish, 'activity_exception')
        ) AS mapped (check_code, is_match, evidence_basis)
        WHERE mapped.is_match = 1

        UNION ALL

        SELECT
            aq.proj_id,
            mapped.check_code,
            aq.task_id,
            mapped.evidence_basis
        FROM [powerbitables].[xertoolkit_fn_activity_quality](@config_version_id) AS aq
        CROSS APPLY
        (
            VALUES
            ('high_duration', aq.is_high_duration, 'activity_exception'),
            ('high_float', aq.is_high_float, 'activity_exception'),
            ('negative_float', aq.is_negative_float, 'activity_exception'),
            ('constraints', aq.is_constraint, 'activity_exception'),
            ('critical_tasks', aq.is_critical_task, 'activity_exception'),
            ('near_critical_tasks', aq.is_near_critical_task, 'activity_exception'),
            ('invalid_dates', aq.is_invalid_date, 'activity_exception'),
            ('in_progress_errors', aq.is_in_progress_error, 'activity_exception'),
            ('riding_progress_date', aq.is_riding_progress_date, 'activity_exception')
        ) AS mapped (check_code, is_match, evidence_basis)
        WHERE mapped.is_match = 1

        UNION ALL

        SELECT
            rq.proj_id,
            mapped.check_code,
            endpoint.task_id,
            mapped.evidence_basis
        FROM [powerbitables].[xertoolkit_fn_relationship_quality](@config_version_id) AS rq
        CROSS APPLY
        (
            VALUES
            ('relationship_leads', rq.is_lead, 'relationship_endpoint'),
            ('relationship_lags', rq.is_lag, 'relationship_endpoint'),
            /*
               The headline relationship ratio is project-level and has no
               individually failing activity. The existing task-level evidence
               governed by that check is the set of non-FS relationship endpoints.
            */
            ('relationship_ratio', rq.is_non_fs, 'non_fs_relationship_endpoint'),
            ('excessive_ss_lag', rq.is_excessive_ss_lag, 'relationship_endpoint'),
            ('excessive_ff_lag', rq.is_excessive_ff_lag, 'relationship_endpoint')
        ) AS mapped (check_code, is_match, evidence_basis)
        CROSS APPLY
        (
            VALUES
            (rq.predecessor_task_id),
            (rq.successor_task_id)
        ) AS endpoint (task_id)
        WHERE mapped.is_match = 1
          AND endpoint.task_id IS NOT NULL
    )
    SELECT DISTINCT
        @config_version_id AS config_version_id,
        proj_id,
        check_code,
        task_id,
        evidence_basis
    FROM task_evidence
);
GO

CREATE OR ALTER FUNCTION [powerbitables].[xertoolkit_fn_out_of_sequence]
(
    @config_version_id bigint
)
RETURNS TABLE
AS
RETURN
(
    WITH relationships AS
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
                 AND
                     (r.relationship_create_date IS NULL OR r.relationship_create_date <= r.last_schedule_date)
                 AND
                     (r.relationship_update_date IS NULL OR r.relationship_update_date <= r.last_schedule_date)
                THEN 1
                ELSE 0
            END
        ) AS is_out_of_sequence,
        CASE
            WHEN r.relationship_type = 'PR_FS'
             AND succ.act_start_date IS NOT NULL
             AND pred.act_end_date IS NULL
             AND r.last_schedule_date IS NOT NULL
             AND
                 (r.relationship_create_date IS NULL OR r.relationship_create_date <= r.last_schedule_date)
             AND
                 (r.relationship_update_date IS NULL OR r.relationship_update_date <= r.last_schedule_date)
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
     AND pred.delete_session_id IS NULL
    JOIN dbo.TASK AS succ
      ON succ.proj_id = r.proj_id
     AND succ.task_id = r.successor_task_id
     AND succ.delete_session_id IS NULL
    WHERE ISNULL(scope.exclude_complete, 0) = 0
       OR succ.status_code <> 'TK_Complete'
);
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_OpenEnds]
AS
SELECT q.*
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
CROSS APPLY [powerbitables].[xertoolkit_fn_open_ends](p.active_config_version_id) AS q
WHERE p.profile_code = N'default';
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_RelationshipQuality]
AS
SELECT q.*
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
CROSS APPLY [powerbitables].[xertoolkit_fn_relationship_quality](p.active_config_version_id) AS q
WHERE p.profile_code = N'default';
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ActivityQuality]
AS
SELECT q.*
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
CROSS APPLY [powerbitables].[xertoolkit_fn_activity_quality](p.active_config_version_id) AS q
WHERE p.profile_code = N'default';
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_OutOfSequence]
AS
SELECT q.*
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
CROSS APPLY [powerbitables].[xertoolkit_fn_out_of_sequence](p.active_config_version_id) AS q
WHERE p.profile_code = N'default';
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ActivityQualityValidation]
AS
SELECT
    q.proj_id,
    q.task_id,
    a.task_code,
    a.task_name,
    a.task_type,
    a.wbs_id,
    q.wbs_name,
    a.status_code AS activity_status,
    a.act_start_date,
    a.act_end_date,
    a.early_start_date,
    a.early_end_date,
    a.late_start_date,
    a.late_end_date,
    a.target_start_date,
    a.target_end_date,
    a.remaining_duration_days,
    a.total_float_days,
    a.cstr_type AS constraint_type,
    a.cstr_type2 AS secondary_constraint_type,
    a.driving_path_flag,
    q.is_constraint,
    q.is_critical_task,
    q.is_high_duration,
    q.is_high_float,
    q.is_in_progress_error,
    q.is_invalid_date,
    q.is_near_critical_task,
    q.is_negative_float,
    q.is_riding_progress_date,
    q.config_version_id
FROM [powerbitables].[xertoolkit_vw_PBI_ActivityQuality] AS q
LEFT JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
  ON a.proj_id = q.proj_id
 AND a.task_id = q.task_id;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
AS
SELECT
    evidence.config_version_id,
    evidence.check_run_id,
    evidence.refreshed_at,
    evidence.proj_id,
    scope.check_code,
    scope.display_name AS check_name,
    scope.sort_order AS check_sort_order,
    evidence.task_id,
    evidence.task_code,
    evidence.task_name,
    activity.total_float_days,
    evidence.evidence_basis
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
  ON scope.config_version_id = evidence.config_version_id
 AND scope.check_code = evidence.check_code
JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
  ON activity.proj_id = evidence.proj_id
 AND activity.task_id = evidence.task_id;
GO

CREATE OR ALTER TRIGGER [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete]
ON [powerbitables].[xertoolkit_result_project_metrics]
AFTER DELETE
AS
BEGIN
    SET NOCOUNT ON;

    DELETE evidence
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
    JOIN deleted
      ON deleted.proj_id = evidence.proj_id;
END;
GO

CREATE OR ALTER TRIGGER [powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert]
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
        THROW 51802, 'Task evidence requires run-stamped project metrics.', 1;

    /* Defensive for direct inserts that were not preceded by the normal delete. */
    DELETE evidence
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
    JOIN inserted
      ON inserted.proj_id = evidence.proj_id;

    ;WITH refresh_configs AS
    (
        SELECT DISTINCT config_version_id
        FROM inserted
    )
    INSERT INTO [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    (
        proj_id,
        check_code,
        task_id,
        task_code,
        task_name,
        evidence_basis,
        check_run_id,
        refreshed_at,
        config_version_id
    )
    SELECT
        result.proj_id,
        evidence.check_code,
        evidence.task_id,
        activity.task_code,
        activity.task_name,
        evidence.evidence_basis,
        result.check_run_id,
        result.refreshed_at,
        result.config_version_id
    FROM refresh_configs AS config
    CROSS APPLY [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]
        (config.config_version_id) AS evidence
    JOIN inserted AS result
      ON result.proj_id = evidence.proj_id
     AND result.config_version_id = config.config_version_id
    JOIN [powerbitables].[xertoolkit_vw_PBI_Activities] AS activity
      ON activity.proj_id = evidence.proj_id
     AND activity.task_id = evidence.task_id;
END;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ProjectMetrics]
AS
SELECT
    proj_id,
    project_name,
    updated_date,
    activity_count,
    dcma_activity_count,
    relationship_count,
    relationship_ratio,
    missing_predecessor_count,
    missing_successor_count,
    open_start_count,
    open_finish_count,
    lead_count,
    lag_count,
    non_fs_count,
    excessive_ss_lag_count,
    excessive_ff_lag_count,
    high_float_count,
    negative_float_count,
    high_duration_count,
    constraint_count,
    invalid_date_count,
    in_progress_error_count,
    riding_progress_date_count,
    critical_task_count,
    near_critical_task_count,
    out_of_sequence_count,
    logical_loop_count,
    config_version_id
FROM [powerbitables].[xertoolkit_result_project_metrics];
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_schedule_quality_settings]
AS
SELECT
    p.profile_id,
    p.profile_code,
    p.profile_name,
    p.active_config_version_id,
    v.config_version_id,
    v.version_number,
    v.state,
    CONVERT(bit, CASE WHEN p.active_config_version_id = v.config_version_id THEN 1 ELSE 0 END) AS is_active,
    v.based_on_config_version_id,
    v.change_note,
    v.settings_hash,
    v.created_at,
    v.created_by,
    v.updated_at,
    v.updated_by,
    v.published_at,
    v.published_by,
    c.check_code,
    c.display_name,
    c.sort_order,
    c.is_enabled,
    c.include_loe,
    c.include_wbs_summary,
    c.include_milestones,
    c.exclude_complete
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
JOIN [powerbitables].[xertoolkit_schedule_quality_config_version] AS v
  ON v.profile_id = p.profile_id
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS c
  ON c.config_version_id = v.config_version_id;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_schedule_quality_options]
AS
SELECT
    p.profile_code,
    p.active_config_version_id,
    v.config_version_id,
    v.version_number,
    v.state,
    o.option_code,
    o.display_name,
    o.data_type,
    o.bit_value,
    o.numeric_value,
    o.text_value,
    o.unit_code,
    o.sort_order
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
JOIN [powerbitables].[xertoolkit_schedule_quality_config_version] AS v
  ON v.profile_id = p.profile_id
JOIN [powerbitables].[xertoolkit_schedule_quality_option] AS o
  ON o.config_version_id = v.config_version_id;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_schedule_quality_constraint_types]
AS
SELECT
    p.profile_code,
    p.active_config_version_id,
    v.config_version_id,
    v.version_number,
    v.state,
    c.constraint_type_code,
    c.display_name,
    c.is_checked,
    c.sort_order
FROM [powerbitables].[xertoolkit_schedule_quality_profile] AS p
JOIN [powerbitables].[xertoolkit_schedule_quality_config_version] AS v
  ON v.profile_id = p.profile_id
JOIN [powerbitables].[xertoolkit_schedule_quality_constraint_type] AS c
  ON c.config_version_id = v.config_version_id;
GO

CREATE OR ALTER PROCEDURE [powerbitables].[xertoolkit_get_or_create_schedule_quality_draft]
    @profile_code nvarchar(50),
    @changed_by nvarchar(150),
    @config_version_id bigint OUTPUT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF NULLIF(LTRIM(RTRIM(@profile_code)), N'') IS NULL
        THROW 51200, 'profile_code is required.', 1;
    IF NULLIF(LTRIM(RTRIM(@changed_by)), N'') IS NULL
        THROW 51201, 'changed_by is required.', 1;

    BEGIN TRY
        BEGIN TRANSACTION;

        DECLARE @profile_id int;
        DECLARE @active_config_version_id bigint;

        SELECT
            @profile_id = profile_id,
            @active_config_version_id = active_config_version_id
        FROM [powerbitables].[xertoolkit_schedule_quality_profile] WITH (UPDLOCK, HOLDLOCK)
        WHERE profile_code = @profile_code;

        IF @profile_id IS NULL
            THROW 51202, 'Unknown schedule-quality profile.', 1;
        IF @active_config_version_id IS NULL
            THROW 51203, 'The schedule-quality profile has no active version to copy.', 1;

        SELECT @config_version_id = config_version_id
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
        WHERE profile_id = @profile_id
          AND state = 'draft';

        IF @config_version_id IS NULL
        BEGIN
            DECLARE @next_version_number int =
            (
                SELECT ISNULL(MAX(version_number), 0) + 1
                FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
                WHERE profile_id = @profile_id
            );

            INSERT INTO [powerbitables].[xertoolkit_schedule_quality_config_version]
            (
                profile_id,
                version_number,
                state,
                based_on_config_version_id,
                change_note,
                settings_hash,
                created_by,
                updated_by
            )
            SELECT
                @profile_id,
                @next_version_number,
                'draft',
                @active_config_version_id,
                NULL,
                settings_hash,
                @changed_by,
                @changed_by
            FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
            WHERE config_version_id = @active_config_version_id;

            SET @config_version_id = SCOPE_IDENTITY();

            INSERT INTO [powerbitables].[xertoolkit_schedule_quality_check_scope]
            (
                config_version_id, check_code, display_name, sort_order, is_enabled,
                include_loe, include_wbs_summary, include_milestones, exclude_complete
            )
            SELECT
                @config_version_id, check_code, display_name, sort_order, is_enabled,
                include_loe, include_wbs_summary, include_milestones, exclude_complete
            FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
            WHERE config_version_id = @active_config_version_id;

            INSERT INTO [powerbitables].[xertoolkit_schedule_quality_option]
            (
                config_version_id, option_code, display_name, data_type,
                bit_value, numeric_value, text_value, unit_code, sort_order
            )
            SELECT
                @config_version_id, option_code, display_name, data_type,
                bit_value, numeric_value, text_value, unit_code, sort_order
            FROM [powerbitables].[xertoolkit_schedule_quality_option]
            WHERE config_version_id = @active_config_version_id;

            INSERT INTO [powerbitables].[xertoolkit_schedule_quality_constraint_type]
            (
                config_version_id, constraint_type_code, display_name, is_checked, sort_order
            )
            SELECT
                @config_version_id, constraint_type_code, display_name, is_checked, sort_order
            FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
            WHERE config_version_id = @active_config_version_id;
        END;

        COMMIT TRANSACTION;

        SELECT
            @config_version_id AS config_version_id,
            version_number,
            state,
            based_on_config_version_id,
            settings_hash
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
        WHERE config_version_id = @config_version_id;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE [powerbitables].[xertoolkit_save_schedule_quality_draft]
    @config_version_id bigint,
    @settings_json nvarchar(max),
    @expected_settings_hash char(64),
    @changed_by nvarchar(150),
    @change_note nvarchar(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF NULLIF(LTRIM(RTRIM(@changed_by)), N'') IS NULL
        THROW 51210, 'changed_by is required.', 1;
    IF @expected_settings_hash IS NULL
       OR LEN(RTRIM(@expected_settings_hash)) <> 64
       OR @expected_settings_hash LIKE '%[^0-9A-Fa-f]%'
        THROW 51222, 'expected_settings_hash must be the 64-character hash last read by the editor.', 1;
    IF ISJSON(@settings_json) <> 1
        THROW 51211, 'settings_json must be valid JSON.', 1;
    IF JSON_QUERY(@settings_json, '$.checks') IS NULL
       OR JSON_QUERY(@settings_json, '$.options') IS NULL
       OR JSON_QUERY(@settings_json, '$.constraint_types') IS NULL
        THROW 51212, 'settings_json requires checks, options, and constraint_types arrays.', 1;

    DECLARE @checks TABLE
    (
        check_code varchar(50) NOT NULL PRIMARY KEY,
        is_enabled bit NULL,
        include_loe bit NULL,
        include_wbs_summary bit NULL,
        include_milestones bit NULL,
        exclude_complete bit NULL
    );
    DECLARE @options TABLE
    (
        option_code varchar(50) NOT NULL PRIMARY KEY,
        bit_value bit NULL,
        numeric_value decimal(18,4) NULL,
        text_value nvarchar(250) NULL
    );
    DECLARE @constraints TABLE
    (
        constraint_type_code varchar(20) NOT NULL PRIMARY KEY,
        is_checked bit NULL
    );

    INSERT INTO @checks
    (
        check_code, is_enabled, include_loe, include_wbs_summary,
        include_milestones, exclude_complete
    )
    SELECT
        check_code, is_enabled, include_loe, include_wbs_summary,
        include_milestones, exclude_complete
    FROM OPENJSON(JSON_QUERY(@settings_json, '$.checks'))
    WITH
    (
        check_code varchar(50) '$.check_code',
        is_enabled bit '$.is_enabled',
        include_loe bit '$.include_loe',
        include_wbs_summary bit '$.include_wbs_summary',
        include_milestones bit '$.include_milestones',
        exclude_complete bit '$.exclude_complete'
    );

    INSERT INTO @options (option_code, bit_value, numeric_value, text_value)
    SELECT option_code, bit_value, numeric_value, text_value
    FROM OPENJSON(JSON_QUERY(@settings_json, '$.options'))
    WITH
    (
        option_code varchar(50) '$.option_code',
        bit_value bit '$.bit_value',
        numeric_value decimal(18,4) '$.numeric_value',
        text_value nvarchar(250) '$.text_value'
    );

    INSERT INTO @constraints (constraint_type_code, is_checked)
    SELECT constraint_type_code, is_checked
    FROM OPENJSON(JSON_QUERY(@settings_json, '$.constraint_types'))
    WITH
    (
        constraint_type_code varchar(20) '$.constraint_type_code',
        is_checked bit '$.is_checked'
    );

    BEGIN TRY
        BEGIN TRANSACTION;

        DECLARE @current_draft_state varchar(12);
        DECLARE @current_settings_hash char(64);

        SELECT
            @current_draft_state = state,
            @current_settings_hash = settings_hash
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version] WITH (UPDLOCK, HOLDLOCK)
        WHERE config_version_id = @config_version_id;

        IF @current_draft_state IS NULL OR @current_draft_state <> 'draft'
            THROW 51213, 'Only a draft configuration version can be saved.', 1;

        IF @current_settings_hash IS NULL OR UPPER(@current_settings_hash) <> UPPER(@expected_settings_hash)
            THROW 51223, 'The draft changed after it was loaded. Reload it before saving.', 1;

        IF EXISTS (SELECT 1 FROM @checks WHERE is_enabled IS NULL)
            THROW 51214, 'Every check requires is_enabled.', 1;
        IF EXISTS (SELECT 1 FROM @constraints WHERE is_checked IS NULL)
            THROW 51215, 'Every constraint type requires is_checked.', 1;

        IF (SELECT COUNT(*) FROM @checks) <>
           (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_schedule_quality_check_scope] WHERE config_version_id = @config_version_id)
           OR EXISTS
           (
               SELECT check_code FROM @checks
               EXCEPT
               SELECT check_code
               FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
               WHERE config_version_id = @config_version_id
           )
           OR EXISTS
           (
               SELECT check_code
               FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
               WHERE config_version_id = @config_version_id
               EXCEPT
               SELECT check_code FROM @checks
           )
            THROW 51216, 'checks must contain every known check exactly once and no unknown checks.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM [powerbitables].[xertoolkit_schedule_quality_check_scope] AS existing
            JOIN @checks AS supplied
              ON supplied.check_code = existing.check_code
            WHERE existing.config_version_id = @config_version_id
              AND
              (
                  (CASE WHEN existing.include_loe IS NULL THEN 1 ELSE 0 END)
                    <> (CASE WHEN supplied.include_loe IS NULL THEN 1 ELSE 0 END)
                  OR (CASE WHEN existing.include_wbs_summary IS NULL THEN 1 ELSE 0 END)
                    <> (CASE WHEN supplied.include_wbs_summary IS NULL THEN 1 ELSE 0 END)
                  OR (CASE WHEN existing.include_milestones IS NULL THEN 1 ELSE 0 END)
                    <> (CASE WHEN supplied.include_milestones IS NULL THEN 1 ELSE 0 END)
                  OR (CASE WHEN existing.exclude_complete IS NULL THEN 1 ELSE 0 END)
                    <> (CASE WHEN supplied.exclude_complete IS NULL THEN 1 ELSE 0 END)
              )
        )
            THROW 51217, 'N/A scope fields must remain NULL; explicit No must be sent as false/0.', 1;

        IF (SELECT COUNT(*) FROM @options) <>
           (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_schedule_quality_option] WHERE config_version_id = @config_version_id)
           OR EXISTS
           (
               SELECT option_code FROM @options
               EXCEPT
               SELECT option_code
               FROM [powerbitables].[xertoolkit_schedule_quality_option]
               WHERE config_version_id = @config_version_id
           )
           OR EXISTS
           (
               SELECT option_code
               FROM [powerbitables].[xertoolkit_schedule_quality_option]
               WHERE config_version_id = @config_version_id
               EXCEPT
               SELECT option_code FROM @options
           )
            THROW 51218, 'options must contain every known option exactly once and no unknown options.', 1;

        IF EXISTS
        (
            SELECT 1
            FROM [powerbitables].[xertoolkit_schedule_quality_option] AS existing
            JOIN @options AS supplied
              ON supplied.option_code = existing.option_code
            WHERE existing.config_version_id = @config_version_id
              AND
              (
                  (existing.data_type = 'bit'
                   AND (supplied.bit_value IS NULL OR supplied.numeric_value IS NOT NULL OR supplied.text_value IS NOT NULL))
                  OR (existing.data_type IN ('integer', 'decimal')
                   AND (supplied.bit_value IS NOT NULL OR supplied.numeric_value IS NULL OR supplied.text_value IS NOT NULL))
                  OR (existing.data_type = 'text'
                   AND (supplied.bit_value IS NOT NULL OR supplied.numeric_value IS NOT NULL OR supplied.text_value IS NULL))
                  OR (existing.data_type = 'integer' AND supplied.numeric_value <> FLOOR(supplied.numeric_value))
              )
        )
            THROW 51219, 'An option value does not match its declared data type.', 1;

        IF EXISTS
        (
            SELECT 1 FROM @options
            WHERE (option_code IN ('high_float_days', 'high_duration_days', 'near_critical_upper_days') AND numeric_value < 0)
               OR (option_code = 'negative_float_days' AND (numeric_value < -100000 OR numeric_value > 100000))
               OR (option_code = 'riding_days_after_data_date' AND (numeric_value < 0 OR numeric_value > 3650))
               OR (option_code IN ('excessive_ss_percent', 'excessive_ff_percent') AND (numeric_value < 0 OR numeric_value > 100))
        )
            THROW 51220, 'One or more numeric options are outside their safe range.', 1;

        IF (SELECT COUNT(*) FROM @constraints) <>
           (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type] WHERE config_version_id = @config_version_id)
           OR EXISTS
           (
               SELECT constraint_type_code FROM @constraints
               EXCEPT
               SELECT constraint_type_code
               FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
               WHERE config_version_id = @config_version_id
           )
           OR EXISTS
           (
               SELECT constraint_type_code
               FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
               WHERE config_version_id = @config_version_id
               EXCEPT
               SELECT constraint_type_code FROM @constraints
           )
            THROW 51221, 'constraint_types must contain every known type exactly once and no unknown types.', 1;

        UPDATE target
        SET
            is_enabled = source.is_enabled,
            include_loe = source.include_loe,
            include_wbs_summary = source.include_wbs_summary,
            include_milestones = source.include_milestones,
            exclude_complete = source.exclude_complete
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope] AS target
        JOIN @checks AS source
          ON source.check_code = target.check_code
        WHERE target.config_version_id = @config_version_id;

        UPDATE target
        SET
            bit_value = source.bit_value,
            numeric_value = source.numeric_value,
            text_value = source.text_value
        FROM [powerbitables].[xertoolkit_schedule_quality_option] AS target
        JOIN @options AS source
          ON source.option_code = target.option_code
        WHERE target.config_version_id = @config_version_id;

        UPDATE target
        SET is_checked = source.is_checked
        FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type] AS target
        JOIN @constraints AS source
          ON source.constraint_type_code = target.constraint_type_code
        WHERE target.config_version_id = @config_version_id;

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
        DECLARE @settings_hash char(64) = CONVERT
        (
            char(64),
            HASHBYTES
            (
                'SHA2_256',
                CONVERT(varbinary(max), CONCAT(@checks_json, N'|', @options_json, N'|', @constraints_json))
            ),
            2
        );

        UPDATE [powerbitables].[xertoolkit_schedule_quality_config_version]
        SET
            change_note = NULLIF(LTRIM(RTRIM(@change_note)), N''),
            settings_hash = @settings_hash,
            updated_at = SYSUTCDATETIME(),
            updated_by = @changed_by
        WHERE config_version_id = @config_version_id;

        COMMIT TRANSACTION;

        SELECT
            config_version_id,
            version_number,
            state,
            settings_hash,
            updated_at,
            updated_by,
            change_note
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
        WHERE config_version_id = @config_version_id;
    END TRY
    BEGIN CATCH
        IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE [powerbitables].[xertoolkit_refresh_all_schedule_quality]
    @proj_id int = NULL,
    @config_version_id bigint = NULL,
    @expected_settings_hash char(64) = NULL,
    @activate_config bit = 0,
    @published_by nvarchar(150) = NULL,
    @trigger_type varchar(20) = 'scheduled'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @check_run_id bigint = NULL;
    DECLARE @processed_project_count int = 0;
    DECLARE @logic_loop_task_count int = 0;
    DECLARE @error_message nvarchar(max);
    DECLARE @profile_id int;
    DECLARE @version_state varchar(12);
    DECLARE @current_settings_hash char(64);
    DECLARE @lock_result int;
    DECLARE @lock_acquired bit = 0;

    SET @trigger_type = LOWER(COALESCE(NULLIF(LTRIM(RTRIM(@trigger_type)), ''), 'scheduled'));

    IF @trigger_type NOT IN ('scheduled', 'manual', 'publish', 'canary')
        THROW 51300, 'trigger_type must be scheduled, manual, publish, or canary.', 1;
    IF @activate_config = 1 AND @proj_id IS NOT NULL
        THROW 51301, 'Publishing a configuration requires an all-project rebuild.', 1;
    IF @activate_config = 1 AND NULLIF(LTRIM(RTRIM(@published_by)), N'') IS NULL
        THROW 51302, 'published_by is required when activating a configuration.', 1;
    IF @activate_config = 1
       AND
       (
           @expected_settings_hash IS NULL
           OR LEN(RTRIM(@expected_settings_hash)) <> 64
           OR @expected_settings_hash LIKE '%[^0-9A-Fa-f]%'
       )
        THROW 51313, 'expected_settings_hash is required when activating a configuration.', 1;

    EXEC @lock_result = sys.sp_getapplock
        @Resource = N'powerbitables.xertoolkit.schedule_quality.refresh',
        @LockMode = 'Exclusive',
        @LockOwner = 'Session',
        @LockTimeout = 0;

    IF @lock_result < 0
        THROW 51303, 'Another schedule-quality refresh or publish is already running.', 1;

    SET @lock_acquired = 1;

    BEGIN TRY
        SELECT
            @profile_id = profile_id,
            @config_version_id = COALESCE(@config_version_id, active_config_version_id)
        FROM [powerbitables].[xertoolkit_schedule_quality_profile]
        WHERE profile_code = N'default';

        IF @profile_id IS NULL OR @config_version_id IS NULL
            THROW 51304, 'The default schedule-quality profile has no active configuration.', 1;

        SELECT @version_state = state
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
        WHERE config_version_id = @config_version_id
          AND profile_id = @profile_id;

        IF @version_state IS NULL
            THROW 51305, 'The requested configuration version does not belong to the default profile.', 1;
        IF @activate_config = 1 AND @version_state <> 'draft'
            THROW 51306, 'Only a draft configuration can be published.', 1;
        IF @activate_config = 0 AND @version_state <> 'active'
            THROW 51307, 'An ordinary refresh can use only the active configuration.', 1;

        CREATE TABLE #TargetProjects
        (
            proj_id int NOT NULL PRIMARY KEY
        );

        IF @proj_id IS NULL
        BEGIN
            INSERT INTO #TargetProjects (proj_id)
            SELECT p.proj_id
            FROM dbo.PROJECT AS p;
        END
        ELSE
        BEGIN
            INSERT INTO #TargetProjects (proj_id)
            SELECT p.proj_id
            FROM dbo.PROJECT AS p
            WHERE p.proj_id = @proj_id;
        END;

        SELECT @processed_project_count = COUNT(*) FROM #TargetProjects;

        IF @proj_id IS NOT NULL AND @processed_project_count = 0
            THROW 51308, 'The requested P6 project was not found.', 1;

        INSERT INTO [powerbitables].[xertoolkit_refresh_run_history]
        (
            requested_proj_id,
            status,
            trigger_type,
            config_version_id
        )
        VALUES
        (
            @proj_id,
            'running',
            @trigger_type,
            @config_version_id
        );

        SET @check_run_id = SCOPE_IDENTITY();

        BEGIN TRANSACTION;

        /*
           Hold the version row for the duration of staging. This blocks a
           concurrent draft save while a publish is calculating, without
           changing the externally visible active pointer.
        */
        SELECT
            @version_state = state,
            @current_settings_hash = settings_hash
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version] WITH (UPDLOCK, HOLDLOCK)
        WHERE config_version_id = @config_version_id
          AND profile_id = @profile_id;

        IF @activate_config = 1 AND @version_state <> 'draft'
            THROW 51309, 'The draft changed state before staging began.', 1;
        IF @activate_config = 1
           AND
           (
               @current_settings_hash IS NULL
               OR UPPER(@current_settings_hash) <> UPPER(@expected_settings_hash)
           )
            THROW 51314, 'The draft changed after it was loaded. Reload it before publishing.', 1;
        IF @activate_config = 0 AND @version_state <> 'active'
            THROW 51310, 'The active configuration changed before staging began.', 1;

        IF (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
            WHERE config_version_id = @config_version_id) <> 20
           OR (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_schedule_quality_option]
               WHERE config_version_id = @config_version_id) <> 12
           OR (SELECT COUNT(*) FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
               WHERE config_version_id = @config_version_id) <> 10
            THROW 51311, 'The configuration is incomplete and cannot be refreshed.', 1;

        SELECT TOP (0) *
        INTO #LogicLoopStage
        FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];

        SELECT TOP (0) *
        INTO #ProjectMetricsStage
        FROM [powerbitables].[xertoolkit_result_project_metrics];

        CREATE UNIQUE CLUSTERED INDEX [IX_ProjectMetricsStage_proj_id]
            ON #ProjectMetricsStage (proj_id);

        DECLARE @logical_loops_enabled bit =
        (
            SELECT is_enabled
            FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
            WHERE config_version_id = @config_version_id
              AND check_code = 'logical_loops'
        );

        IF @logical_loops_enabled = 1
        BEGIN
            CREATE TABLE #CycleEdges
            (
                proj_id int NOT NULL,
                predecessor_task_id int NOT NULL,
                successor_task_id int NOT NULL,
                PRIMARY KEY (proj_id, predecessor_task_id, successor_task_id)
            );

            INSERT INTO #CycleEdges (proj_id, predecessor_task_id, successor_task_id)
            SELECT DISTINCT
                tp.proj_id,
                tpred.pred_task_id,
                tpred.task_id
            FROM dbo.TASKPRED AS tpred
            JOIN #TargetProjects AS tp
              ON tp.proj_id = tpred.proj_id
            WHERE tpred.pred_task_id IS NOT NULL
              AND tpred.task_id IS NOT NULL;

            INSERT INTO #LogicLoopStage
            (
                proj_id,
                task_id,
                loop_path,
                loop_length,
                calculated_date,
                check_run_id,
                calculation_method,
                config_version_id
            )
            SELECT
                ce.proj_id,
                ce.predecessor_task_id,
                '|' + CAST(ce.predecessor_task_id AS varchar(50))
                    + '|' + CAST(ce.successor_task_id AS varchar(50)) + '|',
                1,
                SYSUTCDATETIME(),
                @check_run_id,
                'self_loop',
                @config_version_id
            FROM #CycleEdges AS ce
            WHERE ce.predecessor_task_id = ce.successor_task_id;

            DELETE FROM #CycleEdges
            WHERE predecessor_task_id = successor_task_id;

            CREATE NONCLUSTERED INDEX [IX_CycleEdges_incoming]
                ON #CycleEdges (proj_id, successor_task_id, predecessor_task_id);

            CREATE TABLE #CycleNodes
            (
                proj_id int NOT NULL,
                task_id int NOT NULL,
                PRIMARY KEY (proj_id, task_id)
            );

            INSERT INTO #CycleNodes (proj_id, task_id)
            SELECT proj_id, predecessor_task_id FROM #CycleEdges
            UNION
            SELECT proj_id, successor_task_id FROM #CycleEdges;

            DECLARE @removed int = 1;

            CREATE TABLE #PruneNodes
            (
                proj_id int NOT NULL,
                task_id int NOT NULL,
                PRIMARY KEY (proj_id, task_id)
            );

            WHILE @removed > 0
            BEGIN
                TRUNCATE TABLE #PruneNodes;

                INSERT INTO #PruneNodes (proj_id, task_id)
                SELECT n.proj_id, n.task_id
                FROM #CycleNodes AS n
                WHERE NOT EXISTS
                (
                    SELECT 1
                    FROM #CycleEdges AS incoming
                    WHERE incoming.proj_id = n.proj_id
                      AND incoming.successor_task_id = n.task_id
                )
                   OR NOT EXISTS
                (
                    SELECT 1
                    FROM #CycleEdges AS outgoing
                    WHERE outgoing.proj_id = n.proj_id
                      AND outgoing.predecessor_task_id = n.task_id
                );

                SET @removed = @@ROWCOUNT;

                IF @removed > 0
                BEGIN
                    DELETE e
                    FROM #CycleEdges AS e
                    JOIN #PruneNodes AS p
                      ON p.proj_id = e.proj_id
                     AND p.task_id = e.predecessor_task_id;

                    DELETE e
                    FROM #CycleEdges AS e
                    JOIN #PruneNodes AS p
                      ON p.proj_id = e.proj_id
                     AND p.task_id = e.successor_task_id;

                    DELETE n
                    FROM #CycleNodes AS n
                    JOIN #PruneNodes AS p
                     ON p.proj_id = n.proj_id
                     AND p.task_id = n.task_id;
                END;
            END;

            INSERT INTO #LogicLoopStage
            (
                proj_id,
                task_id,
                loop_path,
                loop_length,
                calculated_date,
                check_run_id,
                calculation_method,
                config_version_id
            )
            SELECT
                n.proj_id,
                n.task_id,
                'cycle_core:' + CAST(n.task_id AS varchar(50)),
                NULL,
                SYSUTCDATETIME(),
                @check_run_id,
                'cycle_core_prune',
                @config_version_id
            FROM #CycleNodes AS n
            WHERE EXISTS
            (
                SELECT 1
                FROM #CycleEdges AS e
                WHERE e.proj_id = n.proj_id
                  AND
                  (
                      e.predecessor_task_id = n.task_id
                      OR e.successor_task_id = n.task_id
                  )
            );
        END;

        SELECT @logic_loop_task_count = COUNT(*) FROM #LogicLoopStage;

        /*
           The optional out-of-sequence detail trigger derives and commits the
           headline count from its materialised detail rows. When that exact
           trigger is enabled, calculating the same function here is redundant.
        */
        DECLARE @oos_detail_trigger_enabled bit =
        (
            SELECT CONVERT
            (
                bit,
                CASE WHEN EXISTS
                (
                    SELECT 1
                    FROM sys.triggers AS trigger_metadata
                    WHERE trigger_metadata.parent_id = OBJECT_ID
                        (N'[powerbitables].[xertoolkit_result_project_metrics]')
                      AND trigger_metadata.name = N'xertoolkit_trg_project_metrics_oos_insert'
                      AND trigger_metadata.is_disabled = 0
                      AND OBJECT_DEFINITION(trigger_metadata.object_id)
                          LIKE N'%SET out_of_sequence_count = counts.out_of_sequence_count%'
                ) THEN 1 ELSE 0 END
            )
        );

        /* SPLIT_STAGE_PROJECT_METRICS_V1 */
        /*
           Materialise each aggregate family independently. Combining every
           inline TVF into one statement can exhaust the optimizer's search
           budget and produce a per-project nested-loop plan. These result sets
           contain at most one row per target project, so the final join stays
           small and predictable for both canary and all-project refreshes.
        */
        SELECT
            a.proj_id,
            COUNT(*) AS activity_count
        INTO #ActivityCounts
        FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
        JOIN #TargetProjects AS tp
          ON tp.proj_id = a.proj_id
        GROUP BY a.proj_id
        OPTION (RECOMPILE);

        CREATE UNIQUE CLUSTERED INDEX [IX_ActivityCounts_proj_id]
            ON #ActivityCounts (proj_id);

        SELECT
            s.proj_id,
            COUNT(*) AS scoped_activity_count
        INTO #ScopedActivityCounts
        FROM [powerbitables].[xertoolkit_fn_activity_in_scope]
            (@config_version_id, 'relationship_ratio') AS s
        JOIN #TargetProjects AS tp
          ON tp.proj_id = s.proj_id
        GROUP BY s.proj_id
        OPTION (RECOMPILE);

        CREATE UNIQUE CLUSTERED INDEX [IX_ScopedActivityCounts_proj_id]
            ON #ScopedActivityCounts (proj_id);

        SELECT
            s.proj_id,
            COUNT(*) AS relationship_count
        INTO #RelationshipCounts
        FROM [powerbitables].[xertoolkit_fn_relationship_in_scope]
            (@config_version_id, 'relationship_ratio') AS s
        JOIN #TargetProjects AS tp
          ON tp.proj_id = s.proj_id
        GROUP BY s.proj_id
        OPTION (RECOMPILE);

        CREATE UNIQUE CLUSTERED INDEX [IX_RelationshipCounts_proj_id]
            ON #RelationshipCounts (proj_id);

        SELECT
            o.proj_id,
            SUM(o.is_missing_predecessor) AS missing_predecessor_count,
            SUM(o.is_missing_successor) AS missing_successor_count,
            SUM(o.is_open_start) AS open_start_count,
            SUM(o.is_open_finish) AS open_finish_count
        INTO #OpenEndCounts
        FROM [powerbitables].[xertoolkit_fn_open_ends](@config_version_id) AS o
        JOIN #TargetProjects AS tp
          ON tp.proj_id = o.proj_id
        GROUP BY o.proj_id
        OPTION (RECOMPILE);

        CREATE UNIQUE CLUSTERED INDEX [IX_OpenEndCounts_proj_id]
            ON #OpenEndCounts (proj_id);

        SELECT
            rq.proj_id,
            SUM(rq.is_lead) AS lead_count,
            SUM(rq.is_lag) AS lag_count,
            SUM(rq.is_non_fs) AS non_fs_count,
            SUM(rq.is_excessive_ss_lag) AS excessive_ss_lag_count,
            SUM(rq.is_excessive_ff_lag) AS excessive_ff_lag_count
        INTO #RelationshipQualityCounts
        FROM [powerbitables].[xertoolkit_fn_relationship_quality](@config_version_id) AS rq
        JOIN #TargetProjects AS tp
          ON tp.proj_id = rq.proj_id
        GROUP BY rq.proj_id
        OPTION (RECOMPILE);

        CREATE UNIQUE CLUSTERED INDEX [IX_RelationshipQualityCounts_proj_id]
            ON #RelationshipQualityCounts (proj_id);

        SELECT
            aq.proj_id,
            SUM(aq.is_high_float) AS high_float_count,
            SUM(aq.is_negative_float) AS negative_float_count,
            SUM(aq.is_high_duration) AS high_duration_count,
            SUM(aq.is_constraint) AS constraint_count,
            SUM(aq.is_invalid_date) AS invalid_date_count,
            SUM(aq.is_in_progress_error) AS in_progress_error_count,
            SUM(aq.is_riding_progress_date) AS riding_progress_date_count,
            SUM(aq.is_critical_task) AS critical_task_count,
            SUM(aq.is_near_critical_task) AS near_critical_task_count
        INTO #ActivityQualityCounts
        FROM [powerbitables].[xertoolkit_fn_activity_quality](@config_version_id) AS aq
        JOIN #TargetProjects AS tp
          ON tp.proj_id = aq.proj_id
        GROUP BY aq.proj_id
        OPTION (RECOMPILE);

        CREATE UNIQUE CLUSTERED INDEX [IX_ActivityQualityCounts_proj_id]
            ON #ActivityQualityCounts (proj_id);

        CREATE TABLE #OutOfSequenceCounts
        (
            proj_id int NOT NULL PRIMARY KEY,
            out_of_sequence_count int NOT NULL
        );

        IF @oos_detail_trigger_enabled = 0
        BEGIN
            INSERT INTO #OutOfSequenceCounts
            (
                proj_id,
                out_of_sequence_count
            )
            SELECT
                oos.proj_id,
                COUNT(DISTINCT CASE WHEN oos.is_out_of_sequence = 1 THEN oos.successor_task_id END)
            FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@config_version_id) AS oos
            JOIN #TargetProjects AS tp
              ON tp.proj_id = oos.proj_id
            GROUP BY oos.proj_id
            OPTION (RECOMPILE);
        END;

        SELECT
            ll.proj_id,
            COUNT(DISTINCT ll.task_id) AS logical_loop_count
        INTO #LogicLoopCounts
        FROM #LogicLoopStage AS ll
        GROUP BY ll.proj_id;

        CREATE UNIQUE CLUSTERED INDEX [IX_LogicLoopCounts_proj_id]
            ON #LogicLoopCounts (proj_id);

        INSERT INTO #ProjectMetricsStage
        (
            proj_id,
            project_name,
            updated_date,
            activity_count,
            dcma_activity_count,
            relationship_count,
            relationship_ratio,
            missing_predecessor_count,
            missing_successor_count,
            open_start_count,
            open_finish_count,
            lead_count,
            lag_count,
            non_fs_count,
            excessive_ss_lag_count,
            excessive_ff_lag_count,
            high_float_count,
            negative_float_count,
            high_duration_count,
            constraint_count,
            invalid_date_count,
            in_progress_error_count,
            riding_progress_date_count,
            critical_task_count,
            near_critical_task_count,
            out_of_sequence_count,
            logical_loop_count,
            check_run_id,
            refreshed_at,
            config_version_id
        )
        SELECT
            p.proj_id,
            COALESCE(NULLIF(p.[Project Name], ''), p.proj_short_name),
            p.data_date,
            ISNULL(ac.activity_count, 0),
            ISNULL(sac.scoped_activity_count, 0),
            ISNULL(rc.relationship_count, 0),
            CAST
            (
                ISNULL(rc.relationship_count, 0) * 1.0
                / NULLIF(ISNULL(sac.scoped_activity_count, 0), 0)
                AS decimal(18,2)
            ),
            ISNULL(oec.missing_predecessor_count, 0),
            ISNULL(oec.missing_successor_count, 0),
            ISNULL(oec.open_start_count, 0),
            ISNULL(oec.open_finish_count, 0),
            ISNULL(rqc.lead_count, 0),
            ISNULL(rqc.lag_count, 0),
            ISNULL(rqc.non_fs_count, 0),
            ISNULL(rqc.excessive_ss_lag_count, 0),
            ISNULL(rqc.excessive_ff_lag_count, 0),
            ISNULL(aqc.high_float_count, 0),
            ISNULL(aqc.negative_float_count, 0),
            ISNULL(aqc.high_duration_count, 0),
            ISNULL(aqc.constraint_count, 0),
            ISNULL(aqc.invalid_date_count, 0),
            ISNULL(aqc.in_progress_error_count, 0),
            ISNULL(aqc.riding_progress_date_count, 0),
            ISNULL(aqc.critical_task_count, 0),
            ISNULL(aqc.near_critical_task_count, 0),
            ISNULL(oos.out_of_sequence_count, 0),
            ISNULL(ll.logical_loop_count, 0),
            @check_run_id,
            SYSUTCDATETIME(),
            @config_version_id
        FROM [powerbitables].[xertoolkit_vw_PBI_Projects] AS p
        JOIN #TargetProjects AS tp
          ON tp.proj_id = p.proj_id
        LEFT JOIN #ActivityCounts AS ac
          ON ac.proj_id = p.proj_id
        LEFT JOIN #ScopedActivityCounts AS sac
          ON sac.proj_id = p.proj_id
        LEFT JOIN #RelationshipCounts AS rc
          ON rc.proj_id = p.proj_id
        LEFT JOIN #OpenEndCounts AS oec
          ON oec.proj_id = p.proj_id
        LEFT JOIN #RelationshipQualityCounts AS rqc
          ON rqc.proj_id = p.proj_id
        LEFT JOIN #ActivityQualityCounts AS aqc
          ON aqc.proj_id = p.proj_id
        LEFT JOIN #OutOfSequenceCounts AS oos
          ON oos.proj_id = p.proj_id
        LEFT JOIN #LogicLoopCounts AS ll
          ON ll.proj_id = p.proj_id
        OPTION (RECOMPILE);

        /* Nothing visible changes before this final transactional swap. */
        IF @proj_id IS NULL
        BEGIN
            DELETE FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];
            DELETE FROM [powerbitables].[xertoolkit_result_project_metrics];
        END
        ELSE
        BEGIN
            DELETE current_loops
            FROM [powerbitables].[xertoolkit_result_logic_loop_tasks] AS current_loops
            JOIN #TargetProjects AS target
              ON target.proj_id = current_loops.proj_id;

            DELETE current_metrics
            FROM [powerbitables].[xertoolkit_result_project_metrics] AS current_metrics
            JOIN #TargetProjects AS target
              ON target.proj_id = current_metrics.proj_id;
        END;

        INSERT INTO [powerbitables].[xertoolkit_result_logic_loop_tasks]
        (
            proj_id,
            task_id,
            loop_path,
            loop_length,
            calculated_date,
            check_run_id,
            calculation_method,
            config_version_id
        )
        SELECT
            proj_id,
            task_id,
            loop_path,
            loop_length,
            calculated_date,
            check_run_id,
            calculation_method,
            config_version_id
        FROM #LogicLoopStage;

        INSERT INTO [powerbitables].[xertoolkit_result_project_metrics]
        (
            proj_id,
            project_name,
            updated_date,
            activity_count,
            dcma_activity_count,
            relationship_count,
            relationship_ratio,
            missing_predecessor_count,
            missing_successor_count,
            open_start_count,
            open_finish_count,
            lead_count,
            lag_count,
            non_fs_count,
            excessive_ss_lag_count,
            excessive_ff_lag_count,
            high_float_count,
            negative_float_count,
            high_duration_count,
            constraint_count,
            invalid_date_count,
            in_progress_error_count,
            riding_progress_date_count,
            critical_task_count,
            near_critical_task_count,
            out_of_sequence_count,
            logical_loop_count,
            check_run_id,
            refreshed_at,
            config_version_id
        )
        SELECT
            proj_id,
            project_name,
            updated_date,
            activity_count,
            dcma_activity_count,
            relationship_count,
            relationship_ratio,
            missing_predecessor_count,
            missing_successor_count,
            open_start_count,
            open_finish_count,
            lead_count,
            lag_count,
            non_fs_count,
            excessive_ss_lag_count,
            excessive_ff_lag_count,
            high_float_count,
            negative_float_count,
            high_duration_count,
            constraint_count,
            invalid_date_count,
            in_progress_error_count,
            riding_progress_date_count,
            critical_task_count,
            near_critical_task_count,
            out_of_sequence_count,
            logical_loop_count,
            check_run_id,
            refreshed_at,
            config_version_id
        FROM #ProjectMetricsStage;

        IF @activate_config = 1
        BEGIN
            UPDATE [powerbitables].[xertoolkit_schedule_quality_config_version]
            SET state = 'superseded'
            WHERE profile_id = @profile_id
              AND state = 'active';

            UPDATE [powerbitables].[xertoolkit_schedule_quality_config_version]
            SET
                state = 'active',
                published_at = SYSUTCDATETIME(),
                published_by = @published_by,
                updated_at = SYSUTCDATETIME(),
                updated_by = @published_by
            WHERE config_version_id = @config_version_id
              AND state = 'draft';

            IF @@ROWCOUNT <> 1
                THROW 51312, 'The draft could not be activated.', 1;

            UPDATE [powerbitables].[xertoolkit_schedule_quality_profile]
            SET active_config_version_id = @config_version_id
            WHERE profile_id = @profile_id;

            DECLARE @published_thresholds TABLE
            (
                setting_name nvarchar(100) NOT NULL PRIMARY KEY,
                setting_value decimal(18,4) NOT NULL
            );

            INSERT INTO @published_thresholds (setting_name, setting_value)
            SELECT N'High Duration', numeric_value
            FROM [powerbitables].[xertoolkit_schedule_quality_option]
            WHERE config_version_id = @config_version_id
              AND option_code = 'high_duration_days'
            UNION ALL
            SELECT N'High Float', numeric_value
            FROM [powerbitables].[xertoolkit_schedule_quality_option]
            WHERE config_version_id = @config_version_id
              AND option_code = 'high_float_days'
            UNION ALL
            SELECT N'Critical Float', CONVERT(decimal(18,4), 0)
            UNION ALL
            SELECT N'Near Critical', numeric_value
            FROM [powerbitables].[xertoolkit_schedule_quality_option]
            WHERE config_version_id = @config_version_id
              AND option_code = 'near_critical_upper_days';

            UPDATE legacy
            SET setting_value = source.setting_value
            FROM [powerbitables].[xertoolkit_settings_thresholds] AS legacy
            JOIN @published_thresholds AS source
              ON source.setting_name = legacy.setting_name;

            INSERT INTO [powerbitables].[xertoolkit_settings_thresholds]
                (setting_name, setting_value)
            SELECT source.setting_name, source.setting_value
            FROM @published_thresholds AS source
            WHERE NOT EXISTS
            (
                SELECT 1
                FROM [powerbitables].[xertoolkit_settings_thresholds] AS legacy
                WHERE legacy.setting_name = source.setting_name
            );
        END;

        UPDATE [powerbitables].[xertoolkit_refresh_run_history]
        SET
            completed_at = SYSUTCDATETIME(),
            status = 'success',
            processed_project_count = @processed_project_count,
            logic_loop_task_count = @logic_loop_task_count,
            error_message = NULL,
            trigger_type = @trigger_type,
            config_version_id = @config_version_id
        WHERE check_run_id = @check_run_id;

        COMMIT TRANSACTION;

        EXEC sys.sp_releaseapplock
            @Resource = N'powerbitables.xertoolkit.schedule_quality.refresh',
            @LockOwner = 'Session';
        SET @lock_acquired = 0;

        SELECT
            @check_run_id AS check_run_id,
            'success' AS status,
            @processed_project_count AS processed_project_count,
            @logic_loop_task_count AS logic_loop_task_count,
            @config_version_id AS config_version_id,
            @activate_config AS configuration_activated;
    END TRY
    BEGIN CATCH
        SET @error_message = ERROR_MESSAGE();

        IF XACT_STATE() <> 0
            ROLLBACK TRANSACTION;

        IF @check_run_id IS NOT NULL
        BEGIN
            UPDATE [powerbitables].[xertoolkit_refresh_run_history]
            SET
                completed_at = SYSUTCDATETIME(),
                status = 'failed',
                processed_project_count = @processed_project_count,
                logic_loop_task_count = @logic_loop_task_count,
                error_message = @error_message,
                trigger_type = @trigger_type,
                config_version_id = @config_version_id
            WHERE check_run_id = @check_run_id;
        END;

        IF @lock_acquired = 1
        BEGIN
            EXEC sys.sp_releaseapplock
                @Resource = N'powerbitables.xertoolkit.schedule_quality.refresh',
                @LockOwner = 'Session';
        END;

        THROW;
    END CATCH;
END;
GO

CREATE OR ALTER PROCEDURE [powerbitables].[xertoolkit_publish_schedule_quality_config]
    @config_version_id bigint,
    @expected_settings_hash char(64),
    @published_by nvarchar(150),
    @trigger_type varchar(20) = 'manual'
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF NULLIF(LTRIM(RTRIM(@published_by)), N'') IS NULL
        THROW 51320, 'published_by is required.', 1;
    IF @expected_settings_hash IS NULL
       OR LEN(RTRIM(@expected_settings_hash)) <> 64
       OR @expected_settings_hash LIKE '%[^0-9A-Fa-f]%'
        THROW 51322, 'expected_settings_hash must be the 64-character hash last read by the editor.', 1;

    IF NOT EXISTS
    (
        SELECT 1
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version] AS v
        JOIN [powerbitables].[xertoolkit_schedule_quality_profile] AS p
          ON p.profile_id = v.profile_id
        WHERE v.config_version_id = @config_version_id
          AND v.state = 'draft'
          AND v.settings_hash IS NOT NULL
          AND UPPER(v.settings_hash) = UPPER(@expected_settings_hash)
          AND p.profile_code = N'default'
    )
        THROW 51321, 'The requested version is not a publishable default-profile draft.', 1;

    EXEC [powerbitables].[xertoolkit_refresh_all_schedule_quality]
        @proj_id = NULL,
        @config_version_id = @config_version_id,
        @expected_settings_hash = @expected_settings_hash,
        @activate_config = 1,
        @published_by = @published_by,
        @trigger_type = @trigger_type;
END;
GO
