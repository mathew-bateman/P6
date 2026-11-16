"""Versioned PostgreSQL reporting rules with optimistic edits and portable recovery."""
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import uuid

from django.db import OperationalError, ProgrammingError
from psycopg import sql

from reporting_mirror.connections import postgres_connection, reporting_available
from reporting_mirror.sql_retirement import retired

CONTROL = "quality_control"
TABLES = ["xertoolkit_schedule_quality_" + name for name in ("profile", "config_version", "check_scope", "option", "constraint_type", "detail_field")]
KEYS = [("profile_id",), ("config_version_id",), ("config_version_id", "check_code"), ("config_version_id", "option_code"), ("config_version_id", "constraint_type_code"), ("detail_field_id",)]
EDIT_LOCK = 620252212


def enabled():
    sql_retired = retired()
    if not reporting_available():
        if sql_retired:
            raise RuntimeError("PostgreSQL reporting settings are not configured; SQL reporting is retired.")
        return False
    from reporting_mirror.models import ReportingConfig
    try:
        postgres_enabled = ReportingConfig.objects.filter(pk=1, manage_settings_in_postgres=True).exists()
    except (OperationalError, ProgrammingError) as error:
        if sql_retired:
            raise RuntimeError("PostgreSQL reporting settings are unavailable; SQL reporting is retired.") from error
        return False
    if sql_retired and not postgres_enabled:
        raise RuntimeError("PostgreSQL reporting settings must remain enabled after SQL reporting retirement.")
    return postgres_enabled


def encode(value):
    if isinstance(value, dict): return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [encode(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)): return {"$binary": bytes(value).hex()}
    if isinstance(value, Decimal): return {"$decimal": str(value)}
    if isinstance(value, (datetime, date)): return {"$datetime": value.isoformat()}
    return value


