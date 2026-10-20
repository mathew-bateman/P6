/* Enable the versioned Include LOE setting for Logical Loops.
   Legacy NULL means include LOE, preserving published results and hashes.
   A later draft save stores an explicit bit through the existing audit flow. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'P62212_1'
    THROW 51780, 'Run this migration against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @refresh nvarchar(max) = OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_refresh_all_schedule_quality'));
    DECLARE @save nvarchar(max) = OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_save_schedule_quality_draft'));
    IF @refresh IS NULL OR @save IS NULL
        THROW 51781, 'The versioned refresh and settings procedures are required.', 1;

    IF CHARINDEX(N'/* LOGICAL_LOOP_LOE_SCOPE_V1 */', @refresh) = 0
    BEGIN
        DECLARE @refresh_marker nvarchar(max) = N'        IF @logical_loops_enabled = 1';
        DECLARE @edge_marker nvarchar(max) = N'AND (@exclude_deleted_activities = 0 OR (predecessor.is_deleted = 0 AND successor.is_deleted = 0));';
        IF CHARINDEX(@refresh_marker, @refresh) = 0 OR CHARINDEX(@edge_marker, @refresh) = 0
            THROW 51782, 'The expected loop calculation could not be located.', 1;
        SET @refresh = REPLACE(@refresh, @refresh_marker, N'        /* LOGICAL_LOOP_LOE_SCOPE_V1 */
        DECLARE @logical_loops_include_loe bit = ISNULL
        (
            (SELECT include_loe
             FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
             WHERE config_version_id = @config_version_id
               AND check_code = ''logical_loops''),
            1
        );

' + @refresh_marker);
        SET @refresh = REPLACE(@refresh, @edge_marker, N'AND (@exclude_deleted_activities = 0 OR (predecessor.is_deleted = 0 AND successor.is_deleted = 0))
              AND (@logical_loops_include_loe = 1 OR (predecessor.is_loe = 0 AND successor.is_loe = 0));');
        SET @refresh = STUFF(@refresh, 1, CHARINDEX(N'PROCEDURE', UPPER(@refresh)) + 8, N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @refresh;
    END;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
