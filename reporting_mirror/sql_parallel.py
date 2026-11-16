"""Retain existing live P6 22 SQL reporting alongside PostgreSQL.

Only reporting rows are written. SQL modules, P6 project data, old settings and
the operator's existing SQL draft are never replaced. P6 23 has no legacy SQL
reporting installation and is deliberately outside this helper's scope.
"""
import logging

from django.utils import timezone
from psycopg import sql

from backups.services.mssql import connect, quote_identifier
from reporting_mirror.connections import postgres_connection, reporting_available
from reporting_mirror.models import ReportingConfig, ReportingSource, SQLParallelRun
from reporting_mirror.native_settings import TABLES
from reporting_mirror.sql_staging import query
from reporting_mirror.sync import LOCK_ID
from reporting_mirror.sql_retirement import retired

logger = logging.getLogger(__name__)
SQL_LOCK = "powerbitables.xertoolkit.schedule_quality.refresh"
VERSION_TABLE = TABLES[1]


def queue_sql_parallel(*, trigger="scheduled", publication=None):
    """Queue once per publication; broker failures cannot invalidate PG results."""
    if retired():
        return None
    if not ReportingConfig.objects.filter(pk=1, sql_parallel_enabled=True, manage_settings_in_postgres=True).exists() or not reporting_available():
        return None
    if publication is not None:
        if publication.status != "success" or publication.engine != "postgres":
            return None
        record, created = SQLParallelRun.objects.get_or_create(publication=publication, defaults={"trigger": trigger})
        if not created:
            return str(record.pk)
    else:
        record = SQLParallelRun.objects.create(trigger=trigger)
    try:
        from reporting_mirror.tasks import refresh_sql_parallel
        refresh_sql_parallel.apply_async(kwargs={"run_id": str(record.pk)}, queue="p6_reporting")
    except Exception as error:
        record.status = "failed"; record.finished_at = timezone.now()
        record.progress = "SQL reporting refresh could not be queued; PostgreSQL reports remain available"
        record.error_message = str(error)[:4000]; record.save()
        logger.exception("Could not queue retained SQL reporting refresh")
    return str(record.pk)


def current_settings():
    """Read only the canonical active version, never an unpublished draft."""
    with postgres_connection() as pg:
        pg.execute("SET TRANSACTION READ ONLY")
        selected = pg.execute("SELECT v.* FROM quality_control.xertoolkit_schedule_quality_profile p JOIN quality_control.xertoolkit_schedule_quality_config_version v ON v.config_version_id=p.active_config_version_id WHERE p.profile_code='default'").fetchone()
        if not selected or selected["state"] not in {"active", "published"}:
            raise RuntimeError("The canonical PostgreSQL reporting settings are not active.")
        checksum = str(selected.get("settings_hash") or "").strip().upper()
        if len(checksum) != 64 or any(c not in "0123456789ABCDEF" for c in checksum):
            raise RuntimeError("The active reporting settings have no valid checksum.")
        children = {}
        for table in TABLES[2:]:
            order = "check_code,sort_order,detail_field_id" if table == TABLES[5] else "1"
            children[table] = pg.execute(sql.SQL("SELECT * FROM quality_control.{} WHERE config_version_id=%s ORDER BY " + order).format(sql.Identifier(table)), (selected["config_version_id"],)).fetchall()
        return dict(selected, settings_hash=checksum), children


def retained_sql_target():
    source = ReportingSource.objects.select_related("target").get(code="p6_22", enabled=True)
    from backups.services.schedule_quality import build_schedule_quality_target
    configured = build_schedule_quality_target()
    target = source.target
    if target.sql_database != "P62212_1" or configured.sql_database != target.sql_database or configured.sql_host.lower() != target.sql_host.lower() or int(configured.sql_port) != int(target.sql_port):
        raise RuntimeError("The retained SQL reporting target does not match the configured P6 22 source.")
    return target


