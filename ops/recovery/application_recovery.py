"""Run inside the production Django container; never print secret values."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import quote


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, default=str)
        stream.write("\n")
    path.chmod(0o600)


def validate_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{5,95}", value):
        raise ValueError("Recovery ID must be 6-96 letters, numbers, underscores or dashes.")
    return value


def setup_django() -> None:
    sys.path.insert(0, "/app")
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "p6_backup_admin.settings")
    import django
    django.setup()


def reporting_state() -> dict:
    from reporting_mirror.models import ReportingConfig, ReportingRun, NativeSettingsCheckpoint
    return {"config": list(ReportingConfig.objects.values("calculation_engine", "manage_settings_in_postgres", "serve_reports", "sql_parallel_enabled", "pending_engine")), "latest_publication": list(ReportingRun.objects.filter(status="success").order_by("-published_at", "-started_at").values("id", "published_at")[:1]), "settings": list(NativeSettingsCheckpoint.objects.values("sha256"))}


def reserve_offsite_folder(client, recovery_id: str) -> None:
    """Create missing parents; the new recovery leaf must never already exist."""
    root_url = f"https://graph.microsoft.com/v1.0/sites/{client.site_id}/drive"
    parent_id = None
    components = ["AxialP6_Backups", "Recovery", recovery_id]
    for index, name in enumerate(components):
        current = "/".join(components[: index + 1])
        response = client.session.get(f"{root_url}/root:/{quote(current, safe='/')}", headers=client.headers, timeout=30)
        if response.status_code == 200:
            if index == len(components) - 1:
                raise FileExistsError("The offsite recovery folder already exists.")
            payload = response.json()
            if "folder" not in payload:
                raise RuntimeError("An offsite recovery parent is not a folder.")
            parent_id = str(payload["id"])
            continue
        if response.status_code != 404:
            response.raise_for_status()
        endpoint = f"{root_url}/items/{quote(parent_id, safe='')}/children" if parent_id else f"{root_url}/root/children"
        created = client.session.post(endpoint, headers={**client.headers, "Content-Type": "application/json"}, json={"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}, timeout=30)
        created.raise_for_status()
        parent_id = str(created.json()["id"])


def completed_download_item(client, uploaded: dict, filename: str) -> dict:
    """Large upload completion can omit the short-lived download URL."""
    if uploaded.get("@microsoft.graph.downloadUrl"):
        return uploaded
    root = f"https://graph.microsoft.com/v1.0/sites/{client.site_id}/drive"
    item_id = str(uploaded.get("id", ""))
    endpoint = f"{root}/items/{quote(item_id, safe='')}" if item_id else f"{root}/root:/{quote(client.folder_path + '/' + filename, safe='/')}"
    response = client.session.get(endpoint, headers=client.headers, timeout=30)
    response.raise_for_status()
    item = response.json()
    if item.get("name") != filename or not item.get("@microsoft.graph.downloadUrl"):
        raise RuntimeError("SharePoint did not return completed recovery file metadata with a download URL.")
    return item


def capture(destination: Path, recovery_id: str) -> dict:
    from django.conf import settings
    from backups.models import BackupTarget
    from backups.services.mssql import execute_statement, fetch_rows, build_backup_sql, build_verifyonly_sql
    from backups.services.security import calculate_file_sha256

    target_dir = destination / "application"
    target_dir.mkdir(mode=0o700)
    database = settings.DATABASES["default"]
    if database["ENGINE"] != "django.db.backends.sqlite3":
        raise RuntimeError("The recovery helper currently requires SQLite application state.")
    source_database = Path(database["NAME"]).resolve(strict=True)
    sqlite_path = target_dir / "django.sqlite3"
    with sqlite3.connect(f"file:{source_database}?mode=ro", uri=True) as source:
        with sqlite3.connect(sqlite_path) as recovered:
            source.backup(recovered)
            if recovered.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise RuntimeError("SQLite recovery snapshot failed its integrity check.")
    sqlite_path.chmod(0o600)
    result = {"sqlite": {"original_path": str(source_database), "path": "application/django.sqlite3", "integrity_check": "ok"}, "sql_backups": []}
    targets = list(BackupTarget.objects.filter(enabled=True).order_by("slug"))
    if not targets:
        raise RuntimeError("No enabled SQL backup targets were found.")
    sql_directory = target_dir / "sql"
    sql_directory.mkdir(mode=0o700)
    for target in targets:
        name = f"recovery_{recovery_id}_{target.slug}.bak"
        if Path(name).name != name:
            raise RuntimeError("Unsafe target slug for recovery backup.")
        mounted = target.app_backup_path / name
        if mounted.exists():
            raise FileExistsError("An SQL recovery staging backup already exists.")
        sql_path = f"{target.sql_backup_dir.rstrip('/')}/{name}"
        statement = build_backup_sql(database_name=target.sql_database, backup_path=sql_path, compression=target.use_compression)
        statement = statement.replace("WITH INIT, CHECKSUM", "WITH COPY_ONLY, INIT, CHECKSUM", 1)
        if "COPY_ONLY" not in statement:
            raise RuntimeError("Could not construct a COPY_ONLY backup statement.")
        count_sql = "SELECT (SELECT COUNT_BIG(*) FROM dbo.PROJECT) AS project_count, (SELECT COUNT_BIG(*) FROM dbo.TASK) AS task_count, (SELECT COUNT_BIG(*) FROM dbo.TASKPRED) AS taskpred_count"
        counts_before = fetch_rows(target, count_sql, database=target.sql_database)[0]
        started = datetime.now(timezone.utc).isoformat()
        execute_statement(target, statement)
        execute_statement(target, build_verifyonly_sql(backup_path=sql_path))
        counts_after = fetch_rows(target, count_sql, database=target.sql_database)[0]
        if counts_before != counts_after:
            raise RuntimeError("P6 row counts changed during capture; retry after imports are paused.")
        catalogue = fetch_rows(target, "SELECT s.name AS schema_name,o.name,o.type,o.type_desc,CONVERT(varchar(64),HASHBYTES('SHA2_256',CONVERT(varbinary(max),m.definition)),2) AS definition_sha256 FROM sys.objects o JOIN sys.schemas s ON s.schema_id=o.schema_id LEFT JOIN sys.sql_modules m ON m.object_id=o.object_id WHERE o.is_ms_shipped=0 ORDER BY s.name,o.name", database=target.sql_database)
        machine = fetch_rows(target, "SELECT CONVERT(nvarchar(128),SERVERPROPERTY('MachineName')) AS machine_name,CONVERT(nvarchar(128),SERVERPROPERTY('ServerName')) AS server_name,DB_NAME() AS database_name", database=target.sql_database)[0]
        if not mounted.is_file():
            raise RuntimeError("SQL recovery backup is not visible through its configured mount.")
        copied = sql_directory / name
        shutil.copyfile(mounted, copied)
        copied.chmod(0o600)
        checksum = calculate_file_sha256(copied)
        if checksum != calculate_file_sha256(mounted):
            raise RuntimeError("SQL recovery backup copy checksum did not match.")
        result["sql_backups"].append({"target_slug": target.slug, "database": target.sql_database, "server": target.sql_host, "port": target.sql_port, "path": f"application/sql/{name}", "source_mount_path": str(mounted), "size_bytes": copied.stat().st_size, "sha256": checksum, "copy_only": True, "checksum": True, "restore_verifyonly": "passed", "row_counts_before": counts_before, "row_counts_after": counts_after, "object_catalogue": catalogue, "source_machine": machine, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat()})
        # The verified copy in the private capture is retained; avoid stale SQL mount copies.
        mounted.unlink()
    write_json(target_dir / "capture.json", result)
    return {"sql_target_count": len(targets), "sqlite_integrity": "ok"}


def publish(destination: Path, recovery_id: str) -> dict:
    from reporting_mirror.models import ReportingSource
    from backups.services.security import calculate_file_sha256, encrypt_file_chunked, decrypt_file_chunked, get_encryption_keys
    from backups.services.sharepoint import build_sharepoint_client, download_file

    archive = destination / "recovery.tar.gz"
    manifest = destination / "manifest.json"
    receipt_path = destination / "offsite_receipt.json"
    encrypted = destination / "recovery.tar.gz.enc"
    if receipt_path.exists() or encrypted.exists():
        raise FileExistsError("Recovery publish output already exists; use a new recovery ID.")
    source = ReportingSource.objects.select_related("target").get(code="p6_22")
    folder = f"AxialP6_Backups/Recovery/{recovery_id}"
    client = build_sharepoint_client(sharepoint_site=source.target.sharepoint_site, sharepoint_folder=folder)
    reserve_offsite_folder(client, recovery_id)
    keys = get_encryption_keys()
    plain_hash = calculate_file_sha256(archive)
    encrypt_file_chunked(archive, encrypted, keys[0])
    encrypted.chmod(0o600)
    encrypted_hash = calculate_file_sha256(encrypted)
    encryption_manifest = {
        "schema_version": 1, "recovery_id": recovery_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "encryption_scheme": "fernet-chunked-v1", "key_sha256": hashlib.sha256(keys[0].encode()).hexdigest(),
        "archive_sha256": plain_hash, "archive_bytes": archive.stat().st_size,
        "encrypted_sha256": encrypted_hash, "encrypted_bytes": encrypted.stat().st_size,
        "capture_manifest_sha256": calculate_file_sha256(manifest),
    }
    encryption_path = destination / "encryption_manifest.json"
    write_json(encryption_path, encryption_manifest)
    uploaded = completed_download_item(client, client.upload_file(encrypted, encrypted.name), encrypted.name)
    download_url = str(uploaded.get("@microsoft.graph.downloadUrl", ""))
    if not download_url:
        raise RuntimeError("SharePoint did not return a recovery download URL.")
    with tempfile.TemporaryDirectory(prefix="offsite-proof-", dir=destination) as temporary:
        root = Path(temporary)
        remote = root / "remote.enc"
        restored = root / "restored.tar.gz"
        download_file(download_url, remote)
        if calculate_file_sha256(remote) != encrypted_hash:
            raise RuntimeError("Downloaded recovery archive encrypted checksum did not match.")
        decrypt_file_chunked(remote, restored, keys)
        if calculate_file_sha256(restored) != plain_hash:
            raise RuntimeError("Downloaded recovery archive plaintext checksum did not match.")
    metadata_item = completed_download_item(client, client.upload_file(encryption_path, encryption_path.name), encryption_path.name)
    metadata_url = str(metadata_item.get("@microsoft.graph.downloadUrl", ""))
    if not metadata_url:
        raise RuntimeError("SharePoint did not return the recovery metadata download URL.")
    with tempfile.TemporaryDirectory(prefix="metadata-proof-", dir=destination) as temporary:
        metadata_copy = Path(temporary) / "metadata.json"
        download_file(metadata_url, metadata_copy)
        if calculate_file_sha256(metadata_copy) != calculate_file_sha256(encryption_path):
            raise RuntimeError("Downloaded recovery metadata checksum did not match.")
    receipt = {"recovery_id": recovery_id, "folder": folder, "archive_item_id": str(uploaded.get("id", "")), "archive_web_url": str(uploaded.get("webUrl", "")), "metadata_item_id": str(metadata_item.get("id", "")), "encrypted_sha256": encrypted_hash, "archive_sha256": plain_hash, "upload_download_decrypt_sha256": "passed", "verified_at": datetime.now(timezone.utc).isoformat()}
    write_json(receipt_path, receipt)
    return receipt


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("capture", "publish", "guard"))
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--recovery-id", required=True, type=validate_id)
    args = parser.parse_args()
    destination = args.destination.resolve(strict=True)
    if destination.name != args.recovery_id or destination.parent.name != ".recovery":
        raise ValueError("Destination must be an existing .recovery/<recovery-id> directory.")
    setup_django()
    result = capture(destination, args.recovery_id) if args.operation == "capture" else publish(destination, args.recovery_id) if args.operation == "publish" else reporting_state()
    print(json.dumps(result, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
