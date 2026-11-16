"""Regression tests for the full-value parity gate.

Set P6_PARITY_TEST_CONNECTION_FILE and P6_PARITY_TEST_DATABASE to run the
database tests against a disposable database.  No source or report data needed.
"""
import importlib.util
import json
import os
from pathlib import Path
import unittest
import uuid

import psycopg
from psycopg import sql


spec = importlib.util.spec_from_file_location("compare_reports",Path(__file__).with_name("compare_reports.py"))
parity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parity)


class ProjectionPolicyTests(unittest.TestCase):
    def test_long_capture_names_are_stable_distinct_and_within_byte_limit(self):
        a = parity.capture_relation_name("construction_progress", "界" * 40)
        b = parity.capture_relation_name("construction_progress", "界" * 39 + "甲")
        self.assertLessEqual(len(a.encode()), 63)
        self.assertNotEqual(a,b)
        self.assertEqual(a,parity.capture_relation_name("construction_progress", "界" * 40))

    def test_unknown_normalization_is_rejected(self):
        with self.assertRaises(ValueError):
            parity.comparison_projection([{"name":"amount","type":"numeric(21,8)","category":"N"}],
                                         {"update_date"})

    def test_business_dates_are_never_automatically_normalized(self):
        known = set().union(*parity.CALCULATION_METADATA.values()) | parity.PUBLICATION_METADATA
        self.assertTrue({"data_date","updated_date","update_date","target_start_date",
                         "config_version_id","published_at","_snapshot_at"}.isdisjoint(known))


@unittest.skipUnless(os.environ.get("P6_PARITY_TEST_CONNECTION_FILE"),"Isolated PostgreSQL test connection not supplied")
class ExactComparisonTests(unittest.TestCase):
    def setUp(self):
        config=json.loads(Path(os.environ["P6_PARITY_TEST_CONNECTION_FILE"]).read_text())
        self.pg=psycopg.connect(**config)
        parity.assert_isolated_database(self.pg,os.environ["P6_PARITY_TEST_DATABASE"])
        self.schema="parity_capture_test_"+uuid.uuid4().hex[:16]
        self.pg.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        for name in ("reference","candidate"):
            self.pg.execute(sql.SQL("CREATE TABLE {}.{} (id integer,label text,amount numeric(21,8),business_date timestamp,check_run_id bigint)")
                            .format(sql.Identifier(self.schema),sql.Identifier(name)))

    def tearDown(self):
        self.pg.rollback()  # All fixtures are transaction-local: no persistent cleanup.
        self.pg.close()

    def insert(self,name,values):
        self.pg.execute(sql.SQL("INSERT INTO {}.{} VALUES (%s,%s,%s,%s,%s)")
                        .format(sql.Identifier(self.schema),sql.Identifier(name)),values)

    def compare(self,**kwargs):
        return parity.compare_relation(self.pg,(self.schema,"reference"),(self.schema,"candidate"),**kwargs)

    def test_identical_values_and_duplicates_pass(self):
        for name in ("reference","candidate"):
            self.insert(name,(1,"A", "1234567890123.12345678","2026-11-16 12:00",42))
            self.insert(name,(1,"A", "1234567890123.12345678","2026-11-16 12:00",42))
            self.insert(name,(2,None,None,None,None))
        self.assertTrue(self.compare()["passed"])

    def test_equal_counts_do_not_hide_duplicate_replacement(self):
        self.insert("reference",(1,"same",1,None,42))
        self.insert("reference",(1,"same",1,None,42))
        self.insert("candidate",(1,"same",1,None,42))
        self.insert("candidate",(2,"same",1,None,42))
        result=self.compare()
        self.assertFalse(result["passed"])
        self.assertEqual((result["reference_only_rows"],result["candidate_only_rows"]),(1,1))

    def test_null_empty_case_decimal_and_business_date_changes_fail(self):
        for baseline,candidate in (
            ((1,None,1,None,42),(1,"",1,None,42)),
            ((1,"A",1,None,42),(1,"a",1,None,42)),
            ((1,"a","1.00000001",None,42),(1,"a","1.00000002",None,42)),
            ((1,"a",1,"2026-11-16",42),(1,"a",1,"2026-11-17",42)),
        ):
            for name in ("reference","candidate"):
                self.pg.execute(sql.SQL("TRUNCATE {}.{}").format(sql.Identifier(self.schema),sql.Identifier(name)))
            self.insert("reference",baseline)
            self.insert("candidate",candidate)
            self.assertFalse(self.compare()["passed"])

    def test_metadata_is_strict_by_default_and_nullness_is_preserved(self):
        self.insert("reference",(1,"a",1,None,42))
        self.insert("candidate",(1,"a",1,None,43))
        self.assertFalse(self.compare()["passed"])
        self.assertTrue(self.compare(normalized_columns={"check_run_id"})["passed"])
        self.pg.execute(sql.SQL("UPDATE {}.candidate SET check_run_id=NULL").format(sql.Identifier(self.schema)))
        self.assertFalse(self.compare(normalized_columns={"check_run_id"})["passed"])

    def test_missing_or_changed_columns_fail(self):
        self.pg.execute(sql.SQL("ALTER TABLE {}.candidate ADD COLUMN unexpected text").format(sql.Identifier(self.schema)))
        result=self.compare()
        self.assertFalse(result["passed"])
        self.assertFalse(result["schema_equal"])


if __name__ == "__main__":
    unittest.main()
