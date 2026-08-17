/* Restore the two module definitions captured by 033 before its deployment. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51702, 'This rollback must be run against P62212_1.', 1;

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]', N'U') IS NULL
    THROW 51703, 'The 033 rollback snapshot is unavailable.', 1;

BEGIN TRANSACTION;

DECLARE @definition nvarchar(max);
DECLARE module_cursor CURSOR LOCAL FAST_FORWARD FOR
    SELECT definition
    FROM [xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]
    ORDER BY object_name;

OPEN module_cursor;
FETCH NEXT FROM module_cursor INTO @definition;
WHILE @@FETCH_STATUS = 0
BEGIN
    EXEC sys.sp_executesql @definition;
    FETCH NEXT FROM module_cursor INTO @definition;
END;
CLOSE module_cursor;
DEALLOCATE module_cursor;

COMMIT TRANSACTION;
GO
