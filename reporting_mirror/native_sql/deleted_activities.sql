SELECT DISTINCT
  assignment.proj_id,
  assignment.task_id
FROM "__schema__".taskactv AS assignment
JOIN "__schema__".actvtype AS code_type
  ON code_type.actv_code_type_id = assignment.actv_code_type_id
  AND code_type.delete_session_id IS NULL
  AND code_type.delete_date IS NULL
JOIN "__schema__".actvcode AS code
  ON code.actv_code_id = assignment.actv_code_id
  AND code.delete_session_id IS NULL
  AND code.delete_date IS NULL
WHERE
  assignment.delete_session_id IS NULL
  AND assignment.delete_date IS NULL
  AND code_type.actv_code_type = 'Activity Status'
  AND code.short_name = 'DEL';
