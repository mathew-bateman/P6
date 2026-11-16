"""All-project acceptance must detect losses hidden by latest-only publication."""
import importlib.util
import json
import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

spec = importlib.util.spec_from_file_location("compare_all_projects", Path(__file__).with_name("compare_all_projects.py"))
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


def fixture_contracts():
    outputs = {"xertoolkit_vw_pbi_fixture_" + str(i): [{"name": "proj_id", "type": "integer"}]
               for i in range(20)}
    return {"published_by_source": {"p6_22": outputs, "p6_23": outputs}}


RAW = {"PROJECT"} | {"fixture_raw_" + str(i) for i in range(20)}


class AllProjectPolicyTests(unittest.TestCase):
    def test_complete_inventory_includes_historical_internal_tables(self):
        entries = trial.relation_inventory("p6_22", fixture_contracts(), RAW)
        self.assertEqual(len(entries), 59)
        self.assertTrue(set(trial.RESULT_TABLES) <= {n for _, n in entries})
        self.assertTrue(set(trial.FUNCTION_TABLES) <= {n for _, n in entries})

    def test_comparison_never_passes_latest_filter_or_metadata_exclusions(self):
        pg = unittest.mock.MagicMock()
        pg.execute.return_value.fetchone.return_value = {"n": 2}
        with patch.object(trial, "require_isolated"), patch.object(trial, "compare_relation",
            return_value={"passed": True}) as comparator:
            receipt = trial.compare_all_generations(pg, "native_test_0123456789abcdef_p6_22",
                "native_shadow_0123456789abcdef_p6_22", "p6_22", fixture_contracts(), RAW)
        self.assertEqual(comparator.call_count, 59)
        self.assertTrue(all(c.kwargs == {"scoped": False, "normalized_columns": ()}
                            for c in comparator.call_args_list))
        self.assertFalse(receipt["publication_filter_applied"])
        self.assertEqual(receipt["metadata_exclusions"], [])

    def test_historical_select_contains_no_project_selection(self):
        query = trial.history_select("native_shadow_0123456789abcdef_p6_22",
            {"name": "example", "columns": [{"name": "proj_id", "type": "integer"}]},
            "p6_22", {"database": "P62212_1", "snapshot_at": "2026-11-16T16:32:00+00:00"},
            "batch", None).as_string()
        self.assertNotIn("WHERE", query.upper())
        self.assertNotIn("latest", query.lower())
        self.assertIn('"native_shadow_0123456789abcdef_p6_22"."example"', query)

    def test_partial_sources_or_contracts_are_rejected(self):
        with self.assertRaises(ValueError):
            trial.validate_manifests({"p6_22": "native_shadow_0123456789abcdef_p6_22"}, {"p6_22": []})
        with self.assertRaises(ValueError):
            trial.relation_inventory("p6_22", fixture_contracts(), {"PROJECT"})

    def test_legacy_inventory_detects_missing_old_view(self):
        names = list(fixture_contracts()["published_by_source"]["p6_22"])
        plan = {"catalogue": {"objects": [{"type": "V", "name": name.upper()} for name in names]}}
        self.assertTrue(trial.legacy_view_inventory(plan, fixture_contracts())["passed"])
        plan["catalogue"]["objects"][0]["name"] = "xertoolkit_vw_pbi_missing"
        result = trial.legacy_view_inventory(plan, fixture_contracts())
        self.assertFalse(result["passed"])
        self.assertEqual(result["sources"]["p6_22"]["missing_legacy_views"], ["xertoolkit_vw_pbi_missing"])


@unittest.skipUnless(os.environ.get("P6_PARITY_TEST_CONNECTION_FILE"), "Isolated PG connection not supplied")
class HistoricalLossTests(unittest.TestCase):
    def setUp(self):
        self.pg = psycopg.connect(**json.loads(Path(os.environ["P6_PARITY_TEST_CONNECTION_FILE"]).read_text()),
                                 row_factory=psycopg.rows.dict_row)
        trial.require_isolated(self.pg, os.environ["P6_PARITY_TEST_DATABASE"])
        self.source = "p6_22"
        self.reference = "native_test_" + uuid.uuid4().hex[:16] + "_p6_22"
        self.candidate = "native_shadow_" + uuid.uuid4().hex[:16] + "_p6_22"
        self.contracts = fixture_contracts()
        for schema in (self.reference, self.candidate):
            self.pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            for _, name in trial.relation_inventory(self.source, self.contracts, RAW):
                self.pg.execute(sql.SQL("CREATE TABLE {}.{} (proj_id integer,value text)")
                    .format(sql.Identifier(schema), sql.Identifier(name)))
                rows = [(1, "latest")] if name == trial.SELECTION_TABLE else [(1, "latest"), (2, "historical")]
                with self.pg.cursor() as cursor:
                    cursor.executemany(sql.SQL("INSERT INTO {}.{} VALUES (%s,%s)")
                        .format(sql.Identifier(schema), sql.Identifier(name)), rows)

    def tearDown(self):
        self.pg.rollback()
        self.pg.close()

    def compare(self):
        return trial.compare_all_generations(self.pg, self.reference, self.candidate, self.source,
            self.contracts, RAW, expected_database=os.environ["P6_PARITY_TEST_DATABASE"])

    def test_historical_calculation_value_loss_is_detected_although_latest_matches(self):
        self.assertTrue(self.compare()["passed"])
        table = trial.RESULT_TABLES[0]
        self.pg.execute(sql.SQL("UPDATE {}.{} SET value='changed' WHERE proj_id=2")
            .format(sql.Identifier(self.candidate), sql.Identifier(table)))
        result = self.compare()
        self.assertFalse(result["passed"])
        failed = [r for r in result["results"] if not r["passed"]]
        self.assertEqual([r["kind"] for r in failed], ["result"])

    def test_latest_only_output_and_duplicate_replacement_are_detected(self):
        table = "xertoolkit_vw_pbi_fixture_0"
        self.pg.execute(sql.SQL("DELETE FROM {}.{} WHERE proj_id=2")
            .format(sql.Identifier(self.candidate), sql.Identifier(table)))
        self.assertFalse(self.compare()["passed"])
        self.pg.execute(sql.SQL("INSERT INTO {}.{} VALUES (1,'latest')")
            .format(sql.Identifier(self.candidate), sql.Identifier(table)))
        result = self.compare()
        self.assertFalse(result["passed"])
        failed = next(r for r in result["results"] if not r["passed"])
        self.assertEqual(failed["reference_rows"], failed["candidate_rows"])
        self.assertEqual((failed["reference_only_rows"], failed["candidate_only_rows"]), (1,1))


if __name__ == "__main__":
    unittest.main()
