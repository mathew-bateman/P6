/*
    Read-only, all-project reconciliation after the full schedule-quality refresh.

    Run immediately after xertoolkit_refresh_all_schedule_quality with @proj_id = NULL,
    while P6 TASK/TASKPRED imports are paused. The script creates only session-scoped
    temporary tables; it does not change application data or configuration.
*/
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51410, 'This reconciliation must be run against P62212_1.', 1;

DECLARE @active_config_version_id bigint;
DECLARE @full_check_run_id bigint;
DECLARE @full_run_processed_project_count int;

SELECT @active_config_version_id = active_config_version_id
FROM [powerbitables].[xertoolkit_schedule_quality_profile]
WHERE profile_code = N'default';

IF @active_config_version_id IS NULL
    THROW 51411, 'The default schedule-quality profile has no active configuration.', 1;

SELECT TOP (1)
    @full_check_run_id = check_run_id,
    @full_run_processed_project_count = processed_project_count
FROM [powerbitables].[xertoolkit_refresh_run_history]
WHERE requested_proj_id IS NULL
  AND status = 'success'
  AND config_version_id = @active_config_version_id
ORDER BY check_run_id DESC;

DECLARE
    @source_project_rows int,
    @source_distinct_projects int,
    @view_project_rows int,
    @view_distinct_projects int,
    @view_missing_projects int,
    @view_extra_projects int,
    @view_duplicate_projects int,
    @materialised_project_rows int,
    @materialised_distinct_projects int,
    @materialised_missing_projects int,
    @materialised_extra_projects int,
    @materialised_duplicate_projects int,
    @materialised_wrong_version_rows int,
    @materialised_wrong_run_rows int,
    @logic_loop_wrong_version_rows int,
    @logic_loop_wrong_run_rows int,
    @logic_loop_orphan_rows int,
    @oos_wrong_version_rows int,
    @oos_wrong_run_rows int,
    @oos_orphan_rows int,
    @oos_count_mismatch_projects int,
    @oos_missing_exception_rows int,
    @oos_extra_exception_rows int,
    @oos_completed_successor_rows int,
    @task_evidence_wrong_version_rows int,
    @task_evidence_wrong_run_rows int,
    @task_evidence_orphan_rows int,
    @task_evidence_missing_rows int,
    @task_evidence_extra_rows int,
    @unified_task_evidence_duplicate_rows int,
    @unified_task_evidence_orphan_rows int,
    @unified_logic_loop_mismatch_projects int,
    @unified_oos_mismatch_projects int;

SELECT
    @source_project_rows = COUNT(*),
    @source_distinct_projects = COUNT(DISTINCT proj_id)
FROM dbo.PROJECT;

SELECT
    @view_project_rows = COUNT(*),
    @view_distinct_projects = COUNT(DISTINCT proj_id)
FROM [powerbitables].[xertoolkit_vw_PBI_Projects];

SELECT @view_missing_projects = COUNT(*)
FROM
(
    SELECT proj_id FROM dbo.PROJECT
    EXCEPT
    SELECT proj_id FROM [powerbitables].[xertoolkit_vw_PBI_Projects]
) AS missing_projects;

SELECT @view_extra_projects = COUNT(*)
FROM
(
    SELECT proj_id FROM [powerbitables].[xertoolkit_vw_PBI_Projects]
    EXCEPT
    SELECT proj_id FROM dbo.PROJECT
) AS extra_projects;

SELECT @view_duplicate_projects = COUNT(*)
FROM
(
    SELECT proj_id
    FROM [powerbitables].[xertoolkit_vw_PBI_Projects]
    GROUP BY proj_id
    HAVING COUNT(*) <> 1
) AS duplicate_projects;

SELECT
    @materialised_project_rows = COUNT(*),
    @materialised_distinct_projects = COUNT(DISTINCT proj_id),
    @materialised_wrong_version_rows = SUM
    (
        CASE
            WHEN config_version_id = @active_config_version_id THEN 0
            ELSE 1
        END
    ),
    @materialised_wrong_run_rows = SUM
    (
        CASE
            WHEN @full_check_run_id IS NOT NULL
             AND check_run_id = @full_check_run_id
            THEN 0
            ELSE 1
        END
    )
FROM [powerbitables].[xertoolkit_result_project_metrics];

SET @materialised_wrong_version_rows = ISNULL(@materialised_wrong_version_rows, 0);
SET @materialised_wrong_run_rows = ISNULL(@materialised_wrong_run_rows, 0);

SELECT @materialised_missing_projects = COUNT(*)
FROM
(
    SELECT proj_id FROM dbo.PROJECT
    EXCEPT
    SELECT proj_id FROM [powerbitables].[xertoolkit_result_project_metrics]
) AS missing_projects;

