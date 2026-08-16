/* Enrich the existing Power BI logic-loop task view without changing refresh data. */
SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() <> N'P62212_1'
    THROW 51800, 'This script must be run against P62212_1.', 1;

IF OBJECT_ID(N'[powerbitables].[xertoolkit_result_logic_loop_tasks]', N'U') IS NULL
   OR OBJECT_ID(N'[powerbitables].[xertoolkit_result_project_metrics]', N'U') IS NULL
   OR OBJECT_ID(N'[dbo].[PROJECT]', N'U') IS NULL
   OR OBJECT_ID(N'[dbo].[TASK]', N'U') IS NULL
   OR OBJECT_ID(N'[dbo].[PROJWBS]', N'U') IS NULL
    THROW 51801, 'The schedule-quality logic-loop dependencies are missing.', 1;
GO

CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_LogicLoops]
AS
WITH ranked_loops AS
(
    SELECT
        logic.proj_id,
        logic.task_id,
        logic.loop_path,
        logic.loop_length,
        logic.calculated_date,
        logic.check_run_id,
        logic.calculation_method,
        logic.config_version_id,
        ROW_NUMBER() OVER
        (
            PARTITION BY logic.proj_id, logic.task_id
            ORDER BY
                CASE WHEN logic.calculation_method = 'self_loop' THEN 0 ELSE 1 END,
                logic.calculated_date DESC,
                logic.check_run_id DESC,
                logic.config_version_id DESC
        ) AS row_rank
    FROM [powerbitables].[xertoolkit_result_logic_loop_tasks] AS logic
)
SELECT
    /* Preserve the original four-column contract and order. */
    logic.proj_id,
    logic.task_id,
    logic.loop_path,
    logic.loop_length,
    project.proj_short_name AS project_name,
    project.last_recalc_date AS project_data_date,
    task.task_code,
    task.task_name,
    task.task_type,
    task.status_code AS activity_status,
    task.wbs_id,
    wbs.wbs_name,
    task.act_start_date AS actual_start,
    task.act_end_date AS actual_finish,
    task.early_start_date AS early_start,
    task.early_end_date AS early_finish,
    task.late_start_date AS late_start,
    task.late_end_date AS late_finish,
    task.target_start_date AS target_start,
    task.target_end_date AS target_finish,
    logic.calculation_method,
    logic.calculated_date,
    logic.check_run_id,
    logic.config_version_id,
    CONVERT(bit, 1) AS is_logical_loop,
    CONVERT(nvarchar(30), N'logical_loop') AS logical_loop_status
FROM ranked_loops AS logic
LEFT JOIN dbo.PROJECT AS project
  ON project.proj_id = logic.proj_id
LEFT JOIN dbo.TASK AS task
  ON task.proj_id = logic.proj_id
 AND task.task_id = logic.task_id
LEFT JOIN dbo.PROJWBS AS wbs
  ON wbs.proj_id = task.proj_id
 AND wbs.wbs_id = task.wbs_id
WHERE logic.row_rank = 1;
GO

SELECT
    OBJECT_NAME(OBJECT_ID(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]')) AS logic_loop_task_view,
    COL_LENGTH(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]', N'is_logical_loop') AS boolean_identifier_length,
    COL_LENGTH(N'[powerbitables].[xertoolkit_vw_PBI_LogicLoops]', N'logical_loop_status') AS text_identifier_length;
