SELECT
  tp.task_pred_id AS "Relationship ID",
  tp.task_id AS "Successor Task ID",
  tp.pred_task_id AS "Predecessor Task ID",
  tp.proj_id,
  tp.pred_proj_id,
  tp.pred_type AS "Relationship Type",
  tp.lag_hr_cnt AS "Lag Hours",
  succ.task_code AS "Successor Code",
  succ.task_name AS "Successor Name",
  pred.task_code AS "Predecessor Code",
  pred.task_name AS "Predecessor Name",
  CAST(tp.lag_hr_cnt / 8.0 AS DECIMAL(18, 2)) AS "Lag (Days)"
FROM "__schema__".taskpred AS tp
LEFT JOIN "__schema__".task AS succ
  ON succ.task_id = tp.task_id
LEFT JOIN "__schema__".task AS pred
  ON pred.task_id = tp.pred_task_id;
