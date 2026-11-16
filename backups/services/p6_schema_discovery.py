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


def _postgres_catalog():
    from reporting_mirror.native_settings import enabled
    if not enabled():
        return None
    from reporting_mirror.connections import postgres_connection
    with postgres_connection() as pg:
        rows = pg.execute("SELECT table_name,columns FROM quality_control.source_catalog WHERE source_code='p6_22' ORDER BY table_name").fetchall()
    return {row['table_name']:row['columns'] for row in rows}


def discover_p6_tables() -> list[str]:
    catalog = _postgres_catalog()
    if catalog is not None:
        return list(catalog)
    target = build_schedule_quality_target()
    rows = fetch_rows(
        target,
        "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_TYPE = 'BASE TABLE' "
        "ORDER BY TABLE_NAME;",
        database=target.sql_database,
    )
    return [str(row["TABLE_NAME"]) for row in rows]


def discover_p6_schema() -> list[dict[str, object]]:
    catalog = _postgres_catalog()
    if catalog is not None:
        return [{'name':name,'columns':columns} for name,columns in catalog.items()]
    target = build_schedule_quality_target()
    rows = fetch_rows(
        target,
        "SELECT TABLE_NAME, COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' ORDER BY TABLE_NAME, ORDINAL_POSITION;",
        database=target.sql_database,
    )
    tables: dict[str, list[str]] = {}
    for row in rows:
        table_name = str(row["TABLE_NAME"])
        tables.setdefault(table_name, []).append(str(row["COLUMN_NAME"]))
    return [{"name": name, "columns": columns} for name, columns in tables.items()]


def discover_p6_fields(category: str) -> list[dict[str, str]]:
    catalog = _postgres_catalog()
    if catalog is not None:
        from reporting_mirror.connections import postgres_connection
        if category in {'activity_code','udf'}:
            with postgres_connection(reader=True) as pg:
                if category == 'activity_code':
                    rows=pg.execute("SELECT actv_code_type_id,actv_code_type FROM p6_22.actvtype WHERE delete_date IS NULL ORDER BY actv_code_type").fetchall()
                    seen=set()
                    return [{'identifier':str(r['actv_code_type_id']),'label':str(r['actv_code_type']),'display_name':str(r['actv_code_type'])} for r in rows if str(r['actv_code_type']).strip() not in seen and not seen.add(str(r['actv_code_type']).strip())]
                rows=pg.execute("SELECT udf_type_id,table_name,udf_type_label,udf_type_name FROM p6_22.udftype WHERE delete_date IS NULL ORDER BY table_name,COALESCE(udf_type_label,udf_type_name)").fetchall()
                return [{'identifier':str(r['udf_type_id']),'label':f"{r['udf_type_label'] or r['udf_type_name']} [{r['table_name']}]",'display_name':str(r['udf_type_label'] or r['udf_type_name'])} for r in rows]
        table=TABLE_CATEGORIES.get(category,category.removeprefix('table:') if category.startswith('table:') else '')
        return [{'identifier':c,'label':c,'display_name':c} for c in catalog.get(table,[])]
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
    if category.startswith("table:"):
        table = category.removeprefix("table:")
    if not table:
        return []
    if table not in discover_p6_tables():
        return []
    rows = fetch_rows(
        target,
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = ? ORDER BY ORDINAL_POSITION;",
        parameters=[table],
        database=target.sql_database,
    )
    return [{"identifier": str(row["COLUMN_NAME"]), "label": str(row["COLUMN_NAME"]), "display_name": str(row["COLUMN_NAME"])} for row in rows]
