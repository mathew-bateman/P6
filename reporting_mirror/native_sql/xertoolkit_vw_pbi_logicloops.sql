WITH ranked_loops AS (
  SELECT
    logic.proj_id,
    logic.task_id,
    logic.loop_path,
    logic.loop_length,
    logic.calculated_date,
    logic.check_run_id,
    logic.calculation_method,
    logic.config_version_id,
    ROW_NUMBER() OVER (
      PARTITION BY logic.proj_id, logic.task_id
      ORDER BY CASE WHEN logic.calculation_method = 'self_loop' THEN 0 ELSE 1 END NULLS FIRST, logic.calculated_date DESC NULLS LAST, logic.check_run_id DESC NULLS LAST, logic.config_version_id DESC NULLS LAST
    ) AS row_rank
  FROM "__schema__"."xertoolkit_result_logic_loop_tasks" AS logic
)
SELECT
  logic.proj_id, /* Preserve the original four-column contract and order. */
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
  CAST(1 AS SMALLINT) AS is_logical_loop,
  CAST(N'logical_loop' AS VARCHAR(30)) AS logical_loop_status
FROM ranked_loops AS logic
LEFT JOIN "__schema__".project AS project
  ON project.proj_id = logic.proj_id
LEFT JOIN "__schema__".task AS task
  ON task.proj_id = logic.proj_id AND task.task_id = logic.task_id
LEFT JOIN "__schema__".projwbs AS wbs
  ON wbs.proj_id = task.proj_id AND wbs.wbs_id = task.wbs_id
WHERE
  logic.row_rank = 1;
