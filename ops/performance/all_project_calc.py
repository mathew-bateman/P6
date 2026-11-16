"""Measure complete all-project native calculations in an isolated database.

No selection predicates or project exclusions are installed here. The default
uses the original SQL; an optional exact deleted-endpoint join optimization
changes evaluation strategy only. The native engine builds every programme, with its
normal reconciliation checks. Temporary function wrappers only record timings.
Run one source per process: instrumentation temporarily patches module globals.
"""
from __future__ import annotations

from contextlib import ExitStack
from functools import wraps
import re
import hashlib
import json
from time import perf_counter
from unittest.mock import patch


_GENERATION = re.compile(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)\Z")


def _scalar(row, name):
    return row[name] if hasattr(row, "keys") else row[0]


def require_isolated(pg, schema):
    database = _scalar(pg.execute("SELECT current_database() AS database").fetchone(), "database")
    if not database.startswith("p6_perf_"):
        raise RuntimeError("All-project experiments require an isolated p6_perf_* database.")
    if not _GENERATION.fullmatch(schema):
        raise ValueError("Use an explicit private native generation schema.")


def _bounded_integer(value, *, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value



def rewrite_oos_deleted_lookup(query, schema):
    """Replace one correlated endpoint lookup with two exact-key LEFT JOINs.

    The caller proves the deleted endpoint relation has unique (proj_id,task_id)
    pairs. Both rewrites retain every relationship and all original project,
    activity, configuration and completion filters. Unexpected SQL fails closed.
    """
    if not _GENERATION.fullmatch(schema):
        raise ValueError("Use an explicit private native generation schema.")
    endpoint = re.compile(
        r"OR\s+NOT\s+EXISTS\(\s*SELECT\s+1\s+"
        r"FROM\s+deleted_activities\s+AS\s+deleted\s+WHERE\s+"
        r"deleted\.proj_id\s*=\s*r\.proj_id\s+AND\s+"
        r"deleted\.task_id\s+IN\s*\(\s*r\.predecessor_task_id\s*,\s*r\.successor_task_id\s*\)\s*\)",
        re.IGNORECASE,
    )
    if len(endpoint.findall(query)) != 1:
        raise ValueError("Expected exactly one original out-of-sequence deleted-endpoint lookup")
    if len(re.findall(r"^WHERE\s*$", query, flags=re.MULTILINE)) != 1:
        raise ValueError("Expected exactly one top-level out-of-sequence WHERE clause")
    if "deleted_predecessor" in query or "deleted_successor" in query:
        raise ValueError("Out-of-sequence query already contains optimized endpoint aliases")
    deleted_cte = re.compile(r"\bWITH\s+deleted_activities\s+AS\s*\(", re.IGNORECASE)
    if len(deleted_cte.findall(query)) != 1:
        raise ValueError("Expected one original deleted-activity CTE")
    # The CTE reads one immutable, DISTINCT table. Inlining both references is
    # semantically identical and retains its (proj_id,task_id) lookup indexes;
    # default materialization can otherwise cause huge repeated CTE scans.
    query = deleted_cte.sub("WITH deleted_activities AS NOT MATERIALIZED (", query, count=1)
    query = endpoint.sub(
        "OR (deleted_predecessor.task_id IS NULL AND deleted_successor.task_id IS NULL)", query,
    )
    joins = (
        "LEFT JOIN deleted_activities AS deleted_predecessor\n"
        "  ON deleted_predecessor.proj_id=r.proj_id\n"
        "  AND deleted_predecessor.task_id=r.predecessor_task_id\n"
        "LEFT JOIN deleted_activities AS deleted_successor\n"
        "  ON deleted_successor.proj_id=r.proj_id\n"
        "  AND deleted_successor.task_id=r.successor_task_id\n"
        "WHERE"
    )
    return re.sub(r"^WHERE\s*$", lambda _: joins, query, count=1, flags=re.MULTILINE)


def _assert_deleted_pair_uniqueness(pg, schema):
    quoted = '\"' + schema + '\"'
    row = pg.execute(f"""SELECT count(*) AS duplicates FROM (
        SELECT proj_id,task_id FROM {quoted}._deleted_activities
        GROUP BY proj_id,task_id HAVING count(*)>1
      ) duplicated_pairs""").fetchone()
    if _scalar(row, "duplicates"):
        raise RuntimeError("Deleted-activity pairs must be unique for exact endpoint joins.")



def _helper_catalog(pg, schema):
    collations = pg.execute("""SELECT to_jsonb(c)-'oid'-'collnamespace'-'collowner' AS attributes
        FROM pg_collation c JOIN pg_namespace n ON n.oid=c.collnamespace
        WHERE n.nspname=%s AND c.collname='p6_text_ci'""", (schema,)).fetchall()
    functions = pg.execute("""SELECT p.proname AS name,
        pg_get_function_identity_arguments(p.oid) AS arguments,
        pg_get_function_result(p.oid) AS result,
        pg_get_functiondef(p.oid) AS definition,
        to_jsonb(p)-'oid'-'pronamespace'-'proowner' AS attributes
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname=%s AND p.proname IN ('p6_try_date','p6_add_days')
        ORDER BY p.proname,p.oid""", (schema,)).fetchall()
    if len(collations) != 1 or len(functions) != 2 or {r["name"] for r in functions} != {"p6_try_date","p6_add_days"}:
        raise RuntimeError("Expected one ICU helper collation and exactly two helper functions.")
    normalized = []
    for row in functions:
        row = dict(row)
        row["attributes"] = dict(row["attributes"])
        # Generation identifiers differ, while all types, flags, configuration
        # and executable bodies must be identical to the baseline helpers.
        for key in ("definition",):
            row[key] = row[key].replace('"'+schema+'"', '"__generation__"').replace(schema, '__generation__')
        for key in ("prosrc",):
            if isinstance(row["attributes"].get(key),str):
                row["attributes"][key] = row["attributes"][key].replace('"'+schema+'"','"__generation__"').replace(schema,'__generation__')
        normalized.append(row)
    return {"collations":[dict(r["attributes"]) for r in collations],"functions":normalized}


def verify_precreated_helpers(pg, schema, reference_schema=None):
    """Verify committed helpers exactly, without reacquiring CREATE locks."""
    require_isolated(pg, schema)
    if reference_schema is None:
        row = pg.execute("SELECT batch_id::text AS batch_id FROM reporting.refresh_status").fetchone()
        batch_id = _scalar(row,"batch_id").replace('-','')
        if not re.fullmatch(r"[0-9a-f]{32}",batch_id):
            raise ValueError("Invalid baseline publication identifier")
        reference_schema = "load_" + batch_id[:16] + "_" + schema[-5:]
    if (not _GENERATION.fullmatch(reference_schema) or reference_schema == schema
        or reference_schema[-5:] != schema[-5:]):
        raise ValueError("Use a distinct baseline generation of the same P6 source.")
    reference = _helper_catalog(pg,reference_schema)
    candidate = _helper_catalog(pg,schema)
    if candidate != reference:
        raise RuntimeError("Precreated helper definitions or collation differ from the baseline.")
    digest = hashlib.sha256(json.dumps(reference,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {"verified":True,"reference_schema":reference_schema,"function_count":2,
            "collation_count":1,"catalog_sha256":digest}


def _assert_project_coverage(pg, schema):
    # Identifiers were validated above, and every raw PROJECT ID must occur in
    # the complete metrics output. Values for each project are verified later by
    # the independent all-row parity comparator.
    quoted = '"' + schema + '"'
    row = pg.execute(f"""WITH source AS MATERIALIZED (
        SELECT proj_id FROM {quoted}.project
      ), output AS MATERIALIZED (
        SELECT proj_id FROM {quoted}.xertoolkit_result_project_metrics
      ) SELECT
        (SELECT count(*) FROM source) source_projects,
        (SELECT count(*) FROM output) result_projects,
        (SELECT count(*) FROM (SELECT * FROM source EXCEPT ALL SELECT * FROM output) d) missing_projects,
        (SELECT count(*) FROM (SELECT * FROM output EXCEPT ALL SELECT * FROM source) d) extra_projects
      """).fetchone()
    if hasattr(row, "keys"):
        coverage = dict(row)
    else:
        coverage = dict(zip(("source_projects", "result_projects", "missing_projects", "extra_projects"), row))
    if coverage["missing_projects"] or coverage["extra_projects"] or coverage["source_projects"] != coverage["result_projects"]:
        raise RuntimeError("All-project calculation did not preserve complete project coverage.")
    return coverage


def calculate_all(pg, schema, config_version_id, check_run_id, *, progress=lambda message: None,
                  engine=None, evidence_module=None, work_mem_mb=None,
                  maintenance_work_mem_mb=None, jit=None, require_coverage=True, optimize_oos=False, oos_disable_nestloop=False,
                  helpers_precreated=False, helper_reference_schema=None):
    """Call the complete native engine and add inclusive per-stage timings.

    Optional session settings are transaction-local. Nothing changes the cluster
    configuration. Events can overlap: materialization includes index work and
    calculate_evidence includes configured-evidence rendering. The authoritative
    total is measured_calculation_seconds, not the sum of nested events.
    """
    require_isolated(pg, schema)
    if engine is None:
        from reporting_mirror import native_engine as engine
    if evidence_module is None:
        try:
            from reporting_mirror import native_evidence as evidence_module
        except ImportError:
            evidence_module = None
    settings = {}
    if work_mem_mb is not None:
        work_mem_mb = _bounded_integer(work_mem_mb, name="work_mem_mb", minimum=4, maximum=256)
        pg.execute("SELECT set_config('work_mem',%s,true)", (f"{work_mem_mb}MB",))
        settings["work_mem_mb"] = work_mem_mb
    if maintenance_work_mem_mb is not None:
        maintenance_work_mem_mb = _bounded_integer(maintenance_work_mem_mb, name="maintenance_work_mem_mb", minimum=64, maximum=1024)
        pg.execute("SELECT set_config('maintenance_work_mem',%s,true)", (f"{maintenance_work_mem_mb}MB",))
        settings["maintenance_work_mem_mb"] = maintenance_work_mem_mb
    if jit is not None:
        if not isinstance(jit, bool):
            raise ValueError("jit must be a bool or None")
        pg.execute("SELECT set_config('jit',%s,true)", ("on" if jit else "off",))
        settings["jit"] = jit
    if not isinstance(optimize_oos, bool) or not isinstance(oos_disable_nestloop, bool) or not isinstance(helpers_precreated,bool):
        raise ValueError("Optimization and helpers_precreated options must be bools")
    helper_receipts = []
    def committed_helpers(connection, generation):
        helper_receipts.append(verify_precreated_helpers(connection,generation,helper_reference_schema))
    if helpers_precreated and not hasattr(engine,"create_helpers"):
        raise ValueError("Precreated helper validation requires the native helper step")
    original_definition = getattr(engine, "query_definition", None)
    if optimize_oos:
        if original_definition is None:
            raise ValueError("Out-of-sequence optimization requires native query definitions")
        deleted_sql = original_definition("deleted_activities", schema, config_version_id)
        if not re.match(r"\s*SELECT\s+DISTINCT\b", deleted_sql, flags=re.IGNORECASE):
            raise ValueError("Expected DISTINCT deleted-activity source definition")
    events = []
    depth = 0

    def timed(function, category, *, relation_position=None):
        @wraps(function)
        def wrapped(*args, **kwargs):
            nonlocal depth
            if len(args) >= 2 and args[1] != schema:
                raise RuntimeError("All-project calculation unexpectedly changed generation.")
            name = category
            if relation_position is not None and len(args) > relation_position:
                name += ":" + str(args[relation_position])
            started, event_depth = perf_counter(), depth
            depth += 1
            status = "passed"
            previous_nestloop = None
            try:
                if (oos_disable_nestloop and category == "materialize" and len(args) > 2
                    and args[2] == "xertoolkit_fn_out_of_sequence"):
                    previous_nestloop = _scalar(pg.execute(
                        "SELECT current_setting('enable_nestloop') AS value"
                    ).fetchone(), "value")
                    pg.execute("SELECT set_config('enable_nestloop','off',true)")
                return function(*args, **kwargs)
            except BaseException:
                status = "failed"
                raise
            finally:
                # SQL errors abort the transaction: caller rollback restores all
                # LOCAL settings. Avoid masking that original exception with an
                # attempted setting change in a failed transaction.
                if previous_nestloop is not None and status == "passed":
                    pg.execute("SELECT set_config('enable_nestloop',%s,true)", (previous_nestloop,))
                depth -= 1
                events.append({"stage": name, "seconds": perf_counter() - started,
                               "depth": event_depth, "inclusive": True, "status": status})
        return wrapped

    def query_definition(name, generation, version):
        if generation != schema:
            raise RuntimeError("All-project calculation unexpectedly changed generation.")
        query = original_definition(name, generation, version)
        if name == "xertoolkit_fn_out_of_sequence":
            _assert_deleted_pair_uniqueness(pg, generation)
            query = rewrite_oos_deleted_lookup(query, generation)
        return query

    started = perf_counter()
    with ExitStack() as stack:
        if optimize_oos:
            stack.enter_context(patch.object(engine, "query_definition", query_definition))
        for name in ("create_helpers", "materialize", "index_and_analyse", "calculate_loops", "calculate_metrics", "calculate_evidence"):
            if hasattr(engine, name):
                original = committed_helpers if name == "create_helpers" and helpers_precreated else getattr(engine, name)
                position = 2 if name in {"materialize", "index_and_analyse"} else None
                stack.enter_context(patch.object(engine, name, timed(original, name, relation_position=position)))
        if evidence_module is not None and hasattr(evidence_module, "render_configured_evidence"):
            original = evidence_module.render_configured_evidence
            stack.enter_context(patch.object(evidence_module, "render_configured_evidence", timed(original, "render_configured_evidence")))
        manifest, quality = engine.calculate(pg, schema, config_version_id, check_run_id, progress=progress)
    if helpers_precreated and len(helper_receipts) != 1:
        raise RuntimeError("Expected exactly one committed helper validation step")
    coverage = _assert_project_coverage(pg, schema) if require_coverage else None
    seconds = perf_counter() - started
    return manifest, dict(quality, performance_scope="all_projects_and_history", project_coverage=coverage,
                          measured_calculation_seconds=seconds, stage_timings=events,
                          session_tuning=settings, project_scope_restricted=False,
                          precreated_helpers=helper_receipts[0] if helper_receipts else None,
                          optimizations={"oos_deleted_endpoint_joins": optimize_oos,
                                         "oos_nested_loop_disabled": oos_disable_nestloop})
