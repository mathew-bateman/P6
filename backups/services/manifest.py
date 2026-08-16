from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


def write_manifest(manifest_path: Path, payload: dict[str, object]) -> None:
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def read_manifest(manifest_path: Path) -> dict[str, object]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def build_backup_manifest(
    *,
    target,
    plain_filename: str,
    encrypted_filename: str,
    plaintext_sha256: str,
    encrypted_sha256: str,
    plaintext_size_bytes: int,
    encrypted_size_bytes: int,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "target_slug": target.slug,
        "target_name": target.name,
        "database_name": target.sql_database,
        "backup_format": "mssql_bak",
        "backup_command": "BACKUP DATABASE",
        "backup_options": {
            "checksum": True,
            "compression": bool(target.use_compression),
        },
        "encrypted": True,
        "encryption_scheme": "fernet-chunked-v1",
        "original_filename": plain_filename,
        "encrypted_filename": encrypted_filename,
        "plaintext_sha256": plaintext_sha256,
        "encrypted_sha256": encrypted_sha256,
        "plaintext_size_bytes": plaintext_size_bytes,
        "encrypted_size_bytes": encrypted_size_bytes,
    }
