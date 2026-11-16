# Touchpoints PostgreSQL views

Database: `p6_reporting` on XPS-Server-4. Source: P6_22. Additive views only; no P6 data writes and no changes to the existing Design Overview objects.

## Public views

- `reporting.touchpoint_activity_detail`: one native activity per submitted cut. Includes valid Global Milestone Type GIVE/GET membership and flagged code conflicts. Filter `is_touchpoint = true` for counted membership.
- `reporting.touchpoint_interface_routes`: same activity grain, including missing and conflicting routes.
- `reporting.touchpoint_finish_movement`: current/previous membership, exact unique Activity ID matching, selected baseline comparison, ownership, commentary and change flags. `activity_row_key` is unique within `proj_id`; previous-only rows remain for review.
- `reporting.touchpoint_finish_history`: selected-cut filter context with up to five available reporting periods at or before that cut. One anchor activity/available historical period; `historical_*` columns identify the comparator, `recent_cut_rank` orders it, and `history_period_offset` preserves calendar gaps. Uncoded historical tasks can match.
- `reporting.touchpoint_movement_summary`: additive counts from movement detail, grouped by cut, current owner/code/route and movement category. Conflicting current membership, previous-only and reclassified-out activities do not enter headline counts.

Every view includes `proj_id`, `project_code_id`, `Project ID`, `Account`, `Lead Planner`, `Responsible Company`, `Period`, `Period Code`, `Project State`, `Project Status`, full financial period, cut/data dates, submission indexes and baseline/comparison status. `responsible_company` remains available alongside its Power BI display alias. Discipline, Project Stage and Milestone Type are available too. No source-system metadata or encoded UI IDs is needed by the public consumer.

## Reporting contract

Reuse the LIVE `reporting.design_overview_submissions` and `design_overview.activity_facts` helpers; never run the old Design Overview installer in production to install these views. Activity eligibility follows their active/DEL rules. Touchpoint membership is valid Global Milestone Type short name GET/GIVE, not Design Key membership. Multiple Milestone Type values are review exceptions, never arbitrarily selected.

The previous cut is the existing exact preceding financial period, subject to uniqueness and increasing data dates. Baseline selection is the existing latest separate BL/Baseline programme for the logical Project ID; no stored-baseline or name fallback. Match exact unique `task_code` Activity IDs within that logical project. Comparator tasks do not need GIVE/GET coding, allowing legacy baseline classifications. Missing/duplicate IDs, dates or comparators keep NULL movement and explicit status.

`current_finish` is canonical scheduled_finish_date: actual finish when complete, otherwise Remaining Early Finish with Planned Finish fallback. Raw planned/actual values and finish_date_source remain inspectable. Movement is current finish calendar date minus comparator finish calendar date, including for start milestones. Positive means later, negative earlier. Raw hours are `total_float_hr_cnt`; `total_float_days_8h` is hours/8, not an activity-calendar conversion.

Remaining means known status other than TK_Complete. Null status stays unknown. Period/baseline movements remain inspectable on complete activities, but categories label them Completed and headline charts exclude them. The donut uses `is_current_touchpoint AND is_remaining`; categories Improved, Delayed, No movement, No comparison partition those records. Delay bars additionally require `movement_status = 'Delayed'`; sort delay_band by delay_band_order. The requested label 0-20 contains positive 1-20 days; zero is No movement, missing is No comparison.

Interface Detail is the original project-scoped route description. Only one assignment with exactly one > and two nonblank endpoints supplies From Company / To Company; missing, conflicting or unstructured values have NULL endpoints and route_status. These are source-text company labels, not canonical party IDs, verified GIVE/GET pairs or endpoint planner mappings. Lead Planner remains programme ownership. Raw dictionary IDs/assignments and name/code conflict flags are retained. Never match interfaces by activity names, short route code or a dependency alone.

Comments and Notes TASK UDF values and notebooks are aggregated before joining, so they cannot multiply activities. Original UDF and notebook JSON remains available. Notebook display text uses basic HTML/entity cleanup; original markup is authoritative.

## Power BI

Use existing `reporting.design_overview_submissions` as the shared cut dimension, joined one-to-many by native `proj_id`. Project ID/logical grouping uses `project_code_id`. All history slicer columns describe the selected cut; use historical_full_period_code/historical_finish for history display. The reporting_ui encoded IDs are incompatible with native proj_id/task_id.

For consistent owner/route slicing across facts, use conformed dimensions with single-direction relationships, or the matching view-level filter columns. An activity-detail company slicer alone does not automatically filter another fact table through a one-direction cut relationship. From/To endpoint planner slicers require an explicitly reviewed party-to-planner mapping; none is invented here.

Donut: movement_summary.movement_status and SUM(remaining_count), filtered is_remaining=true. Delay bars: delay_band, SUM(delayed_count), filtered movement_status=Delayed. Finish table: finish_movement with the same current/remaining filters, plus current_finish, previous_finish, signed days, baseline_finish, float and commentary. Keep review flags available for exceptions. Combine mean movement using SUM(movement_days_sum)/SUM(comparable_count), not an unweighted average of summary averages.

## Install, verify, rollback

Run with psql -X -v ON_ERROR_STOP=1 against the exact database. Installer 001 is transactional, idempotent and uses bounded lock/statement timeouts. It grants the five public views to existing reader/sync roles using the same convention as Design Overview. No external reader access to the private touchpoints schema is needed.

1. Save current Design Overview definitions/ACLs and confirm the five new names are unused on a first install.
2. Run `001_create_views.sql`.
3. Run `002_verify_views.sql`; checks use temporary tables and read the public views as the external reader role.
4. For reversal, run `900_rollback.sql`. It removes only these new objects, without CASCADE. Any downstream SQL dependencies block rollback atomically for review.

Synthetic tests run only in a fresh `coding_check_test_*` database. Bootstrap in order: coding_check/tests/fixtures.sql; design_overview/tests/fixtures.sql; design_overview/tests/replacement_fixtures.sql; touchpoints/tests/fixtures.sql; coding_check/001_create_views.sql; coding_check/006_mark_stored_baselines.sql; design_overview/010_rebuild_views.sql; touchpoints/001_create_views.sql; touchpoints/tests/assertions.sql. Test the new rollback/reinstall too. Do not run fixtures in production.
