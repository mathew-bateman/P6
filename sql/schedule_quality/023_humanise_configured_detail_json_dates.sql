/* Keep future configuration-driven detail_json dates in report-ready text form. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51599, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

    IF @definition IS NULL
        THROW 51600, 'The schedule-quality refresh procedure could not be read.', 1;

    IF CHARINDEX(N'CONVERT(varchar(33), task_record.early_start_date, 126)', @definition) > 0
    BEGIN
        SET @definition = REPLACE
        (
            @definition,
            N'CONVERT(varchar(33), task_record.early_start_date, 126)',
            N'CONVERT(char(11), task_record.early_start_date, 106) + N'' '' + LEFT(CONVERT(char(8), task_record.early_start_date, 108), 5)'
        );
        SET @definition = REPLACE
        (
            @definition,
            N'CONVERT(varchar(33), task_record.early_end_date, 126)',
            N'CONVERT(char(11), task_record.early_end_date, 106) + N'' '' + LEFT(CONVERT(char(8), task_record.early_end_date, 108), 5)'
        );
        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    IF CHARINDEX(N'CONVERT(varchar(33), task_record.early_start_date, 126)',
                 OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) > 0
        THROW 51601, 'Configured start_date is still written as an ISO timestamp.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
