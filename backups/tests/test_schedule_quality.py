from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django_celery_beat.models import PeriodicTask

from backups.forms import ScheduleQualitySettingsForm
from backups.services.schedule_quality import (
    ScheduleQualityCheckScope,
    ScheduleQualityConstraintType,
    ScheduleQualityDetailField,
    ScheduleQualityOption,
    ScheduleQualitySettingsConflict,
    ScheduleQualitySettingsSnapshot,
    build_refresh_schedule_quality_sql,
    build_schedule_quality_target,
    fetch_schedule_quality_settings,
    get_or_create_schedule_quality_draft,
    publish_schedule_quality_config,
    save_schedule_quality_draft,
)
from backups.services.scheduling import sync_schedule_quality_refresh_schedule
from backups.tasks import (
    execute_schedule_quality_refresh,
    publish_schedule_quality_config_task,
)


SETTINGS_HASH = "A" * 64
SAVED_SETTINGS_HASH = "B" * 64


def build_settings_snapshot() -> ScheduleQualitySettingsSnapshot:
    return ScheduleQualitySettingsSnapshot(
        profile_code="default",
        profile_name="Default schedule quality checks",
        active_config_version_id=10,
        config_version_id=11,
        version_number=2,
        state="draft",
        settings_hash=SETTINGS_HASH,
        based_on_config_version_id=10,
        change_note="",
        created_at=None,
        created_by="jason.mappin",
        checks=(
            ScheduleQualityCheckScope(
                check_code="missing_predecessor",
                display_name="Missing predecessor",
                sort_order=10,
                is_enabled=True,
                include_loe=False,
                include_wbs_summary=False,
                include_milestones=True,
                exclude_complete=True,
                limit_type="Percent",
                green_limit=Decimal("3"),
                amber_limit=Decimal("7"),
                green_points=40,
                amber_points=32,
                records_metric="dcma_activity",
                qualifying_metric="missing_predecessor",
            ),
            ScheduleQualityCheckScope(
                check_code="invalid_dates",
                display_name="Invalid dates",
                sort_order=20,
                is_enabled=True,
                include_loe=None,
                include_wbs_summary=None,
                include_milestones=None,
                exclude_complete=None,
                limit_type="Percent",
                green_limit=Decimal("3"),
                amber_limit=Decimal("7"),
                green_points=15,
                amber_points=12,
                records_metric="dcma_activity",
                qualifying_metric="invalid_dates",
            ),
        ),
        options=(
            ScheduleQualityOption(
                option_code="exclude_deleted_activities",
                display_name="Exclude activities marked as deleted",
                data_type="bit",
                bit_value=True,
                numeric_value=None,
                text_value=None,
                unit_code=None,
                sort_order=5,
            ),
            ScheduleQualityOption(
                option_code="high_float_days",
                display_name="High float",
                data_type="integer",
                bit_value=None,
                numeric_value=Decimal("84"),
                text_value=None,
                unit_code="days",
                sort_order=10,
            ),
            ScheduleQualityOption(
                option_code="excessive_ss_percent",
                display_name="SS lag threshold",
                data_type="decimal",
                bit_value=None,
                numeric_value=Decimal("50"),
                text_value=None,
                unit_code="percent",
                sort_order=20,
            ),
        ),
        constraint_types=(
            ScheduleQualityConstraintType(
                constraint_type_code="mandatory_start",
                display_name="Mandatory Start",
                is_checked=True,
                sort_order=10,
            ),
        ),
    )


