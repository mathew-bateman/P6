/* First disable Soft Constraints and publish/rebuild using the normal settings
   workflow, then restore the application files from the deployment snapshot.
   This rollback preserves configuration/audit history and additive columns. */
SET NOCOUNT ON;
SET XACT_ABORT ON;
IF DB_NAME()<>N'P62212_1' THROW 51920,'Run against P62212_1.',1;
IF OBJECT_ID(N'powerbitables.xertoolkit_soft_constraints_module_backup',N'U') IS NULL
    THROW 51921,'Pre-039 module snapshot missing.',1;
IF EXISTS(SELECT 1 FROM powerbitables.xertoolkit_schedule_quality_check_scope s
    JOIN powerbitables.xertoolkit_schedule_quality_profile p ON p.active_config_version_id=s.config_version_id
    WHERE s.check_code='soft_constraints' AND s.is_enabled=1)
    THROW 51922,'Disable Soft Constraints, then publish/rebuild before rollback.',1;
BEGIN TRY
    BEGIN TRANSACTION;
    DECLARE @name sysname,@definition nvarchar(max),@kind varchar(10);
    DECLARE modules CURSOR LOCAL FAST_FORWARD FOR
        SELECT object_name,definition FROM powerbitables.xertoolkit_soft_constraints_module_backup
        ORDER BY CASE object_name WHEN 'xertoolkit_fn_schedule_quality_task_evidence' THEN 0 ELSE 1 END;
    OPEN modules;
    FETCH NEXT FROM modules INTO @name,@definition;
    WHILE @@FETCH_STATUS=0
    BEGIN
        IF @name='xertoolkit_refresh_all_schedule_quality'
        BEGIN
            /* SELECT INTO does not copy the retained columns' defaults. */
            SET @definition=REPLACE(@definition,
              N'        CREATE UNIQUE CLUSTERED INDEX [IX_ProjectMetricsStage_proj_id]',
              N'        ALTER TABLE #ProjectMetricsStage ADD DEFAULT(0) FOR soft_constraint_count;
        ALTER TABLE #ProjectMetricsStage ADD DEFAULT(0) FOR soft_constraint_activity_count;
        CREATE UNIQUE CLUSTERED INDEX [IX_ProjectMetricsStage_proj_id]');
            SET @definition=REPLACE(@definition,
              N'WHERE config_version_id = @config_version_id) <> 20',
              N'WHERE config_version_id = @config_version_id) <>
                CASE WHEN EXISTS(SELECT 1 FROM powerbitables.xertoolkit_schedule_quality_check_scope
                  WHERE config_version_id=@config_version_id AND check_code=''soft_constraints'') THEN 21 ELSE 20 END');
        END;
        SET @kind=CASE WHEN @name LIKE '%fn_%' THEN 'FUNCTION'
                       WHEN @name LIKE '%vw_%' THEN 'VIEW' ELSE 'PROCEDURE' END;
        SET @definition=STUFF(@definition,1,CHARINDEX(@kind,UPPER(@definition))+LEN(@kind)-1,N'ALTER '+@kind);
        EXEC sys.sp_executesql @definition;
        FETCH NEXT FROM modules INTO @name,@definition;
    END;
    CLOSE modules;DEALLOCATE modules;
    COMMIT TRANSACTION;
END TRY
BEGIN CATCH
    IF XACT_STATE()<>0 ROLLBACK TRANSACTION;
    THROW;
END CATCH;
