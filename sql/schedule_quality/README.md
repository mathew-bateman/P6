# Versioned schedule-quality SQL deployment

These scripts move the XER Toolkit schedule-quality rules into versioned SQL Server configuration owned by the `powerbitables` schema. Django is only the editor/publisher; SQL Server remains the source of truth used by the detail views and materialised results.

Run against `P62212_1` in this order:

1. `000_predeploy_snapshot.sql`
2. `001_versioned_settings_forward.sql`
3. `004_out_of_sequence_exceptions.sql`
4. `007_out_of_sequence_status_column.sql`
5. `008_logic_loop_task_details.sql`
6. `009_schedule_quality_task_evidence.sql`
7. `010_out_of_sequence_activity_id.sql`
8. `011_out_of_sequence_complete_successor_fix.sql`
9. `012_p6_out_of_sequence_parity.sql`
10. `013_complete_task_evidence_view.sql`
11. `015_critical_float_rounding_boundary.sql`
12. `016_split_stage_metrics_performance.sql`
13. `029_exclude_deleted_activities.sql`
14. `030_out_of_sequence_deleted_filter_performance.sql`
15. `031_configurable_open_end_evidence.sql`
16. `002_postdeploy_verify.sql`
17. Run a single-project refresh for the canary project, then a full refresh.
18. Run `003_all_project_reconciliation.sql` immediately after the full refresh.

Use `900_rollback.sql` only if the forward deployment must be reversed. It depends on the immutable snapshot created by step 1.

## 2026-07-15 performance hotfix

For an existing versioned deployment, run `005_performance_hotfix.sql` followed by `006_refresh_performance_hotfix.sql`. The first replaces only the three calculation TVFs responsible for repeated `TASK`/`TASKPRED` scans. The second optimizes cycle pruning, compiles canary/full staging for their actual cardinality, and avoids duplicate out-of-sequence aggregation when the authoritative detail trigger is enabled. Neither script changes settings or materialised data by itself. Then run a canary refresh, a full refresh, and `003_all_project_reconciliation.sql`.

Run `016_split_stage_metrics_performance.sql` on an existing deployment to
prevent SQL Server from expanding every calculation TVF into one optimizer-heavy
project-metrics statement. It materialises each aggregate family once into a
small per-project temporary table, then joins those results into the existing
staging table. Calculation rules, the structured `Activity Status = DEL`
exception, SQL-table-driven settings, and publication semantics are unchanged.
Run a canary refresh, a full refresh, and
`003_all_project_reconciliation.sql` after deployment. Use
`916_split_stage_metrics_performance_rollback.sql` to restore only the prior
monolithic metrics statement while retaining the other performance and parity
improvements.

The High Duration validation in `001_versioned_settings_forward.sql` and
`005_performance_hotfix.sql` compares the configured threshold with P6
remaining duration (`remain_drtn_hr_cnt`), matching the remaining-duration
field exposed by the activity-quality validation view. Rerun
`005_performance_hotfix.sql` and refresh the materialised results to apply this
rule change to an existing deployment.

The Missing Predecessor and Missing Successor calculations ignore both P6
soft-deleted rows and activities assigned the structured P6 activity code
`Activity Status = DEL` (`Deleted / Retired`). Retired activities are excluded
as exceptions, while their existing relationship rows still count as logic for
active activities. Activity-name text is not inspected. Open Start and Open
Finish ignore P6 soft-deleted rows. Rerun `005_performance_hotfix.sql` and
refresh the materialised results to apply this rule to an existing deployment.

Open Start treats an SS predecessor as valid only when the same predecessor and
successor pair also has an FF relationship. Open Finish applies the mirror
rule to an FF successor: it also needs SS for the same pair. Rerun
`005_performance_hotfix.sql`, then `027_configured_evidence_all_tables.sql`,
and refresh the materialised results. The refresh reads `TASKPRED.pred_type`
and the related `TASK.task_code` and `TASK.task_name` directly from P6, adding
them as fixed evidence for these checks. Open Start resolves the predecessor;
Open Finish resolves the successor. No pseudo P6 table or configured evidence
field is created.

The High Float validation is inclusive: a configured threshold of 84 days
includes activities with total float of 84 days or more. Rerun
`005_performance_hotfix.sql`, refresh the materialised results, and rerun
`013_complete_task_evidence_view.sql` to apply the inclusive rule and expose
`total_float_days` to an existing Power BI deployment.

The Critical Tasks validation follows the whole-day display rounding used by
the report's standard eight-hour day conversion. An in-scope activity is
critical below four raw hours of total float; at four hours it rounds to half a
day and belongs to Near Critical instead. Near Critical therefore starts at
four hours and continues through its configured upper limit, with no overlap.
Run `015_critical_float_rounding_boundary.sql` on an existing deployment, then
refresh the materialised schedule-quality results. Use
`915_critical_float_rounding_boundary_rollback.sql` to restore the original
zero-hour boundary.

