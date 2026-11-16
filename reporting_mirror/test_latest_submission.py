"""Regression checks for selecting one submitted programme per business project.

Run the database checks with P6_LATEST_SUBMISSION_INTEGRATION=1. Every fixture
schema is created in an uncommitted transaction and removed by rollback.
"""
import os
import importlib.util
import unittest
import uuid


@unittest.skipUnless(importlib.util.find_spec("psycopg"), "psycopg is required.")
class LatestSubmissionBoundaryTests(unittest.TestCase):
    def test_selection_rejects_public_schemas_and_sql_injection(self):
        from reporting_mirror.latest_submission import selection_query

        for schema in ("reporting", "p6_22", "public", 'load_1_p6_22";DROP SCHEMA public;--'):
            with self.subTest(schema=schema), self.assertRaises(ValueError):
                selection_query(schema)

    def test_selection_accepts_only_private_calculation_generation_names(self):
        from reporting_mirror.latest_submission import selection_query

        for prefix in ("load", "native_test", "native_shadow"):
            for source in ("p6_22", "p6_23"):
                schema = prefix + "_0123abcd4567ef00_" + source
                with self.subTest(schema=schema):
                    self.assertIn('"' + schema + '".project', selection_query(schema).as_string())

    def test_publication_scope_preserves_raw_history_and_nonproject_contracts(self):
        from reporting_mirror.latest_submission import scopes_to_latest_submission

        project_column = [{"name": "proj_id"}]
        self.assertTrue(scopes_to_latest_submission({"name": "xertoolkit_vw_pbi_projectmetrics", "columns": project_column}))
        self.assertTrue(scopes_to_latest_submission({"name": "xertoolkit_vw_pbi_schedulequalitytaskevidence", "columns": project_column}))
        for name in ("project", "task", "projwbs", "xertoolkit_result_project_metrics", "design_overview_submissions", "touchpoint_activity_detail"):
            with self.subTest(name=name):
                self.assertFalse(scopes_to_latest_submission({"name": name, "columns": project_column}))
        self.assertFalse(scopes_to_latest_submission({"name": "xertoolkit_vw_pbi_shared_configuration", "columns": [{"name": "config_version_id"}]}))


