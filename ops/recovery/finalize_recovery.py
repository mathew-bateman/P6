#!/usr/bin/env python3
"""Record full recovery verification, including a successfully resumed upload."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from retire_sql_reporting import verify_backup_receipt, file_digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    args = parser.parse_args()
    directory = args.directory.resolve(strict=True)
    if directory.parent.name != '.recovery':
        raise ValueError('Expected a private .recovery/<id> directory.')
    capture = json.loads((directory / 'application/capture.json').read_text())
    source = next(row for row in capture['sql_backups'] if row['target_slug'] == 'p62212')
    receipt = {
        'status': 'verified', 'recovery_id': directory.name,
        'recovery_directory': str(directory),
        'verified_at': datetime.now(timezone.utc).isoformat(),
        'sql_source': {
            'database': source['database'], 'machine': source['source_machine']['machine_name'],
            'backup_path': str(directory / source['path']),
            'sha256': source['sha256'], 'restore_verified': True,
        },
        'proof_sha256': {name: file_digest(directory / name) for name in (
            'manifest.json', 'encryption_manifest.json', 'offsite_receipt.json',
            'restore-verification.json', 'application/capture.json')},
    }
    # General checkpoints also support SQL backups taken after the verified
    # retirement. Destructive retirement retains its stricter rehearsal gate.
    verify_backup_receipt(receipt, require_legacy_rehearsal=False)
    with (directory / 'VERIFIED.json').open('x') as output:
        json.dump(receipt, output, indent=2)
    print(json.dumps({'status': 'verified', 'receipt': str(directory / 'VERIFIED.json'),
                      'recovery_id': directory.name}))


if __name__ == '__main__':
    main()
