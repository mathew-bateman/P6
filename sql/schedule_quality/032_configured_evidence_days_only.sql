/*
    Add a calculated-days-only evidence format for P6 hour-count fields.
    Native values remain available as `native` (P6 hours only), while
    `p6_hours_and_days` continues to show both values.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51690, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    IF EXISTS
    (
        SELECT 1
        FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_detail_field]')
          AND name = N'CK_xertoolkit_schedule_quality_detail_field_display_format'
    )
        ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_detail_field]
            DROP CONSTRAINT [CK_xertoolkit_schedule_quality_detail_field_display_format];

    ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_detail_field]
        ADD CONSTRAINT [CK_xertoolkit_schedule_quality_detail_field_display_format]
            CHECK (display_format IN (N'native', N'p6_days_only', N'p6_hours_and_days'));

    DECLARE @refresh_definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @days_only_branch nvarchar(max) = N'                                WHEN @display_format = N''p6_days_only''
                                 AND @source_identifier LIKE N''%[_]hr[_]cnt''
                                    THEN N''CONCAT
                                    (
                                        N''''Calculated days: '''', CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' / 8.0 AS decimal(38,2))),
                                        N'''' days (from '''', CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' AS decimal(38,2))),
                                        N'''' P6 hours; 8h/day)''''
                                    )''
                                WHEN @display_format = N''p6_hours_and_days''';

    IF @refresh_definition IS NULL
        THROW 51691, 'The schedule-quality refresh procedure could not be read.', 1;

    SET @refresh_definition = REPLACE(@refresh_definition, NCHAR(13) + NCHAR(10), NCHAR(10));

    IF CHARINDEX(N'Configured evidence pass v8', @refresh_definition) = 0
       OR CHARINDEX(N'WHEN @display_format = N''p6_hours_and_days''', @refresh_definition) = 0
        THROW 51692, 'The configured evidence v8 resolver could not be located.', 1;

    SET @refresh_definition = REPLACE(
        @refresh_definition,
        N'                                WHEN @display_format = N''p6_hours_and_days''',
        @days_only_branch
    );
    SET @refresh_definition = REPLACE(
        @refresh_definition,
        N'Configured evidence pass v8: configured native or P6-hours-and-days evidence formats.',
        N'Configured evidence pass v9: configured P6 hours-only, days-only, or combined evidence formats.'
    );
    SET @refresh_definition = REPLACE(@refresh_definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
    SET @refresh_definition = REPLACE(@refresh_definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @refresh_definition;

    IF CHARINDEX(N'p6_days_only', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
       OR CHARINDEX(N'Configured evidence pass v9', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
        THROW 51693, 'Configured evidence days-only formatting was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