SELECT @materialised_extra_projects = COUNT(*)
FROM
(
    SELECT proj_id FROM [powerbitables].[xertoolkit_result_project_metrics]
    EXCEPT
    SELECT proj_id FROM dbo.PROJECT
) AS extra_projects;

SELECT @materialised_duplicate_projects = COUNT(*)
FROM
(
    SELECT proj_id
    FROM [powerbitables].[xertoolkit_result_project_metrics]
    GROUP BY proj_id
    HAVING COUNT(*) <> 1
) AS duplicate_projects;

SELECT
    @logic_loop_wrong_version_rows = SUM
    (
        CASE
            WHEN config_version_id = @active_config_version_id THEN 0
            ELSE 1
        END
    ),
    @logic_loop_wrong_run_rows = SUM
    (
        CASE
            WHEN @full_check_run_id IS NOT NULL
             AND check_run_id = @full_check_run_id
            THEN 0
            ELSE 1
        END
    )
FROM [powerbitables].[xertoolkit_result_logic_loop_tasks];

SET @logic_loop_wrong_version_rows = ISNULL(@logic_loop_wrong_version_rows, 0);
SET @logic_loop_wrong_run_rows = ISNULL(@logic_loop_wrong_run_rows, 0);

SELECT @logic_loop_orphan_rows = COUNT(*)
FROM [powerbitables].[xertoolkit_result_logic_loop_tasks] AS loops
LEFT JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
  ON metrics.proj_id = loops.proj_id
 AND metrics.check_run_id = loops.check_run_id
 AND metrics.config_version_id = loops.config_version_id
WHERE metrics.proj_id IS NULL;

SELECT
    @oos_wrong_version_rows = SUM
    (
        CASE WHEN config_version_id = @active_config_version_id THEN 0 ELSE 1 END
    ),
    @oos_wrong_run_rows = SUM
    (
        CASE
            WHEN @full_check_run_id IS NOT NULL
             AND check_run_id = @full_check_run_id
            THEN 0 ELSE 1
        END
    )
FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions];

SET @oos_wrong_version_rows = ISNULL(@oos_wrong_version_rows, 0);
SET @oos_wrong_run_rows = ISNULL(@oos_wrong_run_rows, 0);

SELECT @oos_orphan_rows = COUNT(*)
FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
LEFT JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
  ON metrics.proj_id = exception.proj_id
 AND metrics.check_run_id = exception.check_run_id
 AND metrics.config_version_id = exception.config_version_id
WHERE metrics.proj_id IS NULL;

SELECT @oos_count_mismatch_projects = COUNT(*)
FROM [powerbitables].[xertoolkit_result_project_metrics] AS metrics
OUTER APPLY
(
    SELECT COUNT(DISTINCT exception.successor_task_id) AS exception_count
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
    WHERE exception.proj_id = metrics.proj_id
      AND exception.check_run_id = metrics.check_run_id
      AND exception.config_version_id = metrics.config_version_id
) AS detail
WHERE metrics.out_of_sequence_count <> detail.exception_count;

SELECT @oos_missing_exception_rows = COUNT(*)
FROM
(
    SELECT proj_id, relationship_id, predecessor_task_id, successor_task_id, relationship_type
    FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@active_config_version_id)
    WHERE is_out_of_sequence = 1
    EXCEPT
    SELECT proj_id, relationship_id, predecessor_task_id, successor_task_id, relationship_type
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
    WHERE config_version_id = @active_config_version_id
      AND check_run_id = @full_check_run_id
) AS missing_exception_rows;

SELECT @oos_extra_exception_rows = COUNT(*)
FROM
(
    SELECT proj_id, relationship_id, predecessor_task_id, successor_task_id, relationship_type
    FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
    WHERE config_version_id = @active_config_version_id
      AND check_run_id = @full_check_run_id
    EXCEPT
    SELECT proj_id, relationship_id, predecessor_task_id, successor_task_id, relationship_type
    FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@active_config_version_id)
    WHERE is_out_of_sequence = 1
) AS extra_exception_rows;

SELECT @oos_completed_successor_rows = COUNT(*)
FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions] AS exception
JOIN [powerbitables].[xertoolkit_schedule_quality_profile] AS profile
  ON profile.profile_code = N'default'
 AND profile.active_config_version_id = exception.config_version_id
JOIN [powerbitables].[xertoolkit_schedule_quality_check_scope] AS scope
  ON scope.config_version_id = profile.active_config_version_id
 AND scope.check_code = 'out_of_sequence'
