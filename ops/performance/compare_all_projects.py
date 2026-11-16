"""Strict all-project acceptance and historical-view publication for isolated trials.

This deliberately never applies the latest-submission predicate to comparison
queries. Every original report column, duplicate and NULL is compared. It also
checks internal calculation tables, which the earlier scoped prototype omitted.
Only new *_history namespaces are published; existing latest-only UI/report
views remain the caller's responsibility and are not changed here.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import time

from psycopg import sql
from psycopg.rows import dict_row

try:
    from .compare_reports import (
        CONTROL_TABLES, SELECTION_TABLE, assert_isolated_database,
        column_layout, compare_relation, comparison_projection,
    )
except ImportError:
    from compare_reports import (
        CONTROL_TABLES, SELECTION_TABLE, assert_isolated_database,
        column_layout, compare_relation, comparison_projection,
    )


GENERATION = re.compile(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)\Z")
FUNCTION_TABLES = tuple("xertoolkit_fn_" + suffix for suffix in (
    "activity_quality", "relationship_quality", "open_ends", "out_of_sequence",
    "soft_constraints", "schedule_quality_task_evidence",
))
RESULT_TABLES = tuple("xertoolkit_result_" + suffix for suffix in (
    "logic_loop_tasks", "out_of_sequence_exceptions", "project_metrics",
    "schedule_quality_task_evidence",
))
HISTORY_SCHEMAS = {"p6_22": "p6_22_history", "p6_23": "p6_23_history"}
HISTORY_COMBINED = "reporting_history"


def legacy_view_inventory(retirement_plan, contracts):
    """Cross-check names against the captured pre-retirement SQL catalogue."""
    old = set()
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "V" and value.get("name"):
                old.add(value["name"].lower())
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(retirement_plan)
    sources = {}
    for source in HISTORY_SCHEMAS:
        current = {name for name in contracts["published_by_source"][source]
                   if name.startswith("xertoolkit_vw_")}
        sources[source] = {"contract_views": len(current), "missing_legacy_views": sorted(old-current),
                           "additional_contract_views": sorted(current-old)}
    return {"complete": True, "scope": "retired_SQL_reporting_views",
            "legacy_view_count": len(old), "legacy_views": sorted(old),
            "sources": sources,
            "passed": len(old) == 20 and all(not s["missing_legacy_views"] for s in sources.values())}


def require_isolated(pg, expected_database="p6_perf_trial"):
    if not expected_database.startswith("p6_perf_"):
        raise ValueError("All-project trials require a p6_perf_* database")
    assert_isolated_database(pg, expected_database)


def validate_generations(reference, candidate, source):
    if source not in HISTORY_SCHEMAS or not all(
        GENERATION.fullmatch(s) and s.endswith("_" + source)
        for s in (reference, candidate)
    ):
        raise ValueError("Use two explicit generations for the same P6 source")


def relation_inventory(source, contracts, raw_tables):
    """Return a complete, unfiltered 59-relation acceptance inventory/source."""
    outputs = {name for name in contracts["published_by_source"][source]
               if name.startswith("xertoolkit_vw_")}
    if len(outputs) != 20:
        raise ValueError("Expected all 20 reporting contracts")
    raw = {name.lower() for name in raw_tables}
    if len(raw) != 21:
        raise ValueError("Expected all 21 raw tables")
    groups = {
        "published_contract": outputs,
        "raw": raw,
        "settings": set(CONTROL_TABLES),
        "result": set(RESULT_TABLES),
        "function_materialization": set(FUNCTION_TABLES),
        "deleted_discovery": {"_deleted_activities"},
        "selection": {SELECTION_TABLE},
    }
    inventory = [(kind, name) for kind, names in groups.items() for name in sorted(names)]
    if len({name for _, name in inventory}) != len(inventory):
        raise ValueError("Acceptance inventory unexpectedly overlaps")
    return inventory


def compare_all_generations(pg, reference, candidate, source, contracts, raw_tables,
                            *, expected_database="p6_perf_trial", progress=None):
    require_isolated(pg, expected_database)
    validate_generations(reference, candidate, source)
    started = time.perf_counter()
    inventory = relation_inventory(source, contracts, raw_tables)
    results = []
    for kind, name in inventory:
        result = compare_relation(pg, (reference, name), (candidate, name),
                                  scoped=False, normalized_columns=())
        result["kind"] = kind
        results.append(result)
        if progress is not None:
            progress(result)
    counts = {}
    for schema in (reference, candidate):
        counts[schema] = int(pg.execute(sql.SQL("SELECT count(*) AS n FROM {}.project")
            .format(sql.Identifier(schema))).fetchone()["n"])
    coverage = {key: sum(kind == value for kind, _ in inventory) for key, value in (
        ("published_contracts", "published_contract"), ("raw_tables", "raw"),
        ("settings_tables", "settings"), ("result_tables", "result"),
        ("function_tables", "function_materialization"),
        ("deleted_discovery_tables", "deleted_discovery"), ("selection_tables", "selection"),
    )}
    return {"complete": True, "passed": all(r["passed"] for r in results),
            "scope": "all_projects", "source": source,
            "reference_schema": reference, "candidate_schema": candidate,
            "all_project_count": counts[candidate], "reference_project_count": counts[reference],
            "coverage": coverage, "metadata_exclusions": [], "publication_filter_applied": False,
            "seconds": time.perf_counter() - started, "results": results}


def history_select(generation, entry, source, snapshot, batch_id, published_at):
    """The normal metadata contract, intentionally without a project filter."""
    if source not in HISTORY_SCHEMAS or not GENERATION.fullmatch(generation):
        raise ValueError("Invalid historical source generation")
    names = {c["name"] for c in entry["columns"]}
    snapshot_at = snapshot["snapshot_at"]
    if isinstance(snapshot_at, str):
        snapshot_at = datetime.fromisoformat(snapshot_at)
    fields = [sql.SQL("t.*")]
    for name, value in (
        ("_source_system", source), ("_source_database", snapshot["database"]),
        ("_snapshot_at", snapshot_at), ("_synced_at", published_at),
        ("_batch_id", str(batch_id)),
    ):
        fields.append(sql.SQL("{} AS {}").format(sql.Literal(value), sql.Identifier(name)))
    fields.append(sql.SQL("{} AS _source_updated_at").format(
        sql.Identifier("t", "update_date") if "update_date" in names else sql.SQL("NULL::timestamp")))
    for field, alias in (("proj_id", "_project_key"), ("task_id", "_activity_key")):
        if field in names:
            fields.append(sql.SQL("{} || ':' || {}::text AS {}")
                .format(sql.Literal(source), sql.Identifier("t", field), sql.Identifier(alias)))
    return sql.SQL("SELECT {} FROM {}.{} t").format(sql.SQL(",").join(fields),
        sql.Identifier(generation), sql.Identifier(entry["name"]))


def validate_manifests(generations, manifests):
    if set(generations) != set(HISTORY_SCHEMAS) or set(manifests) != set(HISTORY_SCHEMAS):
        raise ValueError("Both P6 sources must publish together")
    for source, generation in generations.items():
        validate_generations(generation, generation, source)
        entries = manifests[source]
        names = [entry["name"] for entry in entries]
        if len(names) != len(set(names)) or len(names) != 47:
            raise ValueError("Expected exactly 47 distinct public relations/source")
        if sum(name.startswith("xertoolkit_vw_") for name in names) != 20:
            raise ValueError("Missing full historical reporting contracts")


def publish_history_views(pg, generations, manifests, snapshots, batch_id, published_at,
                          *, expected_database="p6_perf_trial",
                          reader_roles=("p6_reporting_reader", "p6_reporting_external")):
    """Publish full all-project views in the caller's atomic transaction."""
    require_isolated(pg, expected_database)
    validate_manifests(generations, manifests)
    by_name = {}
    for source in sorted(HISTORY_SCHEMAS):
        target_schema = HISTORY_SCHEMAS[source]
        pg.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(target_schema)))
        for entry in manifests[source]:
            query = history_select(generations[source], entry, source,
                                   snapshots[source], batch_id, published_at)
            pg.execute(sql.SQL("CREATE OR REPLACE VIEW {}.{} AS {}").format(
                sql.Identifier(target_schema), sql.Identifier(entry["name"]), query))
            by_name.setdefault(entry["name"], []).append((source, entry))
    pg.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(HISTORY_COMBINED)))
    for name, entries in sorted(by_name.items()):
        fields, types = [], {}
        for _, entry in entries:
            for col in entry["columns"]:
                if col["name"] not in fields:
                    fields.append(col["name"])
                    types[col["name"]] = col["type"]
        metadata = ["_source_system", "_source_database", "_snapshot_at", "_synced_at",
                    "_batch_id", "_source_updated_at"]
        if "proj_id" in fields:
            metadata.append("_project_key")
        if "task_id" in fields:
            metadata.append("_activity_key")
        queries = []
        for source, entry in entries:
            present = {c["name"] for c in entry["columns"]}
            projection = [sql.Identifier(c) if c in present else sql.SQL("NULL::{} AS {}")
                          .format(sql.SQL(types[c]), sql.Identifier(c)) for c in fields]
            projection.extend(sql.Identifier(c) for c in metadata)
            queries.append(sql.SQL("SELECT {} FROM {}.{}").format(sql.SQL(",").join(projection),
                sql.Identifier(HISTORY_SCHEMAS[source]), sql.Identifier(name)))
        pg.execute(sql.SQL("CREATE OR REPLACE VIEW {}.{} AS {}").format(
            sql.Identifier(HISTORY_COMBINED), sql.Identifier(name), sql.SQL(" UNION ALL ").join(queries)))
    for schema in (*HISTORY_SCHEMAS.values(), HISTORY_COMBINED):
        for role in reader_roles:
            pg.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}")
                .format(sql.Identifier(schema), sql.Identifier(role)))
            pg.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}")
                .format(sql.Identifier(schema), sql.Identifier(role)))
    return {"scope": "all_projects", "schemas": list(HISTORY_SCHEMAS.values()) + [HISTORY_COMBINED],
            "source_relations": sum(len(v) for v in manifests.values()),
            "combined_relations": len(by_name), "reader_roles": list(reader_roles)}


