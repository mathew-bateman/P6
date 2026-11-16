"""Exact parity checks for isolated P6 reporting performance trials.

Nothing is sampled or hashed: EXCEPT ALL compares all projected values and
duplicate multiplicities.  Ordinary text is compared as UTF-8 bytes so a
case-insensitive collation cannot hide a changed string.  The default is strict,
including run metadata.  Prefer fixing calculation time and run IDs in both
trials.  Explicit metadata normalization is available only for identified
calculation/publication fields; its use is recorded in every result.

Generation checks need both generations in the same isolated database.
Derived views can be captured before the trial changes its PRIVATE publication
pointer, then compared afterwards.  Capture refuses the production database
name and requires an explicit expected database name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import time

from psycopg import sql
from psycopg.rows import dict_row


DERIVED_SCHEMAS = (
    "coding_check", "construction_progress", "design_overview", "touchpoints",
)
SELECTION_TABLE = "_latest_client_submitted_projects_v1"
CONTROL_TABLES = tuple("xertoolkit_schedule_quality_" + suffix for suffix in (
    "profile", "config_version", "check_scope", "option", "constraint_type", "detail_field",
))
CALCULATION_METADATA = {
    "xertoolkit_vw_pbi_informationalresults": {"check_run_id", "refreshed_at"},
    "xertoolkit_vw_pbi_logicloops": {"check_run_id", "calculated_date"},
    "xertoolkit_vw_pbi_outofsequenceexceptions": {"check_run_id", "refreshed_at"},
    "xertoolkit_vw_pbi_schedulequalityresults": {"check_run_id", "refreshed_at"},
    "xertoolkit_vw_pbi_schedulequalitytaskevidence": {"check_run_id", "refreshed_at"},
}
PUBLICATION_METADATA = {"_batch_id", "_synced_at"}
_GENERATION = re.compile(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)\Z")
_CAPTURE = re.compile(r"parity_capture_[a-z0-9_]{1,60}\Z")


def capture_relation_name(schema, relation):
    """Keep a readable prefix and collision-resistant identity within 63 bytes."""
    full = schema + "__" + relation
    if len(full.encode("utf-8")) <= 63:
        return full
    suffix = "__" + hashlib.sha256(full.encode("utf-8")).hexdigest()[:20]
    limit = 63 - len(suffix)
    prefix = full.encode("utf-8")[:limit].decode("utf-8", errors="ignore")
    return prefix + suffix


def assert_isolated_database(pg, expected_database):
    """Prevent an accidentally redirected test from touching production."""
    with pg.cursor(row_factory=dict_row) as cur:
        actual = cur.execute("SELECT current_database() name").fetchone()["name"]
    if actual != expected_database or actual in {"p6_reporting", "postgres", "template0", "template1"}:
        raise ValueError("Parity trial requires the explicitly named isolated database")


def column_layout(pg, schema, relation):
    with pg.cursor(row_factory=dict_row) as cur:
        rows = cur.execute("""SELECT a.attname name,
            pg_catalog.format_type(a.atttypid,a.atttypmod) type,
            t.typcategory category
          FROM pg_attribute a
          JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_type t ON t.oid=a.atttypid
          WHERE n.nspname=%s AND c.relname=%s
            AND a.attnum>0 AND NOT a.attisdropped
          ORDER BY a.attnum""", (schema, relation)).fetchall()
    if not rows:
        raise ValueError(f"Missing or empty relation layout: {schema}.{relation}")
    return rows


def comparison_projection(layout, normalized_columns=()):
    """Make exact comparable fields; excluded metadata still compares nullness."""
    normalized = set(normalized_columns)
    unknown = normalized - {field["name"] for field in layout}
    if unknown:
        raise ValueError("Unknown normalized columns: " + ",".join(sorted(unknown)))
    fields = []
    for field in layout:
        name = field["name"]
        value = sql.Identifier("t", name)
        if name in normalized:
            value = sql.SQL("({} IS NULL)").format(value)
        elif field["category"] in {"S", "E"} or field["type"] in {"json", "xml"}:
            value = sql.SQL("convert_to({}::text,'UTF8')").format(value)
        fields.append(sql.SQL("{} AS {}").format(value, sql.Identifier(name)))
    return sql.SQL(",").join(fields)


def selection_predicate(schema):
    return sql.SQL(" WHERE EXISTS (SELECT 1 FROM {}.{} s WHERE s.proj_id=t.proj_id)").format(
        sql.Identifier(schema), sql.Identifier(SELECTION_TABLE))


def comparison_statement(reference, candidate, layout, *, scoped=False, normalized_columns=()):
    projection = comparison_projection(layout, normalized_columns)
    left_where = selection_predicate(reference[0]) if scoped else sql.SQL("")
    right_where = selection_predicate(candidate[0]) if scoped else sql.SQL("")
    return sql.SQL("""WITH reference_rows AS MATERIALIZED (
          SELECT {projection} FROM {rs}.{rt} t {rw}
        ), candidate_rows AS MATERIALIZED (
          SELECT {projection} FROM {cs}.{ct} t {cw}
        )
        SELECT
          (SELECT count(*) FROM reference_rows) reference_rows,
          (SELECT count(*) FROM candidate_rows) candidate_rows,
          (SELECT count(*) FROM (SELECT * FROM reference_rows
             EXCEPT ALL SELECT * FROM candidate_rows) d) reference_only_rows,
          (SELECT count(*) FROM (SELECT * FROM candidate_rows
             EXCEPT ALL SELECT * FROM reference_rows) d) candidate_only_rows
        """).format(projection=projection,
            rs=sql.Identifier(reference[0]), rt=sql.Identifier(reference[1]),
            cs=sql.Identifier(candidate[0]), ct=sql.Identifier(candidate[1]),
            rw=left_where, cw=right_where)


def compare_relation(pg, reference, candidate, *, scoped=False, normalized_columns=()):
    started = time.perf_counter()
    reference_layout = column_layout(pg, *reference)
    candidate_layout = column_layout(pg, *candidate)
    result = {"reference": ".".join(reference), "candidate": ".".join(candidate),
              "publication_scoped": scoped,
              "normalized_metadata_columns": sorted(normalized_columns),
              "schema_equal": reference_layout == candidate_layout}
    if not result["schema_equal"]:
        result.update(passed=False, reference_layout=reference_layout, candidate_layout=candidate_layout)
    else:
        statement = comparison_statement(reference, candidate, reference_layout,
            scoped=scoped, normalized_columns=normalized_columns)
        with pg.cursor(row_factory=dict_row) as cur:
            counts = cur.execute(statement).fetchone()
        result.update(counts)
        result["passed"] = (counts["reference_only_rows"] == counts["candidate_only_rows"] == 0
                            and counts["reference_rows"] == counts["candidate_rows"])
    result["seconds"] = round(time.perf_counter() - started, 6)
    return result


def compare_generations(pg, reference_schema, candidate_schema, source, contracts, raw_tables,
                        *, normalize_run_metadata=False):
    """Check all raw values, settings, selection and all 20 public contracts."""
    if not _GENERATION.fullmatch(reference_schema) or not _GENERATION.fullmatch(candidate_schema):
        raise ValueError("Use explicit private generation schemas")
    if source not in {"p6_22", "p6_23"} or not all(s.endswith(source) for s in (reference_schema,candidate_schema)):
        raise ValueError("Generation source mismatch")
    outputs = {name: columns for name, columns in contracts["published_by_source"][source].items()
               if name.startswith("xertoolkit_vw_")}
    if len(outputs) != 20:
        raise ValueError("Unexpected reporting contract count")
    raw_tables = {name.lower() for name in raw_tables}
    names = sorted(raw_tables | set(CONTROL_TABLES) | {SELECTION_TABLE} | set(outputs))
    results = []
    for name in names:
        columns = outputs.get(name, ())
        scoped = name.startswith("xertoolkit_vw_pbi_") and any(c["name"] == "proj_id" for c in columns)
        normalized = CALCULATION_METADATA.get(name, set()) if normalize_run_metadata else set()
        result = compare_relation(pg, (reference_schema,name), (candidate_schema,name),
            scoped=scoped, normalized_columns=normalized)
        result["kind"] = "raw" if name in raw_tables else "published_contract" if name in outputs else "control"
        results.append(result)
    return results


def list_derived_views(pg, schemas=DERIVED_SCHEMAS):
    with pg.cursor(row_factory=dict_row) as cur:
        return cur.execute("""SELECT schemaname,viewname FROM pg_views
            WHERE schemaname=ANY(%s) ORDER BY schemaname,viewname""", (list(schemas),)).fetchall()


def capture_derived_views(pg, capture_schema, expected_database, schemas=DERIVED_SCHEMAS):
    """Freeze complete derived report results before private pointer replacement."""
    assert_isolated_database(pg, expected_database)
    if not _CAPTURE.fullmatch(capture_schema):
        raise ValueError("Use a new parity_capture_<trial> schema")
    views = list_derived_views(pg, schemas)
    if not views:
        raise ValueError("No derived report views found")
    pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(capture_schema)))
    captured = []
    for view in views:
        target = capture_relation_name(view["schemaname"],view["viewname"])
        started = time.perf_counter()
        pg.execute(sql.SQL("CREATE TABLE {}.{} AS SELECT * FROM {}.{}").format(
            sql.Identifier(capture_schema), sql.Identifier(target),
            sql.Identifier(view["schemaname"]), sql.Identifier(view["viewname"])))
        captured.append({**view, "capture_relation": target,
                         "seconds": round(time.perf_counter()-started,6)})
    return {"capture_schema": capture_schema, "expected_database": expected_database, "views": captured}


def compare_derived_views(pg, capture, *, normalize_publication_metadata=False, progress=None):
    assert_isolated_database(pg, capture["expected_database"])
    actual = {(v["schemaname"],v["viewname"]) for v in list_derived_views(pg)}
    expected = {(v["schemaname"],v["viewname"]) for v in capture["views"]}
    if actual != expected:
        raise ValueError("Derived report view inventory changed")
    results = []
    for view in capture["views"]:
        left = (capture["capture_schema"],view["capture_relation"])
        right = (view["schemaname"],view["viewname"])
        normalized = set()
        if normalize_publication_metadata:
            normalized = PUBLICATION_METADATA & {c["name"] for c in column_layout(pg,*left)}
        result = compare_relation(pg,left,right,normalized_columns=normalized)
        results.append(result)
        if progress is not None:
            progress(result)
    return results


def main():
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True,
                        help="Private JSON dict of psycopg connect keyword arguments")
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    sub = parser.add_subparsers(dest="operation", required=True)
    generation = sub.add_parser("generations")
    generation.add_argument("--reference",required=True)
    generation.add_argument("--candidate",required=True)
    generation.add_argument("--source",choices=("p6_22","p6_23"),required=True)
    generation.add_argument("--contracts",type=Path,required=True)
    generation.add_argument("--raw-tables",nargs="+",required=True)
    generation.add_argument("--normalize-run-metadata",action="store_true")
    capture = sub.add_parser("capture-derived")
    capture.add_argument("--capture-schema",required=True)
    derived = sub.add_parser("compare-derived")
    derived.add_argument("--capture-manifest",type=Path,required=True)
    derived.add_argument("--normalize-publication-metadata",action="store_true")
    args = parser.parse_args()
    connection = json.loads(args.connection_file.read_text())
    with psycopg.connect(**connection) as pg:
        assert_isolated_database(pg,args.expected_database)
        pg.execute("SET LOCAL statement_timeout='30min'")
        if args.operation == "generations":
            results = compare_generations(pg,args.reference,args.candidate,args.source,
                json.loads(args.contracts.read_text()),args.raw_tables,
                normalize_run_metadata=args.normalize_run_metadata)
            output = {"passed":all(r["passed"] for r in results),"results":results}
        elif args.operation == "capture-derived":
            output = capture_derived_views(pg,args.capture_schema,args.expected_database)
        else:
            results = compare_derived_views(pg,json.loads(args.capture_manifest.read_text()),
                normalize_publication_metadata=args.normalize_publication_metadata)
            output = {"passed":all(r["passed"] for r in results),"results":results}
    args.output.write_text(json.dumps(output,indent=2)+"\n")
    print(json.dumps({"operation":args.operation,"passed":output.get("passed"),
                      "relations":len(output.get("results",output.get("views",())))}))
    if output.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