WHERE scope.exclude_complete = 1
  AND exception.successor_status = 'TK_Complete';

SELECT
    @task_evidence_wrong_version_rows = SUM
    (
        CASE WHEN config_version_id = @active_config_version_id THEN 0 ELSE 1 END
    ),
    @task_evidence_wrong_run_rows = SUM
    (
        CASE
            WHEN @full_check_run_id IS NOT NULL
             AND check_run_id = @full_check_run_id
            THEN 0 ELSE 1
        END
    )
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence];

SET @task_evidence_wrong_version_rows = ISNULL(@task_evidence_wrong_version_rows, 0);
SET @task_evidence_wrong_run_rows = ISNULL(@task_evidence_wrong_run_rows, 0);

SELECT @task_evidence_orphan_rows = COUNT(*)
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
LEFT JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
  ON metrics.proj_id = evidence.proj_id
 AND metrics.check_run_id = evidence.check_run_id
 AND metrics.config_version_id = evidence.config_version_id
 AND metrics.refreshed_at = evidence.refreshed_at
WHERE metrics.proj_id IS NULL;

SELECT @task_evidence_missing_rows = COUNT(*)
FROM
(
    SELECT config_version_id, proj_id, check_code, task_id, evidence_basis
    FROM [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]
        (@active_config_version_id)
    EXCEPT
    SELECT config_version_id, proj_id, check_code, task_id, evidence_basis
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    WHERE config_version_id = @active_config_version_id
      AND check_run_id = @full_check_run_id
) AS missing_task_evidence;

SELECT @task_evidence_extra_rows = COUNT(*)
FROM
(
    SELECT config_version_id, proj_id, check_code, task_id, evidence_basis
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    WHERE config_version_id = @active_config_version_id
      AND check_run_id = @full_check_run_id
    EXCEPT
    SELECT config_version_id, proj_id, check_code, task_id, evidence_basis
    FROM [powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]
        (@active_config_version_id)
) AS extra_task_evidence;

SELECT @unified_task_evidence_duplicate_rows = COUNT(*)
FROM
(
    SELECT config_version_id, check_run_id, proj_id, check_code, task_id
    FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
    GROUP BY config_version_id, check_run_id, proj_id, check_code, task_id
    HAVING COUNT_BIG(*) <> 1
) AS duplicate_evidence;

SELECT @unified_task_evidence_orphan_rows = COUNT(*)
FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence] AS evidence
LEFT JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
  ON metrics.proj_id = evidence.proj_id
 AND metrics.check_run_id = evidence.check_run_id
 AND metrics.config_version_id = evidence.config_version_id
 AND metrics.refreshed_at = evidence.refreshed_at
WHERE metrics.proj_id IS NULL;

SELECT @unified_logic_loop_mismatch_projects = COUNT(*)
FROM [powerbitables].[xertoolkit_result_project_metrics] AS metrics
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS evidence_count
    FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence] AS evidence
    WHERE evidence.proj_id = metrics.proj_id
      AND evidence.check_run_id = metrics.check_run_id
      AND evidence.config_version_id = metrics.config_version_id
      AND evidence.check_code = 'logical_loops'
) AS detail
WHERE metrics.logical_loop_count <> detail.evidence_count;

SELECT @unified_oos_mismatch_projects = COUNT(*)
FROM [powerbitables].[xertoolkit_result_project_metrics] AS metrics
OUTER APPLY
(
    SELECT COUNT_BIG(*) AS evidence_count
    FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence] AS evidence
    WHERE evidence.proj_id = metrics.proj_id
      AND evidence.check_run_id = metrics.check_run_id
      AND evidence.config_version_id = metrics.config_version_id
      AND evidence.check_code = 'out_of_sequence'
) AS detail
WHERE metrics.out_of_sequence_count <> detail.evidence_count;

DECLARE @assertions TABLE
(
    assertion_name nvarchar(120) NOT NULL PRIMARY KEY,
    passed bit NOT NULL,
    observed_value nvarchar(200) NULL,
    expected_value nvarchar(200) NULL
);

INSERT INTO @assertions
    (assertion_name, passed, observed_value, expected_value)
