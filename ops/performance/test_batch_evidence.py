"""Renderer regression tests; the optional PostgreSQL fixture checks exact bytes.

Local: python -m unittest discover -s ops/performance -p test_batch_evidence.py
Integration additionally needs P6_BATCH_EVIDENCE_TEST_DSN and
P6_LEGACY_EVIDENCE_PATH, and refuses databases outside the isolated benchmark.
"""
from importlib.util import module_from_spec, spec_from_file_location
import os
from pathlib import Path
import re
import sys
from types import ModuleType
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg import sql


def load_renderer(path, name):
    # The repository's native engine is currently supplied by the deployed app;
    # the renderer itself only needs its two identifier helpers for local tests.
    helpers = ModuleType("reporting_mirror.native_engine")
    helpers.identifier = lambda value: sql.Identifier(value).as_string()

    def generation_schema(value):
        if not re.fullmatch(r"(?:load|native_test|native_shadow)_[0-9a-f]{16}_p6_(?:22|23)", value):
            raise ValueError("Native calculations require an isolated generation schema.")
        return helpers.identifier(value)

    helpers.generation_schema = generation_schema
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    with patch.dict(sys.modules, {"reporting_mirror.native_engine": helpers}):
        spec.loader.exec_module(module)
    return module


renderer = load_renderer(os.environ.get("P6_BATCH_EVIDENCE_RENDERER_PATH") or
                         Path(__file__).resolve().parents[2] / "reporting_mirror/native_evidence.py", "batch_evidence")
SCHEMA = "native_test_0123456789abcdef_p6_23"
TABLES = {
    "task": {"proj_id": "integer", "task_id": "integer", "wbs_id": "integer", "task_name": "text",
             "target_drtn_hr_cnt": "numeric", "start_date": "timestamp without time zone", "cstr_type": "text",
             "delete_session_id": "integer"},
    "taskpred": {"proj_id": "integer", "task_id": "integer", "pred_task_id": "integer", "task_pred_id": "integer",
                 "pred_type": "text", "lag_hr_cnt": "numeric", "delete_session_id": "integer"},
    "project": {"proj_id": "integer", "proj_short_name": "text"},
}


def field(number, check="quality", category="task_column", column="task_name", label=None, order=1, display="native"):
    return {"detail_field_id": number, "check_code": check, "source_category": category, "source_identifier": column,
            "display_label": label or f"Field {number}", "sort_order": order, "display_format": display}


class FakePG:
    def __init__(self, fields, duplicates=False):
        self.fields, self.duplicates, self.calls = fields, duplicates, []

    def execute(self, query, parameters=None):
        self.query = query
        self.calls.append((query, parameters))
        return self

    def fetchall(self):
        if "FROM pg_attribute" in self.query:
            return [{"relname": table, "attname": column, "data_type": data_type}
                    for table, columns in TABLES.items() for column, data_type in columns.items()]
        return sorted(self.fields, key=lambda f: (f["check_code"], f["sort_order"], f["detail_field_id"]))

    def fetchone(self):
        return {"duplicate_task_keys": self.duplicates}

    @property
    def inserts(self):
        return [call for call in self.calls if call[0].startswith("INSERT")]


