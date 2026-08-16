/* Store task-evidence dates in their report-ready form: 21 Nov 2026 08:00. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51596, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

    IF @definition IS NULL
        THROW 51597, 'The schedule-quality refresh procedure could not be read.', 1;

    IF CHARINDEX(N'CONVERT(varchar(33), t.early_start_date, 126)', @definition) > 0
    BEGIN
        SET @definition = REPLACE
        (
            @definition,
            N'CONVERT(varchar(33), t.early_start_date, 126)',
            N'CONVERT(char(11), t.early_start_date, 106) + N'' '' + LEFT(CONVERT(char(8), t.early_start_date, 108), 5)'
        );
        SET @definition = REPLACE
        (
            @definition,
            N'CONVERT(varchar(33), t.early_end_date, 126)',
            N'CONVERT(char(11), t.early_end_date, 106) + N'' '' + LEFT(CONVERT(char(8), t.early_end_date, 108), 5)'
        );
        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    UPDATE e
    SET e.detail_json = JSON_MODIFY
    (
        JSON_MODIFY
        (
            e.detail_json,
            N'$.start_date',
            CONVERT(char(11), t.early_start_date, 106) + N' ' + LEFT(CONVERT(char(8), t.early_start_date, 108), 5)
        ),
        N'$.end_date',
        CONVERT(char(11), t.early_end_date, 106) + N' ' + LEFT(CONVERT(char(8), t.early_end_date, 108), 5)
    )
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e
    INNER JOIN dbo.TASK AS t ON t.task_id = e.task_id
    WHERE e.check_code = 'critical_tasks'
      AND (JSON_VALUE(e.detail_json, N'$.start_date') LIKE N'____-__-__T%'
           OR JSON_VALUE(e.detail_json, N'$.end_date') LIKE N'____-__-__T%');

    IF EXISTS
    (
        SELECT 1
        FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        WHERE check_code = 'critical_tasks'
          AND (JSON_VALUE(detail_json, N'$.start_date') LIKE N'____-__-__T%'
               OR JSON_VALUE(detail_json, N'$.end_date') LIKE N'____-__-__T%')
    )
        THROW 51598, 'Some critical-task detail_json dates remain ISO timestamps.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
