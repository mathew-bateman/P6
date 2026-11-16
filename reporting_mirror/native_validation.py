"""Independent same-snapshot SQL/PostgreSQL validation; never publishes reports."""
import json
from datetime import datetime

from django.utils import timezone
from psycopg import sql

from backups.services.mssql import connect
from reporting_mirror.connections import postgres_connection, staging_target
from reporting_mirror.models import NativeEngineValidation, ReportingSource
from reporting_mirror.native_engine import calculate, CONTRACTS, generation_schema
from reporting_mirror.native_settings import ensure_control, active_version, copy_settings
from reporting_mirror.native_pipeline import engine_revision, configure_sql_staging, catalog_source, required_raw_tables
from reporting_mirror.sql_staging import restore_snapshot, install_reporting_definitions, rebuild_quality, safe_stage_name, assert_staging
from reporting_mirror.sync import LOCK_ID, ContentHash, copy_object
import logging

logger = logging.getLogger(__name__)

RUNTIME_COLUMNS = {"check_run_id", "refreshed_at", "calculated_date"}


def report_contracts(code):
    return {name:columns for name,columns in CONTRACTS['published_by_source'][code].items() if name.startswith('xertoolkit_vw_')}


def canonical(value, column):
    # JSON whitespace/property order are syntax; array and string ordering remain significant.
    if column == "detail_json" and value:
        return json.dumps(json.loads(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return value


def compare_outputs(pg, sources, *, progress=lambda value: None):
    results = []
    for code, data in sources.items():
        expected = report_contracts(code)
        if len(data["manifest"]) != 20 or {e["name"] for e in data["manifest"]} != set(expected):
            raise RuntimeError("The native report catalogue is incomplete.")
        for entry in data["manifest"]:
            name = entry["name"]
            if entry["columns"] != expected[name]:
                raise RuntimeError("The native report column contract changed: " + name)
            columns = [c["name"] for c in entry["columns"] if c["name"] not in RUNTIME_COLUMNS]
            hashes, counts = [], []
            for schema in (data["oracle"], data["schema"]):
                generation_schema(schema)
                actual = pg.execute("""SELECT a.attname name,pg_catalog.format_type(a.atttypid,a.atttypmod) type
                  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
                  WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (schema, name)).fetchall()
                if actual != expected[name]:
                    raise RuntimeError("Reference/native column names or types do not match: " + code + "." + name)
                digest = ContentHash()
                with pg.cursor(name="native_parity_" + str(len(results)) + "_" + str(len(hashes))) as cursor:
                    cursor.itersize = 5000
                    cursor.execute(sql.SQL("SELECT {} FROM {}.{}").format(sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(schema), sql.Identifier(name)))
                    for row in cursor:
                        digest.add(tuple(canonical(row[c], c) for c in columns))
                hashes.append(digest.value()); counts.append(digest.count)
            result = {"source": code, "view": name, "matched": hashes[0] == hashes[1] and counts[0] == counts[1], "rows": counts, "digests": hashes}
            results.append(result)
            progress(result)
    return results


def validate_native_engine(validation_id):
    """Fresh COPY_ONLY snapshots feed both engines; all work stays private."""
    from reporting_mirror.native_fixtures import run_fixture_checks
    record = NativeEngineValidation.objects.get(pk=validation_id)
    if record.status not in {"queued", "running"}:
        return record.status
    record.status = "running"; record.engine_revision = engine_revision(); record.save()
    def progress(message):
        NativeEngineValidation.objects.filter(pk=record.pk).update(progress=str(message)[:250])
    try:
        with postgres_connection(autocommit=True) as lock:
            acquired = lock.execute("SELECT pg_try_advisory_lock(%s) acquired", (LOCK_ID,)).fetchone()["acquired"]
            if not acquired:
                raise RuntimeError("A reporting refresh is active. Retry validation after it finishes.")
            try:
                sources = list(ReportingSource.objects.filter(enabled=True).select_related("target"))
                if {s.code for s in sources} != {"p6_22", "p6_23"}:
                    raise RuntimeError("Both P6 reporting sources are required.")
                with postgres_connection() as pg:
                    ensure_control(pg)
                    selected = active_version(pg)
                    record.settings_hash = selected["settings_hash"].strip().upper()
                    record.baseline_batch = pg.execute("SELECT batch_id FROM reporting.refresh_status").fetchone()["batch_id"]
                    required = required_raw_tables(pg, selected["config_version_id"])
                    fixture_results = run_fixture_checks(pg, record.pk)
                snapshots = {s.code: restore_snapshot(s, record.pk, progress) for s in sources}
                record.source_schemas = {code:{'snapshot':snapshot} for code,snapshot in snapshots.items()}
                record.save()
                install_reporting_definitions(progress)
                configure_sql_staging()
                for source in sources:
                    rebuild_quality(source, progress)
                data = {}
                with postgres_connection() as pg:
                    pg.execute("SET LOCAL statement_timeout='15min'")
                    for source in sources:
                        code = source.code
                        schema = "native_test_" + record.id.hex[:16] + "_" + code
                        oracle = "native_shadow_" + record.id.hex[:16] + "_" + code
                        for name in (schema, oracle):
                            generation_schema(name)
                            pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
                        with connect(staging_target(), database=safe_stage_name(code)) as stage:
                            stage.timeout = 1800; assert_staging(stage)
                            allowed = {t.upper(): t for t in catalog_source(pg, stage, code)}
                            if not required <= set(allowed):
                                raise RuntimeError("An evidence table is unavailable or protected.")
                            for table in sorted(required):
                                progress("Validation raw snapshot: " + code + "." + table)
                                copy_object(pg, stage, schema, {"name": allowed[table], "schema_name": "dbo", "type": "U"}, code)
                            for name in report_contracts(code):
                                progress("Independent SQL reference: " + code + "." + name)
                                copy_object(pg, stage, oracle, {"name": name, "schema_name": "powerbitables", "type": "V"}, code)
                        copy_settings(pg, schema, selected["config_version_id"])
                        manifest, quality = calculate(pg, schema, selected["config_version_id"], 1000001, progress=progress)
                        data[code] = {"schema": schema, "oracle": oracle, "manifest": manifest, "quality": quality, "snapshot": snapshots[code]}
                        pg.commit()
                        record.source_schemas = data; record.save()
                    comparisons = compare_outputs(pg, data, progress=lambda row: progress("Compared " + row["source"] + "." + row["view"]))
                    current = active_version(pg)
                    if current["settings_hash"].strip().upper() != record.settings_hash or engine_revision() != record.engine_revision:
                        raise RuntimeError("Settings or engine code changed during validation. Repeat the comparison.")
                passed = len(comparisons) == 40 and all(row["matched"] for row in comparisons)
                record.results = {"comparisons": comparisons, "comparisons_passed": sum(r["matched"] for r in comparisons), "all_views_matched": passed, "fixture_checks_passed": bool(fixture_results), "fixtures": fixture_results, "runtime_columns_excluded": sorted(RUNTIME_COLUMNS), "JSON_syntax_normalised": True}
                record.save()
                if not passed:
                    raise RuntimeError("One or more native PostgreSQL outputs differed from the independent SQL results.")
                record.status = "success"; record.progress = "40 of 40 complete report comparisons passed, with fixture checks"
            finally:
                lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    except Exception as error:
        record.status = "failed"; record.progress = "Validation failed; published reports and current engine are unchanged"; record.error_message = str(error)[:4000]
        raise
    finally:
        record.finished_at = timezone.now(); record.save()
        # The durable audit contains hashes/counts, not another indefinite copy
        # of all P6 project data. Only this validation's exact private schemas go.
        try:
            with postgres_connection() as pg:
                for code in ('p6_22','p6_23'):
                    for prefix in ('native_test_','native_shadow_'):
                        schema=prefix+record.id.hex[:16]+'_'+code
                        generation_schema(schema)
                        pg.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(schema)))
        except Exception:
            logger.exception('Validation audit saved; private schema cleanup needs retry')
    return record.progress
