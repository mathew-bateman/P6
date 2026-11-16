from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
import tempfile
import unittest

from ops.reporting.parity_compare import compare_cursor_rows, difference_samples


COLUMNS = [{"name": "proj_id", "type": "integer"}, {"name": "value", "type": "numeric(38,10)"}]


class ParityComparisonTests(unittest.TestCase):
    def test_order_independent_with_numeric_driver_types(self):
        result = compare_cursor_rows([(1,Decimal("7.00")),(2,Decimal("2"))], [(2,2),(1,7)], COLUMNS)
        self.assertTrue(result["matched"])
        self.assertEqual(result["rows"], [2,2])

    def test_duplicate_multiplicity_is_significant(self):
        result = compare_cursor_rows([(1,7),(1,7)], [(1,7)], COLUMNS)
        self.assertFalse(result["matched"])
        self.assertEqual(result["affected_project_ids"], [1])

    def test_equal_row_counts_and_global_sums_do_not_hide_changes(self):
        result = compare_cursor_rows([(1,2),(2,3)], [(1,3),(2,2)], COLUMNS)
        self.assertFalse(result["matched"])
        self.assertEqual(set(result["affected_project_ids"]), {1,2})

    def test_numeric_normalisation_does_not_round_to_decimal_context(self):
        with localcontext() as context:
            context.prec = 3
            a = Decimal("12345678901234567890123456789012345678")
            b = Decimal("12345678901234567890123456789012345679")
            self.assertFalse(compare_cursor_rows([(1,a)], [(1,b)], COLUMNS)["matched"])

    def test_null_is_not_zero_and_text_is_not_case_or_space_folded(self):
        self.assertFalse(compare_cursor_rows([(1,None)], [(1,0)], COLUMNS)["matched"])
        columns = [{"name":"text","type":"text"}]
        for left,right in [("A","a"),("hello","hello "),("é","e\u0301"),("1",1)]:
            with self.subTest(left=left,right=right):
                self.assertFalse(compare_cursor_rows([(left,)],[(right,)],columns)["matched"])

    def test_only_explicit_runtime_columns_are_ignored(self):
        columns = COLUMNS + [{"name":"check_run_id","type":"bigint"}]
        self.assertTrue(compare_cursor_rows([(1,7,1)],[(1,7,2)],columns)["matched"])
        self.assertFalse(compare_cursor_rows([(1,7,1)],[(1,7,2)],columns,ignored_runtime=set())["matched"])
        self.assertTrue(compare_cursor_rows([(1,7)],[(1,7)],columns)["matched"])

    def test_dictionary_rows_and_missing_column_detection(self):
        self.assertTrue(compare_cursor_rows([{"proj_id":1,"value":7}],[(1,7)],COLUMNS)["matched"])
        with self.assertRaises(KeyError):
            compare_cursor_rows([{"proj_id":1}],[(1,7)],COLUMNS)

    def test_numeric_driver_strings_preserve_project_group_identity(self):
        result = compare_cursor_rows([("1","7.00")],[(1,7)],COLUMNS)
        self.assertTrue(result["matched"])
        self.assertEqual(result["affected_project_ids"],[])
        self.assertEqual([row["proj_id"] for row in result["project_groups"]],[1])

    def test_json_key_order_number_syntax_and_escaping_are_not_differences(self):
        columns = [{"name":"detail_json","type":"text"}]
        left = '{"a":7.00,"b":"x\\/y","c":"\\u00e9"}'
        right = '{ "c":"é", "b":"x/y", "a":7 }'
        self.assertTrue(compare_cursor_rows([(left,)],[(right,)],columns)["matched"])

    def test_json_array_order_strings_boolean_and_null_remain_significant(self):
        columns = [{"name":"detail_json","type":"text"}]
        for left,right in [('[1,2]','[2,1]'),('{"a":7}','{"a":"7"}'),('{"a":true}','{"a":1}'),('{"a":null}','{}')]:
            with self.subTest(left=left,right=right):
                self.assertFalse(compare_cursor_rows([(left,)],[(right,)],columns)["matched"])

    def test_duplicate_json_properties_are_not_silently_discarded(self):
        columns = [{"name":"detail_json","type":"text"}]
        self.assertFalse(compare_cursor_rows([('{"a":1,"a":2}',)],[('{"a":2}',)],columns)["matched"])

    def test_invalid_json_fails_instead_of_being_silently_ignored(self):
        columns = [{"name":"detail_json","type":"text"}]
        with self.assertRaises(ValueError):
            compare_cursor_rows([('{bad}',)],[],columns)

    def test_binary_and_timestamp_instants_compare_losslessly(self):
        columns = [{"name":"binary","type":"bytea"},{"name":"at","type":"timestamp with time zone"}]
        first = datetime(2026,11,16,10,tzinfo=timezone.utc)
        second = datetime(2026,11,16,11,tzinfo=timezone(timedelta(hours=1)))
        self.assertTrue(compare_cursor_rows([(b"\x00\xff",first)],[(memoryview(b"\x00\xff"),second)],columns)["matched"])
        columns = [{"name":"at","type":"timestamp without time zone"}]
        self.assertFalse(compare_cursor_rows([(first.replace(tzinfo=None),)],[(second.replace(tzinfo=None),)],columns)["matched"])

    def test_date_contract_accepts_midnight_datetime_only(self):
        columns = [{"name":"at","type":"date"}]
        self.assertTrue(compare_cursor_rows([(date(2026,11,16),)],[(datetime(2026,11,16),)],columns)["matched"])
        self.assertFalse(compare_cursor_rows([(date(2026,11,16),)],[(datetime(2026,11,16,1),)],columns)["matched"])

    def test_empty_views_match_and_missing_projects_are_reported(self):
        self.assertTrue(compare_cursor_rows([],[],COLUMNS)["matched"])
        self.assertEqual(compare_cursor_rows([(9,1)],[],COLUMNS)["affected_project_ids"],[9])

    def test_samples_are_actual_multiset_differences_and_deterministic(self):
        first = [(1,7),(1,7),(2,4)]
        second = [(1,7),(3,8)]
        a = difference_samples(iter(first),iter(second),COLUMNS,limit=2)
        b = difference_samples(iter(reversed(first)),iter(reversed(second)),COLUMNS,limit=2)
        self.assertEqual(a,b)
        self.assertEqual(a["rows"],[3,2])
        self.assertEqual(a["different_row_values"],3)
        self.assertTrue(a["samples_truncated"])
        self.assertTrue(all(row["sql_count"]!=row["postgres_count"] for row in a["samples"]))

    def test_sample_scratch_is_removed_on_success_and_error(self):
        with tempfile.TemporaryDirectory() as directory:
            difference_samples([(1,7)],[(1,7)],COLUMNS,scratch_directory=directory)
            self.assertEqual(list(Path(directory).iterdir()),[])
            with self.assertRaises(KeyError):
                difference_samples([{"proj_id":1}],[],COLUMNS,scratch_directory=directory)
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_invalid_comparison_layout_is_rejected(self):
        for columns in [[],[{"name":"a"},{"name":"a"}],[{"name":"check_run_id"}]]:
            with self.subTest(columns=columns),self.assertRaises(ValueError):
                compare_cursor_rows([],[],columns)
        with self.assertRaises(ValueError):
            compare_cursor_rows([(1,)],[],COLUMNS)


if __name__ == "__main__":
    unittest.main()
