"""Isolated experiment: calculate public quality outputs for selected projects.

This module does not change source tables, settings, schedules or public views.
It deliberately keeps the full raw generation and uses the owning application's
latest-submission selection query unchanged.  Run only in a p6_perf_* database.
"""
from __future__ import annotations

import re
from contextlib import ExitStack
from time import perf_counter
from unittest.mock import patch


SCOPE_TABLE = "_perf_selected_projects_v1"
VOLATILE_OUTPUT_COLUMNS = frozenset({"check_run_id", "refreshed_at", "calculated_date"})
_SCHEMA = re.compile(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)")


def _identifier(value):
    return '"' + value.replace('"', '""') + '"'


def _scalar(row, key):
    return row[key] if hasattr(row, "keys") else row[0]


def require_isolated(pg, *schemas):
    database = _scalar(pg.execute("SELECT current_database() AS database").fetchone(), "database")
    if not database.startswith("p6_perf_"):
        raise RuntimeError("Performance experiments require an isolated p6_perf_* database.")
    for schema in schemas:
        if not _SCHEMA.fullmatch(schema):
            raise ValueError("Use an explicit private native generation schema.")


def prepare_scope(pg, schema):
    from psycopg import sql
    from reporting_mirror.latest_submission import selection_query

    table = sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(SCOPE_TABLE))
    pg.execute(sql.SQL("CREATE TABLE {} AS {}").format(table, selection_query(schema)))
    pg.execute(sql.SQL("CREATE UNIQUE INDEX ON {} (proj_id)").format(table))
    pg.execute(sql.SQL("CREATE UNIQUE INDEX ON {} (project_identity)").format(table))
    pg.execute(sql.SQL("ANALYZE {}").format(table))


def narrow_query(query, schema):
    """Filter output rows while retaining full raw input/context for semantics."""
    if not _SCHEMA.fullmatch(schema):
        raise ValueError("Use an explicit private native generation schema.")
    target = _identifier(schema) + "." + _identifier(SCOPE_TABLE)
    return (
        "SELECT perf_output.* FROM (" + query.rstrip().rstrip(";") + ") AS perf_output "
        "WHERE EXISTS (SELECT 1 FROM " + target
        + " AS perf_selected WHERE perf_selected.proj_id=perf_output.proj_id)"
    )


def push_scope_into_raw_reads(query, schema):
    """Restrict quality-query inputs without changing physical raw tables.

    Project metadata and global deleted-code discovery must not use this helper.
    Activity assignment scope follows TASK.task_id (not TASKACTV.proj_id), so an
    assignment's existing source-project metadata cannot change its meaning.
    Every lookup within a selected project retains all rows, deleted or live.
    """
    if not _SCHEMA.fullmatch(schema):
        raise ValueError("Use an explicit private native generation schema.")
    quoted = _identifier(schema)
    selected = quoted + "." + _identifier(SCOPE_TABLE)
    pattern = re.compile(
        r"\b(FROM|JOIN)\s+" + re.escape(quoted)
        + r'\.(?:"(task|taskpred|projwbs|taskactv)"|(task|taskpred|projwbs|taskactv))(?![A-Za-z0-9_])',
        re.IGNORECASE,
    )

    def replace(match):
        table = (match.group(2) or match.group(3)).lower()
        relation = quoted + "." + _identifier(table)
        if table == "taskactv":
            predicate = (
                "EXISTS (SELECT 1 FROM " + quoted + '."task" AS perf_scope_task '
                "JOIN " + selected + " AS perf_scope_project ON perf_scope_project.proj_id=perf_scope_task.proj_id "
                "WHERE perf_scope_task.task_id=perf_raw.task_id)"
            )
        else:
            predicate = "EXISTS (SELECT 1 FROM " + selected + " AS perf_scope_project WHERE perf_scope_project.proj_id=perf_raw.proj_id)"
        return match.group(1) + " (SELECT perf_raw.* FROM " + relation + " AS perf_raw WHERE " + predicate + ")"

    return pattern.sub(replace, query)


def _scoped_materialization(engine, name, contract):
    if name == "xertoolkit_vw_pbi_projects":
        return False  # Full metadata is needed to choose latest submissions.
    if name in {"xertoolkit_fn_" + suffix for suffix in engine.FUNCTION_TABLES}:
        return True
    return bool(name.startswith("xertoolkit_vw_pbi_") and contract and any(
        column["name"] == "proj_id" for column in contract
    ))


