WITH deleted_activities AS (
  SELECT
    proj_id,
    task_id
  FROM "__schema__"._deleted_activities
)
SELECT
  t.proj_id,
  t.task_id,
  t.wbs_id,
  t.task_code,
  t.task_name,
  t.task_type,
  t.status_code,
  t.complete_pct_type,
  t.target_drtn_hr_cnt,
  CAST(t.target_drtn_hr_cnt / 8.0 AS DECIMAL(18, 2)) AS target_duration_days,
  t.remain_drtn_hr_cnt,
  CAST(t.remain_drtn_hr_cnt / 8.0 AS DECIMAL(18, 2)) AS remaining_duration_days,
  t.phys_complete_pct,
  t.total_float_hr_cnt,
  CAST(t.total_float_hr_cnt / 8.0 AS DECIMAL(18, 2)) AS total_float_days,
  t.free_float_hr_cnt,
  CAST(t.free_float_hr_cnt / 8.0 AS DECIMAL(18, 2)) AS free_float_days,
  t.act_start_date,
  t.act_end_date,
  t.early_start_date,
  t.early_end_date,
  t.late_start_date,
  t.late_end_date,
  t.target_start_date,
  t.target_end_date,
  t.expect_end_date,
  t.cstr_type,
  t.cstr_date,
  t.cstr_type2,
  t.cstr_date2,
  t.driving_path_flag,
  CAST(CASE WHEN t.task_type = 'TT_LOE' THEN 1 ELSE 0 END AS SMALLINT) AS is_loe,
  CAST(CASE WHEN t.task_type = 'TT_WBS' THEN 1 ELSE 0 END AS SMALLINT) AS is_wbs_summary,
  CAST(CASE WHEN t.task_type IN ('TT_Mile', 'TT_FinMile') THEN 1 ELSE 0 END AS SMALLINT) AS is_milestone,
  CAST(CASE WHEN t.task_type = 'TT_FinMile' THEN 1 ELSE 0 END AS SMALLINT) AS is_finish_milestone,
  CAST(CASE WHEN t.task_type = 'TT_Mile' THEN 1 ELSE 0 END AS SMALLINT) AS is_start_milestone,
  CAST(CASE WHEN t.status_code = 'TK_Complete' THEN 1 ELSE 0 END AS SMALLINT) AS is_complete,
  CAST(CASE WHEN deleted.task_id IS NULL THEN 0 ELSE 1 END AS SMALLINT) AS is_deleted,
  CAST(CASE
    WHEN NOT t.task_type IN ('TT_LOE', 'TT_WBS') AND t.status_code <> 'TK_Complete'
    THEN 1
    ELSE 0
  END AS SMALLINT) AS is_dcma_activity
FROM "__schema__".task AS t
LEFT JOIN deleted_activities AS deleted
  ON deleted.proj_id = t.proj_id AND deleted.task_id = t.task_id
WHERE
  t.delete_session_id IS NULL AND t.delete_date IS NULL;
