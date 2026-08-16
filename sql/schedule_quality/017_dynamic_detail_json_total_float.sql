/*
    Add calculated total float to the dynamic task-evidence detail_json payload.

    P6 stores total float in hours on TASK.total_float_hr_cnt.  The reporting
    contract uses working days, so this writes the same decimal-day calculation
    as xertoolkit_vw_PBI_Activities: hours / 8.0.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51560, 'This hotfix must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]', N'P') IS NULL
    THROW 51561, 'The versioned schedule-quality refresh is not deployed.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));

    IF @definition IS NULL
        THROW 51562, 'The current refresh procedure definition could not be read.', 1;

    IF CHARINDEX(N'AS [Total Float]', @definition) = 0
    BEGIN
        IF @definition NOT LIKE N'%Dynamic detail_json enrichment pass%'
           OR @definition NOT LIKE N'%(SELECT ac.actv_code_name%'
            THROW 51563, 'The refresh procedure does not contain the expected dynamic detail_json block.', 1;

        SET @definition = REPLACE
        (
            @definition,
            N'                    (SELECT ac.actv_code_name ',
            N'                    (SELECT CAST(t.total_float_hr_cnt / 8.0 AS decimal(18,2))
                     FROM dbo.TASK AS t
                     WHERE t.task_id = e.task_id) AS [Total Float],
                    (SELECT ac.actv_code_name '
        );

        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');

        IF @definition NOT LIKE N'%ALTER PROCEDURE%'
            THROW 51564, 'The refresh procedure header could not be normalized to ALTER PROCEDURE.', 1;

        EXEC sys.sp_executesql @definition;
    END;

    IF OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
           NOT LIKE N'%total_float_hr_cnt / 8.0 AS decimal(18,2)%'
       OR CHARINDEX
          (
              N'AS [Total Float]',
              OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
          ) = 0
        THROW 51565, 'Total Float was not added to the dynamic detail_json payload.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0
        ROLLBACK TRANSACTION;
    THROW;
END CATCH;

SELECT CAST
(
    CASE WHEN OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
                  LIKE N'%total_float_hr_cnt / 8.0 AS decimal(18,2)%'
               AND CHARINDEX
                   (
                       N'AS [Total Float]',
                       OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
                   ) > 0
         THEN 1 ELSE 0 END
    AS bit
) AS total_float_detail_json_enabled;
GO
