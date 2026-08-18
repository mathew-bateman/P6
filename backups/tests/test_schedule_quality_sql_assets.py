from pathlib import Path
import re
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_QUALITY_SQL = PROJECT_ROOT / "sql" / "schedule_quality"


class ScheduleQualitySqlAssetTests(TestCase):
    @staticmethod
    def _module_batch(sql: str, module_type: str, module_name: str) -> str:
        match = re.search(
            rf"^CREATE OR ALTER {module_type}\s+\[powerbitables\]\."
            rf"\[{re.escape(module_name)}\](?P<body>.*?)(?=^GO\s*$)",
            sql,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise AssertionError(f"Could not find {module_type} {module_name}")
        return match.group(0).strip()

    @staticmethod
    def _nvarchar_assignment(sql: str, variable_name: str) -> str:
        match = re.search(
            rf"DECLARE @{re.escape(variable_name)} nvarchar\(max\) = "
            rf"N'(?P<body>(?:''|[^'])*)';",
            sql,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match is None:
            raise AssertionError(
                f"Could not find nvarchar assignment for @{variable_name}"
            )
        return match.group("body").replace("''", "'")

    def test_projects_view_keeps_all_projects(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"CREATE OR ALTER VIEW\s+\[powerbitables\]\."
            r"\[xertoolkit_vw_PBI_Projects\](?P<body>.*?)^GO\s*$",
            forward_sql,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(match)
        view_body = match.group("body")
        final_select = view_body.rfind("\nSELECT")
        self.assertGreater(final_select, 0)

        # This is inherited behaviour: only the updated-date helper is active-only.
        self.assertEqual(
            len(re.findall(r"project_flag\s*=\s*'Y'", view_body, re.IGNORECASE)),
            1,
        )
        self.assertNotRegex(
            view_body[final_select:],
            re.compile(r"project_flag", re.IGNORECASE),
        )
        self.assertRegex(
            view_body[final_select:],
            re.compile(r"FROM\s+dbo\.PROJECT\s+AS\s+p", re.IGNORECASE),
        )

    def test_all_project_reconciliation_covers_every_materialised_metric(self):
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("SELECT proj_id FROM dbo.PROJECT", reconciliation_sql)
        self.assertIn("EXCEPT", reconciliation_sql)
        self.assertIn("xertoolkit_result_logic_loop_tasks", reconciliation_sql)
        self.assertIn("THROW 51412", reconciliation_sql)

        metric_names = (
            "activity_count",
            "dcma_activity_count",
            "relationship_count",
            "relationship_ratio",
            "missing_predecessor_count",
            "missing_successor_count",
            "open_start_count",
            "open_finish_count",
            "lead_count",
            "lag_count",
            "non_fs_count",
            "excessive_ss_lag_count",
            "excessive_ff_lag_count",
            "high_float_count",
            "negative_float_count",
            "high_duration_count",
            "constraint_count",
            "invalid_date_count",
            "in_progress_error_count",
            "riding_progress_date_count",
            "critical_task_count",
            "near_critical_task_count",
            "out_of_sequence_count",
            "logical_loop_count",
        )
        for metric_name in metric_names:
            self.assertIn(f"(N'{metric_name}'", reconciliation_sql)

        self.assertIn("N'project_name'", reconciliation_sql)
        self.assertIn("N'updated_date'", reconciliation_sql)

    def test_relationship_ratio_uses_non_fs_relationships_over_total_relationships(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        refresh_hotfix_sql = (
            SCHEDULE_QUALITY_SQL / "006_refresh_performance_hotfix.sql"
        ).read_text(encoding="utf-8")
        split_stage_sql = (
            SCHEDULE_QUALITY_SQL / "016_split_stage_metrics_performance.sql"
        ).read_text(encoding="utf-8")
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")
        migration_sql = (
            SCHEDULE_QUALITY_SQL / "035_relationship_ratio_non_fs.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "935_relationship_ratio_non_fs_rollback.sql"
        ).read_text(encoding="utf-8")

        expected_formula = (
            "ISNULL(rqc.non_fs_count, 0) * 100.0\n"
            "                / NULLIF(ISNULL(rc.relationship_count, 0), 0)"
        )
        for sql in (forward_sql, refresh_hotfix_sql, split_stage_sql):
            self.assertIn(expected_formula, sql)
        self.assertIn("relationship_quality.non_fs_count, 0) * 100.0", reconciliation_sql)
        self.assertIn("relationships.relationship_count, 0), 0)", reconciliation_sql)
        self.assertIn("UPDATE [powerbitables].[xertoolkit_result_project_metrics]", migration_sql)
        self.assertIn("result.non_fs_count, 0) * 100.0", migration_sql)
        self.assertIn("relationship_count, 0) * 1.0", rollback_sql)

    def test_performance_hotfix_uses_single_pass_scope_settings(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        hotfix_sql = (
            SCHEDULE_QUALITY_SQL / "005_performance_hotfix.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")

        function_names = (
            "xertoolkit_fn_open_ends",
            "xertoolkit_fn_relationship_quality",
            "xertoolkit_fn_activity_quality",
        )
        for function_name in function_names:
            forward_batch = self._module_batch(
                forward_sql, "FUNCTION", function_name
            )
            hotfix_batch = self._module_batch(
                hotfix_sql, "FUNCTION", function_name
            )
            self.assertEqual(hotfix_batch, forward_batch)
            self.assertIn("scope_settings AS", hotfix_batch)
            self.assertIn("scope_mask", hotfix_batch)

        open_ends = self._module_batch(
            hotfix_sql, "FUNCTION", "xertoolkit_fn_open_ends"
        )
        relationship_quality = self._module_batch(
            hotfix_sql, "FUNCTION", "xertoolkit_fn_relationship_quality"
        )
        activity_quality = self._module_batch(
            hotfix_sql, "FUNCTION", "xertoolkit_fn_activity_quality"
        )

        self.assertNotIn("xertoolkit_fn_activity_in_scope", open_ends)
        self.assertNotIn("xertoolkit_fn_relationship_in_scope", relationship_quality)
        self.assertNotIn("xertoolkit_fn_activity_in_scope", activity_quality)
        self.assertEqual(open_ends.count("FROM dbo.TASKPRED AS r"), 2)
        self.assertEqual(
            open_ends.count("WHERE r.delete_session_id IS NULL"),
            2,
        )
        self.assertIn(
            "COUNT_BIG(pred_source.task_id) AS predecessor_count",
            open_ends,
        )
        self.assertIn(
            "COUNT_BIG(succ_source.task_id) AS successor_count",
            open_ends,
        )
        self.assertIn("deleted_activities AS", open_ends)
        self.assertIn("FROM dbo.TASKACTV AS assignment", open_ends)
        self.assertIn("code_type.actv_code_type = 'Activity Status'", open_ends)
        self.assertIn("code.short_name = 'DEL'", open_ends)
        self.assertNotIn("AND deleted.task_id IS NULL", open_ends)
        self.assertIn("exclude_deleted_activities", open_ends)
        self.assertIn("OR deleted.task_id IS NULL", open_ends)
        self.assertNotIn("LOWER(a.task_name)", open_ends)
        self.assertIn("pred_source.delete_session_id IS NULL", open_ends)
        self.assertIn("succ_source.delete_session_id IS NULL", open_ends)
        self.assertIn("WHERE pred_type = 'PR_FF'", open_ends)
        self.assertIn("WHERE pred_type = 'PR_SS'", open_ends)
        self.assertEqual(open_ends.count("SELECT DISTINCT proj_id, pred_task_id, task_id"), 2)
        self.assertIn(
            "source_activity.delete_session_id IS NULL",
            open_ends,
        )
        self.assertIn(
            "open-end calculations ignore soft-deleted activities and relationships",
            postdeploy_sql,
        )
        self.assertEqual(
            relationship_quality.count("xertoolkit_vw_PBI_Relationships"), 1
        )
        self.assertEqual(activity_quality.count("xertoolkit_vw_PBI_Activities"), 1)

    def test_critical_tasks_use_non_overlapping_four_hour_rounding_boundary(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        hotfix_sql = (
            SCHEDULE_QUALITY_SQL / "005_performance_hotfix.sql"
        ).read_text(encoding="utf-8")
        boundary_sql = (
            SCHEDULE_QUALITY_SQL / "015_critical_float_rounding_boundary.sql"
        ).read_text(encoding="utf-8")
        superseded_sql = (
            SCHEDULE_QUALITY_SQL / "014_critical_float_under_24_hours.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL
            / "915_critical_float_rounding_boundary_rollback.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")

        for sql in (forward_sql, hotfix_sql):
            activity_quality = self._module_batch(
                sql, "FUNCTION", "xertoolkit_fn_activity_quality"
            )
            self.assertIn("total_float_hr_cnt < 4.0", activity_quality)
            self.assertIn("total_float_hr_cnt >= 4.0", activity_quality)
            self.assertNotIn("total_float_hr_cnt <= 0", activity_quality)
            self.assertNotIn("total_float_hr_cnt > 0", activity_quality)

        self.assertIn("total_float_hr_cnt < 24.0", boundary_sql)
        self.assertIn("total_float_hr_cnt >= 24.0", boundary_sql)
        self.assertIn("total_float_hr_cnt < 4.0", boundary_sql)
        self.assertIn("total_float_hr_cnt >= 4.0", boundary_sql)
        self.assertIn("014 is superseded", superseded_sql)
        self.assertIn(
            "015_critical_float_rounding_boundary.sql",
            superseded_sql,
        )
        self.assertIn("total_float_hr_cnt < 4.0", rollback_sql)
        self.assertIn("total_float_hr_cnt >= 4.0", rollback_sql)
        self.assertIn("total_float_hr_cnt <= 0", rollback_sql)
        self.assertIn("total_float_hr_cnt > 0", rollback_sql)
        self.assertIn(
            "critical and near-critical float ranges do not overlap",
            postdeploy_sql,
        )

    def test_high_float_threshold_is_inclusive_in_forward_and_hotfix_functions(self):
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        for script_name in (
            "001_versioned_settings_forward.sql",
            "005_performance_hotfix.sql",
        ):
            sql = (SCHEDULE_QUALITY_SQL / script_name).read_text(encoding="utf-8")
            function_sql = self._module_batch(
                sql,
                "FUNCTION",
                "xertoolkit_fn_activity_quality",
            )
            self.assertIn(
                "a.total_float_hr_cnt >= o.high_float_days * 8.0",
                function_sql,
            )
            self.assertNotIn(
                "a.total_float_hr_cnt > o.high_float_days * 8.0",
                function_sql,
            )
        self.assertIn(
            "high-float calculation includes the configured threshold",
            postdeploy_sql,
        )

    def test_high_duration_uses_remaining_duration(self):
        for script_name in (
            "001_versioned_settings_forward.sql",
            "005_performance_hotfix.sql",
        ):
            sql = (SCHEDULE_QUALITY_SQL / script_name).read_text(encoding="utf-8")
            activity_quality = self._module_batch(
                sql,
                "FUNCTION",
                "xertoolkit_fn_activity_quality",
            )

            self.assertRegex(
                activity_quality,
                re.compile(
                    r"a\.remain_drtn_hr_cnt\s*>\s*o\.high_duration_days\s*\*\s*8\.0"
                    r"\s+THEN\s+1\s+ELSE\s+0\s+END\)\s+AS\s+is_high_duration",
                    re.IGNORECASE,
                ),
            )
            self.assertNotRegex(
                activity_quality,
                re.compile(
                    r"a\.target_drtn_hr_cnt\s*>\s*o\.high_duration_days",
                    re.IGNORECASE,
                ),
            )

    def test_configured_evidence_preserves_raw_task_values(self):
        sql = (
            SCHEDULE_QUALITY_SQL / "026_configured_evidence_raw_fallback.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("Configured evidence pass v4", sql)
        self.assertIn("FROM sys.columns AS column_definition", sql)
        self.assertIn("OBJECT_ID(N'dbo.TASK')", sql)
        self.assertIn("QUOTENAME(column_definition.name)", sql)
        self.assertIn("type_definition.name IN", sql)
        self.assertIn("N''N/A''", sql)
        self.assertNotIn("FOR JSON PATH", sql)

    def test_configured_evidence_resolves_all_joinable_p6_tables(self):
        sql = (
            SCHEDULE_QUALITY_SQL / "027_configured_evidence_all_tables.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("Configured evidence pass v6", sql)
        self.assertIn("configured_field_cursor", sql)
        self.assertIn("QUOTENAME(@source_table)", sql)
        self.assertIn("QUOTENAME(@source_identifier)", sql)
        self.assertIn("source_row.[task_id] = evidence.task_id", sql)
        self.assertIn("source_row.[pred_task_id] = evidence.task_id", sql)
        self.assertIn("source_row.[wbs_id] = task_record.wbs_id", sql)
        self.assertIn("source_row.[proj_id] = evidence.proj_id", sql)
        self.assertIn("SELECT DISTINCT", sql)
        self.assertIn("xertoolkit_schedule_quality_constraint_type", sql)
        self.assertIn("configured_constraint.display_name", sql)
        self.assertIn("configured_constraint.constraint_type_code", sql)
        self.assertIn(
            "qualifying_relationship.relationship_id = source_row.[task_pred_id]",
            sql,
        )
        self.assertIn("qualifying_relationship.is_lag = 1", sql)
        self.assertIn("qualifying_relationship.is_lead = 1", sql)
        self.assertIn("CONFIGURABLE_OPEN_END_EVIDENCE_V1", sql)
        self.assertIn("@field_check_code = N''open_start''", sql)
        self.assertIn("@field_check_code = N''open_finish''", sql)
        self.assertIn("open_relationship.pred_task_id = source_row.[task_id]", sql)
        self.assertIn("open_relationship.task_id = source_row.[task_id]", sql)
        self.assertIn("REPLACE(source_row.[pred_type]", sql)
        self.assertNotIn("fixed_field.detail_field_id", sql)
        self.assertNotIn("N''Required Paired Relationship''", sql)
        self.assertNotIn("SCHEDULE_QUALITY_OPEN_END_CONTEXT", sql)
        self.assertIn("N''N/A''", sql)

    def test_configurable_open_end_evidence_migration_removes_fixed_rows(self):
        sql = (
            SCHEDULE_QUALITY_SQL / "031_configurable_open_end_evidence.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("CONFIGURABLE_OPEN_END_EVIDENCE_V1", sql)
        self.assertIn("Open-end evidence comes directly from P6 TASKPRED and TASK", sql)
        self.assertIn("@field_check_code = N''open_start''", sql)
        self.assertIn("@field_check_code = N''open_finish''", sql)
        self.assertIn("open_relationship.pred_task_id = source_row.[task_id]", sql)
        self.assertIn("open_relationship.task_id = source_row.[task_id]", sql)
        self.assertIn("REPLACE(source_row.[pred_type]", sql)
        self.assertIn(
            "CHARINDEX(N'Open-end evidence comes directly from P6 TASKPRED and TASK', "
            "@deployed_definition) <> 0",
            sql,
        )

    def test_configured_evidence_display_format_keeps_p6_hours_and_adds_days(self):
        sql = (
            SCHEDULE_QUALITY_SQL
            / "028_configured_evidence_display_formats.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("display_format", sql)
        self.assertIn("p6_hours_and_days", sql)
        self.assertIn("%[_]hr[_]cnt", sql)
        self.assertIn("P6: ", sql)
        self.assertIn("Calculated: ", sql)
        self.assertIn("/ 8.0 AS decimal(38,2)", sql)
        self.assertIn("days (8h/day)", sql)
        self.assertIn("Configured evidence pass v8", sql)

    def test_configured_evidence_display_format_supports_days_only(self):
        sql = (
            SCHEDULE_QUALITY_SQL
            / "032_configured_evidence_days_only.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("p6_days_only", sql)
        self.assertIn("Calculated days: ", sql)
        self.assertIn("from ", sql)
        self.assertIn("Configured evidence pass v9", sql)

    def test_configured_evidence_hour_formats_are_concise(self):
        sql = (
            SCHEDULE_QUALITY_SQL
            / "034_configured_evidence_combined_format.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("hours | ", sql)
        self.assertIn("@days_only_new", sql)
        self.assertIn("days (8h/day)", sql)
        self.assertIn("Configured evidence pass v10", sql)
        self.assertIn("P6: ", sql)
        self.assertIn("Calculated: ", sql)

    def test_deleted_activity_scope_uses_structured_p6_status_and_all_check_families(self):
        migration_sql = (
            SCHEDULE_QUALITY_SQL / "029_exclude_deleted_activities.sql"
        ).read_text(encoding="utf-8")
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        hotfix_sql = (
            SCHEDULE_QUALITY_SQL / "005_performance_hotfix.sql"
        ).read_text(encoding="utf-8")

        for sql in (migration_sql, forward_sql):
            self.assertIn("code_type.actv_code_type = 'Activity Status'", sql)
            self.assertIn("code.short_name = 'DEL'", sql)
            self.assertIn("AS is_deleted", sql)

        for function_name in (
            "xertoolkit_fn_open_ends",
            "xertoolkit_fn_relationship_quality",
            "xertoolkit_fn_activity_quality",
        ):
            function_sql = self._module_batch(
                hotfix_sql, "FUNCTION", function_name
            )
            self.assertIn("exclude_deleted_activities", function_sql)

        self.assertIn("predecessor_is_deleted", forward_sql)
        self.assertIn("successor_is_deleted", forward_sql)
        self.assertIn("@exclude_deleted_activities", forward_sql)
        self.assertIn("predecessor.is_deleted = 0", forward_sql)
        self.assertIn("a.is_deleted = 0", forward_sql)
        self.assertIn(") <> 13", forward_sql)
        self.assertIn("settings_hash", migration_sql)

    def test_out_of_sequence_deleted_filter_avoids_expanding_activity_view_twice(self):
        sql_assets = (
            "001_versioned_settings_forward.sql",
            "012_p6_out_of_sequence_parity.sql",
            "029_exclude_deleted_activities.sql",
            "030_out_of_sequence_deleted_filter_performance.sql",
        )

        for asset_name in sql_assets:
            sql = (SCHEDULE_QUALITY_SQL / asset_name).read_text(encoding="utf-8")
            function_sql = self._module_batch(
                sql, "FUNCTION", "xertoolkit_fn_out_of_sequence"
            )
            self.assertIn("OOS_DELETED_FILTER_SEMIJOIN_V1", function_sql)
            self.assertIn("JOIN dbo.TASK AS pred", function_sql)
            self.assertIn("JOIN dbo.TASK AS succ", function_sql)
            self.assertIn("FROM deleted_activities AS deleted", function_sql)
            self.assertIn("code_type.actv_code_type = 'Activity Status'", function_sql)
            self.assertIn("code.short_name = 'DEL'", function_sql)
            self.assertNotIn(
                "xertoolkit_vw_PBI_Activities] AS pred", function_sql
            )
            self.assertNotIn(
                "xertoolkit_vw_PBI_Activities] AS succ", function_sql
            )

        rollback_sql = (
            SCHEDULE_QUALITY_SQL
            / "930_out_of_sequence_deleted_filter_performance_rollback.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("xertoolkit_vw_PBI_Activities] AS pred", rollback_sql)
        self.assertIn("xertoolkit_vw_PBI_Activities] AS succ", rollback_sql)

    def test_soft_deleted_task_patch_excludes_physical_p6_deletions(self):
        sql = (
            SCHEDULE_QUALITY_SQL / "033_exclude_soft_deleted_tasks.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "933_exclude_soft_deleted_tasks_rollback.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("t.delete_session_id IS NULL", sql)
        self.assertIn("t.delete_date IS NULL", sql)
        self.assertIn("WHERE t.delete_session_id IS NULL", sql)
        self.assertIn("N'AND deleted.task_id = t.task_id' + NCHAR(10)", sql)
        self.assertIn("pred.delete_session_id IS NULL", sql)
        self.assertIn("succ.delete_session_id IS NULL", sql)
        self.assertIn("schedule_quality_20260817_soft_deleted_task_modules", sql)
        self.assertIn("rollback snapshot is incomplete", sql)
        self.assertIn("ALTER VIEW", sql)
        self.assertIn("ALTER FUNCTION", sql)
        self.assertIn("schedule_quality_20260817_soft_deleted_task_modules", rollback_sql)
        self.assertIn("EXEC sys.sp_executesql @definition", rollback_sql)

    def test_refresh_cycle_pruning_has_both_edge_indexes(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        hotfix_sql = (
            SCHEDULE_QUALITY_SQL / "006_refresh_performance_hotfix.sql"
        ).read_text(encoding="utf-8")
        refresh_proc = self._module_batch(
            forward_sql, "PROCEDURE", "xertoolkit_refresh_all_schedule_quality"
        )
        hotfix_proc = self._module_batch(
            hotfix_sql, "PROCEDURE", "xertoolkit_refresh_all_schedule_quality"
        )

        self.assertEqual(hotfix_proc, refresh_proc)
        self.assertIn("IX_CycleEdges_incoming", refresh_proc)
        self.assertIn(
            "ON #CycleEdges (proj_id, successor_task_id, predecessor_task_id)",
            refresh_proc,
        )
        self.assertIn("TRUNCATE TABLE #PruneNodes", refresh_proc)
        self.assertGreaterEqual(refresh_proc.count("NOT EXISTS"), 2)
        self.assertIn("OPTION (RECOMPILE)", refresh_proc)
        self.assertIn("@oos_detail_trigger_enabled", refresh_proc)
        self.assertIn("IF @oos_detail_trigger_enabled = 0", refresh_proc)

    def test_refresh_materialises_metric_families_before_the_final_join(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        hotfix_sql = (
            SCHEDULE_QUALITY_SQL / "006_refresh_performance_hotfix.sql"
        ).read_text(encoding="utf-8")
        migration_sql = (
            SCHEDULE_QUALITY_SQL / "016_split_stage_metrics_performance.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL
            / "916_split_stage_metrics_performance_rollback.sql"
        ).read_text(encoding="utf-8")

        refresh_proc = self._module_batch(
            forward_sql, "PROCEDURE", "xertoolkit_refresh_all_schedule_quality"
        )
        hotfix_proc = self._module_batch(
            hotfix_sql, "PROCEDURE", "xertoolkit_refresh_all_schedule_quality"
        )
        self.assertEqual(hotfix_proc, refresh_proc)

        aggregate_tables = (
            "#ActivityCounts",
            "#ScopedActivityCounts",
            "#RelationshipCounts",
            "#OpenEndCounts",
            "#RelationshipQualityCounts",
            "#ActivityQualityCounts",
            "#OutOfSequenceCounts",
            "#LogicLoopCounts",
        )
        self.assertIn("SPLIT_STAGE_PROJECT_METRICS_V1", refresh_proc)
        self.assertNotIn(";WITH activity_counts AS", refresh_proc)
        for aggregate_table in aggregate_tables:
            self.assertIn(aggregate_table, refresh_proc)
            self.assertLess(
                refresh_proc.index(aggregate_table),
                refresh_proc.index("INSERT INTO #ProjectMetricsStage"),
            )

        split_start = refresh_proc.index(
            "        /* SPLIT_STAGE_PROJECT_METRICS_V1 */"
        )
        split_end = refresh_proc.index(
            "        /* Nothing visible changes before this final transactional swap. */"
        )
        canonical_split_block = refresh_proc[split_start:split_end]
        self.assertEqual(
            self._nvarchar_assignment(migration_sql, "replacement"),
            canonical_split_block,
        )

        restored_block = self._nvarchar_assignment(rollback_sql, "replacement")
        self.assertTrue(restored_block.startswith("        ;WITH activity_counts AS"))
        self.assertIn("LEFT JOIN activity_counts AS ac", restored_block)
        self.assertNotIn("SPLIT_STAGE_PROJECT_METRICS_V1", restored_block)
        self.assertIn("OBJECT_DEFINITION", migration_sql)
        self.assertIn("OBJECT_DEFINITION", rollback_sql)
        self.assertIn(
            "CHARINDEX(N'PROCEDURE', @upper_definition, @create_position)",
            migration_sql,
        )
        self.assertIn(
            "CHARINDEX(N'PROCEDURE', @upper_definition, @create_position)",
            rollback_sql,
        )
        self.assertIn("@normalized_header_infix IN (N'', N'ORALTER')", migration_sql)
        self.assertIn("@normalized_header_infix IN (N'', N'ORALTER')", rollback_sql)
        self.assertIn("N'ALTER PROCEDURE'", migration_sql)
        self.assertIn("N'ALTER PROCEDURE'", rollback_sql)
        self.assertIn("916_split_stage_metrics_performance_rollback.sql", migration_sql)

    def test_out_of_sequence_exception_contract_is_materialised(self):
        extension_sql = (
            SCHEDULE_QUALITY_SQL / "004_out_of_sequence_exceptions.sql"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "CREATE TABLE [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]",
            extension_sql,
        )
        self.assertIn(
            "PRIMARY KEY CLUSTERED (proj_id, relationship_id)",
            extension_sql,
        )
        self.assertIn("CHECK (is_out_of_sequence = 1)", extension_sql)
        self.assertIn("FK_xertoolkit_oos_exception_run", extension_sql)
        self.assertIn("FK_xertoolkit_oos_exception_config", extension_sql)
        self.assertIn("IX_xertoolkit_oos_exception_snapshot", extension_sql)
        self.assertIn("IX_xertoolkit_oos_exception_successor", extension_sql)

        required_columns = (
            "proj_id",
            "project_name",
            "project_data_date",
            "relationship_id",
            "predecessor_task_id",
            "predecessor_code",
            "predecessor_name",
            "predecessor_status",
            "predecessor_actual_start",
            "predecessor_actual_finish",
            "successor_task_id",
            "successor_code",
            "successor_name",
            "successor_status",
            "successor_actual_start",
            "successor_actual_finish",
            "relationship_type",
            "lag_hours",
            "lag_days",
            "out_of_sequence_reason",
            "is_out_of_sequence",
            "check_run_id",
            "refreshed_at",
            "config_version_id",
        )
        for column in required_columns:
            self.assertRegex(extension_sql, rf"\b{column}\b")

    def test_out_of_sequence_power_bi_view_reads_the_snapshot(self):
        extension_sql = (
            SCHEDULE_QUALITY_SQL / "004_out_of_sequence_exceptions.sql"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"CREATE OR ALTER VIEW\s+\[powerbitables\]\."
            r"\[xertoolkit_vw_PBI_OutOfSequenceExceptions\](?P<body>.*?)^GO\s*$",
            extension_sql,
            flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )

        self.assertIsNotNone(match)
        view_body = match.group("body")
        self.assertIn("xertoolkit_result_out_of_sequence_exceptions", view_body)
        self.assertNotIn("xertoolkit_fn_out_of_sequence", view_body)
        self.assertNotIn("SELECT *", view_body.upper())

    def test_out_of_sequence_status_is_an_additive_power_bi_column(self):
        status_sql = (
            SCHEDULE_QUALITY_SQL / "007_out_of_sequence_status_column.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "907_out_of_sequence_status_column_rollback.sql"
        ).read_text(encoding="utf-8")

        all_relationships_view = self._module_batch(
            status_sql, "VIEW", "xertoolkit_vw_PBI_OutOfSequence"
        )
        exceptions_view = self._module_batch(
            status_sql, "VIEW", "xertoolkit_vw_PBI_OutOfSequenceExceptions"
        )

        for view_sql in (all_relationships_view, exceptions_view):
            self.assertIn("is_out_of_sequence", view_sql)
            self.assertIn("out_of_sequence_status", view_sql)
            self.assertIn("N'out_of_sequence'", view_sql)

        self.assertIn("N'not_out_of_sequence'", all_relationships_view)
        self.assertNotIn("N'not_out_of_sequence'", exceptions_view)
        self.assertRegex(all_relationships_view, r"SELECT\s+q\.\*,")
        self.assertNotIn("out_of_sequence_status", self._module_batch(
            rollback_sql, "VIEW", "xertoolkit_vw_PBI_OutOfSequence"
        ))
        self.assertNotIn("out_of_sequence_status", self._module_batch(
            rollback_sql, "VIEW", "xertoolkit_vw_PBI_OutOfSequenceExceptions"
        ))

    def test_out_of_sequence_activity_id_is_an_additive_successor_alias(self):
        activity_sql = (
            SCHEDULE_QUALITY_SQL / "010_out_of_sequence_activity_id.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "910_out_of_sequence_activity_id_rollback.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")

        all_relationships_view = self._module_batch(
            activity_sql, "VIEW", "xertoolkit_vw_PBI_OutOfSequence"
        )
        exceptions_view = self._module_batch(
            activity_sql, "VIEW", "xertoolkit_vw_PBI_OutOfSequenceExceptions"
        )
        for view_sql in (all_relationships_view, exceptions_view):
            self.assertIn("successor_task_id AS activity_id", view_sql)
            self.assertIn("out_of_sequence_status", view_sql)

        for view_name in (
            "xertoolkit_vw_PBI_OutOfSequence",
            "xertoolkit_vw_PBI_OutOfSequenceExceptions",
        ):
            restored_view = self._module_batch(rollback_sql, "VIEW", view_name)
            self.assertNotIn("AS activity_id", restored_view)
            self.assertIn("out_of_sequence_status", restored_view)

        self.assertIn("out-of-sequence views expose additive activity IDs", postdeploy_sql)
        self.assertIn("activity IDs equal successor task IDs", postdeploy_sql)

    def test_out_of_sequence_complete_exclusion_targets_successor_activity(self):
        baseline_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        fix_sql = (
            SCHEDULE_QUALITY_SQL / "011_out_of_sequence_complete_successor_fix.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "911_out_of_sequence_complete_successor_fix_rollback.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")

        baseline_function = self._module_batch(
            baseline_sql, "FUNCTION", "xertoolkit_fn_out_of_sequence"
        )
        fixed_function = self._module_batch(
            fix_sql, "FUNCTION", "xertoolkit_fn_out_of_sequence"
        )
        rollback_function = self._module_batch(
            rollback_sql, "FUNCTION", "xertoolkit_fn_out_of_sequence"
        )

        self.assertIn("OR succ.status_code <> 'TK_Complete'", baseline_function)
        self.assertIn("OR succ.is_complete = 0", fixed_function)
        for function_sql in (baseline_function, fixed_function):
            self.assertNotRegex(
                function_sql,
                r"pred\.(?:is_complete|status_code).*AND\s+succ\.(?:is_complete|status_code)",
            )

        self.assertIn(
            "OR NOT (pred.is_complete = 1 AND succ.is_complete = 1)",
            rollback_function,
        )
        self.assertIn("complete exclusion targets the successor activity", postdeploy_sql)
        self.assertIn("@oos_completed_successor_rows", reconciliation_sql)
        self.assertIn("no completed successor activities", reconciliation_sql)

    def test_out_of_sequence_matches_p6_last_schedule_activity_count(self):
        baseline_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        parity_sql = (
            SCHEDULE_QUALITY_SQL / "012_p6_out_of_sequence_parity.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "912_p6_out_of_sequence_parity_rollback.sql"
        ).read_text(encoding="utf-8")
        extension_sql = (
            SCHEDULE_QUALITY_SQL / "004_out_of_sequence_exceptions.sql"
        ).read_text(encoding="utf-8")
        refresh_sql = (
            SCHEDULE_QUALITY_SQL / "006_refresh_performance_hotfix.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")

        for source_sql in (baseline_sql, parity_sql):
            function_sql = self._module_batch(
                source_sql, "FUNCTION", "xertoolkit_fn_out_of_sequence"
            )
            self.assertIn("relationship_type = 'PR_FS'", function_sql)
            self.assertIn("pred.act_end_date IS NULL", function_sql)
            self.assertIn("last_schedule_date", function_sql)
            self.assertIn("relationship_create_date", function_sql)
            self.assertIn("relationship_update_date", function_sql)
            self.assertNotIn("relationship_type = 'PR_SS'", function_sql)
            self.assertNotIn("relationship_type = 'PR_FF'", function_sql)
            self.assertNotIn("relationship_type = 'PR_SF'", function_sql)

        distinct_count = "COUNT(DISTINCT exception.successor_task_id)"
        self.assertIn(distinct_count, extension_sql)
        self.assertIn(distinct_count, parity_sql)
        self.assertIn(
            "COUNT(DISTINCT CASE WHEN oos.is_out_of_sequence = 1 THEN oos.successor_task_id END)",
            baseline_sql,
        )
        self.assertIn(
            "COUNT(DISTINCT CASE WHEN oos.is_out_of_sequence = 1 THEN oos.successor_task_id END)",
            refresh_sql,
        )
        self.assertIn("COUNT(DISTINCT exception.successor_task_id)", reconciliation_sql)
        self.assertIn("last P6 schedule snapshot", postdeploy_sql)
        self.assertIn("distinct successor activities", postdeploy_sql)

        restored_function = self._module_batch(
            rollback_sql, "FUNCTION", "xertoolkit_fn_out_of_sequence"
        )
        self.assertIn("relationship_type = 'PR_SS'", restored_function)
        self.assertNotIn("last_schedule_date", restored_function)
        self.assertIn("COUNT(exception.relationship_id)", rollback_sql)

    def test_logic_loop_view_exposes_exact_task_details_additively(self):
        baseline_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        detail_sql = (
            SCHEDULE_QUALITY_SQL / "008_logic_loop_task_details.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "908_logic_loop_task_details_rollback.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        full_rollback_sql = (
            SCHEDULE_QUALITY_SQL / "900_rollback.sql"
        ).read_text(encoding="utf-8")

        for source_sql in (baseline_sql, detail_sql):
            view_sql = self._module_batch(
                source_sql, "VIEW", "xertoolkit_vw_PBI_LogicLoops"
            )
            self.assertIn("xertoolkit_result_logic_loop_tasks", view_sql)
            self.assertIn("PARTITION BY logic.proj_id, logic.task_id", view_sql)
            self.assertIn("WHERE logic.row_rank = 1", view_sql)
            self.assertIn("LEFT JOIN dbo.PROJECT", view_sql)
            self.assertIn("LEFT JOIN dbo.TASK", view_sql)
            self.assertIn("LEFT JOIN dbo.PROJWBS", view_sql)
            self.assertIn("AS is_logical_loop", view_sql)
            self.assertIn("N'logical_loop'", view_sql)
            self.assertIn("AS logical_loop_status", view_sql)

            legacy_columns = (
                view_sql.index("logic.proj_id"),
                view_sql.index("logic.task_id"),
                view_sql.index("logic.loop_path"),
                view_sql.index("logic.loop_length"),
            )
            self.assertEqual(tuple(sorted(legacy_columns)), legacy_columns)

        restored_view = self._module_batch(
            rollback_sql, "VIEW", "xertoolkit_vw_PBI_LogicLoops"
        )
        self.assertNotIn("is_logical_loop", restored_view)
        self.assertNotIn("logical_loop_status", restored_view)
        self.assertNotIn("JOIN dbo.TASK", restored_view)
        self.assertIn("CREATE OR ALTER VIEW [powerbitables].[xertoolkit_vw_PBI_LogicLoops]", full_rollback_sql)
        self.assertIn("logic-loop task rows reconcile", postdeploy_sql)
        self.assertIn("logic-loop task rows use the supported identifiers", postdeploy_sql)

    def test_out_of_sequence_triggers_keep_count_and_detail_atomic(self):
        extension_sql = (
            SCHEDULE_QUALITY_SQL / "004_out_of_sequence_exceptions.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("xertoolkit_trg_project_metrics_oos_delete", extension_sql)
        self.assertIn("xertoolkit_trg_project_metrics_oos_insert", extension_sql)
        self.assertIn("AFTER DELETE", extension_sql)
        self.assertIn("AFTER INSERT", extension_sql)
        self.assertIn("JOIN deleted", extension_sql)
        self.assertIn("JOIN inserted AS result", extension_sql)
        self.assertIn(
            "CROSS APPLY [powerbitables].[xertoolkit_fn_out_of_sequence]",
            extension_sql,
        )
        self.assertIn("WHERE oos.is_out_of_sequence = 1", extension_sql)
        self.assertIn("SET out_of_sequence_count = counts.out_of_sequence_count", extension_sql)
        self.assertIn("exception.check_run_id = inserted.check_run_id", extension_sql)
        self.assertIn("exception.config_version_id = inserted.config_version_id", extension_sql)

        detail_insert = extension_sql.index(
            "INSERT INTO [powerbitables].[xertoolkit_result_out_of_sequence_exceptions]"
        )
        count_update = extension_sql.index(
            "SET out_of_sequence_count = counts.out_of_sequence_count"
        )
        self.assertLess(detail_insert, count_update)

    def test_out_of_sequence_verification_and_rollback_are_complete(self):
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")
        targeted_rollback_sql = (
            SCHEDULE_QUALITY_SQL / "904_out_of_sequence_exceptions_rollback.sql"
        ).read_text(encoding="utf-8")
        full_rollback_sql = (
            SCHEDULE_QUALITY_SQL / "900_rollback.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("out-of-sequence exception objects present", postdeploy_sql)
        self.assertGreaterEqual(postdeploy_sql.upper().count("EXCEPT"), 2)
        self.assertIn("@oos_wrong_version_rows", reconciliation_sql)
        self.assertIn("@oos_wrong_run_rows", reconciliation_sql)
        self.assertIn("@oos_orphan_rows", reconciliation_sql)
        self.assertIn("@oos_count_mismatch_projects", reconciliation_sql)
        self.assertIn("@oos_missing_exception_rows", reconciliation_sql)
        self.assertIn("@oos_extra_exception_rows", reconciliation_sql)
        self.assertGreaterEqual(reconciliation_sql.upper().count("EXCEPT"), 6)
        self.assertIn(
            "Run 913_complete_task_evidence_view_rollback.sql",
            targeted_rollback_sql,
        )

        for rollback_sql in (targeted_rollback_sql, full_rollback_sql):
            insert_trigger = rollback_sql.index(
                "DROP TRIGGER IF EXISTS "
                "[powerbitables].[xertoolkit_trg_project_metrics_oos_insert]"
            )
            detail_view = rollback_sql.index(
                "DROP VIEW IF EXISTS "
                "[powerbitables].[xertoolkit_vw_PBI_OutOfSequenceExceptions]"
            )
            detail_table = rollback_sql.index(
                "DROP TABLE IF EXISTS "
                "[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]"
            )
            self.assertLess(insert_trigger, detail_view)
            self.assertLess(detail_view, detail_table)

        unified_view = full_rollback_sql.index(
            "DROP VIEW IF EXISTS "
            "[powerbitables].[xertoolkit_vw_PBI_ScheduleQualityTaskEvidence]"
        )
        oos_table = full_rollback_sql.index(
            "DROP TABLE IF EXISTS "
            "[powerbitables].[xertoolkit_result_out_of_sequence_exceptions]"
        )
        self.assertLess(unified_view, oos_table)

    def test_task_evidence_modules_are_shared_by_forward_and_additive_deployments(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        additive_sql = (
            SCHEDULE_QUALITY_SQL / "009_schedule_quality_task_evidence.sql"
        ).read_text(encoding="utf-8")

        modules = (
            ("FUNCTION", "xertoolkit_fn_schedule_quality_task_evidence"),
            ("VIEW", "xertoolkit_vw_PBI_ScheduleQualityTaskEvidence"),
            ("TRIGGER", "xertoolkit_trg_project_metrics_task_evidence_delete"),
            ("TRIGGER", "xertoolkit_trg_project_metrics_task_evidence_insert"),
        )
        for module_type, module_name in modules:
            self.assertEqual(
                self._module_batch(forward_sql, module_type, module_name),
                self._module_batch(additive_sql, module_type, module_name),
            )

    def test_task_evidence_function_maps_the_requested_checks_to_task_ids(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        function_sql = self._module_batch(
            forward_sql,
            "FUNCTION",
            "xertoolkit_fn_schedule_quality_task_evidence",
        )

        expected_mappings = {
            "missing_predecessor": "is_missing_predecessor",
            "missing_successor": "is_missing_successor",
            "open_start": "is_open_start",
            "open_finish": "is_open_finish",
            "relationship_leads": "is_lead",
            "relationship_lags": "is_lag",
            "relationship_ratio": "is_non_fs",
            "excessive_ss_lag": "is_excessive_ss_lag",
            "excessive_ff_lag": "is_excessive_ff_lag",
            "high_duration": "is_high_duration",
            "high_float": "is_high_float",
            "negative_float": "is_negative_float",
            "constraints": "is_constraint",
            "critical_tasks": "is_critical_task",
            "near_critical_tasks": "is_near_critical_task",
            "invalid_dates": "is_invalid_date",
            "in_progress_errors": "is_in_progress_error",
            "riding_progress_date": "is_riding_progress_date",
        }
        mapped_checks = set(
            re.findall(
                r"\('([a-z_]+)',\s*[a-z]+\.(is_[a-z_]+)",
                function_sql,
                flags=re.IGNORECASE,
            )
        )

        self.assertEqual(mapped_checks, set(expected_mappings.items()))
        self.assertNotIn("logical_loops", function_sql)
        self.assertNotIn("out_of_sequence", function_sql)
        self.assertIn("rq.predecessor_task_id", function_sql)
        self.assertIn("rq.successor_task_id", function_sql)
        self.assertIn("WHERE mapped.is_match = 1", function_sql)
        self.assertIn("SELECT DISTINCT", function_sql)

    def test_task_evidence_is_materialised_with_the_project_metric_snapshot(self):
        forward_sql = (
            SCHEDULE_QUALITY_SQL / "001_versioned_settings_forward.sql"
        ).read_text(encoding="utf-8")
        additive_sql = (
            SCHEDULE_QUALITY_SQL / "009_schedule_quality_task_evidence.sql"
        ).read_text(encoding="utf-8")
        view_sql = self._module_batch(
            forward_sql,
            "VIEW",
            "xertoolkit_vw_PBI_ScheduleQualityTaskEvidence",
        )
        insert_trigger = self._module_batch(
            forward_sql,
            "TRIGGER",
            "xertoolkit_trg_project_metrics_task_evidence_insert",
        )
        delete_trigger = self._module_batch(
            forward_sql,
            "TRIGGER",
            "xertoolkit_trg_project_metrics_task_evidence_delete",
        )

        self.assertIn(
            "CREATE TABLE [powerbitables].[xertoolkit_result_schedule_quality_task_evidence]",
            additive_sql,
        )
        self.assertIn(
            "PRIMARY KEY CLUSTERED (proj_id, check_code, task_id)",
            additive_sql,
        )
        self.assertIn("FK_xertoolkit_task_evidence_run", additive_sql)
        self.assertIn("FK_xertoolkit_task_evidence_config", additive_sql)
        self.assertIn("IX_xertoolkit_task_evidence_check", additive_sql)
        self.assertIn("IX_xertoolkit_task_evidence_snapshot", additive_sql)

        self.assertIn("xertoolkit_result_schedule_quality_task_evidence", view_sql)
        self.assertNotIn("xertoolkit_fn_open_ends", view_sql)
        self.assertNotIn("xertoolkit_fn_relationship_quality", view_sql)
        self.assertNotIn("xertoolkit_fn_activity_quality", view_sql)

        self.assertIn(
            "xertoolkit_fn_schedule_quality_task_evidence",
            insert_trigger,
        )
        self.assertIn("result.check_run_id", insert_trigger)
        self.assertIn("result.refreshed_at", insert_trigger)
        self.assertIn("result.config_version_id", insert_trigger)
        self.assertIn("FROM inserted", insert_trigger)
        self.assertIn("JOIN deleted", delete_trigger)
        self.assertNotIn("UPDATE [powerbitables].[xertoolkit_result_project_metrics]", insert_trigger)
        self.assertNotRegex(
            insert_trigger,
            re.compile(r"SET\s+[a-z_]+_count\s*=", re.IGNORECASE),
        )

        for column_name in (
            "config_version_id",
            "check_run_id",
            "refreshed_at",
            "proj_id",
            "check_code",
            "check_name",
            "check_sort_order",
            "task_id",
            "task_code",
            "task_name",
            "total_float_days",
            "evidence_basis",
        ):
            self.assertRegex(view_sql, rf"\b{column_name}\b")

    def test_complete_task_evidence_view_unifies_special_checks_at_task_grain(self):
        additive_sql = (
            SCHEDULE_QUALITY_SQL / "009_schedule_quality_task_evidence.sql"
        ).read_text(encoding="utf-8")
        extension_sql = (
            SCHEDULE_QUALITY_SQL / "013_complete_task_evidence_view.sql"
        ).read_text(encoding="utf-8")
        rollback_sql = (
            SCHEDULE_QUALITY_SQL / "913_complete_task_evidence_view_rollback.sql"
        ).read_text(encoding="utf-8")
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")
        readme = (SCHEDULE_QUALITY_SQL / "README.md").read_text(encoding="utf-8")
        view_name = "xertoolkit_vw_PBI_ScheduleQualityTaskEvidence"
        view_sql = self._module_batch(extension_sql, "VIEW", view_name)

        self.assertEqual(view_sql.count("UNION ALL"), 2)
        self.assertIn("xertoolkit_result_schedule_quality_task_evidence", view_sql)
        self.assertIn("xertoolkit_result_logic_loop_tasks", view_sql)
        self.assertIn("xertoolkit_result_out_of_sequence_exceptions", view_sql)
        self.assertNotIn("xertoolkit_fn_out_of_sequence", view_sql)

        self.assertIn("scope.check_code = 'logical_loops'", view_sql)
        self.assertIn("'logical_loop_member'", view_sql)
        self.assertIn("exception.successor_task_id AS task_id", view_sql)
        self.assertIn("scope.check_code = 'out_of_sequence'", view_sql)
        self.assertIn("'out_of_sequence_successor'", view_sql)
        self.assertIn("metrics.refreshed_at", view_sql)
        self.assertIn("successor.refreshed_at", view_sql)
        self.assertIn("metrics.refreshed_at = successor.refreshed_at", view_sql)
        self.assertIn("SELECT DISTINCT", view_sql)
        self.assertIn("GROUP BY", view_sql)
        self.assertIn("CONVERT(varchar(40), exception.successor_code)", view_sql)
        self.assertIn("CONVERT(varchar(120), exception.successor_name)", view_sql)
        self.assertEqual(view_sql.count("activity.total_float_days"), 3)

        original_view = self._module_batch(additive_sql, "VIEW", view_name)
        restored_view = self._module_batch(rollback_sql, "VIEW", view_name)
        self.assertEqual(restored_view, original_view)

        self.assertIn(
            "schedule-quality task evidence view includes the two dedicated validation sources",
            postdeploy_sql,
        )
        self.assertNotIn(
            "Unified task evidence reconciles Logical Loops and Out of Sequence",
            postdeploy_sql,
        )
        self.assertIn(
            "Unified Out of Sequence rows equal their project counts",
            reconciliation_sql,
        )
        self.assertIn("@unified_task_evidence_duplicate_rows", reconciliation_sql)
        self.assertIn("@unified_logic_loop_mismatch_projects", reconciliation_sql)
        self.assertIn("@unified_oos_mismatch_projects", reconciliation_sql)
        self.assertIn("013_complete_task_evidence_view.sql", readme)
        self.assertIn("913_complete_task_evidence_view_rollback.sql", readme)

    def test_task_evidence_deployment_verification_and_rollback_are_complete(self):
        postdeploy_sql = (
            SCHEDULE_QUALITY_SQL / "002_postdeploy_verify.sql"
        ).read_text(encoding="utf-8")
        reconciliation_sql = (
            SCHEDULE_QUALITY_SQL / "003_all_project_reconciliation.sql"
        ).read_text(encoding="utf-8")
        additive_sql = (
            SCHEDULE_QUALITY_SQL / "009_schedule_quality_task_evidence.sql"
        ).read_text(encoding="utf-8")
        targeted_rollback_sql = (
            SCHEDULE_QUALITY_SQL / "909_schedule_quality_task_evidence_rollback.sql"
        ).read_text(encoding="utf-8")
        full_rollback_sql = (
            SCHEDULE_QUALITY_SQL / "900_rollback.sql"
        ).read_text(encoding="utf-8")
        view_name = "xertoolkit_vw_PBI_ScheduleQualityTaskEvidence"
        function_name = "xertoolkit_fn_schedule_quality_task_evidence"
        table_name = "xertoolkit_result_schedule_quality_task_evidence"

        self.assertIn("schedule-quality task evidence objects are present", postdeploy_sql)
        self.assertIn("schedule-quality task evidence snapshot has the expected columns", postdeploy_sql)
        self.assertIn("schedule-quality task evidence exposes the expected columns", postdeploy_sql)
        self.assertIn("schedule-quality task evidence uses only supported checks", postdeploy_sql)
        self.assertIn("schedule-quality task evidence matches its project metric snapshot", postdeploy_sql)
        self.assertIn("@task_evidence_wrong_version_rows", reconciliation_sql)
        self.assertIn("@task_evidence_wrong_run_rows", reconciliation_sql)
        self.assertIn("@task_evidence_orphan_rows", reconciliation_sql)
        self.assertIn("@task_evidence_missing_rows", reconciliation_sql)
        self.assertIn("@task_evidence_extra_rows", reconciliation_sql)
        self.assertIn("CREATE OR ALTER FUNCTION", additive_sql)
        self.assertIn("CREATE OR ALTER VIEW", additive_sql)
        for rollback_sql in (targeted_rollback_sql, full_rollback_sql):
            self.assertIn(f"DROP VIEW IF EXISTS [powerbitables].[{view_name}]", rollback_sql)
            self.assertIn(f"DROP FUNCTION IF EXISTS [powerbitables].[{function_name}]", rollback_sql)
            self.assertIn(f"DROP TABLE IF EXISTS [powerbitables].[{table_name}]", rollback_sql)
            self.assertIn("xertoolkit_trg_project_metrics_task_evidence_insert", rollback_sql)
            self.assertIn("xertoolkit_trg_project_metrics_task_evidence_delete", rollback_sql)
