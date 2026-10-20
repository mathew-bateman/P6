/* Read-only verification after publishing/rebuilding Soft Constraints (FYI).
   Run while P6 source records are stable; a later import is a freshness gap. */
SET NOCOUNT ON;
IF DB_NAME()<>N'P62212_1' THROW 51930,'Run against P62212_1.',1;
DECLARE @config bigint=(SELECT active_config_version_id FROM powerbitables.xertoolkit_schedule_quality_profile WHERE profile_code='default');
IF NOT EXISTS(SELECT 1 FROM powerbitables.xertoolkit_schedule_quality_check_scope
    WHERE config_version_id=@config AND check_code='soft_constraints' AND is_enabled=1
      AND green_points=0 AND amber_points=0)
    THROW 51931,'The FYI check is not active with zero points.',1;
IF EXISTS(SELECT 1 FROM powerbitables.xertoolkit_vw_PBI_ScheduleQualityResults WHERE check_code='soft_constraints')
    THROW 51932,'FYI results leaked into the DCMA result contract.',1;
IF EXISTS
(
    SELECT 1 FROM powerbitables.xertoolkit_result_project_metrics m
    OUTER APPLY(SELECT COUNT(*) AS n FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence e
      WHERE e.proj_id=m.proj_id AND e.config_version_id=m.config_version_id
        AND e.check_run_id=m.check_run_id AND e.check_code='soft_constraints') AS detail
    WHERE m.config_version_id=@config AND m.soft_constraint_count<>detail.n
)
    THROW 51933,'FYI headline counts and evidence do not reconcile.',1;
IF EXISTS
(
    SELECT proj_id,task_id FROM powerbitables.xertoolkit_fn_soft_constraints(@config)
    EXCEPT
    SELECT proj_id,task_id FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence
    WHERE config_version_id=@config AND check_code='soft_constraints'
)
OR EXISTS
(
    SELECT proj_id,task_id FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence
    WHERE config_version_id=@config AND check_code='soft_constraints'
    EXCEPT
    SELECT proj_id,task_id FROM powerbitables.xertoolkit_fn_soft_constraints(@config)
)
    THROW 51934,'FYI evidence differs from the current P6 source.',1;
IF EXISTS
(
    SELECT 1 FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence
    WHERE config_version_id=@config AND check_code='soft_constraints'
      AND (detail_display IS NULL OR detail_display NOT LIKE '%Primary Constraint Type:%'
           OR detail_display NOT LIKE '%Secondary Constraint Type:%')
)
    THROW 51935,'Readable primary/secondary constraint evidence is missing.',1;
IF EXISTS
(
 SELECT 1 FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence e
 JOIN dbo.TASK t ON t.proj_id=e.proj_id AND t.task_id=e.task_id
 WHERE e.config_version_id=@config AND e.check_code='soft_constraints'
   AND ISNULL(t.cstr_type,'') NOT IN ('CS_MSOB','CS_MSOA','CS_MEOB','CS_MEOA')
   AND ISNULL(t.cstr_type2,'') NOT IN ('CS_MSOB','CS_MSOA','CS_MEOB','CS_MEOA')
)
 THROW 51943,'FYI evidence contains an activity without an NR soft constraint.',1;
SELECT @config AS config_version_id,COUNT(*) AS informational_project_rows,
    SUM(qualifying_results) AS informational_activities,
    N'FYI evidence reconciles with P6; excluded from DCMA scoring' AS verification
FROM powerbitables.xertoolkit_vw_PBI_InformationalResults;
