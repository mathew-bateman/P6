# Construction Progress reporting

The additive integrated Gantt is documented in [GANTT.md](GANTT.md), with a
[column dictionary](GANTT-COLUMNS.md) and
Deployment and validation receipts are private operational evidence, retained outside public source control with the verified recovery archive. Use the versioned SQL and contract documentation here to review the implementation.
`003_construction_gantt_detail.sql` creates the single requested
`p6_reporting.construction_gantt_detail` view without replacing the four views
below. Validate with `004_validate_gantt.sql`; rollback only this addition with
`903_construction_gantt_rollback.sql`.

These additive P6 22 views reuse `reporting.design_overview_submissions`,
`design_overview.activity_facts` and the Design Overview baseline selector.
Run `001_create_views.sql` with `psql -X -v ON_ERROR_STOP=1`. `002_validate.sql`
is read-only. `900_rollback.sql` removes only these objects without CASCADE.
The source database is a published reporting copy; past P6 submissions can be
edited and are not an immutable archive.

## Views and model

* `reporting.construction_activity_detail` is one row per current ES6 discrete
  activity per cut, including project/cut fields, `task_id`, `task_code`, name,
  `task_type`, status, WBS ID/short name/name/parent ID, current and baseline
  start/finish, actual start/finish, full code JSON, discipline ID, short name,
  description, bucket status and assignments. ES6 must be Global type 379/value
  4328. Start/finish milestones and LOE are excluded. Other coded ordinary
  activities remain in this population.
* `reporting.construction_activity_movement` adds `actual_complete`,
  `baseline_planned_complete`, `previous_planned_complete` and
  `previous_membership_status`. All three are NULL when the data date is
  missing. Comparator flags are NULL for missing or ambiguous comparisons.
  `baseline_match_status` and `previous_match_status` distinguish missing
  activity, missing date, duplicate IDs and unavailable cut/baseline.
* `reporting.construction_milestone_detail` is one row per activity with either
  a P6 start/finish milestone type or at least one Global Milestone Type
  assignment. It is independent of ES6. `classification_source` identifies
  `p6_type`, `code` or `p6_type_and_code`; `milestone_assignments` holds all
  classifications. `baseline_date`, `current_date`, `forecast_date`,
  `actual_date` and `baseline_movement_days` use start for P6 start milestones
  and finish for other rows. Positive movement means later. `current_date` is
  the existing scheduled date, which is actual when complete; `forecast_date`
  is the raw remaining early start/finish and can be NULL.
* `reporting.construction_milestone_type_bridge` has one row per distinct Global
  code assignment with `activity_row_key`, `proj_id`, `task_id`,
  `milestone_type_id`, `milestone_value_id`, `milestone_short_name`, and
  `milestone_description`. P6-type-only milestones have no bridge row and are
  visible when no milestone-type slicer is applied.

All detail views include native `proj_id` (cut), `project_code_id` (logical
project), period and selected baseline fields from the shared helper, plus
`activity_row_key` (`proj_id:task_id`). Join the existing selected-cut dimension
to each fact by `proj_id`. Make the report's existing selected-cut logic choose
the latest eligible cut **per project** within the selected period, or latest
per project without a period filter. The SQL views retain all cuts: importing
them alone does not implement that selection. Verify the actual Power BI model
relationships and selected-cut DAX; this repository does not establish them.
Use a milestone type dimension related to the bridge by value ID and filter
the milestone fact using the bridge's `activity_row_key`, e.g. a controlled
bidirectional bridge relationship or a `TREATAS` measure. Count distinct fact
rows, not bridge rows, to prevent multiple codes multiplying milestones. Keep
activity and milestone fact filtering separate; a code-only milestone may also
be a construction activity, by design.

The immediate previous cut is the unique latest **earlier calendar data date**
within the same logical project, including a cut without a period code. A tie
on either the current or preceding data date yields no predecessor. The view
exposes `chronological_previous_proj_id`, period, data date, status,
`period_distance`, `period_gap`, `same_date_count` and `undated_cut_count`.
A NULL gap means period codes are not comparable; negative or zero distance is
also flagged. Undated other cuts cannot be ordered, so their count is exposed.
The original helper's `previous_proj_id` is the different exact-period rule.

Exact unique `task_code` is matched within the selected logical project and
identified native comparator cut, never across projects. Comparators need not
be ES6 coded. `previous_had_es6` and `previous_membership_status` identify
recoding in; missing previous activities are distinct from present activities
with missing forecast. Current-only grain means recoded-out/deleted activities
are not counted in the current denominator; inspect consecutive cuts separately
to review those changes. Duplicate IDs are not matched. Duplicate identical
code links are deduplicated by the existing helper; multiple different code
values remain explicit in assignment JSON/bridge.

Baseline start/finish use the existing selected separate BL programme's
scheduled dates: actual for completed work, otherwise remaining early date with
the baseline's own target-date fallback. Previous forecast finish is strictly
the previous cut's `reend_date`, with no target or actual-date substitution;
previously completed activities can consequently have an unknown prior forecast.
Actual completion depends only on `act_end_date` on or before the cut calendar
date; status is exposed for exception review. No quantities or physical
percentages are used. The selected baseline is the latest valid baseline under
the existing rule and applies to historical cuts too.

## Power BI measures

Apply the existing selected-cut/project filter to `construction_activity_movement`
before evaluating these measures. Replace the table name if renamed on import.
Use the appropriate planned measure for each comparison. Percentages are
fractions formatted as percentage; variance is a numeric percentage-point value.
Unknown actual dates mean not completed under the agreed Actual Finish rule.

```DAX
Construction activities = COALESCE(COUNTROWS('construction_activity_movement'), 0)
Actual complete = CALCULATE([Construction activities], 'construction_activity_movement'[actual_complete] = TRUE())
Actual unknown = COALESCE(COUNTROWS(FILTER('construction_activity_movement', ISBLANK('construction_activity_movement'[actual_complete]))), 0)
Actual completion % = IF([Actual unknown] > 0, BLANK(), DIVIDE([Actual complete], [Construction activities]))
Baseline planned complete = CALCULATE([Construction activities], 'construction_activity_movement'[baseline_planned_complete] = TRUE())
Baseline unknown = COALESCE(COUNTROWS(FILTER('construction_activity_movement', ISBLANK('construction_activity_movement'[baseline_planned_complete]))), 0)
Baseline known = [Construction activities] - [Baseline unknown]
Baseline planned % = IF([Baseline unknown] > 0, BLANK(), DIVIDE([Baseline planned complete], [Construction activities]))
Baseline variance pp = IF(ISBLANK([Baseline planned %]), BLANK(), 100 * ([Actual completion %] - [Baseline planned %]))
Previous planned complete = CALCULATE([Construction activities], 'construction_activity_movement'[previous_planned_complete] = TRUE())
Previous unknown = COALESCE(COUNTROWS(FILTER('construction_activity_movement', ISBLANK('construction_activity_movement'[previous_planned_complete]))), 0)
Previous known = [Construction activities] - [Previous unknown]
Previous planned % = IF([Previous unknown] > 0, BLANK(), DIVIDE([Previous planned complete], [Construction activities]))
Previous variance pp = IF(ISBLANK([Previous planned %]), BLANK(), 100 * ([Actual completion %] - [Previous planned %]))
```

Use conformed project/discipline dimensions for
cross-fact filtering; a project slicer connected to one fact alone does not
automatically filter the others. SQL summary rows are deliberately omitted;
reassess only after measuring the actual Power BI refresh and visuals.
