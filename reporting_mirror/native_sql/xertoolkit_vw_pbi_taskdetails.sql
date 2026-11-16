WITH global_codes AS (
  SELECT
    ta.task_id,
    MAX(CASE WHEN at.actv_code_type = 'Discipline' THEN ac.actv_code_name END) AS "discipline",
    MAX(CASE WHEN at.actv_code_type = 'CDE Milestones' THEN ac.actv_code_name END) AS "CDE Milestones",
    MAX(CASE WHEN at.actv_code_type = 'Change Reference' THEN ac.actv_code_name END) AS "Change Reference",
    MAX(CASE WHEN at.actv_code_type = 'MILE - Milestone' THEN ac.actv_code_name END) AS "MILE - Milestone",
    MAX(CASE WHEN at.actv_code_type = 'G_DISCIPLINE' THEN ac.actv_code_name END) AS "g_discipline",
    MAX(CASE WHEN at.actv_code_type = 'Contract Change' THEN ac.actv_code_name END) AS "Contract Change",
    MAX(CASE WHEN at.actv_code_type = 'Activity Status' THEN ac.actv_code_name END) AS "Activity Status",
    MAX(CASE WHEN at.actv_code_type = 'Contract Milestones' THEN ac.actv_code_name END) AS "Contract Milestones",
    MAX(CASE WHEN at.actv_code_type = 'Risk' THEN ac.actv_code_name END) AS "risk",
    MAX(CASE WHEN at.actv_code_type = 'GRIP STAGES' THEN ac.actv_code_name END) AS "GRIP STAGES",
    MAX(CASE WHEN at.actv_code_type = 'Interface Detail' THEN ac.actv_code_name END) AS "Interface Detail",
    MAX(CASE WHEN at.actv_code_type = 'Activity Type' THEN ac.actv_code_name END) AS "Activity Type",
    MAX(CASE WHEN at.actv_code_type = 'Milestone Type' THEN ac.actv_code_name END) AS "Milestone Type",
    MAX(CASE WHEN at.actv_code_type = 'Certified Status' THEN ac.actv_code_name END) AS "Certified Status",
    MAX(CASE WHEN at.actv_code_type = 'Planner' THEN ac.actv_code_name END) AS "planner",
    MAX(CASE WHEN at.actv_code_type = 'Work Phase and Type' THEN ac.actv_code_name END) AS "Work Phase and Type",
    MAX(CASE WHEN at.actv_code_type = 'Critical' THEN ac.actv_code_name END) AS "critical",
    MAX(CASE WHEN at.actv_code_type = 'Design Pack' THEN ac.actv_code_name END) AS "Design Pack",
    MAX(
      CASE
        WHEN at.actv_code_type = 'Compensation Event Status'
        THEN ac.actv_code_name
      END
    ) AS "Compensation Event Status",
    MAX(
      CASE WHEN at.actv_code_type = 'EIS Commissioning Stage' THEN ac.actv_code_name END
    ) AS "EIS Commissioning Stage",
    MAX(CASE WHEN at.actv_code_type = 'Area' THEN ac.actv_code_name END) AS "area",
    MAX(CASE WHEN at.actv_code_type = 'Project Stage' THEN ac.actv_code_name END) AS "Project Stage"
  FROM "__schema__".taskactv AS ta
  JOIN "__schema__".actvtype AS at
    ON at.actv_code_type_id = ta.actv_code_type_id
  JOIN "__schema__".actvcode AS ac
    ON ac.actv_code_id = ta.actv_code_id AND ac.actv_code_type_id = ta.actv_code_type_id
  GROUP BY
    ta.task_id
)
SELECT
  t.task_id,
  t.proj_id,
  t.wbs_id,
  t.phys_complete_pct,
  t.complete_pct_type,
  t.task_type,
  t.duration_type,
  t.status_code,
  t.task_code,
  t.task_name,
  t.total_float_hr_cnt AS "Total Float (Hrs)",
  t.free_float_hr_cnt,
  t.remain_drtn_hr_cnt,
  t.act_work_qty,
  t.remain_work_qty,
  t.target_drtn_hr_cnt,
  t.cstr_date,
  t.act_start_date,
  t.act_end_date,
  t.late_start_date,
  t.late_end_date,
  t.expect_end_date,
  t.early_start_date,
  t.early_end_date,
  t.target_start_date,
  t.target_end_date,
  t.cstr_type,
  t.priority_type,
  t.driving_path_flag,
  gc."discipline",
  gc."CDE Milestones",
  gc."Change Reference",
  gc."MILE - Milestone",
  gc."g_discipline",
  gc."Contract Change",
  gc."Activity Status",
  gc."Contract Milestones",
  gc."risk",
  gc."GRIP STAGES",
  gc."Interface Detail",
  gc."Activity Type",
  gc."Milestone Type",
  gc."Certified Status",
  gc."planner",
  gc."Work Phase and Type",
  gc."critical",
  gc."Design Pack",
  gc."Compensation Event Status",
  gc."EIS Commissioning Stage",
  gc."area",
  gc."Project Stage",
  CAST(CASE WHEN LOWER(COALESCE(t.task_name, '')) LIKE '%delete%' THEN 1 ELSE 0 END AS SMALLINT) AS "Is Deleted"
FROM "__schema__".task AS t
LEFT JOIN global_codes AS gc
  ON gc.task_id = t.task_id;
