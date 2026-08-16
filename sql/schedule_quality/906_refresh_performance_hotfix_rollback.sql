/*
    Restore the refresh procedure definition that was live immediately before
    006_refresh_performance_hotfix.sql.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51530, 'This rollback must be run against P62212_1.', 1;

BEGIN TRANSACTION;
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

        INSERT INTO #TargetProjects (proj_id)
        SELECT p.proj_id
        FROM [powerbitables].[xertoolkit_vw_PBI_Projects] AS p
        WHERE @proj_id IS NULL OR p.proj_id = @proj_id;

        SELECT @processed_project_count = COUNT(*) FROM #TargetProjects;

        IF @proj_id IS NOT NULL AND @processed_project_count = 0
            THROW 51308, 'The requested project is not an active P6 project.', 1;

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

            WHILE @removed > 0
            BEGIN
                CREATE TABLE #PruneNodes
                (
                    proj_id int NOT NULL,
                    task_id int NOT NULL,
                    PRIMARY KEY (proj_id, task_id)
                );

                INSERT INTO #PruneNodes (proj_id, task_id)
                SELECT n.proj_id, n.task_id
                FROM #CycleNodes AS n
                OUTER APPLY
                (
                    SELECT COUNT_BIG(*) AS incoming_count
                    FROM #CycleEdges AS e
                    WHERE e.proj_id = n.proj_id
                      AND e.successor_task_id = n.task_id
                ) AS incoming
                OUTER APPLY
                (
                    SELECT COUNT_BIG(*) AS outgoing_count
                    FROM #CycleEdges AS e
                    WHERE e.proj_id = n.proj_id
                      AND e.predecessor_task_id = n.task_id
                ) AS outgoing
                WHERE incoming.incoming_count = 0
                   OR outgoing.outgoing_count = 0;

                SELECT @removed = COUNT(*) FROM #PruneNodes;

                IF @removed > 0
                BEGIN
                    DELETE e
                    FROM #CycleEdges AS e
                    JOIN #PruneNodes AS p
                      ON p.proj_id = e.proj_id
                     AND
                     (
                         p.task_id = e.predecessor_task_id
                         OR p.task_id = e.successor_task_id
                     );

                    DELETE n
                    FROM #CycleNodes AS n
                    JOIN #PruneNodes AS p
                      ON p.proj_id = n.proj_id
                     AND p.task_id = n.task_id;
                END;

                DROP TABLE #PruneNodes;
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

        ;WITH activity_counts AS
        (
            SELECT a.proj_id, COUNT(*) AS activity_count
            FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
            JOIN #TargetProjects AS tp
              ON tp.proj_id = a.proj_id
            GROUP BY a.proj_id
        ),
        scoped_activity_counts AS
        (
            SELECT s.proj_id, COUNT(*) AS scoped_activity_count
            FROM [powerbitables].[xertoolkit_fn_activity_in_scope]
                (@config_version_id, 'relationship_ratio') AS s
            JOIN #TargetProjects AS tp
              ON tp.proj_id = s.proj_id
            GROUP BY s.proj_id
        ),
        relationship_counts AS
        (
            SELECT s.proj_id, COUNT(*) AS relationship_count
            FROM [powerbitables].[xertoolkit_fn_relationship_in_scope]
                (@config_version_id, 'relationship_ratio') AS s
            JOIN #TargetProjects AS tp
              ON tp.proj_id = s.proj_id
            GROUP BY s.proj_id
        ),
        open_end_counts AS
        (
            SELECT
                o.proj_id,
                SUM(o.is_missing_predecessor) AS missing_predecessor_count,
                SUM(o.is_missing_successor) AS missing_successor_count,
                SUM(o.is_open_start) AS open_start_count,
                SUM(o.is_open_finish) AS open_finish_count
            FROM [powerbitables].[xertoolkit_fn_open_ends](@config_version_id) AS o
            JOIN #TargetProjects AS tp
              ON tp.proj_id = o.proj_id
            GROUP BY o.proj_id
        ),
        relationship_quality_counts AS
        (
            SELECT
                rq.proj_id,
                SUM(rq.is_lead) AS lead_count,
                SUM(rq.is_lag) AS lag_count,
                SUM(rq.is_non_fs) AS non_fs_count,
                SUM(rq.is_excessive_ss_lag) AS excessive_ss_lag_count,
                SUM(rq.is_excessive_ff_lag) AS excessive_ff_lag_count
            FROM [powerbitables].[xertoolkit_fn_relationship_quality](@config_version_id) AS rq
            JOIN #TargetProjects AS tp
              ON tp.proj_id = rq.proj_id
            GROUP BY rq.proj_id
        ),
        activity_quality_counts AS
        (
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
            FROM [powerbitables].[xertoolkit_fn_activity_quality](@config_version_id) AS aq
            JOIN #TargetProjects AS tp
              ON tp.proj_id = aq.proj_id
            GROUP BY aq.proj_id
        ),
        out_of_sequence_counts AS
        (
            SELECT oos.proj_id, SUM(oos.is_out_of_sequence) AS out_of_sequence_count
            FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@config_version_id) AS oos
            JOIN #TargetProjects AS tp
              ON tp.proj_id = oos.proj_id
            GROUP BY oos.proj_id
        ),
        logic_loop_counts AS
        (
            SELECT ll.proj_id, COUNT(DISTINCT ll.task_id) AS logical_loop_count
            FROM #LogicLoopStage AS ll
            GROUP BY ll.proj_id
        )
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
        LEFT JOIN activity_counts AS ac
          ON ac.proj_id = p.proj_id
        LEFT JOIN scoped_activity_counts AS sac
          ON sac.proj_id = p.proj_id
        LEFT JOIN relationship_counts AS rc
          ON rc.proj_id = p.proj_id
        LEFT JOIN open_end_counts AS oec
          ON oec.proj_id = p.proj_id
        LEFT JOIN relationship_quality_counts AS rqc
          ON rqc.proj_id = p.proj_id
        LEFT JOIN activity_quality_counts AS aqc
          ON aqc.proj_id = p.proj_id
        LEFT JOIN out_of_sequence_counts AS oos
          ON oos.proj_id = p.proj_id
        LEFT JOIN logic_loop_counts AS ll
          ON ll.proj_id = p.proj_id;

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

DECLARE @definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

IF @definition LIKE N'%IX_CycleEdges_incoming%'
   OR @definition LIKE N'%TRUNCATE TABLE #PruneNodes%'
   OR @definition LIKE N'%OPTION (RECOMPILE)%'
    THROW 51531, 'The pre-hotfix refresh procedure was not restored.', 1;

COMMIT TRANSACTION;

SELECT
    name AS restored_procedure,
    modify_date
FROM sys.objects
WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]');
GO
