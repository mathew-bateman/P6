"""Isolated benchmark transport: SQL Server Arrow -> native PostgreSQL COPY.

This is an experiment, not a production loader. It deliberately does not publish
views. Every value is compared with an independent source reread, ordered by the
source primary key. No Python row normalization/JSON hashing is used.

Input JSON is private and contains ``source_connection_string``, ``postgres``
(psycopg connection keyword arguments), ``schema``, ``source_code``, ``tables``
and ``benchmark_only: true``. PostgreSQL cluster_name must start ``p6-perf``.
The private generation schema is committed before the native writer connects;
only a separate caller may publish a successful, fully verified generation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import re
import time
from urllib.parse import quote

import adbc_driver_postgresql.dbapi as adbc_pg
import mssql_python
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
import pyarrow as pa


SCHEMA = re.compile(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)\Z")
METHOD = "ordered_arrow_exact_v1"


def qi(value):
    return "[" + value.replace("]", "]]") + "]"


def pg_name(value):
    return value if " " in value else value.lower()


def type_mapping(column):
    """Explicit, exact mirror of the live loader's destination contract."""
    kind = column["type_name"].lower()
    if kind in {"varchar", "nvarchar", "nchar", "char", "text", "ntext", "xml"}:
        return "text", pa.string()
    integers = {"int": ("integer", pa.int32()), "bigint": ("bigint", pa.int64()),
                "smallint": ("smallint", pa.int16()), "tinyint": ("smallint", pa.int16()),
                "bit": ("smallint", pa.int16())}
    if kind in integers:
        return integers[kind]
    if kind in {"numeric", "decimal"}:
        precision, scale = int(column["precision"]), int(column["scale"])
        return f"numeric({precision},{scale})", pa.decimal128(precision, scale)
    if kind in {"money", "smallmoney"}:
        return "numeric(19,4)", pa.decimal128(19, 4)
    if kind in {"float", "real"}:
        return ("double precision", pa.float64()) if kind == "float" else ("real", pa.float32())
    if kind in {"datetime", "datetime2", "smalldatetime"}:
        return "timestamp without time zone", pa.timestamp("us")
    if kind == "date":
        return "date", pa.date32()
    if kind == "time":
        return "time without time zone", pa.time64("us")
    if kind == "datetimeoffset":
        return "timestamp with time zone", pa.timestamp("us", tz="UTC")
    if kind in {"binary", "varbinary", "image", "timestamp", "rowversion"}:
        return "bytea", pa.binary()
    # UUID Arrow extension behavior depends on driver versions. Fail closed until
    # the benchmark explicitly adds and validates an exact adapter.
    raise ValueError(f"Native benchmark has no validated exact mapping for {kind}")


