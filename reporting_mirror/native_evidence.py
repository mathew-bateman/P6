"""Configured evidence rendering in PostgreSQL, retaining the existing P6 formats."""
from psycopg import sql

from reporting_mirror.native_engine import generation_schema, identifier

CATEGORIES = {"task_column": "task", "project_column": "project", "relationship_column": "taskpred", "wbs_column": "projwbs", "resource_column": "taskrsrc"}
RELATIONSHIP_FLAGS = {"relationship_leads": "is_lead", "relationship_lags": "is_lag", "relationship_ratio": "is_non_fs", "excessive_ss_lag": "is_excessive_ss_lag", "excessive_ff_lag": "is_excessive_ff_lag"}


def evidence_predicate(schema, table, columns, task_columns, check_code):
    """Only server-discovered identifiers enter generated SQL; values stay bound."""
    s = generation_schema(schema)
    if table == "taskpred":
        if check_code in {"open_start", "open_finish"}:
            field = "task_id" if check_code == "open_start" else "pred_task_id"
            return f"source_row.{field}=evidence.task_id AND source_row.proj_id=evidence.proj_id AND source_row.delete_session_id IS NULL"
        if check_code in RELATIONSHIP_FLAGS:
            flag = RELATIONSHIP_FLAGS[check_code]
            return f"""source_row.proj_id=evidence.proj_id AND EXISTS(SELECT 1 FROM {s}.xertoolkit_fn_relationship_quality r
              WHERE r.relationship_id=source_row.task_pred_id AND (r.predecessor_task_id=evidence.task_id OR r.successor_task_id=evidence.task_id) AND r.{flag}=1)"""
        return "source_row.proj_id=evidence.proj_id AND (source_row.task_id=evidence.task_id OR source_row.pred_task_id=evidence.task_id)"
    if table == "task" and check_code in {"open_start", "open_finish"}:
        anchor, other = ("task_id", "pred_task_id") if check_code == "open_start" else ("pred_task_id", "task_id")
        return f"""source_row.proj_id=evidence.proj_id AND source_row.delete_session_id IS NULL AND EXISTS(
          SELECT 1 FROM {s}.taskpred r WHERE r.proj_id=evidence.proj_id AND r.{anchor}=evidence.task_id AND r.{other}=source_row.task_id AND r.delete_session_id IS NULL)"""
    if "task_id" in columns:
        predicate = "source_row.task_id=evidence.task_id"
    elif "wbs_id" in columns:
        predicate = "source_row.wbs_id=task_record.wbs_id"
    else:
        candidates = [c for c in columns if c.endswith("_id") and c != "delete_session_id" and c in task_columns]
        priorities = {"clndr_id": 1, "rsrc_id": 2, "location_id": 3, "proj_id": 99}
        candidates.sort(key=lambda c: (priorities.get(c, 10), list(columns).index(c)))
        if not candidates:
            return None
        field = candidates[0]
        predicate = f"source_row.{identifier(field)}=task_record.{identifier(field)}"
    if "proj_id" in columns and "source_row.proj_id=" not in predicate:
        predicate += " AND source_row.proj_id=evidence.proj_id"
    return predicate


def format_expression(schema, table, field, data_type, display_format, config_version_id):
    s = generation_schema(schema)
    column = "source_row." + identifier(field)
    if table == "taskpred" and field == "pred_type":
        return f"replace({column},'PR_','')"
    if table == "task" and field in {"cstr_type", "cstr_type2"}:
        return f"COALESCE((SELECT c.display_name FROM {s}.xertoolkit_schedule_quality_constraint_type c WHERE c.config_version_id={int(config_version_id)} AND c.constraint_type_code={column}),{column}::text)"
    if data_type.startswith(("timestamp", "date")):
        return f"to_char({column},'DD Mon YYYY HH24:MI')"
    if field.endswith("_hr_cnt") and display_format == "p6_days_only":
        return f"CAST({column}/8.0 AS numeric(38,2))::text||' days (8h/day)'"
    if field.endswith("_hr_cnt") and display_format == "p6_hours_and_days":
        return f"CAST({column} AS numeric(38,2))::text||' hours | '||CAST({column}/8.0 AS numeric(38,2))::text||' days (8h/day)'"
    if data_type.startswith(("numeric", "decimal", "double", "real")):
        return f"CAST({column} AS numeric(38,2))::text"
    return column + "::text"