class ScheduleQualitySqlTests(SimpleTestCase):
    def test_build_refresh_schedule_quality_sql_runs_all_projects_by_default(self):
        sql = build_refresh_schedule_quality_sql()

        self.assertEqual(
            sql,
            "EXEC [powerbitables].[xertoolkit_refresh_all_schedule_quality] "
            "@trigger_type = N'scheduled';",
        )
        self.assertNotIn("MAX(check_run_id)", sql)

    def test_build_refresh_schedule_quality_sql_accepts_project_id(self):
        sql = build_refresh_schedule_quality_sql(proj_id=1444, trigger="manual")

        self.assertIn(
            "@proj_id = 1444",
            sql,
        )
        self.assertIn("@trigger_type = N'manual'", sql)

    def test_build_refresh_schedule_quality_sql_accepts_config_version(self):
        sql = build_refresh_schedule_quality_sql(
            proj_id=1444,
            config_version_id=22,
            trigger="manual",
        )

        self.assertIn("@proj_id = 1444", sql)
        self.assertIn("@config_version_id = 22", sql)
        self.assertNotIn("UPDATE", sql)

    def test_build_refresh_schedule_quality_sql_accepts_expected_hash(self):
        sql = build_refresh_schedule_quality_sql(
            config_version_id=22,
            expected_settings_hash=SETTINGS_HASH,
            trigger="publish",
        )

        self.assertIn("@config_version_id = 22", sql)
        self.assertIn(f"@expected_settings_hash = N'{SETTINGS_HASH}'", sql)

    @override_settings(
        P6_SCHEDULE_QUALITY_SQL_HOST="sql-host",
        P6_SCHEDULE_QUALITY_SQL_PORT=1443,
        P6_SCHEDULE_QUALITY_SQL_DATABASE="P62212_1",
        P6_SCHEDULE_QUALITY_SQL_USERNAME="admin",
        P6_SCHEDULE_QUALITY_SQL_PASSWORD="secret",
    )
    def test_build_schedule_quality_target_uses_settings(self):
        target = build_schedule_quality_target()

        self.assertEqual(target.sql_host, "sql-host")
        self.assertEqual(target.sql_port, 1443)
        self.assertEqual(target.sql_database, "P62212_1")
        self.assertEqual(target.sql_username, "admin")
        self.assertEqual(target.sql_password, "secret")

    @patch("backups.tasks.refresh_schedule_quality")
    def test_execute_schedule_quality_refresh_task_calls_service(self, refresh_schedule_quality):
        refresh_schedule_quality.return_value = [{"check_run_id": 1, "status": "success"}]

        result = execute_schedule_quality_refresh.run(proj_id=1444, trigger="manual")

        refresh_schedule_quality.assert_called_once_with(proj_id=1444, trigger="manual")
        self.assertEqual(result, [{"check_run_id": 1, "status": "success"}])

    @patch("backups.tasks.publish_schedule_quality_config")
    def test_publish_schedule_quality_config_task_calls_service(self, publish_config):
        publish_schedule_quality_config_task.run(
            config_version_id=11,
            expected_settings_hash=SETTINGS_HASH,
            published_by="jason.mappin",
        )

        publish_config.assert_called_once_with(
            config_version_id=11,
            expected_settings_hash=SETTINGS_HASH,
            published_by="jason.mappin",
            trigger_type="publish",
        )

    @patch("backups.services.schedule_quality.fetch_rows")
    def test_get_or_create_draft_uses_parameterized_proc_owned_transaction(self, fetch_rows):
        fetch_rows.return_value = [{"config_version_id": 22}]

        result = get_or_create_schedule_quality_draft(
            changed_by="jason.mappin",
            profile_code="default",
        )

        self.assertEqual(result, 22)
        _, kwargs = fetch_rows.call_args
        self.assertEqual(kwargs["parameters"], ("default", "jason.mappin"))
        self.assertFalse(kwargs["transactional"])
        self.assertNotIn("jason.mappin", fetch_rows.call_args.args[1])

    @patch("backups.services.schedule_quality.fetch_result_sets")
    def test_fetch_settings_preserves_false_and_not_applicable_scope(self, fetch_sets):
        fetch_sets.return_value = [
            [
                {
                    "profile_code": "default",
                    "profile_name": "Default schedule quality checks",
                    "active_config_version_id": 10,
                    "config_version_id": 11,
                    "version_number": 2,
                    "state": "draft",
                    "settings_hash": SETTINGS_HASH,
                    "based_on_config_version_id": 10,
                    "change_note": None,
                    "created_at": None,
                    "created_by": "jason.mappin",
                }
            ],
            [
                {
                    "check_code": "missing_predecessor",
                    "display_name": "Missing predecessor",
                    "sort_order": 10,
                    "is_enabled": 1,
                    "include_loe": 0,
                    "include_wbs_summary": None,
                    "include_milestones": 1,
                    "exclude_complete": 1,
                }
            ],
            [],
            [
                {
                    "constraint_type_code": "mandatory_start",
                    "display_name": "Mandatory Start",
                    "is_checked": 1,
                    "sort_order": 10,
                }
            ],
        ]

        snapshot = fetch_schedule_quality_settings(config_version_id=11)

        self.assertFalse(snapshot.checks[0].include_loe)
        self.assertIsNone(snapshot.checks[0].include_wbs_summary)
        self.assertTrue(snapshot.checks[0].include_milestones)
        self.assertTrue(snapshot.constraint_types[0].is_checked)
        self.assertEqual(snapshot.settings_hash, SETTINGS_HASH)
        self.assertEqual(fetch_sets.call_args.kwargs["parameters"], (11, 11, 11, 11, 11))

    @patch("backups.services.schedule_quality.fetch_rows")
    def test_save_draft_sends_json_and_hash_as_parameters(self, fetch_rows):
        fetch_rows.return_value = [{"settings_hash": SAVED_SETTINGS_HASH}]
        payload = {
            "checks": [],
            "options": [{"option_code": "high_float", "numeric_value": Decimal("84")}],
            "constraint_types": [],
        }

        saved_hash = save_schedule_quality_draft(
            config_version_id=11,
            payload=payload,
            expected_settings_hash=SETTINGS_HASH,
            changed_by="jason.mappin",
            change_note="Match User Settings",
        )

        sql = fetch_rows.call_args.args[1]
        parameters = fetch_rows.call_args.kwargs["parameters"]
        self.assertNotIn("jason.mappin", sql)
        self.assertEqual(parameters[0], 11)
        self.assertEqual(parameters[2], SETTINGS_HASH)
        self.assertEqual(parameters[3:], ("jason.mappin", "Match User Settings"))
        self.assertEqual(json.loads(parameters[1])["options"][0]["numeric_value"], "84")
        self.assertFalse(fetch_rows.call_args.kwargs["transactional"])
        self.assertEqual(saved_hash, SAVED_SETTINGS_HASH)

    @patch("backups.services.schedule_quality.fetch_rows")
    def test_save_draft_turns_stale_hash_error_into_conflict(self, fetch_rows):
        fetch_rows.side_effect = RuntimeError(
            "The draft changed after it was loaded. Reload it before saving."
        )

        with self.assertRaises(ScheduleQualitySettingsConflict):
            save_schedule_quality_draft(
                config_version_id=11,
                payload={"checks": [], "options": [], "constraint_types": []},
                expected_settings_hash=SETTINGS_HASH,
                changed_by="jason.mappin",
            )

    @patch("backups.services.schedule_quality.execute_statement")
    def test_publish_config_uses_parameterized_proc_owned_transaction(self, execute_statement):
        publish_schedule_quality_config(
            config_version_id=11,
            expected_settings_hash=SAVED_SETTINGS_HASH,
            published_by="jason.mappin",
        )

        self.assertEqual(
            execute_statement.call_args.kwargs["parameters"],
            (11, SAVED_SETTINGS_HASH, "jason.mappin", "publish"),
        )
        self.assertFalse(execute_statement.call_args.kwargs["transactional"])


