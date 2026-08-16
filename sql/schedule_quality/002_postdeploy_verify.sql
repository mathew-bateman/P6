/* Read-only post-deployment and post-refresh verification. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51400, 'This verification must be run against P62212_1.', 1;

DECLARE @assertions TABLE
(
    assertion_name nvarchar(150) NOT NULL,
    passed bit NOT NULL,
    observed_value nvarchar(4000) NULL,
    expected_value nvarchar(4000) NULL
);

INSERT INTO @assertions
SELECT
    N'configuration tables present',
    CONVERT(bit, CASE WHEN COUNT(*) = 5 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'5'
FROM sys.tables AS t
JOIN sys.schemas AS s
  ON s.schema_id = t.schema_id
WHERE s.name = N'powerbitables'
  AND t.name IN
  (
      N'xertoolkit_schedule_quality_profile',
      N'xertoolkit_schedule_quality_config_version',
      N'xertoolkit_schedule_quality_check_scope',
      N'xertoolkit_schedule_quality_option',
      N'xertoolkit_schedule_quality_constraint_type'
  );

INSERT INTO @assertions
SELECT
    N'calculation functions present',
    CONVERT(bit, CASE WHEN COUNT(*) = 7 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'7'
FROM sys.objects AS o
JOIN sys.schemas AS s
  ON s.schema_id = o.schema_id
WHERE s.name = N'powerbitables'
  AND o.type = 'IF'
  AND o.name IN
  (
      N'xertoolkit_fn_activity_in_scope',
      N'xertoolkit_fn_relationship_in_scope',
      N'xertoolkit_fn_open_ends',
      N'xertoolkit_fn_relationship_quality',
      N'xertoolkit_fn_activity_quality',
      N'xertoolkit_fn_schedule_quality_task_evidence',
      N'xertoolkit_fn_out_of_sequence'
  );

INSERT INTO @assertions
SELECT
    N'high-float calculation includes the configured threshold',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
                 LIKE N'%total_float_hr_cnt >= o.high_float_days * 8.0%'
            THEN 1 ELSE 0
        END
    ),
    N'function definition inspected',
    N'total float greater than or equal to the configured threshold';

INSERT INTO @assertions
SELECT
    N'critical and near-critical float ranges do not overlap',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
                 LIKE N'%total_float_hr_cnt < 4.0%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_activity_quality]'))
                 LIKE N'%total_float_hr_cnt >= 4.0%'
            THEN 1 ELSE 0
        END
    ),
    N'function definition inspected',
    N'critical below 4 hours; near critical from 4 hours';

INSERT INTO @assertions
SELECT
    N'open-end calculations ignore soft-deleted activities and relationships',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%source_activity.delete_session_id IS NULL%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%pred_source.delete_session_id IS NULL%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%succ_source.delete_session_id IS NULL%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%WHERE r.delete_session_id IS NULL%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%FROM dbo.TASKACTV AS assignment%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%code_type.actv_code_type = ''Activity Status''%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_open_ends]'))
                    LIKE N'%code.short_name = ''DEL''%'
            THEN 1 ELSE 0
        END
    ),
    N'function definition inspected',
    N'active rows only; Activity Status DEL excluded from missing ends';

INSERT INTO @assertions
SELECT
    N'out-of-sequence complete exclusion targets the successor activity',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                 LIKE N'%OR succ.is_complete = 0%'
              OR OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                 LIKE N'%OR succ.status_code <> ''TK_Complete''%'
            THEN 1 ELSE 0
        END
    ),
    N'function definition inspected',
    N'successor-only exclusion';

INSERT INTO @assertions
SELECT
    N'out-of-sequence calculation matches the last P6 schedule snapshot',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                    LIKE N'%relationship_type = ''PR_FS''%'
             AND OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                    LIKE N'%pred.act_end_date IS NULL%'
             AND OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                    LIKE N'%last_schedule_date%'
             AND OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                    LIKE N'%relationship_create_date%'
             AND OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                    LIKE N'%relationship_update_date%'
             AND OBJECT_DEFINITION(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_out_of_sequence]'))
                    NOT LIKE N'%relationship_type = ''PR_SS''%'
            THEN 1 ELSE 0
        END
    ),
    N'function definition inspected',
    N'FS started successor, unfinished predecessor, relationship present at last schedule';

INSERT INTO @assertions
SELECT
    N'project metrics are materialised in independent aggregate stages',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
                     LIKE N'%SPLIT_STAGE_PROJECT_METRICS_V1%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
                     LIKE N'%INTO #OpenEndCounts%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
                     LIKE N'%LEFT JOIN #ActivityQualityCounts AS aqc%'
             AND OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_all_schedule_quality]'))
                     NOT LIKE N'%LEFT JOIN activity_counts AS ac%'
            THEN 1 ELSE 0
        END
    ),
    N'refresh procedure definition inspected',
    N'independent per-project aggregate temp tables';

INSERT INTO @assertions
SELECT
    N'out-of-sequence headline counts distinct successor activities',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]'))
                 LIKE N'%COUNT(DISTINCT exception.successor_task_id)%'
            THEN 1 ELSE 0
        END
    ),
    N'trigger definition inspected',
    N'one count per qualifying P6 activity';

INSERT INTO @assertions
VALUES
(
    N'schedule-quality task evidence objects are present',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]', N'U') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]', N'IF') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]', N'V') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete]', N'TR') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert]', N'TR') IS NOT NULL
            THEN 1 ELSE 0
        END
    ),
    CONCAT
    (
        N'table=', IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]', N'U') IS NULL, 0, 1),
        N', function=', IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_fn_schedule_quality_task_evidence]', N'IF') IS NULL, 0, 1),
        N', view=', IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]', N'V') IS NULL, 0, 1),
        N', triggers=',
        IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_delete]', N'TR') IS NULL, 0, 1)
        + IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_task_evidence_insert]', N'TR') IS NULL, 0, 1)
    ),
    N'table=1, function=1, view=1, triggers=2'
);

INSERT INTO @assertions
SELECT
    N'schedule-quality task evidence snapshot has the expected columns',
    CONVERT(bit, CASE WHEN COUNT(*) = 9 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'9'
FROM sys.columns
WHERE object_id = OBJECT_ID
      (N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]')
  AND name IN
  (
      N'proj_id', N'check_code', N'task_id', N'task_code', N'task_name',
      N'evidence_basis', N'check_run_id', N'refreshed_at', N'config_version_id'
  );

INSERT INTO @assertions
SELECT
    N'schedule-quality task evidence exposes the expected columns',
    CONVERT(bit, CASE WHEN COUNT(*) = 12 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'12'
FROM sys.columns
WHERE object_id = OBJECT_ID
      (N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
  AND name IN
  (
      N'config_version_id', N'check_run_id', N'refreshed_at', N'proj_id',
      N'check_code', N'check_name', N'check_sort_order', N'task_id',
      N'task_code', N'task_name', N'total_float_days', N'evidence_basis'
  );

INSERT INTO @assertions
SELECT
    N'schedule-quality task evidence view includes the two dedicated validation sources',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_DEFINITION
                 (
                     OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
                 ) LIKE N'%xertoolkit_result_logic_loop_tasks%'
             AND OBJECT_DEFINITION
                 (
                     OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
                 ) LIKE N'%xertoolkit_result_out_of_sequence_exceptions%'
            THEN 1 ELSE 0
        END
    ),
    CONCAT
    (
        N'logic-loops=',
        IIF
        (
            OBJECT_DEFINITION
            (
                OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
            ) LIKE N'%xertoolkit_result_logic_loop_tasks%',
            1,
            0
        ),
        N', out-of-sequence=',
        IIF
        (
            OBJECT_DEFINITION
            (
                OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]')
            ) LIKE N'%xertoolkit_result_out_of_sequence_exceptions%',
            1,
            0
        )
    ),
    N'logic-loops=1, out-of-sequence=1';

INSERT INTO @assertions
SELECT
    N'schedule-quality task evidence uses only supported checks',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
WHERE check_code NOT IN
(
    'missing_predecessor',
    'missing_successor',
    'open_start',
    'open_finish',
    'relationship_leads',
    'relationship_lags',
    'relationship_ratio',
    'excessive_ss_lag',
    'excessive_ff_lag',
    'high_duration',
    'high_float',
    'negative_float',
    'constraints',
    'critical_tasks',
    'near_critical_tasks',
    'invalid_dates',
    'in_progress_errors',
    'riding_progress_date'
);

INSERT INTO @assertions
SELECT
    N'schedule-quality task evidence is unique and resolves activity identifiers',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM
(
    SELECT
        config_version_id,
        proj_id,
        check_code,
        task_id,
        COUNT_BIG(*) AS duplicate_count,
        MIN(task_code) AS task_code,
        MIN(task_name) AS task_name
    FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]
    GROUP BY config_version_id, proj_id, check_code, task_id
) AS evidence
WHERE evidence.duplicate_count <> 1
   OR evidence.task_code IS NULL
   OR evidence.task_name IS NULL;

INSERT INTO @assertions
SELECT
    N'unified schedule-quality task evidence uses only supported checks',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
WHERE check_code NOT IN
(
    'missing_predecessor',
    'missing_successor',
    'open_start',
    'open_finish',
    'relationship_leads',
    'relationship_lags',
    'relationship_ratio',
    'excessive_ss_lag',
    'excessive_ff_lag',
    'high_duration',
    'high_float',
    'negative_float',
    'constraints',
    'critical_tasks',
    'near_critical_tasks',
    'invalid_dates',
    'in_progress_errors',
    'riding_progress_date',
    'logical_loops',
    'out_of_sequence'
);

INSERT INTO @assertions
SELECT
    N'unified schedule-quality task evidence is unique and resolves activity identifiers',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM
(
    SELECT
        config_version_id,
        check_run_id,
        proj_id,
        check_code,
        task_id,
        COUNT_BIG(*) AS duplicate_count,
        MIN(task_code) AS task_code,
        MIN(task_name) AS task_name
    FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
    GROUP BY config_version_id, check_run_id, proj_id, check_code, task_id
) AS evidence
WHERE evidence.duplicate_count <> 1
   OR evidence.task_code IS NULL
   OR evidence.task_name IS NULL;

INSERT INTO @assertions
SELECT
    N'schedule-quality task evidence matches its project metric snapshot',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM [powerbitables].[xertoolkit_result_schedule_quality_task_evidence] AS evidence
LEFT JOIN [powerbitables].[xertoolkit_result_project_metrics] AS metrics
  ON metrics.proj_id = evidence.proj_id
 AND metrics.check_run_id = evidence.check_run_id
 AND metrics.config_version_id = evidence.config_version_id
 AND metrics.refreshed_at = evidence.refreshed_at
WHERE metrics.proj_id IS NULL;

INSERT INTO @assertions
SELECT
    N'configuration procedures present',
    CONVERT(bit, CASE WHEN COUNT(*) = 4 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'4'
FROM sys.procedures AS p
JOIN sys.schemas AS s
  ON s.schema_id = p.schema_id
WHERE s.name = N'powerbitables'
  AND p.name IN
  (
      N'xertoolkit_get_or_create_schedule_quality_draft',
      N'xertoolkit_save_schedule_quality_draft',
      N'xertoolkit_publish_schedule_quality_config',
      N'xertoolkit_refresh_all_schedule_quality'
  );

INSERT INTO @assertions
SELECT
    N'version stamps present',
    CONVERT(bit, CASE WHEN COUNT(*) = 4 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'4'
FROM sys.columns
WHERE name = N'config_version_id'
  AND object_id IN
  (
      OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]'),
      OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]'),
      OBJECT_ID(N'[powerbitables].[xertoolkit_result_schedule_quality_task_evidence]'),
      OBJECT_ID(N'[powerbitables].[xertoolkit_refresh_run_history]')
  );

INSERT INTO @assertions
SELECT
    N'out-of-sequence exception objects present',
    CONVERT
    (
        bit,
        CASE
            WHEN OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]', N'U') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]', N'V') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_delete]', N'TR') IS NOT NULL
             AND OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]', N'TR') IS NOT NULL
            THEN 1 ELSE 0
        END
    ),
    CONCAT
    (
        N'table=', IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]', N'U') IS NULL, 0, 1),
        N', view=', IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]', N'V') IS NULL, 0, 1),
        N', triggers=',
        IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_delete]', N'TR') IS NULL, 0, 1)
        + IIF(OBJECT_ID(N'[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]', N'TR') IS NULL, 0, 1)
    ),
    N'table=1, view=1, triggers=2';

INSERT INTO @assertions
SELECT
    N'out-of-sequence exception snapshot has the expected columns',
    CONVERT(bit, CASE WHEN COUNT(*) = 24 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'24'
FROM sys.columns
WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]')
  AND name IN
  (
      N'proj_id', N'project_name', N'project_data_date', N'relationship_id',
      N'predecessor_task_id', N'predecessor_code', N'predecessor_name',
      N'predecessor_status', N'predecessor_actual_start', N'predecessor_actual_finish',
      N'successor_task_id', N'successor_code', N'successor_name', N'successor_status',
      N'successor_actual_start', N'successor_actual_finish', N'relationship_type',
      N'lag_hours', N'lag_days', N'out_of_sequence_reason', N'is_out_of_sequence',
      N'check_run_id', N'refreshed_at', N'config_version_id'
  );

INSERT INTO @assertions
SELECT
    N'Power BI out-of-sequence views expose the additive text status column',
    CONVERT(bit, CASE WHEN COUNT(*) = 2 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'2'
FROM sys.columns AS c
JOIN sys.types AS t
  ON t.user_type_id = c.user_type_id
WHERE c.object_id IN
      (
          OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequence]'),
          OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]')
      )
  AND c.name = N'out_of_sequence_status'
  AND t.name = N'nvarchar';

INSERT INTO @assertions
SELECT
    N'Power BI out-of-sequence views expose additive activity IDs',
    CONVERT(bit, CASE WHEN COUNT(*) = 2 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'2'
FROM sys.columns AS c
JOIN sys.types AS t
  ON t.user_type_id = c.user_type_id
WHERE c.object_id IN
      (
          OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequence]'),
          OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]')
      )
  AND c.name = N'activity_id'
  AND t.name = N'int';

INSERT INTO @assertions
SELECT
    N'Power BI out-of-sequence activity IDs equal successor task IDs',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM
(
    SELECT activity_id, successor_task_id
    FROM [powerbitables].[xertoolkit_vw_PBI_OutOfSequence]
    WHERE activity_id <> successor_task_id

    UNION ALL

    SELECT activity_id, successor_task_id
    FROM [powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]
    WHERE activity_id <> successor_task_id
) AS invalid_activity_ids;

INSERT INTO @assertions
SELECT
    N'Power BI out-of-sequence status values use the supported labels',
    CONVERT(bit, CASE WHEN COUNT(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'0'
FROM
(
    SELECT out_of_sequence_status
    FROM [powerbitables].[xertoolkit_vw_PBI_OutOfSequence]
    WHERE out_of_sequence_status NOT IN (N'out_of_sequence', N'not_out_of_sequence')

    UNION ALL

    SELECT out_of_sequence_status
    FROM [powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]
    WHERE out_of_sequence_status <> N'out_of_sequence'
) AS invalid_status;

INSERT INTO @assertions
SELECT
    N'Power BI logic-loop task view exposes the complete additive contract',
    CONVERT(bit, CASE WHEN COUNT(*) = 26 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'26'
FROM sys.columns
WHERE object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]')
  AND name IN
  (
      N'proj_id', N'task_id', N'loop_path', N'loop_length', N'project_name',
      N'project_data_date', N'task_code', N'task_name', N'task_type',
      N'activity_status', N'wbs_id', N'wbs_name', N'actual_start',
      N'actual_finish', N'early_start', N'early_finish', N'late_start',
      N'late_finish', N'target_start', N'target_finish', N'calculation_method',
      N'calculated_date', N'check_run_id', N'config_version_id',
      N'is_logical_loop', N'logical_loop_status'
  );

INSERT INTO @assertions
SELECT
    N'Power BI logic-loop identifiers retain Boolean and text types',
    CONVERT(bit, CASE WHEN COUNT(*) = 2 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'2'
FROM sys.columns AS c
JOIN sys.types AS t
  ON t.user_type_id = c.user_type_id
WHERE c.object_id = OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]')
  AND
  (
      (c.name = N'is_logical_loop' AND t.name = N'bit')
      OR (c.name = N'logical_loop_status' AND t.name = N'nvarchar')
  );

DECLARE @logic_loop_view_rows bigint =
(
    SELECT COUNT_BIG(*)
    FROM [powerbitables].[xertoolkit_vw_PBI_LogicLoops]
);
DECLARE @logic_loop_distinct_rows bigint =
(
    SELECT COUNT_BIG(*)
    FROM
    (
        SELECT DISTINCT proj_id, task_id
        FROM [powerbitables].[xertoolkit_result_logic_loop_tasks]
    ) AS distinct_tasks
);
DECLARE @logic_loop_metric_rows bigint =
(
    SELECT ISNULL(SUM(CONVERT(bigint, logical_loop_count)), 0)
    FROM [powerbitables].[xertoolkit_result_project_metrics]
);

INSERT INTO @assertions
VALUES
(
    N'Power BI logic-loop task rows reconcile to materialised IDs and project counts',
    CONVERT
    (
        bit,
        CASE
            WHEN @logic_loop_view_rows = @logic_loop_distinct_rows
             AND @logic_loop_view_rows = @logic_loop_metric_rows
            THEN 1 ELSE 0
        END
    ),
    CONCAT
    (
        N'view=', @logic_loop_view_rows,
        N', distinct=', @logic_loop_distinct_rows,
        N', metrics=', @logic_loop_metric_rows
    ),
    N'all equal'
);

INSERT INTO @assertions
SELECT
    N'Power BI logic-loop task rows use the supported identifiers',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM [powerbitables].[xertoolkit_vw_PBI_LogicLoops]
WHERE is_logical_loop <> 1
   OR logical_loop_status <> N'logical_loop';

INSERT INTO @assertions
SELECT
    N'Unified task evidence uses the supported special-check evidence bases',
    CONVERT(bit, CASE WHEN COUNT_BIG(*) = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(30), COUNT_BIG(*)),
    N'0'
FROM [powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]
WHERE (check_code = 'logical_loops' AND evidence_basis <> 'logical_loop_member')
   OR (check_code = 'out_of_sequence' AND evidence_basis <> 'out_of_sequence_successor');

DECLARE @profile_id int;
DECLARE @active_config_version_id bigint;

SELECT
    @profile_id = profile_id,
    @active_config_version_id = active_config_version_id
FROM [powerbitables].[xertoolkit_schedule_quality_profile]
WHERE profile_code = N'default';

INSERT INTO @assertions
VALUES
(
    N'default profile has an active version',
    CONVERT(bit, CASE WHEN @profile_id IS NOT NULL AND @active_config_version_id IS NOT NULL THEN 1 ELSE 0 END),
    COALESCE(CONVERT(nvarchar(30), @active_config_version_id), N'NULL'),
    N'non-NULL'
);

INSERT INTO @assertions
SELECT
    N'active pointer targets the same-profile active row',
    CONVERT(bit, CASE WHEN COUNT(*) = 1 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), COUNT(*)),
    N'1'
FROM [powerbitables].[xertoolkit_schedule_quality_config_version]
WHERE config_version_id = @active_config_version_id
  AND profile_id = @profile_id
  AND state = 'active';

DECLARE @expected_checks TABLE
(
    check_code varchar(50) NOT NULL PRIMARY KEY,
    include_loe bit NULL,
    include_wbs_summary bit NULL,
    include_milestones bit NULL,
    exclude_complete bit NULL
);

INSERT INTO @expected_checks
VALUES
('missing_predecessor', NULL, NULL, 1, 1),
('missing_successor', NULL, NULL, 1, 1),
('open_finish', NULL, NULL, NULL, 1),
('open_start', NULL, NULL, NULL, 1),
('relationship_leads', 0, 0, 1, 1),
('relationship_lags', 0, 0, 1, 1),
('relationship_ratio', 0, 0, 1, 1),
('constraints', 0, 0, 1, 1),
('high_float', 0, 0, 1, 1),
('negative_float', 0, 0, 1, 1),
('high_duration', 0, 0, 1, 1),
('invalid_dates', 0, 0, 1, 0),
('in_progress_errors', 0, 0, 1, 0),
('logical_loops', NULL, NULL, NULL, NULL),
('out_of_sequence', NULL, NULL, NULL, 1),
('critical_tasks', 0, 0, 1, 1),
('near_critical_tasks', 0, 0, 1, 1),
('riding_progress_date', 0, 0, 1, 1),
('excessive_ss_lag', 0, 0, 1, 1),
('excessive_ff_lag', 0, 0, 1, 1);

DECLARE @check_difference_count int =
(
    SELECT COUNT(*)
    FROM @expected_checks AS expected
    FULL OUTER JOIN
    (
        SELECT check_code, include_loe, include_wbs_summary, include_milestones, exclude_complete
        FROM [powerbitables].[xertoolkit_schedule_quality_check_scope]
        WHERE config_version_id = @active_config_version_id
          AND is_enabled = 1
    ) AS actual
      ON actual.check_code = expected.check_code
    WHERE expected.check_code IS NULL
       OR actual.check_code IS NULL
       OR ISNULL(CONVERT(tinyint, expected.include_loe), 2) <> ISNULL(CONVERT(tinyint, actual.include_loe), 2)
       OR ISNULL(CONVERT(tinyint, expected.include_wbs_summary), 2) <> ISNULL(CONVERT(tinyint, actual.include_wbs_summary), 2)
       OR ISNULL(CONVERT(tinyint, expected.include_milestones), 2) <> ISNULL(CONVERT(tinyint, actual.include_milestones), 2)
       OR ISNULL(CONVERT(tinyint, expected.exclude_complete), 2) <> ISNULL(CONVERT(tinyint, actual.exclude_complete), 2)
);

INSERT INTO @assertions
VALUES
(
    N'active check scopes equal workbook seed',
    CONVERT(bit, CASE WHEN @check_difference_count = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), @check_difference_count),
    N'0 differences'
);

DECLARE @expected_options TABLE
(
    option_code varchar(50) NOT NULL PRIMARY KEY,
    bit_value bit NULL,
    numeric_value decimal(18,4) NULL
);

INSERT INTO @expected_options
VALUES
('high_float_days', NULL, 84),
('negative_float_days', NULL, 0),
('high_duration_days', NULL, 84),
('near_critical_upper_days', NULL, 20),
('riding_days_after_data_date', NULL, 3),
('excessive_ss_percent', NULL, 50),
('excessive_ff_percent', NULL, 50),
('invalid_early_before_progress', 1, NULL),
('invalid_actual_after_progress', 1, NULL),
('progress_started_zero_percent', 1, NULL),
('progress_finished_below_100', 1, NULL),
('progress_percent_without_start', 1, NULL);

DECLARE @option_difference_count int =
(
    SELECT COUNT(*)
    FROM @expected_options AS expected
    FULL OUTER JOIN
    (
        SELECT option_code, bit_value, numeric_value
        FROM [powerbitables].[xertoolkit_schedule_quality_option]
        WHERE config_version_id = @active_config_version_id
    ) AS actual
      ON actual.option_code = expected.option_code
    WHERE expected.option_code IS NULL
       OR actual.option_code IS NULL
       OR ISNULL(CONVERT(tinyint, expected.bit_value), 2) <> ISNULL(CONVERT(tinyint, actual.bit_value), 2)
       OR ISNULL(expected.numeric_value, -999999.0000) <> ISNULL(actual.numeric_value, -999999.0000)
);

INSERT INTO @assertions
VALUES
(
    N'active options equal workbook seed',
    CONVERT(bit, CASE WHEN @option_difference_count = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), @option_difference_count),
    N'0 differences'
);

DECLARE @legacy_threshold_difference_count int =
(
    SELECT COUNT(*)
    FROM
    (
        SELECT N'High Duration' AS setting_name, CONVERT(decimal(18,4), 84) AS setting_value
        UNION ALL SELECT N'High Float', CONVERT(decimal(18,4), 84)
        UNION ALL SELECT N'Critical Float', CONVERT(decimal(18,4), 0)
        UNION ALL SELECT N'Near Critical', CONVERT(decimal(18,4), 20)
    ) AS expected
    FULL OUTER JOIN
    (
        SELECT setting_name, setting_value
        FROM [powerbitables].[xertoolkit_settings_thresholds]
        WHERE setting_name IN (N'High Duration', N'High Float', N'Critical Float', N'Near Critical')
    ) AS actual
      ON actual.setting_name = expected.setting_name
    WHERE expected.setting_name IS NULL
       OR actual.setting_name IS NULL
       OR expected.setting_value <> actual.setting_value
);

INSERT INTO @assertions
VALUES
(
    N'legacy threshold compatibility rows equal active seed',
    CONVERT(bit, CASE WHEN @legacy_threshold_difference_count = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), @legacy_threshold_difference_count),
    N'0 differences'
);

DECLARE @expected_constraints TABLE
(
    constraint_type_code varchar(20) NOT NULL PRIMARY KEY,
    is_checked bit NOT NULL
);

INSERT INTO @expected_constraints
VALUES
('CS_MSOB', 0),
('CS_MSOA', 0),
('CS_MEOB', 0),
('CS_MEOA', 0),
('CS_MANDSTART', 1),
('CS_MANDFIN', 1),
('CS_MEO', 1),
('CS_MSO', 1),
('CS_ALAP', 0),
('CS_EXPECTED', 0);

DECLARE @constraint_difference_count int =
(
    SELECT COUNT(*)
    FROM @expected_constraints AS expected
    FULL OUTER JOIN
    (
        SELECT constraint_type_code, is_checked
        FROM [powerbitables].[xertoolkit_schedule_quality_constraint_type]
        WHERE config_version_id = @active_config_version_id
    ) AS actual
      ON actual.constraint_type_code = expected.constraint_type_code
    WHERE expected.constraint_type_code IS NULL
       OR actual.constraint_type_code IS NULL
       OR expected.is_checked <> actual.is_checked
);

INSERT INTO @assertions
VALUES
(
    N'active constraint checks equal workbook seed',
    CONVERT(bit, CASE WHEN @constraint_difference_count = 0 THEN 1 ELSE 0 END),
    CONVERT(nvarchar(20), @constraint_difference_count),
    N'0 differences'
);

SELECT
    assertion_name,
    CASE WHEN passed = 1 THEN 'PASS' ELSE 'FAIL' END AS status,
    observed_value,
    expected_value
FROM @assertions
ORDER BY assertion_name;

IF EXISTS (SELECT 1 FROM @assertions WHERE passed = 0)
    THROW 51401, 'One or more schedule-quality deployment assertions failed.', 1;

/*
   Materialisation state. Legacy rows are intentionally NULL until refreshed.
   After a full refresh every row must carry the active configuration version.
*/
SELECT
    @active_config_version_id AS active_config_version_id,
    COUNT(*) AS materialised_project_rows,
    SUM(CASE WHEN config_version_id = @active_config_version_id THEN 1 ELSE 0 END) AS active_version_rows,
    SUM(CASE WHEN config_version_id IS NULL THEN 1 ELSE 0 END) AS legacy_unversioned_rows,
    SUM(CASE WHEN config_version_id IS NOT NULL AND config_version_id <> @active_config_version_id THEN 1 ELSE 0 END) AS stale_version_rows
