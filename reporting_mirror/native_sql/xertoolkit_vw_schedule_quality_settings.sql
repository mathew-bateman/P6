SELECT
  p.profile_id,
  p.profile_code,
  p.profile_name,
  p.active_config_version_id,
  v.config_version_id,
  v.version_number,
  v.state,
  CAST(CASE WHEN p.active_config_version_id = v.config_version_id THEN 1 ELSE 0 END AS SMALLINT) AS is_active,
  v.based_on_config_version_id,
  v.change_note,
  v.settings_hash,
  v.created_at,
  v.created_by,
  v.updated_at,
  v.updated_by,
  v.published_at,
  v.published_by,
  c.check_code,
  c.display_name,
  c.sort_order,
  c.is_enabled,
  c.include_loe,
  c.include_wbs_summary,
  c.include_milestones,
  c.exclude_complete
FROM "__schema__"."xertoolkit_schedule_quality_profile" AS p
JOIN "__schema__"."xertoolkit_schedule_quality_config_version" AS v
  ON v.profile_id = p.profile_id
JOIN "__schema__"."xertoolkit_schedule_quality_check_scope" AS c
  ON c.config_version_id = v.config_version_id;
