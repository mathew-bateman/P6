WITH task_evidence AS (
  SELECT
    oe.proj_id,
    mapped.check_code,
    oe.task_id,
    mapped.evidence_basis
  FROM "__schema__".xertoolkit_fn_open_ends AS oe INNER JOIN LATERAL (
    VALUES
      ('missing_predecessor', oe.is_missing_predecessor, 'activity_exception'),
      ('missing_successor', oe.is_missing_successor, 'activity_exception'),
      ('open_start', oe.is_open_start, 'activity_exception'),
      ('open_finish', oe.is_open_finish, 'activity_exception')
  ) AS mapped(check_code, is_match, evidence_basis) ON TRUE
  WHERE
    mapped.is_match = 1
  UNION ALL
  SELECT
    aq.proj_id,
    mapped.check_code,
    aq.task_id,
    mapped.evidence_basis
  FROM "__schema__".xertoolkit_fn_activity_quality AS aq INNER JOIN LATERAL (
    VALUES
      ('high_duration', aq.is_high_duration, 'activity_exception'),
      ('high_float', aq.is_high_float, 'activity_exception'),
      ('negative_float', aq.is_negative_float, 'activity_exception'),
      ('constraints', aq.is_constraint, 'activity_exception'),
      ('critical_tasks', aq.is_critical_task, 'activity_exception'),
      ('near_critical_tasks', aq.is_near_critical_task, 'activity_exception'),
      ('invalid_dates', aq.is_invalid_date, 'activity_exception'),
      ('in_progress_errors', aq.is_in_progress_error, 'activity_exception'),
      ('riding_progress_date', aq.is_riding_progress_date, 'activity_exception')
  ) AS mapped(check_code, is_match, evidence_basis) ON TRUE
  WHERE
    mapped.is_match = 1
  UNION ALL
  SELECT
    rq.proj_id,
    mapped.check_code,
    endpoint.task_id,
    mapped.evidence_basis
  FROM "__schema__".xertoolkit_fn_relationship_quality AS rq INNER JOIN LATERAL (
    VALUES
      ('relationship_leads', rq.is_lead, 'relationship_endpoint'),
      ('relationship_lags', rq.is_lag, 'relationship_endpoint'),
      ('relationship_ratio', rq.is_non_fs, 'non_fs_relationship_endpoint'),
      ('excessive_ss_lag', rq.is_excessive_ss_lag, 'relationship_endpoint'),
      ('excessive_ff_lag', rq.is_excessive_ff_lag, 'relationship_endpoint')
  ) AS mapped(check_code, is_match, evidence_basis) ON TRUE INNER JOIN LATERAL (
    VALUES
      (rq.predecessor_task_id),
      (rq.successor_task_id)
  ) AS endpoint(task_id) ON TRUE
  WHERE
    mapped.is_match = 1 AND NOT endpoint.task_id IS NULL
  /* SOFT_CONSTRAINT_EVIDENCE_V1 */
  UNION ALL
  SELECT
    soft.proj_id,
    'soft_constraints',
    soft.task_id,
    'soft_constraint'
  FROM "__schema__".xertoolkit_fn_soft_constraints AS soft
)
SELECT DISTINCT
  __config_version_id__ AS config_version_id,
  proj_id,
  check_code,
  task_id,
  evidence_basis
FROM task_evidence;
