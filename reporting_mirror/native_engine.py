"""Native PostgreSQL calculations over one immutable P6 source generation.

No SQL Server connection, copied SQL report output, or runtime T-SQL translation
is used here. Ordinary views, materialized views and indexed result tables live
inside the generation schema until the caller validates and publishes it.
"""
from collections import defaultdict, deque
from datetime import datetime, timezone
from itertools import groupby
import json
from pathlib import Path
import re

from psycopg import sql

DIRECTORY = Path(__file__).with_name("native_sql")
CONTRACTS = json.loads((DIRECTORY / "contracts.json").read_text())
ACTIVITY_FLAGS = {
    "high_float_count": "is_high_float", "negative_float_count": "is_negative_float",
    "high_duration_count": "is_high_duration", "constraint_count": "is_constraint",
    "invalid_date_count": "is_invalid_date", "in_progress_error_count": "is_in_progress_error",
    "riding_progress_date_count": "is_riding_progress_date", "critical_task_count": "is_critical_task",
    "near_critical_task_count": "is_near_critical_task",
}
RELATIONSHIP_FLAGS = {
    "lead_count": "is_lead", "lag_count": "is_lag", "non_fs_count": "is_non_fs",
    "excessive_ss_lag_count": "is_excessive_ss_lag", "excessive_ff_lag_count": "is_excessive_ff_lag",
}
OPEN_FLAGS = {name + "_count": "is_" + name for name in ("missing_predecessor", "missing_successor", "open_start", "open_finish")}
BASE_VIEWS = ["activities", "projects", "relationships", "taskdetails", "wbsdetails"]
SCOPE_FUNCTIONS = ("activity_in_scope", "relationship_in_scope")
FUNCTION_TABLES = ("activity_quality", "relationship_quality", "open_ends", "out_of_sequence", "soft_constraints", "schedule_quality_task_evidence")


def identifier(value):
    return sql.Identifier(value).as_string()


def generation_schema(value):
    if not re.fullmatch(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)", value):
        raise ValueError("Native calculations require an isolated generation schema.")
    return identifier(value)


def query_definition(name, schema, config_version_id):
    text = (DIRECTORY / (name + ".sql")).read_text().strip().rstrip(";")
    return text.replace('"__schema__"', generation_schema(schema)).replace("__config_version_id__", str(int(config_version_id)))


def create_helpers(pg, schema):
    s = generation_schema(schema)
    pg.execute(f"CREATE COLLATION {s}.p6_text_ci (provider=icu,locale='en-US-u-ks-level2',deterministic=false,rules='&[first primary ignorable] = ''-''')")
    pg.execute(f"""CREATE FUNCTION {s}.p6_try_date(value text) RETURNS date LANGUAGE plpgsql IMMUTABLE STRICT AS $$
    DECLARE d integer; m integer; y integer;
    BEGIN
      IF value !~ '^[0-9]{{2}}/[0-9]{{2}}/[0-9]{{2}}$' THEN RETURN NULL; END IF;
      d=substring(value,1,2)::integer; m=substring(value,4,2)::integer; y=substring(value,7,2)::integer;
      RETURN make_date(CASE WHEN y<=49 THEN 2000+y ELSE 1900+y END,m,d);
    EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN RETURN NULL;
    END $$""")
    pg.execute(f"""CREATE FUNCTION {s}.p6_add_days(value date, days numeric) RETURNS date
      LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS 'SELECT value+trunc(days)::integer'""")


def create_result_table(pg, schema, name):
    fields = ",".join(identifier(c["name"]) + " " + c["type"] for c in CONTRACTS["results"][name])
    pg.execute(f"CREATE TABLE {generation_schema(schema)}.{identifier(name)} ({fields})")


def index_and_analyse(pg, schema, name, columns):
    target = generation_schema(schema) + "." + identifier(name)
    names = {c["name"] for c in columns}
    for field in ("proj_id", "task_id", "relationship_id", "config_version_id"):
        if field in names:
            pg.execute(f"CREATE INDEX ON {target} ({identifier(field)})")
    if {"proj_id", "task_id"} <= names:
        pg.execute(f"CREATE INDEX ON {target} (proj_id,task_id)")
    pg.execute(f"ANALYZE {target}")


