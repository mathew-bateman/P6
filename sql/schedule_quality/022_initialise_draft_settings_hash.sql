/* Ensure every cloned settings draft has a valid optimistic-lock token. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51610, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_get_or_create_schedule_quality_draft]'));
    DECLARE @copy_marker nvarchar(100) = N'-- Copy detail fields from active version';
    DECLARE @copy_position int = CHARINDEX(@copy_marker, @definition);
    DECLARE @insert_position int = CHARINDEX(N'        END', @definition, @copy_position);

    IF @definition IS NULL OR @copy_position = 0 OR @insert_position = 0
        THROW 51611, 'The draft clone block could not be located.', 1;

    IF CHARINDEX(N'Copy the active version hash', @definition) = 0
    BEGIN
        SET @definition = STUFF
        (
            @definition,
            @insert_position,
            0,
            N'
            -- Copy the active version hash: the cloned rows are identical
            -- until the editor saves a change, so this is a valid lock token.
            UPDATE draft
            SET settings_hash = active_version.settings_hash
            FROM [powerbitables].[xertoolkit_schedule_quality_config_version] AS draft
            INNER JOIN [powerbitables].[xertoolkit_schedule_quality_config_version] AS active_version
                ON active_version.config_version_id = @active_config_version_id
            WHERE draft.config_version_id = @config_version_id;

'
        );
        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    /* Repair any pre-hotfix draft created with a blank hash. */
    UPDATE draft
    SET settings_hash = active_version.settings_hash
    FROM [powerbitables].[xertoolkit_schedule_quality_config_version] AS draft
    INNER JOIN [powerbitables].[xertoolkit_schedule_quality_config_version] AS active_version
        ON active_version.config_version_id = draft.based_on_config_version_id
    WHERE draft.state = 'draft'
      AND (draft.settings_hash IS NULL OR LEN(RTRIM(draft.settings_hash)) <> 64);

    IF EXISTS
    (
        SELECT 1
        FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
        WHERE state = 'draft'
          AND (settings_hash IS NULL OR LEN(RTRIM(settings_hash)) <> 64)
    )
        THROW 51612, 'A schedule-quality draft still has no valid settings hash.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
