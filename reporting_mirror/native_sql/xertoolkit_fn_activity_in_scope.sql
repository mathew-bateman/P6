SELECT
  a.proj_id,
  a.task_id
FROM "__schema__"."xertoolkit_vw_pbi_activities" AS a
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS s
  ON s.config_version_id = __config_version_id__
  AND s.check_code = __check_code__
  AND s.is_enabled = 1
LEFT JOIN "__schema__"."xertoolkit_schedule_quality_option" AS deleted_option
  ON deleted_option.config_version_id = __config_version_id__
  AND deleted_option.option_code = 'exclude_deleted_activities'
WHERE
  (
    COALESCE(s.include_loe, 0) = 1 OR a.is_loe = 0
  )
  AND (
    COALESCE(s.include_wbs_summary, 0) = 1 OR a.is_wbs_summary = 0
  )
  AND (
    s.include_milestones IS NULL OR s.include_milestones = 1 OR a.is_milestone = 0
  )
  AND (
    COALESCE(s.exclude_complete, 0) = 0 OR a.is_complete = 0
  )
  AND (
    COALESCE(deleted_option.bit_value, 1) = 0 OR a.is_deleted = 0
  );