Run `028_configured_evidence_display_formats.sql` on an existing deployment
to add a **Display** choice to every configured evidence field. `Native P6
value` is the default. `P6 hours + calculated days` is intended for P6
hour-count fields (for example, `*_hr_cnt`) and displays both the unchanged
P6 value and the reporting calculation: `P6: 80.00 hours | Calculated: 10.00
days (8h/day)`. Publish the settings and run a refresh to rebuild the
materialised evidence.

Run `029_exclude_deleted_activities.sql` on an existing deployment to add the
global **Exclude activities marked as deleted** scope control. It defaults to
enabled and identifies deleted activities from the structured P6 chain
`TASKACTV -> ACTVTYPE -> ACTVCODE`, where the activity-code type is
`Activity Status` and the short code is `DEL`. The script preserves later
evidence-formatting procedure changes, updates configuration hashes, and
applies the setting to activity, relationship, open-end, out-of-sequence, and
logical-loop results. Publish the desired setting to rebuild the materialised
Power BI rows.

Run `030_out_of_sequence_deleted_filter_performance.sql` after `029` on an
existing deployment. It preserves the same direct P6 `TASK`, `TASKPRED`, and
structured `TASKACTV -> ACTVTYPE -> ACTVCODE` evidence and exclusion rules, but
removes the two expanded Activities-view joins from the out-of-sequence trigger
path. The function reads the predecessor and successor `TASK` rows directly and
evaluates the `Activity Status = DEL` set once, avoiding the CPU-bound refresh
regression introduced by the original `029` function shape. Run a canary and a
full refresh after deployment. Use
`930_out_of_sequence_deleted_filter_performance_rollback.sql` only to restore
the prior function shape in an emergency.

Run `031_configurable_open_end_evidence.sql` after the configured-evidence
scripts on an existing deployment. It removes the permanently injected Open
Start/Open Finish context rows and makes the settings table authoritative.
The legacy `relationship_summary` placeholder is presented in the editor as
the real P6 selections `TASKPRED.pred_type`, `TASK.task_code`, and
`TASK.task_name`; saving or publishing persists those rows. Open Start resolves
the configured `TASK` fields through the predecessor endpoint and Open Finish
through the successor endpoint. Users can then add, remove, relabel, or reorder
these fields without another SQL change.

For a performance-only rollback, run `906_refresh_performance_hotfix_rollback.sql` and then `905_performance_hotfix_rollback.sql`. They restore the exact procedure and function definitions that were live before these hotfixes. Do not rerun the full pre-versioning rollback.

Measure refresh duration from `xertoolkit_refresh_run_history`. The deep reconciliation is a separate audit query that recalculates the detail results and must not be included in the refresh-duration comparison.
Use `904_out_of_sequence_exceptions_rollback.sql` when only the additive
out-of-sequence detail extension needs to be removed. If the unified task
evidence view is installed, run `913_complete_task_evidence_view_rollback.sql`
first so the view no longer depends on the exception table.

Run `007_out_of_sequence_status_column.sql` on an existing deployment to append
`out_of_sequence_status` to both Power BI out-of-sequence views. The existing
`is_out_of_sequence` Boolean/numeric column remains unchanged. Use
`907_out_of_sequence_status_column_rollback.sql` to remove only the text column.

Run `008_logic_loop_task_details.sql` on an existing deployment to enrich the
existing `xertoolkit_vw_PBI_LogicLoops` view with project and task details plus
the additive `is_logical_loop` and `logical_loop_status` identifiers. The first
four legacy columns remain unchanged. Use
`908_logic_loop_task_details_rollback.sql` to restore the original four-column
view without changing the materialised loop rows or project counts.

Run `009_schedule_quality_task_evidence.sql` on an existing deployment to add
the materialised task-evidence table, its refresh triggers, and
`xertoolkit_vw_PBI_ScheduleQualityTaskEvidence`. The next canary or full
schedule-quality refresh replaces the affected projects' evidence inside the
same transaction as their project metrics. The view returns one row per unique
project, check, and task from the 18 checks stored in this base evidence table.
Logical Loops and Out of Sequence remain in their dedicated materialised result
tables until `013_complete_task_evidence_view.sql` unifies them at the Power BI
view layer. Both the internal numeric `task_id` and the user-facing P6
`task_code` are included. Relationship checks return both predecessor and
successor task endpoints. Use
`909_schedule_quality_task_evidence_rollback.sql` to remove this extension.

Run `010_out_of_sequence_activity_id.sql` on an existing deployment to append
`activity_id` to both out-of-sequence Power BI views. It is an alias of
`successor_task_id`, identifying the successor activity whose progress makes the
relationship out of sequence. Use
`910_out_of_sequence_activity_id_rollback.sql` to remove only this alias.

