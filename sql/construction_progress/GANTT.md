# Integrated Construction Programme Gantt — version 1

Installed and validated on 16 November 2026 in database `p6_reporting`:
`p6_reporting.construction_gantt_detail`. This is a schema-qualified view name;
the four authoritative sources are actually in schema `reporting`, not
`p6_reporting`. The new schema is an additive output namespace. No aliases or
replacements for the four existing source views were introduced.

Run `003_construction_gantt_detail.sql` once using `psql -X -v ON_ERROR_STOP=1`.
Run `004_validate_gantt.sql` for reconciliation. Use
`903_construction_gantt_rollback.sql` to remove only this view (no CASCADE).
Reader/sync grants are included; the external reader inherits reader access.
The rollback retains the schema, which may have other owners/users. Its usage
grants confer no access to other objects by themselves.

## Population, dates and disciplines

The activity branch reads `reporting.construction_activity_detail` without
adding eligibility predicates. This preserves Global stage 379/value 4328,
excludes P6 start/finish milestone and LOE duration bars, and retains ordinary
milestone-coded construction activities. `construction_activity_movement` is
reconciled in validation; its KPI calculations are untouched.

The marker branch reads the complete `construction_milestone_detail`, regardless
of stage. A coded ordinary construction activity intentionally has two
representations: `activity` and `milestone`. These have different keys and each
has its own Baseline/Current Period pair. A source activity is counted distinctly
by `(proj_id,task_id)`, not by the number of rows in this output.

The shared extraction `design_overview.activity_codes` joins P6 22 `taskactv`
to the validated `coding_check.code_values` dictionary by source, type ID and
value ID, removing deleted links and deduplicating identical assignments. Its
Global Discipline dictionary is type 383. The milestone source already carries
that result in `codes.discipline`; the new view exposes it directly. Thus no
change to milestone detail or its dependent reports is necessary. No WBS/name
inference is used. `value_id` is preserved even for repeated short names such
as `C` (12022, 12023, 12024).

Each distinct discipline assignment receives an explicit placement in its
actual group. Two assignments mean two pairs per representation; `discipline_status`
remains `conflict` and all assignments remain in JSON. This is intentional
placement expansion, not an additional source activity. Count distinct activity
keys across groups. No assignment yields one `unassigned` placement with
`discipline_label = Unassigned Discipline`, NULL value ID and source status
`uncoded`. No single discipline is silently preferred.

Both programme rows are retained even when dates are missing. Filter
`is_renderable = true` for drawing, and use unfiltered rows in diagnostics.
Activities use source baseline start/finish and current scheduled start/finish.
Milestones use the source baseline/current event date, and always NULL end.
Start milestones use start; other markers use finish, exactly as the source.
No forecast or current date is substituted for missing baseline dates. Source
scheduled-date fallback semantics remain unchanged. The baseline is the existing
selected separate baseline programme, not a newly chosen baseline per cut.

`baseline_match_status` is preserved verbatim. It is primarily finish-oriented
in the source; use `date_status` to assess the actual plotted date pair, including
start milestones. Missing/ambiguous matches stay unresolved. Invalid reversed
duration ranges are retained with `finish_before_start`, not drawn.

## Ordering and relationships

`gantt_row_key` combines `proj_id:task_id:record_type:programme_version_order:discipline_assignment_key`.
`programme_sort_order` is a row number within cut/discipline, ordered by the P6
WBS hierarchy (`seq_num`, then native WBS ID at each level), exact Activity ID
under PostgreSQL C collation, native task ID, representation, then programme
version (1 Baseline, 2 Current Period). Both versions of each representation
are adjacent, including diagnostic rows. The integer is stable for unchanged
source content but can shift as activities are added/recoded: it is not an ID
and is not comparable between cuts. Missing/orphan WBS roots are traversed;
unreachable cyclic WBS falls last with an explicit quality status.

P6 `reporting.taskpred` exists, with relationship type, lag, predecessor task
and predecessor project IDs. The four reference cuts contain 3,876 links, no
cross-project links and no deleted-date links at investigation. Relationships
cover the wider programme, not just displayed activities. A topological order
cannot preserve all meaningful sequence in cyclic, parallel or lagged networks;
no dependency order was fabricated. WBS/Activity ID is a defensible presentation
order, not critical-path or causal sequencing. A future network-based sequence
would require cycle checks and explicit policy for hidden predecessors.

