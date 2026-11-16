"""Publish validated, source-qualified reporting snapshots as one PostgreSQL batch."""
import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal

from django.utils import timezone
from psycopg import sql

from backups.services.mssql import connect, quote_identifier as qi
from reporting_mirror.connections import postgres_connection, staging_target
from reporting_mirror.models import ReportingConfig, ReportingRun, ReportingSource
from reporting_mirror.latest_submission import (
    prepare_latest_submissions, publication_predicate, scope_manifest_entry,
    scopes_to_latest_submission,
)
from reporting_mirror.sql_staging import (
    restore_snapshot, install_reporting_definitions, rebuild_quality,
    reporting_objects, object_columns, safe_stage_name, assert_staging,
)

LOCK_ID = 620252210
logger = logging.getLogger(__name__)
METADATA_COLUMNS = {"_source_system", "_source_database", "_snapshot_at", "_synced_at", "_source_updated_at", "_batch_id", "_project_key", "_activity_key"}


def pg_column(name):
    return name if " " in name else name.lower()


def object_kind(sql_type):
    return {"V": "view", "U": "table"}[sql_type.strip()]


def ui_identifier_offset(source):
    return {"p6_22": 4294967296, "p6_23": 8589934592}[source]


def pg_type(column):
    kind = column["type_name"].lower()
    if kind in {"varchar", "nvarchar", "nchar", "char", "text", "ntext", "xml"}:
        return "text"
    if kind in {"int", "bigint", "smallint"}:
        return {"int": "integer", "bigint": "bigint", "smallint": "smallint"}[kind]
    if kind in {"tinyint", "bit"}:
        return "smallint"
    if kind in {"numeric", "decimal"}:
        return f"numeric({int(column['precision'])},{int(column['scale'])})"
    if kind in {"money", "smallmoney"}:
        return "numeric(19,4)"
    if kind in {"float", "real"}:
        return "double precision" if kind == "float" else "real"
    if kind in {"datetime", "datetime2", "smalldatetime"}:
        return "timestamp without time zone"
    if kind == "date":
        return "date"
    if kind == "time":
        return "time without time zone"
    if kind == "datetimeoffset":
        return "timestamp with time zone"
    if kind in {"binary", "varbinary", "image", "timestamp", "rowversion"}:
        return "bytea"
    if kind == "uniqueidentifier":
        return "uuid"
    raise RuntimeError(f"No lossless reporting type mapping for {kind}.")


