"""SQL restores/calculations confined to a dedicated reporting instance."""
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from backups.services.mssql import connect, fetch_rows, execute_statement, quote_identifier as qi, sql_string as qs
from reporting_mirror.connections import staging_target, secrets_config

STAGE_DATABASES = {"p6_22": "reporting_stage_p6_22", "p6_23": "reporting_stage_p6_23"}
RAW_TABLES = {
    "PROJECT", "PROJWBS", "TASK", "TASKPRED", "TASKRSRC", "RSRC", "CALENDAR",
    "ACTVCODE", "ACTVTYPE", "TASKACTV", "PROJPCAT", "PCATTYPE", "PCATVAL",
    "UDFTYPE", "UDFVALUE", "RSRCRATE", "ROLES", "TASKMEMO", "OBS", "POBS", "OBSPROJ",
}


def query(connection, statement, parameters=()):
    cursor = connection.cursor()
    try:
        cursor.execute(statement, parameters) if parameters else cursor.execute(statement)
        result = []
        while True:
            if cursor.description:
                result.extend(dict(zip([d[0] for d in cursor.description], row)) for row in cursor.fetchall())
            if not cursor.nextset():
                break
        return result
    finally:
        cursor.close()


def assert_staging(connection):
    row = query(connection, "SELECT CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) machine")[0]
    if row["machine"] != secrets_config()["staging"]["hostname"]:
        raise RuntimeError("SQL staging identity check failed; restore was refused.")


def safe_stage_name(code):
    if code not in STAGE_DATABASES:
        raise ValueError("Unknown reporting source.")
    return STAGE_DATABASES[code]


def restore_snapshot(source, run_id, progress):
    """COPY_ONLY backup production; all restores and rebuilds occur on staging."""
    target = source.target
    database = safe_stage_name(source.code)
    bucket = Path(target.app_backup_dir).name
    if bucket not in {"axialp6", "p62212"}:
        raise RuntimeError("Reporting backup mount is not recognised.")
    if shutil.disk_usage(target.app_backup_dir).free < 80 * 1024**3:
        raise RuntimeError("Less than 80 GiB is free; reporting refresh was stopped.")
    filename = f"reporting_{source.code}_{run_id.hex}.bak"
    local_path = Path(target.app_backup_dir) / filename
    source_path = f"{target.sql_backup_dir.rstrip('/')}/{filename}"
    staging_path = f"/source-backups/{bucket}/{filename}"
    try:
        progress(f"Taking a consistent {source.get_code_display()} snapshot")
        with connect(target) as live:
            live.timeout = 1800
            query(live, f"BACKUP DATABASE {qi(target.sql_database)} TO DISK = {qs(source_path)} WITH COPY_ONLY, INIT, CHECKSUM;")
            captured_at = datetime.now(timezone.utc)
        progress(f"Restoring {source.get_code_display()} into the reporting staging container")
        with connect(staging_target()) as stage:
            stage.timeout = 1800
            assert_staging(stage)
            query(stage, f"RESTORE VERIFYONLY FROM DISK={qs(staging_path)} WITH CHECKSUM;")
            files = query(stage, f"RESTORE FILELISTONLY FROM DISK={qs(staging_path)};")
            if not files or any(f["Type"] not in {"D", "L"} for f in files):
                raise RuntimeError("Unexpected SQL backup file layout.")
            if query(stage, "SELECT DB_ID(?) AS id", (database,))[0]["id"]:
                query(stage, f"ALTER DATABASE {qi(database)} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE {qi(database)};")
            moves = []
            for file_number, f in enumerate(files, 1):
                extension = "ldf" if f["Type"] == "L" else "mdf"
                destination = f"/var/opt/mssql/data/{database}_{file_number}.{extension}"
                moves.append(f"MOVE {qs(f['LogicalName'])} TO {qs(destination)}")
            query(stage, f"RESTORE DATABASE {qi(database)} FROM DISK={qs(staging_path)} WITH CHECKSUM, RECOVERY, " + ", ".join(moves))
            query(stage, f"ALTER DATABASE {qi(database)} SET AUTO_CLOSE OFF; ALTER DATABASE {qi(database)} SET AUTO_SHRINK OFF;")
            snapshot = query(stage, f"SELECT (SELECT COUNT_BIG(*) FROM {qi(database)}.dbo.PROJECT) projects,(SELECT COUNT_BIG(*) FROM {qi(database)}.dbo.TASK) activities,(SELECT COUNT_BIG(*) FROM {qi(database)}.dbo.TASKPRED) relationships")[0]
        return {"database": target.sql_database, "staging_database": database, "snapshot_at": captured_at.isoformat(), "counts": snapshot}
    finally:
        # Only the per-run COPY_ONLY file created by this function can be removed.
        if local_path.name == filename and filename.startswith("reporting_"):
            local_path.unlink(missing_ok=True)


