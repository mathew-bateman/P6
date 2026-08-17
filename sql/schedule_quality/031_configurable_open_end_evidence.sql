/*
    Make Open Start and Open Finish relationship context configuration-owned.

    The evidence editor stores genuine P6 selections:
      - TASKPRED.pred_type
      - TASK.task_code
      - TASK.task_name

    Open Start resolves TASK through TASKPRED.pred_task_id (the predecessor).
    Open Finish resolves TASK through TASKPRED.task_id (the successor). The
    former fixed four-row SQL injection is removed, so adding, removing,
    relabelling, and reordering these fields in the settings page controls the
    materialised evidence exactly like every other configured field.

    Safe to rerun. This script changes only the refresh procedure. The current
    draft is migrated by the Django form from the legacy relationship_summary
    placeholder when the settings page is opened and then saved or published.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51720, 'This hotfix must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]', N'P') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_schedule_quality_detail_field]', N'U') IS NULL
    THROW 51721, 'Deploy configured schedule-quality evidence first.', 1;

BEGIN TRY
    BEGIN TRANSACTION;

    DECLARE @definition nvarchar(max) =
        OBJECT_DEFINITION
        (
            OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]')
        );

    IF @definition IS NULL
        THROW 51722, 'The current refresh procedure definition could not be read.', 1;

    SET @definition = REPLACE(@definition, NCHAR(13) + NCHAR(10), NCHAR(10));

    IF CHARINDEX(N'CONFIGURABLE_OPEN_END_EVIDENCE_V1', @definition) = 0
    BEGIN
        DECLARE @fixed_start_marker nvarchar(200) =
            N'            /*
                Open-end evidence comes directly from P6 TASKPRED and TASK.';
        DECLARE @declaration_marker nvarchar(200) =
            N'            DECLARE
                @detail_field_id bigint,';
        DECLARE @fixed_start int = CHARINDEX(@fixed_start_marker, @definition);
        DECLARE @declaration_start int =
            CHARINDEX(@declaration_marker, @definition, @fixed_start);

        IF @fixed_start = 0 OR @declaration_start <= @fixed_start
            THROW 51723, 'The fixed open-end evidence block could not be located.', 1;

        SET @definition = STUFF
        (
            @definition,
            @fixed_start,
            @declaration_start - @fixed_start,
            N'            -- CONFIGURABLE_OPEN_END_EVIDENCE_V1
'
        );

        DECLARE @relationship_anchor nvarchar(max) =
            N'                        IF @join_column = N''relationship_endpoint''
                         AND @field_check_code IN';
        DECLARE @relationship_replacement nvarchar(max) =
            N'                        IF @join_column = N''relationship_endpoint''
                         AND @field_check_code = N''open_start''
                            SET @join_predicate = N''source_row.[task_id] = evidence.task_id
                                AND source_row.[proj_id] = evidence.proj_id
                                AND source_row.[delete_session_id] IS NULL'';
                        ELSE IF @join_column = N''relationship_endpoint''
                         AND @field_check_code = N''open_finish''
                            SET @join_predicate = N''source_row.[pred_task_id] = evidence.task_id
                                AND source_row.[proj_id] = evidence.proj_id
                                AND source_row.[delete_session_id] IS NULL'';
                        ELSE IF @join_column = N''relationship_endpoint''
                         AND @field_check_code IN';

        IF CHARINDEX(@relationship_anchor, @definition) = 0
            THROW 51724, 'The relationship endpoint resolver could not be located.', 1;
        SET @definition = REPLACE
        (
            @definition,
            @relationship_anchor,
            @relationship_replacement
        );

        DECLARE @task_anchor nvarchar(max) =
            N'                        ELSE IF @join_column = N''task_id''
                            SET @join_predicate = N''source_row.[task_id] = evidence.task_id'';';
        DECLARE @task_replacement nvarchar(max) =
            N'                        ELSE IF @source_table = N''TASK''
                         AND @field_check_code = N''open_start''
                            SET @join_predicate = N''EXISTS
                            (
                                SELECT 1
                                FROM dbo.TASKPRED AS open_relationship
                                WHERE open_relationship.proj_id = evidence.proj_id
                                  AND open_relationship.task_id = evidence.task_id
                                  AND open_relationship.pred_task_id = source_row.[task_id]
                                  AND open_relationship.delete_session_id IS NULL
                            )
                            AND source_row.[proj_id] = evidence.proj_id
                            AND source_row.[delete_session_id] IS NULL'';
                        ELSE IF @source_table = N''TASK''
                         AND @field_check_code = N''open_finish''
                            SET @join_predicate = N''EXISTS
                            (
                                SELECT 1
                                FROM dbo.TASKPRED AS open_relationship
                                WHERE open_relationship.proj_id = evidence.proj_id
                                  AND open_relationship.pred_task_id = evidence.task_id
                                  AND open_relationship.task_id = source_row.[task_id]
                                  AND open_relationship.delete_session_id IS NULL
                            )
                            AND source_row.[proj_id] = evidence.proj_id
                            AND source_row.[delete_session_id] IS NULL'';
                        ELSE IF @join_column = N''task_id''
                            SET @join_predicate = N''source_row.[task_id] = evidence.task_id'';';

        IF CHARINDEX(@task_anchor, @definition) = 0
            THROW 51725, 'The task resolver could not be located.', 1;
        SET @definition = REPLACE(@definition, @task_anchor, @task_replacement);

        DECLARE @expression_anchor nvarchar(max) =
            N'                            SET @source_expression = CASE
                                WHEN @source_table = N''TASK''';
        DECLARE @expression_replacement nvarchar(max) =
            N'                            SET @source_expression = CASE
                                WHEN @source_table = N''TASKPRED''
                                 AND @source_identifier = N''pred_type''
                                    THEN N''REPLACE(source_row.[pred_type], N''''PR_'''', N'''''''')''
                                WHEN @source_table = N''TASK''';

        IF CHARINDEX(@expression_anchor, @definition) = 0
            THROW 51726, 'The evidence value formatter could not be located.', 1;
        SET @definition = REPLACE
        (
            @definition,
            @expression_anchor,
            @expression_replacement
        );

        SET @definition = REPLACE(@definition, N'CREATE   PROCEDURE', N'ALTER PROCEDURE');
        SET @definition = REPLACE(@definition, N'CREATE PROCEDURE', N'ALTER PROCEDURE');
        EXEC sys.sp_executesql @definition;
    END;

    DECLARE @deployed_definition nvarchar(max) =
        OBJECT_DEFINITION
        (
            OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]')
        );

    IF CHARINDEX(N'CONFIGURABLE_OPEN_END_EVIDENCE_V1', @deployed_definition) = 0
       OR CHARINDEX(N'Open-end evidence comes directly from P6 TASKPRED and TASK', @deployed_definition) <> 0
       OR CHARINDEX(N'open_relationship.pred_task_id = source_row.[task_id]', @deployed_definition) = 0
       OR CHARINDEX(N'open_relationship.task_id = source_row.[task_id]', @deployed_definition) = 0
       OR CHARINDEX(N'REPLACE(source_row.[pred_type]', @deployed_definition) = 0
        THROW 51727, 'Configurable open-end evidence was not deployed.', 1;

    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
GO
