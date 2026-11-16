WITH project_codes AS (
  SELECT
    pcat.proj_id,
    MAX(CASE WHEN pct.proj_catg_type = 'Contract Type' THEN pcv.proj_catg_name END) AS "Contract Type",
    MAX(CASE WHEN pct.proj_catg_type = 'Project Type' THEN pcv.proj_catg_name END) AS "Project Type",
    MAX(CASE WHEN pct.proj_catg_type = 'Project ID' THEN pcv.proj_catg_name END) AS "Project Name",
    MAX(CASE WHEN pct.proj_catg_type = 'Industry' THEN pcv.proj_catg_name END) AS "industry",
    MAX(CASE WHEN pct.proj_catg_type = 'Account' THEN pcv.proj_catg_name END) AS "account",
    MAX(CASE WHEN pct.proj_catg_type = 'Project Status' THEN pcv.proj_catg_name END) AS "Project Status",
    MAX(CASE WHEN pct.proj_catg_type = 'Planner' THEN pcv.proj_catg_name END) AS "planner",
    MAX(CASE WHEN pct.proj_catg_type = 'Project State' THEN pcv.proj_catg_name END) AS "Project State",
    MAX(CASE WHEN pct.proj_catg_type = 'Lead Planner' THEN pcv.proj_catg_name END) AS "Lead Planner",
    MAX(CASE WHEN pct.proj_catg_type = 'Client' THEN pcv.proj_catg_name END) AS "client"
  FROM "__schema__".projpcat AS pcat
  JOIN "__schema__".pcattype AS pct
    ON pct.proj_catg_type_id = pcat.proj_catg_type_id
  JOIN "__schema__".pcatval AS pcv
    ON pcv.proj_catg_id = pcat.proj_catg_id
    AND pcv.proj_catg_type_id = pcat.proj_catg_type_id
  GROUP BY
    pcat.proj_id
), updated_dates AS (
  SELECT
    dedup.proj_id,
    MAX("__schema__".p6_try_date(formatted.formatted_date)) AS "Updated Date"
  FROM (
    SELECT DISTINCT
      op.obs_id,
      op.proj_id,
      LTRIM(
        RTRIM(
          CASE
            WHEN POSITION('-' IN REVERSE(w.wbs_name)) > 0
            THEN RIGHT(CAST(w.wbs_name AS TEXT), POSITION('-' IN REVERSE(w.wbs_name)) - 1)
            ELSE w.wbs_name
          END
        )
      ) AS update_date_text
    FROM "__schema__".obsproj AS op
    JOIN "__schema__".project AS p
      ON p.proj_id = op.proj_id AND p.project_flag = 'Y'
    LEFT JOIN "__schema__".projwbs AS w
      ON w.wbs_id = op.wbs_id
  ) AS dedup INNER JOIN LATERAL (
    SELECT
      CASE
        WHEN LENGTH(CAST(dedup.update_date_text AS TEXT)) >= 8
        THEN SUBSTRING(dedup.update_date_text FROM 1 FOR 2) || '/' || SUBSTRING(dedup.update_date_text FROM 4 FOR 2) || '/' || SUBSTRING(dedup.update_date_text FROM 7 FOR 2)
        ELSE NULL
      END AS formatted_date
  ) AS formatted ON TRUE
  GROUP BY
    dedup.proj_id
)
SELECT
  p.proj_id,
  p.proj_short_name,
  p.clndr_id,
  p.last_recalc_date,
  p.last_recalc_date AS data_date,
  p.plan_start_date,
  pc."Contract Type",
  pc."Project Type",
  pc."Project Name",
  pc."industry",
  pc."account",
  pc."Project Status",
  pc."planner",
  pc."Project State",
  pc."Lead Planner",
  pc."client",
  ud."Updated Date"
FROM "__schema__".project AS p
LEFT JOIN project_codes AS pc
  ON pc.proj_id = p.proj_id
LEFT JOIN updated_dates AS ud
  ON ud.proj_id = p.proj_id;
