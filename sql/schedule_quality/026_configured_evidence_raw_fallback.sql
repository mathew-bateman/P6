/*
    Make configured TASK evidence resilient to new columns.

    Known value types retain human-friendly formatting. The deployment builds
    a resolver from dbo.TASK metadata, so any other TASK column is emitted raw
    and adding a configured column cannot silently collapse evidence to N/A.
    A configured field emits N/A only when its resolved source value is NULL.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51640, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @start_marker nvarchar(100) = N'        -- Configured evidence pass v3';
    DECLARE @end_marker nvarchar(200) =
        N'        UPDATE [powerbitables].[xertoolkit_refresh_run_history]';
    DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
    IF @start_position = 0
    BEGIN
        SET @start_marker = N'        -- Configured evidence pass v4';
        SET @start_position = CHARINDEX(@start_marker, @definition);
    END;
    DECLARE @end_position int = CHARINDEX(@end_marker, @definition, @start_position);

    IF @definition IS NULL OR @start_position = 0 OR @end_position = 0
        THROW 51641, 'The configured evidence v3/v4 block could not be located.', 1;

    DECLARE @task_column_cases nvarchar(max);

    SELECT @task_column_cases = STRING_AGG
    (
        CAST
        (
            N'
                                WHEN (field.source_category = N''task_column'' OR UPPER(field.source_category) = N''TASK'')
                                 AND field.source_identifier = N''' + REPLACE(column_definition.name, N'''', N'''''') + N'''
                                    THEN ' +
            CASE
                WHEN type_definition.name IN (N'date', N'datetime', N'datetime2', N'smalldatetime')
                    THEN N'CASE WHEN task_record.' + QUOTENAME(column_definition.name) + N' IS NOT NULL
                                         THEN CONVERT(char(11), task_record.' + QUOTENAME(column_definition.name) + N', 106)
                                              + N'' '' + LEFT(CONVERT(char(8), task_record.' + QUOTENAME(column_definition.name) + N', 108), 5)
                                    END'
                WHEN type_definition.name IN (N'decimal', N'numeric', N'money', N'smallmoney', N'float', N'real')
                    THEN N'CONVERT(nvarchar(max), CAST(task_record.' + QUOTENAME(column_definition.name) + N' AS decimal(38,2)))'
                ELSE N'CONVERT(nvarchar(max), task_record.' + QUOTENAME(column_definition.name) + N')'
            END
            AS nvarchar(max)
        ),
        N''
    ) WITHIN GROUP (ORDER BY column_definition.column_id)
    FROM sys.columns AS column_definition
    INNER JOIN sys.types AS type_definition
      ON type_definition.user_type_id = column_definition.user_type_id
    WHERE column_definition.object_id = OBJECT_ID(N'dbo.TASK');

    IF NULLIF(@task_column_cases, N'') IS NULL
        THROW 51643, 'No dbo.TASK columns were available to build the evidence resolver.', 1;

    DECLARE @replacement nvarchar(max) = N'
        -- Configured evidence pass v4: format known values, preserve raw values, N/A only for NULL.
        IF @check_run_id IS NOT NULL AND @config_version_id IS NOT NULL
        BEGIN
            UPDATE evidence
            SET evidence.detail_json = formatted.detail_json,
                evidence.detail_display = formatted.detail_display
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
            INNER JOIN dbo.TASK AS task_record
              ON task_record.proj_id = evidence.proj_id
             AND task_record.task_id = evidence.task_id
            CROSS APPLY
            (
                SELECT
                    CAST(N''{'' + COALESCE
                    (
                        STRING_AGG
                        (
                            CAST(CONCAT(N''"'', STRING_ESCAPE(configured.display_label, ''json''), N''":"'', STRING_ESCAPE(configured.detail_value, ''json''), N''"'') AS nvarchar(max)),
                            N'',''
                        ) WITHIN GROUP (ORDER BY configured.sort_order, configured.detail_field_id),
                        N''''
                    ) + N''}'' AS nvarchar(max)) AS detail_json,
                    CAST
                    (
                        STRING_AGG
                        (
                            CAST(CONCAT(configured.display_label, N'': '', configured.detail_value) AS nvarchar(max)),
                            CHAR(13) + CHAR(10)
                        ) WITHIN GROUP (ORDER BY configured.sort_order, configured.detail_field_id)
                        AS nvarchar(1000)
                    ) AS detail_display
                FROM
                (
                    SELECT
                        field.detail_field_id,
                        field.display_label,
                        field.sort_order,
                        COALESCE
                        (
                            CASE
                                /*TASK_COLUMN_CASES*/
                                WHEN field.source_category = N''activity_code'' AND field.source_identifier = N''385''
                                    THEN
                                    (
                                        SELECT STRING_AGG(CONVERT(nvarchar(max), activity_code.actv_code_name), N'', '')
                                        FROM dbo.TASKACTV AS task_activity
                                        INNER JOIN dbo.ACTVCODE AS activity_code
                                          ON activity_code.actv_code_id = task_activity.actv_code_id
                                        WHERE task_activity.task_id = evidence.task_id
                                          AND activity_code.actv_code_type_id = 385
                                    )
                            END,
                            N''N/A''
                        ) AS detail_value
                    FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                    WHERE field.config_version_id = @config_version_id
                      AND field.check_code = evidence.check_code
                ) AS configured
            ) AS formatted
            WHERE evidence.check_run_id = @check_run_id
              AND EXISTS
              (
                  SELECT 1
                  FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                  WHERE field.config_version_id = @config_version_id
                    AND field.check_code = evidence.check_code
              );
        END;

';

    SET @replacement = REPLACE
    (
        @replacement,
        N'/*TASK_COLUMN_CASES*/',
        @task_column_cases
    );

    SET @definition = STUFF
    (
        @definition,
        @start_position,
        @end_position - @start_position,
        @replacement
    );
    SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
    SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @definition;

    DECLARE @deployed_definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

    IF CHARINDEX(N'Configured evidence pass v4', @deployed_definition) = 0
       OR CHARINDEX(N'field.source_identifier = N''target_drtn_hr_cnt''', @deployed_definition) = 0
       OR CHARINDEX(N'task_record.[target_drtn_hr_cnt]', @deployed_definition) = 0
       OR CHARINDEX(N'field.source_identifier = N''remain_drtn_hr_cnt''', @deployed_definition) = 0
       OR CHARINDEX(N'task_record.[remain_drtn_hr_cnt]', @deployed_definition) = 0
       OR CHARINDEX(N'N''N/A''', @deployed_definition) = 0
        THROW 51642, 'The raw-value evidence fallback was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