class ScheduleQualitySettingsFormTests(SimpleTestCase):
    def test_legacy_open_end_summary_is_replaced_by_configurable_p6_fields(self):
        open_end_checks = (
            ScheduleQualityCheckScope(
                check_code="open_start",
                display_name="Open-Start Tasks",
                sort_order=10,
                is_enabled=True,
                include_loe=False,
                include_wbs_summary=False,
                include_milestones=True,
                exclude_complete=True,
            ),
            ScheduleQualityCheckScope(
                check_code="open_finish",
                display_name="Open-Finish Tasks",
                sort_order=20,
                is_enabled=True,
                include_loe=False,
                include_wbs_summary=False,
                include_milestones=True,
                exclude_complete=True,
            ),
        )
        legacy_fields = tuple(
            ScheduleQualityDetailField(
                detail_field_id=index,
                check_code=check_code,
                source_category="relationship_column",
                source_identifier="relationship_summary",
                display_label="Relationships",
                display_format="native",
                sort_order=1,
            )
            for index, check_code in enumerate(
                ("open_start", "open_finish"), start=1
            )
        )
        snapshot = replace(
            build_settings_snapshot(),
            checks=open_end_checks,
            detail_fields=legacy_fields,
        )

        form = ScheduleQualitySettingsForm(settings_snapshot=snapshot)
        fields = json.loads(form["detail_fields_json"].value())

        self.assertEqual(
            [
                (
                    field["check_code"],
                    field["source_category"],
                    field["source_identifier"],
                    field["display_label"],
                    field["sort_order"],
                )
                for field in fields
            ],
            [
                ("open_start", "TASKPRED", "pred_type", "Relationship Type", 1),
                ("open_start", "TASK", "task_code", "Predecessor Activity ID", 2),
                ("open_start", "TASK", "task_name", "Predecessor Activity Name", 3),
                ("open_finish", "TASKPRED", "pred_type", "Relationship Type", 1),
                ("open_finish", "TASK", "task_code", "Successor Activity ID", 2),
                ("open_finish", "TASK", "task_name", "Successor Activity Name", 3),
            ],
        )

    def test_removed_configurable_open_end_fields_are_not_reintroduced(self):
        snapshot = replace(
            build_settings_snapshot(),
            detail_fields=(
                ScheduleQualityDetailField(
                    detail_field_id=1,
                    check_code="open_start",
                    source_category="TASKPRED",
                    source_identifier="pred_type",
                    display_label="Relationship Type",
                    display_format="native",
                    sort_order=1,
                ),
            ),
        )

        form = ScheduleQualitySettingsForm(settings_snapshot=snapshot)

        self.assertEqual(
            json.loads(form["detail_fields_json"].value()),
            [
                {
                    "check_code": "open_start",
                    "source_category": "TASKPRED",
                    "source_identifier": "pred_type",
                    "display_label": "Relationship Type",
                    "display_format": "native",
                    "sort_order": 1,
                }
            ],
        )

    def test_numeric_option_values_display_to_two_decimal_places(self):
        form = ScheduleQualitySettingsForm(
            settings_snapshot=build_settings_snapshot(),
        )

        self.assertIn(
            'value="84.00"',
            str(form["option__high_float_days"]),
        )
        self.assertIn(
            'value="50.00"',
            str(form["option__excessive_ss_percent"]),
        )

    def test_form_builds_complete_payload_and_treats_clear_as_no(self):
        form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "change_note": "Workbook alignment",
                "check__missing_predecessor__is_enabled": "on",
                "check__missing_predecessor__include_milestones": "on",
                "check__missing_predecessor__exclude_complete": "on",
                "check__invalid_dates__is_enabled": "on",
                "option__exclude_deleted_activities": "on",
                "option__high_float_days": "84",
                "option__excessive_ss_percent": "50",
                "constraint__mandatory_start": "on",
            },
            settings_snapshot=build_settings_snapshot(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        payload = form.build_payload()
        missing_predecessor = payload["checks"][0]
        self.assertFalse(missing_predecessor["include_loe"])
        self.assertFalse(missing_predecessor["include_wbs_summary"])
        self.assertTrue(missing_predecessor["include_milestones"])
        invalid_dates = payload["checks"][1]
        self.assertIsNone(invalid_dates["include_loe"])
        self.assertIsNone(invalid_dates["exclude_complete"])
        self.assertTrue(payload["options"][0]["bit_value"])
        self.assertEqual(payload["options"][1]["numeric_value"], 84)
        self.assertEqual(payload["options"][2]["numeric_value"], Decimal("50"))
        self.assertEqual(missing_predecessor["green_limit"], Decimal("3"))
        self.assertEqual(missing_predecessor["amber_points"], 32)
        self.assertEqual(missing_predecessor["records_metric"], "dcma_activity")
        self.assertTrue(payload["constraint_types"][0]["is_checked"])

    def test_deleted_activity_option_is_rendered_as_global_scope_control(self):
        form = ScheduleQualitySettingsForm(
            settings_snapshot=build_settings_snapshot(),
        )

        self.assertEqual(len(form.scope_option_rows), 1)
        self.assertEqual(
            form.scope_option_rows[0]["option"].option_code,
            "exclude_deleted_activities",
        )
        self.assertEqual(
            [row["option"].option_code for row in form.option_rows],
            ["high_float_days", "excessive_ss_percent"],
        )

    def test_clear_deleted_activity_option_is_saved_as_include_deleted(self):
        form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "check__missing_predecessor__is_enabled": "on",
                "check__invalid_dates__is_enabled": "on",
                "option__high_float_days": "84",
                "option__excessive_ss_percent": "50",
                "constraint__mandatory_start": "on",
            },
            settings_snapshot=build_settings_snapshot(),
        )

        self.assertTrue(form.is_valid(), form.errors)
        deleted_option = next(
            option
            for option in form.build_payload()["options"]
            if option["option_code"] == "exclude_deleted_activities"
        )
        self.assertFalse(deleted_option["bit_value"])

    def test_form_rejects_negative_threshold(self):
        form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "option__high_float_days": "-1",
                "option__excessive_ss_percent": "50",
            },
            settings_snapshot=build_settings_snapshot(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("option__high_float_days", form.errors)

    def test_negative_float_threshold_accepts_negative_values_in_sql_range(self):
        option = ScheduleQualityOption(
            option_code="negative_float_days",
            display_name="Negative float threshold",
            data_type="integer",
            bit_value=None,
            numeric_value=Decimal("0"),
            text_value=None,
            unit_code="days",
            sort_order=10,
        )
        snapshot = replace(
            build_settings_snapshot(),
            checks=(),
            options=(option,),
            constraint_types=(),
        )

        valid_form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "option__negative_float_days": "-5",
            },
            settings_snapshot=snapshot,
        )
        invalid_form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "option__negative_float_days": "-100001",
            },
            settings_snapshot=snapshot,
        )

        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertFalse(invalid_form.is_valid())

    def test_excessive_lag_percent_cannot_exceed_100(self):
        option = ScheduleQualityOption(
            option_code="excessive_ff_percent",
            display_name="Excessive FF lag percentage",
            data_type="decimal",
            bit_value=None,
            numeric_value=Decimal("50"),
            text_value=None,
            unit_code="percent",
            sort_order=10,
        )
        snapshot = replace(
            build_settings_snapshot(),
            checks=(),
            options=(option,),
            constraint_types=(),
        )
        form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "option__excessive_ff_percent": "100.1",
            },
            settings_snapshot=snapshot,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("option__excessive_ff_percent", form.errors)

    def test_riding_days_cannot_exceed_sql_limit(self):
        option = ScheduleQualityOption(
            option_code="riding_days_after_data_date",
            display_name="Riding date days after data date",
            data_type="integer",
            bit_value=None,
            numeric_value=Decimal("3"),
            text_value=None,
            unit_code="days",
            sort_order=10,
        )
        snapshot = replace(
            build_settings_snapshot(),
            checks=(),
            options=(option,),
            constraint_types=(),
        )
        form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SETTINGS_HASH,
                "option__riding_days_after_data_date": "3651",
            },
            settings_snapshot=snapshot,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("option__riding_days_after_data_date", form.errors)

    def test_form_rejects_a_stale_settings_hash(self):
        form = ScheduleQualitySettingsForm(
            {
                "config_version_id": "11",
                "expected_settings_hash": SAVED_SETTINGS_HASH,
                "option__high_float_days": "84",
                "option__excessive_ss_percent": "50",
            },
            settings_snapshot=build_settings_snapshot(),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("changed after the page was loaded", str(form.non_field_errors()))


class ScheduleQualitySchedulingTests(TestCase):
    def test_sync_schedule_quality_refresh_schedule_creates_enabled_periodic_task(self):
        task = sync_schedule_quality_refresh_schedule(
            enabled=True,
            crontab_string="*/15 * * * *",
            proj_id=1444,
        )

        task.refresh_from_db()
        self.assertEqual(task.name, "P6 Schedule Quality Refresh")
        self.assertEqual(task.task, "backups.tasks.execute_schedule_quality_refresh")
        self.assertTrue(task.enabled)
        self.assertEqual(task.kwargs, '{"proj_id": 1444}')
        self.assertEqual(task.crontab.minute, "*/15")
        self.assertEqual(task.crontab.hour, "*")

    def test_sync_schedule_quality_refresh_schedule_can_refresh_all_projects(self):
        task = sync_schedule_quality_refresh_schedule(
            enabled=True,
            crontab_string="0 * * * *",
            proj_id=None,
        )

        self.assertEqual(task.kwargs, "{}")
        self.assertEqual(PeriodicTask.objects.count(), 1)
