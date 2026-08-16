from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from backups.models import BackupRun, BackupTarget
from backups.services.emailing import send_backup_status_email
from backups.services.manifest import build_backup_manifest, write_manifest
from backups.services.mssql import backup_database
from backups.services.retention import plan_backup_retention_deletions
from backups.services.security import (
    calculate_file_sha256,
    encrypt_file_chunked,
    get_encryption_keys,
)
from backups.services.sharepoint import build_sharepoint_client


logger = logging.getLogger(__name__)


def _non_negative_int(value: int, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _cleanup(paths: list[Path], *, keep_local: bool) -> None:
    if keep_local:
        return
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            logger.warning("Failed to remove local backup artifact %s", path, exc_info=True)


def run_backup_for_target(
    *,
    target_slug: str,
    force: bool = False,
    trigger: str = BackupRun.TRIGGER_SCHEDULED,
) -> str:
    target = BackupTarget.objects.get(slug=target_slug)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{target.slug}_backup_{timestamp}"
    plain_filename = f"{backup_name}.bak"
    encrypted_filename = f"{plain_filename}.enc"
    manifest_filename = f"{encrypted_filename}.manifest.json"

    backup_run = BackupRun.objects.create(
        target=target,
        operation=BackupRun.OPERATION_BACKUP,
        status=BackupRun.STATUS_RUNNING,
        trigger=trigger,
        backup_name=backup_name,
        plain_filename=plain_filename,
        encrypted_filename=encrypted_filename,
        manifest_filename=manifest_filename,
        sharepoint_folder=target.sharepoint_folder,
    )

    if not target.enabled and not force:
        backup_run.status = BackupRun.STATUS_DISABLED
        backup_run.finished_at = timezone.now()
        backup_run.save(update_fields=["status", "finished_at"])
        return "Disabled"

    target.app_backup_path.mkdir(parents=True, exist_ok=True)
    plain_path = target.app_backup_path / plain_filename
    encrypted_path = target.app_backup_path / encrypted_filename
    manifest_path = target.app_backup_path / manifest_filename
    artifacts = [plain_path, encrypted_path, manifest_path]

    try:
        backup_database(target, backup_filename=plain_filename)
        if not plain_path.exists():
            raise RuntimeError(
                f"SQL Server completed backup but the app cannot see {plain_path}. "
                "Check the target app_backup_dir mount."
            )

        plaintext_sha256 = calculate_file_sha256(plain_path)
        encryption_key = get_encryption_keys()[0]
        encrypt_file_chunked(plain_path, encrypted_path, encryption_key)
        encrypted_sha256 = calculate_file_sha256(encrypted_path)

        backup_run.plaintext_size_bytes = plain_path.stat().st_size
        backup_run.encrypted_size_bytes = encrypted_path.stat().st_size
        backup_run.plaintext_sha256 = plaintext_sha256
        backup_run.encrypted_sha256 = encrypted_sha256

        manifest_payload = build_backup_manifest(
            target=target,
            plain_filename=plain_filename,
            encrypted_filename=encrypted_filename,
            plaintext_sha256=plaintext_sha256,
            encrypted_sha256=encrypted_sha256,
            plaintext_size_bytes=backup_run.plaintext_size_bytes,
            encrypted_size_bytes=backup_run.encrypted_size_bytes,
        )
        write_manifest(manifest_path, manifest_payload)

        client = build_sharepoint_client(
            sharepoint_site=target.sharepoint_site,
            sharepoint_folder=target.sharepoint_folder,
        )
        backup_upload = client.upload_file(encrypted_path, encrypted_filename)
        manifest_upload = client.upload_file(manifest_path, manifest_filename)

        verify_remote = target.verify_sharepoint_upload or settings.BACKUP_VERIFY_SHAREPOINT_UPLOAD
        if verify_remote:
            download_url = str(backup_upload.get("@microsoft.graph.downloadUrl", ""))
            if download_url:
                remote_sha256 = client.download_and_hash(download_url)
                if remote_sha256 != encrypted_sha256:
                    raise RuntimeError("Remote SharePoint backup hash did not match local encrypted hash.")

        deletion_ids = plan_backup_retention_deletions(
            sharepoint_items=client.list_folder_children(),
            target_slug=target.slug,
            keep_daily=_non_negative_int(target.retention_daily, 14),
            keep_weekly=_non_negative_int(target.retention_weekly, 8),
            keep_monthly=_non_negative_int(target.retention_monthly, 12),
        )
        for item_id in deletion_ids:
            client.delete_item(item_id)

        backup_run.sharepoint_backup_item_id = str(backup_upload.get("id", ""))
        backup_run.sharepoint_manifest_item_id = str(manifest_upload.get("id", ""))
        backup_run.sharepoint_backup_web_url = str(backup_upload.get("webUrl", ""))
        backup_run.sharepoint_manifest_web_url = str(manifest_upload.get("webUrl", ""))
        backup_run.status = BackupRun.STATUS_SUCCESS
        backup_run.finished_at = timezone.now()
        backup_run.save()
        send_backup_status_email(backup_run=backup_run, success=True)
        return "Complete"
    except Exception as error:
        logger.exception("P6 backup failed for target %s", target.slug)
        backup_run.status = BackupRun.STATUS_FAILED
        backup_run.finished_at = timezone.now()
        backup_run.error_message = str(error)
        backup_run.save(update_fields=["status", "finished_at", "error_message"])
        try:
            send_backup_status_email(backup_run=backup_run, success=False)
        except Exception:
            logger.exception("Failed to send backup failure email")
        raise
    finally:
        keep_local = target.keep_local_files or settings.BACKUP_KEEP_LOCAL
        _cleanup(artifacts, keep_local=keep_local)
