#!/usr/bin/env python3
"""Restore a captured recovery set into isolated PostgreSQL and SQL test stores.

Run on the deployment Docker host as root. The production databases are never
restore destinations. Diagnostics stay in the private recovery directory.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tarfile
import time


def checksum(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def command(args, log, *, input_path=None, input_bytes=None):
    log.write(('COMMAND ' + json.dumps(args) + '\n').encode())
    log.flush()
    if input_path:
        with Path(input_path).open('rb') as stream:
            return subprocess.run(args, stdin=stream, stdout=subprocess.PIPE,
                                  stderr=log, check=True).stdout
    return subprocess.run(args, input=input_bytes, stdout=subprocess.PIPE,
                          stderr=log, check=True).stdout


def pg_query(container, user, database, statement, log):
    return command(['docker', 'exec', '-i', container, 'psql', '-X', '-qAt',
                    '-v', 'ON_ERROR_STOP=1', '-U', user, '-d', database], log,
                   input_bytes=statement.encode()).decode().strip()


def verify_postgres(directory, recovery_id, log):
    name = 'p6-recovery-verify-' + recovery_id.lower()
    # Preserve the source bootstrap identity. PostgreSQL records grantors by
    # bootstrap OID; restoring its role memberships under a different bootstrap
    # role can fail even for a new superuser. Only the already-created bootstrap
    # CREATE ROLE is omitted from replay; its ALTER ROLE and grants are retained.
    inspected = json.loads(command(['docker', 'inspect', 'p6-reporting-postgres'], log))[0]
    environment = dict(value.split('=', 1) for value in inspected['Config']['Env'] if '=' in value)
    admin = environment.get('POSTGRES_USER', 'postgres')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', admin):
        raise RuntimeError('Unsupported source bootstrap role name.')
    image = command(['docker', 'inspect', 'p6-reporting-postgres', '--format', '{{.Image}}'], log).decode().strip()
    # The temporary database has no network, no published ports and no live mount.
    command(['docker', 'run', '--detach', '--name', name, '--network', 'none',
             '--memory', '3g', '--cpus', '2', '-e', 'POSTGRES_USER=' + admin,
             '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', image], log)
    completed = False
    try:
        for attempt in range(60):
            ready = subprocess.run(['docker', 'exec', name, 'pg_isready', '-U', admin],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Isolated PostgreSQL did not become ready.')
        globals_sql = (directory / 'postgres-globals.sql').read_text()
        bootstrap_create = 'CREATE ROLE ' + admin + ';'
        if globals_sql.count(bootstrap_create) != 1:
            raise RuntimeError('Expected exactly one source bootstrap CREATE ROLE.')
        globals_sql = globals_sql.replace(bootstrap_create, '-- Existing matching bootstrap role retained.', 1)
        command(['docker', 'exec', '-i', name, 'psql', '-X', '-q', '-v',
                 'ON_ERROR_STOP=1', '-U', admin, '-d', 'postgres'], log,
                input_bytes=globals_sql.encode())
        print('Restoring full PostgreSQL archive into isolated container', flush=True)
        command(['docker', 'exec', '-i', name, 'pg_restore', '--exit-on-error',
                 '--create', '--username', admin, '--dbname', 'postgres'], log,
                input_path=directory / 'p6_reporting.dump')
        publication = json.loads(pg_query(name, admin, 'p6_reporting',
            'SELECT row_to_json(s) FROM reporting.refresh_status s;', log))
        with sqlite3.connect('file:' + str(directory / 'application/django.sqlite3') + '?mode=ro', uri=True) as app:
            if app.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('Recovered SQLite integrity check failed.')
            run = app.execute('SELECT manifest FROM reporting_mirror_reportingrun WHERE id=? AND status=?',
                              (publication['batch_id'].replace('-', ''), 'success')).fetchone()
        if not run:
            raise RuntimeError('PostgreSQL publication is not present in recovered SQLite history.')
        entries = json.loads(run[0])
        queries = []
        expected = {}
        for index, entry in enumerate(entries):
            source, table = entry['source'], entry['name']
            if source not in {'p6_22', 'p6_23'} or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', table):
                raise RuntimeError('Unsafe object in recovered publication manifest.')
            queries.append(f'SELECT {index}::text || chr(9) || count(*)::text FROM "{source}"."{table}";')
            expected[index] = int(entry['rows'])
        observed = {}
        for row in pg_query(name, admin, 'p6_reporting', '\n'.join(queries), log).splitlines():
            key, value = row.split('\t')
            observed[int(key)] = int(value)
        if observed != expected:
            raise RuntimeError('Recovered PostgreSQL row counts do not match publication manifest.')
        with tarfile.open(directory / 'deployment-config.tar.gz', 'r:gz') as archive:
            config = json.load(archive.extractfile('reporting-secrets.json'))
        reader = config['postgres']['reader_user']
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', reader):
            raise RuntimeError('Unexpected reporting reader role name.')
        # Plan every public reporting view as the recovered reader role, proving
        # its restored schema access and SELECT grants as well as definitions.
        check_views = """
        SET ROLE READER_ROLE;
        DO $proof$ DECLARE r record; BEGIN
          FOR r IN SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n
            ON n.oid=c.relnamespace WHERE c.relkind='v' AND n.nspname IN
            ('reporting','p6_22','p6_23') LOOP
            EXECUTE format('SELECT * FROM %I.%I LIMIT 0',r.nspname,r.relname);
          END LOOP;
        END $proof$;
        SELECT json_build_object('views',count(*)) FROM pg_class c JOIN pg_namespace n
          ON n.oid=c.relnamespace WHERE c.relkind='v' AND n.nspname IN
          ('reporting','p6_22','p6_23');
        """.replace('READER_ROLE', '"' + reader + '"')
        views = json.loads(pg_query(name, admin, 'p6_reporting', check_views, log))
        completed = True
        return {'status': 'passed', 'container': name, 'network': 'none',
                'globals_restored': True, 'pg_restore_exit_on_error': 'passed',
                'publication_batch': publication['batch_id'],
                'manifest_row_counts_checked': len(expected),
                'consumer_views_planned': views['views'], 'sqlite_integrity': 'ok'}
    finally:
        # Only remove the exact temporary container created above. Its anonymous
        # data volume is disposable; production volumes are never attached.
        if completed:
            command(['docker', 'rm', '--force', '--volumes', name], log)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.directory.resolve(strict=True)
    if directory.parent.name != '.recovery' or not re.fullmatch(r'[A-Za-z0-9_-]{6,96}', directory.name):
        parser.error('Expected a private .recovery/<safe-id> directory.')
    if os.geteuid() != 0:
        parser.error('Run the host verification as root.')
    output = directory / 'restore-verification.json'
    if output.exists():
        raise FileExistsError('Restore verification already exists; it will not be overwritten.')
    manifest = json.loads((directory / 'manifest.json').read_text())
    for row in manifest['files']:
        path = (directory / row['path']).resolve(strict=True)
        if not path.is_relative_to(directory) or checksum(path) != row['sha256']:
            raise RuntimeError('Captured artifact checksum did not match manifest.')
    with (directory / ('restore-verification-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.log')).open('xb') as log:
        postgres = verify_postgres(directory, directory.name, log)
        print('Restoring SQL backups into unique databases on reporting staging', flush=True)
        capture = json.loads((directory / 'application/capture.json').read_text())
        helper = Path(__file__).with_name('verify_sql_recovery.py')
        result = json.loads(command(['docker', 'exec', 'p6-backup-admin-web', 'python',
            '/app/ops/recovery/' + helper.name, '--directory', '/app/.recovery/' + directory.name], log))
        proof = {'status': 'verified', 'recovery_id': directory.name,
                 'verified_at': datetime.now(timezone.utc).isoformat(),
                 'manifest_sha256': checksum(directory / 'manifest.json'),
                 'postgres': postgres, 'sql': result, 'sqlite_integrity': 'ok'}
        with output.open('x') as stream:
            json.dump(proof, stream, indent=2)
        print(json.dumps({'status': 'verified', 'postgres_manifest_objects': postgres['manifest_row_counts_checked'],
                          'consumer_views': postgres['consumer_views_planned'],
                          'sql_databases_restored': len(result['databases']), 'receipt': str(output)}), flush=True)


if __name__ == '__main__':
    main()