def normalise(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return str(value.normalize()) if value else "0"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    return value


class ContentHash:
    """Order independent row digest; counts and selected relationships are checked separately."""
    def __init__(self):
        self.total = 0
        self.count = 0

    def add(self, row):
        encoded = json.dumps([normalise(v) for v in row], ensure_ascii=False, separators=(",", ":"), default=str).encode()
        self.total = (self.total + int.from_bytes(hashlib.sha256(encoded).digest(), "big")) % (1 << 256)
        self.count += 1

    def value(self):
        return f"{self.total:064x}"


def copy_object(pg, source_connection, schema, obj, source_code):
    name = obj["name"].lower()
    columns = object_columns(source_connection, obj["schema_name"], obj["name"])
    if not columns or any(len(pg_column(c["name"]).encode()) > 63 for c in columns):
        raise RuntimeError(f"Invalid reporting column layout for {name}.")
    names = [pg_column(c["name"]) for c in columns]
    if METADATA_COLUMNS.intersection(names) or len(set(names)) != len(names):
        raise RuntimeError(f"Reporting column name collision in {name}.")
    definition = sql.SQL(",").join(sql.SQL("{} {}").format(sql.Identifier(pg_column(c["name"])), sql.SQL(pg_type(c))) for c in columns)
    pg.execute(sql.SQL("CREATE TABLE {}.{} ({})").format(sql.Identifier(schema), sql.Identifier(name), definition))
    source_cursor = source_connection.cursor()
    digest = ContentHash()
    try:
        source_cursor.execute("SELECT " + ",".join(qi(c["name"]) for c in columns) + " FROM " + qi(obj["schema_name"]) + "." + qi(obj["name"]))
        with pg.cursor().copy(sql.SQL("COPY {}.{} ({}) FROM STDIN").format(sql.Identifier(schema), sql.Identifier(name), sql.SQL(",").join(map(sql.Identifier, names)))) as copy:
            while True:
                rows = source_cursor.fetchmany(2000)
                if not rows:
                    break
                for row in rows:
                    values = tuple(int(v) if isinstance(v, bool) else bytes(v) if isinstance(v, bytearray) else v for v in row)
                    copy.write_row(values)
                    digest.add(values)
    finally:
        source_cursor.close()
    # Verify every copied value from PostgreSQL, not just successful COPY status.
    copied_digest = ContentHash()
    with pg.cursor(name="verify_" + source_code + "_" + name[:40]) as cursor:
        cursor.execute(sql.SQL("SELECT {} FROM {}.{}").format(sql.SQL(",").join(map(sql.Identifier, names)), sql.Identifier(schema), sql.Identifier(name)))
        for row in cursor:
            copied_digest.add(tuple(row.values()))
    if (digest.count, digest.value()) != (copied_digest.count, copied_digest.value()):
        raise RuntimeError(f"Reporting content verification failed: {source_code}.{name}")
    for field in ("proj_id", "task_id", "rsrc_id", "config_version_id"):
        if field in names:
            pg.execute(sql.SQL("CREATE INDEX ON {}.{} ({})").format(sql.Identifier(schema), sql.Identifier(name), sql.Identifier(field)))
    if "proj_id" in names:
        pg.execute(sql.SQL("CREATE INDEX ON {}.{} (({}::bigint+proj_id))").format(sql.Identifier(schema), sql.Identifier(name), sql.Literal(ui_identifier_offset(source_code))))
    pg.execute(sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(name)))
    return {"source": source_code, "name": name, "source_schema": obj["schema_name"], "kind": object_kind(obj["type"]), "rows": digest.count, "sha256_sum": digest.value(), "columns": [{"name": pg_column(c["name"]), "type": pg_type(c)} for c in columns]}


def metadata_select(schema, entry, source, snapshot, published_at, run_id):
    fields = [sql.SQL("t.*")]
    for name, value in (
        ("_source_system", source.code), ("_source_database", source.target.sql_database),
        ("_snapshot_at", datetime.fromisoformat(snapshot["snapshot_at"])),
        ("_synced_at", published_at), ("_batch_id", str(run_id)),
    ):
        fields.append(sql.SQL("{} AS {}").format(sql.Literal(value), sql.Identifier(name)))
    names = {c["name"] for c in entry["columns"]}
    fields.append(sql.SQL("{} AS _source_updated_at").format(sql.Identifier("t", "update_date") if "update_date" in names else sql.SQL("NULL::timestamp")))
    for column, alias in (("proj_id", "_project_key"), ("task_id", "_activity_key")):
        if column in names:
            fields.append(sql.SQL("{} || ':' || {}::text AS {}").format(sql.Literal(source.code), sql.Identifier("t", column), sql.Identifier(alias)))
    query = sql.SQL("SELECT {} FROM {}.{} t").format(sql.SQL(",").join(fields), sql.Identifier(schema), sql.Identifier(entry["name"]))
    if scopes_to_latest_submission(entry):
        query += publication_predicate(schema)
    return query


