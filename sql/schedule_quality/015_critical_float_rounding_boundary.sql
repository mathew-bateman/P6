/* Correct Critical Tasks to the half-day boundary of an eight-hour day. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51910, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]', N'IF') IS NULL
    THROW 51911, 'Deploy the schedule-quality activity function first.', 1;

DECLARE @definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'));

IF @definition LIKE N'%total_float_hr_cnt < 24.0%'
   AND @definition LIKE N'%total_float_hr_cnt >= 24.0%'
BEGIN
    SET @definition = REPLACE
    (
        @definition,
        N'total_float_hr_cnt < 24.0',
        N'total_float_hr_cnt < 4.0'
    );
    SET @definition = REPLACE
    (
        @definition,
        N'total_float_hr_cnt >= 24.0',
        N'total_float_hr_cnt >= 4.0'
    );
END
ELSE IF @definition LIKE N'%total_float_hr_cnt <= 0%'
    AND @definition LIKE N'%total_float_hr_cnt > 0%'
BEGIN
    SET @definition = REPLACE
    (
        @definition,
        N'total_float_hr_cnt <= 0',
        N'total_float_hr_cnt < 4.0'
    );
    SET @definition = REPLACE
    (
        @definition,
        N'total_float_hr_cnt > 0',
        N'total_float_hr_cnt >= 4.0'
    );
END
ELSE
    THROW 51912, 'The activity-quality function does not have a supported prior critical boundary.', 1;

SET @definition =
    N'CREATE OR ALTER ' + SUBSTRING(@definition, CHARINDEX(N'FUNCTION', @definition), LEN(@definition));

EXEC sys.sp_executesql @definition;

IF OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
       NOT LIKE N'%total_float_hr_cnt < 4.0%'
   OR OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
       NOT LIKE N'%total_float_hr_cnt >= 4.0%'
    THROW 51913, 'The four-hour critical boundary was not installed.', 1;

SELECT
    N'critical below 4 hours' AS critical_boundary,
    N'near critical from 4 hours' AS near_critical_boundary;
GO
