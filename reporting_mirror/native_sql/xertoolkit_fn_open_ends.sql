WITH options AS (
  SELECT
    MAX(
      CASE
        WHEN option_code = 'exclude_deleted_activities'
        THEN CAST(bit_value AS INT)
      END
    ) AS exclude_deleted_activities
  FROM "__schema__"."xertoolkit_schedule_quality_option"
  WHERE
    config_version_id = __config_version_id__
), deleted_activities AS (
  SELECT
    proj_id,
    task_id
  FROM "__schema__"._deleted_activities
), pred_logic AS (
  SELECT
    r.proj_id,
    r.task_id,
    COUNT(pred_source.task_id) AS predecessor_count,
    SUM(
      CAST(CASE
        WHEN r.pred_type = 'PR_FS'
        AND NOT pred.task_id IS NULL
        AND NOT pred_source.task_id IS NULL
        AND pred.is_loe = 0
        AND pred.is_wbs_summary = 0
        THEN 1
        WHEN r.pred_type = 'PR_SS'
        AND NOT pred.task_id IS NULL
        AND NOT pred_source.task_id IS NULL
        AND pred.is_loe = 0
        AND pred.is_wbs_summary = 0
        THEN 1
        ELSE 0
      END AS BIGINT)
    ) AS start_logic_count
  FROM "__schema__".taskpred AS r
  LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS pred
    ON pred.proj_id = r.proj_id AND pred.task_id = r.pred_task_id
  LEFT JOIN "__schema__".task AS pred_source
    ON pred_source.proj_id = r.proj_id
    AND pred_source.task_id = r.pred_task_id
    AND pred_source.delete_session_id IS NULL
  LEFT JOIN (
    SELECT DISTINCT
      proj_id,
      pred_task_id,
      task_id
    FROM "__schema__".taskpred
    WHERE
      pred_type = 'PR_FF' AND delete_session_id IS NULL
  ) AS paired_relationship
    ON paired_relationship.proj_id = r.proj_id
    AND paired_relationship.pred_task_id = r.pred_task_id
    AND paired_relationship.task_id = r.task_id
  WHERE
    r.delete_session_id IS NULL
  GROUP BY
    r.proj_id,
    r.task_id
), succ_logic AS (
  SELECT
    r.proj_id,
    r.pred_task_id AS task_id,
    COUNT(succ_source.task_id) AS successor_count,
    SUM(
      CAST(CASE
        WHEN r.pred_type = 'PR_FS'
        AND NOT succ.task_id IS NULL
        AND NOT succ_source.task_id IS NULL
        AND succ.is_loe = 0
        AND succ.is_wbs_summary = 0
        THEN 1
        WHEN r.pred_type = 'PR_FF'
        AND NOT succ.task_id IS NULL
        AND NOT succ_source.task_id IS NULL
        AND succ.is_loe = 0
        AND succ.is_wbs_summary = 0
        THEN 1
        ELSE 0
      END AS BIGINT)
    ) AS finish_logic_count
  FROM "__schema__".taskpred AS r
  LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS succ
    ON succ.proj_id = r.proj_id AND succ.task_id = r.task_id
  LEFT JOIN "__schema__".task AS succ_source
    ON succ_source.proj_id = r.proj_id
    AND succ_source.task_id = r.task_id
    AND succ_source.delete_session_id IS NULL
  LEFT JOIN (
    SELECT DISTINCT
      proj_id,
      pred_task_id,
      task_id
    FROM "__schema__".taskpred
    WHERE
      pred_type = 'PR_SS' AND delete_session_id IS NULL
  ) AS paired_relationship
    ON paired_relationship.proj_id = r.proj_id
    AND paired_relationship.pred_task_id = r.pred_task_id
    AND paired_relationship.task_id = r.task_id
  WHERE
    r.delete_session_id IS NULL
  GROUP BY
    r.proj_id,
    r.pred_task_id
), scope_rows AS (
  SELECT
    check_code,
    CASE WHEN is_enabled = 1 THEN 1 ELSE 0 END + CASE WHEN COALESCE(include_loe, 0) = 1 THEN 2 ELSE 0 END + CASE WHEN COALESCE(include_wbs_summary, 0) = 1 THEN 4 ELSE 0 END + CASE WHEN include_milestones IS NULL OR include_milestones = 1 THEN 8 ELSE 0 END + CASE WHEN COALESCE(exclude_complete, 0) = 1 THEN 16 ELSE 0 END AS scope_mask
  FROM "__schema__"."xertoolkit_schedule_quality_check_scope"
  WHERE
    config_version_id = __config_version_id__
), scope_settings AS (
  SELECT
    MAX(CASE WHEN check_code = 'missing_predecessor' THEN scope_mask END) AS missing_predecessor_scope,
    MAX(CASE WHEN check_code = 'missing_successor' THEN scope_mask END) AS missing_successor_scope,
    MAX(CASE WHEN check_code = 'open_start' THEN scope_mask END) AS open_start_scope,
    MAX(CASE WHEN check_code = 'open_finish' THEN scope_mask END) AS open_finish_scope
  FROM scope_rows
), scoped_activities AS (
  SELECT
    a.proj_id,
    a.task_id,
    a.is_milestone,
    CAST(CASE
      WHEN (
        scope.missing_predecessor_scope & 1
      ) = 1
      AND (
        COALESCE(o.exclude_deleted_activities, 1) = 0 OR deleted.task_id IS NULL
      )
      AND (
        (
          scope.missing_predecessor_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.missing_predecessor_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.missing_predecessor_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.missing_predecessor_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_missing_predecessor_scope,
    CAST(CASE
      WHEN (
        scope.missing_successor_scope & 1
      ) = 1
      AND (
        COALESCE(o.exclude_deleted_activities, 1) = 0 OR deleted.task_id IS NULL
      )
      AND (
        (
          scope.missing_successor_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.missing_successor_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.missing_successor_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.missing_successor_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_missing_successor_scope,
    CAST(CASE
      WHEN (
        scope.open_start_scope & 1
      ) = 1
      AND (
        (
          scope.open_start_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.open_start_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.open_start_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.open_start_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_open_start_scope,
    CAST(CASE
      WHEN (
        scope.open_finish_scope & 1
      ) = 1
      AND (
        (
          scope.open_finish_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.open_finish_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.open_finish_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.open_finish_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_open_finish_scope
  FROM "__schema__"."xertoolkit_vw_pbi_activities" AS a
  JOIN "__schema__".task AS source_activity
    ON source_activity.proj_id = a.proj_id
    AND source_activity.task_id = a.task_id
    AND source_activity.delete_session_id IS NULL
  LEFT JOIN deleted_activities AS deleted
    ON deleted.proj_id = a.proj_id AND deleted.task_id = a.task_id
  CROSS JOIN scope_settings AS scope
  CROSS JOIN options AS o
  WHERE
    COALESCE(o.exclude_deleted_activities, 1) = 0 OR deleted.task_id IS NULL
)
SELECT
  __config_version_id__ AS config_version_id,
  a.proj_id,
  a.task_id,
  a.is_milestone,
  a.in_open_start_scope,
  a.in_open_finish_scope,
  CAST(CASE
    WHEN a.in_missing_predecessor_scope = 1
    AND COALESCE(pred_logic.predecessor_count, 0) = 0
    THEN 1
    ELSE 0
  END AS INT) AS is_missing_predecessor, /* WORKBOOK_OPEN_END_PARITY_V1 */
  CAST(CASE
    WHEN a.in_missing_successor_scope = 1 AND COALESCE(succ_logic.successor_count, 0) = 0
    THEN 1
    ELSE 0
  END AS INT) AS is_missing_successor,
  CAST(CASE
    WHEN a.in_open_start_scope = 1
    AND a.is_milestone = 0
    AND COALESCE(pred_logic.start_logic_count, 0) = 0
    THEN 1
    ELSE 0
  END AS INT) AS is_open_start,
  CAST(CASE
    WHEN a.in_open_finish_scope = 1
    AND a.is_milestone = 0
    AND COALESCE(succ_logic.finish_logic_count, 0) = 0
    THEN 1
    ELSE 0
  END AS INT) AS is_open_finish
FROM scoped_activities AS a
LEFT JOIN pred_logic
  ON pred_logic.proj_id = a.proj_id AND pred_logic.task_id = a.task_id
LEFT JOIN succ_logic
  ON succ_logic.proj_id = a.proj_id AND succ_logic.task_id = a.task_id
WHERE
  a.in_missing_predecessor_scope = 1
  OR a.in_missing_successor_scope = 1
  OR a.in_open_start_scope = 1
  OR a.in_open_finish_scope = 1;
