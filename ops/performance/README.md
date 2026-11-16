# Isolated reporting performance harness

These tools compare reporting implementations on disposable copies. They do not
replace the application's production refresh or establish its current duration.
The harness keeps extraction, raw-data transfer, calculation, verification and
publication measurements separate so comparisons cover the same work.

## Isolation and configuration

Use an isolated SQL restore and a disposable PostgreSQL database on a private
network. Supply connection configuration through a restricted file outside the
checkout. Never commit populated configuration, credentials, source backups,
query plans, report exports or captured results. Run the native bulk experiment
only under the additional restrictions in [native_bulk/README.md](native_bulk/README.md).

Before running a trial, inspect its entry point and supported arguments, verify
the target identities and confirm that the source restore is immutable. Keep
application refresh, publication and engine-switch tasks out of the benchmark
process. Trial cleanup must address only the explicitly named disposable
resources; retain failed stores privately when needed for diagnosis.

## Entry points

| Files | Purpose |
| --- | --- |
| `scope_trial.py`, `calculate_trial.py` | Prepare and calculate a defined trial scope |
| `all_projects_trial.py`, `all_projects_parallel_trial.py` | Compare full-project calculation strategies |
| `pipelined_all_projects_trial.py` | Exercise pipelined transfer and calculation |
| `end_to_end_trial.py` | Coordinate isolated capture, transfer, calculation and verification |
| `all_project_calc.py`, `calculate_all_trial.py`, `calculate_one_all.py` | Calculation helpers for full-project trials |
| `sql_oracle.py`, `compare_all_projects.py`, `compare_reports.py` | Compare the candidate against an independently retained reference |
| `summarize_*.py`, `verify_all_trial.py` | Summarize and validate captured trial results |
| `native_bulk/` | Typed Arrow transfer and independent full-value verification experiment |

`batch_evidence_candidate.py` preserves an unshipped evidence-rendering
optimization for isolated comparison. The application's active renderer remains
`reporting_mirror/native_evidence.py`; preserving the candidate does not activate
it. `test_batch_evidence.py` tests the candidate by default and accepts an
explicit alternative through `P6_BATCH_EVIDENCE_RENDERER_PATH`. The optional
database fixture compares against the reference supplied through
`P6_LEGACY_EVIDENCE_PATH` and requires an isolated
`P6_BATCH_EVIDENCE_TEST_DSN`.

Use matching input snapshots, settings, project scope and report filters for the
reference and candidate. Include all raw rows and nulls in transfer verification,
and compare report membership, values, metadata and externally visible contracts.
Do not treat equal row counts or a successful calculation alone as parity.

## Offline checks

From the repository root:

```bash
python3 -m unittest discover -s ops/performance -p 'test_*.py'
python3 -m unittest discover -s ops/performance/native_bulk -p 'test_*.py'
```

Install optional benchmark dependencies only in a separate benchmark environment
using `native_bulk/requirements.txt`. Review each integration entry point before
running it; offline tests are not permission to connect to operational systems.

## Private evidence

Store run configuration, raw output, query plans, parity differences, timing
summaries and deployment receipts in access-controlled operational storage.
`evidence/` and the measured-result notes are excluded from public Git and Docker
build contexts. Preserve them with a manifest and checksums in the private
recovery archive. Public source documentation describes the harness; current
refresh duration must be read from the active application's run history or a
separately authorized live measurement.
