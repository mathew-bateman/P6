"""Guards and semantic-preservation tests for all-project instrumentation."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import unittest
import json
import os
from copy import deepcopy
from unittest.mock import patch

spec = spec_from_file_location("all_project_calc", Path(__file__).with_name("all_project_calc.py"))
trial = module_from_spec(spec)
spec.loader.exec_module(trial)
SCHEMA = "native_shadow_0123456789abcdef_p6_23"


class FakePG:
    def __init__(self, database="p6_perf_test", coverage=None):
        self.database, self.calls = database, []
        self.coverage = coverage or {"source_projects": 200, "result_projects": 200, "missing_projects": 0, "extra_projects": 0}

    def execute(self, query, parameters=None):
        self.calls.append((str(query), parameters))
        self.query = str(query)
        return self

    def fetchone(self):
        return {"database": self.database} if "current_database" in self.query else {"value":"on"} if "current_setting" in self.query else {"duplicates":0} if "AS duplicates" in self.query else self.coverage


def fixture(fail=False):
    calls = []
    evidence = SimpleNamespace(render_configured_evidence=lambda *args: calls.append("render"))
    engine = SimpleNamespace(BASE_VIEWS=["activities", "projects"], query_definition=lambda *args: "SELECT * FROM all_history")
    engine.create_helpers = lambda *args: calls.append("helpers")
    engine.index_and_analyse = lambda *args: calls.append("index")
    def materialize(pg, schema, name, query):
        calls.append((name, query));engine.index_and_analyse(pg, schema, name, [])
    engine.materialize = materialize
    engine.calculate_loops = lambda *args: calls.append("loops")
    engine.calculate_metrics = lambda *args: calls.append("metrics")
    engine.calculate_evidence = lambda *args: evidence.render_configured_evidence(*args[:3])
    def calculate(pg, schema, version, run, progress):
        engine.create_helpers(pg, schema)
        engine.materialize(pg, schema, "all_projects", engine.query_definition("all", schema, version))
        engine.calculate_loops(pg, schema, version, run, None)
        engine.calculate_metrics(pg, schema, version, run, None)
        engine.calculate_evidence(pg, schema, version, run, None)
        if fail:raise RuntimeError("fixture failure")
        return [{"name": "all_projects", "rows": 200}], {"processed_project_count": 200, "evidence_count_checks_passed": 10}
    engine.calculate = calculate
    return engine, evidence, calls


class AllProjectTests(unittest.TestCase):
    def test_all_rows_and_original_query_definitions_remain_unchanged(self):
        engine, evidence, calls = fixture();pg = FakePG()
        definition, views = engine.query_definition, engine.BASE_VIEWS
        manifest, quality = trial.calculate_all(pg, SCHEMA, 37, 123, engine=engine, evidence_module=evidence)
        self.assertEqual(manifest, [{"name": "all_projects", "rows": 200}])
        self.assertIn(("all_projects", "SELECT * FROM all_history"), calls)
        self.assertIs(engine.query_definition, definition)
        self.assertIs(engine.BASE_VIEWS, views)
        self.assertEqual(quality["processed_project_count"], 200)
        self.assertEqual(quality["evidence_count_checks_passed"], 10)
        self.assertFalse(quality["project_scope_restricted"])
        self.assertEqual(quality["project_coverage"]["source_projects"], 200)
        self.assertFalse(any("selected" in query.lower() for query, _ in pg.calls))
        stages = {r["stage"] for r in quality["stage_timings"]}
        self.assertTrue({"materialize:all_projects", "index_and_analyse:all_projects", "calculate_loops", "calculate_metrics", "calculate_evidence", "render_configured_evidence"} <= stages)

    def test_production_database_is_rejected_before_calculation(self):
        engine, evidence, calls = fixture()
        with self.assertRaisesRegex(RuntimeError, "isolated"):
            trial.calculate_all(FakePG("p6_reporting"), SCHEMA, 37, 123, engine=engine, evidence_module=evidence)
        self.assertEqual(calls, [])

    def test_invalid_schema_is_rejected(self):
        engine, evidence, calls = fixture()
        with self.assertRaises(ValueError):
            trial.calculate_all(FakePG(), SCHEMA + ';DROP SCHEMA public', 37, 123, engine=engine, evidence_module=evidence)
        self.assertEqual(calls, [])

    def test_missing_historical_project_is_rejected(self):
        engine, evidence, _ = fixture()
        pg = FakePG(coverage={"source_projects": 200, "result_projects": 41, "missing_projects": 159, "extra_projects": 0})
        with self.assertRaisesRegex(RuntimeError, "complete project coverage"):
            trial.calculate_all(pg, SCHEMA, 37, 123, engine=engine, evidence_module=evidence)

    def test_session_tuning_is_bounded_and_transaction_local(self):
        engine, evidence, _ = fixture();pg = FakePG()
        _, quality = trial.calculate_all(pg, SCHEMA, 37, 123, engine=engine, evidence_module=evidence,
                                        work_mem_mb=64, maintenance_work_mem_mb=512, jit=False)
        settings = [call for call in pg.calls if "set_config" in call[0]]
        self.assertEqual([p for _,p in settings], [("64MB",), ("512MB",), ("off",)])
        self.assertTrue(all("true" in query for query,_ in settings))
        self.assertEqual(quality["session_tuning"], {"work_mem_mb":64,"maintenance_work_mem_mb":512,"jit":False})
        for value in (True, 0, 257, "64MB;RESET ALL"):
            with self.assertRaises(ValueError):
                trial.calculate_all(FakePG(), SCHEMA, 37, 123, engine=engine, evidence_module=evidence, work_mem_mb=value)

    def test_oos_plan_hint_is_local_and_restored_without_rewriting_query(self):
        engine, evidence, calls = fixture();pg=FakePG()
        def calculate(pg,schema,version,run,progress):
            engine.materialize(pg,schema,"xertoolkit_fn_out_of_sequence","SELECT * FROM all_history")
            return [], {"processed_project_count":200}
        engine.calculate=calculate
        _,quality=trial.calculate_all(pg,SCHEMA,37,123,engine=engine,evidence_module=evidence,
                                     oos_disable_nestloop=True)
        self.assertIn(("xertoolkit_fn_out_of_sequence","SELECT * FROM all_history"),calls)
        settings=[(q,p) for q,p in pg.calls if "set_config('enable_nestloop'" in q]
        self.assertEqual(len(settings),2)
        self.assertIn("'off',true",settings[0][0])
        self.assertEqual(settings[1][1],("on",))
        self.assertFalse(quality["optimizations"]["oos_deleted_endpoint_joins"])
        self.assertTrue(quality["optimizations"]["oos_nested_loop_disabled"])

    def test_wrapped_engine_functions_restore_after_failure(self):
        engine, evidence, _ = fixture(fail=True)
        originals = {name:getattr(engine,name) for name in ("materialize","index_and_analyse","calculate_evidence")}
        renderer = evidence.render_configured_evidence
        with self.assertRaisesRegex(RuntimeError, "fixture failure"):
            trial.calculate_all(FakePG(), SCHEMA, 37, 123, engine=engine, evidence_module=evidence)
        for name, function in originals.items():self.assertIs(getattr(engine,name), function)
        self.assertIs(evidence.render_configured_evidence, renderer)


OOS_FIXTURE = """WITH deleted_activities AS (
 SELECT DISTINCT proj_id,task_id FROM (VALUES
  (1,11),(1,21),(1,11),(NULL::int,11),(1,NULL::int),(2,999)
 ) deleted_source(proj_id,task_id)
), relationships AS (
 SELECT * FROM (VALUES
  (1,1,10,20),(2,1,11,20),(3,1,10,21),(4,1,11,21),
  (5,2,11,21),(6,NULL::int,11,21),(7,1,NULL::int,20),
  (8,1,10,NULL::int),(9,1,NULL::int,21),(10,1,11,NULL::int),
  (11,1,NULL::int,NULL::int)
 ) source(relationship_id,proj_id,predecessor_task_id,successor_task_id)
)
SELECT r.* FROM relationships AS r
CROSS JOIN (SELECT %s::int AS bit_value) AS deleted_option
WHERE
 (true)
 AND (
  COALESCE(deleted_option.bit_value,1)=0
  OR NOT EXISTS(
   SELECT 1 FROM deleted_activities AS deleted
   WHERE deleted.proj_id = r.proj_id
   AND deleted.task_id IN (r.predecessor_task_id, r.successor_task_id)
  )
 )