def insert_row(connection, table, row):
    """Use SQL-generated identities; canonical PG IDs never overwrite SQL IDs."""
    columns = list(row)
    statement = "INSERT INTO powerbitables." + quote_identifier(table) + " (" + ",".join(map(quote_identifier, columns)) + ")"
    if table == VERSION_TABLE:
        statement += " OUTPUT INSERTED.config_version_id"
    statement += " VALUES (" + ",".join("?" for _ in columns) + ")"
    return query(connection, statement, tuple(row[c] for c in columns))


def mirror_and_refresh(connection, selected, children, *, trigger="scheduled"):
    """Caller owns one outer transaction and the session refresh lock.

    The existing procedure publishes results transactionally. Enclosing the
    additive configuration change in that same transaction retains the prior
    SQL pointer and results if any calculation fails.
    """
    identity = query(connection, "SELECT DB_NAME() database_name,CAST(SERVERPROPERTY('MachineName') AS nvarchar(128)) machine_name,OBJECT_ID('dbo.PROJECT','U') project_object,OBJECT_ID('powerbitables.xertoolkit_refresh_all_schedule_quality','P') refresh_object")[0]
    if identity["database_name"] != "P62212_1" or identity["machine_name"] != "xps4-p62212" or not identity["project_object"] or not identity["refresh_object"]:
        raise RuntimeError("SQL reporting identity or existing definitions did not match; no changes were made.")
    active_rows = query(connection, "SELECT p.profile_id,p.active_config_version_id,v.settings_hash,v.state FROM powerbitables.xertoolkit_schedule_quality_profile p WITH(UPDLOCK,HOLDLOCK) JOIN powerbitables.xertoolkit_schedule_quality_config_version v ON v.config_version_id=p.active_config_version_id WHERE p.profile_code='default'")
    if len(active_rows) != 1 or active_rows[0]["state"] != "active":
        raise RuntimeError("The retained SQL reporting profile has no unique active version.")
    active = active_rows[0]
    sql_id = active["active_config_version_id"]
    if str(active["settings_hash"] or "").strip().upper() != selected["settings_hash"]:
        # Preserve every old version and any existing draft. The new immutable
        # published copy is appended as active only within this transaction.
        number = query(connection, "SELECT COALESCE(MAX(version_number),0)+1 number FROM powerbitables.xertoolkit_schedule_quality_config_version WITH(UPDLOCK,HOLDLOCK) WHERE profile_id=?", (active["profile_id"],))[0]["number"]
        query(connection, "UPDATE powerbitables.xertoolkit_schedule_quality_config_version SET state='superseded' WHERE config_version_id=? AND state='active'", (sql_id,))
        header = {k: selected[k] for k in ("settings_hash", "created_at", "created_by", "updated_at", "updated_by", "published_at", "published_by")}
        header.update(profile_id=active["profile_id"], version_number=number, state="active", based_on_config_version_id=sql_id, change_note=("PostgreSQL parallel reporting version " + str(selected["config_version_id"]) + ". " + str(selected.get("change_note") or ""))[:500])
        sql_id = insert_row(connection, VERSION_TABLE, header)[0]["config_version_id"]
        for table in TABLES[2:]:
            for original in children[table]:
                row = {k: v for k, v in original.items() if k not in {"config_version_id", "detail_field_id", "row_version"}}
                row["config_version_id"] = sql_id
                insert_row(connection, table, row)
        query(connection, "UPDATE powerbitables.xertoolkit_schedule_quality_profile SET active_config_version_id=? WHERE profile_id=? AND profile_code='default'", (sql_id, active["profile_id"]))
        # The original publish procedure also maintains these four compatibility
        # settings for older SQL consumers. Keep that contract while preserving
        # the operator's separate SQL draft.
        options = {row["option_code"]: row["numeric_value"] for row in children[TABLES[3]]}
        for name, value in (("High Duration", options["high_duration_days"]), ("High Float", options["high_float_days"]), ("Critical Float", 0), ("Near Critical", options["near_critical_upper_days"])):
            query(connection, "UPDATE powerbitables.xertoolkit_settings_thresholds SET setting_value=? WHERE setting_name=?; IF @@ROWCOUNT=0 INSERT INTO powerbitables.xertoolkit_settings_thresholds(setting_name,setting_value) VALUES(?,?)", (value, name, name, value))
    sql_trigger = trigger if trigger in {"scheduled", "manual", "publish", "canary"} else "publish" if trigger == "settings_publish" else "manual"
    query(connection, "EXEC powerbitables.xertoolkit_refresh_all_schedule_quality @config_version_id=?,@trigger_type=?", (sql_id, sql_trigger))
    result = query(connection, "SELECT TOP(1) check_run_id,status,config_version_id,processed_project_count FROM powerbitables.xertoolkit_refresh_run_history ORDER BY check_run_id DESC")[0]
    if result["status"] != "success" or result["config_version_id"] != sql_id:
        raise RuntimeError("The retained SQL reporting calculation did not finish successfully.")
    return result