def install_reporting_definitions(progress):
    """Reuse the live P6 22 definitions and configuration on the P6 23 restore."""
    source_name = safe_stage_name("p6_22")
    dest_name = safe_stage_name("p6_23")
    target = staging_target()
    progress("Applying the existing reporting definitions and active settings to the P6 23 snapshot")
    with connect(target, database=source_name) as source, connect(target, database=dest_name) as dest:
        assert_staging(source)
        assert_staging(dest)
        query(dest, "IF SCHEMA_ID('powerbitables') IS NULL EXEC('CREATE SCHEMA powerbitables')")
        tables = query(source, "SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE s.name='powerbitables' ORDER BY t.name")
        for row in tables:
            name = row["name"]
            identifier = "powerbitables." + qi(name)
            query(dest, f"SELECT TOP(0) * INTO {identifier} FROM {qi(source_name)}.{identifier};")
            defaults = query(source, "SELECT c.name, d.definition FROM sys.default_constraints d JOIN sys.columns c ON c.object_id=d.parent_object_id AND c.column_id=d.parent_column_id WHERE d.parent_object_id=OBJECT_ID(?)", ("powerbitables." + name,))
            for default in defaults:
                query(dest, f"ALTER TABLE {identifier} ADD DEFAULT {default['definition']} FOR {qi(default['name'])}")
            if not name.startswith("xertoolkit_result_") and name != "xertoolkit_refresh_run_history":
                columns = query(source, "SELECT name,is_identity FROM sys.columns WHERE object_id=OBJECT_ID(?) AND system_type_id<>189 ORDER BY column_id", ("powerbitables." + name,))
                column_list = ",".join(qi(c["name"]) for c in columns)
                identity = any(c["is_identity"] for c in columns)
                if identity:
                    query(dest, f"SET IDENTITY_INSERT {identifier} ON")
                query(dest, f"INSERT INTO {identifier} ({column_list}) SELECT {column_list} FROM {qi(source_name)}.{identifier}")
                if identity:
                    query(dest, f"SET IDENTITY_INSERT {identifier} OFF")
            # SELECT INTO omits indexes and constraints. Preserve index keys,
            # uniqueness and filtering before the reporting rebuild runs.
            index_rows = query(source, "SELECT i.name,i.is_unique,i.type,i.filter_definition,c.name column_name,ic.key_ordinal,ic.is_included_column,ic.is_descending_key FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id WHERE i.object_id=OBJECT_ID(?) AND i.type IN(1,2) AND i.is_disabled=0 AND i.is_hypothetical=0 ORDER BY i.index_id,ic.is_included_column,ic.key_ordinal,ic.index_column_id", ("powerbitables." + name,))
            indexes = {}
            for index_row in index_rows:
                indexes.setdefault(index_row["name"], []).append(index_row)
            for index_name, entries in indexes.items():
                first = entries[0]
                keys = ",".join(qi(e["column_name"]) + (" DESC" if e["is_descending_key"] else " ASC") for e in entries if not e["is_included_column"])
                included = [qi(e["column_name"]) for e in entries if e["is_included_column"]]
                statement = "CREATE " + ("UNIQUE " if first["is_unique"] else "") + ("CLUSTERED " if first["type"] == 1 else "NONCLUSTERED ") + "INDEX " + qi(index_name) + " ON " + identifier + " (" + keys + ")"
                if included:
                    statement += " INCLUDE (" + ",".join(included) + ")"
                if first["filter_definition"]:
                    statement += " WHERE " + first["filter_definition"]
                query(dest, statement)
        modules = query(source, "SELECT s.name schema_name,o.name,RTRIM(o.type) type,OBJECT_DEFINITION(o.object_id) definition,ISNULL(tr.is_disabled,0) is_disabled,OBJECT_SCHEMA_NAME(o.parent_object_id) parent_schema,OBJECT_NAME(o.parent_object_id) parent_name FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id LEFT JOIN sys.triggers tr ON tr.object_id=o.object_id WHERE (s.name='powerbitables' AND o.type IN('V','P','IF','FN','TF')) OR (o.type='TR' AND OBJECT_SCHEMA_NAME(o.parent_object_id)='powerbitables') ORDER BY o.name")
        pending = list(modules)
        errors = {}
        while pending:
            completed = []
            for module in pending:
                definition = re.sub(r"\bCREATE\s+(?:OR\s+ALTER\s+)?(VIEW|FUNCTION|PROCEDURE|TRIGGER)\b", r"CREATE OR ALTER \1", module["definition"], count=1, flags=re.I)
                try:
                    query(dest, definition)
                    if module["type"] == "TR" and module["is_disabled"]:
                        query(dest, f"DISABLE TRIGGER {qi(module['schema_name'])}.{qi(module['name'])} ON {qi(module['parent_schema'])}.{qi(module['parent_name'])}")
                    completed.append(module)
                except Exception as error:
                    errors[module["name"]] = str(error)[:500]
            if not completed:
                raise RuntimeError("Could not install staging reporting definitions: " + str(errors))
            pending = [m for m in pending if m not in completed]
    normalize_reporting_contracts()