def _compare_query_to_relation(pg, query, target):
    layout = column_layout(pg, *target)
    projection = comparison_projection(layout)
    statement = sql.SQL("""WITH expected AS MATERIALIZED (
        SELECT {projection} FROM ({query}) t
      ), actual AS MATERIALIZED (
        SELECT {projection} FROM {schema}.{relation} t
      ) SELECT
        (SELECT count(*) FROM expected) reference_rows,
        (SELECT count(*) FROM actual) candidate_rows,
        (SELECT count(*) FROM (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual) d) reference_only_rows,
        (SELECT count(*) FROM (SELECT * FROM actual EXCEPT ALL SELECT * FROM expected) d) candidate_only_rows
    """).format(projection=projection, query=query,
        schema=sql.Identifier(target[0]), relation=sql.Identifier(target[1]))
    started = time.perf_counter()
    counts = pg.execute(statement).fetchone()
    return {"relation": ".".join(target), **counts,
            "passed": counts["reference_only_rows"] == counts["candidate_only_rows"] == 0
                      and counts["reference_rows"] == counts["candidate_rows"],
            "seconds": time.perf_counter() - started, "metadata_exclusions": [], "scope": "all_projects"}


def compare_history_availability(pg, generations, manifests, snapshots, batch_id, published_at,
                                 *, expected_database="p6_perf_trial",
                                 reader_roles=("p6_reporting_reader", "p6_reporting_external"), progress=None):
    """Prove full historical contract values and reader access to nonlatest data."""
    require_isolated(pg, expected_database)
    validate_manifests(generations, manifests)
    started = time.perf_counter()
    results, sources, latest_ids, layouts = [], {}, {}, []
    for source in sorted(HISTORY_SCHEMAS):
        generation, history = generations[source], HISTORY_SCHEMAS[source]
        expected_names = {entry["name"] for entry in manifests[source]}
        found = {r["viewname"] for r in pg.execute(
            "SELECT viewname FROM pg_views WHERE schemaname=%s", (history,)).fetchall()}
        if found != expected_names:
            raise ValueError("Historical view inventory differs from full generation manifest")
        for entry in manifests[source]:
            base_layout = column_layout(pg, generation, entry["name"])
            public_layout = column_layout(pg, history, entry["name"])
            metadata_names = ["_source_system", "_source_database", "_snapshot_at", "_synced_at",
                              "_batch_id", "_source_updated_at"]
            base_names = {field["name"] for field in base_layout}
            if "proj_id" in base_names:
                metadata_names.append("_project_key")
            if "task_id" in base_names:
                metadata_names.append("_activity_key")
            layout_passed = (public_layout[:len(base_layout)] == base_layout
                             and [field["name"] for field in public_layout[len(base_layout):]] == metadata_names)
            layouts.append({"source": source, "relation": entry["name"], "passed": layout_passed})
            # All raw/setting values are already independently checked; full
            # historical report contracts also compare publication metadata.
            if not entry["name"].startswith("xertoolkit_vw_"):
                continue
            result = _compare_query_to_relation(pg,
                history_select(generation, entry, source, snapshots[source], batch_id, published_at),
                (history, entry["name"]))
            results.append(result)
            if progress is not None:
                progress(result)
        project_count = pg.execute(sql.SQL("SELECT count(*) n FROM {}.project")
            .format(sql.Identifier(generation))).fetchone()["n"]
        latest_count = pg.execute(sql.SQL("SELECT count(*) n FROM {}.{}")
            .format(sql.Identifier(generation), sql.Identifier(SELECTION_TABLE))).fetchone()["n"]
        latest_ids[source] = [r["proj_id"] for r in pg.execute(sql.SQL("SELECT proj_id FROM {}.{}")
            .format(sql.Identifier(generation), sql.Identifier(SELECTION_TABLE))).fetchall()]
        metrics_count = pg.execute(sql.SQL("SELECT count(*) n FROM {}.xertoolkit_result_project_metrics")
            .format(sql.Identifier(generation))).fetchone()["n"]
        nonlatest_metrics = pg.execute(sql.SQL("""SELECT count(*) n
            FROM {}.xertoolkit_vw_pbi_projectmetrics m WHERE NOT EXISTS (
              SELECT 1 FROM {}.{} s WHERE s.proj_id=m.proj_id)""")
            .format(sql.Identifier(history), sql.Identifier(generation), sql.Identifier(SELECTION_TABLE))).fetchone()["n"]
        nonlatest_evidence = pg.execute(sql.SQL("""SELECT count(*) n
            FROM {}.xertoolkit_vw_pbi_schedulequalitytaskevidence e WHERE NOT EXISTS (
              SELECT 1 FROM {}.{} s WHERE s.proj_id=e.proj_id)""")
            .format(sql.Identifier(history), sql.Identifier(generation), sql.Identifier(SELECTION_TABLE))).fetchone()["n"]
        sources[source] = {"all_projects": project_count, "latest_projects": latest_count,
            "calculated_project_metrics": metrics_count, "nonlatest_project_metrics": nonlatest_metrics,
            "nonlatest_evidence_rows": nonlatest_evidence, "history_relations": len(found)}
    reader_checks = []
    # Query the actual historical quality/evidence views under each reader role;
    # no privileged generation/selection access is needed by the reader.
    for role in reader_roles:
        pg.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
        try:
            for source, history in HISTORY_SCHEMAS.items():
                metrics = pg.execute(sql.SQL("SELECT count(*) n FROM {}.xertoolkit_vw_pbi_projectmetrics")
                    .format(sql.Identifier(history))).fetchone()["n"]
                evidence = pg.execute(sql.SQL("SELECT count(*) n FROM {}.xertoolkit_vw_pbi_schedulequalitytaskevidence")
                    .format(sql.Identifier(history))).fetchone()["n"]
                historical_metrics = pg.execute(sql.SQL("SELECT count(*) n FROM {}.xertoolkit_vw_pbi_projectmetrics WHERE NOT COALESCE(proj_id=ANY(%s),false)")
                    .format(sql.Identifier(history)), (latest_ids[source],)).fetchone()["n"]
                historical_evidence = pg.execute(sql.SQL("SELECT count(*) n FROM {}.xertoolkit_vw_pbi_schedulequalitytaskevidence WHERE NOT COALESCE(proj_id=ANY(%s),false)")
                    .format(sql.Identifier(history)), (latest_ids[source],)).fetchone()["n"]
                reader_checks.append({"role": role, "source": source, "scope": "all_projects",
                    "quality_rows": metrics, "evidence_rows": evidence,
                    "nonlatest_quality_rows": historical_metrics,
                    "nonlatest_evidence_rows": historical_evidence,
                    "passed": metrics == sources[source]["all_projects"]
                        and historical_metrics == sources[source]["nonlatest_project_metrics"]
                        and historical_evidence == sources[source]["nonlatest_evidence_rows"]
                        and evidence >= historical_evidence})
            combined = pg.execute(sql.SQL("SELECT _source_system,count(*) n FROM {}.xertoolkit_vw_pbi_projectmetrics GROUP BY _source_system")
                .format(sql.Identifier(HISTORY_COMBINED))).fetchall()
            combined_counts = {r["_source_system"]: r["n"] for r in combined}
            reader_checks.append({"role": role, "source": "combined", "scope": "all_projects",
                "quality_rows_by_source": combined_counts,
                "passed": combined_counts == {s: v["all_projects"] for s, v in sources.items()}})
        finally:
            pg.execute("RESET ROLE")
    combined_names = {r["viewname"] for r in pg.execute(
        "SELECT viewname FROM pg_views WHERE schemaname=%s", (HISTORY_COMBINED,)).fetchall()}
    expected_combined = {entry["name"] for entries in manifests.values() for entry in entries}
    inventory_passed = combined_names == expected_combined
    all_projects = sum(s["all_projects"] for s in sources.values())
    latest_projects = sum(s["latest_projects"] for s in sources.values())
    passed = (all(r["passed"] for r in results) and all(r["passed"] for r in reader_checks)
              and all(r["passed"] for r in layouts)
              and inventory_passed and all(s["calculated_project_metrics"] == s["all_projects"]
                  and s["nonlatest_project_metrics"] == s["all_projects"] - s["latest_projects"]
                  for s in sources.values()))
    return {"complete": True, "passed": passed, "scope": "all_projects",
            "all_project_count": all_projects, "latest_project_count": latest_projects,
            "nonlatest_project_count": all_projects - latest_projects,
            "sources": sources, "reader_checks": reader_checks, "column_layout_checks": layouts,
            "combined_history_relations": len(combined_names),
            "combined_inventory_passed": inventory_passed,
            "metadata_exclusions": [], "publication_filter_applied": False,
            "seconds": time.perf_counter() - started, "results": results}


def main():
    import psycopg
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path, required=True)
    parser.add_argument("--expected-database", default="p6_perf_trial")
    parser.add_argument("--reference", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--source", choices=tuple(HISTORY_SCHEMAS), required=True)
    parser.add_argument("--contracts", type=Path, required=True)
    parser.add_argument("--raw-tables", nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    results = []
    def progress(result):
        results.append(result)
        args.output.write_text(json.dumps({"complete": False, "scope": "all_projects", "results": results}, indent=2) + "\n")
        print(result["candidate"], result["passed"], result["seconds"], flush=True)
    with psycopg.connect(**json.loads(args.connection_file.read_text()), row_factory=dict_row) as pg:
        pg.execute("SET LOCAL work_mem='128MB'")
        pg.execute("SET LOCAL statement_timeout='30min'")
        receipt = compare_all_generations(pg, args.reference, args.candidate, args.source,
            json.loads(args.contracts.read_text()), args.raw_tables,
            expected_database=args.expected_database, progress=progress)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"passed": receipt["passed"], "scope": receipt["scope"], "relations": len(receipt["results"])}))
    raise SystemExit(0 if receipt["passed"] else 1)


if __name__ == "__main__":
    main()
