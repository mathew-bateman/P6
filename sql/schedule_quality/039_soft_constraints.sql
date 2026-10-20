/* Separate, versioned Soft Constraints check. NR's four soft date
   constraints are checked in both primary and secondary slots. One activity
   counts once. Existing Constraints settings and published versions survive. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME() <> N'P62212_1' THROW 51900, 'Run against P62212_1.', 1;
BEGIN TRANSACTION;

IF OBJECT_ID(N'powerbitables.xertoolkit_soft_constraints_module_backup', N'U') IS NULL
BEGIN
    CREATE TABLE powerbitables.xertoolkit_soft_constraints_module_backup
    (object_name sysname NOT NULL PRIMARY KEY, definition nvarchar(max) NOT NULL);
    INSERT powerbitables.xertoolkit_soft_constraints_module_backup
    SELECT o.name, sm.definition FROM sys.objects o
    JOIN sys.sql_modules sm ON sm.object_id=o.object_id
    WHERE SCHEMA_NAME(o.schema_id)='powerbitables' AND o.name IN
    ('xertoolkit_refresh_all_schedule_quality', 'xertoolkit_get_or_create_schedule_quality_draft',
     'xertoolkit_fn_schedule_quality_task_evidence','xertoolkit_vw_PBI_ProjectMetrics',
     'xertoolkit_vw_PBI_ScheduleQualityResults');
    IF @@ROWCOUNT <> 5 THROW 51901, 'Required modules are missing.', 1;
END;

IF COL_LENGTH(N'powerbitables.xertoolkit_result_project_metrics',N'soft_constraint_count') IS NULL
    ALTER TABLE powerbitables.xertoolkit_result_project_metrics ADD
        soft_constraint_count int NOT NULL CONSTRAINT DF_xertoolkit_soft_constraint_count DEFAULT(0),
        soft_constraint_activity_count int NOT NULL CONSTRAINT DF_xertoolkit_soft_constraint_activity_count DEFAULT(0);

DECLARE @policy nvarchar(max)=(SELECT definition FROM sys.check_constraints
    WHERE parent_object_id=OBJECT_ID('powerbitables.xertoolkit_schedule_quality_check_scope')
      AND name='CK_xertoolkit_sq_check_score_policy');
IF @policy IS NULL THROW 51902, 'Score policy constraint is missing.', 1;
IF CHARINDEX('soft_constraints',@policy)=0
BEGIN
    SET @policy=REPLACE(@policy,N'[qualifying_metric]=''constraints''',N'[qualifying_metric]=''soft_constraints'' OR [qualifying_metric]=''constraints''');
    SET @policy=REPLACE(@policy,N'[records_metric]=''dcma_activity''',N'[records_metric]=''soft_constraint_activity'' OR [records_metric]=''dcma_activity''');
    IF CHARINDEX('soft_constraint_activity',@policy)=0 THROW 51903, 'Score policy shape changed.', 1;
    ALTER TABLE powerbitables.xertoolkit_schedule_quality_check_scope DROP CONSTRAINT CK_xertoolkit_sq_check_score_policy;
    EXEC(N'ALTER TABLE powerbitables.xertoolkit_schedule_quality_check_scope ADD CONSTRAINT CK_xertoolkit_sq_check_score_policy CHECK '+@policy);
END;
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name='CK_xertoolkit_soft_constraints_fyi')
    ALTER TABLE powerbitables.xertoolkit_schedule_quality_check_scope
    ADD CONSTRAINT CK_xertoolkit_soft_constraints_fyi CHECK
        (check_code <> 'soft_constraints' OR (green_points=0 AND amber_points=0));
GO
CREATE OR ALTER FUNCTION powerbitables.xertoolkit_fn_soft_constraints(@config_version_id bigint)
RETURNS TABLE
AS RETURN
(
    SELECT scope.proj_id, scope.task_id
    FROM powerbitables.xertoolkit_fn_activity_in_scope(@config_version_id,'soft_constraints') AS scope
    JOIN dbo.TASK AS activity ON activity.proj_id=scope.proj_id AND activity.task_id=scope.task_id
    WHERE activity.cstr_type IN ('CS_MSOB','CS_MSOA','CS_MEOB','CS_MEOA')
       OR activity.cstr_type2 IN ('CS_MSOB','CS_MSOA','CS_MEOB','CS_MEOA')
);
GO
DECLARE @evidence nvarchar(max)=OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_fn_schedule_quality_task_evidence'));
IF CHARINDEX('SOFT_CONSTRAINT_EVIDENCE_V1',@evidence)=0
BEGIN
    DECLARE @marker nvarchar(max)=N'    )
    SELECT DISTINCT';
    IF CHARINDEX(@marker,@evidence)=0 THROW 51904, 'Evidence function shape changed.',1;
    SET @evidence=REPLACE(@evidence,@marker,N'        /* SOFT_CONSTRAINT_EVIDENCE_V1 */
        UNION ALL
        SELECT soft.proj_id, ''soft_constraints'', soft.task_id, ''soft_constraint''
        FROM powerbitables.xertoolkit_fn_soft_constraints(@config_version_id) AS soft
