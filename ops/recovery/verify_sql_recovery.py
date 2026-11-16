#!/usr/bin/env python3
"""Restore saved SQL backups only into uniquely named reporting-stage databases."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.directory.resolve(strict=True)
    if directory.parent.name != '.recovery' or not re.fullmatch(r'[A-Za-z0-9_-]{6,96}', directory.name):
        raise ValueError('Unexpected recovery directory.')
    sys.path.insert(0, '/app')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'p6_backup_admin.settings')
    import django
    django.setup()
    from backups.models import BackupTarget
    from backups.services.mssql import connect, quote_identifier as qi, sql_string as qs
    from backups.services.security import calculate_file_sha256
    from reporting_mirror.connections import staging_target
    from reporting_mirror.sql_staging import query, assert_staging
    capture = json.loads((directory / 'application/capture.json').read_text())
    records = []
    for row in capture['sql_backups']:
        source = (directory / row['path']).resolve(strict=True)
        if not source.is_relative_to(directory) or calculate_file_sha256(source) != row['sha256']:
            raise RuntimeError('SQL recovery artifact checksum failed.')
        target = BackupTarget.objects.get(slug=row['target_slug'])
        bucket = Path(target.app_backup_dir).name
        if bucket not in {'p62212', 'axialp6'}:
            raise RuntimeError('Unknown staging backup mount.')
        suffix = hashlib.sha256((directory.name + row['target_slug']).encode()).hexdigest()[:16]
        database = 'recovery_verify_' + suffix
        mounted = Path(target.app_backup_dir) / ('restore_proof_' + suffix + '.bak')
        # No live database, reusable stage database, or existing file can be replaced.
        with mounted.open('xb') as out, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, out, length=4 * 1024 * 1024)
        mounted.chmod(0o644)
        succeeded = False
        try:
            if calculate_file_sha256(mounted) != row['sha256']:
                raise RuntimeError('SQL restore staging file checksum failed.')
            stage_path = '/source-backups/' + bucket + '/' + mounted.name
            with connect(staging_target()) as stage:
                stage.timeout = 1800
                assert_staging(stage)
                if query(stage, 'SELECT DB_ID(?) id', (database,))[0]['id'] is not None:
                    raise RuntimeError('Unique recovery verification database already exists.')
                headers = query(stage, 'RESTORE HEADERONLY FROM DISK=' + qs(stage_path))
                if len(headers) != 1 or headers[0]['DatabaseName'] != row['database'] or not headers[0]['HasBackupChecksums'] or not headers[0]['IsCopyOnly']:
                    raise RuntimeError('SQL backup header identity/checksum/COPY_ONLY proof failed.')
                files = query(stage, 'RESTORE FILELISTONLY FROM DISK=' + qs(stage_path))
                if not files or any(item['Type'] not in {'D', 'L'} for item in files):
                    raise RuntimeError('Unexpected SQL backup file types.')
                moves = ["MOVE " + qs(item['LogicalName']) + " TO " + qs('/var/opt/mssql/data/' + database + '_' + str(index) + ('.ldf' if item['Type'] == 'L' else '.mdf'))
                         for index, item in enumerate(files, 1)]
                query(stage, 'RESTORE DATABASE ' + qi(database) + ' FROM DISK=' + qs(stage_path) + ' WITH CHECKSUM, RECOVERY, ' + ', '.join(moves))
                if query(stage, 'DBCC CHECKDB (' + qi(database) + ') WITH PHYSICAL_ONLY, NO_INFOMSGS'):
                    raise RuntimeError('DBCC returned database integrity findings.')
                counts = query(stage, 'SELECT (SELECT COUNT_BIG(*) FROM ' + qi(database) + '.dbo.PROJECT) projects,'
                    '(SELECT COUNT_BIG(*) FROM ' + qi(database) + '.dbo.TASK) activities,'
                    '(SELECT COUNT_BIG(*) FROM ' + qi(database) + '.dbo.TASKPRED) relationships')[0]
                expected_counts = row['row_counts_after']
                if counts != {'projects': expected_counts['project_count'], 'activities': expected_counts['task_count'], 'relationships': expected_counts['taskpred_count']}:
                    raise RuntimeError('Restored P6 row counts differ from the captured source.')
                restored_catalogue = query(stage, "SELECT s.name AS schema_name,o.name,o.type,o.type_desc,CONVERT(varchar(64),HASHBYTES('SHA2_256',CONVERT(varbinary(max),m.definition)),2) AS definition_sha256 FROM " + qi(database) + ".sys.objects o JOIN " + qi(database) + ".sys.schemas s ON s.schema_id=o.schema_id LEFT JOIN " + qi(database) + ".sys.sql_modules m ON m.object_id=o.object_id WHERE o.is_ms_shipped=0 ORDER BY s.name,o.name")
                if restored_catalogue != row['object_catalogue']:
                    raise RuntimeError('Restored SQL object catalogue or module hashes differ from capture.')
                modules = query(stage, 'SELECT s.name schema_name,RTRIM(o.type) kind,COUNT_BIG(*) objects FROM ' + qi(database) + '.sys.objects o JOIN ' + qi(database) + '.sys.schemas s ON s.schema_id=o.schema_id WHERE s.name=\'powerbitables\' AND o.type IN(\'V\',\'IF\',\'P\',\'TR\',\'U\') GROUP BY s.name,o.type')
                rehearsal = None
                if row['target_slug'] == 'p62212':
                    from retire_sql_reporting import ALLOWLIST, TRIGGERS, VIEWS, PROCEDURES, FUNCTIONS, RESULT_TABLES, retirement_scope_catalogue, captured_retirement_marker
                    observed = retirement_scope_catalogue(restored_catalogue)
                    if not observed:
                        rehearsal = captured_retirement_marker(directory)
                    elif observed != ALLOWLIST:
                        raise RuntimeError('Restored reporting retirement inventory differs from approved allowlist.')
                    else:
                        query(stage, 'USE ' + qi(database))
                        if query(stage, 'SELECT DB_NAME() n')[0]['n'] != database or not database.startswith('recovery_verify_'):
                            raise RuntimeError('Rehearsal database identity failed.')
                        query(stage, 'BEGIN TRANSACTION')
                        try:
                            for names, kind in ((TRIGGERS, 'TRIGGER'), (VIEWS, 'VIEW'), (PROCEDURES, 'PROCEDURE'), (FUNCTIONS, 'FUNCTION'), (RESULT_TABLES, 'TABLE')):
                                for object_name in names:
                                    query(stage, 'DROP ' + kind + ' powerbitables.' + qi(object_name))
                            remaining = query(stage, "SELECT name FROM sys.objects WHERE schema_id=SCHEMA_ID('powerbitables') AND (type IN('V','P','IF','FN','TF','TR') OR name LIKE 'xertoolkit[_]result[_]%')")
                            if remaining:
                                raise RuntimeError('Retirement rehearsal left reporting outputs behind.')
                            after_counts = query(stage, 'SELECT (SELECT COUNT_BIG(*) FROM dbo.PROJECT) projects,(SELECT COUNT_BIG(*) FROM dbo.TASK) activities,(SELECT COUNT_BIG(*) FROM dbo.TASKPRED) relationships')[0]
                            if after_counts != counts:
                                raise RuntimeError('Retirement rehearsal changed P6 source row counts.')
                            rehearsal = {'status': 'passed', 'objects_removed': len(ALLOWLIST), 'source_row_counts_unchanged': True, 'transaction_rolled_back': True}
                        finally:
                            query(stage, 'ROLLBACK TRANSACTION')
                        query(stage, 'USE master')
                records.append({'target_slug': row['target_slug'], 'database': row['database'],
                    'backup_sha256': row['sha256'], 'restored_database': database,
                    'counts': counts, 'reporting_objects': modules, 'object_catalogue_and_module_hashes': 'matched', 'retirement_rehearsal': rehearsal, 'restore_with_checksum': 'passed',
                    'dbcc_checkdb_physical_only': 'passed'})
                # Drop only the exact freshly created test database, after all proof.
                query(stage, 'DROP DATABASE ' + qi(database))
                succeeded = True
        finally:
            # On failure retain the DB and copied file for private diagnostics.
            if succeeded:
                mounted.unlink()
    result = {'status': 'passed', 'verified_at': datetime.now(timezone.utc).isoformat(), 'databases': records}
    with (directory / 'sql-restore-verification.json').open('x') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
