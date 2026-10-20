/* NR classification: Start On and Finish On remain hard constraints.
   FYI soft constraints are only Start/Finish On or Before / On or After.
   The existing hard-check settings, FYI scope and scoring exclusions survive. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'P62212_1' THROW 51940,'Run against P62212_1.',1;
BEGIN TRANSACTION;
IF OBJECT_ID(N'powerbitables.xertoolkit_nr_soft_constraints_backup',N'U') IS NULL
BEGIN
    CREATE TABLE powerbitables.xertoolkit_nr_soft_constraints_backup
        (object_name sysname NOT NULL PRIMARY KEY,definition nvarchar(max) NOT NULL);
    INSERT powerbitables.xertoolkit_nr_soft_constraints_backup
    SELECT N'xertoolkit_fn_soft_constraints',OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_fn_soft_constraints'));
END;
GO
CREATE OR ALTER FUNCTION powerbitables.xertoolkit_fn_soft_constraints(@config_version_id bigint)
RETURNS TABLE
AS RETURN
(
    SELECT scope.proj_id,scope.task_id
    FROM powerbitables.xertoolkit_fn_activity_in_scope(@config_version_id,'soft_constraints') AS scope
    JOIN dbo.TASK AS activity ON activity.proj_id=scope.proj_id AND activity.task_id=scope.task_id
    WHERE activity.cstr_type IN ('CS_MSOB','CS_MSOA','CS_MEOB','CS_MEOA')
       OR activity.cstr_type2 IN ('CS_MSOB','CS_MSOA','CS_MEOB','CS_MEOA')
);
GO
COMMIT TRANSACTION;
