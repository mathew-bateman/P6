"""Safety/ordering tests for the isolated scope experiment; full parity is SQL-backed."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ops.performance import scope_trial as trial


SCHEMA = "native_test_0123456789abcdef_p6_23"


class FakePG:
    def __init__(self, database="p6_perf_test"):
        self.database = database

    def execute(self, query):
        self.query = query
        return self

    def fetchone(self):
        return {"database": self.database} if "current_database" in self.query else {"n": 2}


class ScopeSafetyTests(unittest.TestCase):
    def test_production_database_rejected_before_any_calculation(self):
        with self.assertRaisesRegex(RuntimeError, "isolated"):
            trial.calculate_scoped(FakePG("p6_reporting"), SCHEMA, 37, 1, engine=object())

    def test_query_does_not_restrict_raw_input_or_delete_context(self):
        original = "SELECT t.proj_id,t.task_id FROM raw.task t LEFT JOIN raw.task predecessor ON true"
        scoped = trial.narrow_query(original, SCHEMA)
        self.assertIn(original, scoped)
        self.assertIn("perf_selected.proj_id=perf_output.proj_id", scoped)
        self.assertNotIn("DELETE", scoped)

    def test_invalid_schema_rejected(self):
        with self.assertRaises(ValueError):
            trial.narrow_query("SELECT proj_id FROM project", "public; DROP TABLE task")

    def test_raw_scope_pushes_project_selection_inside_group_input(self):
        query = 'SELECT r.proj_id,r.task_id,count(*) FROM "' + SCHEMA + '".taskpred AS r GROUP BY r.proj_id,r.task_id'
        scoped = trial.push_scope_into_raw_reads(query, SCHEMA)
        self.assertIn('FROM (SELECT perf_raw.* FROM "' + SCHEMA + '"."taskpred" AS perf_raw WHERE EXISTS', scoped)
        self.assertIn('perf_scope_project.proj_id=perf_raw.proj_id', scoped)
        self.assertTrue(scoped.endswith('AS r GROUP BY r.proj_id,r.task_id'))
        self.assertNotIn('delete_date', scoped)  # Do not alter original deleted/live semantics.

    def test_task_assignment_scope_uses_actual_task_project(self):
        query = 'SELECT a.task_id FROM "' + SCHEMA + '"."taskactv" AS a'
        scoped = trial.push_scope_into_raw_reads(query, SCHEMA)
        self.assertIn('perf_scope_task.task_id=perf_raw.task_id', scoped)
        self.assertIn('perf_scope_project.proj_id=perf_scope_task.proj_id', scoped)
        self.assertNotIn('perf_scope_project.proj_id=perf_raw.proj_id', scoped)

    def test_raw_scope_does_not_touch_other_sources_or_non_input_references(self):
        query = 'SELECT * FROM other.task t JOIN "' + SCHEMA + '".project p ON p.proj_id=t.proj_id'
        self.assertEqual(trial.push_scope_into_raw_reads(query, SCHEMA), query)

    def test_projects_built_before_scoped_activities_and_patches_restored(self):
        calls = []
        engine = SimpleNamespace(BASE_VIEWS=["activities", "projects"], FUNCTION_TABLES=["activity_quality"])

        def materialize(pg, schema, name, query, **options):
            calls.append((name, query))
            return {"name": name}

        def calculate(pg, schema, config_version_id, check_run_id, **options):
            manifest = []
            for suffix in engine.BASE_VIEWS:
                manifest.append(engine.materialize(pg, schema, "xertoolkit_vw_pbi_" + suffix,
                    "SELECT proj_id FROM task", contract=[{"name": "proj_id"}]))
            engine.materialize(pg, schema, "_deleted_activities", "SELECT task_id FROM task")
            engine.materialize(pg, schema, "xertoolkit_fn_activity_quality", "SELECT proj_id FROM task")
            return manifest, {"status": "success"}

        engine.materialize, engine.calculate = materialize, calculate
        with patch.object(trial, "prepare_scope", side_effect=lambda pg, schema: calls.append(("selection", ""))):
            manifest, quality = trial.calculate_scoped(FakePG(), SCHEMA, 37, 1, engine=engine)
        self.assertEqual([c[0] for c in calls], ["xertoolkit_vw_pbi_projects", "selection", "xertoolkit_vw_pbi_activities", "_deleted_activities", "xertoolkit_fn_activity_quality"])
        self.assertNotIn("perf_selected", calls[0][1])
        self.assertIn("perf_selected", calls[2][1])
        self.assertNotIn("perf_selected", calls[3][1])
        self.assertIn("perf_selected", calls[4][1])
        self.assertIs(engine.materialize, materialize)
        self.assertEqual(engine.BASE_VIEWS, ["activities", "projects"])
        self.assertEqual(quality["selected_project_count"], 2)

    def test_exception_restores_process_global_patches(self):
        original = lambda *a, **k: None
        engine = SimpleNamespace(BASE_VIEWS=["activities", "projects"], FUNCTION_TABLES=[], materialize=original,
                                 calculate=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fixture")))
        with self.assertRaisesRegex(RuntimeError, "fixture"):
            trial.calculate_scoped(FakePG(), SCHEMA, 37, 1, engine=engine)
        self.assertIs(engine.materialize, original)
        self.assertEqual(engine.BASE_VIEWS, ["activities", "projects"])


if __name__ == "__main__":
    unittest.main()
