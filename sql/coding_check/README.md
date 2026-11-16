# Coding Check PostgreSQL views

Implements the SQL-view scope of **Coding Check Dashboard Spec.pptx**, supplied
29 October 2026, with the user's answers recorded below. No Power BI interface
is included. The target is `p6_reporting` on XPS-Server-4, not a live P6 SQL Server
database. **All eight coding-check views return P6 22 only**, following the
user's scope adjustment on 29 October 2026. The existing `p6_reporting_reader`
and external reporting account retain access.

## Power BI views

| View in `reporting` | Grain |
| --- | --- |
| `coding_check_project_stage` | Programme and Global Project Stage value |
| `coding_check_discipline` | Programme and Global Discipline value |
| `coding_check_design_key` | Programme and Global Design Key value |
| `coding_check_milestone_type` | Programme and Global Milestone Type value |
| `coding_check_responsible_company` | Programme and Global Responsible Company value |
| `coding_check_path_counts` | Programme and float/path measure, as rows |
| `coding_check_path_summary` | One row per programme, measures as columns |
| `coding_check_activity_code_counts` | All five code dimensions in one view |

The public views omit `_source_system`, `_source_database`, `_project_key`,
`project_code_conflict_count`, `_synced_at` and `_batch_id`, following the user's
column adjustments on 29 October 2026. **`_snapshot_at` remains** and records when
the underlying P6 data was captured. `proj_id` identifies
the programme within these P6 22-only results. When joining combined reporting
tables, explicitly select their P6 22 rows because the underlying database still
contains both P6 sources and their IDs can collide. Internal helper views retain
the source fields needed for filtering and joins.
`proj_short_name` is the actual P6 programme identifier. **`"Project ID"` is the
Project ID project-code value**, as requested by the spec; it is a different
field. `"Account"`, `"Lead Planner"` and `"Period"` are project-code descriptions;
`"Period Code"` also retains the short value. Missing classifications stay NULL.
Conflicting filter classifications stay NULL. The `project_code_conflict_count`
diagnostic remains in the private helper views for verification. Ambiguous
Project State/Status is excluded from eligibility.

From 3 November 2026, all eight public views also append `is_stored_baseline`,
`original_proj_id` and `original_proj_short_name`. An eligible stored P6 baseline
remains included; `is_stored_baseline` is true when `PROJECT.orig_proj_id` is
populated. Filter it to false when only ordinary projects are wanted. The
original-project reference is resolved within P6 22, even when that parent is
not itself an eligible submission. A missing parent leaves its name NULL while
retaining the ID and baseline flag. This metadata is independent of the
user-managed **Project Status = Baseline** code. Existing counts and eligibility
are preserved.

For code views, use `code_value` for display, sum `activity_count`, and filter to
one `code_dimension`. Keep `actv_code_type_id`/`actv_code_id` when identifying
individual values. Different IDs can share a display label; Power BI can sum
them into one label without losing the underlying code identity. `code_raw_value`
retains the description before removing an ES stage prefix; `code_short_name`
retains its short code. A blank description falls back to a nonblank short code;
values where both are whitespace/NULL count as uncoded.

Every active Global dictionary value appears, including zero-count values, and
each programme has a `No codes / Blank` row (`is_uncoded=true`). A missing code
type returns an uncoded row and `code_type_exists=false`. New types, values and
assignments appear following the normal reporting refresh without SQL changes.
Deleted/missing dictionary links and assignments to other scopes do not count as
valid Global assignments. Duplicate assignment rows count once. Multiple valid
assignments for one activity/dimension are counted once in the visible
`Multiple code assignments` bucket (`bucket_status=conflict`), so totals still
reconcile. `project_activity_count` repeats the denominator; do not sum it across
values or dimensions, and do not sum the five code dimensions together.

## Confirmed rules (29 October 2026)

- Only P6 22 is included, using the internal `_source_system=p6_22` filter.
  Other reporting views and the
  underlying combined database retain their existing source coverage.
- Only active P6 project rows (`project_flag=Y`) with Project State **Live** and
  Project Status **Client Submitted**, read through active project-code links.
- Include all activity types and completion states. Exclude physically
  soft-deleted rows and activities assigned **Activity Status = DEL** in any
  scope. Classification and assignment deletions are also respected.
- Resolve P6 22 activity-code types by exact name, case-insensitive and
  space-trimmed, and **AS_Global** scope.
- Strip leading ES designations such as `ES3 -`, `ES4/ES5 -`, including en/em
  dashes, from Project Stage display labels. Retain raw values and IDs. A prefix
  with no following description retains its raw label to avoid an empty category.
- Float groups are exclusive and use **raw, unrounded float hours / 8**:
  Negative Float `<0`; Critical Path `=0`; Project Critical `>0 AND <160 hours`;
  Other Float `>=160 hours`; Missing Float `NULL`. Exactly 20 days is not Project
  Critical. These five counts sum to the eligible activity count.
