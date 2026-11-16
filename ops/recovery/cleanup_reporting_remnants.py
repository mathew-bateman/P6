#!/usr/bin/env python3
"""Archive, rehearse and remove the exact retired SQL reporting remnants.

The SQL allowlist is explicit. Native P6 objects, P6 maintenance recovery
tables, database users and roles are outside its scope. Capture contains
authentication data from the obsolete reporting app and must remain private.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys


P622_TABLES = tuple(("powerbitables", name) for name in (
    "xertoolkit_nr_soft_constraints_backup", "xertoolkit_refresh_run_history",
    "xertoolkit_schedule_quality_check_scope", "xertoolkit_schedule_quality_config_version",
    "xertoolkit_schedule_quality_constraint_type", "xertoolkit_schedule_quality_detail_field",
    "xertoolkit_schedule_quality_option", "xertoolkit_schedule_quality_profile",
    "xertoolkit_settings_thresholds", "xertoolkit_soft_constraints_module_backup",
)) + tuple(("xertoolkit_rollback", name) for name in (
    "schedule_quality_20260714_indexes", "schedule_quality_20260714_logic_loop_tasks",
    "schedule_quality_20260714_modules", "schedule_quality_20260714_project_metrics",
    "schedule_quality_20260714_refresh_history", "schedule_quality_20260714_settings_thresholds",
    "schedule_quality_20260714_table_columns", "schedule_quality_20260817_soft_deleted_task_modules",
))
DJANGO_TABLES = tuple(("dbo", name) for name in (
    "auth_group", "auth_group_permissions", "auth_permission", "auth_user",
    "auth_user_groups", "auth_user_user_permissions", "django_admin_log",
    "django_content_type", "django_migrations", "django_session",
))
SOURCES = {
    "p6_22": {"slug": "p62212", "database": "P62212_1", "machine": "xps4-p62212",
              "tables": P622_TABLES, "schemas": ("powerbitables", "xertoolkit_rollback")},
    "p6_23": {"slug": "axialp6", "database": "Axial", "machine": "xps4-axialp6",
              "tables": DJANGO_TABLES, "schemas": ("powerbitables",)},
}
LOCK_ID = 620252210
PREVIOUS_RECOVERY = Path("/app/.recovery/20261116T1016Z-pre-sql-retirement")
CAPTURE_NAME = "remnants-capture.json"
OWNED_CHILD_TYPES = frozenset({"C", "D", "F", "PK", "UQ"})


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode()).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def private_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, ensure_ascii=False)
        stream.write("\n")
    Path(path).chmod(0o600)


def directory_path(value):
    path = Path(value).resolve()
    if path.parent != Path("/app/.recovery").resolve() or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{5,95}", path.name):
        raise ValueError("Use an exact private /app/.recovery/<new-id> directory.")
    return path


def setup():
    for parent in Path(__file__).resolve().parents:
        if (parent / "manage.py").is_file():
            sys.path.insert(0, str(parent)); break
    sys.path.insert(0, "/app")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "p6_backup_admin.settings")
    import django
    django.setup()


def sql_query(connection, statement, parameters=()):
    from reporting_mirror.sql_staging import query
    return query(connection, statement, parameters)


def qi(name):
    from backups.services.mssql import quote_identifier
    return quote_identifier(name)


def runtime_gate():
    from django_celery_beat.models import PeriodicTask
    from reporting_mirror.models import ReportingConfig, ReportingRun, SQLParallelRun, NativeEngineValidation
    from reporting_mirror.sql_retirement import retired
    config = ReportingConfig.current()
    if not retired() or not (config.enabled and config.serve_reports and config.manage_settings_in_postgres
            and config.calculation_engine == "postgres" and not config.pending_engine and not config.sql_parallel_enabled):
        raise RuntimeError("The retired SQL layer and active PostgreSQL reporting/settings state must be confirmed first.")
    if PeriodicTask.objects.filter(task="backups.tasks.execute_schedule_quality_refresh", enabled=True).exists():
        raise RuntimeError("The obsolete SQL reporting schedule is enabled.")
    if ReportingRun.objects.filter(status__in=["queued", "running"]).exists() or SQLParallelRun.objects.filter(status__in=["queued", "running"]).exists() or NativeEngineValidation.objects.filter(status__in=["queued", "running"]).exists():
        raise RuntimeError("Reporting work is queued or running; retry after it completes.")
    if not ReportingRun.objects.filter(engine="postgres", status="success").exists():
        raise RuntimeError("No successful PostgreSQL publication exists.")


def identity(connection, code, *, staging=False):
    from reporting_mirror.sql_staging import assert_staging, safe_stage_name
    value = sql_query(connection, "SELECT DB_NAME() database_name,CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) machine")[0]
    if staging:
        assert_staging(connection)
        if value["database_name"] != safe_stage_name(code):
            raise RuntimeError("Unexpected staging database.")
    elif value != {"database_name": SOURCES[code]["database"], "machine": SOURCES[code]["machine"]}:
        raise RuntimeError("Live P6 SQL identity does not match the explicit cleanup target.")
    return value


def scoped_objects(objects, table_pairs):
    """Return only the named tables and their own known constraint objects."""
    tables = {r["object_id"] for r in objects if r["type"] == "U" and (r["schema_name"], r["name"]) in table_pairs}
    present = {(r["schema_name"], r["name"]) for r in objects if r["object_id"] in tables}
    if present != set(table_pairs):
        raise RuntimeError("The exact reporting table allowlist changed: " + str(sorted(set(table_pairs) ^ present)))
    ids = set(tables)
    for row in objects:
        if row["parent_object_id"] in tables:
            if row["type"] not in OWNED_CHILD_TYPES:
                raise RuntimeError("An unexpected object is attached to a cleanup table: " + row["name"])
            ids.add(row["object_id"])
    return tables, ids


def protected_objects(objects, removed_ids):
    return [{key: row[key] for key in ("schema_name", "name", "type", "parent_schema", "parent_name",
                "is_encrypted", "created_at", "modified_at", "definition_sha256")}
            for row in objects if row["object_id"] not in removed_ids]


def object_catalogue(connection):
    rows = sql_query(connection, """SELECT o.object_id,s.name schema_name,o.name,RTRIM(o.type) type,
        o.parent_object_id,OBJECT_SCHEMA_NAME(o.parent_object_id) parent_schema,
        OBJECT_NAME(o.parent_object_id) parent_name,OBJECT_DEFINITION(o.object_id) definition,
        CONVERT(int,OBJECTPROPERTYEX(o.object_id,'IsEncrypted')) is_encrypted,
        CONVERT(nvarchar(33),o.create_date,126) created_at,CONVERT(nvarchar(33),o.modify_date,126) modified_at
        FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
        WHERE o.is_ms_shipped=0 ORDER BY s.name,o.name,o.type""")
    for row in rows:
        definition = row.pop("definition")
        if row["type"] in {"V", "P", "FN", "IF", "TF", "TR"} and definition is None and row["is_encrypted"]!=1:
            raise RuntimeError("An executable SQL definition could not be inspected: " + row["name"])
        # P6 ships encrypted native modules. They are never in this removal
        # scope; preserve their exact opaque-object metadata instead of assuming
        # that an unavailable encrypted definition means an empty SQL body.
        row["definition_sha256"] = None if definition is None and row["is_encrypted"]==1 else hashlib.sha256((definition or "").encode()).hexdigest()
    return rows


def validate_dependencies(connection, code, tables, ids):
    external = []
    pairs = set(SOURCES[code]["tables"])
    folded_pairs = {(schema.lower(),name.lower()) for schema,name in pairs}
    for row in sql_query(connection, """SELECT referencing_id,referenced_id,referenced_schema_name,
            referenced_entity_name FROM sys.sql_expression_dependencies"""):
        named_pair=((row["referenced_schema_name"] or "dbo").lower(),(row["referenced_entity_name"] or "").lower())
        if (row["referenced_id"] in ids or named_pair in folded_pairs) and row["referencing_id"] not in ids:
            external.append(row)
    foreign = sql_query(connection, """SELECT fk.name,fk.parent_object_id,fk.referenced_object_id,
        OBJECT_SCHEMA_NAME(fk.parent_object_id) parent_schema,OBJECT_NAME(fk.parent_object_id) parent_name
        FROM sys.foreign_keys fk ORDER BY fk.name""")
    own_foreign = []
    for row in foreign:
        if row["parent_object_id"] in tables or row["referenced_object_id"] in tables:
            if row["parent_object_id"] not in tables or row["referenced_object_id"] not in tables:
                external.append(row)
            else:
                own_foreign.append(row)
    for row in sql_query(connection, "SELECT name,base_object_name FROM sys.synonyms"):
        text = row["base_object_name"].replace("[", "").replace("]", "").lower()
        if any(text.endswith(schema.lower()+"."+name.lower()) for schema,name in pairs):
            external.append(row)
    database = sql_query(connection, "SELECT DB_NAME() n")[0]["n"]
    for other in sql_query(connection, "SELECT name FROM sys.databases WHERE state=0 AND database_id>4 AND name<>DB_NAME() ORDER BY name"):
        for row in sql_query(connection, "SELECT referencing_id,referenced_schema_name,referenced_entity_name FROM " + qi(other["name"]) + ".sys.sql_expression_dependencies WHERE referenced_database_name=?", (database,)):
            if ((row["referenced_schema_name"] or "dbo").lower(),(row["referenced_entity_name"] or "").lower()) in folded_pairs:
                external.append(dict(row, database=other["name"]))
    if external:
        raise RuntimeError("Objects outside the removal allowlist depend on reporting remnants: " + json.dumps(external, default=str))
    return own_foreign


def table_snapshot(connection, schema, name, *, include_rows=True, hold=False):
    from reporting_mirror.native_settings import encode
    from reporting_mirror.sync import ContentHash
    identifier = schema + "." + name
    columns = sql_query(connection, """SELECT c.name,c.column_id,c.system_type_id,c.user_type_id,
        ty.name type_name,SCHEMA_NAME(ty.schema_id) type_schema,c.max_length,c.precision,c.scale,
        c.is_nullable,c.is_identity,c.is_computed,CONVERT(varchar(80),ic.seed_value) identity_seed,
        CONVERT(varchar(80),ic.increment_value) identity_increment,dc.name default_name,dc.definition default_definition,
        cc.definition computed_definition
        FROM sys.columns c JOIN sys.types ty ON ty.user_type_id=c.user_type_id
        LEFT JOIN sys.identity_columns ic ON ic.object_id=c.object_id AND ic.column_id=c.column_id
        LEFT JOIN sys.default_constraints dc ON dc.object_id=c.default_object_id
        LEFT JOIN sys.computed_columns cc ON cc.object_id=c.object_id AND cc.column_id=c.column_id
        WHERE c.object_id=OBJECT_ID(?) ORDER BY c.column_id""", (identifier,))
    if not columns or any(c["is_computed"] for c in columns):
        raise RuntimeError("Unexpected or unavailable cleanup table columns: " + identifier)
    fields = ["CONVERT(varchar(48),"+qi(c["name"])+",127) AS "+qi(c["name"])
              if c["system_type_id"] in {41,42,43} else qi(c["name"]) for c in columns]
    statement = "SELECT " + ",".join(fields) + " FROM " + qi(schema) + "." + qi(name)
    if hold:
        statement += " WITH(TABLOCK,HOLDLOCK)"
    rows = sql_query(connection, statement)
    checksum = ContentHash()
    for row in rows:
        checksum.add(tuple(row[c["name"]] for c in columns))
    indexes = sql_query(connection, """SELECT i.name,i.is_unique,i.type,i.filter_definition,
        i.is_primary_key,i.is_unique_constraint,c.name column_name,ic.key_ordinal,
        ic.is_included_column,ic.is_descending_key
        FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
        JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
        WHERE i.object_id=OBJECT_ID(?) AND i.type IN(1,2)
        ORDER BY i.index_id,ic.is_included_column,ic.key_ordinal,ic.index_column_id""", (identifier,))
    constraints = sql_query(connection, """SELECT name,RTRIM(type) type,OBJECT_DEFINITION(object_id) definition
        FROM sys.objects WHERE parent_object_id=OBJECT_ID(?) ORDER BY name""", (identifier,))
    foreign_columns = sql_query(connection, """SELECT fk.name,fkc.constraint_column_id,
        pc.name parent_column,OBJECT_SCHEMA_NAME(fk.referenced_object_id) referenced_schema,
        OBJECT_NAME(fk.referenced_object_id) referenced_table,rc.name referenced_column,
        fk.delete_referential_action_desc,fk.update_referential_action_desc,fk.is_disabled,fk.is_not_trusted
        FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id=fk.object_id
        JOIN sys.columns pc ON pc.object_id=fkc.parent_object_id AND pc.column_id=fkc.parent_column_id
        JOIN sys.columns rc ON rc.object_id=fkc.referenced_object_id AND rc.column_id=fkc.referenced_column_id
        WHERE fk.parent_object_id=OBJECT_ID(?) ORDER BY fk.name,fkc.constraint_column_id""", (identifier,))
    result = {"schema_name":schema,"name":name,"columns":columns,"indexes":indexes,
        "constraints":constraints,"foreign_key_columns":foreign_columns,"row_count":checksum.count,
        "row_digest":checksum.value(),"temporal_export":"SQL ISO strings preserve datetime2/time/datetimeoffset precision"}
    if include_rows:
        result["rows"] = encode(rows)
    result["metadata_sha256"] = digest({key:result[key] for key in ("columns","indexes","constraints","foreign_key_columns")})
    return result


def source_snapshot(connection, code, *, staging=False, include_rows=True, hold=False):
    value = identity(connection, code, staging=staging)
    objects = object_catalogue(connection)
    tables, ids = scoped_objects(objects, SOURCES[code]["tables"])
    schemas = []
    for name in SOURCES[code]["schemas"]:
        found = sql_query(connection, "SELECT name,USER_NAME(principal_id) owner FROM sys.schemas WHERE name=?", (name,))
        if len(found) != 1 or found[0]["owner"] != "dbo":
            raise RuntimeError("An expected reporting schema or its ownership changed: " + name)
        if any(row["schema_name"]==name and row["object_id"] not in ids for row in objects):
            raise RuntimeError("An unexpected object exists in reporting schema: " + name)
        schemas.extend(found)
    own_foreign = validate_dependencies(connection, code, tables, ids)
    preserved = protected_objects(objects, ids)
    permissions = sql_query(connection, """SELECT dp.class_desc,dp.permission_name,dp.state_desc,
        USER_NAME(dp.grantee_principal_id) grantee,USER_NAME(dp.grantor_principal_id) grantor,
        CASE WHEN dp.class=3 THEN SCHEMA_NAME(dp.major_id) ELSE OBJECT_SCHEMA_NAME(dp.major_id) END schema_name,
        CASE WHEN dp.class=1 THEN OBJECT_NAME(dp.major_id) END object_name,dp.minor_id
        FROM sys.database_permissions dp WHERE dp.class IN(1,3) ORDER BY dp.class,dp.major_id,dp.grantee_principal_id,dp.permission_name""")
    permissions = [p for p in permissions if (p["schema_name"],p["object_name"]) in SOURCES[code]["tables"] or (p["class_desc"]=="SCHEMA" and p["schema_name"] in SOURCES[code]["schemas"])]
    result = {"identity":value,"scope":[dict(schema=s,name=n) for s,n in SOURCES[code]["tables"]],
        "schemas":schemas,"objects":[r for r in objects if r["object_id"] in ids],
        "foreign_keys":own_foreign,"permissions":permissions,
        "protected_catalogue":preserved,"protected_catalogue_sha256":digest(preserved),
        "tables":[table_snapshot(connection,s,n,include_rows=include_rows,hold=hold) for s,n in SOURCES[code]["tables"]]}
    return result


def comparable(snapshot):
    return {"tables":[{k:table[k] for k in ("schema_name","name","row_count","row_digest","metadata_sha256")} for table in snapshot["tables"]],
        "schemas":snapshot["schemas"],"protected_catalogue_sha256":snapshot["protected_catalogue_sha256"]}


def protected_after(connection, captured):
    rows = object_catalogue(connection)
    # Cleanup objects have gone; every remaining object must equal protected capture.
    actual = protected_objects(rows, set())
    if actual != captured["protected_catalogue"]:
        raise RuntimeError("A protected P6 or maintenance object changed during cleanup.")
    return digest(actual)


def core_counts(connection):
    return sql_query(connection, "SELECT (SELECT COUNT_BIG(*) FROM dbo.PROJECT) projects,(SELECT COUNT_BIG(*) FROM dbo.TASK) activities,(SELECT COUNT_BIG(*) FROM dbo.TASKPRED) relationships")[0]


def validate_removal_scope(code, captured):
    expected_schemas = set(SOURCES[code]["schemas"])
    if {s["name"] for s in captured["schemas"]} != expected_schemas or any(s["owner"]!="dbo" for s in captured["schemas"]):
        raise RuntimeError("Removal schemas differ from the explicit allowlist.")
    for foreign in captured["foreign_keys"]:
        if (foreign["parent_schema"],foreign["parent_name"]) not in SOURCES[code]["tables"]:
            raise RuntimeError("A foreign key outside the table allowlist cannot be removed.")


def remove_exact(connection, code, captured):
    """Caller owns transaction. Never infer DROP targets from a schema name."""
    validate_removal_scope(code,captured)
    for foreign in captured["foreign_keys"]:
        sql_query(connection, "ALTER TABLE " + qi(foreign["parent_schema"]) + "." + qi(foreign["parent_name"]) + " DROP CONSTRAINT " + qi(foreign["name"]))
    for schema,name in SOURCES[code]["tables"]:
        sql_query(connection, "DROP TABLE " + qi(schema) + "." + qi(name))
    for row in captured["schemas"]:
        if sql_query(connection, "SELECT count(*) n FROM sys.objects WHERE schema_id=SCHEMA_ID(?)", (row["name"],))[0]["n"]:
            raise RuntimeError("Schema is not empty after the explicit table removals.")
        sql_query(connection, "DROP SCHEMA " + qi(row["name"]))
    for schema,name in SOURCES[code]["tables"]:
        if sql_query(connection, "SELECT OBJECT_ID(?) n", (schema+"."+name,))[0]["n"] is not None:
            raise RuntimeError("A named reporting table remains after cleanup.")
    return protected_after(connection, captured)


def get_connection(code, *, staging=False):
    from backups.models import BackupTarget
    from backups.services.mssql import connect
    from reporting_mirror.connections import staging_target
    from reporting_mirror.sql_staging import safe_stage_name
    if staging:
        connection = connect(staging_target(), database=safe_stage_name(code))
    else:
        target = BackupTarget.objects.get(slug=SOURCES[code]["slug"])
        if target.sql_database != SOURCES[code]["database"]:
            raise RuntimeError("Backup target no longer identifies the approved P6 database.")
        connection = connect(target, database=target.sql_database)
    connection.timeout = 120
    return connection


def capture(directory):
    if (directory/CAPTURE_NAME).exists():
        raise FileExistsError("Capture already exists; use a new cleanup ID.")
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    value = {"format":"p6-retired-reporting-remnants-v1","created_at":datetime.now(timezone.utc).isoformat(),
        "sources":{},"rows_lossless_encoding":"reporting_mirror.native_settings.encode; SQL temporal ISO strings"}
    for code in SOURCES:
        connection = get_connection(code)
        try:
            sql_query(connection, "SET XACT_ABORT ON; BEGIN TRANSACTION")
            value["sources"][code] = source_snapshot(connection, code, hold=True)
        finally:
            try:sql_query(connection, "IF @@TRANCOUNT>0 ROLLBACK TRANSACTION")
            finally:connection.close()
    private_json(directory/CAPTURE_NAME, value)
    # Preserve the legacy reference defaults outside SQL Express for future audits.
    table = next(t for t in value["sources"]["p6_22"]["tables"] if t["name"]=="schedule_quality_20260714_table_columns")
    private_json(directory/"legacy-reference-table-defaults.json", table["rows"])
    result = {"status":"captured","recovery_id":directory.name,"capture_sha256":file_digest(directory/CAPTURE_NAME),
        "tables":sum(len(s["tables"]) for s in value["sources"].values()),"schemas":3}
    private_json(directory/"capture-receipt.json", result)
    return result


def load_capture(directory):
    data = json.loads((directory/CAPTURE_NAME).read_text())
    receipt = json.loads((directory/"capture-receipt.json").read_text())
    if data.get("format")!="p6-retired-reporting-remnants-v1" or receipt.get("capture_sha256")!=file_digest(directory/CAPTURE_NAME) or set(data["sources"])!=set(SOURCES):
        raise RuntimeError("The exact cleanup capture or its checksum changed.")
    for code in SOURCES:
        if [(t["schema_name"],t["name"]) for t in data["sources"][code]["tables"]] != list(SOURCES[code]["tables"]):
            raise RuntimeError("Captured table scope differs from the hard-coded cleanup allowlist.")
    return data, receipt


def rehearse(directory):
    data, receipt = load_capture(directory)
    proof = {"status":"passed","capture_sha256":receipt["capture_sha256"],"sources":{},"verified_at":datetime.now(timezone.utc).isoformat()}
    for code in SOURCES:
        connection = get_connection(code, staging=True)
        try:
            sql_query(connection, "SET XACT_ABORT ON; BEGIN TRANSACTION")
            current = source_snapshot(connection, code, staging=True, include_rows=False, hold=True)
            if comparable(current)!=comparable(data["sources"][code]):
                raise RuntimeError("Staging is not the captured same-snapshot reporting state: " + code)
            before = core_counts(connection)
            protected = remove_exact(connection, code, current)
            after = core_counts(connection)
            if before!=after:
                raise RuntimeError("P6 source row counts changed during isolated cleanup rehearsal.")
            proof["sources"][code] = {"tables_removed":len(SOURCES[code]["tables"]),"protected_catalogue_sha256":protected,
                "core_counts":before,"core_counts_unchanged":True,"transaction_rolled_back":True}
        finally:
            try:sql_query(connection, "IF @@TRANCOUNT>0 ROLLBACK TRANSACTION")
            finally:connection.close()
        check=get_connection(code,staging=True)
        try:
            restored = source_snapshot(check, code, staging=True, include_rows=False)
            if comparable(restored)!=comparable(data["sources"][code]):
                raise RuntimeError("Staging cleanup rehearsal did not roll back exactly.")
        finally:
            check.close()
    private_json(directory/"rehearsal.json", proof)
    return proof


def backup_gate(directory, receipt):
    offsite = json.loads((directory/"offsite_receipt.json").read_text())
    encryption = json.loads((directory/"encryption_manifest.json").read_text())
    if offsite.get("upload_download_decrypt_sha256")!="passed" or offsite.get("recovery_id")!=directory.name:
        raise RuntimeError("The current cleanup export needs verified encrypted offsite publication.")
    if encryption.get("capture_manifest_sha256")!=file_digest(directory/"manifest.json"):
        raise RuntimeError("Offsite publication does not match the current capture manifest.")
    if offsite.get("archive_sha256")!=file_digest(directory/"recovery.tar.gz") or offsite.get("encrypted_sha256")!=file_digest(directory/"recovery.tar.gz.enc"):
        raise RuntimeError("Cleanup recovery archive checksums changed after offsite verification.")
    entries = json.loads((directory/"manifest.json").read_text()).get("files",[])
    expected = {r["path"]:r["sha256"] for r in entries}
    for name in (CAPTURE_NAME,"capture-receipt.json","rehearsal.json"):
        if expected.get(name)!=file_digest(directory/name):
            raise RuntimeError("An approved capture/proof is missing from the offsite manifest: " + name)
    rehearsal = json.loads((directory/"rehearsal.json").read_text())
    if rehearsal.get("status")!="passed" or rehearsal.get("capture_sha256")!=receipt["capture_sha256"]:
        raise RuntimeError("This exact capture has no successful cleanup rehearsal.")
    # Keep the independently verified complete P6 restore point as well.
    previous = json.loads((PREVIOUS_RECOVERY/"VERIFIED.json").read_text())
    restored = json.loads((PREVIOUS_RECOVERY/"restore-verification.json").read_text())
    captured = json.loads((PREVIOUS_RECOVERY/"application/capture.json").read_text())
    previous_offsite = json.loads((PREVIOUS_RECOVERY/"offsite_receipt.json").read_text())
    if previous.get("status")!="verified" or restored.get("status")!="verified" or previous_offsite.get("upload_download_decrypt_sha256")!="passed":
        raise RuntimeError("The original full P6 restore/offsite proof is missing.")
    for code, source in SOURCES.items():
        record = next(r for r in captured["sql_backups"] if r["target_slug"]==source["slug"])
        sql_proof = next(r for r in restored["sql"]["databases"] if r["target_slug"]==source["slug"])
        backup_path=(PREVIOUS_RECOVERY/record["path"]).resolve(strict=True)
        if not backup_path.is_relative_to(PREVIOUS_RECOVERY.resolve(strict=True)):
            raise RuntimeError("The previous SQL backup path escaped its immutable recovery set.")
        if file_digest(backup_path)!=record["sha256"] or sql_proof["backup_sha256"]!=record["sha256"] or sql_proof.get("object_catalogue_and_module_hashes")!="matched":
            raise RuntimeError("The full P6 backup/restore identity or checksum is invalid: " + code)


def validate_completed_journal(record, code, receipt, protected):
    expected = {"database":SOURCES[code]["database"],"machine":SOURCES[code]["machine"],
        "capture_sha256":receipt["capture_sha256"],"tables_removed":len(SOURCES[code]["tables"]),
        "protected_catalogue_sha256":protected}
    if any(record.get(key)!=value for key,value in expected.items()) or set(record.get("schemas_removed",[]))!=set(SOURCES[code]["schemas"]):
        raise RuntimeError("A previous cleanup journal does not match this target, capture and protected catalogue.")


def preflight_target(connection, code, captured, receipt, directory):
    """Hold pending table locks, or prove a fully completed target before skip."""
    identity(connection,code)
    objects=object_catalogue(connection)
    remaining=[r for r in objects if r["type"]=="U" and (r["schema_name"],r["name"]) in SOURCES[code]["tables"]]
    schemas=[r for r in sql_query(connection,"SELECT name FROM sys.schemas") if r["name"] in SOURCES[code]["schemas"]]
    journal=directory/("applied-"+code+".json")
    if not remaining and not schemas:
        protected=protected_after(connection,captured)
        if journal.exists():
            record=json.loads(journal.read_text())
            validate_completed_journal(record,code,receipt,protected)
        else:
            record={"database":SOURCES[code]["database"],"machine":SOURCES[code]["machine"],
                "capture_sha256":receipt["capture_sha256"],"tables_removed":len(SOURCES[code]["tables"]),
                "schemas_removed":list(SOURCES[code]["schemas"]),"protected_catalogue_sha256":protected,
                "resumed_completed_state_verified":True}
        return {"complete":True,"record":record,"journal_exists":journal.exists()}
    if journal.exists():
        raise RuntimeError("A completed journal exists but its reporting tables or schemas have returned.")
    current=source_snapshot(connection,code,include_rows=False,hold=True)
    if comparable(current)!=comparable(captured):
        raise RuntimeError("The live cleanup table contents or protected P6 catalogue changed: "+code)
    return {"complete":False,"snapshot":current}


def apply(directory):
    if (directory/"cleanup-result.json").exists():
        raise FileExistsError("Cleanup result already exists; do not reapply a completed plan.")
    data,receipt=load_capture(directory)
    backup_gate(directory,receipt)
    outcome={"status":"complete","capture_sha256":receipt["capture_sha256"],"sources":{},
        "scope":"Only the explicit 28 retired reporting tables and three custom schemas"}
    connections={}; prepared={}
    try:
        # Preflight every database before the first commit. Pending custom-table
        # read locks are retained until that source's explicit DDL commit.
        for code in SOURCES:
            connection=get_connection(code);connections[code]=connection
            sql_query(connection,"SET XACT_ABORT ON; SET LOCK_TIMEOUT 5000; BEGIN TRANSACTION")
            sql_lock=sql_query(connection,"DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=?,@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=0; SELECT @r n",("powerbitables.xertoolkit.schedule_quality.refresh",))[0]["n"]
            if sql_lock<0:
                raise RuntimeError("Another SQL operation holds the old reporting lock.")
            prepared[code]=preflight_target(connection,code,data["sources"][code],receipt,directory)
        for code in SOURCES:
            connection=connections[code];state=prepared[code]
            journal=directory/("applied-"+code+".json")
            if state["complete"]:
                sql_query(connection,"COMMIT TRANSACTION")
                record=state["record"]
                if not state["journal_exists"]:
                    private_json(journal,record)
                outcome["sources"][code]=record
                continue
            current=state["snapshot"]
            before=core_counts(connection)
            protected=remove_exact(connection,code,current)
            after=core_counts(connection)
            # Native P6 users may continue editing during this tiny DDL-only
            # operation. Observe counts without treating their work as ours.
            sql_query(connection,"COMMIT TRANSACTION")
            record={"database":SOURCES[code]["database"],"machine":SOURCES[code]["machine"],
                "tables_removed":len(SOURCES[code]["tables"]),"capture_sha256":receipt["capture_sha256"],
                "schemas_removed":[s["name"] for s in current["schemas"]],
                "protected_catalogue_sha256":protected,"core_counts_before":before,"core_counts_after":after,
                "core_counts_unchanged":before==after,"native_p6_ddl_unchanged":True}
            outcome["sources"][code]=record
            # If this write fails after SQL committed, the next run recovers
            # only the exact absent-scope/protected-catalogue state above.
            private_json(journal,record)
    finally:
        errors=[]
        for connection in connections.values():
            try:
                sql_query(connection,"IF @@TRANCOUNT>0 ROLLBACK TRANSACTION")
            except Exception as error:
                errors.append(error)
            finally:
                connection.close()
        if errors and sys.exc_info()[0] is None:
            raise RuntimeError("A cleanup transaction could not be rolled back cleanly.") from errors[0]
    outcome["completed_at"]=datetime.now(timezone.utc).isoformat()
    private_json(directory/"cleanup-result.json",outcome)
    return outcome

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation",choices=("capture","rehearse","apply"))
    parser.add_argument("--directory",type=directory_path,required=True)
    args=parser.parse_args()
    os.umask(0o077)
    setup()
    from reporting_mirror.connections import postgres_connection
    runtime_gate()
    with postgres_connection(autocommit=True) as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(%s) n",(LOCK_ID,)).fetchone()["n"]:
            raise RuntimeError("A PostgreSQL reporting operation is running.")
        try:
            runtime_gate()
            result={"capture":capture,"rehearse":rehearse,"apply":apply}[args.operation](args.directory)
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)",(LOCK_ID,))
    # Never print captured table values, authentication material or definitions.
    print(json.dumps({"operation":args.operation,"status":result["status"],"directory":str(args.directory)},sort_keys=True))


if __name__=="__main__":
    main()