def publish_combined_ui_views(pg, manifest):
    """Supply collision-free integer keys to the existing Django report forms.

    Public reporting views keep their original P6 identifiers. Only these private
    UI compatibility views use deterministic surrogate IDs; shared settings appear
    once because both source calculations use the same authoritative profile.
    """
    pg.execute("CREATE SCHEMA IF NOT EXISTS reporting_ui")
    names = sorted({e["name"] for e in manifest})
    identifiers = {"proj_id", "pred_proj_id", "task_id", "pred_task_id", "wbs_id", "parent_wbs_id", "rsrc_id"}
    for name in names:
        columns = pg.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema='reporting' AND table_name=%s ORDER BY ordinal_position", (name,)).fetchall()
        fields, originals = [], []
        for column in columns:
            field = column["column_name"]
            if field in identifiers:
                if column["data_type"] not in {"integer", "smallint", "bigint", "numeric"}:
                    raise RuntimeError("Unexpected P6 reporting identifier type.")
                fields.append(sql.SQL("(CASE WHEN v._source_system='p6_22' THEN 1 ELSE 2 END)::bigint*4294967296+v.{} AS {}").format(sql.Identifier(field), sql.Identifier(field)))
                originals.append(sql.SQL("v.{} AS {}").format(sql.Identifier(field), sql.Identifier("_original_" + field)))
            elif field == "proj_short_name" or (name == "rsrc" and field in {"rsrc_name", "rsrc_short_name"}):
                fields.append(sql.SQL("v.{} || CASE WHEN v._source_system='p6_22' THEN ' [P6 22]' ELSE ' [P6 23]' END AS {}").format(sql.Identifier(field), sql.Identifier(field)))
            else:
                fields.append(sql.Identifier("v", field))
        shared = name.startswith("xertoolkit_schedule_quality_") or name.startswith("xertoolkit_vw_schedule_quality_")
        schema = "p6_22" if shared else "reporting"
        pg.execute(sql.SQL("CREATE OR REPLACE VIEW reporting_ui.{} AS SELECT {} FROM {}.{} v").format(sql.Identifier(name), sql.SQL(",").join(fields + originals), sql.Identifier(schema), sql.Identifier(name)))
    pg.execute("GRANT USAGE ON SCHEMA reporting_ui TO p6_reporting_reader")
    pg.execute("GRANT SELECT ON ALL TABLES IN SCHEMA reporting_ui TO p6_reporting_reader")


def ensure_combined_project_indexes(pg, schemas, manifest):
    """Keep source-qualified project filtering indexed after native rebuilds."""
    for entry in manifest:
        if not any(c['name']=='proj_id' for c in entry.get('columns',[])):
            continue
        schema=schemas[entry['source']];table=entry['name'];offset=ui_identifier_offset(entry['source'])
        expressions=pg.execute("""SELECT pg_get_expr(i.indexprs,i.indrelid) expression
          FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=%s AND c.relname=%s AND i.indexprs IS NOT NULL AND i.indpred IS NULL AND i.indisvalid""",(schema,table)).fetchall()
        expected=f'{offset}::bigint+proj_id'
        if any(re.sub(r"[\s'()]",'',row['expression'])==expected for row in expressions):
            continue
        name='ui_proj_'+hashlib.sha256((schema+table).encode()).hexdigest()[:16]
        pg.execute(sql.SQL('CREATE INDEX IF NOT EXISTS {} ON {}.{} (({}::bigint+proj_id))').format(sql.Identifier(name),sql.Identifier(schema),sql.Identifier(table),sql.Literal(offset)))


def publish_batch(pg, run, sources, snapshots, schemas, manifest):
    if run.engine == 'postgres':
        ensure_combined_project_indexes(pg,schemas,manifest)
    published_at = timezone.now()
    pg.execute("SET LOCAL lock_timeout='10s'")
    pg.execute("CREATE SCHEMA IF NOT EXISTS reporting")
    by_name = {}
    for source in sources:
        prepare_latest_submissions(pg, schemas[source.code])
        pg.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(source.code)))
        for entry in manifest:
            if entry["source"] != source.code:
                continue
            scope_manifest_entry(pg, schemas[source.code], entry)
            pg.execute(sql.SQL("CREATE OR REPLACE VIEW {}.{} AS {}").format(sql.Identifier(source.code), sql.Identifier(entry["name"]), metadata_select(schemas[source.code], entry, source, snapshots[source.code], published_at, run.id)))
            by_name.setdefault(entry["name"], []).append((source, entry))
    for name, entries in by_name.items():
        columns = []
        types = {}
        for source, entry in entries:
            for col in entry["columns"]:
                if col["name"] not in columns:
                    columns.append(col["name"])
                    types[col["name"]] = col["type"]
        metadata = ["_source_system", "_source_database", "_snapshot_at", "_synced_at", "_batch_id", "_source_updated_at"]
        if "proj_id" in columns:
            metadata.append("_project_key")
        if "task_id" in columns:
            metadata.append("_activity_key")
        queries = []
        for source, entry in entries:
            present = {c["name"] for c in entry["columns"]}
            fields = [sql.Identifier(c) if c in present else sql.SQL("NULL::{} AS {}").format(sql.SQL(types[c]), sql.Identifier(c)) for c in columns]
            fields.extend(map(sql.Identifier, metadata))
            queries.append(sql.SQL("SELECT {} FROM {}.{}").format(sql.SQL(",").join(fields), sql.Identifier(source.code), sql.Identifier(name)))
        pg.execute(sql.SQL("CREATE OR REPLACE VIEW reporting.{} AS {}").format(sql.Identifier(name), sql.SQL(" UNION ALL ").join(queries)))
    publish_combined_ui_views(pg, manifest)
    pg.execute("CREATE SCHEMA IF NOT EXISTS sync_audit")
    pg.execute("CREATE TABLE IF NOT EXISTS sync_audit.batches (batch_id uuid PRIMARY KEY, published_at timestamptz NOT NULL, sources jsonb NOT NULL, manifest jsonb NOT NULL, validation_status text NOT NULL DEFAULT 'verified')")
    pg.execute("ALTER TABLE sync_audit.batches ADD COLUMN IF NOT EXISTS validation_status text NOT NULL DEFAULT 'verified'")
    from psycopg.types.json import Jsonb
    pg.execute("INSERT INTO sync_audit.batches (batch_id,published_at,sources,manifest) VALUES (%s,%s,%s,%s)", (run.id, published_at, Jsonb(snapshots), Jsonb(manifest)))
    pg.execute("CREATE OR REPLACE VIEW reporting.refresh_status AS SELECT batch_id,published_at,sources FROM sync_audit.batches WHERE validation_status='verified' ORDER BY published_at DESC LIMIT 1")
    for schema in ["reporting", "sync_audit", *[s.code for s in sources]]:
        pg.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO p6_reporting_reader").format(sql.Identifier(schema)))
        pg.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO p6_reporting_reader").format(sql.Identifier(schema)))
    pg.commit()
    return published_at