def materialize(pg, schema, name, query, *, contract=None, materialized_view=False):
    s = generation_schema(schema)
    if contract:
        fields = ",".join(f"CAST(q.{identifier(c['name'])} AS {c['type']}) AS {identifier(c['name'])}" for c in contract)
        query = f"SELECT {fields} FROM ({query}) q"
    kind = "MATERIALIZED VIEW" if materialized_view else "TABLE"
    pg.execute(f"CREATE {kind} {s}.{identifier(name)} AS {query}")
    if not contract:
        columns = pg.execute("""SELECT a.attname name,pg_catalog.format_type(a.atttypid,a.atttypmod) type
            FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=%s AND c.relname=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (schema, name)).fetchall()
    else:
        columns = contract
    index_and_analyse(pg, schema, name, columns)
    count = pg.execute(f"SELECT COUNT(*) n FROM {s}.{identifier(name)}").fetchone()["n"]
    return {"name": name, "source_schema": "postgres_native", "kind": "view", "storage": "materialized_view" if materialized_view else "indexed_table", "rows": count, "columns": columns}


def cycle_core(edges):
    """Match the established self-loop plus directed-core pruning semantics.

    Iterative degree pruning preserves the SQL engine's treatment of nodes
    between cycles. It deliberately does not silently substitute an SCC test.
    """
    edges = set(edges)
    self_loops = {a for a, b in edges if a == b}
    outgoing, incoming = defaultdict(set), defaultdict(set)
    for a, b in edges:
        if a != b:
            outgoing[a].add(b); incoming[b].add(a)
    nodes = set(outgoing) | set(incoming)
    remove = deque(n for n in nodes if not outgoing[n] or not incoming[n])
    removed = set()
    while remove:
        node = remove.popleft()
        if node in removed:
            continue
        removed.add(node)
        for predecessor in incoming[node]:
            outgoing[predecessor].discard(node)
            if not outgoing[predecessor]: remove.append(predecessor)
        for successor in outgoing[node]:
            incoming[successor].discard(node)
            if not incoming[successor]: remove.append(successor)
    return self_loops, nodes - removed


def calculate_loops(pg, schema, config_version_id, check_run_id, calculated_at):
    s = generation_schema(schema)
    name = "xertoolkit_result_logic_loop_tasks"
    create_result_table(pg, schema, name)
    scope = pg.execute(f"SELECT * FROM {s}.xertoolkit_schedule_quality_check_scope WHERE config_version_id=%s AND check_code='logical_loops'", (config_version_id,)).fetchone()
    if scope and scope["is_enabled"]:
        deleted = pg.execute(f"SELECT bit_value FROM {s}.xertoolkit_schedule_quality_option WHERE config_version_id=%s AND option_code='exclude_deleted_activities'", (config_version_id,)).fetchone()
        exclude = deleted is None or deleted["bit_value"] is None or bool(deleted["bit_value"])
        include_loe = scope["include_loe"] is None or bool(scope["include_loe"])
        query = f"""SELECT DISTINCT t.proj_id,t.pred_task_id predecessor,t.task_id successor
          FROM {s}.taskpred t JOIN {s}.xertoolkit_vw_pbi_activities p ON p.proj_id=t.proj_id AND p.task_id=t.pred_task_id
          JOIN {s}.xertoolkit_vw_pbi_activities a ON a.proj_id=t.proj_id AND a.task_id=t.task_id
          WHERE t.pred_task_id IS NOT NULL AND t.task_id IS NOT NULL
          {'AND p.is_deleted=0 AND a.is_deleted=0' if exclude else ''}
          {'AND p.is_loe=0 AND a.is_loe=0' if not include_loe else ''} ORDER BY t.proj_id"""
        rows = []
        with pg.cursor(name="native_loop_edges") as cursor:
            cursor.itersize = 10000
            cursor.execute(query)
            for project, group in groupby(cursor, key=lambda r: r["proj_id"]):
                self_loops, core = cycle_core((r["predecessor"], r["successor"]) for r in group)
                rows.extend((project, task, f"|{task}|{task}|", 1, calculated_at, check_run_id, "self_loop", config_version_id) for task in sorted(self_loops))
                rows.extend((project, task, f"cycle_core:{task}", None, calculated_at, check_run_id, "cycle_core_prune", config_version_id) for task in sorted(core))
        with pg.cursor().copy(f"COPY {s}.{name} FROM STDIN") as copy:
            for row in rows: copy.write_row(row)
    index_and_analyse(pg, schema, name, CONTRACTS["results"][name])


def calculate_metrics(pg, schema, config_version_id, check_run_id, calculated_at):
    s = generation_schema(schema)
    name = "xertoolkit_result_project_metrics"
    create_result_table(pg, schema, name)
    aggregations = []
    metric_sources = {}
    for alias, table, flags in [("aq", "activity_quality", ACTIVITY_FLAGS), ("rq", "relationship_quality", RELATIONSHIP_FLAGS), ("oe", "open_ends", OPEN_FLAGS)]:
        aggregations.append(f"{alias} AS (SELECT proj_id," + ",".join(f"SUM({flag}) {metric}" for metric, flag in flags.items()) + f" FROM {s}.xertoolkit_fn_{table} GROUP BY proj_id)")
        metric_sources.update({metric: f"COALESCE({alias}.{metric},0)" for metric in flags})
    aggregations.extend([
        f"ac AS (SELECT proj_id,COUNT(*) n FROM {s}.xertoolkit_vw_pbi_activities WHERE is_deleted=0 OR COALESCE((SELECT bit_value FROM {s}.xertoolkit_schedule_quality_option WHERE config_version_id={int(config_version_id)} AND option_code='exclude_deleted_activities'),1)=0 GROUP BY proj_id)",
        f"sc AS (SELECT proj_id,COUNT(*) n FROM {s}.xertoolkit_fn_activity_in_scope('relationship_ratio') GROUP BY proj_id)",
        f"rc AS (SELECT proj_id,COUNT(*) n FROM {s}.xertoolkit_fn_relationship_in_scope('relationship_ratio') GROUP BY proj_id)",
        f"oos AS (SELECT proj_id,COUNT(DISTINCT successor_task_id) n FROM {s}.xertoolkit_fn_out_of_sequence WHERE is_out_of_sequence=1 GROUP BY proj_id)",
        f"ll AS (SELECT proj_id,COUNT(DISTINCT task_id) n FROM {s}.xertoolkit_result_logic_loop_tasks GROUP BY proj_id)",
        f"soft AS (SELECT proj_id,COUNT(*) n FROM {s}.xertoolkit_fn_soft_constraints GROUP BY proj_id)",
        f"soft_scope AS (SELECT proj_id,COUNT(*) n FROM {s}.xertoolkit_fn_activity_in_scope('soft_constraints') GROUP BY proj_id)",
    ])
    values = dict(metric_sources, proj_id="p.proj_id", project_name="COALESCE(NULLIF(p.\"Project Name\",''),p.proj_short_name)", updated_date="p.data_date",
        activity_count="COALESCE(ac.n,0)", dcma_activity_count="COALESCE(sc.n,0)", relationship_count="COALESCE(rc.n,0)",
        relationship_ratio="CAST(COALESCE(rq.non_fs_count,0)*100.0/NULLIF(COALESCE(rc.n,0),0) AS numeric(18,2))",
        out_of_sequence_count="COALESCE(oos.n,0)", logical_loop_count="COALESCE(ll.n,0)",
        soft_constraint_count="COALESCE(soft.n,0)", soft_constraint_activity_count="COALESCE(soft_scope.n,0)",
        check_run_id=str(int(check_run_id)), config_version_id=str(int(config_version_id)), refreshed_at=sql.Literal(calculated_at).as_string())
    columns = CONTRACTS["results"][name]
    select = ",".join(f"CAST({values[c['name']]} AS {c['type']})" for c in columns)
    joins = " ".join(f"LEFT JOIN {alias} ON {alias}.proj_id=p.proj_id" for alias in ("aq", "rq", "oe", "ac", "sc", "rc", "oos", "ll", "soft", "soft_scope"))
    pg.execute(f"WITH {','.join(aggregations)} INSERT INTO {s}.{name} SELECT {select} FROM {s}.xertoolkit_vw_pbi_projects p {joins}")
    index_and_analyse(pg, schema, name, columns)


def calculate_evidence(pg, schema, config_version_id, check_run_id, calculated_at):
    s = generation_schema(schema)
    name = "xertoolkit_result_schedule_quality_task_evidence"
    create_result_table(pg, schema, name)
    pg.execute(f"""INSERT INTO {s}.{name} (proj_id,check_code,task_id,task_code,task_name,evidence_basis,check_run_id,refreshed_at,config_version_id)
      SELECT e.proj_id,e.check_code,e.task_id,CAST(a.task_code AS varchar(40)),CAST(a.task_name AS varchar(120)),e.evidence_basis,%s,%s,%s
      FROM {s}.xertoolkit_fn_schedule_quality_task_evidence e JOIN {s}.xertoolkit_vw_pbi_activities a ON a.proj_id=e.proj_id AND a.task_id=e.task_id""", (check_run_id, calculated_at, config_version_id))
    index_and_analyse(pg, schema, name, CONTRACTS["results"][name])
    from reporting_mirror.native_evidence import render_configured_evidence
    render_configured_evidence(pg, schema, config_version_id)
    name = "xertoolkit_result_out_of_sequence_exceptions"
    create_result_table(pg, schema, name)
    pg.execute(f"""INSERT INTO {s}.{name}
      SELECT m.proj_id,m.project_name,m.updated_date,o.relationship_id,o.predecessor_task_id,p.task_code,p.task_name,o.predecessor_status,
        p.act_start_date,p.act_end_date,o.successor_task_id,a.task_code,a.task_name,o.successor_status,a.act_start_date,a.act_end_date,
        o.relationship_type,r.lag_hours,r.lag_days,o.out_of_sequence_reason,o.is_out_of_sequence,m.check_run_id,m.refreshed_at,m.config_version_id
      FROM {s}.xertoolkit_fn_out_of_sequence o JOIN {s}.xertoolkit_result_project_metrics m ON m.proj_id=o.proj_id
      JOIN {s}.xertoolkit_vw_pbi_activities p ON p.proj_id=o.proj_id AND p.task_id=o.predecessor_task_id
      JOIN {s}.xertoolkit_vw_pbi_activities a ON a.proj_id=o.proj_id AND a.task_id=o.successor_task_id
      LEFT JOIN {s}.xertoolkit_vw_pbi_relationships r ON r.proj_id=o.proj_id AND r.relationship_id=o.relationship_id
      WHERE o.is_out_of_sequence=1""")
    index_and_analyse(pg, schema, name, CONTRACTS["results"][name])


def calculate(pg, schema, config_version_id, check_run_id, *, progress=lambda message: None):
    """Build every reporting output from raw PostgreSQL tables and configuration."""
    started = datetime.now(timezone.utc).replace(tzinfo=None)
    create_helpers(pg, schema)
    materialize(pg, schema, "_deleted_activities", query_definition("deleted_activities", schema, config_version_id))
    manifest = []
    published_contracts = CONTRACTS["published_by_source"]["p6_22" if schema.endswith("p6_22") else "p6_23"]
    for suffix in BASE_VIEWS:
        name = "xertoolkit_vw_pbi_" + suffix
        progress("PostgreSQL: " + suffix)
        manifest.append(materialize(pg, schema, name, query_definition(name, schema, config_version_id), contract=published_contracts[name], materialized_view=True))
    s = generation_schema(schema)
    for suffix in SCOPE_FUNCTIONS:
        name = "xertoolkit_fn_" + suffix
        field = "task_id" if suffix == "activity_in_scope" else "relationship_id"
        query = query_definition(name, schema, config_version_id).replace("__check_code__", "requested_check")
        pg.execute(f"CREATE FUNCTION {s}.{name}(requested_check text) RETURNS TABLE(proj_id integer,{field} integer) LANGUAGE sql STABLE PARALLEL SAFE AS " + sql.Literal(query).as_string())
    for suffix in FUNCTION_TABLES:
        name = "xertoolkit_fn_" + suffix
        progress("PostgreSQL: " + suffix)
        materialize(pg, schema, name, query_definition(name, schema, config_version_id))
    progress("PostgreSQL: logical loop calculation")
    calculate_loops(pg, schema, config_version_id, check_run_id, started)
    calculate_metrics(pg, schema, config_version_id, check_run_id, started)
    calculate_evidence(pg, schema, config_version_id, check_run_id, started)
    for name in published_contracts:
        if not name.startswith("xertoolkit_vw_") or any(e["name"] == name for e in manifest):
            continue
        progress("PostgreSQL: " + name)
        manifest.append(materialize(pg, schema, name, query_definition(name, schema, config_version_id), contract=published_contracts[name], materialized_view=True))
    # The active output contract must remain exactly the same as SQL reporting.
    assert len(manifest) == 20
    for code, metric in [("high_float", "high_float_count"), ("negative_float", "negative_float_count"), ("high_duration", "high_duration_count"), ("invalid_dates", "invalid_date_count"), ("in_progress_errors", "in_progress_error_count"), ("critical_tasks", "critical_task_count"), ("near_critical_tasks", "near_critical_task_count"), ("missing_predecessor", "missing_predecessor_count"), ("missing_successor", "missing_successor_count"), ("soft_constraints", "soft_constraint_count")]:
        count = pg.execute(f"SELECT (SELECT COALESCE(SUM({metric}),0) FROM {s}.xertoolkit_result_project_metrics) metrics,(SELECT COUNT(*) FROM {s}.xertoolkit_result_schedule_quality_task_evidence WHERE check_code=%s) evidence", (code,)).fetchone()
        if count["metrics"] != count["evidence"]:
            raise RuntimeError(f"Native PostgreSQL {code} evidence does not reconcile.")
    completed = datetime.now(timezone.utc).replace(tzinfo=None)
    return manifest, {"status": "success", "config_version_id": config_version_id, "check_run_id": check_run_id, "completed_at": completed.isoformat(), "processed_project_count": pg.execute(f"SELECT COUNT(*) n FROM {s}.project").fetchone()["n"], "evidence_count_checks_passed": 10, "engine": "postgres", "calculation_seconds": (completed - started).total_seconds()}
