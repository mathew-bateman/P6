from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from backups.models import BackupRun, BackupTarget
from backups.services.emailing import send_backup_status_email
from backups.services.manifest import read_manifest
from backups.services.mssql import restore_database
from backups.services.security import (
    calculate_file_sha256,
    decrypt_file_chunked,
    get_encryption_keys,
)
from backups.services.sharepoint import download_file


logger = logging.getLogger(__name__)


def build_sharepoint_backup_rows(
    items: list[dict[str, object]],
    *,
    target_slug: str,
) -> list[dict[str, object]]:
    manifest_lookup: dict[str, str] = {}
    backups: list[dict[str, object]] = []

    for item in items:
        if "file" not in item:
            continue
        name = str(item.get("name", ""))
        download_url = str(item.get("@microsoft.graph.downloadUrl", ""))
        if name.endswith(".manifest.json"):
            manifest_lookup[name[: -len(".manifest.json")]] = download_url

    prefix = f"{target_slug}_backup_"
    for item in items:
        if "file" not in item:
            continue
        name = str(item.get("name", ""))
        if not name.startswith(prefix) or not name.endswith(".bak.enc"):
            continue
        raw_date = str(item.get("lastModifiedDateTime", ""))
        last_modified = raw_date
        if raw_date:
            try:
                parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                last_modified = parsed.strftime("%d %b %Y, %H:%M")
            except ValueError:
                pass
        backups.append(
            {
                "id": str(item.get("id", "")),
                "name": name,
                "display_name": name[:-4],
                "size_mb": round(float(item.get("size", 0) or 0) / (1024 * 1024), 2),
                "last_modified": last_modified,
                "raw_date": raw_date,
                "download_url": str(item.get("@microsoft.graph.downloadUrl", "")),
                "manifest_download_url": manifest_lookup.get(name, ""),
                "has_manifest": bool(manifest_lookup.get(name, "")),
            }
        )

    backups.sort(key=lambda row: str(row["raw_date"]), reverse=True)
    return backups


def _safe_name(file_name: str) -> str:
    return Path(file_name).name


def run_restore_for_target(
    *,
    target_slug: str,
    download_url: str,
    file_name: str,
    manifest_download_url: str,
    confirmation_database: str,
    trigger: str = BackupRun.TRIGGER_MANUAL,
) -> str:
    target = BackupTarget.objects.get(slug=target_slug)
    if confirmation_database != target.sql_database:
        raise RuntimeError("Restore confirmation did not match the target database name.")
    if not download_url or not file_name or not manifest_download_url:
        raise RuntimeError("A backup file and manifest are required for restore.")

    source_filename = _safe_name(file_name)
    if not source_filename.endswith(".bak.enc"):
        raise RuntimeError("Only encrypted .bak.enc backups can be restored.")

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    encrypted_local_name = f"restore_{timestamp}_{source_filename}"
    plain_local_name = encrypted_local_name[:-4]
    manifest_local_name = f"{encrypted_local_name}.manifest.json"

    backup_run = BackupRun.objects.create(
        target=target,
        operation=BackupRun.OPERATION_RESTORE,
        status=BackupRun.STATUS_RUNNING,
        trigger=trigger,
        backup_name=f"restore_{target.slug}_{timestamp}",
        restore_source_filename=source_filename,
        plain_filename=plain_local_name,
        encrypted_filename=encrypted_local_name,
        manifest_filename=manifest_local_name,
        sharepoint_folder=target.sharepoint_folder,
    )

    target.app_backup_path.mkdir(parents=True, exist_ok=True)
    encrypted_path = target.app_backup_path / encrypted_local_name
    plain_path = target.app_backup_path / plain_local_name
    manifest_path = target.app_backup_path / manifest_local_name
    artifacts = [encrypted_path, plain_path, manifest_path]

    try:
        download_file(download_url, encrypted_path)
        download_file(manifest_download_url, manifest_path)
        manifest = read_manifest(manifest_path)

        expected_encrypted_sha = str(manifest.get("encrypted_sha256", ""))
        actual_encrypted_sha = calculate_file_sha256(encrypted_path)
        if expected_encrypted_sha and actual_encrypted_sha != expected_encrypted_sha:
            raise RuntimeError("Encrypted backup hash verification failed.")

        decrypt_file_chunked(encrypted_path, plain_path, get_encryption_keys())

        expected_plain_sha = str(manifest.get("plaintext_sha256", ""))
        actual_plain_sha = calculate_file_sha256(plain_path)
        if expected_plain_sha and actual_plain_sha != expected_plain_sha:
            raise RuntimeError("Decrypted backup hash verification failed.")

        restore_database(target, backup_filename=plain_local_name)

        backup_run.plaintext_size_bytes = plain_path.stat().st_size
        backup_run.encrypted_size_bytes = encrypted_path.stat().st_size
        backup_run.plaintext_sha256 = actual_plain_sha
        backup_run.encrypted_sha256 = actual_encrypted_sha
        backup_run.status = BackupRun.STATUS_SUCCESS
        backup_run.finished_at = timezone.now()
        backup_run.save()
        send_backup_status_email(backup_run=backup_run, success=True)
        return "Restore Complete"
    except Exception as error:
        logger.exception("P6 restore failed for target %s", target.slug)
        backup_run.status = BackupRun.STATUS_FAILED
        backup_run.finished_at = timezone.now()
        backup_run.error_message = str(error)
        backup_run.save(update_fields=["status", "finished_at", "error_message"])
        try:
            send_backup_status_email(backup_run=backup_run, success=False)
        except Exception:
            logger.exception("Failed to send restore failure email")
        raise
    finally:
        keep_local = target.keep_local_files or settings.BACKUP_KEEP_LOCAL
        if not keep_local:
            for path in artifacts:
                try:
                    if path.exists():
                        path.unlink()
                except OSError:
                    logger.warning("Failed to remove restore artifact %s", path, exc_info=True)