def pg_uri(config):
    user = quote(config["user"], safe="")
    password = quote(config["password"], safe="")
    host = config["host"]
    port = int(config.get("port", 5432))
    database = quote(config["dbname"], safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def metadata(connection, table, source_schema="dbo"):
    cursor = connection.cursor()
    try:
        cursor.execute("""SELECT c.name,ty.name type_name,c.precision,c.scale
          FROM sys.tables t JOIN sys.schemas s ON s.schema_id=t.schema_id
          JOIN sys.columns c ON c.object_id=t.object_id
          JOIN sys.types ty ON ty.user_type_id=c.user_type_id
          WHERE s.name=? AND t.name=? ORDER BY c.column_id""", (source_schema, table))
        columns = [dict(zip(("name", "type_name", "precision", "scale"), row)) for row in cursor.fetchall()]
        if not columns:
            raise ValueError(f"Source table {table} has no columns")
        cursor.execute("""SELECT c.name FROM sys.tables t
          JOIN sys.schemas s ON s.schema_id=t.schema_id
          JOIN sys.indexes i ON i.object_id=t.object_id AND i.is_primary_key=1
          JOIN sys.index_columns ic ON ic.object_id=i.object_id AND ic.index_id=i.index_id
          JOIN sys.columns c ON c.object_id=ic.object_id AND c.column_id=ic.column_id
          WHERE s.name=? AND t.name=? ORDER BY ic.key_ordinal""", (source_schema, table))
        keys = [r[0] for r in cursor.fetchall()]
        if not keys:
            raise ValueError(f"Full ordered verification requires a source primary key: {table}")
        return columns, keys
    finally:
        cursor.close()


def normalise_batch(batch, target_schema):
    """C++ column casts; safe=True rejects precision loss and overflow."""
    if batch.num_columns != len(target_schema):
        raise ValueError("Unexpected column count in native stream")
    arrays = [batch.column(i).cast(field.type, safe=True) for i, field in enumerate(target_schema)]
    return pa.RecordBatch.from_arrays(arrays, schema=target_schema)


def compare_streams(left, right, target_schema):
    """Compare every typed value/null while allowing different batch boundaries."""
    left, right = iter(left), iter(right)
    lhs = rhs = None
    li = ri = count = 0
    while True:
        if lhs is None or li == lhs.num_rows:
            lhs = next(left, None)
            li = 0
            while lhs is not None and lhs.num_rows == 0:
                lhs = next(left, None)
            if lhs is not None:
                lhs = normalise_batch(lhs, target_schema)
        if rhs is None or ri == rhs.num_rows:
            rhs = next(right, None)
            ri = 0
            while rhs is not None and rhs.num_rows == 0:
                rhs = next(right, None)
            if rhs is not None:
                rhs = normalise_batch(rhs, target_schema)
        if lhs is None or rhs is None:
            if lhs is not None or rhs is not None:
                raise ValueError(f"Source/destination row counts differ after row {count}")
            return count
        length = min(lhs.num_rows-li, rhs.num_rows-ri)
        if not lhs.slice(li, length).equals(rhs.slice(ri, length), check_metadata=False):
            # Identify the column, never print source data into public logs.
            different = [target_schema[i].name for i in range(len(target_schema))
                         if not lhs.column(i).slice(li, length).equals(rhs.column(i).slice(ri, length))]
            raise ValueError(f"Full-value verification failed near row {count}; columns={different}")
        li += length
        ri += length
        count += length


def verify_table(config, table, columns, keys, target_schema):
    schema = config["schema"]
    source_fields = ",".join(qi(c["name"]) for c in columns)
    source_order = ",".join(qi(k) for k in keys)
    pg_fields = sql.SQL(",").join(sql.Identifier(pg_name(c["name"])) for c in columns)
    pg_order = sql.SQL(",").join(sql.Identifier(pg_name(k)) for k in keys)
    destination_query = sql.SQL("SELECT {} FROM {}.{} ORDER BY {}").format(
        pg_fields, sql.Identifier(schema), sql.Identifier(table.lower()), pg_order).as_string()
    with closing(mssql_python.connect(config["source_connection_string"])) as source:
        with closing(source.cursor()) as sc:
            sc.execute(f"SELECT {source_fields} FROM [dbo].{qi(table)} ORDER BY {source_order}")
            with sc.arrow_reader(batch_size=config.get("batch_size", 50000)) as source_reader:
                with adbc_pg.connect(pg_uri(config["postgres"])) as target:
                    with target.cursor() as cursor:
                        cursor.execute(destination_query)
                        with cursor.fetch_record_batch() as destination_reader:
                            return compare_streams(source_reader, destination_reader, target_schema)


def transfer_table(config, table):
    started = time.perf_counter()
    schema, source_code = config["schema"], config["source_code"]
    with closing(mssql_python.connect(config["source_connection_string"])) as source:
        columns, keys = metadata(source, table)
        mappings = [type_mapping(c) for c in columns]
        names = [pg_name(c["name"]) for c in columns]
        if len(set(names)) != len(names) or any(len(n.encode()) > 63 for n in names):
            raise ValueError("Invalid or colliding PostgreSQL field names")
        target_schema = pa.schema([pa.field(n, mapping[1]) for n, mapping in zip(names, mappings)])
        with psycopg.connect(**config["postgres"]) as target:
            definitions = sql.SQL(",").join(sql.SQL("{} {}").format(sql.Identifier(n), sql.SQL(m[0]))
                                            for n, m in zip(names, mappings))
            target.execute(sql.SQL("CREATE TABLE {}.{} ({})").format(
                sql.Identifier(schema), sql.Identifier(table.lower()), definitions))
        # The table is private and committed so a separate native COPY connection
        # can see it. No application publication is performed by this module.
        metadata_done = time.perf_counter()
        loaded_count = 0
        with closing(source.cursor()) as cursor:
            cursor.execute("SELECT " + ",".join(qi(c["name"]) for c in columns) + " FROM [dbo]." + qi(table))
            with cursor.arrow_reader(batch_size=config.get("batch_size", 50000)) as raw:
                def batches():
                    nonlocal loaded_count
                    for batch in raw:
                        loaded_count += batch.num_rows
                        yield normalise_batch(batch, target_schema)
                with pa.RecordBatchReader.from_batches(target_schema, batches()) as reader:
                    with adbc_pg.connect(pg_uri(config["postgres"])) as target:
                        with target.cursor() as writer:
                            writer.adbc_ingest(table.lower(), reader, mode="append", db_schema_name=schema)
                        target.commit()
        load_done = time.perf_counter()
    # Preserve the existing raw-index policy exactly. Extra UNIQUE/PK indexes can
    # change derived-report join plans even when the copied values are identical.
    # ORDER BY still establishes deterministic full-value verification; the
    # immutable SQL primary key and exact ordered equality cover multiplicities.
    with psycopg.connect(**config["postgres"]) as target:
        if config.get("build_legacy_indexes", True):
            for field in ("proj_id", "task_id", "rsrc_id", "config_version_id"):
                if field in names:
                    target.execute(sql.SQL("CREATE INDEX ON {}.{} ({})").format(
                        sql.Identifier(schema), sql.Identifier(table.lower()), sql.Identifier(field)))
            if "proj_id" in names:
                offset = {"p6_22": 4294967296, "p6_23": 8589934592}[source_code]
                target.execute(sql.SQL("CREATE INDEX ON {}.{} (({}::bigint+proj_id))").format(
                    sql.Identifier(schema), sql.Identifier(table.lower()), sql.Literal(offset)))
        target.execute(sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(table.lower())))
    indexed = time.perf_counter()
    verified_count = verify_table(config, table, columns, keys, target_schema)
    if verified_count != loaded_count:
        raise ValueError(f"Loaded and verified row counts differ for {table}")
    completed = time.perf_counter()
    entry = {"source": source_code, "name": table.lower(), "source_schema": "dbo", "kind": "table",
             "rows": verified_count, "columns": [{"name": n, "type": m[0]} for n, m in zip(names, mappings)],
             "verification": {"method": METHOD, "all_values_equal": True, "rows_compared": verified_count,
                              "primary_key": [pg_name(k) for k in keys]}}
    timings = {"metadata_seconds": metadata_done-started, "load_seconds": load_done-metadata_done,
               "index_seconds": indexed-load_done, "verify_seconds": completed-indexed,
               "total_seconds": completed-started}
    return {"entry": entry, "timings": timings}