FROM [powerbitables].[xertoolkit_result_project_metrics];

/* Canary reconciliation runs only after Radlett project 1444 has been refreshed. */
IF EXISTS
(
    SELECT 1
    FROM [powerbitables].[xertoolkit_result_project_metrics]
    WHERE proj_id = 1444
      AND config_version_id = @active_config_version_id
)
BEGIN
    DECLARE @canary_comparison TABLE
    (
        metric_name nvarchar(100) NOT NULL,
        materialised_value int NOT NULL,
        detail_value int NOT NULL,
        PRIMARY KEY (metric_name)
    );

    ;WITH open_ends AS
    (
        SELECT
            SUM(is_missing_predecessor) AS missing_predecessor_count,
            SUM(is_missing_successor) AS missing_successor_count,
            SUM(is_open_start) AS open_start_count,
            SUM(is_open_finish) AS open_finish_count
        FROM [powerbitables].[xertoolkit_fn_open_ends](@active_config_version_id)
        WHERE proj_id = 1444
    ),
    relationship_quality AS
    (
        SELECT
            SUM(is_lead) AS lead_count,
            SUM(is_lag) AS lag_count,
            SUM(is_non_fs) AS non_fs_count,
            SUM(is_excessive_ss_lag) AS excessive_ss_lag_count,
            SUM(is_excessive_ff_lag) AS excessive_ff_lag_count
        FROM [powerbitables].[xertoolkit_fn_relationship_quality](@active_config_version_id)
        WHERE proj_id = 1444
    ),
    activity_quality AS
    (
        SELECT
            SUM(is_high_float) AS high_float_count,
            SUM(is_negative_float) AS negative_float_count,
            SUM(is_high_duration) AS high_duration_count,
            SUM(is_constraint) AS constraint_count,
            SUM(is_invalid_date) AS invalid_date_count,
            SUM(is_in_progress_error) AS in_progress_error_count,
            SUM(is_riding_progress_date) AS riding_progress_date_count,
            SUM(is_critical_task) AS critical_task_count,
            SUM(is_near_critical_task) AS near_critical_task_count
        FROM [powerbitables].[xertoolkit_fn_activity_quality](@active_config_version_id)
        WHERE proj_id = 1444
    ),
    out_of_sequence AS
    (
        SELECT COUNT(DISTINCT successor_task_id) AS out_of_sequence_count
        FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
        WHERE proj_id = 1444
          AND config_version_id = @active_config_version_id
    ),
    detail AS
    (
        SELECT
            ISNULL(oe.missing_predecessor_count, 0) AS missing_predecessor_count,
            ISNULL(oe.missing_successor_count, 0) AS missing_successor_count,
            ISNULL(oe.open_start_count, 0) AS open_start_count,
            ISNULL(oe.open_finish_count, 0) AS open_finish_count,
            ISNULL(rq.lead_count, 0) AS lead_count,
            ISNULL(rq.lag_count, 0) AS lag_count,
            ISNULL(rq.non_fs_count, 0) AS non_fs_count,
            ISNULL(rq.excessive_ss_lag_count, 0) AS excessive_ss_lag_count,
            ISNULL(rq.excessive_ff_lag_count, 0) AS excessive_ff_lag_count,
            ISNULL(aq.high_float_count, 0) AS high_float_count,
            ISNULL(aq.negative_float_count, 0) AS negative_float_count,
            ISNULL(aq.high_duration_count, 0) AS high_duration_count,
            ISNULL(aq.constraint_count, 0) AS constraint_count,
            ISNULL(aq.invalid_date_count, 0) AS invalid_date_count,
            ISNULL(aq.in_progress_error_count, 0) AS in_progress_error_count,
            ISNULL(aq.riding_progress_date_count, 0) AS riding_progress_date_count,
            ISNULL(aq.critical_task_count, 0) AS critical_task_count,
            ISNULL(aq.near_critical_task_count, 0) AS near_critical_task_count,
            ISNULL(oos.out_of_sequence_count, 0) AS out_of_sequence_count
        FROM open_ends AS oe
        CROSS JOIN relationship_quality AS rq
        CROSS JOIN activity_quality AS aq
        CROSS JOIN out_of_sequence AS oos
    )
    INSERT INTO @canary_comparison
        (metric_name, materialised_value, detail_value)
    SELECT
        comparison.metric_name,
        comparison.materialised_value,
        comparison.detail_value
    FROM [powerbitables].[xertoolkit_result_project_metrics] AS materialised
    CROSS JOIN detail
    CROSS APPLY
    (
        VALUES
        (N'missing_predecessor_count', materialised.missing_predecessor_count, detail.missing_predecessor_count),
        (N'missing_successor_count', materialised.missing_successor_count, detail.missing_successor_count),
        (N'open_start_count', materialised.open_start_count, detail.open_start_count),
        (N'open_finish_count', materialised.open_finish_count, detail.open_finish_count),
        (N'lead_count', materialised.lead_count, detail.lead_count),
        (N'lag_count', materialised.lag_count, detail.lag_count),
        (N'non_fs_count', materialised.non_fs_count, detail.non_fs_count),
        (N'excessive_ss_lag_count', materialised.excessive_ss_lag_count, detail.excessive_ss_lag_count),
        (N'excessive_ff_lag_count', materialised.excessive_ff_lag_count, detail.excessive_ff_lag_count),
        (N'high_float_count', materialised.high_float_count, detail.high_float_count),
        (N'negative_float_count', materialised.negative_float_count, detail.negative_float_count),
        (N'high_duration_count', materialised.high_duration_count, detail.high_duration_count),
        (N'constraint_count', materialised.constraint_count, detail.constraint_count),
        (N'invalid_date_count', materialised.invalid_date_count, detail.invalid_date_count),
        (N'in_progress_error_count', materialised.in_progress_error_count, detail.in_progress_error_count),
        (N'riding_progress_date_count', materialised.riding_progress_date_count, detail.riding_progress_date_count),
        (N'critical_task_count', materialised.critical_task_count, detail.critical_task_count),
        (N'near_critical_task_count', materialised.near_critical_task_count, detail.near_critical_task_count),
        (N'out_of_sequence_count', materialised.out_of_sequence_count, detail.out_of_sequence_count)
    ) AS comparison (metric_name, materialised_value, detail_value)
    WHERE materialised.proj_id = 1444
      AND materialised.config_version_id = @active_config_version_id
    ;

    SELECT
        metric_name,
        materialised_value,
        detail_value,
        CASE WHEN materialised_value = detail_value THEN 'PASS' ELSE 'FAIL' END AS status
    FROM @canary_comparison
    ORDER BY metric_name;

    IF EXISTS
    (
        SELECT 1
        FROM @canary_comparison
        WHERE materialised_value <> detail_value
    )
        THROW 51402, 'Canary project materialised metrics do not reconcile to detail calculations.', 1;

    IF EXISTS
    (
        SELECT relationship_id, predecessor_task_id, successor_task_id, relationship_type
        FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@active_config_version_id)
        WHERE proj_id = 1444
          AND is_out_of_sequence = 1
        EXCEPT
        SELECT relationship_id, predecessor_task_id, successor_task_id, relationship_type
        FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
        WHERE proj_id = 1444
          AND config_version_id = @active_config_version_id
    )
    OR EXISTS
    (
        SELECT relationship_id, predecessor_task_id, successor_task_id, relationship_type
        FROM [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]
        WHERE proj_id = 1444
          AND config_version_id = @active_config_version_id
        EXCEPT
        SELECT relationship_id, predecessor_task_id, successor_task_id, relationship_type
        FROM [powerbitables].[xertoolkit_fn_out_of_sequence](@active_config_version_id)
        WHERE proj_id = 1444
          AND is_out_of_sequence = 1
    )
        THROW 51403, 'Canary out-of-sequence exception identities do not match the active calculation.', 1;

END;
GO