def render_configured_evidence(pg, schema, config_version_id):
    s = generation_schema(schema)
    rows = pg.execute("""SELECT c.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod) data_type
      FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum""", (schema,)).fetchall()
    tables = {}
    for row in rows:
        tables.setdefault(row["relname"], {})[row["attname"]] = row["data_type"]
    fields = pg.execute(f"SELECT * FROM {s}.xertoolkit_schedule_quality_detail_field WHERE config_version_id=%s ORDER BY check_code,sort_order,detail_field_id", (config_version_id,)).fetchall()
    pg.execute(f"CREATE TABLE {s}._configured_evidence_values(proj_id integer,check_code text,task_id integer,detail_field_id bigint,display_label text,sort_order integer,detail_value text)")
    for field in fields:
        category = field["source_category"].lower()
        table = CATEGORIES.get(category, category.removeprefix("table:"))
        column = field["source_identifier"].lower()
        check = field["check_code"]
        resolution = "NULL::text"
        if category == "activity_code" and column.isdigit():
            resolution = f"""(SELECT string_agg(DISTINCT ac.actv_code_name::text COLLATE {s}.p6_text_ci,', ' ORDER BY ac.actv_code_name::text COLLATE {s}.p6_text_ci)
              FROM {s}.taskactv ta JOIN {s}.actvcode ac ON ac.actv_code_id=ta.actv_code_id
              WHERE ta.task_id=evidence.task_id AND ac.actv_code_type_id={int(column)})"""
        elif category == 'udf' and column.isdigit():
            resolution = f"""(SELECT string_agg(DISTINCT COALESCE(uv.udf_text,to_char(uv.udf_date,'DD Mon YYYY HH24:MI'),CAST(uv.udf_number AS numeric(38,2))::text,uv.udf_code_id::text) COLLATE {s}.p6_text_ci,', ' ORDER BY COALESCE(uv.udf_text,to_char(uv.udf_date,'DD Mon YYYY HH24:MI'),CAST(uv.udf_number AS numeric(38,2))::text,uv.udf_code_id::text) COLLATE {s}.p6_text_ci)
              FROM {s}.udfvalue uv JOIN {s}.udftype ut ON ut.udf_type_id=uv.udf_type_id
              WHERE uv.udf_type_id={int(column)} AND uv.delete_session_id IS NULL AND uv.delete_date IS NULL AND ut.delete_session_id IS NULL AND ut.delete_date IS NULL
                AND (uv.proj_id IS NULL OR uv.proj_id=evidence.proj_id)
                AND CASE upper(ut.table_name) WHEN 'TASK' THEN uv.fk_id=evidence.task_id WHEN 'PROJECT' THEN uv.fk_id=evidence.proj_id WHEN 'PROJWBS' THEN uv.fk_id=task_record.wbs_id
                  WHEN 'TASKRSRC' THEN EXISTS(SELECT 1 FROM {s}.taskrsrc tr WHERE tr.taskrsrc_id=uv.fk_id AND tr.task_id=evidence.task_id AND tr.proj_id=evidence.proj_id) ELSE false END)"""
        elif table in tables and column in tables[table]:
            predicate = evidence_predicate(schema, table, tables[table], tables.get("task", {}), check)
            if predicate:
                value = format_expression(schema, table, column, tables[table][column], field.get("display_format") or "native", config_version_id)
                resolution = f"(SELECT string_agg(DISTINCT ({value}) COLLATE {s}.p6_text_ci,', ' ORDER BY ({value}) COLLATE {s}.p6_text_ci) FROM {s}.{identifier(table)} source_row WHERE {predicate} AND source_row.{identifier(column)} IS NOT NULL)"
        pg.execute(f"""INSERT INTO {s}._configured_evidence_values
          SELECT evidence.proj_id,evidence.check_code,evidence.task_id,%s,%s,%s,COALESCE({resolution},'N/A')
          FROM {s}.xertoolkit_result_schedule_quality_task_evidence evidence
          JOIN {s}.task task_record ON task_record.proj_id=evidence.proj_id AND task_record.task_id=evidence.task_id
          WHERE evidence.check_code=%s""", (field["detail_field_id"], field["display_label"], field["sort_order"], check))
    pg.execute(f"""UPDATE {s}.xertoolkit_result_schedule_quality_task_evidence evidence
      SET detail_json=formatted.detail_json,detail_display=formatted.detail_display
      FROM (SELECT proj_id,check_code,task_id,
        json_object_agg(display_label,detail_value ORDER BY sort_order,detail_field_id)::text detail_json,
        left(string_agg(display_label||': '||detail_value,E'\\r\\n' ORDER BY sort_order,detail_field_id),1000) detail_display
        FROM {s}._configured_evidence_values GROUP BY proj_id,check_code,task_id) formatted
      WHERE formatted.proj_id=evidence.proj_id AND formatted.check_code=evidence.check_code AND formatted.task_id=evidence.task_id""")
    pg.execute(f"DROP TABLE {s}._configured_evidence_values")
