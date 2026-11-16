#!/usr/bin/env python3
"""Plan or retire the superseded P6 22 SQL reporting outputs.

Run inside the production Django container, after taking a verified recovery
backup. The default operation is read-only. No dbo object, settings table,
refresh history, or rollback archive is in the deletion allowlist.
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
import tarfile

DATABASE = "P62212_1"
MACHINE = "xps4-p62212"
SCHEMA = "powerbitables"
SQL_LOCK = "powerbitables.xertoolkit.schedule_quality.refresh"
RESULT_TABLES = (
    "xertoolkit_result_schedule_quality_task_evidence",
    "xertoolkit_result_out_of_sequence_exceptions",
    "xertoolkit_result_logic_loop_tasks",
    "xertoolkit_result_project_metrics",
)
VIEWS = tuple("xertoolkit_vw_" + suffix for suffix in (
    "PBI_Activities", "PBI_ActivityQuality", "PBI_ActivityQualityValidation",
    "PBI_InformationalResults", "PBI_LogicLoops", "PBI_OpenEnds",
    "PBI_OutOfSequence", "PBI_OutOfSequenceExceptions", "PBI_ProjectMetrics",
    "PBI_Projects", "PBI_RelationshipDetails", "PBI_RelationshipQuality",
    "PBI_Relationships", "PBI_ScheduleQualityResults",
    "PBI_ScheduleQualityTaskEvidence", "PBI_TaskDetails", "PBI_WBSDetails",
    "schedule_quality_constraint_types", "schedule_quality_options",
    "schedule_quality_settings",
))
FUNCTIONS = tuple("xertoolkit_fn_" + suffix for suffix in (
    "activity_in_scope", "activity_quality", "open_ends", "out_of_sequence",
    "relationship_in_scope", "relationship_quality",
    "schedule_quality_task_evidence", "soft_constraints",
))
PROCEDURES = tuple("xertoolkit_" + suffix for suffix in (
    "get_or_create_schedule_quality_draft", "publish_schedule_quality_config",
    "refresh_all_schedule_quality", "save_schedule_quality_draft",
))
TRIGGERS = tuple("xertoolkit_trg_project_metrics_" + suffix for suffix in (
    "oos_delete", "oos_insert", "task_evidence_delete", "task_evidence_insert",
))
ALLOWLIST = {**dict.fromkeys(VIEWS, "V"), **dict.fromkeys(FUNCTIONS, "IF"),
             **dict.fromkeys(PROCEDURES, "P"), **dict.fromkeys(TRIGGERS, "TR"),
             **dict.fromkeys(RESULT_TABLES, "U")}
RELEVANT_TASKS = {
    "reporting_mirror.tasks.refresh_reporting",
    "reporting_mirror.tasks.refresh_sql_parallel",
    "reporting_mirror.tasks.validate_native_engine",
    "reporting_mirror.tasks.switch_reporting_engine",
    "backups.tasks.execute_schedule_quality_refresh",
    "backups.tasks.publish_schedule_quality_config_task",
}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def file_digest(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def validate_scope(objects):
    actual = {row["name"]: row["type"].strip() for row in objects}
    if actual != ALLOWLIST:
        missing = sorted(set(ALLOWLIST) - set(actual))
        extra = sorted(set(actual) - set(ALLOWLIST))
        changed = sorted(name for name in set(actual) & set(ALLOWLIST)
                         if actual[name] != ALLOWLIST[name])
        raise RuntimeError(f"Retirement catalogue changed: missing={missing}, extra={extra}, changed={changed}")
    if any(row.get("is_schema_bound") for row in objects):
        raise RuntimeError("A retirement object is schema bound; review its dependencies first.")


def dependency_is_internal(dependency, object_ids, result_table_ids):
    """A table's own constraints disappear with that reviewed table.

    Schema membership alone never grants permission. A constraint on a retained
    table remains an external dependency even when it lives in powerbitables.
    Unreviewed triggers remain blocked by the exact executable allowlist.
    """
    return dependency["referencing_id"] in object_ids or (
        dependency.get("referencing_type", "").strip() in {"C", "D", "F", "PK", "UQ"}
        and dependency.get("referencing_parent_object_id") in result_table_ids
    )


def verify_backup_receipt(receipt, *, require_legacy_rehearsal=True):
    source = receipt.get("sql_source", {})
    if receipt.get("status") != "verified" or source.get("database") != DATABASE or source.get("machine") != MACHINE:
        raise RuntimeError("A verified recovery receipt for this SQL source is required.")
    if source.get("restore_verified") is not True:
        raise RuntimeError("The recovery receipt does not confirm SQL restore verification.")
    path = Path(source.get("backup_path", ""))
    checksum = source.get("sha256", "")
    if not path.is_absolute() or not path.is_file() or len(checksum) != 64:
        raise RuntimeError("The SQL recovery backup must be an accessible absolute file with a SHA-256 checksum.")
    if file_digest(path).lower() != checksum.lower():
        raise RuntimeError("The SQL recovery backup checksum did not match the receipt.")
    verify_checkpoint_proof(receipt, path.resolve(strict=True), checksum.lower(),
                            require_legacy_rehearsal=require_legacy_rehearsal)


def checkpoint_file(directory, relative):
    """Read only ordinary artifacts inside this exact private recovery set."""
    relative = Path(relative)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("Unsafe recovery checkpoint artifact path.")
    path = directory / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("A required recovery checkpoint artifact is missing.") from error
    if path.is_symlink() or not resolved.is_relative_to(directory) or not resolved.is_file():
        raise RuntimeError("A recovery checkpoint artifact is unsafe or unreadable.")
    return resolved


def checkpoint_json(directory, relative):
    try:
        payload = json.loads(checkpoint_file(directory, relative).read_text())
    except (OSError, ValueError) as error:
        raise RuntimeError("A required recovery checkpoint proof is unreadable or invalid.") from error
    if not isinstance(payload, dict):
        raise RuntimeError("A recovery checkpoint proof is not a JSON object.")
    return payload


def retirement_scope_catalogue(catalogue):
    return {row["name"]: row["type"].strip() for row in catalogue
            if row["schema_name"] == SCHEMA and
            (row["type"].strip() in {"V", "P", "IF", "FN", "TF", "TR"} or
             (row["type"].strip() == "U" and row["name"].startswith("xertoolkit_result_")))}


def captured_retirement_marker(directory):
    """Prove already-retired state from captured deployment data, never live state."""
    with tarfile.open(checkpoint_file(directory, "deployment-config.tar.gz"), "r:gz") as archive:
        members = [member for member in archive.getmembers()
                   if member.name == "state/sql-reporting-retired.json"]
        if len(members) != 1 or not members[0].isfile() or members[0].size > 1024 * 1024:
            raise RuntimeError("An already-retired SQL backup needs one ordinary captured retirement marker.")
        raw_marker = archive.extractfile(members[0]).read()
    try:
        marker = json.loads(raw_marker)
    except (UnicodeError, ValueError) as error:
        raise RuntimeError("The captured SQL retirement marker is invalid.") from error
    target = marker.get("target", {}) if isinstance(marker, dict) else {}
    if not isinstance(marker, dict) or marker.get("status") != "retired" or not isinstance(target, dict) or target.get("database") != DATABASE or target.get("machine") != MACHINE:
        raise RuntimeError("The captured SQL retirement marker does not match this source.")
    state = checkpoint_json(directory, "reporting-state.json")
    config = state.get("config")
    if not isinstance(config, list) or len(config) != 1 or not isinstance(config[0], dict) or config[0].get("sql_parallel_enabled") is not False:
        raise RuntimeError("The captured reporting configuration must disable parallel SQL reporting.")
    if config[0].get("calculation_engine") != "postgres" or config[0].get("manage_settings_in_postgres") is not True or config[0].get("serve_reports") is not True or config[0].get("pending_engine"):
        raise RuntimeError("The captured reporting configuration must serve PostgreSQL reports and rules.")
    return {"status": "already_retired", "removal_scope_objects": 0,
            "captured_retirement_marker_sha256": hashlib.sha256(raw_marker).hexdigest(),
            "captured_parallel_sql_enabled": False}


def verify_checkpoint_proof(receipt, backup_path, backup_sha256, *, require_legacy_rehearsal=True):
    raw_directory = Path(receipt.get("recovery_directory", ""))
    if not raw_directory.is_absolute():
        raise RuntimeError("The full recovery checkpoint directory must be absolute and readable.")
    try:
        directory = raw_directory.resolve(strict=True)
    except OSError as error:
        raise RuntimeError("The full recovery checkpoint directory is missing.") from error
    recovery_id = directory.name
    if directory.parent.name != ".recovery" or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{5,95}", recovery_id):
        raise RuntimeError("The full recovery checkpoint identity or directory is invalid.")
    manifest = checkpoint_json(directory, "manifest.json")
    manifest_sha256 = file_digest(directory / "manifest.json")
    if manifest.get("recovery_id") != recovery_id:
        raise RuntimeError("The capture manifest belongs to a different recovery checkpoint.")
    entries = manifest.get("files")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("The recovery capture manifest is empty or invalid.")
    captured = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str) or entry["path"] in captured:
            raise RuntimeError("The recovery capture manifest has an invalid or duplicate member.")
        name = entry["path"]
        checksum = entry.get("sha256", "")
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", checksum):
            raise RuntimeError("The recovery capture manifest has an invalid checksum.")
        artifact = checkpoint_file(directory, name)
        if artifact.stat().st_size != entry.get("size_bytes") or file_digest(artifact).lower() != checksum.lower():
            raise RuntimeError("A recovery checkpoint artifact differs from its capture manifest.")
        captured[name] = entry
    required = {"application/capture.json", "application/django.sqlite3", "application-source.tar.gz",
                "deployment-config.tar.gz", "p6_reporting.dump", "postgres-globals.sql",
                "postgres-schema.sql", "docker-images.tar"}
    if not required <= set(captured):
        raise RuntimeError("The recovery checkpoint is missing required application/database/runtime artifacts.")
    encryption = checkpoint_json(directory, "encryption_manifest.json")
    offsite = checkpoint_json(directory, "offsite_receipt.json")
    if encryption.get("recovery_id") != recovery_id or encryption.get("capture_manifest_sha256") != manifest_sha256:
        raise RuntimeError("The encryption manifest does not identify this captured checkpoint.")
    if offsite.get("recovery_id") != recovery_id or offsite.get("upload_download_decrypt_sha256") != "passed":
        raise RuntimeError("The complete offsite upload/download/decryption proof has not passed.")
    for prefix, name in (("archive", "recovery.tar.gz"), ("encrypted", "recovery.tar.gz.enc")):
        value = encryption.get(prefix + "_sha256", "")
        if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value) or offsite.get(prefix + "_sha256") != value:
            raise RuntimeError("The offsite receipt and encryption manifest identify different recovery archives.")
        artifact = checkpoint_file(directory, name)
        if artifact.stat().st_size != encryption.get(prefix + "_bytes") or file_digest(artifact) != value:
            raise RuntimeError("A recovery archive differs from the verified offsite archive.")
    if not offsite.get("archive_item_id") or not offsite.get("metadata_item_id"):
        raise RuntimeError("The offsite receipt does not identify both uploaded recovery artifacts.")
    restore = checkpoint_json(directory, "restore-verification.json")
    if restore.get("status") != "verified" or restore.get("recovery_id") != recovery_id or restore.get("manifest_sha256") != manifest_sha256:
        raise RuntimeError("The isolated restore proof does not match the recovery checkpoint.")
    pg = restore.get("postgres", {})
    if not isinstance(pg, dict) or pg.get("status") != "passed" or pg.get("globals_restored") is not True or pg.get("pg_restore_exit_on_error") != "passed":
        raise RuntimeError("The isolated PostgreSQL database/roles restore proof has not passed.")
    if restore.get("sqlite_integrity") != "ok" or pg.get("sqlite_integrity") != "ok":
        raise RuntimeError("The recovered SQLite application integrity proof has not passed.")
    if not isinstance(pg.get("manifest_row_counts_checked"), int) or pg["manifest_row_counts_checked"] <= 0 or not isinstance(pg.get("consumer_views_planned"), int) or pg["consumer_views_planned"] <= 0:
        raise RuntimeError("The recovered PostgreSQL publication and consumer views were not verified.")
    capture = checkpoint_json(directory, "application/capture.json")
    if capture.get("sqlite", {}).get("integrity_check") != "ok":
        raise RuntimeError("The captured SQLite snapshot has no successful integrity proof.")
    sql_capture = capture.get("sql_backups")
    sql_proof = restore.get("sql", {})
    records = sql_proof.get("databases") if isinstance(sql_proof, dict) else None
    if not isinstance(sql_capture, list) or not isinstance(records, list) or sql_proof.get("status") != "passed":
        raise RuntimeError("The isolated SQL restore proof is absent or incomplete.")
    expected_slugs = {"axialp6", "axial-training", "p62212"}
    if len(sql_capture) != 3 or {row.get("target_slug") for row in sql_capture} != expected_slugs or len(records) != 3 or {row.get("target_slug") for row in records} != expected_slugs:
        raise RuntimeError("The checkpoint must cover all three captured/restored SQL targets exactly once.")
    proved = {row["target_slug"]: row for row in records}
    for row in sql_capture:
        name = row.get("path")
        entry = captured.get(name)
        proof = proved[row["target_slug"]]
        if not entry or entry["sha256"] != row.get("sha256") or proof.get("backup_sha256") != row.get("sha256") or proof.get("database") != row.get("database"):
            raise RuntimeError("The SQL restore proof does not match a captured SQL backup artifact.")
        if row.get("copy_only") is not True or row.get("checksum") is not True or row.get("restore_verifyonly") != "passed" or proof.get("restore_with_checksum") != "passed" or proof.get("dbcc_checkdb_physical_only") != "passed" or proof.get("object_catalogue_and_module_hashes") != "matched":
            raise RuntimeError("A captured SQL target lacks complete checksum/restore/integrity proof.")
        expected_counts = row.get("row_counts_after", {})
        if proof.get("counts") != {"projects": expected_counts.get("project_count"), "activities": expected_counts.get("task_count"), "relationships": expected_counts.get("taskpred_count")}:
            raise RuntimeError("The recovered SQL target counts differ from captured source counts.")
        if row["target_slug"] == "p62212":
            machine = row.get("source_machine", {})
            if row.get("database") != DATABASE or machine.get("database_name") != DATABASE or machine.get("machine_name") != MACHINE:
                raise RuntimeError("The captured SQL retirement source machine/database does not match.")
            if checkpoint_file(directory, name) != backup_path or row.get("sha256", "").lower() != backup_sha256:
                raise RuntimeError("The retirement SQL backup is not the exact captured P6 22 artifact.")
            rehearsal = proof.get("retirement_rehearsal", {})
            if not isinstance(rehearsal, dict):
                raise RuntimeError("The isolated SQL retirement state proof is invalid.")
            if not require_legacy_rehearsal and rehearsal.get("status") == "already_retired":
                if "reporting-state.json" not in captured or not isinstance(row.get("object_catalogue"), list) or retirement_scope_catalogue(row["object_catalogue"]):
                    raise RuntimeError("An already-retired SQL proof needs the captured configuration and an empty removal-scope catalogue.")
                expected_state = captured_retirement_marker(directory)
                if rehearsal != expected_state:
                    raise RuntimeError("The already-retired SQL restore proof differs from its captured retirement marker.")
            elif rehearsal.get("status") != "passed" or rehearsal.get("objects_removed") != len(ALLOWLIST) or rehearsal.get("transaction_rolled_back") is not True or rehearsal.get("source_row_counts_unchanged") is not True:
                raise RuntimeError("The isolated SQL retirement rehearsal has not passed.")


def validate_plan(plan, expected_checksum):
    if set(plan) != {"format", "created_at", "catalogue", "catalogue_sha256"} or plan["format"] != 1:
        raise RuntimeError("Unsupported retirement plan.")
    calculated = digest(plan["catalogue"])
    if calculated != plan["catalogue_sha256"] or calculated != expected_checksum:
        raise RuntimeError("The approved retirement catalogue checksum did not match.")
    if plan["catalogue"].get("identity") != {"database_name": DATABASE, "machine": MACHINE}:
        raise RuntimeError("The retirement plan belongs to a different SQL source.")
    validate_scope(plan["catalogue"]["objects"])


def runtime_gate():
    from reporting_mirror.models import ReportingConfig, ReportingRun, SQLParallelRun, NativeEngineValidation
    from reporting_mirror.native_pipeline import verified_native_engine
    from django_celery_beat.models import PeriodicTask
    config = ReportingConfig.current()
    if not (config.enabled and config.serve_reports and config.manage_settings_in_postgres
            and config.calculation_engine == "postgres" and not config.pending_engine):
        raise RuntimeError("Both reporting and settings must be served by the active PostgreSQL engine.")
    if config.sql_parallel_enabled:
        raise RuntimeError("Disable parallel SQL reporting before retirement.")
    if PeriodicTask.objects.filter(task="backups.tasks.execute_schedule_quality_refresh", enabled=True).exists():
        raise RuntimeError("Disable the duplicate legacy schedule-quality periodic task first.")
    if ReportingRun.objects.filter(status="running").exists() or SQLParallelRun.objects.filter(status__in=["queued", "running"]).exists() or NativeEngineValidation.objects.filter(status__in=["queued", "running"]).exists():
        raise RuntimeError("A reporting operation is queued or running.")
    if not verified_native_engine():
        raise RuntimeError("The active PostgreSQL engine and settings do not have a successful current validation.")


def celery_gate():
    from p6_backup_admin.celery import app
    inspector = app.control.inspect(timeout=5)
    responses = [inspector.active(), inspector.reserved(), inspector.scheduled()]
    if any(response is None for response in responses):
        raise RuntimeError("Worker activity could not be inspected; retirement was refused.")
    for response in responses:
        for worker, tasks in response.items():
            for task in tasks:
                request = task.get("request", task)
                if request.get("name") in RELEVANT_TASKS:
                    raise RuntimeError(f"Reporting task {request.get('name')} is active or queued on {worker}.")
    with app.connection_for_read() as broker:
        channel = broker.channel()
        try:
            # Redis may remove the backing list once its final task is consumed;
            # passive declaration then reports 404 for a healthy empty queue.
            # Declare the known durable Celery queue normally. Kombu's Redis
            # transport counts all priority lists when reporting message_count.
            queued = channel.queue_declare(queue="p6_reporting", passive=False, durable=True).message_count
            if queued:
                raise RuntimeError("The reporting broker queue is not empty.")
        finally:
            channel.close()


def catalogue(connection):
    from reporting_mirror.sql_staging import query
    from backups.services.mssql import quote_identifier as qi
    identity = query(connection, "SELECT DB_NAME() database_name,CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) machine")[0]
    if identity != {"database_name": DATABASE, "machine": MACHINE}:
        raise RuntimeError("SQL source identity did not match; retirement was refused.")
    rows = query(connection, """SELECT o.object_id,o.name,RTRIM(o.type) type,
        CONVERT(nvarchar(33),o.create_date,126) created_at,CONVERT(nvarchar(33),o.modify_date,126) modified_at,
        OBJECTPROPERTY(o.object_id,'IsSchemaBound') is_schema_bound,OBJECT_DEFINITION(o.object_id) definition
        FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
        WHERE s.name='powerbitables' AND (o.type IN('V','P','IF','FN','TF','TR')
        OR (o.type='U' AND o.name LIKE 'xertoolkit[_]result[_]%')) ORDER BY o.name""")
    validate_scope(rows)
    object_ids = {row["object_id"] for row in rows}
    result_table_ids = {row["object_id"] for row in rows if row["name"] in RESULT_TABLES and row["type"] == "U"}
    for row in rows:
        definition = row.pop("definition")
        if row["type"] != "U" and definition is None:
            raise RuntimeError("A reporting module definition could not be inspected.")
        row["definition_sha256"] = hashlib.sha256((definition or "").encode()).hexdigest()
    dependencies = query(connection, """SELECT d.referencing_id,OBJECT_SCHEMA_NAME(d.referencing_id) referencing_schema,
        OBJECT_NAME(d.referencing_id) referencing_name,d.referenced_id,d.referenced_schema_name,d.referenced_entity_name
        ,r.parent_object_id referencing_parent_object_id,RTRIM(r.type) referencing_type
        FROM sys.sql_expression_dependencies d LEFT JOIN sys.objects r ON r.object_id=d.referencing_id
        WHERE d.referenced_schema_name='powerbitables'
        OR d.referenced_id IN(SELECT o.object_id FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name='powerbitables')""")
    external = [d for d in dependencies if (d["referenced_id"] in object_ids or d["referenced_entity_name"] in ALLOWLIST)
                and not dependency_is_internal(d, object_ids, result_table_ids)]
    foreign_keys = query(connection, """SELECT name,parent_object_id,referenced_object_id
        FROM sys.foreign_keys WHERE referenced_object_id IN(SELECT o.object_id FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name='powerbitables' AND o.name LIKE 'xertoolkit[_]result[_]%')""")
    external.extend(fk for fk in foreign_keys if fk["parent_object_id"] not in object_ids)
    synonyms = query(connection, "SELECT name,base_object_name FROM sys.synonyms WHERE base_object_name LIKE '%powerbitables%'")
    if synonyms:
        external.extend(synonyms)
    # Dependencies are recorded in the referencing database, so inspect other
    # online user databases too. Inaccessible metadata fails the whole plan.
    for row in query(connection, "SELECT name FROM sys.databases WHERE state=0 AND database_id>4 AND name<>DB_NAME() ORDER BY name"):
        other = qi(row["name"])
        found = query(connection, f"""SELECT d.referenced_entity_name,OBJECT_NAME(d.referencing_id,DB_ID(?)) referencing_name
            FROM {other}.sys.sql_expression_dependencies d WHERE d.referenced_database_name=?
            AND d.referenced_schema_name='powerbitables'""", (row["name"], DATABASE))
        external.extend(dict(d, database=row["name"]) for d in found if d["referenced_entity_name"] in ALLOWLIST)
    if external:
        raise RuntimeError("Objects outside the retirement scope depend on it: " + json.dumps(external, default=str))
    tables = query(connection, """SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
        WHERE s.name='powerbitables' ORDER BY t.name""")
    counts = {}
    for row in tables:
        counts[row["name"]] = query(connection, "SELECT COUNT_BIG(*) n FROM powerbitables." + qi(row["name"]))[0]["n"]
    columns = query(connection, """SELECT t.name table_name,c.name column_name,c.column_id,c.system_type_id,c.max_length,c.precision,c.scale,c.is_nullable,c.is_identity
        FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.columns c ON c.object_id=t.object_id
        WHERE s.name='powerbitables' ORDER BY t.name,c.column_id""")
    dbo_objects = query(connection, """SELECT o.object_id,o.name,RTRIM(o.type) type FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name='dbo' AND o.is_ms_shipped=0 ORDER BY o.object_id""")
    return {"identity": identity, "objects": rows, "table_rows": counts, "columns": columns,
            "protected_dbo_catalogue_sha256": digest(dbo_objects),
            "external_dependencies": [], "scope": "20 views, 8 functions, 4 procedures, 4 triggers and 4 result tables only"}


def apply_plan(connection, approved):
    from reporting_mirror.sql_staging import query
    from backups.services.mssql import quote_identifier as qi
    current = catalogue(connection)
    if digest(current) != approved["catalogue_sha256"]:
        raise RuntimeError("The live SQL catalogue changed after plan review; create a fresh plan.")
    for names, command in ((TRIGGERS, "TRIGGER"), (VIEWS, "VIEW"),
                           (PROCEDURES, "PROCEDURE"), (FUNCTIONS, "FUNCTION"),
                           (RESULT_TABLES, "TABLE")):
        for name in names:
            query(connection, f"DROP {command} {qi(SCHEMA)}.{qi(name)}")
    remaining = query(connection, """SELECT o.name FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id
        WHERE s.name='powerbitables' AND (o.type IN('V','P','IF','FN','TF','TR')
        OR (o.type='U' AND o.name LIKE 'xertoolkit[_]result[_]%'))""")
    if remaining:
        raise RuntimeError("Retirement left an executable reporting object or result table behind.")
    protected = query(connection, """SELECT o.object_id,o.name,RTRIM(o.type) type FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id=o.schema_id WHERE s.name='dbo' AND o.is_ms_shipped=0 ORDER BY o.object_id""")
    if digest(protected) != current["protected_dbo_catalogue_sha256"]:
        raise RuntimeError("The protected dbo catalogue changed during retirement.")
    retained = {name: count for name, count in current["table_rows"].items() if name not in RESULT_TABLES}
    after = {row["name"]: query(connection, "SELECT COUNT_BIG(*) n FROM powerbitables." + qi(row["name"]))[0]["n"]
             for row in query(connection, "SELECT t.name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id WHERE s.name='powerbitables'")}
    if after != retained:
        raise RuntimeError("Retained SQL settings, history or backup table counts changed.")
    return {"removed": [{"name": name, "type": kind} for name, kind in sorted(ALLOWLIST.items())], "retained_table_rows": retained,
            "protected_dbo_catalogue_sha256": current["protected_dbo_catalogue_sha256"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-out", type=Path, help="Write a read-only retirement plan")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--result-out", type=Path)
    parser.add_argument("--confirm-postgres-consumers", action="store_true", help="Operator confirms reporting consumers use PostgreSQL; dynamic/external client dependencies cannot be proved from SQL metadata")
    args = parser.parse_args(argv)
    if args.apply:
        if not all((args.plan, args.approve_plan_sha256, args.backup_receipt, args.result_out, args.confirm_postgres_consumers)) or args.plan_out:
            parser.error("--apply requires --plan, --approve-plan-sha256, --backup-receipt, --result-out and --confirm-postgres-consumers")
        if args.result_out.exists():
            parser.error("Result output already exists; use a new receipt path")
        approved = json.loads(args.plan.read_text())
        validate_plan(approved, args.approve_plan_sha256)
        backup_receipt = json.loads(args.backup_receipt.read_text())
        verify_backup_receipt(backup_receipt)
    elif not args.plan_out or any((args.plan, args.backup_receipt, args.result_out, args.approve_plan_sha256)):
        parser.error("Default read-only mode requires only --plan-out")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "p6_backup_admin.settings")
    import django
    django.setup()
    from reporting_mirror.connections import postgres_connection
    from reporting_mirror.models import ReportingSource
    from reporting_mirror.sql_staging import query
    from reporting_mirror.sync import LOCK_ID
    from backups.services.mssql import connect
    source = ReportingSource.objects.select_related("target").get(code="p6_22", enabled=True)
    if source.target.sql_database != DATABASE:
        raise RuntimeError("P6 22 source does not match the explicit retirement database.")
    if not args.apply:
        with connect(source.target, database=DATABASE) as connection:
            connection.timeout = 120
            data = catalogue(connection)
        plan = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "catalogue": data, "catalogue_sha256": digest(data)}
        with args.plan_out.open("x") as stream:
            json.dump(plan, stream, indent=2, default=str)
        print(json.dumps({"status": "planned", "objects": len(ALLOWLIST), "plan": str(args.plan_out), "catalogue_sha256": plan["catalogue_sha256"]}))
        return
    with postgres_connection(autocommit=True) as lock:
        if not lock.execute("SELECT pg_try_advisory_lock(%s) acquired", (LOCK_ID,)).fetchone()["acquired"]:
            raise RuntimeError("Another reporting operation holds the publication lock.")
        try:
            runtime_gate()
            celery_gate()
            with connect(source.target, database=DATABASE, autocommit=False) as connection:
                connection.timeout = 120
                try:
                    query(connection, "SET XACT_ABORT ON; IF @@TRANCOUNT=0 BEGIN TRANSACTION")
                    result = query(connection, "DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=?,@LockMode='Exclusive',@LockOwner='Transaction',@LockTimeout=0; SELECT @r result", (SQL_LOCK,))[0]["result"]
                    if result < 0:
                        raise RuntimeError("Another SQL reporting operation holds the refresh lock.")
                    runtime_gate()
                    outcome = apply_plan(connection, approved)
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            receipt = {"status": "retired", "target": {"database": DATABASE, "machine": MACHINE},
                       "completed_at": datetime.now(timezone.utc).isoformat(),
                       "catalogue_sha256": approved["catalogue_sha256"],
                       "recovery_directory": backup_receipt.get("recovery_directory", ""),
                       "backup_receipt": str(args.backup_receipt),
                       "backup_receipt_sha256": file_digest(args.backup_receipt), **outcome}
            with args.result_out.open("x") as stream:
                json.dump(receipt, stream, indent=2, default=str)
            print(json.dumps({"status": "retired", "objects_removed": len(outcome["removed"]), "result": str(args.result_out)}))
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))


if __name__ == "__main__":
    main()
