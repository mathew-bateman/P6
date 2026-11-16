"""Time a complete isolated refresh with the larger source calculated first.

Both helper catalogues are committed before either calculation starts. P6 23's
verified transfer starts its calculation immediately, while P6 22 is captured,
restored and fully transferred/verified. Only two calculation children and two
raw-table workers per source are allowed. Both latest and historical views are
published in one transaction after both complete successfully.

This is an isolated experiment, not a production refresh entry point. Run the
existing verify_all_trial.py TAG separately after the stopwatch for full report
parity. Transfer verification and calculation reconciliation remain inside it.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid


SOURCE_ORDER = ("p6_23", "p6_22")
ARTIFACTS = Path("/bench/artifacts")
TRIAL = Path("/bench/trial")
TAG = re.compile(r"[A-Za-z0-9_]{1,64}\Z")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, default=str) + "\n")


def validate_config(config):
    """Reject redirected endpoints before any SQL Server write is possible."""
    if (config["postgres"]["host"] != "p6-perf-postgres"
            or config["postgres"]["dbname"] != "p6_perf_trial"
            or config["staging"]["host"] != "p6-perf-sql"):
        raise ValueError("Pipelined trials require the dedicated isolated endpoints")


def require_isolated(pg):
    row = pg.execute("SELECT current_database() AS database, "
                     "current_setting('cluster_name') AS cluster").fetchone()
    if row["database"] != "p6_perf_trial" or row["cluster"] != "p6-perf-isolated":
        raise ValueError("Pipelined trials require the dedicated isolated PostgreSQL cluster")


def validate_transfer(receipt, schema, source, raw_tables, source_counts):
    """Require every raw table's complete typed-value comparison before use."""
    if (receipt.get("status") != "verified" or receipt.get("schema") != schema
            or receipt.get("method") != "ordered_arrow_exact_v1"):
        raise RuntimeError("Native transfer did not produce the verified private generation")
    entries = [item["entry"] for item in receipt["tables"]]
    names = [item["name"] for item in entries]
    expected = {table.lower() for table in raw_tables}
    if len(names) != len(expected) or set(names) != expected:
        raise RuntimeError("Full raw-table inventory did not reconcile")
    for entry in entries:
        check = entry.get("verification", {})
        if (entry.get("source") != source
                or check.get("method") != "ordered_arrow_exact_v1"
                or check.get("all_values_equal") is not True
                or check.get("rows_compared") != entry["rows"]):
            raise RuntimeError("Every table requires a complete typed-value verification")
    copied = {item["name"]: item["rows"] for item in entries}
    if any(copied[table] != source_counts[count]
           for table, count in (("project", "projects"), ("task", "activities"),
                                ("taskpred", "relationships"))):
        raise RuntimeError("Core source counts did not reconcile after full verification")
    if receipt["rows"] != sum(entry["rows"] for entry in entries):
        raise RuntimeError("Verified transfer total does not reconcile")