"""


class OutOfSequenceRewriteTests(unittest.TestCase):
    def test_only_exact_expected_predicate_is_rewritten(self):
        rewritten=trial.rewrite_oos_deleted_lookup(OOS_FIXTURE,SCHEMA)
        self.assertEqual(rewritten.count("LEFT JOIN deleted_activities"),2)
        self.assertIn("deleted_activities AS NOT MATERIALIZED",rewritten)
        self.assertNotIn("OR NOT EXISTS",rewritten)
        self.assertIn("deleted_predecessor.task_id IS NULL AND deleted_successor.task_id IS NULL",rewritten)
        self.assertIn("SELECT r.*",rewritten)
        self.assertNotIn("selected",rewritten)
        for changed in (OOS_FIXTURE.replace("r.successor_task_id)","r.other_task_id)"),
                        OOS_FIXTURE+"\nWHERE\n true",rewritten):
            with self.assertRaises(ValueError):trial.rewrite_oos_deleted_lookup(changed,SCHEMA)

    def test_duplicate_deleted_pairs_fail_closed(self):
        class DuplicatePG:
            def execute(self,*args):return self
            def fetchone(self):return {"duplicates":1}
        with self.assertRaisesRegex(RuntimeError,"must be unique"):
            trial._assert_deleted_pair_uniqueness(DuplicatePG(),SCHEMA)


@unittest.skipUnless(os.environ.get("P6_PARITY_TEST_CONNECTION_FILE"),"Isolated PostgreSQL fixture connection not supplied")
class OutOfSequenceDatabaseSemanticsTests(unittest.TestCase):
    def test_nulls_duplicate_deleted_rows_and_option_states_preserve_every_row(self):
        import psycopg
        config=json.loads(Path(os.environ["P6_PARITY_TEST_CONNECTION_FILE"]).read_text())
        with psycopg.connect(**config) as pg:
            trial.require_isolated(pg,SCHEMA)
            candidate=trial.rewrite_oos_deleted_lookup(OOS_FIXTURE,SCHEMA)
            for option in (None,0,1):
                original=pg.execute(OOS_FIXTURE,(option,)).fetchall()
                optimized=pg.execute(candidate,(option,)).fetchall()
                self.assertCountEqual(original,optimized)
                expected=11 if option==0 else 6
                self.assertEqual(len(optimized),expected)


class CommittedHelperTests(unittest.TestCase):
    def catalog(self):
        return {"collations":[{"collname":"p6_text_ci","collprovider":"i","collisdeterministic":False,
                               "colllocale":"en-US-u-ks-level2","collicurules":"hyphen rule"}],
                "functions":[{"name":"p6_add_days","arguments":"value date, days numeric","result":"date",
                              "definition":"add days","attributes":{"prosrc":"SELECT value+trunc(days)::integer","proisstrict":True,"provolatile":"i","proparallel":"s","prosecdef":False}},
                             {"name":"p6_try_date","arguments":"value text","result":"date","definition":"try date",
                              "attributes":{"prosrc":"parse date","proisstrict":True,"provolatile":"i","proparallel":"u","prosecdef":False}}]}

    def test_committed_helpers_only_verify_and_skip_creation(self):
        engine,evidence,calls=fixture();catalog=self.catalog()
        ref="load_aaaaaaaaaaaaaaaa_p6_23"
        with patch.object(trial,"_helper_catalog",return_value=catalog):
            _,quality=trial.calculate_all(FakePG(),SCHEMA,37,123,engine=engine,evidence_module=evidence,
                                         helpers_precreated=True,helper_reference_schema=ref)
        self.assertNotIn("helpers",calls)
        self.assertTrue(quality["precreated_helpers"]["verified"])
        self.assertEqual(quality["precreated_helpers"]["function_count"],2)

    def test_changed_body_flags_signature_or_collation_are_rejected(self):
        reference=self.catalog()
        variants=[]
        changed=deepcopy(reference);changed["functions"][0]["attributes"]["prosrc"]="wrong calculation";variants.append(changed)
        changed=deepcopy(reference);changed["functions"][0]["attributes"]["proisstrict"]=False;variants.append(changed)
        changed=deepcopy(reference);changed["functions"][0]["arguments"]="value date, days integer";variants.append(changed)
        changed=deepcopy(reference);changed["collations"][0]["collisdeterministic"]=True;variants.append(changed)
        for candidate in variants:
            with patch.object(trial,"_helper_catalog",side_effect=[reference,candidate]):
                with self.assertRaisesRegex(RuntimeError,"differ"):
                    trial.verify_precreated_helpers(FakePG(),SCHEMA,"load_aaaaaaaaaaaaaaaa_p6_23")

    def test_missing_helpers_fail_closed(self):
        with patch.object(trial,"_helper_catalog",side_effect=RuntimeError("Expected one ICU helper collation and exactly two helper functions")):
            with self.assertRaises(RuntimeError):trial.verify_precreated_helpers(FakePG(),SCHEMA,"load_aaaaaaaaaaaaaaaa_p6_23")

    def test_self_reference_and_cross_source_reference_are_rejected(self):
        for reference in (SCHEMA,"load_aaaaaaaaaaaaaaaa_p6_22"):
            with self.assertRaises(ValueError):trial.verify_precreated_helpers(FakePG(),SCHEMA,reference)


if __name__ == "__main__":unittest.main()