def calculate_scoped(pg, schema, config_version_id, check_run_id, *, progress=lambda message: None, engine=None, push_raw_scope=True):
    """Return normal calculate() outputs plus timings; never publish a batch.

    Run in its own process: temporary module patches are process-global.  Raw
    TASK/TASKPRED and global codes remain intact, including external/deleted
    activity context used by the original check definitions.
    """
    require_isolated(pg, schema)
    if engine is None:
        from reporting_mirror import native_engine as engine
    original_materialize = engine.materialize
    original_definition = getattr(engine, "query_definition", None)
    timings = []
    scope_ready = False

    def materialize(connection, generation, name, query, *, contract=None, materialized_view=False):
        nonlocal scope_ready
        if generation != schema:
            raise RuntimeError("Scoped calculation unexpectedly changed generation.")
        scoped = _scoped_materialization(engine, name, contract)
        if scoped:
            if not scope_ready:
                raise RuntimeError("Project selection must precede scoped calculations.")
            query = narrow_query(query, schema)
        started = perf_counter()
        output = original_materialize(
            connection, generation, name, query,
            contract=contract, materialized_view=materialized_view,
        )
        timings.append({"stage": name, "seconds": perf_counter() - started, "scoped": scoped})
        if name == "xertoolkit_vw_pbi_projects":
            started = perf_counter()
            prepare_scope(connection, schema)
            timings.append({"stage": "latest_submission_selection", "seconds": perf_counter() - started})
            scope_ready = True
        return output

    def query_definition(name, generation, version):
        query = original_definition(name, generation, version)
        quality_query = name != "xertoolkit_vw_pbi_projects" and (
            name.startswith("xertoolkit_vw_pbi_")
            or name in {"xertoolkit_fn_" + suffix for suffix in engine.FUNCTION_TABLES}
        )
        if push_raw_scope and quality_query:
            if not scope_ready:
                raise RuntimeError("Project selection must precede scoped raw reads.")
            query = push_scope_into_raw_reads(query, generation)
        return query

    base_views = ["projects"] + [suffix for suffix in engine.BASE_VIEWS if suffix != "projects"]
    with ExitStack() as stack:
        stack.enter_context(patch.object(engine, "BASE_VIEWS", base_views))
        stack.enter_context(patch.object(engine, "materialize", materialize))
        if original_definition is not None:
            stack.enter_context(patch.object(engine, "query_definition", query_definition))
        manifest, quality = engine.calculate(
            pg, schema, config_version_id, check_run_id, progress=progress,
        )
    if not scope_ready:
        raise RuntimeError("Native calculation never prepared project selection.")
    quality = dict(quality, performance_scope="latest_submitted_quality_trial", raw_scope_pushdown=push_raw_scope, stage_timings=timings)
    quality["selected_project_count"] = _scalar(pg.execute(
        "SELECT count(*) AS n FROM " + _identifier(schema) + "." + _identifier(SCOPE_TABLE)
    ).fetchone(), "n")
    return manifest, quality


def compare_public_contracts(pg, reference_schema, candidate_schema, *, contracts=None, ignore_generated=True):
    """Exact duplicate-preserving comparison of all published report values.

    Both sides use the production latest-submission policy. Only three explicit
    run-generated IDs/timestamps are excluded. Source dates, settings, decimals,
    text and NULLs are compared. EXCEPT ALL preserves duplicate multiplicities.
    This does not replace independent SQL-to-PostgreSQL raw-value verification.
    """
    from psycopg import sql
    from reporting_mirror.latest_submission import selection_query, scopes_to_latest_submission

    require_isolated(pg, reference_schema, candidate_schema)
    if reference_schema[-5:] != candidate_schema[-5:]:
        raise ValueError("Compare the same P6 source on both sides.")
    if contracts is None:
        from reporting_mirror.native_engine import CONTRACTS
        contracts = CONTRACTS["published_by_source"]["p6_22" if reference_schema.endswith("p6_22") else "p6_23"]
    report_contracts = {name: columns for name, columns in contracts.items() if name.startswith("xertoolkit_vw_")}
    if len(report_contracts) != 20:
        raise RuntimeError("Expected all 20 published native report contracts.")

    # Independent selection must itself be identical, not just resulting reports.
    selected = [selection_query(schema) for schema in (reference_schema, candidate_schema)]
    selection_differences = _scalar(pg.execute(sql.SQL(
        "SELECT count(*) AS n FROM ((SELECT * FROM ({a}) x EXCEPT ALL SELECT * FROM ({b}) y) "
        "UNION ALL (SELECT * FROM ({b}) x EXCEPT ALL SELECT * FROM ({a}) y)) d"
    ).format(a=selected[0], b=selected[1])).fetchone(), "n")
    if selection_differences:
        raise RuntimeError("Candidate latest-submission selection differs from reference.")

    results = []
    for name, columns in report_contracts.items():
        excluded = VOLATILE_OUTPUT_COLUMNS if ignore_generated else frozenset()
        compared = [column["name"] for column in columns if column["name"] not in excluded]
        names = sql.SQL(",").join(sql.Identifier("v", name) for name in compared)
        queries = []
        for schema, selection in zip((reference_schema, candidate_schema), selected):
            query = sql.SQL("SELECT {} FROM {}.{} v").format(names, sql.Identifier(schema), sql.Identifier(name))
            if scopes_to_latest_submission({"name": name, "columns": columns}):
                query += sql.SQL(" WHERE EXISTS (SELECT 1 FROM ({}) selected WHERE selected.proj_id=v.proj_id)").format(selection)
            queries.append(query)
        counts = [int(_scalar(pg.execute(sql.SQL("SELECT count(*) AS n FROM ({}) q").format(query)).fetchone(), "n")) for query in queries]
        differences = int(_scalar(pg.execute(sql.SQL(
            "SELECT count(*) AS n FROM (({a} EXCEPT ALL {b}) UNION ALL ({b} EXCEPT ALL {a})) d"
        ).format(a=queries[0], b=queries[1])).fetchone(), "n"))
        result = {"name": name, "reference_rows": counts[0], "candidate_rows": counts[1],
                  "different_rows": differences, "compared_columns": compared,
                  "excluded_run_columns": [c["name"] for c in columns if c["name"] in excluded]}
        results.append(result)
    if any(row["different_rows"] or row["reference_rows"] != row["candidate_rows"] for row in results):
        error = RuntimeError("Published report parity failed.")
        error.results = results
        raise error
    return {"status": "passed", "contracts_checked": len(results), "selection_different_rows": 0, "contracts": results}
