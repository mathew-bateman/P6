/*
    Make the dynamic detail_json enrichment safe for activities with more than
    one Planner code and add Total Float without rebuilding the whole payload.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51570, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @start_marker nvarchar(100) = N'            SET e.detail_json = (';
    DECLARE @end_marker nvarchar(200) =
        N'            WHERE e.check_run_id = @check_run_id;';
    DECLARE @start_position int = CHARINDEX(@start_marker, @definition);
    DECLARE @end_position int = CHARINDEX(@end_marker, @definition, @start_position);

    IF @definition IS NULL OR @start_position = 0 OR @end_position = 0
        THROW 51571, 'The expected dynamic detail_json block could not be located.', 1;

    IF CHARINDEX(N'JSON_MODIFY', @definition, @start_position) = 0
    BEGIN
        DECLARE @replacement nvarchar(max) = N'            SET e.detail_json = JSON_MODIFY
            (
                COALESCE(e.detail_json, N''{}''),
                N''$."Total Float"'',
                (SELECT CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))
                 FROM dbo.TASK AS t
                 WHERE t.task_id = e.task_id)
            )
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e
';

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
    END;

    IF CHARINDEX(N'JSON_MODIFY', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))) = 0
        THROW 51572, 'The safe Total Float detail_json enrichment was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
