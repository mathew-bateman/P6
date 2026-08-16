from __future__ import annotations

from backups.services.mssql import fetch_rows
from backups.services.schedule_quality import build_schedule_quality_target


TABLE_CATEGORIES = {
    "task_column": "TASK",
    "project_column": "PROJECT",
    "relationship_column": "TASKPRED",
    "wbs_column": "PROJWBS",
    "resource_column": "TASKRSRC",
}


def discover_p6_fields(category: str) -> list[dict[str, str]]:
    target = build_schedule_quality_target()
    if category == "activity_code":
        rows = fetch_rows(target, "SELECT actv_code_type_id, actv_code_type FROM dbo.ACTVTYPE WHERE delete_date IS NULL ORDER BY actv_code_type;", database=target.sql_database)
        seen: set[str] = set()
        return [
            {"identifier": str(row["actv_code_type_id"]), "label": str(row["actv_code_type"]), "display_name": str(row["actv_code_type"])}
            for row in rows if str(row["actv_code_type"]).strip() not in seen and not seen.add(str(row["actv_code_type"]).strip())
        ]
    if category == "udf":
        rows = fetch_rows(target, "SELECT udf_type_id, table_name, udf_type_label, udf_type_name FROM dbo.UDFTYPE WHERE delete_date IS NULL ORDER BY table_name, ISNULL(udf_type_label, udf_type_name);", database=target.sql_database)
        return [{"identifier": str(row["udf_type_id"]), "label": f"{row['udf_type_label'] or row['udf_type_name']} [{row['table_name']}]", "display_name": str(row["udf_type_label"] or row["udf_type_name"])} for row in rows]
    table = TABLE_CATEGORIES.get(category)
    if not table:
        return []
    rows = fetch_rows(target, f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = '{table}' ORDER BY ORDINAL_POSITION;", database=target.sql_database)
    return [{"identifier": str(row["COLUMN_NAME"]), "label": str(row["COLUMN_NAME"]), "display_name": str(row["COLUMN_NAME"])} for row in rows]
