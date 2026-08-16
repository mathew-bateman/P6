/*
    Persist a ready-to-render evidence string alongside the raw JSON values.

    `detail_json` keeps its numeric/ISO values for existing consumers and gains
    a `display` property.  `detail_display` is exposed directly to Power BI so
    the report does not need to format every evidence-row date and number.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51590, 'This hotfix must be run against P62212_1.', 1;

IF COL_LENGTH(N'powerbitables.xertoolkit_result_schedule_quality_task_evidence', N'detail_display') IS NULL
    ALTER TABLE [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        ADD detail_display nvarchar(1000) NULL;
GO

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @start_marker nvarchar(100) = N'            SET e.detail_json =';
    DECLARE @end_marker nvarchar(200) =
        N'            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e';
    DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
    DECLARE @end_position int = CHARINDEX(@end_marker, @definition, @start_position);

    IF @definition IS NULL OR @start_position = 0 OR @end_position = 0
        THROW 51591, 'The dynamic detail_json block could not be located.', 1;

    IF CHARINDEX(N'detail_display', @definition, @start_position) = 0
    BEGIN
        DECLARE @replacement nvarchar(max) = N'            SET e.detail_json = JSON_MODIFY
            (
                CASE WHEN e.check_code = ''critical_tasks'' THEN
                    JSON_MODIFY
                    (
                        JSON_MODIFY
                        (
                            JSON_MODIFY
                            (
                                JSON_MODIFY
                                (
                                    COALESCE(e.detail_json, N''{}''),
                                    N''$."Total Float"'',
                                    (SELECT CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))
                                     FROM dbo.TASK AS t
                                     WHERE t.task_id = e.task_id)
                                ),
                                N''$.start_date'',
                                (SELECT CONVERT(varchar(33), t.early_start_date, 126)
                                 FROM dbo.TASK AS t
                                 WHERE t.task_id = e.task_id)
                            ),
                            N''$.end_date'',
                            (SELECT CONVERT(varchar(33), t.early_end_date, 126)
                             FROM dbo.TASK AS t
                             WHERE t.task_id = e.task_id)
                        ),
                        N''$.total_float_hr'',
                        (SELECT CAST(t.total_float_hr_cnt AS decimal(18,2))
                         FROM dbo.TASK AS t
                         WHERE t.task_id = e.task_id)
                    )
                ELSE JSON_MODIFY
                (
                    COALESCE(e.detail_json, N''{}''),
                    N''$."Total Float"'',
                    (SELECT CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))
                     FROM dbo.TASK AS t
                     WHERE t.task_id = e.task_id)
                )
            END,
            N''$.display'',
            CASE WHEN e.check_code = ''critical_tasks'' THEN
                CONCAT
                (
                    N''Total Float: '',
                    (SELECT CONVERT(varchar(30), CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    N'' days'', CHAR(13) + CHAR(10),
                    N''Start Date: '',
                    (SELECT CONVERT(char(11), t.early_start_date, 106) + N'' '' + LEFT(CONVERT(char(8), t.early_start_date, 108), 5) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    CHAR(13) + CHAR(10), N''End Date: '',
                    (SELECT CONVERT(char(11), t.early_end_date, 106) + N'' '' + LEFT(CONVERT(char(8), t.early_end_date, 108), 5) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    CHAR(13) + CHAR(10), N''Total Float (Hours): '',
                    (SELECT CONVERT(varchar(30), CAST(t.total_float_hr_cnt AS decimal(18,2))) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    N'' hours''
                )
            ELSE CONCAT
            (
                N''Total Float: '',
                (SELECT CONVERT(varchar(30), CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                N'' days''
            ) END
            ),
            e.detail_display = CASE WHEN e.check_code = ''critical_tasks'' THEN
                CONCAT
                (
                    N''Total Float: '',
                    (SELECT CONVERT(varchar(30), CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    N'' days'', CHAR(13) + CHAR(10), N''Start Date: '',
                    (SELECT CONVERT(char(11), t.early_start_date, 106) + N'' '' + LEFT(CONVERT(char(8), t.early_start_date, 108), 5) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    CHAR(13) + CHAR(10), N''End Date: '',
                    (SELECT CONVERT(char(11), t.early_end_date, 106) + N'' '' + LEFT(CONVERT(char(8), t.early_end_date, 108), 5) FROM dbo.TASK AS t WHERE t.task_id = e.task_id),
                    CHAR(13) + CHAR(10), N''Total Float (Hours): '',
                    (SELECT CONVERT(varchar(30), CAST(t.total_float_hr_cnt AS decimal(18,2))) FROM dbo.TASK AS t WHERE t.task_id = e.task_id), N'' hours''
                )
            ELSE CONCAT
            (
                N''Total Float: '',
                (SELECT CONVERT(varchar(30), CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))) FROM dbo.TASK AS t WHERE t.task_id = e.task_id), N'' days''
            ) END
';

        SET @definition = STUFF(@definition, @start_position, @end_position - @start_position, @replacement);
        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    /* Backfill the existing evidence snapshot without changing its raw properties. */
    UPDATE e
    SET
        e.detail_display = d.display_text,
        e.detail_json = JSON_MODIFY(COALESCE(e.detail_json, N'{}'), N'$.display', d.display_text)
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e
    INNER JOIN dbo.TASK AS t ON t.task_id = e.task_id
    CROSS APPLY
    (
        VALUES
        (
            CASE WHEN e.check_code = 'critical_tasks' THEN
                CONCAT
                (
                    N'Total Float: ', CONVERT(varchar(30), CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))), N' days', CHAR(13) + CHAR(10),
                    N'Start Date: ', CONVERT(char(11), t.early_start_date, 106) + N' ' + LEFT(CONVERT(char(8), t.early_start_date, 108), 5), CHAR(13) + CHAR(10),
                    N'End Date: ', CONVERT(char(11), t.early_end_date, 106) + N' ' + LEFT(CONVERT(char(8), t.early_end_date, 108), 5), CHAR(13) + CHAR(10),
                    N'Total Float (Hours): ', CONVERT(varchar(30), CAST(t.total_float_hr_cnt AS decimal(18,2))), N' hours'
                )
            ELSE CONCAT(N'Total Float: ', CONVERT(varchar(30), CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))), N' days')
            END
        )
    ) AS d(display_text)
    WHERE e.detail_json IS NOT NULL;

    EXEC sys.sp_executesql N'
        CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence] AS
        SELECT
            e.config_version_id, e.check_run_id, e.refreshed_at, e.proj_id,
            e.check_code, cs.display_name AS check_name, cs.sort_order AS check_sort_order,
            e.task_id, e.task_code, e.task_name,
            t.total_float_hr_cnt / NULLIF(cal.day_hr_cnt, 0) AS total_float_days,
            e.evidence_basis, e.detail_json, e.detail_display AS evidence_display
        FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] e
        LEFT JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] cs
            ON cs.config_version_id = e.config_version_id AND cs.check_code = e.check_code
        LEFT JOIN dbo.TASK t ON t.task_id = e.task_id
        LEFT JOIN dbo.PROJWBS w ON w.wbs_id = t.wbs_id
        LEFT JOIN dbo.PROJECT p ON p.proj_id = e.proj_id
        LEFT JOIN dbo.CALENDAR cal ON cal.clndr_id = ISNULL(t.clndr_id, p.clndr_id);';

    IF COL_LENGTH(N'powerbitables.xertoolkit_result_schedule_quality_task_evidence', N'detail_display') IS NULL
       OR CHARINDEX(N'evidence_display', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]'))) = 0
        THROW 51592, 'The humanised evidence display was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