def run_sql_parallel(run_id):
    record = SQLParallelRun.objects.get(pk=run_id)
    if record.status not in {"queued", "failed"}:
        return record.progress
    if retired() or not ReportingConfig.objects.filter(pk=1, sql_parallel_enabled=True, manage_settings_in_postgres=True).exists() or not reporting_available():
        record.status = "skipped"; record.progress = "Parallel SQL reporting is disabled"; record.finished_at = timezone.now(); record.save()
        return record.progress
    claimed = SQLParallelRun.objects.filter(pk=record.pk, status__in={"queued", "failed"}).update(status="running", started_at=timezone.now(), finished_at=None, error_message="")
    if not claimed:
        record.refresh_from_db()
        return record.progress
    record.refresh_from_db()
    lock = None; acquired = False; connection = None; sql_lock = False
    try:
        lock = postgres_connection(autocommit=True)
        acquired = lock.execute("SELECT pg_try_advisory_lock(%s) acquired", (LOCK_ID,)).fetchone()["acquired"]
        if not acquired:
            raise RuntimeError("Another reporting operation is running; retry the SQL reporting refresh.")
        selected, children = current_settings()
        record.postgres_config_version_id = selected["config_version_id"]; record.settings_hash = selected["settings_hash"]
        record.progress = "Refreshing the retained P6 22 SQL reports with the active reporting rules"; record.save()
        connection = connect(retained_sql_target(), database="P62212_1", autocommit=False)
        connection.timeout = 5400
        query(connection, "SET XACT_ABORT ON")
        query(connection, "IF @@TRANCOUNT=0 BEGIN TRANSACTION")
        result = query(connection, "DECLARE @r int; EXEC @r=sys.sp_getapplock @Resource=?,@LockMode='Exclusive',@LockOwner='Session',@LockTimeout=0; SELECT @r result", (SQL_LOCK,))[0]["result"]
        if result < 0:
            raise RuntimeError("Another SQL reporting refresh is running.")
        sql_lock = True
        result = mirror_and_refresh(connection, selected, children, trigger=record.trigger)
        connection.commit()
        record.sql_config_version_id = result["config_version_id"]; record.sql_check_run_id = result["check_run_id"]; record.processed_project_count = result["processed_project_count"]
        record.status = "success"; record.progress = "P6 22 SQL reports refreshed in parallel; PostgreSQL continues serving Django"
    except Exception as error:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                logger.exception("SQL reporting rollback failed; closing its session")
        record.status = "failed"; record.error_message = str(error)[:4000]
        record.progress = "SQL refresh failed; previous SQL results and PostgreSQL reports remain available"
        logger.exception("Retained SQL reporting refresh failed")
        raise
    finally:
        record.finished_at = timezone.now()
        try:
            record.save()
        except Exception:
            logger.exception("Could not save the retained SQL reporting audit")
        if connection is not None:
            try:
                if sql_lock:
                    query(connection, "EXEC sys.sp_releaseapplock @Resource=?,@LockOwner='Session'", (SQL_LOCK,))
            except Exception:
                logger.exception("Could not release the SQL reporting session lock; closing its session")
            try:
                connection.close()
            except Exception:
                logger.exception("Could not close the SQL reporting session")
        if lock is not None:
            try:
                if acquired:
                    lock.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            except Exception:
                logger.exception("Could not release the PostgreSQL reporting lock; closing its session")
            try:
                lock.close()
            except Exception:
                logger.exception("Could not close the PostgreSQL reporting lock session")
    return record.progress