def validate_precreated_schema(pg, config):
    """Use only a fresh helper-only private schema prepared by the pipeline."""
    try:
        from ..all_project_calc import verify_precreated_helpers
    except ImportError:
        from all_project_calc import verify_precreated_helpers
    schema = config["schema"]
    if not schema.endswith("_" + config["source_code"]):
        raise ValueError("Precreated schema belongs to a different P6 source")
    relations = pg.execute("""SELECT count(*) AS n FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s""", (schema,)).fetchone()["n"]
    if relations:
        raise ValueError("Precreated raw generation must contain no relations")
    return verify_precreated_helpers(pg, schema, config.get("helper_reference_schema"))


def run(config):
    if config.get("benchmark_only") is not True or not SCHEMA.fullmatch(config["schema"]):
        raise ValueError("Use a private generation schema and benchmark_only=true")
    with psycopg.connect(**config["postgres"], row_factory=dict_row) as pg:
        cluster = pg.execute("SHOW cluster_name").fetchone()["cluster_name"]
        if not cluster.startswith("p6-perf"):
            raise ValueError("Refusing writes: PostgreSQL cluster_name must start p6-perf")
        precreated = config.get("schema_precreated", False)
        if not isinstance(precreated, bool):
            raise ValueError("schema_precreated must be a bool")
        helper_verification = validate_precreated_schema(pg, config) if precreated else None
        if not precreated:
            pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(config["schema"])))
    started = time.perf_counter()
    # Each table uses separate source and target connections, bounded by workers.
    with ThreadPoolExecutor(max_workers=int(config.get("workers", 1))) as pool:
        results = list(pool.map(lambda table: transfer_table(config, table), config["tables"]))
    return {"status": "verified", "method": METHOD, "seconds": time.perf_counter()-started,
            "schema": config["schema"], "tables": results,
            "precreated_helper_verification": helper_verification,
            "rows": sum(r["entry"]["rows"] for r in results)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(json.loads(args.config.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ("status", "method", "seconds", "rows", "schema")}))


if __name__ == "__main__":
    main()
