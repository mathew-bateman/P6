# Recovery capture and reporting retirement

The operating sequence is **capture, verify offsite, restore in isolation, retire, smoke-check**. A successful reporting refresh or an absence of reported issues does not establish recoverability. The PostgreSQL reporting copy and its settings checkpoint serve different purposes from a full P6 or application backup.

## One repeatable capture

Deploy the reviewed `ops/recovery/capture_recovery.py` and `ops/recovery/application_recovery.py` to the matching directory under the production application. These helpers are additive; no service restart or database migration is required.

Before capture, pause reporting publication/settings changes and imports, wait for active refresh and SQL-parallel work to finish, and record the previous schedule/configuration. The helper checks the reporting publication/settings signature before and after the database captures and checks core P6 row counts before and after each backup. Those checks detect drift but do not provide a global cross-database transaction.

From XPS-Server-4, as the existing Docker operator:

```bash
cd /home/mat/Docker/P6-Standby/app
bash ops/recovery/run.sh capture YYYYMMDDTHHMMSSZ
bash ops/recovery/run.sh verify YYYYMMDDTHHMMSSZ
bash ops/recovery/run.sh finalize YYYYMMDDTHHMMSSZ
```

Choose a new ID for every attempt. Reusing a local or remote ID fails; an interrupted run is retained with `FAILED.json` and a private `capture.log`. At least 25 GiB free space is required as an initial floor. Check actual free space against current database and image sizes before starting; the capture temporarily holds original artifacts, compressed payload, encrypted payload, download and decrypted verification files.

Capture output is root-private under `/home/mat/Docker/P6-Standby/app/.recovery/<id>/`. The helper includes:

- the deployed application source, including production-only modules and templates;
- Compose/private configuration, reporting secrets, host recovery scripts, activation state, proxy and Portainer bind data;
- Docker container and network configuration and all unique images used by the named P6 stack services;
- a consistent SQLite online backup with `PRAGMA integrity_check`;
- SQL Server `COPY_ONLY, CHECKSUM` full backups of every enabled target, verified using `RESTORE VERIFYONLY`, with core table counts, source machine details and the user-object catalogue;
- a custom-format `p6_reporting` PostgreSQL dump, global roles and schema SQL;
- a SHA-256 manifest for every payload file, plus a verified compressed archive.

The live application archive excludes SQLite runtime files, `.recovery`, `.git`, virtual environments, caches, staging backups, generated static files, and local `work`/`remote_edits` captures. SQLite is recovered from the dedicated online backup. Proxy/Portainer bind files are copied live; if their own transactionally consistent state is required, arrange a service-specific backup or quiesce those services before capture.

The payload is encrypted with the existing first `BACKUP_ENCRYPTION_KEYS` key and uploaded into the configured P6 22 SharePoint site at `AxialP6_Backups/Recovery/<id>`. Parent folders are created explicitly; the leaf uses fail-on-conflict semantics. The helper downloads the uploaded archive, verifies its encrypted hash, decrypts it, verifies the plaintext hash, and also downloads/verifies the encryption metadata. It does not send emails, change retention, or delete existing remote files.

`COMPLETE.json` means the capture and offsite byte/decryption checks succeeded. It explicitly records `isolated_restore_verified: false`. `offsite_receipt.json` identifies the SharePoint items and their verified hashes. Preserve a separate protected copy of the encryption key outside this host and outside the encrypted payload. A configuration copy inside the payload cannot unlock itself after the host is lost.

## Prove restoration before retirement

Use the captured artifacts in an isolated environment. Never point a rehearsal at the operational databases, application SQLite, or PostgreSQL reporting volume.

1. Download/decrypt the SharePoint archive using the separately protected key. Check `encryption_manifest.json`, then check every payload file against `manifest.json`.
2. Load `docker-images.tar` into the isolated Docker host. Recreate required networks and private mounts from the captured Compose/inspect configuration, adjusting host names, external ports and scheduled work so the rehearsal cannot reach production.
3. Start a fresh PostgreSQL instance from the saved matching image, retaining the source bootstrap role name. Replay `postgres-globals.sql` with only the already-existing bootstrap `CREATE ROLE` omitted; retain its `ALTER ROLE` and all grants. This preserves bootstrap grantor identity and avoids a role-membership replay failure found during the 16 November rehearsal. Restore `p6_reporting.dump` with `pg_restore --create --exit-on-error`. Verify application schemas, settings, source snapshot metadata, table counts, views and representative reports. A `pg_restore --list` success alone does not prove restoration. The supplied verifier uses an isolated container with no network, checks the full publication manifest against recovered SQLite history, and plans public views under the restored reader role.
4. Restore each captured SQL `.bak` into a unique rehearsal database on the dedicated SQL staging instance, using `RESTORE FILELISTONLY` and unique data/log `WITH MOVE` paths. Use no `REPLACE` and no production database names. Run `DBCC CHECKDB` (at least `PHYSICAL_ONLY`), compare PROJECT/TASK/TASKPRED counts and the captured user-object catalogue/module hashes, and record results before removing rehearsal databases.
5. Open a copy of the captured SQLite database, run its integrity check, and verify target configuration, reporting settings, schedules, users and permissions. Start a separate application copy with external writes and schedules disabled before checking authenticated reports/settings.
6. Save the isolated restore proof separately with the recovery ID and manifest checksum. Record precisely which checks were performed; a database restore alone does not prove complete host rebuild or client acceptance.

