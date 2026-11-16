SELECT
  q.*
FROM "__schema__"."xertoolkit_schedule_quality_profile" AS p INNER JOIN "__schema__".xertoolkit_fn_relationship_quality AS q ON TRUE
WHERE
  p.profile_code = N'default';
