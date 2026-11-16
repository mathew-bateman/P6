# Native bulk transfer experiment

The experiment preserves full-value verification. It uses Microsoft's C++ Arrow
SQL Server reader, Arrow ADBC PostgreSQL ingestion and independent source and
destination rereads in primary-key order. Native Arrow comparison includes every
value and null, and rejects different row counts and any precision loss.

It writes only a new private generation schema on a PostgreSQL instance whose
`cluster_name` begins `p6-perf`. It never publishes application views, modifies
SQL Server, adjusts production settings or executes a live reporting refresh.

Run with the pinned dependencies in `requirements.txt` in a benchmark-only
container. The SQL source must be an immutable isolated restore. Target creation
is committed so the separate ADBC writer connection can see the private tables.
The orchestration layer must publish only after all tables and reporting results
have independently passed comparison.

```text
python arrow_transfer.py /private/config.json --output /results/transfer.json
python -m unittest discover -s . -p 'test_*.py'
```

Private configuration format (do not commit populated credentials):

```json
{
  "benchmark_only": true,
  "source_connection_string": "Server=p6-perf-sql;Database=...;UID=...;PWD=...;Encrypt=yes;TrustServerCertificate=yes;",
  "postgres": {"host":"p6-perf-pg","port":5432,"dbname":"p6_perf_trial","user":"...","password":"..."},
  "schema": "native_shadow_0123456789abcdef_p6_23",
  "source_code": "p6_23",
  "tables": ["TASK", "TASKACTV", "TASKPRED"],
  "workers": 1,
  "batch_size": 50000,
  "build_legacy_indexes": true
}
```

The result's `entry` object preserves source/name/columns/rows for the existing
generation manifest. `verification.method=ordered_arrow_exact_v1` explicitly
identifies full typed equality. It does **not** invent a `sha256_sum` matching the
old Python JSON hash. A prototype should additionally be checked once with the
old verifier to establish compatibility independently.

For fair comparison include metadata, extraction, native COPY, index building,
full-value verification and total wall-clock time. Raw indexes follow the
existing loader's non-unique field/expression policy exactly. No extra UNIQUE
primary-key indexes are added: they can alter downstream report plans even when
the values are identical. Ordered verification uses existing indexes or sorts;
source primary keys and full ordered equality preserve duplicate assurance.
Failed private schemas remain for diagnostics; this module never drops a schema
automatically.