Import the new view, all-cut `reporting.design_overview_submissions` as
`SelectedCut`, the existing bridge, project/period dimensions and a discipline
dimension keyed by value ID (plus an explicit `unassigned` member). Keep the
existing eligible cut population; do not substitute the latest-only XERToolkit
project catalogue, which cannot supply history.

Recommended single-direction paths: `DimProject[project_code_id]` (1) →
`SelectedCut[project_code_id]` (*) → Gantt on `proj_id` (1:*). Do not also add a
second active project path to Gantt. Relate a discipline dimension by the text
`discipline_assignment_key` (value ID text or `unassigned`). Keep Period
disconnected for the explicit selection measure below, or adapt the already
validated selected-cut measure without introducing a second active path.
Do not let Period alone filter the fact to every cut in a period.

Keep `DimMilestoneType[milestone_value_id]` → existing bridge (1:*), with no
active bridge→Gantt relationship and no bidirectional relationship to activities.
This keeps the existing bridge and applies its membership only to markers.
The slicer uses value IDs; display descriptions with the IDs when ambiguous.
Two selected values mean OR membership through an existence test, never two
fact records. A dual-representation activity keeps both duration rows regardless
of milestone filtering; only its marker rows are included/excluded by membership.
P6-type-only markers are visible without a classification filter, hidden when
a selected classification does not match them.

## Selected cut and membership DAX

The existing PBIX/model was not supplied or changed. Adapt these table names to
the model; retain the model's governed latest-cut ordering if it is already
established. The example explicitly defines latest as greatest nonblank data
date among eligible cuts within selected periods, independently per project.
Equal latest dates or entirely undated candidates fail closed and need a
selection-quality diagnostic, rather than silently choosing a native ID.
It supports multiple projects and multiple periods (latest among their union).
Test the existing model's definition of latest against this policy before replacing
its measure. Never average/min/max dates across different cuts.

```DAX
Gantt Row Visible =
VAR ProjectId = SELECTEDVALUE('Gantt'[project_code_id])
VAR CutId = SELECTEDVALUE('Gantt'[proj_id])
VAR ActivityKey = SELECTEDVALUE('Gantt'[activity_row_key])
VAR Kind = SELECTEDVALUE('Gantt'[record_type])
VAR PeriodFiltered = ISFILTERED('DimPeriod')
VAR Periods = VALUES('DimPeriod'[full_period_code])
VAR Candidates = FILTER(
    ALL('SelectedCut'),
    'SelectedCut'[project_code_id] = ProjectId &&
    NOT ISBLANK('SelectedCut'[data_date]) &&
    (NOT PeriodFiltered || 'SelectedCut'[full_period_code] IN Periods))
VAR LatestDate = MAXX(Candidates, 'SelectedCut'[data_date])
VAR LatestCuts = FILTER(Candidates, 'SelectedCut'[data_date] = LatestDate)
VAR ChosenCut = IF(COUNTROWS(LatestCuts) = 1,
    MAXX(LatestCuts, 'SelectedCut'[proj_id]))
VAR TypeFiltered = ISFILTERED('DimMilestoneType')
VAR SelectedTypes = VALUES('DimMilestoneType'[milestone_value_id])
VAR MatchingActivities = CALCULATETABLE(
    VALUES('MilestoneBridge'[activity_row_key]),
    REMOVEFILTERS('MilestoneBridge'),
    TREATAS(SelectedTypes, 'MilestoneBridge'[milestone_value_id]))
VAR TypeMatches = Kind = "activity" || NOT TypeFiltered ||
    CONTAINS(MatchingActivities, 'MilestoneBridge'[activity_row_key], ActivityKey)
RETURN INT(
    NOT ISBLANK(ProjectId) && NOT ISBLANK(ChosenCut) && CutId = ChosenCut &&
    SELECTEDVALUE('Gantt'[is_renderable], FALSE()) && TypeMatches)
```

Set the visual filter `Gantt Row Visible = 1`, and ensure each visual task
category includes unique row identity. Do not apply the renderability condition
to a diagnostics table. A separate diagnostic can use the same cut-selection
logic without date/membership predicates. Refresh Gantt, submissions and bridge
together from a consistent publication; asynchronous independent imports can
temporarily disagree. No calculated table frozen at refresh can implement a
dynamic Period slicer by itself.

## Microsoft Gantt field mapping and verified limitations