VALUES
    (
        N'dbo.PROJECT has unique project IDs',
        CONVERT(bit, CASE WHEN @source_project_rows = @source_distinct_projects THEN 1 ELSE 0 END),
        CONCAT(@source_project_rows, N' rows / ', @source_distinct_projects, N' IDs'),
        N'one row per ID'
    ),
    (
        N'Projects view has the source row count',
        CONVERT(bit, CASE WHEN @view_project_rows = @source_project_rows THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @view_project_rows),
        CONVERT(nvarchar(30), @source_project_rows)
    ),
    (
        N'Projects view has one row per project',
        CONVERT(bit, CASE WHEN @view_project_rows = @view_distinct_projects AND @view_duplicate_projects = 0 THEN 1 ELSE 0 END),
        CONCAT(@view_project_rows, N' rows / ', @view_distinct_projects, N' IDs / ', @view_duplicate_projects, N' duplicated IDs'),
        N'one row per source project'
    ),
    (
        N'Projects view is missing no source IDs',
        CONVERT(bit, CASE WHEN @view_missing_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @view_missing_projects),
        N'0'
    ),
    (
        N'Projects view has no extra IDs',
        CONVERT(bit, CASE WHEN @view_extra_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @view_extra_projects),
        N'0'
    ),
    (
        N'Successful full refresh exists for active configuration',
        CONVERT(bit, CASE WHEN @full_check_run_id IS NOT NULL THEN 1 ELSE 0 END),
        COALESCE(CONVERT(nvarchar(30), @full_check_run_id), N'NULL'),
        N'non-NULL check_run_id'
    ),
    (
        N'Full refresh processed every source project',
        CONVERT(bit, CASE WHEN @full_run_processed_project_count = @source_project_rows THEN 1 ELSE 0 END),
        COALESCE(CONVERT(nvarchar(30), @full_run_processed_project_count), N'NULL'),
        CONVERT(nvarchar(30), @source_project_rows)
    ),
    (
        N'Materialised table has the source row count',
        CONVERT(bit, CASE WHEN @materialised_project_rows = @source_project_rows THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @materialised_project_rows),
        CONVERT(nvarchar(30), @source_project_rows)
    ),
    (
        N'Materialised table has one row per project',
        CONVERT(bit, CASE WHEN @materialised_project_rows = @materialised_distinct_projects AND @materialised_duplicate_projects = 0 THEN 1 ELSE 0 END),
        CONCAT(@materialised_project_rows, N' rows / ', @materialised_distinct_projects, N' IDs / ', @materialised_duplicate_projects, N' duplicated IDs'),
        N'one row per source project'
    ),
    (
        N'Materialised table is missing no source IDs',
        CONVERT(bit, CASE WHEN @materialised_missing_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @materialised_missing_projects),
        N'0'
    ),
    (
        N'Materialised table has no extra IDs',
        CONVERT(bit, CASE WHEN @materialised_extra_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @materialised_extra_projects),
        N'0'
    ),
    (
        N'All materialised rows use the active configuration',
        CONVERT(bit, CASE WHEN @materialised_wrong_version_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @materialised_wrong_version_rows),
        N'0 wrong-version rows'
    ),
    (
        N'All materialised rows come from the latest full refresh',
        CONVERT(bit, CASE WHEN @materialised_wrong_run_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @materialised_wrong_run_rows),
        N'0 wrong-run rows'
    ),
    (
        N'All logical-loop rows use the active configuration',
        CONVERT(bit, CASE WHEN @logic_loop_wrong_version_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @logic_loop_wrong_version_rows),
        N'0 wrong-version rows'
    ),
    (
        N'All logical-loop rows come from the latest full refresh',
        CONVERT(bit, CASE WHEN @logic_loop_wrong_run_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @logic_loop_wrong_run_rows),
        N'0 wrong-run rows'
    ),
    (
        N'Logical-loop rows have matching project metrics',
        CONVERT(bit, CASE WHEN @logic_loop_orphan_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @logic_loop_orphan_rows),
        N'0 orphan rows'
    ),
    (
        N'All out-of-sequence exception rows use the active configuration',
        CONVERT(bit, CASE WHEN @oos_wrong_version_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_wrong_version_rows),
        N'0 wrong-version rows'
    ),
    (
        N'All out-of-sequence exception rows come from the latest full refresh',
        CONVERT(bit, CASE WHEN @oos_wrong_run_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_wrong_run_rows),
        N'0 wrong-run rows'
    ),
    (
        N'Out-of-sequence exception rows have matching project metrics',
        CONVERT(bit, CASE WHEN @oos_orphan_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_orphan_rows),
        N'0 orphan rows'
    ),
    (
        N'Out-of-sequence counts equal their materialised exception rows',
        CONVERT(bit, CASE WHEN @oos_count_mismatch_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_count_mismatch_projects),
        N'0 mismatched projects'
    ),
    (
        N'No calculated out-of-sequence exceptions are missing from the snapshot',
        CONVERT(bit, CASE WHEN @oos_missing_exception_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_missing_exception_rows),
        N'0 missing rows'
    ),
    (
        N'The out-of-sequence snapshot has no extra exception identities',
        CONVERT(bit, CASE WHEN @oos_extra_exception_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_extra_exception_rows),
        N'0 extra rows'
    ),
    (
        N'Out-of-sequence exclusion contains no completed successor activities',
        CONVERT(bit, CASE WHEN @oos_completed_successor_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @oos_completed_successor_rows),
        N'0 completed successors'
    ),
    (
        N'All task-evidence rows use the active configuration',
        CONVERT(bit, CASE WHEN @task_evidence_wrong_version_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @task_evidence_wrong_version_rows),
        N'0 wrong-version rows'
    ),
    (
        N'All task-evidence rows come from the latest full refresh',
        CONVERT(bit, CASE WHEN @task_evidence_wrong_run_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @task_evidence_wrong_run_rows),
        N'0 wrong-run rows'
    ),
    (
        N'Task-evidence rows have matching project metrics',
        CONVERT(bit, CASE WHEN @task_evidence_orphan_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @task_evidence_orphan_rows),
        N'0 orphan rows'
    ),
    (
        N'No calculated task evidence is missing from the snapshot',
        CONVERT(bit, CASE WHEN @task_evidence_missing_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @task_evidence_missing_rows),
        N'0 missing rows'
    ),
    (
        N'The task-evidence snapshot has no extra identities',
        CONVERT(bit, CASE WHEN @task_evidence_extra_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @task_evidence_extra_rows),
        N'0 extra rows'
    ),
    (
        N'Unified task evidence has one row per project, check, and task',
        CONVERT(bit, CASE WHEN @unified_task_evidence_duplicate_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @unified_task_evidence_duplicate_rows),
        N'0 duplicate identities'
    ),
    (
        N'Unified task-evidence rows have matching project metrics',
        CONVERT(bit, CASE WHEN @unified_task_evidence_orphan_rows = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @unified_task_evidence_orphan_rows),
        N'0 orphan rows'
    ),
    (
        N'Unified Logical Loops rows equal their project counts',
        CONVERT(bit, CASE WHEN @unified_logic_loop_mismatch_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @unified_logic_loop_mismatch_projects),
        N'0 mismatched projects'
    ),
    (
        N'Unified Out of Sequence rows equal their project counts',
        CONVERT(bit, CASE WHEN @unified_oos_mismatch_projects = 0 THEN 1 ELSE 0 END),
        CONVERT(nvarchar(30), @unified_oos_mismatch_projects),
        N'0 mismatched projects'
    );

