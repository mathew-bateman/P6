# P6 MSSQL Backup Admin Design

## Goal

Build a self-contained lightweight Django project that manages backup and restore for P6 SQL Server databases without depending on the existing `AxialP6API` project.

## Managed Targets

Each database is a separate backup target with its own schedule, SharePoint destination, retention policy, and run history.

- `AxialP6`: SQL Server container/host for the `Axial` database.
- `Axial Training`: SQL Server container/host for the `Axial_Training` database.
- `P62212`: SQL Server container/host for the P62212 `Axial` database.

## Architecture

The new project lives at `C:\Dev\P6\P6BackupAdmin`. It stores its own metadata in SQLite, uses Django templates for staff-only operations, uses Celery and django-celery-beat for scheduled jobs, and uses Microsoft Graph client credentials for SharePoint uploads/downloads and optional Graph email.

SQL Server backups are engine-native `.bak` files. Django connects to SQL Server through ODBC and runs `BACKUP DATABASE` into the SQL Server container's mounted `/var/backups` path. The Django app reads the same file through its own mounted backup path, encrypts it, writes a manifest, uploads both files to SharePoint, and removes local staging files unless configured to keep them.

## Data Flow

1. A scheduled or manual job loads a `BackupTarget`.
2. The app connects to SQL Server with pyodbc.
3. SQL Server writes `{target_slug}_backup_YYYYMMDD_HHMMSS.bak` to the target `sql_backup_dir`.
4. The app verifies the `.bak` with `RESTORE VERIFYONLY`.
5. The app encrypts the `.bak` with chunked Fernet, calculates hashes, and writes a JSON manifest.
6. The app uploads `.bak.enc` and `.manifest.json` to the target SharePoint folder.
7. Retention pruning keeps configured daily, weekly, and monthly copies per target.
8. The app records a `BackupRun` and sends a status email.

Restore reverses the flow: download encrypted backup and manifest, verify encrypted hash, decrypt, verify plaintext hash, stage the `.bak` in the target backup folder, run `RESTORE VERIFYONLY`, inspect `RESTORE FILELISTONLY`, then restore with explicit `WITH MOVE` paths and single-user/multi-user protection.

## Security

The project does not store SQL passwords in the database. Each target references an environment variable containing its SQL password. Backup encryption uses `BACKUP_ENCRYPTION_KEYS`, a JSON list of Fernet keys, so new backups use the first key and restores can read older backups with rotated keys.

Restore actions require staff access and an exact typed database-name confirmation.

## Testing

Unit tests cover SQL generation, chunked encryption/decryption, SharePoint path parsing, retention selection, backup row filtering, and schedule parsing. Live SQL Server and Microsoft Graph calls are isolated behind service classes so they can be mocked.
