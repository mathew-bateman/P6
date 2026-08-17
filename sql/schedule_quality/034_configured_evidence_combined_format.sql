/*
    Simplify calculated-day evidence output.
    Examples:
      Total Float: 952.00 hours | 119.00 days (8h/day)
      Total Float: 119.00 days (8h/day)
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51694, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @refresh_definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @days_only_old nvarchar(max) = N'                                WHEN @display_format = N''p6_days_only''
                                 AND @source_identifier LIKE N''%[_]hr[_]cnt''
                                    THEN N''CONCAT
                                    (
                                        N''''Calculated days: '''', CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' / 8.0 AS decimal(38,2))),
                                        N'''' days (from '''', CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' AS decimal(38,2))),
                                        N'''' P6 hours; 8h/day)''''
                                    )''
                                WHEN @display_format = N''p6_hours_and_days''';
    DECLARE @days_only_new nvarchar(max) = N'                                WHEN @display_format = N''p6_days_only''
                                 AND @source_identifier LIKE N''%[_]hr[_]cnt''
                                    THEN N''CONCAT
                                    (
                                        CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' / 8.0 AS decimal(38,2))),
                                        N'''' days (8h/day)''''
                                    )''
                                WHEN @display_format = N''p6_hours_and_days''';

    IF @refresh_definition IS NULL
        THROW 51695, 'The schedule-quality refresh procedure could not be read.', 1;

    SET @refresh_definition = REPLACE(@refresh_definition, NCHAR(13) + NCHAR(10), NCHAR(10));

    IF CHARINDEX(N'Configured evidence pass v9', @refresh_definition) = 0
       OR CHARINDEX(N'N''''P6: ''''', @refresh_definition) = 0
       OR CHARINDEX(N'N'''' hours | Calculated: ''''', @refresh_definition) = 0
       OR CHARINDEX(@days_only_old, @refresh_definition) = 0
        THROW 51696, 'The configured evidence v9 combined formatter could not be located.', 1;

    SET @refresh_definition = REPLACE(
        @refresh_definition,
        @days_only_old,
        @days_only_new
    );
    SET @refresh_definition = REPLACE(
        @refresh_definition,
        N'N''''P6: '''',',
        N'N'''','
    );
    SET @refresh_definition = REPLACE(
        @refresh_definition,
        N'N'''' hours | Calculated: '''',',
        N'N'''' hours | '''','
    );
    SET @refresh_definition = REPLACE(
        @refresh_definition,
        N'Configured evidence pass v9: configured P6 hours-only, days-only, or combined evidence formats.',
        N'Configured evidence pass v10: concise P6 hours, days-only, or combined evidence formats.'
    );
    SET @refresh_definition = REPLACE(@refresh_definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
    SET @refresh_definition = REPLACE(@refresh_definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @refresh_definition;

    IF CHARINDEX(N'P6: ', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) <> 0
       OR CHARINDEX(N'hours | Calculated: ', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) <> 0
       OR CHARINDEX(N'Calculated days: ', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) <> 0
       OR CHARINDEX(N'days (from ', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) <> 0
       OR CHARINDEX(N'Configured evidence pass v10', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
        THROW 51697, 'The concise combined evidence formatter was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
