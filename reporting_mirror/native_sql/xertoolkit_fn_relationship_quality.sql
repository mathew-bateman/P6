WITH options AS (
  SELECT
    MAX(CASE WHEN option_code = 'excessive_ss_percent' THEN numeric_value END) AS excessive_ss_percent,
    MAX(CASE WHEN option_code = 'excessive_ff_percent' THEN numeric_value END) AS excessive_ff_percent,
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
    MAX(CASE WHEN check_code = 'relationship_leads' THEN scope_mask END) AS leads_scope,
    MAX(CASE WHEN check_code = 'relationship_lags' THEN scope_mask END) AS lags_scope,
    MAX(CASE WHEN check_code = 'relationship_ratio' THEN scope_mask END) AS ratio_scope,
    MAX(CASE WHEN check_code = 'excessive_ss_lag' THEN scope_mask END) AS excessive_ss_scope,
    MAX(CASE WHEN check_code = 'excessive_ff_lag' THEN scope_mask END) AS excessive_ff_scope
  FROM scope_rows
), scoped_relationships AS (
  SELECT
    r.*,
    CAST(COALESCE(predecessor.is_start_milestone, 0) AS SMALLINT) AS predecessor_is_start_milestone,
    CAST(COALESCE(successor.is_finish_milestone, 0) AS SMALLINT) AS successor_is_finish_milestone,
    CAST(CASE
      WHEN (
        scope.leads_scope & 1
      ) = 1
      AND (
        (
          scope.leads_scope & 2
        ) = 2
        OR (
          r.predecessor_is_loe = 0 AND r.successor_is_loe = 0
        )
      )
      AND (
        (
          scope.leads_scope & 4
        ) = 4
        OR (
          r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0
        )
      )
      AND (
        (
          scope.leads_scope & 8
        ) = 8
        OR (
          r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0
        )
      )
      AND (
        (
          scope.leads_scope & 16
        ) = 0
        OR (
          r.predecessor_is_complete = 0 AND r.successor_is_complete = 0
        )
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_leads_scope,
    CAST(CASE
      WHEN (
        scope.lags_scope & 1
      ) = 1
      AND (
        (
          scope.lags_scope & 2
        ) = 2
        OR (
          r.predecessor_is_loe = 0 AND r.successor_is_loe = 0
        )
      )
      AND (
        (
          scope.lags_scope & 4
        ) = 4
        OR (
          r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0
        )
      )
      AND (
        (
          scope.lags_scope & 8
        ) = 8
        OR (
          r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0
        )
      )
      AND (
        (
          scope.lags_scope & 16
        ) = 0
        OR (
          r.predecessor_is_complete = 0 AND r.successor_is_complete = 0
        )
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_lags_scope,
    CAST(CASE
      WHEN (
        scope.ratio_scope & 1
      ) = 1
      AND (
        (
          scope.ratio_scope & 2
        ) = 2
        OR (
          r.predecessor_is_loe = 0 AND r.successor_is_loe = 0
        )
      )
      AND (
        (
          scope.ratio_scope & 4
        ) = 4
        OR (
          r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0
        )
      )
      AND (
        (
          scope.ratio_scope & 8
        ) = 8
        OR (
          r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0
        )
      )
      AND (
        (
          scope.ratio_scope & 16
        ) = 0
        OR (
          r.predecessor_is_complete = 0 AND r.successor_is_complete = 0
        )
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_ratio_scope,
    CAST(CASE
      WHEN (
        scope.excessive_ss_scope & 1
      ) = 1
      AND (
        (
          scope.excessive_ss_scope & 2
        ) = 2
        OR (
          r.predecessor_is_loe = 0 AND r.successor_is_loe = 0
        )
      )
      AND (
        (
          scope.excessive_ss_scope & 4
        ) = 4
        OR (
          r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0
        )
      )
      AND (
        (
          scope.excessive_ss_scope & 8
        ) = 8
        OR (
          r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0
        )
      )
      AND (
        (
          scope.excessive_ss_scope & 16
        ) = 0
        OR (
          r.predecessor_is_complete = 0 AND r.successor_is_complete = 0
        )
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_excessive_ss_scope,
    CAST(CASE
      WHEN (
        scope.excessive_ff_scope & 1
      ) = 1
      AND (
        (
          scope.excessive_ff_scope & 2
        ) = 2
        OR (
          r.predecessor_is_loe = 0 AND r.successor_is_loe = 0
        )
      )
      AND (
        (
          scope.excessive_ff_scope & 4
        ) = 4
        OR (
          r.predecessor_is_wbs_summary = 0 AND r.successor_is_wbs_summary = 0
        )
      )
      AND (
        (
          scope.excessive_ff_scope & 8
        ) = 8
        OR (
          r.predecessor_is_milestone = 0 AND r.successor_is_milestone = 0
        )
      )
      AND (
        (
          scope.excessive_ff_scope & 16
        ) = 0
        OR (
          r.predecessor_is_complete = 0 AND r.successor_is_complete = 0
        )
      )
      THEN 1
      ELSE 0
    END AS SMALLINT) AS in_excessive_ff_scope
  FROM "__schema__"."xertoolkit_vw_pbi_relationships" AS r
  LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS predecessor
    ON predecessor.proj_id = r.proj_id AND predecessor.task_id = r.predecessor_task_id
  LEFT JOIN "__schema__"."xertoolkit_vw_pbi_activities" AS successor
    ON successor.proj_id = r.proj_id AND successor.task_id = r.successor_task_id
  CROSS JOIN scope_settings AS scope
  CROSS JOIN options AS o
  WHERE
    NOT r.predecessor_is_loe IS NULL
    AND NOT r.successor_is_loe IS NULL
    AND (
      COALESCE(o.exclude_deleted_activities, 1) = 0
      OR (
        r.predecessor_is_deleted = 0 AND r.successor_is_deleted = 0
      )
    )
)
SELECT
  __config_version_id__ AS config_version_id,
  r.proj_id,
  r.relationship_id,
  r.predecessor_task_id,
  r.successor_task_id,
  r.predecessor_code,
  r.successor_code,
  r.relationship_type,
  r.lag_hours,
  r.lag_days,
  CAST(CASE WHEN r.in_leads_scope = 1 AND r.lag_hours < 0 THEN 1 ELSE 0 END AS INT) AS is_lead,
  CAST(CASE WHEN r.in_lags_scope = 1 AND r.lag_hours > 0 THEN 1 ELSE 0 END AS INT) AS is_lag,
  CAST(CASE
    WHEN r.in_ratio_scope = 1
    AND (
      (
        r.relationship_type = 'PR_SS' AND r.predecessor_is_start_milestone = 0
      )
      OR (
        r.relationship_type = 'PR_FF' AND r.successor_is_finish_milestone = 0
      )
    )
    THEN 1
    ELSE 0
  END AS INT) AS is_non_fs,
  CAST(CASE
    WHEN r.in_excessive_ss_scope = 1
    AND r.relationship_type = 'PR_SS'
    AND NOT r.predecessor_duration_hours IS NULL
    AND r.lag_hours > 0
    THEN 1
    ELSE 0
  END AS INT) AS is_excessive_ss_eligible,
  CAST(CASE
    WHEN r.in_excessive_ff_scope = 1
    AND r.relationship_type = 'PR_FF'
    AND NOT r.successor_duration_hours IS NULL
    AND r.lag_hours > 0
    THEN 1
    ELSE 0
  END AS INT) AS is_excessive_ff_eligible,
  CAST(CASE
    WHEN r.in_excessive_ss_scope = 1
    AND r.relationship_type = 'PR_SS'
    AND NOT r.predecessor_duration_hours IS NULL
    AND r.lag_hours > r.predecessor_duration_hours * o.excessive_ss_percent / 100.0
    THEN 1
    ELSE 0
  END AS INT) AS is_excessive_ss_lag, /* WORKBOOK_RELATIONSHIP_RATIO_PARITY_V1 */
  CAST(CASE
    WHEN r.in_excessive_ff_scope = 1
    AND r.relationship_type = 'PR_FF'
    AND NOT r.successor_duration_hours IS NULL
    AND r.lag_hours > r.successor_duration_hours * o.excessive_ff_percent / 100.0
    THEN 1
    ELSE 0
  END AS INT) AS is_excessive_ff_lag
FROM scoped_relationships AS r
CROSS JOIN options AS o
WHERE
  r.in_leads_scope = 1
  OR r.in_lags_scope = 1
  OR r.in_ratio_scope = 1
  OR r.in_excessive_ss_scope = 1
  OR r.in_excessive_ff_scope = 1;
