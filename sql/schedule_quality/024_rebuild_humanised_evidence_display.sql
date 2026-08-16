/* Rebuild the stored Power BI display from the current, humanised detail_json. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51602, 'This hotfix must be run against P62212_1.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    UPDATE evidence
    SET evidence.detail_display = display_text.detail_display
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
    OUTER APPLY
    (
        SELECT STRING_AGG
        (
            CONCAT(json_value.[key], N': ', COALESCE(json_value.value, N'')),
            CHAR(13) + CHAR(10)
        ) WITHIN GROUP
        (
            ORDER BY CASE json_value.[key]
                WHEN 'Task Code' THEN 1
                WHEN 'Planner' THEN 2
                WHEN 'Total Float' THEN 3
                WHEN 'start_date' THEN 4
                WHEN 'end_date' THEN 5
                WHEN 'total_float_hr' THEN 6
                ELSE 99
            END
        ) AS detail_display
        FROM OPENJSON(evidence.detail_json) AS json_value
        WHERE json_value.[key] <> 'display'
    ) AS display_text
    WHERE evidence.detail_json IS NOT NULL;

    IF EXISTS
    (
        SELECT 1
        FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
        WHERE JSON_VALUE(detail_json, N'$.start_date') LIKE N'____-__-__T%'
           OR JSON_VALUE(detail_json, N'$.end_date') LIKE N'____-__-__T%'
           OR detail_display LIKE N'%start_date: ____-__-__T%'
           OR detail_display LIKE N'%end_date: ____-__-__T%'
    )
        THROW 51603, 'An ISO timestamp remains in the evidence payload or its display text.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
