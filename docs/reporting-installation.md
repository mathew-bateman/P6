# Reporting installation and recovery

The repository includes the PostgreSQL reporting application, Django migrations,
native calculation SQL and report templates. An application build does not
provision or restore the reporting databases, source P6 instances, certificates
or private configuration. Django state remains in SQLite; the authoritative
reporting rules, source snapshots and published results remain in PostgreSQL.

## Build and start the application

Install `requirements.txt`, or build the supplied Dockerfile. Apply all Django
migrations, including `reporting_mirror`, before serving requests. A local
application without a reporting connection can be used for development; it does
not contain production report data.

```bash
docker compose build
docker compose run --rm web python manage.py migrate --noinput
docker compose run --rm web python manage.py check
```

The development worker consumes `celery,p6_reporting`. Reporting refresh,
publication, engine operations and offsite settings checkpoints use
`p6_reporting`; backup and maintenance jobs use the ordinary queue. A production
installation can dedicate a worker to each queue, but must consume both queues
and run a single Beat scheduler for its application database. Share the same
application state, private reporting configuration and retirement marker across
web, workers and Beat.

## Connect an existing reporting installation

`P6_REPORTING_CONFIG_FILE` points to an operator-owned JSON file. Its shape is:

```json
{
  "postgres": {
    "host": "reporting-postgres",
    "port": 5432,
    "dbname": "p6_reporting",
    "user": "reporting_writer",
    "password": "REPLACE_PRIVATELY",
    "reader_user": "p6_reporting_reader",
    "reader_password": "REPLACE_PRIVATELY"
  },
  "staging": {
    "host": "p6-reporting-sql-stage",
    "port": 1433,
    "username": "REPLACE_PRIVATELY",
    "password": "REPLACE_PRIVATELY",
    "hostname": "REPLACE_WITH_STAGE_SQL_MACHINE_NAME"
  }
}
```

The writer must own the reporting/control schemas and be able to create private
calculation generations and grant the reader access to published objects. The
reader has access to published reporting data. Preserve the database roles and
grants from a verified reporting recovery set when recovering an installation.
The JSON is private configuration: restrict its filesystem permissions and keep
it outside the checkout and build context. External client TLS and trusted CA
configuration are managed separately from this internal application connection.

For Docker, `compose.reporting.yml` adds a read-only `/stage` mount and an
existing reporting network to the application services. The private host
directory contains `reporting-secrets.json` and, for a retired installation,
`state/sql-reporting-retired.json` restored from its verified recovery set. Set
the following in `.env`, using actual host paths and the network shared by the
reporting services:

```dotenv
P6_REPORTING_RUNTIME_DIR=/absolute/private/p6-reporting
P6_REPORTING_NETWORK=p6-reporting
P6_REPORTING_CONFIG_FILE=/stage/reporting-secrets.json
P6_SCHEDULE_QUALITY_REFRESH_ENABLED=false
```

Then use both Compose files for every application command:

```bash
docker compose -f docker-compose.yml -f compose.reporting.yml config --quiet
docker compose -f docker-compose.yml -f compose.reporting.yml up -d --build
docker compose -f docker-compose.yml -f compose.reporting.yml exec -T web python manage.py migrate --noinput
docker compose -f docker-compose.yml -f compose.reporting.yml exec -T web python manage.py check
```

The override deliberately requires an existing private directory and existing
network. It does not create credentials, retirement state or data stores.

## Snapshot-stage contract

Retaining the SQL snapshot stage is necessary after SQL reporting retirement.
It restores consistent P6 backups and transfers raw data; PostgreSQL calculates
the reports. The application refuses to restore to a host other than
`p6-reporting-sql-stage` and verifies its SQL Server `MachineName` against the
private `staging.hostname` value.

Configure the stage with writable SQL data storage at `/var/opt/mssql/data` and
access to the same backup directories as the source P6 containers and application:

| Application path | Stage path | Source SQL path |
| --- | --- | --- |
| Target `app_backup_dir` ending in `axialp6` | `/source-backups/axialp6` | Target `sql_backup_dir` |
| Target `app_backup_dir` ending in `p62212` | `/source-backups/p62212` | Target `sql_backup_dir` |

These paths must refer to the same underlying files. The application requires
at least 80 GiB free at the backup mount before making a reporting snapshot.
It creates a per-run `COPY_ONLY` backup and restores only the dedicated
`reporting_stage_p6_22` and `reporting_stage_p6_23` databases. Source mappings in
`ReportingSource` must identify the intended P6 22 and P6 23 `BackupTarget`
records; verify database names and credentials before a first refresh.

## Recover a retired installation

Restore the verified SQLite application state, PostgreSQL database and global
roles, private connection configuration, encryption key ring and SQL retirement
marker together. The marker path is fixed at
`/stage/state/sql-reporting-retired.json`. Keep it available to every application
process. Missing retirement state must not be treated as a normal way to
re-enable retired SQL calculations.

On the restored application, confirm that `ReportingConfig` uses the PostgreSQL
engine, serves reports and manages settings in PostgreSQL; SQL-parallel work
stays disabled. Confirm the source mappings, then use Reporting Database to
review publication/freshness and manage the PostgreSQL refresh schedule. Review
an authenticated quality report, a portfolio report and an external reporting
query before accepting recovery.

`sync_p6_reporting --bootstrap` is a legacy bootstrap command that also invokes a
sync. It is not a replacement for restoring the versioned PostgreSQL control
tables or a retired installation's application state. Do not reinstall SQL
reporting objects to initialize a retired reporting database. Follow the full
[recovery and reporting retirement runbook](recovery-and-reporting-retirement.md)
for restore verification and any intentional reinstatement of SQL reporting.
