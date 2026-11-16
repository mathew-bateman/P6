# Agreed Design Overview reporting contract

Active installer: 010_rebuild_views.sql. Production verification:
011_verify_rebuild.sql. Rules agreed 4 November 2026 supersede version 001.

## Programmes and baselines

P6 22 ordinary active Live / Client Submitted projects are reporting cuts.
The logical project identity is the Project ID project-code value ID. Full
Period Code is reconstructed from its financial-year/period hierarchy and
ordered numerically, including P13 to the next year's P1.

Baselines are separate ordinary active projects with Project Status short code
BL and description Baseline. Project State need not be Live for a baseline.
Projects Status is also recognized as a code-type alias. Stored P6 baselines
(orig_proj_id populated) and assigned Project Baseline references are not used.
Choose the latest full Period Code for the logical project. Tied latest periods
or missing/invalid baseline periods make selection unresolved; no name,
creation-date or numeric project-ID fallback. The latest baseline currently
available applies to all selected cuts, including historical ones, as requested.
Its ID and period remain visible, so a changed comparator is inspectable.

Match previous and baseline activities using exact Activity ID (task_code) in
their respective programme versions. Native task IDs/GUIDs can change on copy.
Baseline activities need no Design Key assignment. Missing/duplicate identities
stay visible instead of creating arbitrary or many-to-many matches.

## Event dates and outcomes

Design scope is a valid active Global Design Key assignment. Other coding and
active/deleted/DEL rules follow Coding Check. Conflicting Design Keys are
visible but their event basis is unresolved. Match previous/current activities
before retaining design membership in either version; removing a code cannot
erase originally planned work. Both code versions remain available.

D-Start uses P6 Start for every activity type: Actual Start once started,
Remaining Early Start for scheduled not-started work, and Planned Start if
Remaining Early Start is absent before scheduling. start_date_source identifies
the supplying raw field. A progressed activity missing Actual Start stays NULL.
Actual Start achieves Design Start even while the activity remains In Progress.
Other checkpoints use Finish/completion; a start milestone uses its sole event.
Oracle reference: https://docs.oracle.com/cd/F51303_01/client_help/en_US/activity_dates.htm

The exact preceding financial period supplies the previous plan. Both cuts
must be unique with increasing data dates. Use calendar dates [previous,current)
consistently for planned and actual events. Raw P6 timestamps remain available.

- Planned: previous design event in [L,U).
- Achieved: Planned AND checkpoint reached at the selected cut.
- Missed: Planned AND checkpoint not reached.
- Unplanned: newly reached in [L,U), not previously reached, previous forecast
  event >= U. Missing prior activity/date/status is unknown, not automatically
  unplanned. This retains the guide's pulled-forward definition.
- Baseline Planned: matched baseline event < U, with no lower bound.
- Baseline Achieved/Missed: baseline planned AND reached/not reached.
- Baseline Unplanned: reached before U without a baseline event due before U;
  absence of an activity in a resolved baseline qualifies, while absence of a
  resolved baseline programme does not.
- Remaining: checkpoint not reached. Started D-Start is not remaining start work.
- Movement: current minus previous/baseline event calendar dates for remaining
  checkpoints. D-Start uses Start movement; finish_movement_days separately
  exposes finish movement for incomplete activities. NULL comparators are
  excluded from averages. Six bands include no comparable movement.
- Future: current remaining checkpoint events in [U,U+28), [U+28,U+56),
  [U+56,U+84), with an exact activity evidence list for every count.

NULL flags/statuses and separate unknown counts preserve missing comparisons.
Known counts describe a partial population if unknown counts are nonzero.
Planned = Achieved + Missed + unknown planned outcomes; baseline follows the
same identity. Completion % = Achieved / Planned, as a 0–1 fraction. No plan
or unresolved planned/achievement population gives NULL rather than zero.

## Public views

All have prefix reporting.design_overview_:

| Suffix | Purpose |
| --- | --- |
| submissions | Cut catalogue, period indexes, previous/baseline IDs and readiness |
| baselines | Separate BL candidates, full periods and selection status |
| activity_detail | Scoped dates, raw fields, five code labels and JSON evidence |
| activity_code_counts | Design counts by cut/dimension/code, with zero dictionary values |
| performance_detail | Activity-level outcomes, comparator dates, movement and issues |
| performance_code_counts | Measures by dimension and plan/outcome classification basis |
| checkpoint_summary | Checkpoints and inclusive all, percentages, movement means/bands |
| history | Summaries for up to five available periods per logical project |
| future_activity_detail | Exact future-window activity evidence |
| future_code_counts | Checkpoint/all window counts, zero versus unavailable identified |
| issues | Unresolved cut, baseline, activity identity and date issues |

Filter one code_dimension and one code_basis (plan/outcome) before summing
performance_code_counts. Never sum both bases/all dimensions, or combine the
inclusive all checkpoint with individual checkpoint rows. Standard headlines
should use checkpoint_summary. Use proj_id + task IDs/task_code for drillthrough.

submission_index is calendar-period distance from each project's latest cut;
global_submission_index aligns projects to the dataset's latest period.
history_rank limits history to five available periods while preserving gaps.
P6's saved submission copies are the source; later edits/deletions or a new
latest baseline change results after refresh. This is not an immutable archive.

## Validation

For a fresh database named coding_check_test_*, install in order:
coding_check/tests/fixtures.sql, design_overview/tests/fixtures.sql,
design_overview/tests/replacement_fixtures.sql, coding_check/001_create_views.sql,
coding_check/006_mark_stored_baselines.sql, design_overview/010_rebuild_views.sql,
design_overview/tests/replacement_assertions.sql. All files use psql -X with
ON_ERROR_STOP=1. The fixture guards reject production databases.

Production requires existing Coding Check helpers. Install 010 and run 011.
Tests cover separate baseline ranking/ties/year rollover, Activity ID matching,
started ordinary Design Start tasks, Planned Start fallback, exact boundaries,
unknown/removal handling, future placement, means/bands, summary/evidence identity
and actual external-reader/publication-worker access. No P6 source writes occur.
