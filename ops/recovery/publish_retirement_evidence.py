#!/usr/bin/env python3
"""Preserve post-retirement receipts, deployed guards and process tools offsite."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tarfile
import tempfile

from application_recovery import setup_django, write_json, completed_download_item


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    directory = args.directory.resolve(strict=True)
    if directory.parent.name != '.recovery':
        raise ValueError('Expected a private recovery checkpoint directory.')
    final = json.loads((directory / 'post-retirement-verification.json').read_text())
    if final['status'] != 'passed':
        raise RuntimeError('Successful final verification is required.')
    setup_django()
    from backups.services.security import calculate_file_sha256 as sha256, encrypt_file_chunked, decrypt_file_chunked, get_encryption_keys
    from backups.services.sharepoint import build_sharepoint_client, download_file
    from reporting_mirror.models import ReportingSource
    app = Path('/app')
    receipt_names = ('VERIFIED.json', 'manifest.json', 'encryption_manifest.json', 'offsite_receipt.json',
        'restore-verification.json', 'retirement-plan.json', 'retirement-result.json',
        'retirement-controls-before.json', 'post-retirement-refresh-request.json',
        'http-smoke.json', 'http-smoke-after-refresh.json', 'guard-smoke.json', 'post-retirement-verification.json')
    files = [(directory / name, 'receipts/' + name) for name in receipt_names]
    files += [(p, 'process/' + p.name) for p in sorted((app / 'ops/recovery').glob('*.py'))]
    files += [(app / 'ops/recovery/run.sh', 'process/run.sh'),
              (app / 'docs/recovery-and-reporting-retirement.md', 'process/recovery-and-reporting-retirement.md'),
              (app / '.dockerignore', 'runtime/.dockerignore'),
              (Path('/stage/state/sql-reporting-retired.json'), 'runtime/sql-reporting-retired.json')]
    for name in ('forms.py', 'sync.py', 'sql_parallel.py', 'views.py', 'tasks.py', 'sql_retirement.py'):
        files.append((app / 'reporting_mirror' / name, 'runtime/reporting_mirror/' + name))
    files.append((app / 'templates/reporting_mirror/section_engine.html', 'runtime/templates/section_engine.html'))
    manifest = {'schema_version': 1, 'recovery_id': directory.name,
        'purpose': 'Post-retirement verification and process/source supplement; primary database backups remain in recovery.tar.gz.enc',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'files': [{'path': name, 'size_bytes': path.stat().st_size, 'sha256': sha256(path)} for path, name in files]}
    payload_manifest = directory / 'retirement-evidence.payload-manifest.json'
    write_json(payload_manifest, manifest)
    archive = directory / 'retirement-evidence.tar.gz'
    with tarfile.open(archive, 'x:gz', compresslevel=1) as tar:
        for path, name in files:
            tar.add(path, arcname=name, recursive=False)
        tar.add(payload_manifest, arcname='payload-manifest.json', recursive=False)
    with tarfile.open(archive, 'r:gz') as tar:
        for row in manifest['files']:
            source = tar.extractfile(row['path'])
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
                digest.update(chunk)
            if digest.hexdigest() != row['sha256']:
                raise RuntimeError('A supplement source changed during capture.')
    encrypted = directory / 'retirement-evidence.tar.gz.enc'
    keys = get_encryption_keys()
    encrypt_file_chunked(archive, encrypted, keys[0])
    metadata = {'schema_version': 1, 'recovery_id': directory.name,
        'archive_sha256': sha256(archive), 'encrypted_sha256': sha256(encrypted),
        'key_sha256': hashlib.sha256(keys[0].encode()).hexdigest(),
        'primary_manifest_sha256': sha256(directory / 'manifest.json')}
    metadata_path = directory / 'retirement-evidence.manifest.json'
    write_json(metadata_path, metadata)
    source = ReportingSource.objects.select_related('target').get(code='p6_22')
    client = build_sharepoint_client(sharepoint_site=source.target.sharepoint_site,
        sharepoint_folder='AxialP6_Backups/Recovery/' + directory.name)
    names = {item['name'] for item in client.list_folder_children()}
    if names & {encrypted.name, metadata_path.name}:
        raise FileExistsError('The post-retirement supplement already exists offsite.')
    uploaded = {}
    for path in (encrypted, metadata_path):
        with path.open('rb') as stream:
            response = client.session.put(client._content_url(path.name),
                headers={**client.headers, 'Content-Type': 'application/octet-stream', 'If-None-Match': '*'},
                data=stream, timeout=120)
        response.raise_for_status()
        uploaded[path.name] = completed_download_item(client, response.json(), path.name)
    with tempfile.TemporaryDirectory(prefix='evidence-proof-', dir=directory) as temporary:
        root = Path(temporary)
        remote, restored, remote_metadata = root / 'remote.enc', root / 'restored.tar.gz', root / 'metadata.json'
        download_file(uploaded[encrypted.name]['@microsoft.graph.downloadUrl'], remote)
        if sha256(remote) != metadata['encrypted_sha256']:
            raise RuntimeError('Offsite supplement encrypted checksum mismatch.')
        decrypt_file_chunked(remote, restored, keys)
        if sha256(restored) != metadata['archive_sha256']:
            raise RuntimeError('Offsite supplement decrypted checksum mismatch.')
        download_file(uploaded[metadata_path.name]['@microsoft.graph.downloadUrl'], remote_metadata)
        if sha256(remote_metadata) != sha256(metadata_path):
            raise RuntimeError('Offsite supplement metadata checksum mismatch.')
    receipt = {'status': 'verified', 'recovery_id': directory.name,
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'archive_web_url': uploaded[encrypted.name]['webUrl'],
        'upload_download_decrypt_sha256': 'passed', 'files': len(files), **metadata}
    write_json(directory / 'retirement-evidence-receipt.json', receipt)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
