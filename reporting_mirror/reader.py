"""Read-only compatibility adapter for the existing P6 report queries."""
from contextvars import ContextVar
from datetime import datetime, timezone as datetime_timezone

from django.utils import timezone
from sqlglot import exp, parse, ErrorLevel
from sqlglot.dialects import Dialect
from sqlglot.tokens import TokenType

from backups.services.mssql import fetch_rows as sql_fetch_rows
from reporting_mirror.connections import postgres_connection
from reporting_mirror.models import ReportingConfig, ReportingRun
from reporting_mirror.sql_retirement import retired

report_source = ContextVar("p6_report_source", default="p6_22")
report_connection = ContextVar("p6_report_connection", default=None)
report_publication = ContextVar("p6_report_publication", default=None)


def compile_query(statement, source, parameters=()):
    if source not in {"p6_22", "p6_23", "combined"}:
        raise ValueError("Choose P6 22, P6 23 or both versions for this report.")
    schema = "reporting_ui" if source == "combined" else source
    # Name parameters in lexical order before parsing: AST traversal order differs
    # for OFFSET/FETCH, and silently swapping pagination parameters is incorrect.
    tokens = Dialect.get_or_raise("tsql").tokenize(statement)
    positions = [t for t in tokens if t.token_type == TokenType.PLACEHOLDER and t.text == "?"]
    if len(positions) != len(parameters or ()):
        raise ValueError("Reporting query parameter count does not match.")
    for index, token in reversed(list(enumerate(positions))):
        statement = statement[:token.start] + f":p{index}" + statement[token.end + 1:]
    statements = [s for s in parse(statement, read="tsql") if s is not None]
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        raise ValueError("Only one read-only reporting query is permitted.")
    expression = statements[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Into)
    if any(expression.find(t) is not None for t in forbidden):
        raise ValueError("Reporting queries cannot modify data.")
    for table in expression.find_all(exp.Table):
        if table.catalog:
            raise ValueError("Cross-database reporting queries are not permitted.")
        if table.db:
            if table.db.lower() not in {"dbo", "powerbitables"}:
                raise ValueError("Unexpected reporting schema.")
            table.set("db", exp.to_identifier(schema, quoted=True))
    for identifier in expression.find_all(exp.Identifier):
        if " " not in identifier.name:
            identifier.set("this", identifier.name.lower())
    converted = expression.sql(dialect="postgres", unsupported_level=ErrorLevel.RAISE)
    # Preserve literal percent signs when psycopg binds named parameters.
    converted = converted.replace("%", "%%")
    for index in range(len(positions)):
        converted = converted.replace(f"%%(p{index})s", f"%(p{index})s")
    return converted, {f"p{i}": value for i, value in enumerate(parameters or ())}


def active_config():
    return ReportingConfig.objects.filter(pk=1, serve_reports=True).first()


def fetch_rows(target, statement, *, parameters=None, database="master", transactional=False):
    config = active_config()
    if config is None:
        if retired():
            raise RuntimeError("PostgreSQL reporting must remain enabled after SQL reporting retirement.")
        if report_source.get() != "p6_22":
            raise RuntimeError("Combined PostgreSQL reporting is not currently enabled. Choose P6 22 to use the original SQL reports.")
        return sql_fetch_rows(target, statement, parameters=parameters, database=database, transactional=transactional)
    query, bound = compile_query(statement, report_source.get(), parameters or ())
    pinned = report_connection.get()
    if pinned is not None:
        return pinned.execute(query, bound).fetchall()
    with postgres_connection(reader=True) as connection:
        connection.execute("SELECT set_config('statement_timeout',%s,true)", (str(config.query_timeout_seconds * 1000),))
        return connection.execute(query, bound).fetchall()


def snapshot_context(source=None):
    config = active_config()
    if config is None:
        return {}
    source = source or report_source.get()
    publication = report_publication.get()
    run = None if publication else ReportingRun.objects.filter(status="success").first()
    if not publication and not run:
        return {"reporting_snapshot": {"source": source, "available": False}}
    source_details = publication["sources"] if publication else run.sources
    if source == "combined":
        captures = [datetime.fromisoformat(d["snapshot_at"]) for d in source_details.values() if d.get("snapshot_at")]
        qualities = [d.get("quality", {}) for d in source_details.values()]
        completed_dates = [q["completed_at"] for q in qualities if q.get("completed_at")]
        details = {"snapshot_at": min(captures).isoformat() if captures else None, "quality": {"completed_at": max(completed_dates) if completed_dates else None, "config_version_id": qualities[0].get("config_version_id") if qualities else None, "processed_project_count": sum(q.get("processed_project_count", 0) for q in qualities)}}
    else:
        details = source_details.get(source, {})
    captured = datetime.fromisoformat(details["snapshot_at"]) if details.get("snapshot_at") else None
    quality = details.get("quality", {})
    completed = quality.get("completed_at")
    completed = datetime.fromisoformat(completed).replace(tzinfo=datetime_timezone.utc) if completed else None
    stale = captured is None or (timezone.now() - captured).total_seconds() > config.stale_after_minutes * 60
    return {
        "reporting_snapshot": {"source": source, "label": "Both P6 versions" if source == "combined" else "P6 22" if source == "p6_22" else "P6 23", "available": True, "snapshot_at": captured, "published_at": publication["published_at"] if publication else run.published_at, "batch_id": str(publication["batch_id"] if publication else run.id), "stale": stale, "config_version": quality.get("config_version_id")},
        "latest_refresh": {"completed_at": completed, "processed_project_count": quality.get("processed_project_count"), "status": "success"},
    }
