/*
    Replace temporary hard-coded evidence enrichment with the selected Critical
    Tasks fields.  Each property is added only when the staged/active settings
    select its P6 source.  This removes the legacy Total Float (days) entry and
    makes the Power BI display agree with the editor.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51600, 'This hotfix must be run against P62212_1.', 1;

IF COL_LENGTH(N'powerbitables.xertoolkit_result_schedule_quality_task_evidence', N'detail_display') IS NULL
    ALTER TABLE [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        ADD detail_display nvarchar(1000) NULL;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @start_marker nvarchar(100) = N'        -- Dynamic detail_json enrichment pass';
    DECLARE @end_marker nvarchar(200) =
        N'        UPDATE [powerbitables].[xertoolkit_refresh_run_history]';
    DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
    DECLARE @end_position int = CHARINDEX(@end_marker, @definition, @start_position);

    IF @definition IS NULL OR @start_position = 0 OR @end_position = 0
        THROW 51601, 'The current detail enrichment block could not be located.', 1;

    IF CHARINDEX(N'Configured Critical Tasks evidence pass', @definition) = 0
    BEGIN
        DECLARE @replacement nvarchar(max) = N'
        -- Configured Critical Tasks evidence pass
        IF @check_run_id IS NOT NULL AND @config_version_id IS NOT NULL
        BEGIN
            UPDATE evidence
            SET
                evidence.detail_json = configured.detail_json,
                evidence.detail_display = display_text.detail_display
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
            INNER JOIN dbo.TASK AS task_record
                ON task_record.task_id = evidence.task_id
            CROSS APPLY
            (
                VALUES
                (
                    CASE WHEN evidence.check_code = ''critical_tasks'' THEN
                        JSON_MODIFY
                        (
                            JSON_MODIFY
                            (
                                JSON_MODIFY
                                (
                                    JSON_MODIFY
                                    (
                                        JSON_MODIFY
                                        (
                                            N''{}'',
                                            N''$."Task Code"'',
                                            CASE WHEN EXISTS
                                            (
                                                SELECT 1
                                                FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                                                WHERE field.config_version_id = @config_version_id
                                                  AND field.check_code = ''critical_tasks''
                                                  AND field.source_category = ''task_column''
                                                  AND field.source_identifier = ''task_code''
                                            ) THEN task_record.task_code END
                                        ),
                                        N''$."Planner"'',
                                        CASE WHEN EXISTS
                                        (
                                            SELECT 1
                                            FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                                            WHERE field.config_version_id = @config_version_id
                                              AND field.check_code = ''critical_tasks''
                                              AND field.source_category = ''activity_code''
                                              AND field.source_identifier = ''385''
                                        ) THEN
                                        (
                                            SELECT STRING_AGG(CONVERT(nvarchar(max), activity_code.actv_code_name), N'', '')
                                            FROM dbo.TASKACTV AS task_activity
                                            INNER JOIN dbo.ACTVCODE AS activity_code
                                                ON activity_code.actv_code_id = task_activity.actv_code_id
                                            WHERE task_activity.task_id = evidence.task_id
                                              AND activity_code.actv_code_type_id = 385
                                        ) END
                                    ),
                                    N''$.start_date'',
                                    CASE WHEN EXISTS
                                    (
                                        SELECT 1
                                        FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                                        WHERE field.config_version_id = @config_version_id
                                          AND field.check_code = ''critical_tasks''
                                          AND field.source_category = ''task_column''
                                          AND field.source_identifier = ''early_start_date''
                                    ) THEN CONVERT(varchar(33), task_record.early_start_date, 126) END
                                ),
                                N''$.end_date'',
                                CASE WHEN EXISTS
                                (
                                    SELECT 1
                                    FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                                    WHERE field.config_version_id = @config_version_id
                                      AND field.check_code = ''critical_tasks''
                                      AND field.source_category = ''task_column''
                                      AND field.source_identifier = ''early_end_date''
                                ) THEN CONVERT(varchar(33), task_record.early_end_date, 126) END
                            ),
                            N''$.total_float_hr'',
                            CASE WHEN EXISTS
                            (
                                SELECT 1
                                FROM [powerbitables].[xertoolkit_schedule_quality_detail_field] AS field
                                WHERE field.config_version_id = @config_version_id
                                  AND field.check_code = ''critical_tasks''
                                  AND field.source_category = ''task_column''
                                  AND field.source_identifier = ''total_float_hr_cnt''
                            ) THEN CAST(task_record.total_float_hr_cnt AS decimal(18,2)) END
                        )
                    ELSE N''{}'' END
                )
            ) AS configured(detail_json)
            OUTER APPLY
            (
                SELECT STRING_AGG
                (
                    CONCAT(json_value.[key], N'': '', COALESCE(json_value.value, N'''')),
                    CHAR(13) + CHAR(10)
                ) WITHIN GROUP
                (
                    ORDER BY CASE json_value.[key]
                        WHEN ''Task Code'' THEN 1
                        WHEN ''Planner'' THEN 2
                        WHEN ''start_date'' THEN 3
                        WHEN ''end_date'' THEN 4
                        WHEN ''total_float_hr'' THEN 5
                        ELSE 99
                    END
                ) AS detail_display
                FROM OPENJSON(configured.detail_json) AS json_value
            ) AS display_text
            WHERE evidence.check_run_id = @check_run_id;
        END;

';

        SET @definition = STUFF(@definition, @start_position, @end_position - @start_position, @replacement);
        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    IF CHARINDEX(N'Configured Critical Tasks evidence pass', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
        THROW 51602, 'The configured Critical Tasks enrichment block was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
