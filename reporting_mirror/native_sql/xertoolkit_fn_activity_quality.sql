WITH options AS (
  SELECT
    MAX(CASE WHEN option_code = 'high_float_days' THEN numeric_value END) AS high_float_days,
    MAX(CASE WHEN option_code = 'negative_float_days' THEN numeric_value END) AS negative_float_days,
    MAX(CASE WHEN option_code = 'high_duration_days' THEN numeric_value END) AS high_duration_days,
    MAX(CASE WHEN option_code = 'near_critical_upper_days' THEN numeric_value END) AS near_critical_upper_days,
    MAX(CASE WHEN option_code = 'riding_days_after_data_date' THEN numeric_value END) AS riding_days_after_data_date,
    MAX(
      CASE
        WHEN option_code = 'invalid_early_before_progress'
        THEN CAST(bit_value AS INT)
      END
    ) AS invalid_early_before_progress,
    MAX(
      CASE
        WHEN option_code = 'invalid_actual_after_progress'
        THEN CAST(bit_value AS INT)
      END
    ) AS invalid_actual_after_progress,
    MAX(
      CASE
        WHEN option_code = 'progress_started_zero_percent'
        THEN CAST(bit_value AS INT)
      END
    ) AS progress_started_zero_percent,
    MAX(
      CASE
        WHEN option_code = 'progress_finished_below_100'
        THEN CAST(bit_value AS INT)
      END
    ) AS progress_finished_below_100,
    MAX(
      CASE
        WHEN option_code = 'progress_percent_without_start'
        THEN CAST(bit_value AS INT)
      END
    ) AS progress_percent_without_start,
    MAX(
      CASE
        WHEN option_code = 'exclude_deleted_activities'
        THEN CAST(bit_value AS INT)
      END
    ) AS exclude_deleted_activities
  FROM "__schema__"."xertoolkit_schedule_quality_option"
  WHERE
    config_version_id = __config_version_id__
), scope_rows AS (
  SELECT
    check_code,
    CASE WHEN is_enabled = 1 THEN 1 ELSE 0 END + CASE WHEN COALESCE(include_loe, 0) = 1 THEN 2 ELSE 0 END + CASE WHEN COALESCE(include_wbs_summary, 0) = 1 THEN 4 ELSE 0 END + CASE WHEN include_milestones IS NULL OR include_milestones = 1 THEN 8 ELSE 0 END + CASE WHEN COALESCE(exclude_complete, 0) = 1 THEN 16 ELSE 0 END AS scope_mask
  FROM "__schema__"."xertoolkit_schedule_quality_check_scope"
  WHERE
    config_version_id = __config_version_id__
), scope_settings AS (
  SELECT
    MAX(CASE WHEN check_code = 'high_float' THEN scope_mask END) AS high_float_scope,
    MAX(CASE WHEN check_code = 'negative_float' THEN scope_mask END) AS negative_float_scope,
    MAX(CASE WHEN check_code = 'high_duration' THEN scope_mask END) AS high_duration_scope,
    MAX(CASE WHEN check_code = 'constraints' THEN scope_mask END) AS constraints_scope,
    MAX(CASE WHEN check_code = 'invalid_dates' THEN scope_mask END) AS invalid_dates_scope,
    MAX(CASE WHEN check_code = 'in_progress_errors' THEN scope_mask END) AS in_progress_errors_scope,
    MAX(CASE WHEN check_code = 'riding_progress_date' THEN scope_mask END) AS riding_progress_date_scope,
    MAX(CASE WHEN check_code = 'critical_tasks' THEN scope_mask END) AS critical_tasks_scope,
    MAX(CASE WHEN check_code = 'near_critical_tasks' THEN scope_mask END) AS near_critical_tasks_scope
  FROM scope_rows
), scoped_activities AS (
  SELECT
    a.*,
    CAST(CASE
      WHEN (
        scope.high_float_scope & 1
      ) = 1
      AND (
        (
          scope.high_float_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.high_float_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.high_float_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.high_float_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_high_float_scope,
    CAST(CASE
      WHEN (
        scope.negative_float_scope & 1
      ) = 1
      AND (
        (
          scope.negative_float_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.negative_float_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.negative_float_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.negative_float_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_negative_float_scope,
    CAST(CASE
      WHEN (
        scope.high_duration_scope & 1
      ) = 1
      AND (
        (
          scope.high_duration_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.high_duration_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.high_duration_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.high_duration_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_high_duration_scope,
    CAST(CASE
      WHEN (
        scope.constraints_scope & 1
      ) = 1
      AND (
        (
          scope.constraints_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.constraints_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.constraints_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.constraints_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_constraints_scope,
    CAST(CASE
      WHEN (
        scope.invalid_dates_scope & 1
      ) = 1
      AND (
        (
          scope.invalid_dates_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.invalid_dates_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.invalid_dates_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.invalid_dates_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_invalid_dates_scope,
    CAST(CASE
      WHEN (
        scope.in_progress_errors_scope & 1
      ) = 1
      AND (
        (
          scope.in_progress_errors_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.in_progress_errors_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.in_progress_errors_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.in_progress_errors_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_progress_errors_scope,
    CAST(CASE
      WHEN (
        scope.riding_progress_date_scope & 1
      ) = 1
      AND (
        (
          scope.riding_progress_date_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.riding_progress_date_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.riding_progress_date_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.riding_progress_date_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_riding_progress_date_scope,
    CAST(CASE
      WHEN (
        scope.critical_tasks_scope & 1
      ) = 1
      AND (
        (
          scope.critical_tasks_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.critical_tasks_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.critical_tasks_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.critical_tasks_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_critical_tasks_scope,
    CAST(CASE
      WHEN (
        scope.near_critical_tasks_scope & 1
      ) = 1
      AND (
        (
          scope.near_critical_tasks_scope & 2
        ) = 2 OR a.is_loe = 0
      )
      AND (
        (
          scope.near_critical_tasks_scope & 4
        ) = 4 OR a.is_wbs_summary = 0
      )
      AND (
        (
          scope.near_critical_tasks_scope & 8
        ) = 8 OR a.is_milestone = 0
      )
      AND (
        (
          scope.near_critical_tasks_scope & 16
        ) = 0 OR a.is_complete = 0
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_near_critical_tasks_scope
  FROM "__schema__"."xertoolkit_vw_pbi_activities" AS a
  CROSS JOIN scope_settings AS scope
  CROSS JOIN options AS o
  WHERE
    COALESCE(o.exclude_deleted_activities, 1) = 0 OR a.is_deleted = 0
)
SELECT
  __config_version_id__ AS config_version_id,
  a.proj_id,
  a.task_id,
  a.task_code,
  a.task_name,
  w.wbs_name,
  CAST(CASE
    WHEN a.in_high_float_scope = 1 AND a.total_float_hr_cnt >= o.high_float_days * 8.0
    THEN 1
    ELSE 0
  END AS INT) AS is_high_float,
  CAST(CASE
    WHEN a.in_negative_float_scope = 1
    AND a.total_float_hr_cnt < o.negative_float_days * 8.0
    THEN 1
    ELSE 0
  END AS INT) AS is_negative_float,
  CAST(CASE
    WHEN a.in_high_duration_scope = 1
    AND a.remain_drtn_hr_cnt > o.high_duration_days * 8.0
    THEN 1
    ELSE 0
  END AS INT) AS is_high_duration,
  CAST(CASE
    WHEN a.in_constraints_scope = 1
    AND EXISTS(
      SELECT
        1
      FROM "__schema__"."xertoolkit_schedule_quality_constraint_type" AS ct
      WHERE
        ct.config_version_id = __config_version_id__
        AND ct.is_checked = 1
        AND (
          ct.constraint_type_code = a.cstr_type
          OR ct.constraint_type_code = a.cstr_type2
          OR (
            ct.constraint_type_code = 'CS_EXPECTED' AND NOT a.expect_end_date IS NULL
          )
        )
    )
    THEN 1
    ELSE 0
  END AS INT) AS is_constraint,
  CAST(CASE
    WHEN a.in_invalid_dates_scope = 1
    AND NOT p.last_recalc_date IS NULL
    AND (
      (
        o.invalid_early_before_progress = 1
        AND a.is_complete = 0
        AND (
          CAST(a.early_start_date AS DATE) < CAST(p.last_recalc_date AS DATE)
          OR CAST(a.early_end_date AS DATE) < CAST(p.last_recalc_date AS DATE)
        )
      )
      OR (
        o.invalid_actual_after_progress = 1
        AND (
          CAST(a.act_start_date AS DATE) > CAST(p.last_recalc_date AS DATE)
          OR CAST(a.act_end_date AS DATE) > CAST(p.last_recalc_date AS DATE)
        )
      )
    )
    THEN 1
    ELSE 0
  END AS INT) AS is_invalid_date,
  CAST(CASE
    WHEN a.in_progress_errors_scope = 1
    AND (
      (
        o.progress_started_zero_percent = 1
        AND NOT a.act_start_date IS NULL
        AND a.phys_complete_pct = 0
      )
      OR (
        o.progress_finished_below_100 = 1
        AND NOT a.act_end_date IS NULL
        AND a.phys_complete_pct < 100
      )
      OR (
        o.progress_percent_without_start = 1
        AND a.act_start_date IS NULL
        AND a.phys_complete_pct > 0
      )
    )
    THEN 1
    ELSE 0
  END AS INT) AS is_in_progress_error,
  CAST(CASE
    WHEN a.in_riding_progress_date_scope = 1
    AND NOT p.last_recalc_date IS NULL
    AND a.status_code = 'TK_NotStart'
    AND CAST(a.early_start_date AS DATE) >= CAST(p.last_recalc_date AS DATE)
    AND CAST(a.early_start_date AS DATE) <= "__schema__".p6_add_days(CAST(p.last_recalc_date AS DATE), o.riding_days_after_data_date)
    THEN 1
    ELSE 0
  END AS INT) AS is_riding_progress_date,
  CAST(CASE
    WHEN a.in_critical_tasks_scope = 1 AND a.total_float_hr_cnt < 4.0
    THEN 1
    ELSE 0
  END AS INT) AS is_critical_task,
  CAST(CASE
    WHEN a.in_near_critical_tasks_scope = 1
    AND a.total_float_hr_cnt >= 4.0
    AND a.total_float_hr_cnt <= o.near_critical_upper_days * 8.0
    THEN 1
    ELSE 0
  END AS INT) AS is_near_critical_task
FROM scoped_activities AS a
JOIN "__schema__".project AS p
  ON p.proj_id = a.proj_id
CROSS JOIN options AS o
LEFT JOIN "__schema__".projwbs AS w
  ON w.proj_id = a.proj_id AND w.wbs_id = a.wbs_id
WHERE
  a.in_high_float_scope = 1
  OR a.in_negative_float_scope = 1
  OR a.in_high_duration_scope = 1
  OR a.in_constraints_scope = 1
  OR a.in_invalid_dates_scope = 1
  OR a.in_progress_errors_scope = 1
  OR a.in_riding_progress_date_scope = 1
  OR a.in_critical_tasks_scope = 1
  OR a.in_near_critical_tasks_scope = 1;
