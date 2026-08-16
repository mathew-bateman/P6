/* Make Critical Tasks evidence honour the labels and send order saved in Schedule Quality settings. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51630, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) = OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @start_marker nvarchar(100) = N'        -- Configured Critical Tasks evidence pass';
    DECLARE @end_marker nvarchar(200) = N'        UPDATE [powerbitables].[xertoolkit_refresh_run_history]';
    DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
    DECLARE @end_position int = CHARINDEX(@end_marker, @definition, @start_position);

    IF @definition IS NULL OR @start_position = 0 OR @end_position = 0
        THROW 51631, 'The configured evidence block could not be located.', 1;

    DECLARE @replacement nvarchar(max) = N'
        -- Configured Critical Tasks evidence pass v2: labels and order come from settings.
        IF @check_run_id IS NOT NULL AND @config_version_id IS NOT NULL
        BEGIN
            UPDATE evidence
            SET evidence.detail_json = formatted.detail_json,
                evidence.detail_display = formatted.detail_display
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
            INNER JOIN dbo.TASK AS task_record ON task_record.task_id = evidence.task_id
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
                    SELECT field.detail_field_id, field.display_label, field.sort_order,
                        CASE
                            WHEN (field.source_category = N''task_column'' OR UPPER(field.source_category) = N''TASK'') AND field.source_identifier = N''task_code''
                                THEN CONVERT(nvarchar(max), task_record.task_code)
                            WHEN (field.source_category = N''task_column'' OR UPPER(field.source_category) = N''TASK'') AND field.source_identifier = N''early_start_date''
                                THEN CONVERT(char(11), task_record.early_start_date, 106) + N'' '' + LEFT(CONVERT(char(8), task_record.early_start_date, 108), 5)
                            WHEN (field.source_category = N''task_column'' OR UPPER(field.source_category) = N''TASK'') AND field.source_identifier = N''early_end_date''
                                THEN CONVERT(char(11), task_record.early_end_date, 106) + N'' '' + LEFT(CONVERT(char(8), task_record.early_end_date, 108), 5)
                            WHEN (field.source_category = N''task_column'' OR UPPER(field.source_category) = N''TASK'') AND field.source_identifier = N''total_float_hr_cnt''
                                THEN CONVERT(nvarchar(max), CAST(task_record.total_float_hr_cnt AS decimal(18,2)))
                            WHEN field.source_category = N''activity_code'' AND field.source_identifier = N''385''
                                THEN (SELECT STRING_AGG(CONVERT(nvarchar(max), activity_code.actv_code_name), N'', '')
                                      FROM dbo.TASKACTV AS task_activity
                                      INNER JOIN dbo.ACTVCODE AS activity_code ON activity_code.actv_code_id = task_activity.actv_code_id
                                      WHERE task_activity.task_id = evidence.task_id AND activity_code.actv_code_type_id = 385)
                        END AS detail_value
                    FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                    WHERE field.config_version_id = @config_version_id AND field.check_code = evidence.check_code
                ) AS configured
                WHERE configured.detail_value IS NOT NULL
            ) AS formatted
            WHERE evidence.check_run_id = @check_run_id AND evidence.check_code = N''critical_tasks'';
        END;

';

    SET @definition = STUFF(@definition, @start_position, @end_position - @start_position, @replacement);
    SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
    SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @definition;

    DECLARE @deployed_definition nvarchar(max) = OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    IF CHARINDEX(N'Configured Critical Tasks evidence pass v2', @deployed_definition) = 0
       OR CHARINDEX(N'ORDER BY configured.sort_order, configured.detail_field_id', @deployed_definition) = 0
       OR CHARINDEX(N'STRING_ESCAPE(configured.display_label', @deployed_definition) = 0
        THROW 51632, 'The configuration-driven evidence labels and order were not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
