SELECT
  tp.proj_id,
  tp.task_pred_id AS relationship_id,
  tp.pred_task_id AS predecessor_task_id,
  tp.task_id AS successor_task_id,
  pred.task_code AS predecessor_code,
  pred.task_name AS predecessor_name,
  succ.task_code AS successor_code,
  succ.task_name AS successor_name,
  tp.pred_type AS relationship_type,
  tp.lag_hr_cnt AS lag_hours,
  CAST(tp.lag_hr_cnt / 8.0 AS DECIMAL(18, 2)) AS lag_days,
  pred.target_duration_days AS predecessor_duration_days,
  succ.target_duration_days AS successor_duration_days,
  pred.target_drtn_hr_cnt AS predecessor_duration_hours,
  succ.target_drtn_hr_cnt AS successor_duration_hours,
  pred.status_code AS predecessor_status_code,
  succ.status_code AS successor_status_code,
  pred.is_loe AS predecessor_is_loe,
  succ.is_loe AS successor_is_loe,
  pred.is_wbs_summary AS predecessor_is_wbs_summary,
  succ.is_wbs_summary AS successor_is_wbs_summary,
  pred.is_milestone AS predecessor_is_milestone,
  succ.is_milestone AS successor_is_milestone,
  pred.is_complete AS predecessor_is_complete,
  succ.is_complete AS successor_is_complete,
  pred.is_deleted AS predecessor_is_deleted,
  succ.is_deleted AS successor_is_deleted
FROM "__schema__".taskpred AS tp
LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS pred
  ON pred.proj_id = tp.proj_id AND pred.task_id = tp.pred_task_id
LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS succ
  ON succ.proj_id = tp.proj_id AND succ.task_id = tp.task_id;