Run `011_out_of_sequence_complete_successor_fix.sql` to make the Out of Sequence
`exclude_complete` setting apply to the qualifying successor activity. When it
is enabled, successors with `TK_Complete` are excluded even if their predecessor
is incomplete; completed predecessors remain eligible when the successor is not
complete. Rebuild the materialised results after deployment. Use
`911_out_of_sequence_complete_successor_fix_rollback.sql` to restore the
workbook's original both-tasks-complete rule.

Run `012_p6_out_of_sequence_parity.sql` to make the Out of Sequence headline
match P6's last scheduling run. It counts distinct successor activities, not
relationships, and qualifies only Finish-to-Start successors that had started
while their predecessor remained unfinished at `PROJECT.last_schedule_date`.
Relationships created or changed after that timestamp are excluded until P6 is
scheduled again. Projects without a P6 schedule timestamp return zero because
there is no schedule-log snapshot to reproduce. The materialised exception view
continues to retain every qualifying relationship for drill-through. Rebuild the
materialised results after deployment. Use
`912_p6_out_of_sequence_parity_rollback.sql` to restore the immediate pre-parity
011 relationship rule and relationship-level headline count.

Run `013_complete_task_evidence_view.sql` to expose all 20 validations through
`xertoolkit_vw_PBI_ScheduleQualityTaskEvidence`. Its 12-column contract includes
`total_float_days` from the shared Activities view. Logical Loops contributes
one row per distinct loop activity. Out of
Sequence contributes one row per distinct successor activity, matching the P6
headline while its dedicated exception view retains every relationship and
reason for drill-through. The existing `[check_name]` field can therefore drive
one Power BI slicer and task table for all validations. This is a view-only
extension: when the three materialised result sets are already current, only a
Power BI dataset refresh is needed. Use
`913_complete_task_evidence_view_rollback.sql` to restore the original 18-check
view.

The Relationship Ratio headline is project-level and cannot identify a single
failing task. Its rows in the task-evidence view deliberately expose the
existing Non-FS relationship endpoints governed by the same check setting; the
`evidence_basis` value makes that interpretation explicit.

Task evidence is the current snapshot for each project, stamped with the same
`check_run_id`, `refreshed_at`, and `config_version_id` as its project metric.
The unified view keeps one row per project, check, and task. A later refresh
replaces the earlier task rows for that project; this is not a history table.
Run refreshes while P6 imports are paused so the metric and its evidence are
calculated from a stable source state.

## Runner contract

The files use SQL Server `GO` batch separators because `CREATE OR ALTER VIEW`, `FUNCTION`, and `PROCEDURE` must begin their batches. A pyodbc runner must split only lines that match `^\s*GO\s*(?:--.*)?$` case-insensitively and execute the resulting batches in order. Do not send a complete file to `cursor.execute()` unchanged.

All batches from one script must run on the same SQL connection/session. This is required for scripts such as the performance hotfix and task-evidence deployment, whose transactions intentionally span `GO` batch boundaries.

The forward script changes schema and configuration only. It deliberately does not run the potentially long all-project rebuild. Existing materialised rows retain `config_version_id = NULL` until refreshed, which identifies them honestly as legacy/unversioned.

The out-of-sequence extension is also schema-only when deployed. The next
single-project or full refresh populates
`xertoolkit_result_out_of_sequence_exceptions`. Its Power BI view has one row
per qualifying predecessor/successor relationship. The project headline count
is set from those materialised rows inside the same transaction, so a displayed
count can be drilled through to the exact supporting relationships from the same
run and configuration version.

"All projects" means every row in `dbo.PROJECT`, including rows whose `project_flag` is not `Y`. The Projects view retains the legacy `project_flag = 'Y'` condition only inside its updated-date helper; it must not filter the final project set.

## Publication invariant

`xertoolkit_publish_schedule_quality_config` stages a complete all-project rebuild using the draft version. Only after staging succeeds does one transaction swap the materialised rows and activate the configuration pointer. Failure leaves the previous active settings and results in place and marks the run failed.

## Dry-run strategy

- Run the snapshot and forward scripts first against a restored copy of `P62212_1`.
- Run `002_postdeploy_verify.sql`; every `PASS` assertion must pass.
- Execute `xertoolkit_refresh_all_schedule_quality @proj_id = 1444` for the Radlett canary and reconcile the detail views to the staged materialised counts.
- Execute the full refresh on the restored copy before scheduling the production window.
- Immediately run `003_all_project_reconciliation.sql`. It asserts exact project-ID coverage, active version/run stamps, and all 24 numeric metrics plus project name and data date for every project. Run it while P6 imports are paused; source changes after the refresh are correctly reported as freshness failures.
- On production, take the snapshot, deploy, verify, run the same canary, then run the full refresh. The scripts do not mutate the production database merely by being present in this repository.