/* Show that every source project_flag group is represented in the view. */
SELECT
    COALESCE(CONVERT(nvarchar(20), source.project_flag), N'<NULL>') AS project_flag,
    COUNT(*) AS source_rows,
    SUM(CASE WHEN project_view.proj_id IS NOT NULL THEN 1 ELSE 0 END) AS rows_in_projects_view,
    SUM(CASE WHEN metrics.proj_id IS NOT NULL THEN 1 ELSE 0 END) AS rows_in_materialised_table
FROM dbo.PROJECT AS source
LEFT JOIN
(
    SELECT DISTINCT proj_id
    FROM [powerbitables].[xertoolkit_vw_PBI_Projects]
) AS project_view
  ON project_view.proj_id = source.proj_id
LEFT JOIN
(
    SELECT DISTINCT proj_id
    FROM [powerbitables].[xertoolkit_result_project_metrics]
) AS metrics
  ON metrics.proj_id = source.proj_id
GROUP BY source.project_flag
ORDER BY source.project_flag;

/* One expected row per project. Aggregate each parameterised detail source once. */
SELECT
    p.proj_id,
    MAX(COALESCE(NULLIF(p.[Project Name], N''), p.proj_short_name)) AS project_name,
    MAX(p.data_date) AS updated_date
INTO #ExpectedProjects
FROM [powerbitables].[xertoolkit_vw_PBI_Projects] AS p
GROUP BY p.proj_id;

CREATE UNIQUE CLUSTERED INDEX [IX_ExpectedProjects_proj_id]
    ON #ExpectedProjects (proj_id);

SELECT a.proj_id, COUNT(*) AS activity_count
INTO #ActivityCounts
FROM [powerbitables].[xertoolkit_vw_PBI_Activities] AS a
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = a.proj_id
GROUP BY a.proj_id;

SELECT scoped.proj_id, COUNT(*) AS dcma_activity_count
INTO #ScopedActivityCounts
FROM [powerbitables].[xertoolkit_fn_activity_in_scope]
    (@active_config_version_id, 'relationship_ratio') AS scoped
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = scoped.proj_id
GROUP BY scoped.proj_id;