class CalculationProcesses:
    """Own and clean up the two child processes, including on loader failure."""

    def __init__(self, tag, schemas, event, *, artifacts=ARTIFACTS,
                 popen=subprocess.Popen, pause=time.sleep, clock=time.perf_counter):
        self.tag, self.schemas, self.event = tag, schemas, event
        self.artifacts, self.popen, self.pause, self.clock = artifacts, popen, pause, clock
        self.processes = {}
        self.started_at = None
        self.completed = set()

    def start(self, code):
        if code not in SOURCE_ORDER or code in self.processes or len(self.processes) >= 2:
            raise ValueError("Only one calculation per fixed P6 source is allowed")
        self.check()
        environment = os.environ.copy()
        environment.update(P6_PERF_OOS_HASH="1", P6_PERF_PREPARED_HELPERS="1",
                           P6_PERF_OPTIMIZE_OOS="0")
        if self.started_at is None:
            self.started_at = self.clock()
        self.processes[code] = self.popen(
            [sys.executable, str(TRIAL / "calculate_one_all.py"), self.tag, code],
            env=environment,
        )
        self.event("calculation_started", source=code)

    def check(self):
        for code, process in self.processes.items():
            status = process.poll()
            if status is not None and code not in self.completed:
                self.completed.add(code)
                self.event("calculation_exit_observed", source=code, exit_status=status)
            if status not in (None, 0):
                raise RuntimeError("All-project calculation process failed: " + code
                                   + " exit=" + str(status))

    def collect(self):
        if set(self.processes) != set(SOURCE_ORDER):
            raise RuntimeError("Both all-project calculation children are required")
        while True:
            self.check()
            if len(self.completed) == len(SOURCE_ORDER):
                break
            self.pause(0.1)
        result = {
            "scope": "all_projects", "project_scope_restricted": False,
            "source_calculation_processes": 2, "sources": {},
            # This is the elapsed window from first launch until both children
            # complete; it includes the intentionally overlapped P6 22 transfer.
            "parallel_calculation_seconds": self.clock() - self.started_at,
        }
        for code in SOURCE_ORDER:
            path = self.artifacts / (self.tag + "_" + code + "_calculate_result.json")
            child = json.loads(path.read_text())
            if (child.get("scope") != "all_projects"
                    or child.get("project_scope_restricted") is not False
                    or set(child.get("sources", {})) != {code}):
                raise RuntimeError("A complete unscoped source calculation is required")
            source = child["sources"][code]
            coverage = source.get("quality", {}).get("project_coverage", {})
            if (source.get("candidate_schema") != self.schemas[code]
                    or source.get("quality", {}).get("project_scope_restricted") is not False
                    or not coverage
                    or coverage.get("source_projects") != coverage.get("result_projects")
                    or coverage.get("missing_projects") != 0
                    or coverage.get("extra_projects") != 0):
                raise RuntimeError("Calculated generation lacks complete project coverage")
            result["sources"][code] = source
        write_json(self.artifacts / (self.tag + "_calculate_result.json"), result)
        return result

    def stop(self):
        # Stop every running sibling before waiting, so one unresponsive child
        # cannot leave another doing work after the benchmark has failed.
        active = [process for process in self.processes.values() if process.poll() is None]
        for process in active:
            process.terminate()
        for process in active:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def execute_pipeline(*, prepare, transfer, processes, publish):
    """Keep publication dependent on both verified, reconciled generations."""
    try:
        prepare()
        for code in SOURCE_ORDER:
            processes.check()
            transfer(code)
            processes.check()
            processes.start(code)
        calculations = processes.collect()
        publication = publish(calculations)
        return calculations, publication
    finally:
        processes.stop()


def runtime():
    # Lazy imports keep isolation/orchestration tests independent of Django and
    # the benchmark's optional native drivers.
    sys.path[:0] = ["/app", str(Path(__file__).parent)]
    import django
    django.setup()
    from psycopg import sql
    from backups.services.mssql import connect, quote_identifier
    from reporting_mirror.sql_staging import query, RAW_TABLES
    from reporting_mirror.connections import postgres_connection
    from reporting_mirror.sync import metadata_select, ensure_combined_project_indexes
    from reporting_mirror.native_engine import create_helpers
    from native_bulk.arrow_transfer import run
    from compare_all_projects import publish_history_views
    return SimpleNamespace(sql=sql, connect=connect, qi=quote_identifier,
        query=query, raw_tables=RAW_TABLES, postgres_connection=postgres_connection,
        metadata_select=metadata_select,
        ensure_combined_project_indexes=ensure_combined_project_indexes,
        create_helpers=create_helpers, native_transfer=run,
        publish_history_views=publish_history_views)


