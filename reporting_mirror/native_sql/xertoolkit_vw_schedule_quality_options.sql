SELECT
  p.profile_code,
  p.active_config_version_id,
  v.config_version_id,
  v.version_number,
  v.state,
  o.option_code,
  o.display_name,
  o.data_type,
  o.bit_value,
  o.numeric_value,
  o.text_value,
  o.unit_code,
  o.sort_order
FROM "__schema__"."xertoolkit_schedule_quality_profile" AS p
JOIN "__schema__"."xertoolkit_schedule_quality_config_version" AS v
  ON v.profile_id = p.profile_id
JOIN "__schema__"."xertoolkit_schedule_quality_option" AS o
  ON o.config_version_id = v.config_version_id;
