"""Data refresh, settings rebuild, validation and switching for native reporting."""
from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path

from django.utils import timezone
from psycopg import sql
from psycopg.types.json import Jsonb

from reporting_mirror.connections import postgres_connection, staging_target
from reporting_mirror.models import ReportingConfig, ReportingRun, ReportingSource, NativeEngineValidation
from reporting_mirror.native_engine import calculate, CONTRACTS, DIRECTORY, index_and_analyse
from reporting_mirror.native_settings import TABLES, active_version, ensure_control, copy_settings, check_draft, activate_version, checkpoint
from reporting_mirror.sql_staging import RAW_TABLES, restore_snapshot, safe_stage_name, assert_staging, query
from backups.services.mssql import connect, quote_identifier

logger = logging.getLogger(__name__)


def engine_revision():
    paths = [Path(__file__).with_name(name) for name in ("native_pipeline.py", "native_engine.py", "native_evidence.py", "native_settings.py", "native_fixtures.py", "native_validation.py")]
    paths.extend(DIRECTORY.glob("*"))
    digest = hashlib.sha256()
    for path in sorted(paths):
        if path.is_file(): digest.update(path.name.encode() + path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def verified_native_engine():
    latest = NativeEngineValidation.objects.filter(status="success", engine_revision=engine_revision()).first()
    if not (latest and latest.results.get("all_views_matched") and latest.results.get("comparisons_passed") == 40 and latest.results.get("fixture_checks_passed")):
        return None
    with postgres_connection(reader=False) as pg:
        current = active_version(pg)
    return latest if current and current["settings_hash"].strip().upper() == latest.settings_hash.strip().upper() else None


def catalog_source(pg, stage, code):
    """Expose P6 field metadata while excluding authentication and secret tables."""
    import re
    from collections import defaultdict
    rows = query(stage, "SELECT t.name table_name,c.name column_name FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id JOIN sys.columns c ON c.object_id=t.object_id WHERE s.name='dbo' ORDER BY t.name,c.column_id")
    catalog = defaultdict(list)
    for row in rows: catalog[row["table_name"]].append(row["column_name"])
    protected = {"users", "userdata", "usession", "usessaud", "userobs", "userpriv", "usettings"}
    safe = {table: columns for table, columns in catalog.items() if table.lower() not in protected and not any(re.search(r"password|passwd|pwd|secret|token|credential", c, re.I) for c in columns)}
    pg.execute("CREATE TABLE IF NOT EXISTS quality_control.source_catalog(source_code text,table_name text,columns jsonb NOT NULL,PRIMARY KEY(source_code,table_name))")
    pg.execute("DELETE FROM quality_control.source_catalog WHERE source_code=%s", (code,))
    for table, columns in safe.items():
        pg.execute("INSERT INTO quality_control.source_catalog VALUES(%s,%s,%s)", (code, table, Jsonb(columns)))
    return safe


def required_raw_tables(pg, config_version_id):
    from reporting_mirror.native_evidence import CATEGORIES
    tables = set(RAW_TABLES)
    for row in pg.execute("SELECT source_category FROM quality_control.xertoolkit_schedule_quality_detail_field WHERE config_version_id=%s", (config_version_id,)).fetchall():
        category = row["source_category"].lower()
        if category in {"activity_code", "udf"}: continue
        tables.add(CATEGORIES.get(category, category.removeprefix("table:")).upper())
    return tables


def clone_raw_snapshot(pg, source, schema, snapshot, manifest, required):
    """Recalculate cached P6 data without contacting either SQL Server instance."""
    from reporting_mirror.sync import copy_object, ContentHash
    raw = [e for e in manifest if e["source"] == source.code and e["source_schema"] == "dbo"]
    if not required <= {e["name"].upper() for e in raw}:
        return None
    copied = []
    for entry in raw:
        names = sql.SQL(",").join(sql.Identifier(c["name"]) for c in entry["columns"])
        pg.execute(sql.SQL("CREATE TABLE {}.{} AS SELECT {} FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(entry["name"]), names, sql.Identifier(source.code), sql.Identifier(entry["name"])))
        index_and_analyse(pg, schema, entry["name"], entry["columns"])
        count = pg.execute(sql.SQL("SELECT COUNT(*) n FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(entry["name"]))).fetchone()["n"]
        if count != entry["rows"]: raise RuntimeError("The cached P6 snapshot changed during recalculation.")
        copied.append(dict(entry))
    return copied


def describe_control(pg, source, schema):
    rows = []
    for name in TABLES:
        columns = pg.execute("""SELECT a.attname name,pg_catalog.format_type(a.atttypid,a.atttypmod) type
          FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (schema, name)).fetchall()
        count = pg.execute(sql.SQL("SELECT COUNT(*) n FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(name))).fetchone()["n"]
        rows.append({"source": source.code, "source_schema": "quality_control", "name": name, "kind": "table", "rows": count, "columns": columns})
    return rows


def run_native_sync(*, trigger="scheduled", requested_by="", force=False, recalculate_only=False, config_version_id=None, expected_settings_hash=None, switch_engine=False):
    from reporting_mirror.sync import LOCK_ID, publish_batch, prune_generations, copy_object
    config = ReportingConfig.current()
    if not config.enabled and not force: return "Reporting refresh is paused."
    if switch_engine and not verified_native_engine(): raise RuntimeError("Validate the current PostgreSQL engine before switching.")
    operation = "settings_publish" if config_version_id is not None else "engine_switch" if switch_engine else "recalculate" if recalculate_only else "data_sync"
    run = ReportingRun.objects.create(engine="postgres", operation=operation, trigger=trigger, requested_by=requested_by)
    publication_started = timezone.now().replace(tzinfo=None)
    def progress(message): ReportingRun.objects.filter(pk=run.pk).update(progress=message[:250])
    lock = None; acquired = False
    try:
        lock = postgres_connection(autocommit=True)
        acquired = lock.execute("SELECT pg_try_advisory_lock(%s) acquired", (LOCK_ID,)).fetchone()["acquired"]
        if not acquired:
            run.status="skipped";run.progress="Another reporting operation is already running";run.finished_at=timezone.now();run.save()
            return run.progress
        ReportingRun.objects.filter(status="running").exclude(pk=run.pk).update(status="failed",finished_at=timezone.now(),progress="Worker interrupted; previous published data retained")
        sources = list(ReportingSource.objects.filter(enabled=True).select_related("target"))
        if {s.code for s in sources}!={"p6_22","p6_23"}: raise RuntimeError("Both P6 sources must be enabled.")
        schemas={s.code:"load_"+run.id.hex[:16]+"_"+s.code for s in sources}
        snapshots={};manifest=[]
        with postgres_connection() as pg:
            pg.execute("SET LOCAL statement_timeout='15min'")
            ensure_control(pg)
            # Bootstrap has its own short transaction. Do not hold its advisory
            # settings lock throughout a multi-minute data capture and rebuild.
            pg.commit()
            version = check_draft(pg, config_version_id, expected_settings_hash) if config_version_id is not None else active_version(pg)
            selected_id = version["config_version_id"]
            required = required_raw_tables(pg, selected_id)
            previous = pg.execute("SELECT * FROM reporting.refresh_status").fetchone()
            prior_manifest = pg.execute("SELECT manifest FROM sync_audit.batches WHERE batch_id=%s", (previous["batch_id"],)).fetchone()["manifest"]
            # A newly configured evidence table needs a fresh raw-data capture.
            can_recalculate = recalculate_only and all(required <= {e['name'].upper() for e in prior_manifest if e['source']==s.code and e['source_schema']=='dbo'} for s in sources)
            if can_recalculate:
                for source in sources:
                    progress("Copying the cached "+source.get_code_display()+" snapshot for PostgreSQL recalculation")
                    pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schemas[source.code])))
                    manifest.extend(clone_raw_snapshot(pg,source,schemas[source.code],previous['sources'][source.code],prior_manifest,required))
                    snapshots[source.code]=dict(previous['sources'][source.code])
            else:
                for source in sources: snapshots[source.code]=restore_snapshot(source,run.id,progress)
                for source in sources:
                    schema=schemas[source.code]
                    pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                    with connect(staging_target(),database=safe_stage_name(source.code)) as stage:
                        stage.timeout=1800;assert_staging(stage)
                        allowed=catalog_source(pg,stage,source.code)
                        by_upper={table.upper():table for table in allowed}
                        if not required <= set(by_upper): raise RuntimeError("A requested P6 evidence table is unavailable or protected.")
                        for table in sorted(required):
                            progress("Copying raw "+source.get_code_display()+": "+table)
                            manifest.append(copy_object(pg,stage,schema,{'name':by_upper[table],'schema_name':'dbo','type':'U'},source.code))
            for source in sources:
                schema=schemas[source.code]
                copy_settings(pg,schema,selected_id,publishing_by=requested_by if config_version_id is not None else None,published_at=publication_started)
                identifier=pg.execute("SELECT nextval('quality_control.native_check_run_id') n").fetchone()['n']
                outputs,quality=calculate(pg,schema,selected_id,identifier,progress=lambda message:progress(source.get_code_display()+': '+message))
                snapshots[source.code]['quality']=quality
                snapshots[source.code]['orphan_activity_project_links']=pg.execute(sql.SQL("SELECT COUNT(*) n FROM {}.task t LEFT JOIN {}.project p USING(proj_id) WHERE p.proj_id IS NULL").format(sql.Identifier(schema),sql.Identifier(schema))).fetchone()['n']
                raw_counts={e['name']:e['rows'] for e in manifest if e['source']==source.code}
                for table,metric in [('project','projects'),('task','activities'),('taskpred','relationships')]:
                    if raw_counts[table]!=snapshots[source.code]['counts'][metric]:raise RuntimeError('Native snapshot counts did not reconcile.')
                manifest.extend(dict(e,source=source.code) for e in outputs)
                manifest.extend(describe_control(pg,source,schema))
                run.sources=snapshots;run.save(update_fields=['sources'])
            if config_version_id is not None: activate_version(pg,selected_id,expected_settings_hash,requested_by,published_at=publication_started)
            progress('Publishing both native PostgreSQL reporting sources together')
            run.published_at=publish_batch(pg,run,sources,snapshots,schemas,manifest)
            try:prune_generations(pg,config.retained_generations)
            except Exception:logger.exception('Native reporting published; retention will retry next time')
        run.status='success';run.progress='Both P6 sources calculated and published by PostgreSQL';run.sources=snapshots;run.manifest=manifest;run.finished_at=timezone.now();run.save()
        if switch_engine: ReportingConfig.objects.filter(pk=1).update(calculation_engine='postgres',manage_settings_in_postgres=True)
        try:
            checkpoint()
        except Exception:
            logger.exception('Reporting published; portable settings checkpoint needs retry')
        return 'Published native PostgreSQL reporting batch '+str(run.pk)
    except Exception as error:
        run.status='failed';run.progress='Native refresh failed; previous published reports retained';run.error_message=str(error)[:4000];run.finished_at=timezone.now();run.save()
        raise
    finally:
        if lock:
            try:
                if acquired:lock.execute('SELECT pg_advisory_unlock(%s)',(LOCK_ID,))
            except Exception:
                logger.exception('Reporting lock connection was interrupted')
            finally:lock.close()


def configure_sql_staging(*, config_version_id=None, expected_settings_hash=None, published_by="", published_at=None):
    """Keep the SQL fallback engine using the same PostgreSQL-authoritative rules."""
    from reporting_mirror.native_settings import TABLES
    with postgres_connection() as pg:
        ensure_control(pg)
        selected = check_draft(pg,config_version_id,expected_settings_hash) if config_version_id is not None else active_version(pg)
        rows={table:pg.execute(sql.SQL('SELECT * FROM quality_control.{} ORDER BY 1').format(sql.Identifier(table))).fetchall() for table in TABLES}
        if config_version_id is not None:
            for row in rows[TABLES[1]]:
                if row['state']=='published':row['state']='superseded'
                if row['config_version_id']==selected['config_version_id']:
                    row.update(state='published',published_at=published_at,published_by=published_by,updated_at=published_at,updated_by=published_by)
    for code in ('p6_22','p6_23'):
        with connect(staging_target(),database=safe_stage_name(code)) as stage:
            assert_staging(stage)
            for table in ('xertoolkit_result_logic_loop_tasks','xertoolkit_result_project_metrics','xertoolkit_result_schedule_quality_task_evidence','xertoolkit_result_out_of_sequence_exceptions','xertoolkit_refresh_run_history'):
                query(stage,'DELETE FROM powerbitables.'+quote_identifier(table))
            query(stage,'UPDATE powerbitables.xertoolkit_schedule_quality_profile SET active_config_version_id=NULL')
            for table in reversed(TABLES[2:]):query(stage,'DELETE FROM powerbitables.'+quote_identifier(table))
            query(stage,'UPDATE powerbitables.xertoolkit_schedule_quality_config_version SET based_on_config_version_id=NULL')
            query(stage,'DELETE FROM powerbitables.xertoolkit_schedule_quality_config_version')
            for table in TABLES[1:]:
                identifier='powerbitables.'+quote_identifier(table)
                # All operations are confined to disposable staging restores.
                identity=table in {TABLES[1],TABLES[5]}
                columns=[c for c in rows[table][0] if c!='row_version'] if rows[table] else []
                if not columns:continue
                if identity:query(stage,'SET IDENTITY_INSERT '+identifier+' ON')
                try:
                    for row in rows[table]:
                        row=dict(row)
                        query(stage,'INSERT INTO '+identifier+' ('+','.join(map(quote_identifier,columns))+') VALUES ('+','.join('?' for _ in columns)+')',tuple(row[c] for c in columns))
                finally:
                    if identity:query(stage,'SET IDENTITY_INSERT '+identifier+' OFF')
            query(stage,"UPDATE powerbitables.xertoolkit_schedule_quality_profile SET active_config_version_id=? WHERE profile_code='default'",(selected['config_version_id'],))


def refresh_history(*,limit=10,offset=0,status=''):
    from backups.services.schedule_quality import as_london_time,format_refresh_duration
    rows=ReportingRun.objects.all()
    if status in {'success','failed','running'}:rows=rows.filter(status=status)
    return [{'check_run_id':str(r.pk)[:8],'requested_proj_id':None,'config_version_id':next(iter(r.sources.values()),{}).get('quality',{}).get('config_version_id'),'started_at':as_london_time(r.started_at),'completed_at':as_london_time(r.finished_at),'duration_display':format_refresh_duration(r.started_at,r.finished_at),'status':r.status,'processed_project_count':sum(s.get('counts',{}).get('projects',0) for s in r.sources.values()),'logic_loop_task_count':None,'trigger_type':r.trigger,'error_message':r.error_message,'engine':r.engine} for r in rows[max(0,offset):max(0,offset)+max(1,min(limit,50))]]