The supplied verifier also restores every enabled SQL target with unique data/log paths, checks physical database integrity, compares source row counts and every captured module hash, and rehearses the exact reporting-object removals in a rolled-back transaction on the restored P6 22 database. It never uses `REPLACE`. Failed rehearsal stores remain for diagnostics; successful temporary stores are removed. `restore-verification.json` is separate from the immutable capture manifest and offsite receipt.

Future recovery checkpoints verify an already-retired SQL layer against the captured retirement marker, PostgreSQL configuration and empty SQL removal scope. The original pre-retirement checkpoint retains its separate 40-object rollback rehearsal; the destructive retirement command continues to require that legacy rehearsal.

The Windows operator's separate key escrow is `Documents/Codex/P6-Recovery/<id>/backup-keyring.dpapi`. It is protected using Windows DPAPI for that Windows user and restrictive file permissions. It requires that user's Windows profile to decrypt; preserve the existing independently managed backup keys as well.

## Retire legacy SQL reporting safely

Keep source P6 tables and all native PostgreSQL reporting, settings, snapshots and published views. Define an explicit inventory of the SQL Server reporting views, functions, procedures, triggers, result/staging tables and rollback copies to remove; never infer scope from an entire schema name without reviewing its contents.

Disable SQL-parallel publication and any legacy refresh schedules before removal, drain queued/running work, and verify the active application reporting and settings engines use PostgreSQL. Check cross-schema dependencies and capture object definitions, table metadata and the approved removal inventory. Use a transaction with preconditions and explicit object names; fail on unexpected dependencies. Avoid `CASCADE`-style removal or blanket schema drops.

Rehearse removal on the restored P6 22 backup first. Prove the intended objects are gone, the P6 source tables remain, and reporting refresh still operates from its source snapshots. Apply only that reviewed inventory in production, then check the native PostgreSQL refresh, settings editing/publication, authenticated report pages and a representative external reporting query. Keep the pre-retirement recovery bundle immutable.

Do not shrink SQL files automatically after dropping reporting objects. Review actual allocated/free space and maintenance needs separately. Save production evidence and restore the recorded schedules/configuration appropriate to the retired SQL path.

To reinstate only the SQL reporting layer, restore its backup into an isolated database and recover the reporting definitions/configuration from that copy. Reconcile the rules with the current PostgreSQL settings before enabling SQL work again. Do not overwrite the operational P6 database with an older checkpoint just to recover reporting objects; P6 changes made since the checkpoint must be preserved. Remove the retirement marker and re-enable controls only after that reinstatement is tested.

## Complete removal of retained reporting remnants

The 16 November 2026 cleanup removed the remaining 18 reporting/settings/rollback
tables from P6 22, ten obsolete reporting-app Django tables from Axial, and the
three custom schemas `P62212_1.powerbitables`, `P62212_1.xertoolkit_rollback` and
`Axial.powerbitables`. Native P6 objects, P6 maintenance recovery tables, database
users and roles are protected by the explicit allowlist and catalogue checks.

`ops/recovery/cleanup_reporting_remnants.py` supports `capture`, `rehearse` and
`apply --directory /app/.recovery/<id>`. It exports table data and metadata,
verifies dependencies, rehearses removal in rolled-back staging transactions,
requires a verified encrypted offsite archive and the original full P6 recovery
proof, and checks both targets before committing. Capture-bound per-database
journals support safe completion if a later target fails. Treat captures as
private: the obsolete Django tables contain authentication/session data.

The completed recovery set is
`/app/.recovery/20261116-sql-reporting-remnants`, with its verified encrypted copy
under `AxialP6_Backups/Recovery/20261116-sql-reporting-remnants`. The earlier full
pre-retirement recovery set remains immutable. Runtime guards prevent report and
settings requests from falling back to SQL and keep the old schedule disabled.

