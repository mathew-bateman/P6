/*
    Add an explicit evidence display format to the versioned configuration.
    `p6_hours_and_days` keeps the native P6 hours and appends the reporting
    calculation: P6: 80.00 hours | Calculated: 10.00 days (8h/day).
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51660, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    IF COL_LENGTH(N'powerbitables.xertoolkit_schedule_quality_detail_field', N'display_format') IS NULL
        ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_detail_field]
            ADD display_format varchar(40) NOT NULL
                CONSTRAINT [DF_xertoolkit_schedule_quality_detail_field_display_format]
                DEFAULT N'native';

    IF NOT EXISTS
    (
        SELECT 1
        FROM sys.check_constraints
        WHERE parent_object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_detail_field]')
          AND name = N'CK_xertoolkit_schedule_quality_detail_field_display_format'
    )
        ALTER TABLE [powerbitables].[xertoolkit_schedule_quality_detail_field]
            ADD CONSTRAINT [CK_xertoolkit_schedule_quality_detail_field_display_format]
                CHECK (display_format IN (N'native', N'p6_hours_and_days'));

    DECLARE @save_definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_save_schedule_quality_draft]'));
    DECLARE @field_list nvarchar(max) = N'display_label,' + NCHAR(13) + NCHAR(10) + N'            sort_order';
    DECLARE @field_list_with_format nvarchar(max) = N'display_label,' + NCHAR(13) + NCHAR(10) + N'            display_format,' + NCHAR(13) + NCHAR(10) + N'            sort_order';

    IF @save_definition IS NULL
        THROW 51661, 'The settings-draft procedure could not be read.', 1;

    IF CHARINDEX(N'display_format', @save_definition) = 0
    BEGIN
        IF CHARINDEX(N'display_label nvarchar(120) NOT NULL,', @save_definition) = 0
           OR CHARINDEX(@field_list, @save_definition) = 0
           OR CHARINDEX(N'display_label nvarchar(120) ''$.display_label'',', @save_definition) = 0
            THROW 51662, 'The current evidence-field save contract could not be located.', 1;

        SET @save_definition = REPLACE(
            @save_definition,
            N'display_label nvarchar(120) NOT NULL,',
            N'display_label nvarchar(120) NOT NULL,' + NCHAR(13) + NCHAR(10) + N'        display_format varchar(40) NOT NULL,'
        );
        SET @save_definition = REPLACE(
            @save_definition,
            N'display_label nvarchar(120) ''$.display_label'',',
            N'display_label nvarchar(120) ''$.display_label'',' + NCHAR(13) + N'            display_format varchar(40) ''$.display_format'', '
        );
        SET @save_definition = REPLACE(@save_definition, @field_list, @field_list_with_format);
        SET @save_definition = REPLACE(
            @save_definition,
            N'check_code, source_category, source_identifier, display_label, sort_order',
            N'check_code, source_category, source_identifier, display_label, display_format, sort_order'
        );
        SET @save_definition = REPLACE(
            @save_definition,
            N'CREATE   PROCEDURE', N'ALTER PROCEDURE'
        );
        SET @save_definition = REPLACE(
            @save_definition,
            N'CREATE PROCEDURE', N'ALTER PROCEDURE'
        );
        EXEC sys.sp_executesql @save_definition;
    END;

    DECLARE @refresh_definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @numeric_value_expression nvarchar(max) = N'                                WHEN @source_type IN (N''decimal'', N''numeric'', N''money'', N''smallmoney'', N''float'', N''real'')
                                    THEN N''CONVERT(nvarchar(max), CAST(source_row.'' + QUOTENAME(@source_identifier)
                                         + N'' AS decimal(38,2)))''';
    DECLARE @hours_and_days_expression nvarchar(max) = N'                                WHEN @display_format = N''p6_hours_and_days''
                                 AND @source_identifier LIKE N''%[_]hr[_]cnt''
                                    THEN N''CONCAT
                                    (
                                        N''''P6: '''', CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' AS decimal(38,2))),
                                        N'''' hours | Calculated: '''', CONVERT(nvarchar(30), CAST(source_row.'' + QUOTENAME(@source_identifier) + N'' / 8.0 AS decimal(38,2))),
                                        N'''' days (8h/day)''''
                                    )''
                                WHEN @source_type IN (N''decimal'', N''numeric'', N''money'', N''smallmoney'', N''float'', N''real'')
                                    THEN N''CONVERT(nvarchar(max), CAST(source_row.'' + QUOTENAME(@source_identifier)
                                         + N'' AS decimal(38,2)))''';

    IF @refresh_definition IS NULL
        THROW 51663, 'The refresh procedure could not be read.', 1;

    IF CHARINDEX(N'Configured evidence pass v8', @refresh_definition) = 0
    BEGIN
        IF CHARINDEX(N'Configured evidence pass v6', @refresh_definition) = 0
           OR CHARINDEX(@numeric_value_expression, @refresh_definition) = 0
           OR CHARINDEX(N'@display_label, @sort_order, @source_table', @refresh_definition) = 0
            THROW 51664, 'The configured evidence v6 resolver could not be located.', 1;

        SET @refresh_definition = REPLACE(
            @refresh_definition,
            N'@display_label, @sort_order, @source_table',
            N'@display_label, @sort_order, @display_format, @source_table'
        );
        SET @refresh_definition = REPLACE(
            @refresh_definition,
            N'@sort_order int,' + NCHAR(13) + NCHAR(10) + N'                @source_table sysname,',
            N'@sort_order int,' + NCHAR(13) + NCHAR(10) + N'                @display_format varchar(40),' + NCHAR(13) + NCHAR(10) + N'                @source_table sysname,'
        );
        SET @refresh_definition = REPLACE(
            @refresh_definition,
            N'field.sort_order,' + NCHAR(13) + NCHAR(10) + N'                    CASE LOWER(field.source_category)',
            N'field.sort_order,' + NCHAR(13) + NCHAR(10) + N'                    field.display_format,' + NCHAR(13) + NCHAR(10) + N'                    CASE LOWER(field.source_category)'
        );
        SET @refresh_definition = REPLACE(
            @refresh_definition,
            @numeric_value_expression,
            @hours_and_days_expression
        );
        SET @refresh_definition = REPLACE(
            @refresh_definition,
            N'@field_check_code varchar(50)'',',
            N'@field_check_code varchar(50), @display_format varchar(40)'','
        );
        SET @refresh_definition = REPLACE(
            @refresh_definition,
            N'@detail_field_id, @display_label, @sort_order, @field_check_code;',
            N'@detail_field_id, @display_label, @sort_order, @field_check_code, @display_format;'
        );
        SET @refresh_definition = REPLACE(
            @refresh_definition,
            N'Configured evidence pass v6: generic P6 table resolver plus open-end relationship context.',
            N'Configured evidence pass v8: configured native or P6-hours-and-days evidence formats.'
        );
        SET @refresh_definition = REPLACE(@refresh_definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @refresh_definition = REPLACE(@refresh_definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @refresh_definition;
    END;

    IF COL_LENGTH(N'powerbitables.xertoolkit_schedule_quality_detail_field', N'display_format') IS NULL
       OR CHARINDEX(N'display_format', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_save_schedule_quality_draft]'))) = 0
       OR CHARINDEX(N'Configured evidence pass v8', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
       OR CHARINDEX(@hours_and_days_expression, OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
        THROW 51665, 'Configured evidence display formats were not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
