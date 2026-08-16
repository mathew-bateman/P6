/*
    Resolve configured evidence columns from every P6 table that has a
    deterministic key back to the evidence activity, its WBS, or its project.

    Resolution order is activity, relationship endpoint, WBS, another shared
    TASK identifier, then project. Multi-row sources are de-duplicated and
    joined into one display value. Known dates and numbers are humanised; all
    other non-NULL values are passed through raw. N/A means no joined value.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51650, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @start_marker nvarchar(100) = N'        -- Configured evidence pass v4';
    DECLARE @end_marker nvarchar(200) =
        N'        UPDATE [powerbitables].[xertoolkit_refresh_run_history]';
    DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
    IF @start_position = 0
    BEGIN
        SET @start_marker = N'        -- Configured evidence pass v5';
        SET @start_position = CHARINDEX(@start_marker, @definition);
    END;
    DECLARE @end_position int = CHARINDEX(@end_marker, @definition, @start_position);

    IF @definition IS NULL OR @start_position = 0 OR @end_position = 0
        THROW 51651, 'The configured evidence v4/v5 block could not be located.', 1;

    DECLARE @replacement nvarchar(max) = N'
        -- Configured evidence pass v5: generic P6 table resolver with raw fallback.
        IF @check_run_id IS NOT NULL AND @config_version_id IS NOT NULL
        BEGIN
            CREATE TABLE #ConfiguredEvidenceValue
            (
                proj_id int NOT NULL,
                check_code varchar(50) NOT NULL,
                task_id int NOT NULL,
                detail_field_id bigint NOT NULL,
                display_label nvarchar(120) NOT NULL,
                sort_order int NOT NULL,
                detail_value nvarchar(max) NOT NULL
            );

            DECLARE
                @detail_field_id bigint,
                @field_check_code varchar(50),
                @source_category varchar(128),
                @source_identifier varchar(128),
                @display_label nvarchar(120),
                @sort_order int,
                @source_table sysname,
                @source_type sysname,
                @join_column sysname,
                @source_expression nvarchar(max),
                @join_predicate nvarchar(max),
                @field_sql nvarchar(max);

            DECLARE configured_field_cursor CURSOR LOCAL FAST_FORWARD FOR
                SELECT
                    field.detail_field_id,
                    field.check_code,
                    field.source_category,
                    field.source_identifier,
                    field.display_label,
                    field.sort_order,
                    CASE LOWER(field.source_category)
                        WHEN N''task_column'' THEN N''TASK''
                        WHEN N''project_column'' THEN N''PROJECT''
                        WHEN N''relationship_column'' THEN N''TASKPRED''
                        WHEN N''wbs_column'' THEN N''PROJWBS''
                        WHEN N''resource_column'' THEN N''TASKRSRC''
                        ELSE field.source_category
                    END AS source_table
                FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                WHERE field.config_version_id = @config_version_id
                ORDER BY field.check_code, field.sort_order, field.detail_field_id;

            OPEN configured_field_cursor;
            FETCH NEXT FROM configured_field_cursor INTO
                @detail_field_id, @field_check_code, @source_category,
                @source_identifier, @display_label, @sort_order, @source_table;

            WHILE @@FETCH_STATUS = 0
            BEGIN
                SET @field_sql = NULL;
                SET @source_type = NULL;
                SET @join_column = NULL;

                IF LOWER(@source_category) = N''activity_code'' AND @source_identifier = N''385''
                BEGIN
                    INSERT #ConfiguredEvidenceValue
                    SELECT
                        evidence.proj_id,
                        evidence.check_code,
                        evidence.task_id,
                        @detail_field_id,
                        @display_label,
                        @sort_order,
                        COALESCE(code_value.detail_value, N''N/A'')
                    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
                    OUTER APPLY
                    (
                        SELECT STRING_AGG(distinct_code.detail_value, N'', '') AS detail_value
                        FROM
                        (
                            SELECT DISTINCT CONVERT(nvarchar(max), activity_code.actv_code_name) AS detail_value
                            FROM dbo.TASKACTV AS task_activity
                            INNER JOIN dbo.ACTVCODE AS activity_code
                              ON activity_code.actv_code_id = task_activity.actv_code_id
                            WHERE task_activity.task_id = evidence.task_id
                              AND activity_code.actv_code_type_id = 385
                        ) AS distinct_code
                    ) AS code_value
                    WHERE evidence.check_run_id = @check_run_id
                      AND evidence.check_code = @field_check_code;
                END
                ELSE
                BEGIN
                    SELECT @source_type = type_definition.name
                    FROM sys.tables AS table_definition
                    INNER JOIN sys.schemas AS schema_definition
                      ON schema_definition.schema_id = table_definition.schema_id
                    INNER JOIN sys.columns AS column_definition
                      ON column_definition.object_id = table_definition.object_id
                    INNER JOIN sys.types AS type_definition
                      ON type_definition.user_type_id = column_definition.user_type_id
                    WHERE schema_definition.name = N''dbo''
                      AND table_definition.name = @source_table
                      AND column_definition.name = @source_identifier;

                    IF @source_type IS NOT NULL
                    BEGIN
                        IF @source_table = N''TASKPRED'' AND EXISTS
                        (
                            SELECT 1 FROM sys.columns
                            WHERE object_id = OBJECT_ID(N''dbo.TASKPRED'') AND name = N''task_id''
                        ) AND EXISTS
                        (
                            SELECT 1 FROM sys.columns
                            WHERE object_id = OBJECT_ID(N''dbo.TASKPRED'') AND name = N''pred_task_id''
                        )
                            SET @join_column = N''relationship_endpoint'';
                        ELSE IF @source_table = N''TASK''
                            SET @join_column = N''task_id'';
                        ELSE IF EXISTS
                        (
                            SELECT 1 FROM sys.columns
                            WHERE object_id = OBJECT_ID(N''dbo.'' + QUOTENAME(@source_table))
                              AND name = N''task_id''
                        )
                            SET @join_column = N''task_id'';
                        ELSE IF EXISTS
                        (
                            SELECT 1 FROM sys.columns
                            WHERE object_id = OBJECT_ID(N''dbo.'' + QUOTENAME(@source_table))
                              AND name = N''wbs_id''
                        )
                            SET @join_column = N''wbs_id'';
                        ELSE
                        BEGIN
                            SELECT TOP (1) @join_column = source_column.name
                            FROM sys.columns AS source_column
                            INNER JOIN sys.columns AS task_column
                              ON task_column.object_id = OBJECT_ID(N''dbo.TASK'')
                             AND task_column.name = source_column.name
                            WHERE source_column.object_id = OBJECT_ID(N''dbo.'' + QUOTENAME(@source_table))
                              AND source_column.name LIKE N''%[_]id''
                              AND source_column.name NOT IN (N''delete_session_id'')
                            ORDER BY
                                CASE source_column.name
                                    WHEN N''clndr_id'' THEN 1
                                    WHEN N''rsrc_id'' THEN 2
                                    WHEN N''location_id'' THEN 3
                                    WHEN N''proj_id'' THEN 99
                                    ELSE 10
                                END,
                                source_column.column_id;
                        END;

                        IF @join_column = N''relationship_endpoint''
                         AND @field_check_code IN
                         (
                             N''relationship_leads'',
                             N''relationship_lags'',
                             N''relationship_ratio'',
                             N''excessive_ss_lag'',
                             N''excessive_ff_lag''
                         )
                            SET @join_predicate = N''EXISTS
                            (
                                SELECT 1
                                FROM [powerbitables].[xertoolkit_fn_relationship_quality](@config_version_id) AS qualifying_relationship
                                WHERE qualifying_relationship.relationship_id = source_row.[task_pred_id]
                                  AND
                                  (
                                      qualifying_relationship.predecessor_task_id = evidence.task_id
                                      OR qualifying_relationship.successor_task_id = evidence.task_id
                                  )
                                  AND
                                  (
                                      (@field_check_code = N''''relationship_leads'''' AND qualifying_relationship.is_lead = 1)
                                      OR (@field_check_code = N''''relationship_lags'''' AND qualifying_relationship.is_lag = 1)
                                      OR (@field_check_code = N''''relationship_ratio'''' AND qualifying_relationship.is_non_fs = 1)
                                      OR (@field_check_code = N''''excessive_ss_lag'''' AND qualifying_relationship.is_excessive_ss_lag = 1)
                                      OR (@field_check_code = N''''excessive_ff_lag'''' AND qualifying_relationship.is_excessive_ff_lag = 1)
                                  )
                            )'';
                        ELSE IF @join_column = N''relationship_endpoint''
                            SET @join_predicate = N''(source_row.[task_id] = evidence.task_id OR source_row.[pred_task_id] = evidence.task_id)'';
                        ELSE IF @join_column = N''task_id''
                            SET @join_predicate = N''source_row.[task_id] = evidence.task_id'';
                        ELSE IF @join_column = N''wbs_id''
                            SET @join_predicate = N''source_row.[wbs_id] = task_record.wbs_id'';
                        ELSE IF @join_column IS NOT NULL
                            SET @join_predicate = N''source_row.'' + QUOTENAME(@join_column)
                                + N'' = task_record.'' + QUOTENAME(@join_column);
                        ELSE IF EXISTS
                        (
                            SELECT 1 FROM sys.columns
                            WHERE object_id = OBJECT_ID(N''dbo.'' + QUOTENAME(@source_table))
                              AND name = N''proj_id''
                        )
                            SET @join_predicate = N''source_row.[proj_id] = evidence.proj_id'';
                        ELSE
                            SET @join_predicate = NULL;

                        IF @join_predicate IS NOT NULL
                        BEGIN
                            IF EXISTS
                            (
                                SELECT 1 FROM sys.columns
                                WHERE object_id = OBJECT_ID(N''dbo.'' + QUOTENAME(@source_table))
                                  AND name = N''proj_id''
                            ) AND @join_column NOT IN (N''proj_id'', N''relationship_endpoint'')
                                SET @join_predicate += N'' AND source_row.[proj_id] = evidence.proj_id'';

                            IF @join_column = N''relationship_endpoint''
                                SET @join_predicate += N'' AND source_row.[proj_id] = evidence.proj_id'';

                            SET @source_expression = CASE
                                WHEN @source_table = N''TASK''
                                 AND @source_identifier IN (N''cstr_type'', N''cstr_type2'')
                                    THEN N''COALESCE
                                    (
                                        (
                                            SELECT configured_constraint.display_name
                                            FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type] AS configured_constraint
                                            WHERE configured_constraint.config_version_id = @config_version_id
                                              AND configured_constraint.constraint_type_code = source_row.''
                                            + QUOTENAME(@source_identifier) + N''
                                        ),
                                        CONVERT(nvarchar(max), source_row.'' + QUOTENAME(@source_identifier) + N'')
                                    )''
                                WHEN @source_type IN (N''date'', N''datetime'', N''datetime2'', N''smalldatetime'')
                                    THEN N''CONVERT(char(11), source_row.'' + QUOTENAME(@source_identifier)
                                         + N'', 106) + N'''' '''' + LEFT(CONVERT(char(8), source_row.''
                                         + QUOTENAME(@source_identifier) + N'', 108), 5)''
                                WHEN @source_type IN (N''decimal'', N''numeric'', N''money'', N''smallmoney'', N''float'', N''real'')
                                    THEN N''CONVERT(nvarchar(max), CAST(source_row.'' + QUOTENAME(@source_identifier)
                                         + N'' AS decimal(38,2)))''
                                ELSE N''CONVERT(nvarchar(max), source_row.'' + QUOTENAME(@source_identifier) + N'')''
                            END;

                            SET @field_sql = N''
                                INSERT #ConfiguredEvidenceValue
                                SELECT evidence.proj_id, evidence.check_code, evidence.task_id,
                                       @detail_field_id, @display_label, @sort_order,
                                       COALESCE(resolved.detail_value, N''''N/A'''')
                                FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
                                INNER JOIN dbo.TASK AS task_record
                                  ON task_record.proj_id = evidence.proj_id
                                 AND task_record.task_id = evidence.task_id
                                OUTER APPLY
                                (
                                    SELECT STRING_AGG(distinct_value.detail_value, N'''', '''') AS detail_value
                                    FROM
                                    (
                                        SELECT DISTINCT '' + @source_expression + N'' AS detail_value
                                        FROM dbo.'' + QUOTENAME(@source_table) + N'' AS source_row
                                        WHERE '' + @join_predicate + N''
                                          AND source_row.'' + QUOTENAME(@source_identifier) + N'' IS NOT NULL
                                    ) AS distinct_value
                                ) AS resolved
                                WHERE evidence.check_run_id = @check_run_id
                                  AND evidence.check_code = @field_check_code;'';

                            EXEC sys.sp_executesql
                                @field_sql,
                                N''@check_run_id bigint, @config_version_id bigint, @detail_field_id bigint, @display_label nvarchar(120), @sort_order int, @field_check_code varchar(50)'',
                                @check_run_id, @config_version_id, @detail_field_id, @display_label, @sort_order, @field_check_code;
                        END;
                    END;

                    IF @field_sql IS NULL
                    BEGIN
                        INSERT #ConfiguredEvidenceValue
                        SELECT evidence.proj_id, evidence.check_code, evidence.task_id,
                               @detail_field_id, @display_label, @sort_order, N''N/A''
                        FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
                        WHERE evidence.check_run_id = @check_run_id
                          AND evidence.check_code = @field_check_code;
                    END;
                END;

                FETCH NEXT FROM configured_field_cursor INTO
                    @detail_field_id, @field_check_code, @source_category,
                    @source_identifier, @display_label, @sort_order, @source_table;
            END;

            CLOSE configured_field_cursor;
            DEALLOCATE configured_field_cursor;

            UPDATE evidence
            SET evidence.detail_json = formatted.detail_json,
                evidence.detail_display = formatted.detail_display
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
            CROSS APPLY
            (
                SELECT
                    CAST(N''{'' + STRING_AGG
                    (
                        CAST(CONCAT(N''"'', STRING_ESCAPE(value.display_label, ''json''), N''":"'', STRING_ESCAPE(value.detail_value, ''json''), N''"'') AS nvarchar(max)),
                        N'',''
                    ) WITHIN GROUP (ORDER BY value.sort_order, value.detail_field_id) + N''}'' AS nvarchar(max)) AS detail_json,
                    CAST(STRING_AGG
                    (
                        CAST(CONCAT(value.display_label, N'': '', value.detail_value) AS nvarchar(max)),
                        CHAR(13) + CHAR(10)
                    ) WITHIN GROUP (ORDER BY value.sort_order, value.detail_field_id) AS nvarchar(1000)) AS detail_display
                FROM #ConfiguredEvidenceValue AS value
                WHERE value.proj_id = evidence.proj_id
                  AND value.check_code = evidence.check_code
                  AND value.task_id = evidence.task_id
            ) AS formatted
            WHERE evidence.check_run_id = @check_run_id
              AND formatted.detail_json IS NOT NULL;

            DROP TABLE #ConfiguredEvidenceValue;
        END;

';

    SET @definition = STUFF(@definition, @start_position, @end_position - @start_position, @replacement);
    SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
    SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @definition;

    DECLARE @deployed_definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

    IF CHARINDEX(N'Configured evidence pass v5', @deployed_definition) = 0
       OR CHARINDEX(N'configured_field_cursor', @deployed_definition) = 0
       OR CHARINDEX(N'QUOTENAME(@source_table)', @deployed_definition) = 0
       OR CHARINDEX(N'SELECT DISTINCT', @deployed_definition) = 0
       OR CHARINDEX(N'xertoolkit_schedule_quality_constraint_type', @deployed_definition) = 0
       OR CHARINDEX(N'configured_constraint.display_name', @deployed_definition) = 0
       OR CHARINDEX(N'qualifying_relationship.relationship_id = source_row.[task_pred_id]', @deployed_definition) = 0
       OR CHARINDEX(N'qualifying_relationship.is_lag = 1', @deployed_definition) = 0
       OR CHARINDEX(N'N''N/A''', @deployed_definition) = 0
        THROW 51652, 'The all-table evidence resolver was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
