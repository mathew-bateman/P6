/* Restore the prior zero-hour Critical Tasks boundary. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51900, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]', N'IF') IS NULL
    THROW 51901, 'The schedule-quality activity function is missing.', 1;

DECLARE @definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'));

IF @definition NOT LIKE N'%total_float_hr_cnt < 24.0%'
   OR @definition NOT LIKE N'%total_float_hr_cnt >= 24.0%'
    THROW 51902, 'The activity-quality function does not have the under-24-hour boundaries.', 1;

SET @definition = REPLACE
(
    @definition,
    N'total_float_hr_cnt < 24.0',
    N'total_float_hr_cnt <= 0'
);
SET @definition = REPLACE
(
    @definition,
    N'total_float_hr_cnt >= 24.0',
    N'total_float_hr_cnt > 0'
);
SET @definition =
    N'CREATE OR ALTER ' + SUBSTRING(@definition, CHARINDEX(N'FUNCTION', @definition), LEN(@definition));

EXEC sys.sp_executesql @definition;

IF OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
       NOT LIKE N'%total_float_hr_cnt <= 0%'
   OR OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
       NOT LIKE N'%total_float_hr_cnt > 0%'
    THROW 51903, 'The zero-hour critical boundary was not restored.', 1;

SELECT
    N'critical at or below zero hours' AS restored_critical_boundary,
    N'near critical above zero hours' AS restored_near_critical_boundary;
GO
