/* Keep historic evidence readable when its source TASK has since been deleted. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51593, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'));
    DECLARE @marker nvarchar(100) = N'            WHERE e.check_run_id = @check_run_id;';
    DECLARE @position int = CHARINDEX(@marker, @definition);

    IF @definition IS NULL OR @position = 0
        THROW 51594, 'The task-evidence refresh completion marker could not be located.', 1;

    IF CHARINDEX(N'Humanised evidence fallback', @definition) = 0
    BEGIN
        DECLARE @fallback nvarchar(max) = N'

            /* Humanised evidence fallback: a historic task can be deleted after its snapshot. */
            UPDATE e
            SET e.detail_display = CONCAT
                (
                    N''Total Float: '',
                    COALESCE(JSON_VALUE(e.detail_json, N''$."Total Float"''), N''Unknown''),
                    N'' days''
                )
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e
            WHERE e.check_run_id = @check_run_id
              AND e.detail_json IS NOT NULL
              AND NULLIF(e.detail_display, N'''') IS NULL;

            UPDATE e
            SET e.detail_json = JSON_MODIFY(e.detail_json, N''$.display'', e.detail_display)
            FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e
            WHERE e.check_run_id = @check_run_id
              AND e.detail_json IS NOT NULL
              AND NULLIF(e.detail_display, N'''') IS NOT NULL;
';
        SET @definition = STUFF(
            @definition,
            @position + LEN(@marker),
            0,
            @fallback
        );
        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    UPDATE e
    SET
        e.detail_display = CONCAT
        (
            N'Total Float: ',
            COALESCE(JSON_VALUE(e.detail_json, N'$."Total Float"'), N'Unknown'),
            N' days'
        ),
        e.detail_json = JSON_MODIFY
        (
            e.detail_json,
            N'$.display',
            CONCAT
            (
                N'Total Float: ',
                COALESCE(JSON_VALUE(e.detail_json, N'$."Total Float"'), N'Unknown'),
                N' days'
            )
        )
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS e
    WHERE e.detail_json IS NOT NULL
      AND e.detail_display IS NULL;

    IF EXISTS
    (
        SELECT 1
        FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        WHERE detail_json IS NOT NULL
          AND detail_display IS NULL
    )
        THROW 51595, 'Some populated evidence rows still have no humanised display.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
