# Design Overview PostgreSQL views

**Replacement deployed on 4 November 2026 after the user's final rule decisions.**
Use `010_rebuild_views.sql`, `011_verify_rebuild.sql` and the current rules in
`REPLACEMENT-CONTRACT.md`. Eleven public views cover baseline selection,
Design Start achievement, period/baseline metrics, history, future and movement.
Deployment and validation receipts are private operational evidence, retained outside public source control with the verified recovery archive. Use the versioned SQL and contract documentation here to review the implementation.

## Historical 3 November implementation

The remainder of this README describes the removed version. Do not run its
001 installer, 002 verification, old assertions or old removal script against
the replacement. Its finish-based Design Start and all-completions Achieved
definitions have been superseded by the replacement contract.

SQL-only implementation of Design brief.docx and the decisions agreed on
3 November 2026. Targets `p6_reporting` on XPS-Server-4. Requires the existing
Coding Check private helpers; no P6 source data or dashboard is changed.

## Reader views

| View in reporting | Grain and use |
| --- | --- |
| design_overview_submissions | One ordinary eligible P6 project/submission, including invalid or incomplete classifications with a comparison_status |
| design_overview_activity_detail | One design activity per submission, with scheduled/actual dates, all five code dimensions and project/planner fields |
| design_overview_activity_code_counts | Submission, dimension and code bucket; each dimension reconciles to the design activity detail |
| design_overview_performance_detail | Current submission and matched Activity ID, retaining both versions and independent outcome flags |
| design_overview_performance_code_counts | Current submission, dimension, classification basis and code bucket; reconciles to performance detail |
| design_overview_issues | Submission or activity issues preventing a reliable comparison |

## Submission selection and periods

- P6 22 only, active P6 project rows, Project State Live and Project Status
  Client Submitted. Reuses the existing active project-code resolution.
- Stored baselines (`PROJECT.orig_proj_id IS NOT NULL`) stay in Coding Check
  with the new baseline markers, but do not represent separate design submissions.
  A visible ordinary project incorrectly labelled Client Submitted cannot be
  inferred to be a baseline from its name or old data date. Correct its project
  classification in P6; NE-S-12 was identified by the user as such a case.
- The logical project key is the active Project ID project-code value ID,
  not the P6 submission's native project ID, name or description. `project_code`
  retains the short business code; `"Project ID"` retains the existing description.
- Full Period Code comes from the active Period hierarchy, e.g.
  `2026/27.P7`, never from a programme name. Valid years and P1 through P13
  are ordered numerically, including P13 to the next financial year's P1.
- `submission_index=1` is the latest coded period for that logical project;
  `2` is the previous calendar period. Gaps retain their calendar distance.
  `global_submission_index` aligns all projects with the latest period found
  across the dataset, so a stale project's latest submission is distinguishable.
- Each comparison requires unique submissions for the current period and its
  exact preceding financial period. Missing/ambiguous periods or Project IDs,
  and missing/non-increasing data dates remain visible and are not guessed.
- The interval is **previous submission data date inclusive, current submission
  data date exclusive**, using the stored P6 timestamps unchanged. For P7 this
  might be `[2026-09-20 00:01, 2026-10-18 00:01)`. These are P6 data dates, not
  PostgreSQL capture/publication times. Boundary events belong to one period.

## Design activity and date rules

- At least one active, nonblank, valid AS_Global Design Key assignment identifies
  a design activity. Project/EPS-scoped lookalikes and deleted links do not qualify.
- All active activity types/statuses are available; physical deletions and
  Activity Status DEL are excluded using Coding Check conventions. A prior
  activity removed from the current eligible population remains in the comparison.
- Other classifications use the same five dimensions, normalized Project Stage
  labels, distinct assignments and explicit uncoded/conflict buckets as Coding Check.
  Multiple valid Design Key values preserve design membership and appear once in
  the conflict bucket. Raw values and conflicting assignments remain in `codes` JSON.
- Compare exact Activity IDs (`task_code`) within the logical project. Native
  task IDs/GUIDs can change on copying. Missing/duplicate Activity IDs are flagged;
  there is no fuzzy task-name matching or arbitrary many-to-many pairing.
- Match the entire eligible activity population first, then retain Design Key
  membership in **either** submission. Removed codes cannot erase the original plan.
- The original plan is the **previous Client Submitted programme's scheduled
  dates**. For incomplete activities the scheduled fields are `restart_date` and
  `reend_date`; actual dates apply after start/completion. Do not substitute P6
  Planned dates (`target_*`) or Early dates (`early_*`) for missing schedule dates.
  Raw planned, remaining and actual dates and the selected event source are exposed.
- Count normal activities and finish milestones by finish event, regardless of
  how many periods the activity spans. Start milestones use their sole start event.

Date semantics follow Oracle's [P6 22.12 activity date definitions](https://docs.oracle.com/cd/F51303_01/client_help/en_US/activity_dates.htm).

## Performance measures and unknowns

- `is_planned`: incomplete design activity in the previous submission with a
  scheduled counting event inside the reporting interval.
- `is_achieved`: activity marked complete in the current submission with the
  actual counting event inside the interval. Includes unplanned completion.
- `is_missed`: planned work with a known outcome that was not achieved in that period.
- `is_unplanned`: achieved work that was not in the original period's design plan;
  this includes existing activities brought forward, not just newly created rows.
- NULL flags represent insufficient or ambiguous evidence, not false/zero.
  Missing current activities are not proof of completion or a confirmed miss.
  Missing original schedule dates are not proof that a completion was unplanned.
  `unknown_planned` and `unknown_plan_achieved` keep these populations reconcilable.
- `planned = planned_achieved + missed + unknown_planned`.
  `achieved = planned_achieved + unplanned + unknown_plan_achieved`.
  Achieved and Unplanned overlap; do not add them as independent outcomes.

Performance counts expose **two classification bases**. Filter exactly one:
`code_basis='plan'` attributes every measure to the previous codes;
`code_basis='outcome'` attributes every measure to the current codes. This avoids
silently mixing disciplines/companies when an activity is recoded. Both sets
remain available in detail. Missing sides use `No activity in submission`.
Sum exactly one code dimension and one basis; never sum all five dimensions or
both bases. Coding summary `project_activity_count` is a repeated denominator.

## Install and validate

For an existing production installation, apply Coding Check migration
`../coding_check/006_mark_stored_baselines.sql`, then `001_create_views.sql`
here. Both are transactional and additive; use `psql -X -v ON_ERROR_STOP=1`.
Reader grants and the publication worker's grant option are included.

For a fresh isolated database named `coding_check_test_*`, run:

1. `../coding_check/tests/fixtures.sql`
2. `tests/fixtures.sql`
3. `../coding_check/001_create_views.sql`
4. `../coding_check/006_mark_stored_baselines.sql`
5. `001_create_views.sql`
6. `tests/assertions.sql`

The behavioral tests cover full period paths/year rollover, stored baselines,
missing/ambiguous submissions, milestone boundaries, recoding, additions/removals,
unknown dates/outcomes, duplicate Activity IDs, counts/detail reconciliation and
actual reader-role access. Production verification should reconcile the five
dimensions and both bases independently and inspect comparison_status/issues.

These views compare P6's saved submission copies, not archived PostgreSQL refresh
generations. Editing/deleting a P6 submission will change later reporting results.
Immutable historical acceptance evidence would require a separate retained store.