SELECT scoped.proj_id, COUNT(*) AS relationship_count
INTO #RelationshipCounts
FROM [powerbitables].[xertoolkit_fn_relationship_in_scope]
    (@active_config_version_id, 'relationship_ratio') AS scoped
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = scoped.proj_id
GROUP BY scoped.proj_id;

SELECT
    detail.proj_id,
    SUM(detail.is_missing_predecessor) AS missing_predecessor_count,
    SUM(detail.is_missing_successor) AS missing_successor_count,
    SUM(detail.is_open_start) AS open_start_count,
    SUM(detail.is_open_finish) AS open_finish_count
INTO #OpenEndCounts
FROM [powerbitables].[xertoolkit_fn_open_ends](@active_config_version_id) AS detail
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = detail.proj_id
GROUP BY detail.proj_id;

SELECT
    detail.proj_id,
    SUM(detail.is_lead) AS lead_count,
    SUM(detail.is_lag) AS lag_count,
    SUM(detail.is_non_fs) AS non_fs_count,
    SUM(detail.is_excessive_ss_lag) AS excessive_ss_lag_count,
    SUM(detail.is_excessive_ff_lag) AS excessive_ff_lag_count
INTO #RelationshipQualityCounts
FROM [powerbitables].[xertoolkit_fn_relationship_quality](@active_config_version_id) AS detail
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = detail.proj_id
GROUP BY detail.proj_id;

SELECT
    detail.proj_id,
    SUM(detail.is_high_float) AS high_float_count,
    SUM(detail.is_negative_float) AS negative_float_count,
    SUM(detail.is_high_duration) AS high_duration_count,
    SUM(detail.is_constraint) AS constraint_count,
    SUM(detail.is_invalid_date) AS invalid_date_count,
    SUM(detail.is_in_progress_error) AS in_progress_error_count,
    SUM(detail.is_riding_progress_date) AS riding_progress_date_count,
    SUM(detail.is_critical_task) AS critical_task_count,
    SUM(detail.is_near_critical_task) AS near_critical_task_count
INTO #ActivityQualityCounts
FROM [powerbitables].[xertoolkit_fn_activity_quality](@active_config_version_id) AS detail
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = detail.proj_id
GROUP BY detail.proj_id;

SELECT
    detail.proj_id,
    COUNT(DISTINCT CASE WHEN detail.is_out_of_sequence = 1 THEN detail.successor_task_id END)
        AS out_of_sequence_count
INTO #OutOfSequenceCounts
FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@active_config_version_id) AS detail
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = detail.proj_id
GROUP BY detail.proj_id;

SELECT
    loops.proj_id,
    COUNT(DISTINCT loops.task_id) AS logical_loop_count
INTO #LogicalLoopCounts
FROM [powerbitables].[xertoolkit_result_logic_loop_tasks] AS loops
JOIN #ExpectedProjects AS projects
  ON projects.proj_id = loops.proj_id
WHERE loops.config_version_id = @active_config_version_id
  AND loops.check_run_id = @full_check_run_id
GROUP BY loops.proj_id;

SELECT
    projects.proj_id,
    projects.project_name,
    projects.updated_date,
    ISNULL(activities.activity_count, 0) AS activity_count,
    ISNULL(scoped_activities.dcma_activity_count, 0) AS dcma_activity_count,
    ISNULL(relationships.relationship_count, 0) AS relationship_count,
    CAST
    (
        ISNULL(relationships.relationship_count, 0) * 1.0
        / NULLIF(ISNULL(scoped_activities.dcma_activity_count, 0), 0)
        AS decimal(18,2)
    ) AS relationship_ratio,
    ISNULL(open_ends.missing_predecessor_count, 0) AS missing_predecessor_count,
    ISNULL(open_ends.missing_successor_count, 0) AS missing_successor_count,
    ISNULL(open_ends.open_start_count, 0) AS open_start_count,
    ISNULL(open_ends.open_finish_count, 0) AS open_finish_count,
    ISNULL(relationship_quality.lead_count, 0) AS lead_count,
    ISNULL(relationship_quality.lag_count, 0) AS lag_count,
    ISNULL(relationship_quality.non_fs_count, 0) AS non_fs_count,
    ISNULL(relationship_quality.excessive_ss_lag_count, 0) AS excessive_ss_lag_count,
    ISNULL(relationship_quality.excessive_ff_lag_count, 0) AS excessive_ff_lag_count,
    ISNULL(activity_quality.high_float_count, 0) AS high_float_count,
    ISNULL(activity_quality.negative_float_count, 0) AS negative_float_count,
    ISNULL(activity_quality.high_duration_count, 0) AS high_duration_count,
    ISNULL(activity_quality.constraint_count, 0) AS constraint_count,
    ISNULL(activity_quality.invalid_date_count, 0) AS invalid_date_count,
    ISNULL(activity_quality.in_progress_error_count, 0) AS in_progress_error_count,
    ISNULL(activity_quality.riding_progress_date_count, 0) AS riding_progress_date_count,
    ISNULL(activity_quality.critical_task_count, 0) AS critical_task_count,
    ISNULL(activity_quality.near_critical_task_count, 0) AS near_critical_task_count,
    ISNULL(out_of_sequence.out_of_sequence_count, 0) AS out_of_sequence_count,
    ISNULL(logical_loops.logical_loop_count, 0) AS logical_loop_count
