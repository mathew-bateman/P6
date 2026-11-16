"""Verification tests: changed values/nulls/rows fail; batch sizes are irrelevant."""
from decimal import Decimal
from datetime import datetime
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pyarrow as pa

from arrow_transfer import compare_streams, normalise_batch, type_mapping
import arrow_transfer


class NativeVerificationTests(unittest.TestCase):
    def setUp(self):
        self.schema = pa.schema([("id", pa.int32()), ("amount", pa.decimal128(38, 6)),
                                 ("name", pa.string()), ("when", pa.timestamp("us"))])
        self.table = pa.table({
            "id": [1, 2, 3, 4, 5],
            "amount": [Decimal("12345678901234567890123456789012.123456"), None,
                       Decimal("0.000000"), Decimal("-1.234567"), Decimal("3.100000")],
            "name": ["é\tA\n", "", None, "你好", " ",],
            "when": [datetime(2026, 11, 16, 12, 0, 0, 123456), None,
                     datetime(1753, 1, 1), datetime(9999, 12, 31), datetime(2000, 1, 1)],
        }, schema=self.schema)

    def test_every_value_matches_across_different_batch_boundaries(self):
        self.assertEqual(compare_streams(self.table.to_batches(max_chunksize=2),
                                         self.table.to_batches(max_chunksize=3), self.schema), 5)

    def test_high_precision_decimal_difference_fails(self):
        changed = self.table.set_column(1, self.schema[1], pa.array([
            Decimal("12345678901234567890123456789012.123457"), None,
            Decimal("0.000000"), Decimal("-1.234567"), Decimal("3.100000")], type=self.schema[1].type))
        with self.assertRaisesRegex(ValueError, "amount"):
            compare_streams(self.table.to_batches(), changed.to_batches(), self.schema)

    def test_null_is_distinct_from_empty_string(self):
        changed = self.table.set_column(2, self.schema[2], pa.array(["é\tA\n", "", "", "你好", " "]))
        with self.assertRaisesRegex(ValueError, "name"):
            compare_streams(self.table.to_batches(), changed.to_batches(), self.schema)

    def test_missing_rows_fail(self):
        with self.assertRaisesRegex(ValueError, "row counts"):
            compare_streams(self.table.to_batches(max_chunksize=2),
                            self.table.slice(0, 4).to_batches(max_chunksize=3), self.schema)

    def test_duplicate_replaces_missing_row_at_same_total_count(self):
        changed = self.table.take(pa.array([0, 1, 1, 3, 4]))
        self.assertEqual(changed.num_rows, self.table.num_rows)
        with self.assertRaisesRegex(ValueError, "Full-value verification failed"):
            compare_streams(self.table.to_batches(max_chunksize=2),
                            changed.to_batches(max_chunksize=3), self.schema)

    def test_empty_tables_pass(self):
        empty = self.table.slice(0, 0)
        empty_batch = pa.RecordBatch.from_arrays([pa.array([], type=f.type) for f in self.schema], schema=self.schema)
        self.assertEqual(compare_streams(empty.to_batches(), [empty_batch], self.schema), 0)

    def test_changed_microsecond_fails(self):
        times = self.table.column(3).to_pylist()
        times[0] = datetime(2026, 11, 16, 12, 0, 0, 123457)
        changed = self.table.set_column(3, self.schema[3], pa.array(times, type=pa.timestamp("us")))
        with self.assertRaisesRegex(ValueError, "when"):
            compare_streams(self.table.to_batches(), changed.to_batches(), self.schema)

    def test_destination_numeric_string_is_exactly_cast(self):
        string_columns = [self.table.column(0), self.table.column(1).cast(pa.string()),
                          self.table.column(2), self.table.column(3)]
        string_table = pa.Table.from_arrays(string_columns, names=self.schema.names)
        self.assertEqual(compare_streams(self.table.to_batches(), string_table.to_batches(), self.schema), 5)

    def test_numeric_precision_is_not_downcast_to_float(self):
        pg_type, arrow_type = type_mapping({"type_name": "numeric", "precision": 38, "scale": 6})
        self.assertEqual(pg_type, "numeric(38,6)")
        self.assertEqual(arrow_type, pa.decimal128(38, 6))

    def test_unvalidated_type_fails_closed(self):
        with self.assertRaises(ValueError):
            type_mapping({"type_name": "uniqueidentifier"})

    def test_nanosecond_timestamp_loss_fails_closed(self):
        schema = pa.schema([("when", pa.timestamp("us"))])
        batch = pa.record_batch([pa.array([1], type=pa.timestamp("ns"))], names=["when"])
        with self.assertRaises(pa.ArrowInvalid):
            normalise_batch(batch, schema)


