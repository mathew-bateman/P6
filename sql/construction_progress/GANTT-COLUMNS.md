# Column dictionary — construction_gantt_detail v1

All 62 columns below are in view ordinal order. `timestamp` means without time
zone; source programme timestamps are retained. JSON/array columns may be
omitted from the visual import after retaining them in a diagnostics query.

| Column | PostgreSQL type | Meaning |
|---|---|---|
| proj_id | integer | Selected native reporting-cut identity; fact relationship to SelectedCut |
| proj_short_name | text | P6 programme short name for that cut |
| project_code_id | integer | Existing logical Project ID code value; DimProject identity |
| project_code | text | Existing logical project code label |
| Project ID | text | Existing logical project display description; quoted SQL name |
| period_code_id | integer | Source Global Period code value identity |
| full_period_code | text | Full financial-year/period label, e.g. 2026/27.P7 |
| period_serial | integer | Existing comparable year/period ordering integer |
| data_date | timestamp | Reporting cut's source data date |
| cut_date | date | Existing calendar cut date |
| _snapshot_at | timestamptz | Published source snapshot timestamp |
| submission_index | integer | Existing relative period index per logical project |
| global_submission_index | integer | Existing relative period index across projects |
| submission_label | text | Existing Current/Previous/period label |
| baseline_proj_id | integer | Baseline programme selected by the established helper |
| baseline_status | text | Source baseline-selection quality/status |
| baseline_match_status | text | Verbatim source activity-match status; see GANTT.md |
| baseline_task_id | integer | Matched native baseline activity, NULL if unavailable/ambiguous |
| task_id | integer | Native current-cut activity identity |
| task_code | text | Exact P6 Activity ID used in established matching |
| task_name | text | P6 activity description |
| task_type | text | Native P6 activity type |
| status_code | text | Native P6 status |
| task_code_count | bigint | Number of current-cut source activities sharing exact Activity ID |
| activity_row_key | text | Source proj_id:task_id; existing classification bridge link |
| wbs_id | integer | Source current-cut WBS identity |
| wbs_short_name | text | Source WBS short name |
| wbs_name | text | Source WBS description |
| codes | jsonb | Complete shared validated activity-code buckets/assignments |
| discipline_status | text | Source coded/uncoded/conflict status, retained for every placement |
| discipline_assignments | jsonb | All valid distinct Global Discipline assignments, with IDs |
| baseline_start | timestamp | Source matched baseline scheduled start, both representations |
| baseline_finish | timestamp | Source matched baseline scheduled finish, both representations |
| current_start | timestamp | Source selected-cut scheduled start, both representations |
| current_finish | timestamp | Source selected-cut scheduled finish, both representations |
| baseline_date | timestamp | Milestone source event date; NULL for activity representation |
| current_date | timestamp | Milestone source event date; NULL for activity representation; qualify as g.current_date in SQL |
| classification_source | text | Milestone p6_type/code/p6_type_and_code; NULL for activity representation |
| milestone_type_count | integer | Shared number of distinct valid Global Milestone Type assignments |
| milestone_assignments | jsonb | All milestone classifications; no row expansion by this array |
| record_type | text | activity duration bar or milestone marker |
| record_type_order | integer | 1 activity, 2 milestone; separates dual representations |
| discipline_value_id | integer | This placement's actual Global Discipline value ID; NULL if unassigned |
| discipline_short_name | text | This assignment's short name; never a unique identifier |
| discipline_description | text | This assignment's shared display value; NULL if unassigned |
| discipline_assignment_key | text | Value ID as text, or literal unassigned |
| discipline_label | text | Placement description or explicit Unassigned Discipline |
| discipline_assignment_count | integer | Number of shared assignments; repeated on each placement/version |
| wbs_sort_path | integer[] | Alternating source WBS seq_num and WBS ID from root to current WBS; missing seq sorts last |
| wbs_order_status | text | ready or missing_or_cyclic_wbs; no manufactured hierarchy |
| programme_version | text | Baseline or Current Period |
| programme_version_order | integer | 1 Baseline, 2 Current Period |
| gantt_start | timestamp | This representation/version's exact source start/event date |
| gantt_end | timestamp | This version's activity finish; always NULL for markers |
| gantt_row_key | text | Unique cut/activity/representation/version/discipline identity |
| programme_sort_order | bigint | Adjacent-pair sort within cut and discipline; not an identity |
| date_status | text | ready/missing_start/missing_finish/missing_start_and_finish/finish_before_start |
| is_renderable | boolean | True only when dates required for this representation are usable |
| duration_calendar_days | numeric | Activity end minus start, fractional elapsed calendar days; NULL for markers/missing date |
| is_milestone | boolean | Explicit marker discriminator independent of milestone classifications |
| gantt_task_label | text | Activity ID/name/representation/programme display label; not a unique key |
| project_discipline_key | text | Logical project ID plus placement key; use in combined Parent identity |

Extra fields prepare plotting, stable ordering, placement identities and quality
diagnostics. No completion percentage or construction KPI is redefined here.
