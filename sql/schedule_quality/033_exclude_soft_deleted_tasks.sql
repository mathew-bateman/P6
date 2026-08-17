/*
    Exclude physically soft-deleted P6 TASK rows from every schedule-quality
    result.  029 excludes the business Activity Status = DEL code, but a TASK
    whose delete_session_id/delete_date is populated could still enter the
    Activities view and the direct TASK out-of-sequence function.

    This forward patch snapshots both changed module definitions before
    altering them.  Run 933_exclude_soft_deleted_tasks_rollback.sql to restore
    that snapshot if this specific patch must be reversed.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51694, 'This patch must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]', N'V') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]', N'IF') IS NULL
    THROW 51695, 'Deploy the deleted-activity schedule-quality SQL first.', 1;

BEGIN TRANSACTION;
GO

IF SCHEMA_ID(N'xertoolkit_rollback') IS NULL
    EXEC(N'CREATE SCHEMA [xertoolkit_rollback] AUTHORIZATION [dbo];');

IF OBJECT_ID(N'[xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]', N'U') IS NULL
BEGIN
    CREATE TABLE [xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]
    (
        object_name sysname NOT NULL PRIMARY KEY,
        object_type char(2) NOT NULL,
        definition nvarchar(max) NOT NULL,
        captured_at datetime2(7) NOT NULL
            CONSTRAINT [DF_sq_20260817_soft_deleted_task_modules_captured_at]
            DEFAULT SYSUTCDATETIME()
    );

    INSERT INTO [xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]
        (object_name, object_type, definition)
    SELECT o.name, o.type, m.definition
    FROM sys.objects AS o
    JOIN sys.schemas AS s ON s.schema_id = o.schema_id
    JOIN sys.sql_modules AS m ON m.object_id = o.object_id
    WHERE s.name = N'powerbitables'
      AND o.name IN
      (
          N'xertoolkit_vw_PBI_Activities',
          N'xertoolkit_fn_out_of_sequence'
      );

    IF (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]) <> 2
        THROW 51696, 'The soft-deleted TASK rollback snapshot is incomplete.', 1;
END;
GO

DECLARE @activities_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]'));

IF @activities_definition IS NULL
    THROW 51697, 'The Activities view definition could not be read.', 1;

IF CHARINDEX(N't.delete_session_id IS NULL', @activities_definition) = 0
BEGIN
    IF CHARINDEX(N'AND deleted.task_id = t.task_id;', @activities_definition) = 0
        THROW 51698, 'The Activities view did not match the expected 029 shape.', 1;

    SET @activities_definition = REPLACE
    (
        @activities_definition,
        N'AND deleted.task_id = t.task_id;',
        N'AND deleted.task_id = t.task_id\nWHERE t.delete_session_id IS NULL\n  AND t.delete_date IS NULL;'
    );
    EXEC sys.sp_executesql @activities_definition;
END;
GO

DECLARE @out_of_sequence_definition nvarchar(max) =
    OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'));

IF @out_of_sequence_definition IS NULL
    THROW 51699, 'The out-of-sequence function definition could not be read.', 1;

IF CHARINDEX(N'pred.delete_session_id IS NULL', @out_of_sequence_definition) = 0
BEGIN
    IF CHARINDEX(N'AND pred.task_id = r.predecessor_task_id', @out_of_sequence_definition) = 0
       OR CHARINDEX(N'AND succ.task_id = r.successor_task_id', @out_of_sequence_definition) = 0
        THROW 51700, 'The optimized out-of-sequence function did not match the expected shape.', 1;

    SET @out_of_sequence_definition = REPLACE
    (
        @out_of_sequence_definition,
        N'AND pred.task_id = r.predecessor_task_id',
        N'AND pred.task_id = r.predecessor_task_id\n     AND pred.delete_session_id IS NULL\n     AND pred.delete_date IS NULL'
    );
    SET @out_of_sequence_definition = REPLACE
    (
        @out_of_sequence_definition,
        N'AND succ.task_id = r.successor_task_id',
        N'AND succ.task_id = r.successor_task_id\n     AND succ.delete_session_id IS NULL\n     AND succ.delete_date IS NULL'
    );
    EXEC sys.sp_executesql @out_of_sequence_definition;
END;
GO

IF CHARINDEX(N't.delete_session_id IS NULL', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]'))) = 0
   OR CHARINDEX(N't.delete_date IS NULL', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_Activities]'))) = 0
   OR CHARINDEX(N'pred.delete_session_id IS NULL', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))) = 0
   OR CHARINDEX(N'succ.delete_session_id IS NULL', OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))) = 0
    THROW 51701, 'The soft-deleted TASK predicates were not deployed.', 1;

COMMIT TRANSACTION;
GO

SELECT
    N'soft-deleted TASK exclusion deployed' AS deployment_status,
    (SELECT COUNT(*) FROM [xertoolkit_rollback].[schedule_quality_20260817_soft_deleted_task_modules]) AS rollback_module_count;
GO