@unittest.skipUnless(
    os.environ.get("P6_LATEST_SUBMISSION_INTEGRATION") == "1",
    "Set P6_LATEST_SUBMISSION_INTEGRATION=1 for PostgreSQL fixture checks.",
)
class LatestSubmittedProgrammeIntegrationTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "p6_backup_admin.settings")
        import django

        django.setup()
        from psycopg import sql
        from reporting_mirror.connections import postgres_connection

        self.sql = sql
        self.pg = postgres_connection()
        self.addCleanup(self.pg.close)
        self.addCleanup(self.pg.rollback)
        self.pg.execute("SET LOCAL statement_timeout='10s'")
        self.pg.execute("SET LOCAL lock_timeout='2s'")
        self.create_fixture()

    def create_fixture(self, source="p6_22"):
        self.schema = "native_test_" + uuid.uuid4().hex[:16] + "_" + source
        self.pg.execute(
            self.sql.SQL("CREATE SCHEMA {}").format(self.sql.Identifier(self.schema))
        )
        definitions = {
            "project": """
                proj_id integer PRIMARY KEY, project_flag text DEFAULT 'Y',
                orig_proj_id integer, delete_session_id integer,
                delete_date timestamp, update_date timestamp,
                create_date timestamp, last_recalc_date timestamp
            """,
            "pcattype": """
                proj_catg_type_id integer PRIMARY KEY, proj_catg_type text,
                delete_session_id integer, delete_date timestamp
            """,
            "pcatval": """
                proj_catg_id integer, proj_catg_type_id integer,
                proj_catg_name text, proj_catg_short_name text,
                delete_session_id integer, delete_date timestamp,
                PRIMARY KEY (proj_catg_type_id, proj_catg_id)
            """,
            "projpcat": """
                proj_id integer, proj_catg_type_id integer, proj_catg_id integer,
                delete_session_id integer, delete_date timestamp
            """,
            "xertoolkit_vw_pbi_projects": """
                proj_id integer PRIMARY KEY, proj_short_name text,
                "Project Name" text, "Project Status" text,
                "Project State" text, "Lead Planner" text,
                "Updated Date" date, data_date timestamp
            """,
        }
        for name, definition in definitions.items():
            self.pg.execute(
                self.sql.SQL("CREATE TABLE {}.{} ({})").format(
                    self.sql.Identifier(self.schema),
                    self.sql.Identifier(name),
                    self.sql.SQL(definition),
                )
            )

    def execute(self, statement, parameters=()):
        return self.pg.execute(
            self.sql.SQL(statement).format(schema=self.sql.Identifier(self.schema)),
            parameters,
        )

    def code(self, code_id, name="Example project", *, type_id=1, type_name="Project ID"):
        self.execute(
            "INSERT INTO {schema}.pcattype VALUES (%s,%s,NULL,NULL) "
            "ON CONFLICT (proj_catg_type_id) DO NOTHING",
            (type_id, type_name),
        )
        self.execute(
            "INSERT INTO {schema}.pcatval VALUES (%s,%s,%s,%s,NULL,NULL) "
            "ON CONFLICT (proj_catg_type_id,proj_catg_id) DO NOTHING",
            (code_id, type_id, name, str(code_id)),
        )

    def programme(
        self,
        proj_id,
        *,
        code_id=100,
        type_id=1,
        type_name="Project ID",
        updated="2026-10-16",
        data_date="2026-09-20",
        changed="2026-11-01",
        status="Client Submitted",
        planner="Current planner",
        baseline_of=None,
        project_flag="Y",
        deleted=None,
        delete_session=None,
    ):
        self.execute(
            "INSERT INTO {schema}.project "
            "(proj_id,project_flag,orig_proj_id,delete_date,delete_session_id,update_date,last_recalc_date) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (proj_id, project_flag, baseline_of, deleted, delete_session, changed, data_date),
        )
        self.execute(
            "INSERT INTO {schema}.xertoolkit_vw_pbi_projects "
            '(proj_id,proj_short_name,"Project Name","Project Status","Project State",'
            '"Lead Planner","Updated Date",data_date) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (proj_id, "PROG-" + str(proj_id), "Example project", status, "Live", planner, updated, data_date),
        )
        status_id = 900 if status == "Client Submitted" else 901
        self.code(status_id, status, type_id=90, type_name="Project Status")
        self.execute(
            "INSERT INTO {schema}.projpcat VALUES (%s,90,%s,NULL,NULL)",
            (proj_id, status_id),
        )
        if code_id is not None:
            self.code(code_id, type_id=type_id, type_name=type_name)
            self.execute(
                "INSERT INTO {schema}.projpcat VALUES (%s,%s,%s,NULL,NULL)",
                (proj_id, type_id, code_id),
            )

    def selected(self):
        from reporting_mirror.latest_submission import selection_query

        return self.pg.execute(selection_query(self.schema)).fetchall()

    def selected_ids(self):
        return [row["proj_id"] for row in self.selected()]

    def test_dated_submission_wins_over_newer_undated_data_date(self):
        self.programme(1792, updated="2026-10-16", data_date="2026-09-20")
        self.programme(1843, updated=None, data_date="2026-10-18")
        self.assertEqual(self.selected_ids(), [1792])

    def test_latest_update_date_wins_even_when_data_date_is_older(self):
        self.programme(1, updated="2026-09-18", data_date="2026-11-15")
        self.programme(2, updated="2026-10-16", data_date="2026-09-20")
        self.assertEqual(self.selected_ids(), [2])

    def test_data_date_is_fallback_when_all_update_dates_are_missing(self):
        self.programme(1, updated=None, data_date="2026-09-20")
        self.programme(2, updated=None, data_date="2026-10-18")
        self.assertEqual(self.selected_ids(), [2])

    def test_tie_uses_project_update_timestamp_before_numeric_id(self):
        self.programme(1, changed="2026-11-16 12:00")
        self.programme(2, changed="2026-11-15 12:00")
        self.assertEqual(self.selected_ids(), [1])

    def test_tie_uses_numeric_id_as_final_deterministic_choice(self):
        self.programme(1)
        self.programme(2)
        self.assertEqual(self.selected_ids(), [2])

    def test_baselines_deleted_rows_and_other_statuses_are_ineligible(self):
        self.programme(1, baseline_of=0)
        self.programme(2, baseline_of=1, updated="2026-12-01")
        self.programme(3, deleted="2026-11-15", updated="2026-12-01")
        self.programme(4, delete_session=123, updated="2026-12-01")
        self.programme(5, project_flag="N", updated="2026-12-01")
        self.programme(6, status="Live", updated="2026-12-01")
        self.assertEqual(self.selected_ids(), [1])

    def test_p6_23_client_project_id_groups_programme_versions(self):
        self.create_fixture("p6_23")
        self.programme(1, type_id=2, type_name="Client Project ID", updated="2026-09-18")
        self.programme(2, type_id=2, type_name="Client Project ID", updated="2026-10-16")
        self.assertEqual(self.selected_ids(), [2])

    def test_project_id_takes_precedence_over_client_project_id(self):
        self.programme(1, updated="2026-09-18")
        self.programme(2, updated="2026-10-16")
        for proj_id, code_id in ((1, 200), (2, 300)):
            self.code(code_id, type_id=2, type_name="Client Project ID")
            self.execute(
                "INSERT INTO {schema}.projpcat VALUES (%s,2,%s,NULL,NULL)",
                (proj_id, code_id),
            )
        self.assertEqual(self.selected_ids(), [2])

    def test_uncoded_projects_do_not_collapse_into_one_null_group(self):
        self.programme(1, code_id=None)
        self.programme(2, code_id=None)
        rows = self.selected()
        self.assertEqual({row["proj_id"] for row in rows}, {1, 2})
        self.assertEqual(len({row["project_identity"] for row in rows}), 2)

    def test_same_friendly_name_on_distinct_project_codes_stays_distinct(self):
        self.programme(1, code_id=100)
        self.programme(2, code_id=200)
        self.assertEqual(set(self.selected_ids()), {1, 2})

    def test_latest_selection_happens_before_planner_filtering(self):
        self.programme(1, updated="2026-09-18", planner="Previous planner")
        self.programme(2, updated="2026-10-16", planner="New planner")
        self.assertEqual(self.selected_ids(), [2])

    def test_duplicate_code_assignments_do_not_multiply_selected_rows(self):
        self.programme(1)
        self.execute("INSERT INTO {schema}.projpcat VALUES (1,1,100,NULL,NULL)")
        self.execute("INSERT INTO {schema}.projpcat VALUES (1,1,100,NULL,NULL)")
        self.assertEqual(self.selected_ids(), [1])

    def test_deleted_code_assignment_does_not_join_unrelated_project_groups(self):
        self.programme(1, code_id=100, updated="2026-09-18")
        self.programme(2, code_id=200, updated="2026-10-16")
        self.execute(
            "INSERT INTO {schema}.projpcat VALUES (2,1,100,NULL,'2026-11-15')"
        )
        self.assertEqual(set(self.selected_ids()), {1, 2})

    def test_deleted_status_assignment_cannot_make_a_programme_submitted(self):
        self.programme(1, updated="2026-09-18")
        self.programme(2, updated="2026-10-16")
        self.execute(
            "UPDATE {schema}.projpcat SET delete_date='2026-11-15' "
            "WHERE proj_id=2 AND proj_catg_type_id=90"
        )
        self.assertEqual(self.selected_ids(), [1])

    def test_conflicting_active_status_assignments_do_not_select_a_programme(self):
        self.programme(1, updated="2026-09-18")
        self.programme(2, updated="2026-10-16")
        self.code(901, "Live", type_id=90, type_name="Project Status")
        self.execute("INSERT INTO {schema}.projpcat VALUES (2,90,901,NULL,NULL)")
        self.assertEqual(self.selected_ids(), [1])

    def test_deleted_project_code_value_is_not_used_as_project_identity(self):
        self.programme(1, updated="2026-09-18")
        self.programme(2, updated="2026-10-16")
        self.execute(
            "UPDATE {schema}.pcatval SET delete_session_id=123 "
            "WHERE proj_catg_type_id=1 AND proj_catg_id=100"
        )
        rows = self.selected()
        self.assertEqual({row["proj_id"] for row in rows}, {1, 2})
        self.assertTrue(all(row["project_identity"].startswith("project:") for row in rows))


if __name__ == "__main__":
    unittest.main()
