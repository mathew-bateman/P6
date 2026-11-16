SELECT
  p.profile_code,
  p.active_config_version_id,
  v.config_version_id,
  v.version_number,
  v.state,
  c.constraint_type_code,
  c.display_name,
  c.is_checked,
  c.sort_order
FROM "__schema__"."xertoolkit_schedule_quality_profile" AS p
JOIN "__schema__"."xertoolkit_schedule_quality_config_version" AS v
  ON v.profile_id = p.profile_id
JOIN "__schema__"."xertoolkit_schedule_quality_constraint_type" AS c
  ON c.config_version_id = v.config_version_id;
