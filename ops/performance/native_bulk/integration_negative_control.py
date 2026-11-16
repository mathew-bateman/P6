"""Prove full verification rejects a same-row-count value corruption.

Only the unpublished PROJECT smoke table is eligible. The original value is
restored in a finally block before the full comparison is repeated. No source,
published table or application state is changed and no data values are logged.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import time

import mssql_python
import psycopg
from psycopg import sql
import pyarrow as pa

from arrow_transfer import metadata, pg_name, type_mapping, verify_table, METHOD


EXPECTED_SCHEMA = "native_shadow_a000000000000000_p6_23"


def run(config):
    if config.get("benchmark_only") is not True or config["schema"] != EXPECTED_SCHEMA:
        raise ValueError("Negative control is restricted to the unpublished PROJECT smoke schema")
    if config["tables"] != ["PROJECT"]:
        raise ValueError("Negative control is restricted to the PROJECT smoke table")
    with closing(mssql_python.connect(config["source_connection_string"])) as source:
        columns, keys = metadata(source, "PROJECT")
    target_schema = pa.schema([pa.field(pg_name(c["name"]), type_mapping(c)[1]) for c in columns])
    target = sql.SQL("{}.project").format(sql.Identifier(EXPECTED_SCHEMA))
    started = time.perf_counter()
    initial_count = verify_table(config, "PROJECT", columns, keys, target_schema)
    original = None
    row_id = None
    restored = False
    detected = False
    try:
        with psycopg.connect(**config["postgres"]) as pg:
            cluster = pg.execute("SHOW cluster_name").fetchone()[0]
            if not cluster.startswith("p6-perf"):
                raise ValueError("Refusing writes outside the isolated performance cluster")
            row_id, original = pg.execute(sql.SQL(
                "SELECT proj_id,proj_short_name FROM {} ORDER BY proj_id LIMIT 1").format(target)).fetchone()
            changed = pg.execute(sql.SQL("UPDATE {} SET proj_short_name="
                "coalesce(proj_short_name,'') || '__P6_PERF_NEGATIVE_CONTROL__' WHERE proj_id=%s").format(target),
                (row_id,)).rowcount
            if changed != 1:
                raise AssertionError("Negative control did not update exactly one smoke row")
            count = pg.execute(sql.SQL("SELECT count(*) FROM {}").format(target)).fetchone()[0]
            if count != initial_count:
                raise AssertionError("Negative control changed row count")
        try:
            verify_table(config, "PROJECT", columns, keys, target_schema)
        except ValueError as error:
            if "Full-value verification failed" not in str(error) or "proj_short_name" not in str(error):
                raise
            detected = True
        if not detected:
            raise AssertionError("Same-row-count text corruption escaped full verification")
    finally:
        if row_id is not None:
            with psycopg.connect(**config["postgres"]) as pg:
                changed = pg.execute(sql.SQL("UPDATE {} SET proj_short_name=%s WHERE proj_id=%s").format(target),
                                     (original, row_id)).rowcount
                if changed != 1:
                    raise AssertionError("Could not restore exactly one smoke row")
            restored = True
    final_count = verify_table(config, "PROJECT", columns, keys, target_schema)
    if final_count != initial_count:
        raise AssertionError("Restored smoke table failed full row-count comparison")
    return {"status": "passed", "method": METHOD, "schema": EXPECTED_SCHEMA, "table": "project",
            "initial_rows_compared": initial_count, "corrupted_rows": 1, "row_count_unchanged": True,
            "same_row_count_corruption_detected": detected, "original_value_restored": restored,
            "restored_full_value_verification": "passed", "restored_rows_compared": final_count,
            "data_values_logged": False, "seconds": time.perf_counter()-started}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(json.loads(args.config.read_text()))
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