def decode(value):
    if isinstance(value, list): return [decode(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {"$binary"}: return bytes.fromhex(value["$binary"])
        if set(value) == {"$decimal"}: return Decimal(value["$decimal"])
        if set(value) == {"$datetime"}: return datetime.fromisoformat(value["$datetime"])
        return {k: decode(v) for k, v in value.items()}
    return value


def content_hash(value):
    return hashlib.sha256(json.dumps(encode(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest().upper()


def ensure_control(pg):
    """One-time migration of all settings versions; never replaces existing PG edits."""
    pg.execute("SELECT pg_advisory_xact_lock(%s)", (EDIT_LOCK,))
    pg.execute("CREATE SCHEMA IF NOT EXISTS quality_control")
    for table, key in zip(TABLES, KEYS):
        if pg.execute("SELECT to_regclass(%s) present", (CONTROL + "." + table,)).fetchone()["present"]:
            continue
        columns = pg.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='p6_22' AND table_name=%s AND left(column_name,1)<>'_' ORDER BY ordinal_position", (table,)).fetchall()
        if not columns: raise RuntimeError("Bootstrap the existing reporting snapshots before migrating quality settings.")
        names = sql.SQL(",").join(sql.Identifier(c["column_name"]) for c in columns)
        pg.execute(sql.SQL("CREATE TABLE quality_control.{} AS SELECT {} FROM p6_22.{}").format(sql.Identifier(table), names, sql.Identifier(table)))
        pg.execute(sql.SQL("ALTER TABLE quality_control.{} ADD PRIMARY KEY ({})").format(sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, key))))
    for suffix, table, field in [("version", TABLES[1], "config_version_id"), ("detail", TABLES[5], "detail_field_id")]:
        sequence = "native_" + suffix + "_id"
        if not pg.execute("SELECT to_regclass(%s) present", (CONTROL + "." + sequence,)).fetchone()["present"]:
            maximum = pg.execute(sql.SQL("SELECT COALESCE(MAX({}),0) n FROM quality_control.{}").format(sql.Identifier(field), sql.Identifier(table))).fetchone()["n"]
            pg.execute(sql.SQL("CREATE SEQUENCE quality_control.{} START WITH {}").format(sql.Identifier(sequence), sql.Literal(maximum + 1)))
    pg.execute("CREATE SEQUENCE IF NOT EXISTS quality_control.native_check_run_id START WITH 1000000")


def active_version(pg):
    return pg.execute("SELECT v.* FROM quality_control.xertoolkit_schedule_quality_config_version v JOIN quality_control.xertoolkit_schedule_quality_profile p ON p.active_config_version_id=v.config_version_id WHERE p.profile_code='default'").fetchone()


def get_or_create_draft(*, changed_by, profile_code=None):
    if profile_code not in (None, "default"): raise ValueError("Only the default reporting profile is configured.")
    with postgres_connection() as pg:
        ensure_control(pg)
        active = active_version(pg)
        row = pg.execute("SELECT config_version_id FROM quality_control.xertoolkit_schedule_quality_config_version WHERE profile_id=%s AND state='draft' AND based_on_config_version_id=%s ORDER BY config_version_id DESC LIMIT 1", (active["profile_id"], active["config_version_id"])).fetchone()
        if row: return row["config_version_id"]
        identifier = pg.execute("SELECT nextval('quality_control.native_version_id') n").fetchone()["n"]
        number = pg.execute("SELECT COALESCE(MAX(version_number),0)+1 n FROM quality_control.xertoolkit_schedule_quality_config_version WHERE profile_id=%s", (active["profile_id"],)).fetchone()["n"]
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        header = dict(active, config_version_id=identifier, version_number=number, state="draft", based_on_config_version_id=active["config_version_id"], change_note="", created_at=now, created_by=changed_by, updated_at=now, updated_by=changed_by, published_at=None, published_by=None)
        insert_row(pg, TABLES[1], header)
        for table in TABLES[2:]:
            for row in pg.execute(sql.SQL("SELECT * FROM quality_control.{} WHERE config_version_id=%s").format(sql.Identifier(table)), (active["config_version_id"],)).fetchall():
                row["config_version_id"] = identifier
                if table == TABLES[5]: row["detail_field_id"] = pg.execute("SELECT nextval('quality_control.native_detail_id') n").fetchone()["n"]
                insert_row(pg, table, row)
    checkpoint()
    return identifier


def insert_row(pg, table, row, *, schema=CONTROL):
    names = list(row)
    pg.execute(sql.SQL("INSERT INTO {}.{} ({}) VALUES ({})").format(sql.Identifier(schema), sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, names)), sql.SQL(",").join(sql.Placeholder() for _ in names)), tuple(row[n] for n in names))


def fetch_snapshot(*, config_version_id):
    from backups.services.schedule_quality import _snapshot_from_result_sets
    with postgres_connection() as pg:
        header = pg.execute("SELECT p.profile_code,p.profile_name,p.active_config_version_id,v.* FROM quality_control.xertoolkit_schedule_quality_config_version v JOIN quality_control.xertoolkit_schedule_quality_profile p ON p.profile_id=v.profile_id WHERE v.config_version_id=%s", (config_version_id,)).fetchall()
        if not header: raise LookupError("The PostgreSQL settings version was not found.")
        result_sets = [header]
        for table, order in [(TABLES[2], "sort_order,check_code"), (TABLES[3], "sort_order,option_code"), (TABLES[4], "sort_order,constraint_type_code"), (TABLES[5], "check_code,sort_order,detail_field_id")]:
            result_sets.append(pg.execute(sql.SQL("SELECT * FROM quality_control.{} WHERE config_version_id=%s ORDER BY " + order).format(sql.Identifier(table)), (config_version_id,)).fetchall())
    return _snapshot_from_result_sets(result_sets)


def check_draft(pg, config_version_id, expected_settings_hash):
    from backups.services.schedule_quality import ScheduleQualitySettingsConflict, _validated_settings_hash
    expected_settings_hash = _validated_settings_hash(expected_settings_hash)
    row = pg.execute("SELECT * FROM quality_control.xertoolkit_schedule_quality_config_version WHERE config_version_id=%s FOR UPDATE", (config_version_id,)).fetchone()
    if not row or row["state"] != "draft" or row["settings_hash"].strip().upper() != expected_settings_hash:
        raise ScheduleQualitySettingsConflict("The draft changed after it was loaded. Reload it before continuing.")
    return row


def save_draft(*, config_version_id, payload, expected_settings_hash, changed_by, change_note=""):
    with postgres_connection() as pg:
        pg.execute("SELECT pg_advisory_xact_lock(%s)", (EDIT_LOCK,))
        check_draft(pg, config_version_id, expected_settings_hash)
        for key, table, code in [("checks", TABLES[2], "check_code"), ("options", TABLES[3], "option_code"), ("constraint_types", TABLES[4], "constraint_type_code")]:
            current = {r[code]: r for r in pg.execute(sql.SQL("SELECT * FROM quality_control.{} WHERE config_version_id=%s").format(sql.Identifier(table)), (config_version_id,)).fetchall()}
            incoming = payload.get(key, [])
            if len(incoming) != len(current) or {r[code] for r in incoming} != set(current):
                raise ValueError("Settings must retain every configured " + key + " entry.")
            for value in incoming:
                editable = {
                    'checks': {'is_enabled','include_loe','include_wbs_summary','include_milestones','exclude_complete','limit_type','green_limit','amber_limit','green_points','amber_points','records_metric','qualifying_metric'},
                    'options': {'bit_value','numeric_value','text_value'},
                    'constraint_types': {'is_checked'},
                }[key]
                if set(value) - {code} - editable: raise ValueError("Unexpected settings field.")
                if key == 'checks' and value[code] == 'soft_constraints':
                    value = dict(value,limit_type='Percent',green_limit=0,amber_limit=0,green_points=0,amber_points=0)
                updates = {k: int(v) if isinstance(v, bool) else v for k, v in value.items() if k != code}
                pg.execute(sql.SQL("UPDATE quality_control.{} SET {} WHERE config_version_id=%s AND {}=%s").format(sql.Identifier(table), sql.SQL(",").join(sql.SQL("{}=%s").format(sql.Identifier(k)) for k in updates), sql.Identifier(code)), (*updates.values(), config_version_id, value[code]))
        table = TABLES[5]
        pg.execute(sql.SQL("DELETE FROM quality_control.{} WHERE config_version_id=%s").format(sql.Identifier(table)), (config_version_id,))
        known_checks = {r["check_code"] for r in payload["checks"]}
        for value in payload.get("detail_fields", []):
            if value["check_code"] not in known_checks: raise ValueError("Unknown evidence check.")
            if value.get("display_format", "native") not in {"native", "p6_days_only", "p6_hours_and_days"}: raise ValueError("Unsupported evidence format.")
            if set(value) - {'check_code','source_category','source_identifier','display_label','display_format','sort_order','detail_field_id'}: raise ValueError('Unexpected evidence field.')
            row = {k: v for k, v in value.items() if k != "detail_field_id"}
            row.update(config_version_id=config_version_id, detail_field_id=pg.execute("SELECT nextval('quality_control.native_detail_id') n").fetchone()["n"])
            insert_row(pg, table, row)
        checksum = content_hash(payload)
        pg.execute("UPDATE quality_control.xertoolkit_schedule_quality_config_version SET settings_hash=%s,updated_at=%s,updated_by=%s,change_note=%s WHERE config_version_id=%s", (checksum, datetime.now(timezone.utc).replace(tzinfo=None), changed_by, change_note, config_version_id))
    checkpoint()
    return checksum


def copy_settings(pg, schema, config_version_id, *, publishing_by=None, published_at=None):
    """A calculation sees a private, immutable copy of the selected rules."""
    from reporting_mirror.native_engine import generation_schema
    generation_schema(schema)
    for table in TABLES:
        pg.execute(sql.SQL("CREATE TABLE {}.{} AS SELECT * FROM quality_control.{}").format(sql.Identifier(schema), sql.Identifier(table), sql.Identifier(table)))
    pg.execute(sql.SQL("UPDATE {}.xertoolkit_schedule_quality_profile SET active_config_version_id=%s WHERE profile_code='default'").format(sql.Identifier(schema)), (config_version_id,))
    if publishing_by is not None:
        pg.execute(sql.SQL("UPDATE {}.xertoolkit_schedule_quality_config_version SET state='superseded' WHERE state='published' AND config_version_id<>%s").format(sql.Identifier(schema)), (config_version_id,))
        pg.execute(sql.SQL("UPDATE {}.xertoolkit_schedule_quality_config_version SET state='published',published_at=%s,published_by=%s,updated_at=%s,updated_by=%s WHERE config_version_id=%s").format(sql.Identifier(schema)), (published_at, publishing_by, published_at, publishing_by, config_version_id))


def activate_version(pg, config_version_id, expected_settings_hash, published_by, *, published_at=None):
    check_draft(pg, config_version_id, expected_settings_hash)
    old = active_version(pg)
    now = published_at or datetime.now(timezone.utc).replace(tzinfo=None)
    pg.execute("UPDATE quality_control.xertoolkit_schedule_quality_config_version SET state='superseded' WHERE config_version_id=%s", (old["config_version_id"],))
    pg.execute("UPDATE quality_control.xertoolkit_schedule_quality_config_version SET state='published',published_at=%s,published_by=%s,updated_at=%s,updated_by=%s WHERE config_version_id=%s", (now, published_by, now, published_by, config_version_id))
    pg.execute("UPDATE quality_control.xertoolkit_schedule_quality_profile SET active_config_version_id=%s,row_version=%s WHERE profile_code='default'", (config_version_id, uuid.uuid4().bytes[:8]))


def checkpoint():
    from reporting_mirror.models import NativeSettingsCheckpoint
    with postgres_connection(reader=False) as pg:
        payload = {table: encode(pg.execute(sql.SQL("SELECT * FROM quality_control.{} ORDER BY 1").format(sql.Identifier(table))).fetchall()) for table in TABLES}
    checksum = content_hash(payload)
    NativeSettingsCheckpoint.objects.update_or_create(pk=1, defaults={"payload": payload, "sha256": checksum})
    # Do not wait on the network while editing or publishing reporting rules.
    try:
        from reporting_mirror.tasks import backup_reporting_settings
        backup_reporting_settings.apply_async(queue='p6_reporting')
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Settings checkpoint saved; offsite backup will retry on the next refresh')
    return checksum


def restore_checkpoint(pg, payload, expected_sha256):
    if content_hash(payload) != expected_sha256: raise ValueError("Reporting settings recovery checksum did not match.")
    if set(payload) != set(TABLES): raise ValueError("Incomplete reporting settings recovery data.")
    pg.execute("SELECT pg_advisory_xact_lock(%s)", (EDIT_LOCK,))
    for table in reversed(TABLES): pg.execute(sql.SQL("DELETE FROM quality_control.{}").format(sql.Identifier(table)))
    for table in TABLES:
        for row in decode(payload[table]): insert_row(pg, table, row)
    for seq, table, field in [("native_version_id", TABLES[1], "config_version_id"), ("native_detail_id", TABLES[5], "detail_field_id")]:
        maximum = pg.execute(sql.SQL("SELECT COALESCE(MAX({}),0) n FROM quality_control.{}").format(sql.Identifier(field), sql.Identifier(table))).fetchone()["n"]
        pg.execute("SELECT setval(%s,%s,false)", (CONTROL + "." + seq, maximum + 1))
