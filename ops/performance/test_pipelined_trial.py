"""Dependency, complete-verification and failure-safety tests for the pipeline."""
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest


spec = spec_from_file_location("pipelined_trial", Path(__file__).with_name("pipelined_all_projects_trial.py"))
trial = module_from_spec(spec)
spec.loader.exec_module(trial)

SCHEMAS = {source: "native_shadow_0123456789abcdef_" + source for source in trial.SOURCE_ORDER}


class FakeProcess:
    def __init__(self, status=None, refuse_termination=False):
        self.status, self.refuse_termination = status, refuse_termination
        self.terminated = self.killed = False

    def poll(self):
        return self.status

    def terminate(self):
        self.terminated = True
        if not self.refuse_termination:
            self.status = -15

    def wait(self, timeout=None):
        if self.status is None:
            raise subprocess.TimeoutExpired("fixture", timeout)
        return self.status

    def kill(self):
        self.killed, self.status = True, -9


class PipelineTests(unittest.TestCase):
    def test_both_helpers_commit_before_large_source_starts_and_transfer_overlaps(self):
        events = []
        class Processes:
            def check(self):
                pass
            def start(self, code):
                events.append(("calculate", code))
            def collect(self):
                events.append("both_reconciled")
                return "complete"
            def stop(self):
                events.append("children_stopped")
        result = trial.execute_pipeline(
            prepare=lambda: events.append("both_helpers_committed"),
            transfer=lambda code: events.append(("full_transfer_verified", code)),
            processes=Processes(),
            publish=lambda calculations: events.append(("atomic_history_latest", calculations)))
        self.assertEqual(events, ["both_helpers_committed",
            ("full_transfer_verified", "p6_23"), ("calculate", "p6_23"),
            ("full_transfer_verified", "p6_22"), ("calculate", "p6_22"),
            "both_reconciled", ("atomic_history_latest", "complete"), "children_stopped"])
        self.assertEqual(result[0], "complete")

    def test_second_transfer_failure_terminates_running_large_calculation_without_publication(self):
        process = FakeProcess()
        manager = trial.CalculationProcesses("test", SCHEMAS, lambda *args, **kwargs: None,
            popen=lambda *args, **kwargs: process)
        published = []
        def transfer(source):
            if source == "p6_22":
                raise RuntimeError("full-value mismatch")
        with self.assertRaisesRegex(RuntimeError, "full-value mismatch"):
            trial.execute_pipeline(prepare=lambda: None, transfer=transfer,
                processes=manager, publish=lambda value: published.append(value))
        self.assertTrue(process.terminated)
        self.assertEqual(published, [])

    def test_child_failure_terminates_sibling_and_never_publishes(self):
        first, second = FakeProcess(), FakeProcess()
        pending = iter((first, second))
        manager = trial.CalculationProcesses("test", SCHEMAS, lambda *args, **kwargs: None,
            popen=lambda *args, **kwargs: next(pending), pause=lambda _: None)
        manager.start("p6_23")
        manager.start("p6_22")
        first.status = 2
        published = []
        with self.assertRaisesRegex(RuntimeError, "p6_23 exit=2"):
            try:
                calculations = manager.collect()
                published.append(calculations)
            finally:
                manager.stop()
        self.assertTrue(second.terminated)
        self.assertEqual(published, [])

    def test_unresponsive_child_is_killed_after_bounded_termination(self):
        child = FakeProcess(refuse_termination=True)
        manager = trial.CalculationProcesses("test", SCHEMAS, lambda *args, **kwargs: None,
            popen=lambda *args, **kwargs: child)
        manager.start("p6_23")
        manager.stop()
        self.assertTrue(child.terminated)
        self.assertTrue(child.killed)

    def test_children_have_fixed_safe_tuning_and_source_limit(self):
        calls = []
        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return FakeProcess()
        manager = trial.CalculationProcesses("fresh", SCHEMAS, lambda *args, **kwargs: None, popen=popen)
        manager.start("p6_23")
        command, options = calls[0]
        self.assertEqual(command[-2:], ["fresh", "p6_23"])
        self.assertEqual(options["env"]["P6_PERF_OOS_HASH"], "1")
        self.assertEqual(options["env"]["P6_PERF_PREPARED_HELPERS"], "1")
        self.assertEqual(options["env"]["P6_PERF_OPTIMIZE_OOS"], "0")
        with self.assertRaises(ValueError):
            manager.start("p6_23")
        with self.assertRaises(ValueError):
            manager.start("production")
        manager.stop()

    def calculation_fixture(self, source):
        return {"scope": "all_projects", "project_scope_restricted": False,
            "sources": {source: {"candidate_schema": SCHEMAS[source], "manifest": [],
                "quality": {"project_scope_restricted": False,
                    "project_coverage": {"source_projects": 820, "result_projects": 820,
                                         "missing_projects": 0, "extra_projects": 0}}}}}

    def collect_fixture(self, artifacts):
        manager = trial.CalculationProcesses("test", SCHEMAS, lambda *args, **kwargs: None,
            artifacts=artifacts, popen=lambda *args, **kwargs: FakeProcess(0))
        for source in trial.SOURCE_ORDER:
            manager.start(source)
        return manager.collect()

    def test_collection_rejects_latest_only_wrong_generation_and_missing_historical_project(self):
        for change in ("scope", "schema", "coverage", "source"):
            with self.subTest(change=change), TemporaryDirectory() as temporary:
                artifacts = Path(temporary)
                for source in trial.SOURCE_ORDER:
                    fixture = self.calculation_fixture(source)
                    if source == "p6_22":
                        entry = fixture["sources"][source]
                        if change == "scope":
                            fixture["project_scope_restricted"] = True
                        elif change == "schema":
                            entry["candidate_schema"] = "old_successful_generation"
                        elif change == "coverage":
                            entry["quality"]["project_coverage"]["missing_projects"] = 1
                        else:
                            fixture["sources"]["p6_23"] = deepcopy(entry)
                    (artifacts / ("test_" + source + "_calculate_result.json")).write_text(json.dumps(fixture))
                with self.assertRaises(RuntimeError):
                    self.collect_fixture(artifacts)
                self.assertFalse((artifacts / "test_calculate_result.json").exists())

    def test_collection_accepts_only_both_complete_source_receipts(self):
        with TemporaryDirectory() as temporary:
            artifacts = Path(temporary)
            for source in trial.SOURCE_ORDER:
                (artifacts / ("test_" + source + "_calculate_result.json")).write_text(
                    json.dumps(self.calculation_fixture(source)))
            result = self.collect_fixture(artifacts)
            self.assertEqual(set(result["sources"]), set(trial.SOURCE_ORDER))
            self.assertFalse(result["project_scope_restricted"])

    def test_redirected_endpoints_or_cluster_are_rejected(self):
        config = {"postgres": {"host": "p6-perf-postgres", "dbname": "p6_perf_trial"},
                  "staging": {"host": "p6-perf-sql"}}
        trial.validate_config(config)
        for section, key, value in (("postgres", "host", "live"),
                                    ("postgres", "dbname", "p6_reporting"),
                                    ("staging", "host", "live")):
            changed = deepcopy(config)
            changed[section][key] = value
            with self.assertRaises(ValueError):
                trial.validate_config(changed)
        class PG:
            def __init__(self, database, cluster):
                self.database, self.cluster = database, cluster
            def execute(self, query):
                return self
            def fetchone(self):
                return {"database": self.database, "cluster": self.cluster}
        trial.require_isolated(PG("p6_perf_trial", "p6-perf-isolated"))
        for database, cluster in (("p6_reporting", "p6-perf-isolated"),
                                  ("p6_perf_trial", "production")):
            with self.assertRaises(ValueError):
                trial.require_isolated(PG(database, cluster))

    def transfer_fixture(self):
        tables = []
        for name, count in (("project", 820), ("task", 296902), ("taskpred", 448377)):
            tables.append({"entry": {"source": "p6_22", "name": name, "rows": count,
                "verification": {"method": "ordered_arrow_exact_v1", "all_values_equal": True,
                                 "rows_compared": count}}})
        return {"status": "verified", "schema": SCHEMAS["p6_22"],
            "method": "ordered_arrow_exact_v1", "tables": tables,
            "rows": sum(table["entry"]["rows"] for table in tables)}

    def validate_fixture(self, fixture):
        trial.validate_transfer(fixture, SCHEMAS["p6_22"], "p6_22",
            {"PROJECT", "TASK", "TASKPRED"},
            {"projects": 820, "activities": 296902, "relationships": 448377})

    def test_no_sampling_missing_tables_or_count_only_transfer_can_start_a_calculation(self):
        self.validate_fixture(self.transfer_fixture())
        for change in ("sample", "missing", "duplicate", "count", "schema", "total", "source"):
            fixture = self.transfer_fixture()
            if change == "sample":
                fixture["tables"][0]["entry"]["verification"]["all_values_equal"] = False
            elif change == "missing":
                fixture["tables"].pop()
            elif change == "duplicate":
                fixture["tables"].append(fixture["tables"][0])
            elif change == "count":
                fixture["tables"][0]["entry"]["verification"]["rows_compared"] -= 1
            elif change == "schema":
                fixture["schema"] = "old_successful_generation"
            elif change == "source":
                fixture["tables"][0]["entry"]["source"] = "p6_23"
            else:
                fixture["rows"] += 1
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.validate_fixture(fixture)


if __name__ == "__main__":
    unittest.main()
