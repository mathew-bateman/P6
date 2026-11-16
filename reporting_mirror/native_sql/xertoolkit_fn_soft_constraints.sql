SELECT
  scope.proj_id,
  scope.task_id
FROM "__schema__".xertoolkit_fn_activity_in_scope('soft_constraints') AS scope
JOIN "__schema__".task AS activity
  ON activity.proj_id = scope.proj_id AND activity.task_id = scope.task_id
WHERE
  activity.cstr_type IN ('CS_MSOB', 'CS_MSOA', 'CS_MEOB', 'CS_MEOA')
  OR activity.cstr_type2 IN ('CS_MSOB', 'CS_MSOA', 'CS_MEOB', 'CS_MEOA');