class PrecreatedSchemaTests(unittest.TestCase):
    """A precreated catalogue must never become an overwrite/reuse escape hatch."""

    def setUp(self):
        self.config = {
            "benchmark_only": True,
            "schema": "native_shadow_0123456789abcdef_p6_22",
            "source_code": "p6_22",
            "helper_reference_schema": "load_aaaaaaaaaaaaaaaa_p6_22",
            "postgres": {},
            "schema_precreated": True,
            "tables": [],
        }
        self.pg = Mock()
        self.verifier = Mock(return_value={"verified": True, "function_count": 2})
        self.helper_patch = patch.dict("sys.modules", {
            "all_project_calc": SimpleNamespace(verify_precreated_helpers=self.verifier),
        })
        self.helper_patch.start()
        self.addCleanup(self.helper_patch.stop)

    def test_cross_source_schema_is_rejected_before_catalogue_queries(self):
        self.config["schema"] = "native_shadow_0123456789abcdef_p6_23"
        with self.assertRaisesRegex(ValueError, "different P6 source"):
            arrow_transfer.validate_precreated_schema(self.pg, self.config)
        self.pg.execute.assert_not_called()
        self.verifier.assert_not_called()

    def test_any_existing_relation_blocks_reuse_before_helper_verification(self):
        for relation_count in (1, 12):
            with self.subTest(relation_count=relation_count):
                self.pg.reset_mock()
                self.pg.execute.return_value.fetchone.return_value = {"n": relation_count}
                with self.assertRaisesRegex(ValueError, "contain no relations"):
                    arrow_transfer.validate_precreated_schema(self.pg, self.config)
                self.pg.execute.assert_called_once()
                self.assertEqual(self.pg.execute.call_args.args[1], (self.config["schema"],))
                self.verifier.assert_not_called()

    def test_empty_schema_returns_exact_strict_helper_verification_result(self):
        self.pg.execute.return_value.fetchone.return_value = {"n": 0}
        result = arrow_transfer.validate_precreated_schema(self.pg, self.config)
        self.assertIs(result, self.verifier.return_value)
        self.verifier.assert_called_once_with(self.pg, self.config["schema"],
                                              self.config["helper_reference_schema"])

    def test_missing_or_modified_helper_catalogue_fails_closed(self):
        self.pg.execute.return_value.fetchone.return_value = {"n": 0}
        self.verifier.side_effect = RuntimeError("helper definitions differ")
        with self.assertRaisesRegex(RuntimeError, "helper definitions differ"):
            arrow_transfer.validate_precreated_schema(self.pg, self.config)

    def test_verified_precreated_catalogue_is_preserved_in_transfer_receipt(self):
        connection = MagicMock()
        pg = connection.__enter__.return_value
        pg.execute.return_value.fetchone.side_effect = [
            {"cluster_name": "p6-perf-isolated"}, {"n": 0},
        ]
        with patch.object(arrow_transfer.psycopg, "connect", return_value=connection), \
             patch.object(arrow_transfer, "ThreadPoolExecutor") as pool:
            pool.return_value.__enter__.return_value.map.return_value = []
            receipt = arrow_transfer.run(self.config)
        self.assertIs(receipt["precreated_helper_verification"], self.verifier.return_value)
        self.verifier.assert_called_once_with(pg, self.config["schema"],
                                              self.config["helper_reference_schema"])
        # Only cluster and emptiness reads ran; CREATE SCHEMA was skipped.
        self.assertEqual(pg.execute.call_count, 2)
        self.assertTrue(all("CREATE" not in str(call.args[0]).upper()
                            for call in pg.execute.call_args_list))

    def test_non_boolean_precreated_flag_never_creates_or_loads_tables(self):
        # The current run guard checks the flag after reading cluster_name. Mock
        # that read and the connection; this test performs no database I/O.
        for value in ("true", 1, 0, None, [], {}):
            with self.subTest(value=value):
                connection = MagicMock()
                pg = connection.__enter__.return_value
                pg.execute.return_value.fetchone.return_value = {"cluster_name": "p6-perf-isolated"}
                self.config["schema_precreated"] = value
                with patch.object(arrow_transfer.psycopg, "connect", return_value=connection), \
                     patch.object(arrow_transfer, "ThreadPoolExecutor") as pool, \
                     self.assertRaisesRegex(ValueError, "schema_precreated must be a bool"):
                    arrow_transfer.run(self.config)
                pg.execute.assert_called_once_with("SHOW cluster_name")
                pool.assert_not_called()
                self.verifier.assert_not_called()


if __name__ == "__main__":
    unittest.main()
