"""Remove only the experimental raw UNIQUE-PK indexes in explicit private trials.

This repairs the first benchmark policy. It never changes published baseline
load_* schemas, live databases, calculation tables, constraints or source SQL.
The caller must wait for isolated derived QA to be cancelled before running it.
"""
import argparse
from contextlib import closing
import json
from pathlib import Path
import re

import mssql_python
import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from arrow_transfer import metadata, pg_name


PRIVATE = re.compile(r"native_shadow_[0-9a-f]{16}_p6_(?:22|23)\Z")


def fix(config, schemas):
    if config.get("benchmark_only") is not True:
        raise ValueError("Require benchmark_only=true")
    suffix = "_" + config["source_code"]
    if not schemas or len(set(schemas)) != len(schemas) or any(
        not PRIVATE.fullmatch(s) or not s.endswith(suffix) for s in schemas
    ):
        raise ValueError("Use explicit distinct native_shadow schemas of the configured source")
    result = []
    with closing(mssql_python.connect(config["source_connection_string"])) as source:
        with psycopg.connect(**config["postgres"], row_factory=dict_row) as pg:
            if not pg.execute("SHOW cluster_name").fetchone()["cluster_name"].startswith("p6-perf"):
                raise ValueError("Refusing changes outside the isolated performance cluster")
            for schema in schemas:
                for table in config["tables"]:
                    columns, source_keys = metadata(source, table)
                    names = [pg_name(c["name"]) for c in columns]
                    keys = [pg_name(k) for k in source_keys]
                    indexes = pg.execute("""SELECT x.relname name, i.indisunique, i.indisprimary,
                        i.indexprs IS NOT NULL expression, i.indpred IS NOT NULL partial,
                        array(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(attnum,ord)
                          JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum=k.attnum
                          ORDER BY k.ord) columns,
                        EXISTS(SELECT 1 FROM pg_constraint c WHERE c.conindid=x.oid) owned_constraint
                        FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
                        JOIN pg_class x ON x.oid=i.indexrelid
                        JOIN pg_namespace n ON n.oid=t.relnamespace
                        WHERE n.nspname=%s AND t.relname=%s""", (schema, table.lower())).fetchall()
                    # Exactly one generated UNIQUE index, on the source's full PK,
                    # was added by the first native experiment. Any unexpected
                    # unique raw index or dependency aborts the entire transaction.
                    unique = [i for i in indexes if i["indisunique"]]
                    for index in unique:
                        if (index["indisprimary"] or index["expression"] or index["partial"]
                            or index["owned_constraint"] or index["columns"] != keys):
                            raise ValueError(f"Unexpected unique raw index in {schema}.{table}")
                    if len(unique) > 1:
                        raise ValueError(f"Unexpected duplicate unique raw indexes in {schema}.{table}")
                    dropped = []
                    for index in unique:
                        pg.execute(sql.SQL("DROP INDEX {}.{}").format(sql.Identifier(schema), sql.Identifier(index["name"])))
                        dropped.append(index["name"])
                    # The old experiment skipped a plain index when the complete
                    # PK was exactly that field. Recreate that legacy field index.
                    created = []
                    for field in ("proj_id", "task_id", "rsrc_id", "config_version_id"):
                        if field not in names:
                            continue
                        exists = any(not i["indisunique"] and not i["expression"] and not i["partial"]
                                     and i["columns"] == [field] for i in indexes)
                        if not exists:
                            pg.execute(sql.SQL("CREATE INDEX ON {}.{} ({})").format(
                                sql.Identifier(schema), sql.Identifier(table.lower()), sql.Identifier(field)))
                            created.append(field)
                    pg.execute(sql.SQL("ANALYZE {}.{}").format(sql.Identifier(schema), sql.Identifier(table.lower())))
                    result.append({"schema": schema, "table": table.lower(), "dropped_unique_indexes": dropped,
                                   "created_legacy_field_indexes": created})
    return {"status": "repaired", "policy": "original_nonunique_raw_indexes", "tables": result}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--schemas", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = fix(json.loads(args.config.read_text()), args.schemas)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps({"status": result["status"], "policy": result["policy"], "tables": len(result["tables"])}))


if __name__ == "__main__":
    main()
