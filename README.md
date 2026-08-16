# P6BackupAdmin

Self-contained Django backup and restore manager for the local P6 SQL Server environments.

## Targets

Run `python manage.py bootstrap_p6_targets` to create:

- `AxialP6` for database `Axial` on host port `1466`
- `Axial Training` for database `Axial_Training` on host port `1466`
- `P62212` for database `Axial` on host port `1433`

Each target has its own schedule, SharePoint folder, retention policy, run history, and restore page.

## How Backups Work

The app connects to SQL Server with ODBC and runs SQL Server-native backup commands:

```sql
BACKUP DATABASE [DatabaseName]
TO DISK = N'/var/backups/target_backup_YYYYMMDD_HHMMSS.bak'
WITH INIT, CHECKSUM;
```

SQL Server writes the `.bak` file into its own `/var/backups` mount. The Django app reads the same file through its mounted app path, encrypts it with chunked Fernet, writes a manifest with hashes, uploads the encrypted file and manifest to SharePoint, and prunes older SharePoint files by the target retention policy.

Restore downloads the encrypted backup and manifest, verifies hashes, decrypts to a local `.bak`, runs `RESTORE VERIFYONLY`, reads `RESTORE FILELISTONLY`, and restores using explicit `WITH MOVE` paths.

## First Run

```powershell
cd C:\Dev\P6\P6BackupAdmin
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py bootstrap_p6_targets
.\.venv\Scripts\python manage.py sync_backup_schedules
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver 8026
```

Open `http://localhost:8026`.

## Schedule Quality Settings

The schedule-quality settings page reads and writes the versioned configuration held in SQL Server; SQLite is used only for Django users and roles. Grant an existing user editor access with:

```powershell
.\.venv\Scripts\python manage.py grant_schedule_quality_editor jason.mappin
```

Editors can save a shared draft, then publish and rebuild the materialised SQL results atomically. An unchecked setting is No; a setting shown as N/A remains SQL `NULL`.

## Docker Run

Windows:

```powershell
cd C:\Dev\P6\P6BackupAdmin
$env:P6_AXIALP6_BACKUPS_PATH="C:/Dev/P6/AxialP6/backups"
$env:P6_P62212_BACKUPS_PATH="C:/Dev/P6/P62212/backups"
docker compose up --build
```

Ubuntu server:

```bash
cd ~/P6BackupAdmin
printf '\nP6_AXIALP6_BACKUPS_PATH=/home/AxialP6/backups\nP6_P62212_BACKUPS_PATH=/home/mat/P62212/backups\n' >> .env
docker compose up -d --build
```

The app is exposed at `http://localhost:8026`.

On the Ubuntu server, the SQL containers are already on host ports `1466` and `1433`, but the backup admin container should use Docker networking where possible:

- AxialP6 / Axial Training: SQL host `AxialP6`, SQL port `1433`, SQL user `sa`, SQL password saved on the target
- P62212: SQL host `AxialP6_2212`, SQL port `1433`, SQL user `sa`, SQL password saved on the target

SQL compression must stay off for SQL Server Express Edition.

## Required Secrets

Set these in `.env` before running real jobs:

- `GRAPH_TENANT_ID`
- `GRAPH_CLIENT_ID`
- `GRAPH_CLIENT_SECRET`

`BACKUP_ENCRYPTION_KEYS` already contains a generated local Fernet key. Keep it safe; backups encrypted with that key need it for restore.