class BatchEvidenceTests(unittest.TestCase):
    def test_each_check_joins_evidence_and_task_once_with_bound_metadata(self):
        pg = FakePG([field(3, order=2), field(1, label="Quotes ' and %s"),
                     field(2, column="target_drtn_hr_cnt", display="p6_hours_and_days"), field(4, check="another")])
        renderer.render_configured_evidence(pg, SCHEMA, 37)
        self.assertEqual(len(pg.inserts), 2)
        query, parameters = pg.inserts[1]
        self.assertEqual(query.count("JOIN " + renderer.generation_schema(SCHEMA) + ".task task_record"), 1)
        self.assertIn("CROSS JOIN LATERAL (VALUES", query)
        self.assertIn('task_record."task_name"', query)
        self.assertNotIn("FROM " + renderer.generation_schema(SCHEMA) + '."task" source_row', query)
        self.assertNotIn("Quotes ' and %s", query)
        self.assertEqual(parameters, (1, "Quotes ' and %s", 1, 2, "Field 2", 1, 3, "Field 3", 2, "quality"))
        self.assertIn("' hours | '", query)
        self.assertIn("COALESCE(CASE WHEN", query)

    def test_open_end_task_fields_keep_other_endpoint_aggregation(self):
        for check, anchor, other in (("open_start", "task_id", "pred_task_id"), ("open_finish", "pred_task_id", "task_id")):
            expression = renderer._field_resolution(SCHEMA, TABLES, field(1, check=check), 37, True)
            self.assertIn("string_agg(DISTINCT", expression)
            self.assertIn(f"r.{anchor}=evidence.task_id AND r.{other}=source_row.task_id", expression)
            self.assertIn("source_row.delete_session_id IS NULL", expression)
            self.assertIn("p6_text_ci", expression)

    def test_multivalue_relationship_activity_code_and_udf_rules_are_kept(self):
        relationship = renderer._field_resolution(SCHEMA, TABLES, field(1, check="relationship_leads", category="relationship_column", column="pred_type"), 37, True)
        self.assertIn("r.is_lead=1", relationship)
        self.assertIn("replace(source_row.\"pred_type\",'PR_','')", relationship)
        for category in ("activity_code", "udf"):
            expression = renderer._field_resolution(SCHEMA, TABLES, field(1, category=category, column="42"), 37, True)
            self.assertIn("string_agg(DISTINCT", expression)
            self.assertIn("ORDER BY", expression)
            self.assertIn("p6_text_ci", expression)
        self.assertIn("uv.delete_date IS NULL", expression)
        self.assertIn("ut.delete_date IS NULL", expression)
        self.assertIn("WHEN 'TASKRSRC' THEN EXISTS", expression)

    def test_duplicate_task_keys_restore_complete_original_insert_path(self):
        pg = FakePG([field(1), field(2, column="target_drtn_hr_cnt")], duplicates=True)
        renderer.render_configured_evidence(pg, SCHEMA, 37)
        self.assertEqual(len(pg.inserts), 2)
        self.assertTrue(all("CROSS JOIN LATERAL" not in query for query, _ in pg.inserts))
        self.assertTrue(all("string_agg(DISTINCT" in query for query, _ in pg.inserts))
        self.assertEqual(pg.inserts[0][1], (1, "Field 1", 1, "quality"))

    def test_original_json_duplicate_label_order_and_display_limit_are_kept(self):
        pg = FakePG([field(1, label="Same"), field(2, label="Same")])
        renderer.render_configured_evidence(pg, SCHEMA, 37)
        update = next(query for query, _ in pg.calls if query.startswith("UPDATE"))
        self.assertIn("json_object_agg(display_label,detail_value ORDER BY sort_order,detail_field_id)::text", update)
        self.assertNotIn("jsonb", update)
        self.assertIn("E'\\r\\n' ORDER BY sort_order,detail_field_id),1000)", update)
        self.assertEqual(pg.inserts[0][1], (1, "Same", 1, 2, "Same", 1, "quality"))

    def test_missing_columns_and_empty_configuration_keep_original_behavior(self):
        pg = FakePG([field(1, column="not_a_column")])
        renderer.render_configured_evidence(pg, SCHEMA, 37)
        self.assertIn("COALESCE(NULL::text,'N/A')", pg.inserts[0][0])
        empty = FakePG([])
        renderer.render_configured_evidence(empty, SCHEMA, 37)
        self.assertEqual(empty.inserts, [])
        self.assertTrue(empty.calls[-1][0].startswith("DROP TABLE"))


@unittest.skipUnless(os.environ.get("P6_BATCH_EVIDENCE_TEST_DSN") and os.environ.get("P6_LEGACY_EVIDENCE_PATH"),
                     "optional isolated PostgreSQL fixture requires DSN and archived original renderer")
