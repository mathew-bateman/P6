SELECT
  q.*,
  CAST(CASE
    WHEN q.is_out_of_sequence = 1
    THEN N'out_of_sequence'
    ELSE N'not_out_of_sequence'
  END AS VARCHAR(30)) AS out_of_sequence_status,
  q.successor_task_id AS activity_id
FROM "__schema__"."xertoolkit_schedule_quality_profile" AS p INNER JOIN "__schema__".xertoolkit_fn_out_of_sequence AS q ON TRUE
WHERE
  p.profile_code = N'default';