def main(tag):
    if not TAG.fullmatch(tag):
        raise ValueError("Use a new, explicit simple trial tag")
    # Receipts must belong to this run; a failed/repeated tag cannot reuse stale
    # successful child output from an earlier generation.
    if any(ARTIFACTS.glob(tag + "_*.json")):
        raise ValueError("Use a fresh tag; benchmark artifacts already exist")
    cfg = json.loads(Path("/bench/private.json").read_text())
    validate_config(cfg)
    app = runtime()
    pgcfg = {key: cfg["postgres"][key]
             for key in ("host", "port", "dbname", "user", "password")}
    staging = cfg["staging"]
    target = SimpleNamespace(sql_host=staging["host"], sql_port=staging["port"],
        sql_username=staging["username"], sql_password=staging["password"],
        odbc_driver="ODBC Driver 18 for SQL Server", encrypt_connection=True,
        trust_server_certificate=True)
    with app.postgres_connection() as pg:
        require_isolated(pg)
        baseline = pg.execute("SELECT * FROM reporting.refresh_status").fetchone()
    if not baseline or set(baseline["sources"]) != set(SOURCE_ORDER):
        raise RuntimeError("Both fixed-snapshot baseline sources are required")
    token = uuid.uuid4().hex[:16]
    schemas = {code: "native_shadow_" + token + "_" + code for code in SOURCE_ORDER}
    references = {code: "load_" + baseline["batch_id"].hex[:16] + "_" + code
                  for code in SOURCE_ORDER}
    result = {
        "tag": tag, "token": token, "snapshot_stages": {}, "source_counts": {},
        "transfer_stages": {}, "timeline": [], "events": [], "fully_isolated": True,
        "calculation_scope": "all_projects", "project_scope_restricted": False,
        "raw_table_workers": 2, "source_calculation_processes": 2,
        "pipeline_source_order": list(SOURCE_ORDER), "optimize_oos": False,
        "oos_disable_nestloop": True, "helpers_precreated": True,
        "publication_complete": False,
    }
    total = time.perf_counter()
    calculation_starts = {}

    def event(name, **details):
        seconds = time.perf_counter() - total
        result["events"].append(dict(event=name, seconds=seconds, **details))
        if name == "calculation_started":
            calculation_starts[details["source"]] = seconds
        elif name == "calculation_exit_observed":
            result["timeline"].append({
                "stage": "calculation_process", "source": details["source"],
                "start_seconds": calculation_starts[details["source"]],
                "end_seconds": seconds, "completion_timing": "parent_observed",
                "exit_status": details["exit_status"],
            })

    def interval(stage, started, *, source=None):
        result["timeline"].append({"stage": stage, "source": source,
            "start_seconds": started-total, "end_seconds": time.perf_counter()-total,
            "completion_timing": "recorded"})

    def prepare():
        started = time.perf_counter()
        event("helpers_started")
        with app.postgres_connection() as pg:
            require_isolated(pg)
            for code in SOURCE_ORDER:
                pg.execute(app.sql.SQL("CREATE SCHEMA {}").format(
                    app.sql.Identifier(schemas[code])))
                app.create_helpers(pg, schemas[code])
        # Context exit commits BOTH catalogues before a long calculation can
        # hold their shared catalogue/ICU dependencies.
        result["helper_setup_seconds"] = time.perf_counter() - started
        interval("helper_setup", started)
        event("helpers_committed")

    def transfer(code):
        started = time.perf_counter()
        event("snapshot_started", source=code)
        database = "perf_stage_" + token[:8] + "_" + code
        backup = "/var/opt/mssql/data/perf_capture_" + token + "_" + code + ".bak"
        with app.connect(target) as connection:
            connection.timeout = 600
            app.query(connection, "BACKUP DATABASE " + app.qi("perf_" + code)
                + " TO DISK=N'" + backup + "' WITH COPY_ONLY,INIT,CHECKSUM")
            app.query(connection, "RESTORE VERIFYONLY FROM DISK=N'" + backup + "' WITH CHECKSUM")
            files = app.query(connection, "RESTORE FILELISTONLY FROM DISK=N'" + backup + "'")
            moves = []
            for number, entry in enumerate(files):
                path = "/var/opt/mssql/data/" + database + "_" + str(number)
                path += ".ldf" if entry["Type"] == "L" else ".mdf"
                moves.append("MOVE N'" + entry["LogicalName"].replace("'", "''")
                             + "' TO N'" + path + "'")
            app.query(connection, "RESTORE DATABASE " + app.qi(database)
                + " FROM DISK=N'" + backup + "' WITH CHECKSUM,RECOVERY," + ",".join(moves))
            counts = app.query(connection,
                "SELECT (SELECT COUNT_BIG(*) FROM " + app.qi(database) + ".dbo.PROJECT) projects,"
                "(SELECT COUNT_BIG(*) FROM " + app.qi(database) + ".dbo.TASK) activities,"
                "(SELECT COUNT_BIG(*) FROM " + app.qi(database) + ".dbo.TASKPRED) relationships")[0]
        result["source_counts"][code] = counts
        result["snapshot_stages"][code] = time.perf_counter() - started
        interval("snapshot", started, source=code)
        event("snapshot_restored", source=code)
        source_cs = (f"Server={staging['host']},{staging['port']};Database={database};"
                     f"UID={staging['username']};PWD={staging['password']};"
                     "Encrypt=yes;TrustServerCertificate=yes;")
        config = {
            "benchmark_only": True, "postgres": pgcfg,
            "source_connection_string": source_cs, "schema": schemas[code],
            "source_code": code, "tables": sorted(app.raw_tables), "workers": 2,
            "batch_size": 50000, "schema_precreated": True,
            "helper_reference_schema": references[code],
        }
        started = time.perf_counter()
        event("transfer_started", source=code)
        receipt = app.native_transfer(config)
        validate_transfer(receipt, schemas[code], code, app.raw_tables, counts)
        write_json(ARTIFACTS / (tag + "_" + code + ".json"), receipt)
        result["transfer_stages"][code] = time.perf_counter() - started
        interval("transfer", started, source=code)
        event("transfer_verified", source=code, rows=receipt["rows"])
        print("TRANSFER_VERIFIED", code, round(receipt["seconds"], 3),
              receipt["rows"], flush=True)

    def publish(calculations):
        started = time.perf_counter()
        event("publication_started")
        with app.postgres_connection() as pg:
            require_isolated(pg)
            manifest = pg.execute("SELECT manifest FROM sync_audit.batches WHERE batch_id=%s",
                (baseline["batch_id"],)).fetchone()["manifest"]
            app.ensure_combined_project_indexes(pg, schemas, manifest)
            for code in SOURCE_ORDER:
                source = SimpleNamespace(code=code, target=SimpleNamespace(
                    sql_database=baseline["sources"][code]["database"]))
                for entry in manifest:
                    if entry["source"] != code:
                        continue
                    statement = app.metadata_select(schemas[code], entry, source,
                        baseline["sources"][code], baseline["published_at"], baseline["batch_id"])
                    pg.execute(app.sql.SQL("CREATE OR REPLACE VIEW {}.{} AS {}").format(
                        app.sql.Identifier(code), app.sql.Identifier(entry["name"]), statement))
            history = app.publish_history_views(pg, schemas,
                {code: calculations["sources"][code]["manifest"] for code in SOURCE_ORDER},
                baseline["sources"], baseline["batch_id"], baseline["published_at"])
        result["publication_seconds"] = time.perf_counter() - started
        result["publication_complete"] = True
        interval("publication", started)
        event("publication_committed")
        return history

    processes = CalculationProcesses(tag, schemas, event)
    try:
        calculations, history = execute_pipeline(prepare=prepare, transfer=transfer,
            processes=processes, publish=publish)
        result["calculation"], result["historical_publication"] = calculations, history
        result["total_seconds"] = time.perf_counter() - total
        result["target_seconds"] = 180
        result["latency_target_met"] = result["total_seconds"] <= result["target_seconds"]
        write_json(ARTIFACTS / (tag + "_end_to_end.json"), result)
        print("FULL_REFRESH_RESULT", json.dumps({key: result[key] for key in (
            "tag", "total_seconds", "publication_seconds", "target_seconds",
            "latency_target_met")}), flush=True)
        return result
    except Exception as exc:
        # Do not serialize exceptions: driver exceptions may include a private
        # connection string. Stage/source information is enough for the receipt.
        event("failed", exception_type=type(exc).__name__)
        result["total_seconds"] = time.perf_counter() - total
        result["failed"] = True
        write_json(ARTIFACTS / (tag + "_failed.json"), result)
        if result["publication_complete"]:
            message = "Isolated refresh published, but its final benchmark receipt failed"
        else:
            message = "Isolated pipelined refresh failed before publication committed"
        raise RuntimeError(message) from None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag")
    main(parser.parse_args().tag)