An example Power Query import adds visual-only category fields without changing
the fact grain. Use the reporting connection's existing TLS/credential settings:

```powerquery
let
    Source = PostgreSQL.Database("p6operations.axialprojects.com:5432", "p6_reporting"),
    Gantt = Source{[Schema="p6_reporting", Item="construction_gantt_detail"]}[Data],
    WithParent = Table.AddColumn(Gantt, "GanttParent", each
        Text.From([#"Project ID"]) & " / " & [discipline_label] &
        " {" & [project_discipline_key] & "}", type text),
    WithTask = Table.AddColumn(WithParent, "GanttTask", each
        Text.PadStart(Text.From([programme_sort_order]), 8, "0") & " " &
        [gantt_task_label] & " {" & [gantt_row_key] & "}", type text),
    WithMarker = Table.AddColumn(WithTask, "GanttMilestone", each
        if [is_milestone] then "Milestone" else null, type nullable text)
in
    WithMarker
```

Map GanttParent, GanttTask and GanttMilestone to Parent, Task and Milestones.
Hide the technical key columns in the report field list after relationships and
diagnostics are configured. Keep `gantt_start`/`gantt_end` as Date/Time and
`programme_sort_order` as Whole Number. Neither hierarchy names nor JSON fields
should be used as relationship identities.

The investigation checked Microsoft's public repository on 16 November 2026:
[field capabilities](https://github.com/microsoft/powerbi-visuals-gantt/blob/main/capabilities.json)
and [task construction and hierarchy sorting](https://github.com/microsoft/powerbi-visuals-gantt/blob/main/src/gantt.ts).
The current source supports a Milestones category and rhombus shape settings.
It has one Parent and one Task role, not a native arbitrary three-level
Project → Discipline → Task hierarchy. Its explicit hierarchy sort compares
task names; default child sorting can use dates. SQL ORDER BY or Power BI Sort
by Column alone is therefore not proof of the required visual order. Main-branch
behaviour also does not establish the version installed in your PBIX.

For this visual, use a combined Project/Discipline parent label including their
IDs, or one project per visual with discipline as Parent. A full independently
expandable three-level hierarchy needs a visual supporting that hierarchy.
Supply a unique Task label constructed in Power Query from zero-padded
`programme_sort_order`, `gantt_task_label` and `gantt_row_key`; the key suffix
prevents merges of repeated names/IDs across cuts and projects. Select explicit
ascending Task sorting and turn off task grouping that merges programme rows.
The numeric prefix makes name-based ordering reflect SQL ordering. A name-based
sort without that prefix does not implement WBS sequence. The visible prefix/key
is a tradeoff in this visual, not a change to the clean source label.

| Role / setting | Field or action |
|---|---|
| Parent | Combined Project + discipline label + `project_discipline_key` |
| Task | Unique, prefixed label described above; each representation/version separate |
| Start Date | `gantt_start`, exact date/time (not automatic Date Hierarchy) |
| End Date | `gantt_end`, using an aggregation only at the unique row grain |
| Legend | `programme_version`; use consistent baseline/current colours |
| Milestones | Power Query column: if `is_milestone` then `"Milestone"` else null |
| Milestone shape | Rhombus/diamond for `Milestone`; no multi-classification values here |
| Tooltips | Activity ID, record type, match/date/discipline status, cut and baseline ID |
| Duration | Leave unmapped when mapping End Date; duration is calendar elapsed, not P6 working days |

The SQL milestone End remains NULL. Check the installed visual's treatment of
NULL ends: source task construction can synthesise an end from its minimum
duration. A marker must not acquire a visible ordinary duration bar. Configure
minimum duration/marker styling and test; do not mutate SQL dates to mask a
visual limitation. The source also defaults invalid/missing starts to today,
so `is_renderable` filtering is essential.

Acceptance in Desktop and Service must check: Barton Hill P7 resolves cut 1797;
no Period selects latest per project; P6 selects each project's latest P6 cut;
two projects resolve separately; each visible pair stays adjacent; bars and
diamonds are interleaved under discipline; type-only markers behave correctly;
two selected type IDs use OR and bars persist; missing-date rows do not draw;
collapse/expand and publishing do not reorder or merge rows. These UI checks
remain unverified because no Power BI model/session was provided. The SQL view
is deployed and validated; an installed Microsoft visual's exact layout is not
claimed as verified. Use a hierarchy-capable visual if those acceptance checks
cannot be met without compromising the intended layout.