'+@marker);
    SET @evidence=STUFF(@evidence,1,CHARINDEX('FUNCTION',UPPER(@evidence))+7,N'ALTER FUNCTION');
    EXEC sys.sp_executesql @evidence;
END;

DECLARE @refresh nvarchar(max)=OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_refresh_all_schedule_quality'));
IF CHARINDEX('SOFT_CONSTRAINT_METRICS_V1',@refresh)=0
BEGIN
    DECLARE @count_marker nvarchar(max)=N'WHERE config_version_id = @config_version_id) <> 20';
    DECLARE @history_marker nvarchar(max)=N'        UPDATE [powerbitables].[xertoolkit_refresh_run_history]
        SET
            completed_at';
    IF (CHARINDEX(@count_marker,@refresh)=0 AND CHARINDEX(N'THEN 21 ELSE 20 END',@refresh)=0)
       OR CHARINDEX(@history_marker,@refresh)=0
        THROW 51905, 'Refresh procedure shape changed.',1;
    DECLARE @stage_marker nvarchar(max)=N'        CREATE UNIQUE CLUSTERED INDEX [IX_ProjectMetricsStage_proj_id]';
    IF CHARINDEX(@stage_marker,@refresh)=0 THROW 51910, 'Metrics stage shape changed.',1;
    IF CHARINDEX(N'ADD DEFAULT(0) FOR soft_constraint_count',@refresh)=0
    SET @refresh=REPLACE(@refresh,@stage_marker,N'        ALTER TABLE #ProjectMetricsStage ADD DEFAULT(0) FOR soft_constraint_count;
        ALTER TABLE #ProjectMetricsStage ADD DEFAULT(0) FOR soft_constraint_activity_count;

'+@stage_marker);
    SET @refresh=REPLACE(@refresh,@count_marker,N'WHERE config_version_id = @config_version_id) <>
              CASE WHEN EXISTS (SELECT 1 FROM powerbitables.xertoolkit_schedule_quality_check_scope
                    WHERE config_version_id=@config_version_id AND check_code=''soft_constraints'') THEN 21 ELSE 20 END');
    SET @refresh=REPLACE(@refresh,@history_marker,N'        /* SOFT_CONSTRAINT_METRICS_V1: same run/version as the evidence. */
        ;WITH scoped AS
        (
            SELECT proj_id, COUNT(*) AS checked
            FROM powerbitables.xertoolkit_fn_activity_in_scope(@config_version_id,''soft_constraints'')
            GROUP BY proj_id
        ), matched AS
        (
            SELECT proj_id, COUNT(*) AS qualifying
            FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence
            WHERE check_run_id=@check_run_id AND config_version_id=@config_version_id
              AND check_code=''soft_constraints''
            GROUP BY proj_id
        )
        UPDATE metrics SET soft_constraint_count=ISNULL(matched.qualifying,0),
            soft_constraint_activity_count=ISNULL(scoped.checked,0)
        FROM powerbitables.xertoolkit_result_project_metrics AS metrics
        LEFT JOIN scoped ON scoped.proj_id=metrics.proj_id
        LEFT JOIN matched ON matched.proj_id=metrics.proj_id
        WHERE metrics.check_run_id=@check_run_id AND metrics.config_version_id=@config_version_id;

'+@history_marker);
    SET @refresh=STUFF(@refresh,1,CHARINDEX('PROCEDURE',UPPER(@refresh))+8,N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @refresh;
END;

DECLARE @view nvarchar(max)=OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_vw_PBI_ProjectMetrics'));
IF CHARINDEX('soft_constraint_count',@view)=0
BEGIN
    DECLARE @view_marker nvarchar(max)=N'    config_version_id
FROM';
    IF CHARINDEX(@view_marker,@view)=0 THROW 51906, 'Project metrics view shape changed.',1;
    SET @view=REPLACE(@view,@view_marker,N'    config_version_id,
    soft_constraint_activity_count,
    soft_constraint_count
FROM');
    SET @view=STUFF(@view,1,CHARINDEX('VIEW',UPPER(@view))+3,N'ALTER VIEW');
    EXEC sys.sp_executesql @view;
END;

SET @view=OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_vw_PBI_ScheduleQualityResults'));
IF CHARINDEX('soft_constraint_count',@view)=0
BEGIN
    IF CHARINDEX(N'WHEN ''constraints'' THEN metrics.constraint_count',@view)=0
        THROW 51907, 'Normalised results view shape changed.',1;
    SET @view=REPLACE(@view,N'WHEN ''constraints'' THEN metrics.constraint_count',N'WHEN ''constraints'' THEN metrics.constraint_count
            WHEN ''soft_constraints'' THEN metrics.soft_constraint_count');
    SET @view=REPLACE(@view,N'WHEN ''dcma_activity'' THEN metrics.dcma_activity_count',N'WHEN ''dcma_activity'' THEN metrics.dcma_activity_count
            WHEN ''soft_constraint_activity'' THEN metrics.soft_constraint_activity_count');
    SET @view=REPLACE(@view,N'AND scope.is_enabled = 1',N'AND scope.is_enabled = 1
 AND scope.check_code <> ''soft_constraints'' /* FYI_EXCLUDED_FROM_DCMA_V1 */');
    SET @view=STUFF(@view,1,CHARINDEX('VIEW',UPPER(@view))+3,N'ALTER VIEW');
    EXEC sys.sp_executesql @view;
END;

DECLARE @draft nvarchar(max)=OBJECT_DEFINITION(OBJECT_ID(N'powerbitables.xertoolkit_get_or_create_schedule_quality_draft'));
IF CHARINDEX('SOFT_CONSTRAINT_DRAFT_V1',@draft)=0
BEGIN
    /* Older clone code omitted display_format while copying the active hash. */
    DECLARE @copy_start int=CHARINDEX(N'-- Copy detail fields from active version',@draft);
    DECLARE @copy_end int=CHARINDEX(N'-- Copy the active version hash',@draft,@copy_start);
    IF @copy_start=0 OR @copy_end=0 THROW 51911, 'Draft evidence clone shape changed.',1;
    DECLARE @copy_block nvarchar(max)=SUBSTRING(@draft,@copy_start,@copy_end-@copy_start);
    IF CHARINDEX(N'display_format',@copy_block)=0
        SET @draft=STUFF(@draft,@copy_start,@copy_end-@copy_start,
            REPLACE(@copy_block,N'                display_label,',N'                display_label,
                display_format,'));
    DECLARE @draft_marker nvarchar(max)=N'        SELECT
            c.config_version_id,';
    IF CHARINDEX(@draft_marker,@draft)=0 THROW 51908, 'Draft procedure shape changed.',1;
    SET @draft=REPLACE(@draft,@draft_marker,N'        /* SOFT_CONSTRAINT_DRAFT_V1: introduce the new check only in drafts. */
        IF NOT EXISTS (SELECT 1 FROM powerbitables.xertoolkit_schedule_quality_check_scope
            WHERE config_version_id=@config_version_id AND check_code=''soft_constraints'')
        BEGIN
            /* A draft with the copied active hash has never been saved. Repair
               the earlier clone omission without overwriting saved draft edits. */
            UPDATE field SET display_format=published.display_format
            FROM powerbitables.xertoolkit_schedule_quality_detail_field AS field
            JOIN powerbitables.xertoolkit_schedule_quality_config_version AS draft_version
              ON draft_version.config_version_id=field.config_version_id
            JOIN powerbitables.xertoolkit_schedule_quality_config_version AS active_version
              ON active_version.config_version_id=draft_version.based_on_config_version_id
             AND active_version.settings_hash=draft_version.settings_hash
            JOIN powerbitables.xertoolkit_schedule_quality_detail_field AS published
              ON published.config_version_id=active_version.config_version_id
             AND published.check_code=field.check_code
             AND published.source_category=field.source_category
             AND published.source_identifier=field.source_identifier
            WHERE field.config_version_id=@config_version_id AND draft_version.state=''draft'';

            INSERT powerbitables.xertoolkit_schedule_quality_check_scope
            (config_version_id,check_code,display_name,sort_order,is_enabled,include_loe,
             include_wbs_summary,include_milestones,exclude_complete,limit_type,green_limit,
             amber_limit,green_points,amber_points,records_metric,qualifying_metric)
            SELECT @config_version_id,''soft_constraints'',N''Soft Constraints (FYI)'',85,1,
                include_loe,include_wbs_summary,include_milestones,exclude_complete,
                ''Percent'',0,0,0,0,
                ''soft_constraint_activity'',''soft_constraints''
            FROM powerbitables.xertoolkit_schedule_quality_check_scope
            WHERE config_version_id=@config_version_id AND check_code=''constraints'';
            IF @@ROWCOUNT<>1 THROW 51909, ''Existing constraint check missing.'',1;

            INSERT powerbitables.xertoolkit_schedule_quality_detail_field
            (config_version_id,check_code,source_category,source_identifier,display_label,display_format,sort_order)
            VALUES
            (@config_version_id,''soft_constraints'',''task_column'',''cstr_type'',N''Primary Constraint Type'',''native'',1),
            (@config_version_id,''soft_constraints'',''task_column'',''cstr_date'',N''Primary Constraint Date'',''native'',2),
            (@config_version_id,''soft_constraints'',''task_column'',''cstr_type2'',N''Secondary Constraint Type'',''native'',3),
            (@config_version_id,''soft_constraints'',''task_column'',''cstr_date2'',N''Secondary Constraint Date'',''native'',4);

            DECLARE @soft_hash_json nvarchar(max);
            SELECT @soft_hash_json=(SELECT
                (SELECT check_code,is_enabled,include_loe,include_wbs_summary,include_milestones,exclude_complete,
                    limit_type,green_limit,amber_limit,green_points,amber_points,records_metric,qualifying_metric
                 FROM powerbitables.xertoolkit_schedule_quality_check_scope WHERE config_version_id=@config_version_id
                 ORDER BY sort_order FOR JSON PATH) AS checks,
                (SELECT option_code,bit_value,numeric_value,text_value
                 FROM powerbitables.xertoolkit_schedule_quality_option WHERE config_version_id=@config_version_id
                 ORDER BY sort_order FOR JSON PATH) AS options,
                (SELECT constraint_type_code,is_checked FROM powerbitables.xertoolkit_schedule_quality_constraint_type
                 WHERE config_version_id=@config_version_id ORDER BY sort_order FOR JSON PATH) AS constraint_types,
                (SELECT check_code,source_category,source_identifier,display_label,display_format,sort_order
                 FROM powerbitables.xertoolkit_schedule_quality_detail_field WHERE config_version_id=@config_version_id
                 ORDER BY check_code,sort_order FOR JSON PATH) AS detail_fields
                FOR JSON PATH,WITHOUT_ARRAY_WRAPPER);
            UPDATE powerbitables.xertoolkit_schedule_quality_config_version
            SET settings_hash=LOWER(CONVERT(char(64),HASHBYTES(''SHA2_256'',@soft_hash_json),2))
            WHERE config_version_id=@config_version_id;
        END;

'+@draft_marker);
    SET @draft=STUFF(@draft,1,CHARINDEX('PROCEDURE',UPPER(@draft))+8,N'ALTER PROCEDURE');
    EXEC sys.sp_executesql @draft;
END;
COMMIT TRANSACTION;
GO
/* Kept separate from the DCMA/scored result contract. */
CREATE OR ALTER VIEW powerbitables.xertoolkit_vw_PBI_InformationalResults
AS
SELECT metrics.proj_id,metrics.project_name,metrics.updated_date,metrics.check_run_id,
    metrics.refreshed_at,metrics.config_version_id,scope.check_code,scope.display_name,
    metrics.soft_constraint_activity_count AS records_checked,
    metrics.soft_constraint_count AS qualifying_results,
    CONVERT(bit,1) AS is_informational
FROM powerbitables.xertoolkit_result_project_metrics AS metrics
JOIN powerbitables.xertoolkit_schedule_quality_profile AS profile
 ON profile.active_config_version_id=metrics.config_version_id AND profile.profile_code='default'
JOIN powerbitables.xertoolkit_schedule_quality_check_scope AS scope
 ON scope.config_version_id=metrics.config_version_id
 AND scope.check_code='soft_constraints' AND scope.is_enabled=1;
GO