class PostgreSQLParityTests(unittest.TestCase):
    def test_exact_legacy_json_and_display_bytes_including_duplicates_and_nulls(self):
        legacy = load_renderer(os.environ["P6_LEGACY_EVIDENCE_PATH"], "original_evidence_fixture")
        with psycopg.connect(os.environ["P6_BATCH_EVIDENCE_TEST_DSN"], row_factory=dict_row) as pg:
            identity = pg.execute("SELECT current_database() AS database,current_setting('cluster_name') AS cluster").fetchone()
            if not identity["database"].startswith("p6_perf_") or identity["cluster"] != "p6-perf-isolated":
                self.fail("PostgreSQL fixture requires the isolated p6_perf_ benchmark database")
            # Rollback makes even fixture schemas disappear. Never uses a live schema.
            for duplicates in (False, True):
                schema = "native_test_" + uuid4().hex[:16] + "_p6_23"
                s = renderer.generation_schema(schema)
                self._create_fixture(pg, s, duplicates)
                legacy.render_configured_evidence(pg, schema, 37)
                old = pg.execute(f"SELECT proj_id,check_code,task_id,convert_to(detail_json,'UTF8') AS json,convert_to(detail_display,'UTF8') AS display FROM {s}.xertoolkit_result_schedule_quality_task_evidence ORDER BY proj_id,check_code,task_id").fetchall()
                pg.execute(f"UPDATE {s}.xertoolkit_result_schedule_quality_task_evidence SET detail_json='unchanged',detail_display='unchanged'")
                renderer.render_configured_evidence(pg, schema, 37)
                new = pg.execute(f"SELECT proj_id,check_code,task_id,convert_to(detail_json,'UTF8') AS json,convert_to(detail_display,'UTF8') AS display FROM {s}.xertoolkit_result_schedule_quality_task_evidence ORDER BY proj_id,check_code,task_id").fetchall()
                self.assertEqual(old, new, f"TASK duplicates={duplicates}")
                quality = next(row for row in new if row["check_code"] == "quality" and row["task_id"] == 10)
                self.assertEqual(len(bytes(quality["display"]).decode("utf8")), 1000)
                self.assertEqual(bytes(quality["json"]).count(b'"Same"'), 4 if duplicates else 2)
                self.assertIn(b"16 Nov 2026 10:23", bytes(quality["json"]))
                self.assertIn(b"Must start on", bytes(quality["json"]))
                self.assertIn(b"12.50 hours | 1.56 days (8h/day)", bytes(quality["json"]))
                open_start = next(row for row in new if row["check_code"] == "open_start")
                self.assertIn(b"Alpha, Zulu", bytes(open_start["json"]))
                self.assertNotIn(b"Deleted", bytes(open_start["json"]))
                null_task = next(row for row in new if row["check_code"] == "quality" and row["task_id"] == 11)
                self.assertIn(b"N/A", bytes(null_task["json"]))
                self.assertIn(b"\r\n", bytes(null_task["display"]))
                untouched = next(row for row in new if row["check_code"] == "no_configuration")
                self.assertEqual(bytes(untouched["json"]), b"unchanged")
                pg.execute(f"DROP SCHEMA {s} CASCADE")
            pg.rollback()

    @staticmethod
    def _create_fixture(pg, s, duplicates):
        pg.execute(f"CREATE SCHEMA {s}")
        pg.execute(f"CREATE COLLATION {s}.p6_text_ci(provider=icu,locale='und-u-ks-level2',deterministic=false)")
        pg.execute(f"CREATE TABLE {s}.task(proj_id integer,task_id integer,wbs_id integer,task_name text,target_drtn_hr_cnt numeric,start_date timestamp,cstr_type text,delete_session_id integer)")
        pg.execute(f"INSERT INTO {s}.task VALUES (1,10,5,'A' || repeat('x',1100),12.5,'2026-11-16 10:23','CS_MSO',NULL),(1,11,5,'Zulu',NULL,NULL,NULL,NULL),(1,12,5,'Alpha',8,NULL,NULL,NULL),(1,13,5,'Deleted',8,NULL,NULL,1),(2,10,5,'Other project',9,NULL,NULL,NULL)")
        if duplicates:
            pg.execute(f"INSERT INTO {s}.task VALUES (1,10,6,'Duplicate key',17,NULL,NULL,NULL)")
        pg.execute(f"CREATE TABLE {s}.taskpred(proj_id integer,task_id integer,pred_task_id integer,task_pred_id integer,pred_type text,lag_hr_cnt numeric,delete_session_id integer)")
        pg.execute(f"INSERT INTO {s}.taskpred VALUES (1,10,11,1,'PR_FS',-4,NULL),(1,10,12,2,'PR_SS',8,NULL),(1,13,10,3,'PR_FF',0,NULL),(1,10,13,4,'PR_FS',0,1),(1,11,10,5,'PR_FS',2,NULL)")
        pg.execute(f"CREATE TABLE {s}.xertoolkit_fn_relationship_quality(relationship_id integer,predecessor_task_id integer,successor_task_id integer,is_lead integer)")
        pg.execute(f"INSERT INTO {s}.xertoolkit_fn_relationship_quality VALUES (1,11,10,1),(2,12,10,0),(5,10,11,1)")
        pg.execute(f"CREATE TABLE {s}.xertoolkit_schedule_quality_constraint_type(config_version_id integer,constraint_type_code text,display_name text)")
        pg.execute(f"INSERT INTO {s}.xertoolkit_schedule_quality_constraint_type VALUES (37,'CS_MSO','Must start on')")
        pg.execute(f"CREATE TABLE {s}.taskactv(task_id integer,actv_code_id integer)")
        pg.execute(f"CREATE TABLE {s}.actvcode(actv_code_id integer,actv_code_type_id integer,actv_code_name text)")
        pg.execute(f"INSERT INTO {s}.taskactv VALUES (10,1),(10,1),(10,2),(10,3)")
        pg.execute(f"INSERT INTO {s}.actvcode VALUES (1,42,'Zulu'),(2,42,'Alpha'),(3,42,NULL)")
        pg.execute(f"CREATE TABLE {s}.udfvalue(udf_type_id integer,udf_text text,udf_date timestamp,udf_number numeric,udf_code_id integer,delete_session_id integer,delete_date timestamp,proj_id integer,fk_id integer)")
        pg.execute(f"CREATE TABLE {s}.udftype(udf_type_id integer,table_name text,delete_session_id integer,delete_date timestamp)")
        pg.execute(f"INSERT INTO {s}.udftype VALUES (42,'TASK',NULL,NULL)")
        pg.execute(f"INSERT INTO {s}.udfvalue VALUES (42,'Zulu',NULL,NULL,NULL,NULL,NULL,1,10),(42,'Alpha',NULL,NULL,NULL,NULL,NULL,1,10),(42,'Ignored',NULL,NULL,NULL,1,NULL,1,10)")
        pg.execute(f"CREATE TABLE {s}.taskrsrc(taskrsrc_id integer,task_id integer,proj_id integer)")
        pg.execute(f"CREATE TABLE {s}.xertoolkit_schedule_quality_detail_field(detail_field_id bigint,check_code text,source_category text,source_identifier text,display_label text,sort_order integer,display_format text,config_version_id integer)")
        fields = [field(1, label="Same"), field(2, label="Same", column="target_drtn_hr_cnt", display="p6_hours_and_days"),
                  field(3, column="start_date"), field(4, column="cstr_type"), field(5, category="activity_code", column="42"),
                  field(6, category="udf", column="42"), field(7, category="table:TASK", column="missing"),
                  field(8, check="open_start"), field(9, check="open_finish"),
                  field(10, check="relationship_leads", category="relationship_column", column="pred_type")]
        for configured in fields:
            pg.execute(f"INSERT INTO {s}.xertoolkit_schedule_quality_detail_field VALUES (%s,%s,%s,%s,%s,%s,%s,37)", tuple(configured[key] for key in ("detail_field_id", "check_code", "source_category", "source_identifier", "display_label", "sort_order", "display_format")))
        pg.execute(f"CREATE TABLE {s}.xertoolkit_result_schedule_quality_task_evidence(proj_id integer,check_code text,task_id integer,detail_json text,detail_display text)")
        pg.execute(f"INSERT INTO {s}.xertoolkit_result_schedule_quality_task_evidence VALUES (1,'quality',10,'unchanged','unchanged'),(1,'quality',11,'unchanged','unchanged'),(1,'open_start',10,'unchanged','unchanged'),(1,'open_finish',10,'unchanged','unchanged'),(1,'relationship_leads',10,'unchanged','unchanged'),(1,'no_configuration',10,'unchanged','unchanged'),(1,'quality',999,'unchanged','unchanged')")


if __name__ == "__main__":
    unittest.main()