INTO #ExpectedMetrics
FROM #ExpectedProjects AS projects
LEFT JOIN #ActivityCounts AS activities
  ON activities.proj_id = projects.proj_id
LEFT JOIN #ScopedActivityCounts AS scoped_activities
  ON scoped_activities.proj_id = projects.proj_id
LEFT JOIN #RelationshipCounts AS relationships
  ON relationships.proj_id = projects.proj_id
LEFT JOIN #OpenEndCounts AS open_ends
  ON open_ends.proj_id = projects.proj_id
LEFT JOIN #RelationshipQualityCounts AS relationship_quality
  ON relationship_quality.proj_id = projects.proj_id
LEFT JOIN #ActivityQualityCounts AS activity_quality
  ON activity_quality.proj_id = projects.proj_id
LEFT JOIN #OutOfSequenceCounts AS out_of_sequence
  ON out_of_sequence.proj_id = projects.proj_id
LEFT JOIN #LogicalLoopCounts AS logical_loops
  ON logical_loops.proj_id = projects.proj_id;

CREATE UNIQUE CLUSTERED INDEX [IX_ExpectedMetrics_proj_id]
    ON #ExpectedMetrics (proj_id);

CREATE TABLE #Differences
(
    proj_id int NOT NULL,
    metric_name nvarchar(100) NOT NULL,
    materialised_value nvarchar(4000) NULL,
    detail_value nvarchar(4000) NULL
);

INSERT INTO #Differences
    (proj_id, metric_name, materialised_value, detail_value)
SELECT
    materialised.proj_id,
    N'project_name',
    materialised.project_name,
    expected.project_name
FROM [powerbitables].[xertoolkit_result_project_metrics] AS materialised
JOIN #ExpectedMetrics AS expected
  ON expected.proj_id = materialised.proj_id
WHERE materialised.project_name <> expected.project_name
   OR (materialised.project_name IS NULL AND expected.project_name IS NOT NULL)
   OR (materialised.project_name IS NOT NULL AND expected.project_name IS NULL);

INSERT INTO #Differences
    (proj_id, metric_name, materialised_value, detail_value)
SELECT
    materialised.proj_id,
    N'updated_date',
    CONVERT(nvarchar(33), materialised.updated_date, 126),
    CONVERT(nvarchar(33), expected.updated_date, 126)
FROM [powerbitables].[xertoolkit_result_project_metrics] AS materialised
JOIN #ExpectedMetrics AS expected
  ON expected.proj_id = materialised.proj_id
WHERE materialised.updated_date <> expected.updated_date
   OR (materialised.updated_date IS NULL AND expected.updated_date IS NOT NULL)
   OR (materialised.updated_date IS NOT NULL AND expected.updated_date IS NULL);

INSERT INTO #Differences
    (proj_id, metric_name, materialised_value, detail_value)
SELECT
    materialised.proj_id,
    comparison.metric_name,
    CONVERT(nvarchar(100), comparison.materialised_value),
    CONVERT(nvarchar(100), comparison.detail_value)
FROM [powerbitables].[xertoolkit_result_project_metrics] AS materialised
JOIN #ExpectedMetrics AS expected
  ON expected.proj_id = materialised.proj_id