def normalize_reporting_contracts():
    """Bind legacy SELECT-star views by name, retaining each source's contract.

    Earlier SQL function revisions added scope/eligibility columns without
    refreshing dependent view metadata. Explicit projections prevent old
    ordinal bindings from labelling scope flags as check results.
    """
    from reporting_mirror.native_engine import CONTRACTS
    for code in ("p6_22", "p6_23"):
        with connect(staging_target(), database=safe_stage_name(code)) as connection:
            assert_staging(connection)
            for suffix, function in [("openends", "open_ends"), ("relationshipquality", "relationship_quality")]:
                name = "xertoolkit_vw_pbi_" + suffix
                columns = CONTRACTS["published_by_source"][code][name]
                types = {"integer": "int", "bigint": "bigint", "smallint": "smallint", "text": "nvarchar(max)"}
                fields = ",".join("CAST(q." + qi(c["name"]) + " AS " + types.get(c["type"], c["type"]) + ") AS " + qi(c["name"]) for c in columns)
                query(connection, "CREATE OR ALTER VIEW powerbitables." + qi(name) + " AS SELECT " + fields + " FROM powerbitables.xertoolkit_schedule_quality_profile p CROSS APPLY powerbitables.xertoolkit_fn_" + function + "(p.active_config_version_id) q WHERE p.profile_code='default'")


def rebuild_quality(source, progress):
    database = safe_stage_name(source.code)
    progress(f"Calculating {source.get_code_display()} reporting results on the isolated SQL instance")
    with connect(staging_target(), database=database) as connection:
        connection.timeout = 5400
        assert_staging(connection)
        query(connection, "EXEC powerbitables.xertoolkit_refresh_all_schedule_quality @trigger_type='manual';")
        check = query(connection, "SELECT TOP(1) status,processed_project_count,config_version_id,completed_at FROM powerbitables.xertoolkit_refresh_run_history ORDER BY check_run_id DESC")[0]
        if check["status"] != "success":
            raise RuntimeError(f"{source.get_code_display()} reporting rebuild failed.")
        # Detailed evidence is generated by reporting-table DML triggers. A
        # successful metrics procedure alone does not prove evidence is complete.
        for code, metric in (("high_float", "high_float_count"), ("negative_float", "negative_float_count"), ("high_duration", "high_duration_count"), ("invalid_dates", "invalid_date_count"), ("in_progress_errors", "in_progress_error_count"), ("critical_tasks", "critical_task_count"), ("near_critical_tasks", "near_critical_task_count"), ("missing_predecessor", "missing_predecessor_count"), ("missing_successor", "missing_successor_count"), ("soft_constraints", "soft_constraint_count")):
            counts = query(connection, f"SELECT (SELECT COALESCE(SUM(CONVERT(bigint,{qi(metric)})),0) FROM powerbitables.xertoolkit_result_project_metrics) metrics,(SELECT COUNT_BIG(*) FROM powerbitables.xertoolkit_result_schedule_quality_task_evidence WHERE check_code=?) evidence", (code,))[0]
            if counts["metrics"] != counts["evidence"]:
                raise RuntimeError(f"{source.get_code_display()} {code} evidence did not reconcile with quality metrics ({counts['evidence']} / {counts['metrics']}).")
        check["evidence_count_checks_passed"] = 10
        return check


def object_columns(connection, schema, name):
    return query(connection, "SELECT c.name,ty.name type_name,c.max_length,c.precision,c.scale,c.is_nullable FROM sys.columns c JOIN sys.types ty ON ty.user_type_id=c.user_type_id WHERE c.object_id=OBJECT_ID(?) ORDER BY c.column_id", (schema + "." + name,))


def reporting_objects(connection):
    objects = query(connection, "SELECT s.name schema_name,o.name,o.type FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE (s.name='powerbitables' AND (o.type='V' OR (o.type='U' AND o.name LIKE 'xertoolkit_schedule_quality_%'))) OR (s.name='dbo' AND o.type='U') ORDER BY s.name,o.name")
    return [o for o in objects if o["schema_name"] != "dbo" or o["name"].upper() in RAW_TABLES]
