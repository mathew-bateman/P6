"""Encrypted, hash-verified settings recovery copy in the existing backup site."""
import json
from pathlib import Path
import tempfile

from django.utils import timezone

from backups.services.security import encrypt_file_chunked, decrypt_file_chunked, get_encryption_keys, calculate_file_sha256
from backups.services.sharepoint import build_sharepoint_client, download_file
from reporting_mirror.models import NativeSettingsCheckpoint, ReportingSource
from reporting_mirror.native_settings import content_hash


def backup_settings():
    row = NativeSettingsCheckpoint.objects.get(pk=1)
    checksum = row.sha256
    if row.offsite_backed_up_at and row.offsite_sha256 == checksum:
        return "Current PostgreSQL settings already verified offsite."
    if content_hash(row.payload) != checksum:
        raise ValueError("The portable settings checksum did not match.")
    target = ReportingSource.objects.select_related('target').get(code='p6_22').target
    try:
        client = build_sharepoint_client(sharepoint_site=target.sharepoint_site,sharepoint_folder=target.sharepoint_folder)
        with tempfile.TemporaryDirectory(prefix='p6-reporting-settings-') as temp:
            root=Path(temp);plain=root/'settings.json';encrypted=root/'settings.json.enc'
            plain.write_text(json.dumps({'schema_version':1,'sha256':checksum,'payload':row.payload},ensure_ascii=False,sort_keys=True),encoding='utf-8')
            encrypt_file_chunked(plain,encrypted,get_encryption_keys()[0])
            item=client.upload_file(encrypted,'postgres-reporting-settings-'+checksum.lower()+'.json.enc')
            url=str(item.get('@microsoft.graph.downloadUrl',''))
            if not url:
                raise RuntimeError('SharePoint did not return a settings recovery download URL.')
            remote=root/'remote.enc';restored=root/'restored.json'
            download_file(url,remote)
            if calculate_file_sha256(remote)!=calculate_file_sha256(encrypted):
                raise RuntimeError('The offsite encrypted settings hash did not match.')
            decrypt_file_chunked(remote,restored,get_encryption_keys())
            recovered=json.loads(restored.read_text(encoding='utf-8'))
            if recovered['sha256']!=checksum or content_hash(recovered['payload'])!=checksum:
                raise RuntimeError('The downloaded settings recovery proof failed.')
        NativeSettingsCheckpoint.objects.filter(pk=1,sha256=checksum).update(offsite_backed_up_at=timezone.now(),offsite_sha256=checksum,offsite_item_id=str(item.get('id','')),offsite_error='')
        return 'PostgreSQL settings uploaded, downloaded, decrypted and checksum verified.'
    except Exception as error:
        NativeSettingsCheckpoint.objects.filter(pk=1,sha256=checksum).update(offsite_error=str(error)[:2000])
        raise