Retain `p6-reporting-sql-stage`: the PostgreSQL ingestion process still uses it
to restore P6 backups and copy raw source tables. All reporting rules, settings,
calculations and report queries use PostgreSQL.

## Faster routine changes

Use a release ID and explicit file/SQL manifest for each change. Keep the production-only reporting source and runtime build definition under controlled source management before replacing file-copy releases with image releases. Check affected live hashes, capture state, test on a restored copy, deploy the explicit manifest, and record health checks plus a rollback pointer. Treat these as reusable commands with machine-readable receipts rather than manual shell histories.

Keep ordinary encrypted P6 backup retention separate from immutable recovery checkpoints. The settings checkpoint remains useful for fast settings recovery, while periodic database/app recovery captures and isolated restore drills cover host loss and accidental deletion. Alert on meaningful failures and stale recovery proof; avoid repeated unchanged success messages.

Offline helper checks:

```bash
python3 -m unittest ops.recovery.test_recovery
python3 -m py_compile ops/recovery/capture_recovery.py ops/recovery/application_recovery.py
```

## Docker wrapper and interrupted offsite transfers

`ops/recovery/run.sh` runs the capture/verification helpers as root through the operator's existing Docker capability. The disposable helper uses the deployed application image, has no network, and mounts only the deployment tree, Docker socket and Docker executable. It invokes the application container for operations requiring Django configuration or outbound SharePoint access. The wrapper avoids requiring a separate interactive `sudo` session. It does not copy private configuration to the operator's shell.

If capture and encryption have completed but the offsite transfer fails, preserve the original recovery ID and artifacts. Wait until the original publisher has exited, deploy the additive `ops/recovery/resume_publication.py`, then run:

```bash
docker exec p6-backup-admin-web python /app/ops/recovery/resume_publication.py \
  --destination /app/.recovery/YYYYMMDDTHHMMSSZ \
  --recovery-id YYYYMMDDTHHMMSSZ
```

The resume helper refuses concurrent same-ID publishers, locks other resume attempts, validates the original manifest and encrypted/plain archive hashes, and streams through the embedded archive to verify every payload member. It never recaptures databases, rebuilds archives, replaces existing remote files, or overwrites original completion/failure markers. Missing small files use a conditional content PUT with `If-None-Match: *`; existing items remain untouched and must pass download verification. Large files use fail-on-conflict upload sessions. A private transfer sidecar persists the signed session URL and server-confirmed byte ranges, allowing subsequent resume attempts to continue a partially completed session. If the original publisher failed without preserving its upload-session URL, the first resume must restart that file transfer; the existing capture is retained.

After the archive download, decryption and both checksum checks pass, the helper immediately saves a private `.archive-remote-verification.json` sidecar before publishing metadata. A later metadata-only retry can reuse that archive proof only when the recovery ID, expected encrypted/plain hashes, capture-manifest hash, key fingerprint, and remote item ID, eTag, cTag and size all still match. Missing or changed validators require a fresh full download/decryption check. The helper rechecks the archive's server validators before issuing the completed receipt. This avoids another multi-gigabyte transfer when only the small metadata upload failed, while retaining the original immutable artifacts.

Successful resume creates a separate timestamped `offsite_resume_receipt-*.json` and creates `offsite_receipt.json` only when absent. It does not change a prior `FAILED.json` or `COMPLETE.json`. Treat the verified receipt together with `restore-verification.json` and the original immutable manifest as the recovery evidence. Resume diagnostics and signed transfer state stay in the root-private `.recovery/<id>` directory. Do not copy those sidecars into Git or chat output.

Additional offline checks:

```bash
python3 -m unittest ops.recovery.test_recovery ops.recovery.test_resume_publication
```

After a resumed publication succeeds, issue the combined completion receipt without changing the historical failure marker:

```bash
docker exec p6-backup-admin-web python /app/ops/recovery/finalize_recovery.py \
  --directory /app/.recovery/YYYYMMDDTHHMMSSZ
```

`VERIFIED.json` is the primary combined success record. The finalizer uses the same full-proof gate as the retirement command, binding the capture manifest, offsite download/decryption receipt, and independent restoration proof. Its evidence checksums bind the component receipts. It creates this record exclusively and preserves `FAILED.json` as history; a prior failed transfer therefore does not hide a later verified recovery checkpoint. This record proves the stated database restoration and offsite checks, while complete host/proxy/TLS/application rebuild remains a separate exercise.

Normal publication also refreshes completed Graph item metadata when a large-upload completion response omits its download URL. Neither that fallback nor resume prints signed URLs.
