/*
    Restore the monolithic project-metrics statement that was live immediately
    before 016_split_stage_metrics_performance.sql.

    This rollback changes only the refresh procedure's staging shape. The
    structured DEL rule, SQL-backed settings, cycle pruning, and materialised
    result contracts remain unchanged.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51550, 'This rollback must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]', N'P') IS NULL
    THROW 51551, 'The versioned schedule-quality refresh is not deployed.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION
        (
            OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]')
        );

    IF @definition IS NULL
        THROW 51552, 'The current refresh procedure definition could not be read.', 1;

    IF @definition LIKE N'%SPLIT_STAGE_PROJECT_METRICS_V1%'
    BEGIN
        DECLARE @start_marker nvarchar(100) =
            N'        /* SPLIT_STAGE_PROJECT_METRICS_V1 */';
        DECLARE @next_marker nvarchar(200) =
            N'        /* Nothing visible changes before this final transactional swap. */';
        DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
        DECLARE @next_position int = CHARINDEX(@next_marker, @definition);

        IF @start_position = 0
           OR @next_position = 0
           OR @next_position <= @start_position
           OR SUBSTRING(@definition, @start_position, @next_position - @start_position)
                NOT LIKE N'%LEFT JOIN #ActivityCounts AS ac%'
            THROW 51553, 'The refresh procedure does not match the expected split-stage metrics block.', 1;

        DECLARE @replacement nvarchar(max) = N'        ;WITH activity_counts AS
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
                (@config_version_id, ''relationship_ratio'') AS s
            JOIN #TargetProjects AS tp
              ON tp.proj_id = s.proj_id
            GROUP BY s.proj_id
        ),
        relationship_counts AS
        (
            SELECT s.proj_id, COUNT(*) AS relationship_count
            FROM [powerbitables].[xertoolkit_fn_relationship_in_scope]
                (@config_version_id, ''relationship_ratio'') AS s
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
            SELECT
                oos.proj_id,
                COUNT(DISTINCT CASE WHEN oos.is_out_of_sequence = 1 THEN oos.successor_task_id END)
                    AS out_of_sequence_count
            FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@config_version_id) AS oos
            JOIN #TargetProjects AS tp
              ON tp.proj_id = oos.proj_id
            WHERE @oos_detail_trigger_enabled = 0
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
            COALESCE(NULLIF(p.[Project Name], ''''), p.proj_short_name),
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
          ON ll.proj_id = p.proj_id
        OPTION (RECOMPILE);

';

        SET @definition = STUFF
        (
            @definition,
            @start_position,
            @next_position - @start_position,
            @replacement
        );

        DECLARE @upper_definition nvarchar(max) = UPPER(@definition);
        DECLARE @create_position int =
            CHARINDEX(N'CREATE', @upper_definition);
        DECLARE @procedure_position int =
            CHARINDEX(N'PROCEDURE', @upper_definition, @create_position);
        DECLARE @header_infix nvarchar(100) =
            CASE
                WHEN @create_position > 0
                 AND @procedure_position > @create_position
                THEN SUBSTRING
                (
                    @upper_definition,
                    @create_position + LEN(N'CREATE'),
                    @procedure_position - @create_position - LEN(N'CREATE')
                )
            END;
        DECLARE @normalized_header_infix nvarchar(100) =
            REPLACE
            (
                REPLACE
                (
                    REPLACE
                    (
                        REPLACE(ISNULL(@header_infix, N''), N' ', N''),
                        NCHAR(9),
                        N''
                    ),
                    NCHAR(10),
                    N''
                ),
                NCHAR(13),
                N''
            );

        IF @create_position > 0
           AND @procedure_position > @create_position
           AND @normalized_header_infix IN (N'', N'ORALTER')
            SET @definition = STUFF
            (
                @definition,
                @create_position,
                @procedure_position
                    + LEN(N'PROCEDURE')
                    - @create_position,
                N'ALTER PROCEDURE'
            );
        ELSE IF CHARINDEX(N'ALTER PROCEDURE', @upper_definition) = 0
            THROW 51555, 'The refresh procedure header could not be normalized to ALTER PROCEDURE.', 1;

        EXEC sys.sp_executesql @definition;
    END
    ELSE IF @definition NOT LIKE N'%LEFT JOIN activity_counts AS ac%'
        THROW 51553, 'The refresh procedure is neither the split-stage nor expected rollback definition.', 1;

    DECLARE @restored_definition nvarchar(max) =
        OBJECT_DEFINITION
        (
            OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]')
        );

    IF @restored_definition LIKE N'%SPLIT_STAGE_PROJECT_METRICS_V1%'
       OR @restored_definition NOT LIKE N'%LEFT JOIN activity_counts AS ac%'
       OR @restored_definition LIKE N'%LEFT JOIN #ActivityCounts AS ac%'
        THROW 51554, 'The monolithic refresh procedure definition was not restored.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

SELECT
    name AS restored_procedure,
    modify_date,
    CAST
    (
        CASE WHEN OBJECT_DEFINITION(object_id)
                  LIKE N'%SPLIT_STAGE_PROJECT_METRICS_V1%'
             THEN 1 ELSE 0 END
        AS bit
    ) AS split_stage_metrics_enabled
FROM sys.objects
WHERE object_id =
    OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]');
GO