CROSS APPLY
(
    VALUES
    (N'activity_count', CONVERT(decimal(38,4), materialised.activity_count), CONVERT(decimal(38,4), expected.activity_count)),
    (N'dcma_activity_count', CONVERT(decimal(38,4), materialised.dcma_activity_count), CONVERT(decimal(38,4), expected.dcma_activity_count)),
    (N'relationship_count', CONVERT(decimal(38,4), materialised.relationship_count), CONVERT(decimal(38,4), expected.relationship_count)),
    (N'relationship_ratio', CONVERT(decimal(38,4), materialised.relationship_ratio), CONVERT(decimal(38,4), expected.relationship_ratio)),
    (N'missing_predecessor_count', CONVERT(decimal(38,4), materialised.missing_predecessor_count), CONVERT(decimal(38,4), expected.missing_predecessor_count)),
    (N'missing_successor_count', CONVERT(decimal(38,4), materialised.missing_successor_count), CONVERT(decimal(38,4), expected.missing_successor_count)),
    (N'open_start_count', CONVERT(decimal(38,4), materialised.open_start_count), CONVERT(decimal(38,4), expected.open_start_count)),
    (N'open_finish_count', CONVERT(decimal(38,4), materialised.open_finish_count), CONVERT(decimal(38,4), expected.open_finish_count)),
    (N'lead_count', CONVERT(decimal(38,4), materialised.lead_count), CONVERT(decimal(38,4), expected.lead_count)),
    (N'lag_count', CONVERT(decimal(38,4), materialised.lag_count), CONVERT(decimal(38,4), expected.lag_count)),
    (N'non_fs_count', CONVERT(decimal(38,4), materialised.non_fs_count), CONVERT(decimal(38,4), expected.non_fs_count)),
    (N'excessive_ss_lag_count', CONVERT(decimal(38,4), materialised.excessive_ss_lag_count), CONVERT(decimal(38,4), expected.excessive_ss_lag_count)),
    (N'excessive_ff_lag_count', CONVERT(decimal(38,4), materialised.excessive_ff_lag_count), CONVERT(decimal(38,4), expected.excessive_ff_lag_count)),
    (N'high_float_count', CONVERT(decimal(38,4), materialised.high_float_count), CONVERT(decimal(38,4), expected.high_float_count)),
    (N'negative_float_count', CONVERT(decimal(38,4), materialised.negative_float_count), CONVERT(decimal(38,4), expected.negative_float_count)),
    (N'high_duration_count', CONVERT(decimal(38,4), materialised.high_duration_count), CONVERT(decimal(38,4), expected.high_duration_count)),
    (N'constraint_count', CONVERT(decimal(38,4), materialised.constraint_count), CONVERT(decimal(38,4), expected.constraint_count)),
    (N'invalid_date_count', CONVERT(decimal(38,4), materialised.invalid_date_count), CONVERT(decimal(38,4), expected.invalid_date_count)),
    (N'in_progress_error_count', CONVERT(decimal(38,4), materialised.in_progress_error_count), CONVERT(decimal(38,4), expected.in_progress_error_count)),
    (N'riding_progress_date_count', CONVERT(decimal(38,4), materialised.riding_progress_date_count), CONVERT(decimal(38,4), expected.riding_progress_date_count)),
    (N'critical_task_count', CONVERT(decimal(38,4), materialised.critical_task_count), CONVERT(decimal(38,4), expected.critical_task_count)),
    (N'near_critical_task_count', CONVERT(decimal(38,4), materialised.near_critical_task_count), CONVERT(decimal(38,4), expected.near_critical_task_count)),
    (N'out_of_sequence_count', CONVERT(decimal(38,4), materialised.out_of_sequence_count), CONVERT(decimal(38,4), expected.out_of_sequence_count)),
    (N'logical_loop_count', CONVERT(decimal(38,4), materialised.logical_loop_count), CONVERT(decimal(38,4), expected.logical_loop_count))
) AS comparison (metric_name, materialised_value, detail_value)
WHERE comparison.materialised_value <> comparison.detail_value
   OR (comparison.materialised_value IS NULL AND comparison.detail_value IS NOT NULL)
   OR (comparison.materialised_value IS NOT NULL AND comparison.detail_value IS NULL);

SELECT
    assertion_name,
    CASE WHEN passed = 1 THEN 'PASS' ELSE 'FAIL' END AS status,
    observed_value,
    expected_value
FROM @assertions
ORDER BY assertion_name;

SELECT
    metric_name,
    COUNT(*) AS mismatched_projects
FROM #Differences
GROUP BY metric_name
ORDER BY metric_name;

SELECT TOP (200)
    proj_id,
    metric_name,
    materialised_value,
    detail_value
FROM #Differences
ORDER BY proj_id, metric_name;

IF EXISTS (SELECT 1 FROM @assertions WHERE passed = 0)
   OR EXISTS (SELECT 1 FROM #Differences)
    THROW 51412, 'All-project materialised results do not reconcile to source projects and detail calculations.', 1;

SELECT
    @active_config_version_id AS active_config_version_id,
    @full_check_run_id AS full_check_run_id,
    @source_project_rows AS reconciled_projects,
    24 AS reconciled_numeric_metrics_per_project,
    2 AS reconciled_metadata_fields_per_project,
    N'PASS' AS reconciliation_status;
