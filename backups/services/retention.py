from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Callable


MANIFEST_SUFFIX = ".manifest.json"


@dataclass(frozen=True)
class BackupFile:
    item_id: str
    name: str
    timestamp: dt.datetime


def backup_name_pattern(target_slug: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(target_slug)}_backup_(\d{{8}}_\d{{6}})\.bak\.enc$")


def _parse_backup_timestamp(file_name: str, target_slug: str) -> dt.datetime | None:
    match = backup_name_pattern(target_slug).match(file_name)
    if match is None:
        return None
    return dt.datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=dt.UTC)


def _select_latest_by_bucket(
    *,
    files: list[BackupFile],
    bucket_key: Callable[[BackupFile], Any],
    limit: int,
    excluded_names: set[str],
) -> set[str]:
    if limit < 1:
        return set()
    selected: set[str] = set()
    seen_buckets: set[object] = set()
    for backup_file in files:
        if backup_file.name in excluded_names:
            continue
        key = bucket_key(backup_file)
        if key in seen_buckets:
            continue
        seen_buckets.add(key)
        selected.add(backup_file.name)
        if len(selected) >= limit:
            break
    return selected


def plan_backup_retention_deletions(
    *,
    sharepoint_items: list[dict[str, object]],
    target_slug: str,
    keep_daily: int = 14,
    keep_weekly: int = 8,
    keep_monthly: int = 12,
) -> list[str]:
    name_to_id: dict[str, str] = {}
    backup_files: list[BackupFile] = []

    for item in sharepoint_items:
        item_id = str(item.get("id", "")).strip()
        file_name = str(item.get("name", "")).strip()
        if not item_id or not file_name or "file" not in item:
            continue
        name_to_id[file_name] = item_id
        timestamp = _parse_backup_timestamp(file_name, target_slug)
        if timestamp is not None:
            backup_files.append(BackupFile(item_id=item_id, name=file_name, timestamp=timestamp))

    backup_files.sort(key=lambda backup_file: backup_file.timestamp, reverse=True)

    keep_names: set[str] = set()
    keep_names.update(
        _select_latest_by_bucket(
            files=backup_files,
            bucket_key=lambda backup_file: backup_file.timestamp.date(),
            limit=keep_daily,
            excluded_names=set(),
        )
    )
    keep_names.update(
        _select_latest_by_bucket(
            files=backup_files,
            bucket_key=lambda backup_file: backup_file.timestamp.isocalendar()[:2],
            limit=keep_weekly,
            excluded_names=keep_names,
        )
    )
    keep_names.update(
        _select_latest_by_bucket(
            files=backup_files,
            bucket_key=lambda backup_file: (backup_file.timestamp.year, backup_file.timestamp.month),
            limit=keep_monthly,
            excluded_names=keep_names,
        )
    )

    deletions: list[str] = []
    for backup_file in backup_files:
        if backup_file.name not in keep_names:
            deletions.append(backup_file.item_id)

    pattern = backup_name_pattern(target_slug)
    for file_name, item_id in name_to_id.items():
        if not file_name.endswith(MANIFEST_SUFFIX):
            continue
        backup_name = file_name[: -len(MANIFEST_SUFFIX)]
        if pattern.match(backup_name) and backup_name not in keep_names:
            deletions.append(item_id)
    return deletions