- Longest Path uses P6 `TASK.driving_path_flag=Y`. It is independent of the float
  groups. Missing and invalid path flags are separate measures; a normal `N`
  flag is not missing. Do not add all path measures together.

Initial P6 22 source gaps: Design Key is absent and Period has no assignments.
The views preserve these gaps instead of manufacturing classifications. The
original combined-source deployment and its historical counts remain recorded
Deployment and validation receipts are private operational evidence, retained outside public source control with the verified recovery archive. Use the versioned SQL and contract documentation here to review the implementation.

## Install and verify

Use an authorized PostgreSQL owner session with `psql -X -v ON_ERROR_STOP=1`:

1. Run `001_create_views.sql` against `p6_reporting`.
2. Run `006_mark_stored_baselines.sql` to append stored-baseline metadata.
3. Run `002_verify.sql` against that database. It verifies through the actual
   external login role (`p6_reporting_external`, which inherits the reader role),
   independently reconciles the raw population/path counts, checks every
   programme/dimension and P6 23 exclusion, and reports missing
   classifications/code types.

For the existing installation with trimmed metadata, run
`006_mark_stored_baselines.sql`, then `002_verify.sql`. This additive migration
uses `CREATE OR REPLACE VIEW`, preserving existing column order, grants and
dependent objects. Rerun `006_mark_stored_baselines.sql` for idempotent updates;
the earlier `001_create_views.sql` predates the appended columns and should not
be rerun over the extended public views.

For an older P6 22-only installation after the source-column adjustment,
run `005_trim_metadata_columns.sql`, `006_mark_stored_baselines.sql`, then
`002_verify.sql`. Migration 005 removes
`project_code_conflict_count`, `_synced_at` and `_batch_id` while retaining
`_snapshot_at`. The migration recreates the eight public views in one transaction
and refuses unexpected dependencies. Grouped counts are preserved.
The private helper definitions retain the diagnostic and source fields needed
for verification and joins. For an older combined-source installation, first
run `003_p6_22_only.sql`, then `004_remove_source_columns.sql`,
`005_trim_metadata_columns.sql`, `006_mark_stored_baselines.sql` and `002_verify.sql`.
The canonical `001_create_views.sql` includes the P6 22 restriction and the
reduced public column contract used before migration 006 for clean installs.

Refresh Power BI metadata after the column change. Any existing import steps
that explicitly reference the removed columns need updating.

Installation is one transaction with a bounded lock wait; rerunning it is
idempotent. The private `coding_check` schema contains helper definitions.
Consumers only need the views in `reporting`; the existing reader receives
explicit SELECT grants. The sync role receives SELECT WITH GRANT OPTION on the
eight public views so its schema-wide publication grant succeeds even when the
views are owned by the installer. The views depend on stable `reporting` raw
views rather than a `load_*` generation. The existing atomic refresh repoints those stable
views; old-generation pruning does not remove the coding views. No hourly job
change, report engine switch, or P6 write is required.

Keep these files with the deployed application under `app/sql/coding_check`
and in its release backup. The older local checkout does not contain the live
`reporting_mirror` implementation; do not deploy the whole checkout to the server.

`900_rollback.sql` removes only these objects and refuses to cascade through
unexpected dependencies. It leaves raw P6 snapshots and reporting settings intact.

## Regression tests

Create an isolated database named `coding_check_test_*` in a PostgreSQL cluster
with `p6_reporting_sync`, `p6_reporting_reader` and `p6_reporting_external`
roles; the external role must inherit the reader role, matching production.
Run, in order, `tests/fixtures.sql`,
`001_create_views.sql`, `tests/assertions.sql`, and
`tests/stored_baselines.sql`. The last script applies migration 006 twice and
checks ordinary projects, eligible stored baselines, missing parent references,
P6 23 parent-ID collisions, unchanged rows/counts/column positions, unchanged
view OIDs/owners/ACLs, an existing dependent view and external-reader access.
The test scripts refuse to run
outside that database naming pattern. P6 23 collision records remain in the raw
fixtures, and all eight coding-check views must exclude them, including after
new P6 23 exact-name code definitions and assignments appear. Tests check that
all six removed metadata columns are absent from every public view and that
`_snapshot_at` remains. They also cover
duplicates, conflicting assignments, whitespace, deleted rows, missing future
types, 20-day boundaries, reader access and stable publication changes followed
by old-generation pruning. Never load fixtures into
`p6_reporting`. Drop only the isolated test database after validation.

Run `tests/publication_grants.sql` after the canonical install and after any
view-rebuilding migration. It checks the eight view grants and exercises the
publisher's schema-wide grant under `p6_reporting_sync`, rolling it back after
the test.

The `MATERIALIZED` candidate-assignment CTE deliberately bounds the plan before
joining scoped activities. On the initial source snapshot, an unfenced plan
performed millions of repeated assignment lookups. Preserve this fence unless a
new measured plan demonstrates an improvement.
