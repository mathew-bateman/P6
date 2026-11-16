/* OOS_DELETED_FILTER_SEMIJOIN_V1 */
WITH deleted_activities AS (
  SELECT
    proj_id,
    task_id
  FROM "__schema__"._deleted_activities
), relationships AS (
  SELECT
    tp.proj_id,
    tp.task_pred_id AS relationship_id,
    tp.pred_task_id AS predecessor_task_id,
    tp.task_id AS successor_task_id,
    tp.pred_type AS relationship_type,
    tp.create_date AS relationship_create_date,
    tp.update_date AS relationship_update_date,
    project.last_schedule_date
  FROM "__schema__".taskpred AS tp
  JOIN "__schema__".project AS project
    ON project.proj_id = tp.proj_id AND project.delete_session_id IS NULL
  WHERE
    tp.delete_session_id IS NULL
)
SELECT
  __config_version_id__ AS config_version_id,
  r.proj_id,
  r.relationship_id,
  r.predecessor_task_id,
  r.successor_task_id,
  r.relationship_type,
  pred.status_code AS predecessor_status,
  succ.status_code AS successor_status,
  CAST(CASE
    WHEN r.relationship_type = 'PR_FS'
    AND NOT succ.act_start_date IS NULL
    AND pred.act_end_date IS NULL
    AND NOT r.last_schedule_date IS NULL
    AND (
      r.relationship_create_date IS NULL
      OR r.relationship_create_date <= r.last_schedule_date
    )
    AND (
      r.relationship_update_date IS NULL
      OR r.relationship_update_date <= r.last_schedule_date
    )
    THEN 1
    ELSE 0
  END AS INT) AS is_out_of_sequence,
  CASE
    WHEN r.relationship_type = 'PR_FS'
    AND NOT succ.act_start_date IS NULL
    AND pred.act_end_date IS NULL
    AND NOT r.last_schedule_date IS NULL
    AND (
      r.relationship_create_date IS NULL
      OR r.relationship_create_date <= r.last_schedule_date
    )
    AND (
      r.relationship_update_date IS NULL
      OR r.relationship_update_date <= r.last_schedule_date
    )
    THEN 'FS successor had started while predecessor remained unfinished at the last P6 schedule'
    ELSE NULL
  END AS out_of_sequence_reason
FROM relationships AS r
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS scope
  ON scope.config_version_id = __config_version_id__
  AND scope.check_code = 'out_of_sequence'
  AND scope.is_enabled = 1
JOIN "__schema__".task AS pred
  ON pred.proj_id = r.proj_id
  AND pred.task_id = r.predecessor_task_id
  AND pred.delete_session_id IS NULL
  AND pred.delete_date IS NULL
JOIN "__schema__".task AS succ
  ON succ.proj_id = r.proj_id
  AND succ.task_id = r.successor_task_id
  AND succ.delete_session_id IS NULL
  AND succ.delete_date IS NULL
LEFT JOIN "__schema__"."xertoolkit_schedule_quality_option" AS deleted_option
  ON deleted_option.config_version_id = __config_version_id__
  AND deleted_option.option_code = 'exclude_deleted_activities'
WHERE
  (
    COALESCE(scope.exclude_complete, 0) = 0 OR succ.status_code <> 'TK_Complete'
  )
  AND (
    COALESCE(deleted_option.bit_value, 1) = 0
    OR NOT EXISTS(
      SELECT
        1
      FROM deleted_activities AS deleted
      WHERE
        deleted.proj_id = r.proj_id
        AND deleted.task_id IN (r.predecessor_task_id, r.successor_task_id)
    )
  );
