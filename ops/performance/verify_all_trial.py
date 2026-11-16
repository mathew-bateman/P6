"""Offline acceptance of an all-project trial; never run during its stopwatch.

Runs complete reference comparisons, historical reader availability, existing
filtered contract parity and derived-report parity. No fields are normalized.
Receipts are persisted incrementally so an interrupted long query cannot be
mistaken for a completed acceptance run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
import time

sys.path[:0] = ["/app", str(Path(__file__).parent)]
import django
django.setup()

from reporting_mirror.connections import postgres_connection
from reporting_mirror.native_engine import CONTRACTS
from reporting_mirror.sql_staging import RAW_TABLES
from compare_all_projects import (
    compare_all_generations, compare_history_availability, require_isolated,
)
from compare_reports import (
    SELECTION_TABLE, compare_relation, compare_derived_views,
)


ARTIFACTS = Path("/bench/artifacts")


def write(name, value):
    (ARTIFACTS / name).write_text(json.dumps(value, indent=2, default=str) + "\n")


def progress_writer(name, *, scope, extra=None):
    results = []
    def progress(result):
        results.append(result)
        write(name, {"complete": False, "scope": scope, "results": results, **(extra or {})})
        relation = result.get("candidate", result.get("relation", "unknown"))
        print("PARITY_PROGRESS", relation, result["passed"],
              round(result.get("seconds", 0), 3), flush=True)
    return progress


def raw_index_policy(pg, references, generations):
    """Exact legacy raw index signatures, excluding generated index names."""
    rows = []
    def signatures(schema):
        result = pg.execute("""SELECT t.relname relation,i.indisunique unique_index,
            i.indisvalid valid,i.indisready ready,
            split_part(pg_get_indexdef(i.indexrelid),' USING ',2) definition
          FROM pg_index i JOIN pg_class t ON t.oid=i.indrelid
          JOIN pg_namespace n ON n.oid=t.relnamespace
          WHERE n.nspname=%s AND t.relname=ANY(%s)
          ORDER BY t.relname,definition""", (schema,sorted(name.lower() for name in RAW_TABLES))).fetchall()
        return {name: [r for r in result if r["relation"]==name]
                for name in sorted(table.lower() for table in RAW_TABLES)}
    for source in sorted(generations):
        left,right=signatures(references[source]),signatures(generations[source])
        for name in sorted(left):
            valid=all(r["valid"] and r["ready"] and not r["unique_index"] for r in right[name])
            rows.append({"source":source,"relation":name,"passed":left[name]==right[name] and valid,
                         "reference":left[name],"candidate":right[name]})
    return {"complete":True,"scope":"all_projects","passed":len(rows)==42 and all(r["passed"] for r in rows),
            "results":rows}


def verify(tag):
    if not re.fullmatch(r"[A-Za-z0-9_]+", tag):
        raise ValueError("Use an explicit simple trial tag")
    timed = json.loads((ARTIFACTS / (tag + "_end_to_end.json")).read_text())
    if timed.get("calculation_scope") != "all_projects" or timed.get("project_scope_restricted") is not False:
        raise ValueError("Cannot accept a scoped/latest-only timing receipt")
    generations = {source: result["candidate_schema"]
                   for source, result in timed["calculation"]["sources"].items()}
    manifests = {source: result["manifest"]
                 for source, result in timed["calculation"]["sources"].items()}
    stages = {}
    with postgres_connection() as pg:
        require_isolated(pg)
        pg.execute("SET LOCAL work_mem='128MB'")
        pg.execute("SET LOCAL statement_timeout='30min'")
        baseline = pg.execute("SELECT * FROM reporting.refresh_status").fetchone()
        references = {source: "load_" + baseline["batch_id"].hex[:16] + "_" + source
                      for source in generations}
        indices=raw_index_policy(pg,references,generations)
        write("all_scope_index_policy_"+tag+".json",indices)
        stages["raw_index_policy"]=indices["passed"]
        print("ALL_SCOPE_INDEX_POLICY",indices["passed"],len(indices["results"]),flush=True)
        for source in sorted(generations):
            filename = "all_scope_parity_" + tag + "_" + source + ".json"
            result = compare_all_generations(pg, references[source], generations[source], source,
                CONTRACTS, RAW_TABLES,
                progress=progress_writer(filename, scope="all_projects", extra={"source": source}))
            write(filename, result)
            stages[source + "_full_reference"] = result["passed"]
            print("ALL_SCOPE_SOURCE", source, result["passed"], len(result["results"]), flush=True)
        filename = "historical_availability_" + tag + ".json"
        result = compare_history_availability(pg, generations, manifests, baseline["sources"],
            baseline["batch_id"], baseline["published_at"],
            progress=progress_writer(filename, scope="all_projects"))
        write(filename, result)
        stages["historical_availability"] = result["passed"]
        print("HISTORICAL_AVAILABILITY", result["passed"], result["all_project_count"],
              result["nonlatest_project_count"], flush=True)

        # Preserve the existing latest-only reports as well as exposing the full
        # historical views. This comparison intentionally has a distinct scope.
        filename = "all_scope_filtered_parity_" + tag + ".json"
        filtered, started = [], time.perf_counter()
        progress = progress_writer(filename, scope="existing_filtered_reports")
        for source in sorted(generations):
            for name, columns in CONTRACTS["published_by_source"][source].items():
                if not name.startswith("xertoolkit_vw_"):
                    continue
                scoped = name.startswith("xertoolkit_vw_pbi_") and any(c["name"] == "proj_id" for c in columns)
                comparison = compare_relation(pg, (references[source], name), (generations[source], name),
                                              scoped=scoped, normalized_columns=())
                comparison["source"] = source
                filtered.append(comparison)
                progress(comparison)
            comparison = compare_relation(pg, (references[source], SELECTION_TABLE),
                (generations[source], SELECTION_TABLE), scoped=False, normalized_columns=())
            comparison["source"] = source
            filtered.append(comparison)
            progress(comparison)
        passed = all(result["passed"] for result in filtered)
        write(filename, {"complete": True, "passed": passed, "scope": "existing_filtered_reports",
                         "metadata_exclusions": [], "seconds": time.perf_counter() - started,
                         "results": filtered})
        stages["existing_filtered_reports"] = passed

        filename = "all_scope_derived_parity_" + tag + ".json"
        capture = json.loads((ARTIFACTS / "derived_baseline.json").read_text())
        started = time.perf_counter()
        derived = compare_derived_views(pg, capture, normalize_publication_metadata=False,
            progress=progress_writer(filename, scope="existing_derived_reports"))
        passed = all(result["passed"] for result in derived)
        write(filename, {"complete": True, "passed": passed, "scope": "existing_derived_reports",
                         "metadata_exclusions": [], "seconds": time.perf_counter() - started,
                         "results": derived})
        stages["existing_derived_reports"] = passed
    result = {"complete": True, "scope": "all_projects", "passed": all(stages.values()), "gates": stages}
    write("all_scope_qa_" + tag + ".json", result)
    print("ALL_SCOPE_QA_COMPLETE", json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("tag")
    args = parser.parse_args()
    receipt = verify(args.tag)
    raise SystemExit(0 if receipt["passed"] else 1)