def prune_generations(pg, keep):
    # PostgreSQL's publication pointer is authoritative after interrupted workers.
    batches = pg.execute("SELECT batch_id FROM sync_audit.batches WHERE validation_status='verified' ORDER BY published_at DESC LIMIT %s", (keep + 1,)).fetchall()
    prefixes = {"load_" + str(r["batch_id"]).replace("-", "")[:16] + "_" for r in batches}
    schemas = pg.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 'load_%'").fetchall()
    for row in schemas:
        name = row["nspname"]
        if re.fullmatch(r"load_[0-9a-f]{16}_p6_(22|23)", name) and not any(name.startswith(p) for p in prefixes):
            try:
                with pg.transaction():
                    pg.execute("SET LOCAL lock_timeout='2s'")
                    pg.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
            except Exception:
                # An old report can hold a snapshot briefly. Retry pruning next run.
                continue
    pg.commit()


def run_sync(*, trigger="scheduled", requested_by="", force=False, engine=None, recalculate_only=False, config_version_id=None, expected_settings_hash=None, switch_engine=False):
    config = ReportingConfig.current()
    selected_engine = engine or config.calculation_engine
    if selected_engine not in {"sql", "postgres"}:
        raise ValueError("Unknown reporting calculation engine.")
    if selected_engine == "sql":
        from reporting_mirror.sql_retirement import require_sql_reporting
        require_sql_reporting()
    if selected_engine == "postgres":
        from reporting_mirror.native_pipeline import run_native_sync
        return run_native_sync(trigger=trigger, requested_by=requested_by, force=force, recalculate_only=recalculate_only, config_version_id=config_version_id, expected_settings_hash=expected_settings_hash, switch_engine=switch_engine)
    if not config.enabled and not force:
        return "Reporting refresh is paused."
    operation = "settings_publish" if config_version_id is not None else "engine_switch" if switch_engine else "data_sync"
    run = ReportingRun.objects.create(trigger=trigger, requested_by=requested_by, engine="sql", operation=operation)
    publication_started = timezone.now().replace(tzinfo=None)
    def progress(message):
        ReportingRun.objects.filter(pk=run.pk).update(progress=message)
    lock_connection = None
    acquired = False
    try:
        lock_connection = postgres_connection(autocommit=True)
        acquired = lock_connection.execute("SELECT pg_try_advisory_lock(%s) acquired", (LOCK_ID,)).fetchone()["acquired"]
        if not acquired:
            run.status = "skipped"
            run.progress = "Another reporting refresh is already running"
            run.finished_at = timezone.now()
            run.save()
            return run.progress
        # Reconcile interrupted processes without marking the PostgreSQL snapshot invalid.
        ReportingRun.objects.filter(status="running").exclude(pk=run.pk).update(status="failed", progress="Refresh worker was interrupted; previous published data retained", finished_at=timezone.now())
        sources = list(ReportingSource.objects.filter(enabled=True).select_related("target"))
        if {s.code for s in sources} != {"p6_22", "p6_23"}:
            raise RuntimeError("Both P6 22 and P6 23 sources must be enabled for a combined publication.")
        snapshots = {}
        for source in sources:
            snapshots[source.code] = restore_snapshot(source, run.id, progress)
            run.sources = snapshots
            run.save(update_fields=["sources"])
        install_reporting_definitions(progress)
        if config.manage_settings_in_postgres:
            from reporting_mirror.native_pipeline import configure_sql_staging
            configure_sql_staging(config_version_id=config_version_id, expected_settings_hash=expected_settings_hash, published_by=requested_by, published_at=publication_started)
        for source in sources:
            quality = rebuild_quality(source, progress)
            snapshots[source.code]["quality"] = {k: v.isoformat() if isinstance(v, datetime) else v for k, v in quality.items()}
            snapshots[source.code]["quality"]["engine"] = "sql"
        run.sources = snapshots
        run.save(update_fields=["sources"])
        manifest = []
        schemas = {}
        with postgres_connection() as pg:
            for source in sources:
                schema = "load_" + run.id.hex[:16] + "_" + source.code
                schemas[source.code] = schema
                pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                with connect(staging_target(), database=safe_stage_name(source.code)) as stage:
                    stage.timeout = 1800
                    assert_staging(stage)
                    for obj in reporting_objects(stage):
                        progress(f"Copying and verifying {source.get_code_display()}: {obj['name']}")
                        manifest.append(copy_object(pg, stage, schema, obj, source.code))
                pg.commit()
                # Check relationships against the same source snapshot, never across versions.
                orphan_count = pg.execute(sql.SQL("SELECT COUNT(*) n FROM {}.task t LEFT JOIN {}.project p USING (proj_id) WHERE p.proj_id IS NULL").format(sql.Identifier(schema), sql.Identifier(schema))).fetchone()["n"]
                snapshots[source.code]["orphan_activity_project_links"] = orphan_count
                actual = {e["name"]: e["rows"] for e in manifest if e["source"] == source.code}
                for table, metric in (("project", "projects"), ("task", "activities"), ("taskpred", "relationships")):
                    if actual.get(table) != snapshots[source.code]["counts"][metric]:
                        raise RuntimeError(f"{source.code} {metric} snapshot counts did not reconcile.")
            progress("Publishing both validated reporting sources together")
            if config_version_id is not None and config.manage_settings_in_postgres:
                from reporting_mirror.native_settings import activate_version
                activate_version(pg, config_version_id, expected_settings_hash, requested_by, published_at=publication_started)
            run.published_at = publish_batch(pg, run, sources, snapshots, schemas, manifest)
            try:
                prune_generations(pg, config.retained_generations)
            except Exception:
                # Publication is already committed. A retention failure must not
                # mislabel this valid snapshot as failed or hide its timestamps.
                logger.exception("Reporting published, but generation cleanup failed")
        run.status = "success"
        run.progress = "Both P6 sources validated and published"
        run.sources = snapshots
        run.manifest = manifest
        run.finished_at = timezone.now()
        run.save()
        if switch_engine:
            ReportingConfig.objects.filter(pk=1).update(calculation_engine="sql")
        if config.manage_settings_in_postgres:
            from reporting_mirror.native_settings import checkpoint
            try:
                checkpoint()
            except Exception:
                logger.exception("Reporting published; portable settings checkpoint needs retry")
        return f"Published reporting batch {run.id} for P6 22 and P6 23."
    except Exception as error:
        run.status = "failed"
        run.progress = "Refresh failed; previous published reporting data retained"
        run.error_message = str(error)[:4000]
        run.finished_at = timezone.now()
        run.save()
        raise
    finally:
        if lock_connection is not None:
            try:
                if acquired:
                    lock_connection.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception:
                logger.exception("Reporting lock connection was interrupted")
            finally:
                lock_connection.close()
